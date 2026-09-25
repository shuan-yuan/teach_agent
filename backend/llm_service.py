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

    def _build_grading_prompt(self, subject: str = "") -> str:
        """构建批改 prompt 文本。

        subject 为空 = 「自动识别」：让模型自己判科，并把结果放进 JSON 的**第一个**
        字段。放第一位是有意的 —— 输出被长度上限截断时，正则仍能从残缺 JSON 里抠出它。
        """
        if subject:
            persona = f"你是一个专业的{subject}教师，请仔细批改这份学生作业。"
            subject_rule = f'- subject 固定填「{subject}」'
            subject_field = f'"subject": "{subject}",'
            steps = """1. 识别图片中的每一道题目和学生的解答
2. 逐题判断对错
3. 只对错误的题目给出错因和正确解法"""
        else:
            persona = ("你是一个专业的中小学教师，请仔细批改这份学生作业。"
                       "请先判断这份作业属于哪个科目。")
            subject_rule = ('- subject 填这份作业的科目，只能从「语文/数学/英语/物理/化学/生物」'
                            "中选一个；实在无法判断时填空字符串")
            subject_field = '"subject": "科目名称（语文/数学/英语/物理/化学/生物 之一）",'
            steps = """0. 判断科目（语文/数学/英语/物理/化学/生物 之一）
1. 识别图片中的每一道题目和学生的解答
2. 逐题判断对错
3. 只对错误的题目给出错因和正确解法"""
        return f"""{persona}

批改步骤：
{steps}

【输出长度要求 —— 非常重要，违反会导致 JSON 被截断而整份作废】
- 只输出一个 JSON 对象，不要任何解释文字，不要用 markdown 代码块
- 必须一次性输出**完整**的 JSON；题目较多时请把每项写得更精简，不要中途停下
- subject 必须是单个科目词，不要写理由、不要加括号说明
{subject_rule}
- question_text 只写题号和算式/关键条件，16 字以内，不要整段抄写题干
- is_correct 为 true 的题目：analysis 固定写「正确」，error_type 与 knowledge_point 写空字符串
- is_correct 为 false 的题目：analysis 控制在 50 字以内，只讲错因和正确解法
- overall_comment 不超过 60 字；weak_points 最多 3 项

JSON 格式：
{{
    {subject_field}
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

    # ──────────────────────────────────────────────────────────
    # 科目自动识别
    # ──────────────────────────────────────────────────────────
    # 科目是「每次作业的属性」。上传时模型还没看过内容，所以只能在批改结果里认。
    # 取值优先级：模型返回 > 本地关键词兜底 > 空串（前端显示「未识别」）。
    # 注意：真正的科目清单以 backend/database.py 的 SUBJECTS 为准，此处只做别名映射。

    _SUBJECT_ALIASES = {
        "语文": "语文", "chinese": "语文", "中文": "语文", "语文科": "语文", "国文": "语文",
        "数学": "数学", "math": "数学", "maths": "数学", "mathematics": "数学", "算术": "数学",
        "英语": "英语", "english": "英语", "英文": "英语", "英语科": "英语",
        "物理": "物理", "physics": "物理",
        "化学": "化学", "chemistry": "化学",
        "生物": "生物", "biology": "生物",
    }

    # 只在模型没给科目时兜底。宁可返回空串也不乱猜 —— 猜错比不猜更糟。
    _SUBJECT_KEYWORDS = {
        "英语": ["english", "he ", "she ", "the ", "___", "choose the best", "translate",
                 "reading comprehension", "past tense", "完形填空", "英译", "时态",
                 "单词", "选词填空", "短文改错"],
        "数学": ["计算", "解方程", "化简", "求值", "面积", "周长", "分数", "小数",
                 "应用题", "竖式", "余数", "倍数", "因数"],
        "语文": ["拼音", "组词", "造句", "古诗", "作者", "阅读短文", "作文", "笔画",
                 "反义词", "近义词", "标点", "文言文", "照样子写", "填空并解释"],
        "物理": ["压强", "密度", "电路", "欧姆", "浮力", "功率", "串联", "并联", "重力", "杠杆"],
        "化学": ["化学式", "化学方程式", "元素符号", "摩尔", "配平", "溶液", "氧化", "化合价"],
        "生物": ["细胞", "遗传", "光合作用", "生态系统", "染色体", "呼吸作用", "食物链"],
    }

    @classmethod
    def _guess_subject(cls, text: str) -> str:
        """模型没给科目时的本地关键词兜底 —— 宁可返回空串也不乱猜。"""
        if not text:
            return ""
        low = text.lower()
        scores = {s: sum(1 for k in kws if k in low)
                  for s, kws in cls._SUBJECT_KEYWORDS.items()}
        # 算式特征对数学是强信号
        if re.search(r"\d+\s*[+\-×÷*/=]\s*\d+", text):
            scores["数学"] = scores.get("数学", 0) + 2
        best = max(scores, key=lambda s: scores[s])
        if scores[best] < 2:
            return ""
        if list(scores.values()).count(scores[best]) > 1:   # 并列第一时不猜
            return ""
        return best

    @classmethod
    def _resolve_subject(cls, raw: str, hint_text: str = "") -> str:
        """从模型原始输出里认出科目名。

        刻意不看「解析后的 JSON」—— 输出被截断时 JSON 可能不完整，而 subject 被
        要求放在最前面，直接正则抠原文反而更稳。模型不配合时退到本地关键词兜底。
        """
        m = re.search(r'"subject"\s*:\s*"([^"]{1,24})"', raw or "")
        if m:
            name = re.sub(r"[(（].*", "", m.group(1)).strip()
            key = name.lower().replace(" ", "")
            got = cls._SUBJECT_ALIASES.get(key) or cls._SUBJECT_ALIASES.get(name)
            if got:
                return got
        return cls._guess_subject(hint_text or raw)

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

    @staticmethod
    def _extract_delta(delta) -> tuple:
        """
        从一个流式 delta 里取出（正式内容, 思考内容）。

        字段名各家不统一，且 openai SDK 对非标准字段会丢进 model_extra：
          正式内容：content
          思考内容：reasoning_content（DeepSeek / Qwen / GLM 等）
                    reasoning（部分厂商）

        只读 content 会漏掉「推理型模型把整个 max_tokens 预算烧在思考上」这种情形 ——
        现象是正文 0 字符、finish_reason 却是 length，用户只能看到一句没法诊断的报错。
        """
        def pick(*names):
            for n in names:
                v = getattr(delta, n, None)
                if isinstance(v, str) and v:
                    return v
            extra = getattr(delta, "model_extra", None) or {}
            for n in names:
                v = extra.get(n)
                if isinstance(v, str) and v:
                    return v
            return ""

        return pick("content"), pick("reasoning_content", "reasoning")

    @staticmethod
    def _describe_no_content(reasoning_text: str, finish_reason) -> str:
        """
        正文一个字都没有时的诊断文案。

        必须把「思考内容吃光了输出长度」和「模型压根没答/不支持读图」分开 ——
        两者的处置方式完全不同，给同一句话等于让用户自己猜。
        """
        if reasoning_text:
            head = reasoning_text[:160].replace("\n", " ")
            tail = reasoning_text[-160:].replace("\n", " ")
            return (
                f"模型只输出了思考过程（{len(reasoning_text)} 字符），没有输出正式结果。"
                f"原因：思考内容占满了本次输出长度上限（finish_reason={finish_reason}），"
                f"正文还没开始写就被截断了。"
                f"建议：换用不带「深度思考」的模型（或在模型设置里关闭思考模式），"
                f"也可以把作业拆成两次上传，减少单次需要输出的题量。"
                f"【思考开头】{head} … 【思考结尾】{tail}"
            )
        return (
            f"模型没有返回任何内容（finish_reason={finish_reason}）。"
            f"最常见的原因是所选模型不支持图片输入，或图片不清晰无法识别。"
            f"请确认该模型支持视觉识别，或改用「文字输入」方式提交作业。"
        )

    async def grade_homework(self, image_paths: list[str], subject: str = "") -> AsyncGenerator[dict, None]:
        """
        批改作业（视觉模式）- 流式返回思维链和结果

        subject 为空 = 自动识别：让模型先判科，结果随 result 一起返回，
        由调用方回写到作业记录（app.py 的 grade_homework 负责）。
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
            reasoning_text = ""
            async for chunk in stream:
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    text, thinking = self._extract_delta(chunk.choices[0].delta)
                    # 思考过程同样推给前端：推理型模型思考期可能长达几十秒，
                    # 不推的话用户全程只看到「等待智能体输出…」，像卡死
                    if thinking:
                        reasoning_text += thinking
                        yield {"type": "reasoning", "data": thinking}
                    if text:
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

            # 正文一个字都没有：先把原因说清楚再退出，
            # 不要把「原始输出 0 字符」这种没法诊断的文案丢给用户
            if not full_response.strip():
                yield {"type": "thinking", "data": {
                    "step": "grading",
                    "message": "📝 批改中断：模型没有输出批改结果",
                    "status": "done"
                }}
                yield {"type": "error", "data": self._describe_no_content(
                    reasoning_text, finish_reason)}
                return

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
                result["subject"] = subject or self._resolve_subject(full_response)
                if not subject:
                    yield {"type": "thinking", "data": {
                        "step": "subject",
                        "message": (f"🏷️ 识别科目：{result['subject']}" if result["subject"]
                                    else "🏷️ 科目未能识别，可在批改记录里确认"),
                        "status": "done"
                    }}
                yield {"type": "result", "data": result}
            except Exception as e:
                yield {"type": "error", "data": f"批改结果解析失败：{e}（finish_reason={finish_reason}）"}

        except Exception as e:
            yield {"type": "error", "data": f"调用 LLM API 失败: {str(e)}"}

    async def grade_homework_text(self, text: str, subject: str = "") -> AsyncGenerator[dict, None]:
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

        # Build text-only prompt（subject 为空 = 自动识别，理由同 _build_grading_prompt）
        if subject:
            persona = f"你是一个专业的{subject}教师，请仔细批改这份学生作业。"
            subject_rule = f'- subject 固定填「{subject}」'
            subject_field = f'"subject": "{subject}",'
            steps = """1. 仔细识别以上文本中的每一道题目和学生的解答
2. 逐题分析学生的解答过程和结果
3. 判断每道题的对错
4. 对错误的题目给出详细分析，指出错误原因和正确解法"""
        else:
            persona = ("你是一个专业的中小学教师，请仔细批改这份学生作业。"
                       "请先判断这份作业属于哪个科目。")
            subject_rule = ('- subject 填这份作业的科目，只能从「语文/数学/英语/物理/化学/生物」'
                            "中选一个；实在无法判断时填空字符串")
            subject_field = '"subject": "科目名称（语文/数学/英语/物理/化学/生物 之一）",'
            steps = """0. 判断科目（语文/数学/英语/物理/化学/生物 之一）
1. 仔细识别以上文本中的每一道题目和学生的解答
2. 逐题分析学生的解答过程和结果
3. 判断每道题的对错
4. 对错误的题目给出详细分析，指出错误原因和正确解法"""
        prompt = f"""{persona}

以下是学生的作业内容：
------
{text}
------

请按照以下步骤进行批改：
{steps}

{subject_rule}

请严格按照以下JSON格式返回批改结果（不要输出任何其他内容，只输出JSON）：
{{
    {subject_field}
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
            reasoning_text = ""
            async for chunk in stream:
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    text_chunk, thinking = self._extract_delta(chunk.choices[0].delta)
                    # 思考过程也推给前端，别让用户对着「等待智能体输出…」干等
                    if thinking:
                        reasoning_text += thinking
                        yield {"type": "reasoning", "data": thinking}
                    if text_chunk:
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

            # 正文为空时先给出可分辨的原因（思考烧光预算 / 模型没答），再收尾
            if not full_response.strip():
                yield {"type": "thinking", "data": {
                    "step": "grading",
                    "message": "📝 批改中断：模型没有输出批改结果",
                    "status": "done"
                }}
                yield {"type": "error", "data": self._describe_no_content(
                    reasoning_text, finish_reason)}
                return

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
                result["subject"] = subject or self._resolve_subject(full_response, hint_text=text)
                if not subject:
                    yield {"type": "thinking", "data": {
                        "step": "subject",
                        "message": (f"🏷️ 识别科目：{result['subject']}" if result["subject"]
                                    else "🏷️ 科目未能识别，可在批改记录里确认"),
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
            reasoning_text = ""
            async for chunk in stream:
                if chunk.choices:
                    if chunk.choices[0].finish_reason:
                        finish_reason = chunk.choices[0].finish_reason
                    text, thinking = self._extract_delta(chunk.choices[0].delta)
                    if thinking:
                        reasoning_text += thinking
                        yield {"type": "reasoning", "data": thinking}
                    if text:
                        full_response += text
                        yield {"type": "content", "data": text}

                        if not yielded_generating and '"questions"' in full_response:
                            yield {"type": "thinking", "data": {"step": "design", "message": "🎯 题目设计完成", "status": "done"}}
                            yield {"type": "thinking", "data": {"step": "generate", "message": "✍️ 正在生成练习题...", "status": "active"}}
                            yielded_generating = True

            # 正文为空的诊断与批改保持一致：思考烧光预算 / 模型没答，分开说
            if not full_response.strip():
                yield {"type": "thinking", "data": {"step": "generate", "message": "✍️ 练习生成中断：模型没有输出结果", "status": "done"}}
                yield {"type": "error", "data": self._describe_no_content(
                    reasoning_text, finish_reason)}
                return

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
