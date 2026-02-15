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

@auth_bp.before_app_request
def require_login_globally():
    if session.get("user_id"):
        return
    path = (request.path or "/").strip()
    PUBLIC_ENDPOINTS = {"auth.home","auth.login","auth.logout"}
    PUBLIC_PATH_PREFIXES = ("/static/","/suggest_names")
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
                SELECT id, name, designation,
                       COALESCE(
                         DATE_FORMAT(dob, '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%d-%%m-%%Y'), '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%d/%%m/%%Y'), '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%Y-%%m-%%d'), '%%d%%m%%Y'),
                         DATE_FORMAT(STR_TO_DATE(dob, '%%Y/%%m/%%d'), '%%d%%m%%Y')
                       ) AS dob_ddmmyyyy
                FROM users
                WHERE LOWER(TRIM(name)) = %s
                LIMIT 1
            """, (username.lower(),))
            user = cursor.fetchone()
        
        if user and user.get("dob_ddmmyyyy") == password:
            # 🆕 MANUAL OVERRIDE: Aman Shukla 
            designation = user["designation"]
            if user["name"].lower().strip() == "aman shukla":
                designation = "Admin"
            
            session["user_id"] = user["id"]
            session["username"] = user["name"]
            session["designation"] = designation  # 🆕 Overridden designation use karo
            
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
    session.clear()
    return redirect(url_for("auth.home"))