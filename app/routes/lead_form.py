import os
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection
from app.alerts import notify_new_lead_async

lead_form_bp = Blueprint("lead_form", __name__)

@lead_form_bp.route("/lead-form")
def lead_form():
    # No ID preview needed now, just render template
    return render_template("lead_form.html")


@lead_form_bp.route("/submit-lead", methods=["POST"])
def submit_lead():
    if "user_id" not in session:
        return redirect(url_for("auth.home"))

    # ===== Extract form data =====
    phone        = (request.form.get('phone') or '').strip()
    wa_only      = 1 if request.form.get('wa_only') else 0
    name         = (request.form.get('name') or '').strip()
    alt_phone    = (request.form.get('alt_phone') or '').strip()
    alt_wa_only  = 1 if request.form.get('alt_wa_only') else 0
    visit_window = request.form.get('visit_window') or ''
    remarks      = request.form.get('remarks') or ''
    tags         = (request.form.get('tags') or '').strip()
    num_patients = request.form.get('num_patients') or '1'
    files        = request.files.getlist('prescription[]') or []
    created_by   = (session.get('username') or 'Unknown').strip()

    if len(files) > 6:
        files = files[:6]

    upload_folder = os.path.join(os.path.dirname(__file__), "..", "static", "uploads")
    upload_folder = os.path.abspath(upload_folder)
    os.makedirs(upload_folder, exist_ok=True)

    lead_id = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # ===== Insert new lead =====
            cur.execute("""
                INSERT INTO leads
                (phone, wa_only, name, alt_phone, alt_wa_only, visit_window, prescription,
                 remarks, tags, num_patients, created_by, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Open')
            """, (phone,wa_only,name,alt_phone,alt_wa_only,visit_window,'',
                  remarks,tags,num_patients,created_by))
            new_id = cur.lastrowid
            lead_id = f"LD-{new_id:03d}"

            # ===== Save files if any =====
            saved = []
            for f in files:
                if not f or not f.filename:
                    continue
                safe = secure_filename(f"{lead_id}_{f.filename}")
                f.save(os.path.join(upload_folder, safe))
                saved.append(safe)

            # ===== Update with lead_id + prescription list =====
            cur.execute("""
                UPDATE leads SET lead_id=%s, prescription=%s WHERE id=%s
            """, (lead_id, ",".join(saved), new_id))

        conn.commit()

        # ===== Notify async (WhatsApp / alerts) =====
        notify_new_lead_async(
            lead_id=lead_id, phone=phone, wa_only=wa_only, name=name,
            alt_phone=alt_phone, visit_window=visit_window, tags=tags,
            num_patients=num_patients, remarks=remarks, created_by=created_by
        )

    except Exception:
        try:
            conn.rollback()
        except:
            pass
        # Keep one safe marker for debugging
        print("[LEAD_FORM] Error occurred inside lead_form.py")
        raise
    finally:
        conn.close()

    # ===== Return based on request type =====
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"success": True, "next_lead_id": lead_id})
    else:
        return redirect(url_for('dashboard.dashboard') + '#/leads')
