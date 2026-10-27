"""匯入官方「molili-user-data-export」封存檔（ZIP 或已解壓縮的資料夾）。

重複匯入是安全的：已存在的紀錄會略過，本地之後累積的好感度與記憶不會被蓋掉。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from .db import set_meta
from .timeutil import iso, now

EXPORT_FORMAT = "molili-user-data-export"

# 原始人設檔裡夾帶的模型標記，對 prompt 沒有意義
_MODEL_TAG = re.compile(r"\[deepseek\]")
# cognitive.md 的格式：「1. [重要设定]內容 (@2026-05-12 04:19:08)」
_FACT_LINE = re.compile(r"^\s*\d+\.\s*(?P<text>.+?)\s*\(@(?P<time>[\d\-: ]+)\)\s*$")
_TZ_SUFFIX = re.compile(r"([+-]\d{2}:\d{2}|Z)$")


class Archive:
    """把 ZIP 與資料夾包成同一個唯讀介面，路徑一律相對於 manifest.json 所在位置。"""

    def __init__(self, path: Path):
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        if path.is_dir():
            if not (path / "manifest.json").is_file():
                raise ValueError(f"{path} 裡找不到 manifest.json，這不是 Molili 匯出資料")
            self.prefix = ""
            return
        self._zip = zipfile.ZipFile(path)
        manifests = sorted(
            (n for n in self._zip.namelist() if n.rsplit("/", 1)[-1] == "manifest.json"),
            key=len,
        )
        if not manifests:
            raise ValueError(f"{path.name} 裡找不到 manifest.json，這不是 Molili 匯出檔")
        self.prefix = manifests[0][: -len("manifest.json")]

    def read(self, rel: str) -> str | None:
        if self._zip is None:
            p = self.path / rel
            return p.read_text(encoding="utf-8") if p.is_file() else None
        try:
            return self._zip.read(self.prefix + rel).decode("utf-8")
        except KeyError:
            return None

    def find(self, under: str, filename: str) -> str | None:
        """在 under/ 底下找第一個叫 filename 的檔案，回傳相對路徑。"""
        if self._zip is None:
            hit = next((self.path / under).rglob(filename), None) if (self.path / under).is_dir() else None
            return hit.relative_to(self.path).as_posix() if hit else None
        start = self.prefix + under.rstrip("/") + "/"
        for name in self._zip.namelist():
            if name.startswith(start) and name.rsplit("/", 1)[-1] == filename:
                return name[len(self.prefix):]
        return None

    def files_under(self, under: str) -> list[str]:
        """under/ 底下所有檔案的相對路徑。"""
        if self._zip is None:
            base = self.path / under
            return [p.relative_to(self.path).as_posix() for p in base.rglob("*") if p.is_file()] if base.is_dir() else []
        start = self.prefix + under.rstrip("/") + "/"
        return [n[len(self.prefix):] for n in self._zip.namelist() if n.startswith(start) and not n.endswith("/")]

    def read_bytes(self, rel: str) -> bytes:
        if self._zip is None:
            return (self.path / rel).read_bytes()
        return self._zip.read(self.prefix + rel)

    def jsonl(self, rel: str) -> list[dict]:
        text = self.read(rel) or ""
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()


def assistant_parts(content: str) -> dict | None:
    """解析原版 LLM 的 JSON 輸出 {reasoning, mood, expression, messages}；不是這種格式就回傳 None。"""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return None
    texts = []
    for m in data["messages"]:
        if isinstance(m, str):
            texts.append(m)
        elif m.get("type") == "image":
            alt = (m.get("alt") or "").strip()
            texts.append(f"[傳了一張照片：{alt[:60]}]" if alt else "[傳了一張照片]")
        elif m.get("content"):
            texts.append(m["content"])
    data["text"] = "\n".join(t for t in texts if t)
    return data


def _tz_of(snapshot_time: str) -> str:
    m = _TZ_SUFFIX.search(snapshot_time or "")
    return m.group(1) if m else "+08:00"


def _import_persona(conn: sqlite3.Connection, ar: Archive, tz: str) -> int:
    count = 0
    role_path = ar.find("personality", "role_prompt.md")
    if role_path:
        text = _MODEL_TAG.sub("", ar.read(role_path) or "").strip()
        conn.execute("INSERT OR REPLACE INTO persona(name, content) VALUES ('role_prompt', ?)", (text,))
        count += 1
    cog_path = ar.find("personality", "cognitive.md")
    if cog_path:
        for line in _MODEL_TAG.sub("\n", ar.read(cog_path) or "").splitlines():
            m = _FACT_LINE.match(line)
            if not m:
                continue
            text = m["text"].strip()
            source_id = "fact:" + hashlib.sha1(text.encode()).hexdigest()[:16]
            created = m["time"].strip().replace(" ", "T") + tz
            cur = conn.execute(
                "INSERT OR IGNORE INTO memories(source_id, kind, created_at, summary) VALUES (?, 'fact', ?, ?)",
                (source_id, created, text),
            )
            count += cur.rowcount
    return count


def _import_history(conn: sqlite3.Connection, ar: Archive) -> int:
    rows = sorted(ar.jsonl("conversations/history.jsonl"), key=lambda r: (r["created_at"], r["id"]))
    count = 0
    for r in rows:
        role, source, content = r["role"], r["source"], r.get("content") or ""
        expression = mood = reasoning = None
        raw = None
        if source == "notifyInDialog":
            role, source = "system", "notify"
        elif role == "assistant" and source == "chat":
            parsed = assistant_parts(content)
            if parsed is None:
                continue  # 例如「回復超時，請重試一次」這類錯誤訊息，不該進入記憶
            raw = content
            content = parsed["text"]
            expression, mood, reasoning = parsed.get("expression"), parsed.get("mood"), parsed.get("reasoning")
        if not content.strip():
            continue
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
               (source_id, created_at, role, source, text, expression, mood, reasoning, model, raw)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(r["id"]), r["created_at"], role, source, content, expression, mood, reasoning, r.get("model"), raw),
        )
        count += cur.rowcount
    return count


def _import_attachments(conn: sqlite3.Connection, ar: Archive, data_dir: Path) -> int:
    """官方匯出的照片檔名是「<dialog id>_<序號>.jpg」，對回包含該 dialog 的訊息。"""
    owner: dict[str, str] = {}
    for r in ar.jsonl("conversations/history.jsonl"):
        for d in r.get("dialogs") or []:
            owner[str(d.get("id"))] = str(r["id"])
    count = 0
    for rel in ar.files_under("attachments/output"):
        filename = rel.rsplit("/", 1)[-1]
        message_id = owner.get(filename.split("_", 1)[0])
        if not message_id:
            continue
        dest = data_dir / "images" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(ar.read_bytes(rel))
        cur = conn.execute("UPDATE messages SET attachment = ? WHERE source_id = ?", (f"images/{filename}", message_id))
        count += cur.rowcount
    return count


def _import_memory(conn: sqlite3.Connection, ar: Archive) -> tuple[int, bool]:
    count = 0
    profile: tuple[str, str] | None = None
    fallback_profile: tuple[str, str] | None = None
    for r in ar.jsonl("memory/long_term.jsonl"):
        mtype = r.get("memory_type")
        info = r.get("memory_info") or {}
        if mtype == "event_v1" and info.get("summary"):
            cur = conn.execute(
                "INSERT OR IGNORE INTO memories(source_id, kind, created_at, summary, original) VALUES (?, 'event', ?, ?, ?)",
                (r["id"], r["time_iso"], info["summary"], info.get("original_messages")),
            )
            count += cur.rowcount
        elif mtype == "profile_v1" and info.get("user_profile"):
            profile = (info["user_profile"], r.get("time_iso", ""))
        elif mtype == "profile_cache" and r.get("content"):
            fallback_profile = (r["content"], r.get("updated_at", ""))
    chosen = profile or fallback_profile
    if chosen:
        cur = conn.execute("INSERT OR IGNORE INTO profile(id, content, updated_at) VALUES (1, ?, ?)", chosen)
        return count, bool(cur.rowcount)
    return count, False


def _import_diaries(conn: sqlite3.Connection, ar: Archive) -> int:
    count = 0
    for r in ar.jsonl("diaries/diaries.jsonl"):
        cur = conn.execute(
            "INSERT OR IGNORE INTO diaries(date, content, created_at) VALUES (?, ?, ?)",
            (r["date"], r["content"], r.get("created_at")),
        )
        count += cur.rowcount
    return count


def _import_progress(conn: sqlite3.Connection, ar: Archive) -> int:
    text = ar.read("game/progress.json")
    if not text:
        return 0
    data = json.loads(text)
    for f in data.get("favor", [])[:1]:
        conn.execute(
            "INSERT OR IGNORE INTO favor(id, total, level, level_favor, updated_at) VALUES (1, ?, ?, ?, ?)",
            (f["total_favor"], f["level"], f["level_favor"], f.get("updated_at")),
        )
    items = data.get("owned_items", [])
    for it in items:
        d = it.get("definition") or {}
        conn.execute(
            """INSERT OR REPLACE INTO items(code, name, category, slot, quantity, description, effects)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (it["item_code"], d.get("name"), d.get("category"), d.get("slot"), it.get("quantity", 1),
             d.get("description"), json.dumps(d.get("effects") or [], ensure_ascii=False)),
        )
    return len(items)


def _user_nickname(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT content FROM profile WHERE id = 1").fetchone()
    if row:
        try:
            nick = json.loads(row["content"]).get("基础信息", {}).get("昵称")
            if nick:
                return nick
        except json.JSONDecodeError:
            pass
    return "主人"


def import_archive(conn: sqlite3.Connection, path: Path, data_dir: Path) -> dict[str, int]:
    ar = Archive(path)
    try:
        manifest = json.loads(ar.read("manifest.json") or "{}")
        if manifest.get("format") != EXPORT_FORMAT:
            raise ValueError(f"格式不符：預期 {EXPORT_FORMAT}，實際是 {manifest.get('format')!r}")
        tz = _tz_of(manifest.get("snapshot_time", ""))
        with conn:
            counts = {"persona": _import_persona(conn, ar, tz)}
            counts["messages"] = _import_history(conn, ar)
            counts["photos"] = _import_attachments(conn, ar, data_dir)
            counts["memories"], profile_added = _import_memory(conn, ar)
            counts["profile"] = int(profile_added)
            counts["diaries"] = _import_diaries(conn, ar)
            counts["items"] = _import_progress(conn, ar)
            set_meta(conn, "user_id", manifest.get("user_id", ""))
            set_meta(conn, "snapshot_time", manifest.get("snapshot_time", ""))
            set_meta(conn, "user_nickname", _user_nickname(conn))
            set_meta(conn, "imported_at", iso(now()))
        return counts
    finally:
        ar.close()
