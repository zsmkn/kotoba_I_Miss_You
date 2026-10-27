"""命令列入口：python -m molili <指令>"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import db
from .config import Config, load_config
from .diary import missing_dates
from .engine import Companion, Reply
from .importer import import_archive
from .live2d import import_live2d
from .llm import LLMError, create_llm
from .timeutil import describe, now, parse

DIM, PINK, YELLOW, RESET = "\033[2m", "\033[95m", "\033[93m", "\033[0m"

HELP = """指令：
  /debug   顯示或隱藏她的思考過程（reasoning / mood / 用到的記憶）
  /status  好感度與資料統計
  /prompt  印出目前完整的 system prompt
  /quit    離開"""


def _config(args) -> Config:
    return load_config(
        Path(args.config) if args.config else None,
        provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        data_dir=Path(args.data_dir) if args.data_dir else None,
    )


def cmd_import(args) -> None:
    cfg = _config(args)
    conn = db.connect(cfg.data_dir)
    counts = import_archive(conn, Path(args.path), cfg.data_dir)
    print("匯入完成（新增筆數）：")
    for k, v in counts.items():
        print(f"  {k:<9}{v}")
    print(f"資料庫：{cfg.data_dir / 'molili.db'}")


def cmd_import_live2d(args) -> None:
    cfg = _config(args)
    model = import_live2d(Path(args.path), cfg.data_dir, args.password)
    print(f"Live2D 模型已匯入：{model}")
    try:
        reference = _render_reference(model.parent)
        print(f"生圖用的角色參考圖：{reference}")
    except (ImportError, RuntimeError) as e:  # 只用終端機聊天的人可能沒裝 Qt／live2d-py
        print(f"（略過產生角色參考圖：{e}）")


def _render_reference(model_dir: Path) -> Path:
    import live2d
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    from .pet.snapshot import render_reference

    live2d.init()
    live2d.enableLog(False)
    QGuiApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841  離屏繪圖需要 Qt 應用程式物件
    return render_reference(model_dir)


def _status(comp: Companion) -> str:
    conn = comp.conn
    count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    lines = []
    f = comp.favor()
    if f:
        lines.append(f"好感度：Lv.{f['level']}（累計 {f['total']}）")
    last = comp.last_seen()
    if last:
        lines.append(f"上次聊天：{describe(parse(last))}")
    lines.append(
        f"訊息 {count('SELECT COUNT(*) FROM messages')} 則｜"
        f"事件記憶 {len(comp.memory)} 則｜日記 {count('SELECT COUNT(*) FROM diaries')} 篇"
    )
    return "\n".join(lines)


def _show(comp: Companion, r: Reply, debug: bool) -> None:
    if debug:
        if r.memories_used:
            print(f"{DIM}[記憶] " + "\n[記憶] ".join(r.memories_used) + RESET)
        print(f"{DIM}[reasoning] {r.reasoning}\n[mood] {r.mood}{RESET}")
    if r.searched:
        print(f"{DIM}🔍 {comp.search_notice(r.searched)}{RESET}")
    for i, msg in enumerate(r.messages):
        if i and sys.stdout.isatty():
            time.sleep(min(0.4 + len(msg) * 0.03, 1.5))  # 模仿一則一則傳訊息的節奏
        tag = f" {DIM}({r.expression}){RESET}" if i == 0 else ""
        print(f"{PINK}{comp.agent_name}{RESET}{tag}：{msg}")
    if r.image_path:
        print(f"{PINK}{comp.agent_name}{RESET}：📷 {r.image_path}")
    for notice in r.notices:
        print(f"{YELLOW}✦ {notice}{RESET}")


def _write_diaries(comp: Companion) -> None:
    if not comp.cfg.chat.diary or not missing_dates(comp.conn, now().date()):
        return
    print(f"{DIM}（{comp.agent_name}正在補寫日記……）{RESET}")
    try:
        for day in comp.write_missing_diaries():
            print(f"{YELLOW}📘 {comp.agent_name} 寫了一篇關於你的日記（{day}）{RESET}")
    except LLMError as e:
        print(f"{DIM}（寫日記失敗：{e}）{RESET}")


def cmd_diary(args) -> None:
    cfg = _config(args)
    try:
        comp = Companion(cfg, db.connect(cfg.data_dir), create_llm(cfg.llm))
    except (LLMError, RuntimeError) as e:
        sys.exit(f"錯誤：{e}")
    _write_diaries(comp)


def cmd_chat(args) -> None:
    cfg = _config(args)
    conn = db.connect(cfg.data_dir)
    try:
        llm = create_llm(cfg.llm)
        comp = Companion(cfg, conn, llm)
    except (LLMError, RuntimeError) as e:
        sys.exit(f"錯誤：{e}")

    print(f"{DIM}模型：{cfg.llm.provider} / {llm.model}\n{_status(comp)}\n輸入 /help 查看指令{RESET}\n")
    debug = args.debug
    if not args.no_greet:
        try:
            greeting = comp.greet()
            if greeting:
                _show(comp, greeting, debug)
        except LLMError as e:
            print(f"{DIM}（主動問候失敗：{e}）{RESET}")
    _write_diaries(comp)

    while True:
        try:
            text = input(f"\n{comp.user_name}：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text == "/help":
            print(HELP)
            continue
        if text == "/debug":
            debug = not debug
            print(f"debug {'開啟' if debug else '關閉'}")
            continue
        if text == "/status":
            print(_status(comp))
            continue
        if text == "/prompt":
            print(comp.build_context(text)[0])
            continue
        try:
            _show(comp, comp.reply(text), debug)
        except LLMError as e:
            print(f"{DIM}（通訊器好像收不到訊號……{e}）{RESET}")

    print(f"\n{DIM}正在整理記憶……{RESET}")
    comp.wait_background()


def cmd_pet(args) -> None:
    from .pet.app import run  # 延後載入：只用終端機聊天的人不必裝 Qt 與 live2d-py

    sys.exit(run(_config(args)))


def cmd_prompt(args) -> None:
    cfg = _config(args)
    conn = db.connect(cfg.data_dir)

    class _NoLLM:
        model = "-"

    comp = Companion(cfg, conn, _NoLLM())  # type: ignore[arg-type]
    print(comp.build_context(args.query or "")[0])


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:  # 用 pythonw 啟動桌寵時沒有主控台
            stream.reconfigure(encoding="utf-8")
    if sys.stdin is not None and not sys.stdin.isatty():
        sys.stdin.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(prog="molili", description="在本地和言葉繼續聊天")
    p.add_argument("--config", help="設定檔路徑（預設 config.toml）")
    p.add_argument("--data-dir", help="資料目錄（預設 data/）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import", help="匯入官方記憶封存檔（molili-memory-archive.zip 或解壓縮後的資料夾）")
    s.add_argument("path")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("import-live2d", help="匯入官方 Live2D 模型（Live2D.zip 或資料夾）")
    s.add_argument("path")
    s.add_argument("--password", help="官方 Live2D.zip 有密碼保護時使用")
    s.set_defaults(func=cmd_import_live2d)

    s = sub.add_parser("chat", help="開始聊天")
    s.add_argument("--provider", choices=["ollama", "gemini", "openai"])
    s.add_argument("--model")
    s.add_argument("--debug", action="store_true", help="顯示思考過程與命中的記憶")
    s.add_argument("--no-greet", action="store_true", help="不要讓她主動打招呼")
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("diary", help="補寫前幾天還沒寫的日記")
    s.add_argument("--provider", choices=["ollama", "gemini", "openai"])
    s.add_argument("--model")
    s.set_defaults(func=cmd_diary)

    s = sub.add_parser("pet", help="以桌寵模式啟動（需要先匯入 Live2D 模型）")
    s.add_argument("--provider", choices=["ollama", "gemini", "openai"])
    s.add_argument("--model")
    s.set_defaults(func=cmd_pet)

    s = sub.add_parser("prompt", help="印出組好的 system prompt（除錯用）")
    s.add_argument("query", nargs="?", help="模擬使用者輸入，用來觀察會檢索到哪些記憶")
    s.set_defaults(func=cmd_prompt)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"錯誤：{e}")
