# YouTube Transcript Exporter (YTE) 🚀

![Banner](github_repo_banner.png)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/your-username/youtube-transcript-exporter/graphs/commit-activity)

**YouTube Transcript Exporter (YTE)** 是一套功能強大的 Python 工具，致力於將 YouTube 影片字幕完美轉換為易於閱讀的 Markdown 格式。它不僅支持單一影片，還能一鍵掃描整個播放清單與頻道，並具備強大的 anti-429 防封鎖機制與環境適配能力。

---

## ✨ 核心特性

- 📺 **全平台支援**：支持單一影片、Playlists 與整體 Channel。
- 🌍 **智能翻譯**：自動偵測原語音軌，並支持自動翻譯成 **繁體中文 (Traditional Chinese)**。
- 🕒 **精準時間戳**：導出 Markdown 包含 `[HH:MM:SS]` 格式，方便快速導航與筆記。
- 🛡️ **Anti-Blocking**：內建併發控制與隨機延遲，有效降低 HTTP 429 (Too Many Requests) 風險。
- 🛠️ **深度適配**：針對特殊的 `youtube-transcript-api` (v1.2.4) 物件結構進行了底層適配。
- 🚀 **極速同步**：使用 `asyncio` 非同步架構，實現高效的並行下載。

---

## 🛠️ 安裝

1. **複製倉庫**
   ```bash
   git clone https://github.com/your-username/youtube-transcript-exporter.git
   cd youtube-transcript-exporter
   ```

2. **安裝依賴**
   ```bash
   pip install -r requirements.txt
   ```

---

## 🚀 快速開始

直接執行 `ytsub_ag02.py` 並傳入 YouTube 連結：

```powershell
# 下載單一影片字幕
python ytsub_ag02.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# 下載整個播放清單
python ytsub_ag02.py "https://www.youtube.com/playlist?list=PLB07BBAD7BF2ED4A1"

# 下載頻道所有影片字幕
python ytsub_ag02.py "https://www.youtube.com/c/YourChannelHandle"
```

---

## 📂 輸出範例

導出的 Markdown 結構完美適配筆記軟體（如 Obsidian, Notion）：

```markdown
# 影片標題 (Video Title)
URL: https://youtu.be/dQw4w9WgXcQ

## Transcript
[00:00:10] 每個人都需要這套工具...
[00:00:45] 它支持非同步導出，速度極快。
...
```

---

## 🤝 貢獻代碼

如果你發現任何 Bug 或有功能建議，歡迎提交 **Pull Requests** 或 **Issues**！

1. Fork 本倉庫
2. 建立你的 Feature Branch (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到 Branch (`git push origin feature/AmazingFeature`)
5. 開啟 Pull Request

---

## 📄 開源協議

本專案基於 **MIT License** 開源。

---

> [!TIP]
> 如果你在短時間內下載過多影片遇到 429 限制，建議更換 IP 或調整程式中的隨機延遲參數。
