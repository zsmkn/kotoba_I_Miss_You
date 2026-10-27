"""設定載入：config.toml → 環境變數 → CLI 參數（後者覆寫前者）。"""
from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

# 原始碼執行時是專案資料夾；PyInstaller 打包後是 exe 所在的資料夾（設定與資料跟著 exe 走，可攜式）
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent

# 各家預設值。Gemini 走官方的 OpenAI 相容端點，所以和 OpenAI 共用同一個 client。
PROVIDER_DEFAULTS: dict[str, dict] = {
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "",  # 留空 = 自動挑已安裝的第一個模型
        "api_key_env": (),
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",  # 原版 Molili 聊天用的就是這個
        "api_key_env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "api_key_env": ("OPENAI_API_KEY",),
    },
}


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.9
    num_ctx: int = 16384  # 只對 Ollama 有效；太小會把人設從前面截掉
    timeout: float = 180.0


@dataclass
class ChatConfig:
    agent_name: str = "言葉"
    language: str = "zh-TW"
    history_messages: int = 30
    memory_top_k: int = 6
    summarize_memory: bool = True
    greet_after_hours: float = 6.0
    favor_per_message: int = 1
    suggestions: bool = True  # 回覆時順便產生三則「主人可能想回的話」
    idle_chat_minutes: float = 40.0  # 桌寵：主人在電腦前但這麼久沒聊，她會自己找話題；0 = 關閉
    web_search: bool = True  # 允許她用 DuckDuckGo 查即時資訊
    profile_update_every: int = 10  # 每累積幾則主人的新訊息，就更新一次使用者畫像；0 = 關閉
    diary: bool = True  # 每天補寫前一天的日記


# 生圖服務預設值（對應原版「相機＋以太膠片」拍照功能）
IMAGE_DEFAULTS: dict[str, dict] = {
    "none": {"model": "", "base_url": ""},
    "openai": {"model": "gpt-image-1", "base_url": "https://api.openai.com/v1"},
    "gemini": {"model": "gemini-2.5-flash-image", "base_url": "https://generativelanguage.googleapis.com/v1beta"},
    "sd-webui": {"model": "", "base_url": "http://127.0.0.1:7860"},  # AUTOMATIC1111 / Forge 的 API
}


@dataclass
class ImageConfig:
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass
class Config:
    llm: LLMConfig
    chat: ChatConfig
    data_dir: Path
    path: Path = ROOT / "config.toml"
    image: ImageConfig = field(default_factory=ImageConfig)


def _pick(cls, raw: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in raw.items() if k in names}


def load_config(
    path: Path | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
    data_dir: Path | None = None,
) -> Config:
    path = path or ROOT / "config.toml"
    raw: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)

    llm_raw = raw.get("llm", {})
    provider = provider or llm_raw.get("provider", "ollama")
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(f"不支援的 provider：{provider}（可用：{', '.join(PROVIDER_DEFAULTS)}）")
    defaults = PROVIDER_DEFAULTS[provider]
    section = llm_raw.get(provider, {})

    api_key = section.get("api_key", "")
    if not api_key:
        api_key = next((os.environ[e] for e in defaults["api_key_env"] if os.environ.get(e)), "")

    llm = LLMConfig(
        provider=provider,
        model=model or section.get("model") or defaults["model"],
        base_url=section.get("base_url") or defaults["base_url"],
        api_key=api_key,
        temperature=llm_raw.get("temperature", LLMConfig.temperature),
        num_ctx=section.get("num_ctx", LLMConfig.num_ctx),
        timeout=llm_raw.get("timeout", LLMConfig.timeout),
    )
    chat = ChatConfig(**_pick(ChatConfig, raw.get("chat", {})))

    resolved = Path(data_dir or raw.get("data_dir", "data"))
    if not resolved.is_absolute():
        resolved = ROOT / resolved
    return Config(llm=llm, chat=chat, data_dir=resolved, path=path, image=_image_config(raw))


def _image_config(raw: dict) -> ImageConfig:
    section = raw.get("image", {})
    provider = section.get("provider", "none")
    if provider not in IMAGE_DEFAULTS:
        raise ValueError(f"不支援的生圖服務：{provider}（可用：{', '.join(IMAGE_DEFAULTS)}）")
    defaults = IMAGE_DEFAULTS[provider]
    api_key = section.get("api_key", "")
    if not api_key and provider in PROVIDER_DEFAULTS:
        # 沒有另外設定時，沿用同一家聊天模型的 API key
        api_key = raw.get("llm", {}).get(provider, {}).get("api_key", "")
        if not api_key:
            env = PROVIDER_DEFAULTS[provider]["api_key_env"]
            api_key = next((os.environ[e] for e in env if os.environ.get(e)), "")
    return ImageConfig(
        provider=provider,
        model=section.get("model") or defaults["model"],
        base_url=section.get("base_url") or defaults["base_url"],
        api_key=api_key,
    )


def read_raw(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def save_config(path: Path, updates: dict) -> None:
    """把 updates 深度合併進設定檔（設定介面用）。注意：檔案裡原有的註解會消失。"""
    raw = read_raw(path)
    _merge(raw, updates)
    path.write_text("# 由 Molili Local 設定介面寫入\n\n" + dump_toml(raw), encoding="utf-8")


def _merge(base: dict, updates: dict) -> None:
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)  # JSON 字串跳脫規則與 TOML basic string 相容


def dump_toml(data: dict, prefix: str = "") -> str:
    """只支援設定檔會用到的型別：字串、數字、布林與巢狀 table。"""
    lines = [f"{k} = {_toml_value(v)}" for k, v in data.items() if not isinstance(v, dict)]
    out = "\n".join(lines) + ("\n" if lines else "")
    for k, v in data.items():
        if isinstance(v, dict):
            name = f"{prefix}.{k}" if prefix else k
            out += f"\n[{name}]\n" + dump_toml(v, name)
    return out
