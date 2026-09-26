"""
数据库模块 - SQLite 数据库初始化和操作（多用户版）

数据隔离设计：
- users 是账号根表
- students 通过 user_id 挂载到用户，是每个用户的数据根表
- homework_submissions / error_records / practice_sheets 都通过 student_id
  关联到 students，所以只要在 students 上做 user_id 过滤，
  这些表就自动被隔离（查询一律 JOIN students 做越权校验）
"""
import aiosqlite
import os
import json
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "edu_agent.db")

SESSION_DAYS = 30

# 科目维度 —— 前后端唯一来源（前端对应 frontend/src/constants.ts，改动需同步）
SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物"]
SUBJECT_ALL = "全部"       # 前端「全部科目」视图的标识，不参与 SQL 筛选
SUBJECT_AUTO = "自动识别"   # 前端「让模型自己认」的标识；入库时存空串，批改完成后回写真实科目
DEFAULT_SUBJECT = "数学"


def normalize_subject(value) -> str:
    """把前端传来的科目值收敛成入库值。

    只有出现在 SUBJECTS 里的才算真实科目；「自动识别」「全部」以及任何未知值
    一律返回空串 = 「科目未定，等批改识别」。这样旧客户端或脏值不会污染科目维度。
    """
    v = (value or "").strip()
    return v if v in SUBJECTS else ""


async def init_db():
    """初始化数据库表结构"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- 每个用户独立的 API 配置（各自的 Key，互不消耗对方额度）
            CREATE TABLE IF NOT EXISTS user_api_config (
                user_id INTEGER PRIMARY KEY,
                endpoint TEXT NOT NULL DEFAULT '',
                api_key TEXT NOT NULL DEFAULT '',
                model_name TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 1,
                name TEXT NOT NULL,
                grade TEXT NOT NULL DEFAULT '',
                class_name TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '数学',
                avatar_color TEXT NOT NULL DEFAULT '#4F46E5',
                -- 默认学生：同一用户下最多一个。三个业务模块进入时自动选中它，
                -- 省掉每次都要在下拉框里挑一遍。
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS homework_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL DEFAULT '数学',
                image_paths TEXT NOT NULL DEFAULT '[]',
                grading_result TEXT DEFAULT '',
                thinking_chain TEXT DEFAULT '[]',
                score REAL DEFAULT 0,
                total_questions INTEGER DEFAULT 0,
                correct_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                file_type TEXT DEFAULT 'image',
                content_text TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS error_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                homework_id INTEGER NOT NULL,
                question_num INTEGER DEFAULT 0,
                question_text TEXT DEFAULT '',
                error_type TEXT DEFAULT '',
                knowledge_point TEXT DEFAULT '',
                student_answer TEXT DEFAULT '',
                correct_answer TEXT DEFAULT '',
                analysis TEXT DEFAULT '',
                difficulty INTEGER DEFAULT 3,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY (homework_id) REFERENCES homework_submissions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS practice_sheets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                title TEXT DEFAULT '',
                questions TEXT DEFAULT '[]',
                target_knowledge_points TEXT DEFAULT '[]',
                difficulty_level TEXT DEFAULT 'adaptive',
                pdf_path TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_students_user ON students(user_id);
            CREATE INDEX IF NOT EXISTS idx_homework_student ON homework_submissions(student_id);
            CREATE INDEX IF NOT EXISTS idx_errors_student ON error_records(student_id);
            CREATE INDEX IF NOT EXISTS idx_practice_student ON practice_sheets(student_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        ''')
        await db.commit()

        # ── 兼容旧库迁移 ──
        for stmt in (
            "ALTER TABLE students ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE homework_submissions ADD COLUMN file_type TEXT DEFAULT 'image'",
            "ALTER TABLE homework_submissions ADD COLUMN content_text TEXT DEFAULT ''",
            "ALTER TABLE practice_sheets ADD COLUMN subject TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE students ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                await db.execute(stmt)
                await db.commit()
            except Exception:
                pass  # 列已存在


async def get_db():
    """获取数据库连接"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


# ============ User & Session Operations ============

async def count_users() -> int:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as c FROM users")
        return (await cursor.fetchone())['c']
    finally:
        await db.close()


async def create_user(username: str, password_hash: str, display_name: str = "",
                      is_admin: bool = False) -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO users (username, password_hash, display_name, is_admin) VALUES (?, ?, ?, ?)",
            (username, password_hash, display_name or username, 1 if is_admin else 0)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_user_by_username(username: str):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_user_by_id(user_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def create_session(token: str, user_id: int):
    db = await get_db()
    try:
        expires = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires)
        )
        await db.commit()
    finally:
        await db.close()


async def get_session_user(token: str):
    """用 session token 换用户信息，过期/不存在返回 None"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT u.* FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, datetime.now().isoformat())
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def delete_session(token: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()
    finally:
        await db.close()


async def purge_expired_sessions():
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (datetime.now().isoformat(),))
        await db.commit()
    finally:
        await db.close()


# ============ Per-user API Config ============

async def get_config(user_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM user_api_config WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id, "endpoint": "", "api_key": "", "model_name": ""}
    finally:
        await db.close()


async def save_config(user_id: int, endpoint: str, api_key: str, model_name: str):
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR REPLACE INTO user_api_config
               (user_id, endpoint, api_key, model_name, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, endpoint, api_key, model_name, datetime.now().isoformat())
        )
        await db.commit()
    finally:
        await db.close()


# ============ Student Operations ============

async def get_students(user_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            # 默认学生排最前，其余按创建时间倒序 —— 列表顺序与卡片上的「默认」标记一致
            "SELECT * FROM students WHERE user_id = ? ORDER BY is_default DESC, created_at DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        students = []
        for row in rows:
            s = dict(row)
            cursor2 = await db.execute(
                "SELECT COUNT(*) as count, COALESCE(AVG(score), 0) as avg_score FROM homework_submissions WHERE student_id = ?",
                (s['id'],)
            )
            stats = dict(await cursor2.fetchone())
            s['homework_count'] = stats['count']
            s['avg_score'] = round(stats['avg_score'], 1)

            cursor3 = await db.execute(
                "SELECT COUNT(*) as count FROM error_records WHERE student_id = ?",
                (s['id'],)
            )
            s['error_count'] = (await cursor3.fetchone())['count']
            students.append(s)
        return students
    finally:
        await db.close()


async def create_student(user_id: int, name: str, grade: str, class_name: str, subject: str = "数学"):
    import random
    colors = ['#4F46E5', '#7C3AED', '#EC4899', '#EF4444', '#F97316', '#EAB308', '#22C55E', '#06B6D4', '#3B82F6']
    color = random.choice(colors)
    db = await get_db()
    try:
        # 该用户的第一个学生自动成为默认学生 —— 否则添加完还得再去点一次勾选
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM students WHERE user_id = ?", (user_id,)
        )
        is_first = (await cursor.fetchone())["n"] == 0
        cursor = await db.execute(
            "INSERT INTO students (user_id, name, grade, class_name, subject, avatar_color, is_default)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, grade, class_name, subject, color, 1 if is_first else 0)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_student(user_id: int, student_id: int, name: str, grade: str,
                         class_name: str, subject: str = "数学") -> bool:
    """更新学生 — 带 user_id 校验，越权返回 False"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE students SET name=?, grade=?, class_name=?, subject=? WHERE id=? AND user_id=?",
            (name, grade, class_name, subject, student_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def set_default_student(user_id: int, student_id: int, is_default: bool = True) -> bool:
    """设置 / 取消默认学生 —— 同一用户下最多一个。

    设为默认时先清空该用户其它学生的标记，保证唯一性。
    传 is_default=False 只取消这一个；全部取消后，三个业务模块回到「需手动选择」。
    越权或学生不存在返回 False。
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM students WHERE id=? AND user_id=?", (student_id, user_id)
        )
        if not await cursor.fetchone():
            return False
        if is_default:
            await db.execute("UPDATE students SET is_default=0 WHERE user_id=?", (user_id,))
        await db.execute(
            "UPDATE students SET is_default=? WHERE id=? AND user_id=?",
            (1 if is_default else 0, student_id, user_id)
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def delete_student(user_id: int, student_id: int) -> bool:
    """删除学生及其关联数据 — 带 user_id 校验"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM students WHERE id=? AND user_id=?", (student_id, user_id)
        )
        if not await cursor.fetchone():
            return False
        await db.execute("DELETE FROM error_records WHERE student_id=?", (student_id,))
        await db.execute("DELETE FROM homework_submissions WHERE student_id=?", (student_id,))
        await db.execute("DELETE FROM practice_sheets WHERE student_id=?", (student_id,))
        await db.execute("DELETE FROM students WHERE id=? AND user_id=?", (student_id, user_id))
        # 删掉的若是默认学生，把默认转给剩下的第一个 ——
        # 否则三个模块会突然全部变回「未选择」，用户以为设置丢了
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM students WHERE user_id=? AND is_default=1", (user_id,)
        )
        if (await cursor.fetchone())["n"] == 0:
            await db.execute(
                "UPDATE students SET is_default=1 WHERE id = ("
                "SELECT id FROM students WHERE user_id=? ORDER BY created_at DESC LIMIT 1)",
                (user_id,)
            )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_student(user_id: int, student_id: int):
    """获取学生 — 带 user_id 校验"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM students WHERE id = ? AND user_id = ?", (student_id, user_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ============ Homework Operations ============

async def delete_homework(user_id: int, homework_id: int) -> bool:
    """删除作业记录及其关联的错题 — 带 user_id 校验"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT h.id FROM homework_submissions h
               JOIN students s ON h.student_id = s.id
               WHERE h.id = ? AND s.user_id = ?""",
            (homework_id, user_id)
        )
        if not await cursor.fetchone():
            return False
        await db.execute("DELETE FROM error_records WHERE homework_id=?", (homework_id,))
        await db.execute("DELETE FROM homework_submissions WHERE id=?", (homework_id,))
        await db.commit()
        return True
    finally:
        await db.close()


async def create_homework_v2(user_id: int, student_id: int, subject: str, file_paths: list,
                             file_type: str = "image", content_text: str = ""):
    """创建作业记录 — 校验学生归属"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM students WHERE id = ? AND user_id = ?", (student_id, user_id)
        )
        if not await cursor.fetchone():
            return None
        cursor = await db.execute(
            """INSERT INTO homework_submissions
               (student_id, subject, image_paths, file_type, content_text, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (student_id, subject, json.dumps(file_paths), file_type, content_text)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_homework_result(homework_id: int, grading_result: str, thinking_chain: str,
                                  score: float, total_questions: int, correct_count: int):
    db = await get_db()
    try:
        await db.execute(
            """UPDATE homework_submissions
               SET grading_result=?, thinking_chain=?, score=?, total_questions=?, correct_count=?, status='completed'
               WHERE id=?""",
            (grading_result, thinking_chain, score, total_questions, correct_count, homework_id)
        )
        await db.commit()
    finally:
        await db.close()


async def update_homework_subject(homework_id: int, subject: str):
    """批改完成后回写识别出的科目。

    科目在上传时就写库了，那时模型还没看过作业内容 —— 所以「自动识别」模式
    必须在拿到批改结果后补一刀。只回写真科目，避免把空串覆盖掉手选值。
    """
    if subject not in SUBJECTS:
        return False
    db = await get_db()
    try:
        await db.execute(
            "UPDATE homework_submissions SET subject=? WHERE id=?",
            (subject, homework_id)
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_homework_list(user_id: int, student_id: int = None):
    """作业列表 — 只返回当前用户的学生作业"""
    db = await get_db()
    try:
        sql = """SELECT h.*, s.name as student_name
                 FROM homework_submissions h
                 JOIN students s ON h.student_id = s.id
                 WHERE s.user_id = ?"""
        params = [user_id]
        if student_id:
            sql += " AND h.student_id = ?"
            params.append(student_id)
        sql += " ORDER BY h.created_at DESC"
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_homework(user_id: int, homework_id: int):
    """作业详情 — 带 user_id 校验（含 file_type/content_text，批改时要用）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT h.*, s.name as student_name
               FROM homework_submissions h
               JOIN students s ON h.student_id = s.id
               WHERE h.id = ? AND s.user_id = ?""",
            (homework_id, user_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# ============ Error Record Operations ============

async def create_error_records(student_id: int, homework_id: int, errors: list):
    db = await get_db()
    try:
        for err in errors:
            await db.execute(
                """INSERT INTO error_records
                   (student_id, homework_id, question_num, question_text, error_type,
                    knowledge_point, student_answer, correct_answer, analysis, difficulty)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (student_id, homework_id, err.get('question_num', 0),
                 err.get('question_text', ''), err.get('error_type', ''),
                 err.get('knowledge_point', ''), err.get('student_answer', ''),
                 err.get('correct_answer', ''), err.get('analysis', ''),
                 err.get('difficulty', 3))
            )
        await db.commit()
    finally:
        await db.close()


def _subject_error_filter(subject: str) -> tuple:
    """错题按科目筛选的 SQL 片段。

    科目记在作业表上（homework_submissions.subject），错题通过 homework_id 继承，
    用 EXISTS 子查询就不必改动各处查询的 FROM/JOIN 结构。
    """
    if not subject:
        return "", ()
    return (" AND EXISTS (SELECT 1 FROM homework_submissions h"
            " WHERE h.id = error_records.homework_id AND h.subject = ?)", (subject,))


async def get_error_records(user_id: int, student_id: int, subject: str = ""):
    """错题记录 — 带 user_id 校验；subject 非空时只返回该科目"""
    db = await get_db()
    try:
        sql = """SELECT e.*, h.subject, h.created_at as homework_date
                 FROM error_records e
                 JOIN homework_submissions h ON e.homework_id = h.id
                 JOIN students s ON e.student_id = s.id
                 WHERE e.student_id = ? AND s.user_id = ?"""
        params: list = [student_id, user_id]
        if subject:
            sql += " AND h.subject = ?"
            params.append(subject)
        sql += " ORDER BY e.created_at DESC"
        cursor = await db.execute(sql, tuple(params))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_error_records_by_homework(user_id: int, homework_id: int):
    """某一次批改记录里的错题 —— 「按这次作业的错题出练习」的取数入口。

    归属校验直接写在 SQL 里（JOIN students s WHERE s.user_id = ?），
    调用方不必再判一次越权。按题号升序，保证出题时范围稳定。
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT e.*, h.subject, h.created_at as homework_date
               FROM error_records e
               JOIN homework_submissions h ON e.homework_id = h.id
               JOIN students s ON e.student_id = s.id
               WHERE e.homework_id = ? AND s.user_id = ?
               ORDER BY e.question_num ASC, e.id ASC""",
            (homework_id, user_id)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_error_stats(user_id: int, student_id: int, subject: str = ""):
    """获取学生错题统计 — 带 user_id 校验；subject 非空时统计范围限定在该科目"""
    db = await get_db()
    try:
        owned = await db.execute(
            "SELECT id FROM students WHERE id = ? AND user_id = ?", (student_id, user_id)
        )
        if not await owned.fetchone():
            return {"by_knowledge_point": [], "by_error_type": [], "by_difficulty": []}

        sub_sql, sub_params = _subject_error_filter(subject)

        cursor = await db.execute(
            f"""SELECT knowledge_point, COUNT(*) as count
                FROM error_records WHERE student_id = ? AND knowledge_point != ''{sub_sql}
                GROUP BY knowledge_point ORDER BY count DESC""",
            (student_id, *sub_params)
        )
        by_knowledge = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute(
            f"""SELECT error_type, COUNT(*) as count
                FROM error_records WHERE student_id = ? AND error_type != ''{sub_sql}
                GROUP BY error_type ORDER BY count DESC""",
            (student_id, *sub_params)
        )
        by_error_type = [dict(row) for row in await cursor.fetchall()]

        cursor = await db.execute(
            f"""SELECT difficulty, COUNT(*) as count
                FROM error_records WHERE student_id = ?{sub_sql}
                GROUP BY difficulty ORDER BY difficulty""",
            (student_id, *sub_params)
        )
        by_difficulty = [dict(row) for row in await cursor.fetchall()]

        return {
            "by_knowledge_point": by_knowledge,
            "by_error_type": by_error_type,
            "by_difficulty": by_difficulty
        }
    finally:
        await db.close()


async def get_subject_overview(user_id: int, student_id: int) -> list:
    """该学生各科目的作业数 / 错题数 / 练习数 —— 前端科目切换器用。

    科目取自「作业表里真实出现过的 subject」∪「已有练习记录里带的 subject」，
    后者不能漏，否则会出现「有练习历史但 Tab 里找不到该科目」。
    排序按 SUBJECTS 顺序，未知科目（含旧数据的空科目）排最后。
    """
    db = await get_db()
    try:
        owned = await db.execute(
            "SELECT id FROM students WHERE id = ? AND user_id = ?", (student_id, user_id)
        )
        if not await owned.fetchone():
            return []

        cursor = await db.execute(
            """SELECT h.subject as subject,
                      COUNT(DISTINCT h.id) as homework_count,
                      COUNT(e.id) as error_count
               FROM homework_submissions h
               LEFT JOIN error_records e ON e.homework_id = h.id
               WHERE h.student_id = ?
               GROUP BY h.subject""",
            (student_id,)
        )
        merged: dict = {}
        for r in (dict(x) for x in await cursor.fetchall()):
            subj = (r.get("subject") or "").strip() or "未分类"
            merged[subj] = {
                "subject": subj,
                "homework_count": r["homework_count"],
                "error_count": r["error_count"],
                "practice_count": 0,
            }

        cursor = await db.execute(
            """SELECT subject, COUNT(*) as c FROM practice_sheets
               WHERE student_id = ? AND subject != '' GROUP BY subject""",
            (student_id,)
        )
        for r in (dict(x) for x in await cursor.fetchall()):
            merged.setdefault(r["subject"], {
                "subject": r["subject"], "homework_count": 0,
                "error_count": 0, "practice_count": 0,
            })
            merged[r["subject"]]["practice_count"] = r["c"]

        order = {s: i for i, s in enumerate(SUBJECTS)}
        return sorted(merged.values(),
                      key=lambda x: (order.get(x["subject"], 99), x["subject"]))
    finally:
        await db.close()


# ============ Student Profile ============

async def get_student_profile(user_id: int, student_id: int) -> dict:
    """获取学生完整数据画像 — 带 user_id 校验"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM students WHERE id = ? AND user_id = ?", (student_id, user_id)
        )
        student_row = await cursor.fetchone()
        if not student_row:
            return {}
        basic_info = dict(student_row)

        cursor = await db.execute(
            """SELECT COUNT(*) as total,
                      COALESCE(AVG(CASE WHEN status='completed' THEN score END), 0) as avg_score
               FROM homework_submissions WHERE student_id = ?""",
            (student_id,)
        )
        hw_stats = dict(await cursor.fetchone())

        cursor = await db.execute(
            """SELECT score, created_at FROM homework_submissions
               WHERE student_id = ? AND status = 'completed'
               ORDER BY created_at DESC LIMIT 5""",
            (student_id,)
        )
        recent_rows = await cursor.fetchall()
        recent_scores = [{"score": dict(r)['score'], "date": dict(r)['created_at']} for r in recent_rows]
        recent_scores.reverse()

        cursor = await db.execute(
            """SELECT knowledge_point, COUNT(*) as count
               FROM error_records WHERE student_id = ? AND knowledge_point != ''
               GROUP BY knowledge_point ORDER BY count DESC""",
            (student_id,)
        )
        error_knowledge_distribution = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT error_type, COUNT(*) as count
               FROM error_records WHERE student_id = ? AND error_type != ''
               GROUP BY error_type ORDER BY count DESC""",
            (student_id,)
        )
        error_type_distribution = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM practice_sheets WHERE student_id = ?",
            (student_id,)
        )
        practice_count = (await cursor.fetchone())['count']

        return {
            "basic_info": basic_info,
            "total_homeworks": hw_stats['total'],
            "avg_score": round(hw_stats['avg_score'], 1),
            "recent_scores": recent_scores,
            "error_knowledge_distribution": error_knowledge_distribution,
            "error_type_distribution": error_type_distribution,
            "practice_count": practice_count
        }
    finally:
        await db.close()


# ============ Practice Sheet Operations ============

async def create_practice_sheet(user_id: int, student_id: int, title: str, questions: str,
                                 target_knowledge_points: str, pdf_path: str = "",
                                 subject: str = ""):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM students WHERE id = ? AND user_id = ?", (student_id, user_id)
        )
        if not await cursor.fetchone():
            return None
        cursor = await db.execute(
            """INSERT INTO practice_sheets
               (student_id, subject, title, questions, target_knowledge_points, pdf_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (student_id, subject, title, questions, target_knowledge_points, pdf_path)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_practice_sheets(user_id: int, student_id: int = None, subject: str = ""):
    """练习列表 — 只返回当前用户的数据；subject 非空时只返回该科目"""
    db = await get_db()
    try:
        sql = """SELECT p.*, s.name as student_name
                 FROM practice_sheets p
                 JOIN students s ON p.student_id = s.id
                 WHERE s.user_id = ?"""
        params = [user_id]
        if student_id:
            sql += " AND p.student_id = ?"
            params.append(student_id)
        if subject:
            sql += " AND p.subject = ?"
            params.append(subject)
        sql += " ORDER BY p.created_at DESC"
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_practice_sheet(user_id: int, practice_id: int):
    """单个练习 — 带 user_id 校验（下载 PDF 用）"""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT p.* FROM practice_sheets p
               JOIN students s ON p.student_id = s.id
               WHERE p.id = ? AND s.user_id = ?""",
            (practice_id, user_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def update_practice_pdf_path(practice_id: int, pdf_path: str):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE practice_sheets SET pdf_path = ? WHERE id = ?", (pdf_path, practice_id)
        )
        await db.commit()
    finally:
        await db.close()


# ============ Dashboard Stats ============

async def get_dashboard_stats(user_id: int):
    """仪表盘统计 — 只统计当前用户的数据"""
    db = await get_db()
    try:
        stats = {}

        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM students WHERE user_id = ?", (user_id,)
        )
        stats['total_students'] = (await cursor.fetchone())['count']

        cursor = await db.execute(
            """SELECT COUNT(*) as count FROM homework_submissions h
               JOIN students s ON h.student_id = s.id WHERE s.user_id = ?""",
            (user_id,)
        )
        stats['total_homeworks'] = (await cursor.fetchone())['count']

        cursor = await db.execute(
            """SELECT COUNT(*) as count FROM error_records e
               JOIN students s ON e.student_id = s.id WHERE s.user_id = ?""",
            (user_id,)
        )
        stats['total_errors'] = (await cursor.fetchone())['count']

        cursor = await db.execute(
            """SELECT COUNT(*) as count FROM practice_sheets p
               JOIN students s ON p.student_id = s.id WHERE s.user_id = ?""",
            (user_id,)
        )
        stats['total_practices'] = (await cursor.fetchone())['count']

        cursor = await db.execute(
            """SELECT COALESCE(AVG(h.score), 0) as avg FROM homework_submissions h
               JOIN students s ON h.student_id = s.id
               WHERE s.user_id = ? AND h.status='completed'""",
            (user_id,)
        )
        stats['avg_score'] = round((await cursor.fetchone())['avg'], 1)

        cursor = await db.execute(
            """SELECT h.id, h.score, h.status, h.created_at, s.name as student_name, 'homework' as type
               FROM homework_submissions h
               JOIN students s ON h.student_id = s.id
               WHERE s.user_id = ?
               ORDER BY h.created_at DESC LIMIT 10""",
            (user_id,)
        )
        stats['recent_activities'] = [dict(row) for row in await cursor.fetchall()]

        return stats
    finally:
        await db.close()
