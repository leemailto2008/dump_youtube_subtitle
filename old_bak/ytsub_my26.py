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
        """清理非法字元，確保資料夾與檔名安全."""
        return re.sub(r'[\\/*?:"<>|]', "", text).strip()

    def get_video_infos(self, url: str) -> Tuple[List[VideoInfo], str]:
        """解析 URL 獲取影片列表."""
        video_infos = []
        folder_name = "downloads"
        
        p_id_match = re.search(r"list=([^&]+)", url)
        v_id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)

        try:
            if p_id_match:
                p_id = p_id_match.group(1)
                print(f"[*] 檢測到播放清單: {p_id}")
                videos = scrapetube.get_playlist(p_id)
                folder_name = f"Playlist_{p_id}"
                for v in videos:
                    v_id = v.get('videoId')
                    title = v.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown')
                    video_infos.append(VideoInfo(title=title, video_id=v_id, url=f"https://youtu.be/{v_id}"))
            elif any(x in url for x in ["channel/", "c/", "/@"]):
                print(f"[*] 檢測到頻道/用戶網址")
                videos = scrapetube.get_channel(channel_url=url)
                folder_name = "Channel_Archive"
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

    def run_ytdlp(self, args: List[str]) -> subprocess.CompletedProcess:
        """安全執行 yt-dlp CLI."""
        # 優先使用 python -m yt_dlp 確保環境變數正確
        full_cmd = [sys.executable, "-m", "yt_dlp"] + args
        return subprocess.run(full_cmd, capture_output=True, text=True, encoding='utf-8', check=False)

    async def fetch_transcript_cli(self, video_id: str) -> Tuple[Optional[str], Optional[str]]:
        """透過 yt-dlp 下載字幕並清洗."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        tmpdir = os.path.abspath(f".tmp_{video_id}")
        
        if os.path.exists(tmpdir): shutil.rmtree(tmpdir)
        os.makedirs(tmpdir, exist_ok=True)
        
        real_title = f"Video_{video_id}"
        try:
            # 獲取正確標題
            res_info = await asyncio.to_thread(self.run_ytdlp, ["--get-title", url])
            if res_info.returncode == 0:
                real_title = res_info.stdout.strip()

            # 下載字幕
            # --output "%(id)s" 指定檔名為 ID 方便後續定位
            cmd = [
                "--write-subs", "--write-auto-subs",
                "--sub-langs", "zh-Hant,zh-TW,zh-HK,zh-Hans,en,.*",
                "--skip-download",
                "--output", os.path.join(tmpdir, "sub"),
                url
            ]
            await asyncio.to_thread(self.run_ytdlp, cmd)
            
            # 搜尋字幕檔 (優先序)
            files = os.listdir(tmpdir)
            sub_file = None
            priorities = ['.zh-Hant.', '.zh-TW.', '.zh-HK.', '.zh-Hans.', '.en.']
            
            for p in priorities:
                for f in files:
                    if p in f and (f.endswith('.vtt') or f.endswith('.srt')):
                        sub_file = os.path.join(tmpdir, f)
                        break
                if sub_file: break
            
            if not sub_file: # 沒找到優先語言就隨便抓一個
                for f in files:
                    if f.endswith('.vtt') or f.endswith('.srt'):
                        sub_file = os.path.join(tmpdir, f)
                        break

            if sub_file:
                async with aiofiles.open(sub_file, "r", encoding="utf-8") as f:
                    content = await f.read()
                    return self.clean_vtt(content), real_title
        except Exception as e:
            print(f"[-] CLI 處理失敗 {video_id}: {e}")
        finally:
            if os.path.exists(tmpdir):
                shutil.rmtree(tmpdir, ignore_errors=True)
                
        return None, real_title

    def clean_vtt(self, text: str) -> str:
        """精準清洗 VTT 標記與重複行."""
        text = text.lstrip('\ufeff')
        # 移除標頭與樣式
        text = re.sub(r'^WEBVTT.*?\n', '', text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r'STYLE\n.*?\n\n', '', text, flags=re.DOTALL)
        
        blocks = re.split(r'\n\s*\n', text)
        lines = []
        
        for block in blocks:
            # 提取時間戳
            time_match = re.search(r'(\d{2}:\d{2}:\d{2})[.,]\d{3}', block)
            if not time_match: continue
            
            ts = f"[{time_match.group(1)}]"
            
            # 清理內容
            content = re.sub(r'^\d{2}:.*?\n', '', block, flags=re.MULTILINE).strip() # 移除時間行
            content = re.sub(r'<.*?>', '', content) # 移除 HTML 標籤
            content = content.replace('&nbsp;', ' ')
            content = re.sub(r'align:.*|kind:.*|position:.*', '', content) # 移除 VTT 屬性
            
            # 合併多行內容為單行
            clean_text = " ".join([l.strip() for l in content.splitlines() if l.strip()])
            if clean_text:
                lines.append((ts, clean_text))
        
        # 強效去重 (針對 ASR 自動字幕)
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
            
            # 使用更準確的標題
            final_title = real_title if real_title else info.title
            
            if not transcript:
                print(f"[-] 失敗 (無字幕): {final_title}")
                return

            safe_title = self.sanitize(final_title)
            filename = f"{safe_title}_{info.video_id}.md"
            full_path = os.path.join(target_dir, filename)
            
            content = f"# {final_title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
            
            try:
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(content)
                print(f"[+] 匯出成功: {filename}")
            except Exception as e:
                print(f"[-] 寫入失敗 {filename}: {e}")

async def main():
    if len(sys.argv) < 2:
        print("用法: python ytsub_ag01.py \"<YouTube_URL>\" [指定資料夾]")
        return

    url = sys.argv[1]
    user_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    exporter = YouTubeSubExporter(max_concurrent=3)
    video_list, suggested_dir = exporter.get_video_infos(url)
    
    if not video_list:
        print("[-] 未發現可處理影片。")
        return

    final_dir = user_path if user_path else exporter.sanitize(suggested_dir)
    os.makedirs(final_dir, exist_ok=True)
    
    print(f"[*] 輸出目錄: {os.path.abspath(final_dir)}")
    print(f"[*] 影片總數: {len(video_list)}")

    try:
        tasks = [exporter.process_video(v, final_dir) for v in video_list]
        await asyncio.gather(*tasks)
    finally:
        # 清理殘留的臨時目錄
        for d in os.listdir('.'):
            if d.startswith('.tmp_'):
                shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    # Windows 非同步策略修正
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] 使用者中斷執行。")