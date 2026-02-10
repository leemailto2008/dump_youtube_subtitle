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
    def __init__(self, max_concurrent: int = 3):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.http_client = httpx.AsyncClient(timeout=10.0)

    @staticmethod
    def sanitize(text: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        """使用 scrapetube 獲取清單資訊 (保持輕量)."""
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
        """利用 yt-dlp 獲取字幕 JSON 並解析."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # yt-dlp 參數：只抓取字幕元數據，不下載影片
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en.*', 'zh-Hant.*', 'zh-Hans.*', 'zh-TW.*'],
            'quiet': True,
            'no_warnings': True,
        }

        try:
            # 1. 使用 yt-dlp 提取字幕 URL
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(video_url, download=False))
            
            requested_subtitles = info.get('requested_subtitles')
            if not requested_subtitles:
                return None

            # 優先權：找繁中 -> 找英文 -> 找第一個
            sub_info = (requested_subtitles.get('zh-Hant') or 
                        requested_subtitles.get('en') or 
                        next(iter(requested_subtitles.values())))
            
            sub_url = sub_info.get('url')
            if not sub_url: return None

            # 2. 非同步請求字幕內容 (json3 格式)
            response = await self.http_client.get(sub_url)
            if response.status_code != 200: return None
            
            content = response.json()
            return self.parse_json3(content)

        except Exception as e:
            return None

    def parse_json3(self, data: dict) -> str:
        """解析 YouTube json3 字幕格式."""
        lines = []
        events = data.get('events', [])
        for event in events:
            if 'segs' not in event: continue
            
            start_ms = event.get('tStartMs', 0)
            s, ms = divmod(start_ms, 1000)
            m, s = divmod(s, 60)
            h, m = divmod(m, 60)
            timestamp = f"[{h:02d}:{m:02d}:{s:02d}]"
            
            text = "".join([seg.get('utf8', '') for seg in event['segs']]).strip()
            if text:
                lines.append(f"{timestamp} {text}")
        
        return "\n".join(lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 正在抓取: {info.title}...")
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] 失敗 (yt-dlp 也找不到字幕): {info.title}")
                return

            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                await f.write(f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n")
            print(f"[+] 成功匯出: {filename}")

    async def close(self):
        await self.http_client.aclose()

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_ytdlp.py \"<URL>\" [Folder]")
        return

    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    downloader = YtDlpTranscriptDownloader(max_concurrent=3)
    video_list, suggested_dir = downloader.get_video_infos(url)
    
    if not video_list:
        print("[-] 找不到影片。")
        await downloader.close()
        return

    final_dir = user_path if user_path else downloader.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] 輸出至: {os.path.abspath(final_dir)}")
    try:
        tasks = [downloader.process_video(v, final_dir) for v in video_list]
        await asyncio.gather(*tasks)
    finally:
        await downloader.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())