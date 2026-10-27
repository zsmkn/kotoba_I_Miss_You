"""組裝 system prompt。

結構仿照原版：人設（role_prompt.md）+ 記憶 + 目前狀態 + JSON 輸出格式。
輸出格式與 reasoning 檢查清單是從匯出的對話紀錄反推出來的。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from .memory import Memory
from .timeutil import describe, humanize

EXPRESSIONS = [
    "DEFAULT", "EX_HAPPY", "EX_SHY", "EX_MOE", "EX_LOVE", "EX_SAD", "EX_CRY",
    "EX_ANGRY", "EX_DISLIKE", "EX_CONFUSE", "EX_SLEEPY", "EX_TONGUE", "EX_HOLDHANDS",
]

LANGUAGE_RULES = {
    "zh-TW": "一律使用繁體中文（台灣用語）回覆。",
    "zh-CN": "一律使用简体中文回复。",
    "ja": "必ず日本語で返信してください。",
    "en": "Always reply in English.",
}


@dataclass
class PromptContext:
    agent_name: str
    user_name: str
    role_prompt: str
    language: str
    now: datetime
    facts: list[str] = field(default_factory=list)
    profile_json: str | None = None
    memories: list[Memory] = field(default_factory=list)
    diary: tuple[str, str] | None = None  # (date, content)
    last_seen: datetime | None = None
    favor_level: int | None = None
    suggestions: bool = False
    situation: str = ""  # 她剛剛在做的事（桌寵待機小動作），例如「正在看書」
    web_search: bool = False
    films: int | None = None  # 剩餘以太膠片；None = 沒有相機或沒設定生圖服務
    appearance: str = ""      # 目前的打扮（換裝）


def favor_stage(level: int) -> str:
    if level < 10:
        return "初始階段「好奇的貓」"
    if level < 70:
        return "發展階段「信賴的共事者」"
    return "穩定階段「唯一的理解者」"


def render_profile(profile_json: str) -> str:
    """把原版的使用者畫像（帶 [+]/[++]/[-] 權重標記的 JSON）轉成條列文字。"""
    try:
        data = json.loads(profile_json)
    except json.JSONDecodeError:
        return profile_json
    lines = []
    for section, value in data.items():
        if isinstance(value, dict):
            for sub, items in value.items():
                if isinstance(items, list):
                    lines.append(f"- {section}／{sub}：{'、'.join(_render_tag(i) for i in items)}")
                else:
                    lines.append(f"- {section}／{sub}：{items}")
        else:
            lines.append(f"- {section}：{value}")
    return "\n".join(lines)


def _render_tag(item: str) -> str:
    if item.startswith("[++]"):
        return item[4:] + "（很重要）"
    if item.startswith("[-]"):
        return "不喜歡" + item[3:]
    if item.startswith("[+]"):
        return item[3:]
    return item


def build_system_prompt(ctx: PromptContext) -> str:
    name, user = ctx.agent_name, ctx.user_name
    parts = [ctx.role_prompt.strip(), "---", f"# {name}對{user}的記憶"]

    if ctx.facts:
        parts.append("## 重要設定與約定\n" + "\n".join(f"- {f}" for f in ctx.facts))
    if ctx.profile_json:
        parts.append(f"## 關於{user}（從過去的對話整理）\n" + render_profile(ctx.profile_json))
    if ctx.memories:
        lines = [f"- [{m.created_at[:10]}] {m.summary}" for m in ctx.memories]
        parts.append("## 可能和現在話題有關的過往事件\n" + "\n".join(lines))
    if ctx.diary:
        date, content = ctx.diary
        excerpt = content.strip().replace("\n\n", "\n")
        if len(excerpt) > 400:
            excerpt = excerpt[:400] + "……"
        parts.append(f"## 你最近寫的一篇日記（{date}）\n{excerpt}")

    status = [f"- 現在時間：{describe(ctx.now)}"]
    if ctx.last_seen:
        gap = ctx.now - ctx.last_seen
        status.append(f"- 上一次和{user}說話：{describe(ctx.last_seen)}，距今{humanize(gap)}")
        if gap.total_seconds() > 6 * 3600:
            status.append(f"- 已經隔了一段時間，請自然地對這段空白有所反應（想念、好奇{user}最近過得如何等），但不要過度責怪")
    if ctx.situation:
        status.append(f"- 你剛剛{ctx.situation}，可以自然地帶到這件事")
    if ctx.appearance:
        status.append(f"- 你現在的打扮：{ctx.appearance}（{user}幫你換的，被問到或聊到時可以提）")
    if ctx.favor_level is not None:
        status.append(f"- 好感度等級：{ctx.favor_level}／100，目前是{favor_stage(ctx.favor_level)}，請嚴格依照該階段的人格表現說話")
    if ctx.web_search:
        status.append(
            "- 你可以用通信器連上地球的網路查資料：遇到即時資訊（天氣、新聞、價格、活動）或你不確定的事實，"
            "在 JSON 加上 search 欄位；閒聊時不需要"
        )
    if ctx.films is None:
        status.append("- 你的以太相機目前不能用，暫時不能拍照或畫圖給對方看")
    elif ctx.films == 0:
        status.append("- 以太膠片今天用完了，明天才會補充，暫時不能拍照")
    else:
        status.append(
            f"- 你有以太相機和 {ctx.films} 捲以太膠片：{user}想看照片、或你很想分享眼前的景色時，"
            "可以在 JSON 加上 photo 欄位描述畫面（每張用掉一捲，別太常用）。"
            f"畫面裡有你自己時，要寫出你的外表：{ctx.appearance or '銀白色長髮、貓耳與貓尾、紫白漸層小禮服'}"
        )
    status.append(f"- {user}要求你做不到的事時，用符合世界觀的理由說明，絕對不要提到 AI 或系統限制")
    parts.append("# 目前狀態\n" + "\n".join(status))

    rules = [
        LANGUAGE_RULES.get(ctx.language, LANGUAGE_RULES["zh-TW"]),
        f"稱呼對方「{user}」，延續你們過去對話的相處方式與稱呼。",
        f"如果{user}流露出自傷或輕生的念頭，暫時放下傲嬌，真誠地關心，並溫柔地鼓勵{user}聯絡信任的人或當地的求助專線。",
        "不要重複上一輪用過的開頭、口癖和句式。",
        "如果你在上一則訊息已經打過招呼或說過想念，這一輪就不要再打招呼，直接回應對方剛說的內容。",
    ]
    parts.append("# 回覆規則補充\n" + "\n".join(f"- {r}" for r in rules))

    fields = [
        '  "reasoning": "回覆前的簡短思考：1.指代確認 2.聊天模式還是問題模式 3.重複性檢查（上一輪用過哪些口癖、開頭、句式，這輪要避開）'
        '4.依好感階段決定語氣與距離感 5.時間（現在幾點、距離上次對話多久）6.這輪的回覆思路與情緒"',
        f'  "mood": "{name}這一輪的心情，一句話"',
        f'  "expression": "從以下選一個最符合心情的表情：{", ".join(EXPRESSIONS)}"',
        '  "messages": ["像傳訊息一樣拆開的短句", "通常 1～4 則，問題模式可以多一些"]',
    ]
    if ctx.suggestions:
        fields.append(f'  "suggestions": ["三則{user}可以直接送出的回覆", "用{user}的口吻、以「我」自稱，各 15 字以內", "例如：我也想聽森林裡的事"]')
    # 選填欄位不放進範本：小模型會把範本裡的說明文字原封不動抄進去，變成每次都觸發
    optional = []
    if ctx.web_search:
        optional.append(
            '- "search"：需要查資料時才加，值是搜尋關鍵字，例如 "search": "台北 明天 天氣"。'
            "加了之後你會拿到搜尋結果再回答一次，這輪 messages 先寫一句「我查一下」之類的話"
        )
    if ctx.films:
        optional.append(
            '- "photo"：真的要拍照或畫圖給對方時才加，值是具體的畫面描述（場景、光線、人物動作），'
            '例如 "photo": "夕陽穿過樹梢灑在觀測站的木屋上"'
        )
    text = (
        "# 輸出格式\n"
        "只輸出一個 JSON 物件，不要有 markdown 或任何其他文字：\n"
        "{\n" + ",\n".join(fields) + "\n}"
    )
    if optional:
        text += "\n\n以下欄位平常不要加，只有在符合條件時才加進 JSON：\n" + "\n".join(optional)
    parts.append(text)
    return "\n\n".join(parts)
