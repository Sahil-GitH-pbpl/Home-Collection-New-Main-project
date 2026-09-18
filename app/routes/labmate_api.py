import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request


labmate_api_bp = Blueprint("labmate_api", __name__)

REPORT_BASE_URL = os.getenv(
    "LABMATE_REPORT_BASE_URL",
    "http://10.1.1.178:8000/downloadnew/PatientReportDirectView.aspx",
).rstrip("/")

SQLSERVER_CONFIG = {
    "server": os.getenv("LABMATE_MSSQL_HOST", "10.1.1.252"),
    "user": os.getenv("LABMATE_MSSQL_USER", "newlabmate"),
    "password": os.getenv("LABMATE_MSSQL_PASSWORD", "newlabmate"),
    "database": os.getenv("LABMATE_MSSQL_DB", "Bhasin_7001"),
    "timeout": int(os.getenv("LABMATE_MSSQL_TIMEOUT", "8")),
}


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y %H:%M:%S")
    return str(value).strip()


def _report_url(patient_id, net_user_pass):
    pid = _clean(patient_id)
    password = _clean(net_user_pass)
    if not pid or not password:
        return ""
    return f"{REPORT_BASE_URL}?UserIDPassword={pid},{password}"


def _connect_sqlserver():
    try:
        import pymssql
    except ImportError as exc:
        raise RuntimeError("pymssql package is required for Labmate SQL Server lookup") from exc
    return pymssql.connect(
        server=SQLSERVER_CONFIG["server"],
        user=SQLSERVER_CONFIG["user"],
        password=SQLSERVER_CONFIG["password"],
        database=SQLSERVER_CONFIG["database"],
        timeout=SQLSERVER_CONFIG["timeout"],
        login_timeout=SQLSERVER_CONFIG["timeout"],
        as_dict=True,
    )


def get_labmate_patient_by_id(patient_id):
    pid = _clean(patient_id)
    if not pid:
        return None

    conn = _connect_sqlserver()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT TOP 1
                    PatientID,
                    Patientname,
                    MobileNo,
                    refbyDr,
                    company,
                    NetUserId,
                    NetUserPass
                FROM dbo.patient
                WHERE PatientID = %s
                ORDER BY Bdate DESC
                """,
                (pid,),
            )
            patient = cur.fetchone()
            if not patient:
                return None

            mobile = _clean(patient.get("MobileNo"))
            cur.execute(
                """
                SELECT TOP 1
                    PhoneNo,
                    WhatsAppFileName,
                    WhatsAppSendTime
                FROM dbo.WhatsAppIODRTable
                WHERE PatientId = %s
                  AND ISNULL(WhatsAppFileName, '') <> ''
                ORDER BY
                  CASE WHEN PhoneNo = %s THEN 0 ELSE 1 END,
                  WhatsAppIODRTableID DESC
                """,
                (pid, mobile),
            )
            report = cur.fetchone() or {}
    finally:
        conn.close()

    filename = _clean(report.get("WhatsAppFileName"))
    net_user_id = _clean(patient.get("NetUserId")) or _clean(patient.get("PatientID"))
    net_user_pass = _clean(patient.get("NetUserPass"))
    return {
        "patientid": _clean(patient.get("PatientID")),
        "patientname": _clean(patient.get("Patientname")),
        "mobileno": mobile,
        "doctor": _clean(patient.get("refbyDr")),
        "doctormobile": "",
        "panel": _clean(patient.get("company")),
        "whatsapp": _clean(report.get("PhoneNo")) or mobile,
        "ordertest": "",
        "net_user_id": net_user_id,
        "net_user_pass": net_user_pass,
        "report_filename": filename,
        "report_url": _report_url(net_user_id, net_user_pass),
        "whatsapp_send_time": _clean(report.get("WhatsAppSendTime")),
    }


@labmate_api_bp.post("/api/proxy/labmate-patient")
def proxy_labmate_patient():
    data = request.get_json(silent=True) or {}
    patient_id = _clean(data.get("patientid"))
    if not patient_id:
        return jsonify({"ok": False, "error": "Patient ID required"}), 400

    try:
        patient = get_labmate_patient_by_id(patient_id)
    except RuntimeError as exc:
        current_app.logger.error("[labmate_api] configuration error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
    except Exception as exc:
        current_app.logger.exception("[labmate_api] patient lookup failed patientid=%s: %s", patient_id, exc)
        return jsonify({"ok": False, "error": "Unable to fetch patient details"}), 500

    if not patient:
        return jsonify({"ok": False, "error": "Patient not found"}), 404

    return jsonify({
        "ok": True,
        "patient": {
            "patientid": patient["patientid"],
            "patientname": patient["patientname"],
            "mobileno": patient["mobileno"],
            "doctor": patient["doctor"],
            "doctormobile": patient["doctormobile"],
            "panel": patient["panel"],
            "whatsapp": patient["whatsapp"],
            "ordertest": patient["ordertest"],
            "net_user_id": patient["net_user_id"],
            "net_user_pass": patient["net_user_pass"],
            "report_url": patient["report_url"],
        },
    })
