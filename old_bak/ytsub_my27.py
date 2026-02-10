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

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

class YouTubeSubExporter:
    def __init__(self, max_concurrent: int = 3):
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
                videos = scrapetube.get_playlist(p_id)
                folder_name = f"Playlist_{p_id}"
                for v in videos:
                    v_id = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif v_id_match:
                v_id = v_id_match.group(1)
                return [VideoInfo(title=f"Video_{v_id}", video_id=v_id, url=f"https://youtu.be/{v_id}")], "Single_Video"
        except Exception: pass
        return video_infos, folder_name

    def run_ytdlp(self, args: List[str]) -> subprocess.CompletedProcess:
        """修正 Windows 編碼相容性."""
        full_cmd = [sys.executable, "-m", "yt_dlp"] + args
        # 在 Windows 上使用 capture_output 並手動解碼，處理 Unicode 衝突
        return subprocess.run(
            full_cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            errors='ignore', # 核心修正：忽略無法解碼的字元
            check=False
        )

    async def fetch_transcript_cli(self, video_id: str) -> Tuple[Optional[str], Optional[str]]:
        url = f"https://www.youtube.com/watch?v={video_id}"
        tmpdir = os.path.abspath(f".tmp_{video_id}")
        
        if os.path.exists(tmpdir): shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        
        real_title = f"Video_{video_id}"
        try:
            # 獲取標題
            res_info = await asyncio.to_thread(self.run_ytdlp, ["--get-title", url])
            if res_info.returncode == 0 and res_info.stdout:
                real_title = res_info.stdout.strip()

            # 下載字幕
            cmd = [
                "--write-subs", "--write-auto-subs",
                "--sub-langs", "zh-Hant,zh-TW,zh-HK,zh-Hans,en,.*",
                "--skip-download",
                "--output", os.path.join(tmpdir, "sub"),
                url
            ]
            await asyncio.to_thread(self.run_ytdlp, cmd)
            
            files = os.listdir(tmpdir)
            sub_file = None
            priorities = ['.zh-Hant.', '.zh-TW.', '.zh-HK.', '.zh-Hans.', '.en.']
            
            for p in priorities:
                for f in files:
                    if p in f and (f.endswith('.vtt') or f.endswith('.srt')):
                        sub_file = os.path.join(tmpdir, f)
                        break
                if sub_file: break
            
            if not sub_file:
                for f in files:
                    if f.endswith('.vtt') or f.endswith('.srt'):
                        sub_file = os.path.join(tmpdir, f)
                        break

            if sub_file:
                async with aiofiles.open(sub_file, "r", encoding="utf-8", errors="ignore") as f:
                    content = await f.read()
                    return self.clean_vtt(content), real_title
        except Exception as e:
            print(f"[-] CLI 錯誤 {video_id}: {e}")
        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
        return None, real_title

    def clean_vtt(self, text: str) -> str:
        text = text.lstrip('\ufeff')
        text = re.sub(r'^WEBVTT.*?\n', '', text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r'STYLE\n.*?\n\n', '', text, flags=re.DOTALL)
        
        blocks = re.split(r'\n\s*\n', text)
        lines = []
        for block in blocks:
            time_match = re.search(r'(\d{2}:\d{2}:\d{2})[.,]\d{3}', block)
            if not time_match: continue
            
            ts = f"[{time_match.group(1)}]"
            content = re.sub(r'^\d{2}:.*?\n', '', block, flags=re.MULTILINE).strip()
            content = re.sub(r'<.*?>', '', content)
            content = content.replace('&nbsp;', ' ')
            content = re.sub(r'align:.*|kind:.*|position:.*', '', content)
            
            clean_text = " ".join([l.strip() for l in content.splitlines() if l.strip()])
            if clean_text:
                lines.append((ts, clean_text))
        
        unique_lines = []
        last_text = ""
        for ts, txt in lines:
            if txt != last_text:
                unique_lines.append(f"{ts} {txt}")
                last_text = txt
        return "\n".join(unique_lines)

    async def process_video(self, info: VideoInfo, target_dir: str):
        async with self.semaphore:
            print(f"[*] 處理中: {info.title}")
            transcript, real_title = await self.fetch_transcript_cli(info.video_id)
            final_title = real_title if real_title else info.title
            
            if not transcript:
                print(f"[-] 失敗 (無字幕): {final_title}")
                return

            safe_title = self.sanitize(final_title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(f"# {final_title}\n\nURL: https://youtu.be/{info.video_id}\n\n## Transcript\n\n{transcript}\n")
                print(f"[+] 成功匯出: {filename}")
            except Exception as e:
                print(f"[-] 寫入失敗: {e}")

async def main():
    if len(sys.argv) < 2: return
    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    exporter = YouTubeSubExporter(max_concurrent=3)
    video_list, suggested_dir = exporter.get_video_infos(url)
    if not video_list: return

    final_dir = user_path if user_path else exporter.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    tasks = [exporter.process_video(v, final_dir) for v in video_list]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())