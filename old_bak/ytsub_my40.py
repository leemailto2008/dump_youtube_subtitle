import asyncio
import sys
import os
import re
import shutil
import subprocess
import aiofiles
from typing import List, Optional, Tuple
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeSubExporter:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def sanitize(text: str) -> str:
        """清理非法字元，確保資料夾與檔名安全."""
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        """解析 URL 獲取影片列表."""
        video_infos = []
        folder_name = "downloads"
        
        p_id_match = re.search(r"list=([^&]+)", url)
        c_match = re.search(r"(?:channel/|c/|/(@[a-zA-Z0-9_-]+))", url)
        v_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)

        try:
            if p_id_match:
                p_id = p_id_match.group(1)
                print(f"[*] 解析播放清單: {p_id}")
                videos = scrapetube.get_playlist(p_id)
                folder_name = f"Playlist_{p_id}"
                for v in videos:
                    v_id = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif c_match and not v_id_match:
                handle = c_match.group(1) if c_match.group(1) else "Channel"
                print(f"[*] 解析頻道: {handle}")
                folder_name = f"Channel_{handle}"
                videos = scrapetube.get_channel(channel_url=url)
                for v in videos:
                    v_id = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif v_id_match:
                v_id = v_id_match.group(1)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        except Exception as e:
            print(f"[-] 列表獲取失敗: {e}")
            
        return video_infos, folder_name

    async def fetch_transcript_api(self, video_id: str) -> Optional[str]:
        """核心：適配特殊物件結構並支援翻譯."""
        try:
            api = YouTubeTranscriptApi()
            
            # 優先獲取可用字幕列表以便選擇或翻譯
            try:
                ts_list = await asyncio.to_thread(api.list_transcripts, video_id)
                # 嘗試順序：繁中 -> 英文(翻繁中) -> 抓第一個(翻繁中)
                try:
                    ts = ts_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK'])
                except:
                    try:
                        ts = ts_list.find_transcript(['en', 'zh-Hans']).translate('zh-Hant')
                    except:
                        ts = next(iter(ts_list)).translate('zh-Hant')
                data = await asyncio.to_thread(ts.fetch)
            except:
                # 若 list_transcripts 失敗，退回到你測試成功的直接 fetch
                data = await asyncio.to_thread(api.fetch, video_id)

            # 解析邏輯
            lines = []
            
            # A. 處理你環境中特定的 Snippet 物件結構
            if hasattr(data, 'snippets'):
                for s in data.snippets:
                    start = getattr(s, 'start', 0)
                    text = getattr(s, 'text', '').replace('\n', ' ').strip()
                    if text:
                        h, m = divmod(int(start), 3600)
                        m, s_val = divmod(m, 60)
                        lines.append(f"[{h:02d}:{m:02d}:{s_val:02d}] {text}")
                return "\n".join(lines)
            
            # B. 處理標準 list[dict] 結構
            if isinstance(data, list):
                for e in data:
                    start = e['start']
                    text = e['text'].replace('\n', ' ').strip()
                    if text:
                        h, m = divmod(int(start), 3600)
                        m, s_val = divmod(m, 60)
                        lines.append(f"[{h:02d}:{m:02d}:{s_val:02d}] {text}")
                return "\n".join(lines)
                
            return None
        except Exception:
            return None

    async def fetch_video_title(self, video_id: str) -> str:
        """穩定獲取真實標題."""
        try:
            res = await asyncio.to_thread(subprocess.run, 
                [sys.executable, '-m', 'yt_dlp', '--get-title', f"https://youtu.be/{video_id}"],
                capture_output=True, text=True, encoding='utf-8', errors='ignore', check=False)
            if res.returncode == 0:
                return res.stdout.strip()
        except: pass
        return f"Video_{video_id}"

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            # 修正標題
            if info.title.startswith("Video_") or info.title == "Unknown":
                info.title = await self.fetch_video_title(info.video_id)
            
            print(f"[*] 處理中: {info.title}")
            transcript = await self.fetch_transcript_api(info.video_id)
            
            if not transcript:
                print(f"[-] 無法獲取字幕: {info.title}")
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
                print(f"[-] 寫入失敗: {e}")

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_final.py \"<URL>\" [自訂資料夾]")
        return

    input_url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    exporter = YouTubeSubExporter(max_concurrent=5)
    video_list, suggested_dir = exporter.get_video_infos(input_url)
    
    if not video_list:
        print("[-] 找不到可處理的影片。")
        return

    final_dir = user_path if user_path else exporter.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 共 {len(video_list)} 部影片")

    tasks = [exporter.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())