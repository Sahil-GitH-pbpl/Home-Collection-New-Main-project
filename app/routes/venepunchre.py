import os
import re
import json
import time
import hashlib
import threading
from datetime import datetime, timedelta, timezone
import urllib.request as url_request
import urllib.error as url_error
from urllib import parse as url_parse
import base64
import hmac
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    current_app,
)
from werkzeug.utils import secure_filename
import mysql.connector
from app.db.connection import get_db_connection, get_venepunchre_connection

venepunchre_bp = Blueprint("venepunchre", __name__)
IST = timezone(timedelta(hours=5, minutes=30))

# ---- Config (env-first) ----
PATIENT_LOOKUP_URL = os.getenv(
    "PATIENT_LOOKUP_URL",
    "http://10.1.1.252:8000/reportapi/LabmatePatRegistration.svc/Getpatientdatabymobileno",
)
PATIENT_LOOKUP_TIMEOUT = int(os.getenv("PATIENT_LOOKUP_TIMEOUT", "8"))
EVIDENCE_UPLOAD_SUBDIR = (
    (os.getenv("VENE_EVIDENCE_UPLOAD_SUBDIR", "uploads/venepunchre") or "uploads/venepunchre")
    .replace("\\", "/")
    .strip("/")
)
EVIDENCE_MAX_SIZE_BYTES = int(os.getenv("VENE_EVIDENCE_MAX_SIZE_BYTES", str(5 * 1024 * 1024)))
ALLOWED_EVIDENCE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
RECORDS_PAGE_SIZE = int(os.getenv("VENE_RECORDS_PAGE_SIZE", "25"))
RESPONSE_REMINDER_MINUTES = int(os.getenv("VENE_RESPONSE_REMINDER_MINUTES", "1200"))

PHLEBO_DESIGNATIONS = {
    part.strip() for part in os.getenv("VENE_PHLEBO_DESIGNATIONS", "Home Collection Phlebo,Center Phlebo,Technical").split(",") if part.strip()
}
RESOLVE_DETAIL_USER_NAMES = os.getenv("VENE_RESOLVE_DETAIL_USER_NAMES", "1").strip().lower() in {"1", "true", "yes", "y", "on"}

# Users allowed to escalate / close NC flow
NC_CONTROL_USER_IDS = {
    int(part.strip())
    for part in os.getenv("VENE_NC_CONTROL_USER_IDS", "124,125,61").split(",")
    if part.strip().isdigit()
}

# WhatsApp config
WHATSAPP_API_URL = os.getenv(
    "WHATSAPP_API_URL",
    "http://10.1.1.44:3004/api/messages/send",  # default same as legacy app
).strip()
WHATSAPP_ACCOUNT_ID = int(os.getenv("WHATSAPP_ACCOUNT_ID", "1"))
WHATSAPP_TIMEOUT = int(os.getenv("WHATSAPP_TIMEOUT", "2"))
WHATSAPP_MESSAGE_TEMPLATE = os.getenv(
    "WHATSAPP_MESSAGE_TEMPLATE",
    "Hello {name}, Venepuncture record {record_id} has been submitted.",
)
SUBMISSION_RESPONSE_LINK = os.getenv("SUBMISSION_RESPONSE_LINK", "www.example.com")
# Public response link config (mirrors legacy app defaults)
PUBLIC_RESPONSE_EXTERNAL_BASE_URL = os.getenv(
    "PUBLIC_RESPONSE_EXTERNAL_BASE_URL",
    "https://labmate.bhasinpathlabs.com:4666",
).rstrip("/")
PUBLIC_RESPONSE_TOKEN_SECRET = os.getenv(
    "PUBLIC_RESPONSE_TOKEN_SECRET",
    os.getenv("JWT_SECRET", "supersecret"),
).strip()
PUBLIC_RESPONSE_TOKEN_DEFAULT_DAYS = int(os.getenv("PUBLIC_RESPONSE_TOKEN_DEFAULT_DAYS", "3"))
MANAGEMENT_DESIGNATION_NAMES = {
    part.strip().lower()
    for part in os.getenv("VENE_MANAGEMENT_DESIGNATION_NAMES", "admin,management").split(",")
    if part.strip()
}

# ---- Helpers ----
def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _vene_now():
    # Store naive IST datetimes so DB values stay consistent with existing DATETIME columns.
    return datetime.now(IST).replace(tzinfo=None)


def _current_user():
    return {
        "id": session.get("user_id"),
        "name": session.get("username"),
        "designation": session.get("designation"),
    }


def is_nc_control_user(user_id):
    try:
        return int(user_id) in NC_CONTROL_USER_IDS
    except Exception:
        return False


def is_management_session_user():
    designation = (session.get("designation") or "").strip().lower()
    return bool(designation and designation in MANAGEMENT_DESIGNATION_NAMES)


def resolve_user_name(user_id):
    """Return name for a given main DB user id, else None."""
    uid = safe_int(user_id)
    if not uid:
        return None
    profile = get_main_user_by_id(uid)
    if not profile:
        return None
    if isinstance(profile, tuple):
        return profile[1] if len(profile) > 1 else None
    return profile.get("name")


def resolve_user_names_bulk(user_ids):
    """Resolve multiple main DB user ids in one query for faster detail rendering."""
    ids = sorted({safe_int(uid) for uid in (user_ids or []) if safe_int(uid)})
    if not ids:
        return {}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(
                f"SELECT id, name FROM users WHERE LOWER(TRIM(status)) = 'active' AND id IN ({placeholders})",
                tuple(ids),
            )
            rows = cur.fetchall() or []
            mapping = {}
            for row in rows:
                if isinstance(row, dict):
                    mapping[safe_int(row.get("id"))] = row.get("name")
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    mapping[safe_int(row[0])] = row[1]
            return {k: v for k, v in mapping.items() if k and v}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_logged_in():
    if not session.get("user_id"):
        return redirect(url_for("auth.home"))
    return None


def get_main_user_by_id(user_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, designation, contact FROM users WHERE id=%s AND LOWER(TRIM(status)) = 'active' LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
            return row
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_staff_by_name(name, designations=None):
    name_text = (name or "").strip()
    if not name_text:
        return None

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            query = "SELECT id, name, designation, contact FROM users WHERE LOWER(name)=LOWER(%s) AND LOWER(TRIM(status)) = 'active'"
            params = [name_text]
            if designations:
                placeholders = ",".join(["%s"] * len(designations))
                query += f" AND designation IN ({placeholders})"
                params.extend(designations)
            query += " ORDER BY id ASC LIMIT 1"
            cur.execute(query, params)
            return cur.fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_upload_file_size(file_storage):
    if not file_storage:
        return None
    file_stream = getattr(file_storage, "stream", None) or getattr(file_storage, "_file", None)
    if not file_stream:
        return None
    try:
        cur = file_stream.tell()
        file_stream.seek(0, os.SEEK_END)
        size = int(file_stream.tell())
        file_stream.seek(cur)
        return size
    except Exception:
        return None


def validate_evidence_image_file(file_storage):
    if not file_storage:
        return
    original_name = (getattr(file_storage, "filename", "") or "").strip()
    if not original_name:
        return
    safe_name = secure_filename(original_name)
    ext = os.path.splitext(safe_name)[1].lower().lstrip(".")
    if ext not in ALLOWED_EVIDENCE_IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, JPEG, PNG, WEBP allowed in evidence upload.")
    size = get_upload_file_size(file_storage)
    if size is not None and size > EVIDENCE_MAX_SIZE_BYTES:
        raise ValueError("Evidence image exceeds max size limit.")


def save_evidence_image(file_storage, hiccup_id):
    validate_evidence_image_file(file_storage)
    original_name = (getattr(file_storage, "filename", "") or "").strip()
    if not original_name:
        return None, None
    safe_name = secure_filename(original_name)
    ext = os.path.splitext(safe_name)[1].lower()
    ts = _vene_now().strftime("%Y%m%d%H%M%S")
    unique_suffix = hashlib.sha1(f"{hiccup_id}|{safe_name}|{time.time()}".encode("utf-8")).hexdigest()[:10]
    stored = f"{hiccup_id}_{ts}_{unique_suffix}{ext}"

    rel_dir = f"static/{EVIDENCE_UPLOAD_SUBDIR}".replace("\\", "/")
    abs_dir = os.path.join(current_app.root_path, *rel_dir.split("/"))
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(abs_dir, stored)
    file_storage.save(abs_path)
    public_path = f"/{rel_dir}/{stored}".replace("\\", "/")
    return public_path, abs_path


# ---- WhatsApp + public token helpers ----
def normalize_whatsapp_target(contact_value):
    digits = re.sub(r"\D", "", str(contact_value or ""))
    if not digits:
        return None
    if len(digits) == 10:
        return f"91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    if 11 <= len(digits) <= 15:
        return digits
    return None


def send_whatsapp_message(contact_value, record_id, name_hint, message_text=None, target_override=None, action_type=None):
    api_url = WHATSAPP_API_URL
    if not api_url:
        return False, "WHATSAPP_API_URL not configured"

    target = normalize_whatsapp_target(target_override if target_override is not None else contact_value)
    if not target:
        return False, "No valid WhatsApp target"

    final_message = (
        message_text
        if message_text is not None
        else WHATSAPP_MESSAGE_TEMPLATE.format(name=(name_hint or "User"), record_id=(record_id or "-"))
    )
    payload = {"accountId": WHATSAPP_ACCOUNT_ID, "target": target, "message": final_message}
    def audit(sent, error_text=None):
        if not action_type:
            return
        try:
            from app.whatsapp_audit import log_whatsapp_send

            log_whatsapp_send(
                action_type=action_type,
                api_type="local",
                related_id=record_id,
                related_code=record_id,
                recipient=target,
                message_text=final_message,
                payload_json=payload,
                is_success=sent,
                error_text=error_text,
            )
        except Exception:
            pass
    try:
        req = url_request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with url_request.urlopen(req, timeout=WHATSAPP_TIMEOUT):
            audit(True)
            return True, ""
    except url_error.HTTPError as exc:
        err = f"HTTP {exc.code}"
        audit(False, err)
        return False, err
    except url_error.URLError:
        audit(False, "Network error")
        return False, "Network error"
    except Exception as exc:
        err = f"Unexpected error: {exc}"
        audit(False, err)
        return False, err


def b64url_encode(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")


def sign_compact_public_body(body_bytes, length=8):
    secret = (PUBLIC_RESPONSE_TOKEN_SECRET or "").encode("utf-8")
    body_raw = body_bytes if isinstance(body_bytes, (bytes, bytearray)) else str(body_bytes or "").encode("utf-8")
    sig_len = max(1, min(int(length or 8), 32))
    return hmac.new(secret, body_raw, hashlib.sha256).digest()[:sig_len]


def build_public_response_token_payload(case_id, user_id=None, role="staff_user", department_id=None, exp_epoch=None):
    case_text = str(case_id or "").strip()
    if not case_text:
        raise ValueError("case_id required for public token")
    user_text = str(safe_int(user_id) or "").strip()
    role_text = str(role or "staff_user").replace("|", " ").strip() or "staff_user"
    dept_text = str(safe_int(department_id) or "").strip()
    exp_text = str(safe_int(exp_epoch) or "").strip()
    return f"{case_text}|{user_text}|{role_text}|{dept_text}|{exp_text}"


def create_public_response_token(case_id, user_id=None, role="staff_user", department_id=None):
    delta = timedelta(days=PUBLIC_RESPONSE_TOKEN_DEFAULT_DAYS)
    exp_epoch = int((datetime.now(IST) + delta).timestamp())
    body = build_public_response_token_payload(case_id, user_id=user_id, role=role, department_id=department_id, exp_epoch=exp_epoch)
    body_bytes = body.encode("utf-8")
    signature = sign_compact_public_body(body_bytes, length=8)
    compact_payload = body_bytes + b"." + signature
    return f"c2.{b64url_encode(compact_payload)}"


def build_public_response_links(case_id, token):
    case_text = str(case_id or "").strip()
    token_text = str(token or "").strip()
    encoded_token = url_parse.quote(token_text, safe="")
    external_url = f"{PUBLIC_RESPONSE_EXTERNAL_BASE_URL}/Venepunchere/{case_text}/{token_text}"
    internal_url = f"/wa/redirect/{case_text}?t={encoded_token}"
    return {"external_url": external_url, "internal_url": internal_url}


def build_submission_whatsapp_message(record):
    created_at = record.get("created_at")
    time_text = created_at.strftime("%Y-%m-%d %H:%M") if isinstance(created_at, datetime) else "-"
    summary_text = record.get("description") if isinstance(record, dict) else "-"
    # Build tokenized response link like legacy app
    token = None
    try:
        token = create_public_response_token(record.get("hiccup_id"))
    except Exception:
        token = None
    link_target = None
    if token:
        links = build_public_response_links(record.get("hiccup_id"), token)
        link_target = links.get("external_url")
    if not link_target:
        base = SUBMISSION_RESPONSE_LINK.rstrip("/") if SUBMISSION_RESPONSE_LINK else ""
        link_target = f"{base}/{record.get('hiccup_id')}" if base and record.get("hiccup_id") else (SUBMISSION_RESPONSE_LINK or "-")
    raised_by_dept = record.get("raised_by_department") or record.get("raised_against_department_name") or "-"
    lines = [
        "New Venepuncture Raised!",
        f"ID: {record.get('hiccup_id') or '-'}",
        f"Raised By: {record.get('created_by_name') or '-'} ({raised_by_dept})",
        f"Raised Against: {record.get('raised_against') or '-'}",
        f"Type: Person Related",
        f"Time: {time_text}",
        f"Summary: {summary_text or '-'}",
        "",
        f"Tap to respond : {link_target}",
    ]
    if record.get("reported_name_input"):
        lines.append(f"Reported Name: {record.get('reported_name_input')}")
    if record.get("patient_identifier"):
        lines.append(f"Patient ID: {record.get('patient_identifier')}")
    if record.get("patient_name_input"):
        lines.append(f"Patient Name: {record.get('patient_name_input')}")
    if record.get("patient_age_input"):
        lines.append(f"Patient Age: {record.get('patient_age_input')}")
    if record.get("patient_gender_input"):
        lines.append(f"Patient Gender: {record.get('patient_gender_input')}")
    if record.get("patient_mobile_input"):
        lines.append(f"Patient Mobile: {record.get('patient_mobile_input')}")
    return "\n".join(lines)


def build_assignment_whatsapp_message(case_id, assigned_name, raised_against, created_at, summary):
    created_txt = created_at.strftime("%Y-%m-%d %H:%M") if isinstance(created_at, datetime) else str(created_at or "-")
    return (
        "Venepuncture NC Assignment\n\n"
        f"Case ID: {case_id}\n"
        "Status: Escalated to NC\n"
        f"Assigned To: {assigned_name or 'NC Staff'}\n"
        f"Raised Against: {raised_against or '-'}\n"
        f"Created At: {created_txt}\n"
        f"Summary: {summary or '-'}\n"
        "Please review and update the NC form."
    )


def _send_submission_whatsapp_async(contact, hiccup_id, venepunchre_name, message_text, app_logger):
    """Background sender so form submit is not blocked by WA API latency."""
    try:
        sent, err = send_whatsapp_message(contact, hiccup_id, venepunchre_name, message_text=message_text, action_type="ven_create")
        if not sent and app_logger:
            app_logger.warning("WhatsApp not sent for %s: %s", hiccup_id, err)
    except Exception as exc:
        if app_logger:
            app_logger.warning("WhatsApp send failed for %s: %s", hiccup_id, exc)


def parse_list_field(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return []
        try:
            decoded = json.loads(txt)
            if isinstance(decoded, list):
                return [str(v).strip() for v in decoded if str(v).strip()]
        except Exception:
            pass
        return [p.strip() for p in txt.split(",") if p.strip()]
    return []


def encode_list_field(values):
    return json.dumps(parse_list_field(values))


def fetch_nc_form_row(cursor, case_id):
    cursor.execute(
        """
        SELECT id, hiccup_id, staff_name, assigned_staff_id,
               root_cause_flags, root_cause_other, corrective_action, corrective_action_by,
               corrective_action_date, person_responsible, timeline_for_completion,
               preventive_actions, preventive_other, created_at, updated_at
        FROM nc_escalation_forms
        WHERE hiccup_id=%s
        LIMIT 1
        """,
        (case_id,),
    )
    return cursor.fetchone()


def normalize_nc_form_response(row):
    if not row:
        return None
    date_val = row.get("corrective_action_date")
    return {
        "case_id": row.get("hiccup_id"),
        "staff_name": row.get("staff_name") or "",
        "staff_id": safe_int(row.get("assigned_staff_id")),
        "root_cause_flags": parse_list_field(row.get("root_cause_flags")),
        "root_cause_other": row.get("root_cause_other") or "",
        "corrective_action": row.get("corrective_action") or "",
        "corrective_action_by": row.get("corrective_action_by") or "",
        "corrective_action_date": date_val.isoformat() if hasattr(date_val, "isoformat") and date_val else None,
        "person_responsible": row.get("person_responsible") or "",
        "timeline_for_completion": row.get("timeline_for_completion") or "",
        "preventive_actions": parse_list_field(row.get("preventive_actions")),
        "preventive_other": row.get("preventive_other") or "",
    }


def upsert_nc_form(cursor, case_id, payload):
    data = payload or {}
    staff_id = safe_int(data.get("staff_id"))
    staff_name = (data.get("staff_name") or "").strip()
    root_flags = parse_list_field(data.get("root_cause_flags"))
    root_other = (data.get("root_cause_other") or "").strip()
    corrective = (data.get("corrective_action") or "").strip()
    corrective_by = (data.get("corrective_action_by") or "").strip()
    corrective_date = (data.get("corrective_action_date") or "").strip() or None
    person = (data.get("person_responsible") or "").strip()
    timeline = (data.get("timeline_for_completion") or "").strip()
    preventive = parse_list_field(data.get("preventive_actions"))
    preventive_other = (data.get("preventive_other") or "").strip()
    now_dt = _vene_now()

    existing = fetch_nc_form_row(cursor, case_id)
    if existing:
        cursor.execute(
            """
            UPDATE nc_escalation_forms
            SET staff_name=%s, assigned_staff_id=%s, root_cause_flags=%s, root_cause_other=%s,
                corrective_action=%s, corrective_action_by=%s, corrective_action_date=%s,
                person_responsible=%s, timeline_for_completion=%s, preventive_actions=%s,
                preventive_other=%s, updated_at=%s
            WHERE hiccup_id=%s
            """,
            (
                staff_name or existing.get("staff_name") or "Unassigned",
                staff_id,
                encode_list_field(root_flags),
                root_other or None,
                corrective or None,
                corrective_by or None,
                corrective_date,
                person or None,
                timeline or None,
                encode_list_field(preventive),
                preventive_other or None,
                now_dt,
                case_id,
            ),
        )
    else:
        cursor.execute(
            """
            INSERT INTO nc_escalation_forms (
                hiccup_id, staff_name, assigned_staff_id, root_cause_flags, root_cause_other,
                corrective_action, corrective_action_by, corrective_action_date,
                person_responsible, timeline_for_completion, preventive_actions, preventive_other,
                created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                case_id,
                staff_name or "Unassigned",
                staff_id,
                encode_list_field(root_flags),
                root_other or None,
                corrective or None,
                corrective_by or None,
                corrective_date,
                person or None,
                timeline or None,
                encode_list_field(preventive),
                preventive_other or None,
                now_dt,
                now_dt,
            ),
        )


def generate_hiccup_id(cursor):
    year_code = _vene_now().strftime("%y")
    prefix = f"VNR-{year_code}-"
    cursor.execute(
        """
        SELECT MAX(CAST(SUBSTRING_INDEX(hiccup_id, '-', -1) AS UNSIGNED))
        FROM venepunchre_records
        WHERE hiccup_id LIKE %s
        """,
        (f"{prefix}%",),
    )
    row = cursor.fetchone()
    max_seq = 0
    if row is not None:
        if isinstance(row, dict):
            max_seq = list(row.values())[0] or 0
        elif isinstance(row, (list, tuple)):
            max_seq = row[0] or 0
    next_seq = int(max_seq) + 1
    return f"{prefix}{next_seq:03d}"


def build_description(form_values):
    manual = (form_values.get("description") or "").strip()
    if manual:
        return manual
    lines = [
        "Venepuncture record submission",
        f"Reported By Option: {form_values.get('reported_by') or '-'}",
        f"Reported Name: {form_values.get('reported_name') or '-'}",
        f"Location: {form_values.get('location_name') or '-'}",
        f"Venepuncture Done By: {form_values.get('venepunchre_done_by') or '-'}",
        f"Patient Billing Done: {form_values.get('patient_billing_done') or '-'}",
    ]
    return "\n".join(lines)


@venepunchre_bp.route("/venepunchre/form")
def venepunchre_form():
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login
    form_values = {
        "reported_by": "",
        "reported_name": "",
        "reported_staff_id": "",
        "location_name": "",
        "venepunchre_done_by": "",
        "venepunchre_staff_id": "",
        "description": "",
        "patient_billing_done": "",
        "patient_id": "",
        "name": "",
        "age": "",
        "gender": "",
        "mobile_no": "",
    }
    return render_template(
        "venepunchre_form.html",
        form_values=form_values,
        current_user_name=session.get("username") or "",
        current_user_id=session.get("user_id") or "",
    )


@venepunchre_bp.route("/venepunchre/submit", methods=["POST"])
def venepunchre_submit():
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    form_values = {k: (request.form.get(k) or "").strip() for k in [
        "reported_by",
        "reported_name",
        "reported_staff_id",
        "location_name",
        "venepunchre_done_by",
        "venepunchre_staff_id",
        "description",
        "patient_billing_done",
        "patient_id",
        "name",
        "age",
        "gender",
        "mobile_no",
    ]}
    evidence_file = request.files.get("evidence_image")

    required = ["reported_by", "reported_name", "location_name", "venepunchre_done_by", "patient_billing_done"]
    missing = [f for f in required if not form_values.get(f)]
    if missing:
        flash(f"Please fill required fields: {', '.join(missing)}", "error")
        return redirect(url_for("venepunchre.venepunchre_form"))

    creator = _current_user()
    creator_id = safe_int(creator.get("id"))
    creator_name = creator.get("name") or "Unknown"
    creator_profile = get_main_user_by_id(creator_id)
    creator_designation = None
    if creator_profile:
        creator_designation = creator_profile[2] if isinstance(creator_profile, tuple) else creator_profile.get("designation")
    creator_designation = (creator_designation or "").strip() or "Admin"

    venepunchre_staff_id = safe_int(form_values.get("venepunchre_staff_id"))
    venepunchre_name = form_values.get("venepunchre_done_by")
    phlebo_profile = None
    if not venepunchre_staff_id:
        phlebo_profile = get_staff_by_name(venepunchre_name, designations=PHLEBO_DESIGNATIONS)
        if phlebo_profile:
            venepunchre_staff_id = phlebo_profile[0] if isinstance(phlebo_profile, tuple) else phlebo_profile.get("id")
            venepunchre_name = phlebo_profile[1] if isinstance(phlebo_profile, tuple) else phlebo_profile.get("name")
    else:
        phlebo_profile = get_main_user_by_id(venepunchre_staff_id)

    description = build_description(form_values)

    raised_by_department = creator_designation
    raised_against_department = 0

    # Normalize patient billing flag to enum('yes','no')
    billing_val = (form_values.get("patient_billing_done") or "").strip().lower() or None
    if billing_val not in (None, "yes", "no"):
        billing_val = None

    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        hiccup_id = generate_hiccup_id(cursor)
        # Build display names with designations
        target_label = venepunchre_name
        target_designation = None
        if phlebo_profile:
            target_designation = (phlebo_profile[2] if isinstance(phlebo_profile, tuple) and len(phlebo_profile) > 2 else phlebo_profile.get("designation")) or ""
            target_designation = target_designation.strip()
            if target_designation:
                target_label = f"{venepunchre_name} ({target_designation})"
        insert_sql = """
            INSERT INTO venepunchre_records (
                hiccup_id,
                raised_by,
                raised_by_department,
                hiccup_type,
                raised_against,
                raised_against_department,
                description,
                status,
                raised_against_department_name,
                reported_by_option,
                reported_staff_id,
                reported_name_input,
                location_name_input,
                venepunchre_staff_id,
                patient_billing_done,
                patient_identifier,
                patient_name_input,
                patient_age_input,
                patient_gender_input,
                patient_mobile_input,
                created_by_user_id,
                created_by_name,
                created_at,
                updated_at
            ) VALUES (
                %(hiccup_id)s,
                %(raised_by)s,
                %(raised_by_department)s,
                %(hiccup_type)s,
                %(raised_against)s,
                %(raised_against_department)s,
                %(description)s,
                %(status)s,
                %(raised_against_department_name)s,
                %(reported_by_option)s,
                %(reported_staff_id)s,
                %(reported_name_input)s,
                %(location_name_input)s,
                %(venepunchre_staff_id)s,
                %(patient_billing_done)s,
                %(patient_identifier)s,
                %(patient_name_input)s,
                %(patient_age_input)s,
                %(patient_gender_input)s,
                %(patient_mobile_input)s,
                %(created_by_user_id)s,
                %(created_by_name)s,
                %(created_at)s,
                %(updated_at)s
            )
        """
        now_dt = _vene_now()
        payload = {
            "hiccup_id": hiccup_id,
            "raised_by": creator_id,
            "raised_by_department": raised_by_department,
            "hiccup_type": "Person Related",
            "raised_against": target_label,
            "raised_against_department": raised_against_department,
            "description": description,
            "status": "Open",
            "raised_against_department_name": target_designation or "",
            "reported_by_option": form_values.get("reported_by"),
            "reported_staff_id": safe_int(form_values.get("reported_staff_id")),
            "reported_name_input": form_values.get("reported_name"),
            "location_name_input": form_values.get("location_name"),
            "venepunchre_staff_id": venepunchre_staff_id,
            "patient_billing_done": billing_val,
            "patient_identifier": form_values.get("patient_id") or None,
            "patient_name_input": form_values.get("name") or None,
            "patient_age_input": form_values.get("age") or None,
            "patient_gender_input": form_values.get("gender") or None,
            "patient_mobile_input": form_values.get("mobile_no") or None,
            "created_by_user_id": creator_id,
            "created_by_name": creator_name,
            "created_at": now_dt,
            "updated_at": now_dt,
        }
        cursor.execute(insert_sql, payload)
        if evidence_file and getattr(evidence_file, "filename", ""):
            attachment_path, _abs = save_evidence_image(evidence_file, hiccup_id)
            if attachment_path:
                cursor.execute(
                    "UPDATE venepunchre_records SET attachment_path=%s WHERE hiccup_id=%s",
                    (attachment_path, hiccup_id),
                )
        conn.commit()

        # WhatsApp notification to phlebo / raised-against (async, non-blocking)
        try:
            contact = None
            if phlebo_profile:
                contact = phlebo_profile[3] if isinstance(phlebo_profile, tuple) and len(phlebo_profile) > 3 else phlebo_profile.get("contact")
            if contact:
                message = build_submission_whatsapp_message({
                    **payload,
                    "hiccup_id": hiccup_id,
                    "created_at": now_dt,
                })
                app_logger = current_app.logger
                threading.Thread(
                    target=_send_submission_whatsapp_async,
                    args=(contact, hiccup_id, venepunchre_name, message, app_logger),
                    daemon=True,
                ).start()
        except Exception as exc:
            current_app.logger.warning("Failed to queue WhatsApp send for %s: %s", hiccup_id, exc)

        return redirect(url_for("venepunchre.venepunchre_records"))
    except Exception as exc:
        current_app.logger.exception("Failed to save venepuncture record: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        flash("Unable to save record. Please try again. (DB insert error)", "error")
        return redirect(url_for("venepunchre.venepunchre_form"))
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


@venepunchre_bp.route("/venepunchre/records")
def venepunchre_records():
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    search_query = (request.args.get("q") or "").strip()
    status_filter = (request.args.get("status") or "").strip()
    page = safe_int(request.args.get("page")) or 1
    if page < 1:
        page = 1
    offset = (page - 1) * RECORDS_PAGE_SIZE
    current_ui_user_id = safe_int(session.get("user_id"))
    can_manage_case = is_management_session_user()

    conn = None
    cursor = None
    rows = []
    has_next_page = False
    total = None
    try:
        conn = get_venepunchre_connection()
        cursor = conn.cursor(dictionary=True)
        where = []
        params = []
        if search_query:
            where.append(
                "(hiccup_id LIKE %s OR reported_name_input LIKE %s OR patient_identifier LIKE %s OR patient_name_input LIKE %s)"
            )
            like = f"%{search_query}%"
            params.extend([like, like, like, like])
        if status_filter:
            where.append("status = %s")
            params.append(status_filter)
        where_sql = " WHERE " + " AND ".join(where) if where else ""

        # Keep records page snappy: avoid full COUNT query on large tables.
        cursor.execute(
            f"""
            SELECT id, hiccup_id, reported_by_option, reported_name_input, created_by_name,
                   raised_against, patient_name_input, patient_identifier,
                   status, created_at, nc_assigned_staff_id, escalated_by
            FROM venepunchre_records
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            params + [RECORDS_PAGE_SIZE + 1, offset],
        )
        fetched_rows = cursor.fetchall() or []
        has_next_page = len(fetched_rows) > RECORDS_PAGE_SIZE
        rows = fetched_rows[:RECORDS_PAGE_SIZE]
    except mysql.connector.Error as exc:
        current_app.logger.exception("Venepuncture records DB error: %s", exc)
        flash("Venepuncture records DB se connect nahi ho pa raha. Please try again.", "error")
    except Exception as exc:
        current_app.logger.exception("Venepuncture records load failed: %s", exc)
        flash("Venepuncture records load nahi ho paaye. Please try again.", "error")
    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except Exception:
            pass

    start_row = offset + 1 if rows else 0
    end_row = offset + len(rows)
    return render_template(
        "venepunchre_records.html",
        records=rows,
        search_query=search_query,
        status_filter=status_filter,
        page=page,
        total_records=total,
        start_row=start_row,
        end_row=end_row,
        has_next_page=has_next_page,
        page_size=RECORDS_PAGE_SIZE,
        current_ui_user_id=current_ui_user_id,
        can_manage_case=can_manage_case,
    )


@venepunchre_bp.route("/venepunchre/records/<hiccup_id>")
def venepunchre_record_detail(hiccup_id):
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    current_user_id = safe_int(session.get("user_id"))
    open_nc = safe_bool(request.args.get("open_nc"))
    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM venepunchre_records WHERE hiccup_id=%s LIMIT 1",
            (hiccup_id,),
        )
        row = cursor.fetchone()
        if not row:
            flash("Record not found", "error")
            return redirect(url_for("venepunchre.venepunchre_records"))
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

    # Optional user-name resolve via a single bulk query (fast for detail page).
    if RESOLVE_DETAIL_USER_NAMES:
        name_map = resolve_user_names_bulk([
            row.get("escalated_by"),
            row.get("nc_assigned_staff_id"),
            row.get("response_by"),
        ])
        row["escalated_by_name"] = name_map.get(safe_int(row.get("escalated_by")))
        row["nc_assigned_staff_name"] = name_map.get(safe_int(row.get("nc_assigned_staff_id")))
        row["response_by_name"] = name_map.get(safe_int(row.get("response_by")))
    else:
        row["escalated_by_name"] = None
        row["nc_assigned_staff_name"] = None
        row["response_by_name"] = None
    row["raised_by_department_display"] = (row.get("raised_by_department") or "").strip() or "-"

    # Build patient + detail sections for template
    patient_details = [
        {"label": "Patient ID", "value": row.get("patient_identifier")},
        {"label": "Patient Name", "value": row.get("patient_name_input")},
        {"label": "Patient Age", "value": row.get("patient_age_input")},
        {"label": "Patient Gender", "value": row.get("patient_gender_input")},
        {"label": "Patient Mobile", "value": row.get("patient_mobile_input")},
    ]
    record_items = [
        {"label": "Reported By", "value": row.get("reported_by_option")},
        {"label": "Reported Name", "value": row.get("reported_name_input")},
        {"label": "Venepuncture Done By", "value": row.get("raised_against")},
        {"label": "Venepuncture Department", "value": row.get("raised_against_department_name")},
        {"label": "Location", "value": row.get("location_name_input")},
        {"label": "Patient Billing Done", "value": row.get("patient_billing_done")},
        {"label": "Status", "value": row.get("status")},
        {"label": "Assigned To", "value": row.get("nc_assigned_staff_name") or row.get("nc_assigned_staff_id")},
        {"label": "Escalated By", "value": row.get("escalated_by_name") or row.get("escalated_by")},
        {"label": "Created At", "value": row.get("created_at")},
        {"label": "Raised By", "value": row.get("created_by_name")},
        {"label": "Raised By Department", "value": row.get("raised_by_department_display")},
        {"label": "Description", "value": row.get("description")},
        {"label": "Response By", "value": row.get("response_by_name") or row.get("response_by")},
        {"label": "Response Text", "value": row.get("response_text")},
        {"label": "Root Cause", "value": row.get("root_cause")},
        {"label": "Closure Notes", "value": row.get("closure_notes")},
        {"label": "Closed At", "value": row.get("closed_at")},
    ]

    # Permissions
    status_text = (row.get("status") or "").strip()
    assigned_to_logged_user = bool(current_user_id and safe_int(row.get("nc_assigned_staff_id")) == current_user_id)
    can_manage_case = is_management_session_user()
    has_nc_history = status_text == "Escalated to NC" or bool(safe_int(row.get("escalated_by"))) or bool(safe_int(row.get("nc_assigned_staff_id")))
    can_view_nc_form = has_nc_history and (can_manage_case or assigned_to_logged_user)
    can_edit_nc_form = status_text == "Escalated to NC" and (can_manage_case or assigned_to_logged_user)
    can_escalate = (
        is_nc_control_user(current_user_id)
        and status_text in {"Open", "Responded", "Under Review"}
        and not row.get("escalated_by")
        and status_text != "Escalated to NC"
    )
    can_close = is_nc_control_user(current_user_id) and status_text != "Closed"

    return render_template(
        "venepunchre_detail.html",
        record=row,
        patient_details=patient_details,
        record_items=record_items,
        can_manage_case=can_manage_case,
        can_view_nc_form=can_view_nc_form,
        can_edit_nc_form=can_edit_nc_form,
        can_escalate=can_escalate,
        can_close=can_close,
        auto_open_nc=open_nc and (can_escalate or can_edit_nc_form or can_view_nc_form),
    )


@venepunchre_bp.route("/venepunchre/api/staff-search")
def venepunchre_staff_search():
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login
    query = (request.args.get("q") or "").strip()
    phlebo_only = safe_bool(request.args.get("phlebo"))
    if not query:
        return jsonify([])
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            like = f"%{query}%"
            if phlebo_only:
                placeholders = ",".join(["%s"] * len(PHLEBO_DESIGNATIONS)) or "''"
                cur.execute(
                    f"SELECT id, name, designation FROM users WHERE name LIKE %s AND LOWER(TRIM(status)) = 'active' AND designation IN ({placeholders}) ORDER BY name ASC LIMIT 10",
                    tuple([like] + list(PHLEBO_DESIGNATIONS)),
                )
            else:
                cur.execute(
                    "SELECT id, name, designation FROM users WHERE name LIKE %s AND LOWER(TRIM(status)) = 'active' ORDER BY name ASC LIMIT 10",
                    (like,),
                )
            rows = cur.fetchall() or []
            return jsonify([
                {"id": r[0], "name": r[1], "designation": r[2] if len(r) > 2 else None}
                if isinstance(r, tuple) else r
                for r in rows
            ])
    finally:
        try:
            conn.close()
        except Exception:
            pass


@venepunchre_bp.route("/venepunchre/api/reminders")
def venepunchre_reminders():
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RESPONSE_REMINDER_MINUTES)
    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT hiccup_id, raised_against, status, created_at
            FROM venepunchre_records
            WHERE status IN ('Open','Responded') AND created_at <= %s
            ORDER BY created_at ASC
            """,
            (cutoff.replace(tzinfo=None),),
        )
        rows = cursor.fetchall() or []
        return jsonify(rows)
    finally:
        try:
            cursor.close(); conn.close()
        except Exception:
            pass


@venepunchre_bp.route("/venepunchre/api/patient/<patient_id>")
def venepunchre_patient_lookup(patient_id):
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login
    pid = str(patient_id).strip()
    if not pid.isdigit():
        return jsonify({"message": "Please enter a numeric patient ID."}), 400
    payload = json.dumps({"mobileno": "", "patientid": int(pid)}).encode("utf-8")
    import urllib.request as url_request
    import urllib.error as url_error

    req = url_request.Request(
        PATIENT_LOOKUP_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with url_request.urlopen(req, timeout=PATIENT_LOOKUP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8")
            parsed = json.loads(body)
            rows = parsed.get("data") or []
            if not rows:
                return jsonify({"message": "Patient not found for this patient ID."}), 404
            row = rows[0]
            age_text = (row.get("age") or "").strip()
            age_match = re.search(r"\d+", age_text)
            age_value = age_match.group(0) if age_match else ""
            return jsonify({
                "patient_id": str(row.get("patientid") or pid),
                "name": (row.get("patientname") or "").strip(),
                "age": age_value,
                "gender": (row.get("gender") or "").strip(),
                "mobile_no": (row.get("mobileno") or "").strip(),
            })
    except url_error.HTTPError as exc:
        return jsonify({"message": f"Patient API returned HTTP {exc.code}."}), 502
    except url_error.URLError:
        return jsonify({"message": "Unable to reach patient API."}), 502
    except Exception:
        current_app.logger.exception("Patient lookup failed")
        return jsonify({"message": "Unable to fetch patient details right now."}), 500


@venepunchre_bp.route("/venepunchre/records/<hiccup_id>/escalate", methods=["POST"])
def venepunchre_escalate(hiccup_id):
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    user_id = safe_int(session.get("user_id"))
    if not is_nc_control_user(user_id):
        return jsonify({"message": "Only authorized NC control users can escalate to NC."}), 403

    payload = request.get_json(silent=True) or {}
    assigned_staff_id = safe_int(payload.get("nc_staff_id"))
    assigned_staff_name = (payload.get("nc_staff_name") or "").strip() or None
    if not assigned_staff_id and assigned_staff_name:
        prof = get_staff_by_name(assigned_staff_name)
        if prof:
            assigned_staff_id = prof[0] if isinstance(prof, tuple) else prof.get("id")
            if not assigned_staff_name:
                assigned_staff_name = prof[1] if isinstance(prof, tuple) else prof.get("name")
    if not assigned_staff_id:
        return jsonify({"message": "Please select a valid NC staff before escalating."}), 400
    escalation_form = payload if isinstance(payload, dict) else {}

    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT status, escalated_by, raised_against, description FROM venepunchre_records WHERE hiccup_id=%s LIMIT 1", (hiccup_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"message": "Record not found."}), 404

        status_text = (row.get("status") or "").strip()
        if status_text == "Closed":
            return jsonify({"message": "Record already closed."}), 400
        if status_text == "Escalated to NC" or row.get("escalated_by"):
            return jsonify({"message": "Record already escalated to NC."}), 400
        if status_text not in {"Open", "Responded", "Under Review"}:
            return jsonify({"message": f"Cannot escalate from status {status_text or '-'}."}), 400

        now_dt = _vene_now()
        update_fields = ["status=%s", "escalated_by=%s", "updated_at=%s"]
        update_values = ["Escalated to NC", user_id, now_dt]
        if assigned_staff_id:
            update_fields.append("nc_assigned_staff_id=%s")
            update_values.append(assigned_staff_id)

        cursor.execute(
            f"""
            UPDATE venepunchre_records
            SET {', '.join(update_fields)}
            WHERE hiccup_id=%s
            """,
            tuple(update_values + [hiccup_id]),
        )
        if escalation_form:
            upsert_nc_form(
                cursor,
                hiccup_id,
                {
                    "staff_id": assigned_staff_id,
                    "staff_name": assigned_staff_name,
                    "root_cause_flags": escalation_form.get("root_cause_flags"),
                    "root_cause_other": escalation_form.get("root_cause_other"),
                    "corrective_action": escalation_form.get("corrective_action"),
                    "corrective_action_by": escalation_form.get("corrective_action_by"),
                    "corrective_action_date": escalation_form.get("corrective_action_date"),
                    "person_responsible": escalation_form.get("person_responsible"),
                    "timeline_for_completion": escalation_form.get("timeline_for_completion"),
                    "preventive_actions": escalation_form.get("preventive_actions"),
                    "preventive_other": escalation_form.get("preventive_other"),
                },
            )
        conn.commit()
    except Exception as exc:
        current_app.logger.exception("Failed to escalate venepuncture record %s: %s", hiccup_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"message": "Unable to escalate right now."}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

    # WhatsApp notify assigned staff
    try:
        if assigned_staff_id:
            assignee_profile = get_main_user_by_id(assigned_staff_id)
            contact = None
            assigned_name = assigned_staff_name
            if assignee_profile:
                contact = assignee_profile[3] if isinstance(assignee_profile, tuple) and len(assignee_profile) > 3 else assignee_profile.get("contact")
                assigned_name = assigned_name or (assignee_profile[1] if isinstance(assignee_profile, tuple) else assignee_profile.get("name"))
            if contact:
                msg = build_assignment_whatsapp_message(
                    hiccup_id,
                    assigned_name,
                    row.get("raised_against") if row else None,
                    now_dt,
                    row.get("description") if row else None,
                )
                sent, err = send_whatsapp_message(contact, hiccup_id, assigned_name, message_text=msg, action_type="ven_nc")
                if not sent:
                    current_app.logger.warning("WA escalate failed for %s to %s: %s", hiccup_id, contact, err)
    except Exception as exc:
        current_app.logger.warning("WA notify assignment failed for %s: %s", hiccup_id, exc)

    return jsonify({"message": "NC escalated successfully.", "status": "Escalated to NC"})


@venepunchre_bp.route("/api/venepunchere-cases/<case_id>/nc-form", methods=["GET"])
def venepunchre_get_nc_form(case_id):
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    current_user_id = safe_int(session.get("user_id"))
    can_manage_case = is_management_session_user()

    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT hiccup_id, status, nc_assigned_staff_id, escalated_by FROM venepunchre_records WHERE hiccup_id=%s LIMIT 1", (case_id,))
        case_row = cursor.fetchone()
        if not case_row:
            return jsonify({"message": "Case not found."}), 404

        assigned_to_logged_user = bool(current_user_id and safe_int(case_row.get("nc_assigned_staff_id")) == current_user_id)
        has_nc_history = (case_row.get("status") == "Escalated to NC") or bool(safe_int(case_row.get("escalated_by"))) or bool(safe_int(case_row.get("nc_assigned_staff_id")))
        if not (can_manage_case or assigned_to_logged_user or has_nc_history):
            return jsonify({"message": "Not authorized to view NC form."}), 403

        form_row = fetch_nc_form_row(cursor, case_id)
        if not form_row:
            return jsonify({"message": "NC form not found."}), 404
        return jsonify({"case_id": case_id, "nc_form": normalize_nc_form_response(form_row)})
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) == 1146:
            return jsonify({"message": "NC form table not found."}), 404
        current_app.logger.exception("Failed to fetch NC form %s: %s", case_id, exc)
        return jsonify({"message": "Unable to fetch NC form."}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


@venepunchre_bp.route("/api/venepunchere-cases/<case_id>/nc-form", methods=["PATCH"])
def venepunchre_patch_nc_form(case_id):
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    current_user_id = safe_int(session.get("user_id"))
    can_manage_case = is_management_session_user()
    payload = request.get_json(silent=True) or {}

    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT hiccup_id, status, nc_assigned_staff_id FROM venepunchre_records WHERE hiccup_id=%s LIMIT 1", (case_id,))
        case_row = cursor.fetchone()
        if not case_row:
            return jsonify({"message": "Case not found."}), 404
        assigned_to_logged_user = bool(current_user_id and safe_int(case_row.get("nc_assigned_staff_id")) == current_user_id)
        if not (can_manage_case or assigned_to_logged_user):
            return jsonify({"message": "Not allowed to update NC form."}), 403

        upsert_nc_form(cursor, case_id, payload)
        conn.commit()
        row = fetch_nc_form_row(cursor, case_id)
        return jsonify({"message": "NC form updated.", "case_id": case_id, "nc_form": normalize_nc_form_response(row)})
    except mysql.connector.Error as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        if getattr(exc, "errno", None) == 1146:
            return jsonify({"message": "NC form table not found."}), 404
        current_app.logger.exception("Failed to patch NC form %s: %s", case_id, exc)
        return jsonify({"message": "Unable to update NC form."}), 500
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        current_app.logger.exception("Failed to patch NC form %s: %s", case_id, exc)
        return jsonify({"message": "Unable to update NC form."}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


@venepunchre_bp.route("/venepunchre/records/<hiccup_id>/close", methods=["POST"])
def venepunchre_close(hiccup_id):
    needs_login = _ensure_logged_in()
    if needs_login:
        return needs_login

    user_id = safe_int(session.get("user_id"))
    if not is_nc_control_user(user_id):
        return jsonify({"message": "Only authorized NC control users can close this record."}), 403

    payload = request.get_json(silent=True) or {}
    closure_notes = (payload.get("closure_notes") or "").strip() if isinstance(payload, dict) else ""

    conn = get_venepunchre_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT status FROM venepunchre_records WHERE hiccup_id=%s LIMIT 1", (hiccup_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"message": "Record not found."}), 404

        status_text = (row.get("status") or "").strip()
        if status_text == "Closed":
            return jsonify({"message": "Record already closed."}), 400

        now_dt = _vene_now()
        fields = ["status=%s", "closed_at=%s", "updated_at=%s"]
        values = ["Closed", now_dt, now_dt]
        if closure_notes:
            fields.append("closure_notes=%s")
            values.append(closure_notes)

        cursor.execute(
            f"UPDATE venepunchre_records SET {', '.join(fields)} WHERE hiccup_id=%s",
            tuple(values + [hiccup_id]),
        )
        conn.commit()
        return jsonify({"message": "Record closed.", "status": "Closed"})
    except Exception as exc:
        current_app.logger.exception("Failed to close venepuncture record %s: %s", hiccup_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"message": "Unable to close right now."}), 500
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


# --------- Template filters ---------
@venepunchre_bp.app_template_filter("fmt_dt")
def fmt_dt(value):
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except Exception:
            return value
    try:
        return value.strftime("%d-%b-%Y %H:%M")
    except Exception:
        return str(value)
