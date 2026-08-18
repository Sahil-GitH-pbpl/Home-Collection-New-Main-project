import hmac
import os
import re

from flask import Blueprint, current_app, jsonify, request
from flask_cors import CORS
from werkzeug.utils import secure_filename

from app.alerts import notify_new_lead_async
from app.db.connection import get_db_connection


lead_api_bp = Blueprint("lead_api", __name__)
CORS(
    lead_api_bp,
    resources={
        r"/api/v1/leads": {
            "origins": [
                "https://bhasinpathlabs.com",
                "https://www.bhasinpathlabs.com",
            ]
        }
    },
    methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)

PHONE_RE = re.compile(r"^(?:\+91)?\d{10}$")
VALID_VISIT_WINDOWS = {"Today", "Tomorrow", "Flexible"}
VALID_PATIENT_COUNTS = {"1", "2", ">2"}


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _request_data():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form


@lead_api_bp.route("/api/v1/leads", methods=["POST"])
def create_lead():
    configured_key = (os.getenv("LEAD_API_KEY") or "").strip()
    supplied_key = (request.headers.get("X-API-Key") or "").strip()

    if not configured_key:
        current_app.logger.error("LEAD_API_KEY is not configured")
        return _error("Lead API is not configured", 503)
    if not supplied_key or not hmac.compare_digest(supplied_key, configured_key):
        return _error("Unauthorized", 401)

    data = _request_data()
    name = str(data.get("name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    remarks = str(data.get("remarks") or "").strip()
    visit_window = str(data.get("visit_window") or "Today").strip()
    tags = str(data.get("tags") or "").strip()
    num_patients = str(data.get("num_patients") or "1").strip()
    alt_phone = str(data.get("alt_phone") or "").strip()
    wa_only = 1 if str(data.get("wa_only") or "").lower() in {"1", "true", "yes", "on"} else 0
    alt_wa_only = 1 if str(data.get("alt_wa_only") or "").lower() in {"1", "true", "yes", "on"} else 0

    if not name:
        return _error("name is required", 400)
    if not PHONE_RE.fullmatch(phone):
        return _error("phone must be 10 digits or +91 followed by 10 digits", 400)
    if alt_phone and not PHONE_RE.fullmatch(alt_phone):
        return _error("alt_phone must be 10 digits or +91 followed by 10 digits", 400)
    if not remarks:
        return _error("remarks is required", 400)
    if visit_window not in VALID_VISIT_WINDOWS:
        return _error("visit_window must be Today, Tomorrow, or Flexible", 400)
    if num_patients not in VALID_PATIENT_COUNTS:
        return _error("num_patients must be 1, 2, or >2", 400)

    files = request.files.getlist("prescription[]")
    if len(files) > 6:
        return _error("A maximum of 6 prescription files is allowed", 400)

    created_by = (os.getenv("LEAD_API_CREATED_BY") or "Website API").strip()
    upload_folder = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "static", "uploads", "lead")
    )
    os.makedirs(upload_folder, exist_ok=True)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO leads
                (phone, wa_only, name, alt_phone, alt_wa_only, visit_window, prescription,
                 remarks, tags, num_patients, created_by, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Open')
                """,
                (
                    phone, wa_only, name, alt_phone, alt_wa_only, visit_window, "",
                    remarks, tags, num_patients, created_by,
                ),
            )
            new_id = cur.lastrowid
            lead_id = f"LD-{new_id:03d}"

            saved_files = []
            for uploaded_file in files:
                if not uploaded_file or not uploaded_file.filename:
                    continue
                filename = secure_filename(f"{lead_id}_{uploaded_file.filename}")
                uploaded_file.save(os.path.join(upload_folder, filename))
                saved_files.append(filename)

            cur.execute(
                "UPDATE leads SET lead_id=%s, prescription=%s WHERE id=%s",
                (lead_id, ",".join(saved_files), new_id),
            )
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        current_app.logger.exception("Failed to create lead through API")
        return _error("Unable to create lead", 500)
    finally:
        if conn is not None:
            conn.close()

    notify_new_lead_async(
        lead_id=lead_id,
        phone=phone,
        wa_only=wa_only,
        name=name,
        alt_phone=alt_phone,
        visit_window=visit_window,
        tags=tags,
        num_patients=num_patients,
        remarks=remarks,
        created_by=created_by,
    )

    return jsonify({"success": True, "lead_id": lead_id}), 201
