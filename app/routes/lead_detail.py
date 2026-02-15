from flask import Blueprint, render_template
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

lead_detail_bp = Blueprint("lead_detail", __name__)

@lead_detail_bp.route("/lead/<lead_id>")
def lead_detail(lead_id):
    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("SELECT * FROM leads WHERE lead_id=%s", (lead_id,))
            lead = cur.fetchone()

            cur.execute("""
                SELECT id,
                       DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') AS created_at_fmt,
                       status, reason, cce_remarks, callback, next_action,
                       created_by, updated_by
                FROM lead_history
                WHERE lead_id=%s
                ORDER BY created_at ASC
            """, (lead_id,))
            history = cur.fetchall() or []
    finally:
        conn.close()

    return render_template("lead_detail.html", lead=lead, history=history)
