"""第一次啟動的設定精靈：匯入官方資料、選擇模型來源。給不會用終端機的玩家用。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from .. import db
from ..config import PROVIDER_DEFAULTS, Config, save_config
from ..importer import import_archive
from ..live2d import MODEL_DIR_NAME, PATCHED_MODEL, import_live2d
from . import theme
from .snapshot import render_reference

PROVIDERS = {"ollama": "Ollama（本地，免費）", "gemini": "Google Gemini", "openai": "OpenAI"}

QSS = f"""
QDialog {{ background:{theme.DARK}; }}
QLabel {{ color:{theme.TEXT_LIGHT}; font-size:10.5pt; }}
QLabel#title {{ font-size:18pt; font-weight:bold; }}
QLabel#muted {{ color:{theme.TEXT_LIGHT_MUTED}; font-size:9.5pt; }}
QLabel#error {{ color:{theme.PINK}; }}
QLineEdit, QComboBox {{
    background:#2e2b42; color:{theme.TEXT_LIGHT}; border:1px solid {theme.DARK_BORDER};
    border-radius:8px; padding:6px 10px; font-size:10.5pt;
}}
QComboBox QAbstractItemView {{ background:#2e2b42; color:{theme.TEXT_LIGHT}; selection-background-color:{theme.ACCENT}; }}
QPushButton {{
    background:rgba(255,255,255,22); color:{theme.TEXT_LIGHT}; border:none; border-radius:12px; padding:6px 14px;
}}
QPushButton:hover {{ background:rgba(255,255,255,40); }}
QPushButton#primary {{ background:{theme.ACCENT}; color:white; font-size:11pt; padding:9px 30px; border-radius:16px; }}
QPushButton#primary:hover {{ background:{theme.ACCENT_2}; }}
"""


def needs_memory(cfg: Config) -> bool:
    conn = db.connect(cfg.data_dir)
    try:
        return db.get_persona(conn, "role_prompt") is None
    finally:
        conn.close()


def needs_live2d(cfg: Config) -> bool:
    return not (cfg.data_dir / "live2d" / MODEL_DIR_NAME / PATCHED_MODEL).is_file()


def needs_setup(cfg: Config) -> bool:
    return needs_memory(cfg) or needs_live2d(cfg)


class SetupDialog(QDialog):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Molili Local — 第一次設定")
        self.setStyleSheet(QSS)
        self.setMinimumWidth(560)
        col = QVBoxLayout(self)
        col.setContentsMargins(28, 24, 28, 24)
        col.setSpacing(12)
        title = QLabel("歡迎回來")
        title.setObjectName("title")
        col.addWidget(title)
        intro = QLabel("請選擇官方寄給你的資料。這些檔案只會被複製到這台電腦，不會上傳到任何地方。")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        col.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)
        self.memory = QLineEdit()
        self.memory.setPlaceholderText("molili-memory-archive.zip")
        if needs_memory(cfg):
            form.addRow("記憶封存檔", self._picker(self.memory, zip_only=True))
        self.live2d = QLineEdit()
        self.live2d.setPlaceholderText("Live2D.zip 或解壓縮後的資料夾")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("官方 ZIP 有密碼時填寫；選資料夾則不用")
        if needs_live2d(cfg):
            form.addRow("Live2D 模型", self._picker(self.live2d, zip_only=False))
            form.addRow("ZIP 密碼", self.password)

        self.ask_provider = not cfg.path.exists()
        self.provider = QComboBox()
        for key, name in PROVIDERS.items():
            self.provider.addItem(name, key)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key_label = QLabel("API Key")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        if self.ask_provider:
            form.addRow("聊天模型", self.provider)
            form.addRow(self.api_key_label, self.api_key)
        col.addLayout(form)
        self._provider_changed()

        hint = QLabel("之後都可以在右鍵選單的「設定」裡修改。")
        hint.setObjectName("muted")
        col.addWidget(hint)
        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        col.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        start = QPushButton("開始")
        start.setObjectName("primary")
        start.setCursor(Qt.PointingHandCursor)
        start.clicked.connect(self._start)
        row.addWidget(start)
        col.addLayout(row)

    def _picker(self, edit: QLineEdit, *, zip_only: bool) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        pick_zip = QPushButton("選擇 ZIP")
        pick_zip.clicked.connect(lambda: self._choose_file(edit))
        row.addWidget(pick_zip)
        if not zip_only:
            pick_dir = QPushButton("選擇資料夾")
            pick_dir.clicked.connect(lambda: self._choose_dir(edit))
            row.addWidget(pick_dir)
        return holder

    def _choose_file(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇檔案", str(Path.home()), "ZIP 檔 (*.zip)")
        if path:
            edit.setText(path)

    def _choose_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "選擇資料夾", str(Path.home()))
        if path:
            edit.setText(path)

    def _provider_changed(self) -> None:
        key = self.provider.currentData()
        visible = self.ask_provider and key != "ollama"
        self.api_key.setVisible(visible)
        self.api_key_label.setVisible(visible)
        env = PROVIDER_DEFAULTS[key]["api_key_env"]
        self.api_key.setPlaceholderText(f"或設定環境變數 {env[0]}" if env else "")

    def _start(self) -> None:
        self.error.setText("")
        try:
            if needs_memory(self.cfg):
                path = Path(self.memory.text().strip())
                if not path.exists():
                    raise ValueError("請選擇記憶封存檔")
                conn = db.connect(self.cfg.data_dir)
                try:
                    import_archive(conn, path, self.cfg.data_dir)
                finally:
                    conn.close()
            if needs_live2d(self.cfg):
                path = Path(self.live2d.text().strip())
                if not path.exists():
                    raise ValueError("請選擇 Live2D 模型")
                model = import_live2d(path, self.cfg.data_dir, self.password.text() or None)
                render_reference(model.parent)
            if self.ask_provider:
                key = self.provider.currentData()
                section = {"api_key": self.api_key.text().strip()} if self.api_key.text().strip() else {}
                save_config(self.cfg.path, {"llm": {"provider": key, key: section}})
        except (ValueError, OSError, RuntimeError) as e:
            self.error.setText(str(e))
            return
        self.accept()
