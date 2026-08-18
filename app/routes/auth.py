import json
import os
import urllib.request

import pymysql
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app
from app.db.connection import get_db_connection
from urllib.parse import urlparse, quote

auth_bp = Blueprint("auth", __name__)

def _safe_next_url(raw_next: str) -> str | None:
    if not raw_next:
        return None
    p = urlparse(raw_next)
    if p.scheme or p.netloc:
        return None
    return raw_next if raw_next.startswith("/") else "/" + raw_next


def _client_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For") or ""
    return (forwarded_for.split(",")[0].strip() or request.remote_addr or "").strip()


def _issabel_base_url() -> str:
    host = current_app.config.get("ISSABEL_HOST") or os.getenv("ISSABEL_HOST") or request.host.split(":")[0]
    port = current_app.config.get("ISSABEL_PORT") or os.getenv("ISSABEL_PORT") or "2015"
    return (current_app.config.get("ISSABEL_HTTP_URL") or os.getenv("ISSABEL_HTTP_URL") or f"http://{host}:{port}").rstrip("/")


def _post_issabel_presence(path: str, user_id, user_name: str) -> None:
    payload = {
        "user_id": user_id,
        "user_name": user_name or "",
        "ip_address": _client_ip(),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_issabel_base_url()}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:
        pass
@auth_bp.before_app_request
def require_login_globally():
    if session.get("user_id"):
        return
    path = (request.path or "/").strip()
    PUBLIC_ENDPOINTS = {"auth.home", "auth.login", "auth.logout", "lead_api.create_lead"}
    PUBLIC_PATH_PREFIXES = ("/static/","/suggest_names","/CGHS","/webhook/","/uploads/")
    PUBLIC_PATH_EXACT = {"/favicon.ico","/health"}
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if any(path.startswith(p) for p in PUBLIC_PATH_PREFIXES) or path in PUBLIC_PATH_EXACT:
        return
    if request.method == "OPTIONS":
        return ("", 200)
    next_param = quote(path, safe="")
    return redirect(url_for("auth.home") + f"?next={next_param}")

@auth_bp.route("/")
def home():
    return render_template("login.html")

@auth_bp.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    raw_next = request.args.get("next") or request.form.get("next")
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT id, name, designation, contact,
                       COALESCE(
                         DATE_FORMAT(dob, '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%d-%%m-%%Y'), '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%d/%%m/%%Y'), '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%Y-%%m-%%d'), '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%Y/%%m/%%d'), '%%d%%m%%Y')
                       ) AS dob_ddmmyyyy
                FROM users
                WHERE LOWER(TRIM(name)) = %s
                  AND LOWER(TRIM(status)) = 'active'
                LIMIT 1
            """, (username.lower(),))
            user = cursor.fetchone()
        
        if user and user.get("dob_ddmmyyyy") == password:
            # Manual designation overrides for specific users.
            designation = user["designation"]
            contact_norm = "".join(ch for ch in str(user.get("contact") or "") if ch.isdigit())
            if user["name"].lower().strip() == "aman shukla":
                designation = "Admin"
            if contact_norm == "9821957370":
                designation = "Admin"
            
            session["user_id"] = user["id"]
            session["username"] = user["name"]
            session["designation"] = designation  # 🆕 Overridden designation use karo
            _post_issabel_presence("/presence/login", user["id"], user["name"])
            
            nxt = _safe_next_url(raw_next) or (url_for("dashboard.dashboard") + "#%2Flead-form")
            return redirect(nxt)
    except Exception as e:
        current_app.logger.error(f"[login] DB user flow error: {e}")
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return redirect(url_for("auth.home"))
@auth_bp.route("/logout", methods=["POST"])
def logout():
    user_id = session.get("user_id")
    user_name = session.get("username") or ""
    if user_id or user_name:
        _post_issabel_presence("/presence/logout", user_id, user_name)
    session.clear()
    return redirect(url_for("auth.home"))
