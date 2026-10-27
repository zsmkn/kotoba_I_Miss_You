"""待機時她自己的生活：隨機做些小事（看書、寫筆記、哼歌、打瞌睡），久沒聊時主動找話題。"""
from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from . import winapi
from .view import Live2DCanvas

# 狀態名稱 → 給 LLM 的描述（接在「你剛剛」後面）
ACTIVITIES = {
    "reading": "正在看書",
    "writing": "正在用打字機寫觀測筆記",
    "music": "正在一邊聽音樂一邊輕輕哼歌",
    "dozing": "不小心打起了瞌睡",
}
DAY_WEIGHTS = {"reading": 3, "writing": 2, "music": 2, "dozing": 1}
NIGHT_WEIGHTS = {"reading": 1, "writing": 1, "music": 1, "dozing": 5}

START_DELAY_S = (90, 240)    # 閒下來多久後開始做小事
DURATION_S = (40, 120)       # 一件小事持續多久
CHAT_CHECK_MS = 60_000
AWAY_SECONDS = 300           # 系統超過這麼久沒有鍵盤滑鼠操作，就當主人不在電腦前
MAX_UNANSWERED = 2           # 連續主動搭話幾次沒被回應就先不吵


def _pick_activity() -> str:
    hour = datetime.now().hour
    weights = NIGHT_WEIGHTS if hour >= 23 or hour < 6 else DAY_WEIGHTS
    return random.choices(list(weights), weights=list(weights.values()))[0]


class IdleLife(QObject):
    chat_requested = Signal(str)   # 附上她剛剛在做的事

    def __init__(self, view: Live2DCanvas, *, is_busy: Callable[[], bool], chat_minutes: Callable[[], float]):
        super().__init__(view)
        self.view = view
        self.is_busy = is_busy
        self.chat_minutes = chat_minutes
        self.enabled = True
        self.activity: str | None = None
        self.last_interaction = time.monotonic()
        self.unanswered = 0
        self._start_timer = QTimer(self, singleShot=True, timeout=self._start)
        self._end_timer = QTimer(self, singleShot=True, timeout=self.finish)
        self._chat_timer = QTimer(self, timeout=self._check_chat)
        self._chat_timer.start(CHAT_CHECK_MS)

    # ---- 小動作 ---------------------------------------------------------

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if on:
            self.schedule()
        else:
            self._start_timer.stop()
            self.finish(reschedule=False)

    def schedule(self) -> None:
        """她閒下來了：過一陣子開始做點自己的事。"""
        if self.enabled and self.activity is None:
            self._start_timer.start(random.randint(*START_DELAY_S) * 1000)

    def interrupt(self) -> str:
        """有互動發生：停下手邊的事，回傳剛剛在做什麼（之後的狀態由呼叫端決定）。"""
        situation = ACTIVITIES.get(self.activity, "")
        self._start_timer.stop()
        self._end_timer.stop()
        self.activity = None
        return situation

    def finish(self, *, reschedule: bool = True) -> None:
        self._end_timer.stop()
        if self.activity is not None:
            self.activity = None
            self.view.set_state("idle")
        if reschedule:
            self.schedule()

    def _start(self) -> None:
        if self.is_busy() or self.view.state != "idle" or not self.view.isVisible():
            self.schedule()
            return
        self.activity = _pick_activity()
        self.view.set_state(self.activity)
        self._end_timer.start(random.randint(*DURATION_S) * 1000)

    # ---- 主動搭話 -------------------------------------------------------

    def user_interacted(self) -> None:
        self.last_interaction = time.monotonic()
        self.unanswered = 0

    def she_spoke(self) -> None:
        self.last_interaction = time.monotonic()

    def _check_chat(self) -> None:
        minutes = self.chat_minutes()
        if minutes <= 0 or self.is_busy() or not self.view.isVisible():
            return
        if time.monotonic() - self.last_interaction < minutes * 60:
            return
        if self.unanswered >= MAX_UNANSWERED or winapi.user_idle_seconds() > AWAY_SECONDS:
            return
        self.unanswered += 1
        self.last_interaction = time.monotonic()
        self.chat_requested.emit(self.interrupt())
