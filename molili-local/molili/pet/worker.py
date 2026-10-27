"""在背景執行緒呼叫 LLM，畫面才不會在等回覆時卡住。

Companion 和它的 SQLite 連線都在這條執行緒裡建立與使用（sqlite3 連線不能跨執行緒）；
請求透過 Qt 的 queued signal 排隊，一次只處理一件。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .. import db
from ..config import Config
from ..diary import missing_dates
from ..engine import Companion, Reply
from ..llm import LLMError, create_llm
from ..timeutil import now


class CompanionWorker(QObject):
    ready = Signal(str)          # 初始化完成，附上模型名稱
    replied = Signal(object)     # Reply
    failed = Signal(str)
    busy_changed = Signal(bool)
    favor_changed = Signal(int)  # 目前好感等級
    diary_busy = Signal(bool)
    diaries_written = Signal(list)  # 寫好的日期

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.companion: Companion | None = None

    @Slot(object)
    def reconfigure(self, cfg: Config) -> None:
        """設定頁儲存後換上新的模型設定。"""
        self.wait_background()
        self.cfg = cfg
        self.companion = None
        self.setup()

    @Slot()
    def setup(self) -> None:
        try:
            conn = db.connect(self.cfg.data_dir)
            llm = create_llm(self.cfg.llm)
            self.companion = Companion(self.cfg, conn, llm)
        except (LLMError, RuntimeError) as e:
            self.failed.emit(str(e))
            return
        self.ready.emit(f"{self.cfg.llm.provider} / {llm.model}")
        if f := self.companion.favor():
            self.favor_changed.emit(f["level"])

    @Slot()
    def greet(self) -> None:
        self._run(lambda c: c.greet())

    @Slot(str, str)
    def chat(self, text: str, situation: str) -> None:
        self._run(lambda c: c.reply(text, situation))

    @Slot(str, str)
    def touch(self, area: str, situation: str) -> None:
        self._run(lambda c: c.touch(area, situation))

    @Slot(str)
    def idle_chat(self, situation: str) -> None:
        self._run(lambda c: c.idle_chat(situation))

    @Slot(str, str, int)
    def task(self, kind: str, phase: str, minutes: int) -> None:
        self._run(lambda c: c.companion_task(kind, phase, minutes))

    @Slot()
    def daily(self) -> None:
        """補寫前幾天的日記。寫日記不算「回覆」，用 diary_busy 讓畫面顯示她在寫東西。"""
        if self.companion is None:
            return
        if not self.cfg.chat.diary or not missing_dates(self.companion.conn, now().date()):
            return
        self.diary_busy.emit(True)
        try:
            written = self.companion.write_missing_diaries()
        except LLMError as e:
            self.failed.emit(f"寫日記失敗：{e}")
            return
        finally:
            self.diary_busy.emit(False)
        if written:
            self.diaries_written.emit(written)

    def _run(self, action) -> None:
        if self.companion is None:
            self.failed.emit("還沒連上模型，請檢查 config.toml 後重新啟動")
            return
        self.busy_changed.emit(True)
        try:
            reply: Reply | None = action(self.companion)
        except LLMError as e:
            self.failed.emit(str(e))
            return
        finally:
            self.busy_changed.emit(False)
        if reply is not None:
            self.replied.emit(reply)
            if reply.favor:
                self.favor_changed.emit(reply.favor.level)

    def wait_background(self) -> None:
        if self.companion:
            self.companion.wait_background()
