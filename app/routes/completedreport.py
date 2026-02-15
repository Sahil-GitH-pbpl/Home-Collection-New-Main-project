from flask import Blueprint, render_template, jsonify, request
from app.db.connection import get_fail_message_connection, get_whatsapp_groups_connection, get_db_connection
from mysql.connector import Error
from datetime import datetime

completedreport_bp = Blueprint('completedreport', __name__)

@completedreport_bp.route('/')
def index():
    return render_template('completedreport.html')

@completedreport_bp.route('/api/completed-deliveries')
def get_completed_deliveries():
    """
    Fetch all completed/successful deliveries
    Conditions: resstatus NOT NULL and NOT 'failed'
    """
    connection = None
    
    try:
        # Get date filters from request
        from_date = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
        to_date = request.args.get('to', datetime.now().strftime('%Y-%m-%d'))
        
        # If only one date provided, use same for both
        if not from_date:
            from_date = datetime.now().strftime('%Y-%m-%d')
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
        
        # Connect to fail message database
        connection = get_fail_message_connection()
        if not connection:
            return jsonify({'error': 'Remote database connection failed'}), 500
        
        cursor = connection.cursor(dictionary=True)
        
        # Query to fetch completed deliveries
        # Conditions: previously successful responses or manual override
        query = """
        SELECT 
            id,
            labmateid,
            phone,
            message,
            link as report_link,
            status as send_result,
            resstatus,
            resstatusdet as status_details,
            datetimes as sent_at,
            manual_send
        FROM labmatewhats 
        WHERE (
              (resstatus IS NOT NULL 
               AND resstatus != 'failed'
               AND resstatus NOT LIKE '%failed%'
               AND resstatusdet NOT LIKE '%failed%'
               AND resstatusdet NOT LIKE '%error%')
               OR manual_send = 1
          )
          AND datetimes >= %s
          AND datetimes <= %s
        ORDER BY datetimes DESC
        """
        
        cursor.execute(query, (from_datetime, to_datetime))
        rows = cursor.fetchall()
        
        # Fetch manual resolution metadata for manual sends
        manual_meta = {}
        manual_ids = [row['id'] for row in rows if str(row.get('manual_send') or '').strip() == '1']
        if manual_ids:
            local_conn = None
            try:
                local_conn = get_db_connection()
                with local_conn.cursor() as local_cursor:
                    placeholders = ','.join(['%s'] * len(manual_ids))
                    local_cursor.execute(
                        f"""
                        SELECT main_id, resolved_by_username, resolved_at
                        FROM failurereport_resolutions
                        WHERE main_id IN ({placeholders})
                        ORDER BY resolved_at DESC
                        """,
                        manual_ids
                    )
                    for info in local_cursor.fetchall():
                        main_id = info.get('main_id')
                        if main_id is None or main_id in manual_meta:
                            continue
                        resolved_at = info.get('resolved_at')
                        manual_meta[main_id] = {
                            'resolved_by': info.get('resolved_by_username'),
                            'resolved_at': resolved_at.isoformat() if isinstance(resolved_at, datetime) else str(resolved_at) if resolved_at else None
                        }
            except Exception:
                manual_meta = {}
            finally:
                if local_conn:
                    try:
                        local_conn.close()
                    except Exception:
                        pass
        
        transformed_data = []
        for row in rows:
            phone = str(row['phone']).strip()
            original_phone = phone
            
            display_name = phone
            if phone in group_names:
                display_name = group_names[phone]
            
            # Determine channel
            if phone.startswith('91') and len(phone) == 12:
                channel = "WABA"
            else:
                channel = "Unofficial"
            
            # Determine status (match success keywords or include manual overrides)
            resstatus = (row['resstatus'] or '').lower()
            status_details = (row['status_details'] or '').lower()
            manual_flag = str(row.get('manual_send') or '').strip() == '1'
            manual_info = manual_meta.get(row['id'], {})
            combined_status = f"{resstatus} {status_details}".strip()
            failure_phrases = ['not delivered', 'undeliverable', 'unable to deliver']
            contains_failure_phrase = any(phrase in combined_status for phrase in failure_phrases)
            has_success_keyword = (not contains_failure_phrase) and any(
                keyword in combined_status for keyword in ['sent', 'delivered', 'read']
            )
            
            # Manual overrides should always be included and labelled as manual
            if manual_flag:
                status = 'manual'
            else:
                # Skip entries that have neither success keywords nor manual override
                if not has_success_keyword:
                    continue
                
                if 'read' in resstatus or 'read' in status_details:
                    status = 'read'
                elif 'delivered' in resstatus or 'delivered' in status_details:
                    status = 'delivered'
                elif 'sent' in resstatus or 'sent' in status_details:
                    status = 'sent'
                else:
                    # Should not happen because we already checked keywords, but keep safe default
                    status = 'sent'
            
            transformed_data.append({
                'id': f"C-{row['id']}",  # C for Completed
                'labmateid': row['labmateid'],
                'phone': phone,
                'display_name': display_name,
                'original_phone': original_phone,
                'channel': channel,
                'report_link': row['report_link'],
                'status': status,
                'status_details': row['status_details'],
                'send_result': row['send_result'],
                'message': row['message'],
                'sent_at': row['sent_at'].isoformat() if isinstance(row['sent_at'], datetime) else datetime.now().isoformat(),
                'manual_send': row['manual_send'],
                'manual_flag': manual_flag,
                'manual_by': manual_info.get('resolved_by') if manual_flag else None,
                'manual_time': manual_info.get('resolved_at') if manual_flag else None
            })
        
        cursor.close()
        return jsonify(transformed_data)
    
    except Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if connection and connection.is_connected():
            connection.close()
