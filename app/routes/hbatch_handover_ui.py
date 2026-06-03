import json
from collections import OrderedDict
from pathlib import Path
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from app.db.connection import get_db_connection

hbatch_handover_ui_bp = Blueprint("hbatch_handover_ui", __name__)
_WEB_ROOT = Path(__file__).resolve().parents[2]


def _split_csv(raw) -> list[str]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    return [x.strip() for x in text.split(",") if x and x.strip()]


def _build_tubes_map_from_batch_json(raw_tubes_json) -> dict[tuple[int, int | None, int, str], list[str]]:
    out: dict[tuple[int, int | None, int, str], list[str]] = {}
    data = raw_tubes_json
    if isinstance(raw_tubes_json, str):
        try:
            data = json.loads(raw_tubes_json)
        except Exception:
            data = []
    if not isinstance(data, list):
        return out

    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            bid = int(row.get("booking_id") or row.get("bookingId") or 0)
        except Exception:
            bid = 0
        appt_raw = row.get("appointment_id")
        if appt_raw is None:
            appt_raw = row.get("appointmentId")
        try:
            appt_id = int(appt_raw) if appt_raw is not None else None
        except Exception:
            appt_id = None
        source_type = str(row.get("source_type") or row.get("sourceType") or "").strip().upper()
        if source_type not in {"BOOKING", "APPOINTMENT"}:
            source_type = "APPOINTMENT" if appt_id else "BOOKING"
        try:
            pid = int(row.get("patient_id") or row.get("patientId") or 0)
        except Exception:
            pid = 0
        tube = str(
            row.get("tube_name")
            or row.get("tube")
            or row.get("specimen")
            or row.get("specimen_name")
            or ""
        ).strip()
        if bid <= 0 or pid <= 0 or not tube:
            continue
        key = (bid, appt_id, pid, source_type)
        out.setdefault(key, [])
        if tube not in out[key]:
            out[key].append(tube)
    return out


def _files_from_hc_slip(booking_code: str, patient_code: str) -> list[dict]:
    if not booking_code or not patient_code:
        return []
    folder = _WEB_ROOT / "app" / "static" / "uploads" / "hc_slip" / booking_code / patient_code
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*")):
        if p.is_file():
            out.append({"name": p.name, "url": f"/static/uploads/hc_slip/{booking_code}/{patient_code}/{p.name}"})
    return out


def _extract_pending_from_json(raw_json) -> tuple[list[str], list[str]]:
    obj = raw_json
    if isinstance(raw_json, str):
        try:
            obj = json.loads(raw_json)
        except Exception:
            obj = {}
    if not isinstance(obj, dict):
        return [], []

    tubes: list[str] = []
    tests: list[str] = []
    seen_tubes = set()
    seen_tests = set()

    for item in (obj.get("items") or []):
        if not isinstance(item, dict):
            continue
        tube = str(item.get("tube") or "").strip()
        if tube and tube.lower() not in seen_tubes:
            seen_tubes.add(tube.lower())
            tubes.append(tube)
        for p in (item.get("pending") or []):
            if not isinstance(p, dict):
                continue
            name = (
                str(p.get("description") or "").strip()
                or str(p.get("test_name") or "").strip()
                or str(p.get("booked_code") or "").strip()
            )
            if not name:
                continue
            k = name.lower()
            if k in seen_tests:
                continue
            seen_tests.add(k)
            tests.append(name)
    return tubes, tests


@hbatch_handover_ui_bp.get("/hhome-collection/batch-handover-ui")
def batch_handover_ui_page():
    return render_template("hhome_collection/hbatch_handover_ui.html")


def _as_json(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _parse_listish(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    txt = str(raw).strip()
    if not txt:
        return []
    if txt.startswith("[") and txt.endswith("]"):
        try:
            arr = json.loads(txt)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    return [x.strip() for x in txt.split(",") if x.strip()]


def _mode_label(code: str) -> str:
    c = str(code or "").strip().upper()
    if c == "P":
        return "Paying"
    if c == "C":
        return "Credit"
    if c == "F":
        return "Free"
    return c or "-"


def _money(v) -> str:
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0
    return f"Rs. {n:,.2f}"


def _to_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _appointment_payment_patient_row(payment_snapshot_json, patient_id: int) -> dict:
    payload = _as_json(payment_snapshot_json)
    if not isinstance(payload, dict):
        return {}
    for row in (payload.get("payments") or []):
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("patient_id") or 0)
        except Exception:
            pid = 0
        if pid == int(patient_id):
            return row
    return {}


@hbatch_handover_ui_bp.get("/hhome-collection/trf-patient-preview")
def trf_patient_preview_page():
    booking_id = int(request.args.get("booking_id") or 0)
    patient_id = int(request.args.get("patient_id") or 0)
    appointment_id = int(request.args.get("appointment_id") or 0)
    if booking_id <= 0 or patient_id <= 0:
        return "booking_id/patient_id required", 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  b.id,
                  NULLIF(TRIM(b.booking_code), '') AS booking_code,
                  b.preferred_visit_date,
                  b.preferred_time_slot,
                  COALESCE(NULLIF(TRIM(u.name), ''), '-') AS booked_by,
                  COALESCE(NULLIF(TRIM(up.name), ''), '-') AS phlebo_name,
                  bp.referred_by,
                  b.intrnl_rfrncd_by,
                  b.lead_id,
                  b.booking_tags,
                  b.address_snapshot_json,
                  bp.id AS booking_patient_id,
                  bp.cce_level_TBS,
                  bp.APK_TBS,
                  bp.selected_comp_cat_ids,
                  bp.selected_charge_modes,
                  bp.selected_panel_companies,
                  bp.patient_final_amount,
                  bp.additional_discount_amount,
                  bp.report_schedule,
                  bp.report_delivery,
                  bp.payment_mode,
                  bp.payment_amount,
                  bp.due_amount,
                  bp.extra_amount,
                  bp.no_of_pricks,
                  bp.sample_collection_is,
                  bp.additional_sample,
                  p.patient_code,
                  p.title,
                  p.full_name,
                  p.gender,
                  p.age_years,
                  p.date_of_birth,
                  p.contact_mobile,
                  p.alternate_mobile,
                  p.tag AS patient_tag
                FROM hhome_collection_booking b
                INNER JOIN hhome_collection_booking_patient bp ON bp.booking_id = b.id
                INNER JOIN hpatient_master p ON p.id = bp.patient_id
                LEFT JOIN users u ON u.id = b.created_by
                LEFT JOIN users up ON up.id = b.assigned_phlebotomist_id
                WHERE b.id=%s AND bp.patient_id=%s
                LIMIT 1
                """,
                (booking_id, patient_id),
            )
            row = cur.fetchone()
            if not row:
                return "Record not found", 404

            a_snap = _as_json(row.get("address_snapshot_json"))
            visit_date = row.get("preferred_visit_date")
            visit_date_txt = visit_date.strftime("%d-%m-%Y") if hasattr(visit_date, "strftime") else "-"
            visit_slot = str(row.get("preferred_time_slot") or "-").strip() or "-"
            patient_name = " ".join([x for x in [row.get("title"), row.get("full_name")] if str(x or "").strip()])
            dob = row.get("date_of_birth")
            dob_txt = dob.strftime("%d-%m-%Y") if hasattr(dob, "strftime") else "-"

            comp_ids = _parse_listish(row.get("selected_comp_cat_ids"))
            panel_names = _parse_listish(row.get("selected_panel_companies"))
            charge_modes = _parse_listish(row.get("selected_charge_modes"))
            panel_meta = {}
            for idx, comp in enumerate(comp_ids):
                panel_meta[str(comp)] = {
                    "panel_name": panel_names[idx] if idx < len(panel_names) else f"CompCat {comp}",
                    "charge_mode": _mode_label(charge_modes[idx] if idx < len(charge_modes) else ""),
                }

            grouped = OrderedDict()
            if appointment_id > 0:
                cur.execute(
                    """
                    SELECT appointment_tests_snapshot_json, payment_snapshot_json
                    FROM hhome_collection_booking_appointment
                    WHERE id=%s AND booking_id=%s
                    LIMIT 1
                    """,
                    (appointment_id, booking_id),
                )
                ap = cur.fetchone() or {}
                snap = _as_json(ap.get("appointment_tests_snapshot_json"))
                tbm = (snap.get("tests_billing_map") or {}).get(str(patient_id)) or (snap.get("tests_billing_map") or {}).get(patient_id) or {}
                panels = tbm.get("panels") or []
                for sec in panels:
                    if not isinstance(sec, dict):
                        continue
                    billing = sec.get("billing") or {}
                    panel = sec.get("panel") or {}
                    comp = str(billing.get("comp_cat_id") or "").strip()
                    p_name = str(panel.get("pname") or panel_meta.get(comp, {}).get("panel_name") or "-").strip()
                    c_mode = _mode_label(billing.get("selected_charge_mode") or panel_meta.get(comp, {}).get("charge_mode"))
                    key = f"{p_name} ({c_mode})"
                    grouped.setdefault(key, [])
                    for t in (sec.get("selected_tests") or []):
                        code = str(t.get("booked_code") or "").strip()
                        name = str(t.get("description") or code).strip()
                        if code:
                            grouped[key].append({"code": code, "name": name, "charge": float(t.get("charge") or 0), "mrp": float(t.get("mrp") or 0), "max_discount": float(t.get("max_discount") or 0)})
            else:
                cur.execute(
                    """
                    SELECT comp_cat_id, booked_code, COALESCE(NULLIF(TRIM(test_name), ''), TRIM(booked_code)) AS test_name, charge, mrp, max_discount
                    FROM hhome_collection_booking_patient_test
                    WHERE booking_id=%s AND patient_id=%s AND IFNULL(test_status, 0) IN (0, 1)
                    ORDER BY id
                    """,
                    (booking_id, patient_id),
                )
                for t in (cur.fetchall() or []):
                    comp = str(t.get("comp_cat_id") or "").strip()
                    meta = panel_meta.get(comp, {})
                    key = f"{meta.get('panel_name') or ('CompCat ' + comp if comp else 'Panel')} ({meta.get('charge_mode') or '-'})"
                    grouped.setdefault(key, [])
                    grouped[key].append({
                        "code": str(t.get("booked_code") or "").strip(),
                        "name": str(t.get("test_name") or "").strip(),
                        "charge": float(t.get("charge") or 0),
                        "mrp": float(t.get("mrp") or 0),
                        "max_discount": float(t.get("max_discount") or 0),
                    })

            mapped_tubes = []
            cur.execute(
                "SELECT tubes_json FROM hhome_collection_batch ORDER BY id DESC LIMIT 80"
            )
            for br in (cur.fetchall() or []):
                rows = br.get("tubes_json")
                if isinstance(rows, str):
                    try:
                        rows = json.loads(rows)
                    except Exception:
                        rows = []
                if not isinstance(rows, list):
                    continue
                local = []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    try:
                        bid = int(r.get("booking_id") or r.get("bookingId") or 0)
                        pid = int(r.get("patient_id") or r.get("patientId") or 0)
                    except Exception:
                        continue
                    if bid != booking_id or pid != patient_id:
                        continue
                    if appointment_id > 0:
                        try:
                            aid = int(r.get("appointment_id") or r.get("appointmentId") or 0)
                        except Exception:
                            aid = 0
                        if aid != appointment_id:
                            continue
                    tube = str(r.get("tube_name") or r.get("tube") or "").strip()
                    if tube and tube not in local:
                        local.append(tube)
                if local:
                    mapped_tubes = local
                    break

            pending_tube_names = []
            pending_test_names = []
            if appointment_id > 0:
                # Appointment TRF: read pending from this appointment snapshot (latest for that appointment).
                cur.execute(
                    "SELECT appointment_tests_snapshot_json FROM hhome_collection_booking_appointment WHERE id=%s AND booking_id=%s LIMIT 1",
                    (appointment_id, booking_id),
                )
                ap_snap_row = cur.fetchone() or {}
                ap_snap_obj = _as_json(ap_snap_row.get("appointment_tests_snapshot_json"))
                pending_map = ap_snap_obj.get("pending_tests_map") or {}
                pnode = pending_map.get(str(patient_id)) or pending_map.get(patient_id) or {}
                rows_pending = []
                if isinstance(pnode, dict):
                    rows_pending.extend(pnode.get("selected_tests") or [])
                    for sec in (pnode.get("panels") or []):
                        if isinstance(sec, dict):
                            rows_pending.extend(sec.get("selected_tests") or [])
                for r in rows_pending:
                    code_or_name = str(r.get("description") or r.get("test_name") or r.get("booked_code") or "").strip()
                    if code_or_name and code_or_name not in pending_test_names:
                        pending_test_names.append(code_or_name)
                # Tubes are not carried in appointment pending snapshot; keep from booking-origin table fallback if needed.
            else:
                # Booking TRF: stable booking-origin pending only.
                cur.execute(
                    """
                    SELECT pending_child_tests_json
                    FROM hcb_patient_test_pendingchildtest
                    WHERE booking_id=%s AND patient_id=%s
                      AND UPPER(TRIM(COALESCE(source_type, 'BOOKING')))='BOOKING'
                    """,
                    (booking_id, patient_id),
                )
                for pr in (cur.fetchall() or []):
                    tubes, tests = _extract_pending_from_json(pr.get("pending_child_tests_json"))
                    for t in tubes:
                        if t not in pending_tube_names:
                            pending_tube_names.append(t)
                    for t in tests:
                        if t not in pending_test_names:
                            pending_test_names.append(t)

            additional_tubes = _parse_listish(row.get("additional_sample"))
            delivery_opts = _parse_listish(row.get("report_delivery"))

            total_amount = sum(float(x.get("charge") or 0) for lst in grouped.values() for x in lst)
            default_discount = "not define"
            payment_mode = row.get("payment_mode") or "-"
            received_amount = _to_float(row.get("payment_amount"))
            due_amount = _to_float(row.get("due_amount"))
            extra_amount = _to_float(row.get("extra_amount"))
            add_discount = float(row.get("additional_discount_amount") or 0)
            final_amount = float(row.get("patient_final_amount") or max(total_amount - add_discount, 0))

            if appointment_id > 0:
                pay_row = _appointment_payment_patient_row(ap.get("payment_snapshot_json"), patient_id)
                payment_mode = row.get("payment_mode") or "-"
                if pay_row:
                    payment_mode = pay_row.get("payment_mode") or payment_mode or "-"
                    received_amount = _to_float(pay_row.get("payment_amount"))
                    due_amount = _to_float(pay_row.get("due_amount"))
                    extra_amount = _to_float(pay_row.get("extra_amount"))
                    add_discount = _to_float(pay_row.get("additional_discount_amount"))
                    final_amount = _to_float(pay_row.get("total_amount"))
                else:
                    # Appointment flow should not fall back to booking payment amounts.
                    payment_mode = payment_mode or "-"
                    received_amount = 0.0
                    due_amount = 0.0
                    extra_amount = 0.0
                    add_discount = 0.0
                    final_amount = max(total_amount, 0.0)

            return render_template(
                "hhome_collection/trf_patient_preview_dynamic.html",
                ctx={
                    "appointment_type": "Link Appointment" if appointment_id > 0 else "New Appointment",
                    "booking_code": row.get("booking_code") or f"Booking-{booking_id}",
                    "booked_by": row.get("booked_by") or "-",
                    "phlebo_name": row.get("phlebo_name") or "-",
                    "visit_text": f"{visit_date_txt} | {visit_slot}",
                    "report_schedule": str(row.get("report_schedule") or "Routine").strip().title(),
                    "patient_name": patient_name or "-",
                    "gender": row.get("gender") or "-",
                    "age_years": row.get("age_years") if row.get("age_years") is not None else "-",
                    "dob": dob_txt,
                    "pri_mobile": row.get("contact_mobile") or "-",
                    "alt_mobile": row.get("alternate_mobile") or "-",
                    "internal_ref": row.get("intrnl_rfrncd_by") or "-",
                    "card_no": "-",
                    "patient_code": row.get("patient_code") or "-",
                    "lead_id": row.get("lead_id") or "-",
                    "visit_address": ", ".join([x for x in [
                        f"House No {a_snap.get('house_flat_no')}" if a_snap.get("house_flat_no") else "",
                        f"Floor {a_snap.get('floor')}" if a_snap.get("floor") else "",
                        f"Block/Tower No {a_snap.get('block_tower_no')}" if a_snap.get("block_tower_no") else "",
                        f"Street/Sector {a_snap.get('street_line')}" if a_snap.get("street_line") else "",
                        f"Colony {a_snap.get('colony_name_snapshot') or a_snap.get('colony_name')}" if (a_snap.get("colony_name_snapshot") or a_snap.get("colony_name")) else "",
                        f"City {a_snap.get('city')}" if a_snap.get("city") else "",
                        f"- {a_snap.get('pincode_snapshot') or a_snap.get('pincode')}" if (a_snap.get("pincode_snapshot") or a_snap.get("pincode")) else "",
                    ] if x]) or "-",
                    "landmark": a_snap.get("landmark") or "-",
                    "cce_tbs": row.get("cce_level_TBS") or "-",
                    "hcp_tbs": row.get("APK_TBS") or "-",
                    "referred_by": row.get("referred_by") or "-",
                    "p_tag": row.get("patient_tag") or "-",
                    "t_tag": row.get("booking_tags") or "-",
                    "panels": [{"name": k, "tests": v} for k, v in grouped.items()],
                    "mapped_tubes": mapped_tubes,
                    "additional_tubes": additional_tubes,
                    "total_amount": _money(total_amount),
                    "default_discount": default_discount,
                    "additional_discount": _money(add_discount),
                    "final_amount": _money(final_amount),
                    "payment_mode": payment_mode,
                    "received_amount": _money(received_amount),
                    "due_amount": _money(due_amount),
                    "extra_amount": _money(extra_amount),
                    "delivery_options": delivery_opts,
                    "no_of_pricks": row.get("no_of_pricks") or "-",
                    "collection_type": row.get("sample_collection_is") or "-",
                    "collected_tube_count": len(mapped_tubes),
                    "pending_tube_count": len(pending_tube_names),
                    "pending_tube_names": pending_tube_names,
                    "pending_test_names": pending_test_names,
                },
            )
    finally:
        conn.close()


@hbatch_handover_ui_bp.get("/hhome-collection/batch-handover-ui-data")
def batch_handover_ui_data():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT b.id, b.batch_code, b.batch_json, b.booking_ids, b.patients_json, b.tubes_json,
                       b.created_at, b.created_by,
                       COALESCE(NULLIF(TRIM(u.name), ''), CONCAT('User ', b.created_by)) AS phlebo_name
                FROM hhome_collection_batch b
                LEFT JOIN users u ON u.id = b.created_by
                ORDER BY b.id DESC
                LIMIT 50
                """
            )
            batch_rows = cur.fetchall() or []
            all_booking_ids: list[int] = []
            batch_booking_map: dict[int, list[int]] = {}
            batch_meta_map: dict[int, dict] = {}
            batch_tubes_map: dict[int, dict[tuple[int, int], list[str]]] = {}
            batch_patients_map: dict[int, list[dict]] = {}
            appointment_ids_all: list[int] = []

            for br in batch_rows:
                batch_id = int(br.get("id") or 0)
                if batch_id <= 0:
                    continue
                raw_batch_json = br.get("batch_json")
                raw_booking_ids = br.get("booking_ids")
                meta = {}
                booking_ids = []
                try:
                    if isinstance(raw_batch_json, dict):
                        meta = raw_batch_json
                    elif raw_batch_json:
                        meta = json.loads(raw_batch_json)
                except Exception:
                    meta = {}
                try:
                    if isinstance(raw_booking_ids, list):
                        booking_ids = [int(x) for x in raw_booking_ids if int(x or 0) > 0]
                    elif raw_booking_ids:
                        booking_ids = [int(x) for x in (json.loads(raw_booking_ids) or []) if int(x or 0) > 0]
                except Exception:
                    booking_ids = []
                raw_patients_json = br.get("patients_json")
                patients_json = []
                try:
                    if isinstance(raw_patients_json, list):
                        patients_json = raw_patients_json
                    elif raw_patients_json:
                        patients_json = json.loads(raw_patients_json) or []
                except Exception:
                    patients_json = []
                if not isinstance(patients_json, list):
                    patients_json = []
                normalized_patients = []
                for p in patients_json:
                    if not isinstance(p, dict):
                        continue
                    try:
                        pbid = int(p.get("booking_id") or 0)
                    except Exception:
                        pbid = 0
                    if pbid <= 0:
                        continue
                    try:
                        ppid = int(p.get("patient_id") or 0)
                    except Exception:
                        ppid = 0
                    if ppid <= 0:
                        continue
                    appt_raw = p.get("appointment_id")
                    try:
                        appt_id = int(appt_raw) if appt_raw is not None else None
                    except Exception:
                        appt_id = None
                    src = str(p.get("source_type") or "").strip().upper()
                    if src not in {"BOOKING", "APPOINTMENT"}:
                        src = "APPOINTMENT" if appt_id else "BOOKING"
                    normalized_patients.append(
                        {
                            "booking_id": pbid,
                            "appointment_id": appt_id,
                            "source_type": src,
                            "patient_id": ppid,
                        }
                    )
                    if appt_id:
                        appointment_ids_all.append(appt_id)

                batch_booking_map[batch_id] = booking_ids
                batch_patients_map[batch_id] = normalized_patients
                batch_meta_map[batch_id] = {
                    "meta": meta,
                    "batch_code": str(br.get("batch_code") or "").strip() or None,
                    "created_at": br.get("created_at"),
                    "created_by": br.get("created_by"),
                    "phlebo_name": br.get("phlebo_name"),
                }
                batch_tubes_map[batch_id] = _build_tubes_map_from_batch_json(br.get("tubes_json"))
                all_booking_ids.extend(booking_ids)

            all_booking_ids = sorted(set(all_booking_ids))
            if not all_booking_ids:
                return jsonify({"ok": True, "dateIso": datetime.now().strftime("%Y-%m-%d"), "lastSync": datetime.now().strftime("%d %b %Y %I:%M %p"), "batches": []})

            placeholders_all = ",".join(["%s"] * len(all_booking_ids))
            cur.execute(
                f"""
                SELECT
                  hcb.id,
                  NULLIF(TRIM(hcb.booking_code), '') AS booking_code,
                  hcb.preferred_time_slot,
                  hcb.address_snapshot_json
                FROM hhome_collection_booking hcb
                WHERE hcb.id IN ({placeholders_all})
                ORDER BY hcb.id DESC
                """
                ,
                all_booking_ids
            )
            bookings = cur.fetchall() or []
            appointments_by_id: dict[int, dict] = {}
            appointment_tests_by_patient: dict[tuple[int, int, int], list[str]] = {}
            appointment_ids_all = sorted(set([int(x) for x in appointment_ids_all if int(x or 0) > 0]))
            if appointment_ids_all:
                placeholders_ap = ",".join(["%s"] * len(appointment_ids_all))
                cur.execute(
                    f"""
                    SELECT id, booking_id, preferred_time_slot, address_snapshot_json, appointment_tests_snapshot_json
                    FROM hhome_collection_booking_appointment
                    WHERE id IN ({placeholders_ap})
                    """,
                    appointment_ids_all,
                )
                for ar in (cur.fetchall() or []):
                    try:
                        aid = int(ar.get("id") or 0)
                    except Exception:
                        aid = 0
                    if aid > 0:
                        appointments_by_id[aid] = ar
                        bid = int(ar.get("booking_id") or 0)
                        raw_snapshot = ar.get("appointment_tests_snapshot_json")
                        snap = {}
                        if isinstance(raw_snapshot, dict):
                            snap = raw_snapshot
                        elif raw_snapshot:
                            try:
                                snap = json.loads(raw_snapshot)
                            except Exception:
                                snap = {}
                        tests_map = (snap or {}).get("tests_billing_map") or {}
                        pending_map = (snap or {}).get("pending_tests_map") or {}
                        if not isinstance(tests_map, dict):
                            tests_map = {}
                        if not isinstance(pending_map, dict):
                            pending_map = {}
                        parent_map = (snap or {}).get("parent_context_map") or {}
                        if not isinstance(parent_map, dict):
                            parent_map = {}
                        patient_keys = set(tests_map.keys()) | set(pending_map.keys()) | set(parent_map.keys())
                        for k in patient_keys:
                            try:
                                pid = int(k)
                            except Exception:
                                pid = 0
                            if pid <= 0:
                                continue
                            rows_pending = []
                            rows_all = []
                            tb_tests = tests_map.get(k)
                            if not isinstance(tb_tests, dict):
                                tb_tests = tests_map.get(str(k))
                            if isinstance(tb_tests, dict):
                                rows_all.extend(tb_tests.get("selected_tests") or [])
                                for sec in (tb_tests.get("panels") or []):
                                    if isinstance(sec, dict):
                                        rows_all.extend(sec.get("selected_tests") or [])

                            tb_pending = pending_map.get(k)
                            if not isinstance(tb_pending, dict):
                                tb_pending = pending_map.get(str(k))
                            if isinstance(tb_pending, dict):
                                rows_pending.extend(tb_pending.get("selected_tests") or [])
                                for sec in (tb_pending.get("panels") or []):
                                    if isinstance(sec, dict):
                                        rows_pending.extend(sec.get("selected_tests") or [])

                            tb_parent = parent_map.get(k)
                            if not isinstance(tb_parent, dict):
                                tb_parent = parent_map.get(str(k))
                            parent_codes = set()
                            if isinstance(tb_parent, dict):
                                parent_rows = []
                                parent_rows.extend(tb_parent.get("selected_tests") or [])
                                for sec in (tb_parent.get("panels") or []):
                                    if isinstance(sec, dict):
                                        parent_rows.extend(sec.get("selected_tests") or [])
                                for pt in parent_rows:
                                    if not isinstance(pt, dict):
                                        continue
                                    pcode = str(pt.get("booked_code") or "").strip()
                                    if pcode:
                                        parent_codes.add(pcode.upper())

                            # Final appointment tests:
                            # 1) keep pending-child rows
                            # 2) add rows from tests_map excluding parent codes
                            # 3) de-duplicate by code+name while preserving order
                            rows = []
                            rows.extend(rows_pending)
                            for t in rows_all:
                                if not isinstance(t, dict):
                                    continue
                                code_upper = str(t.get("booked_code") or "").strip().upper()
                                if code_upper and code_upper in parent_codes:
                                    continue
                                rows.append(t)
                            seen = set()
                            names = []
                            for t in rows:
                                if not isinstance(t, dict):
                                    continue
                                code = str(t.get("booked_code") or "").strip()
                                tname = (
                                    str(t.get("description") or "").strip()
                                    or str(t.get("test_name") or "").strip()
                                )
                                if not tname:
                                    continue
                                key_name = f"{code}|{tname}".lower()
                                if key_name in seen:
                                    continue
                                seen.add(key_name)
                                names.append(tname)
                            if names and bid > 0:
                                appointment_tests_by_patient[(bid, aid, pid)] = names

            booking_ids = [int(r["id"]) for r in bookings if r.get("id")]
            patients_by_booking = {bid: [] for bid in booking_ids}
            tests_by_booking_patient = {}
            pending_by_booking_patient: dict[tuple[int, int], dict[str, list[str]]] = {}

            if booking_ids:
                placeholders = ",".join(["%s"] * len(booking_ids))
                cur.execute(
                    f"""
                    SELECT
                      bp.booking_id,
                      bp.patient_id,
                      NULLIF(TRIM(hcb.booking_code), '') AS booking_code,
                      COALESCE(NULLIF(TRIM(p.patient_code), ''), CONCAT('PT', p.id)) AS patient_code,
                      COALESCE(NULLIF(TRIM(CONCAT_WS(' ', p.title, p.full_name)), ''), CONCAT('Patient ', p.id)) AS patient_name,
                      p.age_years,
                      p.gender,
                      COALESCE(NULLIF(TRIM(p.contact_mobile), ''), '') AS contact_mobile,
                      COALESCE(bp.prescription_files, '') AS prescription_files,
                      COALESCE(p.patient_documents, '') AS patient_documents
                    FROM hhome_collection_booking_patient bp
                    INNER JOIN hhome_collection_booking hcb ON hcb.id = bp.booking_id
                    INNER JOIN hpatient_master p ON p.id = bp.patient_id
                    WHERE bp.booking_id IN ({placeholders})
                    ORDER BY bp.booking_id, bp.id
                    """,
                    booking_ids,
                )
                for row in (cur.fetchall() or []):
                    bid = int(row.get("booking_id") or 0)
                    pid = int(row.get("patient_id") or 0)
                    if bid <= 0 or pid <= 0:
                        continue
                    booking_code = str(row.get("booking_code") or "").strip()
                    patient_code = str(row.get("patient_code") or "").strip()
                    prescriptions = [
                        {"name": x.split("/")[-1], "url": f"/static/uploads/prescriptions/{x}"}
                        for x in _split_csv(row.get("prescription_files"))
                    ]
                    patient_docs_raw = _split_csv(row.get("patient_documents"))
                    patient_photo = []
                    patient_docs = []
                    for n in patient_docs_raw:
                        url = f"/static/uploads/patient_documents/{n}"
                        if "_PHOTO_" in str(n).upper():
                            patient_photo.append({"name": n, "url": url})
                        else:
                            patient_docs.append({"name": n, "url": url})
                    trf_files = _files_from_hc_slip(booking_code, patient_code)
                    patients_by_booking.setdefault(bid, []).append(
                        {
                            "patientId": pid,
                            "patientCode": row.get("patient_code"),
                            "name": row.get("patient_name"),
                            "age": row.get("age_years"),
                            "gender": row.get("gender"),
                            "mobile": row.get("contact_mobile"),
                            "tests": [],
                            "tubes": [],
                            "pending_tube_names": [],
                            "pending_test_names": [],
                            "docs": [
                                {"id": f"D-{bid}-{pid}-TRF", "type": "TRF / Lab Slip", "kind": "image", "files": trf_files},
                                {"id": f"D-{bid}-{pid}-PRESC", "type": "Prescription", "kind": "image", "files": prescriptions},
                                {"id": f"D-{bid}-{pid}-DOC", "type": "Patient Document", "kind": "image", "files": patient_docs},
                                {"id": f"D-{bid}-{pid}-PHOTO", "type": "Patient Photo", "kind": "image", "files": patient_photo},
                            ],
                        }
                    )

                cur.execute(
                    f"""
                    SELECT booking_id, patient_id, COALESCE(NULLIF(TRIM(test_name), ''), TRIM(booked_code)) AS test_name
                    FROM hhome_collection_booking_patient_test
                    WHERE booking_id IN ({placeholders}) AND IFNULL(test_status, 0) = 1
                    ORDER BY id
                    """,
                    booking_ids,
                )
                for row in (cur.fetchall() or []):
                    bid = int(row.get("booking_id") or 0)
                    pid = int(row.get("patient_id") or 0)
                    tname = str(row.get("test_name") or "").strip()
                    if bid <= 0 or pid <= 0 or not tname:
                        continue
                    key = (bid, pid)
                    tests_by_booking_patient.setdefault(key, [])
                    if tname not in tests_by_booking_patient[key]:
                        tests_by_booking_patient[key].append(tname)

                # Pending-child map for TRF/batch patient context:
                # tube names from items[].tube and test names from items[].pending[].description.
                if self_table_exists := True:
                    try:
                        cur.execute(
                            f"""
                            SELECT booking_id, patient_id, pending_child_tests_json
                            FROM hcb_patient_test_pendingchildtest
                            WHERE booking_id IN ({placeholders})
                              AND IFNULL(pending_status, 0)=0
                              AND UPPER(TRIM(COALESCE(source_type, 'BOOKING')))='BOOKING'
                            ORDER BY id
                            """,
                            booking_ids,
                        )
                        for row in (cur.fetchall() or []):
                            bid = int(row.get("booking_id") or 0)
                            pid = int(row.get("patient_id") or 0)
                            if bid <= 0 or pid <= 0:
                                continue
                            tubes_list, tests_list = _extract_pending_from_json(row.get("pending_child_tests_json"))
                            if not tubes_list and not tests_list:
                                continue
                            key = (bid, pid)
                            bucket = pending_by_booking_patient.setdefault(key, {"tubes": [], "tests": []})
                            seen_t = {x.lower() for x in bucket["tubes"]}
                            for t in tubes_list:
                                tl = t.lower()
                                if tl in seen_t:
                                    continue
                                seen_t.add(tl)
                                bucket["tubes"].append(t)
                            seen_n = {x.lower() for x in bucket["tests"]}
                            for n in tests_list:
                                nl = n.lower()
                                if nl in seen_n:
                                    continue
                                seen_n.add(nl)
                                bucket["tests"].append(n)
                    except Exception:
                        pending_by_booking_patient = pending_by_booking_patient

            booking_ctx_map: dict[int, dict] = {}
            for r in bookings:
                bid = int(r.get("id") or 0)
                raw_snapshot = r.get("address_snapshot_json")
                snap = {}
                if isinstance(raw_snapshot, dict):
                    snap = raw_snapshot
                elif raw_snapshot:
                    try:
                        snap = json.loads(raw_snapshot)
                    except Exception:
                        snap = {}

                colony = str(snap.get("colony_name") or "-").strip() or "-"
                city = str(snap.get("city") or "-").strip() or "-"
                pin = str(snap.get("pincode") or "-").strip() or "-"
                slot = str(r.get("preferred_time_slot") or "-").strip() or "-"
                booking_ctx_map[bid] = {
                    "bookingCode": r.get("booking_code"),
                    "colony": f"{colony}, {city}, {pin}",
                    "slot": slot,
                }

                p_rows = patients_by_booking.get(bid, [])
                for p in p_rows:
                    key = (bid, int(p.get("patientId") or 0))
                    p["tests"] = tests_by_booking_patient.get(key, [])
                    p["tubes"] = []
                    p["pending_tube_names"] = (pending_by_booking_patient.get(key) or {}).get("tubes", [])
                    p["pending_test_names"] = (pending_by_booking_patient.get(key) or {}).get("tests", [])
            patient_detail_map: dict[tuple[int, int], dict] = {}
            for bid, plist in patients_by_booking.items():
                for p in plist:
                    pid = int(p.get("patientId") or 0)
                    if pid > 0:
                        patient_detail_map[(int(bid), pid)] = p

            batches = []
            for br in batch_rows:
                batch_id = int(br.get("id") or 0)
                if batch_id <= 0:
                    continue
                binfo = batch_meta_map.get(batch_id) or {}
                meta = binfo.get("meta") or {}
                raw_created_at = binfo.get("created_at")
                created_at_txt = raw_created_at.strftime("%I:%M %p") if hasattr(raw_created_at, "strftime") else datetime.now().strftime("%I:%M %p")
                created_date_txt = raw_created_at.strftime("%d-%m-%Y") if hasattr(raw_created_at, "strftime") else datetime.now().strftime("%d-%m-%Y")
                created_date_iso = raw_created_at.strftime("%Y-%m-%d") if hasattr(raw_created_at, "strftime") else datetime.now().strftime("%Y-%m-%d")
                booking_list = []
                group_map: dict[tuple[int, int | None, str], set[int]] = {}
                for row in (batch_patients_map.get(batch_id) or []):
                    bid = int(row.get("booking_id") or 0)
                    appt_id = row.get("appointment_id")
                    src = str(row.get("source_type") or "").upper().strip()
                    if src not in {"BOOKING", "APPOINTMENT"}:
                        src = "APPOINTMENT" if appt_id else "BOOKING"
                    pid = int(row.get("patient_id") or 0)
                    if bid <= 0 or pid <= 0:
                        continue
                    group_map.setdefault((bid, appt_id, src), set()).add(pid)

                tubes_for_batch = batch_tubes_map.get(batch_id) or {}
                for (bid, appt_id, src), pid_set in group_map.items():
                    base_ctx = booking_ctx_map.get(int(bid), {})
                    booking_code = base_ctx.get("bookingCode")
                    colony = base_ctx.get("colony", "-")
                    slot = base_ctx.get("slot", "-")
                    if src == "APPOINTMENT" and appt_id and int(appt_id) in appointments_by_id:
                        ap_row = appointments_by_id[int(appt_id)]
                        slot = str(ap_row.get("preferred_time_slot") or slot or "-").strip() or "-"
                        raw_as = ap_row.get("address_snapshot_json")
                        a_snap = {}
                        if isinstance(raw_as, dict):
                            a_snap = raw_as
                        elif raw_as:
                            try:
                                a_snap = json.loads(raw_as)
                            except Exception:
                                a_snap = {}
                        a_colony = str(a_snap.get("colony_name") or "-").strip() or "-"
                        a_city = str(a_snap.get("city") or "-").strip() or "-"
                        a_pin = str(a_snap.get("pincode") or "-").strip() or "-"
                        colony = f"{a_colony}, {a_city}, {a_pin}"

                    item_patients = []
                    for pid in sorted(pid_set):
                        detail = patient_detail_map.get((int(bid), int(pid)))
                        if not detail:
                            continue
                        pp = dict(detail)
                        pp["bookingId"] = int(bid)
                        pp["appointmentId"] = int(appt_id) if appt_id else None
                        if src == "APPOINTMENT" and appt_id:
                            ap_tests = appointment_tests_by_patient.get((int(bid), int(appt_id), int(pid)))
                            if ap_tests is not None:
                                pp["tests"] = ap_tests
                        pp["tubes"] = tubes_for_batch.get((int(bid), appt_id, int(pid), src), []) or []
                        item_patients.append(pp)
                    if not item_patients:
                        continue

                    booking_list.append(
                        {
                            "rowType": src,
                            "appointmentId": int(appt_id) if appt_id else None,
                            "bookingCode": booking_code,
                            "route": "",
                            "colony": colony,
                            "slot": slot,
                            "patients": item_patients,
                        }
                    )
                if not booking_list:
                    continue
                batches.append(
                    {
                        "batchId": str(
                            binfo.get("batch_code")
                            or meta.get("batch_id")
                            or meta.get("batch_code")
                            or f"HCBAT-{batch_id}"
                        ),
                        "status": "pending_verification",
                        "handoverTo": str(meta.get("handover_to") or "-"),
                        "riderName": str(meta.get("rider_name") or "-").strip() or "-",
                        "phleboName": str(binfo.get("phlebo_name") or f"User {binfo.get('created_by') or '-'}"),
                        "createdAt": created_at_txt,
                        "deviceId": created_date_txt,
                        "dateIso": created_date_iso,
                        "routeSummary": "Batch",
                        "appointments": booking_list,
                    }
                )

            payload = {
                "ok": True,
                "dateIso": datetime.now().strftime("%Y-%m-%d"),
                "lastSync": datetime.now().strftime("%d %b %Y %I:%M %p"),
                "batches": batches,
            }
            return jsonify(payload)
    finally:
        conn.close()
