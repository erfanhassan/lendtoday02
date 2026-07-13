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
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://user:password@localhost/lens_today")

# DeepSeek AI
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# Meta Graph API
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_PAGE_ID = os.environ.get("META_PAGE_ID", "")
META_IG_ACCOUNT_ID = os.environ.get("META_IG_ACCOUNT_ID", "")

# Posting schedule: every 8 hours, 5 posts per session.
POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "480"))

# Public base URL (used to serve images to Instagram)
_replit_domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", f"https://{_replit_domain}" if _replit_domain else "")

# --- DASHBOARD AUTH ---
# Set DASHBOARD_USERNAME and DASHBOARD_PASSWORD in Secrets to protect the dashboard.
# If not set, the dashboard is accessible without authentication (dev mode).
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
