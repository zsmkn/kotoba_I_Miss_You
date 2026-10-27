"""頭頂的對話氣泡：一則一則打字顯示她的訊息。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QLabel, QWidget

PADDING = 14
TAIL = 10
MAX_TEXT_WIDTH = 280
TYPE_INTERVAL_MS = 45
THINKING_FRAMES = ["‧", "‧‧", "‧‧‧"]

BG = QColor(255, 255, 255, 238)
BORDER = QColor(214, 160, 205)
TEXT = "#4a3b5c"
DIM_TEXT = "#9a8fa8"


class SpeechBubble(QWidget):
    talking_changed = Signal(bool)
    finished = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        font = QFont()
        font.setPointSize(11)
        self.label.setFont(font)

        self._anchor = QPoint()
        self._queue: list[str] = []
        self._text = ""
        self._shown = 0
        self._frame = 0
        self._type_timer = QTimer(self, interval=TYPE_INTERVAL_MS, timeout=self._type_tick)
        self._hold_timer = QTimer(self, singleShot=True, timeout=self._next)
        self._thinking_timer = QTimer(self, interval=400, timeout=self._thinking_tick)

    def set_anchor(self, p: QPoint) -> None:
        self._anchor = p
        self._place()

    @property
    def thinking(self) -> bool:
        return self._thinking_timer.isActive()

    def show_thinking(self) -> None:
        self._stop()
        self._frame = 0
        self._layout(THINKING_FRAMES[-1], dim=False)
        self.label.setText(THINKING_FRAMES[0])
        self._thinking_timer.start()
        self.show()

    def show_messages(self, messages: list[str]) -> None:
        self._stop()
        self._queue = list(messages)
        self._next()

    def show_notice(self, text: str) -> None:
        """系統提示（連線失敗等），用淡色字顯示，不算她說的話。"""
        self._stop()
        self._layout(text, dim=True)
        self.label.setText(text)
        self.show()
        self._hold_timer.start(6000)

    # ---- 內部 -----------------------------------------------------------

    def _stop(self) -> None:
        for t in (self._type_timer, self._hold_timer, self._thinking_timer):
            t.stop()
        self._queue = []
        self.talking_changed.emit(False)

    def _next(self) -> None:
        if not self._queue:
            self.hide()
            self.finished.emit()
            return
        self._text = self._queue.pop(0)
        self._shown = 0
        self._layout(self._text, dim=False)
        self.label.setText("")
        self.show()
        self.talking_changed.emit(True)
        self._type_timer.start()

    def _type_tick(self) -> None:
        self._shown += 1
        self.label.setText(self._text[: self._shown])
        if self._shown >= len(self._text):
            self._type_timer.stop()
            self.talking_changed.emit(False)
            last = not self._queue
            self._hold_timer.start((4000 if last else 700) + 60 * len(self._text))

    def _thinking_tick(self) -> None:
        self._frame = (self._frame + 1) % len(THINKING_FRAMES)
        self.label.setText(THINKING_FRAMES[self._frame])

    def _layout(self, full_text: str, *, dim: bool) -> None:
        """依完整文字先算好大小，打字過程中氣泡才不會一直跳動。"""
        self.label.setStyleSheet(f"color: {DIM_TEXT if dim else TEXT};")
        fm = self.label.fontMetrics()
        width = min(MAX_TEXT_WIDTH, max(fm.horizontalAdvance(line) for line in full_text.splitlines() or [""]) + 4)
        width = max(width, 28)
        self.label.setFixedWidth(width)
        self.label.setText(full_text)
        height = self.label.heightForWidth(width)
        self.label.setFixedHeight(height)
        self.label.move(PADDING, PADDING)
        self.resize(width + 2 * PADDING, height + 2 * PADDING + TAIL)
        self._place()

    def _place(self) -> None:
        x = self._anchor.x() - self.width() // 2
        y = self._anchor.y() - self.height()
        screen = QGuiApplication.screenAt(self._anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), y)
        self.move(x, y)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(1, 1, self.width() - 2, self.height() - TAIL - 2)
        path = QPainterPath()
        path.addRoundedRect(body, 12, 12)
        # 尾巴指向她的頭；氣泡被螢幕邊緣推開時尾巴跟著偏移
        tail_x = min(max(self._anchor.x() - self.x(), 20), self.width() - 20)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([
            QPointF(tail_x - 8, body.bottom() - 1),
            QPointF(tail_x + 8, body.bottom() - 1),
            QPointF(tail_x, body.bottom() + TAIL - 1),
        ]))
        path = path.united(tail)
        p.setPen(QPen(BORDER, 1.5))
        p.setBrush(BG)
        p.drawPath(path)
