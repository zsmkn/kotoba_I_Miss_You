"""SQLite 儲存。一個資料庫 = 一位玩家和他的言葉。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS persona(
  name    TEXT PRIMARY KEY,
  content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id  TEXT UNIQUE,          -- 官方匯出的原始 id；本地新訊息為 NULL
  created_at TEXT NOT NULL,
  role       TEXT NOT NULL,        -- user | assistant | system
  source     TEXT NOT NULL,        -- chat | autoTask | giftReply | notify
  text       TEXT NOT NULL,        -- 顯示用純文字（多則訊息以換行連接）
  expression TEXT,
  mood       TEXT,
  reasoning  TEXT,
  model      TEXT,
  raw        TEXT
);
CREATE TABLE IF NOT EXISTS memories(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id  TEXT UNIQUE,
  kind       TEXT NOT NULL,        -- event（對話事件摘要）| fact（重要設定）
  created_at TEXT NOT NULL,
  summary    TEXT NOT NULL,
  original   TEXT
);
CREATE TABLE IF NOT EXISTS profile(
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  content    TEXT NOT NULL,        -- JSON：使用者畫像
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS diaries(
  date       TEXT PRIMARY KEY,
  content    TEXT NOT NULL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS favor(
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  total       INTEGER NOT NULL,
  level       INTEGER NOT NULL,
  level_favor INTEGER NOT NULL,
  updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS items(
  code        TEXT PRIMARY KEY,
  name        TEXT,
  category    TEXT,
  slot        TEXT,
  quantity    INTEGER,
  description TEXT,
  effects     TEXT
);
CREATE TABLE IF NOT EXISTS companion_tasks(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,        -- work | study | rest
  started_at TEXT NOT NULL,
  ended_at   TEXT NOT NULL,
  minutes    INTEGER NOT NULL
);
"""

# 舊版資料庫缺少的欄位：(資料表, 欄位, 定義)
MIGRATIONS = [
    ("messages", "attachment", "TEXT"),  # 圖片等附件，相對於資料目錄的路徑
]


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "molili.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def get_persona(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT content FROM persona WHERE name = ?", (name,)).fetchone()
    return row["content"] if row else None
