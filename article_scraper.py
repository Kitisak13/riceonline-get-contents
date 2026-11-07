# article_scraper.py - Multi-Layer News Article Content Extractor & AI Editorial Cleaner
# =========================================================================================

import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
import trafilatura
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

try:
    import curl_cffi.requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False

from config import (
    GEMINI_FALLBACK_MODEL,
    GEMINI_PRIMARY_MODEL,
    GEMINI_TIMEOUT,
    REQUEST_TIMEOUT,
    RESOLVED_URLS_FILE,
    SCRAPED_ARTICLES_FILE,
    SCRAPED_CACHE_FILE,
    SCRAPER_MAX_WORKERS,
    USER_AGENT,
)
from utils import (
    gemini_limiter,
    is_valid_article_url,
    load_json_safe,
    logger,
    save_json_atomic,
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
]


class CleanedArticle(BaseModel):
    """Structured response schema preventing refusal text from leaking into reports."""
    is_valid_article: bool = Field(
        description="True if the text contains a real news article reporting facts. "
                    "False if the text is only a paywall, cookie consent, captcha, sign-in prompt, or 404 error."
    )
    cleaned_body: str = Field(
        description="The cleaned paragraphs of the news article separated by double newlines (\\n\\n). "
                    "Must have clear, readable paragraph breaks (2-4 sentences per paragraph). "
                    "Must be an empty string if is_valid_article is False."
    )


def extract_raw_text_from_url(url: str) -> Optional[str]:
    """
    Multi-layer article extraction:
    Layer 1: curl_cffi with Chrome TLS impersonation + Trafilatura (bypasses Cloudflare)
    Layer 2: Standard Trafilatura direct fetch
    Layer 3: Requests + BeautifulSoup4 DOM extraction
    """
    if not is_valid_article_url(url):
        return None

    # Layer 1: curl_cffi + Trafilatura
    if HAS_CURL_CFFI:
        try:
            resp = curl_requests.get(
                url,
                impersonate="chrome124",
                timeout=REQUEST_TIMEOUT,
                headers={"Accept-Language": "en-US,en;q=0.9"}
            )
            if resp.status_code == 200 and resp.text:
                extracted = trafilatura.extract(
                    resp.text,
                    include_links=False,
                    include_images=False,
                    favor_precision=True
                )
                if extracted and len(extracted.strip()) > 150:
                    return extracted.strip()
        except Exception as e:
            logger.debug(f"Layer 1 (curl_cffi) skipped for {url[:40]}: {e}")

    # Layer 2: Trafilatura direct fetch
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            extracted = trafilatura.extract(
                downloaded,
                include_links=False,
                include_images=False,
                favor_precision=True
            )
            if extracted and len(extracted.strip()) > 150:
                return extracted.strip()
    except Exception as e:
        logger.debug(f"Layer 2 (trafilatura) skipped for {url[:40]}: {e}")

    # Layer 3: Requests + BeautifulSoup4
    try:
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"
        }
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            for element in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                element.decompose()
                
            paragraphs = soup.find_all("p")
            text_blocks = [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 30]
            joined = "\n\n".join(text_blocks)
            if len(joined) > 150:
                return joined
    except Exception as e:
        logger.debug(f"Layer 3 (requests/bs4) failed for {url[:40]}: {e}")

    return None


def clean_content_with_ai(client: genai.Client, raw_text: str, headline: str) -> str:
    """Uses Gemini with Structured Output to clean raw article text."""
    if not raw_text or len(raw_text.strip()) < 100:
        return ""

    prompt = f"""Target Headline: {headline}

Raw Scraped Page Text:
{raw_text[:7000]}

Instructions:
1. Determine if this text contains an actual news article about the headline.
2. If it is only a paywall teaser, cookie consent wall, subscription notice, or error page, set is_valid_article=False and cleaned_body="".
3. If it contains real news reporting, set is_valid_article=True and set cleaned_body to the clean, coherent paragraphs of the article separated by double newlines (\n\n). Ensure text is broken into readable paragraphs (2-4 sentences per paragraph) rather than one single continuous block.
"""
    models_to_try = [GEMINI_PRIMARY_MODEL, GEMINI_FALLBACK_MODEL]

    for model_name in models_to_try:
        for attempt in range(3):
            try:
                gemini_limiter.wait()
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CleanedArticle,
                    temperature=0.1,
                    http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT)
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
                result = CleanedArticle.model_validate_json(response.text)
                if result.is_valid_article and result.cleaned_body:
                    return result.cleaned_body.strip()
                return ""
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "503" in err_msg:
                    backoff = (2 ** attempt) * 4
                    logger.warning(f"⚠️ {model_name} rate limit in clean_content (attempt {attempt+1}/3), waiting {backoff}s...")
                    time.sleep(backoff)
                else:
                    logger.warning(f"AI Cleaning with {model_name} failed: {e}")
                    break

    return ""


def scrape_and_clean_articles(
    resolved_items: Optional[List[Dict[str, str]]] = None,
    max_workers: int = SCRAPER_MAX_WORKERS
) -> List[Dict[str, Any]]:
    """
    Scrapes content concurrently and cleans using Gemini Structured Outputs.
    Preserves persistent cache in data/scraped_cache.json.
    """
    logger.info("=" * 60)
    logger.info("📰 RICE NEWS - Step 3: Scraping Content & AI Editorial Cleaning")
    logger.info("=" * 60)

    if resolved_items is None:
        resolved_items = load_json_safe(RESOLVED_URLS_FILE, [])

    if not resolved_items:
        logger.error(f"No resolved items found in memory or at {RESOLVED_URLS_FILE}")
        return []

    # Initialize Gemini client
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    client = genai.Client(api_key=api_key) if api_key else None

    # Load persistent cache (url -> clean_content)
    persistent_cache: Dict[str, str] = load_json_safe(SCRAPED_CACHE_FILE, {})

    # Filter items that need network scraping
    urls_to_fetch = {}
    for idx, item in enumerate(resolved_items):
        url = item.get("URL", "")
        if url and not persistent_cache.get(url):
            urls_to_fetch[url] = idx

    logger.info(f"Total items: {len(resolved_items)} | URLs to fetch: {len(urls_to_fetch)} (Cached: {len(resolved_items) - len(urls_to_fetch)})")

    # Step 3A: Concurrent Network Scraping (I/O bound)
    raw_texts: Dict[str, Optional[str]] = {}
    if urls_to_fetch:
        logger.info(f"🚀 Running concurrent scraper with {max_workers} worker threads...")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(extract_raw_text_from_url, url): url for url in urls_to_fetch}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    raw_texts[url] = future.result()
                except Exception as e:
                    logger.debug(f"Failed to fetch {url[:40]}: {e}")
                    raw_texts[url] = None

    # Step 3B: AI Editorial Cleaning (Rate-limited via gemini_limiter)
    processed_articles: List[Dict[str, Any]] = []

    for idx, item in enumerate(resolved_items, 1):
        headline = item.get("headline", "")
        url = item.get("URL", "")
        source = item.get("source", "")
        date_str = item.get("date", "")

        if not url:
            processed_articles.append({
                "source": source,
                "headline": headline,
                "URL": "",
                "date": date_str,
                "content": ""
            })
            continue

        # Check cache (only non-empty cached content)
        cached_content = persistent_cache.get(url)
        if cached_content:
            content = cached_content
            logger.info(f"[{idx}/{len(resolved_items)}] ⏩ (Cached Article) {headline[:45]}...")
        else:
            raw_text = raw_texts.get(url)
            if raw_text and client:
                logger.info(f"[{idx}/{len(resolved_items)}] ✨ Cleaning with AI: {headline[:45]}...")
                content = clean_content_with_ai(client, raw_text, headline)
            elif raw_text:
                content = raw_text
            else:
                logger.warning(f"[{idx}/{len(resolved_items)}] ⚠️ No text extracted for {url[:50]}")
                content = ""

            # Only cache non-empty content so failures can be retried on next run
            if content:
                persistent_cache[url] = content
                if len(persistent_cache) > 500:
                    persistent_cache = dict(list(persistent_cache.items())[-500:])
                save_json_atomic(SCRAPED_CACHE_FILE, persistent_cache)

        processed_articles.append({
            "source": source,
            "headline": headline,
            "URL": url,
            "date": date_str,
            "content": content
        })

        # Save per-run output
        save_json_atomic(SCRAPED_ARTICLES_FILE, processed_articles)

    success_count = len([a for a in processed_articles if a.get("content")])
    logger.info(f"✅ Scraping completed! {success_count}/{len(resolved_items)} articles saved with content.")
    return processed_articles


if __name__ == "__main__":
    scrape_and_clean_articles()
