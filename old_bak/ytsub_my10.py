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
    def __init__(self, max_concurrent: int = 1):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        # 檢查是否存在 Cookies 檔案
        self.cookie_path = "youtube_cookies.txt" if os.path.exists("youtube_cookies.txt") else None

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
            print(f"[-] 元數據解析錯誤: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """終極抓取策略：支援 Cookies 與自動降級."""
        try:
            # 優先使用 list_transcripts 獲取對象
            # 若有 cookie_path 則傳入
            ts_list = await asyncio.to_thread(
                YouTubeTranscriptApi.list_transcripts, 
                video_id, 
                cookies=self.cookie_path
            )
            
            # 策略：繁中 -> 英文(翻繁中) -> 第一個可用(翻繁中)
            try:
                transcript = ts_list.find_transcript(['zh-TW', 'zh-Hant'])
            except:
                try:
                    transcript = ts_list.find_transcript(['en', 'en-US'])
                    if transcript.is_translatable:
                        transcript = transcript.translate('zh-TW')
                except:
                    transcript = next(iter(ts_list))
                    if transcript.is_translatable:
                        transcript = transcript.translate('zh-TW')

            data = await asyncio.to_thread(transcript.fetch)
            
        except Exception:
            # 暴力保底：直接獲取英文，不翻譯
            try:
                data = await asyncio.to_thread(
                    YouTubeTranscriptApi.get_transcript, 
                    video_id, 
                    languages=['en', 'en-US'],
                    cookies=self.cookie_path
                )
            except:
                return None

        if not data:
            return None

        return "\n".join([
            f"[{int(e['start']//3600):02d}:{int((e['start']%3600)//60):02d}:{int(e['start']%60):02d}] {e['text'].replace('\n', ' ')}"
            for e in data
        ])

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            # 延長延遲以降低觸發反爬蟲
            await asyncio.sleep(2)
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] 抓取失敗 (需 Cookies 或無字幕): {info.title}")
                return

            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                await f.write(content)
            print(f"[+] 匯出成功: {filename}")

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_my10.py \"<URL>\" [資料夾]")
        return

    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    # 針對頑固影片，並發設為 1 最穩
    dl = YouTubeTranscriptDownloader(max_concurrent=1)
    
    if dl.cookie_path:
        print(f"[*] 已檢測到 Cookies 檔案: {dl.cookie_path}")
    else:
        print("[!] 提醒：若抓取失敗，請在當前目錄放置 youtube_cookies.txt")

    video_list, suggested_dir = dl.get_video_infos(url)
    if not video_list: return

    final_dir = user_path if user_path else dl.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] 目標目錄: {os.path.abspath(final_dir)}")
    tasks = [dl.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())