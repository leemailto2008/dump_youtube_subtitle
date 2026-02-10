import asyncio
import sys
import re
import aiofiles  # 建議安裝: pip install aiofiles
from typing import List, Optional
from pydantic import BaseModel, Field
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str
    # 增加更多 Metadata 有助於日後整理
    publish_date: Optional[str] = None

class YouTubeTranscriptDownloader:
    def __init__(self, max_concurrent: int = 5, output_dir: str = "."):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.output_dir = output_dir
        self.api = YouTubeTranscriptApi()

    @staticmethod
    def extract_id(url: str, param: str = "v") -> Optional[str]:
        """精確提取 YouTube ID."""
        if "list=" in url and param == "list":
            match = re.search(r"list=([^&]+)", url)
            return match.group(1) if match else None
        
        # 處理 short URL 與標準 URL
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        return video_id_match.group(1) if video_id_match else None

    def get_video_infos(self, url: str) -> List[VideoInfo]:
        """使用 scrapetube 獲取元數據."""
        video_infos = []
        try:
            if "list=" in url:
                playlist_id = self.extract_id(url, "list")
                videos = scrapetube.get_playlist(playlist_id)
            elif any(x in url for x in ["channel/", "c/", "/@"]):
                videos = scrapetube.get_channel(channel_url=url)
            else:
                video_id = self.extract_id(url)
                # 直接封裝避免 scrapetube 單片查詢的不穩定性
                return [VideoInfo(title=f"video_{video_id}", video_id=video_id, url=url)]

            for v in videos:
                v_id = v.get('videoId')
                title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
        except Exception as e:
            print(f"Error fetching metadata: {e}")
        return video_infos

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """非同步抓取字幕."""
        try:
            # 優先搜尋語言順序
            transcript_list = await asyncio.to_thread(self.api.list, video_id)
            try:
                transcript = transcript_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK', 'en'])
            except:
                transcript = next(iter(transcript_list))
            
            data = await asyncio.to_thread(transcript.fetch)
            
            lines = []
            for entry in data:
                m, s = divmod(int(entry['start']), 60)
                h, m = divmod(m, 60)
                timestamp = f"{h:02d}:{m:02d}:{s:02d}"
                text = entry['text'].replace('\n', ' ')
                lines.append(f"[{timestamp}] {text}")
            return "\n".join(lines)
            
        except (TranscriptsDisabled, NoTranscriptFound):
            print(f"[-] Subtitles disabled or not found: {video_id}")
        except Exception as e:
            print(f"[-] Unexpected error for {video_id}: {e}")
        return None

    async def save_to_file(self, info: VideoInfo, content: str):
        """非同步寫入檔案，避免 I/O 阻塞."""
        safe_title = re.sub(r'[\\/*?:"<>|]', "", info.title)
        filename = f"{self.output_dir}/{safe_title}_{info.video_id}.md"
        
        header = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n"
        try:
            async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                await f.write(header + content)
            print(f"[+] Saved: {filename}")
        except OSError as e:
            print(f"[-] File system error: {e}")

    async def process_video(self, info: VideoInfo):
        async with self.semaphore:
            print(f"[*] Fetching: {info.title}")
            transcript = await self.fetch_transcript(info.video_id)
            if transcript:
                await self.save_to_file(info, transcript)

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub.py <youtube_url>")
        return

    target_url = sys.argv[1]
    downloader = YouTubeTranscriptDownloader(max_concurrent=10) # 提高並發數
    
    video_list = downloader.get_video_infos(target_url)
    if not video_list:
        print("No videos found.")
        return

    print(f"[*] Found {len(video_list)} videos. Starting...")
    tasks = [downloader.process_video(info) for info in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())