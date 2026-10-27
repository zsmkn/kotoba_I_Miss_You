"""匯入官方釋出的言葉 Live2D 模型（Live2D.zip 或已解壓縮的資料夾）。

專案本身不散布模型，玩家用自己拿到的 ZIP 匯入。官方的 model3.json 沒有登記
表情、動作、眨眼與口型，所以匯入時會另外產生一份補齊的 model.model3.json。
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

MODEL_DIR_NAME = "kotonoha"
PATCHED_MODEL = "model.model3.json"
REFERENCE_IMAGE = "reference.png"   # 給生圖服務的角色外觀參考圖（由桌寵或匯入時產生）
IDLE_GROUP = "Idle"
IDLE_MOTIONS = {"Breath", "Idle"}

# 原版 LLM 輸出的表情代碼 → 要疊加的 exp3 表情。
# 官方的每個 exp3 只控制一個參數，原本的組合寫在 Unity 資源裡，這裡是依名稱重新搭配的。
EXPRESSION_MAP: dict[str, list[str]] = {
    "DEFAULT": [],
    "EX_HAPPY": ["Happy", "Star"],
    "EX_SHY": ["Happy"],
    "EX_MOE": ["Moe", "Happy"],
    "EX_LOVE": ["HeartEyes", "Happy"],
    "EX_SAD": ["Tear"],
    "EX_CRY": ["Tear", "CryMouth"],
    "EX_ANGRY": ["Angry", "AngryEar", "AngryMouth"],
    "EX_DISLIKE": ["Dislike", "DislikeMouth"],
    "EX_CONFUSE": ["Dizzy", "DizzyMouth"],
    "EX_SLEEPY": ["Sleep"],
    "EX_TONGUE": ["Tongue"],
    "EX_HOLDHANDS": ["HoldHand", "Happy"],
}


def _zip_name(info: zipfile.ZipInfo) -> str:
    """沒有 UTF-8 旗標的 ZIP（中文 Windows 壓的）檔名是 GBK，Python 會誤解成 cp437。"""
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437")
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _extract_zip(src: Path, staging: Path, password: str | None) -> None:
    pwd = password.encode() if password else None
    with zipfile.ZipFile(src) as zf:
        if pwd is None and any(i.flag_bits & 0x1 for i in zf.infolist()):
            raise ValueError("這個 ZIP 有密碼保護，請填入官方提供的密碼（指令列用 --password），或改用解壓縮後的資料夾")
        for info in zf.infolist():
            name = _zip_name(info)
            rel = PurePosixPath(name)
            if info.is_dir() or rel.is_absolute() or ".." in rel.parts:
                continue  # 防止 zip slip
            dest = staging.joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info, pwd=pwd) as fin, open(dest, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
            except RuntimeError as e:  # zipfile 用 RuntimeError 表示密碼錯誤
                raise ValueError(f"ZIP 密碼錯誤或無法解密：{e}") from e


def _build_model3(model_dir: Path, original: dict) -> dict:
    refs = dict(original.get("FileReferences", {}))
    expressions = sorted(model_dir.glob("expressions/*.exp3.json"))
    refs["Expressions"] = [
        {"Name": p.name.removesuffix(".exp3.json"), "File": f"expressions/{p.name}"} for p in expressions
    ]
    motions: dict[str, list[dict]] = {}
    for p in sorted(model_dir.glob("motion/*.motion3.json")):
        name = p.name.removesuffix(".motion3.json")
        # Breath（身體擺動）與 Idle（特效動畫）都屬於待機，合成同一組讓待機時隨機輪播
        motions.setdefault(IDLE_GROUP if name in IDLE_MOTIONS else name, []).append({"File": f"motion/{p.name}"})
    refs["Motions"] = motions
    physics = next(model_dir.glob("*.physics3.json"), None)
    if physics:
        refs["Physics"] = physics.name
    return {
        "Version": 3,
        "FileReferences": refs,
        "Groups": [
            {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
            {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]},
        ],
    }


def import_live2d(src: Path, data_dir: Path, password: str | None = None) -> Path:
    staging = data_dir / "live2d" / ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        if src.is_dir():
            shutil.copytree(src, staging / src.name)
        else:
            _extract_zip(src, staging, password)

        models = [p for p in staging.rglob("*.model3.json") if p.name != PATCHED_MODEL]
        if not models:
            raise ValueError("找不到 *.model3.json，這不是 Live2D 模型")
        model_json = models[0]
        model_dir = model_json.parent
        original = json.loads(model_json.read_text(encoding="utf-8"))
        moc = model_dir / original["FileReferences"]["Moc"]
        if not moc.is_file() or moc.read_bytes()[:4] != b"MOC3":
            raise ValueError(f"模型檔 {moc.name} 不存在或不是有效的 .moc3")

        (model_dir / PATCHED_MODEL).write_text(
            json.dumps(_build_model3(model_dir, original), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        available = {p["Name"] for p in _build_model3(model_dir, original)["FileReferences"]["Expressions"]}
        expr_map = {k: [e for e in v if e in available] for k, v in EXPRESSION_MAP.items()}
        (model_dir / "expression_map.json").write_text(
            json.dumps(expr_map, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        target = data_dir / "live2d" / MODEL_DIR_NAME
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(model_dir), str(target))
        return target / PATCHED_MODEL
    finally:
        shutil.rmtree(staging, ignore_errors=True)
