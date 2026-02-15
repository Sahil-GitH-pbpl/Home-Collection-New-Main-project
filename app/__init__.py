import os
from datetime import timedelta
from flask import Flask, request, session, current_app

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")

    app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.permanent_session_lifetime = timedelta(hours=8)

    BASEDIR = os.path.abspath(os.path.dirname(__file__))
    app.config["UPLOAD_FOLDER"] = os.path.join(BASEDIR, "static", "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    

    from app.routes.auth import auth_bp
    from app.routes.suggest import suggest_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.lead_form import lead_form_bp
    from app.routes.leads_list import leads_list_bp
    from app.routes.lead_detail import lead_detail_bp
    from app.routes.lead_update import lead_update_bp
    from app.routes.lead_history_list import lead_history_list_bp
    from app.routes.lead_history_page import lead_history_page_bp
    from app.routes.my_leads import my_leads_bp
    from app.routes.tickets import tickets_bp
    from app.routes.ticketlist import ticketlist_bp
    from app.routes.closedlist import closedlist_bp
    from app.routes.ticket_detail import ticket_detail_bp
    from app.routes.base import base_bp
    from app.routes.cce import cce_bp
    from app.routes.cce_calls import cce_calls_bp
    from app.routes.failurereport import failurereport_bp
    from app.routes.completedreport import completedreport_bp
    

    app.register_blueprint(auth_bp)
    app.register_blueprint(suggest_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(lead_form_bp)
    app.register_blueprint(leads_list_bp)
    app.register_blueprint(lead_detail_bp)
    app.register_blueprint(lead_update_bp)
    app.register_blueprint(lead_history_list_bp)
    app.register_blueprint(lead_history_page_bp)
    app.register_blueprint(my_leads_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(ticketlist_bp)
    app.register_blueprint(closedlist_bp)
    app.register_blueprint(ticket_detail_bp, url_prefix="/tickets")
    app.register_blueprint(base_bp)
    app.register_blueprint(cce_bp)
    app.register_blueprint(cce_calls_bp)
    app.register_blueprint(failurereport_bp, url_prefix='/failurereport')
    app.register_blueprint(completedreport_bp, url_prefix='/completedreport')

    # ---------- CCE popup globals (available on every template) ----------
    # Default to local Exotel listener port if env not set.
    DEFAULT_EXOTEL_PORT = int(os.getenv("EXOTEL_PORT", "8002"))

    def _resolve_exotel_host():
        return (
            current_app.config.get("EXOTEL_HOST")
            or os.getenv("EXOTEL_HOST")
            or request.host.split("/")[0].split(":")[0]
        )

    def _resolve_exotel_port():
        return int(current_app.config.get("EXOTEL_PORT", DEFAULT_EXOTEL_PORT))

    @app.context_processor
    def inject_cce_globals():
        host = _resolve_exotel_host()
        port = _resolve_exotel_port()

        ws_scheme = "wss" if request.is_secure else "ws"
        ws_url = current_app.config.get("EXOTEL_WS_URL") or f"{ws_scheme}://{host}:{port}"
        http_url = f"http://{host}:{port}"

        return {
            "GLOBAL_WS_URL": ws_url,
            "GLOBAL_EXOTEL_HTTP": http_url,
            "GLOBAL_SESSION_USER_NAME": session.get("username") or "",
            "GLOBAL_SESSION_USER_ID": session.get("user_id") or "",
        }

    return app
