"""時間工具：統一用帶時區的 datetime，並產生給 prompt 看的中文描述。"""
from __future__ import annotations

from datetime import datetime, timedelta

WEEKDAYS = "一二三四五六日"


def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.astimezone()


def period(dt: datetime) -> str:
    h = dt.hour
    if h < 5:
        return "凌晨"
    if h < 11:
        return "早上"
    if h < 13:
        return "中午"
    if h < 18:
        return "下午"
    return "晚上"


def describe(dt: datetime) -> str:
    return f"{dt:%Y-%m-%d}（週{WEEKDAYS[dt.weekday()]}）{period(dt)} {dt:%H:%M}"


def humanize(delta: timedelta) -> str:
    sec = delta.total_seconds()
    if sec < 60:
        return "剛剛"
    if sec < 3600:
        return f"約 {int(sec // 60)} 分鐘"
    if sec < 86400:
        return f"約 {int(sec // 3600)} 小時"
    return f"約 {int(sec // 86400)} 天"
