from flask import Blueprint, render_template, make_response, jsonify, request
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection, get_whatsapp_connection
import threading
import time
from datetime import datetime

dashboard_bp = Blueprint("dashboard", __name__)

dashboard_cache = {}
cache_lock = threading.Lock()
background_started = False

def background_data_fetcher():
    while True:
        try:
            today_data = fetch_all_stats(date_range="today")
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with cache_lock:
                dashboard_cache["today"] = {
                    "data": today_data,
                    "last_updated": current_time,
                }

        except Exception:
            pass

        time.sleep(3600)

def start_background_fetcher():
    global background_started
    if not background_started:
        thread = threading.Thread(target=background_data_fetcher, daemon=True)
        thread.start()
        background_started = True

@dashboard_bp.route("/dashboard")
def dashboard():
    resp = make_response(render_template("dashboard.html"))
    resp.headers["Cache-Control"] = "public, max-age=120"
    return resp

dashboard_bp.add_url_rule("/dashboard", endpoint="dashboard_view", view_func=dashboard)

@dashboard_bp.route('/leads/new')
def lead_shell():
    return render_template('lead_shell.html')

@dashboard_bp.route('/tickets')
def tickets_page():
    return render_template('tickets.html')

@dashboard_bp.route("/cce-dashboard")
def cce_dashboard():
    return render_template("cce_dashboard.html")

@dashboard_bp.route("/api/cce-dashboard/all-stats")
def all_stats():
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if (start_date and not end_date) or (end_date and not start_date):
        return (
            jsonify({"ok": False, "error": "Both start_date and end_date are required."}),
            400,
        )

    if start_date and end_date:
        try:
            start_obj = datetime.strptime(start_date, "%Y-%m-%d")
            end_obj = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return (
                jsonify({"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}),
                400,
            )

        if start_obj > end_obj:
            return (
                jsonify({"ok": False, "error": "start_date cannot be after end_date."}),
                400,
            )

        data = fetch_all_stats(start_date=start_date, end_date=end_date)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return jsonify(
            {
                "ok": True,
                "leaderboard": data,
                "last_updated": current_time,
            }
        )

    with cache_lock:
        cached = dashboard_cache.get("today")

    if cached:
        return jsonify(
            {
                "ok": True,
                "leaderboard": cached["data"],
                "last_updated": cached["last_updated"],
            }
        )

    data = fetch_all_stats(date_range="today")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with cache_lock:
        dashboard_cache["today"] = {"data": data, "last_updated": current_time}

    return jsonify(
        {
            "ok": True,
            "leaderboard": data,
            "last_updated": current_time,
        }
    )

def fetch_all_stats(date_range="today", start_date=None, end_date=None):
    cutoff_datetime = "2025-11-13 15:00:00"

    if start_date and end_date:
        date_condition = (
            f"DATE(created_at) BETWEEN '{start_date}' AND '{end_date}'"
        )
        closed_date_condition = (
            f"DATE(closed_at) BETWEEN '{start_date}' AND '{end_date}'"
        )
        reports_date_condition = (
            f"DATE(resolved_at) BETWEEN '{start_date}' AND '{end_date}'"
        )
        whatsapp_date_condition = f"""
            STR_TO_DATE(wabadatetime, '%d-%m-%Y %h:%i %p') >= '{cutoff_datetime}'
            AND DATE(STR_TO_DATE(wabadatetime, '%d-%m-%Y %h:%i %p')) BETWEEN '{start_date}' AND '{end_date}'
        """
        hcb_date_condition = f"""
            bookingtime >= '{cutoff_datetime}'
            AND DATE(bookingtime) BETWEEN '{start_date}' AND '{end_date}'
        """
    elif date_range == "today":
        date_condition = "DATE(created_at) = CURDATE()"
        closed_date_condition = "DATE(closed_at) = CURDATE()"
        reports_date_condition = "DATE(resolved_at) = CURDATE()"
        whatsapp_date_condition = f"""
            (STR_TO_DATE(wabadatetime, '%d-%m-%Y %h:%i %p') >= '{cutoff_datetime}' 
             AND DATE(STR_TO_DATE(wabadatetime, '%d-%m-%Y %h:%i %p')) = CURDATE())
        """
        hcb_date_condition = f"bookingtime >= '{cutoff_datetime}' AND DATE(bookingtime) = CURDATE()"
    else:
        date_condition = "1=1"
        closed_date_condition = "1=1"
        reports_date_condition = "1=1"
        whatsapp_date_condition = "1=1"
        hcb_date_condition = "1=1"

    conn = None
    try:
        conn = get_db_connection()
        
        main_query = f"""
            SELECT 
                user_name,
                SUM(calls_claimed) as calls_claimed,
                SUM(completed_calls) as completed_calls,
                SUM(total_duration_seconds) as total_duration_seconds,
                SUM(lead_updates) as lead_updates,
                SUM(leads_booked) as leads_booked,
                SUM(leads_canceled) as leads_canceled,
                SUM(ticket_updates) as ticket_updates,
                SUM(tickets_created) as tickets_created,
                SUM(tickets_closed) as tickets_closed,
                SUM(reports_sent) as reports_sent
            FROM (
                SELECT 
                    UPPER(TRIM(accepted_by_name)) as user_name,
                    COUNT(*) as calls_claimed,
                    SUM(CASE WHEN LOWER(call_type) = 'completed' THEN 1 ELSE 0 END) as completed_calls,
                    COALESCE(SUM(dial_call_duration), 0) as total_duration_seconds,
                    0 as lead_updates,
                    0 as leads_booked,
                    0 as leads_canceled,
                    0 as ticket_updates,
                    0 as tickets_created,
                    0 as tickets_closed,
                    0 as reports_sent
                FROM exotel_incoming_calls 
                WHERE accepted_by_name IS NOT NULL 
                    AND accepted_by_name != ''
                    AND {date_condition}
                GROUP BY UPPER(TRIM(accepted_by_name))
                
                UNION ALL
                
                SELECT 
                    UPPER(TRIM(updated_by)) as user_name,
                    0 as calls_claimed,
                    0 as completed_calls,
                    0 as total_duration_seconds,
                    COUNT(*) as lead_updates,
                    SUM(CASE WHEN status = 'Booked' THEN 1 ELSE 0 END) as leads_booked,
                    SUM(CASE WHEN status = 'Canceled' THEN 1 ELSE 0 END) as leads_canceled,
                    0 as ticket_updates,
                    0 as tickets_created,
                    0 as tickets_closed,
                    0 as reports_sent
                FROM lead_history 
                WHERE updated_by IS NOT NULL 
                    AND updated_by != ''
                    AND {date_condition}
                GROUP BY UPPER(TRIM(updated_by))
                
                UNION ALL
                
                SELECT 
                    UPPER(TRIM(updated_by)) as user_name,
                    0 as calls_claimed,
                    0 as completed_calls,
                    0 as total_duration_seconds,
                    0 as lead_updates,
                    0 as leads_booked,
                    0 as leads_canceled,
                    COUNT(*) as ticket_updates,
                    0 as tickets_created,
                    0 as tickets_closed,
                    0 as reports_sent
                FROM ticket_assign_updates 
                WHERE updated_by IS NOT NULL 
                    AND updated_by != ''
                    AND {date_condition}
                GROUP BY UPPER(TRIM(updated_by))
                
                UNION ALL
                
                SELECT 
                    UPPER(TRIM(created_by)) as user_name,
                    0 as calls_claimed,
                    0 as completed_calls,
                    0 as total_duration_seconds,
                    0 as lead_updates,
                    0 as leads_booked,
                    0 as leads_canceled,
                    0 as ticket_updates,
                    COUNT(CASE WHEN ticket_origin = 'CCE' THEN 1 END) as tickets_created,
                    0 as tickets_closed,
                    0 as reports_sent
                FROM tickets 
                WHERE created_by IS NOT NULL 
                    AND created_by != ''
                    AND ticket_origin = 'CCE'
                    AND {date_condition}
                GROUP BY UPPER(TRIM(created_by))
                
                UNION ALL
                
                SELECT 
                    UPPER(TRIM(u.name)) as user_name,
                    0 as calls_claimed,
                    0 as completed_calls,
                    0 as total_duration_seconds,
                    0 as lead_updates,
                    0 as leads_booked,
                    0 as leads_canceled,
                    0 as ticket_updates,
                    0 as tickets_created,
                    COUNT(*) as tickets_closed,
                    0 as reports_sent
                FROM tickets t
                JOIN users u ON t.closed_by_user_id = u.id
                WHERE t.closed_by_user_id IS NOT NULL 
                    AND t.status = 'Closed'
                    AND {closed_date_condition}
                GROUP BY UPPER(TRIM(u.name))
                
                UNION ALL
                
                SELECT 
                    UPPER(TRIM(resolved_by_username)) as user_name,
                    0 as calls_claimed,
                    0 as completed_calls,
                    0 as total_duration_seconds,
                    0 as lead_updates,
                    0 as leads_booked,
                    0 as leads_canceled,
                    0 as ticket_updates,
                    0 as tickets_created,
                    0 as tickets_closed,
                    COUNT(*) as reports_sent
                FROM failurereport_resolutions 
                WHERE resolved_by_username IS NOT NULL 
                    AND resolved_by_username != ''
                    AND resolved_at IS NOT NULL
                    AND {reports_date_condition}
                GROUP BY UPPER(TRIM(resolved_by_username))
            ) as all_stats
            GROUP BY user_name
            HAVING user_name IS NOT NULL AND user_name != ''
        """
        
        with conn.cursor(DictCursor) as cur:
            cur.execute(main_query)
            main_results = cur.fetchall()
        
        whatsapp_conn = None
        try:
            whatsapp_conn = get_whatsapp_connection()
            if whatsapp_conn:
                with whatsapp_conn.cursor(DictCursor) as cur:
                    whatsapp_sql = f"""
                        SELECT 
                            UPPER(TRIM(empname)) as user_name,
                            COUNT(*) as whatsapp_reverts
                        FROM waba 
                        WHERE empname IS NOT NULL 
                            AND empname != '' 
                            AND empname != 'Patient'
                            AND {whatsapp_date_condition}
                        GROUP BY UPPER(TRIM(empname))
                    """
                    cur.execute(whatsapp_sql)
                    whatsapp_results = cur.fetchall()
                    
                    hcb_sql = f"""
                        SELECT 
                            UPPER(TRIM(bookedby)) as user_name,
                            COUNT(*) as hcb_count
                        FROM tblbooking 
                        WHERE bookedby IS NOT NULL 
                            AND bookedby != ''
                            AND {hcb_date_condition}
                        GROUP BY UPPER(TRIM(bookedby))
                    """
                    cur.execute(hcb_sql)
                    hcb_results = cur.fetchall()
        except Exception:
            whatsapp_results = []
            hcb_results = []
        finally:
            if whatsapp_conn:
                whatsapp_conn.close()
        
        user_data = {}
        
        for row in main_results:
            user_name = row.get("user_name", "").strip()
            if not user_name:
                continue
                
            user_data[user_name] = {
                "calls_claimed": int(row.get("calls_claimed", 0)),
                "completed_calls": int(row.get("completed_calls", 0)),
                "total_duration_seconds": float(row.get("total_duration_seconds", 0)),
                "lead_updates": int(row.get("lead_updates", 0)),
                "leads_booked": int(row.get("leads_booked", 0)),
                "leads_canceled": int(row.get("leads_canceled", 0)),
                "ticket_updates": int(row.get("ticket_updates", 0)),
                "tickets_created": int(row.get("tickets_created", 0)),
                "tickets_closed": int(row.get("tickets_closed", 0)),
                "reports_sent": int(row.get("reports_sent", 0)),
                "whatsapp_reverts": 0,
                "hcb_count": 0,
                "total_points": 0
            }
        
        for row in whatsapp_results:
            user_name = row.get("user_name", "").strip()
            if user_name and user_name in user_data:
                user_data[user_name]["whatsapp_reverts"] = int(row.get("whatsapp_reverts", 0))
            elif user_name:
                user_data[user_name] = {
                    "calls_claimed": 0, "completed_calls": 0, "total_duration_seconds": 0,
                    "lead_updates": 0, "leads_booked": 0, "leads_canceled": 0,
                    "ticket_updates": 0, "tickets_created": 0, "tickets_closed": 0,
                    "reports_sent": 0, "whatsapp_reverts": int(row.get("whatsapp_reverts", 0)),
                    "hcb_count": 0, "total_points": 0
                }
        
        for row in hcb_results:
            user_name = row.get("user_name", "").strip()
            if user_name and user_name in user_data:
                user_data[user_name]["hcb_count"] = int(row.get("hcb_count", 0))
            elif user_name:
                user_data[user_name] = {
                    "calls_claimed": 0, "completed_calls": 0, "total_duration_seconds": 0,
                    "lead_updates": 0, "leads_booked": 0, "leads_canceled": 0,
                    "ticket_updates": 0, "tickets_created": 0, "tickets_closed": 0,
                    "reports_sent": 0, "whatsapp_reverts": 0,
                    "hcb_count": int(row.get("hcb_count", 0)), "total_points": 0
                }
        
        for user_name, data in user_data.items():
            calls_claimed = float(data["calls_claimed"])
            total_duration_seconds = float(data["total_duration_seconds"])
            lead_updates = float(data["lead_updates"])
            leads_booked = float(data["leads_booked"])
            leads_canceled = float(data["leads_canceled"])
            ticket_updates = float(data["ticket_updates"])
            tickets_created = float(data["tickets_created"])
            tickets_closed = float(data["tickets_closed"])
            reports_sent = float(data["reports_sent"])
            whatsapp_reverts = float(data["whatsapp_reverts"])
            hcb_count = float(data["hcb_count"])
            
            calls_points = calls_claimed * 2
            duration_minutes = total_duration_seconds / 60
            duration_points = duration_minutes * 0.1
            
            lead_updates_points = lead_updates * 1
            leads_booked_points = leads_booked * 4
            leads_canceled_points = leads_canceled * 2
            
            ticket_updates_points = ticket_updates * 1
            tickets_created_points = tickets_created * 2
            tickets_closed_points = tickets_closed * 3
            
            reports_points = reports_sent * 0.5
            whatsapp_points = whatsapp_reverts * 0.5
            hcb_points = hcb_count * 6
            
            total_points = (calls_points + duration_points + lead_updates_points + 
                          leads_booked_points + leads_canceled_points + ticket_updates_points + 
                          tickets_created_points + tickets_closed_points + reports_points + 
                          whatsapp_points + hcb_points)
            
            user_data[user_name]["total_points"] = round(total_points, 2)
            
            total_minutes = int(duration_minutes)
            user_data[user_name]["total_duration"] = f"{total_minutes}m"
        
        final_data = []
        for user_name, data in user_data.items():
            final_data.append({
                "user_name": user_name.title(),
                "total_points": data["total_points"],
                "calls_claimed": data["calls_claimed"],
                "completed_calls": data["completed_calls"],
                "total_duration": data["total_duration"],
                "lead_updates": data["lead_updates"],
                "leads_booked": data["leads_booked"],
                "leads_canceled": data["leads_canceled"],
                "ticket_updates": data["ticket_updates"],
                "tickets_created": data["tickets_created"],
                "tickets_closed": data["tickets_closed"],
                "whatsapp_reverts": data["whatsapp_reverts"],
                "hcb_count": data["hcb_count"],
                "reports_sent": data["reports_sent"]
            })
        
        final_data.sort(key=lambda x: x["total_points"], reverse=True)
        
        for i, user in enumerate(final_data, 1):
            user["rank"] = i
        
        return final_data[:50]
        
    except Exception as e:
        print(f"Error in fetch_all_stats: {e}")
        return []
    finally:
        if conn:
            conn.close()

start_background_fetcher()
