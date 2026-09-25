"""
LLM 服务模块 - 与 OpenAI 兼容格式的多模态大模型 API 交互
"""
import openai
import base64
import json
import os
import re
from typing import AsyncGenerator


class LLMService:
    """大模型服务 - 支持 OpenAI 兼容格式的 API"""

    def __init__(self):
        self.client = None
        self.model = ""
        self.configured = False

    def configure(self, endpoint: str, api_key: str, model_name: str):
        """配置 LLM API"""
        if not endpoint or not api_key or not model_name:
            self.configured = False
            return

        self.client = openai.AsyncOpenAI(
            base_url=endpoint,
            api_key=api_key
        )
        self.model = model_name
        self.configured = True

    # 判定「这个报错是不是因为输出长度超限」的关键词。
    # 宁可多降一档（多一次立即返回的 4xx，通常不计费），也不要漏降（整次批改写死）。
    _LENGTH_HINTS = (
        "max_tokens", "max_completion_tokens", "max_new_tokens",
        "max_output_tokens", "output length", "output token", "output_length",
        "token limit", "token_limit", "length",
        "输出长度", "输出上限", "最大输出", "最多输出", "单次输出",
        "输出token", "输出 token",
    )
    # 语义兜底：同时命中「容量词」与「超限词」也算
    _CAPACITY_HINTS = ("token", "length", "长度", "输出", "output")
    _OVERRUN_HINTS = ("超", "上限", "最大", "最多", "limit", "exceed",
                      "too large", "too long", "maximum")
    _MIN_MAX_TOKENS = 1024

    @classmethod
    def _looks_like_length_rejection(cls, err: Exception) -> bool:
        """
        判断报错是否属于「输出上限超了」这一类，决定要不要降级重试。

        各家文案差异极大，实测遇到的形态：
          含 max_tokens 的英文/中文文案        → 关键词命中
          "output token limit exceeded ..."   → 关键词命中 output token
          "超出最大输出长度，请调小输出长度"    → 关键词命中 输出长度
          "该模型单次最多输出 4096 个 token"   → 只能靠语义组合命中

        判定刻意偏宽松：漏判的代价是整次批改失败，多判一次的代价只是
        一次立即返回的 4xx。就算真误判了别的原因（余额不足、key 无效），
        试完所有档位后仍会把最后一个真实错误原样抛出，不会掩盖问题。
        """
        text = str(err).lower()
        if any(h in text for h in cls._LENGTH_HINTS):
            return True
        return (any(c in text for c in cls._CAPACITY_HINTS)
                and any(o in text for o in cls._OVERRUN_HINTS))

    async def _create_stream(self, **kwargs):
        """
        发起对话请求（流式/非流式通用），max_tokens 超限时自动降级重试。

        各家 OpenAI 兼容服务的输出上限差异极大：
        例如 DeepSeek 的 deepseek-flash 支持 384K 输出，
        而部分厂商只允许 8192/4096，超出会直接返回 4xx。
        这里按阶梯逐级回退，**停在厂商能接受的最高档**（不会一路跌到底），
        避免因为一个参数把整次批改写死。
        传进来的 max_tokens 作为最高档，向下依次取半。
        """
        top = int(kwargs.pop("max_tokens", 8192) or 8192)
        ladder = []
        v = top
        while v >= self._MIN_MAX_TOKENS:
            ladder.append(v)
            v //= 2
        if not ladder:
            ladder = [top]

        last_err = None
        for mt in ladder:
            try:
                return await self.client.chat.completions.create(max_tokens=mt, **kwargs)
            except openai.APIStatusError as e:
                status = getattr(e, "status_code", None)
                if not (status and 400 <= status < 500):
                    raise       # 5xx 是服务端问题，降级没有意义
                if not self._looks_like_length_rejection(e):
                    raise       # 与输出上限无关的报错，直接抛出，不要掩盖真实原因
                last_err = e
                print(f"[llm] max_tokens={mt} 被拒绝（HTTP {status}，输出上限），"
                      f"降级重试：{str(e)[:140]}", flush=True)
        raise last_err

    async def test_connection(self) -> dict:
        """测试 API 连接"""
        if not self.configured:
            return {"success": False, "message": "API 未配置"}

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "请回复'连接成功'四个字"}],
                max_tokens=50
            )
            content = response.choices[0].message.content
            return {"success": True, "message": f"连接成功！模型回复: {content}"}
        except Exception as e:
            return {"success": False, "message": f"连接失败: {str(e)}"}

    # ==================== 模型列表 & Key 验证 ====================

    @staticmethod
    async def list_models(endpoint: str, api_key: str) -> list[dict]:
        """
        获取可用模型列表

        Args:
            endpoint: API 端点 URL
            api_key: API 密钥

        Returns:
            模型列表 [{id, name}, ...]
        """
        try:
            client = openai.AsyncOpenAI(base_url=endpoint, api_key=api_key)
            models_response = await client.models.list()
            models = []
            for model in models_response.data:
                models.append({
                    "id": model.id,
                    "name": getattr(model, 'name', model.id)
                })
            # 按 id 排序
            models.sort(key=lambda m: m['id'])
            return models
        except Exception as e:
            raise RuntimeError(f"获取模型列表失败: {str(e)}")

    @staticmethod
    async def validate_key(endpoint: str, api_key: str) -> dict:
        """
        验证 API Key 是否有效（轻量级测试，尝试获取模型列表）

        Args:
            endpoint: API 端点 URL
            api_key: API 密钥

        Returns:
            {"valid": bool, "message": str}
        """
        try:
            client = openai.AsyncOpenAI(base_url=endpoint, api_key=api_key)
            models_response = await client.models.list()
            model_count = len(models_response.data)
            return {
                "valid": True,
                "message": f"API Key 有效，可用模型数: {model_count}"
            }
        except openai.AuthenticationError:
            return {
                "valid": False,
                "message": "API Key 无效，认证失败"
            }
        except openai.PermissionDeniedError:
            return {
                "valid": False,
                "message": "权限不足，请检查 API Key 权限"
            }
        except Exception as e:
            return {
                "valid": False,
                "message": f"验证失败: {str(e)}"
            }

    # ==================== 作业批改 ====================

    def _build_grading_prompt(self, subject: str) -> str:
        """构建批改 prompt 文本"""
        return f"""你是一个专业的{subject}教师，请仔细批改这份学生作业。

批改步骤：
1. 识别图片中的每一道题目和学生的解答
2. 逐题判断对错
3. 只对错误的题目给出错因和正确解法

【输出长度要求 —— 非常重要，违反会导致 JSON 被截断而整份作废】
- 只输出一个 JSON 对象，不要任何解释文字，不要用 markdown 代码块
- 必须一次性输出**完整**的 JSON；题目较多时请把每项写得更精简，不要中途停下
- question_text 只写题号和算式/关键条件，16 字以内，不要整段抄写题干
- is_correct 为 true 的题目：analysis 固定写「正确」，error_type 与 knowledge_point 写空字符串
- is_correct 为 false 的题目：analysis 控制在 50 字以内，只讲错因和正确解法
- overall_comment 不超过 60 字；weak_points 最多 3 项

JSON 格式：
{{
    "total_questions": 题目总数,
    "correct_count": 正确题数,
    "score": 得分(百分制,保留1位小数),
    "questions": [
        {{
            "question_num": 题号,
            "question_text": "题目内容（从图片中识别）",
            "student_answer": "学生的回答",
            "correct_answer": "正确答案",
            "is_correct": true或false,
            "error_type": "错误类型(计算错误/概念错误/粗心大意/方法错误/审题错误/步骤缺失等，正确则填空字符串)",
            "knowledge_point": "涉及的知识点",
            "analysis": "详细分析(如果正确简要说明,如果错误详细分析错因和正确解法)",
            "difficulty": 难度(1-5的整数)
        }}
    ],
    "overall_comment": "总体评语(鼓励性语言,指出优点和改进方向)",
    "weak_points": ["薄弱知识点1", "薄弱知识点2"]
}}"""

    def _parse_json_response(self, full_response: str, truncated_hint: bool = False) -> dict:
        """
        从 LLM 响应中提取 JSON 结果。

        分四层尝试，避免「模型输出被长度上限截断」时整份作业白批：
          1. 直接解析（含 ```json 代码块剥离）
          2. 正则抠出最外层 {...}
          3. 截断抢救：把已经完整的题目对象取出来，拼一份可用的部分结果
          4. 都失败则抛出带诊断信息的异常（含截断位置），便于定位

        Args:
            truncated_hint: 调用方是否已判定输出被截断（finish_reason == "length"）
        """
        json_str = full_response.strip()

        # 处理 markdown 代码块
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        candidates = [json_str]
        json_match = re.search(r'\{[\s\S]*\}', full_response)
        if json_match:
            candidates.append(json_match.group())

        for candidate in candidates:
            try:
                result = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict) and "questions" in result:
                if truncated_hint:
                    result["truncated"] = True
                return result

        # 第三层：截断抢救
        salvaged = self._salvage_truncated_json(full_response)
        if salvaged:
            return salvaged

        raise ValueError(self._describe_unparsable(full_response, truncated_hint))

    @staticmethod
    def _salvage_truncated_json(text: str):
        """
        从被截断的 JSON 里抢救出「已经完整」的题目对象。

        模型输出被长度上限砍断时，前面若干道题通常是完整的，
        把它们捞出来仍能给学生一份可用的批改结果，而不是全部作废。
        返回 None 表示无法抢救。
        """
        anchor = re.search(r'"questions"\s*:\s*\[', text)
        if not anchor:
            return None

        raw_objects = []
        i, n = anchor.end(), len(text)
        depth, start, in_str, escaped = 0, None, False, False

        while i < n:
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start is not None:
                        raw_objects.append(text[start:i + 1])
                        start = None
            i += 1

        questions = []
        for raw in raw_objects:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "question_num" in obj:
                questions.append(obj)

        if not questions:
            return None

        def head_int(key):
            mm = re.search(rf'"{key}"\s*:\s*(\d+)', text)
            return int(mm.group(1)) if mm else None

        total = head_int("total_questions") or len(questions)
        correct = sum(1 for q in questions if q.get("is_correct"))
        score = round(correct / total * 100, 1) if total else 0.0

        return {
            "total_questions": total,
            "correct_count": correct,
            "score": score,
            "questions": questions,
            "overall_comment": (
                f"⚠️ 本次只批改了前 {len(questions)} 道题（共 {total} 道）："
                "模型单次输出达到长度上限被截断。建议把作业分成几次上传，"
                "或改用输出长度上限更大的模型。"
            ),
            "weak_points": [],
            "truncated": True,
        }

    @staticmethod
    def _describe_unparsable(raw: str, truncated_hint: bool) -> str:
        """生成可诊断的解析失败说明（含首尾片段，便于判断问题类型）"""
        stripped = raw.rstrip()
        if truncated_hint:
            reason = "模型输出达到长度上限被截断，JSON 不完整"
        elif "{" not in raw:
            reason = ("模型没有返回 JSON 格式的内容：可能该模型不支持图片输入，"
                      "或图片不清晰无法识别。请确认所选模型支持视觉，或改用文字输入")
        elif not stripped.endswith("}"):
            reason = "模型输出在 JSON 中途结束（疑似被截断）"
        else:
            reason = "模型返回的 JSON 内容不合法"

        head = stripped[:160].replace("\n", " ")
        tail = stripped[-160:].replace("\n", " ")
        return (f"{reason}；原始输出 {len(raw)} 字符。"
                f"【开头】{head} … 【结尾】{tail}")

    async def grade_homework(self, image_paths: list[str], subject: str = "数学") -> AsyncGenerator[dict, None]:
        """
        批改作业（视觉模式）- 流式返回思维链和结果
        yields: {"type": "thinking"|"content"|"result"|"error", "data": ...}
        """
        if not self.configured:
            yield {"type": "error", "data": "LLM API 未配置，请先在设置页面配置 API"}
            return

        # Step 1: 文件接收
        yield {"type": "thinking", "data": {
            "step": "receive",
            "message": f"📥 接收到 {len(image_paths)} 个文件，类型: 图片",
            "status": "done"
        }}

        # Step 2: 文件解析
        yield {"type": "thinking", "data": {
            "step": "parse",
            "message": f"📄 文件解析完成，共 {len(image_paths)} 张图片，走视觉识别路径",
            "status": "done"
        }}

        # Step 3: 内容识别
        yield {"type": "thinking", "data": {
            "step": "recognize",
            "message": "🔍 AI 正在识别题目...",
            "status": "active"
        }}

        # Build multimodal message
        content = []
        content.append({
            "type": "text",
            "text": self._build_grading_prompt(subject)
        })

        # Add images
        for img_path in image_paths:
            try:
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode()

                ext = img_path.lower().rsplit('.', 1)[-1] if '.' in img_path else 'jpeg'
                mime_map = {
                    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'gif': 'image/gif', 'webp': 'image/webp', 'bmp': 'image/bmp'
                }
                mime_type = mime_map.get(ext, 'image/jpeg')

                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{img_data}"
                    }
                })
            except Exception as e:
                yield {"type": "error", "data": f"读取图片失败 {img_path}: {str(e)}"}
                return

        # Call LLM
        try:
            full_response = ""
            stream = await self._create_stream(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                stream=True,
                max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "32768"))
            )

            yielded_grading = False
            finish_reason = None
            async for chunk in stream:
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    if chunk.choices[0].delta.content:
                        text = chunk.choices[0].delta.content
                        full_response += text
                        yield {"type": "content", "data": text}

                        # Update thinking status based on content
                        if not yielded_grading and '"questions"' in full_response:
                            yield {"type": "thinking", "data": {
                                "step": "recognize",
                                "message": "🔍 题目识别完成",
                                "status": "done"
                            }}
                            yield {"type": "thinking", "data": {
                                "step": "grading",
                                "message": "📝 逐题批改中...",
                                "status": "active"
                            }}
                            yielded_grading = True

            # finish_reason == "length" 说明模型输出被长度上限截断，JSON 必然不完整
            truncated = finish_reason == "length"

            yield {"type": "thinking", "data": {
                "step": "grading",
                "message": "📝 批改完成" + ("（输出被截断，仅保留已完成的题目）" if truncated else ""),
                "status": "done"
            }}

            # Step: 错误分析
            yield {"type": "thinking", "data": {
                "step": "analyze",
                "message": "🧠 分析错误类型和知识点...",
                "status": "active"
            }}

            # Step: 生成报告
            yield {"type": "thinking", "data": {
                "step": "report",
                "message": "📊 正在生成批改报告...",
                "status": "active"
            }}

            # Parse result
            try:
                result = self._parse_json_response(full_response, truncated_hint=truncated)
                yield {"type": "thinking", "data": {
                    "step": "analyze",
                    "message": "🧠 错误分析完成",
                    "status": "done"
                }}
                yield {"type": "thinking", "data": {
                    "step": "report",
                    "message": "📊 批改报告生成完成",
                    "status": "done"
                }}
                yield {"type": "result", "data": result}
            except Exception as e:
                yield {"type": "error", "data": f"批改结果解析失败：{e}（finish_reason={finish_reason}）"}

        except Exception as e:
            yield {"type": "error", "data": f"调用 LLM API 失败: {str(e)}"}

    async def grade_homework_text(self, text: str, subject: str = "数学") -> AsyncGenerator[dict, None]:
        """
        批改作业（文本模式）- 流式返回思维链和结果
        将文本内容作为 prompt 的一部分发送给 LLM，使用同样的 JSON 输出格式

        Args:
            text: 作业的文本内容
            subject: 学科名称

        yields: {"type": "thinking"|"content"|"result"|"error", "data": ...}
        """
        if not self.configured:
            yield {"type": "error", "data": "LLM API 未配置，请先在设置页面配置 API"}
            return

        # Step 1: 文件接收
        yield {"type": "thinking", "data": {
            "step": "receive",
            "message": f"📥 接收到文本内容，共 {len(text)} 字",
            "status": "done"
        }}

        # Step 2: 文件解析
        yield {"type": "thinking", "data": {
            "step": "parse",
            "message": "📄 文本内容解析完成，走纯文本批改路径",
            "status": "done"
        }}

        # Step 3: 内容识别
        yield {"type": "thinking", "data": {
            "step": "recognize",
            "message": "🔍 AI 正在识别题目...",
            "status": "active"
        }}

        # Build text-only prompt
        prompt = f"""你是一个专业的{subject}教师，请仔细批改这份学生作业。

以下是学生的作业内容：
------
{text}
------

请按照以下步骤进行批改：
1. 仔细识别以上文本中的每一道题目和学生的解答
2. 逐题分析学生的解答过程和结果
3. 判断每道题的对错
4. 对错误的题目给出详细分析，指出错误原因和正确解法

请严格按照以下JSON格式返回批改结果（不要输出任何其他内容，只输出JSON）：
{{
    "total_questions": 题目总数,
    "correct_count": 正确题数,
    "score": 得分(百分制,保留1位小数),
    "questions": [
        {{
            "question_num": 题号,
            "question_text": "题目内容",
            "student_answer": "学生的回答",
            "correct_answer": "正确答案",
            "is_correct": true或false,
            "error_type": "错误类型(计算错误/概念错误/粗心大意/方法错误/审题错误/步骤缺失等，正确则填空字符串)",
            "knowledge_point": "涉及的知识点",
            "analysis": "详细分析(如果正确简要说明,如果错误详细分析错因和正确解法)",
            "difficulty": 难度(1-5的整数)
        }}
    ],
    "overall_comment": "总体评语(鼓励性语言,指出优点和改进方向)",
    "weak_points": ["薄弱知识点1", "薄弱知识点2"]
}}"""

        # Call LLM
        try:
            full_response = ""
            stream = await self._create_stream(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "32768"))
            )

            yielded_grading = False
            finish_reason = None
            async for chunk in stream:
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    if chunk.choices[0].delta.content:
                        text_chunk = chunk.choices[0].delta.content
                        full_response += text_chunk
                        yield {"type": "content", "data": text_chunk}

                        if not yielded_grading and '"questions"' in full_response:
                            yield {"type": "thinking", "data": {
                                "step": "recognize",
                                "message": "🔍 题目识别完成",
                                "status": "done"
                            }}
                            yield {"type": "thinking", "data": {
                                "step": "grading",
                                "message": "📝 逐题批改中...",
                                "status": "active"
                            }}
                            yielded_grading = True

            truncated = finish_reason == "length"

            yield {"type": "thinking", "data": {
                "step": "grading",
                "message": "📝 批改完成" + ("（输出被截断，仅保留已完成的题目）" if truncated else ""),
                "status": "done"
            }}

            yield {"type": "thinking", "data": {
                "step": "analyze",
                "message": "🧠 分析错误类型和知识点...",
                "status": "active"
            }}

            yield {"type": "thinking", "data": {
                "step": "report",
                "message": "📊 正在生成批改报告...",
                "status": "active"
            }}

            # Parse result
            try:
                result = self._parse_json_response(full_response, truncated_hint=truncated)
                yield {"type": "thinking", "data": {
                    "step": "analyze",
                    "message": "🧠 错误分析完成",
                    "status": "done"
                }}
                yield {"type": "thinking", "data": {
                    "step": "report",
                    "message": "📊 批改报告生成完成",
                    "status": "done"
                }}
                yield {"type": "result", "data": result}
            except Exception as e:
                yield {"type": "error", "data": f"批改结果解析失败：{e}（finish_reason={finish_reason}）"}

        except Exception as e:
            yield {"type": "error", "data": f"调用 LLM API 失败: {str(e)}"}

    # ==================== 练习题生成 ====================

    async def generate_practice(self, error_records: list, student_name: str,
                                 subject: str = "数学",
                                 student_profile: dict = None) -> AsyncGenerator[dict, None]:
        """
        根据错题生成分层练习 - 流式返回

        Args:
            error_records: 错题记录列表
            student_name: 学生姓名
            subject: 学科
            student_profile: 学生数据画像（来自 db.get_student_profile()）

        yields: {"type": "thinking"|"content"|"result"|"error", "data": ...}
        """
        if not self.configured:
            yield {"type": "error", "data": "LLM API 未配置"}
            return

        yield {"type": "thinking", "data": {"step": "analyze", "message": "📋 正在分析错题记录...", "status": "active"}}

        # Build error summary
        errors_text = ""
        knowledge_points = set()
        for i, err in enumerate(error_records, 1):
            kp = err.get('knowledge_point', '')
            if kp:
                knowledge_points.add(kp)
            errors_text += f"""
错题{i}:
  - 题目: {err.get('question_text', '未知')}
  - 学生答案: {err.get('student_answer', '未知')}
  - 正确答案: {err.get('correct_answer', '未知')}
  - 错误类型: {err.get('error_type', '未知')}
  - 知识点: {kp}
  - 难度: {err.get('difficulty', 3)}/5
"""

        yield {"type": "thinking", "data": {"step": "analyze", "message": f"📋 分析完成，发现 {len(knowledge_points)} 个薄弱知识点", "status": "done"}}
        yield {"type": "thinking", "data": {"step": "design", "message": "🎯 正在设计分层练习题...", "status": "active"}}

        # 构建学生画像注入段
        profile_text = ""
        if student_profile:
            profile_text = "\n## 学生数据画像\n"

            avg_score = student_profile.get('avg_score', 0)
            profile_text += f"- 历史平均分: {avg_score}\n"

            recent_scores = student_profile.get('recent_scores', [])
            if recent_scores:
                scores_str = " → ".join([str(s['score']) for s in recent_scores])
                profile_text += f"- 最近得分趋势: {scores_str}\n"

            kp_dist = student_profile.get('error_knowledge_distribution', [])
            if kp_dist:
                top_kps = kp_dist[:5]
                kp_str = ", ".join([f"{k['knowledge_point']}({k['count']}次)" for k in top_kps])
                profile_text += f"- 高频错误知识点: {kp_str}\n"

            et_dist = student_profile.get('error_type_distribution', [])
            if et_dist:
                et_str = ", ".join([f"{e['error_type']}({e['count']}次)" for e in et_dist])
                profile_text += f"- 错误类型分布: {et_str}\n"

            practice_count = student_profile.get('practice_count', 0)
            profile_text += f"- 已完成练习次数: {practice_count}\n"

            profile_text += "\n请根据以上学生画像，设计更有针对性的个性化题目。对于高频错误知识点要重点加强，根据得分趋势调整题目难度。\n"

        prompt = f"""你是一个经验丰富的{subject}教师，正在为学生"{student_name}"设计个性化分层练习。

## 学生错题分析
{errors_text}

## 薄弱知识点
{', '.join(knowledge_points) if knowledge_points else '待分析'}
{profile_text}
## 出题要求
请严格按照分层教学原则，设计一套针对性练习题：

### 分层设计：
1. **基础巩固层**（2-3道）：针对错题涉及的基本概念，难度低于原题，帮助学生重新理解和掌握基础
2. **能力提升层**（2-3道）：与原题难度相当，变换题目形式，检验学生是否真正掌握
3. **拓展挑战层**（1-2道）：综合多个知识点，难度略高于原题，培养学生综合运用能力

### 每道题要求：
- 题目表述清晰准确
- 提供详细的参考答案
- 提供完整的解题思路和步骤

请严格按照以下JSON格式返回（不要输出其他内容，只输出JSON）：
{{
    "title": "针对{student_name}的{subject}专项练习",
    "description": "本练习针对以下薄弱知识点设计：{', '.join(knowledge_points) if knowledge_points else '综合练习'}",
    "target_knowledge_points": {json.dumps(list(knowledge_points), ensure_ascii=False)},
    "questions": [
        {{
            "id": 1,
            "level": "基础巩固",
            "level_en": "basic",
            "question": "题目内容",
            "options": ["A. xxx", "B. xxx", "C. xxx", "D. xxx"],
            "answer": "参考答案",
            "solution": "详细解题思路和步骤",
            "knowledge_point": "考查知识点",
            "difficulty": 1
        }}
    ],
    "study_suggestions": "学习建议（针对学生的薄弱环节给出具体的学习建议）"
}}

注意：
- options 字段：选择题填写选项数组，非选择题填 null
- difficulty 字段：1-5 的整数
- level 字段只能是"基础巩固"、"能力提升"或"拓展挑战"之一"""

        try:
            full_response = ""
            stream = await self._create_stream(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "32768"))
            )

            yielded_generating = False
            finish_reason = None
            async for chunk in stream:
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    if chunk.choices[0].delta.content:
                        text = chunk.choices[0].delta.content
                        full_response += text
                        yield {"type": "content", "data": text}

                        if not yielded_generating and '"questions"' in full_response:
                            yield {"type": "thinking", "data": {"step": "design", "message": "🎯 题目设计完成", "status": "done"}}
                            yield {"type": "thinking", "data": {"step": "generate", "message": "✍️ 正在生成练习题...", "status": "active"}}
                            yielded_generating = True

            yield {"type": "thinking", "data": {"step": "generate", "message": "✍️ 练习题生成完成", "status": "done"}}

            # Parse result
            try:
                result = self._parse_json_response(full_response, truncated_hint=(finish_reason == "length"))
                yield {"type": "result", "data": result}
            except Exception as e:
                yield {"type": "error", "data": f"练习题生成结果解析失败：{e}（finish_reason={finish_reason}）"}

        except Exception as e:
            yield {"type": "error", "data": f"调用 LLM API 失败: {str(e)}"}


# Global instance
llm_service = LLMService()
