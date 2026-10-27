"""UI 執行緒用的唯讀查詢（背景對話引擎有自己的連線；WAL 模式下可以同時讀寫）。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .. import db
from ..engine import next_level_cost
from ..textconv import localize
from ..timeutil import now, parse
from ..wardrobe import Outfit, load_outfit, save_outfit


@dataclass
class FavorInfo:
    level: int
    progress: int
    cost: int
    total: int


class PetData:
    def __init__(self, data_dir: Path, language: str):
        self.conn = db.connect(data_dir)
        self.data_dir = data_dir
        self.language = language  # 官方道具與部分日記是簡體，顯示時轉成目前的角色文本語言

    def favor(self) -> FavorInfo | None:
        row = self.conn.execute("SELECT * FROM favor WHERE id = 1").fetchone()
        if not row:
            return None
        return FavorInfo(row["level"], row["level_favor"], next_level_cost(row["level"]), row["total"])

    def days_known(self) -> int:
        row = self.conn.execute("SELECT MIN(created_at) AS first FROM messages").fetchone()
        if not row or not row["first"]:
            return 0
        return (now().date() - parse(row["first"]).date()).days + 1

    def chat_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM messages WHERE role = 'user'").fetchone()[0]

    def diary_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM diaries").fetchone()[0]

    def messages(self, limit: int, before_id: int | None = None) -> list[sqlite3.Row]:
        """依時間正序回傳最近 limit 則（before_id 用於往上載入更早的）。"""
        if before_id is None:
            rows = self.conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, limit)
            ).fetchall()
        return rows[::-1]

    def outfit(self) -> Outfit:
        return load_outfit(self.conn)

    def save_outfit(self, outfit: Outfit) -> None:
        save_outfit(self.conn, outfit)

    def item_names(self, codes: list[str]) -> list[str]:
        rows = self.conn.execute(
            f"SELECT code, name FROM items WHERE code IN ({','.join('?' * len(codes))})", codes
        ).fetchall()
        names = {r["code"]: localize(r["name"] or "", self.language) for r in rows}
        return [names[c] for c in codes if c in names]

    def diaries(self) -> list[dict]:
        rows = self.conn.execute("SELECT date, content FROM diaries ORDER BY date DESC").fetchall()
        return [{"date": r["date"], "content": localize(r["content"], self.language)} for r in rows]

    def items(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM items ORDER BY
               CASE category WHEN 'clothing' THEN 0 WHEN 'accessory' THEN 1 WHEN 'gift' THEN 2 ELSE 3 END, name"""
        ).fetchall()
        return [
            {**dict(r), "name": localize(r["name"] or "", self.language),
             "description": localize(r["description"] or "", self.language)}
            for r in rows
        ]
