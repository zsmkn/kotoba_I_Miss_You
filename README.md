# Molili Local — 和言葉繼續聊天

Molili AI Friends 停運後，在自己的電腦上延續和言葉的對話。
匯入官方提供給你的記憶封存檔，她會記得你們聊過的事、你的喜好和你們的好感度。

> 本專案**不包含**任何官方素材（人設、記憶、Live2D 模型），免安裝版也一樣。請使用官方寄給你的 ZIP 自行匯入。

## 不想碰程式？直接用免安裝版

1. 到 [Releases](../../releases) 下載 `MoliliLocal-windows-amd64.zip`，解壓縮到任意資料夾
2. 執行 `MoliliLocal.exe`，第一次會跳出設定精靈：選擇官方寄給你的記憶封存檔與 Live2D 模型，再選聊天模型
3. 她就會出現在桌面右下角

資料與設定都存在 exe 旁邊（`data/`、`config.toml`），整個資料夾可以直接搬走。以下是從原始碼執行的方式，程式碼都在 `molili-local/`。

## 需求

- Python 3.11 以上
- 任選一種模型來源：
  - **Ollama**（完全本地、免費）：需要先 `ollama pull` 一個模型
  - **Gemini**：需要 API key（原版遊戲聊天使用的就是 `gemini-2.5-flash`）
  - **OpenAI**：需要 API key，也可以接任何 OpenAI 相容服務

## 安裝

```bash
cd molili-local
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux：source .venv/bin/activate
pip install -r requirements.txt
copy config.example.toml config.toml   # macOS / Linux：cp
```

編輯 `config.toml`，選擇 `provider` 並填入模型或 API key（API key 也可以用環境變數 `GEMINI_API_KEY` / `OPENAI_API_KEY`）。

## 匯入官方資料

```bash
# 記憶封存檔（ZIP 或解壓縮後的資料夾都可以）
python -m molili import molili-memory-archive.zip

# Live2D 模型（官方 ZIP 有密碼保護，請加上官方提供的密碼；或直接指定解壓縮後的資料夾）
python -m molili import-live2d Live2D.zip --password <密碼>
```

重複匯入是安全的，不會覆蓋你之後在本地累積的對話、記憶與好感度。

## 聊天

```bash
python -m molili chat
python -m molili chat --provider gemini      # 臨時切換模型來源
python -m molili chat --debug                # 顯示她的思考過程與檢索到的記憶
```

聊天中可用 `/debug`、`/status`、`/prompt`、`/quit`。

如果你很久沒上線，開啟時她會先主動跟你打招呼。

## 桌寵模式

```bash
python -m molili pet
pythonw -m molili pet    # Windows：不開主控台視窗
```

| 操作 | 效果 |
|---|---|
| 點她身旁的圓形按鈕 | 打開／收起聊天面板 |
| 點她的頭／臉／手／身體／腿 | 摸頭、捏臉、戳她……她會有反應（游標會先變成手的圖示） |
| 拖曳 | 移動位置；拖到螢幕左右邊緣放開，她會從邊緣探頭 |
| 滑鼠滾輪 | 放大縮小 |
| 右鍵／系統匣圖示 | 選單：主面板、對話記錄、日記、置頂、推薦回覆、尺寸、語言、設定 |

主面板可以看好感度、完整對話紀錄、她的日記和背包，也能在「設定」頁切換模型來源與填寫 API Key。

主面板的「衣櫃」可以幫她換飾品（以太星辰、圓形眼鏡、蝴蝶…）和替洋裝、頭髮染色，她會知道自己穿什麼，拍照時也會照現在的打扮畫。
（官方釋出的模型只有預設服裝，女僕裝等其他服裝沒辦法換。）

右鍵選單可以開啟「開機自動啟動」。

右鍵選單或聊天面板的「⏱ 陪伴」可以開番茄鐘：你工作或念書時，她會在旁邊陪你一起打字或看書，結束時好感度會上升。

她每天會寫日記（主面板「日記」頁），遇到天氣、新聞這類問題會用通信器上網查（DuckDuckGo）。
在設定頁開啟「拍照／畫圖」並接上生圖服務（OpenAI、Gemini 或本機 Stable Diffusion WebUI）後，她也會拍照給你看，每天有 3 捲以太膠片。

你沒理她的時候，她會自己找事做：看書、寫筆記、哼歌，深夜還會打瞌睡。
你在電腦前但很久沒聊時，她也會主動找話題（預設 40 分鐘，可在設定調整或關閉）；離開電腦時不會對著空氣說話。

她身體以外的透明區域點擊會直接穿透到後面的視窗，不會擋住你工作。

## 平台支援

Windows 是主要測試平台。macOS／Linux 的桌寵用視窗遮罩讓她身體以外的地方可以點穿（X11 上是 XShape），
但尚未在實機上測試；Wayland 不允許程式自己決定視窗位置，桌寵可能無法正常擺放。

## 打包免安裝版

```bash
pip install -r requirements-dev.txt
python packaging/build.py
```

產出 `dist/MoliliLocal/` 與壓縮好的 `dist/MoliliLocal-<平台>.zip`，不含任何官方素材。
要做 macOS／Linux 版需在該平台上執行。免安裝版內含 [live2d-py](https://github.com/Arkueid/live2d-py) 附帶的 Live2D Cubism Core，其使用依 [Live2D 的授權條款](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)。

## 模型建議

角色扮演很吃模型的指令遵循能力。實測 12B 左右的本地模型能維持人設與記憶，
但比較容易重複句式、過度使用「喵」。想要最接近原版的感覺，建議用 `gemini-2.5-flash`，
或 14B 以上、中文能力好的本地模型（例如 Qwen 系列）。

## 運作方式

| 原版 | 本專案 |
|---|---|
| 人設 `role_prompt.md` | 原文放進 system prompt |
| 火山引擎記憶庫（事件摘要＋使用者畫像） | SQLite＋中文 bigram 檢索；每輪對話後由模型整理成新的事件記憶 |
| LLM 輸出 `{reasoning, mood, expression, messages}` | 沿用同一格式，`expression` 之後用來驅動 Live2D 表情 |
| 好感度等級與人格階段 | 沿用匯入的等級；升級公式是依匯出資料推估的近似值 |
| autoTask 主動搭話 | 久未上線時開啟會主動問候 |

資料存在 `molili-local/data/`（`molili.db` 與 `live2d/`），請自行備份。

## 授權

本專案程式碼採用 [MIT 授權](LICENSE)。官方素材與 Live2D Cubism Core 不在此授權範圍內，各自依其原有條款。
