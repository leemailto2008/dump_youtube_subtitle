import asyncio
import sys
import os
import re
import shutil
import aiofiles
import random
from typing import List, Optional, Tuple
from pydantic import BaseModel
import yt_dlp
import scrapetube

# 強制控制台 UTF-8 顯示
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeSubExporter:
    def __init__(self, max_concurrent: int = 1):
        # 針對 429 錯誤，並發必須設為 1
        self.semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def sanitize(text: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        video_infos = []
        folder_name = "downloads"
        p_id_match = re.search(r"list=([^&]+)", url)
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
            elif v_id_match:
                v_id = v_id_match.group(1)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        except Exception as e:
            print(f"[-] 列表解析失敗: {e}")
        return video_infos, folder_name

    async def fetch_transcript_lib(self, video_id: str) -> Tuple[Optional[str], str]:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        tmpdir = os.path.abspath(f".tmp_{video_id}")
        
        if os.path.exists(tmpdir): shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        
        # 增加偽裝，降低 429 風險
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'writesubtitles': True,
            'subtitleslangs': ['zh-Hant', 'zh-TW', 'zh-HK', 'en'],
            'outtmpl': os.path.join(tmpdir, 'sub'),
            'quiet': True,
            'no_warnings': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'sleep_interval': 5,      # yt-dlp 內建隨機等待
            'max_sleep_interval': 10,
        }

        real_title = f"Video_{video_id}"
        try:
            loop = asyncio.get_event_loop()
            # 在請求前加入隨機等待，避免規律性抓取
            wait_time = random.uniform(3, 7)
            await asyncio.sleep(wait_time)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(video_url, download=True))
                real_title = info.get('title', real_title)

            files = os.listdir(tmpdir)
            sub_file = None
            for lang in ['zh-Hant', 'zh-TW', 'zh-HK', 'zh-Hans', 'en']:
                pattern = f"sub.{lang}.vtt"
                if pattern in files:
                    sub_file = os.path.join(tmpdir, pattern)
                    break
            
            if not sub_file:
                for f in files:
                    if f.endswith('.vtt'):
                        sub_file = os.path.join(tmpdir, f)
                        break

            if sub_file:
                async with aiofiles.open(sub_file, "r", encoding="utf-8", errors="replace") as f:
                    content = await f.read()
                    return self.clean_vtt(content), real_title
                    
        except Exception:
            pass
        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        return None, real_title

    def clean_vtt(self, text: str) -> str:
        text = text.lstrip('\ufeff')
        text = re.sub(r'^WEBVTT.*?\n', '', text, flags=re.IGNORECASE | re.MULTILINE)
        blocks = re.split(r'\n\s*\n', text)
        lines = []
        for block in blocks:
            time_match = re.search(r'(\d{2}:\d{2}:\d{2})', block)
            if not time_match: continue
            ts = f"[{time_match.group(1)}]"
            content = re.sub(r'^\d{2}:.*?\n', '', block, flags=re.MULTILINE).strip()
            content = re.sub(r'<[^>]+>', '', content).replace('&nbsp;', ' ')
            clean_text = " ".join([l.strip() for l in content.splitlines() if l.strip()])
            if clean_text: lines.append((ts, clean_text))
        
        unique_lines = []
        last_text = ""
        for ts, txt in lines:
            if txt != last_text:
                unique_lines.append(f"{ts} {txt}")
                last_text = txt
        return "\n".join(unique_lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            transcript, real_title = await self.fetch_transcript_lib(info.video_id)
            
            if not transcript:
                print(f"[-] 失敗 (429 限制或無字幕): {info.video_id}")
                return

            filename = f"{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8", errors="replace") as f:
                    content = f"# {real_title}\n\nURL: https://youtu.be/{info.video_id}\n\n## Transcript\n\n{transcript}\n"
                    await f.write(content)
                display_title = real_title[:20].encode('utf-8', 'replace').decode('utf-8')
                print(f"[+] 成功: {filename} ({display_title}...)")
            except Exception as e:
                print(f"[-] 寫入失敗: {e}")

async def main():
    if len(sys.argv) < 2: return
    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    exporter = YouTubeSubExporter(max_concurrent=1) # 429 狀態下絕對要設為 1
    video_list, suggested_dir = exporter.get_video_infos(url)
    if not video_list: return

    final_dir = user_path if user_path else exporter.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 總影片數: {len(video_list)} (注意：已開啟防封鎖慢速模式)")
    
    tasks = [exporter.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())