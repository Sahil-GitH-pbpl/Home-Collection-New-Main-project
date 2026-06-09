import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.alerts import send_whatsapp_to_number
from app.db.connection import get_db_connection


TARGET_PHONE = "8057054076"

TBS_LABELS = {
    "1": "Test confirmed and booked",
    "2": "Prescription attached but test not booked",
    "3": "No test information: ask to patient for tests",
    "4": "Incompleted test, phlebo verification pending to confirm and book",
}

PHLEBO_TBS_LABELS = {
    "confirmed_booked": "Confirmed Booked",
    "manual_hcb_slip": "Manual HCB Slip",
    "incomplete_reg_exec": "Incomplete Reg Exec",
}


def norm(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def as_float(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def money(value) -> str:
    value = round(as_float(value), 2)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}"


def fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%y")


def parse_json_obj(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_json_list(raw):
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    return data if isinstance(data, list) else []


def parse_time_to_minutes(value):
    text = norm(value).replace("–", "-").replace("—", "-")
    if not text:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})\s*([AP]M)?", text, flags=re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = (match.group(3) or "").upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    return hour * 60 + minute


def slot_start_end(slot_text):
    parts = norm(slot_text).split("-", 1)
    start = parse_time_to_minutes(parts[0] if parts else "")
    end = parse_time_to_minutes(parts[1] if len(parts) > 1 else "")
    if start is not None and end is None:
        end = start + 30
    return start, end


def is_late_start(row) -> bool:
    start_mins, _ = slot_start_end(row.get("preferred_time_slot"))
    actual_start = parse_time_to_minutes(row.get("strt_time"))
    return start_mins is not None and actual_start is not None and actual_start > start_mins + 5


def is_late_complete(row) -> bool:
    _, end_mins = slot_start_end(row.get("preferred_time_slot"))
    actual_complete = parse_time_to_minutes(row.get("cmplt_time"))
    try:
        status = int(row.get("status") or 0)
    except Exception:
        status = 0
    return status == 3 and end_mins is not None and actual_complete is not None and actual_complete > end_mins + 5


def selected_patient_ids(raw):
    ids = []
    for item in parse_json_list(raw):
        try:
            pid = int(item or 0)
        except Exception:
            pid = 0
        if pid > 0:
            ids.append(pid)
    return ids


def tbs_label(raw) -> str:
    text = norm(raw)
    if text in TBS_LABELS:
        return TBS_LABELS[text]
    lower = text.lower()
    for label in TBS_LABELS.values():
        if lower == label.lower():
            return label
    return text


def phlebo_tbs_label(raw) -> str:
    text = norm(raw)
    if not text or text == "-":
        return "None"
    key = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return PHLEBO_TBS_LABELS.get(key, text)


def fetch_booking_rows(cur, target_date: date):
    cur.execute(
        """
        SELECT
            'BOOKING' AS source_type,
            b.id AS booking_id,
            0 AS appointment_id,
            b.booking_status AS status,
            b.preferred_visit_date,
            b.preferred_time_slot,
            COALESCE(NULLIF(TRIM(b.strt_time), ''), NULLIF(TRIM(b.start_time), ''), '') AS strt_time,
            COALESCE(NULLIF(TRIM(b.cmplt_time), ''), NULLIF(TRIM(b.complete_time), ''), '') AS cmplt_time,
            b.selected_address_id,
            b.assigned_phlebotomist_id,
            COALESCE(NULLIF(TRIM(u_ph.name), ''), '-') AS assigned_to,
            COALESCE(NULLIF(TRIM(u_book.name), ''), '-') AS booked_by,
            COALESCE(cm.primary_mobile, '') AS caller_mobile,
            COALESCE(b.F_Apt_Am, 0) AS gross_amount,
            COALESCE(b.credit_amount, 0) AS credit_amount,
            COALESCE(b.F_dis, 0) AS discount_amount,
            COALESCE(b.total_amount, 0) AS final_amount,
            NULL AS payment_snapshot_json,
            NULL AS selected_patient_ids_json,
            NULL AS reason_text
        FROM hhome_collection_booking b
        INNER JOIN hcaller_master cm ON cm.id = b.caller_id
        LEFT JOIN users u_ph ON u_ph.id = b.assigned_phlebotomist_id
        LEFT JOIN users u_book ON u_book.id = b.created_by
        WHERE b.preferred_visit_date = %s
        """,
        (target_date,),
    )
    return cur.fetchall() or []


def fetch_appointment_rows(cur, target_date: date):
    cur.execute(
        """
        SELECT
            'APPOINTMENT' AS source_type,
            b.id AS booking_id,
            ap.id AS appointment_id,
            ap.appointment_status AS status,
            ap.preferred_visit_date,
            ap.preferred_time_slot,
            COALESCE(NULLIF(TRIM(ap.start_time), ''), '') AS strt_time,
            COALESCE(NULLIF(TRIM(ap.complete_time), ''), '') AS cmplt_time,
            COALESCE(ap.selected_address_id, b.selected_address_id) AS selected_address_id,
            ap.assigned_phlebotomist_id,
            COALESCE(NULLIF(TRIM(u_ph.name), ''), '-') AS assigned_to,
            COALESCE(NULLIF(TRIM(u_book.name), ''), '-') AS booked_by,
            COALESCE(cm.primary_mobile, '') AS caller_mobile,
            0 AS gross_amount,
            0 AS credit_amount,
            0 AS discount_amount,
            0 AS final_amount,
            ap.payment_snapshot_json,
            ap.selected_patient_ids_json,
            COALESCE(ap.reason_text, '') AS reason_text
        FROM hhome_collection_booking_appointment ap
        INNER JOIN hhome_collection_booking b ON b.id = ap.booking_id
        INNER JOIN hcaller_master cm ON cm.id = b.caller_id
        LEFT JOIN users u_ph ON u_ph.id = ap.assigned_phlebotomist_id
        LEFT JOIN users u_book ON u_book.id = b.created_by
        WHERE ap.preferred_visit_date = %s
        """,
        (target_date,),
    )
    return cur.fetchall() or []


def appointment_amounts(row):
    summary = parse_json_obj(row.get("payment_snapshot_json")).get("summary") or {}
    gross = as_float(summary.get("sub_total"))
    credit = as_float(summary.get("credit_amount"))
    discount = as_float(summary.get("final_discount"))
    final = as_float(summary.get("total_amount"))
    return gross, credit, discount, final


def active_rows(rows):
    return [r for r in rows if int(r.get("status") or 0) != 4]


def load_patient_maps(cur, rows):
    booking_ids = sorted({int(r.get("booking_id") or 0) for r in rows if int(r.get("booking_id") or 0) > 0})
    if not booking_ids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(booking_ids))
    cur.execute(
        f"""
        SELECT
            bp.booking_id,
            bp.patient_id,
            bp.cce_level_TBS,
            bp.APK_TBS,
            COALESCE(bp.prescription_files, '') AS prescription_files,
            COALESCE(NULLIF(TRIM(p.full_name), ''), '') AS patient_name,
            COALESCE(NULLIF(TRIM(p.contact_mobile), ''), '') AS patient_mobile
        FROM hhome_collection_booking_patient bp
        LEFT JOIN hpatient_master p ON p.id = bp.patient_id
        WHERE bp.booking_id IN ({placeholders})
        """,
        tuple(booking_ids),
    )
    by_booking = defaultdict(list)
    by_patient = {}
    for row in cur.fetchall() or []:
        by_booking[int(row.get("booking_id") or 0)].append(row)
        by_patient[int(row.get("patient_id") or 0)] = row
    return by_booking, by_patient


def latest_cancel_reasons(cur, booking_ids):
    if not booking_ids:
        return {}
    placeholders = ",".join(["%s"] * len(booking_ids))
    cur.execute(
        f"""
        SELECT a.booking_id, a.reason_text
        FROM hbooking_action_audit a
        INNER JOIN (
            SELECT booking_id, MAX(id) AS max_id
            FROM hbooking_action_audit
            WHERE action_type='CANCEL' AND booking_id IN ({placeholders})
            GROUP BY booking_id
        ) x ON x.max_id = a.id
        """,
        tuple(booking_ids),
    )
    return {int(r.get("booking_id") or 0): norm(r.get("reason_text")) for r in cur.fetchall() or []}


def has_google_location(cur, address_ids):
    ids = sorted({int(x or 0) for x in address_ids if int(x or 0) > 0})
    if not ids:
        return set()
    placeholders = ",".join(["%s"] * len(ids))
    cur.execute(
        f"""
        SELECT id
        FROM haddress_master
        WHERE id IN ({placeholders})
          AND google_location IS NOT NULL
          AND TRIM(google_location) <> ''
          AND TRIM(google_location) <> '-'
        """,
        tuple(ids),
    )
    return {int(r.get("id") or 0) for r in cur.fetchall() or []}


def build_planned_message(cur, target_date: date):
    rows = fetch_booking_rows(cur, target_date) + fetch_appointment_rows(cur, target_date)
    active = active_rows(rows)
    cancelled_count = len(rows) - len(active)
    patients_by_booking, patients_by_id = load_patient_maps(cur, rows)
    google_address_ids = has_google_location(cur, [r.get("selected_address_id") for r in active])

    prescription_count = 0
    tbs_counts = Counter()
    amount = {"gross": 0.0, "credit": 0.0, "discount": 0.0, "final": 0.0}
    assigned_counts = Counter()

    for row in active:
        source = row.get("source_type")
        booking_id = int(row.get("booking_id") or 0)
        if norm(row.get("assigned_to")) and norm(row.get("assigned_to")) != "-":
            assigned_counts[norm(row.get("assigned_to"))] += 1

        if source == "APPOINTMENT":
            gross, credit, discount, final = appointment_amounts(row)
            patient_ids = selected_patient_ids(row.get("selected_patient_ids_json"))
            patient_rows = [patients_by_id.get(pid) for pid in patient_ids if patients_by_id.get(pid)]
        else:
            gross = as_float(row.get("gross_amount"))
            credit = as_float(row.get("credit_amount"))
            discount = as_float(row.get("discount_amount"))
            final = as_float(row.get("final_amount"))
            patient_rows = patients_by_booking.get(booking_id, [])

        amount["gross"] += gross
        amount["credit"] += credit
        amount["discount"] += discount
        amount["final"] += final

        for patient in patient_rows:
            if norm(patient.get("prescription_files")):
                prescription_count += 1
            label = tbs_label(patient.get("cce_level_TBS"))
            if label:
                tbs_counts[label] += 1

    lines = [
        "*Home Collection Today Summary*",
        f"Date: {fmt_date(target_date)}",
        "",
        f"Total Collection: {len(rows)}",
        f"Total Prescription: {prescription_count}",
        f"Total Google Location: {len(google_address_ids)}",
    ]
    if cancelled_count > 0:
        lines.append(f"Canceled Collection: {cancelled_count}")

    lines.extend(["", "*Booking Assigned*"])
    if assigned_counts:
        for name, count in sorted(assigned_counts.items(), key=lambda x: x[0].upper()):
            lines.append(f"{name}: {count}")
    else:
        lines.append("-")

    final_amount = amount["final"]
    credit_amount = amount["credit"]
    paying_amount = final_amount - credit_amount
    if paying_amount < 0:
        paying_amount = 0.0
    total_tbs_patients = sum(tbs_counts.values())
    lines.extend(
        [
            "",
            "*Amount Summary*",
            f"Gross Amount: {money(amount['gross'])}",
            f"Credit Amount: {money(credit_amount)}",
            f"Paying Amount: {money(paying_amount)}",
            f"Discount: {money(amount['discount'])}",
            f"*Final Amount: {money(final_amount)}*",
            "",
            "*CCE Level TBS*",
            f"Total Patients: {total_tbs_patients}",
        ]
    )
    any_tbs = False
    for label in TBS_LABELS.values():
        if tbs_counts[label] > 0:
            lines.append(f"{label}: {tbs_counts[label]}")
            any_tbs = True
    if not any_tbs:
        lines.append("-")
    return "\n".join(lines).strip()


def build_actual_message(cur, target_date: date):
    rows = fetch_booking_rows(cur, target_date) + fetch_appointment_rows(cur, target_date)
    patients_by_booking, patients_by_id = load_patient_maps(cur, rows)
    cancel_reasons = latest_cancel_reasons(
        cur,
        [int(r.get("booking_id") or 0) for r in rows if r.get("source_type") == "BOOKING" and int(r.get("status") or 0) == 4],
    )

    complete = cancelled = running = pending = late_start = late_complete = 0
    amount = {"gross": 0.0, "credit": 0.0, "discount": 0.0, "final": 0.0}
    tbs_counts = Counter()
    phlebo_tbs_counts = Counter()
    cancel_blocks = []

    for row in rows:
        status = int(row.get("status") or 0)
        active_row = status != 4
        if status == 3:
            complete += 1
        elif status == 4:
            cancelled += 1
        elif status == 2:
            running += 1
        elif status in (0, 1):
            pending += 1

        if is_late_start(row):
            late_start += 1
        if is_late_complete(row):
            late_complete += 1

        booking_id = int(row.get("booking_id") or 0)
        if row.get("source_type") == "APPOINTMENT":
            patient_ids = selected_patient_ids(row.get("selected_patient_ids_json"))
            patient_rows = [patients_by_id.get(pid) for pid in patient_ids if patients_by_id.get(pid)]
            if active_row:
                gross, credit, discount, final = appointment_amounts(row)
                amount["gross"] += gross
                amount["credit"] += credit
                amount["discount"] += discount
                amount["final"] += final
        else:
            patient_rows = patients_by_booking.get(booking_id, [])
            if active_row:
                amount["gross"] += as_float(row.get("gross_amount"))
                amount["credit"] += as_float(row.get("credit_amount"))
                amount["discount"] += as_float(row.get("discount_amount"))
                amount["final"] += as_float(row.get("final_amount"))

        if active_row:
            for patient in patient_rows:
                label = tbs_label(patient.get("cce_level_TBS"))
                if label:
                    tbs_counts[label] += 1
                phlebo_tbs_counts[phlebo_tbs_label(patient.get("APK_TBS"))] += 1

        if status == 4:
            if row.get("source_type") == "APPOINTMENT":
                reason = norm(row.get("reason_text"))
            else:
                reason = cancel_reasons.get(booking_id, "")
            names = ", ".join([norm(p.get("patient_name")) for p in patient_rows if norm(p.get("patient_name"))]) or "-"
            mobiles = ", ".join([norm(p.get("patient_mobile")) for p in patient_rows if norm(p.get("patient_mobile"))]) or norm(row.get("caller_mobile")) or "-"
            cancel_blocks.append(
                "\n".join(
                    [
                        f"Name: {names}",
                        f"Mobile: {mobiles}",
                        f"Reason Of Cancel: {reason or '-'}",
                        f"Booked By: {norm(row.get('booked_by')) or '-'}",
                        f"Assigned To: {norm(row.get('assigned_to')) or '-'}",
                    ]
                )
            )

    lines = [
        "*Home Collection Previous Day Summary*",
        f"Date: {fmt_date(target_date)}",
        "",
        f"Total Collection: {len(rows)}",
        f"Complete Collection: {complete}",
        f"Cancelled Collection: {cancelled}",
        f"Running Collection: {running}",
        f"Pending Collection: {pending}",
        f"Late Start Collection: {late_start}",
        f"Late Complete Collection: {late_complete}",
    ]
    final_amount = amount["final"]
    credit_amount = amount["credit"]
    paying_amount = final_amount - credit_amount
    if paying_amount < 0:
        paying_amount = 0.0
    total_tbs_patients = sum(tbs_counts.values())
    lines.extend(
        [
            "",
            "*Amount Summary*",
            f"Gross Amount: {money(amount['gross'])}",
            f"Credit Amount: {money(credit_amount)}",
            f"Paying Amount: {money(paying_amount)}",
            f"Discount: {money(amount['discount'])}",
            f"*Final Amount: {money(final_amount)}*",
            "",
            "*CCE Level TBS*",
            f"Total Patients: {total_tbs_patients}",
        ]
    )
    any_tbs = False
    for label in TBS_LABELS.values():
        if tbs_counts[label] > 0:
            lines.append(f"{label}: {tbs_counts[label]}")
            any_tbs = True
    if not any_tbs:
        lines.append("-")

    total_phlebo_tbs_patients = sum(phlebo_tbs_counts.values())
    lines.extend(["", "*Phlebo TBS*", f"Total Patients: {total_phlebo_tbs_patients}"])
    any_phlebo_tbs = False
    for label in list(PHLEBO_TBS_LABELS.values()) + ["None"]:
        if phlebo_tbs_counts[label] > 0:
            lines.append(f"{label}: {phlebo_tbs_counts[label]}")
            any_phlebo_tbs = True
    if not any_phlebo_tbs:
        lines.append("-")

    if cancel_blocks:
        lines.extend(["", "*Cancel Response*"])
        lines.append("\n\n".join(cancel_blocks))
    return "\n".join(lines).strip()


def parse_args():
    parser = argparse.ArgumentParser(description="Send Home Collection daily WhatsApp summaries.")
    parser.add_argument("--phone", default=TARGET_PHONE, help="WhatsApp target number.")
    parser.add_argument("--today", default="", help="Override today date as YYYY-MM-DD for testing.")
    return parser.parse_args()


def main():
    args = parse_args()
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    yesterday = today - timedelta(days=1)

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            actual_msg = build_actual_message(cur, yesterday)
            planned_msg = build_planned_message(cur, today)
    finally:
        conn.close()

    for msg in (actual_msg, planned_msg):
        send_whatsapp_to_number(args.phone, msg)


if __name__ == "__main__":
    main()
