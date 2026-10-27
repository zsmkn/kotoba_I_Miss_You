"""視窗的滑鼠穿透與置頂。

Windows：Qt 的透明 OpenGL 視窗本身就是 layered window，但透明區域預設仍會攔截點擊。
加上 WS_EX_TRANSPARENT 後點擊會整個穿透到後面的視窗，所以要依游標是否在她身上動態切換
（原版 Unity 用的也是同一招）。
macOS／Linux：沒有對應的 API，改用視窗遮罩（QWidget.setMask，在 X11 上就是 XShape），
只讓她的輪廓範圍接收滑鼠；見 view.PetView 的遮罩模式。
"""
from __future__ import annotations

import os
import sys

# 設定 MOLILI_FORCE_MASK=1 可以在 Windows 上測試遮罩模式
NATIVE_CLICK_THROUGH = sys.platform == "win32" and not os.environ.get("MOLILI_FORCE_MASK")

if sys.platform == "win32":
    import ctypes

    _GWL_EXSTYLE = -20
    _WS_EX_TRANSPARENT = 0x20
    _WS_EX_LAYERED = 0x80000

    _user32 = ctypes.windll.user32
    _user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    _user32.SetWindowLongW.restype = ctypes.c_long

    _HWND_TOPMOST = -1
    _HWND_NOTOPMOST = -2
    _SWP_NOSIZE_NOMOVE_NOACTIVATE = 0x0001 | 0x0002 | 0x0010
    _user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]

    def set_click_through(hwnd: int, enabled: bool) -> None:
        style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        if enabled:
            style |= _WS_EX_TRANSPARENT | _WS_EX_LAYERED
        else:
            style &= ~_WS_EX_TRANSPARENT
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)

    def set_topmost(hwnd: int, on: bool) -> None:
        """切換置頂。用 SetWindowPos 而不是改 Qt 視窗旗標，避免重建視窗導致 OpenGL 內容重載。"""
        _user32.SetWindowPos(
            hwnd, _HWND_TOPMOST if on else _HWND_NOTOPMOST, 0, 0, 0, 0, _SWP_NOSIZE_NOMOVE_NOACTIVATE
        )

    class _LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetTickCount.restype = ctypes.c_uint

    def user_idle_seconds() -> float:
        """距離使用者最後一次操作鍵盤滑鼠（整個系統，不只本程式）過了幾秒。"""
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000

else:
    def set_click_through(hwnd: int, enabled: bool) -> None:
        pass

    def set_topmost(hwnd: int, on: bool) -> None:
        pass

    def user_idle_seconds() -> float:
        return 0.0  # 無法偵測時當作使用者一直在
