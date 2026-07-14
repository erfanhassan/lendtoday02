import asyncio
import httpx
import json
import logging
import os
from app.config import META_ACCESS_TOKEN, META_PAGE_ID, META_IG_ACCOUNT_ID

logger = logging.getLogger(__name__)


async def upload_image_to_facebook(image_path: str, caption: str) -> str | None:
    """
    Publish a photo to the Facebook Page's public timeline.

    Two-step approach:
      1. Upload the photo as unpublished — gets a photo ID.
      2. Post to /feed with that photo attached — creates a proper
         public timeline post (visible to all followers and the public).

    The old single-step /photos approach posted to the Photos album only,
    making posts invisible outside the page/admin accounts.

    Returns the Facebook post ID on success, None on failure.
    """
    if not META_ACCESS_TOKEN or not META_PAGE_ID:
        logger.warning("Missing Meta credentials. Skipping Facebook publish.")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:

            # ── Step 1: Upload photo without publishing ───────────────────────
            photo_url = f"https://graph.facebook.com/v19.0/{META_PAGE_ID}/photos"
            with open(image_path, 'rb') as img:
                files = {'source': (os.path.basename(image_path), img, 'image/jpeg')}
                upload_data = {
                    'published': 'false',
                    'access_token': META_ACCESS_TOKEN,
                }
                upload_resp = await client.post(photo_url, data=upload_data, files=files)

            if upload_resp.status_code != 200:
                logger.error(f"Facebook photo upload failed: {upload_resp.text}")
                return None

            photo_id = upload_resp.json().get('id')
            if not photo_id:
                logger.error("Facebook photo upload returned no ID.")
                return None

            logger.info(f"Facebook photo uploaded (unpublished): {photo_id}")

            # ── Step 2: Post to timeline feed with photo attached ─────────────
            feed_url = f"https://graph.facebook.com/v19.0/{META_PAGE_ID}/feed"
            feed_data = {
                'message': caption,
                'attached_media': json.dumps([{"media_fbid": photo_id}]),
                'access_token': META_ACCESS_TOKEN,
            }
            feed_resp = await client.post(feed_url, data=feed_data)

            if feed_resp.status_code == 200:
                post_id = feed_resp.json().get('id')
                logger.info(f"Successfully posted to Facebook timeline: {post_id}")
                return post_id
            else:
                logger.error(f"Facebook feed post failed: {feed_resp.text}")
                return None

    except httpx.TimeoutException:
        logger.error("Facebook post timed out after 60s. Will retry next run.")
        return None
    except Exception as e:
        logger.error(f"Exception during Facebook post: {e}")
        return None


async def publish_to_instagram(image_url: str, caption: str) -> str | None:
    """
    Publish to Instagram via Graph API.
    Instagram requires the image to be at a publicly reachable URL —
    APP_BASE_URL must point to the production domain, not localhost/dev.

    Returns the Instagram post ID on success, None on failure.
    """
    if not META_ACCESS_TOKEN or not META_IG_ACCOUNT_ID:
        logger.warning("Missing Meta credentials. Skipping Instagram publish.")
        return None

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:

            # ── Step 1: Create media container ───────────────────────────────
            container_url = f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT_ID}/media"
            container_data = {
                'image_url': image_url,
                'caption': caption,
                'access_token': META_ACCESS_TOKEN,
            }
            c_resp = await client.post(container_url, data=container_data)
            if c_resp.status_code != 200:
                logger.error(f"IG media container creation failed: {c_resp.text}")
                return None

            creation_id = c_resp.json().get('id')
            if not creation_id:
                logger.error("IG media container returned no ID.")
                return None

            logger.info(f"IG media container created: {creation_id}")

            # ── Step 2: Poll until container is ready (up to 60 s) ───────────
            # Instagram processes the image asynchronously. We must wait until
            # status == FINISHED before calling media_publish.
            status_url = f"https://graph.facebook.com/v19.0/{creation_id}"
            for attempt in range(12):          # 12 × 5 s = 60 s max
                await asyncio.sleep(5)
                st_resp = await client.get(
                    status_url,
                    params={
                        'fields': 'status_code',
                        'access_token': META_ACCESS_TOKEN,
                    }
                )
                if st_resp.status_code != 200:
                    logger.warning(f"IG status check failed (attempt {attempt+1}): {st_resp.text}")
                    continue
                status_code = st_resp.json().get('status_code', '')
                logger.info(f"IG container status (attempt {attempt+1}): {status_code}")
                if status_code == 'FINISHED':
                    break
                if status_code == 'ERROR':
                    logger.error("IG container entered ERROR state — image URL may be unreachable.")
                    return None
            else:
                logger.error("IG container never reached FINISHED state after 60 s.")
                return None

            # ── Step 3: Publish ───────────────────────────────────────────────
            publish_url = f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT_ID}/media_publish"
            p_resp = await client.post(
                publish_url,
                data={'creation_id': creation_id, 'access_token': META_ACCESS_TOKEN},
            )
            if p_resp.status_code == 200:
                ig_post_id = p_resp.json().get('id')
                logger.info(f"Successfully posted to Instagram: {ig_post_id}")
                return ig_post_id
            else:
                logger.error(f"IG media_publish failed: {p_resp.text}")
                return None

    except httpx.TimeoutException:
        logger.error("Instagram post timed out after 90s. Will retry next run.")
        return None
    except Exception as e:
        logger.error(f"Exception during Instagram post: {e}")
        return None


async def verify_meta_post_exists(post_id: str) -> str:
    """
    Check whether a Meta post ID (Instagram or Facebook) still exists via the Graph API.
    Used on startup to decide what to do with PUBLISHING articles.

    Returns one of three string literals — never a boolean — so callers can act on
    the distinction between a definitive "not found" and a transient error:

      "confirmed"  — post exists and is accessible; safe to mark PUBLISHED.
      "not_found"  — Meta explicitly returned 404 / "does not exist"; safe to requeue.
      "unknown"    — network error, timeout, or any other API error; do NOT requeue —
                     leave in PUBLISHING and retry verification on the next restart.
    """
    if not META_ACCESS_TOKEN or not post_id:
        return "unknown"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://graph.facebook.com/v19.0/{post_id}",
                params={'fields': 'id', 'access_token': META_ACCESS_TOKEN},
            )
            if resp.status_code == 200 and resp.json().get('id'):
                return "confirmed"
            if resp.status_code == 404:
                return "not_found"
            # 400 with "does not exist" style error also means not found
            body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {}
            error_code = body.get('error', {}).get('code')
            if resp.status_code == 400 and error_code in (100, 803):
                # 100 = Invalid parameter / object not found; 803 = Some object at URL not found
                return "not_found"
            logger.warning(f"Unexpected Meta API response for post {post_id}: {resp.status_code} {resp.text[:200]}")
            return "unknown"
    except httpx.TimeoutException:
        logger.warning(f"Timeout verifying Meta post {post_id} — treating as unknown.")
        return "unknown"
    except Exception as e:
        logger.warning(f"Could not verify Meta post {post_id}: {e}")
        return "unknown"

async def publish_video_to_facebook(video_path: str, caption: str, title: str) -> bool:
    """
    Publish a video to the Facebook Page's timeline.
    """
    if not META_ACCESS_TOKEN or not META_PAGE_ID:
        logger.warning("Missing Meta credentials. Skipping Facebook video publish.")
        return False

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            video_url = f"https://graph.facebook.com/v19.0/{META_PAGE_ID}/videos"
            with open(video_path, 'rb') as video_file:
                files = {'source': (os.path.basename(video_path), video_file, 'video/mp4')}
                upload_data = {
                    'description': caption,
                    'title': title,
                    'access_token': META_ACCESS_TOKEN,
                }
                upload_resp = await client.post(video_url, data=upload_data, files=files)

            if upload_resp.status_code == 200:
                logger.info(f"Successfully posted video to Facebook: {upload_resp.json().get('id')}")
                return True
            else:
                logger.error(f"Facebook video upload failed: {upload_resp.text}")
                return False
    except Exception as e:
        logger.error(f"Exception during Facebook video post: {e}")
        return False


async def publish_video_to_instagram(video_url: str, caption: str) -> bool:
    """
    Publish a Reel to Instagram via Graph API.
    video_url must be publicly accessible.
    """
    if not META_ACCESS_TOKEN or not META_IG_ACCOUNT_ID:
        logger.warning("Missing Meta credentials. Skipping Instagram video publish.")
        return False

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Step 1: Create media container for REELS
            container_url = f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT_ID}/media"
            container_data = {
                'video_url': video_url,
                'media_type': 'REELS',
                'caption': caption,
                'access_token': META_ACCESS_TOKEN,
            }
            c_resp = await client.post(container_url, data=container_data)
            if c_resp.status_code != 200:
                logger.error(f"IG video container creation failed: {c_resp.text}")
                return False

            creation_id = c_resp.json().get('id')
            logger.info(f"IG video container created: {creation_id}")

            # Step 2: Poll until ready (up to 3 minutes for video)
            status_url = f"https://graph.facebook.com/v19.0/{creation_id}"
            for attempt in range(36): # 36 * 5 = 180s
                await asyncio.sleep(5)
                st_resp = await client.get(
                    status_url,
                    params={'fields': 'status_code', 'access_token': META_ACCESS_TOKEN}
                )
                if st_resp.status_code == 200:
                    status_code = st_resp.json().get('status_code', '')
                    if status_code == 'FINISHED':
                        break
                    if status_code == 'ERROR':
                        logger.error("IG video container entered ERROR state.")
                        return False
            else:
                logger.error("IG video container timed out.")
                return False

            # Step 3: Publish
            publish_url = f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT_ID}/media_publish"
            p_resp = await client.post(
                publish_url,
                data={'creation_id': creation_id, 'access_token': META_ACCESS_TOKEN},
            )
            if p_resp.status_code == 200:
                logger.info(f"Successfully posted video to Instagram: {p_resp.json().get('id')}")
                return True
            else:
                logger.error(f"IG video media_publish failed: {p_resp.text}")
                return False
    except Exception as e:
        logger.error(f"Exception during Instagram video post: {e}")
        return False

