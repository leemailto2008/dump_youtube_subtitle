# r:\transcript\ytsub.py
import asyncio
import sys
import os
import re
from typing import List, Optional
from pydantic import BaseModel
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi

class VideoInfo(BaseModel):
    title: str
    video_id: str
    url: str

def extract_id(url: str, param: str = "v") -> Optional[str]:
    """從 URL 中提取 ID (Extract ID from URL)."""
    match = re.search(rf"{param}=([^&]+)", url)
    if match:
        return match.group(1)
    if "list=" in url and param == "list":
        match = re.search(r"list=([^&]+)", url)
        return match.group(1)
    # 處理 short URL
    if "youtu.be/" in url:
        return url.split("/")[-1].split("?")[0]
    return url.split("/")[-1].split("?")[0]

def parse_video_data(video: dict) -> VideoInfo:
    """解析 scrapetube 回傳的影片資料 (Parse scrapetube video data)."""
    # print(f"DEBUG keys: {video.keys()}")
    title = "Unknown Title"
    try:
        title = video.get('title', {}).get('runs', [{}])[0].get('text', 'Unknown Title')
    except (IndexError, AttributeError):
        pass
    
    video_id = video.get('videoId')
    if not video_id:
        # 兼容性：某些版本可能是 videoId
        video_id = video.get('id')
        
    return VideoInfo(
        title=title,
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}"
    )

def get_video_infos(url: str) -> List[VideoInfo]:
    """使用 scrapetube 獲取影片資訊 (Fetch video metadata using scrapetube)."""
    video_infos = []
    
    if "list=" in url:
        playlist_id = extract_id(url, "list")
        print(f"Fetching Playlist: {playlist_id}")
        videos = scrapetube.get_playlist(playlist_id)
        for video in videos:
            video_infos.append(parse_video_data(video))
    elif "channel/" in url or "c/" in url or "/@" in url:
        print(f"Fetching Channel: {url}")
        videos = scrapetube.get_channel(channel_url=url)
        for video in videos:
            video_infos.append(parse_video_data(video))
    else:
        video_id = extract_id(url, "v")
        print(f"Fetching Single Video: {video_id}")
        try:
            video = scrapetube.get_video(video_id)
            video_infos.append(parse_video_data(video))
        except Exception as e:
            print(f"Fall-back to basic info for {video_id} due to scrapetube error: {e}")
            video_infos.append(VideoInfo(
                title=f"Video_{video_id}",
                video_id=video_id,
                url=url
            ))
            
    return video_infos

def format_timestamp(seconds: float) -> str:
    """將秒數轉換為 HH:MM:SS 格式 (Format seconds to HH:MM:SS)."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

async def get_transcript(video_id: str) -> Optional[str]:
    """獲取並格式化字幕內容 (Fetch and format transcript)."""
    try:
        api = YouTubeTranscriptApi()
        # 目前環境中 API method 為 list()
        transcript_list = api.list(video_id)
        
        try:
            # 優先搜尋中英文
            transcript = transcript_list.find_transcript(['zh-TW', 'zh-Hant', 'zh-HK', 'en'])
        except Exception:
            # 沒找到就抓第一個
            transcript = next(iter(transcript_list))
            
        data = transcript.fetch()
        
        output = []
        for entry in data:
            timestamp = format_timestamp(entry['start'])
            text = entry['text'].replace('\n', ' ')
            output.append(f"[{timestamp}] {text}")
        
        return "\n".join(output)
    except Exception as e:
        print(f"Error fetching transcript for {video_id}: {e}")
        return None

def sanitize_filename(filename: str) -> str:
    """清理檔名中的非法字元 (Sanitize filename)."""
    return re.sub(r'[\\/*?:"<>|]', "", filename)

async def process_video(info: VideoInfo):
    """處理單個影片的抓取與存檔 (Process single video)."""
    print(f"Processing: {info.title} ({info.video_id})")
    transcript = await get_transcript(info.video_id)
    
    if transcript:
        safe_title = sanitize_filename(info.title)
        filename = f"{safe_title}.md"
        content = f"# {info.title}\n\nURL: {info.url}\n\n## Transcript\n\n{transcript}\n"
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Successfully exported: {filename}")
    else:
        print(f"Failed to fetch transcript: {info.title}")

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ytsub.py <youtube_url>")
        sys.exit(1)
    
    url = sys.argv[1]
    print(f"Initializing for: {url}")
    
    try:
        video_infos = get_video_infos(url)
        print(f"Total videos to process: {len(video_infos)}")
        
        if not video_infos:
            print("No videos found.")
            return

        semaphore = asyncio.Semaphore(5)
        
        async def sem_process(info):
            async with semaphore:
                await process_video(info)
        
        tasks = [sem_process(info) for info in video_infos]
        await asyncio.gather(*tasks)
        
    except Exception as e:
        print(f"Fatal error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
