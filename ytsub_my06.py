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
            print(f"[-] 獲取清單失敗: {e}")
        return video_infos, folder_name

    async def fetch_transcript(self, video_id: str) -> Optional[str]:
        """
        進階字幕抓取邏輯：
        1. 獲取所有可用軌道。
        2. 遍歷軌道，尋找繁體中文。
        3. 若無，尋找英文或簡中並翻譯。
        4. 若皆無，抓第一個可用軌道強制翻譯。
        """
        try:
            # 獲取字幕清單
            transcript_list = await asyncio.to_thread(YouTubeTranscriptApi.list_transcripts, video_id)
            
            # 初始化目標字幕軌
            target_transcript = None

            # 策略 A: 尋找繁體中文 (Manual 或 Generated)
            try:
                target_transcript = transcript_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
            except NoTranscriptFound:
                # 策略 B: 尋找英文或簡體，並嘗試翻譯成繁體
                try:
                    target_transcript = transcript_list.find_transcript(['en', 'zh-Hans'])
                    if target_transcript.is_translatable:
                        target_transcript = target_transcript.translate('zh-TW')
                except NoTranscriptFound:
                    # 策略 C: 暴力破解，抓清單中第一個
                    try:
                        target_transcript = next(iter(transcript_list))
                        if target_transcript.is_translatable:
                            target_transcript = target_transcript.translate('zh-TW')
                    except Exception:
                        return None

            if not target_transcript:
                return None

            # 執行抓取
            data = await asyncio.to_thread(target_transcript.fetch)
            
            lines = []
            for entry in data:
                m, s = divmod(int(entry['start']), 60)
                h, m = divmod(m, 60)
                timestamp = f"[{h:02d}:{m:02d}:{s:02d}]"
                text = entry['text'].replace('\n', ' ')
                lines.append(f"{timestamp} {text}")
            
            return "\n".join(lines)

        except (TranscriptsDisabled, NoTranscriptFound):
            return None
        except Exception as e:
            # 處理如 Proxy 或封鎖問題
            return None

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            transcript = await self.fetch_transcript(info.video_id)
            
            if not transcript:
                print(f"[-] 無法取得字幕 (或此影片無字幕軌): {info.title}")
                return

            safe_title = self.sanitize(info.title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] 成功匯出: {filename}")
            except Exception as e:
                print(f"[-] 寫入錯誤 {info.video_id}: {e}")

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_my06.py \"<URL>\" [資料夾路徑]")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    dl = YouTubeTranscriptDownloader(max_concurrent=5)
    video_list, suggested_dir = dl.get_video_infos(input_url)
    
    if not video_list:
        print("[-] 找不到任何影片。")
        return

    # 決定存放資料夾
    final_dir = user_path if user_path else dl.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)

    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 預計處理: {len(video_list)} 部影片")

    tasks = [dl.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())