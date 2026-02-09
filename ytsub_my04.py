import asyncio
import sys
import re
import os
import aiofiles
from typing import List, Optional, Tuple
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeTranscriptDownloader:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def sanitize(text: str) -> str:
        """清理非法字元."""
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
                print(f"[*] 解析播放清單: {p_id}")
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
            print(f"[-] 獲取列表失敗: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """最強力字幕抓取：不設語言限制，抓到就翻繁中."""
        try:
            # 必須使用 list_transcripts 才能看到自動生成的字幕
            ts_list = await asyncio.to_thread(YouTubeTranscriptApi.list_transcripts, video_id)
            
            # 策略：嘗試繁中 -> 嘗試翻譯任何現有字幕 -> 報錯
            try:
                # 1. 嘗試找現成的繁體
                transcript = ts_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
            except:
                # 2. 找不到繁體，就抓第一個可用的（不論是英、日、簡中自動生成）並翻譯
                transcript = next(iter(ts_list))
                if transcript.is_translatable:
                    transcript = transcript.translate('zh-Hant')
                else:
                    # 如果不可翻譯且不是中文，那就直接抓取
                    pass

            data = await asyncio.to_thread(transcript.fetch)
            lines = []
            for e in data:
                m, s = divmod(int(e['start']), 60)
                h, m = divmod(m, 60)
                lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {e['text'].replace('\n', ' ')}")
            return "\n".join(lines)
        except Exception:
            return None

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            transcript = await self.fetch_transcript(info.video_id)
            if not transcript:
                print(f"[-] 無法取得字幕: {info.title}")
                return

            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                await f.write(content)
            print(f"[+] 成功匯出: {filename}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_final.py \"<URL>\" [資料夾名稱]")
        return

    url = sys.argv[1]
    custom_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    dl = YouTubeTranscriptDownloader(max_concurrent=8)
    videos, suggested_dir = dl.get_video_infos(url)
    
    if not videos:
        print("[-] 找不到影片。")
        return

    final_dir = custom_dir if custom_dir else dl.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] 儲存至目錄: {os.path.abspath(final_dir)}")
    tasks = [dl.process_video(v, final_dir) for v in videos]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())