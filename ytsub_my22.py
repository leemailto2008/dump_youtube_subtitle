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
        self.http_client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    @staticmethod
    def sanitize(text: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        """解析 URL，支援播放清單與單一影片."""
        video_infos = []
        folder_name = "downloads"
        
        # 1. 嘗試提取 Playlist ID
        playlist_match = re.search(r"list=([^&]+)", url)
        # 2. 嘗試提取 Video ID
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
        
        try:
            if "list=" in url and playlist_match:
                p_id = playlist_match.group(1)
                print(f"[*] 解析播放清單: {p_id}")
                videos = scrapetube.get_playlist(p_id)
                folder_name = f"Playlist_{p_id}"
                for v in videos:
                    v_id = v.get('videoId')
                    title_data = v.get('title', {}).get('runs', [{}])
                    title = title_data[0].get('text', 'Unknown') if title_data else 'Unknown'
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif video_id_match:
                v_id = video_id_match.group(1)
                print(f"[*] 解析單一影片: {v_id}")
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        except Exception as e:
            print(f"[-] Meta Error: {e}")
            
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """使用 yt-dlp 獲取字幕 JSON 並強制要求伺服器端翻譯."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        try:
            loop = asyncio.get_event_loop()
            # 提取所有字幕資訊
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(video_url, download=False))
            
            # 合併字幕字典
            all_subs = {**info.get('subtitles', {}), **info.get('automatic_captions', {})}
            
            if not all_subs:
                return None

            # 搜尋可用語言代碼：優先找中英文
            target_key = None
            for key in ['zh-Hant', 'zh-TW', 'en', 'en-orig', 'en-US']:
                if key in all_subs:
                    target_key = key
                    break
            
            if not target_key:
                target_key = next(iter(all_subs.keys()))

            # 獲取 JSON3 格式的 URL
            sub_url = None
            for sub_item in all_subs[target_key]:
                if sub_item.get('ext') == 'json3' or 'fmt=json3' in sub_item.get('url', ''):
                    sub_url = sub_item['url']
                    break
            
            if not sub_url:
                sub_url = all_subs[target_key][0]['url']

            # 強制添加 json3 格式與繁中翻譯參數
            if "fmt=json3" not in sub_url: sub_url += "&fmt=json3"
            if "tlang=zh-Hant" not in sub_url: sub_url += "&tlang=zh-Hant"

            resp = await self.http_client.get(sub_url)
            if resp.status_code == 200:
                return self.parse_json3(resp.json())
        except Exception:
            return None
        return None

    def parse_json3(self, data: dict) -> str:
        lines = []
        events = data.get('events', [])
        for event in events:
            if 'segs' not in event: continue
            start_ms = event.get('tStartMs', 0)
            m, s = divmod(int(start_ms // 1000), 60)
            h, m = divmod(m, 60)
            timestamp = f"[{h:02d}:{m:02d}:{s:02d}]"
            text = "".join([seg.get('utf8', '') for seg in event['segs']]).strip()
            if text:
                lines.append(f"{timestamp} {text}")
        return "\n".join(lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 處理中: {info.title} ({info.video_id})")
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] 無法獲取字幕軌道: {info.title}")
                return

            safe_title = self.sanitize(info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
                await f.write(content)
            print(f"[+] 匯出成功: {filename}")

    async def close(self):
        await self.http_client.aclose()

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_my22.py \"<URL>\" [Custom_Folder]")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    downloader = YtDlpTranscriptDownloader(max_concurrent=2)
    video_list, suggested_dir = downloader.get_video_infos(input_url)
    
    if not video_list:
        print("[-] 找不到影片資訊。")
        await downloader.close()
        return

    final_dir = user_path if user_path else downloader.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 預計處理: {len(video_list)} 部影片")

    try:
        tasks = [downloader.process_video(v, final_dir) for v in video_list]
        await asyncio.gather(*tasks)
    finally:
        await downloader.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())