# pipeline.py - Unified Orchestrator for Rice Online News
# ==========================================================

import argparse
import os
import sys
import time
from datetime import datetime

from article_scraper import scrape_and_clean_articles
from docx_generator import create_report_document
from drive_uploader import upload_report_to_drive
from raw_headline import fetch_headlines
from url_resolver import resolve_urls
from utils import load_json_safe, logger, save_last_processed_date
from config import RAW_HEADLINES_FILE, RESOLVED_URLS_FILE, SCRAPED_ARTICLES_FILE


def run_pipeline(
    limit: int = 0,
    force: bool = False,
    skip_fetch: bool = False,
    skip_resolve: bool = False,
    skip_scrape: bool = False
):
    """
    Executes the full automated news pipeline end-to-end.
    """
    start_time = time.time()
    logger.info("=" * 70)
    logger.info("🚀 RICE ONLINE NEWS - AUTOMATED AGENTIC PIPELINE STARTING")
    logger.info(f"   Execution Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if limit > 0:
        logger.info(f"   Mode: TEST MODE (Limit {limit} headlines)")
    if force:
        logger.info("   Mode: FORCE RUN (Ignoring duplicate date checks)")
    logger.info("=" * 70)

    # -------------------------------------------------------------
    # STEP 1: Fetch Headlines from riceonline.com
    # -------------------------------------------------------------
    top_date = None
    if not skip_fetch:
        logger.info("\n>>> [STEP 1/5] Scraping Headlines from riceonline.com...")
        headlines, is_already_processed, top_date = fetch_headlines(force=force)
        if is_already_processed:
            logger.info("☕ No new news to process today. Pipeline completed successfully.")
            return
        if not headlines:
            logger.error("❌ Failed to fetch headlines from riceonline.com. Aborting.")
            sys.exit(1)
    else:
        logger.info("\n>>> [STEP 1/5] Skipping headline fetch, loading existing file...")
        headlines = load_json_safe(RAW_HEADLINES_FILE, [])
        top_date = headlines[0].get("date") if headlines else None
        if not headlines:
            logger.error(f"❌ No existing headlines found at {RAW_HEADLINES_FILE}. Aborting.")
            sys.exit(1)

    if limit > 0:
        headlines = headlines[:limit]

    # -------------------------------------------------------------
    # STEP 2: Resolve URLs using Gemini Search Grounding (SKILL.md)
    # -------------------------------------------------------------
    if not skip_resolve:
        logger.info("\n>>> [STEP 2/4] Resolving News Article URLs with Gemini Search Grounding...")
        resolved = resolve_urls(headlines=headlines, limit=limit if limit > 0 else None)
    else:
        logger.info("\n>>> [STEP 2/4] Skipping URL resolution, loading existing resolved file...")
        resolved = load_json_safe(RESOLVED_URLS_FILE, [])

    # -------------------------------------------------------------
    # STEP 3: Multi-Layer Scraping & AI Content Cleaning
    # -------------------------------------------------------------
    if not skip_scrape:
        logger.info("\n>>> [STEP 3/4] Scraping Articles & Cleaning Content with Gemini AI...")
        articles = scrape_and_clean_articles(resolved_items=resolved)
    else:
        logger.info("\n>>> [STEP 3/4] Skipping scraping, loading existing articles file...")
        articles = load_json_safe(SCRAPED_ARTICLES_FILE, [])

    # -------------------------------------------------------------
    # STEP 4: Generate Formatted Word Report (.docx)
    # -------------------------------------------------------------
    logger.info("\n>>> [STEP 4/4] Generating Word Document Report...")
    if not articles:
        logger.error("❌ No articles available to generate report. Aborting.")
        sys.exit(1)

    # Check that at least one article has content (prevent silent failure with empty documents)
    valid_articles = [a for a in articles if a.get("content")]
    if not valid_articles:
        logger.error("❌ No article bodies were successfully retrieved or cleaned. Aborting.")
        sys.exit(1)

    report_path = create_report_document(articles)

    # -------------------------------------------------------------
    # STEP 5: Upload Report to Google Drive (if configured)
    # -------------------------------------------------------------
    drive_result = upload_report_to_drive(report_path)

    # Save state of successfully processed date
    if top_date:
        save_last_processed_date(top_date, os.path.basename(report_path))

    duration = round(time.time() - start_time, 1)
    logger.info("\n" + "=" * 70)
    logger.info("🎉 PIPELINE COMPLETED SUCCESSFULLY!")
    logger.info(f"   ⏱️ Total Duration: {duration}s")
    logger.info(f"   📄 Local Report: {report_path}")
    if drive_result and drive_result.get("file_url"):
        logger.info(f"   ☁️ Drive URL: {drive_result.get('file_url')}")
    logger.info("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Rice Online News Aggregator Pipeline")
    parser.add_argument("--test", type=int, default=0, help="Run in test mode with first N headlines (e.g. --test 3)")
    parser.add_argument("--force", action="store_true", help="Force run even if today's news date was already processed")
    parser.add_argument("--step", type=str, choices=["all", "headlines", "resolve", "scrape", "report", "upload"], default="all", help="Specific step to run")
    args = parser.parse_args()

    if args.step == "all":
        run_pipeline(limit=args.test, force=args.force)
    elif args.step == "headlines":
        items, is_dup, top_date = fetch_headlines(force=args.force)
        if is_dup:
            logger.info("☕ No new news: Top section date already processed.")
        elif not items:
            sys.exit(1)
    elif args.step == "resolve":
        resolve_urls(limit=args.test if args.test > 0 else None)
    elif args.step == "scrape":
        scrape_and_clean_articles()
    elif args.step == "report":
        articles = load_json_safe(SCRAPED_ARTICLES_FILE, [])
        if not articles:
            sys.exit(1)
        path = create_report_document(articles)
        upload_report_to_drive(path)
    elif args.step == "upload":
        import glob
        from config import OUTPUT_DIR
        doc_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "*.docx")), key=os.path.getmtime, reverse=True)
        if not doc_files:
            logger.error(f"❌ No .docx files found in {OUTPUT_DIR} to upload.")
            sys.exit(1)
        latest_doc = doc_files[0]
        upload_report_to_drive(latest_doc)


if __name__ == "__main__":
    main()
