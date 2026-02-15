from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, session, jsonify
)
import json
import datetime
from zoneinfo import ZoneInfo
from pymysql.cursors import DictCursor

from app.db.connection import get_db_connection

ticket_detail_bp = Blueprint("ticket_detail", __name__)

# ---------- Time / TZ ----------
IST = ZoneInfo("Asia/Kolkata")

def _now() -> datetime.datetime:
    return datetime.datetime.now(IST)

# ---------- Helpers ----------
def _parse_iso_aware(s: str) -> datetime.datetime | None:
    if not s:
        return None
    try:
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("utf-8", "ignore")
        if isinstance(s, str):
            s = s.strip()
            if not s:
                return None
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
    except Exception:
        return None
    return None

def _safe_json_list(value) -> list:
    try:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "ignore")
        if isinstance(value, str):
            x = json.loads(value)
            if isinstance(x, str):
                try:
                    x = json.loads(x)
                except Exception:
                    pass
            if isinstance(x, dict):
                return [x]
            if isinstance(x, list):
                return x
    except Exception:
        pass
    return []

def _fmt_commitment(commitment_at) -> dict:
    if not commitment_at:
        return {"display": "-", "state": None, "text": None}

    dt_aware: datetime.datetime | None = None
    if isinstance(commitment_at, datetime.datetime):
        dt_aware = (
            commitment_at.replace(tzinfo=IST)
            if commitment_at.tzinfo is None
            else commitment_at.astimezone(IST)
        )
    elif isinstance(commitment_at, str):
        try:
            naive = datetime.datetime.strptime(commitment_at, "%Y-%m-%d %H:%M:%S")
            dt_aware = naive.replace(tzinfo=IST)
        except Exception:
            iso = _parse_iso_aware(commitment_at)
            if iso:
                dt_aware = iso.astimezone(IST)
            else:
                return {"display": commitment_at, "state": None, "text": None}
    else:
        return {"display": str(commitment_at), "state": None, "text": None}

    display = dt_aware.strftime("%d %b %Y, %I:%M %p")
    delta_sec = (dt_aware - _now()).total_seconds()

    if delta_sec >= 3600:
        state, text = "ok", f"in ~{int(delta_sec // 60)} min"
    elif 0 <= delta_sec < 3600:
        state, text = "near", f"due in {int(delta_sec // 60)} min"
    else:
        state, text = "overdue", f"overdue by {int(abs(delta_sec) // 60)} min"

    return {"display": display, "state": state, "text": text}

def _username() -> str:
    return session.get("username") or "System"

def _user_id_int():
    try:
        return int(session.get("user_id"))
    except Exception:
        return None

def _casefold(s):
    return (s or "").strip().casefold()

def _wants_json() -> bool:
    if (request.args.get("format") or "").lower() == "json":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


# ------------------ DETAIL: /<ticket_id> ------------------
@ticket_detail_bp.route("/<int:ticket_id>", methods=["GET"])
def ticket_detail(ticket_id: int):
    conn = get_db_connection()
    cur = conn.cursor(DictCursor)

    cur.execute("SELECT * FROM tickets WHERE id=%s", (ticket_id,))
    ticket = cur.fetchone()

    if not ticket:
        cur.close()
        conn.close()
        if _wants_json():
            return jsonify({"ok": False, "error": "Ticket not found"}), 404
        flash("Ticket not found", "danger")
        return redirect(url_for("ticketlist.tickets_list"))

    # Closed flag
    is_closed = (str(ticket.get("status") or "").strip().lower() == "closed")

    # Tags JSON → list
    tags = _safe_json_list(ticket.get("tags_json"))

    tag_staff_ids = [t.get("staffId") for t in tags if isinstance(t, dict) and t.get("staffId")]

    # Pull latest remark per tagged staff
    last_by_staff: dict[int, str] = {}
    if tag_staff_ids:
        placeholders = ",".join(["%s"] * len(tag_staff_ids))
        cur.execute(
            f"""
            SELECT
              staff_id,
              MAX(created_at) AS last_time,
              SUBSTRING_INDEX(
                GROUP_CONCAT(remark ORDER BY created_at DESC SEPARATOR '||'),
                '||', 1
              ) AS last_remark
            FROM ticket_tag_updates
            WHERE ticket_id=%s AND staff_id IN ({placeholders})
            GROUP BY staff_id
            """,
            (ticket_id, *tag_staff_ids),
        )
        for row in cur.fetchall() or []:
            last_by_staff[row["staff_id"]] = row["last_remark"]

    decorated_tags = []
    for t in tags:
        if not isinstance(t, dict):
            decorated_tags.append({"raw": t, "dueAt_display": None, "last_remark": None})
            continue

        due_disp = None
        if t.get("dueAt"):
            dt = _parse_iso_aware(t["dueAt"])
            if dt:
                due_disp = dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p")
            else:
                due_disp = t["dueAt"]

        staff_id = t.get("staffId")
        decorated = dict(t)
        decorated["dueAt_display"] = due_disp
        decorated["last_remark"] = last_by_staff.get(staff_id)
        decorated_tags.append(decorated)

    # --- Commitment (default from ticket)
    commitment = _fmt_commitment(ticket.get("commitment_at"))

    # Fetch ALL users (legacy)
    cur.execute("SELECT id, name FROM users ORDER BY name")
    users = cur.fetchall() or []

    # --- DB Assignee (base)
    base_assignee = {"id": ticket.get("assign_to_user_id"), "name": "-"}
    if ticket.get("assign_to_user_id"):
        cur.execute("SELECT id, name FROM users WHERE id=%s", (ticket["assign_to_user_id"],))
        r = cur.fetchone()
        if r:
            base_assignee = r

    # --- Assignment updates history
    cur.execute(
        """
        SELECT a.*, u1.name AS from_name, u2.name AS to_name
        FROM ticket_assign_updates a
        LEFT JOIN users u1 ON u1.id = a.from_user_id
        LEFT JOIN users u2 ON u2.id = a.to_user_id
        WHERE a.ticket_id = %s
        ORDER BY a.created_at DESC
        """,
        (ticket_id,),
    )
    assign_updates = cur.fetchall() or []

    # --- Active claim (to show effective assignee)
    cur.execute("""
        SELECT c.user_id, c.expires_at, u.name AS claim_user_name
        FROM ticket_claims c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE c.ticket_id=%s AND c.is_active=1 AND c.expires_at > NOW()
        LIMIT 1
    """, (ticket_id,))
    claim = cur.fetchone()

    # By business rule: Assign To me CLAIM holder ka naam dikhana hai
    current_assignee = dict(base_assignee)
    claim_banner = None
    if claim:
        current_assignee = {"id": claim["user_id"], "name": claim["claim_user_name"]}
        # (Optional) commitment ko claim expiry se override karke show karo
        try:
            exp = claim.get("expires_at")
            if exp:
                exp_aware = exp.replace(tzinfo=IST) if exp.tzinfo is None else exp.astimezone(IST)
                delta_sec = (exp_aware - datetime.now(IST)).total_seconds()
                if delta_sec >= 0:
                    # Rebuild commitment dict against claim expiry
                    display = exp_aware.strftime("%d %b %Y, %I:%M %p")
                    if delta_sec >= 3600:
                        state, text = "ok", f"in ~{int(delta_sec // 60)} min"
                    elif 0 <= delta_sec < 3600:
                        state, text = "near", f"due in {int(delta_sec // 60)} min"
                    else:
                        state, text = "overdue", f"overdue by {int(abs(delta_sec) // 60)} min"
                    commitment = {"display": display, "state": state, "text": text}
            claim_banner = {
                "by": claim["claim_user_name"],
                "until": exp_aware.strftime("%d %b %Y, %I:%M %p") if exp else None
            }
        except Exception:
            pass

    # Closed: show date/time but hide badge (state/text)
    if is_closed:
        if not (commitment.get("display")) and ticket.get("commitment_at"):
            commitment = _fmt_commitment(ticket.get("commitment_at"))
        commitment["state"] = None
        commitment["text"] = None

    # ---------- Filter tags for current user (for Tag Update dropdown) ----------
    me_id = _user_id_int()
    me_name_cf = _casefold(_username())
    tags_for_user = []
    for t in decorated_tags:
        if not isinstance(t, dict):
            continue
        # match by staffId OR by name/text (case-insensitive)
        is_me = False
        if t.get("staffId") is not None and me_id is not None:
            try:
                is_me = int(t.get("staffId")) == me_id
            except Exception:
                is_me = False
        if not is_me:
            if _casefold(t.get("staffName")) == me_name_cf or _casefold(t.get("text")) == me_name_cf:
                is_me = True
        if is_me:
            tags_for_user.append(t)

    cur.close()
    conn.close()

    if _wants_json():
        return jsonify({
            "ok": True,
            "ticket": ticket,
            "tags": decorated_tags,
            "tags_for_user": tags_for_user,
            "users": users,
            "current_assignee": current_assignee,  # 👈 show claim-holder if any
            "base_assignee": base_assignee,        # 👈 optional (for UI note)
            "commitment": commitment,
            "assign_updates": assign_updates,
            "claim_banner": claim_banner,
            "is_closed": is_closed,                # 👈 added
        })

    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        tags=decorated_tags,
        tags_for_user=tags_for_user,
        users=users,
        current_assignee=current_assignee,   # 👈 claim-holder name shows in “Assign To”
        base_assignee=base_assignee,         # 👈 optional: show somewhere as “Base assignee”
        commitment=commitment,
        assign_updates=assign_updates,
        claim_banner=claim_banner,
        is_closed=is_closed,                 # 👈 added
    )


# ------------------ POST: Quick Remark / Close ------------------
@ticket_detail_bp.route("/<int:ticket_id>/quick_remark", methods=["POST"])
def ticket_quick_remark(ticket_id: int):
    """
    Quick Remark:
      - Add history row (from/to/reason = NULL), remark + updated_by + created_at
      - Ticket stays open

    Close Ticket:
      - Same history row as above
      - tickets.status = 'Closed'
      - NEW: tickets.closed_at = NOW()
      - NEW: tickets.closed_remark = "{HAPPY|SAD} | {user remark}"
      - NEW: tickets.closed_by_user_id = current_user_id
      - NEW: if closer != effective assignee -> require confirm_cross_close=1
      - NEW: when closing -> require closure_mood in {happy, sad}
    """
    remark = (request.form.get("remark") or "").strip()
    close_ticket = (request.form.get("close_ticket") == "1")

    # NEW: extra fields sent by the single modal (sections hide/show)
    closure_mood = (request.form.get("closure_mood") or "").strip().lower()  # "happy" | "sad" (required only when closing)
    confirm_cross_close = (request.form.get("confirm_cross_close") == "1")   # "1" only after user confirms cross-assignee close

    if not remark:
        if _wants_json():
            return jsonify({"ok": False, "error": "Remark is required"}), 400
        flash("Remark is required", "danger")
        return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

    conn = get_db_connection()
    cur = conn.cursor(DictCursor)

    try:
        # --------- Determine effective assignee (claim overrides base) ----------
        effective_assignee_id = None

        # Active claim?
        cur.execute(
            """
            SELECT user_id
            FROM ticket_claims
            WHERE ticket_id=%s AND is_active=1 AND expires_at > NOW()
            LIMIT 1
            """,
            (ticket_id,),
        )
        row = cur.fetchone()
        if row and row.get("user_id") is not None:
            effective_assignee_id = int(row["user_id"])
        else:
            # Fall back to base assignment on ticket
            cur.execute("SELECT assign_to_user_id FROM tickets WHERE id=%s", (ticket_id,))
            row2 = cur.fetchone()
            if row2 and row2.get("assign_to_user_id") is not None:
                effective_assignee_id = int(row2["assign_to_user_id"])

        me_id = _user_id_int()

        # --------------------- Close-time validations (no writes yet) ---------------------
        if close_ticket:
            # Require mood selection
            if closure_mood not in ("happy", "sad"):
                if _wants_json():
                    return jsonify({"ok": False, "error": "closure_mood is required (happy/sad) when closing."}), 400
                flash("Please select Happy or Sad closure.", "danger")
                return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

            # If someone else is assigned, ask for confirmation
            is_cross = (
                effective_assignee_id is not None and
                me_id is not None and
                int(effective_assignee_id) != int(me_id)
            )
            if is_cross and not confirm_cross_close:
                # Frontend should show the "Are you sure you are closing a ticket assigned to {{AssigneeName}}?" step
                if _wants_json():
                    return jsonify({"ok": False, "error": "Cross-assignee confirmation required."}), 409
                flash("Confirmation required: you are closing a ticket assigned to someone else.", "warning")
                return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

        # -------------- 1) Always insert a history row (same behavior) --------------
        cur_hist = conn.cursor()
        cur_hist.execute(
            """
            INSERT INTO ticket_assign_updates
                (ticket_id, from_user_id, to_user_id, reason, remark, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (ticket_id, None, None, None, remark, _username()),
        )
        cur_hist.close()

        msg = "Remark submitted."

        # --------------------- 2) Close path: update ticket fields ----------------------
        if close_ticket:
            mood_tag = "HAPPY" if closure_mood == "happy" else "SAD"
            combined_remark = mood_tag

            cur2 = conn.cursor()
            # UPDATED QUERY: Added closed_by_user_id field
            cur2.execute(
                """
                UPDATE tickets
                SET status=%s,
                    closed_at=NOW(),
                    closed_remark=%s,
                    closed_by_user_id=%s
                WHERE id=%s
                """,
                ("Closed", combined_remark, me_id, ticket_id),
            )
            cur2.close()
            msg = "Ticket closed."

        conn.commit()

        if _wants_json():
            return jsonify({"ok": True, "message": msg})

        flash(msg, "success")
        if close_ticket:
            return redirect(url_for("closedlist.closed_list"))
        else:
            return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        # Optional: log error
        try:
            from flask import current_app as _ca
            _ca.logger.exception(f"[ticket_quick_remark] failed for ticket_id={ticket_id}: {e}")
        except Exception:
            pass

        if _wants_json():
            return jsonify({"ok": False, "error": "Internal error while saving your update."}), 500

        flash("Something went wrong while saving your update.", "danger")
        return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# ------------------ POST: Shift / Assign ------------------
@ticket_detail_bp.route("/<int:ticket_id>/assign_shift", methods=["POST"])
def ticket_assign_shift(ticket_id: int):
    to_user_id = request.form.get("to_user_id")
    reason = request.form.get("reason")
    remark = request.form.get("remark")

    if not to_user_id:
        if _wants_json():
            return jsonify({"ok": False, "error": "to_user_id is required"}), 400
        flash("Please select a user to shift assignment to.", "danger")
        return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

    conn = get_db_connection()
    cur = conn.cursor(DictCursor)

    # Ensure to_user_id is a CCE user (only customer care allowed)
    cur.execute("""
        SELECT id FROM users
        WHERE id=%s
          AND LOWER(TRIM(status))='active'
          AND (
            LOWER(TRIM(designation))='customer care'
            OR LOWER(TRIM(designation))='customer care executive'
            OR LOWER(TRIM(designation))='cce'
            OR LOWER(TRIM(designation)) LIKE '%%customer care%%'
          )
        LIMIT 1
    """, (to_user_id,))
    cce_ok = cur.fetchone()
    if not cce_ok:
        cur.close()
        conn.close()
        if _wants_json():
            return jsonify({"ok": False, "error": "Only Customer Care users can be assigned."}), 400
        flash("Only Customer Care users can be assigned.", "danger")
        return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

    # --- Determine current assignee ---
    cur.execute("""
        SELECT id, user_id, expires_at
        FROM ticket_claims
        WHERE ticket_id=%s AND is_active=1 AND expires_at > NOW()
        LIMIT 1
    """, (ticket_id,))
    claim_row = cur.fetchone()

    if claim_row:
        from_user_id = claim_row["user_id"]

        # Transfer claim to new user but keep same expiry
        cur.execute("""
            UPDATE ticket_claims
            SET user_id=%s
            WHERE id=%s
        """, (to_user_id, claim_row["id"]))
    else:
        cur.execute("SELECT assign_to_user_id FROM tickets WHERE id=%s", (ticket_id,))
        row = cur.fetchone()
        from_user_id = row["assign_to_user_id"] if row else None

    # --- Update assignment in tickets table ---
    cur2 = conn.cursor()
    cur2.execute(
        """
        UPDATE tickets
        SET assign_to_user_id=%s
        WHERE id=%s
        """,
        (to_user_id, ticket_id),
    )

    # --- Insert history row ---
    cur2.execute(
        """
        INSERT INTO ticket_assign_updates
            (ticket_id, from_user_id, to_user_id, reason, remark, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (ticket_id, from_user_id, to_user_id, reason, remark, _username()),
    )

    conn.commit()
    cur2.close()
    cur.close()
    conn.close()

    if _wants_json():
        return jsonify({"ok": True, "message": "Assignment updated."})
    flash("Assignment updated.", "success")
    return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id))

# ------------------ POST: Tag Update / Add New Tag ------------------

@ticket_detail_bp.route("/<int:ticket_id>/tag_update", methods=["POST"])
def ticket_tag_update(ticket_id: int):
    """
    Modes:
    1) Add new tag:
       - fields: new_staff_id (required), new_reason (optional), new_due (cap enforced)
       - NO remark saved for this path
    2) Update existing tag remark:
       - fields: tag_staff_id (required) + remark (required)
       - Guard: only the same staff can update their own tag (by id; falls back to username->id)
    """
    # --- Preserve current URL/mode on redirect ---
    current_mode = request.args.get("mode") or request.form.get("_redirect_mode") or "full"
    def _back():
        # Prefer going back to the exact page user was on
        ref = request.headers.get("Referer")
        if ref:
            return redirect(ref)
        # Fallback: same detail page with same (or inferred) mode
        return redirect(url_for("ticket_detail.ticket_detail", ticket_id=ticket_id, mode=current_mode))

    tag_staff_id = request.form.get("tag_staff_id")
    new_staff_id = request.form.get("new_staff_id")
    new_reason = request.form.get("new_reason") or None
    new_due = request.form.get("new_due")
    remark = (request.form.get("remark") or "").strip()

    # validate intent
    if not new_staff_id and not (tag_staff_id and remark):
        if _wants_json():
            return jsonify({"ok": False, "error": "Select a tag operation: add new tag OR update existing with remark."}), 400
        flash("Select a tag operation: add new tag OR update existing with remark.", "danger")
        return _back()

    conn = get_db_connection()
    cur = conn.cursor(DictCursor)

    # fetch ticket details for cap check
    cur.execute("SELECT commitment_at, tags_json FROM tickets WHERE id=%s", (ticket_id,))
    r = cur.fetchone()
    tags_json = r["tags_json"] if r else "[]"
    ticket_commitment = r["commitment_at"] if r else None

    # compute cap (minutes) = remaining − 20
    cap_minutes = None
    if ticket_commitment:
        dt = None
        try:
            dt = datetime.datetime.strptime(str(ticket_commitment), "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except Exception:
            iso = _parse_iso_aware(str(ticket_commitment))
            if iso:
                dt = iso.astimezone(IST)
        if dt:
            remaining = int((dt - _now()).total_seconds() // 60)
            cap_minutes = max(0, remaining - 20)

    # 1) add new tag
    if new_staff_id:
        cur.execute("SELECT id, name FROM users WHERE id=%s", (new_staff_id,))
        u = cur.fetchone()
        if not u:
            cur.close(); conn.close()
            msg = "Selected staff not found."
            if _wants_json():
                return jsonify({"ok": False, "error": msg}), 400
            flash(msg, "danger")
            return _back()

        if new_due and str(new_due).isdigit() and cap_minutes is not None:
            if int(new_due) > cap_minutes:
                cur.close(); conn.close()
                msg = f"Selected due exceeds allowed window. Max allowed is {cap_minutes} minutes (Commitment − 20m)."
                if _wants_json():
                    return jsonify({"ok": False, "error": msg}), 400
                flash(msg, "danger")
                return _back()

        dueAt = None
        if new_due and str(new_due).isdigit():
            dueAt = (_now() + datetime.timedelta(minutes=int(new_due))).isoformat(timespec="minutes")

        tag_obj = {
            "staffId": u["id"],
            "staffName": u["name"],
            "dueInMinutes": int(new_due) if (new_due and str(new_due).isdigit()) else None,
            "reason": new_reason,
            "createdAt": _now().isoformat(timespec="minutes"),
            "dueAt": dueAt,
        }

        tags = _safe_json_list(tags_json)
        tags.append(tag_obj)

        cur2 = conn.cursor()
        cur2.execute(
            "UPDATE tickets SET tags_json=%s WHERE id=%s",
            (json.dumps(tags, ensure_ascii=False), ticket_id),
        )
        conn.commit()
        cur2.close()

    # 2) update existing tag remark
    if tag_staff_id and remark:
        # determine current user id; if missing, resolve via username
        me_id = _user_id_int()
        if me_id is None:
            uname = _username()
            if uname:
                try:
                    cur.execute("SELECT id FROM users WHERE name=%s LIMIT 1", (uname,))
                    row = cur.fetchone()
                    if row and row.get("id") is not None:
                        me_id = row["id"]
                except Exception:
                    pass  # if lookup fails, me_id stays None

        if me_id is None or str(me_id) != str(tag_staff_id):
            cur.close(); conn.close()
            msg = "You can only update your own tag."
            if _wants_json():
                return jsonify({"ok": False, "error": msg}), 403
            flash(msg, "danger")
            return _back()

        # history entry for remark
        cur3 = conn.cursor()
        cur3.execute(
            """
            INSERT INTO ticket_tag_updates (ticket_id, staff_id, remark, updated_by)
            VALUES (%s, %s, %s, %s)
            """,
            (ticket_id, tag_staff_id, remark, _username()),
        )
        conn.commit()
        cur3.close()

        # NEW: mark the corresponding tag as 'acknowledged' so list page hides countdown
        try:
            cur4 = conn.cursor(DictCursor)
            cur4.execute("SELECT tags_json FROM tickets WHERE id=%s", (ticket_id,))
            row2 = cur4.fetchone()
            cur4.close()

            tags_list = _safe_json_list(row2["tags_json"] if row2 else "[]")
            ack_ts = _now().isoformat(timespec="minutes")
            changed = False

            for tg in tags_list:
                # prefer staffId match; fallback to name
                if str(tg.get("staffId")) == str(me_id) or _casefold(tg.get("staffName")) == _casefold(_username()):
                    tg["ackedAt"] = ack_ts   # 👈 this flag will suppress countdown in list page
                    changed = True
                    break

            if changed:
                cur5 = conn.cursor()
                cur5.execute(
                    "UPDATE tickets SET tags_json=%s WHERE id=%s",
                    (json.dumps(tags_list, ensure_ascii=False), ticket_id),
                )
                conn.commit()
                cur5.close()
        except Exception as e:
            # non-blocking
            current_app = None  # avoid import cycle in this isolated file
            try:
                from flask import current_app as _ca
                _ca.logger.warning(f"[ticket_tag_update] set ackedAt failed: {e}")
            except Exception:
                pass

    cur.close()
    conn.close()

    if _wants_json():
        # JSON clients can handle UI; still echo what mode we inferred
        return jsonify({"ok": True, "message": "Tag action completed.", "mode": current_mode})

    flash("Tag action completed.", "success")
    return _back()