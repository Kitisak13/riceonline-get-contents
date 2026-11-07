# config.py - Central Configuration for Rice Online News Automation
# ======================================================================

import os
from dotenv import load_dotenv

load_dotenv()

# --- DIRECTORIES ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
LOG_DIR = os.path.join(BASE_DIR, "logs")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# --- TARGET URLS ---
RICEONLINE_URL = "https://riceonline.com/"

# --- GEMINI MODEL CONFIGURATION ---
# Primary model for URL Search Grounding and Editorial Cleaning
GEMINI_PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
GEMINI_API_DELAY = 4.2  # Seconds between API calls (protects 15 RPM free tier limit)
GEMINI_TIMEOUT = 45000  # Milliseconds for google-genai HttpOptions (45 seconds)

# --- URL RESOLVER (SKILL.MD COMPLIANCE) ---
DATE_FRESHNESS_DAYS = 7  # Reject news articles older than 7 days
MIN_SUBPATH_SEGMENTS = 1  # Require at least domain.com/path

BLOCKED_DOMAINS = [
    # Social Media
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
    # Video Platforms
    "youtube.com",
    # Forums
    "reddit.com",
    # Source Aggregator
    "riceonline.com",
    # Search Engine & Grounding Wrapper Domains (must resolve to actual target)
    "vertexaisearch.cloud.google.com", "google.com",
    # Specific Non-News / Excluded Domains
    "who.int", "oryza.com", "livemint.com", "fas.usda.gov"
]

BLOCKED_EXTENSIONS = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx')

# --- SCRAPER & BROWSER CONFIGURATION ---
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15  # HTTP timeout in seconds
SCRAPER_MAX_WORKERS = 3  # Parallel workers for HTTP content fetching

# --- FILE PATHS ---
RAW_HEADLINES_FILE = os.path.join(DATA_DIR, "raw_headlines.json")
RESOLVED_URLS_FILE = os.path.join(DATA_DIR, "resolved_urls.json")
SCRAPED_ARTICLES_FILE = os.path.join(DATA_DIR, "scraped_articles.json")

# Persistent cache files (separated from per-run output artifacts)
URL_CACHE_FILE = os.path.join(DATA_DIR, "url_cache.json")
SCRAPED_CACHE_FILE = os.path.join(DATA_DIR, "scraped_cache.json")
