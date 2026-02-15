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
                sql = """
                    SELECT
                        exotel_outgoing_calls.from_number,
                        exotel_outgoing_calls.to_number,
                        'callback' AS call_type,
                        COALESCE(exotel_outgoing_calls.dial_call_duration, 0) AS dial_call_duration,
                        exotel_outgoing_calls.created_at,
                        NULL AS accepted_by_name,
                        i.call_related_to AS call_related_to,
                        exotel_outgoing_calls.callback_by_name
                    FROM exotel_outgoing_calls
                    LEFT JOIN exotel_incoming_calls i
                      ON i.call_sid = exotel_outgoing_calls.call_sid
                    WHERE DATE(exotel_outgoing_calls.created_at) BETWEEN %s AND %s
                    ORDER BY exotel_outgoing_calls.created_at DESC
                """
                cur.execute(sql, (from_date_str, to_date_str))
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
                    WHERE DATE(received_at) BETWEEN %s AND %s
                """
                if call_type == "completed":
                    sql += """
                        AND NOT EXISTS (
                            SELECT 1 FROM exotel_outgoing_calls o
                            WHERE o.call_sid = exotel_incoming_calls.call_sid
                        )
                    """
                sql += " ORDER BY received_at DESC"
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
