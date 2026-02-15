#!/usr/bin/env python3
"""
Daily Ticketing Summary - CCE & ODT Combined (Auto-send, IST Today Only)
Updated with local WhatsApp API
"""

import os
import sys
import argparse
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from pymysql.cursors import DictCursor
import requests

# Ensure console can emit UTF-8 (for emoji-laden preview)
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---- Import your DB connector ----
try:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from app.db.connection import get_db_connection  # noqa: E402
except Exception as e:
    print(f"❌ Could not import get_db_connection: {e}", file=sys.stderr)
    sys.exit(1)

IST = ZoneInfo("Asia/Kolkata")

# ----------------- WhatsApp (Local API) -----------------
WHATSAPP_API_URL = "http://192.168.0.71:3004/api/messages/send"
WHATSAPP_ACCOUNT_ID = 1
# Default WhatsApp destination (CCE daily summary group)  
DEFAULT_TARGET = "120363418373355488@g.us"


def send_whatsapp(phone: str, message: str) -> tuple[int, str]:
    """Send WhatsApp using local API (POST request with JSON payload)."""
    try:
        normalized = (phone or "").strip()

        if normalized and "@g.us" not in normalized:
            normalized = normalized.replace("+", "").replace(" ", "")
            if normalized.startswith("0"):
                normalized = normalized.lstrip("0")
            if not normalized.startswith("91"):
                normalized = f"91{normalized}"

        payload = {
            "accountId": WHATSAPP_ACCOUNT_ID,
            "target": normalized,
            "message": message,
        }

        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        response = requests.post(
            WHATSAPP_API_URL, json=payload, headers=headers, timeout=30
        )
        return response.status_code, (response.text or "")

    except requests.exceptions.Timeout:
        return 408, "Request timeout"
    except requests.exceptions.ConnectionError:
        return 503, "Connection error - cannot reach WhatsApp API"
    except Exception as e:
        return 500, f"Exception: {e}"


# ----------------- Helpers -----------------
def _ist_day_window(date_yyyy_mm_dd: str | None) -> tuple[datetime, datetime, str]:
    if date_yyyy_mm_dd:
        d = datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").date()
    else:
        d = datetime.now(IST).date()
    start_ist = datetime.combine(d, time.min, tzinfo=IST)
    end_ist = datetime.combine(d, time.max, tzinfo=IST)
    pretty = d.strftime("%d-%b-%Y")
    return start_ist, end_ist, pretty


def _fmt_dt(dt) -> str:
    if not dt:
        return "-"
    try:
        if isinstance(dt, str):
            try:
                dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST).strftime("%Y-%m-%d %I:%M %p")
    except Exception:
        return str(dt)


def _q_scalar(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    v = cur.fetchone()
    cur.close()
    if isinstance(v, (tuple, list)):
        return v[0]
    if isinstance(v, dict):
        return list(v.values())[0] if v else 0
    return v or 0


def _q_all(conn, sql, params=(), dict_cursor=True):
    cur = conn.cursor(DictCursor if dict_cursor else None)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows or []


# ----------------- Message Builder - CCE & ODT Combined -----------------
def _lead_status_breakdown(conn, status: str, start_ist, end_ist):
    sql = """
        SELECT DATE(l.created_at) AS created_date, COUNT(*) AS cnt
        FROM lead_history lh
        JOIN leads l ON l.lead_id = lh.lead_id
        WHERE lh.created_at BETWEEN %s AND %s
          AND lh.status = %s
        GROUP BY DATE(l.created_at)
    """
    return _q_all(conn, sql, (start_ist, end_ist, status))


def _format_lead_breakdown(rows, today_date):
    if not rows:
        return ""
    yesterday = today_date - timedelta(days=1)
    date_counts = {}
    for r in rows:
        c_date = r.get("created_date")
        cnt = int(r.get("cnt") or 0)
        date_counts[c_date] = date_counts.get(c_date, 0) + cnt

    parts = []
    today_cnt = date_counts.pop(today_date, 0)
    if today_cnt:
        parts.append(f"Today: {today_cnt}")
    yesterday_cnt = date_counts.pop(yesterday, 0)
    if yesterday_cnt:
        parts.append(f"Yesterday: {yesterday_cnt}")

    other_dates = sorted(d for d in date_counts.keys() if d)
    for dt_key in other_dates:
        parts.append(f"{dt_key.strftime('%d-%b')}: {date_counts[dt_key]}")

    unknown_cnt = date_counts.get(None, 0)
    if unknown_cnt:
        parts.append(f"Unknown: {unknown_cnt}")

    return f" ({', '.join(parts)})" if parts else ""


def build_daily_ticket_summary_combined(conn, date_str: str | None = None) -> str:
    """
    CCE + ODT Combined - Today IST
    """
    start_ist, end_ist, pretty_date = _ist_day_window(date_str)

    # Call Metrics (Exotel) - align with UI logic
    total_calls = _q_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM exotel_incoming_calls
        WHERE received_at BETWEEN %s AND %s
        """,
        (start_ist, end_ist),
    ) or 0

    completed_live_calls = _q_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM exotel_incoming_calls i
        WHERE i.received_at BETWEEN %s AND %s
          AND LOWER(i.call_type) = 'completed'
          AND NOT EXISTS (
            SELECT 1 FROM exotel_outgoing_calls o
            WHERE o.call_sid = i.call_sid
          )
        """,
        (start_ist, end_ist),
    ) or 0

    callbacks_outgoing = _q_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM exotel_outgoing_calls
        WHERE created_at BETWEEN %s AND %s
        """,
        (start_ist, end_ist),
    ) or 0

    # Lead Metrics
    leads_created = _q_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM leads
        WHERE created_at BETWEEN %s AND %s
        """,
        (start_ist, end_ist),
    ) or 0

    booked_rows = _lead_status_breakdown(conn, "Booked", start_ist, end_ist)
    canceled_rows = _lead_status_breakdown(conn, "Canceled", start_ist, end_ist)
    leads_booked = sum(int(r.get("cnt") or 0) for r in booked_rows)
    leads_canceled = sum(int(r.get("cnt") or 0) for r in canceled_rows)
    today_date = start_ist.date()
    booked_breakdown = _format_lead_breakdown(booked_rows, today_date)
    canceled_breakdown = _format_lead_breakdown(canceled_rows, today_date)

    # Failure Reports
    failure_reports_resolved = _q_scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM failurereport_resolutions
        WHERE resolved_at BETWEEN %s AND %s
        """,
        (start_ist, end_ist),
    ) or 0

    # CCE Metrics
    count_raised_cce = _q_scalar(
        conn,
        "SELECT COUNT(*) FROM tickets WHERE created_at BETWEEN %s AND %s AND ticket_origin = 'CCE'",
        (start_ist, end_ist),
    )

    count_closed_today_cce = _q_scalar(
        conn,
        "SELECT COUNT(*) FROM tickets WHERE status='Closed' AND closed_at BETWEEN %s AND %s AND ticket_origin = 'CCE'",
        (start_ist, end_ist),
    )

    # ODT Metrics
    count_raised_odt = _q_scalar(
        conn,
        "SELECT COUNT(*) FROM tickets WHERE created_at BETWEEN %s AND %s AND ticket_origin = 'ODT'",
        (start_ist, end_ist),
    )

    count_closed_today_odt = _q_scalar(
        conn,
        "SELECT COUNT(*) FROM tickets WHERE status='Closed' AND closed_at BETWEEN %s AND %s AND ticket_origin = 'ODT'",
        (start_ist, end_ist),
    )

    # By Category - CCE
    by_category_cce = _q_all(
        conn,
        """
        SELECT COALESCE(ticket_category,'-') AS ticket_category, COUNT(*) AS cnt
        FROM tickets
        WHERE created_at BETWEEN %s AND %s AND ticket_origin = 'CCE'
        GROUP BY ticket_category
        ORDER BY cnt DESC, ticket_category ASC
        """,
        (start_ist, end_ist),
    )

    # By Category - ODT
    by_category_odt = _q_all(
        conn,
        """
        SELECT COALESCE(ticket_category,'-') AS ticket_category, COUNT(*) AS cnt
        FROM tickets
        WHERE created_at BETWEEN %s AND %s AND ticket_origin = 'ODT'
        GROUP BY ticket_category
        ORDER BY cnt DESC, ticket_category ASC
        """,
        (start_ist, end_ist),
    )

    # By Staff - CCE
    by_staff_cce = _q_all(
        conn,
        """
        SELECT COALESCE(created_by,'-') AS created_by, COUNT(*) AS cnt
        FROM tickets
        WHERE created_at BETWEEN %s AND %s AND ticket_origin = 'CCE'
        GROUP BY created_by
        ORDER BY cnt DESC, created_by ASC
        """,
        (start_ist, end_ist),
    )

    # By Staff - ODT
    by_staff_odt = _q_all(
        conn,
        """
        SELECT COALESCE(created_by,'-') AS created_by, COUNT(*) AS cnt
        FROM tickets
        WHERE created_at BETWEEN %s AND %s AND ticket_origin = 'ODT'
        GROUP BY created_by
        ORDER BY cnt DESC, created_by ASC
        """,
        (start_ist, end_ist),
    )

    # Sad Closures - CCE
    sad_cce = _q_all(
        conn,
        """
        SELECT id AS ticket_id, ticket_category, COALESCE(additional_info,'') AS additional_info,
               closed_at, created_at, created_by, ticket_origin
        FROM tickets
        WHERE status='Closed'
          AND closed_at BETWEEN %s AND %s
          AND closed_remark LIKE '%%SAD%%'
          AND ticket_origin = 'CCE'
        ORDER BY closed_at ASC
        """,
        (start_ist, end_ist),
    )

    # Sad Closures - ODT
    sad_odt = _q_all(
        conn,
        """
        SELECT id AS ticket_id, ticket_category, COALESCE(additional_info,'') AS additional_info,
               closed_at, created_at, created_by, ticket_origin
        FROM tickets
        WHERE status='Closed'
          AND closed_at BETWEEN %s AND %s
          AND closed_remark LIKE '%%SAD%%'
          AND ticket_origin = 'ODT'
        ORDER BY closed_at ASC
        """,
        (start_ist, end_ist),
    )

    # Build message - Combined
    lines = []
    lines.append("📝 *Daily Ticket Summary - CCE & ODT*")
    lines.append(f"📅 Date: {pretty_date} (IST)")
    lines.append("")

    # CCE Section
    lines.append("🏢 *CCE - Tickets:*")
    lines.append(f"📌 Tickets Raised: {count_raised_cce}")
    lines.append(f"✅ Tickets Closed: {count_closed_today_cce}")
    lines.append("")
    lines.append("🎯 *CCE - By Category:*")
    if by_category_cce:
        for r in by_category_cce:
            lines.append(f"▪️ {r['ticket_category']}: {r['cnt']}")
    else:
        lines.append("—")
    lines.append("")
    lines.append("👤 *CCE - By Staff:*")
    if by_staff_cce:
        for r in by_staff_cce:
            lines.append(f"▪️ {r['created_by']}: {r['cnt']}")
    else:
        lines.append("—")
    lines.append("")

    # ODT Section
    lines.append("🏥 *ODT - Tickets:*")
    lines.append(f"📌 Tickets Raised: {count_raised_odt}")
    lines.append(f"✅ Tickets Closed: {count_closed_today_odt}")
    lines.append("")
    lines.append("🎯 *ODT - By Category:*")
    if by_category_odt:
        for r in by_category_odt:
            lines.append(f"▪️ {r['ticket_category']}: {r['cnt']}")
    else:
        lines.append("—")
    lines.append("")
    lines.append("👤 *ODT - By Staff:*")
    if by_staff_odt:
        for r in by_staff_odt:
            lines.append(f"▪️ {r['created_by']}: {r['cnt']}")
    else:
        lines.append("—")
    lines.append("")

    # Sad Closures - Combined
    lines.append("😞 *Sad Closures (Today):*")
    lines.append("")

    if sad_cce:
        lines.append("*CCE Sad Closures:*")
        for t in sad_cce:
            lines.extend(
                [
                    f"🆔 Ticket ID: {t['ticket_id']}",
                    f"Category: {t['ticket_category'] or '-'}",
                    f"📝 Remarks: {t['additional_info'].strip() if t['additional_info'] else '-'}",
                    f"🕓 Time: {_fmt_dt(t['closed_at'])}",
                    f"👤 Created By: {t['created_by'] or '-'}",
                    "",
                ]
            )
    else:
        lines.append("✅ *No CCE Sad Closures Today!* 🎉")
        lines.append("")

    if sad_odt:
        lines.append("*ODT Sad Closures:*")
        for t in sad_odt:
            lines.extend(
                [
                    f"🆔 Ticket ID: {t['ticket_id']}",
                    f"Category: {t['ticket_category'] or '-'}",
                    f"📝 Remarks: {t['additional_info'].strip() if t['additional_info'] else '-'}",
                    f"🕓 Time: {_fmt_dt(t['closed_at'])}",
                    f"👤 Created By: {t['created_by'] or '-'}",
                    "",
                ]
            )
    else:
        lines.append("✅ *No ODT Sad Closures Today!* 🎉")
        lines.append("")

    lines.append("")
    lines.append("----------------------------------------")
    lines.append("📝 *Daily Lead Summary - CCE & ODT*")
    lines.append(f"📅 Date: {pretty_date} (IST)")
    lines.append("")
    lines.append("🧮 *Leads (Today):*")
    lines.append(f" Total Created: {leads_created}")
    lines.append(f" Total Booked: {leads_booked}{booked_breakdown}")
    lines.append(f" Total Canceled: {leads_canceled}{canceled_breakdown}")
    lines.append("")
    lines.append("----------------------------------------")
    lines.append("📝 *Daily Call Summary - CCE & ODT*")
    lines.append(f"📅 Date: {pretty_date} (IST)")
    lines.append("")
    lines.append("📞 *Calls (CCE Desk):*")
    lines.append(f" Total Incoming: {total_calls}")
    lines.append(f" Completed (Live Pick): {completed_live_calls}")
    lines.append(f" Callbacks Initiated (Outgoing): {callbacks_outgoing}")
    lines.append("")
    lines.append("----------------------------------------")
    lines.append("📝 *Daily Failure Summary - CCE & ODT*")
    lines.append(f"📅 Date: {pretty_date} (IST)")
    lines.append("")
    lines.append("⚠️ *Failure Reports:*")
    lines.append(f" Resolved Today: {failure_reports_resolved}")
    lines.append("")
    return "\n".join(lines)


# ----------------- CLI - Combined -----------------
def main():
    ap = argparse.ArgumentParser(
        description="Daily Ticketing Summary - CCE & ODT Combined"
    )
    ap.add_argument("--to", help="Override destination phone number")
    args = ap.parse_args()

    conn = get_db_connection()
    try:
        msg = build_daily_ticket_summary_combined(conn, None)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print("\n" + "=" * 30 + " COMBINED PREVIEW " + "=" * 30 + "\n")
    print(msg)
    print("\n" + "=" * 72 + "\n")

    phone = args.to or DEFAULT_TARGET
    if not phone:
        print("❌ Missing phone number.", file=sys.stderr)
        return 2

    print(f"📱 Sending to: {phone}")
    print("🔄 Sending WhatsApp message...")

    status, resp = send_whatsapp(phone, msg)
    ok = status in (200, 201)

    print(f"📡 API Response - HTTP Status: {status} | Success: {ok}")
    if resp:
        print(f"📄 Response: {resp[:800]}")

    if ok:
        print("✅ WhatsApp message sent successfully!")
    else:
        print(f"❌ Failed to send WhatsApp message. Status: {status}")
        print(f"Error: {resp}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
