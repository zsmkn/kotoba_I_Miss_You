"""網路搜尋（原版的「通信器連上地球網路」）。

用 DuckDuckGo，不需要 API key，三種模型來源都能用：模型在 JSON 填 search 欄位，
程式搜尋後再把結果交回給她回答。注意：搜尋關鍵字會送到 DuckDuckGo。
"""
from __future__ import annotations

from dataclasses import dataclass

REGIONS = {"zh-TW": "tw-tzh", "zh-CN": "cn-zh", "ja": "jp-jp", "en": "us-en"}
MAX_RESULTS = 5


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def web_search(query: str, language: str = "zh-TW", max_results: int = MAX_RESULTS) -> list[SearchResult]:
    from ddgs import DDGS  # 延後載入：沒開搜尋的人不需要這個套件

    hits = DDGS().text(query, region=REGIONS.get(language, "wt-wt"), max_results=max_results) or []
    return [SearchResult(h.get("title", ""), h.get("href", ""), h.get("body", "")) for h in hits]


def format_results(results: list[SearchResult]) -> str:
    return "\n".join(f"{i}. {r.title}（{r.url}）\n   {r.snippet}" for i, r in enumerate(results, 1))
