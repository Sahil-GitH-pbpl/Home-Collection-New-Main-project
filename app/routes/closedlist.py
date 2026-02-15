import pymysql
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, request, session, redirect, url_for, current_app
from app.db.connection import get_db_connection

closedlist_bp = Blueprint("closedlist", __name__)

IST = ZoneInfo("Asia/Kolkata")

# ---------- Helpers ----------

def _fmt_dt_ist(dt):
    """
    Any datetime -> 'YYYY-MM-DD HH:MM' in IST for display.
    If None/invalid -> '-'
    """
    if not dt:
        return "-"
    try:
        dt_aware = dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
        return dt_aware.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


def _closure_badge(commitment_at, closed_at):
    """
    Compare closed_at vs commitment_at and return (text, state).
    state -> 'ok' (early/on-time), 'near' (<=10 min late), 'overdue' (>10 min late)
    """
    if not commitment_at or not closed_at:
        return (None, None)

    ca = commitment_at.replace(tzinfo=IST) if commitment_at.tzinfo is None else commitment_at.astimezone(IST)
    cl = closed_at.replace(tzinfo=IST) if closed_at.tzinfo is None else closed_at.astimezone(IST)

    delta = cl - ca
    mins = int((abs(delta).total_seconds() + 59) // 60)
    h = mins // 60
    m = mins % 60
    span = f"{h}h {m:02d}m" if h else f"{m}m"

    if cl <= ca:
        return ("Closed on time" if cl == ca else f"Closed early by {span}", "ok")
    else:
        return (f"Closed late by {span}", "near" if mins <= 10 else "overdue")


# ---------- Routes ----------

@closedlist_bp.route("/tickets/closed")
def closed_list():
    if not session.get("user_id") or not session.get("username"):
        return redirect(url_for("auth.home"))

    # ✅ Get user designation for role-based filtering
    designation = session.get("designation", "").strip()
    special_roles = ["Customer Care", "Marketing", "Admin"]
    is_special_user = designation in special_roles

    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 25)), 5), 100)

    today_ist = datetime.now(IST).date()
    default_from = (today_ist - timedelta(days=6)).strftime("%Y-%m-%d")
    default_to = today_ist.strftime("%Y-%m-%d")

    date_from = (request.args.get("date_from") or default_from).strip()
    date_to = (request.args.get("date_to") or default_to).strip()

    q = (request.args.get("q") or "").strip()

    rows = []
    total = 0
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            where = ["(t.status='Closed' OR t.status='closed')"]
            params = []

            closed_col = "t.closed_at"

            where.append(f"DATE({closed_col}) BETWEEN %s AND %s")
            params.extend([date_from, date_to])

            # ✅ ROLE-BASED FILTERING: Special users see all, others see only ODT
            if not is_special_user:
                where.append("t.ticket_origin = 'ODT'")
                # Note: No parameter needed for this filter

            if q:
                where.append("(t.mobile_number LIKE %s OR t.patient_labmate_id LIKE %s)")
                like = f"%{q}%"
                params.extend([like, like])

            sql = f"""
                SELECT
                  t.id,
                  t.mobile_number,
                  t.patient_name,
                  t.patient_labmate_id,
                  t.ticket_category,
                  t.ticket_origin,  -- 🆕 ADDED: to show origin in template if needed
                  t.created_by,
                  t.closed_remark
                FROM tickets t
                WHERE {" AND ".join(where)}
                ORDER BY {closed_col} DESC, t.id DESC
                LIMIT %s OFFSET %s
            """
            params_page = params + [page_size, (page - 1) * page_size]
            cur.execute(sql, params_page)
            rows = cur.fetchall()

            # Count query with same filters
            cur.execute(f"SELECT COUNT(*) AS c FROM tickets t WHERE {' AND '.join(where)}", params)
            total = cur.fetchone()["c"]

    except Exception as e:
        current_app.logger.error(f"[closed_list] DB Error: {e}")
        rows = []
        total = 0
    finally:
        if conn:
            conn.close()

    return render_template(
        "closedlist.html",
        tickets=rows,
        page=page,
        page_size=page_size,
        total=total,
        has_prev=(page > 1),
        has_next=(page * page_size < total),
        date_from=date_from,
        date_to=date_to,
        q=q,
        fmt_dt_ist=_fmt_dt_ist,
        closure_badge=_closure_badge,
        is_special_user=is_special_user,  # 🆕 Pass to template
    )