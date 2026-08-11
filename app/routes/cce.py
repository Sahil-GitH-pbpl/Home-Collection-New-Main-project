from flask import Blueprint, render_template, jsonify, request, current_app, session
import os
import requests
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

cce_bp = Blueprint("cce", __name__, template_folder="../templates")

# ------------------ Configuration ------------------
DEFAULT_ISSABEL_PORT = int(os.getenv("ISSABEL_PORT", "2015"))
ISSABEL_TIMEOUT = float(os.getenv("ISSABEL_HTTP_TIMEOUT", "4.0"))

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
                    COALESCE(NULLIF(LOWER(i.direction), ''), 'incoming') IN ('incoming', 'inbound')
                    AND NOT (CHAR_LENGTH(COALESCE(i.from_number, '')) = 4
                         AND CHAR_LENGTH(COALESCE(i.to_number, '')) = 4)
                    AND CHAR_LENGTH(COALESCE(i.from_number, '')) > 4
                    AND COALESCE(i.from_number, '') NOT IN ('1149989898', '01149989898', '49989898')
                    -- A caller who disconnects in the IVR can have no to_number;
                    -- that is still a missed external call. Final PBX outcome,
                    -- not extension/claim metadata, decides missed status.
                    AND (
                        COALESCE(TRIM(i.to_number), '') = ''
                        OR (
                            LOWER(REPLACE(COALESCE(i.call_type, ''), '_', '-')) IN
                                ('client-hangup', 'busy', 'failed', 'no-answer',
                                 'call-attempt', 'abandon', 'abandoned')
                            AND LOWER(COALESCE(i.dial_call_status, '')) NOT IN
                                ('answered', 'completed', 'completeagent', 'completecaller')
                        )
                    )
                    AND i.callback_at IS NULL
                ORDER BY i.created_at DESC
                LIMIT 200
            """)
            rows = cur.fetchall() or []

        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        conn.close()


# ------------------ Mark missed call callback ------------------
@cce_bp.route("/cce/callback", methods=["POST"])
def cce_callback():
    user_id = session.get("user_id")
    user_name = session.get("username")
    if not user_name:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    payload = request.get_json(silent=True) or {}
    call_id = payload.get("id") or payload.get("call_id")
    try:
        call_id = int(call_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid call id"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE exotel_incoming_calls
                SET call_type = 'callback',
                    callback_by_name = %s,
                    callback_at = NOW()
                WHERE id = %s
                  AND callback_at IS NULL
                """,
                (user_name, call_id),
            )
        conn.commit()
        return jsonify({"status": "success", "updated": cur.rowcount})
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

    # While the call is active, keep the user's selection in the listener's
    # in-memory CallSession. It will be included in the single hangup-time insert.
    listener_pending = False
    try:
        listener_response = requests.post(
            f"http://{_resolve_issabel_host()}:{_resolve_issabel_port()}/calls/classify",
            json={"call_sid": call_sid, "call_related_to": related},
            timeout=ISSABEL_TIMEOUT,
        )
        if listener_response.ok:
            listener_data = listener_response.json() if listener_response.content else {}
            listener_pending = listener_data.get("pending") is True
    except Exception:
        # A finalized call may no longer be held by the listener; update its DB
        # row below. If no final row exists, the normal not-found response applies.
        pass

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
            if listener_pending:
                return jsonify({"status": "ok", "pending": True}), 200
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

