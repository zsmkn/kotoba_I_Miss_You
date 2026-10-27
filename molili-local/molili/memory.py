"""事件記憶檢索：中文字 bigram + IDF 加權。

不用 embedding，是為了讓只用 Ollama 的玩家也不必再裝 embedding 模型。
官方匯出的摘要簡繁混雜，比對前先統一轉成繁體。
"""
from __future__ import annotations

import math
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass

from .textconv import normalize

_CJK_RUN = re.compile(r"[㐀-鿿]+")
_WORD = re.compile(r"[A-Za-z0-9]{2,}")
MIN_SCORE = 6.0  # 至少要命中約兩三個少見詞才算相關


def tokenize(text: str) -> set[str]:
    text = normalize(text)
    tokens: set[str] = set()
    for run in _CJK_RUN.findall(text):
        tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    tokens.update(w.lower() for w in _WORD.findall(text))
    return tokens


@dataclass
class Memory:
    id: int
    created_at: str
    summary: str
    tokens: set[str]


class MemoryIndex:
    def __init__(self, conn: sqlite3.Connection):
        self._lock = threading.Lock()
        self._docs: list[Memory] = []
        self._df: Counter[str] = Counter()
        rows = conn.execute("SELECT id, created_at, summary FROM memories WHERE kind = 'event' ORDER BY id")
        for row in rows:
            self._add(row["id"], row["created_at"], row["summary"])

    def _add(self, mem_id: int, created_at: str, summary: str) -> None:
        doc = Memory(mem_id, created_at, summary, tokenize(summary))
        self._docs.append(doc)
        self._df.update(doc.tokens)

    def add(self, mem_id: int, created_at: str, summary: str) -> None:
        with self._lock:
            self._add(mem_id, created_at, summary)

    def __len__(self) -> int:
        return len(self._docs)

    def search(self, query: str, k: int, *, before: str | None = None) -> list[Memory]:
        """回傳最相關的 k 則記憶（依時間排序）。before：只找這個時間點之前的，避免和近期對話重複。"""
        q = tokenize(query)
        if not q or k <= 0:
            return []
        with self._lock:
            n = len(self._docs)
            scored = []
            for doc in self._docs:
                if before and doc.created_at >= before:
                    continue
                hit = q & doc.tokens
                if not hit:
                    continue
                raw = sum(math.log((n + 1) / (self._df[t] + 0.5)) for t in hit)
                if raw < MIN_SCORE:
                    continue
                scored.append((raw / math.sqrt(len(doc.tokens)), doc))
        top = [doc for _, doc in sorted(scored, key=lambda x: x[0], reverse=True)[:k]]
        return sorted(top, key=lambda d: d.created_at)
