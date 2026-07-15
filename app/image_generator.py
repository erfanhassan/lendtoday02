import os
import logging
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "images")

os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

MIN_IMAGE_WIDTH = 600
MIN_IMAGE_HEIGHT = 400

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/apng,*/*;q=0.8',
    'Referer': 'https://www.google.com/'
}

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    os.path.join(ASSETS_DIR, "Inter-Bold.ttf"),
    os.path.join(ASSETS_DIR, "fonts", "Roboto-Bold.ttf"),
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("No TrueType font found — using PIL default. Text will be tiny.")
    return ImageFont.load_default()


def _is_url_likely_graphic(url: str) -> bool:
    url_lower = url.lower()
    # NOTE: do NOT include 'default' here — Drupal stores all article images
    # under /sites/default/files/ so that word matches legitimate photos.
    # Tracker/paywall patterns are handled separately in _is_url_bad_image().
    bad_words = ['logo', 'icon', 'avatar', 'button', 'banner', 'svg',
                 'advertisement', 'placeholder', 'sprite', 'blank']
    return any(x in url_lower for x in bad_words)


def _is_url_bad_image(url: str) -> bool:
    """Return True for known non-article URLs that should never be used as backgrounds.

    Catches:
    - Analytics/tracking pixels  (facebook.com/tr, google-analytics, etc.)
    - Paywall/subscriber badges  (tbsplus.png, subscriber.png, premium.png, etc.)
    - Plain data: URIs
    """
    url_lower = url.lower()
    bad_patterns = [
        'facebook.com/tr',
        'google-analytics',
        'analytics.',
        'tracking',
        'tbsplus',
        'tbs-plus',
        'tbs_plus',
        'subscriber',
        'premium',
        'paywall',
        'data:image',
        'pixel.gif',
        'pixel.png',
        '1x1',
        'spacer',
    ]
    return any(p in url_lower for p in bad_patterns)


import re as _re
_DRUPAL_STYLE_RE = _re.compile(r'/styles/[^/]+/public/')


def _drupal_original_url(styled_url: str) -> str | None:
    """Derive the full-resolution Drupal file URL from a styled/resized variant.

    Drupal styled images follow the pattern:
      .../sites/default/files/styles/{style}/public/{path}
    The original lives at:
      .../sites/default/files/{path}

    Returns None if the URL doesn't match the pattern.
    """
    m = _DRUPAL_STYLE_RE.search(styled_url)
    if not m:
        return None
    prefix = styled_url[:m.start()]            # e.g. https://tbsnews.net/sites/default/files
    suffix = styled_url[m.end():]              # e.g. organization/logo/image.jpg
    return f"{prefix}/{suffix}"


def _download_and_validate(url: str, headers: dict, timeout: int = 10) -> Image.Image | None:
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = BytesIO(resp.content)
        img = Image.open(data)
        img.load()
        w, h = img.size
        if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
            logger.warning(f"Image too small ({w}x{h}), skipping: {url}")
            return None
        return img
    except Exception as e:
        logger.warning(f"Failed to download/validate image from {url}: {e}")
        return None


def _stage1_publisher(article_image_url: str,
                       article_images: list | None = None,
                       rss_image: str | None = None) -> Image.Image | None:
    """Try every candidate image from the article page.

    Priority order:
      1. RSS media:content / media:thumbnail image (most reliable for paywalled sites)
         — also tries the Drupal original-resolution URL if it looks like a styled variant
      2. og:image / twitter:image from the article page
      3. newspaper top_image
      4. Other page images

    We stop at the first one that passes size validation.
    """
    candidates: list[str] = []

    # Prepend RSS image as highest-priority candidates (with Drupal original fallback)
    if rss_image:
        original = _drupal_original_url(rss_image)
        if original and original != rss_image:
            candidates.append(original)   # full-res first
        candidates.append(rss_image)      # styled version as backup

    if article_images:
        for u in article_images:
            if u and u not in candidates:
                candidates.append(u)
    elif article_image_url and article_image_url not in candidates:
        candidates.append(article_image_url)

    if not candidates:
        return None

    for url in candidates:
        if not url:
            continue
        if _is_url_bad_image(url):
            logger.debug(f"Stage 1: Skipping bad/tracker/paywall URL: {url[:80]}")
            continue
        if _is_url_likely_graphic(url):
            logger.debug(f"Stage 1: Skipping graphic/logo URL: {url[:80]}")
            continue
        logger.info(f"Stage 1: Trying article image — {url[:80]}")
        img = _download_and_validate(url, BROWSER_HEADERS, timeout=10)
        if img:
            logger.info(f"Stage 1: Success — {url[:80]} ({img.size[0]}x{img.size[1]})")
            return img
        logger.debug(f"Stage 1: Rejected (too small or failed): {url[:80]}")

    logger.info(f"Stage 1: All {len(candidates)} article image(s) failed — moving to Stage 2.")
    return None


def _stage2_google(search_query: str) -> Image.Image | None:
    api_key = os.getenv("GOOGLE_API_KEY")
    google_cx = os.getenv("GOOGLE_CX")
    if not api_key or not google_cx:
        logger.warning("Stage 2: GOOGLE_API_KEY or GOOGLE_CX not set. Skipping Google.")
        return None
    if not search_query:
        return None
    enhanced_query = f"{search_query} high quality news photo"
    logger.info(f"Stage 2: Searching Google Images for '{enhanced_query}'")
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "q": enhanced_query,
                "cx": google_cx,
                "key": api_key,
                "searchType": "image",
                "num": 8,
                "safe": "off",
                "imgSize": "large",
                "imgType": "photo",
            },
            timeout=10
        )
        if resp.status_code != 200:
            body = resp.text[:200]
            # 400 with "API key not valid" means key is missing/wrong — treat as
            # a config warning, not a runtime error, to avoid flooding logs.
            if resp.status_code == 400 and "API key not valid" in body:
                logger.warning("Stage 2: Google API key not valid — skipping. Update GOOGLE_API_KEY/GOOGLE_CX secrets to enable Google image search.")
            else:
                logger.error(f"Stage 2: Google API error {resp.status_code} — {body}")
            return None
        items = resp.json().get("items", [])
        for item in items:
            url = item.get("link")
            if not url or _is_url_likely_graphic(url):
                continue
            img = _download_and_validate(url, BROWSER_HEADERS, timeout=10)
            if img:
                logger.info(f"Stage 2: Google success — {url} ({img.size[0]}x{img.size[1]})")
                return img
    except Exception as e:
        logger.error(f"Stage 2: Google search failed: {e}")
    logger.info("Stage 2: No valid Google result found.")
    return None


_WIKI_STOP_WORDS = {
    'a', 'an', 'the', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
    'is', 'are', 'was', 'were', 'high', 'quality', 'news', 'photo', 'image',
    'during', 'from', 'by', 'with', 'its', 'as', 'be', 'this', 'that', 'its',
    'their', 'our', 'will', 'has', 'had', 'have', 'after', 'before', 'up',
}


def _score_wiki_title_relevance(query: str, page_title: str) -> int:
    """Count how many meaningful query words appear in the Wikipedia page title.

    A score of 0 means the page is completely off-topic (e.g. 'Healthcare in
    Bangladesh' for a query about 'Bangladesh investment seminar Beijing') and
    its image should be rejected.
    """
    query_words = {
        w.lower().strip('.,!?-')
        for w in query.split()
        if w.lower().strip('.,!?-') not in _WIKI_STOP_WORDS and len(w) > 2
    }
    title_lower = page_title.lower()
    return sum(1 for w in query_words if w in title_lower)


def _stage3_wikipedia(search_query: str) -> tuple[Image.Image | None, str]:
    """Search Wikipedia for a relevant image.

    Returns a ``(image, page_title)`` tuple so callers can record the source.
    Returns ``(None, '')`` when nothing suitable is found.

    Pages are filtered by title relevance: if a Wikipedia page title shares
    zero keywords with the search query it is almost certainly off-topic and
    its image is rejected regardless of whether the download succeeds.
    """
    if not search_query:
        return None, ''
    logger.info(f"Stage 3: Searching Wikipedia for '{search_query}'")
    wiki_headers = {'User-Agent': 'LensTodayBot/1.0 (https://github.com)'}
    try:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": search_query,
            "gsrlimit": 10,
            "prop": "pageimages|info",
            "piprop": "original",
            "inprop": "url",
        }
        res = requests.get("https://en.wikipedia.org/w/api.php",
                           params=params, headers=wiki_headers, timeout=10).json()
        pages = res.get('query', {}).get('pages', {})

        scored: list[tuple[int, str, str]] = []
        for p in pages.values():
            if 'original' not in p:
                continue
            title = p.get('title', '')
            url = p['original']['source']
            score = _score_wiki_title_relevance(search_query, title)
            scored.append((score, title, url))

        scored.sort(key=lambda x: x[0], reverse=True)

        for score, title, url in scored:
            if score == 0:
                logger.info(
                    f"Stage 3: Skipping off-topic Wikipedia page '{title}' "
                    f"(0 keyword overlap with query '{search_query}')"
                )
                continue
            img = _download_and_validate(url, wiki_headers, timeout=8)
            if img:
                logger.info(
                    f"Stage 3: Wikipedia success — page='{title}' score={score} "
                    f"url={url} ({img.size[0]}x{img.size[1]})"
                )
                return img, title
            logger.debug(f"Stage 3: Image download failed for '{title}': {url[:80]}")

        logger.info("Stage 3: No relevant Wikipedia page/image found after relevance filtering.")
    except Exception as e:
        logger.error(f"Stage 3: Wikipedia search failed: {e}")
    return None, ''


def _stage4_fallback() -> Image.Image:
    logger.warning("Stage 4: All image sources failed — using solid colour canvas.")
    return Image.new('RGB', (1080, 1350), color=(15, 23, 42))


def _draw_text_with_outline(draw: ImageDraw.ImageDraw, pos: tuple,
                             text: str, font, fill: str, outline: str,
                             outline_width: int = 3):
    x, y = pos
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _wrap_text(text: str, font, draw: ImageDraw.ImageDraw, max_width: int, max_lines: int = 3) -> list[str]:
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = ' '.join(current + [word])
        try:
            w = draw.textbbox((0, 0), test, font=font)[2]
        except AttributeError:
            w, _ = draw.textsize(test, font=font)
        if w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(' '.join(current))
                current = [word]
            else:
                # Single word wider than max_width — force it on its own line
                lines.append(word)
                current = []
        # Stop cleanly at max_lines — never dump overflow onto the last line
        if len(lines) >= max_lines:
            return lines[:max_lines]
    if current:
        lines.append(' '.join(current))
    return lines[:max_lines]


def _wrap_text_with_font_scaling(text: str, draw: ImageDraw.ImageDraw,
                                  max_width: int, max_lines: int = 3,
                                  start_size: int = 82) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Try progressively smaller fonts until the full headline fits within max_lines.

    Two conditions must both pass before accepting a size:
      1. Width: every line is within max_width pixels.
      2. Completeness: all words from the original text appear in the output.
         A size that silently truncates words is rejected.
    """
    all_words = text.split()

    for size in [start_size, 70, 58, 48, 38, 30]:
        font = _load_font(size)
        lines = _wrap_text(text, font, draw, max_width, max_lines)

        # Check 1 — width
        try:
            width_ok = all(draw.textbbox((0, 0), ln, font=font)[2] <= max_width for ln in lines)
        except AttributeError:
            width_ok = all(draw.textsize(ln, font=font)[0] <= max_width for ln in lines)

        # Check 2 — completeness (no words silently dropped)
        returned_words = ' '.join(lines).split()
        complete = (returned_words == all_words)

        if width_ok and complete:
            return lines, font

        # If completeness failed but width is fine, try with an extra line
        # (happens when max_lines is the only constraint, not width)
        if width_ok and not complete:
            extended = _wrap_text(text, font, draw, max_width, max_lines + 1)
            returned_ext = ' '.join(extended).split()
            if returned_ext == all_words:
                try:
                    ext_width_ok = all(draw.textbbox((0, 0), ln, font=font)[2] <= max_width for ln in extended)
                except AttributeError:
                    ext_width_ok = all(draw.textsize(ln, font=font)[0] <= max_width for ln in extended)
                if ext_width_ok:
                    return extended, font

    # Absolute last resort: use smallest font, allow up to 4 lines
    font = _load_font(30)
    lines = _wrap_text(text, font, draw, max_width, max_lines + 1)
    logger.warning(f"Headline could not be fully fitted cleanly — using 30px font: {text!r}")
    return lines, font


def generate_graphic(article_id: int, decision: dict) -> str:
    headline = decision.get("headline", "Breaking News")
    search_query = decision.get("search_query", "")
    source_text = decision.get("source_text", "")
    article_image_url = decision.get("article_image_url", "")

    if source_text:
        source_text = source_text.replace(" | None", "").replace(" | null", "").strip()

    # ── 4-Stage Image Waterfall ──────────────────────────────────────────────
    # _qa_override_bg is injected by the QA agent when it finds a replacement
    # background during a fallback-image retry, avoiding a redundant re-search.
    _override = decision.get("_qa_override_bg")
    article_images = decision.get("article_images")  # full candidate list from scraper

    image_source_label = "unknown"
    bg_img: Image.Image | None = None

    rss_image = decision.get("rss_image", "")

    if _override:
        bg_img = _override
        image_source_label = decision.get("_qa_override_source", "qa_retry")
    elif (s1 := _stage1_publisher(article_image_url, article_images, rss_image)):
        bg_img = s1
        image_source_label = "article"
    elif (s2 := _stage2_google(search_query)):
        bg_img = s2
        image_source_label = "google"
    else:
        wiki_img, wiki_title = _stage3_wikipedia(search_query)
        if wiki_img:
            bg_img = wiki_img
            image_source_label = f"wikipedia:{wiki_title}"
        else:
            bg_img = _stage4_fallback()
            image_source_label = "fallback"

    # Record the image source in the decision dict so the QA agent can check relevance.
    decision["_image_source_label"] = image_source_label
    logger.info(f"Image source for article {article_id}: {image_source_label}")

    # ── Strategy: Fit Image to Canvas (Cover) ───────
    # Fill the 1080x1350 canvas completely, cropping as necessary.
    # centering=(0.5, 0.0) anchors the crop at the top so that if vertical cropping
    # is needed, we don't lose the top of the image (since the bottom will be covered by the black panel).
    canvas = ImageOps.fit(bg_img.convert("RGBA"), (1080, 1350), method=Image.LANCZOS, centering=(0.5, 0.0))

    # ── Overlay branded template (Lens Today frame) ──────────────────────────
    template_path = os.path.join(ASSETS_DIR, "post 1.png")
    if os.path.exists(template_path):
        template = Image.open(template_path).convert("RGBA")
        if template.size != (1080, 1350):
            template = template.resize((1080, 1350), Image.LANCZOS)
        canvas.alpha_composite(template)
    else:
        logger.warning("Template not found — building branded frame programmatically.")
        # Draw a dark gradient panel at the bottom
        overlay = Image.new("RGBA", (1080, 1350), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        panel_top = 950
        for y in range(panel_top, 1350):
            alpha = int(220 * ((y - panel_top) / (1350 - panel_top)))
            od.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
        canvas.alpha_composite(overlay)

    # ── Fonts ─────────────────────────────────────────────────────────────────
    source_font = _load_font(34)

    draw = ImageDraw.Draw(canvas)

    # ── Headline text — centred in bottom panel ───────────────────────────────
    max_text_width = 960
    lines, headline_font = _wrap_text_with_font_scaling(
        headline, draw, max_text_width, max_lines=3, start_size=82
    )

    font_size_approx = headline_font.size if hasattr(headline_font, 'size') else 82
    line_height = max(int(font_size_approx * 1.18), 72)
    total_h = len(lines) * line_height
    text_area_center_y = 1160
    start_y = text_area_center_y - total_h // 2

    for line in lines:
        try:
            lw = draw.textbbox((0, 0), line, font=headline_font)[2]
        except AttributeError:
            lw, _ = draw.textsize(line, font=headline_font)
        x = max(20, (1080 - lw) // 2)
        _draw_text_with_outline(draw, (x, start_y), line,
                                font=headline_font,
                                fill="#FFFFFF",
                                outline="#000000",
                                outline_width=3)
        start_y += line_height

    # ── Source label — top right ──────────────────────────────────────────────
    if source_text:
        try:
            sw = draw.textbbox((0, 0), source_text, font=source_font)[2]
        except AttributeError:
            sw, _ = draw.textsize(source_text, font=source_font)
        _draw_text_with_outline(draw, (1080 - sw - 36, 250), source_text,
                                font=source_font,
                                fill="#FFFFFF",
                                outline="#000000",
                                outline_width=2)

    # ── Save at maximum quality ───────────────────────────────────────────────
    output_path = os.path.join(OUTPUT_DIR, f"article_{article_id}.jpg")
    canvas.convert("RGB").save(output_path, "JPEG", quality=97, subsampling=0)

    logger.info(f"Graphic saved: {output_path}")
    return output_path
