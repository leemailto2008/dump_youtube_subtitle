import asyncio
import sys
import os
import re
import shutil
import aiofiles
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
    def __init__(self, max_concurrent: int = 3):
        self.semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def sanitize(text: str) -> str:
        """清理非法字元，僅用於資料夾命名."""
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
        """使用 yt-dlp Library 模式，徹底解決編碼衝突."""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        tmpdir = os.path.abspath(f".tmp_{video_id}")
        
        if os.path.exists(tmpdir): shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        
        # yt-dlp 配置：直接使用其 Python 介面
        ydl_opts = {
            'skip_download': True,
            'writeautomaticsub': True,
            'writesubtitles': True,
            'subtitleslangs': ['zh-Hant', 'zh-TW', 'zh-HK', 'zh-Hans', 'en'],
            'outtmpl': os.path.join(tmpdir, 'sub'),
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }

        real_title = f"Video_{video_id}"
        try:
            loop = asyncio.get_event_loop()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # 提取資訊
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(video_url, download=True))
                real_title = info.get('title', real_title)

            # 尋找下載的字幕檔
            files = os.listdir(tmpdir)
            sub_file = None
            # 按語言優先級尋找
            for lang in ['zh-Hant', 'zh-TW', 'zh-HK', 'zh-Hans', 'en']:
                pattern = f"sub.{lang}.vtt"
                if pattern in files:
                    sub_file = os.path.join(tmpdir, pattern)
                    break
            
            if not sub_file:
                # 保底：找任何 .vtt 檔案
                for f in files:
                    if f.endswith('.vtt'):
                        sub_file = os.path.join(tmpdir, f)
                        break

            if sub_file:
                async with aiofiles.open(sub_file, "r", encoding="utf-8", errors="replace") as f:
                    content = await f.read()
                    return self.clean_vtt(content), real_title
                    
        except Exception as e:
            # print(f"[-] Debug {video_id}: {e}")
            pass
        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        return None, real_title

    def clean_vtt(self, text: str) -> str:
        """清洗 VTT 並去除 ASR 重複行."""
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
            # 使用 VideoID 作為內部標識，減少亂碼干擾
            transcript, real_title = await self.fetch_transcript_lib(info.video_id)
            
            if not transcript:
                print(f"[-] 失敗 (無字幕軌道): {info.video_id}")
                return

            # 強制使用 VideoID 做檔名，穩定性 100%
            filename = f"{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8", errors="replace") as f:
                    content = (
                        f"# {real_title}\n\n"
                        f"URL: https://youtu.be/{info.video_id}\n\n"
                        f"## Transcript\n\n{transcript}\n"
                    )
                    await f.write(content)
                # 輸出時進行編碼保護，避免控制台崩潰
                display_title = real_title[:20].encode('utf-8', 'replace').decode('utf-8')
                print(f"[+] 成功匯出: {filename} ({display_title}...)")
            except Exception as e:
                print(f"[-] 寫入失敗 {info.video_id}: {e}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub_my30.py \"URL\"")
        return
    
    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    exporter = YouTubeSubExporter(max_concurrent=3)
    video_list, suggested_dir = exporter.get_video_infos(url)
    if not video_list: return

    final_dir = user_path if user_path else exporter.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 總影片數: {len(video_list)}")
    
    tasks = [exporter.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass