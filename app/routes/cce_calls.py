from datetime import datetime, date
from flask import Blueprint, render_template, request
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

cce_calls_bp = Blueprint("cce_calls", __name__, template_folder="../templates")

@cce_calls_bp.route("/cce/received", methods=["GET"])
def received_calls():
    today_str = date.today().strftime("%Y-%m-%d")
    from_str = request.args.get("from", today_str)
    to_str = request.args.get("to", today_str)
    call_type = request.args.get("type", "completed")

    def norm(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            return today_str

    from_date_str = norm(from_str)
    to_date_str = norm(to_str)

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cur:
            if call_type == "callback":
                cur.execute("""
                    SELECT
                        from_number,
                        to_number,
                        'callback' AS call_type,
                        COALESCE(dial_call_duration, 0) AS dial_call_duration,
                        created_at,
                        callback_by_name,
                        missed_reason,
                        COALESCE(dial_call_status, call_status, '') AS call_related_to
                    FROM exotel_outgoing_calls
                    WHERE DATE(created_at) BETWEEN %s AND %s
                    ORDER BY created_at DESC
                """, (from_date_str, to_date_str))
                rows = cur.fetchall()
            else:
                sql = """
                    SELECT
                        from_number,
                        to_number,
                        call_type,
                        COALESCE(dial_call_duration, 0) AS dial_call_duration,
                        created_at,
                        accepted_by_name,
                        call_related_to,
                        callback_by_name
                    FROM exotel_incoming_calls
                    WHERE DATE(COALESCE(received_at, created_at)) BETWEEN %s AND %s
                      AND LOWER(COALESCE(call_type, '')) = 'completed'
                """
                sql += " ORDER BY COALESCE(received_at, created_at) DESC"
                cur.execute(sql, (from_date_str, to_date_str))
                rows = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "recived_call_table.html",
        rows=rows,
        from_date_str=from_date_str,
        to_date_str=to_date_str,
        current_call_type=call_type
    )
