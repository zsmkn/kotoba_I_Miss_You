"""深色圓角右鍵選單，支援帶開關的項目。"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QWidget, QWidgetAction

from . import theme
from .widgets import ToggleSwitch


def styled_menu(title: str = "", parent: QWidget | None = None) -> QMenu:
    menu = QMenu(title, parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(theme.MENU_QSS)
    return menu


def add_submenu(parent: QMenu, title: str) -> QMenu:
    sub = styled_menu(title, parent)
    parent.addMenu(sub)
    return sub


class ToggleRow(QWidget):
    def __init__(self, text: str, checked: bool, on_toggle: Callable[[bool], None]):
        super().__init__()
        self.setAttribute(Qt.WA_Hover)
        self.setStyleSheet(f"""
            ToggleRow {{ border-radius: 8px; }}
            ToggleRow:hover {{ background: #3c3a52; }}
            QLabel {{ color: {theme.TEXT_LIGHT}; font-size: 11pt; background: transparent; }}
        """)
        self.setAttribute(Qt.WA_StyledBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 7, 10, 7)
        row.addWidget(QLabel(text))
        row.addStretch(1)
        self.switch = ToggleSwitch(checked)
        self.switch.toggled.connect(on_toggle)
        row.addWidget(self.switch)

    def mouseReleaseEvent(self, e) -> None:
        # 點整列都能切換，而且選單不會因此關閉
        if e.button() == Qt.LeftButton and not self.switch.underMouse():
            self.switch.toggle()


def add_toggle(menu: QMenu, text: str, checked: bool, on_toggle: Callable[[bool], None]) -> ToggleRow:
    row = ToggleRow(text, checked, on_toggle)
    action = QWidgetAction(menu)
    action.setDefaultWidget(row)
    menu.addAction(action)
    return row
