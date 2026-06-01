# tickets.py — unified route for both CCE and ODT
from flask import render_template 
import json
import re
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional
import pymysql
from flask import Blueprint, jsonify, session, request, current_app, redirect, url_for
from app.db.connection import get_db_connection, get_labmate_connection
from app.alerts import send_whatsapp_to_number, send_whatsapp_document_to_number
from pymysql.cursors import DictCursor

tickets_bp = Blueprint("tickets", __name__)
IST = ZoneInfo("Asia/Kolkata")

# -------------------- helpers --------------------
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _session_snapshot():
    return {
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "designation": session.get("designation"),
    }

def _incoming_data() -> Dict[str, Any]:
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return {}
        return {k: (v if v is not None else "") for k, v in data.items()}
    return request.form.to_dict(flat=True)

def _g(data: Dict[str, Any], key: str, default: str = "") -> str:
    return str(data.get(key) or default)

def _now():
    return datetime.now(IST)


def _safe_report_filename(patient_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (patient_name or "").strip()).strip("_")
    return f"{cleaned or 'Patient'}_Report.pdf"


def _find_recent_cvt(cur, patient_labmate_id: str, mobile_number: str):
    patient_labmate_id = (patient_labmate_id or "").strip()
    mobile_number = (mobile_number or "").strip()

    clauses = []
    params = []

    if patient_labmate_id:
        clauses.append("patient_labmate_id = %s")
        params.append(patient_labmate_id)
    if mobile_number:
        clauses.append("mobile_number = %s")
        params.append(mobile_number)

    if not clauses:
        return None

    cur.execute(
        f"""
        SELECT id, patient_name, patient_labmate_id, mobile_number, created_at, status
        FROM tickets
        WHERE ticket_origin = 'CVT'
          AND created_at >= (NOW() - INTERVAL 24 HOUR)
          AND ({' OR '.join(clauses)})
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        params,
    )
    return cur.fetchone()


def _designation_cf() -> str:
    return _norm(session.get("designation") or session.get("role") or "")


def _has_any_designation(*needles: str) -> bool:
    desig = _designation_cf()
    return any(needle and needle in desig for needle in needles)


def _require_login_json():
    if not session.get("user_id"):
        return jsonify({"ok": False, "error": "Not logged in"}), 401
    return None


def _require_cce_raise_access_json():
    login_err = _require_login_json()
    if login_err:
        return login_err
    if not (_has_any_designation("marketing", "customer care") or _designation_cf() == "admin"):
        return jsonify({"ok": False, "error": "Not authorized to create this ticket type"}), 403
    return None


def _require_cvt_access_page():
    if not session.get("user_id"):
        return redirect(url_for("auth.home"))
    if not (_has_any_designation("customer care") or _designation_cf() == "admin"):
        return redirect(url_for("ticketlist.tickets_list"))
    return None


def _require_cvt_access_json():
    login_err = _require_login_json()
    if login_err:
        return login_err
    if not (_has_any_designation("customer care") or _designation_cf() == "admin"):
        return jsonify({"ok": False, "error": "Not authorized to create CV tickets"}), 403
    return None


def _require_rst_access_page():
    if not session.get("user_id"):
        return redirect(url_for("auth.home"))
    if not (_has_any_designation("technical", "tech") or _designation_cf() == "admin"):
        return redirect(url_for("ticketlist.tickets_list"))
    return None


def _require_rst_access_json():
    login_err = _require_login_json()
    if login_err:
        return login_err
    if not (_has_any_designation("technical", "tech") or _designation_cf() == "admin"):
        return jsonify({"ok": False, "error": "Not authorized to create rejected sample tickets"}), 403
    return None

def _parse_predefined_label_to_dt(label: str) -> Optional[datetime]:
    s = _norm(label)
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    if not m:
        return None
    n = int(m.group(1))
    now = _now()
    if "day" in s:
        return now + timedelta(days=n)
    if "hour" in s:
        return now + timedelta(hours=n)
    if "min" in s:
        return now + timedelta(minutes=n)
    return now + timedelta(minutes=n)

def _parse_custom_to_dt(date_str: str, time_str: str) -> Optional[datetime]:
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
    if not date_str or not time_str:
        return None
    if re.fullmatch(r"\d{2}:\d{2}$", time_str):
        time_str += ":00"
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=IST)
    except Exception:
        return None

def _parse_iso_like_to_dt(iso_like: str) -> Optional[datetime]:
    s = (iso_like or "").strip()
    if not s:
        return None
    s = s.replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", s):
        s += ":00"
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=IST)
    except Exception:
        return None

def parse_minutes(label):
    """Parse minutes from commitment label"""
    if not label:
        return 0
    s = str(label).lower().strip()
    n = int(re.sub(r"[^0-9]", "", s)) if re.search(r"\d", s) else 0
    if "day" in s:
        return n * 24 * 60
    if "hour" in s:
        return n * 60
    if "min" in s:
        return n
    return n

def get_custom_commitment_minutes(commitment_dt):
    """Calculate minutes from now to commitment datetime"""
    now = _now()
    diff = commitment_dt - now
    return max(0, math.floor(diff.total_seconds() / 60))



@tickets_bp.route("/tickets/odt")
def tickets_create_odt_page():
    """Render ODT ticket creation page"""
    if not session.get("user_id"):
        return redirect(url_for("auth.home"))
    return render_template("ticket_create_odt.html")

# -------------------- UNIFIED TICKET CREATE ROUTE --------------------
@tickets_bp.route("/tickets/create", methods=["POST"])
def tickets_create_unified():
    """
    Single unified route for both CCE and ODT tickets
    """
    conn = None
    cursor = None

    try:
        data = _incoming_data()

        # Determine ticket origin (CCE or ODT)
        ticket_origin = _g(data, "ticket_origin") or "CCE"
        if ticket_origin == "CCE":
            auth_err = _require_cce_raise_access_json()
            if auth_err:
                return auth_err

        # Extract common fields
        source = _g(data, "source")
        if not source:
            if _g(data, "patient_name") or _g(data, "patient_id"):
                source = "patient"
            else:
                source = "client"

        country_code = _g(data, "country_code").strip()
        mobile_number = _g(data, "mobile_number").strip()
        patient_name = _g(data, "patient_name").strip() or None
        patient_labmate_id = _g(data, "patient_labmate_id").strip() or None
        
        # ✅ FIXED: client_name handling - don't convert empty string to None
        client_name_raw = _g(data, "client_name")
        client_name = client_name_raw.strip() if client_name_raw.strip() else None
        
        priority = _g(data, "priority").strip() or None

        whatsapp_raw = _g(data, "whatsapp_opt_in")
        whatsapp_opt_in = 1 if _norm(whatsapp_raw) in ("on", "1", "true", "yes") else 0

        ticket_category = _g(data, "ticket_category").strip()
        commitment_at_dt = None

        # Commitment parsing
        direct = _g(data, "commitment_at") or _g(data, "commitment_normalized") or _g(data, "commitment_time")
        if direct:
            commitment_at_dt = _parse_iso_like_to_dt(direct)

        if commitment_at_dt is None:
            commitment_mode = _g(data, "commitment_mode", "predefined").strip() or "predefined"

            if commitment_mode == "predefined":
                label = _g(data, "commitment_predefined").strip()
                if not label:
                    label = _g(data, "commitment_time").strip()
                commitment_at_dt = _parse_predefined_label_to_dt(label)
            else:
                custom_date = _g(data, "commitment_date").strip()
                custom_time = (
                    _g(data, "callbackTime").strip()
                    or _g(data, "callback_time").strip()
                    or _g(data, "commitment_time").strip()
                )
                if custom_date and "T" in custom_time:
                    maybe = _parse_iso_like_to_dt(custom_time)
                    commitment_at_dt = maybe if maybe else _parse_custom_to_dt(custom_date, custom_time)
                else:
                    commitment_at_dt = _parse_custom_to_dt(custom_date, custom_time)

        if commitment_at_dt is None:
            return jsonify({"ok": False, "error": "Unable to determine commitment datetime"}), 400

        if commitment_at_dt <= _now():
            return jsonify({"ok": False, "error": "Commitment must be a future datetime"}), 400

        commitment_at = commitment_at_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Origin-specific validation
        assign_to_user_id = _g(data, "assign_to") or None
        try:
            assign_to_user_id = int(assign_to_user_id) if assign_to_user_id else None
        except ValueError:
            assign_to_user_id = None

        assignment_reason = _g(data, "assignment_reason").strip() or None

        # CCE-specific validation
        if ticket_origin == "CCE":
            if not assign_to_user_id:
                return jsonify({"ok": False, "error": "Assignment is required for CCE tickets"}), 400

        # Tags processing
        tags_json_raw = _g(data, "tags_json") or "[]"
        try:
            parsed_tags = json.loads(tags_json_raw) if tags_json_raw else []
            cleaned_tags = []
            if isinstance(parsed_tags, list):
                for t in parsed_tags:
                    if not isinstance(t, dict):
                        continue
                    cleaned_tags.append({
                        "staffId": t.get("staffId"),
                        "staffName": t.get("staffName"),
                        "dueInMinutes": t.get("dueInMinutes"),
                        "reason": t.get("reason"),
                        "createdAt": t.get("createdAt"),
                        "dueAt": t.get("dueAt"),
                    })
            tags_json = json.dumps(cleaned_tags, ensure_ascii=False)
        except Exception:
            return jsonify({"ok": False, "error": "Invalid tags_json"}), 400

        # Tag validation for CCE
        if ticket_origin == "CCE":
            commitment_minutes = 0
            commitment_mode_for_validation = _g(data, "commitment_mode", "predefined").strip() or "predefined"
            
            if commitment_mode_for_validation == "predefined":
                label = _g(data, "commitment_predefined").strip()
                commitment_minutes = parse_minutes(label)
            else:
                commitment_minutes = get_custom_commitment_minutes(commitment_at_dt)
            
            if commitment_minutes > 30 and len(cleaned_tags) == 0:
                return jsonify({"ok": False, "error": "For commitments >30 minutes, at least one tag is required for CCE tickets"}), 400

        additional_info = _g(data, "additional_info").strip() or None
        created_by = session.get("username")

        # Common validation
        if not ticket_category:
            return jsonify({"ok": False, "error": "ticket_category is required"}), 400

        # ✅ FIXED: Patient mode mein client_name optional, Client mode mein required
        if source == "patient":
            if not country_code or not mobile_number:
                return jsonify({"ok": False, "error": "Patient mode: country_code and mobile_number are required"}), 400
        elif source == "client":
            if not client_name:
                return jsonify({"ok": False, "error": "Client mode: client_name is required"}), 400
            if not mobile_number:
                mobile_number = None
            if not country_code:
                country_code = None
        else:
            return jsonify({"ok": False, "error": f"Unknown source '{source}'"}), 400

        if not mobile_number:
            whatsapp_opt_in = 0

        # Database insertion
        cols = [
            "source", "country_code", "mobile_number",
            "patient_name", "patient_labmate_id",
            "client_name", "priority", "whatsapp_opt_in",
            "ticket_category", "commitment_at",
            "tags_json", "additional_info", "status", "created_by",
            "ticket_origin", "designation"
        ]

        vals = [
            source, country_code, mobile_number,
            patient_name, patient_labmate_id,
            client_name, (priority if priority else None),
            whatsapp_opt_in, ticket_category, commitment_at,
            tags_json, additional_info, "Open", created_by,
            ticket_origin, session.get("designation")
        ]

        if assign_to_user_id is not None:
            cols.append("assign_to_user_id")
            vals.append(assign_to_user_id)
        
        if assignment_reason is not None:
            cols.append("assignment_reason")
            vals.append(assignment_reason)

        placeholders = ", ".join(["%s"] * len(vals))
        sql = f"INSERT INTO tickets ({', '.join(cols)}) VALUES ({placeholders})"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql, vals)
        conn.commit()
        ticket_id = cursor.lastrowid

        # WhatsApp
        if whatsapp_opt_in and country_code and mobile_number:
            try:
                cc = re.sub(r"\D", "", country_code or "91")
                ms = re.sub(r"\D", "", mobile_number or "")
                if ms.startswith("0"):
                    ms = ms[1:]
                phone = f"{cc}{ms}"
                if re.fullmatch(r"\d{8,15}", phone or ""):
                    who = (patient_name or client_name or "Sir/Madam").strip()
                    commit_str = commitment_at_dt.strftime("%d %b %Y, %I:%M %p")
                    msg = (
                        f"Hello {who},\n\n"
                        "✅ Your ticket has been created successfully.\n"
                        f"Ticket ID: {ticket_id}\n"
                        f"Type: {ticket_category}\n"
                        f"Commitment: {commit_str}\n\n"
                        "We will keep you updated. For any help, please contact our helpline.\n"
                        "— Dr. Bhasin's Lab"
                    )
                    SEND_WA = False
                    if SEND_WA:
                        send_whatsapp_to_number(phone, msg)
            except Exception:
                pass

        return jsonify({"ok": True, "ticket_id": ticket_id, "ticket_origin": ticket_origin}), 200

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            if cursor: cursor.close()
        except Exception:
            pass
        try:
            if conn: conn.close()
        except Exception:
            pass


# -------------------- EXISTING ROUTES (NO CHANGES) --------------------
@tickets_bp.route("/tickets/<int:ticket_id>/send-wa", methods=["POST"])
def tickets_send_wa(ticket_id: int):
    """
    Minimal: Resend WhatsApp confirmation for a specific ticket ID.
    """
    conn = get_db_connection()
    with conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT id, country_code, mobile_number, ticket_type,
                       patient_name, client_name, commitment_at
                FROM tickets WHERE id=%s LIMIT 1
            """, (ticket_id,))
            t = cur.fetchone()
            if not t:
                return jsonify({"ok": False, "error": "Ticket not found"}), 404

    cc = re.sub(r"\D", "", (t.get("country_code") or "91"))
    ms = re.sub(r"\D", "", (t.get("mobile_number") or ""))
    if ms.startswith("0"):
        ms = ms[1:]
    phone = f"{cc}{ms}"
    if not re.fullmatch(r"\d{8,15}", phone or ""):
        return jsonify({"ok": False, "error": "Invalid phone"}), 400

    who = (t.get("patient_name") or t.get("client_name") or "Sir/Madam").strip()
    commit = t.get("commitment_at")
    if isinstance(commit, str):
        try:
            commit = datetime.strptime(commit, "%Y-%m-%d %H:%M:%S")
        except Exception:
            commit = None
    commit_str = commit.strftime("%d %b %Y, %I:%M %p") if isinstance(commit, datetime) else "-"

    msg = (
        f"Hello {who},\n\n"
        "✅ Your ticket has been created successfully.\n"
        f"Ticket ID: {t['id']}\n"
        f"Type: {t.get('ticket_category') or '-'}\n"
        f"Commitment: {commit_str}\n\n"
        "We will keep you updated. For any help, please contact our helpline.\n"
        "— Dr. Bhasin’s Lab"
    )

    # 🔴 CHANGED: Guarded send
    SEND_WA = False  # Future me True karna hai
    if SEND_WA:
        status, resp = send_whatsapp_to_number(phone, msg)
    else:
        status, resp = 200, "WA sending disabled (test mode)"

    ok = status in (200, 201)
    return jsonify({
        "ok": ok, "ticket_id": t["id"], "phone": phone,
        "status": status, "response": (resp[:800] if isinstance(resp, str) else str(resp))
    }), (200 if ok else 502)


# -------------------- (existing routes remain as-is) --------------------
@tickets_bp.route("/api/users/customer-care")
def get_customer_care():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, name
                FROM users
                WHERE LOWER(TRIM(status)) = 'active'
                  AND (
                    LOWER(TRIM(designation)) = 'customer care'
                    OR LOWER(TRIM(designation)) = 'customer care executive'
                    OR LOWER(TRIM(designation)) = 'cce'
                    OR LOWER(TRIM(designation)) LIKE '%customer care%'
                  )
                ORDER BY name ASC
            """)
            cce_users = cursor.fetchall() or []
            manual_names = ["shahana parveen", "aman shukla"]
            cursor.execute(f"""
                SELECT id, name
                FROM users
                WHERE LOWER(TRIM(status)) = 'active'
                  AND LOWER(TRIM(name)) IN ({", ".join(["%s"] * len(manual_names))})
            """, manual_names)
            manual_rows = cursor.fetchall() or []
        by_id = {str(u["id"]): {"id": u["id"], "name": u["name"]} for u in cce_users}
        for r in manual_rows:
            by_id[str(r["id"])] = {"id": r["id"], "name": r["name"]}
        combined_users = sorted(by_id.values(), key=lambda x: (x["name"] or "").lower())
        cur_id = session.get("user_id")
        cur_name = session.get("username")
        cur_desig = session.get("designation")
        if not cur_desig and cur_id:
            with conn.cursor(pymysql.cursors.DictCursor) as c2:
                c2.execute(
                    "SELECT designation FROM users WHERE id=%s AND LOWER(TRIM(status)) = 'active' LIMIT 1",
                    (cur_id,),
                )
                row = c2.fetchone()
                cur_desig = row["designation"] if row else None
        norm = (cur_desig or "").strip().lower()
        is_cce = (
            norm in ("customer care", "customer care executive", "cce")
            or "customer care" in norm
            or any(str(u["id"]) == str(cur_id) for u in combined_users)
        )
        return jsonify({
            "users": combined_users,
            "current_user": {
                "id": cur_id,
                "name": cur_name,
                "designation": cur_desig,
                "is_customer_care": bool(is_cce),
            },
            "assign_default_user_id": cur_id
        })
    except Exception as e:
        return jsonify({"error": f"DB error: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()


@tickets_bp.route("/api/users/all-staff")
def get_all_staff():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, name
                FROM users
                WHERE LOWER(TRIM(status)) = 'active'
                ORDER BY name ASC
            """)
            users = cursor.fetchall()
        return jsonify(users)
    except Exception as e:
        return jsonify({"error": f"DB error: {str(e)}"}), 500
    finally:
        if conn:
            conn.close()

@tickets_bp.route("/api/labmate/clients/search", methods=["GET"])
def labmate_clients_search():
    """
    Autocomplete for Client Name (Client mode) from labmate_data.labmate_cd

    Query params:
      q      : search text (min 2 chars) [required]
      limit  : max results (default 25, max 50)
      type   : optional filter on `type` column (e.g., 'client'/'Client')
               if not given, no type filter applied

    Table: labmate_cd (columns: id, type, labmate_id, name)
    Matches on: name LIKE, labmate_id LIKE
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"ok": True, "results": []})

    # cap limit 1..50
    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25
    limit = max(1, min(limit, 50))

    # optional type filter
    type_filter = (request.args.get("type") or "").strip()
    use_type = bool(type_filter)

    like = f"%{q}%"

    # Build SQL with/without type filter
    if use_type:
        sql = """
            SELECT id, type, labmate_id, name
            FROM labmate_cd
            WHERE
                type = %s
                AND (name LIKE %s OR labmate_id LIKE %s)
            ORDER BY name ASC
            LIMIT %s
        """
        params = (type_filter, like, like, limit)
    else:
        sql = """
            SELECT id, type, labmate_id, name
            FROM labmate_cd
            WHERE name LIKE %s OR labmate_id LIKE %s
            ORDER BY name ASC
            LIMIT %s
        """
        params = (like, like, limit)

    try:
        conn = get_labmate_connection()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        # Normalize for frontend
        results = []
        for r in rows:
            # label: "Name – LMID (Type)" jahan jo available ho dikhayen
            pieces = [str(r.get("name") or "").strip()]
            if r.get("labmate_id"):
                pieces.append(str(r["labmate_id"]).strip())
            if r.get("type"):
                pieces.append(f"({str(r['type']).strip()})")
            label = " – ".join([p for p in pieces if p])

            results.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "labmate_id": r.get("labmate_id"),
                "type": r.get("type"),
                "label": label
            })

        return jsonify({"ok": True, "results": results})

    except Exception as e:
        current_app.logger.exception("[labmate_clients_search] error: %s", e)
        return jsonify({"ok": True, "error": "DB error while searching clients"}), 500


# -------------------- CV Ticket Routes --------------------
@tickets_bp.route("/tickets/cv")
def tickets_cv_form():
    """Render Critical Value ticket creation form."""
    auth_err = _require_cvt_access_page()
    if auth_err:
        return auth_err
    return render_template("ticket_create_cv.html")


@tickets_bp.route("/tickets/cv/create", methods=["POST"])
def tickets_cv_create():
    """
    Create a Critical Value (CVT) ticket.
    - Auto sets commitment_at = now + 30 minutes
    - Forces ticket_origin = CVT and ticket_category = Critical Value
    - Sends WhatsApp on creation
    - Persists test rows into cv_ticket_tests
    """
    auth_err = _require_cvt_access_json()
    if auth_err:
        return auth_err
    data = request.get_json(silent=True) or request.form.to_dict(flat=True)

    country_code = (data.get("country_code") or "+91").strip()
    mobile_number = (data.get("mobile_number") or "").strip()
    doctor_mobile = (data.get("doctor_mobile") or "").strip()
    panel_mobile = (data.get("panel_mobile") or "").strip()
    patient_name = (data.get("patient_name") or "").strip()
    patient_labmate_id = (data.get("patient_labmate_id") or "").strip()
    assign_to_user_id = data.get("assign_to_user_id")
    additional_info = (data.get("additional_info") or "").strip() or None
    report_url = (
        (data.get("report_url") or "")
        or (data.get("report_link") or "")
        or (data.get("link") or "")
        or (data.get("pdffile") or "")
        or (data.get("report") or "")
    ).strip()
    doc_pan_json = json.dumps({
        "doctor": {
            "name": (data.get("doctor") or "").strip(),
            "mobile": (data.get("doctor_mobile") or "").strip()
        },
        "panel": {
            "name": (data.get("panel") or "").strip(),
            "mobile": (data.get("panel_mobile") or "").strip()
        }
    })

    tests = data.get("tests") or []
    if isinstance(tests, str):
        try:
            tests = json.loads(tests)
        except Exception:
            tests = []
    first_test_name = ""
    if isinstance(tests, list):
        for t in tests:
            if not isinstance(t, dict):
                continue
            first_test_name = (t.get("test_name") or "").strip()
            if first_test_name:
                break

    # Basic validation (mobile optional because API may not send)
    if not patient_name:
        return jsonify({"ok": False, "error": "Patient name required"}), 400

    try:
        assign_to_user_id = int(assign_to_user_id) if assign_to_user_id else None
    except ValueError:
        assign_to_user_id = None

    commitment_at = (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    created_by = session.get("username")
    designation = session.get("designation")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            existing_cvt = _find_recent_cvt(cur, patient_labmate_id, mobile_number)
            if existing_cvt:
                existing_id = existing_cvt.get("id")
                existing_created = existing_cvt.get("created_at")
                created_text = (
                    existing_created.strftime("%d-%b-%Y %I:%M %p")
                    if isinstance(existing_created, datetime)
                    else str(existing_created or "").strip()
                )
                return jsonify({
                    "ok": False,
                    "error": (
                        "CVT already exists for this patient within the last 24 hours."
                        + (f" Existing Ticket ID: {existing_id}" if existing_id else "")
                        + (f" ({created_text})" if created_text else "")
                    ),
                    "duplicate_ticket_id": existing_id,
                }), 409

            # Insert main ticket
            cur.execute(
                """
                INSERT INTO tickets
                (source, country_code, mobile_number, patient_name, patient_labmate_id,
                 client_name, priority, whatsapp_opt_in, ticket_category, commitment_at,
                 assign_to_user_id, assignment_reason, tags_json, additional_info, doc_pan_json,
                 status, created_by, designation, created_at, ticket_origin)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
                """,
                (
                    "patient",
                    country_code,
                    mobile_number,
                    patient_name,
                    patient_labmate_id,
                    None,               # client_name
                    "High",             # priority
                    1,                  # whatsapp_opt_in
                    "Critical Value",   # ticket_category
                    commitment_at,
                    assign_to_user_id,
                    None,               # assignment_reason
                    None,               # tags_json
                    additional_info,
                    doc_pan_json,
                    "Open",
                    created_by,
                    designation,
                    "CVT",
                ),
            )
            ticket_id = cur.lastrowid

            # Insert tests
            rows = []
            for t in tests:
                rows.append(
                    (
                        ticket_id,
                        (t.get("test_name") or "").strip(),
                        (t.get("value_text") or "").strip() or None,
                        (t.get("result_text") or "").strip() or None,
                        (t.get("interp_text") or "").strip() or None,
                    )
                )
            rows = [r for r in rows if r[1]]
            if rows:
                cur.executemany(
                    """
                    INSERT INTO cv_ticket_tests
                        (ticket_id, test_name, value_text, result_text, interp_text)
                    VALUES (%s,%s,%s,%s,%s)
                    """,
                    rows,
                )

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # WhatsApp on create: patient / doctor / panel (supports @g.us group IDs too)
    test_name_for_msg = first_test_name or "test"
    msg = (
        f"⚠️ IMPORTANT: URGENT LAB RESULT\n"
        f"Dear {patient_name or 'Patient'}\n"
        f"Your {test_name_for_msg} report is attached and requires urgent clinical review.\n"
        f"Kindly consult your treating doctor immediately.\n"
        f"This message does not replace medical advice."
    )

    patient_target = ""
    if mobile_number:
        cc_digits = re.sub(r"\D", "", country_code or "91")
        ms_digits = re.sub(r"\D", "", mobile_number or "")
        if ms_digits.startswith("0"):
            ms_digits = ms_digits[1:]
        patient_target = f"{cc_digits}{ms_digits}" if ms_digits else ""

    recipients = [patient_target, doctor_mobile, panel_mobile]
    seen = set()
    unique_recipients = []
    for raw in recipients:
        target = (raw or "").strip()
        if not target:
            continue
        key = target.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_recipients.append(target)

    for target in unique_recipients:
        try:
            if report_url:
                status_code, _ = send_whatsapp_document_to_number(
                    target,
                    msg,
                    report_url,
                    filename=_safe_report_filename(patient_name),
                )
                if status_code not in (200, 201):
                    # Fallback so critical info still reaches even if attachment API fails.
                    send_whatsapp_to_number(target, f"{msg}\n\nReport: {report_url}")
            else:
                send_whatsapp_to_number(target, msg)
        except Exception:
            current_app.logger.exception("[tickets_cv_create] WA send failed for target=%s", target)

    return jsonify({"ok": True, "ticket_id": ticket_id}), 200


@tickets_bp.route("/tickets/cv/open")
def tickets_cv_open():
    """List open CV tickets with test details."""
    conn = get_db_connection()
    tickets = []
    tests_by = {}
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute(
                """
                SELECT id, patient_name, patient_labmate_id, mobile_number,
                       assign_to_user_id, commitment_at, created_at, status
                FROM tickets
                WHERE ticket_origin='CVT'
                  AND (status IS NULL OR status='' OR status='Open' OR status='open')
                ORDER BY commitment_at ASC
                """
            )
            tickets = cur.fetchall() or []
            ids = [t["id"] for t in tickets]
            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"""
                    SELECT ticket_id, test_name, value_text, result_text, interp_text
                    FROM cv_ticket_tests
                    WHERE ticket_id IN ({placeholders})
                    ORDER BY id ASC
                    """,
                    tuple(ids),
                )
                for r in cur.fetchall():
                    tests_by.setdefault(r["ticket_id"], []).append(r)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return render_template("cv_ticket_list.html", tickets=tickets, tests_by=tests_by)

# -------------------- Rejected Sample (RST) Routes --------------------
@tickets_bp.route("/tickets/rejected-sample")
def tickets_rs_form():
    """Render Rejected Sample ticket creation form."""
    auth_err = _require_rst_access_page()
    if auth_err:
        return auth_err
    return render_template("ticket_create_rs.html")


@tickets_bp.route("/tickets/rs/create", methods=["POST"])
def tickets_rs_create():
    """
    Create Rejected Sample ticket.
    - ticket_origin = RST
    - ticket_category = Rejected Sample
    - commitment_at = now + 60 minutes
    - no assignment (assign_to_user_id NULL)
    - stores sample_type & rejection_reason
    """
    auth_err = _require_rst_access_json()
    if auth_err:
        return auth_err
    data = request.get_json(silent=True) or request.form.to_dict(flat=True)
    country_code = (data.get("country_code") or "+91").strip()
    mobile_number = (data.get("mobile_number") or "").strip()
    doctor_mobile = (data.get("doctor_mobile") or "").strip()
    panel_mobile = (data.get("panel_mobile") or "").strip()
    patient_name = (data.get("patient_name") or "").strip()
    patient_labmate_id = (data.get("patient_labmate_id") or "").strip()
    sample_type = (data.get("sample_type") or "").strip()
    rejection_reason = (data.get("rejection_reason") or "").strip() or None
    doc_pan_json = json.dumps({
        "doctor": {
            "name": (data.get("doctor") or "").strip(),
            "mobile": (data.get("doctor_mobile") or "").strip()
        },
        "panel": {
            "name": (data.get("panel") or "").strip(),
            "mobile": (data.get("panel_mobile") or "").strip()
        }
    })

    if not patient_name:
        return jsonify({"ok": False, "error": "Patient name required"}), 400

    commitment_at = (datetime.now() + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
    created_by = session.get("username")
    designation = session.get("designation")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tickets
                (source, country_code, mobile_number, patient_name, patient_labmate_id,
                 client_name, priority, whatsapp_opt_in, ticket_category, commitment_at,
                 assign_to_user_id, assignment_reason, tags_json, additional_info,
                 doc_pan_json, status, created_by, designation, created_at, ticket_origin,
                 sample_type, rejection_reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s)
                """,
                (
                    "patient",
                    country_code,
                    mobile_number,
                    patient_name,
                    patient_labmate_id,
                    None,
                    "High",
                    1,
                    "Rejected Sample",
                    commitment_at,
                    None,           # assign_to_user_id
                    None,           # assignment_reason
                    None,           # tags_json
                    None,           # additional_info
                    doc_pan_json,
                    "Open",
                    created_by,
                    designation,
                    "RST",
                    sample_type if sample_type else None,
                    rejection_reason,
                ),
            )
            ticket_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # WhatsApp send (disabled for now) - recipients would include patient, doctor, panel mobiles
    # recipients = [m for m in [mobile_number, doctor_mobile, panel_mobile] if m]
    # TODO: add WA send when enabled

    return jsonify({"ok": True, "ticket_id": ticket_id}), 200


@tickets_bp.route("/api/debug/session")
def debug_session():
    return jsonify(_session_snapshot())

@tickets_bp.route("/api/proxy/labmate-patient", methods=["POST"])
def proxy_labmate_patient():
    try:
        import requests 
        
        data = request.get_json()
        patient_id = (data.get("patientid") or "").strip()
        
        if not patient_id:
            return jsonify({"ok": False, "error": "Patient ID required"}), 400

        # Updated Labmate patient fetch endpoint (internal)
        labmate_url = "http://10.1.1.252:8000/reportapi/LabmatePatRegistration.svc/Getpatientdatabymobileno"
        
        response = requests.post(
            labmate_url,
            json={"mobileno": "", "patientid": patient_id},
            timeout=10
        )
        
        if response.status_code != 200:
            return jsonify({"ok": False, "error": f"API returned {response.status_code}"}), 500

        j = response.json() or {}
        data_arr = j.get("data") or []
        first = data_arr[0] if data_arr else {}
        payload = {
            "ok": True,
            "raw": first,
            "data": data_arr,  # backward compatibility for old JS expecting data.data
            "name": first.get("patientname") or first.get("name"),
            "mobile": first.get("mobileno") or first.get("mobile") or first.get("whatsapp"),
            "patientid": first.get("patientid"),
            "patientname": first.get("patientname"),
            "mobileno": first.get("mobileno"),
            "doctor": first.get("doctor"),
            "doctormobile": first.get("doctormobile"),
            "panel": first.get("panel"),
            "whatsapp": first.get("whatsapp"),  # panel contact (used as panel mobile)
            "report_url": (
                first.get("report_url")
                or first.get("report_link")
                or first.get("link")
                or first.get("url")
                or first.get("pdffile")
                or first.get("report")
            ),
        }
        return jsonify(payload)
            
    except requests.exceptions.RequestException as e:
        return jsonify({"ok": False, "error": f"API connection failed"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"Server error"}), 500
