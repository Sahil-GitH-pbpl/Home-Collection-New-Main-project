from flask import Blueprint, render_template, request
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

lead_history_list_bp = Blueprint("lead_history_list", __name__)

@lead_history_list_bp.route("/leads/history")
def lead_history_list():
    view = request.args.get("view", "completed")
    from_date_str = (request.args.get("from_date") or "").strip()
    to_date_str   = (request.args.get("to_date") or "").strip()
    status_filter = (request.args.get("status_filter") or "all").strip()

    conn = get_db_connection()
    try:
        with conn.cursor(DictCursor) as cursor:
            rows = []
            if view == "completed":
                where = ["status IN ('Booked','Canceled')"]
                params = []
                if status_filter in ("Booked", "Canceled"):
                    where.append("status = %s"); params.append(status_filter)
                if not from_date_str and not to_date_str:
                    where.append("DATE(created_at) = CURDATE()")
                elif from_date_str and to_date_str:
                    where.append("DATE(created_at) BETWEEN %s AND %s")
                    params.extend([from_date_str, to_date_str])
                elif from_date_str and not to_date_str:
                    where.append("DATE(created_at) = %s")
                    params.append(from_date_str)
                else:
                    where.append("DATE(created_at) <= %s")
                    params.append(to_date_str)

                sql = f"""
                    SELECT lead_id, name, phone, created_by, status, next_action, created_at,
                           DATE_FORMAT(created_at, '%%d-%%b-%%Y %%I:%%i %%p') AS created_at_fmt
                    FROM leads
                    WHERE {" AND ".join(where)}
                    ORDER BY created_at DESC
                """
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall() or []

            elif view == "running":
                cursor.execute("""
                    SELECT lead_id, name, phone, created_by, status, next_action, created_at,
                           DATE_FORMAT(created_at, '%%d-%%b-%%Y %%I:%%i %%p') AS created_at_fmt
                    FROM leads
                    WHERE status IN ('No Response','Call Back Later')
                    ORDER BY created_at DESC
                """)
                rows = cursor.fetchall() or []

            else:
                cursor.execute("""
                    SELECT lead_id, name, phone, created_by, status, next_action, created_at,
                           DATE_FORMAT(created_at, '%%d-%%b-%%Y %%I:%%i %%p') AS created_at_fmt
                    FROM leads
                    ORDER BY created_at DESC
                """)
                rows = cursor.fetchall() or []
    finally:
        conn.close()

    return render_template("lead_history_list.html", leads=rows, view=view)
