"""開機自動啟動（Windows 登錄檔 Run、macOS LaunchAgents、Linux XDG autostart）。"""
from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

from ..config import FROZEN, ROOT

APP_ID = "MoliliLocal"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "net.molili.local.plist"
DESKTOP_ENTRY = Path.home() / ".config" / "autostart" / "molili-local.desktop"


def launch_command() -> list[str]:
    """開機時要執行的指令：打包版直接開 exe；原始碼版用 pythonw 執行 run_pet.pyw（不跳主控台）。"""
    if FROZEN:
        return [sys.executable]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.exists():
            python = windowless
    return [str(python), str(ROOT / "run_pet.pyw")]


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_ID)
            return True
        except OSError:
            return False
    if sys.platform == "darwin":
        return LAUNCH_AGENT.exists()
    return DESKTOP_ENTRY.exists()


def set_enabled(on: bool) -> None:
    command = launch_command()
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, APP_ID, 0, winreg.REG_SZ, " ".join(f'"{c}"' for c in command))
            else:
                try:
                    winreg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        if on:
            LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENT.write_bytes(plistlib.dumps({"Label": "net.molili.local", "ProgramArguments": command,
                                                      "RunAtLoad": True}))
        else:
            LAUNCH_AGENT.unlink(missing_ok=True)
    else:
        if on:
            DESKTOP_ENTRY.parent.mkdir(parents=True, exist_ok=True)
            DESKTOP_ENTRY.write_text(
                "[Desktop Entry]\nType=Application\nName=Molili Local\n"
                f"Exec={shlex.join(command)}\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        else:
            DESKTOP_ENTRY.unlink(missing_ok=True)


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
