import feedparser
import newspaper
import logging
import asyncio
import re
import calendar
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from app.config import TIER_1_FEEDS, TIER_2_FEEDS, CORE_KEYWORDS, ENTERTAINMENT_KEYWORDS
from app.db import is_url_processed

MAX_ARTICLE_AGE_HOURS = 24

logger = logging.getLogger(__name__)

def check_category(title: str) -> str:
    """
    Returns 'Core', 'Entertainment', or None based on keywords.
    """
    title_lower = title.lower()

    # Check Entertainment First
    for kw in ENTERTAINMENT_KEYWORDS:
        if re.search(r'\b' + re.escape(kw.lower()) + r'\b', title_lower):
            return 'Entertainment'

    # Check Core
    for kw in CORE_KEYWORDS:
        if re.search(r'\b' + re.escape(kw.lower()) + r'\b', title_lower):
            return 'Core'

    return None

def extract_article_content(url: str) -> Optional[Dict[str, str]]:
    """
    Use newspaper3k to download and parse article content.
    Returns the parsed text and article image url or None if failed.
    """
    try:
        config = newspaper.Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

        article = newspaper.Article(url, config=config)
        article.download()
        article.parse()
        text = article.text.strip()
        if not text:
            return None

        result = {"content": text}

        # Collect every candidate image from the article page so the
        # image generator has multiple relevant options to try.
        # Priority order: og:image → twitter:image → newspaper top_image → all page images
        candidate_images: list[str] = []
        if article.html:
            soup = BeautifulSoup(article.html, "html.parser")
            for meta_name in ["og:image", "og:image:secure_url", "twitter:image"]:
                tag = soup.find("meta", property=meta_name) or soup.find("meta", attrs={"name": meta_name})
                if tag and tag.get("content"):
                    url = tag["content"].strip()
                    if url and url not in candidate_images:
                        candidate_images.append(url)

        if article.top_image and article.top_image not in candidate_images:
            candidate_images.append(article.top_image)

        # newspaper3k's full image list — take up to 6 additional candidates
        for img_url in list(article.images)[:6]:
            if img_url and img_url not in candidate_images:
                candidate_images.append(img_url)

        if candidate_images:
            result["article_images"] = candidate_images
            result["article_image_url"] = candidate_images[0]

        return result
    except Exception as e:
        logger.error(f"Failed to extract content from {url}: {e}")
        return None

async def _scrape_feeds(feeds: Dict[str, str], core_needed: int, ent_needed: int) -> List[Dict]:
    results = []

    for source_name, feed_url in feeds.items():
        if core_needed <= 0 and ent_needed <= 0:
            break

        logger.info(f"Parsing feed: {source_name} -> {feed_url}")
        try:
            feed = await asyncio.to_thread(feedparser.parse, feed_url)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)

            for entry in feed.entries:
                if core_needed <= 0 and ent_needed <= 0:
                    break

                raw_title = entry.get('title', '')
                # Some feeds (e.g. Daily Star Bangladesh) wrap titles in <a> tags —
                # strip all HTML so only the plain text remains.
                title = BeautifulSoup(raw_title, "html.parser").get_text(separator=' ', strip=True)
                link = entry.get('link', '')

                # If link is missing but title contained an <a href>, extract that.
                if not link:
                    import re as _re
                    m = _re.search(r'href=["\']([^"\']+)["\']', raw_title)
                    if m:
                        link = m.group(1)

                if not title or not link:
                    continue

                # ── Freshness filter ────────────────────────────────────────
                published_parsed = entry.get('published_parsed')  # time.struct_time UTC
                if published_parsed:
                    try:
                        pub_dt = datetime.fromtimestamp(
                            calendar.timegm(published_parsed), tz=timezone.utc
                        )
                        if pub_dt < cutoff:
                            logger.debug(f"Skipping old article ({pub_dt.date()}): {title[:60]}")
                            continue
                        published = pub_dt.strftime("%Y-%m-%d")
                    except Exception:
                        published = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                else:
                    # No date in feed — include it but log a warning
                    published = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    logger.debug(f"No publish date for: {title[:60]} — including anyway")

                if await is_url_processed(link):
                    continue

                # Grab RSS summary as a content fallback for paywalled sources
                summary = entry.get('summary', '') or entry.get('description', '')
                if summary:
                    summary = BeautifulSoup(summary, "html.parser").get_text(separator=' ', strip=True)

                # ── Extract image directly from the RSS entry ──────────────
                # Tier 1 sources like TBS News embed the article photo in
                # media:content / media:thumbnail.  We store it as rss_image
                # so the image generator can use it even when the article page
                # is behind a paywall and og:image returns a badge/placeholder.
                rss_image = None
                media_content = entry.get('media_content') or []
                media_thumbnail = entry.get('media_thumbnail') or []
                for media_list in (media_content, media_thumbnail):
                    for m in media_list:
                        url_candidate = m.get('url', '')
                        if url_candidate and url_candidate.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            rss_image = url_candidate
                            break
                    if rss_image:
                        break

                # Fall back to <img> tags inside the RSS <description> HTML
                if not rss_image and summary:
                    raw_summary = entry.get('summary', '') or entry.get('description', '')
                    img_tag = BeautifulSoup(raw_summary, "html.parser").find('img')
                    if img_tag and img_tag.get('src'):
                        rss_image = img_tag['src']

                category = check_category(title)
                article_meta = {
                    "source": source_name,
                    "title": title,
                    "url": link,
                    "category": category,
                    "date": published,
                    "rss_summary": summary,
                }
                if rss_image:
                    article_meta["rss_image"] = rss_image
                    logger.debug(f"RSS image found for '{title[:60]}': {rss_image[:80]}")

                if category == 'Core' and core_needed > 0:
                    results.append(article_meta)
                    core_needed -= 1
                elif category == 'Entertainment' and ent_needed > 0:
                    results.append(article_meta)
                    ent_needed -= 1

        except Exception as e:
            logger.error(f"Error fetching RSS for {source_name}: {e}")

    return results

async def fetch_5_articles() -> List[Dict]:
    """
    Fetch 4 Core and 1 Entertainment articles.
    Returns valid articles.
    """
    articles = []
    core_needed = 4
    ent_needed = 1

    # Tier 1
    t1_articles = await _scrape_feeds(TIER_1_FEEDS, core_needed, ent_needed)
    articles.extend(t1_articles)

    for a in t1_articles:
        if a['category'] == 'Core':
            core_needed -= 1
        elif a['category'] == 'Entertainment':
            ent_needed -= 1

    # Tier 2 (if needed)
    if core_needed > 0 or ent_needed > 0:
        logger.info(f"Falling back to Tier 2. Need Core: {core_needed}, Ent: {ent_needed}")
        t2_articles = await _scrape_feeds(TIER_2_FEEDS, core_needed, ent_needed)
        articles.extend(t2_articles)

    return articles
