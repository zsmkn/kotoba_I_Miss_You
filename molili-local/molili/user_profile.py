"""使用者畫像隨新對話更新（原版火山引擎記憶庫的 profile_v1）。"""
from __future__ import annotations

import json
import re
import sqlite3

from .llm import LLM, LLMError
from .timeutil import iso, now

PROFILE_PROMPT = """你是使用者畫像整理助手。下面是「{user}」目前的畫像（JSON）和最近的新對話。
請輸出更新後的完整畫像 JSON：
- 保留原本的結構與分類（例如 基础信息、兴趣偏好、情感需求），需要時可以新增子分類
- 每個列表項目前面標記權重：[+] 一般、[++] 重要或多次提到、[-] 不喜歡或排斥
- 新資訊加進對應分類；和舊項目意思相同就把權重升為 [++]，不要重複新增
- 和舊資訊矛盾時以新對話為準（例如換了工作，就更新職業）
- 每個列表最多 40 項，超過時刪掉最不重要的
- 只根據{user}自己說的內容，不要從{name}的話臆測
只輸出 JSON，不要有其他文字。"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
MAX_TRANSCRIPT = 8000


def update_profile(conn: sqlite3.Connection, llm: LLM, *, user: str, name: str, since_id: int) -> int | None:
    """用 since_id 之後的對話更新畫像；成功回傳處理到的最後一則訊息 id。"""
    rows = conn.execute(
        "SELECT id, role, text FROM messages WHERE id > ? AND role IN ('user', 'assistant') ORDER BY id",
        (since_id,),
    ).fetchall()
    if not rows:
        return None
    transcript = "\n".join(f"{user if r['role'] == 'user' else name}：{r['text']}" for r in rows)[-MAX_TRANSCRIPT:]
    current = conn.execute("SELECT content FROM profile WHERE id = 1").fetchone()
    try:
        text = llm.complete([
            {"role": "system", "content": PROFILE_PROMPT.format(user=user, name=name)},
            {"role": "user", "content": f"目前的畫像：\n{current['content'] if current else '{}'}\n\n最近的對話：\n{transcript}"},
        ], json_mode=True)
    except LLMError:
        return None
    m = _JSON_OBJECT.search(text)
    try:
        profile = json.loads(m.group()) if m else None
    except json.JSONDecodeError:
        profile = None
    if not isinstance(profile, dict) or not profile:
        return None  # 格式不對就保留舊畫像，下次再試
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile(id, content, updated_at) VALUES (1, ?, ?)",
            (json.dumps(profile, ensure_ascii=False), iso(now())),
        )
    return rows[-1]["id"]
