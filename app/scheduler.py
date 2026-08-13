import logging
import asyncio
import os
import random
from datetime import datetime, timezone, timedelta
from app.scraper import fetch_5_articles, extract_article_content, fetch_1_article
from app.ai import analyze_articles_batch
from app.image_generator import generate_graphic
from app.publisher import upload_image_to_facebook, publish_to_instagram
from app.qa_agent import run_qa_checks, run_video_qa_checks
from app.config import APP_BASE_URL
from app.db import (
    is_url_processed, insert_article, update_article_ai_decision,
    mark_article_published, mark_article_publishing, reset_publishing_to_publish,
    get_unpublished_articles, update_article_qa_status, mark_article_qa_failed,
    get_pending_video, update_video_status, store_publishing_post_ids,
    get_pending_articles,
)
from app.video_scraper import download_video_and_metadata
from app.video_processor import apply_video_template
from app.publisher import publish_video_to_facebook, publish_video_to_instagram
from app.ai import generate_video_text

logger = logging.getLogger(__name__)

_pipeline_lock = asyncio.Lock()
_video_pipeline_lock = asyncio.Lock()

# ── Random self-rescheduling scheduler ────────────────────────────────────────
_app_scheduler = None       # set by main.py via set_scheduler()
_daily_post_count = 0       # how many pipeline runs completed today (UTC date)
_daily_post_date  = None    # the UTC date the counter belongs to (datetime.date)

DAILY_POST_CAP = 4          # never post more than this many times in one UTC day
_PIPELINE_JOB_ID = "run_pipeline_dynamic"


def set_scheduler(scheduler) -> None:
    """Store a reference to the APScheduler instance so run_pipeline can reschedule itself."""
    global _app_scheduler
    _app_scheduler = scheduler


def _next_run_delay_minutes() -> int:
    """Return a random delay (minutes) weighted by Bangladesh peak hours.

    Bangladesh Standard Time = UTC+6.
    Peak (6–11 PM BST = 12–17 UTC):   60–150 min  — post more often
    Eve wind-down (11 PM–2 AM BST = 17–20 UTC): 120–240 min
    Morning/Afternoon (7 AM–6 PM BST = 01–12 UTC): 150–300 min
    Overnight (2–7 AM BST = 20–01 UTC): 300–420 min  — post rarely
    """
    hour = datetime.now(timezone.utc).hour  # 0–23 UTC
    if 12 <= hour < 17:
        return random.randint(60, 150)
    elif 17 <= hour < 20:
        return random.randint(120, 240)
    elif 1 <= hour < 12:
        return random.randint(150, 300)
    else:
        # 20–23 and 0 (overnight in Bangladesh)
        return random.randint(240, 360)


def schedule_next_pipeline_run(scheduler=None) -> None:
    """Add a one-shot date-trigger job for the next pipeline run.

    Removes any previously scheduled job with the same ID first so there is
    never more than one pending pipeline trigger at a time.
    """
    sch = scheduler or _app_scheduler
    if sch is None or not sch.running:
        logger.warning("schedule_next_pipeline_run: scheduler not available, cannot reschedule.")
        return

    delay = _next_run_delay_minutes()
    next_run = datetime.now(timezone.utc) + timedelta(minutes=delay)

    # Replace any existing dynamic job (idempotent)
    sch.add_job(
        run_pipeline,
        "date",
        run_date=next_run,
        id=_PIPELINE_JOB_ID,
        replace_existing=True,
    )
    logger.info(
        f"Next pipeline run in {delay} min "
        f"(at {next_run.strftime('%Y-%m-%d %H:%M UTC')})"
    )


def _build_image_url(article_id: int) -> str:
    return f"{APP_BASE_URL}/static/images/article_{article_id}.jpg"


async def _publish_to_all(article_id: int, image_path: str, caption: str) -> tuple[str | None, str | None]:
    """
    Publish to Facebook and Instagram.
    Returns (ig_post_id, fb_post_id) — either may be None if that platform failed.
    At least one will be non-None on overall success.
    """
    image_url = _build_image_url(article_id)

    fb_post_id = await upload_image_to_facebook(image_path, caption)
    await asyncio.sleep(3)

    ig_post_id = None
    if APP_BASE_URL:
        ig_post_id = await publish_to_instagram(image_url, caption)
    else:
        logger.warning("APP_BASE_URL not set — skipping Instagram (needs public URL).")

    return ig_post_id, fb_post_id


async def _run_qa_and_publish(article_id: int, image_path: str, caption: str,
                               source_text: str, headline: str, hashtags: str,
                               article_url: str, decision: dict) -> bool:
    qa_result = await run_qa_checks(
        article_id=article_id,
        image_path=image_path,
        caption=caption,
        source_text=source_text,
        headline=headline,
        hashtags=hashtags,
        article_url=article_url,
        decision=decision,
    )

    fixes_str = "; ".join(qa_result["fixes_applied"]) if qa_result["fixes_applied"] else ""
    failures_str = "; ".join(qa_result["failures"]) if qa_result["failures"] else ""
    qa_notes = ""
    if fixes_str:
        qa_notes += f"Fixes: {fixes_str}"
    if failures_str:
        qa_notes += (" | " if qa_notes else "") + f"Failures: {failures_str}"

    if not qa_result["passed"]:
        logger.error(f"QA BLOCKED article {article_id}: {failures_str}")
        await mark_article_qa_failed(article_id, qa_notes[:1000])
        return False

    await update_article_qa_status(article_id, qa_result["status"], qa_notes[:1000])

    claimed = await mark_article_publishing(article_id)
    if not claimed:
        logger.warning(f"Article {article_id} already claimed by another run — skipping to prevent duplicate post.")
        return False

    verified_image_path = qa_result["image_path"]
    verified_caption = qa_result["caption"]

    logger.info(f"QA {qa_result['status']} for article {article_id}. Proceeding to publish.")
    try:
        ig_post_id, fb_post_id = await _publish_to_all(article_id, verified_image_path, verified_caption)
    except Exception as e:
        logger.error(f"Publish error for article {article_id}: {e}")
        await reset_publishing_to_publish(article_id)
        return False

    if ig_post_id or fb_post_id:
        # ── Step 1: Persist post IDs immediately while still PUBLISHING ───────
        # Writing the IDs before the status flip is intentional: if the process
        # crashes between the Meta API return and the PUBLISHED transition, the
        # IDs will already be in the DB so startup verification can confirm the
        # post exists instead of blindly re-queuing.
        #
        # Retry with backoff — a transient DB hiccup must not leave the article
        # in an unverifiable state (no IDs + PUBLISHING = potential duplicate on
        # next restart).
        id_stored = False
        for attempt in range(1, 4):   # up to 3 attempts
            try:
                await store_publishing_post_ids(article_id, ig_post_id, fb_post_id)
                id_stored = True
                break
            except Exception as e:
                wait = 2 ** attempt   # 2 s, 4 s, 8 s
                logger.warning(
                    f"Article {article_id}: ID storage attempt {attempt} failed ({e}). "
                    f"Retrying in {wait}s…"
                )
                await asyncio.sleep(wait)

        if not id_stored:
            # All retries exhausted — the post is live but IDs couldn't be stored.
            # Leave in PUBLISHING (NOT reverted) so startup verification treats this
            # as "unknown" and does NOT requeue, avoiding a duplicate post.
            logger.error(
                f"Article {article_id}: CRITICAL — published on Meta but could not store "
                f"post IDs after 3 attempts. ig={ig_post_id} fb={fb_post_id}. "
                f"Left in PUBLISHING for manual review; will not be requeued automatically."
            )
            return True  # post did go live; caller should not reset to PUBLISH

        # ── Step 2: Flip status to PUBLISHED ─────────────────────────────────
        try:
            await mark_article_published(article_id, ig_post_id, fb_post_id)
            logger.info(
                f"Article {article_id} marked as PUBLISHED "
                f"(ig={ig_post_id} fb={fb_post_id})."
            )
        except Exception as e:
            logger.error(
                f"Article {article_id} posted and IDs stored, but final status flip failed: {e}. "
                f"Leaving in PUBLISHING — startup verification will confirm and recover."
            )
    else:
        await reset_publishing_to_publish(article_id)

    return bool(ig_post_id or fb_post_id)


async def run_pipeline():
    global _daily_post_count, _daily_post_date

    if _pipeline_lock.locked():
        logger.warning("Pipeline already running — skipping this trigger to prevent duplicate posts.")
        return

    async with _pipeline_lock:
        # ── Daily cap check ──────────────────────────────────────────────────
        today = datetime.now(timezone.utc).date()
        if _daily_post_date != today:
            _daily_post_count = 0
            _daily_post_date = today

        if _daily_post_count >= DAILY_POST_CAP:
            # Cap reached — skip today, schedule first run of tomorrow's peak window
            # (6 PM BST = 12:00 UTC)
            tomorrow = today + timedelta(days=1)
            next_run = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 0,
                                tzinfo=timezone.utc)
            if _app_scheduler and _app_scheduler.running:
                _app_scheduler.add_job(
                    run_pipeline, "date", run_date=next_run,
                    id=_PIPELINE_JOB_ID, replace_existing=True,
                )
            logger.info(
                f"Daily post cap reached ({_daily_post_count}/{DAILY_POST_CAP}). "
                f"Next run tomorrow at {next_run.strftime('%Y-%m-%d %H:%M UTC')}."
            )
            return

        _daily_post_count += 1
        logger.info(f"Pipeline run {_daily_post_count}/{DAILY_POST_CAP} today.")

        try:
            await _run_pipeline_inner()
        finally:
            # Always reschedule — even if the inner pipeline raised an exception
            schedule_next_pipeline_run()


async def _run_pipeline_inner():
    logger.info("Starting Lens Today Pipeline execution...")

    articles_meta = await fetch_5_articles()
    pending_articles_db = await get_pending_articles(limit=5)

    # ── Phase 1: Process any new articles from feeds ──────────────────────────
    # We always fall through to Phase 2 (retry loop) regardless of whether
    # new articles were found, so stuck PUBLISH articles are never abandoned.
    articles_with_content = []

    # First, try to process articles that are stuck in PENDING status in the DB
    for meta in pending_articles_db:
        url = meta['url']
        title = meta['title']
        article_id = meta['article_id']
        logger.info(f"Resuming pending article from DB: {title}")

        result = await asyncio.to_thread(extract_article_content, url)
        if not result or not result.get("content"):
            logger.warning(f"Could not extract content for {url}. Dropping pending article.")
            await update_article_ai_decision(
                article_id, "DROP", "none", 0, "", "", "", "", "", "", None
            )
            continue

        meta['content'] = result['content']
        if 'article_image_url' in result:
            meta['article_image_url'] = result['article_image_url']
        if 'article_images' in result:
            meta['article_images'] = result['article_images']
        articles_with_content.append(meta)

    # Then process newly scraped articles up to a total of 5
    for meta in articles_meta:
        if len(articles_with_content) >= 5:
            break

        url = meta['url']
        title = meta['title']
        source = meta['source']

        if await is_url_processed(url):
            continue

        logger.info(f"Processing new article: {title}")
        article_id = await insert_article(source, url, title)

        result = await asyncio.to_thread(extract_article_content, url)
        if not result or not result.get("content"):
            rss_summary = meta.get("rss_summary", "")
            if rss_summary:
                logger.info(f"Full article blocked for {url} — using RSS summary as fallback.")
                result = {"content": rss_summary}
            else:
                logger.warning(f"Could not extract content for {url}. Dropping.")
                await update_article_ai_decision(
                    article_id, "DROP", "none", 0, "", "", "", "", "", "", None
                )
                continue

        meta['content'] = result['content']
        if 'article_image_url' in result:
            meta['article_image_url'] = result['article_image_url']
        if 'article_images' in result:
            meta['article_images'] = result['article_images']
        meta['article_id'] = article_id
        articles_with_content.append(meta)

    if articles_with_content:
        ai_decisions = await analyze_articles_batch(articles_with_content)

        if not ai_decisions:
            logger.error("AI returned no decisions for new articles.")
        else:
            for idx, article in enumerate(articles_with_content):
                article_id = article['article_id']

                decision = None
                for d in ai_decisions:
                    if d.get('slot') == idx + 1:
                        decision = d
                        break

                if not decision and idx < len(ai_decisions):
                    decision = ai_decisions[idx]

                if decision:
                    status = "PUBLISH"
                    category = decision.get("category", "Core")
                    try:
                        slot = int(decision.get("slot", idx + 1))
                    except (ValueError, TypeError):
                        slot = idx + 1
                    headline = decision.get("headline", "")
                    source_text = decision.get("source_text", "")
                    search_query = decision.get("search_query", "")
                    image_prompt = decision.get("image_prompt", "")
                    social_caption = decision.get("social_media_caption", "")
                    engagement_question = decision.get("engagement_question", "")

                    raw_hashtags = decision.get("hashtags", [])
                    if isinstance(raw_hashtags, list):
                        hashtags = " ".join([h if h.startswith('#') else f"#{h}" for h in raw_hashtags])
                    else:
                        hashtags = str(raw_hashtags)

                    await update_article_ai_decision(
                        article_id=article_id,
                        status=status,
                        category=category,
                        slot=slot,
                        headline=headline,
                        source_text=source_text,
                        search_query=search_query,
                        social_media_caption=social_caption,
                        engagement_question=engagement_question,
                        hashtags=hashtags,
                        article_image_url=article.get('article_image_url'),
                        image_prompt=image_prompt
                    )

                    logger.info(f"AI Decision: PUBLISH. Category: {category}, Slot: {slot}")

                    if 'article_image_url' in article:
                        decision['article_image_url'] = article['article_image_url']
                    if 'article_images' in article:
                        decision['article_images'] = article['article_images']
                    if 'rss_image' in article:
                        decision['rss_image'] = article['rss_image']

                    image_path = await asyncio.to_thread(generate_graphic, article_id, decision)

                    article_url = article.get('url', '')
                    full_caption = f"{social_caption}\n\n{engagement_question}\n\n{hashtags}"
                    if article_url:
                        full_caption += f"\n\nSource Article: {article_url}"
                    full_caption = full_caption[:2000]

                    await _run_qa_and_publish(
                        article_id=article_id,
                        image_path=image_path,
                        caption=full_caption,
                        source_text=source_text,
                        headline=headline,
                        hashtags=hashtags,
                        article_url=article_url,
                        decision=decision,
                    )
                else:
                    logger.warning(f"No AI decision for article {article['url']}")
                    await update_article_ai_decision(
                        article_id, "DROP", "none", 0, "", "", "", "", "", "", None
                    )
    else:
        logger.info("No new articles with content — skipping AI phase, going straight to retry.")

    # ── Phase 2: Retry approved articles that weren't posted yet ─────────────
    stuck_articles = await get_unpublished_articles()
    if stuck_articles:
        logger.info(f"Found {len(stuck_articles)} approved article(s) pending publish. Retrying...")
        for article in stuck_articles:
            article_id = article['id']
            image_path = f"static/images/article_{article_id}.jpg"
            caption = article.get('social_media_caption', '')
            engagement_question = article.get('engagement_question', '')
            source_text = article.get('source_text', '')
            hashtags = article.get('hashtags', '')
            headline = article.get('headline', '')
            article_url = article.get('url', '')

            full_caption = f"{caption}\n\n{engagement_question}\n\n{hashtags}"
            if article_url:
                full_caption += f"\n\nSource Article: {article_url}"
            full_caption = full_caption[:2000]

            if not os.path.exists(image_path):
                logger.info(f"Image missing for article {article_id}, regenerating...")
                image_path = await asyncio.to_thread(generate_graphic, article_id, article)

            await _run_qa_and_publish(
                article_id=article_id,
                image_path=image_path,
                caption=full_caption,
                source_text=source_text,
                headline=headline,
                hashtags=hashtags,
                article_url=article_url,
                decision=article,
            )
            await asyncio.sleep(5)

    logger.info("Pipeline execution completed.")

async def run_instant_pipeline():
    """
    Scrapes exactly 1 article, processes it via DeepSeek, generates an image, 
    and publishes it immediately. Used for the Instant Post button.
    """
    logger.info("Starting INSTANT Pipeline execution...")
    try:
        articles_meta = await fetch_1_article()
        if not articles_meta:
            logger.info("No fresh articles found for Instant Post.")
            return

        meta = articles_meta[0]
        url = meta['url']
        title = meta['title']
        source = meta['source']

        if await is_url_processed(url):
            logger.info(f"Instant Post skipped: Article already processed: {title}")
            return

        logger.info(f"Instant Post processing new article: {title}")
        article_id = await insert_article(source, url, title)

        result = await asyncio.to_thread(extract_article_content, url)
        if not result or not result.get("content"):
            rss_summary = meta.get("rss_summary", "")
            if rss_summary:
                logger.info(f"Full article blocked for {url} — using RSS summary as fallback.")
                result = {"content": rss_summary}
            else:
                logger.warning(f"Could not extract content for {url}. Dropping.")
                await update_article_ai_decision(
                    article_id, "DROP", "none", 0, "", "", "", "", "", "", None
                )
                return

        meta['content'] = result['content']
        if 'article_image_url' in result:
            meta['article_image_url'] = result['article_image_url']
        if 'article_images' in result:
            meta['article_images'] = result['article_images']
        meta['article_id'] = article_id

        # Phase 2: Run AI decision
        from app.ai import analyze_articles_batch
        logger.info(f"Instant Post AI Evaluation for: {title}")
        ai_decisions = await analyze_articles_batch([meta])
        
        if not ai_decisions:
            logger.error(f"AI returned no decision for instant article {article_id}.")
            await update_article_ai_decision(
                article_id, "DROP", "none", 0, "", "", "", "", "", "DeepSeek-v4", None
            )
            return

        decision = ai_decisions[0]
        status_action = "PUBLISH"
        category = decision.get("category", "Core")
        headline = decision.get("headline", "")
        search_query = decision.get("search_query", "")
        image_prompt = decision.get("image_prompt", "")
        social_caption = decision.get("social_media_caption", "")
        engagement_question = decision.get("engagement_question", "")
        
        raw_hashtags = decision.get("hashtags", [])
        if isinstance(raw_hashtags, list):
            hashtags = " ".join([h if h.startswith('#') else f"#{h}" for h in raw_hashtags])
        else:
            hashtags = str(raw_hashtags)
        
        full_caption = f"{social_caption}\n\n{engagement_question}\n\n{hashtags}"
        if url:
            full_caption += f"\n\nSource Article: {url}"

        await update_article_ai_decision(
            article_id=article_id,
            status=status_action,
            category=category,
            slot=0,
            headline=headline,
            source_text=meta.get('source', ''),
            search_query=search_query,
            social_media_caption=social_caption,
            engagement_question=engagement_question,
            hashtags=hashtags,
            article_image_url=meta.get('article_image_url', ''),
            image_prompt=image_prompt
        )

        if status_action == "PUBLISH":
            logger.info(f"Instant Post Image Generation for: {title}")
            from app.image_generator import generate_graphic
            decision["article_image_url"] = meta.get("article_image_url")
            decision["article_images"] = meta.get("article_images")
            decision["rss_image"] = meta.get("rss_image")

            img_path = await asyncio.to_thread(generate_graphic, article_id, decision)
            if img_path:
                logger.info(f"Instant Post publishing to Facebook and Instagram for: {title}")
                await mark_article_publishing(article_id)
                ig_id, fb_id = await _publish_to_all(article_id, img_path, full_caption)
                if ig_id or fb_id:
                    await mark_article_published(article_id, ig_id, fb_id)
                    logger.info("Instant Post complete!")
                else:
                    from app.db import reset_publishing_to_publish
                    await reset_publishing_to_publish(article_id)
                    logger.error("Instant Post failed to publish.")
            else:
                logger.error("Instant Post image generation failed.")
                await mark_article_qa_failed(article_id, "Image generation failed.")
        else:
            logger.info(f"Instant Post AI decided to DROP article: {title}")

    except Exception as e:
        logger.error(f"Error during Instant Pipeline execution: {e}", exc_info=True)


async def run_video_pipeline():
    if _video_pipeline_lock.locked():
        logger.info("Video pipeline already running — skipping duplicate trigger.")
        return

    async with _video_pipeline_lock:
        await _run_video_pipeline_inner()


async def _run_video_pipeline_inner():
    while True:
        video_req = await get_pending_video()
        if not video_req:
            logger.info("No pending video requests to process.")
            break

        video_id = video_req['id']
        url = video_req['url']
        context = video_req.get('context', '')
        video_index = video_req.get('video_index', 1)

        logger.info(f"Starting video pipeline for request {video_id} (URL: {url}, Index: {video_index})")

        video_meta = await asyncio.to_thread(download_video_and_metadata, url, video_id, video_index)
        if not video_meta:
            logger.error(f"Failed to download video {video_id}")
            await update_video_status(video_id, 'FAILED')
            continue

        video_path = video_meta['video_path']
        original_caption = video_meta.get('description', '')

        logger.info(f"Generating AI text for video {video_id}...")
        ai_text = await generate_video_text(original_caption, context)
        if not ai_text:
            logger.error(f"Failed to generate AI text for video {video_id}")
            await update_video_status(video_id, 'FAILED')
            continue

        short_title = ai_text.get('short_title', 'Viral Video')
        caption = ai_text.get('social_media_caption', '')
        hashtags = ai_text.get('hashtags', '')

        if isinstance(hashtags, list):
            hashtags = " ".join([h if h.startswith('#') else f"#{h}" for h in hashtags])

        full_caption = f"{caption}\n\n{hashtags}"

        uploader = video_meta.get('uploader', 'Unknown')
        platform_tag = ""
        url_lower = url.lower()
        if "facebook.com" in url_lower or "fb.watch" in url_lower:
            platform_tag = " @FB"
        elif "instagram.com" in url_lower:
            platform_tag = " @IG"
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            platform_tag = " @YT"
            
        full_caption += f"\n\nSource: {uploader}{platform_tag}"

        logger.info(f"Applying video template for video {video_id}...")
        template_path = "static/video_template.png"
        credit_text = f"Via {uploader}{platform_tag}"
        final_video = await asyncio.to_thread(apply_video_template, video_path, template_path, short_title, credit_text, video_id)

        if not final_video:
            logger.error(f"Failed to apply video template for video {video_id}")
            await update_video_status(video_id, 'FAILED')
            continue

        # --- Video QA Agent Check ---
        qa_result = await run_video_qa_checks(video_id, final_video, full_caption)
        if not qa_result["passed"]:
            logger.error(f"Video {video_id} failed QA checks: {qa_result['failures']}. Halting publish.")
            await update_video_status(video_id, 'FAILED')
            continue

        logger.info(f"Publishing video {video_id} to Facebook...")
        fb_success = await publish_video_to_facebook(final_video, full_caption, short_title)

        ig_success = False
        if APP_BASE_URL:
            video_url = f"{APP_BASE_URL}/static/videos/final_{video_id}.mp4"
            logger.info(f"Publishing video {video_id} to Instagram Reels...")
            await asyncio.sleep(3)
            ig_success = await publish_video_to_instagram(video_url, full_caption)
        else:
            logger.warning("APP_BASE_URL not set — skipping Instagram video (needs public URL).")

        if fb_success or ig_success:
            logger.info(f"Successfully published video {video_id}!")
            await update_video_status(video_id, 'PUBLISHED')
        else:
            logger.error(f"Failed to publish video {video_id}")
            await update_video_status(video_id, 'FAILED')
