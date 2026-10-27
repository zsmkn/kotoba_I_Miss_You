"""聊天面板與主面板共用的元件：訊息氣泡、通知卡、開關、訊息列表。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme

BUBBLE_MAX_WIDTH = 380


class ToggleSwitch(QAbstractButton):
    """iOS 風格的開關（右鍵選單與設定頁用）。"""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self, duration=140, easingCurve=QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_knob(self) -> float:
        return self._pos

    def _set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height()).adjusted(1, 1, -1, -1)
        off, on = QColor("#5b5970"), QColor(theme.ACCENT_2)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 4
        x = r.left() + 2 + (r.width() - d - 4) * self._pos
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, r.top() + 2, d, d))


class Bubble(QLabel):
    PAD_X, PAD_Y = 18, 10

    def __init__(self, text: str, role: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if role == "user":
            bg, fg = "#f6f5ff", theme.TEXT_DARK
        else:
            bg, fg = theme.ACCENT, "white"
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:20px;"
            f" padding:{self.PAD_Y}px {self.PAD_X}px; font-size:11pt;"
        )
        self.set_text(text)

    def set_text(self, text: str) -> None:
        # 會換行的 QLabel 在版面裡常被壓到最窄，所以依文字長度自己決定寬度
        self.setText(text)
        self.ensurePolished()
        width = self.fontMetrics().horizontalAdvance(text) + 2 * self.PAD_X + 4
        self.setFixedWidth(max(min(width, BUBBLE_MAX_WIDTH), 44))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)


class ImageBubble(QLabel):
    """她傳來的照片：圓角縮圖，點一下用系統看圖程式打開原圖。"""

    WIDTH = 260

    def __init__(self, path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.path = path
        source = QPixmap(str(path))
        if source.isNull():
            self.setText("（照片遺失了……）")
            self.setStyleSheet(f"color:{theme.TEXT_MUTED};")
            return
        scaled = source.scaledToWidth(self.WIDTH, Qt.SmoothTransformation)
        rounded = QPixmap(scaled.size())
        rounded.fill(Qt.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(rounded.rect()), 16, 16)
        p.setClipPath(clip)
        p.drawPixmap(0, 0, scaled)
        p.end()
        self.setPixmap(rounded)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("點一下看原圖")

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))


PHOTO_LINE = ("[傳了一張照片", "[传了一张照片")


def _notice_kind(text: str) -> tuple[str, str | None, str | None]:
    """依通知文字決定圖示與連結（原版的 metadata 沒有匯出，只能從文字判斷）。"""
    if "日記" in text or "日记" in text:
        return "📘", "查看日記", "diary"
    if "好感" in text and "提升" in text:
        return "💗", "查看", "favor"
    if "查了" in text:
        return "🔍", None, None
    if any(k in text for k in ("工作完成", "學習結束", "休息好了", "陪伴")):
        return "⏱", None, None
    if "郵件" in text or "邮件" in text:
        return "✉", None, None
    return "✦", None, None


class NoticeCard(QFrame):
    link_activated = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        icon, link_text, target = _notice_kind(text)
        self.setStyleSheet(f"NoticeCard {{ background:{theme.LIGHT}; border-radius:22px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 18, 8)
        row.setSpacing(10)
        badge = QLabel(icon)
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"background:{theme.LIGHT_2}; border-radius:15px; font-size:12pt;")
        label = QLabel(text)
        label.setStyleSheet(f"color:{theme.TEXT_MUTED}; font-size:10.5pt;")
        row.addWidget(badge)
        row.addWidget(label)
        if link_text and target:
            link = QPushButton(f"{link_text} ›")
            link.setCursor(Qt.PointingHandCursor)
            link.setStyleSheet(
                f"QPushButton {{ color:{theme.TEXT_MUTED}; background:transparent; border:none;"
                f" border-bottom:1px solid {theme.TEXT_MUTED}; font-size:10.5pt; padding:0 0 1px 0; }}"
                f"QPushButton:hover {{ color:{theme.ACCENT}; border-bottom-color:{theme.ACCENT}; }}"
            )
            link.clicked.connect(lambda: self.link_activated.emit(target))
            row.addWidget(link)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


class MessageList(QScrollArea):
    """由下往上堆疊的訊息列表；背景透明，只畫氣泡本身。"""

    link_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, date_color: str = theme.TEXT_LIGHT_MUTED):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(theme.SCROLL_QSS)
        self.date_color = date_color
        inner = QWidget()
        inner.setAttribute(Qt.WA_TranslucentBackground)
        self.column = QVBoxLayout(inner)
        self.column.setContentsMargins(4, 4, 10, 4)
        self.column.setSpacing(8)
        self.column.addStretch(1)
        self.setWidget(inner)
        self._thinking: QWidget | None = None
        self._dots = 0
        self._dots_timer = QTimer(self, interval=400, timeout=self._tick_dots)

    # ---- 新增內容 -------------------------------------------------------

    def _row(self, widget: QWidget, align_right: bool) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        if align_right:
            h.addStretch(1)
        h.addWidget(widget)
        if not align_right:
            h.addStretch(1)
        return row

    def _insert(self, widget: QWidget, at_top: bool) -> None:
        if at_top:
            self.column.insertWidget(1, widget)
        else:
            index = self.column.count()
            if self._thinking is not None:
                index -= 1  # 永遠讓「輸入中」留在最下面
            self.column.insertWidget(index, widget)

    def add_message(self, role: str, text: str, *, at_top: bool = False, image: Path | None = None) -> None:
        lines = [line for line in text.splitlines() if line.strip()] or [text]
        if image is not None:
            lines = [line for line in lines if not line.startswith(PHOTO_LINE)]  # 用照片本身取代文字描述
        widgets = [self._row(Bubble(line, role), role == "user") for line in lines]
        if image is not None:
            widgets.append(self._row(ImageBubble(image), role == "user"))
        for w in (reversed(widgets) if at_top else widgets):
            self._insert(w, at_top)

    def add_notice(self, text: str, *, at_top: bool = False) -> None:
        card = NoticeCard(text)
        card.link_activated.connect(self.link_activated)
        self._insert(self._row(card, False), at_top)

    def add_date(self, text: str, *, at_top: bool = False) -> None:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color:{self.date_color}; font-size:9.5pt; padding:6px;")
        self._insert(label, at_top)

    def clear(self) -> None:
        self.set_thinking(False)
        while self.column.count() > 1:
            item = self.column.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

    def set_thinking(self, on: bool) -> None:
        if on and self._thinking is None:
            self._thinking_label = Bubble("‧", "assistant")
            self._thinking = self._row(self._thinking_label, False)
            self.column.addWidget(self._thinking)
            self._dots_timer.start()
            self.scroll_to_bottom()
        elif not on and self._thinking is not None:
            self._dots_timer.stop()
            self._thinking.deleteLater()
            self._thinking = None

    def _tick_dots(self) -> None:
        self._dots = (self._dots + 1) % 3
        self._thinking_label.set_text("‧" * (self._dots + 1))

    def scroll_to_bottom(self) -> None:
        # 版面要等下一輪事件迴圈才會算好高度
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))


def render_history(view: MessageList, rows, data_dir: Path, *, at_top: bool = False, with_dates: bool = True) -> None:
    """把 messages 資料表的列畫進 MessageList；跨日時插入日期。"""
    items: list[tuple[str, object]] = []
    last_date = None
    for r in rows:
        date = r["created_at"][:10]
        if with_dates and date != last_date:
            items.append(("date", date))
            last_date = date
        items.append(("row", r))
    for kind, value in (reversed(items) if at_top else items):
        if kind == "date":
            view.add_date(value, at_top=at_top)
        elif value["role"] == "system":
            view.add_notice(value["text"], at_top=at_top)
        else:
            attachment = value["attachment"] if "attachment" in value.keys() else None
            image = data_dir / attachment if attachment else None
            view.add_message(value["role"], value["text"], at_top=at_top, image=image)
