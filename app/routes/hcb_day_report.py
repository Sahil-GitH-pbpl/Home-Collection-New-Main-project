import json
import re
from datetime import date, datetime

import pymysql
from flask import Blueprint, jsonify, render_template, request

from app.db.connection import get_db_connection

hcb_day_report_bp = Blueprint("hcb_day_report", __name__)


def _norm(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\x00", "").strip()


def _split_csv(text: str):
    raw = _norm(text)
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _slot_start_minutes(slot_text: str):
    text = _norm(slot_text).upper().replace(".", "")
    if not text:
        return 9999

    m = re.search(r"(\d{1,2})\s*:\s*(\d{2})", text)
    if not m:
        return 9999

    try:
        hh = int(m.group(1))
        mm = int(m.group(2))
    except Exception:
        return 9999

    am_pm = None
    m2 = re.search(r"\b(AM|PM)\b", text)
    if m2:
        am_pm = m2.group(1)

    if am_pm == "PM" and hh != 12:
        hh += 12
    if am_pm == "AM" and hh == 12:
        hh = 0

    return hh * 60 + mm


def _safe_json_obj(raw):
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_json_list(raw):
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _format_visit_address_from_snapshot(snapshot_obj: dict):
    house = _norm(snapshot_obj.get("house_flat_no"))
    floor = _norm(snapshot_obj.get("floor"))
    block = _norm(snapshot_obj.get("block_tower_no"))
    street = _norm(snapshot_obj.get("street_line"))
    colony = _norm(snapshot_obj.get("colony_name"))
    city = _norm(snapshot_obj.get("city"))
    pincode = _norm(snapshot_obj.get("pincode"))
    landmark = _norm(snapshot_obj.get("landmark"))
    access_notes = _norm(snapshot_obj.get("access_notes"))

    parts = []
    if house:
        parts.append(f"House No {house}")
    if floor:
        parts.append(f"Floor {floor}")
    if block:
        parts.append(f"Block/Tower No {block}")
    if street:
        parts.append(f"Street/Sector {street}")
    if colony:
        parts.append(f"Colony {colony}")
    if city:
        parts.append(f"City {city}")
    if pincode:
        parts.append(f"- {pincode}")

    return {
        "visit_address": ", ".join(parts) if parts else "-",
        "landmark": landmark or "-",
        "access_notes": access_notes or "-",
        "colony_name": colony or "-",
    }


def _extract_panels_from_appointment_snapshot(snapshot_obj: dict):
    names = []
    seen = set()

    def push(name):
        nm = _norm(name)
        key = nm.lower()
        if not nm or key in seen:
            return
        seen.add(key)
        names.append(nm)

    tbm = snapshot_obj.get("tests_billing_map")
    if isinstance(tbm, dict):
        for _, val in tbm.items():
            if not isinstance(val, dict):
                continue
            for panel in val.get("panels") or []:
                if isinstance(panel, dict):
                    p = panel.get("panel") or {}
                    if isinstance(p, dict):
                        push(p.get("pname"))
            p = val.get("panel") or {}
            if isinstance(p, dict):
                push(p.get("pname"))

    for panel in snapshot_obj.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        p = panel.get("panel") or {}
        if isinstance(p, dict):
            push(p.get("pname"))
        push(panel.get("panel_company"))

    return names


@hcb_day_report_bp.get("/hhome-collection/hcb-day-report")
def page():
    return render_template("hhome_collection/hcb_day_report.html", default_date=date.today().isoformat())


@hcb_day_report_bp.get("/hhome-collection/hcb-day-report/data")
def report_data():
    date_str = _norm(request.args.get("date"))
    phlebo_q = _norm(request.args.get("phlebo"))

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"ok": False, "message": "Invalid date"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT
                    b.id AS booking_id,
                    0 AS appointment_id,
                    'Booking' AS row_type,
                    b.preferred_visit_date,
                    b.preferred_time_slot,
                    b.address_snapshot_json AS address_snapshot_json,
                    NULL AS appointment_tests_snapshot_json,
                    NULL AS selected_patient_ids_json,
                    COALESCE(u_book.name, '-') AS booked_by,
                    COALESCE(u_ph.name, '-') AS phlebo_name,
                    b.booking_tags,
                    am.house_flat_no, am.floor, am.block_tower_no, am.street_line, am.landmark, am.access_notes, am.colony_name, am.city, am.pincode
                FROM hhome_collection_booking b
                LEFT JOIN users u_book ON u_book.id = b.created_by
                LEFT JOIN users u_ph ON u_ph.id = b.assigned_phlebotomist_id
                LEFT JOIN haddress_master am ON am.id = b.selected_address_id
                WHERE b.preferred_visit_date = %s

                UNION ALL

                SELECT
                    ap.booking_id AS booking_id,
                    ap.id AS appointment_id,
                    'Appointment' AS row_type,
                    ap.preferred_visit_date,
                    ap.preferred_time_slot,
                    ap.address_snapshot_json AS address_snapshot_json,
                    ap.appointment_tests_snapshot_json,
                    ap.selected_patient_ids_json,
                    COALESCE(u_book2.name, '-') AS booked_by,
                    COALESCE(u_ph2.name, '-') AS phlebo_name,
                    b2.booking_tags,
                    am2.house_flat_no, am2.floor, am2.block_tower_no, am2.street_line, am2.landmark, am2.access_notes, am2.colony_name, am2.city, am2.pincode
                FROM hhome_collection_booking_appointment ap
                INNER JOIN hhome_collection_booking b2 ON b2.id = ap.booking_id
                LEFT JOIN users u_book2 ON u_book2.id = b2.created_by
                LEFT JOIN users u_ph2 ON u_ph2.id = ap.assigned_phlebotomist_id
                LEFT JOIN haddress_master am2 ON am2.id = COALESCE(ap.selected_address_id, b2.selected_address_id)
                WHERE ap.preferred_visit_date = %s
                """,
                (target_date, target_date),
            )
            base_rows = cur.fetchall() or []

            booking_ids = sorted({int(r["booking_id"]) for r in base_rows if int(r.get("booking_id") or 0) > 0})

            booking_patient_rows = []
            if booking_ids:
                ph = ",".join(["%s"] * len(booking_ids))
                cur.execute(
                    f"""
                    SELECT
                        bp.booking_id,
                        bp.patient_id,
                        COALESCE(TRIM(p.full_name), '') AS full_name,
                        COALESCE(TRIM(p.contact_mobile), '') AS contact_mobile,
                        COALESCE(TRIM(p.tag), '') AS patient_tag,
                        COALESCE(bp.selected_panel_companies, '') AS selected_panel_companies
                    FROM hhome_collection_booking_patient bp
                    LEFT JOIN hpatient_master p ON p.id = bp.patient_id
                    WHERE bp.booking_id IN ({ph})
                    ORDER BY bp.id
                    """,
                    booking_ids,
                )
                booking_patient_rows = cur.fetchall() or []

            active_users = []
            cur.execute(
                """
                SELECT TRIM(name) AS name
                FROM users
                WHERE status='Active' AND TRIM(COALESCE(name,'')) <> ''
                ORDER BY name
                """
            )
            active_users = cur.fetchall() or []

        booking_patients_map = {}
        booking_panel_map = {}
        for r in booking_patient_rows:
            bid = int(r.get("booking_id") or 0)
            if bid <= 0:
                continue
            booking_patients_map.setdefault(bid, []).append(
                {
                    "patient_id": int(r.get("patient_id") or 0),
                    "full_name": _norm(r.get("full_name")) or "-",
                    "contact_mobile": _norm(r.get("contact_mobile")) or "-",
                    "patient_tag": _norm(r.get("patient_tag")) or "-",
                }
            )
            for nm in _split_csv(r.get("selected_panel_companies")):
                booking_panel_map.setdefault(bid, [])
                if nm.lower() not in [x.lower() for x in booking_panel_map[bid]]:
                    booking_panel_map[bid].append(nm)

        all_patient_ids = sorted(
            {
                p["patient_id"]
                for plist in booking_patients_map.values()
                for p in plist
                if int(p.get("patient_id") or 0) > 0
            }
        )
        patient_master_map = {}
        if all_patient_ids:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                ph = ",".join(["%s"] * len(all_patient_ids))
                cur.execute(
                    f"""
                    SELECT id, COALESCE(TRIM(full_name), '') AS full_name,
                           COALESCE(TRIM(contact_mobile), '') AS contact_mobile,
                           COALESCE(TRIM(tag), '') AS patient_tag
                    FROM hpatient_master
                    WHERE id IN ({ph})
                    """,
                    all_patient_ids,
                )
                for pr in cur.fetchall() or []:
                    patient_master_map[int(pr["id"])] = {
                        "patient_id": int(pr["id"]),
                        "full_name": _norm(pr.get("full_name")) or "-",
                        "contact_mobile": _norm(pr.get("contact_mobile")) or "-",
                        "patient_tag": _norm(pr.get("patient_tag")) or "-",
                    }

        rows = []
        for raw in base_rows:
            booking_id = int(raw.get("booking_id") or 0)
            appointment_id = int(raw.get("appointment_id") or 0)
            row_type = _norm(raw.get("row_type")) or "Booking"
            phlebo_name = _norm(raw.get("phlebo_name")) or "-"
            if phlebo_q and phlebo_q.lower() not in phlebo_name.lower():
                continue

            snapshot = _safe_json_obj(raw.get("address_snapshot_json"))
            has_snapshot_addr = any(
                _norm(snapshot.get(k))
                for k in ("house_flat_no", "street_line", "colony_name", "city", "pincode", "landmark", "access_notes")
            )
            if has_snapshot_addr:
                addr = _format_visit_address_from_snapshot(snapshot)
            else:
                addr = _format_visit_address_from_snapshot(
                    {
                        "house_flat_no": raw.get("house_flat_no"),
                        "floor": raw.get("floor"),
                        "block_tower_no": raw.get("block_tower_no"),
                        "street_line": raw.get("street_line"),
                        "landmark": raw.get("landmark"),
                        "access_notes": raw.get("access_notes"),
                        "colony_name": raw.get("colony_name"),
                        "city": raw.get("city"),
                        "pincode": raw.get("pincode"),
                    }
                )

            if row_type.lower() == "appointment":
                selected_ids = []
                for item in _safe_json_list(raw.get("selected_patient_ids_json")):
                    try:
                        pid = int(item or 0)
                    except Exception:
                        pid = 0
                    if pid > 0:
                        selected_ids.append(pid)
                selected_ids = list(dict.fromkeys(selected_ids))
                if selected_ids:
                    patient_lines = [patient_master_map.get(pid) for pid in selected_ids]
                    patient_lines = [x for x in patient_lines if x]
                else:
                    patient_lines = booking_patients_map.get(booking_id, [])

                ap_snap = _safe_json_obj(raw.get("appointment_tests_snapshot_json"))
                panels = _extract_panels_from_appointment_snapshot(ap_snap) or booking_panel_map.get(booking_id, [])
            else:
                patient_lines = booking_patients_map.get(booking_id, [])
                panels = booking_panel_map.get(booking_id, [])

            patient_details = []
            t_tag = _norm(raw.get("booking_tags")) or "-"
            for p in patient_lines:
                patient_details.append(
                    {
                        "name": _norm(p.get("full_name")) or "-",
                        "mobile": _norm(p.get("contact_mobile")) or "-",
                        "p_tag": _norm(p.get("patient_tag")) or "-",
                        "t_tag": t_tag,
                    }
                )

            rows.append(
                {
                    "booking_id": booking_id,
                    "appointment_id": appointment_id,
                    "booking_type": row_type,
                    "booking_id_and_type": f"{booking_id} & {row_type}",
                    "patient_details": patient_details,
                    "visit_address": addr["visit_address"],
                    "landmark": addr["landmark"],
                    "access_notes": addr["access_notes"],
                    "selected_address_text": f"VISIT ADDRESS\n{addr['visit_address']}\n\nLANDMARK\n{addr['landmark']}" + (f"\n\nACCESS NOTES\n{addr['access_notes']}" if _norm(addr.get("access_notes")) and _norm(addr.get("access_notes")) != "-" else ""),
                    "panel_company": ", ".join(panels) if panels else "-",
                    "colony_name": addr["colony_name"],
                    "booked_by": _norm(raw.get("booked_by")) or "-",
                    "phlebo_name": phlebo_name,
                    "slot_time": _norm(raw.get("preferred_time_slot")) or "-",
                    "slot_key": _slot_start_minutes(raw.get("preferred_time_slot")),
                }
            )

        rows.sort(key=lambda r: (r.get("slot_key", 9999), _norm(r.get("phlebo_name")).lower(), int(r.get("booking_id") or 0)))

        return jsonify(
            {
                "ok": True,
                "date": target_date.isoformat(),
                "active_users": [(_norm(x.get("name")) or "-") for x in active_users],
                "rows": rows,
            }
        )
    finally:
        conn.close()
