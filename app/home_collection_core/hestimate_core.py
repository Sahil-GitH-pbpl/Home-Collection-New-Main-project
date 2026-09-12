import json
from datetime import datetime
import json

from app.db.connection import get_db_connection


class HEstimateCore:
    def __init__(self):
        pass

    def _norm_text(self, value) -> str:
        if value is None:
            return ""
        return str(value).replace("\x00", "").strip()

    def _json_list(self, value):
        if isinstance(value, list):
            return value
        if isinstance(value, str) and value.strip():
            try:
                loaded = json.loads(value)
                return loaded if isinstance(loaded, list) else []
            except Exception:
                return []
        return []

    def _datetime_text(self, value):
        if isinstance(value, datetime):
            return value.strftime("%d-%m-%Y %I:%M %p")
        return self._norm_text(value)

    def estimate_test_tat_map(self, test_codes):
        return {}

    def _row_to_dict(self, row):
        patients = self._json_list(row.get("patients_json"))
        patients = self._fill_patient_mobiles_from_master(patients)
        tests = self._json_list(row.get("tests_json"))
        patient_names = ", ".join(
            [
                f"{self._norm_text(p.get('patient_name'))} ({self._norm_text(p.get('mobile'))})"
                if self._norm_text(p.get("mobile")) else self._norm_text(p.get("patient_name"))
                for p in patients
                if self._norm_text(p.get("patient_name"))
            ]
        )
        return {
            "id": int(row.get("id") or 0),
            "caller_id": row.get("caller_id"),
            "address_text": self._norm_text(row.get("address_text")),
            "patients": patients,
            "tests": tests,
            "patient_names": patient_names,
            "grand_mrp": float(row.get("grand_mrp") or 0),
            "grand_discount": float(row.get("grand_discount") or 0),
            "grand_total": float(row.get("grand_total") or 0),
            "created_by": row.get("created_by"),
            "created_by_name": self._norm_text(row.get("created_by_name")) or "-",
            "created_at": self._datetime_text(row.get("created_at")),
            "updated_by": row.get("updated_by"),
            "updated_by_name": self._norm_text(row.get("updated_by_name")),
            "updated_at": self._datetime_text(row.get("updated_at")),
        }

    def _fill_patient_mobiles_from_master(self, patients):
        def to_int(value):
            try:
                return int(value or 0)
            except Exception:
                return 0

        missing_ids = [
            to_int(p.get("patient_id"))
            for p in (patients or [])
            if to_int(p.get("patient_id"))
        ]
        if not missing_ids:
            return patients
        placeholders = ",".join(["%s"] * len(missing_ids))
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, contact_mobile FROM hpatient_master WHERE id IN ({placeholders})",
                    tuple(missing_ids),
                )
                mobile_by_id = {int(r.get("id") or 0): self._norm_text(r.get("contact_mobile")) for r in cur.fetchall() or []}
        finally:
            conn.close()
        for patient in patients:
            pid = to_int(patient.get("patient_id"))
            if pid:
                patient["mobile"] = mobile_by_id.get(pid, "")
        return patients

    def _normalize_payload(self, payload):
        patients = self._json_list(payload.get("patients"))
        tests = self._json_list(payload.get("tests"))
        if not patients:
            return None, None, {"ok": False, "message": "Patient data is required"}
        if not tests:
            return None, None, {"ok": False, "message": "At least one test is required"}

        for test in tests:
            code = self._norm_text(test.get("booked_code"))
            test["booked_code"] = code
            test["gender_rule"] = self._norm_text(test.get("gender_rule"))
            test["test_name"] = self._norm_text(test.get("test_name") or test.get("description"))
            test["includes_type"] = self._norm_text(test.get("includes_type") or test.get("includes")) or "Single"
            test["test_tat"] = ""
            test["mrp"] = float(test.get("mrp") or 0)
            test["charge"] = float(test.get("charge") or 0)
            test["max_discount"] = float(test.get("max_discount") or 0)

        patients = self._fill_patient_mobiles_from_master(patients)
        for patient in patients:
            patient["patient_id"] = int(patient.get("patient_id") or patient.get("id") or 0)
            patient["patient_name"] = self._norm_text(patient.get("patient_name") or patient.get("full_name"))
            patient["age"] = self._norm_text(patient.get("age"))
            patient["gender"] = self._norm_text(patient.get("gender"))
            patient["mobile"] = self._norm_text(patient.get("mobile") or patient.get("contact_mobile"))
            patient["panel"] = self._norm_text(patient.get("panel"))
            patient["mode"] = self._norm_text(patient.get("mode"))
            patient["mode_code"] = self._norm_text(patient.get("mode_code"))
            patient["mrp"] = float(patient.get("mrp") or 0)
            patient["discount"] = float(patient.get("discount") or 0)
            patient["total"] = float(patient.get("total") or 0)
        return patients, tests, None

    def save_estimate(self, payload, actor_user_id=None, actor_name=""):
        patients, tests, error = self._normalize_payload(payload or {})
        if error:
            return error
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hestimate_master
                        (caller_id, address_text, patients_json, tests_json,
                         grand_mrp, grand_discount, grand_total, created_by, created_by_name)
                    VALUES
                        (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        payload.get("caller_id") or None,
                        self._norm_text(payload.get("address_text")),
                        json.dumps(patients, ensure_ascii=False),
                        json.dumps(tests, ensure_ascii=False),
                        float(payload.get("grand_mrp") or 0),
                        float(payload.get("grand_discount") or 0),
                        float(payload.get("grand_total") or 0),
                        actor_user_id,
                        self._norm_text(actor_name),
                    ),
                )
                estimate_id = int(cur.lastrowid or 0)
            conn.commit()
            return {"ok": True, "id": estimate_id}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def list_estimates(self, limit=200, estimate_date=None, date_from=None, date_to=None, search=None):
        try:
            limit = max(1, min(int(limit or 200), 500))
        except Exception:
            limit = 200
        date_text = self._norm_text(estimate_date)
        from_text = self._norm_text(date_from)
        to_text = self._norm_text(date_to)
        search_text = self._norm_text(search).lower()
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                params = []
                where_parts = []
                if date_text:
                    where_parts.append("DATE(created_at) = %s")
                    params.append(date_text)
                else:
                    if from_text:
                        where_parts.append("DATE(created_at) >= %s")
                        params.append(from_text)
                    if to_text:
                        where_parts.append("DATE(created_at) <= %s")
                        params.append(to_text)
                where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
                params.append(limit)
                cur.execute(
                    f"""
                    SELECT id, patients_json, grand_total, created_by_name, created_at
                    FROM hestimate_master
                    {where_sql}
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall() or []
            items = [self._row_to_dict(row) for row in rows]
            if search_text:
                items = [
                    item for item in items
                    if search_text in self._norm_text(item.get("patient_names")).lower()
                ]
            return items
        finally:
            conn.close()

    def get_estimate(self, estimate_id):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM hestimate_master WHERE id = %s", (int(estimate_id or 0),))
                row = cur.fetchone()
            if not row:
                return {"ok": False, "message": "Estimate not found"}
            return {"ok": True, "estimate": self._row_to_dict(row)}
        finally:
            conn.close()

    def update_estimate_tests(self, estimate_id, payload, actor_user_id=None, actor_name=""):
        patients, tests, error = self._normalize_payload(payload or {})
        if error:
            return error
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE hestimate_master
                    SET patients_json = %s,
                        tests_json = %s,
                        grand_mrp = %s,
                        grand_discount = %s,
                        grand_total = %s,
                        updated_by = %s,
                        updated_by_name = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        json.dumps(patients, ensure_ascii=False),
                        json.dumps(tests, ensure_ascii=False),
                        float(payload.get("grand_mrp") or 0),
                        float(payload.get("grand_discount") or 0),
                        float(payload.get("grand_total") or 0),
                        actor_user_id,
                        self._norm_text(actor_name),
                        int(estimate_id or 0),
                    ),
                )
                affected = int(cur.rowcount or 0)
            conn.commit()
            if not affected:
                return {"ok": False, "message": "Estimate not found"}
            return {"ok": True, "id": int(estimate_id or 0)}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()
