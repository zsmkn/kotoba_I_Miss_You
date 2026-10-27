"""每日日記：補寫前幾天還沒寫的日記（原版每天都會寫一篇，並在對話裡通知「言葉 寫了一篇關於你的日記」）。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .llm import LLM
from .prompt import LANGUAGE_RULES, favor_stage
from .textconv import localize
from .timeutil import WEEKDAYS, iso, now

MAX_BACKFILL_DAYS = 3        # 隔很久才打開時，只補最近幾天，不一次生出一大堆
MAX_TRANSCRIPT = 6000
STYLE_SAMPLE = 700

DIARY_PROMPT = """{role_prompt}

---

# 任務：寫日記
你是{name}，請寫 {day} 的日記。
- 第一人稱、像寫給自己看的日記，比聊天更細膩，約 500～800 字，分成幾個段落
- 寫下這一天在森林與觀測站發生的事、你的研究、心情
- 如果這天有和{user}說話，要寫到你們聊了什麼、你當時的感受（日記是私密的，可以比聊天更坦率）
- 如果這天沒有和{user}說話，就寫你自己的一天，也可以寫你在想{user}
- 目前好感度等級 {level}，處於{stage}，心情與親密程度要符合這個階段
- 不要用 markdown 標題或列表，不要寫日期標題
- {language}"""


def missing_dates(conn: sqlite3.Connection, today: date) -> list[date]:
    row = conn.execute("SELECT MAX(date) AS last FROM diaries").fetchone()
    start = today - timedelta(days=MAX_BACKFILL_DAYS)
    if row and row["last"]:
        start = max(start, date.fromisoformat(row["last"]) + timedelta(days=1))
    return [start + timedelta(days=i) for i in range((today - start).days)]


def write_diary(conn: sqlite3.Connection, llm: LLM, *, day: date, role_prompt: str, name: str, user: str,
                language: str) -> str:
    rows = conn.execute(
        "SELECT role, text FROM messages WHERE substr(created_at, 1, 10) = ? AND role IN ('user', 'assistant') ORDER BY id",
        (day.isoformat(),),
    ).fetchall()
    transcript = "\n".join(f"{user if r['role'] == 'user' else name}：{r['text']}" for r in rows)[-MAX_TRANSCRIPT:]
    samples = conn.execute("SELECT date, content FROM diaries WHERE date < ? ORDER BY date DESC LIMIT 2",
                           (day.isoformat(),)).fetchall()
    favor = conn.execute("SELECT level FROM favor WHERE id = 1").fetchone()
    level = favor["level"] if favor else 1

    system = DIARY_PROMPT.format(
        role_prompt=role_prompt, name=name, user=user, level=level, stage=favor_stage(level),
        day=f"{day.isoformat()}（週{WEEKDAYS[day.weekday()]}）",
        language=LANGUAGE_RULES.get(language, LANGUAGE_RULES["zh-TW"]),
    )
    parts = []
    if samples:
        parts.append("你以前寫的日記（參考文風就好，不要重複內容）：\n" + "\n\n".join(
            f"【{s['date']}】\n{s['content'][:STYLE_SAMPLE]}" for s in samples))
    parts.append(f"這天和{user}的對話：\n{transcript}" if transcript else f"這天沒有和{user}說話。")
    text = llm.complete([{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}])
    content = localize(text.strip(), language)
    with conn:
        conn.execute("INSERT OR REPLACE INTO diaries(date, content, created_at) VALUES (?, ?, ?)",
                     (day.isoformat(), content, iso(now())))
        conn.execute(
            "INSERT INTO messages(created_at, role, source, text) VALUES (?, 'system', 'notify', ?)",
            (iso(now()), f"{name} 寫了一篇關於你的日記"),
        )
    return content
