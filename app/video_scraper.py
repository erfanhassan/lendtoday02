import os
import tempfile
import yt_dlp
import logging
import time
import random

logger = logging.getLogger(__name__)

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0'
]

def _write_cookie_file(cookie_content: str, prefix: str) -> str:
    """Write a Netscape cookie string to a temp file and return the path."""
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', delete=False, prefix=prefix
    )
    content = cookie_content.strip()
    if not content.startswith("# Netscape HTTP Cookie File"):
        content = "# Netscape HTTP Cookie File\n" + content
    tmp.write(content)
    tmp.flush()
    tmp.close()
    return tmp.name


def _get_ydl_opts(url: str, output_template: str) -> dict:
    """
    Build yt-dlp options tailored to the platform.
    Injects browser cookies for YouTube, Facebook, and Instagram when available.
    """
    from app.config import YOUTUBE_COOKIES, FACEBOOK_COOKIES, INSTAGRAM_COOKIES

    ua = random.choice(USER_AGENTS)

    base_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'no_check_certificates': True,
        'prefer_insecure': True,
        'http_headers': {
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        }
    }

    url_lower = url.lower()
    is_youtube = "youtube.com" in url_lower or "youtu.be" in url_lower
    is_facebook = "facebook.com" in url_lower or "fb.watch" in url_lower
    is_instagram = "instagram.com" in url_lower

    if is_youtube:
        if YOUTUBE_COOKIES:
            base_opts['cookiefile'] = _write_cookie_file(YOUTUBE_COOKIES, 'yt_cookies_')
            logger.info("YouTube download: using cookies from YOUTUBE_COOKIES secret.")
        else:
            logger.warning(
                "YouTube download attempted without YOUTUBE_COOKIES secret. "
                "Download may fail due to bot detection. "
                "Export cookies from a logged-in browser and add them as YOUTUBE_COOKIES secret."
            )

    elif is_facebook:
        if FACEBOOK_COOKIES:
            base_opts['cookiefile'] = _write_cookie_file(FACEBOOK_COOKIES, 'fb_cookies_')
            logger.info("Facebook download: using cookies from FACEBOOK_COOKIES secret.")
        else:
            logger.warning(
                "Facebook download attempted without FACEBOOK_COOKIES secret. "
                "Facebook blocks unauthenticated server downloads. "
                "Export cookies from a logged-in browser and add them as FACEBOOK_COOKIES secret."
            )

    elif is_instagram:
        if INSTAGRAM_COOKIES:
            base_opts['cookiefile'] = _write_cookie_file(INSTAGRAM_COOKIES, 'ig_cookies_')
            logger.info("Instagram download: using cookies from INSTAGRAM_COOKIES secret.")
        else:
            logger.warning(
                "Instagram download attempted without INSTAGRAM_COOKIES secret. "
                "Instagram blocks unauthenticated server downloads. "
                "Export cookies from a logged-in browser and add them as INSTAGRAM_COOKIES secret."
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
    max_retries = 3
    backoff_factor = 2

    try:
        for attempt in range(max_retries):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    logger.info(f"Extracting info and downloading video for: {url} (Attempt {attempt + 1}/{max_retries})")
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
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e).lower()
                if "429" in error_msg or "403" in error_msg or "too many requests" in error_msg or "forbidden" in error_msg:
                    if attempt < max_retries - 1:
                        sleep_time = backoff_factor * (2 ** attempt)
                        logger.warning(f"Rate limited (429/403) from {url}. Retrying in {sleep_time}s... Error: {e}")
                        time.sleep(sleep_time)
                        # Rotate UA on retry
                        ydl_opts['http_headers']['User-Agent'] = random.choice(USER_AGENTS)
                    else:
                        logger.error(f"Hard block: Failed to download video from {url} after {max_retries} attempts: {e}")
                        return None
                else:
                    logger.error(f"Failed to download video from {url}: {e}")
                    return None
            except Exception as e:
                logger.error(f"Unexpected error downloading video from {url}: {e}")
                return None
    finally:
        # Clean up temp cookie file
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
            except Exception:
                pass
    
    return None
