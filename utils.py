# utils.py - Shared Utilities and Helper Functions
# ===================================================

import html
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

from config import (
    BLOCKED_DOMAINS,
    BLOCKED_EXTENSIONS,
    DATE_FRESHNESS_DAYS,
    GEMINI_API_DELAY,
    LOG_DIR,
    MIN_SUBPATH_SEGMENTS,
    USER_AGENT,
)

# --- LOGGING SETUP WITH ROTATION ---
log_file = os.path.join(LOG_DIR, "pipeline.log")
logger = logging.getLogger("RiceNews")
logger.setLevel(logging.INFO)

if not logger.handlers:
    # Console Handler
    c_handler = logging.StreamHandler()
    c_handler.setLevel(logging.INFO)
    c_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    c_handler.setFormatter(c_format)
    logger.addHandler(c_handler)

    # Rotating File Handler (Max 5MB per file, up to 5 backups)
    f_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    f_handler.setLevel(logging.INFO)
    f_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    f_handler.setFormatter(f_format)
    logger.addHandler(f_handler)


# --- THREAD-SAFE RATE LIMITER ---
class RateLimiter:
    """Thread-safe rate limiter ensuring minimum interval between actions."""
    def __init__(self, delay_seconds: float = GEMINI_API_DELAY):
        self.delay = delay_seconds
        self.last_call = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.delay:
                sleep_time = self.delay - elapsed
                time.sleep(sleep_time)
            self.last_call = time.time()


gemini_limiter = RateLimiter(GEMINI_API_DELAY)


# --- DATE & TIME HELPERS ---
def calculate_lookback_days() -> Tuple[int, str]:
    """Calculate lookback days based on weekday (matches GAS logic)."""
    today = datetime.now()
    weekday = today.weekday()  # Monday=0, Sunday=6
    
    if weekday == 0:  # Monday
        return 3, "Monday: Looking back to Friday (skipping weekend)"
    elif weekday == 6:  # Sunday
        return 2, "Sunday: Looking back to Friday"
    return 1, "Normal day: Looking back to yesterday"


def parse_date_flexible(date_str: str) -> Optional[datetime]:
    """Parses various date string formats."""
    if not date_str:
        return None
        
    date_str = date_str.strip()
    
    formats = [
        "%B %d, %Y", "%b %d, %Y",
        "%Y-%m-%d", "%Y/%m/%d",
        "%d %B %Y", "%d %b %Y",
        "%d-%m-%Y", "%d/%m/%Y",
        "%m-%d-%Y", "%m/%d/%Y",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
            
    # Try regex extraction for "Month Day, Year"
    match = re.search(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', date_str)
    if match:
        try:
            month, day, year = match.groups()
            cleaned = f"{month} {day}, {year}"
            return datetime.strptime(cleaned, "%B %d, %Y")
        except ValueError:
            pass
            
    return None


def is_date_fresh(article_date_str: str, max_days: int = DATE_FRESHNESS_DAYS) -> bool:
    """Checks if date is within freshness threshold (within max_days)."""
    parsed = parse_date_flexible(article_date_str)
    if not parsed:
        return True  # If unable to extract exact date from candidate, don't discard prematurely
    age = (datetime.now().date() - parsed.date()).days
    return 0 <= age <= max_days


# --- URL RESOLUTION & REDIRECT UNWRAPPER ---
def resolve_redirect_url(url: str, timeout: int = 5) -> str:
    """
    Unwraps redirect URLs such as vertexaisearch.cloud.google.com or google.com/url
    to discover the true canonical destination URL.
    """
    if not url:
        return ""
        
    url = url.strip()
    
    # 1. Check if destination URL is embedded in query string parameters (fast, no network)
    if "vertexaisearch.cloud.google.com" in url or "google.com/url" in url:
        try:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            for param in ["url", "q", "dest"]:
                if param in qs and qs[param]:
                    candidate = qs[param][0]
                    if candidate.startswith("http://") or candidate.startswith("https://"):
                        return candidate
        except Exception:
            pass

        # 2. Check HTTP redirect (Google grounding redirects return HTTP 302 with Location header)
        try:
            headers = {"User-Agent": USER_AGENT}
            resp = requests.get(url, allow_redirects=False, timeout=timeout, headers=headers)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if loc and loc != url:
                    return loc
            elif resp.url and resp.url != url:
                return resp.url
        except Exception:
            pass

        # 3. Fallback: follow redirect with stream
        try:
            headers = {"User-Agent": USER_AGENT}
            with requests.get(url, allow_redirects=True, timeout=timeout, headers=headers, stream=True) as resp:
                if resp.url and resp.url != url:
                    return resp.url
        except Exception:
            pass

        # If unwrapping a known grounding redirect wrapper fails, fail fast by returning ""
        return ""

    return url


# --- URL VALIDATION & FILTERING ---
def extract_domain(url: str) -> str:
    """Extract clean domain name without www or port."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc
    except Exception:
        return ""


def is_blocked_domain(url: str) -> bool:
    """Check if URL matches any excluded or blocked domain."""
    domain = extract_domain(url)
    if not domain:
        return True
    for blocked in BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False


def is_valid_article_url(url: str) -> bool:
    """
    Validates URL according to SKILL.md rules:
    - Resolves redirect URLs before checking domain
    - Must start with http:// or https://
    - Must NOT be a homepage/root domain (requires at least MIN_SUBPATH_SEGMENTS)
    - Must NOT be on the domain exclusion list
    - Must NOT end with blocked file extensions (pdf, doc, etc.)
    """
    if not url or not isinstance(url, str):
        return False
        
    canonical_url = resolve_redirect_url(url)
    if not (canonical_url.startswith("http://") or canonical_url.startswith("https://")):
        return False
        
    try:
        parsed = urlparse(canonical_url)
        path = parsed.path.strip("/")
        
        # Sub-path Requirement: Must have at least MIN_SUBPATH_SEGMENTS
        segments = [s for s in path.split("/") if s]
        if len(segments) < MIN_SUBPATH_SEGMENTS:
            return False
            
        # Reject blocked extensions
        lower_path = path.lower()
        if any(lower_path.endswith(ext) for ext in BLOCKED_EXTENSIONS):
            return False
            
        # Reject blocked domains
        if is_blocked_domain(canonical_url):
            return False
            
        return True
    except Exception:
        return False


# --- ATOMIC FILE STORAGE ---
def save_json_atomic(filepath: str, data: Any):
    """Saves data to a temporary file then atomically replaces target."""
    dir_name = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(dir_name, exist_ok=True)
    
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, indent=2, ensure_ascii=False)
        temp_path = tf.name
        
    os.replace(temp_path, filepath)


def load_json_safe(filepath: str, default: Any = None) -> Any:
    """Loads JSON file safely, returns default if not found or corrupted."""
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read {filepath}: {e}")
        return default
