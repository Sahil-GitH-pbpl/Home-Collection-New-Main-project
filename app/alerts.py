# app/routes/alerts.py

from flask import Blueprint
from app.db.connection import get_db_connection  # noqa: F401 (kept for future use)
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import threading
import logging
import uuid

alerts_bp = Blueprint('alerts', __name__)
IST = ZoneInfo("Asia/Kolkata")

# =========================
# Config
# =========================
WHATSAPP_API_URL = "http://192.168.0.71:3004/api/messages/send"
WHATSAPP_ACCOUNT_ID = 1
# Group ID ya mobile (local WA API format)
WHATSAPP_MOBILE = "917838104597-1635675661@g.us"

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
def send_whatsapp_to_number(phone: str, message: str):
    """
    Local WhatsApp API wrapper with detailed logs.
    Returns: (status_code: int, response_text: str)
    """
    req_id = str(uuid.uuid4())[:8]  # short trace id for this attempt
    try:
        target = (phone or "").strip()
        if target and "@g.us" not in target:
            target = target.replace("+", "").replace(" ", "")
            while target.startswith("0"):
                target = target[1:]
            if len(target) == 10 and not target.startswith("91"):
                target = f"91{target}"

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
