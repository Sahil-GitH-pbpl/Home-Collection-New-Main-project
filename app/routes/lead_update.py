# app/routes/lead_update.py
# Full updated code: backend-enforced lock check + safe update + auto-release
# - Rejects updates if someone else currently holds a valid lock (423 Locked)
# - Releases lock automatically after a successful update by the holder
# - Uses DictCursor where needed; consistent user-name resolution
from flask import jsonify  # ADD
from app.alerts import send_whatsapp_to_number  # ADD
from flask import Blueprint, request, redirect, url_for, session, abort
from datetime import datetime
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

lead_update_bp = Blueprint("lead_update", __name__)


def _current_user_name() -> str:
    """Resolve current user's display name (same logic as list page)."""
    uname = None
    u = session.get("user")
    if isinstance(u, dict):
        uname = u.get("name") or uname
    if not uname:
        uname = session.get("username")
    if not uname:
        uname = request.headers.get("X-User-Name") or request.args.get("user_name")
    if not uname or str(uname).strip() == "":
        uname = "Unknown"
    return str(uname).strip()


def _parse_callback_iso(s: str | None) -> str | None:
    """
    Accepts:
      - 'YYYY-MM-DDTHH:MM[:SS[.fff]][Z]'
      - 'YYYY-MM-DD HH:MM:SS'
    Returns 'YYYY-MM-DD HH:MM:SS' or None.
    """
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        # Tolerate trailing Z
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        if "T" in s and len(s) >= 16:
            return (s.replace("T", " ") + ":00")[:19]
        return None

# helper function-------
def _normalize_msisdn(raw: str | None) -> str | None:
    """
    Normalize to digits-only MSISDN for WhatsApp.
    - Strip non-digits
    - Drop leading zeros
    - If 10-digit (India), prefix '91'
    Returns normalized string or None.
    """
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    # remove leading zeros
    while digits.startswith("0"):
        digits = digits[1:]
    # 10-digit Indian mobile -> add 91
    if len(digits) == 10:
        return "91" + digits
    # already has country code or some other valid form
    return digits


@lead_update_bp.route("/lead/<lead_id>/update", methods=["POST"])
def update_lead(lead_id: str):
    # Keep your existing auth gate
    if "user_id" not in session:
        return redirect(url_for("auth.home"))

    # -------- Read + validate form --------
    status       = (request.form.get("status")  or "").strip()
    reason       = (request.form.get("reason")  or "").strip() or None
    callback_raw = (request.form.get("callback") or "").strip()
    cce_remarks  = (request.form.get("remarks") or "").strip() or None

    allowed = {"Booked", "Canceled", "No Response", "Call Back Later"}
    if status not in allowed:
        return abort(400, description="Invalid status")

    callback_dt = _parse_callback_iso(callback_raw)

    # Compute next_action and normalize fields per status
    if status == "Call Back Later":
        next_action = f"Call back at {callback_dt}" if callback_dt else "Call back - schedule pending"
    elif status == "No Response":
        next_action = f"Follow up at {callback_dt}" if callback_dt else "Follow up - retry"
    elif status == "Booked":
        callback_dt = None
        reason = None
        next_action = "Lead closed: Booked"
    elif status == "Canceled":
        callback_dt = None
        next_action = f"Lead closed: Canceled ({reason})" if reason else "Lead closed: Canceled"
    else:
        next_action = "-"

    updated_by = _current_user_name()

    # -------- DB work (transactional) --------
    conn = get_db_connection()
    with conn:
        # 0) Guard: ensure lead exists + enforce lock holder via SELECT ... FOR UPDATE
        with conn.cursor(DictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id AS lead_pk_id,
                    current_lock_user_name,
                    lock_expires_at,
                    (lock_expires_at IS NOT NULL AND lock_expires_at > NOW()) AS is_locked_now
                FROM leads
                WHERE lead_id = %s
                FOR UPDATE
                """,
                (lead_id,),
            )
            row = cur.fetchone()
            if not row:
                return abort(404, description="Lead not found")

            holder = (row.get("current_lock_user_name") or "").strip().lower()
            is_locked_now = bool(row.get("is_locked_now"))

            # If locked by someone else (and not expired) -> reject update
            if is_locked_now and holder and holder != updated_by.strip().lower():
                # 423 Locked communicates the intent clearly
                return abort(423, description=f"Lead is picked by {row.get('current_lock_user_name')}")

        # 1) Safe to update main lead record
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE leads
                   SET status=%s,
                       reason=%s,
                       callback=%s,
                       cce_remarks=%s,
                       next_action=%s
                 WHERE lead_id=%s
                """,
                (status, reason, callback_dt, cce_remarks, next_action, lead_id),
            )

        # 2) Insert a snapshot into lead_history (after update so it captures latest)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO lead_history
                    (lead_id, phone, name, visit_window, remarks, tags, num_patients,
                     created_at, created_by, updated_by, status, reason, callback, cce_remarks, next_action)
                SELECT lead_id, phone, name, visit_window, remarks, tags, num_patients,
                       NOW(), created_by, %s, status, reason, callback, cce_remarks, next_action
                  FROM leads
                 WHERE lead_id=%s
                """,
                (updated_by, lead_id),
            )

        # 3) Auto-release lock if updater was the holder
        with conn.cursor(DictCursor) as cur:
            cur.execute(
                "SELECT id AS lead_pk_id, current_lock_user_name FROM leads WHERE lead_id=%s",
                (lead_id,),
            )
            row2 = cur.fetchone()
            if row2:
                lead_pk_id = row2.get("lead_pk_id")
                current_holder = (row2.get("current_lock_user_name") or "").strip().lower()
                if updated_by and current_holder == updated_by.strip().lower():
                    # Clear the lock
                    cur.execute(
                        """
                        UPDATE leads
                           SET current_lock_user_name = NULL,
                               lock_expires_at = NULL
                         WHERE lead_id = %s
                           AND LOWER(COALESCE(current_lock_user_name,'')) = LOWER(%s)
                        """,
                        (lead_id, updated_by),
                    )
                    # Log the release
                    cur.execute(
                        """
                        INSERT INTO lead_lock_history
                            (lead_pk_id, user_name, action, action_time, lock_expires_at)
                        VALUES (%s, %s, %s, NOW(), %s)
                        """,
                        (lead_pk_id, updated_by, "release_on_update", None),
                    )

        conn.commit()

    # Redirect to detail page (existing UX)
    return redirect(url_for("lead_detail.lead_detail", lead_id=lead_id))

# for send a whatsapp message after lead update

@lead_update_bp.route("/lead/<lead_id>/send-wa", methods=["POST"])
def send_lead_whatsapp(lead_id: str):
    """
    Send WhatsApp message(s) for a lead.
    Expects JSON:
      {
        "phones": ["8057054076", "9998887776"],  # optional; if missing, we fallback to DB
        "message": "text to send"
      }
    Behavior:
      - Auth required (same as update)
      - Validates lead exists
      - Uses payload phones OR falls back to (phone, alt_phone) from DB
      - Normalizes -> de-dupes -> sends via alerts.send_whatsapp_to_number
      - Returns per-number status JSON
    """
    # Same auth guard as update
    if "user_id" not in session:
        return redirect(url_for("auth.home"))

    # Parse body
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    phones_payload = payload.get("phones") or []

    if not message:
        return jsonify({"ok": False, "error": "Message is required"}), 400

    # Ensure lead exists + fallback numbers from DB
    primary = None
    alt = None
    conn = get_db_connection()
    with conn:
        with conn.cursor(DictCursor) as cur:
            cur.execute("SELECT phone, alt_phone FROM leads WHERE lead_id=%s", (lead_id,))
            row = cur.fetchone()
            if not row:
                abort(404, description="Lead not found")
            primary = (row.get("phone") or "").strip()
            alt = (row.get("alt_phone") or "").strip()

    # Build candidate number list
    candidates = []
    if isinstance(phones_payload, list) and phones_payload:
        candidates.extend([str(x or "") for x in phones_payload])
    else:
        candidates.extend([primary, alt])

    # Normalize + de-dupe
    cleaned = []
    seen = set()
    for raw in candidates:
        n = _normalize_msisdn(raw)
        if n and n not in seen:
            seen.add(n)
            cleaned.append(n)

    if not cleaned:
        return jsonify({"ok": False, "error": "No valid phone numbers"}), 400

    # Send to each number via alerts helper
    results = []
    for n in cleaned:
        try:
            status_code, resp_text = send_whatsapp_to_number(n, message)
            results.append({
                "phone": n,
                "status_code": status_code,
                "ok": bool(status_code in (200, 201)),
                "response": (resp_text[:500] if isinstance(resp_text, str) else str(resp_text))
            })
        except Exception as e:
            results.append({
                "phone": n,
                "status_code": 500,
                "ok": False,
                "response": f"Exception: {e}"
            })

    overall_ok = any(r.get("ok") for r in results)
    return jsonify({
        "ok": overall_ok,
        "lead_id": lead_id,
        "sent_to": cleaned,
        "results": results
    }), (200 if overall_ok else 502)

