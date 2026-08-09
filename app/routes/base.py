# base.py
from flask import Blueprint, render_template, redirect, url_for, session, jsonify, current_app
from app.db.connection import get_complaint_connection, get_db_connection, get_whatsapp_panel_connection
import pymysql
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import mysql.connector


base_bp = Blueprint("base", __name__)

IST = ZoneInfo("Asia/Kolkata")


@base_bp.before_app_request
def _ensure_session_defaults():
    """Safe defaults so templates/JS don't explode when session keys are missing."""
    session.setdefault("user_id", None)
    session.setdefault("username", None)
    session.setdefault("designation", None)


@base_bp.route("/")
def home():
    return redirect(url_for("base.dashboard"))


@base_bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# ---------------- Helpers ----------------
def _as_aware_ist(val):
    """
    Convert DB datetime (str or datetime) to IST-aware datetime.
    - If naive datetime: assume IST.
    - If aware datetime: convert to IST.
    - If string: parse common formats, assume IST.
    - Else: return None.
    """
    if not val:
        return None

    if isinstance(val, datetime):
        return val.replace(tzinfo=IST) if val.tzinfo is None else val.astimezone(IST)

    if isinstance(val, str):
        fmts = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        )
        for fmt in fmts:
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=IST)
            except Exception:
                continue
        return None

    return None


def _slot_start_minutes(slot_text: str):
    token = re.split(r"\bto\b|-", (slot_text or "").strip(), maxsplit=1, flags=re.IGNORECASE)[0].strip()
    token = token.replace(".", "").upper().replace(" ", "")
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?(AM|PM)$", token)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = match.group(3)
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    return hour * 60 + minute


# ---------------- COUNTERS API (NO POOL) ----------------
@base_bp.route("/api/tickets/counters")
def tickets_counters():
    user_id = session.get("user_id")
    username = (session.get("username") or "").strip().lower()
    if not user_id:
        return jsonify({"ok": False, "error": "Not logged in"}), 401
    try:
        user_id = int(user_id)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid user id"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # 1) GLOBAL breached (pure SQL)
            cur.execute("""
                SELECT COUNT(*) AS breached
                FROM tickets
                WHERE (status IS NULL OR status='' OR status='Open' OR status='open')
                  AND commitment_at IS NOT NULL
                  AND commitment_at <= NOW()
            """)
            my_breached = cur.fetchone()["breached"] or 0

            # 2) ASSIGNED to current user (effective assignee via active claim OR static)
            cur.execute("""
                SELECT COUNT(*) AS assigned_cnt
                FROM tickets t
                LEFT JOIN (
                    SELECT ticket_id, user_id
                    FROM ticket_claims
                    WHERE is_active=1 AND expires_at > NOW()
                ) c ON c.ticket_id = t.id
                WHERE (t.status IS NULL OR t.status='' OR t.status='Open' OR t.status='open')
                  AND COALESCE(c.user_id, t.assign_to_user_id) = %s
            """, (user_id,))
            assigned = cur.fetchone()["assigned_cnt"] or 0

            # 3) TAGGED (user-specific, needs JSON; fetch ONLY open tickets with tags_json not null)
            cur.execute("""
                SELECT id, tags_json
                FROM tickets
                WHERE (status IS NULL OR status='' OR status='Open' OR status='open')
                  AND tags_json IS NOT NULL AND tags_json <> ''
            """)
            rows = cur.fetchall()

            # 4) Fresh Leads
            cur.execute("""
                SELECT COUNT(*) AS fresh_leads
                FROM leads
                WHERE (
                        status = 'Open'
                     OR (status IN ('No Response','Call Back Later')
                         AND callback IS NOT NULL
                         AND callback <= NOW())
                      )
                  AND (
                        current_lock_user_name IS NULL
                     OR lock_expires_at IS NULL
                     OR lock_expires_at < NOW()
                  )
            """)
            leads_fresh = cur.fetchone().get("fresh_leads", 0) or 0

            # 5) Missed Calls counter
            cur.execute("""
                SELECT COUNT(*) AS missed_calls
                FROM exotel_incoming_calls i
                WHERE 
                    (
                        LOWER(i.call_type) = 'client-hangup'
                        OR (
                            LOWER(i.call_type) = 'call-attempt'
                            AND i.created_at <= NOW() - INTERVAL 15 MINUTE
                        )
                        OR LOWER(i.call_type) = 'incomplete'
                    )
                    AND LOWER(i.call_type) <> 'completed'
            """)
            missed_calls = cur.fetchone().get("missed_calls", 0) or 0

            # 6) Unassigned ODT + RST Tickets
            cur.execute("""
                SELECT COUNT(*) AS odt_rst_unassigned
                FROM tickets
                WHERE (status IS NULL OR status='' OR status='Open' OR status='open')
                  AND ticket_origin IN ('ODT', 'RST')
                  AND assign_to_user_id IS NULL
            """)
            odt_rst_unassigned = cur.fetchone().get("odt_rst_unassigned", 0) or 0

            # 7) Open CVT tickets (all open, regardless of assignee)
            cur.execute("""
                SELECT COUNT(*) AS cvt_open
                FROM tickets
                WHERE (status IS NULL OR status='' OR status='Open' OR status='open')
                  AND ticket_origin = 'CVT'
            """)
            cvt_open = cur.fetchone().get("cvt_open", 0) or 0

            now_ist = datetime.now(IST)
            target_date = now_ist.date()
            cutoff_minutes = (now_ist.hour * 60) + now_ist.minute + 30
            cur.execute("""
                SELECT preferred_time_slot
                FROM hhome_collection_booking
                WHERE preferred_visit_date = %s
                  AND booking_status = 0
                  AND (assigned_phlebotomist_id IS NULL OR assigned_phlebotomist_id = 0)
                  AND preferred_time_slot IS NOT NULL
                  AND TRIM(preferred_time_slot) <> ''
                UNION ALL
                SELECT preferred_time_slot
                FROM hhome_collection_booking_appointment
                WHERE preferred_visit_date = %s
                  AND appointment_status = 0
                  AND (assigned_phlebotomist_id IS NULL OR assigned_phlebotomist_id = 0)
                  AND preferred_time_slot IS NOT NULL
                  AND TRIM(preferred_time_slot) <> ''
            """, (target_date, target_date))
            hcb_due_unassigned = sum(
                1
                for row in (cur.fetchall() or [])
                if (_slot_start_minutes(row.get("preferred_time_slot") or "") or 99999) <= cutoff_minutes
            )

    except Exception as e:
        current_app.logger.error(f"[tickets_counters] DB error: {e}")
        return jsonify({"ok": False, "error": "DB error"}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # 3b) Parse tags locally (200-ish rows -> fast)
    tagged = 0
    for r in rows:
        raw = r.get("tags_json") or "[]"
        try:
            tags = json.loads(raw)
            if isinstance(tags, str):
                tags = json.loads(tags)
        except Exception:
            tags = []
        if isinstance(tags, dict):
            tags = [tags]
        if not isinstance(tags, list):
            continue

        # find first un-acked match for this user
        for tg in tags:
            try:
                staff_id = tg.get("staffId")
                staff_name = (tg.get("staffName") or tg.get("text") or "").strip().lower()
                acked = tg.get("ackedAt")
                if not acked and (
                    (staff_id and int(staff_id) == user_id)
                    or (staff_name == username and username != "")
                ):
                    tagged += 1
                    break
            except Exception:
                continue



    wchat_unassigned = 0
    wchat_mine = 0
    wa_conn = None
    try:
        current_user_name = (session.get("username") or "").strip()
        wa_conn = get_whatsapp_panel_connection()
        with wa_conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT
                  SUM(CASE
                    WHEN COALESCE(cs.status, 'open') <> 'closed'
                     AND COALESCE(TRIM(cs.owner_name), '') = ''
                    THEN 1 ELSE 0 END) AS wchat_unassigned,
                  SUM(CASE
                    WHEN COALESCE(cs.status, 'open') <> 'closed'
                     AND COALESCE(TRIM(cs.owner_name), '') = %s
                    THEN 1 ELSE 0 END) AS wchat_mine
                FROM (
                  SELECT mobile
                  FROM (
                    SELECT mobile, datetimess, msg, img, pdff, docid, imgid FROM ofc_waba_incoming
                    UNION ALL
                    SELECT mobile, datetimess, msg, img, pdff, docid, imgid FROM ofc_waba_outgoing
                  ) msg_rows
                  WHERE msg <> '' OR img <> '' OR pdff <> '' OR docid <> '' OR imgid <> ''
                  GROUP BY mobile
                ) active_chats
                LEFT JOIN ofc_conversation_live_state cs ON cs.mobile = active_chats.mobile
            """, (current_user_name,))
            row = cur.fetchone() or {}
            wchat_unassigned = int(row.get("wchat_unassigned") or 0)
            wchat_mine = int(row.get("wchat_mine") or 0)
    except Exception as e:
        current_app.logger.error(f"[tickets_counters] WhatsApp panel counter error: {e}")
        wchat_unassigned = 0
        wchat_mine = 0
    finally:
        try:
            if wa_conn:
                wa_conn.close()
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "assigned": assigned,
        "tagged": tagged,
        "my_breached": my_breached,
        "leads_fresh": int(leads_fresh),
        "missed_calls": int(missed_calls),
        "odt_rst_unassigned": int(odt_rst_unassigned),
        "cvt_open": cvt_open,
        "hcb_due_unassigned": int(hcb_due_unassigned),
        "wchat_unassigned": wchat_unassigned,
        "wchat_mine": wchat_mine,
        "nchat_unassigned": wchat_unassigned,
    })


# ---------------- FAILED MESSAGES COUNTER (separate) ----------------
@base_bp.route("/api/tickets/failed-messages")
def tickets_failed_messages():
    failed_messages = 0
    try:
        labmate_conn = mysql.connector.connect(
            host='10.1.1.51',
            user='sahil',
            password='sahil@123',
            database='labmaterecod'
        )
        with labmate_conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT COUNT(*) as failed_count
                FROM labmatewhats 
                WHERE (resstatus = 'failed' 
                   OR resstatus LIKE '%failed%'
                   OR resstatusdet LIKE '%failed%'
                   OR resstatusdet LIKE '%error%')
                   AND (manual_send = 0 OR manual_send IS NULL)
            """)
            result = cur.fetchone()
            failed_messages = result['failed_count'] if result else 0
        labmate_conn.close()
    except Exception as e:
        current_app.logger.error(f"Failed to fetch failed messages count: {e}")
        failed_messages = 0
    return jsonify({"ok": True, "failed_messages": failed_messages})


@base_bp.route("/api/tickets/complaint-count")
def tickets_complaint_count():
    complaint_count = 0
    conn = None
    cursor = None
    try:
        conn = get_complaint_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT COUNT(*) AS complaint_count
            FROM feedback_responses
            WHERE has_updates = 0
              AND status IN ('manual_review', 'ticket_created')
            """
        )
        result = cursor.fetchone()
        complaint_count = int((result or {}).get("complaint_count") or 0)
    except Exception as exc:
        current_app.logger.error(f"Failed to fetch complaint count: {exc}")
        complaint_count = 0
    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except Exception:
            pass

    return jsonify({"ok": True, "complaint_count": complaint_count})
