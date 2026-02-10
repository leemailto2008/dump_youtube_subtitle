# YouTube 字幕匯出工具 (YouTube Subtitle Exporter) 規格書

## 1. 目標 (Objective)
建立一個 CLI 工具，輸入 YouTube 連結（單一影片或播放清單 (Playlist)），自動爬取字幕並彙整輸出為 Markdown 檔案。

## 2. 功能需求 (Functional Requirements)
- **支援單一連結:** 輸入單個 YouTube Video URL 即可下載。
- **支援播放清單 (Playlist Support):** 輸入 Playlist URL 時，應自動解析所有影片並續一處理。
- **自動摘要與整理:** 將字幕依據時間戳 (Timestamp) 或分段進行整理。
- **Markdown 輸出:** 每個影片產出一個 `.md` 檔案，檔名使用影片標題。
- **非同步處理 (Asynchronous Processing):** 使用 `asyncio` 提升多影片處裡的效率。

## 3. 技術棧 (Technology Stack)
- **語言:** Python 3.10+
- **核心套件:**
    - `yt-dlp`: 抓取影片資訊 (Metadata) 與播放清單解析。
    - `youtube-transcript-api`: 獲取字幕 (Transcript)。
    - `httpx`: 非同步 I/O。
    - `pydantic`: 資料模型 (Data Model)。
- **CLI 框架:** `argparse` 或內建 `sys.argv` (KISS 原則)。

## 4. 檔案結構 (File Structure)
- `r:\transcript\ytsub.py`: 主要執行檔。
- `r:\transcript\requirements.txt`: 必要相依套件。

## 5. 實作流程 (Implementation Steps)
1. 安裝相依套件。
2. 實作影片連結解析逻辑。
3. 實作字幕抓取與清理 (Translation/Cleanup)。
4. 實作 Markdown 產生器。
5. 封裝為 CLI 工具。
