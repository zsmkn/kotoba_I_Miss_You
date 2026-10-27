"""LLM 介面：Ollama（原生 API）與 OpenAI 相容 API（OpenAI、Gemini）。

Ollama 刻意不走 /v1 相容端點，因為相容端點無法設定 num_ctx，
而預設的上下文長度會把幾千字的人設從前面靜默截斷。
"""
from __future__ import annotations

import re
from typing import Protocol

import httpx

from .config import LLMConfig

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    model: str

    def complete(self, messages: list[dict], *, json_mode: bool = False) -> str: ...


def strip_think(text: str) -> str:
    """去掉推理模型（qwen3、deepseek-r1 等）輸出的 <think> 區塊。"""
    return _THINK_BLOCK.sub("", text).strip()


class OllamaLLM:
    def __init__(self, cfg: LLMConfig):
        base = cfg.base_url.rstrip("/").removesuffix("/v1")
        self.cfg = cfg
        self.http = httpx.Client(base_url=base, timeout=cfg.timeout)
        self.model = cfg.model or self._first_installed_model()

    def _first_installed_model(self) -> str:
        try:
            models = self.http.get("/api/tags").json().get("models", [])
        except httpx.HTTPError as e:
            raise LLMError(f"連不上 Ollama（{self.http.base_url}），請確認 Ollama 已啟動：{e}") from e
        if not models:
            raise LLMError("Ollama 裡沒有任何模型，請先 `ollama pull <模型>`，或在 config.toml 指定 model")
        return models[0]["name"]

    def complete(self, messages: list[dict], *, json_mode: bool = False) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": self.cfg.temperature, "num_ctx": self.cfg.num_ctx},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            resp = self.http.post("/api/chat", json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama 請求失敗：{e}") from e
        if resp.status_code != 200:
            raise LLMError(f"Ollama 回傳 {resp.status_code}：{resp.text[:300]}")
        return strip_think(resp.json()["message"]["content"])


class OpenAICompatLLM:
    """OpenAI 與 Gemini（以及任何 OpenAI 相容服務）。"""

    def __init__(self, cfg: LLMConfig):
        from openai import OpenAI

        if not cfg.api_key:
            env = "GEMINI_API_KEY" if cfg.provider == "gemini" else "OPENAI_API_KEY"
            raise LLMError(f"缺少 {cfg.provider} 的 API key：請在 config.toml 設定 api_key，或設定環境變數 {env}")
        self.cfg = cfg
        self.model = cfg.model
        self.client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout)
        # 有些模型不接受 response_format 或自訂 temperature（例如推理模型），失敗一次後就記住不再送
        self._supports_json = True
        self._supports_temperature = True

    def complete(self, messages: list[dict], *, json_mode: bool = False) -> str:
        from openai import APIError, BadRequestError

        while True:
            kwargs: dict = {"model": self.model, "messages": messages}
            if self._supports_temperature:
                kwargs["temperature"] = self.cfg.temperature
            if json_mode and self._supports_json:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except BadRequestError as e:
                msg = str(e).lower()
                if "temperature" in msg and self._supports_temperature:
                    self._supports_temperature = False
                    continue
                if "response_format" in kwargs and self._supports_json:
                    self._supports_json = False
                    continue
                raise LLMError(f"{self.cfg.provider} 拒絕請求：{e}") from e
            except APIError as e:
                raise LLMError(f"{self.cfg.provider} 請求失敗：{e}") from e
            return strip_think(resp.choices[0].message.content or "")


def create_llm(cfg: LLMConfig) -> LLM:
    if cfg.provider == "ollama":
        return OllamaLLM(cfg)
    return OpenAICompatLLM(cfg)
