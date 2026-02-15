from flask import Blueprint, request, jsonify
from pymysql.cursors import DictCursor
from app.db.connection import get_db_connection

suggest_bp = Blueprint("suggest", __name__)

@suggest_bp.route("/suggest_names")
def suggest_names():
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify([])

    try:
        conn = get_db_connection()
        with conn.cursor(DictCursor) as cursor:
            like = f"%{q}%"
            cursor.execute("""
                SELECT name FROM users
                WHERE LOWER(name) LIKE %s
                AND status = 'active' 
                ORDER BY name ASC LIMIT 10
            """, (like,))
            rows = cursor.fetchall() or []
            db_names = [r["name"] for r in rows]
            return jsonify(db_names)
    except Exception as e:
        print("suggest_names error:", e)
        return jsonify([])