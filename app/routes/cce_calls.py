from datetime import datetime, date
from urllib.parse import quote
from flask import Blueprint, render_template, request
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

cce_calls_bp = Blueprint("cce_calls", __name__, template_folder="../templates")

RECORDING_PATH_PREFIX = "/var/spool/asterisk/monitor/"
RECORDING_URL_PREFIX = "http://10.1.1.167/recordings/"


def recording_url(recording_file):
    path = str(recording_file or "").strip()
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith(RECORDING_PATH_PREFIX):
        return ""
    relative_path = path[len(RECORDING_PATH_PREFIX):].lstrip("/")
    return RECORDING_URL_PREFIX + quote(relative_path, safe="/")

@cce_calls_bp.route("/cce/received", methods=["GET"])
def received_calls():
    today_str = date.today().strftime("%Y-%m-%d")
    from_str = request.args.get("from", today_str)
    to_str = request.args.get("to", today_str)
    try:
        call_category = int(request.args.get("type", "1"))
    except (TypeError, ValueError):
        call_category = 1
    if call_category not in {1, 2, 3, 4, 5}:
        call_category = 1

    category_labels = {
        1: "Incoming Completed",
        2: "Missed Call Revert",
        3: "Direct Outgoing",
        4: "Internal Calls",
        5: "Force Removed",
    }

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
            if call_category in {2, 3, 4}:
                cur.execute("""
                    SELECT
                        from_number,
                        to_number,
                        %s AS call_type,
                        COALESCE(dial_call_duration, 0) AS dial_call_duration,
                        created_at,
                        callback_by_name,
                        missed_reason,
                        recording_file,
                        COALESCE(dial_call_status, call_status, '') AS call_related_to
                    FROM exotel_outgoing_calls
                    WHERE DATE(created_at) BETWEEN %s AND %s
                      AND COALESCE(
                            call_category,
                            CASE
                              WHEN CHAR_LENGTH(COALESCE(from_number, '')) <= 4
                               AND CHAR_LENGTH(COALESCE(to_number, '')) <= 4 THEN 4
                              ELSE 3
                            END
                          ) = %s
                    ORDER BY created_at DESC
                """, (category_labels[call_category], from_date_str, to_date_str, call_category))
                rows = cur.fetchall()
            else:
                sql = """
                    SELECT
                        from_number,
                        to_number,
                        %s AS call_type,
                        COALESCE(dial_call_duration, 0) AS dial_call_duration,
                        COALESCE(received_at, created_at) AS created_at,
                        accepted_by_name,
                        call_related_to,
                        dial_call_status,
                        callback_by_name,
                        recording_file
                    FROM exotel_incoming_calls
                    WHERE DATE(COALESCE(received_at, created_at)) BETWEEN %s AND %s
                      AND COALESCE(
                            call_category,
                            CASE
                              WHEN LOWER(COALESCE(call_type, '')) = 'completed' THEN 1
                              WHEN LOWER(REPLACE(COALESCE(call_type, ''), '_', '-')) = 'force-removed' THEN 5
                              ELSE NULL
                            END
                          ) = %s
                      AND (%s <> 1 OR COALESCE(TRIM(to_number), '') <> '')
                """
                sql += " ORDER BY COALESCE(received_at, created_at) DESC"
                cur.execute(sql, (
                    category_labels[call_category],
                    from_date_str,
                    to_date_str,
                    call_category,
                    call_category,
                ))
                rows = cur.fetchall()
    finally:
        conn.close()

    for row in rows:
        row["recording_url"] = recording_url(row.get("recording_file"))
        if call_category == 5:
            status = str(row.get("dial_call_status") or "").strip()
            if not str(row.get("to_number") or "").strip() and status.lower() == "answered":
                status = "busy"
            row["call_related_to"] = status or "force-removed"

    return render_template(
        "recived_call_table.html",
        rows=rows,
        from_date_str=from_date_str,
        to_date_str=to_date_str,
        current_call_type=call_category,
        category_labels=category_labels,
    )
