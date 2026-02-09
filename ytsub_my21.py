import asyncio
import sys
import re
import os
import aiofiles
import httpx
from typing import List, Optional, Tuple
from pydantic import BaseModel
import yt_dlp
import scrapetube

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YtDlpTranscriptDownloader:
    def __init__(self, max_concurrent: int = 2):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.http_client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    @staticmethod
    def sanitize(text: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        video_infos = []
        folder_name = "downloads"
        v_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        v_id = v_id_match.group(1) if v_id_match else None
        
        try:
            if "list=" in url:
                p_id = re.search(r"list=([^&]+)", url).group(1)
                videos = scrapetube.get_playlist(p_id)
                folder_name = f"Playlist_{p_id}"
                for v in videos:
                    v_id_v = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id_v, url=f"https://youtu.be/{v_id_v}"))
            else:
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        except Exception as e:
            print(f"[-] Meta Error: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """修正後的 yt-dlp 字幕提取邏輯：支援 ASR 與自動翻譯."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,  # 必須開啟 ASR 支持
            'quiet': True,
            'no_warnings': True,
        }

        try:
            loop = asyncio.get_event_loop()
            # 提取元數據
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(video_url, download=False))
            
            # 合併人工字幕與自動字幕清單
            subs = info.get('subtitles', {})
            auto_subs = info.get('automatic_captions', {})
            
            # 優先權：1. 人工繁中 2. 人工英文 3. 自動任何語言
            sub_url = None
            if 'zh-Hant' in subs:
                sub_url = subs['zh-Hant'][0]['url']
            elif 'en' in subs:
                sub_url = subs['en'][0]['url']
            elif auto_subs:
                # 遍歷自動字幕尋找可用軌道 (通常是 en)
                best_key = next((k for k in ['en', 'en-orig', 'zh-Hans'] if k in auto_subs), next(iter(auto_subs.keys())))
                sub_url = auto_subs[best_key][0]['url']

            if not sub_url:
                return None

            # 強力修正：確保請求的是 json3 格式並請求 YouTube 伺服器端翻譯成 zh-Hant (繁體)
            # 如果 URL 裡已經有 tlang，替換它；否則加上它
            if "fmt=json3" not in sub_url:
                sub_url += "&fmt=json3"
            if "tlang=zh-Hant" not in sub_url:
                sub_url += "&tlang=zh-Hant"

            # 下載字幕 JSON
            response = await self.http_client.get(sub_url)
            if response.status_code != 200:
                return None
            
            return self.parse_json3(response.json())

        except Exception as e:
            # print(f"DEBUG: {e}")
            return None

    def parse_json3(self, data: dict) -> str:
        """解析 json3 格式，處理多段文字拼接."""
        lines = []
        events = data.get('events', [])
        for event in events:
            if 'segs' not in event: continue
            
            start_ms = event.get('tStartMs', 0)
            s, ms = divmod(int(start_ms), 1000)
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            timestamp = f"[{h:02d}:{m:02d}:{s:02d}]"
            
            # 拼接所有分段文字 (segs)
            text = "".join([seg.get('utf8', '') for seg in event['segs']]).strip()
            if text and text != "\n":
                lines.append(f"{timestamp} {text}")
        
        return "\n".join(lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 處理中: {info.title}")
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] 無法獲取字幕軌道: {info.title}")
                return

            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)