"""番茄鐘陪伴（原版的「陪伴」：工作／學習／休息）。她會陪你做同樣的事，開始、中場、結束時各說幾句。"""
from __future__ import annotations

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import theme

KINDS = {"work": ("💼", "工作"), "study": ("📚", "學習"), "rest": ("🌙", "休息")}
MODES = {"pomodoro": "🍅 番茄鐘", "countdown": "⏳ 倒數", "free": "∞ 不限時"}
# 陪伴時她在做的事（對應 view 的狀態）；中場休息時她會哼歌
KIND_STATES = {"work": "writing", "study": "reading", "rest": "dozing"}
BREAK_STATE = "music"


class TaskRunner(QObject):
    phase_changed = Signal(str, str, int)   # 種類, start | break | resume | end, 分鐘數
    ticked = Signal(str)                    # 給計時條顯示的文字

    def __init__(self):
        super().__init__()
        self.seconds_per_minute = 60  # 測試時可以調小
        self.kind = ""
        self.mode = ""
        self.phase = ""
        self.running = False
        self.paused = False
        self._timer = QTimer(self, interval=1000, timeout=self._tick)

    def start(self, kind: str, mode: str, focus_min: int, break_min: int = 5, rounds: int = 1) -> None:
        self.kind, self.mode = kind, mode
        self.focus_min, self.break_min, self.rounds = focus_min, break_min, max(rounds, 1)
        self.round = 0
        self.focus_seconds = 0
        self.phase = "focus"
        self.remaining = None if mode == "free" else focus_min * self.seconds_per_minute
        self.running, self.paused = True, False
        self._timer.start()
        self.phase_changed.emit(kind, "start", 0 if mode == "free" else self._planned_minutes())
        self.ticked.emit(self.label())

    def _planned_minutes(self) -> int:
        return self.focus_min * (self.rounds if self.mode == "pomodoro" else 1)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.ticked.emit(self.label())

    def stop(self) -> None:
        if self.running:
            self._finish()

    def target_state(self) -> str:
        return BREAK_STATE if self.phase == "break" else KIND_STATES.get(self.kind, "idle")

    def _tick(self) -> None:
        if self.paused:
            return
        if self.phase == "focus":
            self.focus_seconds += 1
        if self.remaining is not None:
            self.remaining -= 1
            if self.remaining <= 0:
                self._phase_done()
                if not self.running:
                    return
        self.ticked.emit(self.label())

    def _phase_done(self) -> None:
        if self.mode != "pomodoro":
            self._finish()
            return
        if self.phase == "focus":
            self.round += 1
            if self.round >= self.rounds:
                self._finish()
                return
            self.phase = "break"
            self.remaining = self.break_min * self.seconds_per_minute
            self.phase_changed.emit(self.kind, "break", self.focus_min)
        else:
            self.phase = "focus"
            self.remaining = self.focus_min * self.seconds_per_minute
            self.phase_changed.emit(self.kind, "resume", 0)

    def _finish(self) -> None:
        self._timer.stop()
        self.running = False
        minutes = round(self.focus_seconds / self.seconds_per_minute)
        self.phase_changed.emit(self.kind, "end", minutes)

    def label(self) -> str:
        icon, name = KINDS.get(self.kind, ("⏱", "陪伴"))
        if self.phase == "break":
            icon, name = "☕", "休息一下"
        if self.remaining is None:
            seconds = self.focus_seconds
        else:
            seconds = max(self.remaining, 0)
        clock = f"{seconds // 60:02d}:{seconds % 60:02d}"
        if self.mode == "pomodoro":
            clock += f"　{min(self.round + 1, self.rounds)}/{self.rounds}"
        return f"{icon} {name}　{clock}" + ("　（暫停）" if self.paused else "")


DIALOG_QSS = f"""
QFrame#card {{ background:{theme.DARK_2}; border:1px solid {theme.DARK_BORDER}; border-radius:18px; }}
QLabel {{ color:{theme.TEXT_LIGHT}; font-size:11pt; background:transparent; }}
QLabel#title {{ font-size:15pt; font-weight:bold; }}
QLabel#muted {{ color:{theme.TEXT_LIGHT_MUTED}; font-size:9.5pt; }}
QPushButton#choice {{
    background:rgba(255,255,255,18); color:{theme.TEXT_LIGHT}; border:none; border-radius:14px;
    padding:8px 12px; font-size:10.5pt;
}}
QPushButton#choice:checked {{ background:{theme.ACCENT}; color:white; font-weight:bold; }}
QPushButton#primary {{ background:{theme.ACCENT}; color:white; border:none; border-radius:16px; padding:9px; font-size:11pt; }}
QPushButton#primary:hover {{ background:{theme.ACCENT_2}; }}
QPushButton#close {{ background:transparent; color:{theme.TEXT_LIGHT_MUTED}; border:none; font-size:13pt; }}
QSpinBox {{
    background:#2e2b42; color:{theme.TEXT_LIGHT}; border:1px solid {theme.DARK_BORDER};
    border-radius:8px; padding:4px 8px; min-width:70px;
}}
"""


def _choices(options: dict[str, str], default: str) -> tuple[QHBoxLayout, QButtonGroup]:
    row = QHBoxLayout()
    group = QButtonGroup(row)
    for key, text in options.items():
        b = QPushButton(text)
        b.setObjectName("choice")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("key", key)
        b.setChecked(key == default)
        group.addButton(b)
        row.addWidget(b)
    return row, group


def _spin(value: int, lo: int, hi: int, suffix: str) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSuffix(suffix)
    return s


class TaskDialog(QWidget):
    start_requested = Signal(str, str, int, int, int)   # 種類, 模式, 專注分鐘, 休息分鐘, 輪數

    def __init__(self, agent_name: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(DIALOG_QSS)
        self.setFixedWidth(340)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        col = QVBoxLayout(card)
        col.setContentsMargins(20, 14, 20, 18)
        col.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("⏱ 陪伴")
        title.setObjectName("title")
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.hide)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(close)
        col.addLayout(top)
        hint = QLabel(f"{agent_name}會陪你一起做，結束時好感度會上升")
        hint.setObjectName("muted")
        col.addWidget(hint)

        kinds, self.kind_group = _choices({k: f"{i} {n}" for k, (i, n) in KINDS.items()}, "work")
        col.addLayout(kinds)
        modes, self.mode_group = _choices(MODES, "pomodoro")
        col.addLayout(modes)

        self.focus = _spin(25, 1, 180, " 分鐘")
        self.brk = _spin(5, 1, 60, " 分鐘")
        self.rounds = _spin(4, 1, 12, " 輪")
        self.rows: dict[str, QWidget] = {}
        for key, label, spin in (("focus", "專注", self.focus), ("break", "中場休息", self.brk), ("rounds", "輪數", self.rounds)):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(label))
            h.addStretch(1)
            h.addWidget(spin)
            self.rows[key] = row
            col.addWidget(row)

        start = QPushButton("開始")
        start.setObjectName("primary")
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self._start)
        col.addWidget(start)
        self.mode_group.buttonClicked.connect(self._update_rows)
        self._update_rows()

    def _checked(self, group: QButtonGroup) -> str:
        return group.checkedButton().property("key")

    def _update_rows(self, *_args) -> None:
        mode = self._checked(self.mode_group)
        self.rows["focus"].setVisible(mode != "free")
        self.rows["break"].setVisible(mode == "pomodoro")
        self.rows["rounds"].setVisible(mode == "pomodoro")
        self.adjustSize()

    def _start(self) -> None:
        self.start_requested.emit(
            self._checked(self.kind_group), self._checked(self.mode_group),
            self.focus.value(), self.brk.value(), self.rounds.value(),
        )
        self.hide()

    def popup_near(self, anchor: QPoint) -> None:
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(anchor.x() - self.width() - 10, area.right() - self.width()))
        y = max(area.top(), min(anchor.y() - self.height() // 2, area.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()


class TaskBadge(QWidget):
    """陪伴進行中顯示在她身旁的小計時條：剩餘時間、暫停、結束。"""

    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(f"""
            QFrame#pill {{ background:{theme.DARK_2}; border:1px solid {theme.DARK_BORDER}; border-radius:15px; }}
            QLabel {{ color:{theme.TEXT_LIGHT}; font-size:10pt; font-weight:bold; background:transparent; }}
            QPushButton {{ color:{theme.TEXT_LIGHT}; background:transparent; border:none; font-size:10pt; padding:0 4px; }}
            QPushButton:hover {{ color:{theme.ACCENT_2}; }}
        """)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        pill = QFrame()
        pill.setObjectName("pill")
        outer.addWidget(pill)
        row = QHBoxLayout(pill)
        row.setContentsMargins(12, 5, 8, 5)
        self.text = QLabel("")
        self.pause = QPushButton("⏸")
        self.stop = QPushButton("■")
        self.pause.setToolTip("暫停／繼續")
        self.stop.setToolTip("結束陪伴")
        for b in (self.pause, self.stop):
            b.setCursor(Qt.PointingHandCursor)
        self.pause.clicked.connect(self.pause_clicked)
        self.stop.clicked.connect(self.stop_clicked)
        row.addWidget(self.text)
        row.addWidget(self.pause)
        row.addWidget(self.stop)

    def set_text(self, text: str, paused: bool) -> None:
        self.text.setText(text)
        self.pause.setText("▶" if paused else "⏸")
        self.adjustSize()
