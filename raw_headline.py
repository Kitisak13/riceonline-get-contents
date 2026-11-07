# raw_headline.py - Direct RiceOnline Headline Extractor
# ========================================================

import html
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from config import RICEONLINE_URL, USER_AGENT, RAW_HEADLINES_FILE
from utils import (
    calculate_lookback_days,
    logger,
    parse_date_flexible,
    save_json_atomic,
)


def clean_headline_text(raw_text: str) -> str:
    """Removes HTML tags, decodes entities, and normalizes quotation marks."""
    if not raw_text:
        return ""
    # Unescape all HTML entities (&quot;, &#39;, &amp;, etc.)
    text = html.unescape(raw_text)
    # Strip residual HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Normalize curved/fancy quotes to standard quotes
    text = text.replace('“', '"').replace('”', '"').replace('’', "'").replace('‘', "'")
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_headlines_from_html(html_content: str, cutoff_date_str: str) -> List[Dict[str, str]]:
    """
    Parses riceonline.com DOM with BeautifulSoup:
    - Finds all date accordion sections (div.accordionButton)
    - Processes each section whose date >= cutoff_date (handles weekends properly)
    - Extracts article links (<a class="free_article_subscribers">)
    - Strips leading colons, stray quotes, and normalizes whitespace
    """
    soup = BeautifulSoup(html_content, "html.parser")
    accordion_buttons = soup.find_all("div", class_="accordionButton")

    if not accordion_buttons:
        logger.error("No date headers (.accordionButton) found on page")
        return []

    results: List[Dict[str, str]] = []
    seen_headlines = set()

    for btn in accordion_buttons:
        date_text = clean_headline_text(btn.get_text())
        parsed_date = parse_date_flexible(date_text)

        if not parsed_date:
            continue

        section_date_str = parsed_date.strftime("%Y-%m-%d")

        # Stop processing older sections if we've passed the cutoff date
        if cutoff_date_str and section_date_str < cutoff_date_str:
            logger.info(f"🛑 Reached section {section_date_str} which is before cutoff {cutoff_date_str}. Stopping.")
            break

        logger.info(f"📅 Processing date section: {date_text} ({section_date_str})")

        # Find the next sibling accordionContent
        content_div = btn.find_next_sibling("div", class_="accordionContent")
        if not content_div:
            continue

        links = content_div.find_all("a", class_="free_article_subscribers")
        for a_tag in links:
            raw_text = clean_headline_text(a_tag.get_text())

            # Split Source and Headline by the first colon ':'
            colon_index = raw_text.find(":")
            if colon_index == -1:
                continue

            source = raw_text[:colon_index].strip()
            headline_part = raw_text[colon_index + 1:].strip()

            # ข้ามรายการที่ไม่มีเครื่องหมายคำพูด (เพราะเป็นชื่อ Report เช่น "USA Rice Daily", ไม่ใช่ข่าวจริง)
            if '"' not in headline_part:
                continue

            # Clean leading colons, quotes, and whitespace
            headline = headline_part.lstrip(': \t').strip('\'" \t\n\r').strip()

            if headline and source:
                key = (source.lower(), headline.lower())
                if key not in seen_headlines:
                    seen_headlines.add(key)
                    results.append({
                        "source": source,
                        "headline": headline,
                        "date": section_date_str,
                    })

    return results


def fetch_headlines() -> List[Dict[str, str]]:
    """
    Fetches riceonline.com homepage, extracts headlines within cutoff window,
    saves to data/raw_headlines.json, and returns list.
    """
    logger.info("=" * 60)
    logger.info("🌾 RICE NEWS - Step 1: Fetching Headlines from riceonline.com")
    logger.info("=" * 60)

    lookback_days, lookback_msg = calculate_lookback_days()
    today = datetime.now()
    cutoff_date = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    logger.info(f"🗓️  {lookback_msg}")
    logger.info(f"🗓️  Cutoff date: {cutoff_date}")

    fetch_url = f"{RICEONLINE_URL}?_ts={int(time.time() * 1000)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache, max-age=0",
        "Pragma": "no-cache"
    }

    try:
        response = requests.get(fetch_url, headers=headers, timeout=15)
        response.raise_for_status()
        html_text = response.text
        logger.info(f"✅ Page fetched successfully: {len(html_text):,} bytes")
    except Exception as e:
        logger.error(f"❌ Failed to fetch riceonline: {e}")
        return []

    headlines = extract_headlines_from_html(html_text, cutoff_date)
    
    if headlines:
        save_json_atomic(RAW_HEADLINES_FILE, headlines)
        logger.info(f"✅ Extracted & Saved {len(headlines)} headlines to {RAW_HEADLINES_FILE}")
        return headlines

    logger.warning("No headlines extracted")
    return []


if __name__ == "__main__":
    items = fetch_headlines()
    print(f"Total headlines fetched: {len(items)}")
    for i, it in enumerate(items[:5], 1):
        print(f" {i}. [{it['source']}] {it['headline']} ({it['date']})")
