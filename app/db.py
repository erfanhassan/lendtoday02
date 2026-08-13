import asyncpg
import logging
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

logger = logging.getLogger(__name__)

pool = None

async def init_db():
    global pool
    # Log the DB host (sanitized) to help diagnose connection issues
    try:
        from urllib.parse import urlparse
        _parsed = urlparse(DATABASE_URL)
        logger.info(f"Initializing database connection pool → {_parsed.hostname}:{_parsed.port or 5432}/{_parsed.path.lstrip('/')}")
    except Exception:
        logger.info("Initializing database connection pool...")
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            timeout=10,          # seconds to wait for a connection from the pool
            command_timeout=15,  # seconds before a query times out
            ssl=ctx
        )
        async with pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    id SERIAL PRIMARY KEY,
                    source VARCHAR(100),
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    status VARCHAR(20) DEFAULT 'PENDING',
                    category VARCHAR(50),
                    slot INTEGER,
                    headline TEXT,
                    source_text TEXT,
                    search_query TEXT,
                    social_media_caption TEXT,
                    engagement_question TEXT,
                    hashtags TEXT,
                    published_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS video_queue (
                    id SERIAL PRIMARY KEY,
                    url TEXT NOT NULL,
                    context TEXT,
                    video_index INTEGER DEFAULT 1,
                    status VARCHAR(20) DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            for col_def in [
                'ALTER TABLE articles ADD COLUMN slot INTEGER;',
                'ALTER TABLE articles ADD COLUMN headline TEXT;',
                'ALTER TABLE articles ADD COLUMN source_text TEXT;',
                'ALTER TABLE articles ADD COLUMN search_query TEXT;',
                'ALTER TABLE articles ADD COLUMN image_prompt TEXT;',
                'ALTER TABLE articles ADD COLUMN social_media_caption TEXT;',
                'ALTER TABLE articles ADD COLUMN engagement_question TEXT;',
                'ALTER TABLE articles ADD COLUMN hashtags TEXT;',
                'ALTER TABLE articles ADD COLUMN article_image_url TEXT;',
                "ALTER TABLE articles ADD COLUMN qa_status VARCHAR(20);",
                'ALTER TABLE articles ADD COLUMN qa_notes TEXT;',
                'ALTER TABLE video_queue ADD COLUMN video_index INTEGER DEFAULT 1;',
                'ALTER TABLE articles ADD COLUMN meta_post_id VARCHAR(200);',
                'ALTER TABLE articles ADD COLUMN fb_post_id VARCHAR(200);',
            ]:
                try:
                    await conn.execute(col_def)
                except Exception:
                    pass

        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.warning(f"Failed to initialize database: {e} - Application will start but pipeline is disabled.")

async def close_db():
    if pool:
        logger.info("Closing database connection pool...")
        await pool.close()

async def get_pool():
    if not pool:
        await init_db()
    return pool

async def get_article_status(article_id: int) -> str | None:
    p = await get_pool()
    async with p.acquire() as conn:
        record = await conn.fetchrow('SELECT status FROM articles WHERE id = $1', article_id)
        return record['status'] if record else None

async def is_url_processed(url: str) -> bool:
    p = await get_pool()
    async with p.acquire() as conn:
        record = await conn.fetchrow('SELECT id FROM articles WHERE url = $1', url)
        return record is not None

async def insert_article(source: str, url: str, title: str) -> int:
    p = await get_pool()
    async with p.acquire() as conn:
        record = await conn.fetchrow('''
            INSERT INTO articles (source, url, title, status)
            VALUES ($1, $2, $3, 'PENDING')
            RETURNING id
        ''', source, url, title)
        return record['id']

async def update_article_ai_decision(
    article_id: int,
    status: str,
    category: str,
    slot: int,
    headline: str,
    source_text: str,
    search_query: str,
    social_media_caption: str,
    engagement_question: str,
    hashtags: str,
    article_image_url: str = None,
    image_prompt: str = None
):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = $1, category = $2, slot = $3, headline = $4, source_text = $5,
                search_query = $6, social_media_caption = $7, engagement_question = $8,
                hashtags = $9, article_image_url = $10, image_prompt = $11
            WHERE id = $12
        ''', status, category, slot, headline, source_text, search_query,
            social_media_caption, engagement_question, hashtags, article_image_url, image_prompt, article_id)

async def mark_article_publishing(article_id: int) -> bool:
    """
    Atomically transition PUBLISH → PUBLISHING.
    Returns True only if this run successfully claimed the article.
    """
    p = await get_pool()
    async with p.acquire() as conn:
        result = await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISHING'
            WHERE id = $1 AND status = 'PUBLISH'
        ''', article_id)
        return result == 'UPDATE 1'

async def store_publishing_post_ids(article_id: int, ig_post_id: str = None, fb_post_id: str = None):
    """
    Persist the Meta post IDs immediately after the API call succeeds, while the
    article is still in PUBLISHING state.  Writing the IDs as a separate step —
    before the status transition — means that if the process crashes between the
    Meta API return and the final DB update, startup verification can still find
    the IDs and confirm the post exists rather than blindly re-queuing.
    """
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET meta_post_id = COALESCE($2, meta_post_id),
                fb_post_id   = COALESCE($3, fb_post_id)
            WHERE id = $1 AND status = 'PUBLISHING'
        ''', article_id, ig_post_id, fb_post_id)

async def mark_article_published(article_id: int, ig_post_id: str = None, fb_post_id: str = None):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISHED', published_at = NOW(),
                meta_post_id = COALESCE($2, meta_post_id),
                fb_post_id   = COALESCE($3, fb_post_id)
            WHERE id = $1
        ''', article_id, ig_post_id, fb_post_id)

async def reset_publishing_to_publish(article_id: int):
    """Roll back PUBLISHING → PUBLISH so retry logic can pick it up."""
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISH'
            WHERE id = $1 AND status = 'PUBLISHING'
        ''', article_id)

async def get_stale_publishing_articles() -> list[dict]:
    """
    Return all articles currently stuck in PUBLISHING state (from a previous crash).
    """
    p = await get_pool()
    async with p.acquire() as conn:
        records = await conn.fetch('''
            SELECT id, meta_post_id, fb_post_id
            FROM articles
            WHERE status = 'PUBLISHING'
        ''')
        return [dict(r) for r in records]

async def reset_publishing_article_to_publish(article_id: int):
    """Mark a single PUBLISHING article back to PUBLISH (truly stale — never actually sent)."""
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles SET status = 'PUBLISH' WHERE id = $1 AND status = 'PUBLISHING'
        ''', article_id)

async def confirm_publishing_article_published(article_id: int):
    """Mark a PUBLISHING article as PUBLISHED once confirmed via Meta API."""
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISHED', published_at = NOW()
            WHERE id = $1 AND status = 'PUBLISHING'
        ''', article_id)

async def get_unpublished_articles():
    """
    Get articles approved by AI but not yet posted.
    Only returns articles in PUBLISH status for at least 10 minutes
    to avoid retrying articles mid-flight in the current run.
    """
    p = await get_pool()
    async with p.acquire() as conn:
        records = await conn.fetch('''
            SELECT * FROM articles
            WHERE status = 'PUBLISH'
              AND created_at < NOW() - INTERVAL '10 minutes'
            ORDER BY created_at ASC
        ''')
        return [dict(r) for r in records]

async def update_article_qa_status(article_id: int, qa_status: str, qa_notes: str):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles SET qa_status = $1, qa_notes = $2 WHERE id = $3
        ''', qa_status, qa_notes, article_id)

async def mark_article_qa_failed(article_id: int, qa_notes: str):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles SET status = 'QA_FAILED', qa_status = 'FAILED', qa_notes = $1 WHERE id = $2
        ''', qa_notes, article_id)

async def get_recent_articles(limit: int = 50):
    p = await get_pool()
    async with p.acquire() as conn:
        records = await conn.fetch('''
            SELECT * FROM articles
            ORDER BY created_at DESC
            LIMIT $1
        ''', limit)
        return [dict(r) for r in records]

async def get_pending_articles(limit: int = 5) -> list[dict]:
    """Fetch un-processed articles that are currently in PENDING status."""
    p = await get_pool()
    async with p.acquire() as conn:
        records = await conn.fetch('''
            SELECT id as article_id, source, url, title, category, status, created_at
            FROM articles
            WHERE status = 'PENDING'
            ORDER BY created_at ASC
            LIMIT $1
        ''', limit)
        return [dict(r) for r in records]

async def insert_video_request(url: str, context: str, video_index: int = 1) -> int:
    p = await get_pool()
    async with p.acquire() as conn:
        record = await conn.fetchrow('''
            INSERT INTO video_queue (url, context, video_index, status)
            VALUES ($1, $2, $3, 'PENDING')
            RETURNING id
        ''', url, context, video_index)
        return record['id']

async def get_pending_video() -> dict | None:
    """Fetch one pending video and immediately mark it as PROCESSING"""
    p = await get_pool()
    async with p.acquire() as conn:
        record = await conn.fetchrow('''
            UPDATE video_queue
            SET status = 'PROCESSING'
            WHERE id = (
                SELECT id FROM video_queue
                WHERE status = 'PENDING'
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
        ''')
        return dict(record) if record else None

async def update_video_status(video_id: int, status: str):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE video_queue SET status = $1 WHERE id = $2
        ''', status, video_id)

async def get_recent_videos(limit: int = 20) -> list[dict]:
    p = await get_pool()
    async with p.acquire() as conn:
        records = await conn.fetch('''
            SELECT id, url, context, video_index, status, created_at
            FROM video_queue
            ORDER BY created_at DESC
            LIMIT $1
        ''', limit)
        return [dict(r) for r in records]

async def clear_database():
    """Clear all data from articles and video_queue tables."""
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('TRUNCATE TABLE articles, video_queue RESTART IDENTITY CASCADE')
