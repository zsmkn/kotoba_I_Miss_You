"""不開視窗，把 Live2D 模型畫成一張 PNG（給生圖服務當角色外觀參考圖）。"""
from __future__ import annotations

from pathlib import Path

import live2d
import numpy as np
from OpenGL import GL
from PySide6.QtCore import QRect
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QOffscreenSurface, QOpenGLContext, QPainter, QSurfaceFormat,
)
from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat

from ..live2d import PATCHED_MODEL, REFERENCE_IMAGE
from ..wardrobe import Outfit

SIZE = (768, 1075)   # 全身模型的長寬比
MARGIN = 24

# live2d-py 會快取 shader，換一個沒有共用資源的 context 就畫不出來，所以整個程式只建一次並重複使用
_context: tuple[QOffscreenSurface, QOpenGLContext] | None = None


def _make_current() -> QOpenGLContext:
    global _context
    if _context is None:
        fmt = QSurfaceFormat.defaultFormat()
        fmt.setAlphaBufferSize(8)
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        ctx = QOpenGLContext()
        ctx.setFormat(fmt)
        if shared := QOpenGLContext.globalShareContext():
            ctx.setShareContext(shared)  # 和桌寵視窗共用 GL 資源
        if not ctx.create():
            raise RuntimeError("無法建立 OpenGL context")
        _context = (surface, ctx)
    surface, ctx = _context
    if not ctx.makeCurrent(surface):
        raise RuntimeError("無法切換到離屏 OpenGL context")
    return ctx


def render_reference(model_dir: Path, outfit: Outfit | None = None) -> Path:
    """依目前的打扮重畫生圖用的參考圖。"""
    outfit = outfit or Outfit()
    return render_model(model_dir / PATCHED_MODEL, model_dir / REFERENCE_IMAGE,
                        expressions=outfit.accessories, part_colors=outfit.part_colors())


def _crop_to_content(image: QImage) -> QImage:
    image = image.convertToFormat(QImage.Format_ARGB32)
    alpha = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)[..., 3]
    ys, xs = np.nonzero(alpha[:, : image.width()] > 8)
    if len(xs) == 0:
        return image
    rect = QRect(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
    return image.copy(rect.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN))


def render_model(model_json: Path, out: Path, *, expressions: list[str] | None = None,
                 part_colors: dict[str, tuple[float, float, float]] | None = None,
                 background: QColor | None = QColor("white")) -> Path:
    """畫一張正面、預設表情的全身圖。需要已建立 QGuiApplication。

    expressions：要疊加的表情（例如目前穿戴的飾品）；part_colors：部件 id → 乘色（換色）。
    """
    if QGuiApplication.instance() is None:
        raise RuntimeError("render_model 需要先建立 QGuiApplication")
    ctx = _make_current()
    try:
        w, h = SIZE
        fbo_format = QOpenGLFramebufferObjectFormat()
        fbo_format.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
        fbo = QOpenGLFramebufferObject(w, h, fbo_format)
        fbo.bind()
        GL.glViewport(0, 0, w, h)  # 離屏 surface 的預設 viewport 不是 FBO 的大小
        live2d.glInit()
        model = live2d.Model()
        model.LoadModelJson(str(model_json))
        model.Resize(w, h)
        for e in expressions or []:
            model.AddExpression(e)
        ids = model.GetPartIds()
        for part, rgb in (part_colors or {}).items():
            if part in ids:
                model.SetPartMultiplyColor(ids.index(part), *rgb, 1.0)
        # 讓表情淡入、物理穩定下來再畫
        for _ in range(60):
            model.Update(1 / 30)
        live2d.clearBuffer(0.0, 0.0, 0.0, 0.0)
        model.Draw()
        image = fbo.toImage(True)
        fbo.release()
        del model
    finally:
        ctx.doneCurrent()

    image = _crop_to_content(image)
    if background is not None:
        canvas = QImage(image.size(), QImage.Format_ARGB32)
        canvas.fill(background)
        p = QPainter(canvas)
        p.drawImage(0, 0, image)
        p.end()
        image = canvas
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(out))
    return out
