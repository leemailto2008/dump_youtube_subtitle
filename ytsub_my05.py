import asyncio
import sys
import re
import os
import aiofiles
from typing import List, Optional, Tuple
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeTranscriptDownloader:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def sanitize(text: str) -> str:
        """清理資料夾或檔名中的非法字元 (Sanitize filename/folder)."""
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    @staticmethod
    def extract_id(url: str) -> Optional[str]:
        """從 URL 提取 Video ID 或 Playlist ID."""
        if "list=" in url:
            match = re.search(r"list=([^&]+)", url)
            return match.group(1) if match else None
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        return video_id_match.group(1) if video_id_match else None

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        """獲取影片清單與建議資料夾名."""
        video_infos = []
        folder_name = "downloads"
        try:
            if "list=" in url:
                p_id = self.extract_id(url)
                print(f"[*] 解析播放清單: {p_id}")
                folder_name = f"Playlist_{p_id}"
                videos = scrapetube.get_playlist(p_id)
            elif any(x in url for x in ["channel/", "c/", "/@"]):
                handle = re.search(r"/(@[a-zA-Z0-9_-]+)", url)
                folder_name = handle.group(1) if handle else "Channel_Archive"
                videos = scrapetube.get_channel(channel_url=url)
            else:
                v_id = self.extract_id(url)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"

            for v in videos:
                v_id = v.get('videoId')
                title_data = v.get('title', {}).get('runs', [{}])
                title = title_data[0].get('text', 'Unknown') if title_data else 'Unknown'
                video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
        except Exception as e:
            print(f"[-] 獲取列表失敗: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """極限嘗試抓取字幕，支援所有可用軌道之翻譯."""
        try:
            # 取得所有字幕列表
            ts_list = await asyncio.to_thread(YouTubeTranscriptApi.list_transcripts, video_id)
            
            transcript = None
            # 1. 優先找繁體中文 (zh-TW, zh-Hant, zh-HK)
            try:
                transcript = ts_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
            except NoTranscriptFound:
                # 2. 退而求其次找任何中文或英文，並翻譯
                try:
                    transcript = ts_list.find_transcript(['zh-Hans', 'en', 'ja'])
                    if transcript.is_translatable:
                        transcript = transcript.translate('zh-Hant')
                except NoTranscriptFound:
                    # 3. 強制抓第一個並翻譯
                    try:
                        transcript = next(iter(ts_list))
                        if transcript.is_translatable:
                            transcript = transcript.translate('zh-Hant')
                    except Exception:
                        return None

            if not transcript:
                return None

            data = await asyncio.to_thread(transcript.fetch)
            lines = []
            for e in data:
                m, s = divmod(int(e['start']), 60)
                h, m = divmod(m, 60)
                # 移除換行符避免破壞 Markdown 格式
                clean_text = e['text'].replace('\n', ' ')
                lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {clean_text}")
            
            return "\n".join(lines)

        except TranscriptsDisabled:
            # 影片本身關閉了字幕功能
            return None
        except Exception:
            # 其他如網路錯誤、被 YouTube 阻擋
            return None

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            transcript = await self.fetch_transcript(info.video_id)
            if not transcript:
                print(f"[-] 無字幕或自動字幕未產生: {info.title}")
                return

            # 清理檔名並建立完整路徑
            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] 成功匯出: {filename}")
            except Exception as e:
                print(f"[-] 檔案寫入失敗: {e}")

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_my05.py \"<URL>\" [指定資料夾路徑]")
        return

    url = sys.argv[1]
    # 若有第二個參數則為自定義路徑，否則為 None
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    dl = YouTubeTranscriptDownloader(max_concurrent=8)
    videos, suggested_dir = dl.get_video_infos(url)
    
    if not videos:
        print("[-] 找不到影片資訊。")
        return

    # 優先權：1. 使用者輸入路徑 2. 自動產生的資料夾名 (sanitize 過)
    final_dir = user_path if user_path else dl.sanitize(suggested_dir)
    
    # 建立目錄 (exist_ok=True 避免已存在時噴錯)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 處理進度: 共 {len(videos)} 部影片...")

    tasks = [dl.process_video(v, final_dir) for v in videos]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())