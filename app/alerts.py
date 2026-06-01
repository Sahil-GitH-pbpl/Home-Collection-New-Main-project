# app/routes/alerts.py

from flask import Blueprint
from app.db.connection import get_db_connection  # noqa: F401 (kept for future use)
import os
import requests
import re
import base64
from datetime import datetime
from zoneinfo import ZoneInfo
import threading
import logging
import uuid
from urllib.parse import urlparse, urlunparse

alerts_bp = Blueprint('alerts', __name__)
IST = ZoneInfo("Asia/Kolkata")

# =========================
# Config
# =========================
WHATSAPP_API_URL = os.getenv(
    "WHATSAPP_API_URL",
    "http://10.1.1.44:3004/api/messages/send",
).strip()
WHATSAPP_ACCOUNT_ID = 1
# Group ID ya mobile (local WA API format)
WHATSAPP_MOBILE = "917838104597-1635675661@g.us"
INTERNAL_REPORT_HOST = "10.1.1.252:8000"

# =========================
# Logger Setup
# =========================
logger = logging.getLogger(__name__)
if not logger.handlers:
    # Lightweight default logging setup in case app-wide config not present
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )


# =========================
# WhatsApp Send Helper
# =========================
def _normalize_wa_target(phone: str) -> str:
    target = (phone or "").strip()
    if target and "@g.us" not in target:
        # Heuristic: long numeric IDs are likely WhatsApp group IDs without suffix.
        if re.fullmatch(r"\d{16,}", target):
            return f"{target}@g.us"
        target = target.replace("+", "").replace(" ", "")
        while target.startswith("0"):
            target = target[1:]
        if len(target) == 10 and not target.startswith("91"):
            target = f"91{target}"
    return target


def _prefer_internal_report_url(file_url: str) -> str:
    """
    Keep report path/query but switch host to internal report server when URL is HTTP(S).
    """
    try:
        u = urlparse((file_url or "").strip())
        if u.scheme in ("http", "https") and u.path:
            return urlunparse((u.scheme, INTERNAL_REPORT_HOST, u.path, u.params, u.query, u.fragment))
    except Exception:
        pass
    return (file_url or "").strip()


def send_whatsapp_to_number(phone: str, message: str):
    """
    Local WhatsApp API wrapper with detailed logs.
    Returns: (status_code: int, response_text: str)
    """
    req_id = str(uuid.uuid4())[:8]  # short trace id for this attempt
    try:
        target = _normalize_wa_target(phone)

        logger.info(
            "WA[%s] → Preparing send | target=%s | len(message)=%s | accountId=%s",
            req_id, target, len(message or ""), WHATSAPP_ACCOUNT_ID
        )

        r = requests.post(
            WHATSAPP_API_URL,
            json={
                "accountId": WHATSAPP_ACCOUNT_ID,
                "target": target,
                "message": message
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=10
        )

        logger.info(
            "WA[%s] ← Response | status=%s | body=%s",
            req_id, r.status_code, (r.text[:500] if r.text else "")
        )

        return r.status_code, r.text

    except Exception as e:
        logger.exception("WA[%s] ❌ send_whatsapp_to_number exception: %s", req_id, e)
        return 500, str(e)


def send_whatsapp_document_to_number(phone: str, message: str, file_url: str, filename: str | None = None):
    """
    Download a document from URL and send it to WhatsApp target.
    Uses WA API contract: media.data (base64) + media.mimetype.
    Returns: (status_code: int, response_text: str)
    """
    req_id = str(uuid.uuid4())[:8]
    target = _normalize_wa_target(phone)
    url = (file_url or "").strip()
    download_url = _prefer_internal_report_url(url)
    if not target:
        return 400, "Empty WhatsApp target"
    if not url:
        return 400, "Empty file_url"

    try:
        dl = requests.get(download_url, timeout=25)
        if dl.status_code != 200:
            logger.error(
                "WA[%s] ❌ document download failed | download_url=%s | original_url=%s | status=%s",
                req_id, download_url, url, dl.status_code
            )
            return dl.status_code, f"Download failed: HTTP {dl.status_code}"
        content = dl.content or b""
        if not content:
            return 400, "Downloaded file is empty"
        if len(content) > 15 * 1024 * 1024:
            return 413, "File too large (>15MB) for WhatsApp send"

        if not filename:
            path_name = (urlparse(url).path or "").split("/")[-1]
            filename = path_name or "report.pdf"
        if "." not in filename:
            filename = f"{filename}.pdf"

        content_type = dl.headers.get("Content-Type") or "application/pdf"
        logger.info(
            "WA[%s] → doc send prepare | target=%s | file=%s | bytes=%s | content_type=%s | download_url=%s | original_url=%s",
            req_id, target, filename, len(content), content_type, download_url, url
        )

        payload = {
            "accountId": WHATSAPP_ACCOUNT_ID,
            "target": target,
            "message": message,
            "media": {
                "data": base64.b64encode(content).decode("ascii"),
                "mimetype": content_type,
                "filename": filename,
            },
        }
        r = requests.post(
            WHATSAPP_API_URL,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30,
        )
        logger.info(
            "WA[%s] ← doc send | status=%s | body=%s",
            req_id, r.status_code, (r.text[:500] if r.text else "")
        )
        return r.status_code, (r.text or "")
    except Exception as e:
        logger.exception("WA[%s] ❌ send_whatsapp_document_to_number exception: %s", req_id, e)
        return 500, str(e)


# =========================
# Lead Alert Message Builder
# =========================
def _build_lead_message(
    lead_id: str,
    phone: str,
    wa_only: int,
    name: str,
    alt_phone: str,
    visit_window: str,
    tags: str,
    num_patients: str,
    remarks: str,
    created_by: str
) -> str:
    """
    WhatsApp-friendly message for new lead creation alert.
    """
    now = datetime.now(IST).strftime("%d-%b-%Y %I:%M %p")
    lines = [
        "✨🧪🧬 *Fresh Lead Captured!* 🧬🧪✨",
        f"*Name:* {name or '-'}",
        f"*Phone:* {phone or '-'}{' (WA only)' if wa_only else ''}",
        *( [f"*Alt Phone:* {alt_phone}"] if alt_phone else [] ),
        f"*Visit Window:* {visit_window or '-'}",
        f"*Patients:* {num_patients or '1'}",
        f"*Tags:* {tags or '-'}",
        f"*Remarks:* {remarks or '-'}",
        f"*Created By:* {created_by or '-'}",
        f"_Time:_ {now}"
    ]
    return "\n".join(lines)


# =========================
# Async Notifier
# =========================
def notify_new_lead_async(**lead):
    """
    Fire-and-forget thread to keep user redirect fast.

    Expected keys in **lead:
        lead_id, phone, wa_only, name, alt_phone, visit_window,
        tags, num_patients, remarks, created_by
    """
    def _worker():
        try:
            msg = _build_lead_message(
                lead_id=lead.get("lead_id", ""),
                phone=lead.get("phone", ""),
                wa_only=int(lead.get("wa_only") or 0),
                name=lead.get("name", ""),
                alt_phone=lead.get("alt_phone", ""),
                visit_window=lead.get("visit_window", ""),
                tags=lead.get("tags", ""),
                num_patients=lead.get("num_patients", "1"),
                remarks=lead.get("remarks", ""),
                created_by=lead.get("created_by", "")
            )

            logger.info(
                "LeadAlert → Sending to WA group/number=%s | lead_id=%s | msg_len=%s",
                WHATSAPP_MOBILE, lead.get("lead_id", ""), len(msg)
            )

            status, resp = send_whatsapp_to_number(WHATSAPP_MOBILE, msg)

            if status in (200, 201):
                logger.info("LeadAlert ✅ Sent | status=%s | resp=%s", status, (resp[:300] if resp else ""))
            else:
                logger.error("LeadAlert ❌ Failed | status=%s | resp=%s", status, (resp[:500] if resp else ""))

        except Exception as e:
            logger.exception("WA lead alert exception: %s", e)

    threading.Thread(target=_worker, daemon=True).start()
