import json
from datetime import datetime, time, timedelta

import pymysql
from flask import Blueprint, jsonify, render_template, request

from app.db.connection import get_db_connection

phlebo_summary_bp = Blueprint("phlebo_summary", __name__)


def _norm(v) -> str:
    if v is None:
        return ""
    return str(v).replace("\x00", "").strip()


def _parse_time_to_minutes(value):
    text = _norm(value)
    if not text:
        return None
    text = text.replace("–", "-").replace("—", "-")
    parts = text.split()
    if len(parts) >= 2 and ":" in parts[0]:
        try:
            hh, mm = parts[0].split(":")
            hours = int(hh)
            minutes = int(mm)
            modifier = parts[1].upper()
            if modifier == "PM" and hours != 12:
                hours += 12
            if modifier == "AM" and hours == 12:
                hours = 0
            return hours * 60 + minutes
        except Exception:
            return None
    if ":" in text and "AM" not in text.upper() and "PM" not in text.upper():
        try:
            hh, mm = text.split(":")[:2]
            return int(hh) * 60 + int(mm)
        except Exception:
            return None
    return None


def _generate_slots():
    slots = []
    start = datetime.strptime("06:00 AM", "%I:%M %p")
    for _ in range(36):
        end = start + timedelta(minutes=30)
        slots.append(f"{start.strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}")
        start = end
    return slots


def _status_text(status):
    try:
        status_num = int(status or 0)
    except Exception:
        status_num = 0
    return {
        0: "Pending",
        1: "Assigned",
        2: "Started",
        3: "Completed",
        4: "Cancelled",
    }.get(status_num, "Unknown")


def _check_late(booking, slot_start_mins, slot_end_mins):
    try:
        status = int(booking.get("booking_status") or 0)
    except Exception:
        status = 0

    now = datetime.now()
    now_mins = now.hour * 60 + now.minute
    start_mins = _parse_time_to_minutes(booking.get("strt_time"))
    complete_mins = _parse_time_to_minutes(booking.get("cmplt_time"))

    indicators = []
    if status in (0, 1) and not booking.get("strt_time"):
        if now_mins > slot_start_mins + 5:
            indicators.append({"type": "NS", "text": "NS"})
    if booking.get("strt_time") and start_mins is not None and start_mins > slot_start_mins + 5:
        indicators.append({"type": "LS", "text": "LS"})
    if status == 3 and booking.get("cmplt_time") and complete_mins is not None and complete_mins > slot_end_mins + 5:
        indicators.append({"type": "LC", "text": "LC"})

    return {
        "indicators": indicators,
        "should_red": bool(indicators) and status in (0, 1, 2),
    }


def _slot_key(slot_text: str):
    start = (slot_text or "").split("-")[0].strip()
    mins = _parse_time_to_minutes(start)
    return mins if mins is not None else 9999


def _parse_patient_ids_json(raw_value):
    if not raw_value:
        return []
    try:
        data = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    ids = []
    for item in data:
        try:
            pid = int(item or 0)
        except Exception:
            pid = 0
        if pid > 0 and pid not in ids:
            ids.append(pid)
    return ids


def _merge_tag_values(*values) -> str:
    tags = []
    seen = set()
    for value in values:
        for part in str(value or "").replace("|", ",").split(","):
            tag = _norm(part)
            key = tag.lower()
            if not tag or key in seen:
                continue
            seen.add(key)
            tags.append(tag)
    return ", ".join(tags)


@phlebo_summary_bp.get("/hhome-collection/phlebo-summary")
def page():
    return render_template("hhome_collection/hphlebo_summary.html")


@phlebo_summary_bp.get("/hhome-collection/phlebo-summary-data")
def summary_data():
    date_str = (request.args.get("date") or "").strip()
    view = (request.args.get("view") or "phlebo").strip().lower()
    if view not in ("phlebo", "route"):
        view = "phlebo"
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT
                    hcb.id AS booking_id,
                    hcb.preferred_visit_date,
                    hcb.preferred_time_slot,
                    hcb.booking_status,
                    COALESCE(NULLIF(TRIM(hcb.strt_time), ''), NULLIF(TRIM(hcb.start_time), ''), '') AS strt_time,
                    COALESCE(NULLIF(TRIM(hcb.cmplt_time), ''), NULLIF(TRIM(hcb.complete_time), ''), '') AS cmplt_time,
                    COALESCE(NULLIF(TRIM(hcb.start_location), ''), '') AS start_location,
                    COALESCE(NULLIF(TRIM(hcb.complete_location), ''), '') AS complete_location,
                    hcb.assigned_phlebotomist_id,
                    COALESCE(NULLIF(TRIM(u.name), ''), 'UNASSIGNED') AS phlebo_name,
                    COALESCE(NULLIF(TRIM(am.route_no), ''), 'UNASSIGNED') AS route_name,
                    COALESCE(NULLIF(TRIM(am.house_flat_no), ''), '') AS house_flat_no,
                    COALESCE(NULLIF(TRIM(am.floor), ''), '') AS floor,
                    COALESCE(NULLIF(TRIM(am.block_tower_no), ''), '') AS block_tower_no,
                    COALESCE(NULLIF(TRIM(am.street_line), ''), '') AS street_line,
                    COALESCE(NULLIF(TRIM(am.landmark), ''), '') AS landmark,
                    COALESCE(NULLIF(TRIM(am.colony_name), ''), '') AS colony_name,
                    COALESCE(NULLIF(TRIM(am.city), ''), '') AS city,
                    COALESCE(NULLIF(TRIM(am.pincode), ''), '') AS pincode,
                    COALESCE(NULLIF(TRIM(cm.full_name), ''), '') AS caller_name,
                    COALESCE(NULLIF(TRIM(cm.primary_mobile), ''), '') AS caller_mobile,
                    COALESCE(NULLIF(TRIM(hcb.booking_tags), ''), '') AS booking_tags,
                    COUNT(DISTINCT hbp.patient_id) AS patient_count,
                    GROUP_CONCAT(DISTINCT p.full_name ORDER BY p.full_name SEPARATOR ' | ') AS patient_names,
                    GROUP_CONCAT(DISTINCT p.contact_mobile ORDER BY p.contact_mobile SEPARATOR ' | ') AS patient_mobiles,
                    GROUP_CONCAT(
                        DISTINCT DATE_FORMAT(p.date_of_birth, '%%d-%%m-%%Y')
                        ORDER BY p.date_of_birth
                        SEPARATOR ' | '
                    ) AS patient_dobs,
                    GROUP_CONCAT(DISTINCT p.tag ORDER BY p.tag SEPARATOR ', ') AS patient_tags
                FROM hhome_collection_booking hcb
                INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                INNER JOIN haddress_master am ON am.id = hcb.selected_address_id
                LEFT JOIN users u ON u.id = hcb.assigned_phlebotomist_id
                LEFT JOIN hhome_collection_booking_patient hbp ON hbp.booking_id = hcb.id
                LEFT JOIN hpatient_master p ON p.id = hbp.patient_id
                WHERE hcb.preferred_visit_date = %s
                GROUP BY
                    hcb.id,
                    hcb.preferred_visit_date,
                    hcb.preferred_time_slot,
                    hcb.booking_status,
                    hcb.strt_time,
                    hcb.cmplt_time,
                    hcb.start_location,
                    hcb.complete_location,
                    hcb.assigned_phlebotomist_id,
                    phlebo_name,
                    route_name,
                    am.house_flat_no,
                    am.floor,
                    am.block_tower_no,
                    am.street_line,
                    am.landmark,
                    am.colony_name,
                    am.city,
                    am.pincode,
                    cm.full_name,
                    cm.primary_mobile,
                    hcb.booking_tags
                ORDER BY hcb.id DESC
                """,
                (target_date,),
            )
            booking_rows = cur.fetchall() or []

            cur.execute(
                """
                SELECT
                    ap.id AS appointment_id,
                    ap.booking_id,
                    ap.preferred_visit_date,
                    ap.preferred_time_slot,
                    ap.appointment_status AS booking_status,
                    COALESCE(NULLIF(TRIM(ap.start_time), ''), '') AS strt_time,
                    COALESCE(NULLIF(TRIM(ap.complete_time), ''), '') AS cmplt_time,
                    COALESCE(NULLIF(TRIM(ap.start_location), ''), '') AS start_location,
                    COALESCE(NULLIF(TRIM(ap.complete_location), ''), '') AS complete_location,
                    ap.assigned_phlebotomist_id,
                    COALESCE(NULLIF(TRIM(u.name), ''), 'UNASSIGNED') AS phlebo_name,
                    COALESCE(NULLIF(TRIM(am.route_no), ''), 'UNASSIGNED') AS route_name,
                    COALESCE(NULLIF(TRIM(am.house_flat_no), ''), '') AS house_flat_no,
                    COALESCE(NULLIF(TRIM(am.floor), ''), '') AS floor,
                    COALESCE(NULLIF(TRIM(am.block_tower_no), ''), '') AS block_tower_no,
                    COALESCE(NULLIF(TRIM(am.street_line), ''), '') AS street_line,
                    COALESCE(NULLIF(TRIM(am.landmark), ''), '') AS landmark,
                    COALESCE(NULLIF(TRIM(am.colony_name), ''), '') AS colony_name,
                    COALESCE(NULLIF(TRIM(am.city), ''), '') AS city,
                    COALESCE(NULLIF(TRIM(am.pincode), ''), '') AS pincode,
                    COALESCE(NULLIF(TRIM(cm.full_name), ''), '') AS caller_name,
                    COALESCE(NULLIF(TRIM(cm.primary_mobile), ''), '') AS caller_mobile,
                    COALESCE(NULLIF(TRIM(hcb.booking_tags), ''), '') AS booking_tags,
                    ap.selected_patient_ids_json,
                    ap.address_snapshot_json
                FROM hhome_collection_booking_appointment ap
                INNER JOIN hhome_collection_booking hcb ON hcb.id = ap.booking_id
                INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                LEFT JOIN haddress_master am ON am.id = COALESCE(ap.selected_address_id, hcb.selected_address_id)
                LEFT JOIN users u ON u.id = ap.assigned_phlebotomist_id
                WHERE ap.preferred_visit_date = %s
                ORDER BY ap.id DESC
                """,
                (target_date,),
            )
            appointment_rows = cur.fetchall() or []

            appointment_patient_ids = set()
            for ap in appointment_rows:
                ids = _parse_patient_ids_json(ap.get("selected_patient_ids_json"))
                if not ids:
                    # Legacy fallback: older records could have this inside snapshot json
                    try:
                        snapshot_obj = json.loads(ap.get("address_snapshot_json") or "{}")
                    except Exception:
                        snapshot_obj = {}
                    ids = _parse_patient_ids_json(
                        snapshot_obj.get("_selected_patient_ids") if isinstance(snapshot_obj, dict) else []
                    )
                ap["_selected_patient_ids"] = ids
                for pid in ids:
                    appointment_patient_ids.add(pid)

            patient_details = {}
            if appointment_patient_ids:
                ph = ",".join(["%s"] * len(appointment_patient_ids))
                cur.execute(
                    f"""
                    SELECT id, COALESCE(NULLIF(TRIM(full_name), ''), '') AS full_name,
                           COALESCE(NULLIF(TRIM(contact_mobile), ''), '') AS contact_mobile,
                           DATE_FORMAT(date_of_birth, '%%d-%%m-%%Y') AS date_of_birth,
                           COALESCE(NULLIF(TRIM(tag), ''), '') AS tag
                    FROM hpatient_master
                    WHERE id IN ({ph})
                    """,
                    tuple(sorted(appointment_patient_ids)),
                )
                for r in cur.fetchall() or []:
                    patient_details[int(r["id"])] = {
                        "name": _norm(r.get("full_name")),
                        "mobile": _norm(r.get("contact_mobile")),
                        "dob": _norm(r.get("date_of_birth")),
                        "tag": _norm(r.get("tag")),
                    }

            rows = list(booking_rows)
            for ap in appointment_rows:
                ids = ap.get("_selected_patient_ids") or []
                names = []
                mobiles = []
                dobs = []
                tags = []
                for pid in ids:
                    d = patient_details.get(int(pid) or 0) or {}
                    n = _norm(d.get("name"))
                    m = _norm(d.get("mobile"))
                    dob = _norm(d.get("dob"))
                    tag = _norm(d.get("tag"))
                    if n and n not in names:
                        names.append(n)
                    if m and m not in mobiles:
                        mobiles.append(m)
                    if dob and dob not in dobs:
                        dobs.append(dob)
                    if tag and tag not in tags:
                        tags.append(tag)
                rows.append(
                    {
                        "booking_id": int(ap.get("appointment_id") or 0),
                        "preferred_visit_date": ap.get("preferred_visit_date"),
                        "preferred_time_slot": ap.get("preferred_time_slot"),
                        "booking_status": int(ap.get("booking_status") or 0),
                        "strt_time": ap.get("strt_time"),
                        "cmplt_time": ap.get("cmplt_time"),
                        "start_location": ap.get("start_location"),
                        "complete_location": ap.get("complete_location"),
                        "assigned_phlebotomist_id": ap.get("assigned_phlebotomist_id"),
                        "phlebo_name": ap.get("phlebo_name"),
                        "route_name": ap.get("route_name"),
                        "house_flat_no": ap.get("house_flat_no"),
                        "floor": ap.get("floor"),
                        "block_tower_no": ap.get("block_tower_no"),
                        "street_line": ap.get("street_line"),
                        "landmark": ap.get("landmark"),
                        "colony_name": ap.get("colony_name"),
                        "city": ap.get("city"),
                        "pincode": ap.get("pincode"),
                        "caller_name": ap.get("caller_name"),
                        "caller_mobile": ap.get("caller_mobile"),
                        "patient_count": len(ids),
                        "patient_names": " | ".join(names),
                        "patient_mobiles": " | ".join(mobiles),
                        "patient_dobs": " | ".join(dobs),
                        "patient_tags": ", ".join(tags),
                    }
                )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass

    bookings = []
    phlebos = []
    routes = []
    phlebo_set = set()
    route_set = set()
    skipped = 0
    assigned_count = 0

    for row in rows:
        slot_text = _norm(row.get("preferred_time_slot"))
        if not slot_text:
            skipped += 1
            continue

        phlebo_name = _norm(row.get("phlebo_name")) or "UNASSIGNED"
        route_name = _norm(row.get("route_name")) or "UNASSIGNED"
        if phlebo_name:
            phlebo_set.add(phlebo_name)
        if route_name:
            route_set.add(route_name)
        try:
            if int(row.get("booking_status") or 0) == 1 or int(row.get("assigned_phlebotomist_id") or 0) > 0:
                assigned_count += 1
        except Exception:
            pass

        bookings.append(
            {
                "booking_id": row.get("booking_id"),
                "phlebo": phlebo_name,
                "route": route_name,
                "slot": slot_text,
                "status": int(row.get("booking_status") or 0),
                "caller_name": _norm(row.get("caller_name")),
                "caller_mobile": _norm(row.get("caller_mobile")),
                "colony_name": _norm(row.get("colony_name")),
                "city": _norm(row.get("city")),
                "pincode": _norm(row.get("pincode")),
                "house_flat_no": _norm(row.get("house_flat_no")),
                "floor": _norm(row.get("floor")),
                "block_tower_no": _norm(row.get("block_tower_no")),
                "street_line": _norm(row.get("street_line")),
                "landmark": _norm(row.get("landmark")),
                "patient_count": int(row.get("patient_count") or 0),
                "patient_names": _norm(row.get("patient_names")),
                "patient_mobiles": _norm(row.get("patient_mobiles")),
                "patient_dobs": _norm(row.get("patient_dobs")),
                "tags": _merge_tag_values(row.get("booking_tags"), row.get("patient_tags")),
                "strt_time": _norm(row.get("strt_time")),
                "cmplt_time": _norm(row.get("cmplt_time")),
                "start_location": _norm(row.get("start_location")),
                "complete_location": _norm(row.get("complete_location")),
                "preferred_visit_date": str(row.get("preferred_visit_date") or ""),
            }
        )

    for label in sorted(phlebo_set, key=str.upper):
        phlebos.append(label)
    for label in sorted([x for x in route_set if x != "UNASSIGNED"], key=str.upper):
        routes.append(label)
    if "UNASSIGNED" in route_set:
        routes.append("UNASSIGNED")

    return jsonify(
        {
            "ok": True,
            "date": date_str,
            "phlebos": phlebos if view == "phlebo" else [],
            "routes": routes if view == "route" else [],
            "bookings": bookings,
            "skipped": skipped,
            "total_bookings": len(rows),
            "assigned_bookings": assigned_count,
            "skipped_assignments": len(rows) - assigned_count,
        }
    )
