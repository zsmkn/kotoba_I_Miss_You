"""配色與共用樣式。色調參考原版：深藍紫底、淡紫卡片、紫色訊息氣泡。"""
from __future__ import annotations

ACCENT = "#7f81ee"          # 她的訊息氣泡、主要按鈕
ACCENT_2 = "#a48df2"        # 漸層另一端
LIGHT = "#ebeafc"           # 淡紫卡片
LIGHT_2 = "#dcdbf5"         # 卡片內的晶片
TEXT_DARK = "#3d3a5a"
TEXT_MUTED = "#8c8ab8"
DARK = "#1f1d2e"            # 深色面板
DARK_2 = "#2a2838"
DARK_BORDER = "#3d3b52"
TEXT_LIGHT = "#ecebf7"
TEXT_LIGHT_MUTED = "#a9a7c6"
PINK = "#f28bb4"

FONT_FAMILY = "Microsoft JhengHei UI"

MENU_QSS = f"""
QMenu {{
    background: {DARK_2};
    border: 1px solid {DARK_BORDER};
    border-radius: 12px;
    padding: 8px;
    color: {TEXT_LIGHT};
    font-size: 11pt;
}}
QMenu::item {{ padding: 9px 26px 9px 14px; border-radius: 8px; }}
QMenu::item:selected {{ background: #3c3a52; }}
QMenu::item:disabled {{ color: {TEXT_LIGHT_MUTED}; }}
QMenu::separator {{ height: 1px; background: {DARK_BORDER}; margin: 6px 10px; }}
QMenu::indicator {{ width: 0px; }}
"""

SCROLL_QSS = """
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 4px 0; }
QScrollBar::handle:vertical { background: rgba(160, 158, 210, 120); border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; background: none; }
"""
