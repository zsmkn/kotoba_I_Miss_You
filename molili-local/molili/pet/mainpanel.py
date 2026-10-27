"""主面板：側邊欄＋總覽、好感度、對話、日記、背包、設定。

原版還有技能、商店（星塵、會員）等遊戲經濟系統，本地版沒有對應功能，所以不做。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..config import IMAGE_DEFAULTS, PROVIDER_DEFAULTS, Config, read_raw
from ..wardrobe import ACCESSORIES, DRESS_COLORS, HAIR_COLORS, MISSING_OUTFITS, Outfit
from ..prompt import LANGUAGE_RULES, favor_stage
from . import theme
from .data import PetData
from .view import Live2DCanvas
from .widgets import MessageList, ToggleSwitch, render_history

PAGES = [
    ("overview", "❖  總覽"),
    ("favor", "💗  好感度"),
    ("history", "💬  對話"),
    ("diary", "📖  日記"),
    ("bag", "🎒  背包"),
    ("wardrobe", "👗  衣櫃"),
    ("settings", "⚙  設定"),
]
CATEGORY_NAMES = {"clothing": "服裝", "accessory": "飾品", "gift": "禮物", "function": "道具"}
EFFECT_NAMES = {"favor": "好感", "energy": "電量"}
PROVIDER_NAMES = {"ollama": "Ollama（本地）", "gemini": "Google Gemini", "openai": "OpenAI／相容服務"}
IMAGE_PROVIDER_NAMES = {
    "none": "關閉",
    "openai": "OpenAI（gpt-image）",
    "gemini": "Google Gemini",
    "sd-webui": "Stable Diffusion WebUI（本地）",
}
LANGUAGE_NAMES = {"zh-TW": "繁體中文", "zh-CN": "简体中文", "ja": "日本語", "en": "English"}
STAGES = [
    (1, 9, "好奇的貓", "客氣但很好奇，想拉近距離；回答私人問題前會猶豫。"),
    (10, 69, "信賴的共事者", "公式化的外殼開始融化，會笨拙地關心你、開親密的玩笑。"),
    (70, 100, "唯一的理解者", "卸下防備，直接表達思念、擔憂與喜悅，和你分享整個內心世界。"),
]
HISTORY_PAGE = 80

QSS = f"""
QFrame#frame {{ background:{theme.DARK}; border-radius:16px; }}
QFrame#sidebar {{ background:#232134; border-top-left-radius:16px; border-bottom-left-radius:16px; }}
QWidget#content {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #241f36, stop:1 #3a2d52);
    border-top-right-radius:16px; border-bottom-right-radius:16px;
}}
QPushButton#nav {{
    color:#cfcdea; background:transparent; border:none; border-radius:24px;
    padding:13px 26px; text-align:left; font-size:13pt;
}}
QPushButton#nav:hover {{ background:rgba(255,255,255,18); }}
QPushButton#nav:checked {{
    color:white; font-weight:bold;
    background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {theme.ACCENT}, stop:1 {theme.ACCENT_2});
}}
QPushButton#winbtn {{ color:{theme.TEXT_LIGHT}; background:transparent; border:none; font-size:14pt; padding:4px 12px; }}
QPushButton#winbtn:hover {{ background:rgba(255,255,255,25); border-radius:6px; }}
QFrame#card {{ background:rgba(12,10,22,150); border-radius:10px; }}
QFrame#card[current="true"] {{ border:1.5px solid {theme.ACCENT_2}; }}
QLabel {{ color:{theme.TEXT_LIGHT}; font-size:11pt; background:transparent; }}
QLabel#muted {{ color:{theme.TEXT_LIGHT_MUTED}; font-size:10pt; }}
QLabel#title {{ font-size:28pt; font-weight:bold; }}
QLabel#h2 {{ font-size:15pt; font-weight:bold; }}
QLabel#big {{ font-size:20pt; font-weight:bold; }}
QLabel#tag {{ background:{theme.ACCENT}; color:white; border-radius:9px; padding:2px 9px; font-size:9pt; }}
QLabel#chip {{ background:rgba(127,129,238,60); border-radius:12px; padding:5px 12px; font-size:10pt; }}
QPushButton#chipbtn {{
    background:rgba(255,255,255,18); color:{theme.TEXT_LIGHT}; border:1.5px solid transparent;
    border-radius:14px; padding:6px 12px; font-size:10pt;
}}
QPushButton#chipbtn:checked {{ border-color:{theme.ACCENT_2}; background:rgba(127,129,238,70); }}
QProgressBar {{ background:#3a3850; border:none; border-radius:4px; max-height:8px; }}
QProgressBar::chunk {{ background:qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {theme.PINK}, stop:1 {theme.ACCENT_2}); border-radius:4px; }}
QPushButton#primary {{
    background:{theme.ACCENT}; color:white; border:none; border-radius:18px; padding:9px 26px; font-size:11pt;
}}
QPushButton#primary:hover {{ background:{theme.ACCENT_2}; }}
QPushButton#secondary {{
    background:rgba(255,255,255,20); color:{theme.TEXT_LIGHT}; border:none; border-radius:14px; padding:6px 16px;
}}
QPushButton#secondary:hover {{ background:rgba(255,255,255,40); }}
QLineEdit, QComboBox, QDoubleSpinBox {{
    background:#2e2b42; color:{theme.TEXT_LIGHT}; border:1px solid {theme.DARK_BORDER};
    border-radius:8px; padding:6px 10px; font-size:10.5pt; min-height:22px;
}}
QComboBox QAbstractItemView {{ background:#2e2b42; color:{theme.TEXT_LIGHT}; selection-background-color:{theme.ACCENT}; }}
QListWidget {{
    background:rgba(12,10,22,120); border:none; border-radius:10px; color:{theme.TEXT_LIGHT};
    font-size:11pt; padding:6px; outline:none;
}}
QListWidget::item {{ padding:9px 12px; border-radius:8px; }}
QListWidget::item:selected {{ background:{theme.ACCENT}; color:white; }}
QTextBrowser {{
    background:#f6f1e7; color:#4a3f35; border:none; border-radius:10px; padding:26px 30px;
    font-size:12pt;
}}
{theme.SCROLL_QSS}
"""


def _card(*widgets: QWidget, spacing: int = 6, margins=(20, 14, 20, 14)) -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    box = QVBoxLayout(card)
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    for w in widgets:
        box.addWidget(w)
    return card


def _label(text: str = "", name: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _stat_row(title: str) -> tuple[QFrame, QLabel]:
    value = _label("", "big")
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(_label(title))
    h.addStretch(1)
    h.addWidget(value)
    return _card(row), value


def _flow(widgets: list[QWidget], columns: int) -> QWidget:
    holder = QWidget()
    grid = QGridLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(8)
    for i, w in enumerate(widgets):
        grid.addWidget(w, i // columns, i % columns)
    return holder


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


class OverviewPage(QWidget):
    def __init__(self, model_json: Path, agent_name: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 40, 30)
        self.canvas = Live2DCanvas(model_json, framing="half")
        self.canvas.setAttribute(Qt.WA_AlwaysStackOnTop)  # 讓透明背景透出後面的漸層
        self.canvas.setMinimumWidth(460)
        row.addWidget(self.canvas, 3)

        col = QVBoxLayout()
        col.setSpacing(12)
        col.addSpacing(30)
        col.addWidget(_label(agent_name, "title"))
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:rgba(255,255,255,60);")
        col.addWidget(line)
        self.favor_value = _label("", "big")
        self.favor_level = _label("", "muted")
        self.favor_bar = QProgressBar()
        self.favor_bar.setTextVisible(False)
        head = QWidget()
        h = QHBoxLayout(head)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(_label("💗 好感度"))
        h.addWidget(self.favor_level)
        h.addStretch(1)
        h.addWidget(self.favor_value)
        col.addWidget(_card(head, self.favor_bar))
        card, self.days = _stat_row("📅 相識天數")
        col.addWidget(card)
        card, self.chats = _stat_row("💬 對話次數")
        col.addWidget(card)
        card, self.diaries = _stat_row("📖 日記數量")
        col.addWidget(card)
        self.wardrobe = QVBoxLayout()
        wardrobe_card = _card(_label("👗 擁有的服裝與飾品"))
        wardrobe_card.layout().addLayout(self.wardrobe)
        col.addWidget(wardrobe_card)
        col.addStretch(1)
        row.addLayout(col, 2)

    def refresh(self, data: PetData) -> None:
        self.canvas.set_outfit(data.outfit())
        if f := data.favor():
            self.favor_level.setText(f"lv.{f.level}")
            self.favor_value.setText(f"{f.progress}/{f.cost}")
            self.favor_bar.setRange(0, f.cost)
            self.favor_bar.setValue(f.progress)
        self.days.setText(f"第 {data.days_known()} 天")
        self.chats.setText(f"{data.chat_count()} 次")
        self.diaries.setText(f"{data.diary_count()} 頁")
        _clear(self.wardrobe)
        names = [r["name"] for r in data.items() if r["category"] in ("clothing", "accessory")]
        chips = [_label(n, "chip") for n in names]
        self.wardrobe.addWidget(_flow(chips, 3))


class FavorPage(QWidget):
    def __init__(self, agent_name: str):
        super().__init__()
        self.agent_name = agent_name
        col = QVBoxLayout(self)
        col.setContentsMargins(40, 10, 40, 30)
        col.setSpacing(14)
        col.addWidget(_label("好感度", "title"))
        self.level = _label("", "big")
        self.stage = _label("", "muted")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.progress = _label("", "muted")
        col.addWidget(_card(self.level, self.stage, self.bar, self.progress, spacing=10, margins=(24, 20, 24, 20)))
        col.addWidget(_label("關係階段", "h2"))
        self.stage_cards: list[QFrame] = []
        for lo, hi, name, desc in STAGES:
            card = _card(_label(f"Lv.{lo}～{hi}　「{name}」", "h2"), _label(desc, "muted", wrap=True))
            self.stage_cards.append(card)
            col.addWidget(card)
        col.addWidget(_label("和她聊天會增加好感度；等級越高，她的態度會從好奇、傲嬌，慢慢變得依賴與親近。", "muted", wrap=True))
        col.addStretch(1)

    def refresh(self, data: PetData) -> None:
        f = data.favor()
        if not f:
            return
        self.level.setText(f"Lv.{f.level}")
        self.stage.setText(f"{self.agent_name}目前處於{favor_stage(f.level)}　·　累計好感 {f.total}")
        self.bar.setRange(0, f.cost)
        self.bar.setValue(f.progress)
        self.progress.setText(f"距離下一級：{f.progress} / {f.cost}")
        for card, (lo, hi, *_rest) in zip(self.stage_cards, STAGES):
            card.setProperty("current", lo <= f.level <= hi)
            card.style().unpolish(card)
            card.style().polish(card)


class HistoryPage(QWidget):
    link_activated = Signal(str)

    def __init__(self):
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(40, 10, 30, 20)
        top = QHBoxLayout()
        top.addWidget(_label("對話紀錄", "title"))
        top.addStretch(1)
        self.more = QPushButton("載入更早的對話")
        self.more.setObjectName("secondary")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.clicked.connect(self._load_more)
        top.addWidget(self.more)
        col.addLayout(top)
        self.list = MessageList()
        self.list.link_activated.connect(self.link_activated)
        col.addWidget(self.list, 1)
        self.data: PetData | None = None
        self.oldest_id: int | None = None

    def refresh(self, data: PetData) -> None:
        self.data = data
        rows = data.messages(HISTORY_PAGE)
        self.list.clear()
        render_history(self.list, rows, self.data.data_dir)
        self.oldest_id = rows[0]["id"] if rows else None
        self.more.setEnabled(len(rows) == HISTORY_PAGE)
        self.list.scroll_to_bottom()

    def _load_more(self) -> None:
        if self.data is None or self.oldest_id is None:
            return
        bar = self.list.verticalScrollBar()
        before = bar.maximum() - bar.value()
        rows = self.data.messages(HISTORY_PAGE, before_id=self.oldest_id)
        render_history(self.list, rows, self.data.data_dir, at_top=True)
        self.oldest_id = rows[0]["id"] if rows else self.oldest_id
        self.more.setEnabled(len(rows) == HISTORY_PAGE)
        # 保持目前看到的位置不跳動
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - before))


class DiaryPage(QWidget):
    def __init__(self, agent_name: str):
        super().__init__()
        self.agent_name = agent_name
        col = QVBoxLayout(self)
        col.setContentsMargins(40, 10, 40, 30)
        col.addWidget(_label(f"{agent_name}的日記", "title"))
        row = QHBoxLayout()
        row.setSpacing(16)
        self.dates = QListWidget()
        self.dates.setFixedWidth(170)
        self.dates.currentRowChanged.connect(self._show)
        self.reader = QTextBrowser()
        row.addWidget(self.dates)
        row.addWidget(self.reader, 1)
        col.addLayout(row, 1)
        self.entries: list = []

    def refresh(self, data: PetData) -> None:
        self.entries = data.diaries()
        self.dates.clear()
        self.dates.addItems([e["date"] for e in self.entries])
        if self.entries:
            self.dates.setCurrentRow(0)

    def _show(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            e = self.entries[index]
            paragraphs = "".join(f"<p style='line-height:170%; margin:0 0 12px 0'>{p}</p>"
                                 for p in e["content"].split("\n") if p.strip())
            self.reader.setHtml(f"<h3 style='color:#8a6f5a'>{e['date']}</h3>{paragraphs}")


class BagPage(QWidget):
    def __init__(self):
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(40, 10, 30, 20)
        col.addWidget(_label("背包", "title"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.grid = QGridLayout(inner)
        self.grid.setSpacing(12)
        self.grid.setContentsMargins(0, 0, 10, 0)
        scroll.setWidget(inner)
        col.addWidget(scroll, 1)

    def refresh(self, data: PetData) -> None:
        _clear(self.grid)
        for i, item in enumerate(data.items()):
            title = QWidget()
            h = QHBoxLayout(title)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(_label(item["name"], "h2"))
            h.addWidget(_label(CATEGORY_NAMES.get(item["category"], item["category"] or "其他"), "tag"))
            h.addStretch(1)
            if (item["quantity"] or 1) > 1:
                h.addWidget(_label(f"×{item['quantity']}", "muted"))
            parts = [title, _label(item["description"] or "", "muted", wrap=True)]
            effects = json.loads(item["effects"] or "[]")
            if effects:
                text = "　".join(f"{EFFECT_NAMES.get(e.get('effectType'), e.get('effectType'))} +{e.get('value')}" for e in effects)
                parts.append(_label(text))
            card = _card(*parts)
            self.grid.addWidget(card, i // 2, i % 2)
        self.grid.setRowStretch(self.grid.rowCount(), 1)


def _swatch(rgb: tuple[float, float, float] | None, base: QColor) -> QIcon:
    """顏色樣本：原色顯示基底色，其他顯示乘色後的大致效果。"""
    color = base if rgb is None else QColor(int(base.red() * rgb[0]), int(base.green() * rgb[1]), int(base.blue() * rgb[2]))
    pm = QPixmap(18, 18)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(255, 255, 255, 160))
    p.drawEllipse(1, 1, 16, 16)
    p.end()
    return QIcon(pm)


class WardrobePage(QWidget):
    """衣櫃：飾品開關、洋裝與頭髮染色。改動會即時套用到桌寵並存起來。"""

    outfit_changed = Signal(object)

    def __init__(self, model_json: Path):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 30, 20)
        self.preview = Live2DCanvas(model_json, framing="full")
        self.preview.setAttribute(Qt.WA_AlwaysStackOnTop)
        self.preview.setMinimumWidth(360)
        row.addWidget(self.preview, 2)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 10, 0)
        col.setSpacing(14)
        scroll.setWidget(inner)
        row.addWidget(scroll, 3)
        col.addWidget(_label("衣櫃", "title"))

        grid = QGridLayout()
        grid.setSpacing(10)
        self.toggles: dict[str, ToggleSwitch] = {}
        for i, (expr, _code, name) in enumerate(ACCESSORIES):
            cell = QHBoxLayout()
            cell.addWidget(_label(name))
            cell.addStretch(1)
            switch = ToggleSwitch()
            switch.toggled.connect(self._changed)
            self.toggles[expr] = switch
            cell.addWidget(switch)
            grid.addLayout(cell, i // 2, i % 2)
        col.addWidget(_card(_label("飾品", "h2"), _wrap_layout(grid), spacing=12))

        self.dress = self._palette(col, "洋裝染色　「銀月貓祈」", DRESS_COLORS, QColor("#b9b2e8"))
        self.hair = self._palette(col, "髮色", HAIR_COLORS, QColor("#e4e2ee"))
        self.missing = _label("", "muted", wrap=True)
        col.addWidget(_card(_label("其他服裝", "h2"), self.missing))
        col.addStretch(1)
        self._loading = False

    def _palette(self, col, title: str, palette: dict, base: QColor) -> QButtonGroup:
        group = QButtonGroup(self)
        line = QHBoxLayout()
        for key, (name, rgb) in palette.items():
            b = QPushButton(name)
            b.setObjectName("chipbtn")
            b.setIcon(_swatch(rgb, base))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("key", key)
            group.addButton(b)
            line.addWidget(b)
        line.addStretch(1)
        group.buttonClicked.connect(self._changed)
        col.addWidget(_card(_label(title, "h2"), _wrap_layout(line)))
        return group

    def refresh(self, data: PetData) -> None:
        self.data = data
        outfit = data.outfit()
        self._loading = True
        for expr, switch in self.toggles.items():
            switch.setChecked(expr in outfit.accessories)
        for group, key in ((self.dress, outfit.dress), (self.hair, outfit.hair)):
            for b in group.buttons():
                b.setChecked(b.property("key") == key)
        self._loading = False
        self.preview.set_outfit(outfit)
        names = "、".join(f"「{n}」" for n in data.item_names(MISSING_OUTFITS))
        self.missing.setText(f"{names}{'：' if names else ''}這些服裝不在官方釋出的 Live2D 模型裡，目前沒辦法換上。")

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        outfit = Outfit(
            accessories=[e for e, s in self.toggles.items() if s.isChecked()],
            dress=self.dress.checkedButton().property("key") if self.dress.checkedButton() else "default",
            hair=self.hair.checkedButton().property("key") if self.hair.checkedButton() else "default",
        )
        self.preview.set_outfit(outfit)
        self.outfit_changed.emit(outfit)


class SettingsPage(QWidget):
    saved = Signal(dict)   # 要寫進 config.toml 的更新

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.raw = read_raw(cfg.path)
        page = QVBoxLayout(self)
        page.setContentsMargins(40, 10, 30, 20)
        page.setSpacing(14)
        page.addWidget(_label("設定", "title"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 10, 0)
        col.setSpacing(14)
        scroll.setWidget(inner)
        page.addWidget(scroll, 1)

        form = _form()
        self.provider = QComboBox()
        for key, name in PROVIDER_NAMES.items():
            self.provider.addItem(name, key)
        self.provider.currentIndexChanged.connect(self._load_provider)
        form.addRow("模型來源", self.provider)

        self.model = QComboBox()
        self.model.setEditable(True)
        self.detect = QPushButton("偵測已安裝模型")
        self.detect.setObjectName("secondary")
        self.detect.setCursor(Qt.PointingHandCursor)
        self.detect.clicked.connect(self._detect_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.detect)
        form.addRow("模型", model_row)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key_label = _label("API Key")
        form.addRow(self.api_key_label, self.api_key)
        self.base_url = QLineEdit()
        form.addRow("服務網址", self.base_url)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        form.addRow("溫度（越高越隨性）", self.temperature)
        col.addWidget(_card(_label("聊天模型", "h2"), _wrap_layout(form), margins=(24, 18, 24, 20)))

        form = _form()
        self.language = QComboBox()
        for key in LANGUAGE_RULES:
            self.language.addItem(LANGUAGE_NAMES.get(key, key), key)
        form.addRow("角色文本", self.language)

        self.greet_hours = QDoubleSpinBox()
        self.greet_hours.setRange(0.5, 168)
        self.greet_hours.setSuffix(" 小時")
        form.addRow("多久沒聊會主動問候", self.greet_hours)

        self.idle_chat = QDoubleSpinBox()
        self.idle_chat.setRange(0, 600)
        self.idle_chat.setDecimals(0)
        self.idle_chat.setSingleStep(5)
        self.idle_chat.setSuffix(" 分鐘")
        self.idle_chat.setSpecialValueText("關閉")
        form.addRow("你在電腦前多久沒聊，她會找話題", self.idle_chat)

        self.suggestions = ToggleSwitch()
        form.addRow("智慧推薦回覆", self.suggestions)
        self.summarize = ToggleSwitch()
        form.addRow("每輪對話整理成記憶", self.summarize)
        self.web_search = ToggleSwitch()
        form.addRow("上網搜尋（DuckDuckGo）", self.web_search)
        self.diary = ToggleSwitch()
        form.addRow("每天寫日記", self.diary)
        col.addWidget(_card(_label("她的功能", "h2"), _wrap_layout(form), margins=(24, 18, 24, 20)))

        form = _form()
        self.image_provider = QComboBox()
        for key, name in IMAGE_PROVIDER_NAMES.items():
            self.image_provider.addItem(name, key)
        self.image_provider.currentIndexChanged.connect(self._load_image_provider)
        form.addRow("生圖服務", self.image_provider)
        self.image_model = QLineEdit()
        form.addRow("生圖模型", self.image_model)
        self.image_url = QLineEdit()
        form.addRow("服務網址", self.image_url)
        self.image_key = QLineEdit()
        self.image_key.setEchoMode(QLineEdit.Password)
        self.image_key.setPlaceholderText("留空則沿用聊天模型的 API Key")
        self.image_key_label = _label("API Key")
        form.addRow(self.image_key_label, self.image_key)
        hint = _label("需要「相機」道具；每張照片用掉一捲以太膠片，每天補充到 3 捲。", "muted", wrap=True)
        col.addWidget(_card(_label("拍照／畫圖", "h2"), _wrap_layout(form), hint, margins=(24, 18, 24, 20)))
        col.addStretch(1)

        bottom = QHBoxLayout()
        self.status = _label("", "muted")
        bottom.addWidget(self.status, 1)
        save = QPushButton("儲存並重新連線")
        save.setObjectName("primary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        bottom.addWidget(save)
        page.addLayout(bottom)
        self.refresh()

    def refresh(self, _data: PetData | None = None) -> None:
        self.raw = read_raw(self.cfg.path)
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(self.provider.findData(self.cfg.llm.provider), 0))
        self.provider.blockSignals(False)
        self._load_provider()
        self.temperature.setValue(self.cfg.llm.temperature)
        self.language.setCurrentIndex(max(self.language.findData(self.cfg.chat.language), 0))
        self.greet_hours.setValue(self.cfg.chat.greet_after_hours)
        self.idle_chat.setValue(self.cfg.chat.idle_chat_minutes)
        self.suggestions.setChecked(self.cfg.chat.suggestions)
        self.summarize.setChecked(self.cfg.chat.summarize_memory)
        self.web_search.setChecked(self.cfg.chat.web_search)
        self.diary.setChecked(self.cfg.chat.diary)
        self.image_provider.blockSignals(True)
        self.image_provider.setCurrentIndex(max(self.image_provider.findData(self.cfg.image.provider), 0))
        self.image_provider.blockSignals(False)
        self._load_image_provider()

    def _load_image_provider(self) -> None:
        key = self.image_provider.currentData()
        section = self.raw.get("image", {})
        same = section.get("provider") == key
        defaults = IMAGE_DEFAULTS[key]
        self.image_model.setText((section.get("model") if same else "") or defaults["model"])
        self.image_url.setText((section.get("base_url") if same else "") or defaults["base_url"])
        self.image_key.setText(section.get("api_key", "") if same else "")
        enabled = key != "none"
        needs_key = key in ("openai", "gemini")
        for w in (self.image_model, self.image_url):
            w.setEnabled(enabled)
        self.image_key.setVisible(needs_key)
        self.image_key_label.setVisible(needs_key)
        self.image_model.setPlaceholderText("留空使用 WebUI 目前載入的模型" if key == "sd-webui" else "")

    def _load_provider(self) -> None:
        key = self.provider.currentData()
        defaults = PROVIDER_DEFAULTS[key]
        section = self.raw.get("llm", {}).get(key, {})
        self.model.clear()
        self.model.setEditText(section.get("model") or defaults["model"])
        self.base_url.setText(section.get("base_url") or defaults["base_url"])
        self.api_key.setText(section.get("api_key", ""))
        env = defaults["api_key_env"]
        self.api_key.setPlaceholderText(f"留空則使用環境變數 {env[0]}" if env else "")
        is_ollama = key == "ollama"
        self.api_key.setVisible(not is_ollama)
        self.api_key_label.setVisible(not is_ollama)
        self.detect.setVisible(is_ollama)
        self.status.setText("Ollama 模型留空時會自動使用第一個已安裝的模型" if is_ollama else "")

    def _detect_models(self) -> None:
        base = self.base_url.text().rstrip("/").removesuffix("/v1")
        try:
            models = [m["name"] for m in httpx.get(f"{base}/api/tags", timeout=5).json().get("models", [])]
        except httpx.HTTPError as e:
            self.status.setText(f"連不上 Ollama：{e}")
            return
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setEditText(current or (models[0] if models else ""))
        self.status.setText(f"找到 {len(models)} 個模型" if models else "Ollama 裡還沒有模型，請先 ollama pull")

    def _save(self) -> None:
        key = self.provider.currentData()
        section: dict = {"model": self.model.currentText().strip(), "base_url": self.base_url.text().strip()}
        if key != "ollama" and self.api_key.text().strip():
            section["api_key"] = self.api_key.text().strip()
        self.saved.emit({
            "llm": {"provider": key, "temperature": round(self.temperature.value(), 2), key: section},
            "chat": {
                "language": self.language.currentData(),
                "greet_after_hours": self.greet_hours.value(),
                "idle_chat_minutes": self.idle_chat.value(),
                "suggestions": self.suggestions.isChecked(),
                "summarize_memory": self.summarize.isChecked(),
                "web_search": self.web_search.isChecked(),
                "diary": self.diary.isChecked(),
            },
            "image": self._image_section(),
        })

    def _image_section(self) -> dict:
        section = {
            "provider": self.image_provider.currentData(),
            "model": self.image_model.text().strip(),
            "base_url": self.image_url.text().strip(),
        }
        if section["provider"] in ("openai", "gemini") and self.image_key.text().strip():
            section["api_key"] = self.image_key.text().strip()
        return section

    def show_result(self, text: str) -> None:
        self.status.setText(text)


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setSpacing(12)
    form.setLabelAlignment(Qt.AlignRight)
    return form


def _wrap_layout(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


class MainPanel(QWidget):
    settings_saved = Signal(dict)
    outfit_changed = Signal(object)

    def __init__(self, cfg: Config, data: PetData, model_json: Path):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle(f"{cfg.chat.agent_name} — Molili Local")
        self.resize(1160, 700)
        self.setStyleSheet(QSS)
        self.data = data
        self._drag: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("frame")
        outer.addWidget(frame)
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(18, 70, 18, 30)
        nav.setSpacing(14)
        content = QWidget()
        content.setObjectName("content")
        content.setAttribute(Qt.WA_StyledBackground)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 8, 8, 0)
        title_bar = QHBoxLayout()
        title_bar.addStretch(1)
        for text, slot in (("—", self.showMinimized), ("✕", self.hide)):
            b = QPushButton(text)
            b.setObjectName("winbtn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            title_bar.addWidget(b)
        body.addLayout(title_bar)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        row.addWidget(sidebar)
        row.addWidget(content, 1)

        name = cfg.chat.agent_name
        self.overview = OverviewPage(model_json, name)
        self.history = HistoryPage()
        self.history.link_activated.connect(self.show_page)
        self.settings = SettingsPage(cfg)
        self.settings.saved.connect(self.settings_saved)
        self.wardrobe = WardrobePage(model_json)
        self.wardrobe.outfit_changed.connect(self.outfit_changed)
        self.pages = {
            "overview": self.overview,
            "favor": FavorPage(name),
            "history": self.history,
            "diary": DiaryPage(name),
            "bag": BagPage(),
            "wardrobe": self.wardrobe,
            "settings": self.settings,
        }
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in PAGES:
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav_group.addButton(b)
            self.nav_buttons[key] = b
            nav.addWidget(b)
            self.stack.addWidget(self.pages[key])
        nav.addStretch(1)

    def show_page(self, key: str) -> None:
        page = self.pages.get(key, self.overview)
        page.refresh(self.data)
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key if key in self.pages else "overview"].setChecked(True)
        if not self.isVisible():
            self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def refresh_current(self) -> None:
        if self.isVisible():
            page = self.stack.currentWidget()
            if page is not self.settings:  # 避免蓋掉正在編輯的設定
                page.refresh(self.data)

    # 標題列拖曳（無邊框視窗）
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and e.position().y() < 60:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
