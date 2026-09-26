"""
教育智能体 - 主应用入口
FastAPI 后端服务（多用户版）

部署要点：
- 监听 PORT 环境变量（云沙箱注入），绑定 0.0.0.0
- 同时伺服前端构建产物（webapp/ 目录），单端口即可访问整个应用
- 所有业务接口强制登录，数据按 user_id 隔离
"""
import os
import json
import uuid
import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import database as db
import auth
from llm_service import llm_service, LLMService
from pdf_service import (generate_practice_pdf, generate_error_report_pdf,
                         pdf_missing_cjk_font, font_status)
from file_parser import parse_files, ParseResult

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
WEBAPP_DIR = os.path.join(BASE_DIR, "webapp")          # 前端构建产物
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

MASK_SENTINEL = "__KEEP_EXISTING__"

# 本地开发时前端跑在 5173，需要放行；生产环境同源部署不触发 CORS。
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) > 8:
        return key[:4] + "*" * (len(key) - 8) + key[-4:]
    return "****"


def public_user(user: dict) -> dict:
    """剔除敏感字段，只暴露前端需要的"""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "is_admin": bool(user.get("is_admin")),
    }


async def get_llm_for(user: dict) -> LLMService:
    """按用户取出其独立配置的 LLM 客户端（多用户各用各的 Key）"""
    cfg = await db.get_config(user["id"])
    svc = LLMService()
    if cfg.get("endpoint") and cfg.get("api_key") and cfg.get("model_name"):
        svc.configure(cfg["endpoint"], cfg["api_key"], cfg["model_name"])
    return svc


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    await db.purge_expired_sessions()
    yield


app = FastAPI(
    title="教育智能体 - 智能作业批改与分层练习系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Auth ====================

@app.post("/api/auth/register")
async def register(data: dict, response: Response):
    """注册。第一个注册的用户自动成为管理员。"""
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()

    err = auth.validate_credentials(username, password)
    if err:
        raise HTTPException(400, err)

    if await db.get_user_by_username(username):
        raise HTTPException(400, "该用户名已被占用")

    is_first = (await db.count_users()) == 0
    password_hash = await run_in_threadpool(auth.hash_password, password)
    user_id = await db.create_user(username, password_hash, display_name or username, is_admin=is_first)

    token = auth.new_token()
    await db.create_session(token, user_id)
    auth.set_session_cookie(response, token)

    user = await db.get_user_by_id(user_id)
    return {"success": True, "user": public_user(user), "is_admin": is_first}


@app.post("/api/auth/login")
async def login(data: dict, response: Response):
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        raise HTTPException(400, "请输入用户名和密码")

    user = await db.get_user_by_username(username)
    # 用户不存在时也走一次哈希比对，避免通过响应时间枚举用户名
    stored = user["password_hash"] if user else (
        "pbkdf2_sha256$200000$00000000000000000000000000000000$0"
    )
    ok = await run_in_threadpool(auth.verify_password, password, stored)
    if not user or not ok:
        raise HTTPException(401, "用户名或密码错误")

    token = auth.new_token()
    await db.create_session(token, user["id"])
    auth.set_session_cookie(response, token)
    return {"success": True, "user": public_user(user)}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        await db.delete_session(token)
    auth.clear_session_cookie(response)
    return {"success": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.get_current_user)):
    return {"user": public_user(user)}


# ==================== API Config ====================

@app.get("/api/config")
async def get_config(user: dict = Depends(auth.get_current_user)):
    """获取当前用户的 API 配置。

    安全约定：完整 API Key 永不离开服务器，只返回掩码。
    前端保存时若未改动 Key，回传 MASK_SENTINEL 表示保留原值。
    """
    config = await db.get_config(user["id"])
    key = config.get("api_key") or ""
    return {
        "endpoint": config.get("endpoint", ""),
        "model_name": config.get("model_name", ""),
        "api_key_masked": mask_key(key),
        "has_api_key": bool(key),
        "is_configured": bool(config.get("endpoint") and key and config.get("model_name")),
        "mask_sentinel": MASK_SENTINEL,
    }


@app.post("/api/config")
async def save_config(data: dict, user: dict = Depends(auth.get_current_user)):
    endpoint = (data.get("endpoint") or "").strip().rstrip("/")
    api_key = (data.get("api_key") or "").strip()
    model_name = (data.get("model_name") or "").strip()

    if not endpoint or not model_name:
        raise HTTPException(400, "请填写完整的配置信息")

    # 前端未修改 Key（传了哨兵值或留空）→ 沿用已保存的 Key
    if not api_key or api_key == MASK_SENTINEL:
        old = await db.get_config(user["id"])
        api_key = old.get("api_key") or ""

    if not api_key:
        raise HTTPException(400, "请填写 API Key")

    await db.save_config(user["id"], endpoint, api_key, model_name)
    return {"success": True, "message": "配置保存成功"}


@app.post("/api/config/test")
async def test_config(user: dict = Depends(auth.get_current_user)):
    svc = await get_llm_for(user)
    return await svc.test_connection()


@app.post("/api/config/normalize-url")
async def normalize_url(data: dict, user: dict = Depends(auth.get_current_user)):
    url = (data.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "请输入 URL")

    tips = ""
    original_url = url
    url = url.rstrip("/")
    if url != original_url:
        tips += "已自动去除尾部斜杠。"

    if url.endswith("/v1"):
        tips += " URL 已包含 /v1 路径，无需修改。"
    else:
        url = url + "/v1"
        tips += " 已自动追加 /v1 路径。"

    return {"url": url, "tips": tips.strip()}


@app.post("/api/config/validate-key")
async def validate_api_key(data: dict, user: dict = Depends(auth.get_current_user)):
    endpoint = (data.get("endpoint") or "").strip().rstrip("/")
    api_key = (data.get("api_key") or "").strip()

    # 允许沿用已存的 Key（前端不回传明文）
    if not api_key or api_key == MASK_SENTINEL:
        old = await db.get_config(user["id"])
        api_key = old.get("api_key") or ""

    if not endpoint or not api_key:
        raise HTTPException(400, "请提供 endpoint 和 api_key")

    return await LLMService.validate_key(endpoint, api_key)


@app.post("/api/config/models")
async def list_models(data: dict, user: dict = Depends(auth.get_current_user)):
    endpoint = (data.get("endpoint") or "").strip().rstrip("/")
    api_key = (data.get("api_key") or "").strip()

    if not api_key or api_key == MASK_SENTINEL:
        old = await db.get_config(user["id"])
        api_key = old.get("api_key") or ""

    if not endpoint or not api_key:
        raise HTTPException(400, "请提供 endpoint 和 api_key")

    try:
        models = await LLMService.list_models(endpoint, api_key)
        return {"models": models, "message": f"成功获取 {len(models)} 个模型"}
    except RuntimeError as e:
        return {"models": [], "message": str(e)}
    except Exception as e:
        return {"models": [], "message": f"获取模型列表失败: {str(e)}"}


# ==================== Students ====================

@app.get("/api/students")
async def list_students(user: dict = Depends(auth.get_current_user)):
    return {"students": await db.get_students(user["id"])}


@app.post("/api/students")
async def create_student(data: dict, user: dict = Depends(auth.get_current_user)):
    name = (data.get("name") or "").strip()
    grade = (data.get("grade") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    subject = (data.get("subject") or "数学").strip()

    if not name:
        raise HTTPException(400, "请填写学生姓名")

    student_id = await db.create_student(user["id"], name, grade, class_name, subject)
    return {"success": True, "id": student_id, "message": f"学生 {name} 添加成功"}


@app.put("/api/students/{student_id}")
async def update_student(student_id: int, data: dict, user: dict = Depends(auth.get_current_user)):
    name = (data.get("name") or "").strip()
    grade = (data.get("grade") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    subject = (data.get("subject") or "数学").strip()

    if not name:
        raise HTTPException(400, "请填写学生姓名")

    ok = await db.update_student(user["id"], student_id, name, grade, class_name, subject)
    if not ok:
        raise HTTPException(404, "学生不存在")
    return {"success": True, "message": "更新成功"}


@app.delete("/api/students/{student_id}")
async def delete_student(student_id: int, user: dict = Depends(auth.get_current_user)):
    ok = await db.delete_student(user["id"], student_id)
    if not ok:
        raise HTTPException(404, "学生不存在")
    return {"success": True, "message": "删除成功"}


@app.put("/api/students/{student_id}/default")
async def set_student_default(student_id: int, data: dict, user: dict = Depends(auth.get_current_user)):
    """设为 / 取消默认学生。设为默认时由后端保证同一用户下唯一。"""
    is_default = bool(data.get("is_default", True))
    ok = await db.set_default_student(user["id"], student_id, is_default)
    if not ok:
        raise HTTPException(404, "学生不存在")
    return {
        "success": True,
        "is_default": is_default,
        "message": "已设为默认学生" if is_default else "已取消默认学生",
    }


@app.get("/api/students/{student_id}")
async def get_student(student_id: int, user: dict = Depends(auth.get_current_user)):
    student = await db.get_student(user["id"], student_id)
    if not student:
        raise HTTPException(404, "学生不存在")
    return student


# ==================== Homework Grading ====================

@app.post("/api/homework/upload")
async def upload_homework(
    user: dict = Depends(auth.get_current_user),
    files: list[UploadFile] = File(default=[]),
    student_id: int = Form(...),
    subject: str = Form(default=""),
    content_text: str = Form(default=""),
):
    """上传作业（图片/PDF/Word/纯文本），或直接提交文字内容。

    修复点：files 与 content_text 均为可选，至少提供其一。
    原实现要求 files 必填且未声明 content_text，导致「文字输入」模式必然 422。
    """
    files = [f for f in files if f and f.filename]
    content_text = (content_text or "").strip()
    # 「自动识别」/「全部」/任何未知值 → 空串 = 科目未定，批改时按内容识别后回写
    subject = db.normalize_subject(subject)

    if not files and not content_text:
        raise HTTPException(400, "请上传文件或输入作业内容")

    # 校验学生归属
    student = await db.get_student(user["id"], student_id)
    if not student:
        raise HTTPException(404, "学生不存在")

    saved_paths = []
    for file in files:
        ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
        ext = "".join(c for c in ext if c.isalnum())[:10] or "jpg"
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(await file.read())
        saved_paths.append(filepath)

    # 纯文字输入：不经过文件解析，直接作为文本批改材料
    if not saved_paths:
        parse_result = ParseResult(
            mode="text", images=[], text=content_text, file_type="text", page_count=1
        )
    else:
        try:
            parse_result = parse_files(saved_paths)
        except Exception as e:
            raise HTTPException(400, f"文件解析失败: {str(e)}")

        # 文件走文本路径但内容为空时，回填用户手输的文字
        if parse_result.mode == "text" and not parse_result.text.strip() and content_text:
            parse_result.text = content_text

    if parse_result.mode == "vision" and parse_result.images:
        store_paths = parse_result.images
    else:
        store_paths = saved_paths

    homework_id = await db.create_homework_v2(
        student_id=student_id,
        subject=subject,
        file_paths=store_paths,
        file_type=parse_result.file_type,
        content_text=parse_result.text,
        user_id=user["id"],
    )
    if homework_id is None:
        raise HTTPException(404, "学生不存在")

    return {
        "success": True,
        "homework_id": homework_id,
        "image_count": len(saved_paths),
        "message": f"成功上传 {len(saved_paths) or 1} 份内容",
        "parse_result": {
            "mode": parse_result.mode,
            "file_type": parse_result.file_type,
            "page_count": parse_result.page_count,
            "image_count": len(parse_result.images),
            "text_length": len(parse_result.text),
        },
    }


@app.get("/api/homework/grade/{homework_id}")
async def grade_homework(homework_id: int, user: dict = Depends(auth.get_current_user)):
    """批改作业 — SSE 流式返回（视觉模式或文本模式）"""
    homework = await db.get_homework(user["id"], homework_id)
    if not homework:
        raise HTTPException(404, "作业不存在")

    subject = (homework.get("subject") or "").strip()   # 空 = 未定科目，批改时自动识别
    file_type = homework.get("file_type", "image")
    content_text = homework.get("content_text", "") or ""

    use_text_mode = bool(file_type in ("pdf_text", "docx", "text") and content_text)

    image_paths = []
    if not use_text_mode:
        try:
            image_paths = json.loads(homework["image_paths"])
        except Exception:
            image_paths = []
        if not image_paths:
            raise HTTPException(400, "没有可批改的内容")

    svc = await get_llm_for(user)

    async def stream_with_keepalive(agen, interval: float = float(os.environ.get("SSE_KEEPALIVE_SEC", "12"))):
        """
        包装异步生成器：长时间没有事件时插入 SSE 注释行保活。

        大模型（尤其视觉模型）首字可能要等几十秒，中间层代理若判定连接空闲
        就会掐断，前端只能看到「连接中断」。定期发注释行可避免这种情况。
        """
        it = agen.__aiter__()
        while True:
            nxt = asyncio.ensure_future(it.__anext__())
            try:
                while True:
                    done, _ = await asyncio.wait({nxt}, timeout=interval)
                    if done:
                        break
                    yield {"type": "__keepalive__", "data": None}
            except BaseException:
                nxt.cancel()
                raise

            exc = nxt.exception()
            if isinstance(exc, StopAsyncIteration):
                return
            if exc is not None:
                # 生成器内部抛异常：转成 error 事件，避免静默断流
                yield {"type": "error", "data": f"批改过程异常: {type(exc).__name__}: {exc}"}
                return
            yield nxt.result()

    async def event_stream():
        full_result = None
        thinking_steps = []
        had_error = False

        grading_stream = (
            svc.grade_homework_text(content_text, subject)
            if use_text_mode
            else svc.grade_homework(image_paths, subject)
        )

        async for event in stream_with_keepalive(grading_stream):
            event_type = event["type"]
            data = event["data"]
            if event_type == "__keepalive__":
                yield ": keepalive\n\n"          # SSE 注释行，浏览器自动忽略
                continue
            if event_type == "error":
                had_error = True
            if event_type in ("thinking", "content", "reasoning", "result", "error"):
                yield f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"
            if event_type == "result":
                full_result = data

        if not full_result:
            # 必须给出明确的终止事件。否则连接关闭时 EventSource 只会报「连接中断」，
            # 把服务端的真实原因（如输出被截断、模型不返回 JSON）整个盖掉。
            if not had_error:
                yield f"data: {json.dumps({'type': 'error', 'data': '批改未产出结果：模型没有按要求的 JSON 格式返回，请重试或更换模型'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'failed', 'data': {'homework_id': homework_id, 'reason': 'no_result'}}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'type': 'thinking', 'data': {'step': 'save', 'message': '💾 正在保存批改结果和错题记录...', 'status': 'active'}}, ensure_ascii=False)}\n\n"
        try:
            score = full_result.get("score", 0)
            total_q = full_result.get("total_questions", 0)
            correct_c = full_result.get("correct_count", 0)

            await db.update_homework_result(
                homework_id,
                json.dumps(full_result, ensure_ascii=False),
                json.dumps(thinking_steps, ensure_ascii=False),
                score, total_q, correct_c,
            )

            # 「自动识别」模式：上传时科目是空的，这里拿到模型判科结果后回写。
            # 手选科目时不回写 —— 用户的显式选择优先于模型判断。
            # 这条事件刻意复用 step="subject"（与 llm_service 的「识别科目」同一步）：
            # 前端 thinking 是按 step 去重的，所以对应那一行会从「识别科目：X」
            # 升级成「已按「X」归档」，用户看到的是最终状态，不会多出一行冗余。
            if not subject:
                detected = (full_result.get("subject") or "").strip()
                if await db.update_homework_subject(homework_id, detected):
                    yield f"data: {json.dumps({'type': 'thinking', 'data': {'step': 'subject', 'message': f'🏷️ 已按「{detected}」归档', 'status': 'done'}}, ensure_ascii=False)}\n\n"

            errors = []
            for q in full_result.get("questions", []):
                if not q.get("is_correct", True):
                    errors.append({
                        "question_num": q.get("question_num", 0),
                        "question_text": q.get("question_text", ""),
                        "error_type": q.get("error_type", ""),
                        "knowledge_point": q.get("knowledge_point", ""),
                        "student_answer": q.get("student_answer", ""),
                        "correct_answer": q.get("correct_answer", ""),
                        "analysis": q.get("analysis", ""),
                        "difficulty": q.get("difficulty", 3),
                    })

            if errors:
                await db.create_error_records(homework["student_id"], homework_id, errors)

            yield f"data: {json.dumps({'type': 'thinking', 'data': {'step': 'save', 'message': f'💾 已保存 {len(errors)} 条错题记录', 'status': 'done'}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'data': {'homework_id': homework_id, 'score': score, 'errors_saved': len(errors)}}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'thinking', 'data': {'step': 'save', 'message': '💾 保存失败', 'status': 'done'}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'error', 'data': f'保存结果失败: {str(e)}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'failed', 'data': {'homework_id': homework_id, 'reason': 'save_failed'}}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/homework")
async def list_homework(student_id: Optional[int] = Query(None),
                       user: dict = Depends(auth.get_current_user)):
    return {"homeworks": await db.get_homework_list(user["id"], student_id)}


@app.delete("/api/homework/{homework_id}")
async def delete_homework(homework_id: int, user: dict = Depends(auth.get_current_user)):
    ok = await db.delete_homework(user["id"], homework_id)
    if not ok:
        raise HTTPException(404, "作业不存在")
    return {"success": True, "message": "删除成功"}


@app.get("/api/homework/{homework_id}")
async def get_homework_detail(homework_id: int, user: dict = Depends(auth.get_current_user)):
    homework = await db.get_homework(user["id"], homework_id)
    if not homework:
        raise HTTPException(404, "作业不存在")

    for field in ("grading_result", "thinking_chain", "image_paths"):
        if homework.get(field):
            try:
                homework[field] = json.loads(homework[field])
            except Exception:
                pass
    return homework


# ==================== Error Analysis ====================

@app.get("/api/students/{student_id}/errors")
async def get_student_errors(student_id: int, subject: str = Query(default=""),
                             user: dict = Depends(auth.get_current_user)):
    """错题列表 + 统计。subject 非空时只返回该科目，不传=全部科目"""
    errors = await db.get_error_records(user["id"], student_id, subject=subject)
    stats = await db.get_error_stats(user["id"], student_id, subject=subject)
    return {"errors": errors, "stats": stats, "subject": subject}


@app.get("/api/students/{student_id}/subjects")
async def get_student_subjects(student_id: int, user: dict = Depends(auth.get_current_user)):
    """该学生各科目的作业数/错题数/练习数 —— 前端科目 Tab 的数据源"""
    student = await db.get_student(user["id"], student_id)
    if not student:
        raise HTTPException(404, "学生不存在")
    return {
        "subjects": await db.get_subject_overview(user["id"], student_id),
        "all_subjects": db.SUBJECTS,
    }


# ==================== Practice Generation ====================

@app.post("/api/practice/generate")
async def generate_practice(data: dict, user: dict = Depends(auth.get_current_user)):
    """生成练习题 — SSE 流式返回。

    科目取自请求参数（用户在练习页选的那一科），不再取 student.subject ——
    后者是学生档案上的固定值，会把语文错题当数学来出题（本次修掉的 bug）。
    """
    student_id = data.get("student_id")
    error_ids = data.get("error_ids", [])
    subject = (data.get("subject") or "").strip()
    if subject == db.SUBJECT_ALL:
        subject = ""

    if not student_id:
        raise HTTPException(400, "请选择学生")

    student = await db.get_student(user["id"], student_id)
    if not student:
        raise HTTPException(404, "学生不存在")

    # 未指定科目（前端「全部」视图不选科直接调）时，挑错题最多的科目兜底。
    # 关键：必须在取错题之前把科目定下来，否则会把多科错题混进同一个提示词
    # —— 那正是这次要修掉的问题。
    if not subject:
        overview = await db.get_subject_overview(user["id"], student_id)
        with_errors = [s for s in overview if s["error_count"] > 0]
        subject = (max(with_errors, key=lambda s: s["error_count"])["subject"]
                   if with_errors else db.DEFAULT_SUBJECT)

    errors = await db.get_error_records(user["id"], student_id, subject=subject)
    if not errors:
        raise HTTPException(400, f"该学生暂无{subject}错题记录，请先批改{subject}作业")

    if error_ids:
        errors = [e for e in errors if e["id"] in error_ids]
    errors = errors[:15]

    try:
        student_profile = await db.get_student_profile(user["id"], student_id)
    except Exception:
        student_profile = None

    svc = await get_llm_for(user)

    async def event_stream():
        full_result = None

        async for event in svc.generate_practice(
            errors, student["name"], subject, student_profile=student_profile
        ):
            event_type = event["type"]
            event_data = event["data"]
            if event_type in ("thinking", "content", "reasoning", "result", "error"):
                yield f"data: {json.dumps({'type': event_type, 'data': event_data}, ensure_ascii=False)}\n\n"
            if event_type == "result":
                full_result = event_data

        if full_result:
            try:
                pdf_path = generate_practice_pdf(
                    full_result, student["name"], subject=subject
                )
                practice_id = await db.create_practice_sheet(
                    user["id"],
                    student_id,
                    full_result.get("title", "练习题"),
                    json.dumps(full_result.get("questions", []), ensure_ascii=False),
                    json.dumps(full_result.get("target_knowledge_points", []), ensure_ascii=False),
                    pdf_path,
                    subject=subject,
                )
                yield f"data: {json.dumps({'type': 'done', 'data': {'practice_id': practice_id, 'pdf_path': pdf_path}}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'data': f'保存练习题失败: {str(e)}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/practice")
async def list_practice(student_id: Optional[int] = Query(None),
                        subject: str = Query(default=""),
                        user: dict = Depends(auth.get_current_user)):
    return {"practice_sheets": await db.get_practice_sheets(user["id"], student_id,
                                                             subject=subject)}


@app.get("/api/practice/{practice_id}/pdf")
async def download_practice_pdf(practice_id: int, user: dict = Depends(auth.get_current_user)):
    sheet = await db.get_practice_sheet(user["id"], practice_id)
    if not sheet:
        raise HTTPException(404, "练习题不存在")

    pdf_path = sheet.get("pdf_path", "")
    # 字体修复前生成的历史 PDF 里没嵌中文字体（中文全是方块）。
    # 这类文件不直接发给用户，就地重新生成一份 —— 用户不必知道
    # 「字体修好了但要重新生成」这回事，点一次下载就该拿到能看的 PDF。
    stale_broken = (bool(pdf_path) and os.path.exists(pdf_path)
                    and pdf_missing_cjk_font(pdf_path))
    old_path = pdf_path
    if not pdf_path or not os.path.exists(pdf_path) or stale_broken:
        questions_data = {
            "title": sheet.get("title", "练习题"),
            "questions": json.loads(sheet.get("questions") or "[]"),
            "target_knowledge_points": json.loads(sheet.get("target_knowledge_points") or "[]"),
        }
        student = await db.get_student(user["id"], sheet["student_id"])
        student_name = student["name"] if student else ""
        # 补生成（旧记录没有 pdf_path）时也要带上科目，
        # 否则会落到 exports/{学生}/ 根目录且标题不带科目，和按科分层的约定不一致。
        pdf_path = generate_practice_pdf(questions_data, student_name,
                                         subject=sheet.get("subject", "") or "")
        await db.update_practice_pdf_path(practice_id, pdf_path)
        # 方块版旧文件从磁盘清掉，免得以后有人直接翻 exports 目录又看到它
        if stale_broken and old_path and os.path.abspath(old_path) != os.path.abspath(pdf_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
        sheet["pdf_path"] = pdf_path

    return FileResponse(
        pdf_path, media_type="application/pdf", filename=os.path.basename(pdf_path)
    )


@app.get("/api/students/{student_id}/error-report-pdf")
async def download_error_report(student_id: int, subject: str = Query(default=""),
                                user: dict = Depends(auth.get_current_user)):
    """错题报告 PDF。subject 非空时只导出该科目，标题也会带科目"""
    student = await db.get_student(user["id"], student_id)
    if not student:
        raise HTTPException(404, "学生不存在")

    errors = await db.get_error_records(user["id"], student_id, subject=subject)
    if not errors:
        raise HTTPException(400, f"暂无{subject}错题" if subject else "暂无错题记录")
    stats = await db.get_error_stats(user["id"], student_id, subject=subject)
    pdf_path = generate_error_report_pdf(student["name"], errors, stats, subject=subject)
    return FileResponse(
        pdf_path, media_type="application/pdf", filename=os.path.basename(pdf_path)
    )


# ==================== Dashboard ====================

@app.get("/api/stats")
async def get_stats(user: dict = Depends(auth.get_current_user)):
    return await db.get_dashboard_stats(user["id"])


# ==================== Image serving ====================

@app.get("/api/uploads/{filename}")
async def serve_upload(filename: str):
    """提供上传的作业图片。

    文件名是 UUID4（122 位随机），不可枚举；这里不再叠加登录校验，
    以免 <img src> 等场景的鉴权复杂度上升。
    """
    safe = os.path.basename(filename)
    filepath = os.path.join(UPLOAD_DIR, safe)
    if not os.path.realpath(filepath).startswith(os.path.realpath(UPLOAD_DIR)):
        raise HTTPException(404, "文件不存在")
    if not os.path.isfile(filepath):
        raise HTTPException(404, "文件不存在")
    return FileResponse(filepath)


# ==================== Health check ====================

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/health/font")
async def health_font():
    """字体自检 —— 线上 PDF 中文是否具备渲染条件。

    无需鉴权：只回「有没有中文字体 / 字体来自哪一类 / 字体文件名」，
    不含任何用户数据或绝对路径。发布后不必登录即可远程确认字体状态。
    """
    return font_status()


# ==================== Frontend (SPA) ====================

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend(full_path: str):
    """伺服前端构建产物，并为 BrowserRouter 提供 SPA 回退。

    注意：必须最后注册，且显式排除 /api/*，否则未匹配的接口会返回 HTML，
    前端拿到 HTML 再按 JSON 解析会报出误导性的错误。
    """
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")

    webapp_root = os.path.realpath(WEBAPP_DIR)

    if full_path:
        candidate = os.path.realpath(os.path.join(WEBAPP_DIR, full_path))
        if candidate.startswith(webapp_root) and os.path.isfile(candidate):
            base = os.path.basename(candidate)
            # service worker 必须每次校验，否则用户会长期吃旧缓存
            if base in ("sw.js", "sw.js.map"):
                return FileResponse(candidate, media_type="application/javascript",
                                    headers={"Cache-Control": "no-cache, must-revalidate"})
            return FileResponse(candidate)

    index = os.path.join(WEBAPP_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index, media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    raise HTTPException(status_code=404, detail="前端资源未就绪，请先构建前端")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
