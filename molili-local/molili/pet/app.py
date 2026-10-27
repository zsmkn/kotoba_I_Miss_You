"""桌寵主程式：Live2D 本體、聊天按鈕與面板、頭頂氣泡、右鍵選單、主面板與背景對話引擎。"""
from __future__ import annotations

import os
import sys

import live2d
from PySide6.QtCore import QLockFile, QObject, QPoint, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon

from ..config import Config, load_config, save_config
from ..engine import Reply
from ..live2d import MODEL_DIR_NAME, PATCHED_MODEL, REFERENCE_IMAGE
from ..wardrobe import Outfit
from . import autostart, theme, winapi
from .bubble import SpeechBubble
from .chatpanel import ChatButton, ChatPanel
from .data import PetData
from .idle import IdleLife
from .mainpanel import LANGUAGE_NAMES, MainPanel
from .menu import add_submenu, add_toggle, styled_menu
from .setup import SetupDialog, needs_setup
from .snapshot import render_reference
from .tasks import TaskBadge, TaskDialog, TaskRunner
from .view import PetView
from .worker import CompanionWorker

# 各取景下 1.0 倍時的視窗大小
FRAME_SIZES = {"full": QSize(380, 532), "half": QSize(380, 400)}
ZOOM_STEPS = [0.6, 0.7, 0.8, 0.9, 1.0, 1.15, 1.3, 1.5]
SIZE_PRESETS = {"小": 1, "中": 4, "大": 6}   # 右鍵選單的三段尺寸 → ZOOM_STEPS 索引
DEFAULT_ZOOM = 4
EDGE_SNAP = 0.2          # 視窗中心離螢幕邊緣小於寬度的這個比例就吸附
EDGE_VISIBLE = 0.52      # 吸附後留在螢幕內的比例：身體大半藏到螢幕外，臉剛好完整露出
SIDE_FRAMING = {"full": "全身", "half": "半身"}
DAILY_CHECK_MS = 30 * 60 * 1000
REFERENCE_DEBOUNCE_MS = 1500


def _tray_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.ACCENT))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    font = QFont()
    font.setPixelSize(36)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(pm.rect(), Qt.AlignCenter, "言")
    p.end()
    return QIcon(pm)


class PetController(QObject):
    # 送到背景執行緒的請求
    request_greet = Signal()
    request_chat = Signal(str, str)     # 訊息, 她剛剛在做的事
    request_touch = Signal(str, str)    # 部位, 她剛剛在做的事
    request_idle_chat = Signal(str)
    request_task = Signal(str, str, int)    # 種類, 階段, 分鐘
    request_daily = Signal()
    request_reconfigure = Signal(object)

    def __init__(self, cfg: Config, app: QApplication):
        super().__init__()
        self.cfg = cfg
        self.app = app
        self.name = cfg.chat.agent_name
        self.settings = QSettings("MoliliLocal", "Pet")
        self.data = PetData(cfg.data_dir, cfg.chat.language)
        self.busy = False
        self.ready = False
        self._greeted = False
        self._reconnecting = False
        model_json = cfg.data_dir / "live2d" / MODEL_DIR_NAME / PATCHED_MODEL

        self.view = PetView(model_json)
        self.bubble = SpeechBubble()
        self.button = ChatButton()
        self.panel = ChatPanel(self.name)
        self.main = MainPanel(cfg, self.data, model_json)

        self.view.clicked.connect(self._on_click)
        self.view.moved.connect(self._follow)
        self.view.drag_started.connect(lambda: self._set_edge(None, move=False))
        self.view.drag_finished.connect(self._snap_to_edge)
        self.view.context_requested.connect(lambda pos: self.menu.popup(pos))
        self.view.zoom_requested.connect(lambda d: self._set_zoom(self.zoom + d))
        self.button.clicked.connect(self._toggle_panel)
        self.tasks = TaskRunner()
        self.task_dialog = TaskDialog(self.name)
        self.task_badge = TaskBadge()
        self.tasks.phase_changed.connect(self._on_task_phase)
        self.tasks.ticked.connect(lambda text: self.task_badge.set_text(text, self.tasks.paused))
        self.task_dialog.start_requested.connect(self.tasks.start)
        self.task_badge.pause_clicked.connect(self.tasks.toggle_pause)
        self.task_badge.stop_clicked.connect(self.tasks.stop)
        self.panel.open_tasks.connect(self._open_tasks)
        self.life = IdleLife(
            self.view,
            is_busy=lambda: self.busy or not self.ready or self.tasks.running,
            # 陪伴中不要主動找話題打擾專注
            chat_minutes=lambda: 0 if self.tasks.running else self.cfg.chat.idle_chat_minutes,
        )
        self.life.enabled = self.settings.value("activities", True, type=bool)
        self.life.chat_requested.connect(self.request_idle_chat)
        self.bubble.talking_changed.connect(self.view.set_talking)
        self.bubble.finished.connect(self._done_speaking)
        self.panel.talking_changed.connect(self.view.set_talking)
        self.panel.finished.connect(self._done_speaking)
        self.panel.submitted.connect(self._send)
        self.panel.open_page.connect(self.main.show_page)
        self.main.settings_saved.connect(self._save_settings)
        self.main.outfit_changed.connect(self._change_outfit)
        self.view.set_outfit(self.data.outfit())
        self.model_dir = model_json.parent
        self._reference_timer = QTimer(self, singleShot=True, interval=REFERENCE_DEBOUNCE_MS,
                                       timeout=self._render_reference)
        if not (self.model_dir / REFERENCE_IMAGE).exists():
            self._reference_timer.start()

        self.menu = styled_menu()
        self.menu.aboutToShow.connect(self._populate_menu)
        self.tray = QSystemTrayIcon(_tray_icon())
        self.tray.setToolTip(self.name)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(
            lambda reason: self._toggle_visible() if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

        self.framing = str(self.settings.value("framing", "full"))
        self.zoom = int(self.settings.value("zoom", DEFAULT_ZOOM))
        self.edge: str | None = None
        self.view.set_framing(self.framing)
        self._resize_view(keep_bottom=False)
        self._restore_position()
        self.view.show()
        self.button.show()
        self._apply_topmost(self.settings.value("topmost", True, type=bool))
        self._refresh_status()
        self.panel.load_history(self.data.messages(40), cfg.data_dir)
        self._start_worker()
        self.life.schedule()
        # 跨日檢查：開著過夜時，隔天也會補寫昨天的日記
        self.daily_timer = QTimer(self, timeout=lambda: self.ready and self.request_daily.emit())
        self.daily_timer.start(DAILY_CHECK_MS)

    # ---- 背景對話引擎 ----------------------------------------------------

    def _start_worker(self) -> None:
        self.thread = QThread()
        self.worker = CompanionWorker(self.cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.setup)
        self.request_greet.connect(self.worker.greet)
        self.request_chat.connect(self.worker.chat)
        self.request_touch.connect(self.worker.touch)
        self.request_idle_chat.connect(self.worker.idle_chat)
        self.request_task.connect(self.worker.task)
        self.request_daily.connect(self.worker.daily)
        self.request_reconfigure.connect(self.worker.reconfigure)
        self.worker.ready.connect(self._on_ready)
        self.worker.replied.connect(self._on_reply)
        self.worker.failed.connect(self._on_failed)
        self.worker.busy_changed.connect(self._on_busy)
        self.worker.diary_busy.connect(self._on_diary_busy)
        self.worker.diaries_written.connect(self._on_diaries)
        self.thread.start()

    def _on_ready(self, model: str) -> None:
        self.ready = True
        self.tray.setToolTip(f"{self.name}（{model}）")
        if self._reconnecting:
            self._reconnecting = False
            self.main.settings.show_result(f"已連線：{model}")
        if not self._greeted:
            self._greeted = True
            self.request_greet.emit()
            self.request_daily.emit()  # 排在問候之後（背景執行緒依序處理）

    def _on_busy(self, busy: bool) -> None:
        self.busy = busy
        self.view.touch_enabled = not busy
        self.panel.set_thinking(busy)
        if busy:
            self.life.interrupt()
            self.view.set_state("thinking")
            if not self.panel.isVisible():
                self._follow()
                self.bubble.show_thinking()
        else:
            # 不一定有回覆（例如不需要主動問候）；有的話 _on_reply 會緊接著切到 speaking
            self._back_to_rest()
            if self.bubble.thinking:
                self.bubble.hide()

    def _done_speaking(self) -> None:
        self._back_to_rest()

    def _back_to_rest(self) -> None:
        """說完話或想完之後：陪伴中回到陪伴的動作，否則回到待機。"""
        if self.tasks.running:
            self.view.set_state(self.tasks.target_state())
        else:
            self.view.set_state("idle")
            self.life.schedule()

    def _on_reply(self, r: Reply) -> None:
        self.life.she_spoke()
        self.view.set_emotion(r.expression)
        self.view.set_state("speaking")
        if r.searched:
            self.panel.add_notice(f"{self.name} 用通信器查了 {r.searched} 個網頁")
        if self.panel.isVisible():
            self.bubble.hide()
            self.panel.show_reply(r.messages, image=r.image_path)
        else:
            self.panel.show_reply(r.messages, image=r.image_path, animate=False)
            self._follow()
            extra = ["📷 傳了一張照片，打開聊天看看吧"] if r.image_path else []
            self.bubble.show_messages(r.messages + extra)
            self.button.set_unread(True)
        self.panel.set_suggestions(r.suggestions if self.cfg.chat.suggestions else [])
        for notice in r.notices:
            if notice.startswith("拍照失敗"):
                self.tray.showMessage("Molili", notice, QSystemTrayIcon.Warning, 8000)
            else:
                self.panel.add_notice(notice)
        if r.favor:
            self._refresh_status()
            if r.favor.leveled_up:
                self.tray.showMessage(self.name, f"💗 {self.name}的好感度等級提升到 lv.{r.favor.level}",
                                      QSystemTrayIcon.Information, 5000)
        self.main.refresh_current()

    def _on_diary_busy(self, busy: bool) -> None:
        self.busy = busy
        self.view.touch_enabled = not busy
        self.panel.set_thinking(busy)
        if busy:
            self.life.interrupt()
            self.view.set_state("writing")  # 她正在寫日記
        else:
            self._back_to_rest()

    def _on_diaries(self, dates: list) -> None:
        text = f"{self.name} 寫了一篇關於你的日記"
        for _ in dates:
            self.panel.add_notice(text)
        if not self.panel.isVisible():
            self.button.set_unread(True)
        self.tray.showMessage(self.name, f"📘 {text}（{'、'.join(dates)}）", QSystemTrayIcon.Information, 6000)
        self.main.refresh_current()

    # ---- 換裝 -----------------------------------------------------------

    def _change_outfit(self, outfit: Outfit) -> None:
        self.data.save_outfit(outfit)
        self.view.set_outfit(outfit)
        self._reference_timer.start()  # 連續調整時只在最後重畫一次

    def _render_reference(self) -> None:
        """依目前打扮重畫生圖參考圖，讓她照片裡的樣子和桌面上一致。"""
        try:
            render_reference(self.model_dir, self.data.outfit())
        except RuntimeError as e:
            self.tray.showMessage("Molili", f"無法產生角色參考圖：{e}", QSystemTrayIcon.Warning, 6000)

    # ---- 陪伴（番茄鐘） ----------------------------------------------------

    def _open_tasks(self) -> None:
        if self.tasks.running:
            self.tasks.stop()
        else:
            self.task_dialog.popup_near(self.button.geometry().center())

    def _on_task_phase(self, kind: str, phase: str, minutes: int) -> None:
        if phase == "end":
            self.task_badge.hide()
            self.life.set_enabled(self.settings.value("activities", True, type=bool))
        else:
            if phase == "start":
                self.life.interrupt()
                self.life.set_enabled(False)
                self.task_badge.show()
                self._follow()
            if not self.busy:
                self.view.set_state(self.tasks.target_state())
        self.request_task.emit(kind, phase, minutes)

    def _on_failed(self, message: str) -> None:
        self.view.set_state("idle")
        if self._reconnecting:
            self._reconnecting = False
            self.main.settings.show_result(f"連線失敗：{message}")
        self._follow()
        self.bubble.show_notice("（通訊器好像收不到訊號……）")
        self.tray.showMessage("Molili", message, QSystemTrayIcon.Warning, 8000)

    def _refresh_status(self) -> None:
        self.panel.set_status(self.data.favor(), self.data.days_known())

    # ---- 互動 -----------------------------------------------------------

    def _on_click(self, area: str) -> None:
        """點她身上的觸摸區（照原版：聊天面板只由旁邊的圓形按鈕打開，點身體是戳她）。"""
        if not self.busy:
            self.life.user_interacted()
            situation = self.life.interrupt()
            self.view.set_state("idle")
            self.view.play_touch(area)
            self.request_touch.emit(area, situation)

    def _toggle_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
            self.button.set_open(False)
        else:
            self.bubble.hide()
            self._follow()
            self.panel.place_beside(self.button.geometry())  # _follow 只會移動已顯示的面板
            self.panel.popup()
            self.button.set_open(True)

    def _send(self, text: str) -> None:
        if not self.busy:
            self.life.user_interacted()
            self.panel.add_user(text)
            self.request_chat.emit(text, self.life.interrupt())

    def _follow(self) -> None:
        """按鈕、氣泡、面板跟著她移動。"""
        g = self.view.geometry()
        area = self._screen_area()
        # 聊天按鈕放在她身體旁、胸口高度；探頭時放在露出來的那一側
        rel_x = {"left": 0.86, "right": 0.12}.get(self.edge, 0.14)
        bx = g.x() + int(g.width() * rel_x) - self.button.width() // 2
        by = g.y() + int(g.height() * (0.42 if self.framing == "full" else 0.55))
        bx = max(area.left(), min(bx, area.right() - self.button.width()))
        self.button.move(bx, by)
        if self.task_badge.isVisible():
            self.task_badge.adjustSize()
            tx = bx + self.button.width() // 2 - self.task_badge.width() // 2
            tx = max(area.left(), min(tx, area.right() - self.task_badge.width()))
            self.task_badge.move(tx, by + self.button.height() + 4)
        self.bubble.set_anchor(self.view.head_anchor())
        if self.panel.isVisible():
            self.panel.place_beside(self.button.geometry())

    def _screen_area(self):
        screen = QGuiApplication.screenAt(self.view.geometry().center()) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    # ---- 螢幕邊緣探頭 -----------------------------------------------------

    def _snap_to_edge(self) -> None:
        g = self.view.geometry()
        area = self._screen_area()
        margin = g.width() * EDGE_SNAP
        if g.center().x() < area.left() + margin:
            self._set_edge("left")
        elif g.center().x() > area.right() - margin:
            self._set_edge("right")
        else:
            self._set_edge(None, move=False)
        self.settings.setValue("pos", self.view.pos())

    def _set_edge(self, side: str | None, *, move: bool = True) -> None:
        self.edge = side
        self.view.set_edge(side)
        self.settings.setValue("edge", side or "")
        if move and side:
            g = self.view.geometry()
            area = self._screen_area()
            visible = int(g.width() * EDGE_VISIBLE)
            x = area.left() - (g.width() - visible) if side == "left" else area.right() - visible + 1
            y = max(area.top(), min(g.y(), area.bottom() - g.height() + 1))
            self.view.move(x, y)
        self._follow()

    # ---- 大小、位置、置頂 ---------------------------------------------------

    def _set_zoom(self, zoom: int) -> None:
        zoom = max(0, min(len(ZOOM_STEPS) - 1, zoom))
        if zoom != self.zoom:
            self.zoom = zoom
            self.settings.setValue("zoom", zoom)
            self._resize_view(keep_bottom=True)

    def _set_framing(self, framing: str) -> None:
        if framing != self.framing:
            self.framing = framing
            self.settings.setValue("framing", framing)
            self.view.set_framing(framing)
            self._resize_view(keep_bottom=True)

    def _resize_view(self, *, keep_bottom: bool) -> None:
        old = self.view.geometry()
        base = FRAME_SIZES[self.framing]
        scale = ZOOM_STEPS[self.zoom]
        size = QSize(int(base.width() * scale), int(base.height() * scale))
        self.view.resize(size)
        if keep_bottom:  # 以腳底中心為基準縮放，看起來像站在原地變大變小
            self.view.move(old.center().x() - size.width() // 2, old.bottom() - size.height())
            if self.edge:
                self._set_edge(self.edge)
        self._follow()

    def _restore_position(self) -> None:
        pos = self.settings.value("pos")
        if isinstance(pos, QPoint) and any(s.geometry().contains(pos + QPoint(self.view.width() // 2, 10))
                                           for s in self.app.screens()):
            self.view.move(pos)
        else:
            self._move_to_screen(self.app.primaryScreen())
        edge = self.settings.value("edge", "")
        if edge in ("left", "right"):
            self._set_edge(edge)

    def _move_to_screen(self, screen) -> None:
        area = screen.availableGeometry()
        self._set_edge(None, move=False)
        self.view.move(area.right() - self.view.width() - 40, area.bottom() - self.view.height())
        self._follow()

    def _apply_topmost(self, on: bool) -> None:
        self.settings.setValue("topmost", on)
        for w in (self.view, self.button, self.bubble, self.panel):
            winapi.set_topmost(int(w.winId()), on)

    # ---- 選單 -----------------------------------------------------------

    def _populate_menu(self) -> None:
        m = self.menu
        m.clear()
        m.addAction("❖  打開主面板", lambda: self.main.show_page("overview"))
        m.addAction("💬  對話記錄", lambda: self.main.show_page("history"))
        m.addAction("📖  日記", lambda: self.main.show_page("diary"))
        m.addAction("⏱  " + ("結束陪伴" if self.tasks.running else "陪伴"), self._open_tasks)
        m.addAction("👗  衣櫃", lambda: self.main.show_page("wardrobe"))
        m.addSeparator()
        add_toggle(m, "📌  桌面角色置頂", self.settings.value("topmost", True, type=bool), self._apply_topmost)
        add_toggle(m, "✎  智慧推薦回覆", self.cfg.chat.suggestions,
                   lambda on: self._save_settings({"chat": {"suggestions": on}}, quiet=True))
        add_toggle(m, "📚  待機小動作", self.life.enabled, self._set_activities)
        add_toggle(m, "🚀  開機自動啟動", autostart.is_enabled(), self._set_autostart)
        add_toggle(m, "💭  主動找話題", self.cfg.chat.idle_chat_minutes > 0,
                   lambda on: self._save_settings(
                       {"chat": {"idle_chat_minutes": float(self.settings.value("idle_chat_minutes", 40)) if on else 0.0}},
                       quiet=True))

        preset = next((k for k, v in SIZE_PRESETS.items() if v == self.zoom), "自訂")
        size = add_submenu(m, f"🔳  角色顯示尺寸　　{preset} / {SIDE_FRAMING[self.framing]}")
        for label, index in SIZE_PRESETS.items():
            size.addAction(("✓  " if index == self.zoom else "     ") + label, lambda i=index: self._set_zoom(i))
        size.addSeparator()
        for key, label in SIDE_FRAMING.items():
            size.addAction(("✓  " if key == self.framing else "     ") + label, lambda k=key: self._set_framing(k))

        screens = QGuiApplication.screens()
        if len(screens) > 1:
            current = QGuiApplication.screenAt(self.view.geometry().center())
            sub = add_submenu(m, f"🖥  多屏切換　　{current.name() if current else ''}")
            for s in screens:
                sub.addAction(("✓  " if s is current else "     ") + s.name(), lambda s=s: self._move_to_screen(s))

        lang = add_submenu(m, f"文  角色文本　　{LANGUAGE_NAMES.get(self.cfg.chat.language, '')}")
        for key, label in LANGUAGE_NAMES.items():
            lang.addAction(("✓  " if key == self.cfg.chat.language else "     ") + label,
                           lambda k=key: self._save_settings({"chat": {"language": k}}, quiet=True))
        m.addSeparator()
        m.addAction("👁  " + ("隱藏角色" if self.view.isVisible() else "顯示角色"), self._toggle_visible)
        m.addAction("⚙  設定", lambda: self.main.show_page("settings"))
        m.addAction("✕  退出 Molili", self.quit)

    def _set_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as e:
            self.tray.showMessage("Molili", f"無法設定開機自動啟動：{e}", QSystemTrayIcon.Warning, 6000)

    def _set_activities(self, on: bool) -> None:
        self.settings.setValue("activities", on)
        self.life.set_enabled(on)

    def _toggle_visible(self) -> None:
        if self.view.isVisible():
            for w in (self.view, self.bubble, self.button, self.panel, self.task_badge, self.task_dialog):
                w.hide()
            self.button.set_open(False)
        else:
            self.view.show()
            self.button.show()
            if self.tasks.running:
                self.task_badge.show()
            self._follow()

    # ---- 設定 -----------------------------------------------------------

    def _save_settings(self, updates: dict, *, quiet: bool = False) -> None:
        if self.cfg.chat.idle_chat_minutes > 0:
            # 記住關閉前的分鐘數，從選單重新開啟時沿用
            self.settings.setValue("idle_chat_minutes", self.cfg.chat.idle_chat_minutes)
        save_config(self.cfg.path, updates)
        self.cfg = load_config(self.cfg.path, data_dir=self.cfg.data_dir)
        self.ready = False
        self.main.settings.cfg = self.cfg
        self.data.language = self.cfg.chat.language
        if not self.cfg.chat.suggestions:
            self.panel.set_suggestions([])
        self._reconnecting = not quiet
        if not quiet:
            self.main.settings.show_result("已儲存，正在重新連線…")
        self.request_reconfigure.emit(self.cfg)

    def quit(self) -> None:
        if not self.edge:
            self.settings.setValue("pos", self.view.pos())
        self.tray.hide()
        self.thread.quit()
        self.thread.wait(5000)
        self.worker.wait_background()  # 讓最後一輪的記憶整理寫完
        self.app.quit()


def run(cfg: Config) -> int:
    if settings_dir := os.environ.get("MOLILI_SETTINGS_DIR"):
        # 測試用：視窗位置等偏好改存到指定資料夾，不碰使用者真正的設定
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, settings_dir)
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    # 桌寵和主面板總覽各有一個 Live2D 畫面；共用 GL context 資源，避免 shader 只存在其中一個 context
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    live2d.init()
    live2d.enableLog(False)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    font = QFont(theme.FONT_FAMILY)
    font.setPointSize(10)
    app.setFont(font)
    app.setWindowIcon(_tray_icon())

    # 同一份資料只允許一隻她（例如開機自動啟動後又手動打開）
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(cfg.data_dir / ".pet.lock"))
    if not lock.tryLock(200):
        QMessageBox.information(None, "Molili", f"{cfg.chat.agent_name}已經在桌面上了，可以從右下角的系統匣圖示找到她。")
        return 0

    if needs_setup(cfg):
        if SetupDialog(cfg).exec() != QDialog.Accepted:
            return 0
        cfg = load_config(cfg.path, data_dir=cfg.data_dir)
    controller = PetController(cfg, app)  # noqa: F841  保持參照，避免被回收
    code = app.exec()
    live2d.dispose()
    lock.unlock()
    return code
