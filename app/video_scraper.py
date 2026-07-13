import os
import yt_dlp
import logging

logger = logging.getLogger(__name__)

def download_video_and_metadata(url: str, output_id: int, video_index: int = 1) -> dict | None:
    """
    Downloads a video from a given URL using yt-dlp and extracts metadata.
    Saves the video to static/videos/video_{output_id}.mp4.
    Returns a dict with 'video_path', 'uploader', and 'description' or None on failure.
    """
    output_dir = "static/videos"
    os.makedirs(output_dir, exist_ok=True)
    
    video_path_template = os.path.join(output_dir, f"video_{output_id}.%(ext)s")
    
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': video_path_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'playlist_items': str(video_index)
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Extracting info and downloading video for: {url}")
            info_dict = ydl.extract_info(url, download=True)
            
            uploader = info_dict.get('uploader') or info_dict.get('channel') or 'Unknown'
            description = info_dict.get('description') or info_dict.get('title') or ''
            
            # Find the actual downloaded file path
            # yt-dlp might have replaced %(ext)s with mp4
            expected_path = os.path.join(output_dir, f"video_{output_id}.mp4")
            
            if not os.path.exists(expected_path):
                # Fallback if yt-dlp didn't merge to mp4 for some reason
                actual_ext = info_dict.get('ext', 'mp4')
                fallback_path = os.path.join(output_dir, f"video_{output_id}.{actual_ext}")
                if os.path.exists(fallback_path):
                    expected_path = fallback_path
                else:
                    logger.error(f"Downloaded video file not found at {expected_path}")
                    return None

            return {
                'video_path': expected_path,
                'uploader': uploader,
                'description': description
            }
    except Exception as e:
        logger.error(f"Failed to download video from {url}: {e}")
        return None
