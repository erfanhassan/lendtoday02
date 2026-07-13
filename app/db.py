import asyncpg
import logging
from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

pool = None

async def init_db():
    global pool
    logger.info("Initializing database connection pool...")
    try:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
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
                'ALTER TABLE articles ADD COLUMN social_media_caption TEXT;',
                'ALTER TABLE articles ADD COLUMN engagement_question TEXT;',
                'ALTER TABLE articles ADD COLUMN hashtags TEXT;',
                'ALTER TABLE articles ADD COLUMN article_image_url TEXT;',
                "ALTER TABLE articles ADD COLUMN qa_status VARCHAR(20);",
                'ALTER TABLE articles ADD COLUMN qa_notes TEXT;',
                'ALTER TABLE video_queue ADD COLUMN video_index INTEGER DEFAULT 1;',
            ]:
                try:
                    await conn.execute(col_def)
                except Exception:
                    pass

        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise e

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
    article_image_url: str = None
):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = $1, category = $2, slot = $3, headline = $4, source_text = $5,
                search_query = $6, social_media_caption = $7, engagement_question = $8,
                hashtags = $9, article_image_url = $10
            WHERE id = $11
        ''', status, category, slot, headline, source_text, search_query,
            social_media_caption, engagement_question, hashtags, article_image_url, article_id)

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

async def mark_article_published(article_id: int):
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISHED', published_at = NOW()
            WHERE id = $1
        ''', article_id)

async def reset_publishing_to_publish(article_id: int):
    """Roll back PUBLISHING → PUBLISH so retry logic can pick it up."""
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISH'
            WHERE id = $1 AND status = 'PUBLISHING'
        ''', article_id)

async def reset_stale_publishing_articles() -> int:
    """
    On startup, reset any articles left in PUBLISHING (from a previous crash)
    back to PUBLISH so the retry loop can pick them up.
    Returns the number of articles reset.
    """
    p = await get_pool()
    async with p.acquire() as conn:
        result = await conn.execute('''
            UPDATE articles
            SET status = 'PUBLISH'
            WHERE status = 'PUBLISHING'
        ''')
        parts = result.split()
        return int(parts[1]) if len(parts) >= 2 else 0

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
