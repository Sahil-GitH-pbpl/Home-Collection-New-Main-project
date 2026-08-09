from flask import Blueprint, render_template, jsonify, request, current_app, session
import os
import requests
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

cce_bp = Blueprint("cce", __name__, template_folder="../templates")

# ------------------ Configuration ------------------
DEFAULT_ISSABEL_PORT = int(os.getenv("ISSABEL_PORT", "2015"))
ISSABEL_TIMEOUT = float(os.getenv("ISSABEL_HTTP_TIMEOUT", "4.0"))

ISSABEL_PRELOAD_PATHS = os.getenv(
    "ISSABEL_PRELOAD_PATHS",
    "/calls/recent,/raw"
).split(",")

ISSABEL_ACCEPT_PATHS = os.getenv(
    "ISSABEL_ACCEPT_PATHS",
    "/calls/accept,/accept"
).split(",")

# Persist unclaimed completed calls for refresh survivability
PERSIST_WINDOW_MINUTES = int(os.getenv("CCE_PERSIST_WINDOW_MINUTES", "720"))

# Terminal set (must match frontend)
TERMINAL_TYPES = {
    "completed","canceled","failed","busy","no-answer","not-answered",
    "hangup","client-hangup","machine-hangup"
}

# ------------------ Helpers ------------------
def _resolve_issabel_host() -> str:
    host = (
        current_app.config.get("ISSABEL_HOST")
        or os.getenv("ISSABEL_HOST")
        or request.host.split("/")[0].split(":")[0]
    )
    return host

def _resolve_issabel_port() -> int:
    return int(current_app.config.get("ISSABEL_PORT") or DEFAULT_ISSABEL_PORT)

# ------------------ Routes ------------------

@cce_bp.route("/cce")
def live_page():
    issabel_host = _resolve_issabel_host()
    issabel_port = _resolve_issabel_port()

    ws_scheme = "wss" if request.is_secure else "ws"
    ws_url = current_app.config.get("ISSABEL_WS_URL") or f"{ws_scheme}://{issabel_host}:{issabel_port}"
    http_url = f"http://{issabel_host}:{issabel_port}"

    me_id = session.get("user_id") or ""
    me_name = session.get("username") or ""

    return render_template(
        "cce.html",
        WS_URL=ws_url,
        ISSABEL_HTTP=http_url,
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
                    ticket_category AS ticket_type,
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
    issabel_host = _resolve_issabel_host()
    issabel_port = _resolve_issabel_port()
    base = f"http://{issabel_host}:{issabel_port}"

    last_err = None
    for path in ISSABEL_PRELOAD_PATHS:
        url = f"{base}{path.strip()}"
        try:
            limit = request.args.get("limit", "50")
            params = {"limit": limit} if any(k in path for k in ("raw", "recent", "live")) else {}
            resp = requests.get(url, params=params, timeout=ISSABEL_TIMEOUT)
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
    issabel_host = _resolve_issabel_host()
    issabel_port = _resolve_issabel_port()
    base = f"http://{issabel_host}:{issabel_port}"

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
    for path in ISSABEL_ACCEPT_PATHS:
        url = f"{base}{path.strip()}"
        try:
            r = requests.post(url, json=forward, headers=headers, timeout=ISSABEL_TIMEOUT)
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


# ------------------ Outgoing callback disabled after Issabel migration ------------------
@cce_bp.route("/cce/callback", methods=["POST"])
def cce_callback():
    return jsonify({"status": "error", "message": "Outbound callback is disabled. Calls are handled by Issabel."}), 410

# ------------------ NEW: Complete endpoint (with call_related_to) ------------------
@cce_bp.route("/cce/answered-popup", methods=["POST"])
def cce_answered_popup():
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    call_sid = (payload.get("call_sid") or "").strip()
    phone = "".join([c for c in (payload.get("phone") or "") if c.isdigit()])[-10:]
    extension = (payload.get("extension") or "").strip()
    if not call_sid:
        return jsonify({"status": "error", "message": "call_sid required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                INSERT INTO exotel_incoming_calls
                    (call_sid, from_number, to_number, call_type, created_at,
                     dial_call_duration, dial_call_status, direction,
                     accepted_by_name, accepted_by_id, accepted_at, recording_file)
                VALUES
                    (%s, %s, %s, 'call-attempt', NOW(),
                     0, 'answered', 'incoming',
                     %s, %s, NOW(), '')
                ON DUPLICATE KEY UPDATE
                    from_number = COALESCE(NULLIF(VALUES(from_number), ''), from_number),
                    to_number = COALESCE(NULLIF(VALUES(to_number), ''), to_number),
                    dial_call_status = VALUES(dial_call_status),
                    accepted_by_name = VALUES(accepted_by_name),
                    accepted_by_id = VALUES(accepted_by_id),
                    accepted_at = COALESCE(accepted_at, VALUES(accepted_at))
            """, (call_sid, phone, extension, user_name, user_id))
        conn.commit()
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


@cce_bp.route("/cce/pending-popups")
def cce_pending_popups():
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT id, call_sid, from_number, to_number, accepted_at, created_at,
                       dial_call_duration, dial_call_status, call_type
                FROM exotel_incoming_calls
                WHERE (call_related_to IS NULL OR call_related_to = '')
                  AND (
                    accepted_by_id = %s
                    OR UPPER(TRIM(accepted_by_name)) = UPPER(TRIM(%s))
                  )
                  AND COALESCE(accepted_at, created_at) >= NOW() - INTERVAL 12 HOUR
                ORDER BY COALESCE(accepted_at, created_at) DESC
                LIMIT 20
            """, (user_id, user_name))
            rows = cur.fetchall() or []
        data = []
        for row in rows:
            data.append({
                "popup_key": f"sid:{row.get('call_sid') or ''}",
                "call_sid": row.get("call_sid") or "",
                "phone": row.get("from_number") or "",
                "extension": row.get("to_number") or "",
                "answered_at": row.get("accepted_at") or row.get("created_at") or "",
                "dial_call_duration": row.get("dial_call_duration") or 0,
                "dial_call_status": row.get("dial_call_status") or "",
                "call_type": row.get("call_type") or "",
            })
        return jsonify({"status": "ok", "data": data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


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
                SET call_related_to = %s,
                    accepted_by_name = COALESCE(NULLIF(accepted_by_name, ''), %s),
                    accepted_by_id = COALESCE(accepted_by_id, %s),
                    accepted_at = COALESCE(accepted_at, NOW())
                WHERE call_sid = %s
                LIMIT 1
            """, (related, user_name, user_id, call_sid))
            rows = cur.rowcount

        conn.commit()
        if rows == 0:
            return jsonify({"status": "error", "message": "Call not found"}), 404
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


@cce_bp.route("/cce/call-status")
def cce_call_status():
    call_sid = (request.args.get("call_sid") or "").strip()
    if not call_sid:
        return jsonify({"status": "error", "message": "call_sid required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT call_related_to
                FROM exotel_incoming_calls
                WHERE call_sid = %s
                LIMIT 1
            """, (call_sid,))
            row = cur.fetchone()
        if not row:
            return jsonify({"status": "error", "message": "Call not found"}), 404
        related = (row.get("call_related_to") or "").strip()
        return jsonify({
            "status": "ok",
            "call_related_to": related,
            "has_call_related_to": bool(related),
        })
    except Exception as e:
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


# ------------------ Call Back Endpoint disabled after Issabel migration ------------------
@cce_bp.route("/cce/make-call", methods=["POST"])
def make_call():
    return jsonify({"status": "error", "message": "Outbound callback is disabled. Calls are handled by Issabel."}), 410


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

