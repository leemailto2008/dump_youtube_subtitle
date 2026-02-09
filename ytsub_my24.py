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
    def __init__(self, max_concurrent: int = 1):
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
            print(f"[-] 元數據提取錯誤: {e}")
            
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """終極字幕抓取策略：精確匹配 JSON3 翻譯軌道."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # 關鍵：強制列出所有可用字幕但不下載
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'quiet': True,
            'no_warnings': True,
        }

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(video_url, download=False))
            
            # 獲取自動字幕字典
            auto_caps = info.get('automatic_captions', {})
            manual_subs = info.get('subtitles', {})
            
            # 尋找原始語言鍵 (通常是 'en' 或 'en-orig')
            src_lang = None
            for lang in ['en', 'en-orig', 'zh-TW', 'zh-Hant']:
                if lang in auto_caps or lang in manual_subs:
                    src_lang = lang
                    break
            
            if not src_lang:
                # 沒找到就抓第一個
                src_lang = next(iter(auto_caps.keys())) if auto_caps else next(iter(manual_subs.keys()), None)

            if not src_lang: return None

            # 取得原始軌道的資料 (優先從自動字幕拿)
            cap_data = auto_caps.get(src_lang) or manual_subs.get(src_lang)
            
            # 尋找 JSON3 格式的原始 URL
            base_url = None
            for item in cap_data:
                if item.get('ext') == 'json3' or 'fmt=json3' in item.get('url', ''):
                    base_url = item['url']
                    break
            
            if not base_url: base_url = cap_data[0]['url']

            # 核心修正：手動構建翻譯與格式化參數，確保伺服器端穩定回傳
            final_url = base_url
            if 'fmt=json3' not in final_url: final_url += '&fmt=json3'
            if 'tlang=zh-Hant' not in final_url: final_url += '&tlang=zh-Hant'

            resp = await self.http_client.get(final_url)
            if resp.status_code == 200:
                return self.parse_json3(resp.json())
                
        except Exception:
            return None
        return None

    def parse_json3(self, data: dict) -> str:
        """精確解析 JSON3 格式."""
        lines = []
        events = data.get('events', [])
        for event in events:
            if 'segs' not in event: continue
            
            # 計算時間戳
            start_ms = event.get('tStartMs', 0)
            seconds = int(start_ms // 1000)
            m, s = divmod(seconds, 60)
            h, m = divmod(m, 60)
            timestamp = f"[{h:02d}:{m:02d}:{s:02d}]"
            
            # 提取文字
            text = "".join([seg.get('utf8', '') for seg in event['segs']]).strip()
            # 排除掉格式標記與空行
            if text and not text.isspace():
                lines.append(f"{timestamp} {text}")
        
        return "\n".join(lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 正在獲取字幕: {info.title}")
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] 無法從 YouTube 伺服器獲取任何字幕軌道: {info.title}")
                return

            safe_title = self.sanitize(info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                await f.write(content)
            print(f"[+] 成功匯出檔案: {filename}")

    async def close(self):
        await self.http_client.aclose()

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_my24.py \"<URL>\"")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    downloader = YtDlpTranscriptDownloader(max_concurrent=1)
    video_list, suggested_dir = downloader.get_video_infos(input_url)
    
    if not video_list:
        await downloader.close()
        return

    final_dir = user_path if user_path else downloader.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 目標目錄: {os.path.abspath(final_dir)}")

    try:
        tasks = [downloader.process_video(v, final_dir) for v in video_list]
        await asyncio.gather(*tasks)
    finally:
        await downloader.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())