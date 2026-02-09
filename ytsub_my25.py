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
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
        v_id = video_id_match.group(1) if video_id_match else None
        
        if "list=" not in url and v_id:
            print(f"[*] 解析單一影片: {v_id}")
            return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        
        # 處理播放清單 (保持原邏輯)
        p_id_match = re.search(r"list=([^&]+)", url)
        if p_id_match:
            p_id = p_id_match.group(1)
            videos = scrapetube.get_playlist(p_id)
            return [VideoInfo(title=v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown'), 
                              video_id=v.get('videoId'), url=f"https://youtu.be/{v.get('videoId')}") for v in videos], f"Playlist_{p_id}"
        
        return [], "downloads"

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """強力抓取：嘗試多種語言代碼與翻譯路徑."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # 讓 yt-dlp 負責枚舉所有翻譯軌道
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['zh-Hant', 'zh-TW', 'en'],
            'quiet': True,
        }

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(video_url, download=False))
            
            # 優先搜尋已由 yt-dlp 處理好的翻譯軌道 (requested_subtitles)
            subs = info.get('requested_subtitles') or {}
            target_sub = subs.get('zh-Hant') or subs.get('zh-TW') or subs.get('en')
            
            if not target_sub:
                # 若找不到翻譯軌道，嘗試從自動字幕中暴力搜尋包含 'en' 的軌道
                auto_caps = info.get('automatic_captions', {})
                en_key = next((k for k in auto_caps.keys() if 'en' in k), None)
                if en_key:
                    target_sub = auto_caps[en_key][0]
                    # 手動嘗試追加翻譯參數 (最後手段)
                    if 'tlang=zh-Hant' not in target_sub['url']:
                        target_sub['url'] += '&tlang=zh-Hant'
            
            if not target_sub: return None

            resp = await self.http_client.get(target_sub['url'])
            if resp.status_code != 200: return None

            # 根據回傳內容判斷格式
            content_text = resp.text
            if '"events"' in content_text:
                return self.parse_json3(resp.json())
            else:
                return self.clean_vtt(content_text)
                
        except Exception:
            return None

    def parse_json3(self, data: dict) -> str:
        lines = []
        for event in data.get('events', []):
            if 'segs' not in event: continue
            start = event.get('tStartMs', 0)
            ts = f"[{int(start//3600000):02d}:{int((start%3600000)//60000):02d}:{int((start%60000)//1000):02d}]"
            text = "".join([s.get('utf8', '') for s in event['segs']]).strip()
            if text: lines.append(f"{ts} {text}")
        return "\n".join(lines)

    def clean_vtt(self, vtt_text: str) -> str:
        """強力清洗 VTT 字幕標籤."""
        lines = []
        # 移除 VTT 標頭與樣式定義
        content = re.sub(r'WEBVTT[\s\S]*?\n\n', '', vtt_text)
        # 移除時間戳後面的樣式屬性 (如 align:start)
        content = re.sub(r'(\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}).*', r'\1', content)
        # 移除 HTML 類型的標籤 (如 <c>, <00:00:00.000>)
        content = re.sub(r'<[^>]+>', '', content)
        
        # 簡單解析時間與內容
        for block in content.split('\n\n'):
            parts = block.strip().split('\n')
            if len(parts) >= 2:
                # 轉換 00:00:00.000 為 [00:00:00]
                ts_match = re.match(r'(\d{2}:\d{2}:\d{2})', parts[0])
                if ts_match:
                    ts = f"[{ts_match.group(1)}]"
                    text = " ".join(parts[1:]).strip()
                    if text: lines.append(f"{ts} {text}")
        return "\n".join(lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 處理中: {info.title}")
            transcript = await self.fetch_transcript(info.video_id)
            if not transcript:
                print(f"[-] 無法抓取: {info.title}")
                return

            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            async with aiofiles.open(os.path.join(target_dir, filename), "w", encoding="utf-8") as f:
                await f.write(f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n")
            print(f"[+] 匯出成功: {filename}")

    async def close(self):
        await self.http_client.aclose()

async def main():
    if len(sys.argv) < 2: return
    dl = YtDlpTranscriptDownloader()
    video_list, folder = dl.get_video_infos(sys.argv[1])
    target_path = sys.argv[2] if len(sys.argv) > 2 else dl.sanitize(folder)
    os.makedirs(target_path, exist_ok=True)
    print(f"[*] 目標目錄: {os.path.abspath(target_path)}")
    try:
        await asyncio.gather(*[dl.process_video(v, target_path) for v in video_list])
    finally:
        await dl.close()

if __name__ == "__main__":
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())