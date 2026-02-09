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
    def __init__(self, max_concurrent: int = 2): # 降低並發，減少被 YouTube 阻擋機率
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
            print(f"[-] Meta Error: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """
        全方位字幕檢索策略：
        1. 直接獲取所有軌道清單。
        2. 針對「自動生成」軌道進行特化處理。
        3. 支援翻譯。
        """
        try:
            # 獲取字幕清單物件
            proxy = None # 若有需要可在此加入代理 {'http': '...', 'https': '...'}
            ts_list = await asyncio.to_thread(YouTubeTranscriptApi.list_transcripts, video_id, proxies=proxy)
            
            transcript = None

            # 步驟 1: 尋找人工或自動產生的繁體中文
            try:
                transcript = ts_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
            except NoTranscriptFound:
                # 步驟 2: 尋找任何「自動產生」的英語或簡中軌道
                # 許多教學影片的標籤是 'en' 但實質是 Generated
                try:
                    transcript = ts_list.find_generated_transcript(['en', 'en-US', 'zh-Hans'])
                    if transcript.is_translatable:
                        transcript = transcript.translate('zh-TW')
                except NoTranscriptFound:
                    # 步驟 3: 暴力枚舉，嘗試翻譯清單中任何第一個可用的軌道
                    try:
                        transcript = next(iter(ts_list))
                        if transcript.is_translatable:
                            transcript = transcript.translate('zh-TW')
                    except Exception:
                        pass

            if not transcript:
                return None

            data = await asyncio.to_thread(transcript.fetch)
            
            lines = []
            for e in data:
                m, s = divmod(int(e['start']), 60)
                h, m = divmod(m, 60)
                lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {e['text'].replace('\n', ' ')}")
            return "\n".join(lines)

        except Exception:
            # 針對 LFz_HIJ0MWs 這種頑固影片，最後嘗試直接獲取 en 原始碼
            try:
                data = await asyncio.to_thread(YouTubeTranscriptApi.get_transcript, video_id, languages=['en'])
                return "\n".join([f"[{int(e['start']//3600):02d}:{int((e['start']%3600)//60):02d}:{int(e['start']%60):02d}] {e['text']}" for e in data])
            except:
                return None

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            # 加入微小延遲避免被 YouTube 偵測
            await asyncio.sleep(0.5)
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] Failed: {info.title} (No Captions Found)")
                return

            filename = f"{self.sanitize(info.title)}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] Exported: {filename}")
            except Exception as e:
                print(f"[-] Write Error: {e}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_my09.py \"<URL>\" [Folder]")
        return

    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    dl = YouTubeTranscriptDownloader(max_concurrent=2)
    videos, suggested_dir = dl.get_video_infos(url)
    
    if not videos:
        print("[-] No videos found.")
        return

    final_dir = user_path if user_path else dl.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] Saving to: {os.path.abspath(final_dir)}")
    tasks = [dl.process_video(v, final_dir) for v in videos]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())