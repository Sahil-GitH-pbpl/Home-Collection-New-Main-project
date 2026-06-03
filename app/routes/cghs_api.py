import os
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from app.db.connection import get_db_connection


cghs_api_bp = Blueprint("cghs_api", __name__)

ALLOWED_PANELS = {"nha cghs", "capf ayushman"}
ALLOWED_UPLOAD_PREFIXES = {
    "prescriptions",
    "patient_documents",
    "booking_patient_documents",
    "hc_slip",
}
UPLOAD_ROOT = Path(__file__).resolve().parents[1] / "static" / "uploads"


def _norm(value) -> str:
    return str(value or "").strip()


def _norm_panel(value) -> str:
    return " ".join(_norm(value).lower().split())


def _split_csv(value) -> list[str]:
    text = _norm(value)
    if not text:
        return []
    return [x.strip().lstrip("/") for x in text.split(",") if x.strip()]


def _fmt_dt(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return _norm(value)


def _parse_date_arg(name: str, default_value: date) -> tuple[date | None, str | None]:
    raw = _norm(request.args.get(name))
    if not raw:
        return default_value, None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"Invalid {name}; expected YYYY-MM-DD"


def _check_token():
    token = _norm(os.getenv("CGHS_API_TOKEN"))
    if not token:
        return None
    header = _norm(request.headers.get("Authorization"))
    expected = f"Bearer {token}"
    if header != expected:
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    return None


def _safe_rel_path(*parts: str) -> str:
    cleaned = []
    for part in parts:
        p = _norm(part).replace("\\", "/").strip("/")
        if not p:
            continue
        cleaned.append(p)
    return "/".join(cleaned)


def _file_exists(rel_path: str) -> bool:
    if not rel_path:
        return False
    try:
        full = (UPLOAD_ROOT / rel_path).resolve()
        full.relative_to(UPLOAD_ROOT.resolve())
        return full.is_file()
    except Exception:
        return False


def _add_doc(docs: list[dict], seen: set[str], doc_type: str, doc_scope: str, server_path: str):
    server_path = _safe_rel_path(server_path)
    if not server_path or server_path in seen:
        return
    if not _file_exists(server_path):
        return
    seen.add(server_path)
    docs.append(
        {
            "doc_type": doc_type,
            "doc_scope": doc_scope,
            "server_path": server_path,
        }
    )


def _collect_booking_patient_files(booking_code: str, patient_id: int, docs: list[dict], seen: set[str]):
    folder_rel = _safe_rel_path("booking_patient_documents", booking_code, f"PT{int(patient_id)}")
    folder = UPLOAD_ROOT / folder_rel
    if not folder.is_dir():
        return
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file():
            continue
        name_upper = path.name.upper()
        doc_type = "Photo" if "_PHOTO_" in name_upper else "Booking Document"
        _add_doc(docs, seen, doc_type, "booking", f"{folder_rel}/{path.name}")


def _collect_hc_slip_files(booking_code: str, patient_id: int, docs: list[dict], seen: set[str]):
    folder_rel = _safe_rel_path("hc_slip", booking_code, f"PT{int(patient_id)}")
    folder = UPLOAD_ROOT / folder_rel
    if not folder.is_dir():
        return
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if path.is_file():
            _add_doc(docs, seen, "TRF", "booking", f"{folder_rel}/{path.name}")


def _get_cghs_patients(from_date: date, to_date: date) -> list[dict]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    hcb.id AS booking_id,
                    hcb.booking_code,
                    hcb.preferred_visit_date,
                    hcb.created_at,
                    hcb.assigned_phlebotomist_id,
                    COALESCE(NULLIF(TRIM(u.name), ''), '') AS phlebo_name,
                    hcbp.id AS booking_patient_id,
                    hcbp.patient_id,
                    hcbp.prescription_files,
                    COALESCE(NULLIF(TRIM(hcbp.selected_panel_companies), ''), NULLIF(TRIM(p.panel_company), '')) AS panel_company,
                    COALESCE(NULLIF(TRIM(p.patient_code), ''), CONCAT('PT', p.id)) AS patient_code,
                    NULLIF(TRIM(p.labmate_pid), '') AS labmate_pid,
                    TRIM(CONCAT_WS(' ', p.title, p.full_name)) AS patient_name,
                    p.card_number,
                    p.patient_documents
                FROM hhome_collection_booking hcb
                INNER JOIN hhome_collection_booking_patient hcbp ON hcbp.booking_id = hcb.id
                INNER JOIN hpatient_master p ON p.id = hcbp.patient_id
                LEFT JOIN users u ON u.id = hcb.assigned_phlebotomist_id
                WHERE hcb.preferred_visit_date BETWEEN %s AND %s
                  AND IFNULL(hcb.booking_status, 0) <> 4
                  AND IFNULL(hcbp.booking_patient_status, 0) <> 4
                  AND LOWER(TRIM(COALESCE(NULLIF(TRIM(hcbp.selected_panel_companies), ''), NULLIF(TRIM(p.panel_company), '')))) IN (%s, %s)
                ORDER BY hcb.preferred_visit_date DESC, hcb.id DESC, p.full_name
                """,
                (from_date, to_date, "nha cghs", "capf ayushman"),
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    patients = []
    for row in rows:
        booking_code = _norm(row.get("booking_code")) or f"HC26-{int(row.get('booking_id') or 0)}"
        patient_id = int(row.get("patient_id") or 0)
        docs: list[dict] = []
        seen: set[str] = set()

        for item in _split_csv(row.get("prescription_files")):
            rel = item if item.startswith("prescriptions/") else f"prescriptions/{item}"
            _add_doc(docs, seen, "Prescription", "booking", rel)

        for item in _split_csv(row.get("patient_documents")):
            rel = item if item.startswith("patient_documents/") else f"patient_documents/{item}"
            doc_type = "Card" if "CGHS" in item.upper() else "Patient Document"
            _add_doc(docs, seen, doc_type, "patient", rel)

        if patient_id > 0:
            _collect_booking_patient_files(booking_code, patient_id, docs, seen)
            _collect_hc_slip_files(booking_code, patient_id, docs, seen)

        patients.append(
            {
                "cghs_id": _norm(row.get("card_number")),
                "patient_name": _norm(row.get("patient_name")) or f"Patient {patient_id}",
                "labmate_id": _norm(row.get("labmate_pid")) or _norm(row.get("patient_code")),
                "phlebo_id": str(row.get("assigned_phlebotomist_id") or ""),
                "phlebo_name": _norm(row.get("phlebo_name")),
                "booking_id": int(row.get("booking_id") or 0),
                "booking_patient_id": int(row.get("booking_patient_id") or 0),
                "patient_id": patient_id,
                "booking_code": booking_code,
                "panel_company": _norm(row.get("panel_company")),
                "visit_date": row.get("preferred_visit_date").strftime("%Y-%m-%d") if hasattr(row.get("preferred_visit_date"), "strftime") else _norm(row.get("preferred_visit_date")),
                "created_at": _fmt_dt(row.get("created_at")),
                "docs": docs,
            }
        )
    return patients


@cghs_api_bp.get("/CGHS/recent-uploads")
def cghs_recent_uploads():
    auth_response = _check_token()
    if auth_response:
        return auth_response

    today = date.today()
    from_date, from_err = _parse_date_arg("from", today)
    to_date, to_err = _parse_date_arg("to", today)
    if from_err or to_err:
        return jsonify({"ok": False, "message": from_err or to_err}), 400
    if from_date > to_date:
        return jsonify({"ok": False, "message": "from date cannot be after to date"}), 400

    patients = _get_cghs_patients(from_date, to_date)
    return jsonify({"ok": True, "patients": patients})


@cghs_api_bp.get("/CGHS/upload-stats")
def cghs_upload_stats():
    auth_response = _check_token()
    if auth_response:
        return auth_response

    today = date.today()
    patients_all = _get_cghs_patients(date(1970, 1, 1), date(2999, 12, 31))
    patients_today = _get_cghs_patients(today, today)

    document_counts: dict[str, int] = {}
    phlebo_counts: dict[tuple[str, str], int] = {}
    total_uploads = 0
    today_uploads = 0

    for p in patients_all:
        for doc in p.get("docs") or []:
            total_uploads += 1
            document_counts[doc.get("doc_type") or "Other"] = document_counts.get(doc.get("doc_type") or "Other", 0) + 1
            ph_key = (str(p.get("phlebo_id") or ""), str(p.get("phlebo_name") or ""))
            phlebo_counts[ph_key] = phlebo_counts.get(ph_key, 0) + 1

    for p in patients_today:
        today_uploads += len(p.get("docs") or [])

    document_stats = [{"doc_type": k, "count": v} for k, v in sorted(document_counts.items())]
    phlebo_stats = [
        {"phlebo_id": k[0], "phlebo_name": k[1], "count": v}
        for k, v in sorted(phlebo_counts.items(), key=lambda item: item[1], reverse=True)
    ]
    return jsonify(
        {
            "ok": True,
            "total_uploads": total_uploads,
            "today_uploads": today_uploads,
            "document_stats": document_stats,
            "phlebo_stats": phlebo_stats,
        }
    )


@cghs_api_bp.get("/CGHS/uploads/<path:server_path>")
def cghs_upload_file(server_path: str):
    auth_response = _check_token()
    if auth_response:
        return auth_response

    raw = _norm(server_path).replace("\\", "/")
    if raw.startswith("/") or ".." in raw.split("/"):
        return jsonify({"ok": False, "message": "Invalid file path"}), 400
    first = raw.split("/", 1)[0]
    if first not in ALLOWED_UPLOAD_PREFIXES:
        return jsonify({"ok": False, "message": "File area not allowed"}), 403

    try:
        upload_root = UPLOAD_ROOT.resolve()
        full_path = (upload_root / raw).resolve()
        full_path.relative_to(upload_root)
    except Exception:
        return jsonify({"ok": False, "message": "Invalid file path"}), 400
    if not full_path.is_file():
        return jsonify({"ok": False, "message": "File not found"}), 404
    return send_file(full_path)
