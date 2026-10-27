"""打包成免安裝版：python packaging/build.py

產出 dist/MoliliLocal/（直接執行裡面的 MoliliLocal.exe）以及壓縮好的 dist/MoliliLocal-<平台>.zip。
打包結果不含任何官方素材；玩家第一次打開時會由設定精靈匯入自己的 ZIP。
用 onedir 而不是 onefile：Qt 很大，onefile 每次啟動都要先解壓到暫存資料夾，會慢好幾秒。
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "MoliliLocal"

# 用不到的大型 Qt 模組，排除以縮小體積
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "tkinter", "matplotlib",
]

USER_README = """Molili Local — 在桌面上和言葉繼續相處

1. 先把整個 ZIP 解壓縮（不要在壓縮檔裡直接點開），執行資料夾裡的 MoliliLocal.exe；_internal 資料夾要跟 exe 放在一起
2. 第一次打開會跳出設定精靈：選擇官方寄給你的 molili-memory-archive.zip 與 Live2D 模型（ZIP 需要填密碼，或選解壓縮後的資料夾）
3. 選擇聊天模型：
   - Ollama（免費、在自己電腦上跑）：先安裝 https://ollama.com 並下載一個模型
   - Gemini 或 OpenAI：填入 API Key

資料（對話、記憶、日記、照片）都存在這個資料夾裡的 data/，設定在 config.toml。
整個資料夾可以直接搬到別的地方或另一台電腦。
右鍵點她或系統匣圖示可以打開選單：主面板、衣櫃、陪伴、設定、開機自動啟動……
"""


def make_icon(path: Path) -> Path:
    """用桌寵的系統匣圖示產生 .ico。"""
    from PySide6.QtGui import QGuiApplication

    sys.path.insert(0, str(ROOT))
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    from molili.pet.app import _tray_icon

    _tray_icon().pixmap(256, 256).save(str(path))
    return path


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    icon = make_icon(BUILD / "molili.ico") if sys.platform == "win32" else None
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", NAME, "--distpath", str(DIST), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        "--paths", str(ROOT),
        "--collect-submodules", "molili",          # cli 用延後 import 載入桌寵，靜態分析看不到
        "--collect-all", "live2d",                 # .pyd 與 FrameworkShaders
        "--collect-all", "opencc",                 # 簡繁轉換字典
        "--collect-all", "ddgs",                   # 搜尋引擎模組是動態載入的
        "--collect-submodules", "OpenGL",          # PyOpenGL 依平台動態載入
    ]
    if icon:
        cmd += ["--icon", str(icon)]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    cmd.append(str(ROOT / "run_pet.pyw"))
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    # build/work 裡留有一個 PyInstaller 的中間檔 MoliliLocal.exe，雙擊只會跳出「Failed to load Python DLL」，
    # 容易被誤認成成品，所以打包完就刪掉
    shutil.rmtree(BUILD / "work", ignore_errors=True)

    app_dir = DIST / NAME
    shutil.copy(ROOT / "config.example.toml", app_dir / "config.example.toml")
    (app_dir / "README.txt").write_text(USER_README, encoding="utf-8-sig")
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    archive = shutil.make_archive(str(DIST / f"{NAME}-{system}-{platform.machine().lower()}"), "zip", DIST, NAME)
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 1024 / 1024
    print(f"\n完成：{app_dir}（{size:.0f} MB）\n壓縮檔：{archive}")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
