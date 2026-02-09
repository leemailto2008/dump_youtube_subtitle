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
        video_infos = []
        folder_name = "downloads"
        
        playlist_match = re.search(r"list=([^&]+)", url)
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
        """極限抓取策略：讓 yt-dlp 直接處理自動翻譯軌道."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # 關鍵：在 yt-dlp 階段就指定要翻譯成 zh-Hant
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['zh-Hant', 'en'], # 請求繁體中文
            'quiet': True,
            'no_warnings': True,
        }

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(video_url, download=False))
            
            # yt-dlp 會把翻譯過的軌道放在 requested_subtitles
            requested_subs = info.get('requested_subtitles')
            
            sub_url = None
            if requested_subs:
                # 優先抓取我們請求的翻譯軌道
                for lang in ['zh-Hant', 'en']:
                    if lang in requested_subs:
                        sub_url = requested_subs[lang].get('url')
                        break
            
            # 如果還是找不到，嘗試從 automatic_captions 暴力搜尋
            if not sub_url:
                auto_caps = info.get('automatic_captions', {})
                # 尋找任何包含 'en' 的鍵 (例如 'en-orig', 'en-US')
                en_key = next((k for k in auto_caps.keys() if 'en' in k), None)
                if en_key:
                    # 獲取該語言的第一個格式 URL 並強制翻譯
                    sub_url = auto_caps[en_key][0]['url']
                    if 'tlang=zh-Hant' not in sub_url:
                        sub_url += '&tlang=zh-Hant'
                    if 'fmt=json3' not in sub_url:
                        sub_url += '&fmt=json3'

            if not sub_url:
                return None

            resp = await self.http_client.get(sub_url)
            if resp.status_code == 200:
                data = resp.json()
                # 判斷是否為 json3 格式
                if 'events' in data:
                    return self.parse_json3(data)
                else:
                    # 若是其他格式 (如 vtt)，這裡做簡單處理
                    return str(data)[:1000] + "... (格式非 JSON3，解析受限)"
                    
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
            
            # 過濾掉樣式標記，只留純文字
            text = "".join([seg.get('utf8', '') for seg in event['segs']]).strip()
            # 排除掉重複的換行或空行
            if text and text != "\n":
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
        print("Usage: python ytsub_my23.py \"<URL>\" [Custom_Folder]")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    downloader = YtDlpTranscriptDownloader(max_concurrent=1) # 單影片模式建議並發設為 1
    video_list, suggested_dir = downloader.get_video_infos(input_url)
    
    if not video_list:
        print("[-] 找不到影片資訊。")
        await downloader.close()
        return

    final_dir = user_path if user_path else downloader.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")

    try:
        tasks = [downloader.process_video(v, final_dir) for v in video_list]
        await asyncio.gather(*tasks)
    finally:
        await downloader.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())