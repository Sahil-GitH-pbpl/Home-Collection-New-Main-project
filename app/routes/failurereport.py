from flask import Blueprint, render_template, jsonify, session, request
from flask_cors import CORS
from app.db.connection import get_db_connection, get_fail_message_connection, get_whatsapp_groups_connection
from mysql.connector import Error
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

failurereport_bp = Blueprint('failurereport', __name__)
CORS(failurereport_bp)

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
    connection = None
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
        
        connection = get_fail_message_connection()
        if not connection:
            return jsonify({'error': 'Remote database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        query = """
        SELECT 
            id,
            labmateid,
            phone,
            message,
            link as report_link,
            status as send_result,
            resstatus as provider_status,
            resstatusdet as error,
            datetimes as sent_at,
            manual_send
        FROM labmatewhats 
        WHERE (resstatus = 'failed' 
           OR resstatus LIKE '%failed%'
           OR resstatusdet LIKE '%failed%'
           OR resstatusdet LIKE '%error%')
           AND (manual_send = 0 OR manual_send IS NULL)
        ORDER BY datetimes DESC
        """
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        transformed_data = []
        for row in rows:
            phone = str(row['phone']).strip()
            original_phone = phone
            
            display_name = phone
            
            if phone in group_names:
                display_name = group_names[phone]
            
            # SIMPLE CHANNEL LOGIC - Only 2 channels
            if phone.startswith('91') and len(phone) == 12:
                channel = "WABA"  # Changed from "WABA (Individual)"
            else:
                channel = "Unofficial"  # WhatsApp Group will also be Unofficial
            
            transformed_data.append({
                'id': f"F-{row['id']}",
                'labmateid': row['labmateid'],
                'phone': phone,
                'display_name': display_name,
                'original_phone': original_phone,
                'channel': channel,  # Only WABA or Unofficial
                'report_link': row['report_link'],
                'send_result': row['send_result'],
                'provider_status': row['provider_status'],
                'error': row['error'] or 'Unknown error',
                'message': row['message'],
                'sent_at': row['sent_at'].isoformat() if isinstance(row['sent_at'], datetime) else datetime.now().isoformat(),
                'attempts': 1
            })
        
        cursor.close()
        return jsonify(transformed_data)
    
    except Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if connection and connection.is_connected():
            connection.close()

@failurereport_bp.route('/api/mark-resolved/<int:message_id>', methods=['POST'])
def mark_resolved(message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Login required'}), 401
    
    user_id = session.get('user_id')
    username = session.get('username', 'Unknown')
    
    remote_conn = None
    local_conn = None
    
    try:
        remote_conn = get_fail_message_connection()
        if not remote_conn:
            return jsonify({'error': 'Cannot connect to message database'}), 500
        
        remote_cursor = remote_conn.cursor(dictionary=True)
        
        remote_cursor.execute("""
            SELECT labmateid, phone 
            FROM labmatewhats 
            WHERE id = %s
        """, (message_id,))
        
        message_data = remote_cursor.fetchone()
        
        if not message_data:
            return jsonify({'error': 'Message not found'}), 404
        
        labmate_id = message_data['labmateid']
        phone = message_data['phone']
        
        remote_cursor.execute("""
            UPDATE labmatewhats 
            SET manual_send = 1 
            WHERE id = %s
        """, (message_id,))
        
        remote_conn.commit()
        remote_cursor.close()
        
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
                'resolved_at': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500
    
    finally:
        if remote_conn and remote_conn.is_connected():
            remote_conn.close()
        if local_conn:
            try:
                local_conn.close()
            except:
                pass

@failurereport_bp.route('/api/auto-mark-failed', methods=['POST'])
def auto_mark_failed():
    connection = None
    try:
        connection = get_fail_message_connection()
        if not connection:
            return jsonify({'error': 'Remote database connection failed'}), 500
        
        cursor = connection.cursor()
        
        update_query = """
        UPDATE labmatewhats 
        SET resstatus = 'failed',
            resstatusdet = CONCAT(COALESCE(resstatusdet, ''), ' [Auto-marked: No status after 20min]')
        WHERE (resstatus IS NULL OR resstatus = '')
        AND datetimes <= NOW() - INTERVAL 20 MINUTE
        AND (manual_send = 0 OR manual_send IS NULL)
        ORDER BY datetimes DESC
        LIMIT 500
        """
        
        cursor.execute(update_query)
        updated_count = cursor.rowcount
        connection.commit()
        cursor.close()
        
        return jsonify({
            'success': True, 
            'message': f'Automatically marked {updated_count} records as failed',
            'updated_count': updated_count
        })
    
    except Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if connection and connection.is_connected():
            connection.close()
