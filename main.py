import secrets
import hashlib
import hmac as _hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import (
    init_db, close_db, get_recent_articles,
    get_stale_publishing_articles,
    confirm_publishing_article_published,
    reset_publishing_article_to_publish,
)
from app.scheduler import run_pipeline, set_scheduler
from app.config import DASHBOARD_USERNAME, DASHBOARD_PASSWORD, SESSION_SECRET, AUTO_START_PIPELINE

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

_AUTH_ENABLED = bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)
_COOKIE_NAME = "lens_session"


# ---------------------------------------------------------------------------
# Cookie-based auth helpers (HMAC-SHA256, no external dependencies)
# ---------------------------------------------------------------------------

def _sign(value: str) -> str:
    return _hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def _make_session_cookie(username: str) -> str:
    return f"{username}:{_sign(username)}"


def _is_valid_cookie(cookie_value: str) -> bool:
    if not cookie_value:
        return False
    parts = cookie_value.rsplit(":", 1)
    if len(parts) != 2:
        return False
    username, sig = parts
    return (
        _hmac.compare_digest(sig, _sign(username))
        and secrets.compare_digest(username, DASHBOARD_USERNAME)
    )


def is_authenticated(request: Request) -> bool:
    """Return True if the request carries a valid session cookie (or auth is disabled)."""
    if not _AUTH_ENABLED:
        return True
    return _is_valid_cookie(request.cookies.get(_COOKIE_NAME, ""))


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
        import asyncio
        
        async def _startup_recovery_and_pipeline():
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

                    # Verify each platform independently
                    ig_state = await verify_meta_post_exists(ig_post_id) if ig_post_id else None
                    fb_state = await verify_meta_post_exists(fb_post_id) if fb_post_id else None

                    logger.info(
                        f"Startup: article {article_id} verification — "
                        f"ig={ig_post_id}({ig_state}) fb={fb_post_id}({fb_state})"
                    )

                    states = {s for s in (ig_state, fb_state) if s is not None}

                    if "confirmed" in states:
                        await confirm_publishing_article_published(article_id)
                        logger.info(f"Startup: article {article_id} confirmed live — marked PUBLISHED.")
                        confirmed += 1
                    elif states and states <= {"not_found"}:
                        await reset_publishing_article_to_publish(article_id)
                        logger.info(f"Startup: article {article_id} not found on Meta — reverted to PUBLISH.")
                        reverted += 1
                    else:
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

            if AUTO_START_PIPELINE:
                await run_pipeline()
            else:
                logger.info("AUTO_START_PIPELINE is false — skipping immediate pipeline run on startup.")

        scheduler.start()
        set_scheduler(scheduler)   # give scheduler.py a reference so run_pipeline can self-reschedule

        asyncio.create_task(_startup_recovery_and_pipeline())
    else:
        logger.warning("Scheduler NOT started — database is not connected.")

    yield

    logger.info("Shutting down...")
    if scheduler.running:
        scheduler.shutdown()
    if db_ready:
        await close_db()


app = FastAPI(title="Lens Today Dashboard", lifespan=lifespan)

os.makedirs("static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    user_ok = secrets.compare_digest(username, DASHBOARD_USERNAME)
    pass_ok = secrets.compare_digest(password, DASHBOARD_PASSWORD)

    if user_ok and pass_ok:
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            _COOKIE_NAME,
            _make_session_cookie(username),
            httponly=True,
            samesite="lax",
            secure=False,
            max_age=60 * 60 * 24 * 7,  # 7 days
        )
        return response

    return templates.TemplateResponse(
        request, "login.html",
        {"error": "Invalid username or password."},
        status_code=200,
    )


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(_COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Return login page (200) when not authenticated — keeps the deployment
    # startup probe happy (it hits GET / and needs a 200).
    if not is_authenticated(request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    from app.db import get_recent_articles, get_recent_videos
    articles = await get_recent_articles(limit=50)
    videos = await get_recent_videos(limit=20)
    return templates.TemplateResponse(request, "index.html", {
        "articles": articles,
        "videos": videos,
    })


@app.post("/trigger")
async def trigger_pipeline(request: Request):
    """Manually trigger the pipeline. Requires an active session."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    import asyncio
    asyncio.create_task(run_pipeline())
    return {"message": "Pipeline triggered"}


@app.post("/api/v1/test-simulation")
async def test_simulation_endpoint(request: Request):
    """Run the test simulation pipeline safely on Replit."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    from app.simulator import run_simulation
    results = await run_simulation()
    
    # Check if there was any failure
    if "failed_phase_error" in results:
        return JSONResponse(status_code=500, content=results)
        
    return JSONResponse(status_code=200, content=results)


@app.post("/submit-video", response_class=HTMLResponse)
async def submit_video(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
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


@app.get("/health")
async def health():
    """Unprotected health check required by Replit deployment."""
    return {"status": "healthy"}


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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False, workers=1)
