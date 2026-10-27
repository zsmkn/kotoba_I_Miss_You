"""拍照／畫圖：把她描述的畫面交給生圖服務（原版要有「相機」和「以太膠片」道具才能用）。"""
from __future__ import annotations

import base64
from pathlib import Path

import httpx

from .config import ImageConfig

STYLE = "2D 日系動畫風格插畫，柔和光影，細緻背景，橫幅構圖"
SD_NEGATIVE = "lowres, bad anatomy, bad hands, text, watermark, blurry"
TIMEOUT = 180


class ImageError(RuntimeError):
    pass


REFERENCE_HINT = (
    "附上的參考圖是角色本人的外觀設定，只用來對齊長相、髮型與服裝。"
    "如果畫面裡有這個角色，請讓她的外觀和參考圖一致；構圖、姿勢與場景依照下面的描述，"
    "不要照抄參考圖的站姿和白色背景。畫面裡沒有她時就忽略參考圖。\n"
)


def generate_image(cfg: ImageConfig, prompt: str, reference: Path | None = None) -> bytes:
    """回傳圖片檔內容（PNG／JPEG）。reference：角色外觀參考圖（Live2D 模型的截圖）。"""
    if reference is not None and not reference.is_file():
        reference = None
    try:
        if cfg.provider == "openai":
            return _openai(cfg, prompt, reference)
        if cfg.provider == "gemini":
            return _gemini(cfg, prompt, reference)
        if cfg.provider == "sd-webui":
            # img2img 會照抄參考圖的構圖，角色參考需要 IP-Adapter 等擴充，所以 SD 只用文字
            return _sd_webui(cfg, f"{prompt}。{STYLE}")
    except httpx.HTTPError as e:
        raise ImageError(f"連不上生圖服務：{e}") from e
    raise ImageError("尚未設定生圖服務")


def _with_reference(prompt: str, reference: Path | None) -> str:
    return f"{REFERENCE_HINT if reference else ''}{prompt}。{STYLE}"


def _require_key(cfg: ImageConfig) -> None:
    if not cfg.api_key:
        raise ImageError(f"缺少 {cfg.provider} 的 API key")


def _openai(cfg: ImageConfig, prompt: str, reference: Path | None) -> bytes:
    from openai import OpenAI, OpenAIError

    _require_key(cfg)
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=TIMEOUT)
    is_dalle = cfg.model.startswith("dall-e")
    try:
        if reference and not is_dalle:
            # gpt-image 系列可以用 edits 端點帶參考圖；DALL·E 的 edits 是局部重繪，不適用
            with open(reference, "rb") as f:
                resp = client.images.edit(model=cfg.model, image=[f], prompt=_with_reference(prompt, reference),
                                          size="1536x1024")
        else:
            kwargs: dict = {"model": cfg.model, "prompt": _with_reference(prompt, None), "size": "1536x1024"}
            if is_dalle:
                kwargs.update(size="1792x1024", response_format="b64_json")  # gpt-image 系列預設就回傳 base64
            resp = client.images.generate(**kwargs)
    except OpenAIError as e:
        raise ImageError(f"OpenAI 生圖失敗：{e}") from e
    return base64.b64decode(resp.data[0].b64_json)


def _gemini(cfg: ImageConfig, prompt: str, reference: Path | None) -> bytes:
    _require_key(cfg)
    parts: list[dict] = []
    if reference:
        parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(reference.read_bytes()).decode()}})
    parts.append({"text": _with_reference(prompt, reference)})
    resp = httpx.post(
        f"{cfg.base_url.rstrip('/')}/models/{cfg.model}:generateContent",
        headers={"x-goog-api-key": cfg.api_key},
        json={
            "contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
        },
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise ImageError(f"Gemini 生圖失敗（{resp.status_code}）：{resp.text[:300]}")
    for cand in resp.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            data = (part.get("inlineData") or part.get("inline_data") or {}).get("data")
            if data:
                return base64.b64decode(data)
    raise ImageError("Gemini 沒有回傳圖片（可能被安全過濾擋下）")


def _sd_webui(cfg: ImageConfig, prompt: str) -> bytes:
    payload: dict = {"prompt": prompt, "negative_prompt": SD_NEGATIVE, "steps": 25, "width": 1024, "height": 768}
    if cfg.model:
        payload["override_settings"] = {"sd_model_checkpoint": cfg.model}
    resp = httpx.post(f"{cfg.base_url.rstrip('/')}/sdapi/v1/txt2img", json=payload, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise ImageError(f"Stable Diffusion WebUI 回傳 {resp.status_code}（記得用 --api 啟動）：{resp.text[:200]}")
    images = resp.json().get("images") or []
    if not images:
        raise ImageError("Stable Diffusion WebUI 沒有回傳圖片")
    return base64.b64decode(images[0].split(",", 1)[-1])
