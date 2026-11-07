# drive_uploader.py - Google Drive Upload Module for Rice News Pipeline
# ======================================================================

import base64
import os
from typing import Any, Dict, Optional

import requests

from config import GDRIVE_WEBHOOK_URL
from utils import logger


def upload_report_to_drive(
    file_path: str,
    webhook_url: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Uploads a generated Word report (.docx) to Google Drive via Google Apps Script Web App.
    
    Args:
        file_path: Absolute or relative path to the .docx file to upload.
        webhook_url: Optional custom webhook URL. Defaults to GDRIVE_WEBHOOK_URL from config.
        
    Returns:
        Dict with upload result (e.g. {'status': 'success', 'file_url': '...', 'file_id': '...'})
        or None if upload failed or was skipped.
    """
    url = webhook_url or GDRIVE_WEBHOOK_URL
    if not url:
        logger.warning("⚠️ Skipping Google Drive upload: GDRIVE_WEBHOOK_URL is not configured.")
        return None

    if not os.path.exists(file_path):
        logger.error(f"❌ Upload failed: File not found at '{file_path}'")
        return None

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        logger.error(f"❌ Upload failed: File is empty (0 bytes) at '{file_path}'")
        return None

    filename = os.path.basename(file_path)
    logger.info("=" * 60)
    logger.info(f"☁️ RICE NEWS - Step 5: Uploading Report to Google Drive")
    logger.info(f"   Target File: {filename} ({file_size:,} bytes)")
    logger.info("=" * 60)

    try:
        with open(file_path, "rb") as f:
            encoded_content = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "filename": filename,
            "content_base64": encoded_content,
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        }

        # Google Apps Script Web App redirects 302 to script.googleusercontent.com on response
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=90,
            allow_redirects=True
        )

        if response.status_code == 200:
            try:
                res_data = response.json()
            except Exception:
                res_data = {"status": "success", "raw": response.text}

            file_url = res_data.get("file_url") or res_data.get("url") or ""
            file_id = res_data.get("file_id") or res_data.get("id") or ""

            if res_data.get("status") == "error":
                logger.error(f"❌ Google Drive upload reported error: {res_data.get('message')}")
                return None

            logger.info("✅ Report uploaded to Google Drive successfully!")
            if file_url:
                logger.info(f"   🔗 Drive URL: {file_url}")
            elif file_id:
                logger.info(f"   🆔 File ID: {file_id}")

            return res_data
        else:
            logger.error(f"❌ Google Drive upload HTTP error {response.status_code}: {response.text[:200]}")
            return None

    except requests.exceptions.Timeout:
        logger.error("❌ Google Drive upload timed out after 90 seconds.")
        return None
    except Exception as e:
        logger.error(f"❌ Google Drive upload unexpected error: {e}")
        return None

