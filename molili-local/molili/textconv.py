"""簡繁轉換。沒裝 opencc 時全部退化成原樣輸出。"""
from __future__ import annotations

from functools import cache

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover
    OpenCC = None


@cache
def _converter(config: str):
    return OpenCC(config) if OpenCC else None


def convert(text: str, config: str) -> str:
    cc = _converter(config)
    return cc.convert(text) if cc else text


def normalize(text: str) -> str:
    """檢索比對用：統一成繁體。"""
    return convert(text, "s2t")


# 依輸出語言決定要不要強制轉換；只靠 prompt 要求，模型還是常被簡體人設帶偏
OUTPUT_CONVERSION = {"zh-TW": "s2twp", "zh-CN": "t2s"}


def localize(text: str, language: str) -> str:
    config = OUTPUT_CONVERSION.get(language)
    return convert(text, config) if config else text
