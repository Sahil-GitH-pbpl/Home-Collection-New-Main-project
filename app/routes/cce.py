from flask import Blueprint, render_template, jsonify, request, current_app, session
import os
import time
import requests
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection
from typing import Tuple, Optional

cce_bp = Blueprint("cce", __name__, template_folder="../templates")

# ------------------ Configuration ------------------
DEFAULT_EXOTEL_PORT = int(os.getenv("EXOTEL_PORT", "8002"))
EXOTEL_TIMEOUT = float(os.getenv("EXOTEL_HTTP_TIMEOUT", "4.0"))

EXOTEL_PRELOAD_PATHS = os.getenv(
    "EXOTEL_PRELOAD_PATHS",
    "/calls/live,/calls/recent,/raw"
).split(",")

EXOTEL_ACCEPT_PATHS = os.getenv(
    "EXOTEL_ACCEPT_PATHS",
    "/calls/accept,/accept"
).split(",")

# Persist unclaimed completed calls for refresh survivability
PERSIST_WINDOW_MINUTES = int(os.getenv("CCE_PERSIST_WINDOW_MINUTES", "720"))

# Exotel API credentials (fallback to legacy defaults if env/config missing)
EXOTEL_API_KEY_FALLBACK = os.getenv("EXOTEL_API_KEY", "bhasinpathlabs")
EXOTEL_API_TOKEN_FALLBACK = os.getenv("EXOTEL_API_TOKEN", "284fd844c57bf078f7ba2b0491ffeabcb1590cf1")
EXOTEL_SID_FALLBACK = os.getenv("EXOTEL_SID", "bhasinpathlabs")  # older setup used key as account name
EXOTEL_CALLER_ID_FALLBACK = os.getenv("EXOTEL_CALLER_ID", "01141194585")  # Exotel-approved company number
EXOTEL_SUBDOMAIN_FALLBACK = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
# Default CCE landline used when UI does not pass a selected extension
CCE_NUMBER_FALLBACK = os.getenv("CCE_NUMBER_FALLBACK", "01149989851")

# Terminal set (must match frontend)
TERMINAL_TYPES = {
    "completed","canceled","failed","busy","no-answer","not-answered",
    "hangup","client-hangup","machine-hangup"
}

# Landline list mirrors legacy PHP options
LANDLINE_OPTIONS = [
    "01149989851",  # cc ext1
    "01149989859",  # cc ext2
    "01149989865",  # cc ext3
    "01149989868",  # cc ext4
    "01149989861",  # cc ext5
    "01149989867",  # cc ext6
    "01149989869",  # cc ext7
    "01149989881",  # resp ext1
    "01149989880",  # resp ext2
    "01149989882",  # resp ext3
    "01149989877",  # shahana
]

# ------------------ Helpers ------------------
def _resolve_exotel_host() -> str:
    host = (
        current_app.config.get("EXOTEL_HOST")
        or os.getenv("EXOTEL_HOST")
        or request.host.split("/")[0].split(":")[0]
    )
    return host

def _resolve_exotel_port() -> int:
    return int(current_app.config.get("EXOTEL_PORT", DEFAULT_EXOTEL_PORT))

def _resolve_exotel_creds() -> Tuple[str, str, str, str]:
    """
    Pull Exotel REST creds. CallerId must be your Exotel-approved number.
    Env/config keys: EXOTEL_API_KEY, EXOTEL_API_TOKEN, EXOTEL_SID, EXOTEL_CALLER_ID.
    """
    key = current_app.config.get("EXOTEL_API_KEY") or os.getenv("EXOTEL_API_KEY") or EXOTEL_API_KEY_FALLBACK
    token = current_app.config.get("EXOTEL_API_TOKEN") or os.getenv("EXOTEL_API_TOKEN") or EXOTEL_API_TOKEN_FALLBACK
    sid = current_app.config.get("EXOTEL_SID") or os.getenv("EXOTEL_SID") or EXOTEL_SID_FALLBACK
    caller = current_app.config.get("EXOTEL_CALLER_ID") or os.getenv("EXOTEL_CALLER_ID") or EXOTEL_CALLER_ID_FALLBACK
    if not all([key, token, sid, caller]):
        raise RuntimeError("Exotel credentials missing")
    return key, token, sid, caller

def _digits_only(num: str) -> str:
    return "".join([c for c in (num or "") if c.isdigit()])

# ------------------ Routes ------------------

@cce_bp.route("/cce")
def live_page():
    exo_host = _resolve_exotel_host()
    exo_port = _resolve_exotel_port()

    ws_scheme = "wss" if request.is_secure else "ws"
    ws_url = current_app.config.get("EXOTEL_WS_URL") or f"{ws_scheme}://{exo_host}:{exo_port}"
    http_url = f"http://{exo_host}:{exo_port}"

    me_id = session.get("user_id") or ""
    me_name = session.get("username") or ""

    return render_template(
        "cce.html",
        WS_URL=ws_url,
        EXOTEL_HTTP=http_url,
        SESSION_USER_ID=me_id,
        SESSION_USER_NAME=me_name,
    )

# ------------------ Matches lookup (unchanged logic) ------------------
@cce_bp.route("/cce/matches")
def cce_matches():
    phone = (request.args.get("phone") or "").strip()
    if not phone:
        return jsonify({"ok": False, "error": "Phone required"}), 400

    phone_digits = "".join([c for c in phone if c.isdigit()])
    if len(phone_digits) < 6:
        return jsonify({"ok": False, "error": "Invalid phone"}), 400

    conn = get_db_connection()
    matches = {"tickets": [], "leads": []}

    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT 
                    id,
                    ticket_type,
                    patient_name,
                    client_name,
                    mobile_number,
                    status,
                    created_at
                FROM tickets
                WHERE REPLACE(REPLACE(REPLACE(mobile_number, ' ', ''), '-', ''), '+', '') LIKE %s
                  AND status = 'Open'
                  AND created_at >= NOW() - INTERVAL 7 DAY
                ORDER BY created_at DESC
            """, (f"%{phone_digits[-10:] or phone_digits}%",))
            tickets = cur.fetchall() or []

            cur.execute("""
                SELECT 
                    lead_id,
                    name,
                    phone,
                    status,
                    created_at
                FROM leads
                WHERE REPLACE(REPLACE(REPLACE(phone, ' ', ''), '-', ''), '+', '') LIKE %s
                  AND status NOT IN ('Booked', 'Canceled')
                ORDER BY created_at DESC
            """, (f"%{phone_digits[-10:] or phone_digits}%",))
            leads = cur.fetchall() or []

        matches["tickets"] = tickets
        matches["leads"] = leads

        ticket_label = (tickets[0]["ticket_type"] if tickets else "") or ""
        lead_label = (leads[0]["name"] if leads else "") or ""
        display_label = ticket_label or lead_label or "-"

        mobile = (
            (tickets[0]["mobile_number"] if tickets else None)
            or (leads[0]["phone"] if leads else None)
            or phone
        )

        summary = {
            "label": display_label,
            "name": lead_label or "",
            "mobile": mobile or phone,
            "ticket_count": len(tickets),
            "lead_count": len(leads)
        }

        return jsonify({"ok": True, "matches": matches, "summary": summary})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

# ------------------ Missed calls (uses exotel_incoming_calls) ------------------
@cce_bp.route("/cce/missed")
def missed_calls_list():
    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT 
                    i.id,
                    i.call_sid,
                    i.from_number,
                    i.to_number,
                    i.call_type,
                    i.callback_by_name,
                    i.accepted_by_name,
                    i.created_at
                FROM exotel_incoming_calls i
                WHERE 
                    (
                        LOWER(i.call_type) = 'client-hangup'
                        OR (
                            LOWER(i.call_type) = 'call-attempt'
                            AND i.created_at <= NOW() - INTERVAL 15 MINUTE
                        )
                        OR LOWER(i.call_type) = 'incomplete'
                    )
                    AND LOWER(i.call_type) <> 'completed'
                    AND NOT EXISTS (
                        SELECT 1 FROM exotel_outgoing_calls o 
                        WHERE o.call_sid = i.call_sid
                    )
                ORDER BY i.created_at DESC
                LIMIT 200
            """)
            rows = cur.fetchall() or []

        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ------------------ Persist unclaimed terminals (refresh-safe popups) ------------------
@cce_bp.route("/cce/persist")
def persist_unclaimed_terminals():
    window = max(1, int(request.args.get("minutes", PERSIST_WINDOW_MINUTES)))
    placeholders = ",".join(["%s"] * len(TERMINAL_TYPES))
    sql = f"""
        SELECT 
            call_sid,
            from_number,
            to_number,
            call_type,
            accepted_by_name,
            accepted_by_id,
            call_related_to,
            created_at
        FROM exotel_incoming_calls
        WHERE created_at >= NOW() - INTERVAL %s MINUTE
          AND (
            (
              LOWER(call_type) IN ({placeholders})
              AND (accepted_by_name IS NULL OR accepted_by_name = '')
            )
            OR (
              accepted_by_name IS NOT NULL
              AND accepted_by_name != ''
              AND (call_related_to IS NULL OR call_related_to = '')
            )
          )
        ORDER BY created_at DESC
        LIMIT 200
    """
    params = (window,) + tuple(t.lower() for t in TERMINAL_TYPES)

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() or []

        data = []
        for r in rows:
            data.append({
                "call_sid": r["call_sid"],
                "from_number": r["from_number"],
                "to_number": r["to_number"],
                "call_type": r["call_type"],
                "accepted_by_name": r.get("accepted_by_name") or "",
                "accepted_by_id": r.get("accepted_by_id") or "",
                "created_at": r["created_at"],
                "dial_call_status": "completed",
                "direction": "incoming",
            })
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()

# ------------------ Raw & Accept proxy (unchanged behaviour) ------------------
@cce_bp.route("/cce/raw")
def raw_proxy():
    exo_host = _resolve_exotel_host()
    exo_port = _resolve_exotel_port()
    base = f"http://{exo_host}:{exo_port}"

    last_err = None
    for path in EXOTEL_PRELOAD_PATHS:
        url = f"{base}{path.strip()}"
        try:
            limit = request.args.get("limit", "50")
            params = {"limit": limit} if any(k in path for k in ("raw", "recent", "live")) else {}
            resp = requests.get(url, params=params, timeout=EXOTEL_TIMEOUT)
            if resp.status_code == 404:
                last_err = f"404 {url}"
                continue
            resp.raise_for_status()
            data = resp.json() or []
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            return jsonify({"status": "ok", "data": data}), 200
        except Exception as e:
            last_err = e
            continue

    return jsonify({"status": "ok", "data": [], "message": "No preload endpoint found"}), 200


@cce_bp.route("/cce/accept", methods=["POST"])
def accept_proxy():
    exo_host = _resolve_exotel_host()
    exo_port = _resolve_exotel_port()
    base = f"http://{exo_host}:{exo_port}"

    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    call_sid = (payload.get("call_sid") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if not call_sid:
        return jsonify({"status": "error", "message": "call_sid required"}), 400

    forward = {
        "call_sid": call_sid,
        "phone": phone,
        "accepted_by_name": user_name,
        "accepted_by_id": user_id or None,
    }
    headers = {"X-User-Name": user_name}

    last_err = None
    for path in EXOTEL_ACCEPT_PATHS:
        url = f"{base}{path.strip()}"
        try:
            r = requests.post(url, json=forward, headers=headers, timeout=EXOTEL_TIMEOUT)
            if r.status_code == 404:
                last_err = f"404 at {url}"
                continue
            ok = 200 <= r.status_code < 300
            j = {}
            try:
                j = r.json() if r.content else {}
            except Exception:
                j = {"message": r.text}

            if ok:
                try:
                    conn = get_db_connection()
                    with conn.cursor(DictCursor) as cur:
                        cur.execute("""
                            UPDATE exotel_incoming_calls
                            SET accepted_by_name = %s,
                                accepted_by_id   = %s,
                                accepted_at      = NOW()
                            WHERE call_sid = %s
                            LIMIT 1
                        """, (user_name, user_id, call_sid))
                    conn.commit()
                except Exception:
                    pass
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

                j.setdefault("status", "ok")
                j.setdefault("accepted_by_name", user_name)
                if user_id:
                    j.setdefault("accepted_by_id", user_id)
                return jsonify(j), 200
            else:
                msg = j.get("detail") or j.get("message") or f"Accept failed at {url}"
                return jsonify({"status": "error", "message": msg}), 409
        except requests.exceptions.ConnectionError as ce:
            last_err = f"Connection error {url}: {ce}"
        except Exception as e:
            last_err = f"Error {url}: {e}"

    return jsonify({"status": "error", "message": "Accept endpoint not found on listener"}), 502


# ------------------ NEW: Outgoing callback (Exotel Connect) ------------------
@cce_bp.route("/cce/callback", methods=["POST"])
def cce_callback():
    """
    Initiate outbound (agent -> patient) via Exotel Connect.
    Steps:
      1) Validate user + numbers
      2) Insert log in exotel_outgoing_calls
      3) Hit Exotel connect (first rings our landline, then bridges to patient)
      4) Update the same row with CallSid/status
    """
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    to_number_raw = payload.get("to_number") or payload.get("phone") or ""
    from_number_raw = payload.get("from_number") or payload.get("landline") or ""
    incoming_sid = (payload.get("call_sid") or "").strip()

    to_number = _digits_only(to_number_raw)
    from_number = _digits_only(from_number_raw)

    if len(to_number) < 10:
        return jsonify({"status": "error", "message": "Invalid patient number"}), 400
    if from_number not in LANDLINE_OPTIONS:
        return jsonify({"status": "error", "message": "Invalid or missing landline"}), 400

    try:
        api_key, api_token, exo_sid, caller_id = _resolve_exotel_creds()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    conn = get_db_connection()
    row_id: Optional[int] = None
    try:
        init_sid = incoming_sid or ""  # use incoming SID as primary
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                INSERT INTO exotel_outgoing_calls
                    (call_sid, from_number, to_number, call_status, dial_call_status,
                     dial_call_duration, callback_by_id, callback_by_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                init_sid,
                from_number,
                to_number,
                "initiated",
                "initiated",
                0,
                user_id,
                user_name,
            ))
            row_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.rollback()
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({"status": "error", "message": f"DB error (insert): {e}"}), 500

    exotel_url = f"https://{api_key}:{api_token}@api.exotel.com/v1/Accounts/{exo_sid}/Calls/connect"
    post_data = {
        "From": from_number,
        "To": to_number,
        "CallerId": caller_id,
    }

    call_sid = None
    status_txt = "initiated"
    err_msg = None

    try:
        resp = requests.post(exotel_url, data=post_data, timeout=EXOTEL_TIMEOUT)
        ok = 200 <= resp.status_code < 300
        status_txt = "initiated" if ok else f"failed ({resp.status_code})"
        try:
            j = resp.json()
            call_sid = (
                j.get("Call", {}).get("Sid")
                or j.get("call", {}).get("sid")
                or j.get("sid")
            )
        except Exception:
            call_sid = None
        if not ok:
            err_msg = resp.text or "Exotel connect failed"
    except requests.exceptions.RequestException as e:
        status_txt = "failed"
        err_msg = str(e)

    try:
        final_sid = call_sid or init_sid or f"CB_{int(time.time())}"
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                UPDATE exotel_outgoing_calls
                SET call_sid = %s,
                    call_status = %s,
                    dial_call_status = %s
                WHERE id = %s
                LIMIT 1
            """, (final_sid, status_txt, status_txt, row_id))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Also mark matching incoming as completed (so missed/popup clear)
    if incoming_sid:
        try:
            with get_db_connection() as c:
                with c.cursor(DictCursor) as cur:
                    cur.execute("""
                        UPDATE exotel_incoming_calls
                        SET call_type = 'completed',
                            dial_call_status = COALESCE(dial_call_status, 'completed')
                        WHERE call_sid = %s
                        LIMIT 1
                    """, (incoming_sid,))
                c.commit()
        except Exception:
            pass

    if status_txt.startswith("failed"):
        return jsonify({"status": "error", "message": err_msg or status_txt}), 502

    return jsonify({
        "status": "ok",
        "call_sid": call_sid,
        "message": "Call initiated: first your landline will ring, then the patient."
    }), 200

# ------------------ NEW: Complete endpoint (with call_related_to) ------------------
@cce_bp.route("/cce/complete", methods=["POST"])
def cce_complete():
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    call_sid = (payload.get("call_sid") or "").strip()
    related = (payload.get("call_related_to") or "").strip()

    if not call_sid:
        return jsonify({"status": "error", "message": "call_sid required"}), 400
    
    # ✅ UPDATE YEH LINE - "Report Query" add karo
    if related not in ("Lead", "Ticket", "Home Collection Appointment", "Report Query", "Test Inquiry", "Spam Call"):
        return jsonify({"status": "error", "message": "Invalid call_related_to"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                UPDATE exotel_incoming_calls
                SET call_related_to = %s
                WHERE call_sid = %s
                        
                  AND (accepted_by_id IS NULL OR accepted_by_id = %s)
                LIMIT 1
            """, (related, call_sid, user_id))
            rows = cur.rowcount

        conn.commit()
        if rows == 0:
            return jsonify({"status": "error", "message": "Already handled by another user or not allowed"}), 404
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()
# ------------------ NEW: Release endpoint (Accepted by Mistake) ------------------
@cce_bp.route("/cce/release", methods=["POST"])
def cce_release():
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    call_sid = (payload.get("call_sid") or "").strip()
    if not call_sid:
        return jsonify({"status": "error", "message": "call_sid required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                UPDATE exotel_incoming_calls
                SET accepted_by_name = NULL,
                    accepted_by_id   = NULL,
                    released_by_name = %s,
                    released_at      = NOW()
                WHERE call_sid = %s
                LIMIT 1
            """, (user_name, call_sid))
        conn.commit()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


# ------------------ NEW: Call Back Endpoint (CCE first, then patient) ------------------
@cce_bp.route("/cce/make-call", methods=["POST"])
def make_call():
    """
    Legacy-style flow: first ring CCE number, then bridge to patient.
    Uses Exotel connect with Sequence=sequential.
    """
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    incoming_sid = (payload.get("call_sid") or "").strip()
    patient_number_raw = (payload.get("to") or payload.get("patient") or "").strip()
    if not patient_number_raw:
        return jsonify({"status": "error", "message": "Patient number required"}), 400

    # Normalize numbers
    patient_digits = _digits_only(patient_number_raw)
    if len(patient_digits) == 10:
        patient_digits = "91" + patient_digits  # Exotel expects country code style digits
    cce_number = _digits_only(payload.get("cce_number") or CCE_NUMBER_FALLBACK)
    if not cce_number:
        return jsonify({"status": "error", "message": "CCE number missing"}), 400

    try:
        api_key, api_token, exo_sid, caller_id = _resolve_exotel_creds()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    exo_subdomain = current_app.config.get("EXOTEL_SUBDOMAIN") or os.getenv("EXOTEL_SUBDOMAIN") or EXOTEL_SUBDOMAIN_FALLBACK
    company_number = _digits_only(current_app.config.get("EXOTEL_COMPANY_NUMBER") or os.getenv("EXOTEL_COMPANY_NUMBER") or EXOTEL_CALLER_ID_FALLBACK)

    # In Connect API: From rings first, then To is bridged. Use CCE as From, patient as To.
    call_sequence = patient_digits
    exotel_url = f"https://{exo_subdomain}/v1/Accounts/{exo_sid}/Calls/connect"

    try:
        resp = requests.post(
            exotel_url,
            auth=(api_key, api_token),
            data={
                "From": cce_number,           # first leg: CCE landline
                "To": call_sequence,         # second leg: patient
                "CallerId": caller_id,
                "CallType": "trans",
                "Timeout": "15"
            },
            timeout=EXOTEL_TIMEOUT,
            verify=False  # match legacy behaviour; Exotel uses trusted certs but keep off to avoid SSL issues
        )
    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": f"Exotel connect failed: {e}"}), 502

    if not (200 <= resp.status_code < 300):
        return jsonify({"status": "error", "message": f"Exotel API failed: {resp.status_code}", "detail": resp.text}), 500

    call_sid = None
    call_status = "initiated"
    dial_duration = 0
    try:
        j = resp.json() if resp.content else {}
        call_info = j.get("Call") or j.get("call") or {}
        call_sid = call_info.get("Sid") or call_info.get("sid")
        call_status = call_info.get("Status") or call_info.get("status") or "initiated"
        dial_duration = call_info.get("Duration") or call_info.get("duration") or 0
    except Exception:
        pass

    # Log into outgoing table (matches existing schema)
    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                INSERT INTO exotel_outgoing_calls
                    (call_sid, from_number, to_number, call_status, dial_call_status,
                     dial_call_duration, callback_by_id, callback_by_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                call_sid or incoming_sid or f"CB_{int(time.time())}",
                cce_number,
                call_sequence,
                call_status or "initiated",
                call_status or "initiated",
                int(dial_duration or 0),
                user_id,
                user_name,
            ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": f"DB error (insert): {e}"}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass
    # Also mark matching incoming as completed (so missed/popup clear)
    if incoming_sid:
        try:
            with get_db_connection() as c:
                with c.cursor(DictCursor) as cur:
                    cur.execute("""
                        UPDATE exotel_incoming_calls
                        SET call_type = 'completed',
                            dial_call_status = COALESCE(dial_call_status, 'completed')
                        WHERE call_sid = %s
                        LIMIT 1
                    """, (incoming_sid,))
                c.commit()
        except Exception:
            pass

    return jsonify({
        "status": "ok",
        "message": "Call initiated (CCE first, then patient)",
        "call_sid": call_sid,
        "call_status": call_status,
        "to": call_sequence
    }), 200


# ------------------ NEW: Last Claimant Info ------------------
@cce_bp.route("/cce/last-claimant")
def last_claimant():
    phone = (request.args.get("phone") or "").strip()
    if not phone:
        return jsonify({"ok": False, "error": "Phone required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT accepted_by_name 
                FROM exotel_incoming_calls 
                WHERE from_number = %s 
                  AND accepted_by_name IS NOT NULL 
                  AND accepted_by_name != ''
                  AND created_at >= NOW() - INTERVAL 72 HOUR
                ORDER BY created_at DESC 
                LIMIT 1
            """, (phone,))
            row = cur.fetchone()
            
        return jsonify({
            "ok": True, 
            "last_claimed_by": row["accepted_by_name"] if row else None
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()
