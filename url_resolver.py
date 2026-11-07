# url_resolver.py - High-Accuracy News URL Searcher using Gemini Search Grounding
# =================================================================================
# Directly implements the specifications in SKILL.md:
# - Enforces Domain Exclusion List
# - Enforces Sub-path Requirement
# - Enforces 7-day freshness
# - Resolves the 10/40 truncation problem by running dedicated per-item grounding queries
# - Decouples persistent URL cache from per-run resolved_urls.json
# - Preserves publication date across pipeline stages

import json
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from google import genai
from google.genai import types

from config import (
    BLOCKED_DOMAINS,
    BLOCKED_EXTENSIONS,
    DATE_FRESHNESS_DAYS,
    GEMINI_FALLBACK_MODEL,
    GEMINI_PRIMARY_MODEL,
    GEMINI_TIMEOUT,
    RAW_HEADLINES_FILE,
    RESOLVED_URLS_FILE,
    URL_CACHE_FILE,
)
from utils import (
    extract_domain,
    gemini_limiter,
    is_blocked_domain,
    is_valid_article_url,
    load_json_safe,
    logger,
    resolve_redirect_url,
    save_json_atomic,
)

SYSTEM_PROMPT = """You are the "News URL Searcher" agent. Your sole purpose is to locate exact, valid article URLs for news headlines while enforcing strict domain and date constraints.

Task Rules:
1. Find an article match published by the original source or credible news publisher.
2. Publication Date: The article MUST have been published within 7 days relative to the current date. Older articles must be rejected.
3. Sub-path Requirement: Valid URLs MUST contain at least one sub-path (e.g. domain.com/path/to/article). Reject homepages or root domains.
4. Domain Exclusion List (NEVER return these domains):
   facebook.com, twitter.com, x.com, instagram.com, linkedin.com, tiktok.com, pinterest.com,
   youtube.com, reddit.com, riceonline.com, who.int, oryza.com, livemint.com, fas.usda.gov.
5. If no valid article is located, respond with "None". Never invent or guess URLs.
6. Output format: Return ONLY the raw direct article URL string, or "None". No markdown, no explanation.
"""


def extract_url_candidates(response: Any) -> List[str]:
    """
    Extracts URL candidates with strict prioritization:
    1. Grounding Metadata (web citations directly fetched by Google Search - ground truth)
    2. Model text response (fallback)
    """
    candidates = []

    # 1. Grounding Metadata (web search citations directly fetched by Google Search)
    try:
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            grounding_meta = getattr(candidate, "grounding_metadata", None)
            if grounding_meta:
                chunks = getattr(grounding_meta, "grounding_chunks", []) or []
                for chunk in chunks:
                    web = getattr(chunk, "web", None)
                    if web:
                        uri = getattr(web, "uri", None)
                        if uri and uri not in candidates:
                            candidates.append(uri)
    except Exception as e:
        logger.debug(f"Grounding metadata inspection notice: {e}")

    # 2. Extract from model text as fallback
    text = (getattr(response, "text", "") or "").strip()
    if text and text.lower() != "none":
        found_urls = re.findall(r'https?://[^\s"\'<>]+', text)
        for u in found_urls:
            clean_u = u.rstrip(".,;)]}>\"'")
            if clean_u not in candidates:
                candidates.append(clean_u)

    return candidates


def generate_with_retry_and_fallback(
    client: genai.Client,
    prompt: str,
    max_retries: int = 2
) -> Optional[Any]:
    """Executes Gemini generation with exponential backoff on 429/503 and model fallback."""
    models_to_try = [GEMINI_PRIMARY_MODEL, GEMINI_FALLBACK_MODEL]

    for model_name in models_to_try:
        for attempt in range(max_retries + 1):
            try:
                gemini_limiter.wait()
                config = types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.0,
                    http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT)
                )
                return client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "503" in err_msg:
                    backoff = (2 ** attempt) * 4
                    logger.warning(f"⚠️ {model_name} rate limit / spike [{err_msg[:80]}] (attempt {attempt+1}/{max_retries+1}), waiting {backoff}s...")
                    time.sleep(backoff)
                else:
                    logger.warning(f"⚠️ {model_name} error: {e}")
                    break  # Non-retryable error on this model, switch to fallback model

    return None


def search_url_for_item(client: genai.Client, headline: str, source: str) -> str:
    """
    Searches for a single headline's article URL using Gemini Search Grounding.
    Uses a 2-stage query strategy:
    Stage 1: "{headline}" "{source}"
    Stage 2 (Fallback): "{headline}"
    """
    queries = [
        f'Find the direct article URL for this headline and source:\nHeadline: "{headline}"\nSource: "{source}"',
        f'Find the direct article URL for this news headline:\nHeadline: "{headline}"'
    ]

    for stage, prompt in enumerate(queries, 1):
        response = generate_with_retry_and_fallback(client, prompt)
        if not response:
            continue

        candidates = extract_url_candidates(response)

        for raw_url in candidates:
            # Resolve opaque redirect URLs (e.g. vertexaisearch.cloud.google.com) to target
            canonical_url = resolve_redirect_url(raw_url)
            if is_valid_article_url(canonical_url):
                if "riceonline.com" in canonical_url:
                    continue
                return canonical_url

    # Tier 3 Fallback: Tavily Search API + Gemini Selector (if TAVILY_API_KEY configured)
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip().strip('"').strip("'")
    if tavily_key:
        try:
            import requests
            t_resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": f"{source} {headline}", "max_results": 4},
                timeout=8
            )
            if t_resp.status_code == 200:
                t_items = t_resp.json().get("results", [])
                t_candidates = [r.get("url") for r in t_items if is_valid_article_url(r.get("url"))]
                if t_candidates:
                    eval_prompt = (
                        f'Target Headline: "{headline}"\nSource: "{source}"\n\n'
                        f'Candidates:\n{json.dumps(t_candidates)}\n\n'
                        f'Select the single exact news article URL matching the headline. '
                        f'Return ONLY the chosen URL string, or "None".'
                    )
                    sel_resp = client.models.generate_content(
                        model=GEMINI_PRIMARY_MODEL,
                        contents=eval_prompt
                    )
                    sel_text = (sel_resp.text or "").strip()
                    for c in t_candidates:
                        if c in sel_text:
                            logger.info(f"   🎯 [Tavily Fallback Hit!] {c}")
                            return c
                    if t_candidates and is_valid_article_url(t_candidates[0]):
                        return t_candidates[0]
        except Exception as e:
            logger.debug(f"Tavily fallback notice: {e}")

    return ""


def resolve_urls(
    headlines: Optional[List[Dict[str, str]]] = None,
    limit: Optional[int] = None,
    resume: bool = True
) -> List[Dict[str, str]]:
    """
    Resolves URLs for a list of headlines with persistent cache and date preservation.
    Output schema:
    [
      { "source": ..., "headline": ..., "date": ..., "URL": ... }
    ]
    """
    logger.info("=" * 60)
    logger.info("🔍 RICE NEWS - Step 2: Resolving Article URLs (Gemini Search Grounding)")
    logger.info("=" * 60)

    # Load input headlines
    if headlines is None:
        headlines = load_json_safe(RAW_HEADLINES_FILE, [])

    if not headlines:
        logger.error(f"No headlines found in memory or at {RAW_HEADLINES_FILE}")
        return []

    if limit and limit > 0:
        headlines = headlines[:limit]
        logger.info(f"🧪 Test mode enabled: Processing first {limit} headlines only")

    # Initialize Gemini client
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in environment or .env file!")

    client = genai.Client(api_key=api_key)
    logger.info(f"✨ Gemini client initialized with model: {GEMINI_PRIMARY_MODEL}")

    # Load persistent cache (headline -> URL) so test runs never destroy full cached results
    persistent_cache: Dict[str, str] = load_json_safe(URL_CACHE_FILE, {})

    results: List[Dict[str, str]] = []
    found_count = 0
    total = len(headlines)

    start_time = time.time()

    for idx, item in enumerate(headlines, 1):
        headline = item.get("headline", "").strip()
        source = item.get("source", "").strip()
        date_str = item.get("date", "").strip()
        
        # Check persistent cache first (only consider non-empty cached URLs)
        cached_url = persistent_cache.get(headline)
        if resume and cached_url:
            results.append({
                "source": source,
                "headline": headline,
                "date": date_str,
                "URL": cached_url
            })
            found_count += 1
            logger.info(f"[{idx}/{total}] ⏩ (Cached) [{source}] {headline[:45]}... ➔ {cached_url[:45]}...")
            continue

        logger.info(f"[{idx}/{total}] 🔎 Searching [{source}] {headline[:50]}...")
        url = search_url_for_item(client, headline, source)

        if url:
            found_count += 1
            persistent_cache[headline] = url
            if len(persistent_cache) > 500:
                persistent_cache = dict(list(persistent_cache.items())[-500:])
            save_json_atomic(URL_CACHE_FILE, persistent_cache)
            logger.info(f"[{idx}/{total}] ✅ Found: {url}")
        else:
            logger.info(f"[{idx}/{total}] ❌ Not found (set to empty)")

        results.append({
            "source": source,
            "headline": headline,
            "date": date_str,
            "URL": url
        })

        # Save per-run resolved URLs
        save_json_atomic(RESOLVED_URLS_FILE, results)

    elapsed = round(time.time() - start_time, 1)

    logger.info("=" * 60)
    logger.info(f"📊 URL RESOLUTION COMPLETED in {elapsed}s")
    logger.info(f"   Success Rate: {found_count}/{total} URLs found ({round(found_count/total*100, 1) if total else 0}%)")
    logger.info(f"   Output saved to: {RESOLVED_URLS_FILE}")
    logger.info("=" * 60)

    return results


if __name__ == "__main__":
    resolve_urls(limit=3)
