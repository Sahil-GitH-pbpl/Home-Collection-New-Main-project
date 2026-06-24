from flask import Blueprint, current_app, jsonify, render_template, request, session
from datetime import date, timedelta

from app.home_collection_core import HHomeCollectionCore

hhome_collection_dashboard_bp = Blueprint("hhome_collection_dashboard", __name__)
service = HHomeCollectionCore()


@hhome_collection_dashboard_bp.get("/hhome-collection/dashboard")
def dashboard():
    return render_template("hhome_collection/hdashboard.html")


@hhome_collection_dashboard_bp.get("/hhome-collection/assign-booking")
def assign_booking():
    default_date = (date.today() + timedelta(days=1)).isoformat()
    return render_template("hhome_collection/hassign_booking.html", default_date=default_date)


@hhome_collection_dashboard_bp.get("/hhome-collection/leaderboard")
def leaderboard():
    # Temporary HC leaderboard screen (explicitly documented in README for future removal).
    return render_template("hhome_collection/hleaderboard.html")


@hhome_collection_dashboard_bp.get("/hhome-collection/audit-trail")
def audit_trail():
    return render_template("hhome_collection/haudit_trail.html")


@hhome_collection_dashboard_bp.get("/hhome-collection/dashboard-data")
def dashboard_data():
    page_raw = (request.args.get("page") or "1").strip()
    page_size_raw = (request.args.get("page_size") or "50").strip()
    sort_key = (request.args.get("sort_key") or "").strip()
    sort_dir = (request.args.get("sort_dir") or "asc").strip()
    params = {
        "date_from": request.args.get("date_from"),
        "date_to": request.args.get("date_to"),
        "status": request.args.get("status"),
        "cce_tbs": request.args.get("cce_tbs"),
        "route": request.args.get("route"),
        "search": request.args.get("search"),
        "page": page_raw,
        "page_size": page_size_raw,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
    }
    return jsonify({"ok": True, **service.dashboard_rows(params)})


@hhome_collection_dashboard_bp.get("/hhome-collection/leaderboard-data")
def leaderboard_data():
    # TEMP: simple aggregated leaderboard for booking creation + completion ownership.
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    return jsonify({"ok": True, **service.leaderboard_counts(date_from=date_from, date_to=date_to)})


@hhome_collection_dashboard_bp.get("/hhome-collection/audit-trail-data")
def audit_trail_data():
    booking_id = int(request.args.get("booking_id", 0) or 0)
    result = service.get_booking_audit_trail(booking_id)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.get("/hhome-collection/assign-booking-data")
def assign_booking_data():
    plan_date = request.args.get("date")
    return jsonify(service.assignment_planner_data(plan_date))


@hhome_collection_dashboard_bp.post("/hhome-collection/assign-bookings-commit")
def assign_bookings_commit():
    payload = request.get_json(silent=True) or {}
    result = service.commit_assignment_plan(
        plan_date=payload.get("plan_date"),
        assignments=payload.get("assignments") or [],
        actor_user_id=session.get("user_id"),
        app_obj=current_app._get_current_object(),
    )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.get("/hhome-collection/booking/<int:booking_id>")
def booking_detail(booking_id: int):
    appointment_id = int(request.args.get("appointment_id", 0) or 0)
    booking = service.get_booking_full(booking_id, appointment_id=appointment_id)
    if not booking:
        return jsonify({"ok": False, "message": "Not found"}), 404
    return jsonify({"ok": True, "booking": booking})


@hhome_collection_dashboard_bp.get("/hhome-collection/phlebotomists")
def phlebotomists():
    return jsonify({"ok": True, "phlebotomists": service.get_phlebotomists()})


@hhome_collection_dashboard_bp.post("/hhome-collection/assign-phlebotomist")
def assign_phlebotomist():
    payload = request.get_json(silent=True) or {}
    booking_id = int(payload.get("booking_id", 0))
    appointment_id = int(payload.get("appointment_id", 0))
    user_id = int(payload.get("user_id", 0))
    result = service.assign_phlebotomist(
        booking_id,
        user_id,
        actor_user_id=session.get("user_id"),
        appointment_id=appointment_id,
        app_obj=current_app._get_current_object(),
    )
    status = 200 if result["ok"] else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.post("/hhome-collection/unassign-phlebotomist")
def unassign_phlebotomist():
    payload = request.get_json(silent=True) or {}
    result = service.unassign_phlebotomist(
        booking_id=int(payload.get("booking_id", 0) or 0),
        appointment_id=int(payload.get("appointment_id", 0) or 0),
        reason_text=(payload.get("reason_text") or "").strip(),
        actor_user_id=session.get("user_id"),
    )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.post("/hhome-collection/cancel-booking")
def cancel_booking():
    payload = request.get_json(silent=True) or {}
    booking_id = int(payload.get("booking_id", 0))
    appointment_id = int(payload.get("appointment_id", 0))
    reason_text = (payload.get("reason_text") or "").strip()
    additional_info = (payload.get("additional_info") or "").strip()
    if reason_text == "Patient requested cancellation" and not additional_info:
        return jsonify({"ok": False, "message": "Additional information is required"}), 400
    result = service.cancel_booking(
        booking_id,
        reason_text=reason_text,
        remark=additional_info,
        actor_user_id=session.get("user_id"),
        appointment_id=appointment_id,
        reschedule_requested=bool(int(payload.get("reschedule_requested", 0) or 0)),
        new_slot_known=bool(int(payload.get("new_slot_known", 0) or 0)),
        proposed_visit_date=(payload.get("proposed_visit_date") or "").strip(),
        proposed_time_slot=(payload.get("proposed_time_slot") or "").strip(),
    )
    status = 200 if result["ok"] else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.post("/hhome-collection/book-appointment-init")
def book_appointment_init():
    payload = request.get_json(silent=True) or {}
    booking_id = int(payload.get("booking_id", 0))
    reason_text = (payload.get("reason_text") or "").strip()
    result = service.begin_followup_appointment_session(
        booking_id=booking_id,
        reason_text=reason_text,
        session=session,
        actor_user_id=session.get("user_id"),
    )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.post("/hhome-collection/modify-init")
def modify_init():
    payload = request.get_json(silent=True) or {}
    booking_id = int(payload.get("booking_id", 0))
    appointment_id = int(payload.get("appointment_id", 0))
    reason_text = (payload.get("reason_text") or "").strip()
    if appointment_id > 0:
        result = service.begin_modify_appointment_session(
            booking_id=booking_id,
            appointment_id=appointment_id,
            reason_text=reason_text,
            session=session,
            actor_user_id=session.get("user_id"),
        )
    else:
        result = service.begin_modify_booking_session(
            booking_id=booking_id,
            reason_text=reason_text,
            session=session,
            actor_user_id=session.get("user_id"),
        )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_dashboard_bp.post("/hhome-collection/reschedule-booking")
def reschedule_booking():
    payload = request.get_json(silent=True) or {}
    result = service.reschedule_booking(
        booking_id=int(payload.get("booking_id", 0)),
        appointment_id=int(payload.get("appointment_id", 0)),
        preferred_visit_date=(payload.get("preferred_visit_date") or "").strip(),
        preferred_time_slot=(payload.get("preferred_time_slot") or "").strip(),
        reason_text=(payload.get("reason_text") or "").strip(),
        actor_user_id=session.get("user_id"),
    )
    status = 200 if result.get("ok") else 400
    return jsonify(result), status
