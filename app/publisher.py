import asyncio
import httpx
import json
import logging
import os
from app.config import META_ACCESS_TOKEN, META_PAGE_ID, META_IG_ACCOUNT_ID

logger = logging.getLogger(__name__)


async def upload_image_to_facebook(image_path: str, caption: str) -> bool:
    """
    Publish a photo to the Facebook Page's public timeline.

    Two-step approach:
      1. Upload the photo as unpublished — gets a photo ID.
      2. Post to /feed with that photo attached — creates a proper
         public timeline post (visible to all followers and the public).

    The old single-step /photos approach posted to the Photos album only,
    making posts invisible outside the page/admin accounts.
    """
    if not META_ACCESS_TOKEN or not META_PAGE_ID:
        logger.warning("Missing Meta credentials. Skipping Facebook publish.")
        return False

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
                return False

            photo_id = upload_resp.json().get('id')
            if not photo_id:
                logger.error("Facebook photo upload returned no ID.")
                return False

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
                return True
            else:
                logger.error(f"Facebook feed post failed: {feed_resp.text}")
                return False

    except httpx.TimeoutException:
        logger.error("Facebook post timed out after 60s. Will retry next run.")
        return False
    except Exception as e:
        logger.error(f"Exception during Facebook post: {e}")
        return False


async def publish_to_instagram(image_url: str, caption: str) -> bool:
    """
    Publish to Instagram via Graph API.
    Instagram requires the image to be at a publicly reachable URL —
    APP_BASE_URL must point to the production domain, not localhost/dev.
    """
    if not META_ACCESS_TOKEN or not META_IG_ACCOUNT_ID:
        logger.warning("Missing Meta credentials. Skipping Instagram publish.")
        return False

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
                return False

            creation_id = c_resp.json().get('id')
            if not creation_id:
                logger.error("IG media container returned no ID.")
                return False

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
                    return False
            else:
                logger.error("IG container never reached FINISHED state after 60 s.")
                return False

            # ── Step 3: Publish ───────────────────────────────────────────────
            publish_url = f"https://graph.facebook.com/v19.0/{META_IG_ACCOUNT_ID}/media_publish"
            p_resp = await client.post(
                publish_url,
                data={'creation_id': creation_id, 'access_token': META_ACCESS_TOKEN},
            )
            if p_resp.status_code == 200:
                ig_post_id = p_resp.json().get('id')
                logger.info(f"Successfully posted to Instagram: {ig_post_id}")
                return True
            else:
                logger.error(f"IG media_publish failed: {p_resp.text}")
                return False

    except httpx.TimeoutException:
        logger.error("Instagram post timed out after 90s. Will retry next run.")
        return False
    except Exception as e:
        logger.error(f"Exception during Instagram post: {e}")
        return False

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

