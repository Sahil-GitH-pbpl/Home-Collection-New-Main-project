from flask import Blueprint, render_template
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

lead_history_page_bp = Blueprint("lead_history_page", __name__)

@lead_history_page_bp.route("/lead/<lead_id>/history")
def lead_history_page(lead_id):
    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            cur.execute("""
                SELECT lead_id, name, phone, created_by, status, created_at
                FROM leads WHERE lead_id=%s LIMIT 1
            """, (lead_id,))
            master = cur.fetchone()

            cur.execute("""
                SELECT id, lead_id, phone, name, created_by, status, reason,
                       callback, cce_remarks, next_action, created_at
                FROM lead_history
                WHERE lead_id=%s
                ORDER BY created_at ASC
            """, (lead_id,))
            history = cur.fetchall() or []
    finally:
        conn.close()

    return render_template("lead_history.html", master=master, history=history)
