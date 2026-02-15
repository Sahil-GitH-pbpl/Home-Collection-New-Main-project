# leads_list.py (updated: lock by user_name only; history now stores lead PRIMARY KEY id)

from flask import Blueprint, render_template, request, jsonify, session
from pymysql.cursors import DictCursor
from datetime import date
from app.db.connection import get_db_connection

leads_list_bp = Blueprint("leads_list", __name__)

# =========================
# Helpers (user + dates)
# =========================
def _today_bounds_str():
    sel_date = date.today().strftime("%Y-%m-%d")
    start_ts = f"{sel_date} 00:00:00"
    return start_ts


def _current_user_name():
    """
    Resolve current user's display name only (since user_id is dropped from DB).
    Checks:
      - session['user'] = {'name': ...}
      - session['username']
      - header: X-User-Name
      - query arg: user_name
    Falls back to "Unknown".
    """
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


def _require_user_name():
    """
    Ensure we have a real user_name (not Unknown). Return uname or None.
    """
    uname = _current_user_name()
    if (not uname) or (uname == "Unknown"):
        return None
    return uname


def _get_lead_pk(conn, lead_id):
    """
    Given a business Lead ID (e.g., 'LD-100'), fetch the PRIMARY KEY (int) from leads.id.
    Returns the int id or None if not found.
    """
    with conn.cursor(DictCursor) as cur:
        cur.execute("SELECT id FROM leads WHERE lead_id = %s", (lead_id,))
        row = cur.fetchone()
        return row["id"] if row else None


# =========================
# Existing pages & counters
# =========================
@leads_list_bp.route("/leads")
def leads():
    view = request.args.get("view", "new")
    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cursor:
            if view == "followup":
                cursor.execute("""
                    SELECT lead_id, name, phone, visit_window, num_patients, created_by, tags,
                           CASE
                             WHEN status IN ('No Response','Call Back Later') AND callback IS NOT NULL
                               THEN CONCAT(
                                 CASE WHEN status='No Response' THEN 'Follow up at '
                                      ELSE 'Call back at ' END,
                                 DATE_FORMAT(callback, '%d-%b-%Y %H:%i')
                               )
                             ELSE COALESCE(next_action, '-')
                           END AS next_action,
                           (CASE 
                              WHEN status IN ('No Response','Call Back Later') 
                                   AND callback IS NOT NULL 
                                   AND callback <= NOW()
                              THEN 1 ELSE 0 
                            END) AS due_flag,
                           created_at
                    FROM leads
                    WHERE status IN ('No Response','Call Back Later')
                      AND (callback IS NULL OR callback > NOW())
                    ORDER BY COALESCE(callback, created_at) ASC
                """)
            else:
                cursor.execute("""
                    SELECT lead_id, name, phone, visit_window, num_patients, created_by, tags,
                           CASE
                             WHEN status IN ('No Response','Call Back Later') AND callback IS NOT NULL
                               THEN CONCAT(
                                 CASE WHEN status='No Response' THEN 'Follow up at '
                                      ELSE 'Call back at ' END,
                                 DATE_FORMAT(callback, '%d-%b-%Y %H:%i')
                               )
                             ELSE COALESCE(next_action, '-')
                           END AS next_action,
                           (CASE 
                              WHEN status IN ('No Response','Call Back Later') 
                                   AND callback IS NOT NULL 
                                   AND callback <= NOW()
                              THEN 1 ELSE 0 
                            END) AS due_flag,
                           created_at
                    FROM leads
                    WHERE status = 'Open'
                       OR (status IN ('No Response','Call Back Later')
                           AND callback IS NOT NULL
                           AND callback <= NOW())
                    ORDER BY 
                        CASE 
                          WHEN status IN ('No Response','Call Back Later') AND callback <= NOW() THEN 0
                          ELSE 1
                        END,
                        created_at ASC
                """)
            rows = cursor.fetchall()
    finally:
        conn.close()

    # Pass user_name into template (useful if frontend forwards it as headers later)
    uname = _current_user_name()
    return render_template("lead_list.html", leads=rows, view=view, user_name=uname)


@leads_list_bp.route("/leads/counters")
def counters():
    start_ts = _today_bounds_str()
    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT
                  COUNT(*) AS received,
                  SUM(CASE WHEN status IN ('No Response','Call Back Later') 
                            AND callback IS NOT NULL 
                            AND callback > NOW()
                           THEN 1 ELSE 0 END) AS followup,
                  SUM(CASE WHEN status = 'Booked' THEN 1 ELSE 0 END) AS booked,
                  SUM(CASE WHEN status = 'Canceled' THEN 1 ELSE 0 END) AS canceled
                FROM leads
                WHERE created_at >= %s
                  AND created_at < DATE_ADD(%s, INTERVAL 1 DAY)
            """, (start_ts, start_ts))
            row = cur.fetchone() or {"received":0, "followup":0, "booked":0, "canceled":0}
    finally:
        conn.close()

    def i(x): 
        try: return int(x or 0)
        except: return 0

    return jsonify({
        "received": i(row.get("received")),
        "followup": i(row.get("followup")),
        "booked":   i(row.get("booked")),
        "canceled": i(row.get("canceled")),
    })


# ==========================================
# Lock/Pick API (user_name-based locking)
# Columns used in leads:
#   current_lock_user_name (TEXT/VARCHAR, nullable)
#   lock_expires_at        (DATETIME,   nullable)
#
# History table (lead_lock_history) columns used (UPDATED):
#   lead_pk_id (INT, FK to leads.id), user_name, action, action_time, lock_expires_at
#   (user_id removed; business lead_id not required but can be added separately if you want)
# ==========================================

LOCK_MINUTES = 10  # lock window


def _state_payload(row, current_user_name):
    """
    Build a state payload for frontend based on DB row of 'leads'.
    """
    locked = False
    is_me = False
    user_name = None
    expires_at = None

    if row and row.get("lock_expires_at"):
        locked = True
        user_name = row.get("current_lock_user_name")
        expires_at = (
            row.get("lock_expires_at").strftime("%Y-%m-%d %H:%M:%S")
            if hasattr(row.get("lock_expires_at"), "strftime") else str(row.get("lock_expires_at"))
        )
        # Compare by name (case-insensitive) since user_id no longer stored
        if user_name and current_user_name:
            is_me = (str(user_name).strip().lower() == str(current_user_name).strip().lower())

    return {
        "locked": bool(locked),
        "user_name": user_name,
        "expires_at": expires_at,
        "is_me": bool(is_me)
    }


def _insert_history(cur, lead_pk_id, user_name, action, lock_expires_at):
    """
    Insert lock action into lead_lock_history using PRIMARY KEY id (lead_pk_id).
    """
    cur.execute(
        """
        INSERT INTO lead_lock_history
            (lead_pk_id, user_name, action, action_time, lock_expires_at)
        VALUES
            (%s, %s, %s, NOW(), %s)
        """,
        (lead_pk_id, str(user_name), str(action), lock_expires_at)
    )


def _auto_unlock_if_expired(conn, lead_id):
    """
    If the lock has expired, clear it and log 'auto_unlock'.
    Uses lead's PRIMARY KEY id in history.
    Returns True if unlocked.
    """
    with conn.cursor(DictCursor) as cur:
        cur.execute(
            """
            SELECT id AS lead_pk_id, current_lock_user_name, lock_expires_at
            FROM leads
            WHERE lead_id = %s
            """,
            (lead_id,)
        )
        row = cur.fetchone()
        if not row or not row.get("lock_expires_at"):
            return False

        cur.execute(
            """
            SELECT (lock_expires_at < NOW()) AS expired
            FROM leads
            WHERE lead_id = %s
            """,
            (lead_id,)
        )
        exp = cur.fetchone()
        if not exp or not exp.get("expired"):
            return False

        # Clear lock
        cur.execute(
            """
            UPDATE leads
            SET current_lock_user_name = NULL,
                lock_expires_at = NULL
            WHERE lead_id = %s AND lock_expires_at < NOW()
            """,
            (lead_id,)
        )
        if cur.rowcount > 0:
            _insert_history(cur, row.get("lead_pk_id"), row.get("current_lock_user_name"), "auto_unlock", None)
            conn.commit()
            return True
        return False


@leads_list_bp.route("/leads/lock/state")
def lock_state():
    """
    GET /leads/lock/state?lead_id=LD-100
    GET /leads/lock/state?ids=LD-100,LD-101
    Returns single payload or {states:{...}}.
    Auto-unlocks expired locks for requested ids.
    """
    current_user_name = _current_user_name()
    lead_id = request.args.get("lead_id")
    ids_csv = request.args.get("ids")

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            if lead_id:
                _auto_unlock_if_expired(conn, lead_id)
                cur.execute(
                    """
                    SELECT lead_id, current_lock_user_name, lock_expires_at
                    FROM leads
                    WHERE lead_id = %s
                    """,
                    (lead_id,)
                )
                row = cur.fetchone()
                return jsonify(_state_payload(row, current_user_name) if row else {
                    "locked": False, "user_name": None, "expires_at": None, "is_me": False
                })

            if ids_csv:
                ids = [x.strip() for x in ids_csv.split(",") if x.strip()]
                for lid in ids:
                    _auto_unlock_if_expired(conn, lid)

                if not ids:
                    return jsonify({"states": {}})

                placeholders = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"""
                    SELECT lead_id, current_lock_user_name, lock_expires_at
                    FROM leads
                    WHERE lead_id IN ({placeholders})
                    """,
                    tuple(ids)
                )
                rows = cur.fetchall() or []
                by_id = {r["lead_id"]: r for r in rows}
                states = {}
                for lid in ids:
                    r = by_id.get(lid)
                    states[lid] = _state_payload(r, current_user_name) if r else {
                        "locked": False, "user_name": None, "expires_at": None, "is_me": False
                    }
                return jsonify({"states": states})

            return jsonify({"error": "lead_id or ids required"}), 400
    finally:
        conn.close()


@leads_list_bp.route("/leads/lock/pick", methods=["POST"])
def lock_pick():
    """
    Body: { lead_id }
    - If unlocked/expired or same owner (by user_name) -> acquire/refresh for 10 min & log 'pick'
    - Else return current state
    History now records PRIMARY KEY id (lead_pk_id) in lead_lock_history.
    """
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id")
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    user_name = _require_user_name()
    if user_name is None:
        return jsonify({"error": "user_missing",
                        "hint": "Set session['username'] or send X-User-Name header"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            # Resolve PK once
            lead_pk_id = _get_lead_pk(conn, lead_id)
            if lead_pk_id is None:
                return jsonify({"error": "lead_not_found"}), 404

            # Acquire if free/expired OR already held by same user_name
            cur.execute(
                """
                UPDATE leads
                SET current_lock_user_name = %s,
                    lock_expires_at = DATE_ADD(NOW(), INTERVAL %s MINUTE)
                WHERE lead_id = %s
                  AND (
                        lock_expires_at IS NULL
                        OR lock_expires_at < NOW()
                        OR LOWER(current_lock_user_name) = LOWER(%s)
                      )
                """,
                (user_name, LOCK_MINUTES, lead_id, user_name)
            )
            if cur.rowcount > 0:
                cur.execute("SELECT lock_expires_at FROM leads WHERE lead_id = %s", (lead_id,))
                nx = cur.fetchone()
                _insert_history(cur, lead_pk_id, user_name, "pick", nx["lock_expires_at"] if nx else None)
                conn.commit()

            cur.execute(
                "SELECT lead_id, current_lock_user_name, lock_expires_at FROM leads WHERE lead_id = %s",
                (lead_id,)
            )
            row = cur.fetchone()

            # If expired between update & select → auto unlock and refetch
            if row and row.get("lock_expires_at"):
                cur.execute("SELECT (lock_expires_at < NOW()) AS expired FROM leads WHERE lead_id = %s", (lead_id,))
                exp = cur.fetchone()
                if exp and exp.get("expired"):
                    _auto_unlock_if_expired(conn, lead_id)
                    cur.execute(
                        "SELECT lead_id, current_lock_user_name, lock_expires_at FROM leads WHERE lead_id = %s",
                        (lead_id,)
                    )
                    row = cur.fetchone()

            return jsonify(_state_payload(row, user_name) if row else {
                "locked": False, "user_name": None, "expires_at": None, "is_me": False
            })
    finally:
        conn.close()


@leads_list_bp.route("/leads/lock/giveup", methods=["POST"])
def lock_giveup():
    """
    Body: { lead_id }
    - Only current owner (by user_name) can give up before expiry
    - If expired → auto unlock
    - History action: 'giveup' (using PRIMARY KEY id)
    """
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id")
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    user_name = _require_user_name()
    if user_name is None:
        return jsonify({"error": "user_missing",
                        "hint": "Set session['username'] or send X-User-Name header"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            # Auto unlock if expired
            if _auto_unlock_if_expired(conn, lead_id):
                return jsonify({"locked": False, "user_name": None, "expires_at": None, "is_me": False})

            # Verify ownership by name and get PK
            cur.execute(
                "SELECT id AS lead_pk_id, current_lock_user_name, lock_expires_at FROM leads WHERE lead_id = %s",
                (lead_id,)
            )
            row = cur.fetchone()
            
            if not row or not row.get("lock_expires_at"):
                return jsonify({"locked": False, "user_name": None, "expires_at": None, "is_me": False})

            db_name = (row.get("current_lock_user_name") or "").strip().lower()
            if db_name != user_name.strip().lower():
                return jsonify({
                    "error": "locked_by_other",
                    "locked": True,
                    "user_name": row.get("current_lock_user_name"),
                    "expires_at": row["lock_expires_at"].strftime("%Y-%m-%d %H:%M:%S") if hasattr(row["lock_expires_at"], "strftime") else str(row["lock_expires_at"]),
                    "is_me": False
                }), 403

            # Clear lock
            cur.execute(
                """
                UPDATE leads
                SET current_lock_user_name = NULL,
                    lock_expires_at = NULL
                WHERE lead_id = %s
                  AND LOWER(COALESCE(current_lock_user_name, '')) = LOWER(%s)
                """,
                (lead_id, user_name)
            )
            if cur.rowcount > 0:
                _insert_history(cur, row.get("lead_pk_id"), user_name, "giveup", None)
                conn.commit()

            return jsonify({"locked": False, "user_name": None, "expires_at": None, "is_me": False})
    finally:
        conn.close()
