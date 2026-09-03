import json
import mimetypes
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory, session
from flask_socketio import join_room
from werkzeug.utils import secure_filename

from app.db.connection import get_db_connection, get_whatsapp_panel_connection
from app.extensions import socketio


whatsapp_panel_bp = Blueprint("whatsapp_panel", __name__)

WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER") or os.getenv("WA_PANEL_PROVIDER", "disabled")
NETCORE_URL = os.getenv("NETCORE_URL", "https://waapi.pepipost.com/api/v2/message/").strip()
NETCORE_MEDIA_URL = os.getenv("NETCORE_MEDIA_URL", "https://cpaaswa.netcorecloud.net/api/v2/media").strip()
NETCORE_TOKEN = os.getenv("NETCORE_TOKEN", "").strip()
NETCORE_SOURCE = os.getenv("NETCORE_SOURCE", "").strip()
NETCORE_IMAGE_TEMPLATE = os.getenv("NETCORE_IMAGE_TEMPLATE", "waba_image").strip()
NETCORE_DOCUMENT_TEMPLATE = os.getenv("NETCORE_DOCUMENT_TEMPLATE", "waba_pdf").strip()
NETCORE_IMAGE_TEMPLATE_PARAM_COUNT = int(os.getenv("NETCORE_IMAGE_TEMPLATE_PARAM_COUNT", "0"))
NETCORE_DOCUMENT_TEMPLATE_PARAM_COUNT = int(os.getenv("NETCORE_DOCUMENT_TEMPLATE_PARAM_COUNT", "0"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip()
WHATSAPP_INCOMING_TABLE = os.getenv("WA_PANEL_INCOMING_TABLE", "ofc_waba_incoming")
WHATSAPP_OUTGOING_TABLE = os.getenv("WA_PANEL_OUTGOING_TABLE", "ofc_waba_outgoing")
UPLOAD_ROOT = Path(__file__).resolve().parents[1] / "static" / "uploads" / "whatsapp"
INCOMING_UPLOAD_DIR = UPLOAD_ROOT / "incoming"
OUTGOING_UPLOAD_DIR = UPLOAD_ROOT / "outgoing"
ALLOWED_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "pdf"}


def qid(value):
    return f"`{str(value).replace('`', '``')}`"


def incoming_table():
    return qid(WHATSAPP_INCOMING_TABLE)


def outgoing_table():
    return qid(WHATSAPP_OUTGOING_TABLE)


def ensure_upload_dirs():
    INCOMING_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTGOING_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def init_whatsapp_panel_db():
    ensure_upload_dirs()
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {incoming_table()} (
                  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  mobile VARCHAR(45) NOT NULL,
                  msg TEXT NOT NULL,
                  img TEXT NOT NULL,
                  pdff VARCHAR(225) NOT NULL,
                  docid TEXT NOT NULL,
                  imgid TEXT NOT NULL,
                  empname VARCHAR(225) NOT NULL,
                  datetimess DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX idx_waba_incoming_mobile_date (mobile, datetimess),
                  INDEX idx_waba_incoming_mobile_id (mobile, id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {outgoing_table()} (
                  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  mobile VARCHAR(45) NOT NULL,
                  msg TEXT NOT NULL,
                  img TEXT NOT NULL,
                  pdff VARCHAR(225) NOT NULL,
                  docid TEXT NOT NULL,
                  imgid TEXT NOT NULL,
                  empname VARCHAR(225) NOT NULL,
                  datetimess DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  provider_message_id VARCHAR(191) NULL,
                  delivery_status VARCHAR(80) NULL,
                  delivery_status_remark TEXT NULL,
                  INDEX idx_waba_outgoing_mobile_date (mobile, datetimess),
                  INDEX idx_waba_outgoing_mobile_id (mobile, id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ofc_conversation_live_state (
                  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  mobile VARCHAR(45) NOT NULL UNIQUE,
                  owner_name VARCHAR(120) NULL,
                  conversation_type VARCHAR(80) NULL,
                  sla_started_at DATETIME NULL,
                  status VARCHAR(40) NOT NULL DEFAULT 'open',
                  closed_by_name VARCHAR(120) NULL,
                  closed_at DATETIME NULL,
                  closure_note TEXT NULL,
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                  INDEX idx_state_status (status),
                  INDEX idx_state_type (conversation_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ofc_conversation_audit (
                  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                  mobile VARCHAR(45) NOT NULL,
                  action_type VARCHAR(80) NOT NULL,
                  performed_by_name VARCHAR(120) NOT NULL,
                  old_owner_name VARCHAR(120) NULL,
                  new_owner_name VARCHAR(120) NULL,
                  old_value TEXT NULL,
                  new_value TEXT NULL,
                  reason TEXT NULL,
                  payload_json LONGTEXT NULL,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  INDEX idx_actions_mobile_created (mobile, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        conn.commit()
    finally:
        conn.close()


def current_user():
    user_id = session.get("user_id")
    username = (session.get("username") or "").strip()
    if not user_id or not username:
        return None
    return {"id": int(user_id), "username": username, "display_name": username}


def require_user():
    user = current_user()
    if not user:
        return None, (jsonify(ok=False, error="Not logged in"), 401)
    return user, None


@socketio.on("connect")
def socket_connect():
    user = current_user()
    if not user:
        return False
    join_room(f"user:{user['id']}")
    join_room("console")


def socket_safe_message(row):
    return serialize_datetime_values(row or {})


def local_datetime_string(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def serialize_datetime_values(value):
    if isinstance(value, dict):
        return {key: serialize_datetime_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_datetime_values(item) for item in value]
    return local_datetime_string(value)


def emit_whatsapp_event(event, payload):
    try:
        socketio.emit(event, payload, to="console")
    except Exception as exc:
        current_app.logger.warning("[whatsapp socket] emit failed for %s: %s", event, exc)


def normalize_mobile(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "-")[:45]


def public_url(path):
    base_url = PUBLIC_BASE_URL or request.url_root.rstrip("/")
    return f"{base_url.rstrip('/')}/{str(path).lstrip('/')}"


def mask_mobile(value):
    text = str(value or "")
    return text if len(text) <= 4 else f"{'*' * (len(text) - 4)}{text[-4:]}"


def outbound_whatsapp_mobile(value):
    digits = phone_digits(value)
    if len(digits) == 10:
        return f"91{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"91{digits[-10:]}"
    return digits or normalize_mobile(value)


def parse_payload_date(value):
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts)
    text = str(value or "").strip()
    if not text:
        return datetime.now()
    if text.isdigit():
        return parse_payload_date(int(text))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return datetime.now()


def log_panel_event(message, **data):
    current_app.logger.info("[whatsapp panel] %s %s", message, json.dumps(data, ensure_ascii=False, default=str))


def netcore_message_payload(mobile, msg, media, patient_name="Patient", template_name="", template_attributes=None):
    if template_name:
        return {
            "recipient_whatsapp": mobile,
            "message_type": "template",
            "recipient_type": "individual",
            "type_template": [
                {
                    "name": template_name,
                    "attributes": [str(x or "").strip() for x in (template_attributes or [])],
                    "language": {"locale": "en", "policy": "deterministic"},
                }
            ],
        }
    base = {
        "recipient_whatsapp": mobile,
        "recipient_type": "individual",
        "source": NETCORE_SOURCE,
        "x-apiheader": "custom_data",
    }
    if media:
        media_payload = {"type": media["kind"], "url": media["url"]}
        if media["kind"] == "document":
            media_payload["filename"] = media.get("filename") or "document.pdf"
        template_name = NETCORE_IMAGE_TEMPLATE if media["kind"] == "image" else NETCORE_DOCUMENT_TEMPLATE
        template_param_count = NETCORE_IMAGE_TEMPLATE_PARAM_COUNT if media["kind"] == "image" else NETCORE_DOCUMENT_TEMPLATE_PARAM_COUNT
        template = {
            "name": template_name,
            "language": {"locale": "en", "policy": "deterministic"},
        }
        if template_param_count:
            template["attributes"] = [patient_name]
        return {
            **base,
            "message_type": "media_template",
            "type_media_template": media_payload,
            "type_template": [template],
        }
    return {
        **base,
        "message_type": "text",
        "type_text": [{"preview_url": "false", "content": msg}],
    }


def send_via_provider(mobile, msg, media=None, template_name="", template_attributes=None):
    provider = WHATSAPP_PROVIDER
    recipient_mobile = outbound_whatsapp_mobile(mobile)
    log_panel_event(
        "send_attempt",
        provider=provider,
        mobile=mask_mobile(mobile),
        recipient=mask_mobile(recipient_mobile),
        messageLength=len(msg or ""),
        mediaKind=(media or {}).get("kind", ""),
        template=template_name,
    )
    if provider == "disabled":
        if template_name:
            raise ValueError("WhatsApp template provider is disabled")
        return {"skipped": True, "provider": "disabled"}
    if provider != "netcore":
        raise ValueError(f"Unknown WHATSAPP_PROVIDER: {provider}")
    token = NETCORE_TOKEN or (os.getenv("PEPIPOST_WA_TOKEN") or "").strip()
    if template_name and not token:
        raise ValueError("PEPIPOST_WA_TOKEN or NETCORE_TOKEN is required")
    if not template_name and (not token or not NETCORE_SOURCE):
        raise ValueError("NETCORE_TOKEN and NETCORE_SOURCE are required")

    session_obj = requests.Session()
    session_obj.trust_env = False
    response = session_obj.post(
        NETCORE_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": [netcore_message_payload(recipient_mobile, msg, media, template_name=template_name, template_attributes=template_attributes)]},
        timeout=30,
    )
    text = response.text or ""
    log_panel_event("netcore_response", status=response.status_code, ok=response.ok, body=text[:2000])
    response.raise_for_status()
    try:
        response_data = response.json()
    except ValueError:
        response_data = {}
    if str(response_data.get("status") or "").lower() not in {"success", "accepted"}:
        error = response_data.get("error") or {}
        code = error.get("code") or "unknown"
        detail = error.get("message") or response_data.get("message") or "Provider rejected the message"
        raise ValueError(f"Netcore rejected attachment/message ({code}): {detail}")
    provider_message_id = response_data.get("data", {}).get("id") or ""
    return {"provider": "netcore", "providerMessageId": provider_message_id, "response": text}


def phone_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def phone_variants(value):
    digits = phone_digits(value)
    if not digits:
        return set()
    meaningful = digits[-10:] if len(digits) >= 10 else digits
    variants = {digits, meaningful}
    if len(meaningful) == 10:
        variants.add(f"91{meaningful}")
    return {item for item in variants if item}


def day_bounds(date_text):
    day = datetime.strptime(date_text, "%Y-%m-%d")
    next_day = day + timedelta(days=1)
    return day.strftime("%Y-%m-%d 00:00:00"), next_day.strftime("%Y-%m-%d 00:00:00")


def today_bounds():
    return day_bounds(datetime.now().strftime("%Y-%m-%d"))


def upload_kind(filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        return ""
    return "document" if extension == "pdf" else "image"


def media_url(value):
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://", "/uploads/")) else ""


def normalize_media(data):
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    url = str(media.get("url") or data.get("media_url") or "").strip()
    filename = secure_filename(str(media.get("filename") or data.get("filename") or "").strip())[:225]
    kind = str(media.get("kind") or data.get("media_kind") or "").strip().lower()
    if not kind and filename:
        kind = upload_kind(filename)
    return {"kind": kind, "url": url, "filename": filename}


def download_netcore_media(media_id, kind, mime_type="", mobile=""):
    if not media_id or not NETCORE_TOKEN:
        return ""
    fallback_extension = {
        "document": ".pdf",
        "audio": ".ogg",
        "video": ".mp4",
    }.get(kind, ".jpg")
    extension = mimetypes.guess_extension(mime_type or "") or fallback_extension
    if extension == ".jpe":
        extension = ".jpg"
    if extension == ".oga":
        extension = ".ogg"
    INCOMING_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    mobile_prefix = normalize_mobile(mobile) or "unknown"
    saved_name = f"{mobile_prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{extension}"
    target = INCOMING_UPLOAD_DIR / saved_name
    session_obj = requests.Session()
    session_obj.trust_env = False
    response = session_obj.get(
        f"{NETCORE_MEDIA_URL.rstrip('/')}/{media_id}",
        headers={"Authorization": f"Bearer {NETCORE_TOKEN}"},
        timeout=30,
    )
    response.raise_for_status()
    target.write_bytes(response.content)
    return public_url(f"uploads/incoming/{saved_name}")


def normalize_incoming_payload(data):
    item = data.get("incoming_message", data)
    if isinstance(item, list):
        item = item[0] if item else {}
    if not isinstance(item, dict):
        raise ValueError("incoming_message array missing")

    mobile = str(item.get("from") or item.get("mobile") or item.get("phone") or "").strip()
    if not mobile:
        raise ValueError("incoming mobile/from missing")

    received_at = item.get("received_at") or item.get("timestamp") or int(datetime.now().timestamp())
    received_dt = parse_payload_date(received_at)
    text_type = item.get("text_type") if isinstance(item.get("text_type"), dict) else {}
    image_type = item.get("image_type") if isinstance(item.get("image_type"), dict) else {}
    video_type = item.get("video_type") if isinstance(item.get("video_type"), dict) else {}
    audio_type = item.get("audio_type") if isinstance(item.get("audio_type"), dict) else {}
    document_type = item.get("document_type") if isinstance(item.get("document_type"), dict) else {}

    image_id = str(image_type.get("id") or image_type.get("media_id") or "").strip()
    image_url = str(image_type.get("url") or image_type.get("link") or image_type.get("media_url") or item.get("image_url") or "").strip()
    document_id = str(document_type.get("id") or document_type.get("media_id") or "").strip()
    document_url = str(document_type.get("url") or document_type.get("link") or document_type.get("media_url") or item.get("document_url") or "").strip()
    document_name = str(document_type.get("filename") or document_type.get("name") or Path(document_url).name or "").strip()

    if image_id and not image_url:
        try:
            image_url = download_netcore_media(image_id, "image", str(image_type.get("mime_type") or "image/jpeg"), mobile)
        except Exception as exc:
            log_panel_event("incoming_image_download_failed", mediaId=image_id, error=str(exc))
    if document_id and not document_url:
        try:
            document_url = download_netcore_media(document_id, "document", str(document_type.get("mime_type") or "application/pdf"), mobile)
        except Exception as exc:
            log_panel_event("incoming_document_download_failed", mediaId=document_id, error=str(exc))

    return {
        "mobile": mobile,
        "msg": str(text_type.get("text") or item.get("text") or item.get("msg") or image_type.get("caption") or video_type.get("caption") or document_type.get("caption") or audio_type.get("caption") or ""),
        "img": image_url or str(image_type.get("sha256") or ""),
        "pdff": document_name or (Path(document_url).name if document_url else ""),
        "docid": document_url or document_id,
        "imgid": image_url or image_id,
        "received_at": str(received_at),
        "received_datetime": received_dt,
    }


def normalize_delivery_payload(data):
    item = data.get("delivery_status", data)
    if isinstance(item, list):
        item = item[0] if item else {}
    if not isinstance(item, dict):
        raise ValueError("delivery_status array missing")
    nested = item.get("data") if isinstance(item.get("data"), dict) else {}
    provider_message_id = str(item.get("ncmessage_id") or item.get("message_id") or item.get("id") or nested.get("id") or "").strip()
    if not provider_message_id:
        raise ValueError("provider message id missing")
    return {
        "provider_message_id": provider_message_id,
        "status": str(item.get("status") or item.get("delivery_status") or "").strip(),
        "status_remark": str(item.get("status_remark") or item.get("remark") or item.get("reason") or "").strip(),
        "received_at": str(item.get("received_at") or item.get("timestamp") or item.get("created_at") or datetime.now().isoformat()).strip(),
    }


def message_union_sql():
    return f"""
        SELECT id, mobile, msg, img, pdff, docid, imgid, empname, datetimess,
               'red' AS color, NULL AS provider_message_id, NULL AS delivery_status, NULL AS delivery_status_remark
        FROM {incoming_table()}
        UNION ALL
        SELECT id, mobile, msg, img, pdff, docid, imgid, empname, datetimess,
               'green' AS color, provider_message_id, delivery_status, delivery_status_remark
        FROM {outgoing_table()}
    """


def apply_sla_fields(rows):
    now = datetime.now()
    for row in rows or []:
        last_incoming = row.get("last_incoming_at")
        last_user_reply = row.get("last_user_reply_at")
        sla_started_at = row.get("sla_started_at") or last_incoming
        closed = row.get("workflow_status") == "closed"
        pending = bool(last_incoming and not closed and (not last_user_reply or last_incoming > last_user_reply))
        row["sla_pending"] = pending
        row["sla_started_at"] = sla_started_at
        row["sla_minutes"] = max(0, round((now - sla_started_at).total_seconds() / 60)) if pending and sla_started_at else 0
    return rows


def dedupe_by_id(rows):
    seen = set()
    result = []
    for row in rows or []:
        row_id = row.get("id")
        key = row_id if row_id is not None else json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def mobile_clean_sql(column_name):
    parts = str(column_name).split(".")
    column = ".".join(qid(part) for part in parts)
    return (
        f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({column}, ' ', ''), '-', ''), '+', ''), '.', ''), '(', '')"
    )


def attach_patient_names(rows):
    if not rows:
        return rows
    variants_by_mobile = {row["mobile"]: phone_variants(row["mobile"]) for row in rows if row.get("mobile")}
    all_variants = sorted({variant for variants in variants_by_mobile.values() for variant in variants})
    if not all_variants:
        return rows
    placeholders = ", ".join(["%s"] * len(all_variants))
    sql = f"""
        SELECT id, patient_code, full_name, contact_mobile, alternate_mobile
        FROM hpatient_master
        WHERE {mobile_clean_sql("contact_mobile")} IN ({placeholders})
           OR {mobile_clean_sql("alternate_mobile")} IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        LIMIT 500
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, all_variants + all_variants)
            patients = cur.fetchall()
    finally:
        conn.close()
    contact_matches = {}
    alternate_matches = {}
    for patient in patients:
        for variant in phone_variants(patient.get("contact_mobile")):
            contact_matches.setdefault(variant, patient)
        for variant in phone_variants(patient.get("alternate_mobile")):
            alternate_matches.setdefault(variant, patient)
    for row in rows:
        matched = None
        for variant in variants_by_mobile.get(row.get("mobile"), set()):
            matched = contact_matches.get(variant) or alternate_matches.get(variant)
            if matched:
                break
        row["patient_name"] = matched.get("full_name") if matched else ""
        row["patient_code"] = matched.get("patient_code") if matched else ""
    return rows


def patient_mobile_variants_for_name_search(query):
    query = str(query or "").strip()
    if len(query) < 2:
        return []
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT contact_mobile, alternate_mobile
                FROM hpatient_master
                WHERE full_name LIKE %s
                LIMIT 500
                """,
                (f"%{query}%",),
            )
            patients = cur.fetchall()
    finally:
        conn.close()
    return sorted({
        variant
        for patient in patients
        for mobile in (patient.get("contact_mobile"), patient.get("alternate_mobile"))
        for variant in phone_variants(mobile)
    })


def resolve_caller(cur, variants):
    if not variants:
        return None
    params = list(variants)
    placeholders = ", ".join(["%s"] * len(params))
    cur.execute(
        f"""
        SELECT c.id, c.caller_code, c.full_name, c.primary_mobile, c.alternate_mobile,
               c.email, c.caller_status, c.active, c.created_at, c.updated_at
        FROM hcaller_mobile_map mm
        INNER JOIN hcaller_master c ON c.id = mm.caller_id
        WHERE mm.is_active = 1 AND mm.mobile_norm IN ({placeholders})
        ORDER BY mm.id DESC
        LIMIT 1
        """,
        params,
    )
    caller = cur.fetchone()
    if caller:
        return caller
    cur.execute(
        f"""
        SELECT id, caller_code, full_name, primary_mobile, alternate_mobile,
               email, caller_status, active, created_at, updated_at
        FROM hcaller_master
        WHERE {mobile_clean_sql("primary_mobile")} IN ({placeholders})
           OR {mobile_clean_sql("alternate_mobile")} IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        params + params,
    )
    return cur.fetchone()


def fetch_linked_patients(cur, caller_id):
    if not caller_id:
        return []
    cur.execute(
        """
        SELECT p.id, p.patient_code, p.title, p.full_name, p.contact_mobile, p.alternate_mobile, p.tag,
               l.created_at AS linked_at
        FROM hcaller_patient_link l
        INNER JOIN hpatient_master p ON p.id = l.patient_id
        WHERE l.caller_id = %s AND l.is_active = 1
        ORDER BY l.created_at DESC, p.full_name ASC
        """,
        (caller_id,),
    )
    return dedupe_by_id(cur.fetchall())


def fetch_patient_addresses(cur, patient_ids):
    patient_ids = [item for item in patient_ids if item]
    if not patient_ids:
        return []
    placeholders = ", ".join(["%s"] * len(patient_ids))
    cur.execute(
        f"""
        SELECT pal.patient_id, pal.address_id, pal.is_default,
               a.house_flat_no, a.floor, a.block_tower_no, a.street_line,
               a.landmark, a.city, a.colony_name, a.pincode, a.route_no
        FROM hpatient_address_link pal
        INNER JOIN haddress_master a ON a.id = pal.address_id
        WHERE pal.patient_id IN ({placeholders}) AND pal.is_active = 1
        ORDER BY pal.is_default DESC, a.created_at DESC
        """,
        patient_ids,
    )
    return cur.fetchall()


def fetch_reference_addresses(cur, caller_id):
    if not caller_id:
        return []
    cur.execute(
        """
        SELECT id, caller_id, area, city, pincode, routename, address, created_at, updated_at
        FROM hcaller_reference_address
        WHERE caller_id = %s AND (status IS NULL OR status != 'removed')
        ORDER BY updated_at DESC, created_at DESC
        """,
        (caller_id,),
    )
    return dedupe_by_id(cur.fetchall())


def fetch_home_collection_bookings(cur, caller_id):
    if not caller_id:
        return []
    cur.execute(
        """
        SELECT b.id, b.booking_code, b.caller_id, b.preferred_visit_date, b.preferred_time_slot,
               b.booking_status, b.created_at, b.lead_id, b.remarks, b.paying_amount, b.total_amount,
               COALESCE(NULLIF(TRIM(u.name), ''), '-') AS assigned_phlebotomist
        FROM hhome_collection_booking b
        LEFT JOIN users u ON u.id = b.assigned_phlebotomist_id
        WHERE b.caller_id = %s
          AND b.created_at >= NOW() - INTERVAL 10 DAY
        ORDER BY b.created_at DESC, b.id DESC
        LIMIT 100
        """,
        (caller_id,),
    )
    bookings = dedupe_by_id(cur.fetchall())
    booking_ids = [row["id"] for row in bookings if row.get("id")]
    if not booking_ids:
        return bookings
    placeholders = ", ".join(["%s"] * len(booking_ids))
    cur.execute(
        f"""
        SELECT bp.booking_id, bp.patient_id, bp.booking_patient_status, bp.cancel_reason, bp.cancel_remark,
               p.patient_code, p.full_name
        FROM hhome_collection_booking_patient bp
        LEFT JOIN hpatient_master p ON p.id = bp.patient_id
        WHERE bp.booking_id IN ({placeholders})
        ORDER BY bp.booking_id DESC, bp.id ASC
        """,
        booking_ids,
    )
    patient_map = {}
    for row in cur.fetchall():
        patient_map.setdefault(row["booking_id"], []).append(row)
    for booking in bookings:
        booking["patients"] = patient_map.get(booking["id"], [])
    return bookings


def fetch_leads_by_mobile(cur, variants):
    if not variants:
        return []
    params = list(variants)
    placeholders = ", ".join(["%s"] * len(params))
    cur.execute(
        f"""
        SELECT l.id, l.lead_id, l.name, l.phone, l.alt_phone, l.status, l.tags,
               l.remarks, l.visit_window, l.created_by, COALESCE(u.name, l.created_by) AS created_by_name, l.created_at
        FROM leads l
        LEFT JOIN users u ON l.created_by REGEXP '^[0-9]+$' AND u.id = CAST(l.created_by AS UNSIGNED)
        WHERE (
            {mobile_clean_sql("l.phone")} IN ({placeholders})
            OR {mobile_clean_sql("l.alt_phone")} IN ({placeholders})
        )
          AND l.created_at >= NOW() - INTERVAL 10 DAY
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT 100
        """,
        params + params,
    )
    return dedupe_by_id(cur.fetchall())


def fetch_tickets_by_mobile(cur, variants):
    if not variants:
        return []
    params = list(variants)
    placeholders = ", ".join(["%s"] * len(params))
    cur.execute(
        f"""
        SELECT id, ticket_origin, ticket_category, patient_name, client_name,
               mobile_number, status, created_at, commitment_at
        FROM tickets
        WHERE {mobile_clean_sql("mobile_number")} IN ({placeholders})
          AND created_at >= NOW() - INTERVAL 10 DAY
        ORDER BY created_at DESC, id DESC
        LIMIT 100
        """,
        params,
    )
    return dedupe_by_id(cur.fetchall())


def unified_mobile_lookup(mobile, sections=None):
    allowed_sections = {"contact", "hc", "leads", "tickets"}
    requested_sections = set(sections or allowed_sections) & allowed_sections
    variants = phone_variants(mobile)
    normalized_mobile = next((item for item in sorted(variants, key=len) if len(item) == 10), phone_digits(mobile))
    response = {
        "ok": True,
        "search_mobile": str(mobile or ""),
        "normalized_mobile": normalized_mobile,
        "mobile_variants": sorted(variants),
        "sections": sorted(requested_sections),
        "caller": None,
        "linked_patients": [],
        "home_collection_bookings": [],
        "leads": [],
        "tickets": [],
    }
    if not variants:
        return response
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            caller = None
            if {"contact", "hc"} & requested_sections:
                caller = resolve_caller(cur, variants)
                response["caller"] = caller
            if caller and "contact" in requested_sections:
                response["linked_patients"] = fetch_linked_patients(cur, caller["id"])
            if caller and "hc" in requested_sections:
                response["home_collection_bookings"] = fetch_home_collection_bookings(cur, caller["id"])
            if "leads" in requested_sections:
                response["leads"] = fetch_leads_by_mobile(cur, variants)
            if "tickets" in requested_sections:
                response["tickets"] = fetch_tickets_by_mobile(cur, variants)
    finally:
        conn.close()
    return response


def ensure_live_conversation_state(cur, mobile):
    cur.execute(
        """
        INSERT INTO ofc_conversation_live_state (mobile, status)
        VALUES (%s, 'open')
        ON DUPLICATE KEY UPDATE mobile = VALUES(mobile)
        """,
        (mobile,),
    )
    cur.execute("SELECT * FROM ofc_conversation_live_state WHERE mobile = %s", (mobile,))
    return cur.fetchone()


def log_conversation_action(cur, mobile, action_type, user, old_state=None, new_owner=None, old_value=None, new_value=None, reason="", payload=None):
    old_state = old_state or {}
    new_owner = new_owner or {}
    cur.execute(
        """
        INSERT INTO ofc_conversation_audit
          (mobile, action_type, performed_by_name, old_owner_name, new_owner_name, old_value, new_value, reason, payload_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            mobile,
            action_type,
            user["display_name"],
            old_state.get("owner_name"),
            new_owner.get("display_name"),
            old_value,
            new_value,
            reason,
            json.dumps(payload or {}, ensure_ascii=False),
        ),
    )


def ensure_current_owner(state_row, user):
    if state_row.get("status") == "closed":
        return "Conversation is closed"
    if not state_row.get("owner_name"):
        return "Chat is unassigned. Take ownership before replying."
    if state_row.get("owner_name") != user["display_name"]:
        return f"Only current owner can perform this action. Current owner: {state_row.get('owner_name') or 'another user'}"
    return ""


def reopen_closed_conversation_for_incoming(cur, mobile):
    state_row = ensure_live_conversation_state(cur, mobile)
    if state_row.get("status") != "closed":
        return False
    cur.execute(
        """
        UPDATE ofc_conversation_live_state
        SET owner_name = NULL,
            status = 'open',
            sla_started_at = NOW()
        WHERE mobile = %s
        """,
        (mobile,),
    )
    log_conversation_action(
        cur,
        mobile,
        "reopen_for_incoming",
        {"display_name": "System"},
        old_state=state_row,
        new_value="open",
        payload={"closed_at": str(state_row.get("closed_at") or "")},
    )
    return True


def has_recent_conversation_activity(cur, mobile, hours=24):
    cutoff = datetime.now() - timedelta(hours=hours)
    cur.execute(
        f"""
        SELECT MAX(last_at) AS last_at
        FROM (
          SELECT MAX(datetimess) AS last_at FROM {incoming_table()} WHERE mobile = %s
          UNION ALL
          SELECT MAX(datetimess) AS last_at FROM {outgoing_table()} WHERE mobile = %s
        ) activity
        """,
        (mobile, mobile),
    )
    row = cur.fetchone() or {}
    last_at = row.get("last_at")
    return bool(last_at and last_at >= cutoff)


def release_unanswered_ownerships():
    cutoff = datetime.now() - timedelta(minutes=15)
    released = []
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT cs.mobile, cs.owner_name, cs.status, cs.sla_started_at,
                       incoming.last_incoming_at, outgoing.last_owner_reply_at
                FROM ofc_conversation_live_state cs
                LEFT JOIN (
                  SELECT mobile, MAX(datetimess) AS last_incoming_at
                  FROM {incoming_table()}
                  GROUP BY mobile
                ) incoming ON incoming.mobile = cs.mobile
                LEFT JOIN (
                  SELECT mobile, empname, MAX(datetimess) AS last_owner_reply_at
                  FROM {outgoing_table()}
                  GROUP BY mobile, empname
                ) outgoing ON outgoing.mobile = cs.mobile AND outgoing.empname = cs.owner_name
                WHERE cs.owner_name IS NOT NULL AND cs.status <> 'closed'
                """
            )
            rows = cur.fetchall()
            for state_row in rows:
                last_incoming_at = state_row.get("last_incoming_at")
                last_owner_reply_at = state_row.get("last_owner_reply_at")
                timer_start = state_row.get("sla_started_at") or last_incoming_at
                if not timer_start or timer_start > cutoff:
                    continue
                if last_owner_reply_at and last_incoming_at and last_owner_reply_at >= last_incoming_at:
                    continue
                cur.execute(
                    """
                    UPDATE ofc_conversation_live_state
                    SET owner_name = NULL, status = 'open', sla_started_at = NOW()
                    WHERE mobile = %s AND owner_name = %s AND status <> 'closed'
                    """,
                    (state_row["mobile"], state_row["owner_name"]),
                )
                if cur.rowcount:
                    released.append(
                        {
                            "mobile": state_row["mobile"],
                            "owner_name": state_row["owner_name"],
                        }
                    )
                    log_conversation_action(
                        cur,
                        state_row["mobile"],
                        "auto_release_ownership",
                        {"display_name": "System"},
                        old_state=state_row,
                        new_value="unassigned",
                        reason="Ownership automatically released after 15 minutes without a reply",
                    )
        conn.commit()
    finally:
        conn.close()
    if released:
        emit_whatsapp_event("ownership_released", {"count": len(released), "items": released})


def fetch_operator_rows(query):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if query and len(query.strip()) >= 2:
                needle = f"%{query.strip()}%"
                cur.execute(
                    """
                    SELECT id, name, designation
                    FROM users
                    WHERE LOWER(TRIM(status)) = 'active'
                      AND LOWER(name) LIKE LOWER(%s)
                    ORDER BY name ASC
                    LIMIT 10
                    """,
                    (needle,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, name, designation
                    FROM users
                    WHERE LOWER(TRIM(status)) = 'active'
                    ORDER BY name ASC
                    LIMIT 10
                    """
                )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "username": row["name"],
            "display_name": row["name"],
            "role": row.get("designation") or "",
        }
        for row in rows
    ]


def fetch_operator_by_id(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, designation
                FROM users
                WHERE id = %s
                  AND LOWER(TRIM(status)) = 'active'
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["name"],
        "display_name": row["name"],
        "role": row.get("designation") or "",
    }


@whatsapp_panel_bp.route("/whatsapp-panel")
def whatsapp_panel_page():
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    return render_template(
        "whatsapp_panel.html",
        provider=WHATSAPP_PROVIDER,
        current_user=user,
    )


@whatsapp_panel_bp.route("/uploads/<path:filename>")
def whatsapp_uploaded_file(filename):
    init_whatsapp_panel_db()
    return send_from_directory(UPLOAD_ROOT, filename)


@whatsapp_panel_bp.route("/api/uploads", methods=["POST"])
def whatsapp_upload_file():
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify(ok=False, error="File missing"), 400
    original_name = secure_filename(file.filename)
    kind = upload_kind(original_name)
    if not kind:
        return jsonify(ok=False, error="Only JPG, PNG, WEBP, and PDF files are allowed"), 400
    saved_name = f"{uuid.uuid4().hex}_{original_name}"
    file.save(OUTGOING_UPLOAD_DIR / saved_name)
    url_path = f"uploads/outgoing/{saved_name}"
    return jsonify(
        ok=True,
        media={
            "kind": kind,
            "filename": original_name,
            "url": public_url(url_path),
            "path": f"/uploads/outgoing/{saved_name}",
        },
    )


@whatsapp_panel_bp.route("/api/stats")
def whatsapp_stats():
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    today_start, tomorrow_start = today_bounds()
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM {incoming_table()}")
            incoming = cur.fetchone()["total"] or 0
            cur.execute(f"SELECT COUNT(*) AS total FROM {outgoing_table()}")
            outgoing = cur.fetchone()["total"] or 0
            cur.execute(
                f"""
                SELECT
                  (SELECT COUNT(*) FROM {incoming_table()} WHERE datetimess >= %s AND datetimess < %s)
                  + (SELECT COUNT(*) FROM {outgoing_table()} WHERE datetimess >= %s AND datetimess < %s) AS total
                """,
                (today_start, tomorrow_start, today_start, tomorrow_start),
            )
            today = cur.fetchone()["total"] or 0
    finally:
        conn.close()
    return jsonify(ok=True, total=incoming + outgoing, today=today, incoming=incoming, outgoing=outgoing, provider=WHATSAPP_PROVIDER)


@whatsapp_panel_bp.route("/api/spellcheck", methods=["POST"])
def whatsapp_spellcheck():
    user, error = require_user()
    if error:
        return error
    return jsonify(ok=True, suggestions=[])


@whatsapp_panel_bp.route("/api/autocomplete", methods=["POST"])
def whatsapp_autocomplete():
    user, error = require_user()
    if error:
        return error
    return jsonify(ok=True, suggestions=[])


@whatsapp_panel_bp.route("/api/conversations")
def whatsapp_conversations():
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    release_unanswered_ownerships()
    date_arg = request.args.get("date", "").strip()
    query = request.args.get("q", "").strip()
    filters = [
        "(msg != '' OR img != '' OR pdff != '' OR docid != '' OR imgid != '')",
    ]
    params = []
    if date_arg:
        start_date, end_date = day_bounds(date_arg)
        filters.append("""
        (
            mobile NOT IN (
                SELECT mobile
                FROM ofc_conversation_live_state
                WHERE status = 'closed'
            )
            OR mobile IN (
                SELECT mobile
                FROM ofc_conversation_live_state
                WHERE status = 'closed' AND closed_at >= %s AND closed_at < %s
            )
        )
        """)
        params = [start_date, end_date]
    else:
        filters.append("""
            mobile NOT IN (
                SELECT mobile
                FROM ofc_conversation_live_state
                WHERE status = 'closed'
            )
        """)
    if query:
        patient_mobile_variants = patient_mobile_variants_for_name_search(query)
        search_filters = ["mobile LIKE %s"]
        search_params = [f"%{query}%"]
        if patient_mobile_variants:
            placeholders = ", ".join(["%s"] * len(patient_mobile_variants))
            search_filters.append(f"mobile IN ({placeholders})")
            search_params.extend(patient_mobile_variants)
        filters.append(f"({' OR '.join(search_filters)})")
        params.extend(search_params)
    sql = f"""
        SELECT w.id, w.mobile, w.msg, w.img, w.pdff, w.docid, w.imgid, w.empname, w.datetimess, w.color,
               w.provider_message_id, w.delivery_status,
               activity.last_incoming_at, activity.last_outgoing_at, activity.last_user_reply_at,
               cs.owner_name, cs.conversation_type, cs.sla_started_at,
               cs.status AS workflow_status, cs.closed_at, cs.closure_note, cs.updated_at AS workflow_updated_at
        FROM (
          SELECT ranked.*
          FROM (
            SELECT filtered.*,
                   ROW_NUMBER() OVER (PARTITION BY filtered.mobile ORDER BY filtered.datetimess DESC, filtered.id DESC) AS rn
            FROM ({message_union_sql()}) filtered
            WHERE {' AND '.join(filters)}
          ) ranked
          WHERE ranked.rn = 1
        ) w
        LEFT JOIN (
          SELECT contacts.mobile, incoming.last_incoming_at, outgoing.last_outgoing_at, user_reply.last_user_reply_at
          FROM (
            SELECT mobile FROM {incoming_table()}
            UNION
            SELECT mobile FROM {outgoing_table()}
          ) contacts
          LEFT JOIN (
            SELECT mobile, MAX(datetimess) AS last_incoming_at
            FROM {incoming_table()}
            WHERE msg != '' OR img != '' OR pdff != '' OR docid != '' OR imgid != ''
            GROUP BY mobile
          ) incoming ON incoming.mobile = contacts.mobile
          LEFT JOIN (
            SELECT mobile, MAX(datetimess) AS last_outgoing_at
            FROM {outgoing_table()}
            WHERE msg != '' OR img != '' OR pdff != '' OR docid != '' OR imgid != ''
            GROUP BY mobile
          ) outgoing ON outgoing.mobile = contacts.mobile
          LEFT JOIN (
            SELECT mobile, MAX(datetimess) AS last_user_reply_at
            FROM {outgoing_table()}
            WHERE empname = %s AND (msg != '' OR img != '' OR pdff != '' OR docid != '' OR imgid != '')
            GROUP BY mobile
          ) user_reply ON user_reply.mobile = contacts.mobile
        ) activity ON activity.mobile = w.mobile
        LEFT JOIN ofc_conversation_live_state cs ON cs.mobile = w.mobile
        ORDER BY w.datetimess DESC
        LIMIT 300
    """
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params + [user["display_name"]])
            rows = cur.fetchall()
    finally:
        conn.close()
    rows = serialize_datetime_values(attach_patient_names(apply_sla_fields(rows)))
    return jsonify(ok=True, rows=rows)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/messages")
def whatsapp_list_messages(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    normalized = normalize_mobile(mobile)
    if not normalized:
        return jsonify(ok=False, error="Mobile missing"), 400
    try:
        limit = max(20, min(int(request.args.get("limit", 100)), 200))
    except ValueError:
        limit = 100
    try:
        before_id = int(request.args.get("before_id", 0))
    except ValueError:
        before_id = 0
    filters = ["mobile = %s"]
    params = [normalized]
    if before_id > 0:
        filters.append("id < %s")
        params.append(before_id)
    params.append(limit + 1)
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, mobile, msg, img, pdff, docid, imgid, empname, datetimess,
                       color, provider_message_id, delivery_status, delivery_status_remark
                FROM ({message_union_sql()}) messages
                WHERE {' AND '.join(filters)}
                ORDER BY datetimess DESC, id DESC
                LIMIT %s
                """,
                params,
            )
            fetched_rows = cur.fetchall()
    finally:
        conn.close()
    has_more = len(fetched_rows) > limit
    rows = list(reversed(fetched_rows[:limit]))
    rows = attach_patient_names(rows)
    rows.sort(key=lambda row: row.get("datetimess") or datetime.min)
    oldest_id = min((row["id"] for row in rows if row.get("id")), default=None)
    return jsonify(ok=True, mobile=normalized, rows=serialize_datetime_values(rows), has_more=has_more, oldest_id=oldest_id, limit=limit)


@whatsapp_panel_bp.route("/api/operators")
def whatsapp_list_operators():
    user, error = require_user()
    if error:
        return error
    query = request.args.get("q", "").strip()
    rows = fetch_operator_rows(query)
    return jsonify(ok=True, users=rows, currentUser=current_user())


@whatsapp_panel_bp.route("/api/mobile-lookup")
def whatsapp_mobile_lookup():
    user, error = require_user()
    if error:
        return error
    mobile = request.args.get("mobile", "").strip()
    if not mobile:
        return jsonify(ok=False, error="Mobile number missing"), 400
    sections = [item.strip().lower() for item in request.args.get("sections", "").split(",") if item.strip()]
    return jsonify(unified_mobile_lookup(mobile, sections or None))


@whatsapp_panel_bp.route("/api/conversations/<mobile>/ownership", methods=["POST"])
def whatsapp_take_ownership(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    normalized = normalize_mobile(mobile)
    data = request.get_json(silent=True) or {}
    reopen = bool(data.get("reopen"))
    release_unanswered_ownerships()
    if not normalized:
        return jsonify(ok=False, error="Mobile missing"), 400
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            old_state = ensure_live_conversation_state(cur, normalized)
            if old_state.get("status") == "closed" and not reopen:
                return jsonify(ok=False, error="Conversation is closed"), 400
            if old_state.get("owner_name") and old_state.get("owner_name") != user["display_name"] and not reopen:
                return jsonify(ok=False, error=f"Already owned by {old_state.get('owner_name') or 'another user'}"), 409
            cur.execute(
                """
                UPDATE ofc_conversation_live_state
                SET owner_name = %s, status = 'owned', sla_started_at = NOW()
                WHERE mobile = %s
                """,
                (user["display_name"], normalized),
            )
            log_conversation_action(cur, normalized, "reopen_conversation" if old_state.get("status") == "closed" else "take_ownership", user, old_state=old_state, new_owner=user, new_value=user["display_name"])
            cur.execute("SELECT * FROM ofc_conversation_live_state WHERE mobile = %s", (normalized,))
            state_row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, state=state_row)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/type", methods=["POST"])
def whatsapp_update_conversation_type(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    normalized = normalize_mobile(mobile)
    conversation_type = str((request.get_json(silent=True) or {}).get("conversation_type") or "").strip()[:80]
    if not normalized or not conversation_type:
        return jsonify(ok=False, error="Conversation type missing"), 400
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            old_state = ensure_live_conversation_state(cur, normalized)
            owner_error = ensure_current_owner(old_state, user)
            if owner_error:
                return jsonify(ok=False, error=owner_error), 403
            cur.execute("UPDATE ofc_conversation_live_state SET conversation_type = %s WHERE mobile = %s", (conversation_type, normalized))
            log_conversation_action(cur, normalized, "update_type", user, old_state=old_state, old_value=old_state.get("conversation_type"), new_value=conversation_type)
            cur.execute("SELECT * FROM ofc_conversation_live_state WHERE mobile = %s", (normalized,))
            state_row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, state=state_row)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/close", methods=["POST"])
def whatsapp_close_conversation(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    normalized = normalize_mobile(mobile)
    conversation_type = str(data.get("conversation_type") or "").strip()[:80]
    note = str(data.get("note") or "").strip()
    if not normalized or not conversation_type:
        return jsonify(ok=False, error="Conversation type missing"), 400
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            old_state = ensure_live_conversation_state(cur, normalized)
            owner_error = ensure_current_owner(old_state, user)
            if owner_error:
                return jsonify(ok=False, error=owner_error), 403
            cur.execute(
                """
                UPDATE ofc_conversation_live_state
                SET conversation_type = %s, status = 'closed', closed_by_name = %s, closed_at = NOW(), closure_note = %s
                WHERE mobile = %s
                """,
                (conversation_type, user["display_name"], note, normalized),
            )
            log_conversation_action(cur, normalized, "close_conversation", user, old_state=old_state, old_value=old_state.get("status"), new_value="closed", reason=note, payload={"conversation_type": conversation_type})
            cur.execute("SELECT * FROM ofc_conversation_live_state WHERE mobile = %s", (normalized,))
            state_row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, state=state_row)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/reassign", methods=["POST"])
def whatsapp_reassign_conversation(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    normalized = normalize_mobile(mobile)
    new_owner_id = data.get("owner_id")
    reason = str(data.get("reason") or "").strip()
    if not normalized or not new_owner_id:
        return jsonify(ok=False, error="New owner missing"), 400
    new_owner = fetch_operator_by_id(new_owner_id)
    if not new_owner:
        return jsonify(ok=False, error="Owner not found"), 404
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            old_state = ensure_live_conversation_state(cur, normalized)
            owner_error = ensure_current_owner(old_state, user)
            if owner_error:
                return jsonify(ok=False, error=owner_error), 403
            cur.execute(
                """
                UPDATE ofc_conversation_live_state
                SET owner_name = %s, status = 'owned', sla_started_at = NOW()
                WHERE mobile = %s
                """,
                (new_owner["display_name"], normalized),
            )
            log_conversation_action(cur, normalized, "reassign", user, old_state=old_state, new_owner=new_owner, old_value=old_state.get("owner_name"), new_value=new_owner["display_name"], reason=reason)
            cur.execute("SELECT * FROM ofc_conversation_live_state WHERE mobile = %s", (normalized,))
            state_row = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, state=state_row)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/actions")
def whatsapp_list_conversation_actions(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    normalized = normalize_mobile(mobile)
    if not normalized:
        return jsonify(ok=False, error="Mobile missing"), 400
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, action_type, performed_by_name, old_owner_name, new_owner_name, old_value, new_value, reason, payload_json, created_at
                FROM ofc_conversation_audit
                WHERE mobile = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 50
                """,
                (normalized,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify(ok=True, actions=rows)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/actions", methods=["POST"])
def whatsapp_record_conversation_action(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    normalized = normalize_mobile(mobile)
    action_type = str(data.get("action_type") or "").strip()[:80]
    reason = str(data.get("reason") or "").strip()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if not normalized or not action_type:
        return jsonify(ok=False, error="Action missing"), 400
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            state_row = ensure_live_conversation_state(cur, normalized)
            owner_error = ensure_current_owner(state_row, user)
            if owner_error:
                return jsonify(ok=False, error=owner_error), 403
            log_conversation_action(cur, normalized, action_type, user, old_state=state_row, reason=reason, payload=payload)
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)


@whatsapp_panel_bp.route("/api/conversations/<mobile>/messages", methods=["POST"])
def whatsapp_create_message(mobile):
    init_whatsapp_panel_db()
    user, error = require_user()
    if error:
        return error
    normalized = normalize_mobile(mobile)
    data = request.get_json(silent=True) or {}
    msg = str(data.get("msg") or data.get("message") or "").strip()
    template_name = str(data.get("template_name") or "").strip()
    media = normalize_media(data)
    if not normalized:
        return jsonify(ok=False, error="Mobile missing"), 400
    if not msg and not media["url"] and not template_name:
        return jsonify(ok=False, error="Message or attachment missing"), 400
    if media["url"] and not media["kind"]:
        return jsonify(ok=False, error="Unsupported attachment type"), 400
    if template_name:
        msg = "Chat Initiated"
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            state_row = ensure_live_conversation_state(cur, normalized)
            owner_error = ensure_current_owner(state_row, user)
            if owner_error:
                return jsonify(ok=False, error=owner_error), 403
            if template_name == "arpra_whatsapp_2" and has_recent_conversation_activity(cur, normalized, 24):
                log_conversation_action(
                    cur,
                    normalized,
                    "skip_new_conversation_template",
                    user,
                    old_state=state_row,
                    new_value="recent_activity_24h",
                    payload={"template_name": template_name},
                )
                conn.commit()
                return jsonify(
                    ok=True,
                    template_skipped=True,
                    reason="recent_activity_24h",
                    message="Recent chat opened without sending template",
                )
    finally:
        conn.close()

    try:
        provider_result = send_via_provider(
            normalized,
            msg,
            media if media["url"] else None,
            template_name=template_name,
            template_attributes=[user["display_name"], user["display_name"]] if template_name else None,
        )
    except Exception as exc:
        log_panel_event("send_failed", mobile=mask_mobile(normalized), error=str(exc))
        return jsonify(ok=False, error=str(exc)), 500

    provider_message_id = provider_result.get("providerMessageId") or None
    delivery_status = "accepted" if provider_message_id else ("local" if provider_result.get("skipped") else None)
    delivery_status_remark = str(provider_result.get("response") or "")[:1000]
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            image_value = media["url"] if media["kind"] == "image" else ""
            pdf_name = media["filename"] if media["kind"] == "document" else ""
            document_id = media["url"] if media["kind"] == "document" else ""
            image_id = media["url"] if media["kind"] == "image" else ""
            cur.execute(
                f"""
                INSERT INTO {outgoing_table()}
                  (mobile, msg, img, pdff, docid, imgid, empname, provider_message_id, delivery_status, delivery_status_remark)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    normalized,
                    msg,
                    image_value,
                    pdf_name,
                    document_id,
                    image_id,
                    user["display_name"][:225],
                    provider_message_id,
                    delivery_status,
                    delivery_status_remark,
                ),
            )
            row_id = cur.lastrowid
            cur.execute(
                f"""
                SELECT id, mobile, msg, img, pdff, docid, imgid, empname, datetimess,
                       'green' AS color, provider_message_id, delivery_status, delivery_status_remark
                FROM {outgoing_table()}
                WHERE id = %s
                """,
                (row_id,),
            )
            message_row = cur.fetchone()
            log_conversation_action(
                cur,
                normalized,
                "send_message",
                user,
                old_state=state_row,
                new_value=template_name or ("attachment" if media["url"] else "text"),
                payload=template_name or f"message_row_id:{row_id}",
            )
        conn.commit()
    finally:
        conn.close()
    if message_row:
        emit_whatsapp_event("outgoing_message", socket_safe_message(message_row))
    return jsonify(ok=True, id=row_id, message=serialize_datetime_values(message_row), provider=provider_result), 201


@whatsapp_panel_bp.route("/webhook/incoming-message", methods=["POST"])
def whatsapp_incoming_webhook():
    init_whatsapp_panel_db()
    raw_body = request.get_data(as_text=True)
    data = request.get_json(silent=True) or {}
    try:
        payload = normalize_incoming_payload(data)
        live_message = None
        conn = get_whatsapp_panel_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {incoming_table()}
                      (mobile, msg, img, pdff, docid, imgid, empname, datetimess)
                    VALUES (%s, %s, %s, %s, %s, %s, 'Patient', %s)
                    """,
                    (
                        payload["mobile"],
                        payload["msg"],
                        payload["img"],
                        payload["pdff"],
                        payload["docid"],
                        payload["imgid"],
                        payload["received_datetime"],
                    ),
                )
                message_id = cur.lastrowid
                live_message = {
                    "id": message_id,
                    "mobile": payload["mobile"],
                    "msg": payload["msg"],
                    "img": payload["img"],
                    "pdff": payload["pdff"],
                    "docid": payload["docid"],
                    "imgid": payload["imgid"],
                    "datetimess": str(payload["received_datetime"]),
                    "empname": "Patient",
                    "color": "red",
                }
                reopened = reopen_closed_conversation_for_incoming(cur, payload["mobile"])
                cur.execute(
                    "UPDATE ofc_conversation_live_state SET sla_started_at = NOW() WHERE mobile = %s",
                    (payload["mobile"],),
                )
            conn.commit()
        finally:
            conn.close()
        if live_message:
            emit_whatsapp_event("incoming_message", live_message)
        log_panel_event("incoming_message_saved", mobile=mask_mobile(payload["mobile"]), receivedAt=payload["received_at"], reopened=reopened)
        return jsonify(ok=True, message="Incoming WhatsApp message saved", data={**payload, "reopened": reopened})
    except Exception as exc:
        log_panel_event("incoming_message_failed", error=str(exc), body=raw_body[:2000])
        return jsonify(ok=False, error=str(exc)), 500


@whatsapp_panel_bp.route("/webhook/delivery-status", methods=["POST"])
def whatsapp_delivery_webhook():
    init_whatsapp_panel_db()
    raw_body = request.get_data(as_text=True)
    data = request.get_json(silent=True) or {}
    try:
        payload = normalize_delivery_payload(data)
        conn = get_whatsapp_panel_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {outgoing_table()}
                    SET delivery_status = %s, delivery_status_remark = %s
                    WHERE provider_message_id = %s
                    """,
                    (payload["status"], payload["status_remark"], payload["provider_message_id"]),
                )
                updated = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        log_panel_event("delivery_status_received", currentUpdated=updated, **payload)
        return jsonify(ok=True, message="Delivery status saved", data={**payload, "currentUpdated": updated})
    except Exception as exc:
        log_panel_event("delivery_status_failed", error=str(exc), body=raw_body[:2000])
        return jsonify(ok=False, error=str(exc)), 500
