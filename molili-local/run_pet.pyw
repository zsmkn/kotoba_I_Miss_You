"""直接啟動桌寵（不開主控台）。開機自動啟動與 PyInstaller 打包都用這個檔案當入口。

可以帶全域參數，例如：MoliliLocal.exe --data-dir D:\\molili-data
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from molili.cli import main  # noqa: E402

main([*sys.argv[1:], "pet"])
