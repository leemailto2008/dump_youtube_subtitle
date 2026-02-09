import asyncio
import sys
import re
import aiofiles
from typing import List, Optional
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeTranscriptDownloader:
    def __init__(self, max_concurrent: int = 5, output_dir: str = "."):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.output_dir = output_dir
        # YouTubeTranscriptApi 本身不支援非同步，需透過 ThreadPool 運行
        self.api = YouTubeTranscriptApi()

    @staticmethod
    def extract_id(url: str) -> Optional[str]:
        """精確提取 YouTube Video ID 或 Playlist ID."""
        if "list=" in url:
            match = re.search(r"list=([^&]+)", url)
            return match.group(1) if match else None
        
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        return video_id_match.group(1) if video_id_match else None

    def get_video_infos(self, url: str) -> List[VideoInfo]:
        """解析 URL 並獲取影片清單."""
        video_infos = []
        try:
            if "list=" in url:
                p_id = self.extract_id(url)
                print(f"[*] Detecting Playlist: {p_id}")
                videos = scrapetube.get_playlist(p_id)
            elif any(x in url for x in ["channel/", "c/", "/@"]):
                print(f"[*] Detecting Channel: {url}")
                videos = scrapetube.get_channel(channel_url=url)
            else:
                v_id = self.extract_id(url)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")]

            for v in videos:
                v_id = v.get('videoId')
                title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
        except Exception as e:
            print(f"[-] Metadata extraction failed: {e}")
        return video_infos

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """
        核心邏輯：支援自動生成字幕與自動翻譯
        優先級：繁中(人工) > 繁中(自動) > 簡中(自動) > 英文(自動) > 自動翻譯回繁中
        """
        try:
            # 使用 to_thread 避免同步庫阻塞 Event Loop
            transcript_list = await asyncio.to_thread(YouTubeTranscriptApi.list_transcripts, video_id)
            
            try:
                # 1. 嘗試直接找尋中文或英文 (含自動生成)
                transcript = transcript_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK', 'zh-Hans', 'en'])
            except NoTranscriptFound:
                # 2. 如果都沒有，嘗試抓取清單中第一個可用的字幕並翻譯成繁中
                transcript = next(iter(transcript_list))
                if transcript.is_translatable:
                    transcript = transcript.translate('zh-Hant')
                else:
                    print(f"[-] No translatable captions for {video_id}")
                    return None

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
            return None
        except Exception as e:
            print(f"[-] Error processing {video_id}: {type(e).__name__}")
            return None

    async def process_video(self, info: VideoInfo):
        """處理單一影片的下載與存檔."""
        async with self.semaphore:
            transcript = await self.fetch_transcript(info.video_id)
            if not transcript:
                print(f"[-] Skipped (No Captions): {info.title}")
                return

            # 清理檔名並寫入
            safe_title = re.sub(r'[\\/*?:"<>|]', "", info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] Exported: {filename}")
            except Exception as e:
                print(f"[-] Write Error {info.video_id}: {e}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub.py \"<youtube_url>\"")
        return

    url = sys.argv[1]
    downloader = YouTubeTranscriptDownloader(max_concurrent=5)
    
    video_list = downloader.get_video_infos(url)
    if not video_list:
        print("[-] No videos to process.")
        return

    print(f"[*] Found {len(video_list)} videos. Starting concurrent download...")
    tasks = [downloader.process_video(info) for info in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    # 針對 Windows 的特定 Event Loop 處理（若需要）
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())