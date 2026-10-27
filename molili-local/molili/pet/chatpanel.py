"""她身旁的圓形聊天按鈕，以及展開後的聊天面板。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme, winapi
from .data import FavorInfo
from .widgets import Bubble, ImageBubble, MessageList, NoticeCard, render_history

BUTTON_SIZE = 52
PANEL_WIDTH = 470
HISTORY_LIMIT = 40
MESSAGE_GAP_MS = 900   # 多則訊息之間的間隔，模仿一則一則傳來


class ChatButton(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(BUTTON_SIZE + 8, BUTTON_SIZE + 8)
        self.setCursor(Qt.PointingHandCursor)
        self.opened = False
        self.unread = False

    def set_open(self, opened: bool) -> None:
        self.opened = opened
        if opened:
            self.unread = False
        self.update()

    def set_unread(self, unread: bool) -> None:
        self.unread = unread and not self.opened
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(4, 4, BUTTON_SIZE, BUTTON_SIZE)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(r.translated(0, 2))  # 淡淡的陰影
        p.setBrush(QColor(theme.DARK_2) if self.opened else QColor("#eef0ff"))
        p.drawEllipse(r)
        c = r.center()
        if self.opened:
            p.setPen(QPen(QColor("white"), 2.4, Qt.SolidLine, Qt.RoundCap))
            d = 9
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
        else:
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(QRectF(c.x() - 13, c.y() - 12, 26, 22))
            p.drawPolygon(QPolygonF([QPointF(c.x() - 8, c.y() + 6), QPointF(c.x() - 11, c.y() + 13),
                                     QPointF(c.x() - 1, c.y() + 9)]))
            p.setBrush(QColor("white"))
            for dx in (-6, 0, 6):
                p.drawEllipse(QRectF(c.x() + dx - 2, c.y() - 3, 4, 4))
            if self.unread:
                p.setBrush(QColor(theme.PINK))
                p.drawEllipse(QRectF(r.right() - 12, r.top(), 12, 12))


class ChatPanel(QWidget):
    submitted = Signal(str)
    open_page = Signal(str)        # 要求打開主面板的某一頁
    open_tasks = Signal()          # 打開番茄鐘陪伴設定
    talking_changed = Signal(bool)
    finished = Signal()

    def __init__(self, agent_name: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_WIDTH)
        self.agent_name = agent_name
        self._queue: list[str] = []
        self._timer = QTimer(self, singleShot=True, timeout=self._next_message)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.list = MessageList(date_color="rgba(255,255,255,170)")
        self.list.link_activated.connect(self.open_page)
        root.addWidget(self.list, 1)
        self.card = self._build_card()
        root.addWidget(self.card)
        if not winapi.NATIVE_CLICK_THROUGH:
            # macOS／Linux 的透明區域不會自動穿透：定期把遮罩設成「看得到的氣泡＋輸入卡片」
            self._mask_timer = QTimer(self, interval=300, timeout=self._update_mask)
            self._mask_timer.start()

    def _update_mask(self) -> None:
        if not self.isVisible():
            return
        viewport = QRect(self.list.viewport().mapTo(self, QPoint(0, 0)), self.list.viewport().size())
        region = QRegion(self.card.geometry())
        region += QRegion(QRect(self.list.verticalScrollBar().mapTo(self, QPoint(0, 0)), self.list.verticalScrollBar().size()))
        for w in self.list.widget().findChildren(QWidget):
            if isinstance(w, (Bubble, ImageBubble, NoticeCard)) and w.isVisible():
                rect = QRect(w.mapTo(self, QPoint(0, 0)), w.size()).intersected(viewport)
                if not rect.isEmpty():
                    region += QRegion(rect)
        self.setMask(region)

    def _build_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(f"""
            QFrame#card {{ background:{theme.DARK_2}; border-radius:24px; }}
            QFrame#input {{ background:{theme.LIGHT}; border-radius:24px; }}
            QPushButton#chip {{
                background:{theme.LIGHT_2}; color:{theme.TEXT_DARK}; border:none; border-radius:16px;
                padding:8px 14px; font-size:10.5pt; text-align:left;
            }}
            QPushButton#chip:hover {{ background:#cfcdf2; }}
            QLineEdit {{ background:transparent; border:none; color:{theme.TEXT_DARK}; font-size:11pt; padding:6px 4px; }}
            QPushButton#send {{
                background:{theme.ACCENT}; color:white; border:none; border-radius:17px; font-size:12pt;
                min-width:34px; min-height:34px; max-width:34px; max-height:34px;
            }}
            QPushButton#send:disabled {{ background:#b9b8e0; }}
            QLabel#stat {{ color:{theme.TEXT_LIGHT}; font-size:10.5pt; font-weight:bold; }}
            QPushButton#link {{ color:{theme.TEXT_LIGHT}; background:transparent; border:none; font-size:10.5pt; font-weight:bold; }}
            QPushButton#link:hover {{ color:{theme.ACCENT_2}; }}
        """)
        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 10)
        outer.setSpacing(8)

        box = QFrame()
        box.setObjectName("input")
        inner = QVBoxLayout(box)
        inner.setContentsMargins(18, 16, 14, 12)
        inner.setSpacing(8)
        self.chips_box = QVBoxLayout()
        self.chips_box.setSpacing(6)
        inner.addLayout(self.chips_box)
        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        self.divider.setStyleSheet("background:#d3d2ee;")
        inner.addWidget(self.divider)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("想聊些什麼呀~")
        self.edit.returnPressed.connect(self._submit)
        self.send = QPushButton("➤")
        self.send.setObjectName("send")
        self.send.setCursor(Qt.PointingHandCursor)
        self.send.clicked.connect(self._submit)
        row.addWidget(self.edit, 1)
        row.addWidget(self.send)
        inner.addLayout(row)
        outer.addWidget(box)

        status = QHBoxLayout()
        status.setContentsMargins(22, 0, 22, 0)
        self.favor_label = QLabel("💗 lv.-")
        self.favor_label.setObjectName("stat")
        self.days_label = QLabel("")
        self.days_label.setObjectName("stat")
        panel_link = QPushButton("❖ 主面板")
        panel_link.setObjectName("link")
        panel_link.setCursor(Qt.PointingHandCursor)
        panel_link.clicked.connect(lambda: self.open_page.emit("overview"))
        task_link = QPushButton("⏱ 陪伴")
        task_link.setObjectName("link")
        task_link.setCursor(Qt.PointingHandCursor)
        task_link.clicked.connect(self.open_tasks)
        status.addWidget(self.favor_label)
        status.addStretch(1)
        status.addWidget(self.days_label)
        status.addStretch(1)
        status.addWidget(task_link)
        status.addSpacing(12)
        status.addWidget(panel_link)
        outer.addLayout(status)
        self.set_suggestions([])
        return card

    # ---- 內容 -----------------------------------------------------------

    def load_history(self, rows, data_dir: Path) -> None:
        self.list.clear()
        render_history(self.list, rows, data_dir)
        self.list.scroll_to_bottom()

    def add_user(self, text: str) -> None:
        self.set_suggestions([])
        self.list.add_message("user", text)
        self.list.scroll_to_bottom()

    def add_notice(self, text: str) -> None:
        self.list.add_notice(text)
        self.list.scroll_to_bottom()

    def set_thinking(self, on: bool) -> None:
        self.list.set_thinking(on)
        self.edit.setEnabled(not on)
        self.send.setEnabled(not on)
        self.edit.setPlaceholderText(f"{self.agent_name}正在輸入…" if on else "想聊些什麼呀~")
        if not on and self.isVisible():
            self.edit.setFocus()

    def show_reply(self, messages: list[str], *, image: Path | None = None, animate: bool = True) -> None:
        """animate=False：面板沒開時直接把訊息補進紀錄，由頭頂氣泡負責「說話」。照片排在文字之後。"""
        items: list[str | Path] = [*messages, *([image] if image else [])]
        if not animate:
            for item in items:
                self._add_item(item)
            self.list.scroll_to_bottom()
            return
        self._queue = items
        self._next_message()

    def _add_item(self, item: str | Path) -> None:
        if isinstance(item, Path):
            self.list.add_message("assistant", "", image=item)
        else:
            self.list.add_message("assistant", item)

    def _next_message(self) -> None:
        if not self._queue:
            self.talking_changed.emit(False)
            self.finished.emit()
            return
        item = self._queue.pop(0)
        self._add_item(item)
        self.list.scroll_to_bottom()
        is_text = isinstance(item, str)
        self.talking_changed.emit(is_text)
        self._timer.start(MESSAGE_GAP_MS + (35 * len(item) if is_text else 0))

    def set_suggestions(self, suggestions: list[str]) -> None:
        while self.chips_box.count():
            w = self.chips_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for s in suggestions:
            chip = QPushButton(f"✎  {s}")
            chip.setObjectName("chip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            chip.clicked.connect(lambda _=False, text=s: self.submitted.emit(text))
            row = QHBoxLayout()
            row.addWidget(chip)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.chips_box.addWidget(holder)
        self.divider.setVisible(bool(suggestions))

    def set_status(self, favor: FavorInfo | None, days: int) -> None:
        if favor:
            self.favor_label.setText(f"💗 lv.{favor.level}　{favor.progress}/{favor.cost}")
        self.days_label.setText(f"📅 相識第 {days} 天" if days else "")

    def _submit(self) -> None:
        text = self.edit.text().strip()
        if text and self.edit.isEnabled():
            self.edit.clear()
            self.submitted.emit(text)

    def popup(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.edit.setFocus()
        self.list.scroll_to_bottom()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    # ---- 位置 -----------------------------------------------------------

    def place_beside(self, button: QRect) -> None:
        """放在聊天按鈕旁邊；左邊放不下就放右邊。底部對齊按鈕下方一些。"""
        screen = QGuiApplication.screenAt(button.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        height = min(760, area.height() - 40)
        self.setFixedHeight(height)
        x = button.left() - PANEL_WIDTH - 6
        if x < area.left():
            x = button.right() + 6
        bottom = min(button.bottom() + 260, area.bottom())
        y = max(area.top(), bottom - height)
        self.move(x, y)
