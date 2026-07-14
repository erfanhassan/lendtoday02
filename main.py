import secrets
import base64
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import (
    init_db, close_db, get_recent_articles,
    get_stale_publishing_articles,
    confirm_publishing_article_published,
    reset_publishing_article_to_publish,
)
from app.scheduler import run_pipeline
from app.config import POLL_INTERVAL_MINUTES, DASHBOARD_USERNAME, DASHBOARD_PASSWORD

POLL_INTERVAL_HOURS = round(POLL_INTERVAL_MINUTES / 60, 1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

_AUTH_ENABLED = bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth middleware — only active when credentials are configured."""

    async def dispatch(self, request: Request, call_next):
        if not _AUTH_ENABLED:
            return await call_next(request)

        # Allow static files and health-check endpoints through without auth
        if request.url.path.startswith("/static/") or request.url.path in ("/next-run", "/healthz"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, _, password = decoded.partition(":")
                user_ok = secrets.compare_digest(username, DASHBOARD_USERNAME)
                pass_ok = secrets.compare_digest(password, DASHBOARD_PASSWORD)
                if user_ok and pass_ok:
                    return await call_next(request)
            except Exception:
                pass

        return HTMLResponse(
            content="Unauthorized",
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="Lens Today"'},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Lens Today application...")

    db_ready = False
    try:
        await init_db()
        db_ready = True
    except Exception as e:
        logger.error(
            f"DATABASE UNAVAILABLE — server will start but pipeline is disabled: {e}"
        )

    if db_ready:
        # Verify articles stuck in PUBLISHING against the Meta API before deciding what to do.
        # If the post was actually sent, mark it PUBLISHED. If not, revert to PUBLISH for retry.
        stale = await get_stale_publishing_articles()
        if stale:
            from app.publisher import verify_meta_post_exists
            confirmed = 0
            reverted = 0
            left_unknown = 0
            for article in stale:
                article_id = article['id']
                ig_post_id = article.get('meta_post_id')   # stored as meta_post_id in DB
                fb_post_id = article.get('fb_post_id')

                # IDs are written to the DB immediately after the Meta API call
                # (before the status flip to PUBLISHED), so a non-null ID means
                # publishing was at least attempted — verify whether it landed.
                #
                # verify_meta_post_exists returns a tri-state:
                #   "confirmed"  → mark PUBLISHED (post is live)
                #   "not_found"  → revert to PUBLISH (safe to retry)
                #   "unknown"    → leave in PUBLISHING (transient error; retry next restart)
                #
                # Articles with NO stored IDs are also "unknown": we cannot prove the
                # post didn't go live, so we must NOT requeue — leave in PUBLISHING
                # for manual review rather than risk a duplicate.

                # Verify each platform independently so that a "not_found" on one
                # platform cannot mask a live post on the other.
                ig_state = await verify_meta_post_exists(ig_post_id) if ig_post_id else None
                fb_state = await verify_meta_post_exists(fb_post_id) if fb_post_id else None

                logger.info(
                    f"Startup: article {article_id} verification — "
                    f"ig={ig_post_id}({ig_state}) fb={fb_post_id}({fb_state})"
                )

                states = {s for s in (ig_state, fb_state) if s is not None}

                # Decision rules (evaluated in priority order):
                #   1. Any platform confirmed → post is live → mark PUBLISHED.
                #   2. All checked platforms say not_found (and at least one was checked)
                #      → post never landed → safe to revert to PUBLISH.
                #   3. Any unknown (error/timeout/no IDs) → cannot be sure → leave in
                #      PUBLISHING; do NOT requeue to avoid a potential duplicate.
                if "confirmed" in states:
                    await confirm_publishing_article_published(article_id)
                    logger.info(
                        f"Startup: article {article_id} confirmed live — marked PUBLISHED."
                    )
                    confirmed += 1
                elif states and states <= {"not_found"}:
                    # Every ID we have was explicitly not found — safe to retry.
                    await reset_publishing_article_to_publish(article_id)
                    logger.info(
                        f"Startup: article {article_id} not found on Meta — reverted to PUBLISH."
                    )
                    reverted += 1
                else:
                    # No IDs stored, or at least one platform returned "unknown"
                    # (transient error / timeout). Do NOT requeue — leave in PUBLISHING.
                    logger.warning(
                        f"Startup: article {article_id} state unresolvable — "
                        f"left in PUBLISHING; manual review may be needed."
                    )
                    left_unknown += 1

            summary_parts = []
            if confirmed:
                summary_parts.append(f"{confirmed} confirmed PUBLISHED")
            if reverted:
                summary_parts.append(f"{reverted} reverted to PUBLISH")
            if left_unknown:
                summary_parts.append(f"{left_unknown} left in PUBLISHING (unknown state)")
            if summary_parts:
                logger.info(f"Startup recovery: {', '.join(summary_parts)}.")

        scheduler.add_job(run_pipeline, 'interval', minutes=POLL_INTERVAL_MINUTES)
        scheduler.start()

        import asyncio
        asyncio.create_task(run_pipeline())
    else:
        logger.warning("Scheduler NOT started — database is not connected.")

    yield

    logger.info("Shutting down...")
    if scheduler.running:
        scheduler.shutdown()
    if db_ready:
        await close_db()


app = FastAPI(title="Lens Today Dashboard", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)

os.makedirs("static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from app.db import get_recent_articles, get_recent_videos
    articles = await get_recent_articles(limit=50)
    videos = await get_recent_videos(limit=20)
    return templates.TemplateResponse(request, "index.html", {
        "articles": articles,
        "videos": videos,
        "poll_interval_hours": POLL_INTERVAL_HOURS,
    })


@app.post("/trigger")
async def trigger_pipeline(request: Request):
    """
    Manually trigger the pipeline.
    Protected by BasicAuthMiddleware — same credentials as the dashboard.
    """
    import asyncio
    asyncio.create_task(run_pipeline())
    return {"message": "Pipeline triggered"}


@app.post("/submit-video", response_class=HTMLResponse)
async def submit_video(request: Request):
    """Handle video submission from dashboard form."""
    form_data = await request.form()
    url = form_data.get("url", "").strip()
    context = form_data.get("context", "").strip()
    video_index_str = form_data.get("video_index", "1").strip()
    try:
        video_index = int(video_index_str)
    except ValueError:
        video_index = 1
    
    if not url:
        return HTMLResponse("<div class='p-4 bg-red-900/50 text-red-200 border border-red-700 rounded-lg'>Error: URL is required.</div>")
    
    from app.db import insert_video_request
    await insert_video_request(url, context, video_index)
    
    # We will trigger the background video processor here later
    from app.scheduler import run_video_pipeline
    import asyncio
    asyncio.create_task(run_video_pipeline())
    
    return HTMLResponse("<div class='p-4 bg-green-900/50 text-green-200 border border-green-700 rounded-lg'>✅ Video queued successfully! Processing will begin in the background.</div>")


@app.get("/healthz")
async def healthz():
    """Public health endpoint used by the deployment startup probe — no auth required."""
    return {"status": "ok"}


@app.get("/next-run")
async def next_run():
    """Return the UTC ISO timestamp of the next scheduled pipeline run."""
    try:
        jobs = scheduler.get_jobs()
        for job in jobs:
            if job.next_run_time:
                return {"next_run": job.next_run_time.isoformat()}
    except Exception:
        pass
    return {"next_run": None}
