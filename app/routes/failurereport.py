from flask import Blueprint, render_template, jsonify, session, request
from flask_cors import CORS
from app.db.connection import get_db_connection, get_whatsapp_panel_connection, get_whatsapp_groups_connection
from mysql.connector import Error
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

failurereport_bp = Blueprint('failurereport', __name__)
CORS(failurereport_bp)
IST = ZoneInfo("Asia/Kolkata")

scheduler = None

def init_scheduler(app):
    global scheduler
    
    def schedule_auto_mark_failed():
        with app.app_context():
            try:
                pass
            except Exception:
                pass

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=schedule_auto_mark_failed, trigger="interval", minutes=20)
    scheduler.start()
    
    atexit.register(lambda: scheduler.shutdown() if scheduler else None)

@failurereport_bp.record_once
def on_load(state):
    app = state.app
    init_scheduler(app)

@failurereport_bp.route('/')
def index():
    return render_template('failurereport.html')

@failurereport_bp.route('/api/failed-deliveries')
def get_failed_deliveries():
    try:
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
        
        resolved_ids = set()
        local_conn = None
        try:
            local_conn = get_db_connection()
            with local_conn.cursor() as local_cursor:
                local_cursor.execute("SELECT main_id FROM failurereport_resolutions")
                resolved_ids = {int((r or {}).get("main_id") or 0) for r in local_cursor.fetchall() or []}
        except Exception:
            resolved_ids = set()
        finally:
            if local_conn:
                local_conn.close()

        connection = get_whatsapp_panel_connection()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, action_type, lab_id, recipient, message_text, media_url, api_type, template_name,
                       error_text, payload_json, created_at
                FROM whatsapp_send_logs
                WHERE is_success = 0
                ORDER BY created_at DESC, id DESC
                """
            )
            rows = cursor.fetchall() or []
        connection.close()
        
        transformed_data = []
        for row in rows:
            if int(row.get('id') or 0) in resolved_ids:
                continue
            phone = str(row.get('recipient') or '').strip()
            original_phone = phone
            
            display_name = phone
            
            if phone in group_names:
                display_name = group_names[phone]
            
            # Official API rows are WABA; local/group API rows are unofficial.
            if (row.get('api_type') or '').lower() == 'official':
                channel = "WABA"
            else:
                channel = "Unofficial"
            
            transformed_data.append({
                'id': f"F-{row['id']}",
                'labmateid': row.get('lab_id'),
                'phone': phone,
                'display_name': display_name,
                'original_phone': original_phone,
                'channel': channel,  # Only WABA or Unofficial
                'report_link': row.get('media_url'),
                'send_result': 'failed',
                'provider_status': f"{row.get('action_type') or 'whatsapp'} / {row.get('api_type') or '-'}",
                'error': row.get('error_text') or 'Unknown error',
                'message': row.get('message_text'),
                'payload': row.get('payload_json'),
                'sent_at': row['created_at'].isoformat() if isinstance(row.get('created_at'), datetime) else datetime.now(IST).isoformat(),
                'attempts': 1
            })
        
        return jsonify(transformed_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@failurereport_bp.route('/api/mark-resolved/<int:message_id>', methods=['POST'])
def mark_resolved(message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Login required'}), 401
    
    user_id = session.get('user_id')
    username = session.get('username', 'Unknown')
    
    local_conn = None
    
    try:
        wa_conn = get_whatsapp_panel_connection()
        with wa_conn.cursor() as wa_cursor:
            wa_cursor.execute(
                """
                SELECT id, lab_id, recipient
                FROM whatsapp_send_logs
                WHERE id = %s
                  AND is_success = 0
                LIMIT 1
                """,
                (message_id,),
            )
            message_data = wa_cursor.fetchone()
        wa_conn.close()

        if not message_data:
            return jsonify({'error': 'Message not found'}), 404

        labmate_id = message_data.get('lab_id')
        phone = message_data.get('recipient')
        
        local_conn = get_db_connection()
        if local_conn:
            cursor = local_conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS failurereport_resolutions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    main_id INT,
                    labmate_id VARCHAR(100),
                    phone VARCHAR(20),
                    resolved_by_userid INT,
                    resolved_by_username VARCHAR(100),
                    resolved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                INSERT INTO failurereport_resolutions 
                    (main_id, labmate_id, phone, resolved_by_userid, resolved_by_username)
                VALUES (%s, %s, %s, %s, %s)
            """, (message_id, labmate_id, phone, user_id, username))
            
            local_conn.commit()
            cursor.close()
        
        return jsonify({
            'success': True,
            'message': f'Message {message_id} marked as resolved',
            'data': {
                'message_id': message_id,
                'labmate_id': labmate_id,
                'resolved_by': username,
                'resolved_at': datetime.now(IST).isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500
    
    finally:
        if local_conn:
            try:
                local_conn.close()
            except:
                pass

@failurereport_bp.route('/api/auto-mark-failed', methods=['POST'])
def auto_mark_failed():
    return jsonify({
        'success': True,
        'message': 'No auto-mark needed; failures are written directly to whatsapp_send_logs',
        'updated_count': 0
    })
