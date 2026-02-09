import asyncio
import sys
import re
import os
import aiofiles
from typing import List, Optional, Tuple
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeTranscriptDownloader:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def sanitize(text: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    @staticmethod
    def extract_id(url: str) -> Optional[str]:
        if "list=" in url:
            match = re.search(r"list=([^&]+)", url)
            return match.group(1) if match else None
        video_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
        return video_id_match.group(1) if video_id_match else None

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        video_infos = []
        folder_name = "downloads"
        try:
            if "list=" in url:
                p_id = self.extract_id(url)
                folder_name = f"Playlist_{p_id}"
                videos = scrapetube.get_playlist(p_id)
            elif any(x in url for x in ["channel/", "c/", "/@"]):
                handle = re.search(r"/(@[a-zA-Z0-9_-]+)", url)
                folder_name = handle.group(1) if handle else "Channel_Archive"
                videos = scrapetube.get_channel(channel_url=url)
            else:
                v_id = self.extract_id(url)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"

            for v in videos:
                v_id = v.get('videoId')
                title_data = v.get('title', {}).get('runs', [{}])
                title = title_data[0].get('text', 'Unknown') if title_data else 'Unknown'
                video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
        except Exception as e:
            print(f"[-] Metadata extraction error: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """
        強力抓取邏輯：
        1. 優先透過 list_transcripts 嘗試繁中與翻譯。
        2. 若失敗，直接調用 get_transcript 請求英文 (en) 或原始語言。
        """
        try:
            # 第一階段：嘗試從字幕列表獲取並翻譯
            try:
                ts_list = await asyncio.to_thread(YouTubeTranscriptApi.list_transcripts, video_id)
                try:
                    transcript = ts_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
                except NoTranscriptFound:
                    # 嘗試抓英文/簡中並翻譯
                    try:
                        transcript = ts_list.find_transcript(['en', 'zh-Hans'])
                        if transcript.is_translatable:
                            transcript = transcript.translate('zh-TW')
                    except NoTranscriptFound:
                        # 抓取第一個可用的並翻譯
                        transcript = next(iter(ts_list))
                        if transcript.is_translatable:
                            transcript = transcript.translate('zh-TW')
                
                data = await asyncio.to_thread(transcript.fetch)
            
            except Exception:
                # 第二階段：API 列舉失敗，直接暴力請求 get_transcript
                # 許多 Windows 故障排除影片 (如 RIVcqT2OGPA) 預設為 en
                try:
                    data = await asyncio.to_thread(
                        YouTubeTranscriptApi.get_transcript, video_id, languages=['en', 'zh-TW', 'zh-CN']
                    )
                except Exception:
                    return None

            if not data:
                return None

            lines = []
            for entry in data:
                m, s = divmod(int(entry['start']), 60)
                h, m = divmod(m, 60)
                timestamp = f"[{h:02d}:{m:02d}:{s:02d}]"
                text = entry['text'].replace('\n', ' ')
                lines.append(f"{timestamp} {text}")
            
            return "\n".join(lines)

        except Exception:
            return None

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            transcript = await self.fetch_transcript(info.video_id)
            if not transcript:
                print(f"[-] Skip (No Transcript available): {info.title}")
                return

            safe_title = self.sanitize(info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] Success: {filename}")
            except Exception as e:
                print(f"[-] Write Error: {e}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_my07.py \"<URL>\" [Folder]")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    dl = YouTubeTranscriptDownloader(max_concurrent=5)
    video_list, suggested_dir = dl.get_video_infos(input_url)
    
    if not video_list:
        print("[-] No videos found.")
        return

    final_dir = user_path if user_path else dl.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] Output: {os.path.abspath(final_dir)}")
    tasks = [dl.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())