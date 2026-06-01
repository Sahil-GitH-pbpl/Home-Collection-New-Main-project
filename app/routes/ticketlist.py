import json
import pymysql
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Blueprint, render_template, current_app, request, session, redirect, url_for, flash
from app.db.connection import get_db_connection

ticketlist_bp = Blueprint("ticketlist", __name__)
IST = ZoneInfo("Asia/Kolkata")

def _parse_iso_aware(s: str):
    """Parse ISO-8601 string to aware datetime."""
    if not s:
        return None
    try:
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("utf-8", "ignore")
        if isinstance(s, str):
            s = s.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception as e:
        current_app.logger.warning(f"[tickets_list] ISO parse failed: {e} for value={s!r}")
    return None

def _safe_json_to_list(v):
    """Return a list from DB value."""
    try:
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return [v]
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", "ignore")
        if isinstance(v, str):
            x = json.loads(v)
            if isinstance(x, str):
                try:
                    x = json.loads(x)
                except Exception:
                    pass
            if isinstance(x, dict):
                return [x]
            if isinstance(x, list):
                return x
    except Exception as e:
        current_app.logger.warning(f"[tickets_list] tags_json parse error: {e}")
    return []

def _normalize_tags(raw_items):
    """Map incoming shapes to a consistent dict."""
    norm = []
    for it in raw_items:
        staff_id = None
        staff_name = ""
        due_at_dt = None
        created_at_dt = None
        dim_val = None
        acked_at_dt = None

        if isinstance(it, dict):
            staff_id = it.get("staffId") or it.get("staff_id")
            staff_name = (
                it.get("staffName") or it.get("tag_to_name") or 
                it.get("tagToName") or it.get("name") or ""
            )
            text = (
                it.get("text") or staff_name or it.get("tag_name") or 
                it.get("label") or it.get("title") or ""
            )
            due_at = it.get("dueAt")
            created_at = it.get("createdAt")
            dim = it.get("dueInMinutes")
            acked_at = it.get("ackedAt")

            due_at_dt = _parse_iso_aware(due_at) if isinstance(due_at, str) else None
            created_at_dt = _parse_iso_aware(created_at) if isinstance(created_at, str) else None
            acked_at_dt = _parse_iso_aware(acked_at) if isinstance(acked_at, str) else None

            try:
                dim_val = int(dim) if dim is not None else None
            except Exception:
                dim_val = None
        else:
            text = str(it or "")

        norm.append({
            "text": (text or "").strip(),
            "staffId": staff_id,
            "staffName": (staff_name or "").strip(),
            "dueAt": due_at_dt,
            "createdAt": created_at_dt,
            "ackedAt": acked_at_dt,
            "dueInMinutes": dim_val,
        })
    return norm

def _countdown_for_target(target_dt_aware, now_aware):
    """Return (text, state) for minute-granularity countdown."""
    if not target_dt_aware:
        return (None, None)

    if target_dt_aware.tzinfo is None:
        target_dt_aware = target_dt_aware.replace(tzinfo=IST)

    diff = target_dt_aware - now_aware
    secs = diff.total_seconds()

    if secs <= 0:
        over_secs = abs(secs)
        mins = int((over_secs + 59) // 60)
        h = mins // 60
        m = mins % 60
        if h > 0:
            txt = f"Overdue by {h}h {m:02d}m"
        else:
            txt = f"Overdue by {m}m"
        return (txt, "overdue")

    mins = int((secs - 1) // 60)
    h = mins // 60
    m = mins % 60
    txt = f"{h}h {m:02d}m" if h > 0 else f"{m}m"
    state = "ok" if mins > 10 else "near"
    return (txt, state)

def _coerce_int(val):
    try:
        return int(val)
    except Exception:
        return None

def _casefold(s):
    return (s or "").strip().casefold()

def _expire_stale_claims(conn, ticket_id=None):
    """Expire any active claims that have passed expires_at."""
    with conn.cursor() as cur:
        if ticket_id is None:
            cur.execute("""
                UPDATE ticket_claims
                SET is_active=0
                WHERE is_active=1 AND expires_at <= NOW()
            """)
        else:
            cur.execute("""
                UPDATE ticket_claims
                SET is_active=0
                WHERE ticket_id=%s AND is_active=1 AND expires_at <= NOW()
            """, (ticket_id,))
    conn.commit()

def _enrich_ticket_data(ticket, active_claim, now_ist):
    """Enrich ticket data with countdowns and display fields."""
    # Normalize tags
    raw = _safe_json_to_list(ticket.get("tags_json"))
    ticket["tags"] = _normalize_tags(raw)
    
    # Commitment countdown
    dt = ticket.get("commitment_at")
    if dt:
        try:
            dt_aware = dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
            ticket["commitment_at_display"] = dt_aware.strftime("%Y-%m-%d %H:%M")
            ctxt, cstate = _countdown_for_target(dt_aware, now_ist)
            ticket["commitment_countdown_text"] = ctxt
            ticket["commitment_countdown_state"] = cstate
        except Exception:
            ticket["commitment_at_display"] = str(dt)
            ticket["commitment_countdown_text"] = None
            ticket["commitment_countdown_state"] = None
    else:
        ticket["commitment_at_display"] = "-"
        ticket["commitment_countdown_text"] = None
        ticket["commitment_countdown_state"] = None

    # Override with claim data if active claim exists
    if active_claim:
        ticket["assign_to_name_effective"] = active_claim.get("claim_user_name") or ticket.get("assign_to_name")
        exp = active_claim.get("expires_at")
        try:
            exp_aware = exp.replace(tzinfo=IST) if exp and exp.tzinfo is None else (exp.astimezone(IST) if exp else None)
            if exp_aware:
                ticket["commitment_at_display"] = exp_aware.strftime("%Y-%m-%d %H:%M")
                ctxt, cstate = _countdown_for_target(exp_aware, now_ist)
                ticket["commitment_countdown_text"] = ctxt
                ticket["commitment_countdown_state"] = cstate
        except Exception:
            pass
    else:
        ticket["assign_to_name_effective"] = ticket.get("assign_to_name")

    # Tag countdowns
    for tag in ticket["tags"]:
        if tag.get("ackedAt"):
            tag["countdown_text"] = None
            tag["countdown_state"] = None
            continue

        target = tag.get("dueAt")
        if not target:
            ca = tag.get("createdAt")
            dim = tag.get("dueInMinutes")
            if ca and isinstance(dim, int):
                target = ca + timedelta(minutes=dim)
        if target:
            target = target.astimezone(IST)
        txt, state = _countdown_for_target(target, now_ist)
        tag["countdown_text"] = txt
        tag["countdown_state"] = state

    return ticket

def _check_ticket_ownership(ticket, user_id_int, username_cf, active_claim):
    """Check if user owns the ticket through assignment, tags, claim, or creation."""
    # Assignment match
    assign_match = False
    if user_id_int is not None and isinstance(ticket.get("assign_to_user_id"), int):
        assign_match = (ticket.get("assign_to_user_id") == user_id_int)
    if not assign_match and ticket.get("assign_to_name"):
        assign_match = (_casefold(ticket.get("assign_to_name")) == username_cf)

    # Active claim match
    has_active_claim = bool(active_claim and active_claim.get("user_id") == user_id_int)

    # Tag match (only un-acked tags)
    tag_match = False
    for tag in ticket.get("tags", []):
        is_me = False
        if tag.get("staffId") is not None and user_id_int is not None:
            is_me = (_coerce_int(tag.get("staffId")) == user_id_int)
        if not is_me:
            if _casefold(tag.get("staffName")) == username_cf or _casefold(tag.get("text")) == username_cf:
                is_me = True
        if is_me and not tag.get("ackedAt"):
            tag_match = True
            break

    # Creator match
    creator_match = False
    if ticket.get("created_by"):
        creator_match = (_casefold(ticket.get("created_by")) == username_cf)

    return assign_match, has_active_claim, tag_match, creator_match


# ------------------ LIST: /tickets/list ------------------
@ticketlist_bp.route("/tickets/list")
def tickets_list():
    user_id = session.get("user_id")
    username = session.get("username")
    designation = session.get("designation", "").strip()
    if not user_id or not username:
        return redirect(url_for("auth.home"))

    page = max(int(request.args.get("page", 1)), 1)
    page_size = min(max(int(request.args.get("page_size", 25)), 5), 100)
    view_mode = request.args.get("view", "my")
    if view_mode not in ("my", "all"):
        view_mode = "my"

    conn = None
    all_tickets = []
    filtered_tickets = []
    breach_pool = []
    unassigned_odt_tickets = []
    unassigned_rs_tickets = []
    now_ist = datetime.now(IST)
    user_id_int = _coerce_int(user_id)
    username_cf = _casefold(username)

    special_roles = ["Customer Care", "Marketing", "Admin"]
    is_special_user = designation in special_roles

    try:
        conn = get_db_connection()
        _expire_stale_claims(conn)

        if is_special_user:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("""
                    SELECT
                      t.id, t.mobile_number, t.patient_name, t.client_name,
                      t.ticket_category, t.ticket_origin, t.commitment_at,
                      t.assign_to_user_id, u.name AS assign_to_name
                    FROM tickets t
                    LEFT JOIN users u ON u.id = t.assign_to_user_id
                    LEFT JOIN ticket_claims c ON c.ticket_id = t.id AND c.is_active=1 AND c.expires_at > NOW()
                    WHERE (t.status='Open' OR t.status='open' OR t.status IS NULL)
                      AND t.commitment_at IS NOT NULL
                      AND NOW() >= t.commitment_at
                      AND c.ticket_id IS NULL
                      AND (
                        t.ticket_origin = 'CCE'
                        OR (t.ticket_origin = 'ODT' AND t.assign_to_user_id IS NOT NULL)
                        OR t.ticket_origin = 'CVT'
                      )
                    ORDER BY t.commitment_at ASC
                    LIMIT 200
                """)
                breach_pool = cur.fetchall()
        else:
            breach_pool = []

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if is_special_user:
                cur.execute("""
                    SELECT
                      t.id, t.mobile_number, t.patient_name, t.client_name,
                      t.ticket_category, t.ticket_origin, t.commitment_at,
                      t.tags_json, t.assign_to_user_id, u.name AS assign_to_name,
                      t.created_at, t.created_by
                    FROM tickets t
                    LEFT JOIN users u ON t.assign_to_user_id = u.id
                    LEFT JOIN ticket_claims c ON c.ticket_id = t.id AND c.is_active=1 AND c.expires_at > NOW()
                    WHERE (t.status IS NULL OR t.status='open' OR t.status='Open')
                      AND t.ticket_origin = 'ODT'
                      AND t.assign_to_user_id IS NULL
                      AND c.ticket_id IS NULL
                    ORDER BY t.created_at DESC
                """)
            else:
                cur.execute("""
                    SELECT
                      t.id, t.mobile_number, t.patient_name, t.client_name,
                      t.ticket_category, t.ticket_origin, t.commitment_at,
                      t.tags_json, t.assign_to_user_id, u.name AS assign_to_name,
                      t.created_at, t.created_by
                    FROM tickets t
                    LEFT JOIN users u ON t.assign_to_user_id = u.id
                    LEFT JOIN ticket_claims c ON c.ticket_id = t.id AND c.is_active=1 AND c.expires_at > NOW()
                    WHERE (t.status IS NULL OR t.status='open' OR t.status='Open')
                      AND t.ticket_origin = 'ODT'
                      AND t.assign_to_user_id IS NULL
                      AND c.ticket_id IS NULL
                      AND t.designation = %s
                    ORDER BY t.created_at DESC
                """, (designation,))
            unassigned_odt_raw = cur.fetchall()

        # Unassigned RS tickets (special users only)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if is_special_user:
                cur.execute("""
                    SELECT
                      t.id, t.mobile_number, t.patient_name, t.client_name,
                      t.ticket_category, t.ticket_origin, t.commitment_at,
                      t.tags_json, t.assign_to_user_id, u.name AS assign_to_name,
                      t.created_at, t.created_by
                    FROM tickets t
                    LEFT JOIN users u ON t.assign_to_user_id = u.id
                    LEFT JOIN ticket_claims c ON c.ticket_id = t.id AND c.is_active=1 AND c.expires_at > NOW()
                    WHERE (t.status IS NULL OR t.status='open' OR t.status='Open')
                      AND t.ticket_origin = 'RST'
                      AND t.assign_to_user_id IS NULL
                      AND c.ticket_id IS NULL
                    ORDER BY t.created_at DESC
                """)
                unassigned_rs_raw = cur.fetchall()
            else:
                unassigned_rs_raw = []

        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            if is_special_user:
                cursor.execute("""
                    SELECT
                      t.id, t.mobile_number, t.patient_name, t.client_name,
                      t.ticket_category, t.ticket_origin, t.commitment_at,
                      t.tags_json, t.assign_to_user_id, u.name AS assign_to_name,
                      t.created_at, t.created_by
                    FROM tickets t
                    LEFT JOIN users u ON t.assign_to_user_id = u.id
                    WHERE (t.status IS NULL OR t.status='open' OR t.status='Open')
                    ORDER BY t.created_at DESC
                """)
            else:
                cursor.execute("""
                    SELECT
                      t.id, t.mobile_number, t.patient_name, t.client_name,
                      t.ticket_category, t.ticket_origin, t.commitment_at,
                      t.tags_json, t.assign_to_user_id, u.name AS assign_to_name,
                      t.created_at, t.created_by
                    FROM tickets t
                    LEFT JOIN users u ON t.assign_to_user_id = u.id
                    WHERE (t.status IS NULL OR t.status='open' OR t.status='Open')
                      AND t.ticket_origin = 'ODT'
                      AND t.designation = %s
                    ORDER BY t.created_at DESC
                """, (designation,))
            all_tickets = cursor.fetchall()

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT c.ticket_id, c.user_id, c.picked_at, c.expires_at, u.name AS claim_user_name
                FROM ticket_claims c
                LEFT JOIN users u ON u.id = c.user_id
                WHERE c.is_active = 1 AND c.expires_at > NOW()
            """)
            active_claim_rows = cur.fetchall()
        claim_by_ticket = {r["ticket_id"]: r for r in active_claim_rows}

        for ticket in unassigned_odt_raw:
            active_claim = claim_by_ticket.get(ticket.get("id"))
            ticket = _enrich_ticket_data(ticket, active_claim, now_ist)
            
            tags_for_user = []
            for tag in ticket.get("tags", []):
                is_me = False
                if tag.get("staffId") is not None and user_id_int is not None:
                    is_me = (_coerce_int(tag.get("staffId")) == user_id_int)
                if not is_me:
                    if _casefold(tag.get("staffName")) == username_cf or _casefold(tag.get("text")) == username_cf:
                        is_me = True
                if is_me:
                    tags_for_user.append(tag)
            
            ticket["tags_for_user"] = tags_for_user
            ticket["actions_enabled"] = is_special_user
            unassigned_odt_tickets.append(ticket)

        for ticket in unassigned_rs_raw if is_special_user else []:
            active_claim = claim_by_ticket.get(ticket.get("id"))
            ticket = _enrich_ticket_data(ticket, active_claim, now_ist)
            ticket["tags_for_user"] = []
            ticket["actions_enabled"] = is_special_user
            unassigned_rs_tickets.append(ticket)

        for ticket in all_tickets:
            active_claim = claim_by_ticket.get(ticket.get("id"))
            ticket = _enrich_ticket_data(ticket, active_claim, now_ist)
            
            assign_match, has_active_claim, tag_match, creator_match = _check_ticket_ownership(
                ticket, user_id_int, username_cf, active_claim
            )

            if ticket.get("ticket_origin") == "ODT" and ticket.get("assign_to_user_id") is None and not has_active_claim:
                continue

            if view_mode == "my":
                if not (assign_match or tag_match or has_active_claim or creator_match):
                    continue

            tags_for_user = []
            for tag in ticket.get("tags", []):
                is_me = False
                if tag.get("staffId") is not None and user_id_int is not None:
                    is_me = (_coerce_int(tag.get("staffId")) == user_id_int)
                if not is_me:
                    if _casefold(tag.get("staffName")) == username_cf or _casefold(tag.get("text")) == username_cf:
                        is_me = True
                if is_me:
                    tags_for_user.append(tag)
            
            ticket["tags_for_user"] = tags_for_user
            ticket["actions_enabled"] = is_special_user
            filtered_tickets.append(ticket)

        for ticket in breach_pool:
            dt = ticket.get("commitment_at")
            if dt:
                try:
                    dt_aware = dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
                    ticket["commitment_at_display"] = dt_aware.strftime("%Y-%m-%d %H:%M")
                    overdue_text, _ = _countdown_for_target(dt_aware, now_ist)
                    ticket["overdue_text"] = overdue_text
                except Exception:
                    ticket["commitment_at_display"] = str(dt)
                    ticket["overdue_text"] = None
            else:
                ticket["commitment_at_display"] = "-"
                ticket["overdue_text"] = None

    except Exception as e:
        current_app.logger.error(f"[tickets_list] DB Error: {e}")
    finally:
        if conn:
            conn.close()

    total = len(filtered_tickets)
    start = (page - 1) * page_size
    end = start + page_size
    page_tickets = filtered_tickets[start:end]

    return render_template(
        "ticketlist.html",
        breach_pool=breach_pool,
        unassigned_odt_tickets=unassigned_odt_tickets,
        unassigned_rs_tickets=unassigned_rs_tickets,
        tickets=page_tickets,
        page=page,
        page_size=page_size,
        total=total,
        has_prev=(page > 1),
        has_next=(end < total),
        view_mode=view_mode,
        is_special_user=is_special_user,
    )

# ------------------ PICK CCE TICKET: /tickets/pick/<id> ------------------
@ticketlist_bp.route("/tickets/pick/<int:ticket_id>", methods=["POST"])
def pick_ticket(ticket_id: int):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.home"))
    
    user_id_int = _coerce_int(user_id)
    if user_id_int is None:
        current_app.logger.error(f"[pick_ticket] Invalid session user_id={user_id!r}")
        flash("Your login is not linked to an internal numeric user. Please re-login.", "danger")
        return redirect(url_for("ticketlist.tickets_list"))

    conn = None
    try:
        conn = get_db_connection()
        _expire_stale_claims(conn, ticket_id=ticket_id)

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Check for active claim
            cur.execute("""
                SELECT id FROM ticket_claims
                WHERE ticket_id=%s AND is_active=1 AND expires_at > NOW()
                LIMIT 1 FOR UPDATE
            """, (ticket_id,))
            if cur.fetchone():
                flash("Already picked by another user. Try again later.", "warning")
                return redirect(url_for("ticketlist.tickets_list"))

            # Get current assignee
            cur.execute("SELECT assign_to_user_id FROM tickets WHERE id=%s", (ticket_id,))
            row = cur.fetchone()
            from_user_id = row["assign_to_user_id"] if row else None

            # Insert claim
            cur.execute("""
                INSERT INTO ticket_claims (ticket_id, user_id, picked_at, expires_at, is_active, note)
                VALUES (%s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 30 MINUTE), 1, 'Picked from breach pool')
            """, (ticket_id, user_id_int))

            # Update ticket assignment
            cur.execute("""
                UPDATE tickets SET assign_to_user_id=%s, assignment_reason=%s WHERE id=%s
            """, (user_id_int, "Picked from breach pool", ticket_id))

            # Insert history
            cur.execute("""
                INSERT INTO ticket_assign_updates
                (ticket_id, from_user_id, to_user_id, reason, remark, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                ticket_id, from_user_id, user_id_int,
                "Picked from breach pool", None,
                session.get("username") or "System",
            ))

        conn.commit()
        flash("Picked for 30 minutes. You can work on it now.", "success")

    except Exception as e:
        current_app.logger.error(f"[pick_ticket] Error: {e}")
        if conn:
            conn.rollback()
        flash("Failed to pick the ticket. Please try again.", "danger")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("ticketlist.tickets_list"))


# ------------------ PICK ODT TICKET: /tickets/pick-odt/<id> ------------------
@ticketlist_bp.route("/tickets/pick-odt/<int:ticket_id>", methods=["POST"])
def pick_odt_ticket(ticket_id: int):
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("auth.home"))
    
    user_id_int = _coerce_int(user_id)
    if user_id_int is None:
        current_app.logger.error(f"[pick_odt_ticket] Invalid session user_id={user_id!r}")
        flash("Your login is not linked to an internal numeric user. Please re-login.", "danger")
        return redirect(url_for("ticketlist.tickets_list"))

    conn = None
    try:
        conn = get_db_connection()
        
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get current assignee
            cur.execute("SELECT assign_to_user_id FROM tickets WHERE id=%s", (ticket_id,))
            row = cur.fetchone()
            from_user_id = row["assign_to_user_id"] if row else None

            # Update ticket assignment
            cur.execute("""
                UPDATE tickets SET assign_to_user_id=%s, assignment_reason=%s WHERE id=%s
            """, (user_id_int, "Picked from unassigned ODT", ticket_id))

            # Insert history
            cur.execute("""
                INSERT INTO ticket_assign_updates
                (ticket_id, from_user_id, to_user_id, reason, remark, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                ticket_id, from_user_id, user_id_int,
                "Picked from unassigned ODT", None,
                session.get("username") or "System",
            ))

        conn.commit()
        flash("ODT ticket assigned to you successfully.", "success")

    except Exception as e:
        current_app.logger.error(f"[pick_odt_ticket] Error: {e}")
        if conn:
            conn.rollback()
        flash("Failed to pick the ODT ticket. Please try again.", "danger")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("ticketlist.tickets_list"))
