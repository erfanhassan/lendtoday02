import json
import logging
from typing import List, Dict
from openai import AsyncOpenAI
from app.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

logger = logging.getLogger(__name__)

_client = None

def _get_client():
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            return None
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL
        )
    return _client

SYSTEM_PROMPT = """You are a Lead Editor. You will receive between 1 and 5 news articles. Review them and deduplicate any matching stories. Return a JSON array with exactly ONE object per article provided — no more, no fewer. Adjust the tone: authoritative for politics/economy, energetic for sports/movies, and strictly somber/respectful for accidents/tragedies (no emojis here).
Each JSON object MUST contain:
- 'slot': The article's position number as given in the input (1, 2, 3, etc.).
- 'category': 'Core' or 'Entertainment'.
- 'headline': A punchy 5-8 word headline.
- 'source_text': Formatted exactly as 'Via [Source Name]'. Do NOT include a date or day.
- 'search_query': Act as an Art Director. ALWAYS start with the specific person's full name, team name, place name, or organisation name that is the main subject of the story — never omit it. Then add 2-3 highly visual, physical descriptive words. The query MUST be anchored to the exact real subject so image search returns a photo of the correct person or place.
- Example for Messi story: 'Lionel Messi World Cup goal celebration Argentina'.
- Example for Haaland story: 'Erling Haaland Manchester City portrait'.
- Example for US-Iran talks: 'Qatar Prime Minister US Iran diplomatic talks'.
- Example for Kenya Health Minister: 'Kenya Health Minister Susan Nakhumicha court'.
- Example for economy: 'stock market digital chart red' or 'frustrated shopper empty wallet'.
- Example for Politics: '[Politician Full Name] press conference portrait'.
- 'social_media_caption': A rephrased summary for social media. Do NOT include any date or day in the caption.
- 'engagement_question': A question to drive comments.
- 'hashtags': 3 to 5 relevant hashtags."""

def _build_article_prompt(articles: List[Dict]) -> str:
    n = len(articles)
    prompt = f"You have been given {n} article(s). Return a JSON array with exactly {n} object(s).\n\n"
    for idx, article in enumerate(articles, start=1):
        # Trim content to 800 chars so the full response fits within token budget
        content = (article.get('content') or '')[:800]
        prompt += f"Article {idx}:\n"
        prompt += f"Title: {article.get('title')}\n"
        prompt += f"Source: {article.get('source')}\n"
        prompt += f"Category: {article.get('category')}\n"
        prompt += f"Content:\n{content}\n\n"
    return prompt


def _parse_json_response(raw: str) -> list | None:
    """Strip markdown fences and parse JSON. Returns list or None."""
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


async def analyze_articles_batch(articles: List[Dict]) -> List[Dict]:
    """
    Sends articles to DeepSeek and requests a strict JSON array back.
    Handles 1–5 articles gracefully. Retries up to 3 times on bad JSON.
    """
    client = _get_client()
    if not client:
        logger.error("DEEPSEEK_API_KEY is missing!")
        return []

    if not articles:
        return []

    n = len(articles)
    prompt = _build_article_prompt(articles)

    for attempt in range(1, 4):  # up to 3 attempts
        try:
            response = await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=4000,
            )

            raw_output = response.choices[0].message.content or ""
            data = _parse_json_response(raw_output)

            if data is not None:
                if len(data) != n:
                    logger.warning(
                        f"DeepSeek returned {len(data)} decisions for {n} articles — "
                        f"will match by index as fallback."
                    )
                return data

            logger.warning(
                f"Attempt {attempt}/3: DeepSeek returned invalid JSON. "
                f"Raw (first 300 chars): {raw_output[:300]!r}"
            )

        except Exception as e:
            logger.warning(f"Attempt {attempt}/3: DeepSeek API error: {e}")

    logger.error("All 3 attempts failed — no AI decisions returned for this batch.")
    return []

VIDEO_SYSTEM_PROMPT = """You are a social media manager.
You will be given the original caption of a viral video and optionally some context hints.
Create two things:
1. 'short_title': A punchy 3-5 word title to overlay directly on the video. Make it exciting and attention-grabbing.
2. 'social_media_caption': A viral-style social media caption including 3-5 hashtags.

Return a strict JSON object containing EXACTLY these two keys: 'short_title' and 'social_media_caption'."""

async def generate_video_text(original_caption: str, context: str) -> dict | None:
    """
    Sends video caption and context to DeepSeek to generate a short title and post caption.
    Returns a dict with 'short_title' and 'social_media_caption'.
    """
    client = _get_client()
    if not client:
        logger.error("DEEPSEEK_API_KEY is missing!")
        return None

    prompt = f"Original Caption: {original_caption}\nContext Hint: {context}"

    try:
        response = await client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": VIDEO_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )

        raw_output = response.choices[0].message.content.strip()

        if raw_output.startswith("```json"):
            raw_output = raw_output[7:]
        if raw_output.startswith("```"):
            raw_output = raw_output[3:]
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]

        data = json.loads(raw_output.strip())
        return data

    except Exception as e:
        logger.error(f"Error generating video text from DeepSeek: {e}")
        return None

