"""換裝：飾品開關＋洋裝／頭髮染色。

官方釋出的模型只有預設服裝「銀月貓祈」，原版的女僕裝、森林風語等服裝都不在模型裡，無法還原。
能做的是模型本身就有的飾品表情（對應背包裡的道具），以及用部件乘色替洋裝和頭髮換顏色。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field

from . import db

# (模型的表情名稱, 對應的背包道具, 顯示名稱)
ACCESSORIES: list[tuple[str, str | None, str]] = [
    ("HeadRing", "default_halo_1", "以太星辰"),
    ("Glasses", "round_glass_1", "圓形眼鏡"),
    ("ButterFly", "bufferfly_1", "蝴蝶"),
    ("LegDecoration", "default_leg_bufferfly_1", "腿飾蝴蝶"),
    ("ArmRing", "default_arm_ring_1", "臂環"),
    ("FrontHairOrnament", "default_hairpin_1", "髮夾"),
    ("TwinTailHairOrnament", "default_hair_ornament_1", "髮飾"),
    ("StarEffects", "star_effect_1", "星星碎片"),
    ("EarRing", None, "耳環"),
    ("BreastOrnament", None, "胸前飾品"),
    ("SkirtOrnament", None, "裙飾"),
    ("LightPointEffects", None, "光點"),
]
ACCESSORY_NAMES = {expr: name for expr, _, name in ACCESSORIES}

# 顏色 key → (顯示名稱, 乘色 RGB；None = 原色)
DRESS_COLORS: dict[str, tuple[str, tuple[float, float, float] | None]] = {
    "default": ("原色", None),
    "sakura": ("櫻花粉", (1.0, 0.8, 0.9)),
    "mint": ("薄荷綠", (0.75, 1.0, 0.88)),
    "night": ("夜空藍", (0.5, 0.48, 0.68)),
    "sunset": ("晚霞橘", (1.0, 0.86, 0.72)),
}
HAIR_COLORS: dict[str, tuple[str, tuple[float, float, float] | None]] = {
    "default": ("銀白", None),
    "pink": ("粉色", (1.0, 0.8, 0.9)),
    "lavender": ("薰衣草紫", (0.86, 0.82, 1.0)),
    "cocoa": ("可可棕", (0.72, 0.6, 0.52)),
}
# cdi3 的部件：服装、后服（裙擺）／前发、呆毛、后发、左右側髮
DRESS_PARTS = ["Part97", "Part152"]
HAIR_PARTS = ["Part44", "Part45", "Part167", "Part174", "Part179"]

# 背包裡有、但模型沒有收錄的服裝
MISSING_OUTFITS = ["maid_cloth_1", "wind_cloth_1", "cloud_cloth_1", "lolita_cloth_1"]

DRESS_NAME = "銀月貓祈"


@dataclass
class Outfit:
    accessories: list[str] = field(default_factory=list)
    dress: str = "default"
    hair: str = "default"

    def part_colors(self) -> dict[str, tuple[float, float, float]]:
        """部件 id → 乘色；原色是 (1, 1, 1)，要明確設回去才能取消先前的染色。"""
        colors = {}
        for parts, palette, key in ((DRESS_PARTS, DRESS_COLORS, self.dress), (HAIR_PARTS, HAIR_COLORS, self.hair)):
            rgb = palette.get(key, (None, None))[1] or (1.0, 1.0, 1.0)
            colors.update({p: rgb for p in parts})
        return colors

    def describe(self) -> str:
        """給 LLM 看的外觀描述。"""
        hair = HAIR_COLORS.get(self.hair, HAIR_COLORS["default"])[0]
        dress = DRESS_COLORS.get(self.dress, DRESS_COLORS["default"])[0]
        text = f"{hair}色長髮、貓耳與貓尾，穿著{'' if self.dress == 'default' else dress + '色的'}小禮服「{DRESS_NAME}」"
        worn = [ACCESSORY_NAMES[a] for a in self.accessories if a in ACCESSORY_NAMES]
        if worn:
            text += "，戴著" + "、".join(worn)
        return text


def load_outfit(conn: sqlite3.Connection) -> Outfit:
    raw = db.get_meta(conn, "outfit")
    if not raw:
        return Outfit()
    try:
        data = json.loads(raw)
        return Outfit(
            accessories=[a for a in data.get("accessories", []) if a in ACCESSORY_NAMES],
            dress=data.get("dress", "default") if data.get("dress") in DRESS_COLORS else "default",
            hair=data.get("hair", "default") if data.get("hair") in HAIR_COLORS else "default",
        )
    except (json.JSONDecodeError, AttributeError):
        return Outfit()


def save_outfit(conn: sqlite3.Connection, outfit: Outfit) -> None:
    with conn:
        db.set_meta(conn, "outfit", json.dumps(asdict(outfit), ensure_ascii=False))
