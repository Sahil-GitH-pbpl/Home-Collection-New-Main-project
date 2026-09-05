from flask import Blueprint, render_template, jsonify, request
from app.db.connection import get_whatsapp_groups_connection, get_whatsapp_panel_connection
from mysql.connector import Error
from datetime import datetime
from zoneinfo import ZoneInfo

completedreport_bp = Blueprint('completedreport', __name__)
IST = ZoneInfo("Asia/Kolkata")

@completedreport_bp.route('/')
def index():
    return render_template('completedreport.html')

@completedreport_bp.route('/api/completed-deliveries')
def get_completed_deliveries():
    """
    Fetch completed/successful Labmate report WhatsApp sends from audit logs.
    """
    try:
        # Get date filters from request
        from_date = request.args.get('from', datetime.now(IST).strftime('%Y-%m-%d'))
        to_date = request.args.get('to', datetime.now(IST).strftime('%Y-%m-%d'))
        
        # If only one date provided, use same for both
        if not from_date:
            from_date = datetime.now(IST).strftime('%Y-%m-%d')
        if not to_date:
            to_date = from_date
        
        # Add time to dates for SQL query
        from_datetime = f"{from_date} 00:00:00"
        to_datetime = f"{to_date} 23:59:59"
        
        # Fetch group names for display
        group_names = {}
        try:
            groups_conn = get_whatsapp_groups_connection()
            with groups_conn.cursor() as cursor:
                cursor.execute("SELECT group_id, group_name FROM whatsapp_groups")
                for row in cursor.fetchall():
                    group_id = str(row['group_id'])
                    group_names[group_id] = row['group_name']
            groups_conn.close()
        except Exception:
            pass
        
        connection = get_whatsapp_panel_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, lab_id, recipient, message_text, media_url, api_type, template_name,
                       error_text, payload_json, created_at, sent_at
                FROM whatsapp_send_logs
                WHERE action_type = 'labmate_rpt'
                  AND is_success = 1
                  AND COALESCE(sent_at, created_at) >= %s
                  AND COALESCE(sent_at, created_at) <= %s
                ORDER BY COALESCE(sent_at, created_at) DESC, id DESC
                """,
                (from_datetime, to_datetime),
            )
            rows = cursor.fetchall() or []
        connection.close()
        
        transformed_data = []
        for row in rows:
            phone = str(row.get('recipient') or '').strip()
            original_phone = phone
            
            display_name = phone
            if phone in group_names:
                display_name = group_names[phone]
            
            if row.get('api_type') == 'official':
                channel = "WABA"
            else:
                channel = "Unofficial"
            
            sent_at = row.get('sent_at') or row.get('created_at')
            
            transformed_data.append({
                'id': f"C-{row['id']}",  # C for Completed
                'labmateid': row.get('lab_id'),
                'phone': phone,
                'display_name': display_name,
                'original_phone': original_phone,
                'channel': channel,
                'report_link': row.get('media_url'),
                'status': 'sent',
                'status_details': row.get('template_name') or row.get('api_type'),
                'send_result': 'success',
                'message': row.get('message_text'),
                'sent_at': sent_at.isoformat() if isinstance(sent_at, datetime) else datetime.now(IST).isoformat(),
                'manual_send': 0,
                'manual_flag': False,
                'manual_by': None,
                'manual_time': None
            })
        
        return jsonify(transformed_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
