"""對話引擎：組上下文 → 呼叫 LLM → 解析 → 存檔 → 背景整理記憶。"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .config import Config
from .diary import missing_dates, write_diary
from .images import ImageError, generate_image
from .importer import assistant_parts
from .live2d import MODEL_DIR_NAME, REFERENCE_IMAGE
from .llm import LLM, LLMError
from .memory import MemoryIndex
from .prompt import EXPRESSIONS, PromptContext, build_system_prompt
from .search import format_results, web_search
from .textconv import localize
from .timeutil import describe, humanize, iso, now, parse, period
from .user_profile import update_profile
from .wardrobe import load_outfit

# 會放進 LLM 上下文的訊息來源（系統通知如「好感度 +3」不放）
CONTEXT_SOURCES = ("chat", "autoTask", "giftReply")
MAX_LEVEL = 100
TOUCH_ACTIONS = {
    "head": "輕輕摸了摸你的頭",
    "face": "捏了捏你的臉頰",
    "arm": "輕輕碰了碰你的手",
    "body": "戳了戳你",
    "leg": "碰了碰你的腿",
}
TASK_NAMES = {"work": "工作", "study": "學習", "rest": "休息"}
TASK_COMPANY = {
    "work": "你也在旁邊用打字機整理論文陪著",
    "study": "你也在旁邊看書陪著一起念",
    "rest": "你也打算一起小睡一下",
}
TASK_DONE = {"work": "工作完成，一起努力了 {minutes} 分鐘", "study": "學習結束，一起念了 {minutes} 分鐘",
             "rest": "休息好了"}
FILM_DAILY = 3      # 以太膠片每天補充到幾捲（原版要在商店買，本地版改成每日補給）
IMAGE_DIR = "images"

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

SUMMARY_PROMPT = (
    "你是記憶整理助手。把下面這段對話整理成一則第三人稱的事件記憶摘要：1～3 句、繁體中文，"
    "開頭寫出日期與時段（例如「2026年8月2日下午，」），記錄{user}說了什麼、{name}如何回應，"
    "以及可以推測的{user}近況或偏好。只輸出摘要本身。"
)


@dataclass
class FavorChange:
    gained: int
    level: int
    leveled_up: bool


@dataclass
class Reply:
    messages: list[str]
    expression: str = "DEFAULT"
    mood: str = ""
    reasoning: str = ""
    raw: str = ""
    suggestions: list[str] = field(default_factory=list)
    favor: FavorChange | None = None
    memories_used: list[str] = field(default_factory=list)
    search: str = ""                 # 模型要求搜尋的關鍵字
    photo: str = ""                  # 模型要求拍的畫面描述
    searched: int = 0                # 實際查了幾個網頁
    image_path: Path | None = None   # 拍好的照片
    notices: list[str] = field(default_factory=list)  # 這輪附帶的系統通知（升級、陪伴結算等）


def next_level_cost(level: int) -> int:
    """升到下一級需要的好感值。原版主面板在 Lv.20 顯示「1/165」，與 8×等級＋5 吻合。"""
    return 8 * level + 5


def parse_reply(text: str) -> Reply:
    """解析 LLM 輸出；格式不對時盡量救回內容，而不是整輪失敗。"""
    m = _JSON_OBJECT.search(text)
    data = None
    if m:
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return Reply(messages=lines or ["……"], raw=text)

    msgs = data.get("messages")
    if isinstance(msgs, str):
        msgs = [msgs]
    if isinstance(msgs, list):
        parsed = assistant_parts(json.dumps({"messages": msgs}, ensure_ascii=False))
        messages = parsed["text"].splitlines() if parsed else []
    else:
        messages = []
    messages = [s.strip() for s in messages if s and s.strip()] or ["……"]

    expression = str(data.get("expression", "DEFAULT")).strip().upper()
    if expression not in EXPRESSIONS:
        expression = "DEFAULT"
    suggestions = data.get("suggestions")
    suggestions = [str(s).strip() for s in suggestions if str(s).strip()][:3] if isinstance(suggestions, list) else []
    return Reply(
        messages=messages,
        expression=expression,
        mood=str(data.get("mood", "")),
        reasoning=str(data.get("reasoning", "")),
        raw=text,
        suggestions=suggestions,
        search=_optional_text(data.get("search")),
        photo=_optional_text(data.get("photo")),
    )


def _optional_text(value) -> str:
    """選填欄位：小模型常把「不需要」寫成空字串、null、「無」，或把格式說明照抄進來，都當作沒填。"""
    text = str(value or "").strip()
    if text.lower() in ("", "none", "null", "無", "无", "否") or "選填" in text or "选填" in text:
        return ""
    return text


class Companion:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, llm: LLM):
        self.cfg = cfg
        self.conn = conn
        self.llm = llm
        self.memory = MemoryIndex(conn)
        self.agent_name = cfg.chat.agent_name
        self.user_name = db.get_meta(conn, "user_nickname", "主人") or "主人"
        role_prompt = db.get_persona(conn, "role_prompt")
        if not role_prompt:
            raise RuntimeError("資料庫裡沒有人設，請先執行 `python -m molili import <官方 ZIP>`")
        self.role_prompt = role_prompt
        self._bg: list[threading.Thread] = []
        self._profile_thread: threading.Thread | None = None

    # ---- 讀取狀態 -------------------------------------------------------

    def recent_messages(self, limit: int) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            f"""SELECT * FROM messages
                WHERE role IN ('user', 'assistant') AND source IN ({",".join("?" * len(CONTEXT_SOURCES))})
                ORDER BY id DESC LIMIT ?""",
            (*CONTEXT_SOURCES, limit),
        ).fetchall()
        return rows[::-1]

    def last_seen(self) -> str | None:
        """主人最後一次說話的時間：決定要不要主動問候。"""
        row = self.conn.execute(
            "SELECT created_at FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["created_at"] if row else None

    def last_activity(self) -> str | None:
        """雙方最後一則訊息的時間：給 prompt 判斷「距離上次對話多久」。
        若只看主人的訊息，她主動問候後下一輪仍會以為隔了很久，又把問候重講一遍。"""
        row = self.conn.execute(
            f"""SELECT created_at FROM messages
                WHERE role IN ('user', 'assistant') AND source IN ({",".join("?" * len(CONTEXT_SOURCES))})
                ORDER BY id DESC LIMIT 1""",
            CONTEXT_SOURCES,
        ).fetchone()
        return row["created_at"] if row else None

    def favor(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM favor WHERE id = 1").fetchone()

    # ---- 組上下文 -------------------------------------------------------

    def build_context(self, query: str, situation: str = "") -> tuple[str, list[dict], list[str]]:
        history = self.recent_messages(self.cfg.chat.history_messages)
        before = history[0]["created_at"] if history else None
        recent_text = " ".join(r["text"] for r in history[-2:])
        memories = self.memory.search(f"{query} {recent_text}", self.cfg.chat.memory_top_k, before=before)

        facts = [r["summary"] for r in self.conn.execute("SELECT summary FROM memories WHERE kind = 'fact' ORDER BY created_at")]
        profile = self.conn.execute("SELECT content FROM profile WHERE id = 1").fetchone()
        diary = self.conn.execute("SELECT date, content FROM diaries ORDER BY date DESC LIMIT 1").fetchone()
        favor = self.favor()
        last_seen = self.last_activity()

        system = build_system_prompt(PromptContext(
            agent_name=self.agent_name,
            user_name=self.user_name,
            role_prompt=self.role_prompt,
            language=self.cfg.chat.language,
            now=now(),
            facts=facts,
            profile_json=profile["content"] if profile else None,
            memories=memories,
            diary=(diary["date"], diary["content"]) if diary else None,
            last_seen=parse(last_seen) if last_seen else None,
            favor_level=favor["level"] if favor else None,
            suggestions=self.cfg.chat.suggestions,
            situation=situation,
            web_search=self.cfg.chat.web_search,
            films=self.films(),
            appearance=load_outfit(self.conn).describe(),
        ))

        # 合併連續同角色訊息、並確保第一則是 user：部分模型（Gemini、Gemma 範本）要求嚴格交替
        turns: list[dict] = []
        for r in history:
            if turns and turns[-1]["role"] == r["role"]:
                turns[-1]["content"] += "\n" + r["text"]
            else:
                turns.append({"role": r["role"], "content": r["text"]})
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        return system, turns, [m.summary for m in memories]

    # ---- 對話 -----------------------------------------------------------

    def _complete(self, system: str, turns: list[dict]) -> Reply:
        text = self.llm.complete([{"role": "system", "content": system}, *turns], json_mode=True)
        if not text.strip():
            raise LLMError("模型回傳了空白內容")
        r = parse_reply(text)
        lang = self.cfg.chat.language
        r.messages = [localize(m, lang) for m in r.messages]
        r.suggestions = [localize(s, lang) for s in r.suggestions]
        r.mood = localize(r.mood, lang)
        return r

    def reply(self, user_text: str, situation: str = "") -> Reply:
        user_text = user_text.strip()
        system, turns, used = self.build_context(user_text, situation)
        if turns and turns[-1]["role"] == "user":
            turns[-1]["content"] += "\n" + user_text
        else:
            turns.append({"role": "user", "content": user_text})

        result = self._finalize(system, turns, self._complete(system, turns))
        result.memories_used = used

        ts = iso(now())
        with self.conn:
            self.conn.execute(
                "INSERT INTO messages(created_at, role, source, text) VALUES (?, 'user', 'chat', ?)",
                (ts, user_text),
            )
            if result.searched:
                self._notify(self.search_notice(result.searched), ts)
            self._save_assistant(result, "chat", ts)
            self._apply_favor(result, self.cfg.chat.favor_per_message, ts)
        if self.cfg.chat.summarize_memory:
            self._remember_async(ts, user_text, result)
        self._maybe_update_profile()
        return result

    def greet(self) -> Reply | None:
        """隔了一段時間才上線時，讓她主動開口（對應原版的 autoTask）。"""
        last = self.last_seen()
        if not last:
            return None
        gap = now() - parse(last)
        if gap.total_seconds() < self.cfg.chat.greet_after_hours * 3600:
            return None
        return self._event_reply(
            f"{self.user_name}隔了{humanize(gap)}，在{period(now())}重新打開通訊器上線了。"
            f"請你主動開口打招呼，可以自然地提起你們上次聊到的事、或你這段時間在做什麼。"
        )

    def touch(self, area: str, situation: str = "") -> Reply:
        """被摸頭、捏臉等觸摸互動的反應（對應原版 Unity 的 IA_HEAD / IA_FACE / IA_ARM / IA_BODY / IA_LEG 觸摸區）。"""
        action = TOUCH_ACTIONS.get(area, TOUCH_ACTIONS["body"])
        return self._event_reply(
            f"{self.user_name}透過通訊器的觸感回饋{action}。"
            "請用一到兩則很短的訊息做出符合目前好感階段的即時反應。",
            situation,
        )

    def idle_chat(self, situation: str = "") -> Reply:
        """主人在電腦前但一段時間沒說話時，她自己找話題（原版 autoTask 也會這樣主動搭話）。"""
        last = self.last_activity()
        gap = humanize(now() - parse(last)) if last else "一陣子"
        return self._event_reply(
            f"{self.user_name}正在電腦前忙自己的事，已經{gap}沒有跟你說話了。"
            f"請你主動、自然地開啟一個輕鬆的小話題：可以分享你正在做的事、森林裡的小發現，或關心{self.user_name}，"
            f"一到兩則短訊息。不要責怪{self.user_name}沒理你，也不要每次都問對方在忙什麼。",
            situation,
        )

    def _event_reply(self, event: str, situation: str = "") -> Reply:
        """由系統事件（而不是主人說話）觸發的回覆；事件本身不存檔，只存她的回應。"""
        system, turns, used = self.build_context(event, situation)
        event = f"（系統事件，不是{self.user_name}說的話）{event}"
        if turns and turns[-1]["role"] == "user":
            turns[-1]["content"] += "\n" + event
        else:
            turns.append({"role": "user", "content": event})
        result = self._finalize(system, turns, self._complete(system, turns), allow_search=False)
        result.memories_used = used
        with self.conn:
            self._save_assistant(result, "autoTask", iso(now()))
        return result

    def companion_task(self, kind: str, phase: str, minutes: int, situation: str = "") -> Reply:
        """番茄鐘陪伴（原版的工作／學習／休息任務）：開始、中場休息、結束時她各說幾句；結束時加好感。"""
        name = TASK_NAMES.get(kind, kind)
        length = f"{minutes} 分鐘的" if minutes else "不限時的"
        with_her = TASK_COMPANY.get(kind, "")
        events = {
            "start": f"{self.user_name}開始了{length}{name}，{with_her}。請用一兩則短訊息替{self.user_name}打氣。",
            "break": f"{self.user_name}專心{name}了 {minutes} 分鐘，現在是中場休息。請用一兩則短訊息慰勞，提醒起來動一動或喝水。",
            "resume": f"休息結束，{self.user_name}要繼續{name}了。用一則短訊息打氣。",
            "end": f"{self.user_name}結束了{name}，這次一共 {minutes} 分鐘。請用一兩則短訊息稱讚或關心{self.user_name}。",
        }
        result = self._event_reply(events.get(phase, events["start"]), situation)
        if phase == "end":
            gained = 0 if kind == "rest" else max(minutes // 10, 1 if minutes >= 5 else 0)
            ts = iso(now())
            with self.conn:
                self.conn.execute(
                    "INSERT INTO companion_tasks(kind, started_at, ended_at, minutes) VALUES (?, ?, ?, ?)",
                    (kind, ts, ts, minutes),
                )
                summary = TASK_DONE.get(kind, "陪伴結束").format(minutes=minutes)
                if gained:
                    summary += f"，好感度 +{gained}"
                self._notify(summary, ts)
                result.notices.append(summary)
                self._apply_favor(result, gained, ts)
        return result

    def _finalize(self, system: str, turns: list[dict], r: Reply, *, allow_search: bool = True) -> Reply:
        """處理模型在 JSON 裡提出的 search（搜尋後重答）與 photo（拍照）。"""
        if r.search and allow_search and self.cfg.chat.web_search:
            r = self._answer_with_search(system, turns, r)
        if r.photo and (self.films() or 0) > 0:
            self._take_photo(r)
        return r

    def _answer_with_search(self, system: str, turns: list[dict], first: Reply) -> Reply:
        try:
            results = web_search(first.search, self.cfg.chat.language)
        except Exception:  # 網路問題、DuckDuckGo 限流等；不要讓整輪對話失敗
            results = []
        found = format_results(results) if results else "（通信器收不到訊號，沒有查到任何資料）"
        followup = [
            *turns,
            {"role": "assistant", "content": first.raw},
            {"role": "user", "content": (
                f"（系統事件，不是{self.user_name}說的話）你用通信器搜尋了「{first.search}」，結果如下：\n{found}\n"
                f"請根據這些資料，用你自己的口吻回答{self.user_name}剛剛的問題，可以提到資料來自哪個網站。"
                "查不到就老實說。這次不要再填 search。"
            )},
        ]
        second = self._complete(system, followup)
        second.searched = len(results)
        if not second.photo:
            second.photo = first.photo
        return second

    def films(self) -> int | None:
        """剩餘以太膠片；None 表示沒有相機或沒設定生圖服務。每天補充到 FILM_DAILY 捲。"""
        if self.cfg.image.provider == "none":
            return None
        if not self.conn.execute("SELECT 1 FROM items WHERE code = 'EtherCamera'").fetchone():
            return None
        today = now().date().isoformat()
        row = self.conn.execute("SELECT quantity FROM items WHERE code = 'EtherFilm'").fetchone()
        quantity = row["quantity"] if row else 0
        if db.get_meta(self.conn, "film_refill_date") != today:
            with self.conn:
                if quantity < FILM_DAILY:
                    quantity = FILM_DAILY
                    self.conn.execute(
                        """INSERT INTO items(code, name, category, quantity, description, effects)
                           VALUES ('EtherFilm', '以太胶片', 'function', ?, '可以直接用肉眼觀察成像的以太胶片。', '[]')
                           ON CONFLICT(code) DO UPDATE SET quantity = excluded.quantity""",
                        (quantity,),
                    )
                db.set_meta(self.conn, "film_refill_date", today)
        return quantity

    def _take_photo(self, r: Reply) -> None:
        reference = Path(self.cfg.data_dir) / "live2d" / MODEL_DIR_NAME / REFERENCE_IMAGE
        try:
            data = generate_image(self.cfg.image, r.photo, reference)
        except ImageError as e:
            r.messages.append(localize("（相機好像卡住了，照片沒有洗出來……）", self.cfg.chat.language))
            r.notices.append(f"拍照失敗：{e}")
            return
        images = Path(self.cfg.data_dir) / IMAGE_DIR
        images.mkdir(parents=True, exist_ok=True)
        path = images / f"{now():%Y%m%d_%H%M%S}.png"
        path.write_bytes(data)
        r.image_path = path
        with self.conn:
            self.conn.execute("UPDATE items SET quantity = MAX(quantity - 1, 0) WHERE code = 'EtherFilm'")

    def _save_assistant(self, r: Reply, source: str, ts: str) -> None:
        text = "\n".join(r.messages)
        attachment = None
        if r.image_path:
            text += f"\n[傳了一張照片：{r.photo[:60]}]"
            attachment = r.image_path.relative_to(Path(self.cfg.data_dir)).as_posix()
        self.conn.execute(
            """INSERT INTO messages(created_at, role, source, text, expression, mood, reasoning, model, raw, attachment)
               VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, source, text, r.expression, r.mood, r.reasoning, self.llm.model, r.raw, attachment),
        )

    def search_notice(self, pages: int) -> str:
        # 原版的卡片文字是「言葉 瀏覽了 8 個頁面並思考了 28 秒！」
        return f"{self.agent_name} 用通信器查了 {pages} 個網頁"

    def _notify(self, text: str, ts: str) -> None:
        """在對話紀錄留下通知卡（和原版一樣）。"""
        self.conn.execute(
            "INSERT INTO messages(created_at, role, source, text) VALUES (?, 'system', 'notify', ?)", (ts, text)
        )

    def _apply_favor(self, r: Reply, amount: int, ts: str) -> None:
        r.favor = self._gain_favor(amount)
        if r.favor and r.favor.leveled_up:
            text = f"{self.agent_name}的好感度等級提升到 lv.{r.favor.level}"
            self._notify(text, ts)
            r.notices.append(text)

    # ---- 日記與使用者畫像 ---------------------------------------------------

    def write_missing_diaries(self) -> list[str]:
        """補寫前幾天的日記；回傳寫好的日期。"""
        if not self.cfg.chat.diary:
            return []
        written = []
        for day in missing_dates(self.conn, now().date()):
            write_diary(self.conn, self.llm, day=day, role_prompt=self.role_prompt, name=self.agent_name,
                        user=self.user_name, language=self.cfg.chat.language)
            written.append(day.isoformat())
        return written

    def _maybe_update_profile(self) -> None:
        every = self.cfg.chat.profile_update_every
        if every <= 0 or self._profile_thread and self._profile_thread.is_alive():
            return
        since = int(db.get_meta(self.conn, "profile_msg_id") or 0)
        if since == 0:  # 第一次：從匯入的資料之後開始算
            since = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE source_id IS NOT NULL").fetchone()[0]
            with self.conn:
                db.set_meta(self.conn, "profile_msg_id", str(since))
        pending = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE id > ? AND role = 'user'", (since,)
        ).fetchone()[0]
        if pending >= every:
            self._profile_thread = threading.Thread(target=self._update_profile, args=(since,), daemon=True)
            self._profile_thread.start()
            self._bg.append(self._profile_thread)

    def _update_profile(self, since: int) -> None:
        conn = db.connect(Path(self.cfg.data_dir))
        try:
            last = update_profile(conn, self.llm, user=self.user_name, name=self.agent_name, since_id=since)
            if last:
                with conn:
                    db.set_meta(conn, "profile_msg_id", str(last))
        finally:
            conn.close()

    def _gain_favor(self, amount: int) -> FavorChange | None:
        f = self.favor()
        if f is None or amount <= 0:
            return None
        level, progress = f["level"], f["level_favor"] + amount
        leveled = False
        while level < MAX_LEVEL and progress >= next_level_cost(level):
            progress -= next_level_cost(level)
            level += 1
            leveled = True
        self.conn.execute(
            "UPDATE favor SET total = total + ?, level = ?, level_favor = ?, updated_at = ? WHERE id = 1",
            (amount, level, progress, iso(now())),
        )
        return FavorChange(gained=amount, level=level, leveled_up=leveled)

    # ---- 記憶寫回 --------------------------------------------------------

    def _remember_async(self, ts: str, user_text: str, r: Reply) -> None:
        dialog = f"{describe(parse(ts))}\n{self.user_name}：{user_text}\n{self.agent_name}：" + "\n".join(r.messages)
        t = threading.Thread(target=self._remember, args=(ts, dialog), daemon=True)
        t.start()
        self._bg = [x for x in self._bg if x.is_alive()] + [t]

    def _remember(self, ts: str, dialog: str) -> None:
        try:
            summary = self.llm.complete([
                {"role": "system", "content": SUMMARY_PROMPT.format(user=self.user_name, name=self.agent_name)},
                {"role": "user", "content": dialog},
            ]).strip()
        except LLMError:
            summary = ""
        summary = summary or dialog  # 摘要失敗就存原文，至少不遺失
        conn = db.connect(Path(self.cfg.data_dir))  # 背景執行緒用自己的連線
        try:
            with conn:
                cur = conn.execute(
                    "INSERT INTO memories(kind, created_at, summary, original) VALUES ('event', ?, ?, ?)",
                    (ts, summary, dialog),
                )
            self.memory.add(cur.lastrowid, ts, summary)
        finally:
            conn.close()

    def wait_background(self, timeout: float = 60) -> None:
        for t in self._bg:
            t.join(timeout)
