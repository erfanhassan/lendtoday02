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

from app.db import init_db, close_db, get_recent_articles, reset_stale_publishing_articles
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

        # Allow static files through without auth (needed for Instagram image serving)
        if request.url.path.startswith("/static/"):
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
    await init_db()

    # Reset any articles stuck in PUBLISHING from a previous crash
    reset_count = await reset_stale_publishing_articles()
    if reset_count:
        logger.info(f"Startup: reset {reset_count} stale PUBLISHING article(s) back to PUBLISH.")

    scheduler.add_job(run_pipeline, 'interval', minutes=POLL_INTERVAL_MINUTES)
    scheduler.start()

    import asyncio
    asyncio.create_task(run_pipeline())

    yield

    logger.info("Shutting down...")
    scheduler.shutdown()
    await close_db()


app = FastAPI(title="Lens Today Dashboard", lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)

os.makedirs("static/images", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    articles = await get_recent_articles(limit=50)
    return templates.TemplateResponse(request, "index.html", {
        "articles": articles,
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
    
    if not url:
        return HTMLResponse("<div class='p-4 bg-red-900/50 text-red-200 border border-red-700 rounded-lg'>Error: URL is required.</div>")
    
    from app.db import insert_video_request
    await insert_video_request(url, context)
    
    # We will trigger the background video processor here later
    from app.scheduler import run_video_pipeline
    import asyncio
    asyncio.create_task(run_video_pipeline())
    
    return HTMLResponse("<div class='p-4 bg-green-900/50 text-green-200 border border-green-700 rounded-lg'>✅ Video queued successfully! Processing will begin in the background.</div>")


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
