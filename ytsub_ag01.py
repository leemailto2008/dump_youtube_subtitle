# r:\transcript\ytsub_ag01.py
import asyncio
import sys
import os
import re
import json
import httpx
import aiofiles
import subprocess
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeSubExporter:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    def sanitize(self, text: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        video_infos = []
        folder_name = "downloads"
        
        playlist_match = re.search(r"list=([^&]+)", url)
        channel_match = re.search(r"(?:channel/|c/|/(@[a-zA-Z0-9_-]+))", url)
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)

        try:
            if playlist_match:
                p_id = playlist_match.group(1)
                videos = scrapetube.get_playlist(p_id)
                folder_name = f"Playlist_{p_id}"
                for v in videos:
                    v_id = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif channel_match and not video_id_match:
                handle = channel_match.group(1) if channel_match.group(1) else "Channel"
                folder_name = f"Channel_{handle}"
                videos = scrapetube.get_channel(channel_url=url)
                for v in videos:
                    v_id = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif video_id_match:
                v_id = video_id_match.group(1)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        except Exception: pass
            
        return video_infos, folder_name

    async def fetch_transcript_api(self, video_id: str) -> Optional[str]:
        """適配特殊版本 1.2.4 的物件結構."""
        try:
            # 必須實例化
            api = YouTubeTranscriptApi()
            
            # 使用 fetch 直接獲取 (已知此版本 fetch 可運作)
            data = await asyncio.to_thread(api.fetch, video_id)
            
            # 判斷回傳類型
            if hasattr(data, 'snippets'):
                # 處理 FetchedTranscript 物件
                lines = []
                for s in data.snippets:
                    # 經測試屬性為 start (秒), text (內容)
                    start = getattr(s, 'start', 0)
                    text = getattr(s, 'text', '').replace('\n', ' ').strip()
                    if text:
                        h, m = divmod(int(start), 3600)
                        m, s_val = divmod(m, 60)
                        timestamp = f"[{h:02d}:{m:02d}:{s_val:02d}]"
                        lines.append(f"{timestamp} {text}")
                return "\n".join(lines)
            
            # 處理標準 list[dict]
            return "\n".join([
                f"[{int(e['start']//3600):02d}:{int((e['start']%3600)//60):02d}:{int(e['start']%60):02d}] {e['text'].replace('\n', ' ')}"
                for e in data
            ])
        except Exception as e:
            # print(f"DEBUG API Error: {e}")
            return None

    async def fetch_video_title(self, video_id: str) -> str:
        """嘗試獲取影片真實標題."""
        try:
            res = await asyncio.to_thread(subprocess.run, 
                [sys.executable, '-m', 'yt_dlp', '--get-title', f"https://youtu.be/{video_id}"],
                capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res.stdout.strip()
        except: pass
        return f"Video_{video_id}"

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 處理中: {info.title}")
            
            # 獲取標題與字幕
            if info.title.startswith("Video_") or info.title == "Unknown":
                real_title = await self.fetch_video_title(info.video_id)
                info.title = real_title
            
            transcript = await self.fetch_transcript_api(info.video_id)
            
            if not transcript:
                print(f"[-] 無法獲取字幕: {info.title}")
                return

            safe_title = self.sanitize(info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                await f.write(content)
            print(f"[+] 成功匯出: {filename}")

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_ag01.py \"<URL>\" [自訂資料夾]")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    exporter = YouTubeSubExporter(max_concurrent=5)
    video_list, suggested_dir = exporter.get_video_infos(input_url)
    
    if not video_list:
        print("[-] 找不到影片。")
        return

    final_dir = user_path if user_path else exporter.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 共 {len(video_list)} 部影片")

    tasks = [exporter.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
