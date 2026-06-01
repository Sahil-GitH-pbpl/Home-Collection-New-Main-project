from flask import Blueprint, current_app, jsonify, render_template, request, session
from datetime import date, timedelta

from app.home_collection_core import HHomeCollectionCore

hhome_collection_bp = Blueprint("hhome_collection", __name__)
service = HHomeCollectionCore()


@hhome_collection_bp.get("/hhome-collection")
def wizard():
    mode = (request.args.get("mode") or "").strip().lower()
    is_modify_mode = mode in {"modify", "book-appointment"}
    has_modify_ctx = bool(session.get("hmodify_context"))
    if not (is_modify_mode and has_modify_ctx):
        service.clear_booking_session(session)
        session.pop("search_mobile", None)
        session.pop("hmodify_context", None)
    return render_template("hhome_collection/hwizard.html")


@hhome_collection_bp.get("/hhome-collection/panel-test-master")
def panel_test_master_page():
    return render_template("hhome_collection/hpanel_test_master.html")


@hhome_collection_bp.get("/hhome-collection/step/<int:step>")
def load_step(step: int):
    templates = {
        1: "hhome_collection/hstep1_caller.html",
        2: "hhome_collection/hstep2_patient.html",
        3: "hhome_collection/hstep3_address.html",
        4: "hhome_collection/hstep4_booking.html",
    }
    if step not in templates:
        return jsonify({"ok": False, "message": "Invalid step"}), 400
    return render_template(templates[step])


@hhome_collection_bp.post("/hhome-collection/search-caller")
def search_caller():
    payload = request.get_json(silent=True) or {}
    mobile = (payload.get("mobile") or "").strip()
    if not mobile:
        return jsonify({"ok": False, "message": "Mobile is required"}), 400

    caller = service.get_caller_by_mobile(mobile)
    if caller:
        service.reset_session_for_new_caller(session)
        session["hcaller_id"] = caller["id"]
        selected = session.get("hselected_patients", [])
        linked_patients = service.get_linked_patients(caller["id"], session)
        reference_addresses = service.get_reference_addresses_for_caller(caller["id"])
        selected_enriched = service.get_selected_patients_enriched(caller["id"], selected, session=session)
        addresses = service.get_addresses_for_caller(caller["id"])
        return jsonify(
            {
                "ok": True,
                "found": True,
                "caller": caller,
                "linked_patients": linked_patients,
                "reference_addresses": reference_addresses,
                "selected_patients": selected_enriched,
                "addresses": addresses,
                "caller_history": service.get_caller_history_summary(caller["id"]),
                "selected_address_id": session.get("hselected_address_id"),
            }
        )

    service.reset_session_for_new_caller(session)
    session["search_mobile"] = mobile
    return jsonify(
        {
            "ok": True,
            "found": False,
            "mobile": mobile,
            "linked_patients": [],
            "reference_addresses": [],
            "selected_patients": [],
            "addresses": [],
            "caller_history": service.get_caller_history_summary(0),
            "selected_address_id": None,
        }
    )


@hhome_collection_bp.get("/hhome-collection/linked-patients")
def linked_patients():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": True, "patients": [], "reference_addresses": []})
    return jsonify(
        {
            "ok": True,
            "patients": service.get_linked_patients(caller_id, session),
            "reference_addresses": service.get_reference_addresses_for_caller(caller_id),
        }
    )


@hhome_collection_bp.get("/hhome-collection/current-caller")
def current_caller():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": True, "caller": None, "caller_history": service.get_caller_history_summary(0)})
    return jsonify(
        {
            "ok": True,
            "caller": service.get_caller(caller_id),
            "caller_history": service.get_caller_history_summary(caller_id),
        }
    )


@hhome_collection_bp.get("/hhome-collection/caller-history-booking")
def caller_history_booking():
    booking_id = request.args.get("booking_id", type=int) or 0
    result = service.get_caller_history_booking_detail(booking_id)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/select-patient")
def select_patient():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400

    payload = request.get_json(silent=True) or {}
    patient_id = payload.get("patient_id")
    result = service.select_patient(caller_id, patient_id, session, actor_user_id=session.get("user_id"))
    if result.get("ok"):
        result.update(service.get_step1_bundle(caller_id, session))
    status = 200 if result["ok"] else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/remove-selected-patient")
def remove_selected_patient():
    caller_id = session.get("hcaller_id")
    payload = request.get_json(silent=True) or {}
    patient_id = int(payload.get("patient_id", 0))
    selected = session.get("hselected_patients", [])
    session["hselected_patients"] = [item for item in selected if int(item["patient_id"]) != patient_id]
    bundle = service.get_step1_bundle(caller_id, session)
    return jsonify({"ok": True, **bundle})


@hhome_collection_bp.get("/hhome-collection/selected-patients")
def selected_patients():
    caller_id = session.get("hcaller_id")
    selected = session.get("hselected_patients", [])
    enriched = service.get_selected_patients_enriched(caller_id, selected, session=session)
    return jsonify({"ok": True, "selected_patients": enriched})


@hhome_collection_bp.post("/hhome-collection/patient/<int:patient_id>/prescriptions")
def upload_prescriptions(patient_id: int):
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400

    uploaded = request.files.getlist("files") or request.files.getlist("prescription[]") or []
    if not uploaded:
        return jsonify({"ok": False, "message": "No files uploaded"}), 400

    result = service.stage_patient_prescription_files(
        session,
        caller_id=caller_id,
        patient_id=patient_id,
        uploaded_files=uploaded,
        actor_user_id=session.get("user_id"),
    )
    if result.get("ok"):
        selected = session.get("hselected_patients", [])
        result["selected_patients"] = service.get_selected_patients_enriched(caller_id, selected, session=session)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/create-patient")
def create_patient():
    caller_id = session.get("hcaller_id")
    if caller_id and not service.get_caller(caller_id):
        session.pop("hcaller_id", None)
        caller_id = None

    if request.files or request.form:
        payload = request.form.to_dict()
        uploaded_documents = request.files.getlist("patient_documents")
    else:
        payload = request.get_json(silent=True) or {}
        uploaded_documents = []
    if not caller_id:
        contact_mobile = (payload.get("contact_mobile") or payload.get("searched_mobile") or "").strip()
        if not contact_mobile:
            return jsonify({"ok": False, "message": "Contact number is required for new caller"}), 400
        existing = service.get_caller_by_mobile(contact_mobile)
        if existing:
            caller_id = existing["id"]
        else:
            caller_payload = {
                "full_name": (payload.get("full_name") or "").strip(),
                "primary_mobile": contact_mobile,
                "alternate_mobile": None,
                "email": (payload.get("email") or "").strip() or None,
            }
            created = service.create_caller(caller_payload, actor_user_id=session.get("user_id"))
            if not created["ok"]:
                return jsonify(created), 400
            caller_id = created["caller"]["id"]
        session["hcaller_id"] = caller_id

    result = service.create_patient_and_link(
        caller_id,
        payload,
        session,
        actor_user_id=session.get("user_id"),
        uploaded_documents=uploaded_documents,
    )
    if result.get("ok"):
        result.update(service.get_step1_bundle(caller_id, session))
    status = 200 if result["ok"] else 400
    return jsonify(result), status


@hhome_collection_bp.get("/hhome-collection/patient/<int:patient_id>")
def patient_detail(patient_id: int):
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400
    patient = service.get_patient_for_edit(caller_id, patient_id)
    if not patient:
        return jsonify({"ok": False, "message": "Patient not found"}), 404
    return jsonify({"ok": True, "patient": patient})


@hhome_collection_bp.patch("/hhome-collection/patient/<int:patient_id>")
def update_patient(patient_id: int):
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400
    if request.files or request.form:
        payload = request.form.to_dict()
        uploaded_documents = request.files.getlist("patient_documents")
    else:
        payload = request.get_json(silent=True) or {}
        uploaded_documents = []
    result = service.update_patient_for_caller(
        caller_id,
        patient_id,
        payload,
        actor_user_id=session.get("user_id"),
        uploaded_documents=uploaded_documents,
    )
    if result.get("ok"):
        result.update(service.get_step1_bundle(caller_id, session))
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/merge-caller")
def merge_caller():
    source_caller_id = session.get("hcaller_id")
    if not source_caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400

    payload = request.get_json(silent=True) or {}
    target_caller_id = int(payload.get("target_caller_id") or 0)
    result = service.merge_callers(
        source_caller_id,
        target_caller_id,
        actor_user_id=session.get("user_id"),
    )
    if result.get("ok"):
        session["hcaller_id"] = result["target_caller_id"]
        service.reset_session_for_new_caller(session)
        session["hcaller_id"] = result["target_caller_id"]
        result.update(service.get_step1_bundle(result["target_caller_id"], session))
        result["caller"] = service.get_caller(result["target_caller_id"])
        result["caller_history"] = service.get_caller_history_summary(result["target_caller_id"])
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.get("/hhome-collection/colonies")
def colonies():
    city = request.args.get("city")
    return jsonify({"ok": True, "colonies": service.list_colonies(city=city)})

@hhome_collection_bp.get("/hhome-collection/addresses")
def addresses():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": True, "addresses": [], "selected_address_id": session.get("hselected_address_id")})
    return jsonify({
        "ok": True,
        "addresses": service.get_addresses_for_caller(caller_id),
        "selected_address_id": session.get("hselected_address_id"),
    })


@hhome_collection_bp.get("/hhome-collection/reference-addresses")
def reference_addresses():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": True, "reference_addresses": []})
    return jsonify({
        "ok": True,
        "reference_addresses": service.get_reference_addresses_for_caller(caller_id),
    })


@hhome_collection_bp.post("/hhome-collection/create-address")
def create_address():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400

    selected = session.get("hselected_patients", [])
    patient_ids = [int(x["patient_id"]) for x in selected]
    if not patient_ids:
        return jsonify({"ok": False, "message": "Select at least one patient first"}), 400

    payload = request.get_json(silent=True) or {}
    result = service.create_address_for_patients(
        patient_ids, payload, actor_user_id=session.get("user_id")
    )
    if result["ok"]:
        session["hselected_address_id"] = result["address"]["id"]
        session["hselected_address_snapshot"] = result["address_snapshot"]
    status = 200 if result["ok"] else 400
    return jsonify(result), status


@hhome_collection_bp.get("/hhome-collection/address/<int:address_id>")
def address_detail(address_id: int):
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400
    address = service.get_address_for_caller(caller_id, address_id)
    if not address:
        return jsonify({"ok": False, "message": "Address not found"}), 404
    return jsonify({"ok": True, "address": address})


@hhome_collection_bp.patch("/hhome-collection/address/<int:address_id>")
def update_address(address_id: int):
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400

    payload = request.get_json(silent=True) or {}
    result = service.update_address_for_caller(
        caller_id, address_id, payload, actor_user_id=session.get("user_id")
    )
    if result.get("ok"):
        session["hselected_address_id"] = result["address"]["id"]
        session["hselected_address_snapshot"] = result["address_snapshot"]
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/reference-address/<int:reference_address_id>/finalize")
def finalize_reference_address(reference_address_id: int):
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller is required first"}), 400

    result = service.finalize_reference_address_for_caller(
        caller_id,
        reference_address_id,
        actor_user_id=session.get("user_id"),
    )
    if result.get("ok"):
        result.update(service.get_step1_bundle(caller_id, session))
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/select-address")
def select_address():
    payload = request.get_json(silent=True) or {}
    address_id = int(payload.get("address_id", 0))
    snapshot = service.get_address_snapshot(address_id)
    if not snapshot:
        return jsonify({"ok": False, "message": "Address not found"}), 404

    session["hselected_address_id"] = address_id
    session["hselected_address_snapshot"] = snapshot
    return jsonify({"ok": True, "selected_address_id": address_id, "snapshot": snapshot})


@hhome_collection_bp.get("/hhome-collection/summary")
def summary():
    caller_id = session.get("hcaller_id")
    if not caller_id:
        return jsonify({"ok": False, "message": "Caller missing"}), 400

    caller = service.get_caller(caller_id)
    selected = session.get("hselected_patients", [])
    selected_enriched = service.get_selected_patients_enriched(caller_id, selected, session=session)
    address = session.get("hselected_address_snapshot")
    return jsonify({"ok": True, "caller": caller, "selected_patients": selected_enriched, "selected_address": address})


@hhome_collection_bp.get("/hhome-collection/route-slot-grid")
def route_slot_grid():
    visit_date = (request.args.get("date") or "").strip()
    if not visit_date:
        visit_date = (date.today() + timedelta(days=1)).isoformat()
    selected_route = (request.args.get("route") or "").strip()
    if not selected_route:
        selected_route = ((session.get("hselected_address_snapshot") or {}).get("route_no_snapshot") or "").strip()
    result = service.route_slot_grid_data(visit_date=visit_date, selected_route=selected_route)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.get("/hhome-collection/internal-ref-users")
def internal_ref_users():
    try:
        q = (request.args.get("q") or "").strip()
        limit = request.args.get("limit", default=20, type=int)
        rows = service.search_active_staff_users(q=q, limit=limit)
        return jsonify({"ok": True, "items": rows})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-companies")
def panel_companies():
    try:
        q = (request.args.get("q") or "").strip()
        atype = (request.args.get("atype") or "").strip()
        rows = service.search_panel_companies(
            q,
            limit=request.args.get("limit", default=15, type=int),
            atype=atype or None,
        )
        return jsonify({"ok": True, "items": rows})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-groups")
def panel_groups():
    try:
        comp_cat_id = request.args.get("comp_cat_id")
        if not comp_cat_id:
            return jsonify({"ok": False, "message": "comp_cat_id is required"}), 400
        return jsonify({"ok": True, "groups": service.panel_groups(comp_cat_id)})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-subgroups")
def panel_subgroups():
    try:
        comp_cat_id = request.args.get("comp_cat_id")
        gcode = request.args.get("gcode")
        if not comp_cat_id or not gcode:
            return jsonify({"ok": False, "message": "comp_cat_id and gcode are required"}), 400
        return jsonify({"ok": True, "subgroups": service.panel_subgroups(comp_cat_id, gcode)})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-tests")
def panel_tests():
    try:
        comp_cat_id = request.args.get("comp_cat_id")
        gcode = request.args.get("gcode")
        scode = request.args.get("scode")
        if not comp_cat_id or not gcode or not scode:
            return jsonify({"ok": False, "message": "comp_cat_id, gcode and scode are required"}), 400
        return jsonify({"ok": True, "tests": service.panel_tests(comp_cat_id, gcode, scode)})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-companies-initial")
def panel_companies_initial():
    try:
        rows = service.panel_companies_initial(limit=request.args.get("limit", default=5, type=int))
        return jsonify({"ok": True, "items": rows})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-tests-by-company")
def panel_tests_by_company():
    try:
        comp_cat_id = request.args.get("comp_cat_id")
        if not comp_cat_id:
            return jsonify({"ok": False, "message": "comp_cat_id is required"}), 400
        rows = service.panel_tests_by_company(comp_cat_id)
        return jsonify({"ok": True, "tests": rows})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-test-search")
def panel_test_search():
    try:
        comp_cat_id = request.args.get("comp_cat_id")
        query = (request.args.get("q") or "").strip()
        limit = request.args.get("limit", default=50, type=int)
        if not comp_cat_id or len(query) < 2:
            return jsonify({"ok": True, "tests": []})
        return jsonify({"ok": True, "tests": service.search_panel_tests(comp_cat_id, query, limit=limit)})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/panel-child-tests")
def panel_child_tests():
    try:
        parent_gcode = request.args.get("parent_gcode")
        parent_scode = request.args.get("parent_scode")
        parent_test_code = request.args.get("parent_test_code")
        if not parent_gcode or not parent_scode or not parent_test_code:
            return jsonify({"ok": False, "message": "parent_gcode, parent_scode and parent_test_code are required"}), 400
        rows = service.panel_child_tests(parent_gcode, parent_scode, parent_test_code)
        return jsonify({"ok": True, "tests": rows})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/test-specimen-catalog")
def test_specimen_catalog():
    try:
        return jsonify({"ok": True, **service.test_specimen_catalog()})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


@hhome_collection_bp.get("/hhome-collection/modify-context")
def modify_context():
    tag_options = {
        "patient": [x.get("tag_name") for x in service.list_tag_master("patient") if (x or {}).get("tag_name")],
        "permanent": [x.get("tag_name") for x in service.list_tag_master("permanent") if (x or {}).get("tag_name")],
        "transactional": [x.get("tag_name") for x in service.list_tag_master("transactional") if (x or {}).get("tag_name")],
    }
    ctx = session.get("hmodify_context") or {}
    if not ctx:
        return jsonify({"ok": True, "active": False, "tag_options": tag_options})
    return jsonify({"ok": True, "active": True, "context": ctx, "tag_options": tag_options})


@hhome_collection_bp.post("/hhome-collection/modify-booking")
def modify_booking():
    ctx = session.get("hmodify_context") or {}
    booking_id = int(ctx.get("booking_id") or 0)
    if booking_id <= 0:
        return jsonify({"ok": False, "message": "Modify session not found"}), 400

    caller_id = session.get("hcaller_id")
    selected_patients = session.get("hselected_patients", [])
    selected_address_id = session.get("hselected_address_id")
    selected_snapshot = session.get("hselected_address_snapshot")

    payload = request.get_json(silent=True) or {}
    payload["modify_reason_text"] = (ctx.get("reason_text") or "").strip()
    payload["_session_ref"] = session

    flow_type = (ctx.get("flow_type") or "").strip().lower()
    modify_scope = (ctx.get("modify_scope") or "").strip().lower()
    payload["_modify_flow_type"] = flow_type
    if flow_type == "followup_appointment":
        payload["followup_reason_text"] = (ctx.get("reason_text") or "").strip()
        result = service.create_followup_appointment(
            booking_id=booking_id,
            caller_id=caller_id,
            selected_patients=selected_patients,
            selected_address_id=selected_address_id,
            selected_snapshot=selected_snapshot,
            payload=payload,
            actor_user_id=session.get("user_id"),
        )
    elif flow_type == "modify_appointment" or flow_type == "auto_followup_pending_child" or modify_scope == "appointment":
        result = service.modify_appointment(
            booking_id=booking_id,
            appointment_id=int(ctx.get("appointment_id") or 0),
            caller_id=caller_id,
            selected_patients=selected_patients,
            selected_address_id=selected_address_id,
            selected_snapshot=selected_snapshot,
            payload=payload,
            actor_user_id=session.get("user_id"),
        )
    else:
        result = service.modify_booking(
            booking_id=booking_id,
            caller_id=caller_id,
            selected_patients=selected_patients,
            selected_address_id=selected_address_id,
            selected_snapshot=selected_snapshot,
            payload=payload,
            actor_user_id=session.get("user_id"),
        )

    if result.get("ok"):
        session["last_booking_id"] = booking_id
        service.clear_booking_session(session)
        session.pop("hmodify_context", None)

    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@hhome_collection_bp.post("/hhome-collection/confirm-booking")
def confirm_booking():
    caller_id = session.get("hcaller_id")
    selected_patients = session.get("hselected_patients", [])
    selected_address_id = session.get("hselected_address_id")
    selected_snapshot = session.get("hselected_address_snapshot")

    payload = request.get_json(silent=True) or {}
    payload["_session_ref"] = session
    result = service.confirm_booking(
        caller_id=caller_id,
        selected_patients=selected_patients,
        selected_address_id=selected_address_id,
        selected_snapshot=selected_snapshot,
        payload=payload,
        actor_user_id=session.get("user_id"),
        app_obj=current_app._get_current_object(),
    )

    if result["ok"]:
        session["last_booking_id"] = result["booking_id"]
        service.clear_booking_session(session)
        session.pop("hmodify_context", None)
    status = 200 if result["ok"] else 400
    return jsonify(result), status


@hhome_collection_bp.get("/hhome-collection/success")
def success():
    booking_id = request.args.get("booking_id", type=int)
    if not booking_id:
        return render_template("hhome_collection/hsuccess.html", booking=None)
    return render_template("hhome_collection/hsuccess.html", booking=service.get_booking_full(booking_id))


@hhome_collection_bp.get("/hhome-collection/print/<int:booking_id>")
def print_slip(booking_id: int):
    booking = service.get_booking_full(booking_id)
    if not booking:
        return "Booking not found", 404
    return render_template("hhome_collection/hprint_slip.html", booking=booking)
