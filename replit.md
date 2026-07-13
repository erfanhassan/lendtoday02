# Lens Today

An automated news aggregation and Instagram publishing pipeline focused on Bangladesh news.

## What it does

1. **Scrapes** RSS feeds from Bangladeshi and international news sources on a configurable schedule
2. **Filters** articles by keyword relevance (politics, economy, sports, entertainment)
3. **Uses AI** (DeepSeek) to write Instagram captions, engagement questions, and hashtags
4. **Generates images** for articles
5. **QA checks** the post before publishing
6. **Publishes** to a Facebook Page / Instagram account via the Meta Graph API
7. **Dashboard** — a web UI at `/` showing article pipeline status

## Stack

- **Backend:** FastAPI + asyncpg (Python 3.12)
- **Database:** Replit-managed PostgreSQL (schema auto-created on startup)
- **AI:** DeepSeek API (OpenAI-compatible)
- **Scheduler:** APScheduler (interval-based, default every 8 hours)
- **Server:** Uvicorn on port 5000

## Running

The workflow "Start application" runs:
```
uvicorn main:app --host 0.0.0.0 --port 5000
```

## Required secrets

Add these in the Replit Secrets pane before the pipeline will fully work:

| Secret | Description |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek API key for AI writing |
| `META_ACCESS_TOKEN` | Meta Graph API long-lived page access token |
| `META_PAGE_ID` | Facebook Page ID |
| `META_IG_ACCOUNT_ID` | Instagram Business Account ID |

## Optional secrets / env vars

| Key | Default | Description |
|---|---|---|
| `DASHBOARD_USERNAME` | _(none)_ | HTTP Basic Auth username for dashboard |
| `DASHBOARD_PASSWORD` | _(none)_ | HTTP Basic Auth password for dashboard |
| `POLL_INTERVAL_MINUTES` | `480` | How often the pipeline runs (minutes) |
| `APP_BASE_URL` | auto-detected | Public base URL used for serving images to Instagram |

Without `DEEPSEEK_API_KEY` the AI step will fail silently and articles won't advance past PENDING. Without Meta credentials posts won't be published but the dashboard and scraper will still work.

## User preferences

- Keep the existing project structure and stack
