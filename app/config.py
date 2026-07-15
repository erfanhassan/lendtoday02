import os
from dotenv import load_dotenv

# Load variables from .env file if it exists
load_dotenv()

# --- RSS FEED CONFIGURATION ---
TIER_1_FEEDS = {
    "The Daily Star":        "https://www.thedailystar.net/news/bangladesh/rss.xml",
    "The Business Standard": "https://www.tbsnews.net/bangladesh/rss.xml",
}

TIER_2_FEEDS = {
    "Prothom Alo": "https://en.prothomalo.com/feed",
    "BBC News":    "https://feeds.bbci.co.uk/news/rss.xml",
    "Al Jazeera":  "https://www.aljazeera.com/xml/rss/all.xml",
    "NY Times":    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
}

# --- KEYWORD FILTERS ---
CORE_KEYWORDS = [
    'Dhaka', 'Chattogram', 'Sylhet', 'Rajshahi', 'Khulna', 'Barishal',
    'Bangladesh', 'Bangladeshi', 'Yunus', 'Tarique Rahman', 'BNP',
    'Awami League', 'National Citizen Party', 'NCP', 'Parliament', 'Election',
    'Polls', 'Reform', 'Remittance', 'RMG', 'Garment', 'Taka', 'BB', 'BGMEA',
    'Teesta', 'Rohingya', 'Cox\'s Bazar', 'BSF', 'Border',
    'India', 'New Delhi', 'China', 'Beijing', 'US', 'America', 'Washington',
    'Economy', 'IMF', 'Inflation', 'Sanctions', 'Refugee', 'UN',
    'Human Rights', 'AI', 'Artificial Intelligence', 'OpenAI', 'DeepSeek',
    'War', 'Conflict', 'Middle East', 'Russia', 'Ukraine',
]

ENTERTAINMENT_KEYWORDS = [
    'Cricket', 'Football', 'BCB', 'Shakib', 'Tamim', 'BPL', 'IPL', 'T20',
    'World Cup', 'Messi', 'Ronaldo', 'FIFA', 'Bollywood', 'Dhallywood',
    'Tollywood', 'Hollywood', 'Movie', 'Actor', 'Actress', 'Drama', 'Viral',
    'Rumor', 'Wedding', 'Robbery', 'Arrested', 'Bus', 'Truck', 'Accident', 'Fire',
]

# --- ENVIRONMENT VARIABLES ---

# PostgreSQL
# Priority: explicit DATABASE_URL env var > Replit-provisioned PG* vars > local fallback
def _build_database_url() -> str:
    """Resolve the database connection URL, supporting Replit's auto-provisioned PostgreSQL."""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit

    # Replit's postgresql-16 module sets individual PG* environment variables
    pg_host = os.environ.get("PGHOST")
    pg_port = os.environ.get("PGPORT", "5432")
    pg_user = os.environ.get("PGUSER")
    pg_password = os.environ.get("PGPASSWORD")
    pg_database = os.environ.get("PGDATABASE")

    if pg_host and pg_user and pg_database:
        password_part = f":{pg_password}" if pg_password else ""
        return f"postgresql://{pg_user}{password_part}@{pg_host}:{pg_port}/{pg_database}"

    return "postgresql://user:password@localhost/lens_today"

DATABASE_URL = _build_database_url()

# DeepSeek AI
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Meta Graph API
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_PAGE_ID = os.environ.get("META_PAGE_ID", "")
META_IG_ACCOUNT_ID = os.environ.get("META_IG_ACCOUNT_ID", "")

# Posting schedule: every 8 hours, 5 posts per session.
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "480"))

# Public base URL (used to serve images/videos to Instagram)
def _build_app_base_url() -> str:
    """Resolve the public base URL, supporting both Replit dev and production."""
    explicit = os.environ.get("APP_BASE_URL")
    if explicit:
        return explicit
    # Development workspace
    dev_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
    if dev_domain:
        return f"https://{dev_domain}"
    # Production deploy — REPLIT_DOMAINS is a comma-separated list
    prod_domains = os.environ.get("REPLIT_DOMAINS", "")
    if prod_domains:
        first_domain = prod_domains.split(",")[0].strip()
        return f"https://{first_domain}"
    return ""

APP_BASE_URL = _build_app_base_url()

# --- DASHBOARD AUTH ---
# Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD in Secrets to protect the dashboard.
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-insecure-secret-change-me")
# If not set, the dashboard is accessible without authentication (dev mode).
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

# --- PLATFORM COOKIES ---
# Paste the full Netscape-format cookies.txt content (exported from your browser
# while logged in to each platform) into the respective secret.
# Required for Facebook and Instagram downloads (they block unauthenticated server IPs).
# YouTube cookies also help bypass bot-detection on restricted videos.
YOUTUBE_COOKIES = os.environ.get("YOUTUBE_COOKIES", "")
FACEBOOK_COOKIES = os.environ.get("FACEBOOK_COOKIES", "")
INSTAGRAM_COOKIES = os.environ.get("INSTAGRAM_COOKIES", "")
