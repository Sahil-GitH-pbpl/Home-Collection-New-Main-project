from flask import Blueprint, render_template, request, redirect, url_for, session
from datetime import datetime, date
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

my_leads_bp = Blueprint("my_leads", __name__)

@my_leads_bp.route("/my-leads")
def my_leads():
    if "user_id" not in session:
        return redirect(url_for("auth.home"))

    created_by = (session.get("username") or "").strip() or "__none__"
    sel_date = (request.args.get("d") or "").strip()
    try:
        sel_date = sel_date if sel_date else date.today().strftime("%Y-%m-%d")
        _ = datetime.strptime(sel_date, "%Y-%m-%d")  # validate
    except Exception:
        sel_date = date.today().strftime("%Y-%m-%d")

    start_ts = f"{sel_date} 00:00:00"

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT lead_id, name, phone, tags, num_patients, status,
                       DATE_FORMAT(created_at, '%%d-%%b-%%Y %%I:%%i %%p') AS created_at_fmt
                FROM leads
                WHERE created_by=%s
                  AND created_at >= %s
                  AND created_at < DATE_ADD(%s, INTERVAL 1 DAY)
                ORDER BY created_at DESC
            """, (created_by, start_ts, start_ts))
            rows = cur.fetchall() or []
    finally:
        conn.close()

    return render_template("my_leads.html", rows=rows, selected_date=sel_date)
