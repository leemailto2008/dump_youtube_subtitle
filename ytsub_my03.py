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

    @staticmethod
    def extract_id(url: str) -> Optional[str]:
        """提取 ID."""
        if "list=" in url:
            match = re.search(r"list=([^&]+)", url)
            return match.group(1) if match else None
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        return video_id_match.group(1) if video_id_match else None

    def get_video_infos(self, url: str) -> List[VideoInfo]:
        """獲取元數據."""
        video_infos = []
        try:
            if "list=" in url:
                p_id = self.extract_id(url)
                print(f"[*] Detecting Playlist: {p_id}")
                videos = scrapetube.get_playlist(p_id)
            elif any(x in url for x in ["channel/", "c/", "/@"]):
                videos = scrapetube.get_channel(channel_url=url)
            else:
                v_id = self.extract_id(url)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")]

            for v in videos:
                v_id = v.get('videoId')
                # 處理標題抓取邏輯
                title_data = v.get('title', {}).get('runs', [{}])
                title = title_data[0].get('text', 'Unknown') if title_data else 'Unknown'
                video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
        except Exception as e:
            print(f"[-] Metadata extraction failed: {e}")
        return video_infos

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """修正後的字幕抓取邏輯."""
        try:
            # 修正：直接調用類別方法，避免實例化導致的 AttributeError
            transcript_list = await asyncio.to_thread(
                YouTubeTranscriptApi.list_transcripts, video_id
            )
            
            try:
                # 優先找繁體中文 (含自動產生)
                transcript = transcript_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
            except NoTranscriptFound:
                try:
                    # 備選：找任何中文或英文，若非繁中則進行翻譯
                    transcript = transcript_list.find_transcript(['zh-Hans', 'en'])
                    if transcript.language_code != 'zh-TW':
                        transcript = transcript.translate('zh-Hant')
                except NoTranscriptFound:
                    # 最後手段：抓取第一個可用的並翻譯
                    transcript = next(iter(transcript_list))
                    if transcript.is_translatable:
                        transcript = transcript.translate('zh-Hant')
                    else:
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
            
        except Exception as e:
            # 這裡會捕捉到 TranscriptsDisabled 或其他 API 錯誤
            return None

    async def process_video(self, info: VideoInfo):
        async with self.semaphore:
            transcript = await self.fetch_transcript(info.video_id)
            if not transcript:
                print(f"[-] Skipped (No Captions): {info.title}")
                return

            safe_title = re.sub(r'[\\/*?:"<>|]', "", info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(filename, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] Exported: {filename}")
            except Exception as e:
                print(f"[-] Write Error: {e}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_my03.py \"<url>\"")
        return

    url = sys.argv[1]
    downloader = YouTubeTranscriptDownloader(max_concurrent=5)
    
    video_list = downloader.get_video_infos(url)
    if not video_list:
        return

    print(f"[*] Found {len(video_list)} videos. Processing...")
    tasks = [downloader.process_video(info) for info in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())