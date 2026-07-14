import os
import tempfile
import yt_dlp
import logging

logger = logging.getLogger(__name__)


def _get_ydl_opts(url: str, output_template: str) -> dict:
    """
    Build yt-dlp options tailored to the platform.
    For YouTube, injects browser cookies when YOUTUBE_COOKIES is set.
    """
    from app.config import YOUTUBE_COOKIES

    base_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
    }

    url_lower = url.lower()
    is_youtube = "youtube.com" in url_lower or "youtu.be" in url_lower

    if is_youtube:
        if YOUTUBE_COOKIES:
            # Write the Netscape cookie string to a temp file for yt-dlp
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False, prefix='yt_cookies_'
            )
            # Ensure the file starts with the Netscape header yt-dlp expects
            content = YOUTUBE_COOKIES.strip()
            if not content.startswith("# Netscape HTTP Cookie File"):
                content = "# Netscape HTTP Cookie File\n" + content
            tmp.write(content)
            tmp.flush()
            tmp.close()
            base_opts['cookiefile'] = tmp.name
            logger.info("YouTube download: using cookies from YOUTUBE_COOKIES secret.")
        else:
            logger.warning(
                "YouTube download attempted without YOUTUBE_COOKIES secret. "
                "Download will likely fail due to bot detection. "
                "Export cookies from a logged-in browser and add them as YOUTUBE_COOKIES secret."
            )

    return base_opts


def download_video_and_metadata(url: str, output_id: int, video_index: int = 1) -> dict | None:
    """
    Downloads a video from a given URL using yt-dlp and extracts metadata.
    Saves the video to static/videos/video_{output_id}.mp4.
    Returns a dict with 'video_path', 'uploader', and 'description' or None on failure.
    """
    output_dir = "static/videos"
    os.makedirs(output_dir, exist_ok=True)

    video_path_template = os.path.join(output_dir, f"video_{output_id}.%(ext)s")
    ydl_opts = _get_ydl_opts(url, video_path_template)
    ydl_opts['playlist_items'] = str(video_index)

    cookie_file = ydl_opts.get('cookiefile')
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Extracting info and downloading video for: {url}")
            info_dict = ydl.extract_info(url, download=True)

            uploader = info_dict.get('uploader') or info_dict.get('channel') or 'Unknown'
            description = info_dict.get('description') or info_dict.get('title') or ''

            # Find the actual downloaded file
            expected_path = os.path.join(output_dir, f"video_{output_id}.mp4")

            if not os.path.exists(expected_path):
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
                'description': description,
            }
    except Exception as e:
        logger.error(f"Failed to download video from {url}: {e}")
        return None
    finally:
        # Clean up temp cookie file
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
            except Exception:
                pass
