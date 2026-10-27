"""Live2D 渲染。

Live2DCanvas：共用的渲染、表情、動作、嘴型（主面板總覽也用它）。
PetView：桌寵本體——透明置頂視窗、滑鼠穿透、拖曳、螢幕邊緣探頭。
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

import live2d
import numpy as np
from OpenGL import GL
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QBitmap, QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPixmap, QRegion
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from ..wardrobe import Outfit
from . import winapi

FPS = 60
# 觸摸區（模型座標：左、上、右、下），照原版 Unity 的 TouchAll/TouchAreas 換算而來，依序判定，前面的優先。
# 模型的「脸」part 其實包住整顆頭（耳朵到下巴），不能拿來分頭和臉
TOUCH_AREAS = [
    ("face", (-0.095, 0.346, 0.098, 0.265)),   # 眼睛到下巴
    ("head", (-0.106, 0.472, 0.096, 0.337)),   # 耳尖到眼睛
    ("arm", (-0.214, 0.113, -0.093, -0.089)),  # 左手
    ("arm", (0.084, 0.113, 0.204, -0.089)),    # 右手
    ("body", (-0.106, 0.277, 0.096, -0.044)),
    ("leg", (-0.106, -0.036, 0.096, -0.519)),
]
HEAD_TOP = 0.472
ALPHA_THRESHOLD = 16          # 游標下的像素不透明度超過這個值才算「在她身上」
DRAG_THRESHOLD = 5
EMOTION_HOLD_MS = 25_000      # 表情維持多久後回到預設
TOUCH_EXPRESSIONS = {"head": "TouchHead", "face": "PinchFaceAction"}
TOUCH_HOLD_MS = 2_500
# 游標移到可以摸的地方時換成手的圖示：部位 → emoji
TOUCH_CURSORS = {"head": "🖐", "face": "🤏", "arm": "☝", "body": "☝", "leg": "☝"}
CURSOR_SIZE = 32

# 狀態 → (動作群組, 動作編號；None = 群組內隨機)。後四個是待機小動作（對應原版 ST_STUDY / ST_WORK / ST_REST 等）
STATE_MOTIONS = {
    "idle": ("Idle", None),
    "thinking": ("Type", 0),
    "speaking": ("Speak", 0),
    "reading": ("Read", 0),
    "writing": ("Type", 0),
    "music": ("Music", 0),
    "dozing": ("Idle", 1),    # Idle.motion3 才有 ZZZ 的特效動畫
}
# 狀態要拿出的道具或疊加的表情
STATE_PROPS = {"thinking": "TypeWriter", "writing": "TypeWriter", "reading": "ReadBook", "dozing": "Sleep"}

# 取景：(縮放, 垂直位移)。半身把上半身放大置中
FRAMINGS = {"full": (1.0, 0.0), "half": (1.9, -0.95)}

# 螢幕邊緣探頭（原版 GetEdgeState / ST_*_L/R）。
# 原版的 LeftBreath／RightRead 等邊緣專用動作只在遊戲本體裡，官方釋出的模型沒有，所以用參數程序化地做：
# 身體大半藏到螢幕外（app.EDGE_VISIBLE），只稍微往內傾，讓臉露出來；吸附時彈一下、平常輕輕晃、偶爾歪頭往螢幕裡偷看。
EDGE_INWARD = {"left": 1, "right": -1}   # 往螢幕內側的方向（參數正值 = 往畫面右邊）
EDGE_ROTATION = 8.0                       # 太斜會很明顯看出身體是歪的
EDGE_ENTER_S = 0.55                       # 探出來的時間
EDGE_SWAY = (1.0, 5.5)                    # 平常晃動：角度、週期（秒）
EDGE_HEAD_TILT = 5.0                      # 平常就微微歪頭
EDGE_PEEK_EVERY = (7.0, 15.0)             # 多久偷看一次（秒）
EDGE_PEEK_S = 2.4                         # 偷看一次多久
# 偷看時額外疊加的量：(身體傾斜角度, {參數: 值})
EDGE_PEEK = (3.0, {"ParamAngleX": 20.0, "ParamAngleZ": 7.0, "ParamBodyAngleZ": 3.0, "ParamEyeBallX": 0.6})

# 視線跟隨：游標位置先經過平滑再交給模型，數字越大轉頭越慢（秒）
GAZE_SMOOTH_S = 0.45

# macOS／Linux 的遮罩式穿透：每幾幀更新一次輪廓、縮小幾倍計算、往外擴幾格
MASK_EVERY_FRAMES = 15
MASK_DOWNSCALE = 4
MASK_DILATE = 3


class Live2DCanvas(QOpenGLWidget):
    def __init__(self, model_json: Path, parent: QWidget | None = None, *, framing: str = "full"):
        super().__init__(parent)
        self.model_json = model_json
        self.expression_map: dict[str, list[str]] = json.loads(
            (model_json.parent / "expression_map.json").read_text(encoding="utf-8")
        )
        self.model: live2d.Model | None = None
        self.framing = framing
        self.edge: str | None = None
        self._edge_since = 0.0
        self._gaze: tuple[float, float] | None = None
        self._last_frame = time.monotonic()
        self._next_peek = 0.0
        self.outfit = Outfit()
        self._worn: list[str] = []
        self._state = "idle"
        self._emotion: list[str] = []
        self._talking = False
        self._emotion_timer = QTimer(self, singleShot=True, timeout=lambda: self.set_emotion("DEFAULT"))
        self._frame_timer = QTimer(self, timeout=self.update)
        self._frame_timer.start(1000 // FPS)

    # ---- 對外控制 -------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        if state == self._state or self.model is None:
            self._state = state
            return
        if prop := STATE_PROPS.get(self._state):
            self.model.RemoveExpression(prop)
        self._state = state
        if prop := STATE_PROPS.get(state):
            self.model.AddExpression(prop)
        self.model.StopAllMotions()  # 下一幀依新狀態挑動作

    def set_emotion(self, code: str) -> None:
        if self.model is None:
            return
        for e in self._emotion:
            self.model.RemoveExpression(e)
        self._emotion = list(self.expression_map.get(code, []))
        for e in self._emotion:
            self.model.AddExpression(e)
        if self._emotion:
            self._emotion_timer.start(EMOTION_HOLD_MS)

    def set_talking(self, talking: bool) -> None:
        self._talking = talking

    def play_touch(self, area: str) -> None:
        expr = TOUCH_EXPRESSIONS.get(area)
        if self.model is None or expr is None:
            return
        self.model.AddExpression(expr)
        QTimer.singleShot(TOUCH_HOLD_MS, lambda: self.model and self.model.RemoveExpression(expr))

    def set_outfit(self, outfit: Outfit) -> None:
        """換裝：飾品是常駐疊加的表情，染色是部件乘色。模型還沒載入時先記著，載入後再套用。"""
        self.outfit = outfit
        if self.model is None:
            return
        for e in self._worn:
            self.model.RemoveExpression(e)
        self._worn = list(outfit.accessories)
        for e in self._worn:
            self.model.AddExpression(e)
        ids = self.model.GetPartIds()
        for part, rgb in outfit.part_colors().items():
            if part in ids:
                self.model.SetPartMultiplyColor(ids.index(part), *rgb, 1.0)

    def set_framing(self, framing: str) -> None:
        self.framing = framing
        self._apply_transform()

    def set_edge(self, edge: str | None) -> None:
        if edge != self.edge:
            self._edge_since = time.monotonic()
            self._next_peek = self._edge_since + random.uniform(3.0, 6.0)
        self.edge = edge
        self._apply_transform()

    def _apply_transform(self) -> None:
        if self.model is None:
            return
        scale, dy = FRAMINGS[self.framing]
        self.model.SetScale(scale)
        self.model.SetOffset(0.0, dy)
        self.model.Rotate(self._edge_pose(time.monotonic())[0])

    def _edge_pose(self, now: float) -> tuple[float, dict[str, float]]:
        """探頭時這一幀的整體旋轉角度，以及要疊加的參數。"""
        if not self.edge:
            return 0.0, {}
        inward = EDGE_INWARD[self.edge]
        # 吸附時彈出來（ease-out-back，會稍微超過再回來）
        x = min((now - self._edge_since) / EDGE_ENTER_S, 1.0) - 1.0
        enter = 1 + 2.7 * x ** 3 + 1.7 * x ** 2
        amp, period = EDGE_SWAY
        lean = EDGE_ROTATION * enter + amp * math.sin(now * 2 * math.pi / period)
        params = {"ParamAngleZ": EDGE_HEAD_TILT * enter}
        # 偷看：淡入、停一下、淡出；輪到時不在待機（正在說話、看書……）就延到下一次
        t = (now - self._next_peek) / EDGE_PEEK_S
        if t > 1.0 or (0.0 <= t * EDGE_PEEK_S < 0.1 and self._state != "idle"):
            self._next_peek = now + random.uniform(*EDGE_PEEK_EVERY)
        elif t >= 0.0:
            ramp = min(t / 0.3, 1.0, (1.0 - t) / 0.3)
            weight = ramp * ramp * (3 - 2 * ramp)
            extra_lean, extra = EDGE_PEEK
            lean += extra_lean * weight
            for k, v in extra.items():
                params[k] = params.get(k, 0.0) + v * weight
        return -inward * lean, {k: inward * v for k, v in params.items()}

    # 看不見時停止重繪，省 GPU
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._frame_timer.start(1000 // FPS)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._frame_timer.stop()

    # ---- OpenGL ---------------------------------------------------------

    def initializeGL(self) -> None:
        live2d.glInit()
        self.model = live2d.Model()
        self.model.LoadModelJson(str(self.model_json))
        self.model.SetAutoBlink(True)
        self.model.SetAutoBreath(True)
        self._apply_transform()
        self.set_outfit(self.outfit)

    def resizeGL(self, w: int, h: int) -> None:
        if self.model:
            self.model.Resize(w, h)

    def paintGL(self) -> None:
        live2d.clearBuffer()
        m = self.model
        if m is None:
            return
        if m.IsMotionFinished():
            group, index = STATE_MOTIONS[self._state]
            priority = live2d.MotionPriority.IDLE if self._state == "idle" else live2d.MotionPriority.NORMAL
            if index is None:
                m.StartRandomMotion(group, priority)
            else:
                m.StartMotion(group, index, priority)
        cursor = self.mapFromGlobal(QCursor.pos())
        now = time.monotonic()
        dt, self._last_frame = min(now - self._last_frame, 0.1), now
        if self._gaze is None:
            self._gaze = (float(cursor.x()), float(cursor.y()))
        k = 1.0 - math.exp(-dt / GAZE_SMOOTH_S)
        gx, gy = self._gaze
        self._gaze = (gx + (cursor.x() - gx) * k, gy + (cursor.y() - gy) * k)
        m.Drag(*self._gaze)  # 視線慢慢跟上游標
        m.Update()
        # 參數必須在 Update() 之後設定才會生效（Update 一開始會還原參數）
        if self._talking:
            # 沒有語音，用正弦波模擬說話時的嘴型
            m.SetParamById("ParamMouthOpenY", 0.15 + 0.6 * abs(math.sin(time.monotonic() * 13)))
        if self.edge:
            rotation, params = self._edge_pose(time.monotonic())
            m.Rotate(rotation)
            for k, v in params.items():
                m.AddParamById(k, v)  # 疊加在視線跟隨與動作上
        m.Draw()
        self.after_draw(cursor)

    def after_draw(self, cursor: QPoint) -> None:
        pass

    def _mvp(self) -> np.ndarray:
        return np.array(self.model.GetMvp(), dtype=float).reshape(4, 4).T  # live2d-py 給的是 column-major

    def to_model(self, p: QPoint) -> tuple[float, float]:
        """視窗座標 → 模型座標（已含縮放、取景、探頭的旋轉）。"""
        ndc = np.array([2 * p.x() / max(self.width(), 1) - 1, 1 - 2 * p.y() / max(self.height(), 1), 0.0, 1.0])
        x, y = np.linalg.solve(self._mvp(), ndc)[:2]
        return float(x), float(y)

    def to_widget(self, x: float, y: float) -> QPoint:
        v = self._mvp() @ np.array([x, y, 0.0, 1.0])
        return QPoint(int((v[0] + 1) * self.width() / 2), int((1 - v[1]) * self.height() / 2))

    def area_at(self, p: QPoint) -> str | None:
        """游標下的觸摸區；不在任何一區（頭髮、尾巴、裙襬……）就是 None。"""
        if self.model is None:
            return None
        x, y = self.to_model(p)
        for area, (left, top, right, bottom) in TOUCH_AREAS:
            if left <= x <= right and bottom <= y <= top:
                return area
        return None


def _emoji_cursor(emoji: str) -> QCursor:
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    pixmap = QPixmap(int(CURSOR_SIZE * dpr), int(CURSOR_SIZE * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    font = QFont()
    font.setFamilies(["Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"])
    font.setPixelSize(CURSOR_SIZE - 6)
    painter.setFont(font)
    painter.drawText(0, 0, CURSOR_SIZE, CURSOR_SIZE, Qt.AlignCenter, emoji)  # 設了 devicePixelRatio，座標是邏輯尺寸
    painter.end()
    return QCursor(pixmap, CURSOR_SIZE // 2, CURSOR_SIZE // 2)


class PetView(Live2DCanvas):
    clicked = Signal(str)            # head | face | arm | body | leg
    context_requested = Signal(QPoint)
    moved = Signal()
    drag_started = Signal()
    drag_finished = Signal()
    zoom_requested = Signal(int)     # 滾輪方向：+1 放大、-1 縮小

    def __init__(self, model_json: Path):
        super().__init__(model_json)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._cursor_alpha = 0
        self._click_through: bool | None = None
        self._mask_frame = MASK_EVERY_FRAMES
        self._press_global: QPoint | None = None
        self._press_window: QPoint | None = None
        self._dragging = False
        self.touch_enabled = True    # 她在忙（想回覆）時摸不到，游標也不換
        self._touch_cursors = {area: _emoji_cursor(emoji) for area, emoji in TOUCH_CURSORS.items()}
        self._cursor_area: str | None = None
        self._hover_timer = QTimer(self, timeout=self._on_hover)
        self._hover_timer.start(33)

    def head_anchor(self) -> QPoint:
        """頭頂上方的螢幕座標，給對話氣泡定位。"""
        if self.model is None:
            return self.mapToGlobal(QPoint(self.width() // 2, int(self.height() * 0.23)))
        top = self.to_widget(0.0, HEAD_TOP).y() - int(self.height() * 0.02)
        return self.mapToGlobal(QPoint(self.width() // 2, max(top, 0)))

    # ---- 滑鼠穿透 -------------------------------------------------------

    def after_draw(self, cursor: QPoint) -> None:
        if winapi.NATIVE_CLICK_THROUGH:
            self._cursor_alpha = self._alpha_at(cursor)
            return
        self._mask_frame += 1
        if self._mask_frame >= MASK_EVERY_FRAMES and self._press_global is None:
            self._mask_frame = 0
            self._update_mask()

    def _update_mask(self) -> None:
        """macOS／Linux：用她目前的輪廓當視窗遮罩，遮罩外的點擊會穿透到後面的視窗。"""
        dpr = self.devicePixelRatioF()
        fw, fh = int(self.width() * dpr), int(self.height() * dpr)
        if fw <= 0 or fh <= 0:
            return
        data = GL.glReadPixels(0, 0, fw, fh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        pixels = np.frombuffer(data, dtype=np.uint8) if isinstance(data, bytes) else np.asarray(data, dtype=np.uint8)
        alpha = pixels.reshape(fh, fw, 4)[::-1, :, 3]                 # OpenGL 的原點在左下角
        inside = alpha[::MASK_DOWNSCALE, ::MASK_DOWNSCALE] > ALPHA_THRESHOLD
        for _ in range(MASK_DILATE):                                    # 往外擴一點，頭髮擺動時才不會被切掉
            grown = inside.copy()
            grown[1:] |= inside[:-1]
            grown[:-1] |= inside[1:]
            grown[:, 1:] |= inside[:, :-1]
            grown[:, :-1] |= inside[:, 1:]
            inside = grown
        if not inside.any():
            return
        # 遮罩內 = 黑色（QBitmap 的 color1）
        gray = np.where(inside, 0, 255).astype(np.uint8)
        gray = np.ascontiguousarray(gray)
        image = QImage(gray.data, gray.shape[1], gray.shape[0], gray.strides[0], QImage.Format_Grayscale8)
        image = image.scaled(self.width(), self.height())
        bitmap = QBitmap.fromImage(image, Qt.ThresholdDither | Qt.MonoOnly)
        self.setMask(QRegion(bitmap))

    def _alpha_at(self, p: QPoint) -> int:
        if not (0 <= p.x() < self.width() and 0 <= p.y() < self.height()):
            return 0
        dpr = self.devicePixelRatioF()
        fb_h = int(self.height() * dpr)
        x = int(p.x() * dpr)
        y = max(fb_h - 1 - int(p.y() * dpr), 0)
        data = GL.glReadPixels(x, y, 1, 1, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        pixel = np.frombuffer(data, dtype=np.uint8) if isinstance(data, bytes) else np.asarray(data, dtype=np.uint8)
        return int(pixel.reshape(-1)[3])

    def _on_hover(self) -> None:
        if not self.isVisible():
            return
        self._update_click_through()
        self._update_cursor()

    def _update_click_through(self) -> None:
        if not winapi.NATIVE_CLICK_THROUGH:
            return
        pressed = QGuiApplication.mouseButtons() != Qt.NoButton
        over = self._cursor_alpha > ALPHA_THRESHOLD or (pressed and self._press_global is not None)
        if over != (self._click_through is False):
            self._click_through = not over
            winapi.set_click_through(int(self.winId()), self._click_through)

    def _update_cursor(self) -> None:
        """游標在頭上或臉上、而且摸得到時，換成摸頭／捏臉的手。"""
        if self._dragging:
            area = "drag"
        else:
            p = self.mapFromGlobal(QCursor.pos())
            if winapi.NATIVE_CLICK_THROUGH:
                over = self._cursor_alpha > ALPHA_THRESHOLD
            else:
                over = self.rect().contains(p)  # 遮罩模式：遮罩外的游標本來就不歸這個視窗管
            area = self.area_at(p) if over and self.touch_enabled else None
        if area == self._cursor_area:
            return
        self._cursor_area = area
        if area == "drag":
            self.setCursor(Qt.ClosedHandCursor)
        elif area in self._touch_cursors:
            self.setCursor(self._touch_cursors[area])
        else:
            self.unsetCursor()

    # ---- 滑鼠 -----------------------------------------------------------

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._press_window = self.pos()
            self._dragging = False
        elif e.button() == Qt.RightButton:
            self.context_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e) -> None:
        if self._press_global is None:
            return
        delta = e.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() > DRAG_THRESHOLD:
            self._dragging = True
            self.drag_started.emit()
        if self._dragging:
            self.move(self._press_window + delta)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._press_global is None:
            return
        if self._dragging:
            self.drag_finished.emit()
        else:
            if area := self.area_at(e.position().toPoint()):
                self.clicked.emit(area)
        self._press_global = None
        self._dragging = False

    def wheelEvent(self, e) -> None:
        self.zoom_requested.emit(1 if e.angleDelta().y() > 0 else -1)

    def moveEvent(self, e) -> None:
        super().moveEvent(e)
        self.moved.emit()
