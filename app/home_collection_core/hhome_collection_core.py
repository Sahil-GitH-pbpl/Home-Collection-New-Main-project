import json
import os
import shutil
import uuid
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta
import re
from threading import Lock, Thread
from pathlib import Path
import urllib.error
import urllib.request

from app.db.connection import get_bhasin7001_connection, get_db_connection
from app.home_collection_core.hcodegen import hage_label, hcalculate_age_parts, hcode_from_id, hto_json

HSYSTEM_USER_ID = 1
HTAG_TYPE_TO_FLAG = {
    "permanent": "allow_in_permanent",
    "transactional": "allow_in_transactional",
    "patient": "allow_in_patient_tag",
}
HFALLBACK_TAGS_BY_TYPE = {
    "permanent": [
        "send senior phlebo",
        "use butterfly needle",
        "dont take pp charges",
        "regular be safe and carefull",
        "special assistance",
        "vip(high priority)",
        "vvip(top priority)",
    ],
    "transactional": [
        "non fasting",
        "dont take pp charges",
        "75g glucose",
        "50 g glucose",
        "100g glucose",
        "first time be careful",
        "New Patient",
        "high value",
        "child collection",
        "Flowrich | Anrich | Bio elite",
        "urgent report",
        "urgent collection",
        "previous complaint delay",
        "previous complaint prick",
    ],
    "patient": [
        "use butterfly needle",
        "special assistance",
    ],
}
TEST_STATUS_PENDING = 0
TEST_STATUS_COMPLETED = 1
TEST_STATUS_DROPPED = 2


class HHomeCollectionCore:
    def __init__(self):
        self._panel_lock = Lock()
        self._panelrate_discount_cache = {}
        self._table_columns_cache = {}
        self._panel_loaded = False
        self._panel_catalog = {
            "panels": [],
            "prefix2": {},
            "groups_by_comp": {},
            "subgroups_by_comp_g": {},
            "tests_by_comp_g_s": {},
            "tests_search_by_comp": {},
            "profile_children_map": {},
            "test_by_testcode1": {},
            "test_by_g_s_testcode": {},
        }
        self.WHATSAPP_ENABLED = False

    def _prescription_root(self) -> Path:
        return Path(__file__).resolve().parents[2] / "app" / "static" / "uploads" / "prescriptions"

    def _pepipost_booking_enabled(self) -> bool:
        return str(os.getenv("PEPIPOST_WA_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}

    def _pepipost_booking_timeout(self) -> int:
        try:
            timeout = int(str(os.getenv("PEPIPOST_WA_TIMEOUT", "8")).strip())
            return timeout if timeout > 0 else 8
        except Exception:
            return 8

    def _pepipost_booking_template(self) -> str:
        return (os.getenv("PEPIPOST_BOOKING_TEMPLATE") or "arpra_booking").strip() or "arpra_booking"

    def _pepipost_assignment_template(self) -> str:
        return (os.getenv("PEPIPOST_ASSIGNMENT_TEMPLATE") or "newassignment").strip() or "newassignment"

    def _normalize_indian_whatsapp_number(self, raw_number) -> str:
        digits = "".join(ch for ch in str(raw_number or "") if ch.isdigit())
        if len(digits) == 10:
            return f"91{digits}"
        if len(digits) == 12 and digits.startswith("91"):
            return digits
        return ""

    def _patient_whatsapp_recipients(self, rows) -> list[str]:
        recipients = []
        seen = set()
        for row in rows or []:
            mobile = self._normalize_indian_whatsapp_number((row or {}).get("contact_mobile"))
            if not mobile or mobile in seen:
                continue
            seen.add(mobile)
            recipients.append(mobile)
        return recipients

    def _assignment_experience_value(self, experience_value) -> str:
        try:
            exp = Decimal(str(experience_value or 0))
        except Exception:
            exp = Decimal("0")

        def _fmt(value: Decimal) -> str:
            value = value.quantize(Decimal("0.01")).normalize()
            return format(value, "f")

        return _fmt(exp)

    def _format_patient_names_for_whatsapp(self, rows) -> str:
        names = []
        for row in rows or []:
            full_name = " ".join(
                [
                    self._norm_code((row or {}).get("title")),
                    self._norm_code((row or {}).get("full_name")),
                ]
            ).strip()
            if full_name:
                names.append(full_name)
        return ", ".join(names)

    def _format_booking_date_for_whatsapp(self, preferred_visit_date) -> str:
        raw = self._norm_code(preferred_visit_date)
        if not raw:
            return ""
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%d-%m-%Y")
            except Exception:
                continue
        return raw

    def _format_booking_address_for_whatsapp(self, snapshot) -> str:
        snap = snapshot or {}
        parts = []
        field_labels = [
            ("house_flat_no", "House/Flat No"),
            ("floor", "Floor"),
            ("block_tower_no", "Block/Tower No"),
            ("street_line", "Street/Sector"),
            ("landmark", "Landmark"),
            ("colony_name", "Colony"),
            ("city", "City"),
        ]
        for key, label in field_labels:
            value = self._norm_code(snap.get(key))
            if value:
                parts.append(f"{label} - {value}")
        return ", ".join(parts)

    def _booking_local_whatsapp_message(self, whatsapp_payload: dict) -> str:
        patient_names = self._norm_code(whatsapp_payload.get("patient_names")) or "-"
        slot = self._norm_code(whatsapp_payload.get("preferred_time_slot")) or "-"
        visit_date = self._norm_code(whatsapp_payload.get("preferred_visit_date")) or "-"
        address = self._norm_code(whatsapp_payload.get("address")) or "-"
        lines = [
            f"Hello {patient_names},",
            "Your home sample collection is confirmed - a responsible step you've taken today towards taking care of your health.",
            "Appointment Details",
            f"Date/Time: {slot} | {visit_date}",
            f"Address: {address}",
            "Thank you for choosing Dr Bhasin's Lab. For over 25 years, we've handled diagnostic testing with the understanding that accuracy directly impacts health decisions.",
            "Every sample represents a person, and is treated with care and responsibility we would show for our loved one.",
            "If any details need correction, please let us know.",
            "With care,",
            "Dr Vishu Bhasin & Dr Vipul Bhasin",
            "Dr Bhasin's Lab",
        ]
        return "\n".join(lines)

    def _assignment_local_whatsapp_message(self, whatsapp_payload: dict) -> str:
        patient_names = self._norm_code(whatsapp_payload.get("patient_names")) or "-"
        phlebo_name = self._norm_code(whatsapp_payload.get("phlebo_name")) or "-"
        phlebo_mobile = self._norm_code(whatsapp_payload.get("phlebo_mobile")) or "-"
        total_exp = self._norm_code(whatsapp_payload.get("total_experience")) or "-"
        lines = [
            f"Hello {patient_names},",
            f"To ensure a safe and hygienic sample collection as part of your health journey, we've assigned {phlebo_name} to visit you.",
            f"His mobile number is {phlebo_mobile}.",
            "",
            f"He is a qualified and trained phlebotomist, with {total_exp} years of experience.",
            "",
            "He is trained to carry out the collection gently and with minimal discomfort, while maintaining strict hygiene and respecting your privacy.",
            "",
            "With care,",
            "Dr Vishu Bhasin & Dr Vipul Bhasin",
            "Dr Bhasin's Lab",
        ]
        return "\n".join(lines)

    def _lookup_panel_ofax_targets(self, patient_rows: list[dict]) -> list[str]:
        panel_names = []
        seen_names = set()
        for row in patient_rows or []:
            panel_name = self._norm_code((row or {}).get("panel_company"))
            key = panel_name.lower()
            if not panel_name or key in seen_names:
                continue
            seen_names.add(key)
            panel_names.append(panel_name)
        if not panel_names:
            return []

        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(panel_names))
                cur.execute(
                    f"""
                    SELECT DISTINCT OFax
                    FROM address
                    WHERE Active=1
                      AND OFax IS NOT NULL
                      AND TRIM(OFax) <> ''
                      AND LOWER(TRIM(pname)) IN ({placeholders})
                    """,
                    tuple(name.lower() for name in panel_names),
                )
                targets = []
                seen = set()
                for row in cur.fetchall() or []:
                    target = self._norm_code(row.get("OFax"))
                    if not target or target in seen:
                        continue
                    seen.add(target)
                    targets.append(target)
                return targets
        finally:
            conn.close()

    def _lookup_patient_ref_by_ofax_targets(self, patient_rows: list[dict]) -> list[str]:
        ref_names = []
        seen_names = set()
        for row in patient_rows or []:
            ref_name = self._norm_code((row or {}).get("ref_by") or (row or {}).get("referred_by"))
            key = ref_name.lower()
            if not ref_name or key in seen_names:
                continue
            seen_names.add(key)
            ref_names.append(ref_name)
        if not ref_names:
            return []

        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(ref_names))
                params = tuple(name.lower() for name in ref_names)
                cur.execute(
                    f"""
                    SELECT DISTINCT OFax
                    FROM address
                    WHERE Active=1
                      AND UPPER(TRIM(Atype))='D'
                      AND OFax IS NOT NULL
                      AND TRIM(OFax) <> ''
                      AND (
                        LOWER(TRIM(pname)) IN ({placeholders})
                        OR LOWER(TRIM(ABARID)) IN ({placeholders})
                      )
                    """,
                    params + params,
                )
                targets = []
                seen = set()
                for row in cur.fetchall() or []:
                    target = self._norm_code(row.get("OFax"))
                    if not target or target in seen:
                        continue
                    seen.add(target)
                    targets.append(target)
                return targets
        finally:
            conn.close()

    def _lookup_internal_ref_contact(self, cur, internal_ref: str | None) -> str:
        value = self._norm_code(internal_ref)
        if not value:
            return ""
        if value.isdigit():
            cur.execute(
                """
                SELECT contact
                FROM users
                WHERE id=%s AND contact IS NOT NULL AND TRIM(contact) <> ''
                LIMIT 1
                """,
                (int(value),),
            )
        else:
            cur.execute(
                """
                SELECT contact
                FROM users
                WHERE status='Active'
                  AND contact IS NOT NULL
                  AND TRIM(contact) <> ''
                  AND LOWER(TRIM(name))=LOWER(TRIM(%s))
                ORDER BY id
                LIMIT 1
                """,
                (value,),
            )
        row = cur.fetchone() or {}
        return self._norm_code(row.get("contact"))

    def _extra_local_whatsapp_targets(self, cur, patient_rows: list[dict], internal_ref: str | None) -> list[str]:
        targets = []
        seen = set()
        for target in self._lookup_panel_ofax_targets(patient_rows):
            if target and target not in seen:
                seen.add(target)
                targets.append(target)
        for target in self._lookup_patient_ref_by_ofax_targets(patient_rows):
            if target and target not in seen:
                seen.add(target)
                targets.append(target)
        internal_contact = self._lookup_internal_ref_contact(cur, internal_ref)
        if internal_contact and internal_contact not in seen:
            seen.add(internal_contact)
            targets.append(internal_contact)
        return targets

    def _queue_local_whatsapp_message(self, app_obj, *, log_prefix: str, booking_id: int, targets: list[str], message: str) -> int:
        deduped = []
        seen = set()
        for target in targets or []:
            clean = self._norm_code(target)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            deduped.append(clean)
        if not deduped or not self._norm_code(message):
            return 0

        def _runner():
            try:
                from app.alerts import send_whatsapp_to_number
                for target in deduped:
                    status, resp = send_whatsapp_to_number(target, message)
                    if app_obj:
                        app_obj.logger.info(
                            "[%s] local send booking_id=%s target=%s status=%s response=%s",
                            log_prefix,
                            booking_id,
                            target,
                            status,
                            (resp[:300] if resp else ""),
                        )
            except Exception as exc:
                if app_obj:
                    app_obj.logger.error("[%s] local send failed booking_id=%s error=%s", log_prefix, booking_id, exc)

        Thread(target=_runner, daemon=True).start()
        return len(deduped)

    def _queue_assignment_local_whatsapp(self, app_obj, assignment_payloads: list[dict]) -> int:
        queued = 0
        seen = set()
        for item in assignment_payloads or []:
            booking_id = int((item or {}).get("booking_id") or 0)
            targets = tuple(self._norm_code(x) for x in ((item or {}).get("_local_whatsapp_targets") or []) if self._norm_code(x))
            if not booking_id or not targets:
                continue
            key = (booking_id, targets)
            if key in seen:
                continue
            seen.add(key)
            queued += self._queue_local_whatsapp_message(
                app_obj,
                log_prefix="hc assignment local whatsapp",
                booking_id=booking_id,
                targets=list(targets),
                message=self._assignment_local_whatsapp_message(item),
            )
        return queued

    def _queue_booking_whatsapp(self, app_obj, whatsapp_payload: dict) -> bool:
        if not app_obj:
            return False

        def _runner():
            self._send_booking_whatsapp(app_obj, whatsapp_payload)

        try:
            Thread(target=_runner, daemon=True).start()
            return True
        except Exception as exc:
            app_obj.logger.error(
                "[hc booking whatsapp] failed booking_id=%s recipient=%s error=%s",
                whatsapp_payload.get("booking_id"),
                whatsapp_payload.get("recipient"),
                exc,
            )
            return False

    def _format_mobile_for_template(self, raw_number) -> str:
        digits = "".join(ch for ch in str(raw_number or "") if ch.isdigit())
        if len(digits) == 12 and digits.startswith("91"):
            return digits[2:]
        return digits

    def _send_template_whatsapp(
        self,
        app_obj,
        *,
        log_prefix: str,
        booking_id: int,
        booking_code: str,
        recipient: str,
        template_name: str,
        attributes: list[str],
    ):
        token = (os.getenv("PEPIPOST_WA_TOKEN") or "").strip()
        if not self._pepipost_booking_enabled():
            app_obj.logger.info("[%s] disabled; skipped booking_id=%s booking_code=%s", log_prefix, booking_id, booking_code)
            return
        if not token:
            app_obj.logger.info("[%s] token missing; skipped booking_id=%s", log_prefix, booking_id)
            return
        if not recipient:
            app_obj.logger.info("[%s] recipient missing; skipped booking_id=%s", log_prefix, booking_id)
            return

        timeout = self._pepipost_booking_timeout()
        request_body = {
            "message": [
                {
                    "recipient_whatsapp": recipient,
                    "message_type": "template",
                    "recipient_type": "individual",
                    "type_template": [
                        {
                            "name": template_name,
                            "attributes": [self._norm_code(x) for x in (attributes or [])],
                            "language": {"locale": "en", "policy": "deterministic"},
                        }
                    ],
                }
            ]
        }
        body = json.dumps(request_body).encode("utf-8")
        req = urllib.request.Request(
            "https://waapi.pepipost.com/api/v2/message/",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
                app_obj.logger.info(
                    "[%s] sent booking_id=%s booking_code=%s recipient=%s status=%s response=%s",
                    log_prefix,
                    booking_id,
                    booking_code,
                    recipient,
                    getattr(resp, "status", ""),
                    response_body,
                )
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            app_obj.logger.error(
                "[%s] failed booking_id=%s recipient=%s error=HTTP %s %s",
                log_prefix,
                booking_id,
                recipient,
                exc.code,
                error_body,
            )
        except Exception as exc:
            app_obj.logger.error(
                "[%s] failed booking_id=%s recipient=%s error=%s",
                log_prefix,
                booking_id,
                recipient,
                exc,
            )

    def _send_booking_whatsapp(self, app_obj, whatsapp_payload: dict):
        self._send_template_whatsapp(
            app_obj,
            log_prefix="hc booking whatsapp",
            booking_id=int(whatsapp_payload.get("booking_id") or 0),
            booking_code=self._norm_code(whatsapp_payload.get("booking_code")),
            recipient=self._norm_code(whatsapp_payload.get("recipient")),
            template_name=self._pepipost_booking_template(),
            attributes=[
                whatsapp_payload.get("patient_names") or "",
                whatsapp_payload.get("preferred_time_slot") or "",
                whatsapp_payload.get("preferred_visit_date") or "",
                whatsapp_payload.get("address") or "",
            ],
        )

    def _send_assignment_whatsapp(self, app_obj, whatsapp_payload: dict):
        self._send_template_whatsapp(
            app_obj,
            log_prefix="hc assignment whatsapp",
            booking_id=int(whatsapp_payload.get("booking_id") or 0),
            booking_code=self._norm_code(whatsapp_payload.get("booking_code")),
            recipient=self._norm_code(whatsapp_payload.get("recipient")),
            template_name=self._pepipost_assignment_template(),
            attributes=[
                whatsapp_payload.get("patient_names") or "",
                whatsapp_payload.get("phlebo_name") or "",
                whatsapp_payload.get("phlebo_mobile") or "",
                whatsapp_payload.get("total_experience") or "",
            ],
        )

    def _queue_assignment_whatsapp(self, app_obj, whatsapp_payload: dict) -> bool:
        if not app_obj:
            return False

        def _runner():
            self._send_assignment_whatsapp(app_obj, whatsapp_payload)

        try:
            Thread(target=_runner, daemon=True).start()
            return True
        except Exception as exc:
            app_obj.logger.error(
                "[hc assignment whatsapp] failed booking_id=%s recipient=%s error=%s",
                whatsapp_payload.get("booking_id"),
                whatsapp_payload.get("recipient"),
                exc,
            )
            return False

    def _patient_document_root(self) -> Path:
        return Path(__file__).resolve().parents[2] / "app" / "static" / "uploads" / "patient_documents"

    def _mirror_upload_roots(self, kind: str) -> list[Path]:
        local_root = Path(__file__).resolve().parents[2]
        apk_root = local_root.parent / "home collection apk backend"
        if kind == "patient_documents":
            rel = Path("app") / "static" / "uploads" / "patient_documents"
        else:
            rel = Path("app") / "static" / "uploads" / "prescriptions"
        roots = [local_root / rel, apk_root / rel]
        uniq = []
        seen = set()
        for p in roots:
            key = str(p.resolve()) if p.exists() else str(p)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(p)
        return uniq

    def _mirror_saved_upload(self, kind: str, relative_path: str, src_file: Path):
        rel = Path(relative_path.replace("\\", "/"))
        for root in self._mirror_upload_roots(kind):
            dst = root / rel
            try:
                if dst.resolve() == src_file.resolve():
                    continue
            except Exception:
                pass
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst)
            except Exception:
                # Mirror failure should not block booking flow.
                pass

    def _prescription_stage_file(self, session) -> Path | None:
        token = (session or {}).get("hprescription_stage_token") if session else None
        if not token:
            return None
        return self._prescription_root() / "_stage" / f"{token}.json"

    def _load_stage_map(self, session) -> dict:
        stage_file = self._prescription_stage_file(session)
        if not stage_file or not stage_file.exists():
            return {}
        try:
            return json.loads(stage_file.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {}

    def _save_stage_map(self, session, data: dict):
        stage_file = self._prescription_stage_file(session)
        if not stage_file:
            return
        stage_file.parent.mkdir(parents=True, exist_ok=True)
        stage_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _clear_stage_map(self, session):
        stage_file = self._prescription_stage_file(session)
        if stage_file and stage_file.exists():
            try:
                stage_file.unlink()
            except Exception:
                pass
        if stage_file:
            try:
                stage_dir = stage_file.parent
                if stage_dir.exists() and stage_dir.is_dir():
                    shutil.rmtree(stage_dir, ignore_errors=True)
            except Exception:
                pass
        if session is not None:
            session.pop("hprescription_stage_token", None)

    def _ensure_stage_token(self, session):
        if session is None:
            return None
        token = (session.get("hprescription_stage_token") or "").strip()
        if not token:
            token = uuid.uuid4().hex
            session["hprescription_stage_token"] = token
        return token

    def _split_prescription_files(self, value) -> list:
        text = self._norm_code(value)
        if not text:
            return []
        return [x.strip() for x in text.split(",") if x.strip()]

    def _split_patient_documents(self, value) -> list:
        text = self._norm_code(value)
        if not text:
            return []
        return [x.strip() for x in text.split(",") if x.strip()]

    def get_patient_prescription_files(self, patient_id: int, booking_id: int | None = None):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if booking_id:
                    cur.execute(
                        """
                        SELECT prescription_files
                        FROM hhome_collection_booking_patient
                        WHERE booking_id=%s AND patient_id=%s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (booking_id, patient_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT prescription_files
                        FROM hhome_collection_booking_patient
                        WHERE patient_id=%s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (patient_id,),
                    )
                row = cur.fetchone()
                return self._split_prescription_files((row or {}).get("prescription_files"))
        finally:
            conn.close()

    def patient_belongs_to_caller(self, caller_id: int, patient_id: int) -> bool:
        if not caller_id or not patient_id:
            return False
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM hcaller_patient_link
                    WHERE caller_id=%s AND patient_id=%s AND is_active=1
                    LIMIT 1
                    """,
                    (caller_id, patient_id),
                )
                return bool(cur.fetchone())
        finally:
            conn.close()

    def append_patient_prescription_files(self, patient_id: int, new_files: list[str], actor_user_id=None, booking_id: int | None = None):
        actor = self._actor(actor_user_id)
        files = [f for f in new_files if self._norm_code(f)]
        if not files:
            return {"ok": True, "files": []}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if booking_id:
                    cur.execute(
                        """
                        SELECT id, prescription_files
                        FROM hhome_collection_booking_patient
                        WHERE booking_id=%s AND patient_id=%s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (booking_id, patient_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, prescription_files
                        FROM hhome_collection_booking_patient
                        WHERE patient_id=%s
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (patient_id,),
                    )
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "message": "Booking patient row not found"}
                existing = self._split_prescription_files((row or {}).get("prescription_files"))
                merged = existing[:]
                for f in files:
                    if f not in merged:
                        merged.append(f)
                cur.execute(
                    """
                    UPDATE hhome_collection_booking_patient
                    SET prescription_files=%s, created_by=%s, pres_up_by=%s
                    WHERE id=%s
                    """,
                    (",".join(merged) or None, actor, f"W,{actor}", int(row.get('id') or 0)),
                )
                conn.commit()
                return {"ok": True, "files": merged}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def get_patient_document_files(self, patient_id: int):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT patient_documents FROM hpatient_master WHERE id=%s", (patient_id,))
                row = cur.fetchone()
                return self._split_patient_documents((row or {}).get("patient_documents"))
        finally:
            conn.close()

    def _save_patient_documents_with_cursor(self, cur, patient_id: int, uploaded_files: list, actor_user_id=None):
        actor = self._actor(actor_user_id)
        files = [f for f in (uploaded_files or []) if f and getattr(f, "filename", "")]
        if not files:
            return {"ok": True, "files": []}

        allowed_exts = {".pdf", ".jpg", ".jpeg", ".png"}
        for file_obj in files:
            orig_name = getattr(file_obj, "filename", "") or ""
            ext = Path(orig_name).suffix.lower()
            if ext not in allowed_exts:
                return {"ok": False, "message": "Only PDF, JPG, JPEG, PNG files are allowed"}

        cur.execute("SELECT patient_documents FROM hpatient_master WHERE id=%s", (patient_id,))
        row = cur.fetchone()
        if not row:
            return {"ok": False, "message": "Patient not found"}

        existing = self._split_patient_documents(row.get("patient_documents"))
        if len(existing) + len(files) > 5:
            return {"ok": False, "message": "Maximum 5 patient documents per patient allowed"}

        folder_name = f"PT{int(patient_id)}"
        final_dir = self._patient_document_root() / folder_name
        final_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        seq = len(existing) + 1
        for file_obj in files:
            ext = Path(getattr(file_obj, "filename", "") or "").suffix.lower()
            final_name = f"PT{int(patient_id)}_DOC_{seq}{ext}"
            final_rel = f"{folder_name}/{final_name}"
            final_path = final_dir / final_name
            file_obj.save(final_path)
            self._mirror_saved_upload("patient_documents", final_rel, final_path)
            saved.append(final_rel)
            seq += 1

        merged = existing + saved
        cur.execute(
            "UPDATE hpatient_master SET patient_documents=%s, updated_by=%s WHERE id=%s",
            (",".join(merged) or None, actor, patient_id),
        )
        return {"ok": True, "files": merged, "saved": saved}

    def append_patient_document_files(self, patient_id: int, uploaded_files: list, actor_user_id=None):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                result = self._save_patient_documents_with_cursor(
                    cur, patient_id, uploaded_files, actor_user_id=actor_user_id
                )
                if not result.get("ok"):
                    conn.rollback()
                    return result
                conn.commit()
                return result
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def stage_patient_prescription_files(self, session, caller_id: int, patient_id: int, uploaded_files: list, actor_user_id=None):
        token = self._ensure_stage_token(session)
        if not token:
            return {"ok": False, "message": "Session token missing"}
        if not self.patient_belongs_to_caller(caller_id, patient_id):
            return {"ok": False, "message": "Patient not linked with selected caller"}

        files = [f for f in (uploaded_files or []) if f]
        if not files:
            return {"ok": False, "message": "No files uploaded"}

        stage_map = self._load_stage_map(session)
        staged = []
        for item in stage_map.get(str(int(patient_id)), []) or []:
            rel = self._norm_code(item.get("rel_name"))
            if rel:
                staged.append(rel)

        if len(staged) + len(files) > 4:
            return {"ok": False, "message": "Maximum 4 prescriptions per patient allowed"}

        root = self._prescription_root()
        stage_dir = root / "_stage" / token / f"patient_{int(patient_id)}"
        stage_dir.mkdir(parents=True, exist_ok=True)

        current_items = stage_map.get(str(int(patient_id)), []) or []
        seq = len(staged) + 1
        allowed_exts = {".pdf", ".jpg", ".jpeg", ".png"}
        saved = []
        for file_obj in files:
            orig_name = getattr(file_obj, "filename", "") or ""
            ext = Path(orig_name).suffix.lower()
            if ext not in allowed_exts:
                return {"ok": False, "message": "Only PDF, JPG, JPEG, PNG files are allowed"}
            tmp_name = f"{uuid.uuid4().hex}{ext}"
            tmp_path = stage_dir / tmp_name
            file_obj.save(tmp_path)
            rel_name = f"staged/{token}/patient_{int(patient_id)}/{tmp_name}"
            current_items.append({
                "tmp_path": str(tmp_path),
                "ext": ext,
                "rel_name": rel_name,
                "seq": seq,
                "orig_name": orig_name,
            })
            saved.append(rel_name)
            seq += 1

        stage_map[str(int(patient_id))] = current_items
        self._save_stage_map(session, stage_map)
        return {"ok": True, "files": saved, "count": len(staged) + len(files)}

    def get_patient_prescription_files_with_stage(self, patient_id: int, session=None):
        db_files = self.get_patient_prescription_files(patient_id)
        if not session:
            return db_files
        stage_map = self._load_stage_map(session)
        staged = []
        for item in stage_map.get(str(int(patient_id)), []) or []:
            rel = self._norm_code(item.get("rel_name"))
            if rel:
                staged.append(rel)
        merged = []
        for item in db_files + staged:
            if item and item not in merged:
                merged.append(item)
        return merged

    def get_patient_staged_prescription_count(self, patient_id: int, session=None) -> int:
        if not session:
            return 0
        stage_map = self._load_stage_map(session)
        count = 0
        for item in stage_map.get(str(int(patient_id)), []) or []:
            rel = self._norm_code((item or {}).get("rel_name"))
            if rel:
                count += 1
        return count

    def get_patient_staged_prescription_items(self, patient_id: int, session=None) -> list[dict]:
        if not session:
            return []
        stage_map = self._load_stage_map(session)
        items = []
        for item in stage_map.get(str(int(patient_id)), []) or []:
            rel = self._norm_code((item or {}).get("rel_name"))
            if not rel:
                continue
            label = self._norm_code((item or {}).get("orig_name"))
            if not label:
                label = rel.split("/")[-1]
            items.append({"rel_name": rel, "label": label})
        return items

    def remove_staged_patient_prescription(self, session, caller_id: int, patient_id: int, rel_name: str):
        if not session:
            return {"ok": False, "message": "Session is required"}
        if not self.patient_belongs_to_caller(caller_id, patient_id):
            return {"ok": False, "message": "Patient not linked with selected caller"}

        rel_target = self._norm_code(rel_name)
        if not rel_target:
            return {"ok": False, "message": "Prescription file is required"}

        stage_map = self._load_stage_map(session)
        patient_key = str(int(patient_id))
        current_items = stage_map.get(patient_key, []) or []
        kept = []
        removed = None
        for item in current_items:
            rel = self._norm_code((item or {}).get("rel_name"))
            if rel == rel_target and removed is None:
                removed = item
                continue
            kept.append(item)

        if removed is None:
            return {"ok": False, "message": "Prescription file not found in current booking"}

        tmp_path = self._norm_code((removed or {}).get("tmp_path"))
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

        if kept:
            stage_map[patient_key] = kept
        else:
            stage_map.pop(patient_key, None)
        self._save_stage_map(session, stage_map)
        return {"ok": True, "count": len(kept)}

    def get_patient_prescription_display_files_with_stage(self, patient_id: int, session=None):
        db_files = self.get_patient_prescription_files(patient_id)
        labels = [self._norm_code(f).split("/")[-1] for f in db_files if self._norm_code(f)]
        if not session:
            return labels
        stage_map = self._load_stage_map(session)
        for item in stage_map.get(str(int(patient_id)), []) or []:
            label = self._norm_code(item.get("orig_name"))
            if not label:
                label = self._norm_code(item.get("rel_name")).split("/")[-1]
            if label and label not in labels:
                labels.append(label)
        return labels

    def merge_staged_prescriptions(self, session, booking_code: str, booking_id: int, actor_user_id=None):
        stage_map = self._load_stage_map(session)
        if not stage_map:
            return {"ok": True, "moved": 0}

        root = self._prescription_root()
        moved = 0
        try:
            for pid_str, items in stage_map.items():
                pid = int(pid_str or 0)
                if pid <= 0:
                    continue
                final_dir = root / booking_code
                final_dir.mkdir(parents=True, exist_ok=True)
                final_names = []
                seq = 1
                for item in items or []:
                    src = item.get("tmp_path")
                    ext = self._norm_code(item.get("ext")).lower()
                    if not src or not os.path.exists(src):
                        continue
                    final_name = f"{booking_code}_PT{pid}_{seq}{ext}"
                    final_rel = f"{booking_code}/{final_name}"
                    final_path = final_dir / final_name
                    shutil.move(src, final_path)
                    self._mirror_saved_upload("prescriptions", final_rel, final_path)
                    final_names.append(final_rel)
                    seq += 1
                    moved += 1
                if final_names:
                    res = self.append_patient_prescription_files(pid, final_names, actor_user_id=actor_user_id, booking_id=booking_id)
                    if not res.get("ok"):
                        return res
            self._clear_stage_map(session)
            return {"ok": True, "moved": moved}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    def _norm_code(self, v) -> str:
        if v is None:
            return ""
        return str(v).replace("\x00", "").strip()

    def _normalize_charge_mode(self, v) -> str:
        raw = self._norm_code(v).upper()
        if not raw:
            return ""
        keep = []
        for ch in raw:
            if ch in ("C", "P", "F") and ch not in keep:
                keep.append(ch)
        ordered = [x for x in ("C", "P", "F") if x in keep]
        return "".join(ordered)

    def _round_charge_amount(self, value) -> float:
        try:
            amount = Decimal(str(value or 0))
        except Exception:
            return 0.0
        return float(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _normalized_paying_pricing(self, mrp_value, max_discount_value) -> tuple[float, float]:
        try:
            mrp_value = round(float(mrp_value or 0), 2)
        except Exception:
            mrp_value = 0.0
        try:
            max_discount_value = round(float(max_discount_value or 0), 2)
        except Exception:
            max_discount_value = 0.0
        charge_value = max(0.0, mrp_value - max_discount_value)
        rounded_charge = self._round_charge_amount(charge_value)
        if rounded_charge > mrp_value:
            rounded_charge = mrp_value
        normalized_discount = round(max(0.0, mrp_value - rounded_charge), 2)
        return rounded_charge, normalized_discount

    def _selected_charge_mode(self, billing: dict) -> str:
        if not isinstance(billing, dict):
            return ""
        mode = self._normalize_charge_mode(
            billing.get("selected_charge_mode")
            or billing.get("charge_mode_code")
            or billing.get("charge_mode")
        )
        if len(mode) > 1:
            return mode[0]
        return mode

    def _truthy_flag(self, value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return int(value) == 1
        return self._norm_code(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _audit_source_context(self, audit_context):
        ctx = audit_context if isinstance(audit_context, dict) else {}
        booking_id = int(ctx.get("booking_id") or 0) or None
        appointment_id = int(ctx.get("appointment_id") or 0) or None
        if appointment_id:
            source_type = "APPOINTMENT_MODIFY"
        elif booking_id:
            source_type = "BOOKING_MODIFY"
        else:
            source_type = "DIRECT"
        return source_type, booking_id, appointment_id

    def _audit_changed_values(self, old_row, new_values):
        old_changed, new_changed = {}, {}
        old_row = old_row or {}
        for field, new_value in (new_values or {}).items():
            old_value = old_row.get(field)
            if hasattr(old_value, "isoformat"):
                old_value = old_value.isoformat()
            old_norm = None if old_value is None else str(old_value)
            new_norm = None if new_value is None else str(new_value)
            if old_norm != new_norm:
                old_changed[field] = old_value
                new_changed[field] = new_value
        return old_changed, new_changed

    def _section_show_mrp(self, section: dict | None) -> bool:
        sec = section or {}
        panel = sec.get("panel") or {}
        billing = sec.get("billing") or {}
        if self._truthy_flag(panel.get("showmrp") if isinstance(panel, dict) else None):
            return True
        if self._truthy_flag(billing.get("showmrp") if isinstance(billing, dict) else None):
            return True

        comp = self._norm_code((billing or {}).get("comp_cat_id"))
        pname = self._norm_code((panel or {}).get("pname")).lower()
        if not comp or not pname:
            return False
        try:
            self.preload_panel_catalog()
            for row in self._panel_catalog.get("panels", []):
                if self._norm_code(row.get("CompCatID")) == comp and self._norm_code(row.get("pname")).lower() == pname:
                    return self._truthy_flag(row.get("showmrp"))
        except Exception:
            return False
        return False

    def _test_status_code(self, v) -> int:
        raw = self._norm_code(v).upper()
        if raw in ("1", "COMPLETED"):
            return TEST_STATUS_COMPLETED
        if raw in ("2", "DROPPED", "CANCELLED"):
            return TEST_STATUS_DROPPED
        return TEST_STATUS_PENDING

    def _test_status_sql(self, column_expr: str = "test_status") -> str:
        col = column_expr.strip() or "test_status"
        return (
            "CASE "
            f"WHEN {col} IS NULL OR TRIM({col})='' THEN 0 "
            f"WHEN UPPER(TRIM({col})) IN ('PENDING','0') THEN 0 "
            f"WHEN UPPER(TRIM({col})) IN ('COMPLETED','1') THEN 1 "
            f"WHEN UPPER(TRIM({col})) IN ('DROPPED','CANCELLED','2') THEN 2 "
            "ELSE 0 END"
        )

    def _table_exists(self, cur, table_name: str) -> bool:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
            LIMIT 1
            """,
            (table_name,),
        )
        return bool(cur.fetchone())

    def _compute_booking_amount_components(
        self,
        tests_meta_map,
        additional_discount_amount: float = 0.0,
        additional_discount_by_patient: dict | None = None,
    ) -> tuple[float, float, float, float, float, float, float, dict]:
        subtotal = 0.0
        credit_subtotal = 0.0
        paying_subtotal = 0.0
        base_discount_total = 0.0
        additional_cap_total = 0.0
        total = 0.0
        meta_map = tests_meta_map or {}
        patient_caps: dict[int, float] = {}
        patient_additional_applied: dict[int, float] = {}
        addl_by_patient = additional_discount_by_patient or {}
        for patient_meta in meta_map.values():
            try:
                pid_int = int((patient_meta or {}).get("patient_id") or 0)
            except Exception:
                pid_int = 0
            for section in self._patient_panel_sections(patient_meta):
                billing = section.get("billing") or {}
                comp_cat_id = billing.get("comp_cat_id")
                selected_charge_mode = self._selected_charge_mode(billing)
                is_paying_mode = selected_charge_mode == "P"
                is_free_mode = selected_charge_mode == "F"
                show_mrp = self._section_show_mrp(section)
                for t in (section.get("selected_tests") or []):
                    try:
                        mrp = float(t.get("mrp") or 0)
                    except Exception:
                        mrp = 0.0
                    try:
                        max_discount = float(t.get("max_discount") or 0)
                    except Exception:
                        max_discount = 0.0
                    try:
                        max_allowed_discount = float(t.get("max_allowed_discount") or 0)
                    except Exception:
                        max_allowed_discount = 0.0
                    if max_allowed_discount <= 0:
                        max_allowed_discount = self._max_allowed_discount_from_panelrates(
                            comp_cat_id,
                            t.get("booked_code"),
                            mrp,
                        )
                    if is_free_mode:
                        mrp = 0.0
                        max_discount = 0.0
                        max_allowed_discount = 0.0
                    elif show_mrp:
                        max_discount = 0.0
                    subtotal += mrp
                    if is_paying_mode:
                        paying_subtotal += mrp
                        base_discount_total += max_discount
                    elif not is_free_mode:
                        credit_subtotal += mrp
                    if is_paying_mode:
                        cap_inc = max(0.0, max_allowed_discount - max_discount)
                        additional_cap_total += cap_inc
                        if pid_int > 0:
                            patient_caps[pid_int] = round(float(patient_caps.get(pid_int) or 0) + cap_inc, 2)
                    final_charge = mrp - (max_discount if is_paying_mode else 0.0)
                    if final_charge < 0:
                        final_charge = 0.0
                    total += final_charge
        additional_applied = 0.0
        if addl_by_patient:
            for raw_pid, raw_val in (addl_by_patient or {}).items():
                try:
                    pid = int(raw_pid)
                except Exception:
                    continue
                cap = float(patient_caps.get(pid) or 0)
                try:
                    asked = float(raw_val or 0)
                except Exception:
                    asked = 0.0
                if asked < 0:
                    asked = 0.0
                applied = min(asked, max(0.0, cap))
                if applied > 0:
                    patient_additional_applied[pid] = round(applied, 2)
                additional_applied += applied
            additional_applied = min(additional_applied, max(0.0, additional_cap_total))
        try:
            addl_discount = float(additional_discount_amount or 0)
        except Exception:
            addl_discount = 0.0
        if addl_discount < 0:
            addl_discount = 0.0
        if not addl_by_patient:
            additional_applied = min(addl_discount, max(0.0, additional_cap_total))
        total_discount = base_discount_total + additional_applied
        total = subtotal - total_discount
        if total < 0:
            total = 0.0
        return (
            round(subtotal, 2),
            round(credit_subtotal, 2),
            round(paying_subtotal, 2),
            round(base_discount_total, 2),
            round(additional_applied, 2),
            round(total_discount, 2),
            round(total, 2),
            {int(k): round(float(v or 0), 2) for k, v in patient_additional_applied.items()},
        )

    def _compute_booking_amount_breakup(self, tests_meta_map, additional_discount_amount: float = 0.0) -> tuple[float, float, float]:
        subtotal, _credit_subtotal, _paying_subtotal, _base_discount_total, _additional_applied, total_discount, total, _patient_addl = self._compute_booking_amount_components(
            tests_meta_map,
            additional_discount_amount,
        )

    def _booking_code_from_id(self, booking_id: int, ref_date=None) -> str:
        """
        Booking code format: HC{YY}-{id}
        Example (2026): HC26-108
        """
        try:
            bid = int(booking_id or 0)
        except Exception:
            bid = 0
        if bid <= 0:
            return ""
        dt = ref_date
        if isinstance(dt, str):
            try:
                dt = datetime.strptime(dt, "%Y-%m-%d").date()
            except Exception:
                dt = None
        if dt is None:
            dt = date.today()
        yy = str(int(dt.year) % 100).zfill(2)
        return f"HC{yy}-{bid}"
        return subtotal, total_discount, total

    def _paying_amount_for_store(self, paying_subtotal: float, base_discount_total: float) -> float:
        """
        Store paying amount same as review UI Paying Amount:
        paying subtotal minus base discount (before additional discount).
        """
        try:
            paying = float(paying_subtotal or 0)
        except Exception:
            paying = 0.0
        try:
            base_dis = float(base_discount_total or 0)
        except Exception:
            base_dis = 0.0
        net_paying = paying - base_dis
        if net_paying < 0:
            net_paying = 0.0
        return round(net_paying, 2)

    def _compute_booking_total_amount(self, tests_meta_map) -> float:
        _subtotal, _discount, total = self._compute_booking_amount_breakup(tests_meta_map)
        return total

    def _compute_patient_final_amounts(self, tests_meta_map, patient_additional_applied: dict | None = None) -> dict[int, float]:
        meta_map = tests_meta_map or {}
        addl_map = patient_additional_applied or {}
        patient_charge_totals: dict[int, float] = {}
        for patient_meta in meta_map.values():
            try:
                pid_int = int((patient_meta or {}).get("patient_id") or 0)
            except Exception:
                pid_int = 0
            if pid_int <= 0:
                continue
            for section in self._patient_panel_sections(patient_meta):
                billing = section.get("billing") or {}
                show_mrp = self._section_show_mrp(section)
                for t in (section.get("selected_tests") or []):
                    _mrp, charge, _max_discount = self._normalized_test_pricing_for_mode(t, billing, show_mrp=show_mrp)
                    patient_charge_totals[pid_int] = round(float(patient_charge_totals.get(pid_int) or 0) + charge, 2)

        result: dict[int, float] = {}
        for pid, charge_total in patient_charge_totals.items():
            try:
                addl = float(addl_map.get(pid) or addl_map.get(str(pid)) or 0)
            except Exception:
                addl = 0.0
            final_amount = charge_total - addl
            if final_amount < 0:
                final_amount = 0.0
            result[int(pid)] = round(final_amount, 2)
        return result

    def _normalized_test_pricing_for_mode(self, test_row: dict | None, billing: dict | None, show_mrp: bool = False) -> tuple[float, float, float]:
        t = test_row or {}
        b = billing or {}
        try:
            mrp = round(float(t.get("mrp") or 0), 2)
        except Exception:
            mrp = 0.0
        try:
            max_discount = round(float(t.get("max_discount") or 0), 2)
        except Exception:
            max_discount = 0.0
        selected_charge_mode = self._selected_charge_mode(b)
        if selected_charge_mode == "F":
            return 0.0, 0.0, 0.0
        if selected_charge_mode == "P":
            if show_mrp:
                return mrp, mrp, 0.0
            charge, normalized_discount = self._normalized_paying_pricing(mrp, max_discount)
            return mrp, charge, normalized_discount
        return mrp, mrp, 0.0

    def _compute_appointment_snapshot_total(self, snapshot_json) -> float:
        try:
            snap = json.loads(snapshot_json) if snapshot_json else {}
        except Exception:
            snap = {}
        if not isinstance(snap, dict):
            return 0.0
        tests_map = snap.get("tests_billing_map") or {}
        pending_map = snap.get("pending_tests_map") or {}
        if not isinstance(tests_map, dict):
            tests_map = {}
        if not isinstance(pending_map, dict):
            pending_map = {}

        grand_total = 0.0
        for pid, tb in tests_map.items():
            panels = (tb or {}).get("panels") or []
            p_tb = pending_map.get(str(pid)) or pending_map.get(pid) or {}
            p_panels = (p_tb or {}).get("panels") or []

            pending_by_parent = {}
            pending_orphans = []
            for ps in p_panels:
                for pt in (ps.get("selected_tests") or []):
                    pcode = self._norm_code(pt.get("parent_booked_code"))
                    if pcode:
                        pending_by_parent.setdefault(pcode, []).append(pt)
                    else:
                        pending_orphans.append(pt)

            for section in panels:
                replaced = []
                used_parents = set()
                for t in (section.get("selected_tests") or []):
                    code = self._norm_code(t.get("booked_code"))
                    repl = pending_by_parent.get(code) or []
                    if repl:
                        replaced.extend(repl)
                        used_parents.add(code)
                    else:
                        replaced.append(t)
                for pcode, arr in pending_by_parent.items():
                    if pcode not in used_parents:
                        replaced.extend(arr or [])
                replaced.extend(pending_orphans)

                seen = set()
                for x in replaced:
                    code = self._norm_code(x.get("booked_code"))
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    try:
                        grand_total += float(x.get("charge") or 0)
                    except Exception:
                        pass
        return round(grand_total, 2)

    def _compute_appointment_selected_tests_total(self, snapshot_json) -> float:
        """Sum only selected tests from tests_billing_map (ignore pending replacement)."""
        try:
            snap = json.loads(snapshot_json) if snapshot_json else {}
        except Exception:
            snap = {}
        if not isinstance(snap, dict):
            return 0.0
        tests_map = snap.get("tests_billing_map") or {}
        if not isinstance(tests_map, dict):
            return 0.0
        total = 0.0
        for tb in (tests_map or {}).values():
            panels = (tb or {}).get("panels") or []
            if not panels and isinstance(tb, dict):
                panels = [{
                    "selected_tests": (tb or {}).get("selected_tests") or [],
                }]
            seen = set()
            for sec in panels:
                for t in (sec.get("selected_tests") or []):
                    code = self._norm_code((t or {}).get("booked_code"))
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    try:
                        total += float((t or {}).get("charge") or 0)
                    except Exception:
                        pass
        return round(total, 2)

    def _row_additional_discount(self, row: dict) -> float:
        if not isinstance(row, dict):
            return 0.0
        raw = (
            row.get("Ad_dis")
            if row.get("Ad_dis") is not None
            else row.get("Ad_Dis")
        )
        try:
            return max(0.0, float(raw or 0))
        except Exception:
            return 0.0

    def _appointment_payment_snapshot_obj(self, raw_value):
        if isinstance(raw_value, dict):
            return raw_value
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value) if isinstance(raw_value, str) else {}
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _appointment_payment_summary(self, raw_value):
        payload = self._appointment_payment_snapshot_obj(raw_value)
        summary = payload.get("summary") if isinstance(payload, dict) else {}
        return summary if isinstance(summary, dict) else {}

    def _appointment_patient_context(self, raw_value):
        payload = self._appointment_payment_snapshot_obj(raw_value)
        ctx = payload.get("patient_context") if isinstance(payload, dict) else {}
        return ctx if isinstance(ctx, dict) else {}

    def _build_appointment_patient_context_from_db(self, cur, booking_id: int, patient_ids, default_status: int = 0, existing_context=None):
        ids = sorted({int(x) for x in (patient_ids or []) if int(x or 0) > 0})
        out = dict(existing_context or {}) if isinstance(existing_context, dict) else {}
        if not ids:
            return out
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"""
            SELECT patient_id, payment_mode, due_amount, extra_amount, additional_discount_amount
            FROM hhome_collection_booking_patient
            WHERE booking_id=%s
              AND patient_id IN ({placeholders})
            """,
            tuple([int(booking_id)] + ids),
        )
        for row in (cur.fetchall() or []):
            pid = int(row.get("patient_id") or 0)
            if pid <= 0:
                continue
            existing = out.get(str(pid)) if isinstance(out.get(str(pid)), dict) else {}
            out[str(pid)] = {
                "appointment_patient_status": int(existing.get("appointment_patient_status") if existing.get("appointment_patient_status") is not None else default_status),
                "booking_due_amount": float(row.get("due_amount") or 0),
                "booking_extra_amount": float(row.get("extra_amount") or 0),
                "booking_payment_mode": self._norm_code(row.get("payment_mode")),
                "booking_additional_discount_amount": float(row.get("additional_discount_amount") or 0),
                "appointment_additional_discount_amount": float(existing.get("appointment_additional_discount_amount") or 0),
            }
        return out

    def _appointment_payment_snapshot_json(self, existing_raw=None, summary=None, patient_context=None):
        payload = self._appointment_payment_snapshot_obj(existing_raw)
        if not isinstance(payload, dict):
            payload = {}
        if not isinstance(payload.get("payments"), list):
            payload["payments"] = []
        if not isinstance(payload.get("payment_screenshots"), dict):
            payload["payment_screenshots"] = {}
        if summary is not None:
            payload["summary"] = summary
        elif not isinstance(payload.get("summary"), dict):
            payload["summary"] = {}
        if patient_context is not None:
            payload["patient_context"] = patient_context if isinstance(patient_context, dict) else {}
        elif not isinstance(payload.get("patient_context"), dict):
            payload["patient_context"] = {}
        return hto_json(payload)

    def _normalize_pending_tests_map_zero_bill(self, pending_tests_map: dict) -> dict:
        if not isinstance(pending_tests_map, dict):
            return {}
        out = {}
        for pid_key, tb in pending_tests_map.items():
            if not isinstance(tb, dict):
                continue
            tb_copy = dict(tb)
            panels = tb_copy.get("panels")
            if isinstance(panels, list):
                fixed_panels = []
                for section in panels:
                    if not isinstance(section, dict):
                        continue
                    sec_copy = dict(section)
                    selected = sec_copy.get("selected_tests")
                    if isinstance(selected, list):
                        fixed_tests = []
                        for t in selected:
                            if not isinstance(t, dict):
                                continue
                            tt = dict(t)
                            tt["charge"] = 0.0
                            tt["mrp"] = 0.0
                            tt["max_discount"] = 0.0
                            tt["max_allowed_discount"] = 0.0
                            tt["pending_carried"] = True
                            fixed_tests.append(tt)
                        sec_copy["selected_tests"] = fixed_tests
                    fixed_panels.append(sec_copy)
                tb_copy["panels"] = fixed_panels
                if fixed_panels:
                    first = fixed_panels[0]
                    tb_copy["panel"] = first.get("panel") or {}
                    tb_copy["billing"] = first.get("billing") or {}
                    tb_copy["selected_tests"] = first.get("selected_tests") or []
            out[str(pid_key)] = tb_copy
        return out

    def _enrich_pending_tests_map_descriptions(self, pending_tests_map: dict) -> dict:
        if not isinstance(pending_tests_map, dict):
            return {}
        self.preload_panel_catalog()
        test_by_code = self._panel_catalog.get("test_by_testcode1") or {}
        out = {}
        for pid_key, tb in pending_tests_map.items():
            if not isinstance(tb, dict):
                continue
            tb_copy = dict(tb)
            panels = tb_copy.get("panels")
            if isinstance(panels, list):
                fixed_panels = []
                for section in panels:
                    if not isinstance(section, dict):
                        continue
                    sec_copy = dict(section)
                    selected = sec_copy.get("selected_tests")
                    if isinstance(selected, list):
                        fixed_tests = []
                        for t in selected:
                            if not isinstance(t, dict):
                                continue
                            tt = dict(t)
                            code = self._norm_code(tt.get("booked_code"))
                            desc = self._norm_code(tt.get("description"))
                            if code and (not desc or desc.upper() == code.upper()):
                                meta = test_by_code.get(code) or {}
                                tt["description"] = self._norm_code(meta.get("description")) or desc or code
                            fixed_tests.append(tt)
                        sec_copy["selected_tests"] = fixed_tests
                    fixed_panels.append(sec_copy)
                tb_copy["panels"] = fixed_panels
                if fixed_panels:
                    first = fixed_panels[0]
                    tb_copy["panel"] = first.get("panel") or {}
                    tb_copy["billing"] = first.get("billing") or {}
                    tb_copy["selected_tests"] = first.get("selected_tests") or []
            out[str(pid_key)] = tb_copy
        return out

    def _normalize_parent_context_map(self, parent_context_map: dict) -> dict:
        if not isinstance(parent_context_map, dict):
            return {}
        out = {}
        for pid_key, tb in parent_context_map.items():
            if not isinstance(tb, dict):
                continue
            tb_copy = dict(tb)
            panels = tb_copy.get("panels")
            if isinstance(panels, list):
                fixed_panels = []
                for section in panels:
                    if not isinstance(section, dict):
                        continue
                    sec_copy = dict(section)
                    selected = sec_copy.get("selected_tests")
                    if isinstance(selected, list):
                        fixed_tests = []
                        for t in selected:
                            if isinstance(t, dict):
                                fixed_tests.append(dict(t))
                        sec_copy["selected_tests"] = fixed_tests
                    else:
                        sec_copy["selected_tests"] = []
                    fixed_panels.append(sec_copy)
                tb_copy["panels"] = fixed_panels
                if fixed_panels:
                    first = fixed_panels[0]
                    tb_copy["panel"] = first.get("panel") or {}
                    tb_copy["billing"] = first.get("billing") or {}
                    tb_copy["selected_tests"] = first.get("selected_tests") or []
            out[str(pid_key)] = tb_copy
        return out

    def _derive_parent_context_map(self, tests_map: dict, pending_map: dict) -> dict:
        if not isinstance(tests_map, dict):
            tests_map = {}
        if not isinstance(pending_map, dict):
            pending_map = {}
        out = {}
        for pid_key in set(tests_map.keys()) | set(pending_map.keys()):
            pid = str(pid_key or "").strip()
            if not pid:
                continue
            pnode = pending_map.get(pid_key) or pending_map.get(pid) or {}
            parent_codes = []
            parent_meta = {}
            direct_parent = self._norm_code(((pnode.get("parent") or {}).get("booked_code")))
            if direct_parent:
                parent_codes.append(direct_parent)
                parent_meta[direct_parent] = self._norm_code(((pnode.get("parent") or {}).get("test_name"))) or direct_parent
            pending_tests = []
            if isinstance((pnode or {}).get("selected_tests"), list):
                pending_tests.extend((pnode or {}).get("selected_tests") or [])
            if isinstance((pnode or {}).get("panels"), list):
                for sec in ((pnode or {}).get("panels") or []):
                    if isinstance(sec, dict):
                        pending_tests.extend(sec.get("selected_tests") or [])
            if isinstance((pnode or {}).get("items"), list):
                for item in ((pnode or {}).get("items") or []):
                    if isinstance(item, dict):
                        pending_tests.extend(item.get("pending") or [])
            seen_parent = set(parent_codes)
            for t in pending_tests:
                root_code = self._norm_code((t or {}).get("root_booked_code"))
                if root_code and root_code not in seen_parent:
                    parent_codes.append(root_code)
                    seen_parent.add(root_code)
                    parent_meta[root_code] = self._norm_code((t or {}).get("root_test_name")) or root_code

            if not parent_codes:
                continue

            tnode = tests_map.get(pid_key) or tests_map.get(pid) or {}
            panel_sections = (tnode.get("panels") or []) if isinstance(tnode, dict) else []
            if not panel_sections and isinstance(tnode, dict):
                panel_sections = [{
                    "panel": tnode.get("panel") or {},
                    "billing": tnode.get("billing") or {},
                    "selected_tests": tnode.get("selected_tests") or [],
                }]
            panel_index = {}
            sections = []
            existing_codes = set()
            for sec in panel_sections:
                if not isinstance(sec, dict):
                    continue
                panel = sec.get("panel") or {}
                billing = sec.get("billing") or {}
                selected_tests = sec.get("selected_tests") or []
                matched = []
                for t in selected_tests:
                    code = self._norm_code((t or {}).get("booked_code"))
                    if code and code in parent_codes and code not in existing_codes:
                        matched.append(dict(t))
                        existing_codes.add(code)
                if not matched:
                    continue
                key = "|".join([
                    self._norm_code(panel.get("pname")),
                    self._norm_code(billing.get("comp_cat_id")),
                    self._norm_code(billing.get("selected_charge_mode") or billing.get("charge_mode_code")),
                ])
                panel_index[key] = len(sections)
                sections.append({
                    "panel": dict(panel),
                    "billing": dict(billing),
                    "selected_tests": matched,
                })
            for code in parent_codes:
                if code in existing_codes:
                    continue
                if not sections:
                    sections.append({
                        "panel": {"pname": ""},
                        "billing": {"comp_cat_id": "", "selected_charge_mode": ""},
                        "selected_tests": [],
                    })
                sections[0]["selected_tests"].append(
                    {
                        "booked_code": code,
                        "description": parent_meta.get(code) or code,
                        "charge": 0.0,
                        "mrp": 0.0,
                        "max_discount": 0.0,
                        "max_allowed_discount": 0.0,
                    }
                )
            if sections:
                out[pid] = {
                    "panels": sections,
                    "panel": sections[0].get("panel") or {},
                    "billing": sections[0].get("billing") or {},
                    "selected_tests": sections[0].get("selected_tests") or [],
                }
        return self._normalize_parent_context_map(out)

    def _fetch_test_descriptions_by_code1(self, codes: set[str]) -> dict[str, str]:
        code_list = [self._norm_code(c) for c in (codes or set()) if self._norm_code(c)]
        if not code_list:
            return {}
        out = {}
        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                placeholders = ",".join(["%s"] * len(code_list))
                cur.execute(
                    f"""
                    SELECT Testcode1, Description
                    FROM test
                    WHERE Testcode1 IN ({placeholders})
                    """,
                    code_list,
                )
                for row in (cur.fetchall() or []):
                    code = self._norm_code((row or {}).get("Testcode1"))
                    desc = self._norm_code((row or {}).get("Description"))
                    if code and desc:
                        out[code] = desc
        except Exception:
            return {}
        finally:
            conn.close()
        return out

    def _extract_selected_codes_map(self, tests_meta_map) -> dict[str, list[str]]:
        out = {}
        meta_map = tests_meta_map or {}
        for pid_key, patient_meta in meta_map.items():
            pid = str(pid_key)
            rows = []
            for section in self._patient_panel_sections(patient_meta):
                billing = section.get("billing") or {}
                comp_cat_id = self._norm_code(billing.get("comp_cat_id"))
                for t in (section.get("selected_tests") or []):
                    code = self._norm_code(t.get("booked_code"))
                    if not code:
                        continue
                    try:
                        mrp = round(float(t.get("mrp") or 0), 2)
                    except Exception:
                        mrp = 0.0
                    try:
                        max_discount = round(float(t.get("max_discount") or 0), 2)
                    except Exception:
                        max_discount = 0.0
                    try:
                        charge = round(float(t.get("charge") or 0), 2)
                    except Exception:
                        charge = 0.0
                    rows.append(f"{comp_cat_id}|{code}|{mrp}|{max_discount}|{charge}")
            out[pid] = sorted(rows)
        return out

    def _all_tests_missing_max_allowed_discount(self, tests_meta_map) -> bool:
        meta_map = tests_meta_map or {}
        found_any = False
        for patient_meta in meta_map.values():
            for section in self._patient_panel_sections(patient_meta):
                for t in (section.get("selected_tests") or []):
                    found_any = True
                    try:
                        mad = float(t.get("max_allowed_discount") or 0)
                    except Exception:
                        mad = 0.0
                    if mad > 0:
                        return False
        return found_any

    def _should_recalculate_on_modify_save(self, payload: dict, tests_meta_map) -> bool:
        session_ref = payload.get("_session_ref") if isinstance(payload, dict) else None
        ctx = ((session_ref or {}).get("hmodify_context") or {}) if isinstance(session_ref, dict) else {}
        old_tests_map = ctx.get("tests_billing_map") or {}
        old_codes = self._extract_selected_codes_map(old_tests_map)
        new_codes = self._extract_selected_codes_map(tests_meta_map or {})
        tests_changed = old_codes != new_codes
        old_addl = 0.0
        try:
            old_addl = float(((ctx.get("appointment") or {}).get("additional_discount_amount")) or 0)
        except Exception:
            old_addl = 0.0
        try:
            new_addl = float((payload or {}).get("additional_discount_amount") or 0)
        except Exception:
            new_addl = 0.0
        addl_changed = round(old_addl, 2) != round(new_addl, 2)
        if not (tests_changed or addl_changed):
            return False
        return True

    def _max_allowed_discount_from_panelrates(self, comp_cat_id, booked_code, mrp_value: float) -> float:
        comp = self._norm_code(comp_cat_id)
        code = self._norm_code(booked_code)
        if not comp or not code:
            return 0.0
        try:
            mrp_num = float(mrp_value or 0)
        except Exception:
            mrp_num = 0.0
        if mrp_num <= 0:
            return 0.0
        cache_key = (comp, code, round(mrp_num, 4))
        if cache_key in self._panelrate_discount_cache:
            return self._panelrate_discount_cache[cache_key]

        # Fast path: resolve from in-memory preloaded catalog to avoid per-test DB hits.
        try:
            self.preload_panel_catalog()
            rows = (self._panel_catalog.get("tests_search_by_comp", {}) or {}).get(comp, []) or []
            matched = [r for r in rows if self._norm_code(r.get("booked_code")) == code]
            if matched:
                selected = None
                if mrp_num > 0:
                    for r in matched:
                        try:
                            if abs(float(r.get("mrp") or 0) - mrp_num) < 0.0001:
                                selected = r
                                break
                        except Exception:
                            continue
                if selected is None:
                    selected = matched[0]
                value = round(float(selected.get("max_allowed_discount") or 0), 2)
                self._panelrate_discount_cache[cache_key] = value
                return value
        except Exception:
            pass

        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                gcode = ""
                scode = ""
                test_code = ""
                m_full = re.match(r"^(G\d{2})?(S\d{2})?(T\d+)$", code, flags=re.IGNORECASE)
                if m_full:
                    gcode = (m_full.group(1) or "").upper()
                    scode = (m_full.group(2) or "").upper()
                    test_code = (m_full.group(3) or "").upper()
                if not test_code:
                    m_tail = re.search(r"(T\d+)$", code, flags=re.IGNORECASE)
                    if m_tail:
                        test_code = m_tail.group(1).upper()

                pct = 0.0
                if not (gcode and scode and test_code):
                    return 0.0

                cur.execute(
                    """
                    SELECT MaximumpercentageAllowed
                    FROM panelrates
                    WHERE CompCatID=%s
                      AND BookedFlag=1
                      AND COALESCE(Active, 0)=1
                      AND GCode=%s
                      AND SCode=%s
                      AND TestCode=%s
                      AND ABS(COALESCE(MRP,0) - %s) < 0.0001
                    LIMIT 1
                    """,
                    (comp, gcode, scode, test_code, mrp_num),
                )
                exact_row = cur.fetchone() or {}
                pct = float(exact_row.get("MaximumpercentageAllowed") or 0)

                if pct <= 0:
                    cur.execute(
                        """
                        SELECT MAX(MaximumpercentageAllowed) AS MaximumpercentageAllowed
                        FROM panelrates
                        WHERE CompCatID=%s
                          AND BookedFlag=1
                          AND COALESCE(Active, 0)=1
                          AND GCode=%s
                          AND SCode=%s
                          AND TestCode=%s
                        """,
                        (comp, gcode, scode, test_code),
                    )
                    scoped_row = cur.fetchone() or {}
                    pct = float(scoped_row.get("MaximumpercentageAllowed") or 0)
                if pct <= 0:
                    return 0.0
                value = round((mrp_num * pct) / 100.0, 2)
                self._panelrate_discount_cache[cache_key] = value
                return value
        except Exception:
            return 0.0
        finally:
            conn.close()

    def _recalculate_followup_required(self, cur, booking_id: int):
        cur.execute(
            f"""
            SELECT COUNT(*) AS pending_count
            FROM hhome_collection_booking_patient_test
            WHERE booking_id=%s
              AND {self._test_status_sql('test_status')}=0
            """,
            (booking_id,),
        )
        pending_count = int((cur.fetchone() or {}).get("pending_count") or 0)
        return pending_count

    def _next_appointment_no(self, cur, booking_id: int) -> int:
        cur.execute(
            "SELECT COALESCE(MAX(appointment_no), 0) AS max_no FROM hhome_collection_booking_appointment WHERE booking_id=%s",
            (booking_id,),
        )
        max_no = int((cur.fetchone() or {}).get("max_no") or 0)
        return max_no + 1

    def _patient_ids_to_json(self, patient_ids: list[int]) -> str:
        cleaned = sorted({int(x) for x in (patient_ids or []) if int(x or 0) > 0})
        return hto_json(cleaned)

    def _patient_ids_from_json(self, raw) -> list[int]:
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return sorted({int(x) for x in data if int(x or 0) > 0})

    def _patient_panel_sections(self, patient_meta: dict) -> list:
        if not isinstance(patient_meta, dict):
            return []
        panels = patient_meta.get("panels")
        if isinstance(panels, list):
            return [p for p in panels if isinstance(p, dict)]
        return [
            {
                "panel": patient_meta.get("panel") or {},
                "billing": patient_meta.get("billing") or {},
                "selected_tests": patient_meta.get("selected_tests") or [],
            }
        ]

    def _patient_tbs_code(self, patient_meta: dict):
        if not isinstance(patient_meta, dict):
            return None
        raw = patient_meta.get("cce_level_tbs")
        if raw is None:
            raw = patient_meta.get("cce_level_TBS")
        code_map = {
            1: "Test confirmed and booked",
            2: "Prescription attached but test not booked",
            3: "No test information: ask to patient for tests",
            4: "Incompleted test, phlebo verification pending to confirm and book",
        }
        try:
            iv = int(raw)
            if iv in code_map:
                return code_map[iv]
        except Exception:
            pass
        txt = self._norm_code(raw)
        if not txt:
            return None
        allowed = {
            "Test confirmed and booked",
            "Prescription attached but test not booked",
            "No test information: ask to patient for tests",
            "Incompleted test, phlebo verification pending to confirm and book",
        }
        return txt if txt in allowed else None

    def _patient_tbs_value_for_save(self, patient_meta: dict):
        return self._patient_tbs_code(patient_meta)

    def _patient_panel_meta_csv(self, patient_meta: dict) -> tuple[str, str, str]:
        comp_ids = []
        charge_modes = []
        panel_names = []
        for section in self._patient_panel_sections(patient_meta):
            panel = section.get("panel") or {}
            billing = section.get("billing") or {}
            comp = self._norm_code(billing.get("comp_cat_id"))
            mode = self._selected_charge_mode(billing)
            pname = self._norm_code(panel.get("pname"))
            if not comp:
                continue
            comp_ids.append(comp)
            charge_modes.append(mode)
            panel_names.append(pname)
        return ",".join(comp_ids), ",".join(charge_modes), ",".join(panel_names)

    def _patient_cat_details_csv(self, patient_meta: dict) -> str:
        details = []
        for section in self._patient_panel_sections(patient_meta):
            billing = section.get("billing") or {}
            cat_details = self._norm_code(billing.get("cat_details"))
            details.append(cat_details)
        return ",".join(details)

    def _patient_panel_entries_from_row(self, row: dict) -> list[dict]:
        if not isinstance(row, dict):
            return []
        comp_ids = [self._norm_code(x) for x in self._norm_code(row.get("selected_comp_cat_ids")).split(",")]
        modes = [self._normalize_charge_mode(x) for x in self._norm_code(row.get("selected_charge_modes")).split(",")]
        names = [self._norm_code(x) for x in self._norm_code(row.get("selected_panel_companies")).split(",")]
        details = [self._norm_code(x) for x in self._norm_code(row.get("selected_cat_details")).split(",")]
        max_len = max(len(comp_ids), len(modes), len(names), len(details), 0)
        entries = []
        for i in range(max_len):
            comp = comp_ids[i] if i < len(comp_ids) else ""
            if not comp:
                continue
            entries.append({
                "comp_cat_id": comp,
                "selected_charge_mode": modes[i] if i < len(modes) else "",
                "panel_name": names[i] if i < len(names) else "",
                "cat_details": details[i] if i < len(details) else "",
            })
        return entries

    def _panel_name_from_patient_row(self, row: dict, comp_cat_id: str, panel_name_hint: str = "") -> str:
        if not isinstance(row, dict):
            return ""
        comp = self._norm_code(comp_cat_id)
        hint = self._norm_code(panel_name_hint).lower()
        mode_hint = self._normalize_charge_mode(row.get("selected_charge_mode") or row.get("charge_mode"))
        entries = self._patient_panel_entries_from_row(row)
        if comp and entries:
            matches = [e for e in entries if e.get("comp_cat_id") == comp]
            if hint:
                for entry in matches:
                    if self._norm_code(entry.get("panel_name")).lower() == hint:
                        return entry.get("panel_name") or ""
            if mode_hint:
                for entry in matches:
                    if self._normalize_charge_mode(entry.get("selected_charge_mode")) == mode_hint:
                        return entry.get("panel_name") or ""
            if matches:
                return matches[0].get("panel_name") or ""
        return entries[0].get("panel_name") if entries else ""

    def _charge_mode_from_patient_row(self, row: dict, comp_cat_id: str, panel_name_hint: str = "") -> str:
        if not isinstance(row, dict):
            return ""
        direct_mode = self._selected_charge_mode({"selected_charge_mode": row.get("selected_charge_mode") or row.get("charge_mode")})
        if direct_mode:
            return direct_mode
        comp = self._norm_code(comp_cat_id)
        hint = self._norm_code(panel_name_hint).lower()
        entries = self._patient_panel_entries_from_row(row)
        if comp and entries:
            matches = [e for e in entries if e.get("comp_cat_id") == comp]
            if hint:
                for entry in matches:
                    if self._norm_code(entry.get("panel_name")).lower() == hint:
                        return entry.get("selected_charge_mode") or ""
            if matches:
                return matches[0].get("selected_charge_mode") or ""
        return entries[0].get("selected_charge_mode") if entries else ""

    def _cat_details_from_patient_row(self, row: dict, comp_cat_id: str, panel_name_hint: str = "") -> str:
        if not isinstance(row, dict):
            return ""
        comp = self._norm_code(comp_cat_id)
        hint = self._norm_code(panel_name_hint).lower()
        entries = self._patient_panel_entries_from_row(row)
        if comp and entries:
            matches = [e for e in entries if e.get("comp_cat_id") == comp]
            if hint:
                for entry in matches:
                    if self._norm_code(entry.get("panel_name")).lower() == hint:
                        return entry.get("cat_details") or ""
            if matches:
                return matches[0].get("cat_details") or ""
        return entries[0].get("cat_details") if entries else ""

    def _validate_prescription_required_for_tbs(self, cur, selected_patient_ids: list[int], tests_meta_map: dict, session_ref=None):
        for pid in selected_patient_ids:
            patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
            tbs_code = self._patient_tbs_code(patient_meta)
            if tbs_code != "Prescription attached but test not booked":
                continue
            existing_count = len(self.get_patient_prescription_files_with_stage(pid, session=session_ref))
            if existing_count > 0:
                continue
            patient_name = f"Patient {pid}"
            try:
                cur.execute("SELECT full_name FROM hpatient_master WHERE id=%s LIMIT 1", (pid,))
                prow = cur.fetchone() or {}
                patient_name = self._norm_code(prow.get("full_name")) or patient_name
            except Exception:
                pass
            return {
                "ok": False,
                "message": f"{patient_name} prescription upload is pending...",
            }
        return {"ok": True}

    def _validate_patient_test_duplicates(self, pid: int, panel_sections: list):
        seen = {}
        for section in panel_sections:
            panel_name = self._norm_code((section.get("panel") or {}).get("pname"))
            for t in section.get("selected_tests") or []:
                booked_code = self._norm_code(t.get("booked_code"))
                if not booked_code:
                    continue
                if booked_code in seen:
                    return {
                        "ok": False,
                        "message": f"Test {booked_code} already selected for patient {pid} in {seen[booked_code] or 'another panel'}",
                    }
                seen[booked_code] = panel_name
        return {"ok": True}

    def _find_recent_web_booking_duplicate_id(
        self,
        cur,
        *,
        caller_id: int,
        preferred_visit_date: str,
        preferred_time_slot: str,
        created_by: int,
        window_minutes: int = 10,
    ) -> int:
        cur.execute(
            """
            SELECT id
            FROM hhome_collection_booking
            WHERE caller_id = %s
              AND created_by = %s
              AND preferred_visit_date = %s
              AND preferred_time_slot = %s
              AND COALESCE(booking_status, 0) <> 4
              AND created_at >= (NOW() - INTERVAL %s MINUTE)
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                int(caller_id or 0),
                int(created_by or 0),
                preferred_visit_date,
                preferred_time_slot,
                int(window_minutes or 10),
            ),
        )
        row = cur.fetchone() or {}
        return int((row or {}).get("id") or 0)

    def _normalize_wa_target(self, phone: str) -> str:
        digits = re.sub(r"\D", "", str(phone or ""))
        if not digits:
            return ""
        if len(digits) == 10:
            return "91" + digits
        if len(digits) == 11 and digits.startswith("0"):
            return "91" + digits[1:]
        if len(digits) == 12 and digits.startswith("91"):
            return digits
        return digits

    def preload_panel_catalog(self, force: bool = False):
        """Load panel/company + billing + GST tree once per server start."""
        with self._panel_lock:
            if self._panel_loaded and not force:
                return

            conn = get_bhasin7001_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT
                            a.CenterID,
                            a.pname,
                            a.category AS CompCatID,
                            cc.CatDetails,
                            a.BillingChargeMode,
                            COALESCE(a.Active, 0) AS Active,
                            COALESCE(a.showmrp, 0) AS showmrp,
                            COALESCE(a.showinHC, 0) AS showinHC,
                            a.Atype
                        FROM address a
                        LEFT JOIN compcategory cc ON cc.CompCatID = a.category
                        WHERE a.pname IS NOT NULL
                          AND TRIM(a.pname) <> ''
                          AND UPPER(TRIM(a.Atype)) = 'C'
                        ORDER BY a.pname
                        """
                    )
                    panel_rows = cur.fetchall()

                    cur.execute("SELECT Gcode, Description FROM groupmaster")
                    group_name = {self._norm_code(r["Gcode"]): self._norm_code(r.get("Description")) for r in cur.fetchall()}

                    cur.execute("SELECT Gcode, Scode, Description FROM subgroup")
                    subgroup_name = {
                        (self._norm_code(r["Gcode"]), self._norm_code(r["Scode"])): self._norm_code(r.get("Description"))
                        for r in cur.fetchall()
                    }

                    cur.execute(
                        """
                        SELECT Gcode, Scode, TestCode, Testcode1, Test, Description, Shortname, Profile, SpecimenID
                        FROM test
                        """
                    )
                    test_rows = cur.fetchall()
                    cur.execute(
                        """
                        SELECT test_code, tat_raw, tat_normalized
                        FROM test_tat_master
                        """
                    )
                    tat_by_code = {
                        self._norm_code(r.get("test_code")): {
                            "tat_raw": self._norm_code(r.get("tat_raw")),
                            "tat_normalized": self._norm_code(r.get("tat_normalized")),
                        }
                        for r in cur.fetchall()
                        if self._norm_code(r.get("test_code"))
                    }
                    cur.execute(
                        """
                        SELECT SpecimenID, SpName
                        FROM testspecimen
                        """
                    )
                    specimen_name_by_id = {}
                    for sr in cur.fetchall():
                        sid = sr.get("SpecimenID")
                        if sid is None:
                            continue
                        try:
                            specimen_name_by_id[int(sid)] = self._norm_code(sr.get("SpName"))
                        except Exception:
                            continue

                    test_by_gst = {}
                    test_by_code1 = {}
                    for r in test_rows:
                        g = self._norm_code(r.get("Gcode"))
                        s = self._norm_code(r.get("Scode"))
                        tc = self._norm_code(r.get("TestCode"))
                        tc1 = self._norm_code(r.get("Testcode1"))
                        desc = self._norm_code(r.get("Description"))
                        shortname = self._norm_code(r.get("Shortname"))
                        is_profile = int(r.get("Profile") or 0) == 1
                        try:
                            specimen_id = int(r.get("SpecimenID")) if r.get("SpecimenID") is not None else None
                        except Exception:
                            specimen_id = None
                        base = {
                            "gcode": g,
                            "scode": s,
                            "test_code": tc,
                            "testcode1": tc1,
                            "gender_rule": self._norm_code(r.get("Test")),
                            "description": desc,
                            "shortname": shortname,
                            "is_profile": is_profile,
                            "specimen_id": specimen_id,
                            "specimen_name": specimen_name_by_id.get(specimen_id, ""),
                        }
                        test_by_gst[(g, s, tc)] = base
                        if tc1:
                            test_by_code1[tc1] = base

                    cur.execute(
                        """
                        SELECT Gcode, SCode, ProfileCode, TestCode
                        FROM testprofile
                        """
                    )
                    testprofile_rows = cur.fetchall()

                    cur.execute(
                        """
                        SELECT
                            CompCatID, CenterID, GCode, SCode, TestCode, CTestCode, CTestName,
                            MRP, PercentageOnStandard, MaximumpercentageAllowed
                        FROM panelrates
                        WHERE BookedFlag = 1
                          AND COALESCE(Active, 0) = 1
                        """
                    )
                    rate_rows = cur.fetchall()
            finally:
                conn.close()

            panels = []
            prefix2 = {}
            panel_map = {}
            for r in panel_rows:
                pname = self._norm_code(r.get("pname"))
                if not pname:
                    continue
                comp_cat = self._norm_code(r.get("CompCatID"))
                key = (pname.lower(), comp_cat)
                mode = self._normalize_charge_mode(r.get("BillingChargeMode"))
                if key not in panel_map:
                    atype = self._norm_code(r.get("Atype")).upper()
                    panel = {
                        "CenterID": r.get("CenterID"),
                        "pname": pname,
                        "CompCatID": r.get("CompCatID"),
                        "CatDetails": self._norm_code(r.get("CatDetails")),
                        "BillingChargeMode": mode,
                        "Active": 1 if int(r.get("Active") or 0) else 0,
                        "showmrp": 1 if int(r.get("showmrp") or 0) else 0,
                        "showinHC": 1 if int(r.get("showinHC") or 0) else 0,
                        "_has_c": atype == "C",
                        "_has_d": atype == "D",
                        "_pname_lc": pname.lower(),
                    }
                    panel_map[key] = panel
                    panels.append(panel)
                    p2 = panel["_pname_lc"][:2]
                    prefix2.setdefault(p2, []).append(panel)
                else:
                    atype = self._norm_code(r.get("Atype")).upper()
                    if atype == "C":
                        panel_map[key]["_has_c"] = True
                    elif atype == "D":
                        panel_map[key]["_has_d"] = True
                    if mode:
                        existing = panel_map[key].get("BillingChargeMode") or ""
                        merged = self._normalize_charge_mode(existing + mode)
                        panel_map[key]["BillingChargeMode"] = merged
                    if int(r.get("showmrp") or 0):
                        panel_map[key]["showmrp"] = 1
                    if int(r.get("showinHC") or 0):
                        panel_map[key]["showinHC"] = 1

            groups_by_comp = {}
            subgroups_by_comp_g = {}
            tests_by_comp_g_s = {}
            tests_search_by_comp = {}
            profile_children_map = {}
            seen_tests = set()

            for r in rate_rows:
                cc = self._norm_code(r.get("CompCatID"))
                center = self._norm_code(r.get("CenterID"))
                g = self._norm_code(r.get("GCode"))
                s = self._norm_code(r.get("SCode"))
                if not cc or not g:
                    continue
                rate_keys = [cc]
                if center:
                    rate_keys.insert(0, f"{cc}|{center}")
                try:
                    mrp_value = float(r.get("MRP") or 0)
                except Exception:
                    mrp_value = 0.0
                try:
                    pct_on_standard = float(r.get("PercentageOnStandard") or 0)
                except Exception:
                    pct_on_standard = 0.0
                try:
                    max_pct_allowed = float(r.get("MaximumpercentageAllowed") or 0)
                except Exception:
                    max_pct_allowed = 0.0
                calc_discount = round((mrp_value * pct_on_standard) / 100.0, 2) if mrp_value > 0 else 0.0
                max_allowed_discount = round((mrp_value * max_pct_allowed) / 100.0, 2) if mrp_value > 0 else 0.0
                calc_final_charge, calc_discount = self._normalized_paying_pricing(mrp_value, calc_discount)

                gname = group_name.get(g, "")
                for rate_key in rate_keys:
                    groups_by_comp.setdefault(rate_key, {})
                    groups_by_comp[rate_key][g] = {"gcode": g, "description": gname}
                    if s:
                        sname = subgroup_name.get((g, s), "")
                        subgroups_by_comp_g.setdefault((rate_key, g), {})
                        subgroups_by_comp_g[(rate_key, g)][s] = {"scode": s, "description": sname}

                panel_test_code = self._norm_code(r.get("TestCode"))
                panel_ctest_code = self._norm_code(r.get("CTestCode"))
                panel_ctest_name = self._norm_code(r.get("CTestName"))

                meta = test_by_gst.get((g, s, panel_test_code)) if s and panel_test_code else None
                if not meta and panel_ctest_code:
                    meta = test_by_code1.get(panel_ctest_code)

                test_code = self._norm_code((meta or {}).get("test_code")) or panel_test_code
                testcode1 = self._norm_code((meta or {}).get("testcode1")) or panel_ctest_code
                description = self._norm_code((meta or {}).get("description")) or panel_ctest_name
                booked_code = testcode1 or test_code
                if not booked_code:
                    continue
                tat_info = tat_by_code.get(booked_code) or tat_by_code.get(testcode1) or tat_by_code.get(test_code) or {}

                group_desc = self._norm_code(group_name.get(g, ""))
                subgroup_desc = self._norm_code(subgroup_name.get((g, s), ""))
                has_children = bool(profile_children_map.get((g, s, self._norm_code(test_code))))
                search_item = {
                    "gcode": g,
                    "scode": s,
                    "test_code": test_code,
                    "testcode1": testcode1,
                    "booked_code": booked_code,
                    "gender_rule": self._norm_code((meta or {}).get("gender_rule")),
                    "description": description,
                    "description_lc": description.lower(),
                    "shortname": self._norm_code((meta or {}).get("shortname")),
                    "shortname_lc": self._norm_code((meta or {}).get("shortname")).lower(),
                    "group_description": group_desc,
                    "subgroup_description": subgroup_desc,
                    "charge": calc_final_charge,
                    "mrp": mrp_value,
                    "max_discount": calc_discount,
                    "max_allowed_discount": max_allowed_discount,
                    "tat_raw": tat_info.get("tat_raw", ""),
                    "tat_normalized": tat_info.get("tat_normalized", ""),
                    "is_profile": bool((meta or {}).get("is_profile")),
                    "has_children": has_children,
                }
                for rate_key in rate_keys:
                    t_key = (rate_key, g, s, booked_code)
                    if t_key in seen_tests:
                        continue
                    seen_tests.add(t_key)
                    tests_search_by_comp.setdefault(rate_key, []).append(dict(search_item))
                    tests_by_comp_g_s.setdefault((rate_key, g, s), []).append(dict(search_item))

            for r in testprofile_rows:
                g = self._norm_code(r.get("Gcode"))
                s = self._norm_code(r.get("SCode"))
                profile_code = self._norm_code(r.get("ProfileCode"))
                child_testcode1 = self._norm_code(r.get("TestCode"))
                if not g or not s or not profile_code or not child_testcode1:
                    continue
                child = test_by_code1.get(child_testcode1)
                if not child:
                    continue
                key = (g, s, profile_code)
                profile_children_map.setdefault(key, [])
                profile_children_map[key].append(
                    {
                        "gcode": child.get("gcode") or g,
                        "scode": child.get("scode") or s,
                        "test_code": child.get("test_code") or "",
                        "testcode1": child.get("testcode1") or child_testcode1,
                        "booked_code": child.get("testcode1") or child.get("test_code") or child_testcode1,
                        "gender_rule": self._norm_code(child.get("gender_rule")),
                        "description": child.get("description") or "",
                        "shortname": child.get("shortname") or "",
                        "is_profile": bool(child.get("is_profile")),
                    }
                )

            for key, rows in profile_children_map.items():
                uniq = {}
                for x in rows:
                    ukey = (
                        self._norm_code(x.get("gcode")),
                        self._norm_code(x.get("scode")),
                        self._norm_code(x.get("test_code")),
                        self._norm_code(x.get("testcode1")),
                    )
                    uniq[ukey] = x
                profile_children_map[key] = sorted(
                    uniq.values(),
                    key=lambda x: (x.get("test_code") or "", x.get("testcode1") or "", x.get("booked_code") or ""),
                )

            for key, arr in tests_by_comp_g_s.items():
                for item in arr:
                    parent_key = (
                        self._norm_code(item.get("gcode")),
                        self._norm_code(item.get("scode")),
                        self._norm_code(item.get("test_code")),
                    )
                    item["has_children"] = bool(profile_children_map.get(parent_key))
                arr.sort(
                    key=lambda x: (
                        x.get("test_code") or "",
                        x.get("testcode1") or "",
                        x.get("booked_code") or "",
                    )
                )

            self._panel_catalog = {
                "panels": panels,
                "prefix2": prefix2,
                "groups_by_comp": groups_by_comp,
                "subgroups_by_comp_g": subgroups_by_comp_g,
                "tests_by_comp_g_s": tests_by_comp_g_s,
                "tests_search_by_comp": tests_search_by_comp,
                "profile_children_map": profile_children_map,
                "test_by_testcode1": test_by_code1,
                "test_by_g_s_testcode": test_by_gst,
            }
            self._panel_loaded = True

    def refresh_panel_catalog(self):
        self._panelrate_discount_cache = {}
        self.preload_panel_catalog(force=True)

    def _actor(self, actor_user_id=None) -> int:
        try:
            uid = int(actor_user_id or 0)
            if uid > 0:
                return uid
        except Exception:
            pass
        return HSYSTEM_USER_ID

    def _parse_raw_tags(self, raw_tag) -> list[str]:
        if raw_tag is None:
            return []
        if isinstance(raw_tag, list):
            raw_items = raw_tag
        else:
            raw_items = str(raw_tag).split(",")
        out = []
        for item in raw_items:
            txt = self._norm_code(item)
            if txt:
                out.append(txt)
        return out

    def _tag_rows_from_master(self, tag_type: str, query: str = "", limit: int = 20) -> list[dict]:
        tag_key = self._norm_code(tag_type).lower()
        flag_col = HTAG_TYPE_TO_FLAG.get(tag_key)
        if not flag_col:
            return []
        like_text = self._norm_code(query)
        safe_limit = max(1, min(int(limit or 20), 50))

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if like_text:
                    cur.execute(
                        f"""
                        SELECT tag_name
                        FROM tag_master
                        WHERE is_active = 1
                          AND {flag_col} = 1
                          AND LOWER(TRIM(tag_name)) LIKE LOWER(%s)
                        ORDER BY tag_name
                        LIMIT %s
                        """,
                        (f"%{like_text}%", safe_limit),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT tag_name
                        FROM tag_master
                        WHERE is_active = 1
                          AND {flag_col} = 1
                        ORDER BY tag_name
                        LIMIT %s
                        """,
                        (safe_limit,),
                    )
                return cur.fetchall() or []
        except Exception:
            return [{"tag_name": x} for x in HFALLBACK_TAGS_BY_TYPE.get(tag_key, [])]
        finally:
            conn.close()

    def search_tag_master(self, tag_type: str, q: str, limit: int = 20) -> list[dict]:
        query = self._norm_code(q)
        if len(query) < 2:
            return []
        return self._tag_rows_from_master(tag_type=tag_type, query=query, limit=limit)

    def list_tag_master(self, tag_type: str, limit: int = 500) -> list[dict]:
        return self._tag_rows_from_master(tag_type=tag_type, query="", limit=limit)

    def sanitize_tags_by_type(self, raw_tag, tag_type: str) -> str:
        requested = self._parse_raw_tags(raw_tag)
        if not requested:
            return ""

        allowed_rows = self._tag_rows_from_master(tag_type=tag_type, query="", limit=500)
        allowed_map = {}
        for row in allowed_rows:
            raw_name = self._norm_code((row or {}).get("tag_name"))
            if raw_name:
                allowed_map[raw_name.lower()] = raw_name

        if not allowed_map:
            return ""

        seen = set()
        filtered = []
        for item in requested:
            key = item.lower()
            canonical = allowed_map.get(key)
            if tag_type == "permanent" and not canonical:
                # Allow dynamic phlebo preference tags created from UI picker.
                if re.match(r"^(prefered|preferred)\s+.+\s+phlebo$", item, flags=re.IGNORECASE):
                    canonical = item
                elif re.match(r"^avoid\s+.+\s+phlebo$", item, flags=re.IGNORECASE):
                    canonical = item
            if canonical and key not in seen:
                seen.add(key)
                filtered.append(canonical)
        return ",".join(filtered)

    def sanitize_patient_tags(self, raw_tag) -> str:
        return self.sanitize_tags_by_type(raw_tag, "patient")

    def sanitize_permanent_tags(self, raw_tag) -> str:
        return self.sanitize_tags_by_type(raw_tag, "permanent")

    def sanitize_transactional_tags(self, raw_tag) -> str:
        return self.sanitize_tags_by_type(raw_tag, "transactional")

    def _merge_tag_csv(self, existing_raw, incoming_raw) -> str:
        merged = []
        seen = set()
        for item in self._parse_raw_tags(existing_raw) + self._parse_raw_tags(incoming_raw):
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return ",".join(merged)

    def _with_rescheduled_tag(self, existing_raw) -> str:
        return self._merge_tag_csv(existing_raw, "rescheduled")

    def _selected_patient_ids(self, selected_patients) -> list[int]:
        out = []
        seen = set()
        for item in selected_patients or []:
            try:
                pid = int((item or {}).get("patient_id") or 0)
            except Exception:
                pid = 0
            if pid > 0 and pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out

    def _has_child_patient(self, cur, patient_ids: list[int]) -> bool:
        ids = [int(x) for x in (patient_ids or []) if int(x or 0) > 0]
        if not ids:
            return False
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"""
            SELECT id, age_years, date_of_birth
            FROM hpatient_master
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )
        today = date.today()
        for row in cur.fetchall() or []:
            age = None
            try:
                if row.get("age_years") is not None:
                    age = int(row.get("age_years") or 0)
            except Exception:
                age = None
            if age is None or age <= 0:
                dob = row.get("date_of_birth")
                if dob:
                    try:
                        if isinstance(dob, str):
                            dob = datetime.strptime(dob[:10], "%Y-%m-%d").date()
                        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                    except Exception:
                        age = None
            if age is not None and age <= 12:
                return True
        return False

    def _auto_transactional_tags(
        self,
        cur,
        *,
        caller_id,
        selected_patients,
        total_amount,
        existing_tags,
        include_new_patient: bool,
    ) -> str:
        auto_tags = []
        if include_new_patient:
            auto_tags.append("New Patient")
        if float(total_amount or 0) >= 10000:
            auto_tags.append("High Value")
        if self._has_child_patient(cur, self._selected_patient_ids(selected_patients)):
            auto_tags.append("Child Collection")
        patient_ids = self._selected_patient_ids(selected_patients)
        if self._has_flowrich_anrich_bioelite_patient_panel(cur, patient_ids):
            auto_tags.append("Flowrich | Anrich | Bio elite")
        return self._merge_tag_csv(existing_tags, ",".join(auto_tags))

    def _is_flowrich_anrich_bioelite_name(self, raw_name) -> bool:
        target_names = {
            "flowrich pharma pvt ltd",
            "anrich pharma",
            "bio elite",
        }
        name = self._norm_code(raw_name).strip().rstrip(".").lower()
        return name in target_names

    def _has_flowrich_anrich_bioelite_patient_panel(self, cur, patient_ids: list[int]) -> bool:
        ids = [int(x) for x in (patient_ids or []) if int(x or 0) > 0]
        if not ids:
            return False
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"""
            SELECT panel_company
            FROM hpatient_master
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )
        return any(self._is_flowrich_anrich_bioelite_name((row or {}).get("panel_company")) for row in (cur.fetchall() or []))

    def _linked_patient_ids_for_caller(self, cur, caller_id) -> list[int]:
        try:
            cid = int(caller_id or 0)
        except Exception:
            cid = 0
        if cid <= 0:
            return []
        cur.execute(
            """
            SELECT DISTINCT patient_id
            FROM hcaller_patient_link
            WHERE caller_id=%s
              AND is_active=1
            """,
            (cid,),
        )
        return sorted(
            [
                int(r.get("patient_id") or 0)
                for r in (cur.fetchall() or [])
                if int(r.get("patient_id") or 0) > 0
            ]
        )

    def normalize_mobile(self, mobile: str) -> str:
        digits = re.sub(r"\D", "", (mobile or "").strip())
        if not digits:
            return ""
        if len(digits) > 10:
            digits = digits[-10:]
        return digits

    def _booking_status_label(self, status_code: int) -> str:
        mapping = {
            0: "Pending",
            1: "Assigned",
            2: "Started",
            3: "Completed",
            4: "Cancelled",
        }
        try:
            return mapping.get(int(status_code), "Unknown")
        except Exception:
            return "Unknown"

    def _slot_end_dt(self, visit_date_value, slot_text: str):
        visit_date = None
        if isinstance(visit_date_value, date):
            visit_date = visit_date_value
        else:
            txt = self._norm_code(visit_date_value)
            if txt:
                try:
                    visit_date = datetime.strptime(txt, "%Y-%m-%d").date()
                except Exception:
                    visit_date = None
        if not visit_date:
            return None

        slot_raw = self._norm_code(slot_text).upper().replace(".", "")
        if not slot_raw:
            return None
        slot_raw = re.sub(r"\s+", " ", slot_raw).strip()
        tokens = re.findall(r"\d{1,2}:\d{2}\s*[AP]M", slot_raw)
        if not tokens:
            return None

        def _parse_time(x):
            val = re.sub(r"\s+", " ", x).strip()
            try:
                return datetime.strptime(val, "%I:%M %p").time()
            except Exception:
                return None

        if len(tokens) >= 2:
            end_t = _parse_time(tokens[-1])
            if end_t is None:
                return None
            return datetime.combine(visit_date, end_t)

        start_t = _parse_time(tokens[0])
        if start_t is None:
            return None
        return datetime.combine(visit_date, start_t) + timedelta(minutes=30)

    def _is_booking_delayed(self, visit_date_value, slot_text: str, cmplt_time_value) -> bool:
        if not cmplt_time_value:
            return False
        end_dt = self._slot_end_dt(visit_date_value, slot_text)
        if end_dt is None:
            return False
        try:
            return cmplt_time_value > (end_dt + timedelta(minutes=10))
        except Exception:
            return False

    def get_caller_history_summary(self, caller_id: int, limit: int = 3):
        if int(caller_id or 0) <= 0:
            return {
                "counts": {
                    "linked_patients": 0,
                    "total_bookings": 0,
                    "delayed_bookings": 0,
                    "cancelled_bookings": 0,
                },
                "last_bookings": [],
            }

        safe_limit = max(1, min(int(limit or 4), 10))
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT patient_id) AS linked_patients
                    FROM hcaller_patient_link
                    WHERE caller_id=%s AND is_active=1
                    """,
                    (caller_id,),
                )
                linked_patients = int((cur.fetchone() or {}).get("linked_patients") or 0)

                cur.execute(
                    """
                    SELECT COUNT(*) AS total_bookings,
                           SUM(CASE WHEN booking_status=4 THEN 1 ELSE 0 END) AS cancelled_bookings
                    FROM hhome_collection_booking
                    WHERE caller_id=%s
                    """,
                    (caller_id,),
                )
                agg = cur.fetchone() or {}
                total_bookings = int(agg.get("total_bookings") or 0)
                cancelled_bookings = int(agg.get("cancelled_bookings") or 0)

                cur.execute(
                    """
                    SELECT preferred_visit_date, preferred_time_slot, cmplt_time
                    FROM hhome_collection_booking
                    WHERE caller_id=%s
                      AND cmplt_time IS NOT NULL
                    """,
                    (caller_id,),
                )
                delayed_bookings = 0
                for r in (cur.fetchall() or []):
                    if self._is_booking_delayed(
                        r.get("preferred_visit_date"),
                        r.get("preferred_time_slot"),
                        r.get("cmplt_time"),
                    ):
                        delayed_bookings += 1

                cur.execute(
                    f"""
                    SELECT id, booking_code, preferred_visit_date, preferred_time_slot,
                           booking_status, strt_time, cmplt_time, total_amount
                    FROM hhome_collection_booking
                    WHERE caller_id=%s
                    ORDER BY id DESC
                    LIMIT {safe_limit}
                    """,
                    (caller_id,),
                )
                last_rows = cur.fetchall() or []

                last_bookings = []
                for idx, r in enumerate(last_rows):
                    bid = int(r.get("id") or 0)
                    delayed = self._is_booking_delayed(
                        r.get("preferred_visit_date"),
                        r.get("preferred_time_slot"),
                        r.get("cmplt_time"),
                    )
                    last_bookings.append(
                        {
                            "booking_id": bid,
                            "booking_code": self._norm_code(r.get("booking_code")),
                            "preferred_visit_date": str(r.get("preferred_visit_date") or ""),
                            "preferred_time_slot": self._norm_code(r.get("preferred_time_slot")),
                            "status_code": int(r.get("booking_status") or 0),
                            "status_label": self._booking_status_label(int(r.get("booking_status") or 0)),
                            "total_amount": float(r.get("total_amount") or 0),
                            "start_time": str(r.get("strt_time") or ""),
                            "complete_time": str(r.get("cmplt_time") or ""),
                            "is_delayed": bool(delayed),
                            "is_latest": idx == 0,
                            "patient_count": 0,
                        }
                    )

                return {
                    "counts": {
                        "linked_patients": linked_patients,
                        "total_bookings": total_bookings,
                        "delayed_bookings": delayed_bookings,
                        "cancelled_bookings": cancelled_bookings,
                    },
                    "last_bookings": last_bookings,
                }
        finally:
            conn.close()

    def get_caller_history_booking_detail(self, booking_id: int):
        bid = int(booking_id or 0)
        if bid <= 0:
            return {"ok": False, "message": "booking_id is required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, booking_code, preferred_visit_date, preferred_time_slot, booking_status, total_amount
                    FROM hhome_collection_booking
                    WHERE id=%s
                    LIMIT 1
                    """,
                    (bid,),
                )
                b = cur.fetchone()
                if not b:
                    return {"ok": False, "message": "Booking not found"}

                cur.execute(
                    """
                    SELECT
                        bp.patient_id,
                        p.title,
                        p.full_name,
                        p.gender,
                        p.tag,
                        p.age_years,
                        p.date_of_birth,
                        p.panel_company,
                        p.labmate_pid,
                        p.contact_mobile
                    FROM hhome_collection_booking_patient bp
                    INNER JOIN hpatient_master p ON p.id = bp.patient_id
                    WHERE bp.booking_id=%s
                    ORDER BY p.full_name
                    """,
                    (bid,),
                )
                patients = []
                for r in (cur.fetchall() or []):
                    patients.append(
                        {
                            "patient_id": int(r.get("patient_id") or 0),
                            "full_name": " ".join([self._norm_code(r.get("title")), self._norm_code(r.get("full_name"))]).strip(),
                            "gender": self._norm_code(r.get("gender")),
                            "age": hage_label(r.get("age_years"), r.get("date_of_birth")),
                            "tag": self._norm_code(r.get("tag")),
                            "panel_company": self._norm_code(r.get("panel_company")),
                            "labmate_pid": self._norm_code(r.get("labmate_pid")),
                            "contact_mobile": self._norm_code(r.get("contact_mobile")),
                        }
                    )

                return {
                    "ok": True,
                    "booking": {
                        "booking_id": int(b.get("id") or 0),
                        "booking_code": self._norm_code(b.get("booking_code")),
                        "preferred_visit_date": str(b.get("preferred_visit_date") or ""),
                        "preferred_time_slot": self._norm_code(b.get("preferred_time_slot")),
                        "status_code": int(b.get("booking_status") or 0),
                        "status_label": self._booking_status_label(int(b.get("booking_status") or 0)),
                        "total_amount": float(b.get("total_amount") or 0),
                        "patients": patients,
                    },
                }
        finally:
            conn.close()

    def _as_bool_flag(self, value) -> bool:
        if isinstance(value, bool):
            return value
        text = self._norm_code(value).lower()
        return text in ("1", "true", "yes", "on", "y")

    def _normalize_floor_value(self, floor_value, full_house_flag=False):
        text = self._norm_code(floor_value)
        if full_house_flag:
            special_map = {
                "ground_f": "Ground_F",
                "basement": "Basement",
                "upper_f": "Upper_F",
                "top_floor": "Top_Floor",
                "full_hous": "Full_hous",
            }
            mapped = special_map.get(text.lower())
            return mapped or "Full_hous"

        if not text:
            return ""
        if not re.fullmatch(r"\d{1,2}", text):
            return ""
        num = int(text)
        if num < 1 or num > 99:
            return ""
        return str(num)

    def _compose_street_line(self, block_tower_no=None, street_sector=None) -> str:
        return self._norm_code(street_sector)

    def _street_sector_from_street_line(self, value) -> str:
        street = self._norm_code(value)
        return re.sub(r"^street\s*/\s*sector\s*:\s*", "", street, flags=re.IGNORECASE).strip()

    def _normalize_block_tower_no(self, value) -> str:
        return self._norm_code(value)

    def _enrich_address_row(self, row):
        if not row:
            return None
        enriched = dict(row)
        floor = self._norm_code(enriched.get("floor"))
        enriched["is_full_house"] = floor.lower() == "full_hous"
        enriched["floor_display"] = floor
        enriched["block_tower_no"] = self._normalize_block_tower_no(enriched.get("block_tower_no"))
        enriched["street_sector"] = self._street_sector_from_street_line(enriched.get("street_line"))
        return enriched

    def reset_session_for_new_caller(self, session):
        session.pop("hcaller_id", None)
        session.pop("hnew_caller_created", None)
        session["hselected_patients"] = []
        session["hselected_address_id"] = None
        session["hselected_address_snapshot"] = None
        self._clear_stage_map(session)

    def clear_booking_session(self, session):
        session.pop("hcaller_id", None)
        session.pop("hnew_caller_created", None)
        session.pop("hselected_patients", None)
        session.pop("hselected_address_id", None)
        session.pop("hselected_address_snapshot", None)
        self._clear_stage_map(session)

    def _temp_code(self, prefix: str) -> str:
        return f"{prefix}{uuid.uuid4().hex[:12]}"

    def get_caller_by_mobile(self, mobile: str):
        mobile_norm = self.normalize_mobile(mobile)
        if not mobile_norm:
            return None

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, caller_code, full_name, primary_mobile, alternate_mobile,
                           email, caller_status, active
                    FROM hcaller_master
                    WHERE id = (
                        SELECT caller_id
                        FROM hcaller_mobile_map
                        WHERE mobile_norm = %s AND is_active = 1
                        LIMIT 1
                    )
                      AND active = 1
                    LIMIT 1
                    """,
                    (mobile_norm,),
                )
                caller = cur.fetchone()
                if caller:
                    return caller

                cur.execute(
                    """
                    SELECT id, caller_code, full_name, primary_mobile, alternate_mobile,
                           email, caller_status, active
                    FROM hcaller_master
                    WHERE active = 1
                      AND (primary_mobile = %s OR alternate_mobile = %s)
                    LIMIT 1
                    """,
                    (mobile_norm, mobile_norm),
                )
                caller = cur.fetchone()
                if caller:
                    self._upsert_caller_mobile(
                        cur,
                        caller["id"],
                        caller.get("primary_mobile"),
                        "Primary",
                        raise_on_conflict=False,
                    )
                    self._upsert_caller_mobile(
                        cur,
                        caller["id"],
                        caller.get("alternate_mobile"),
                        "Alternate",
                        raise_on_conflict=False,
                    )
                    conn.commit()
                return caller
        finally:
            conn.close()

    def get_caller(self, caller_id: int):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, caller_code, full_name, primary_mobile, alternate_mobile,
                           email, caller_status, active, created_at, created_by, updated_at, updated_by
                    FROM hcaller_master
                    WHERE id=%s
                    """,
                    (caller_id,),
                )
                return cur.fetchone()
        finally:
            conn.close()

    def _upsert_caller_mobile(
        self,
        cur,
        caller_id: int,
        mobile: str,
        phone_type: str,
        raise_on_conflict=True,
        actor_user_id=None,
    ):
        mobile_norm = self.normalize_mobile(mobile)
        if not mobile_norm:
            return

        cur.execute(
            """
            SELECT caller_id
            FROM hcaller_mobile_map
            WHERE mobile_norm = %s AND is_active = 1
            LIMIT 1
            """,
            (mobile_norm,),
        )
        existing = cur.fetchone()
        if existing and int(existing["caller_id"]) != int(caller_id):
            if raise_on_conflict:
                raise ValueError(f"Mobile {mobile_norm} is already mapped to another caller")
            return
        raw = (mobile or "").strip()

        cur.execute(
            """
            INSERT INTO hcaller_mobile_map
            (caller_id, mobile_norm, mobile_raw, phone_type, is_active, created_by)
            VALUES (%s,%s,%s,%s,1,%s)
            ON DUPLICATE KEY UPDATE
            caller_id = VALUES(caller_id),
            mobile_raw = VALUES(mobile_raw),
            phone_type = VALUES(phone_type),
            is_active = 1
            """,
            (caller_id, mobile_norm, raw or mobile_norm, phone_type, self._actor(actor_user_id)),
        )

    def _caller_merge_conflict_response(self, cur, current_caller_id: int, mobile: str):
        mobile_norm = self.normalize_mobile(mobile)
        if not mobile_norm:
            return None

        cur.execute(
            """
            SELECT c.id, c.caller_code, c.full_name, c.primary_mobile, c.alternate_mobile
            FROM hcaller_mobile_map m
            INNER JOIN hcaller_master c ON c.id = m.caller_id
            WHERE m.mobile_norm = %s
              AND m.is_active = 1
              AND c.active = 1
              AND m.caller_id <> %s
            LIMIT 1
            """,
            (mobile_norm, current_caller_id),
        )
        target = cur.fetchone()
        if not target:
            return None

        cur.execute(
            """
            SELECT p.id, CONCAT_WS(' ', p.title, p.full_name) AS full_name,
                   p.contact_mobile, p.alternate_mobile
            FROM hcaller_patient_link cpl
            INNER JOIN hpatient_master p ON p.id = cpl.patient_id
            WHERE cpl.caller_id = %s
              AND cpl.is_active = 1
              AND (
                    p.contact_mobile = %s
                    OR p.alternate_mobile = %s
                  )
            ORDER BY p.id DESC
            LIMIT 5
            """,
            (target["id"], mobile_norm, mobile_norm),
        )
        patients = cur.fetchall() or []
        patient_text = ", ".join(
            [self._norm_code(p.get("full_name")) or f"Patient {p.get('id')}" for p in patients]
        ) or "an existing patient"
        target_mobile = target.get("primary_mobile") or target.get("alternate_mobile") or f"caller #{target['id']}"
        return {
            "ok": False,
            "merge_required": True,
            "message": (
                f"This mobile number is already mapped with {patient_text} under caller {target_mobile}. "
                "Do you want to merge the current caller into that caller?"
            ),
            "conflict_mobile": mobile_norm,
            "target_caller": target,
            "patients": patients,
        }

    def merge_callers(self, source_caller_id: int, target_caller_id: int, actor_user_id=None):
        source_caller_id = int(source_caller_id or 0)
        target_caller_id = int(target_caller_id or 0)
        actor = self._actor(actor_user_id)
        if source_caller_id <= 0 or target_caller_id <= 0:
            return {"ok": False, "message": "Source and target caller are required"}
        if source_caller_id == target_caller_id:
            return {"ok": True, "target_caller_id": target_caller_id}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM hcaller_master WHERE id=%s AND active=1 LIMIT 1",
                    (target_caller_id,),
                )
                if not cur.fetchone():
                    return {"ok": False, "message": "Target caller not found"}
                cur.execute(
                    "SELECT id, primary_mobile, alternate_mobile FROM hcaller_master WHERE id=%s LIMIT 1",
                    (source_caller_id,),
                )
                source = cur.fetchone()
                if not source:
                    return {"ok": False, "message": "Source caller not found"}

                cur.execute(
                    """
                    INSERT INTO hcaller_patient_link (caller_id, patient_id, is_active, created_by)
                    SELECT %s, patient_id, 1, %s
                    FROM hcaller_patient_link
                    WHERE caller_id = %s AND is_active = 1
                    ON DUPLICATE KEY UPDATE is_active = 1
                    """,
                    (target_caller_id, actor, source_caller_id),
                )
                cur.execute(
                    "UPDATE hcaller_patient_link SET is_active=0 WHERE caller_id=%s",
                    (source_caller_id,),
                )
                cur.execute(
                    "UPDATE hcaller_reference_address SET caller_id=%s, updated_by=%s WHERE caller_id=%s",
                    (target_caller_id, actor, source_caller_id),
                )
                cur.execute(
                    "UPDATE hcaller_mobile_map SET caller_id=%s, is_active=1 WHERE caller_id=%s",
                    (target_caller_id, source_caller_id),
                )
                for mobile, phone_type in (
                    (source.get("primary_mobile"), "MergedPrimary"),
                    (source.get("alternate_mobile"), "MergedAlternate"),
                ):
                    mobile_norm = self.normalize_mobile(mobile)
                    if not mobile_norm:
                        continue
                    cur.execute(
                        """
                        INSERT INTO hcaller_mobile_map
                        (caller_id, mobile_norm, mobile_raw, phone_type, is_active, created_by)
                        VALUES (%s,%s,%s,%s,1,%s)
                        ON DUPLICATE KEY UPDATE caller_id=VALUES(caller_id),
                                                phone_type=VALUES(phone_type),
                                                is_active=1
                        """,
                        (target_caller_id, mobile_norm, str(mobile).strip(), phone_type, actor),
                    )
                cur.execute(
                    """
                    UPDATE hcaller_master
                    SET active=0, caller_status='Inactive', updated_by=%s
                    WHERE id=%s
                    """,
                    (actor, source_caller_id),
                )
                conn.commit()
                return {
                    "ok": True,
                    "message": "Caller merged successfully",
                    "target_caller_id": target_caller_id,
                    "source_caller_id": source_caller_id,
                }
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def create_caller(self, payload: dict, actor_user_id=None):
        actor = self._actor(actor_user_id)
        full_name = (payload.get("full_name") or "").strip()
        primary_mobile = self.normalize_mobile(payload.get("primary_mobile"))
        alternate_mobile = self.normalize_mobile(payload.get("alternate_mobile"))
        if not full_name or not primary_mobile:
            return {"ok": False, "message": "Full name and primary mobile are required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT caller_id FROM hcaller_mobile_map WHERE mobile_norm=%s AND is_active=1 LIMIT 1",
                    (primary_mobile,),
                )
                if cur.fetchone():
                    return {"ok": False, "message": "This mobile is already registered"}

                temp_code = self._temp_code("HCLR-TMP-")
                cur.execute(
                    """
                    INSERT INTO hcaller_master
                    (caller_code, full_name, primary_mobile, alternate_mobile, email,
                     caller_status, created_by, updated_by)
                    VALUES (%s,%s,%s,%s,%s,'Active',%s,%s)
                    """,
                    (
                        temp_code,
                        full_name,
                        primary_mobile,
                        alternate_mobile or None,
                        payload.get("email"),
                        actor,
                        actor,
                    ),
                )
                caller_id = cur.lastrowid
                caller_code = hcode_from_id("HCLR-", caller_id)
                cur.execute("UPDATE hcaller_master SET caller_code=%s WHERE id=%s", (caller_code, caller_id))
                self._upsert_caller_mobile(cur, caller_id, primary_mobile, "Primary", actor_user_id=actor)
                self._upsert_caller_mobile(cur, caller_id, alternate_mobile, "Alternate", actor_user_id=actor)
                conn.commit()

                return {
                    "ok": True,
                    "caller": {
                        "id": caller_id,
                        "caller_code": caller_code,
                        "full_name": full_name,
                        "primary_mobile": primary_mobile,
                    },
                }
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def get_linked_patients(self, caller_id: int, session):
        conn = get_db_connection()
        try:
            selected = {int(x["patient_id"]) for x in session.get("hselected_patients", [])}
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.patient_code, CONCAT_WS(' ', p.title, p.full_name) AS full_name, p.tag, p.age_years, p.date_of_birth,
                           am.id AS default_address_id,
                           EXISTS(
                               SELECT 1
                               FROM hhome_collection_booking_patient bp
                               WHERE bp.patient_id = p.id
                               LIMIT 1
                           ) AS has_booking
                    FROM hcaller_patient_link cpl
                    INNER JOIN hpatient_master p ON p.id = cpl.patient_id
                    LEFT JOIN hpatient_address_link pal ON pal.id = (
                        SELECT pal2.id
                        FROM hpatient_address_link pal2
                        WHERE pal2.patient_id = p.id AND pal2.is_default = 1 AND pal2.is_active = 1
                        ORDER BY pal2.id DESC
                        LIMIT 1
                    )
                    LEFT JOIN haddress_master am ON am.id = pal.address_id
                    WHERE cpl.caller_id = %s
                      AND cpl.is_active = 1
                      AND COALESCE(p.patient_status, 0) = 0
                    ORDER BY p.full_name
                    """,
                    (caller_id,),
                )
                rows = cur.fetchall()
                for row in rows:
                    row["age"] = hage_label(row.get("age_years"), row.get("date_of_birth"))
                    row["selected"] = int(row["id"]) in selected
                    row["can_inactivate"] = not bool(int(row.get("has_booking") or 0))
                return rows
        finally:
            conn.close()

    def mark_linked_patient_inactive(self, caller_id: int, patient_id: int, actor_user_id=None):
        caller_id = int(caller_id or 0)
        patient_id = int(patient_id or 0)
        if caller_id <= 0 or patient_id <= 0:
            return {"ok": False, "message": "Caller and patient are required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id
                    FROM hcaller_patient_link cpl
                    INNER JOIN hpatient_master p ON p.id = cpl.patient_id
                    WHERE cpl.caller_id=%s
                      AND cpl.patient_id=%s
                      AND cpl.is_active=1
                      AND COALESCE(p.patient_status, 0)=0
                    LIMIT 1
                    """,
                    (caller_id, patient_id),
                )
                if not cur.fetchone():
                    return {"ok": False, "message": "Active linked patient not found"}

                cur.execute(
                    """
                    SELECT 1
                    FROM hhome_collection_booking_patient
                    WHERE patient_id=%s
                    LIMIT 1
                    """,
                    (patient_id,),
                )
                if cur.fetchone():
                    return {"ok": False, "message": "This patient is already included in a booking and cannot be marked inactive here"}

                cur.execute(
                    """
                    UPDATE hpatient_master
                    SET patient_status=1,
                        updated_by=%s
                    WHERE id=%s
                    """,
                    (self._actor(actor_user_id), patient_id),
                )
                conn.commit()
                return {"ok": True}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def _upsert_caller_patient_link(self, cur, caller_id: int, patient_id: int, actor_user_id=None):
        cur.execute(
            """
            INSERT INTO hcaller_patient_link
            (caller_id, patient_id, is_active, created_by)
            VALUES (%s,%s,1,%s)
            ON DUPLICATE KEY UPDATE
            is_active = 1
            """,
            (caller_id, patient_id, self._actor(actor_user_id)),
        )

    def select_patient(self, caller_id: int, patient_id, session, actor_user_id=None):
        try:
            patient_id = int(patient_id)
        except Exception:
            return {"ok": False, "message": "Invalid patient"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, full_name FROM hpatient_master WHERE id=%s", (patient_id,))
                patient = cur.fetchone()
                if not patient:
                    return {"ok": False, "message": "Patient not found"}

                self._upsert_caller_patient_link(cur, caller_id, patient_id, actor_user_id=actor_user_id)
                conn.commit()

            selected = session.get("hselected_patients", [])
            exists = any(int(x["patient_id"]) == patient_id for x in selected)
            if not exists:
                selected.append({"patient_id": patient_id})
            session["hselected_patients"] = selected
            return {"ok": True, "selected_patients": selected}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def create_patient_and_link(self, caller_id: int, payload: dict, session, actor_user_id=None, uploaded_documents=None):
        actor = self._actor(actor_user_id)
        full_name_input = (payload.get("full_name") or "").strip()
        title = (payload.get("title") or "").strip() or None
        labmate_pid = (payload.get("labmate_pid") or "").strip() or None
        panel_company = (payload.get("panel_company") or "").strip() or None
        card_number = (payload.get("card_number") or "").strip() or None
        gender = (payload.get("gender") or "").strip()
        if not full_name_input or not gender:
            return {"ok": False, "message": "Patient full name and gender are required"}
        full_name = full_name_input.upper()
        tag = self.sanitize_patient_tags(payload.get("tag"))
        dob = payload.get("date_of_birth") or None
        age_years = payload.get("age_years") or None
        contact_mobile = self.normalize_mobile(payload.get("contact_mobile"))
        alternate_mobile = self.normalize_mobile(payload.get("alternate_mobile"))
        if not contact_mobile:
            return {"ok": False, "message": "Contact No is required"}

        if dob and not age_years:
            age_years, _ = hcalculate_age_parts(dob)

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for mobile in (contact_mobile, alternate_mobile):
                    conflict = self._caller_merge_conflict_response(cur, caller_id, mobile)
                    if conflict:
                        return conflict

                self._upsert_caller_mobile(
                    cur, caller_id, payload.get("contact_mobile"), "PatientContact", actor_user_id=actor
                )
                self._upsert_caller_mobile(
                    cur, caller_id, payload.get("alternate_mobile"), "Alternate", actor_user_id=actor
                )
                temp_code = self._temp_code("HPT-TMP-")
                cur.execute(
                    """
                    INSERT INTO hpatient_master
                    (patient_code, title, full_name, labmate_pid, panel_company, card_number, tag,
                     gender, date_of_birth, age_years, contact_mobile, alternate_mobile,
                     patient_status, created_by, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s)
                    """,
                    (
                        temp_code,
                        title,
                        full_name,
                        labmate_pid,
                        panel_company,
                        card_number,
                        tag or None,
                        gender,
                        dob,
                        age_years,
                        contact_mobile or None,
                        alternate_mobile or None,
                        actor,
                        actor,
                    ),
                )
                patient_id = cur.lastrowid
                patient_code = hcode_from_id("HPT-HC-", patient_id)
                cur.execute("UPDATE hpatient_master SET patient_code=%s WHERE id=%s", (patient_code, patient_id))

                document_result = self._save_patient_documents_with_cursor(
                    cur, patient_id, uploaded_documents or [], actor_user_id=actor
                )
                if not document_result.get("ok"):
                    conn.rollback()
                    return document_result

                self._upsert_caller_patient_link(cur, caller_id, patient_id, actor_user_id=actor)
                conn.commit()

            selected = session.get("hselected_patients", [])
            selected.append({"patient_id": patient_id})
            session["hselected_patients"] = selected

            return {
                "ok": True,
                "patient": {
                    "id": patient_id,
                    "patient_code": patient_code,
                    "full_name": full_name,
                },
                "selected_patients": selected,
            }
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def get_patient_for_edit(self, caller_id: int, patient_id: int):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.title, p.full_name, p.labmate_pid, p.panel_company, p.card_number,
                           p.patient_documents, p.tag,
                           p.gender, p.date_of_birth, p.age_years, p.contact_mobile, p.alternate_mobile,
                           cm.email
                    FROM hcaller_patient_link cpl
                    INNER JOIN hpatient_master p ON p.id = cpl.patient_id
                    INNER JOIN hcaller_master cm ON cm.id = cpl.caller_id
                    WHERE cpl.caller_id = %s
                      AND p.id = %s
                      AND cpl.is_active = 1
                    LIMIT 1
                    """,
                    (caller_id, patient_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if row.get("date_of_birth"):
                    row["date_of_birth"] = row["date_of_birth"].isoformat()
                row["patient_documents"] = self._split_patient_documents(row.get("patient_documents"))
                return row
        finally:
            conn.close()

    def update_patient_for_caller(self, caller_id: int, patient_id: int, payload: dict, actor_user_id=None, uploaded_documents=None, audit_context=None):
        actor = self._actor(actor_user_id)
        full_name = (payload.get("full_name") or "").strip()
        gender = (payload.get("gender") or "").strip()
        if not full_name or not gender:
            return {"ok": False, "message": "Patient full name and gender are required"}

        title = (payload.get("title") or "").strip() or None
        labmate_pid = (payload.get("labmate_pid") or "").strip() or None
        panel_company = (payload.get("panel_company") or "").strip() or None
        card_number = (payload.get("card_number") or "").strip() or None
        tag = self.sanitize_patient_tags(payload.get("tag"))
        dob = payload.get("date_of_birth") or None
        age_years = payload.get("age_years") or None
        if dob and not age_years:
            age_years, _ = hcalculate_age_parts(dob)

        contact_mobile = self.normalize_mobile(payload.get("contact_mobile"))
        alternate_mobile = self.normalize_mobile(payload.get("alternate_mobile"))
        if not contact_mobile:
            return {"ok": False, "message": "Contact No is required"}
        email = (payload.get("email") or "").strip() or None

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for mobile in (contact_mobile, alternate_mobile):
                    conflict = self._caller_merge_conflict_response(cur, caller_id, mobile)
                    if conflict:
                        return conflict

                cur.execute(
                    """
                    SELECT p.title, p.full_name, p.labmate_pid, p.panel_company, p.card_number,
                           p.tag, p.gender, p.date_of_birth, p.age_years,
                           p.contact_mobile, p.alternate_mobile
                    FROM hcaller_patient_link
                    INNER JOIN hpatient_master p ON p.id = hcaller_patient_link.patient_id
                    WHERE caller_id = %s AND patient_id = %s AND is_active = 1
                    LIMIT 1
                    """,
                    (caller_id, patient_id),
                )
                linked_row = cur.fetchone()
                if not linked_row:
                    return {"ok": False, "message": "Patient not linked with selected caller"}
                existing_tag = (linked_row or {}).get("tag")
                merged_tag = self._merge_tag_csv(existing_tag, tag)
                new_patient_values = {
                    "title": title,
                    "full_name": full_name,
                    "labmate_pid": labmate_pid,
                    "panel_company": panel_company,
                    "card_number": card_number,
                    "tag": merged_tag or None,
                    "gender": gender,
                    "date_of_birth": dob,
                    "age_years": age_years,
                    "contact_mobile": contact_mobile or None,
                    "alternate_mobile": alternate_mobile or None,
                }

                cur.execute(
                    """
                    UPDATE hpatient_master
                    SET title=%s,
                        full_name=%s,
                        labmate_pid=%s,
                        panel_company=%s,
                        card_number=%s,
                        tag=%s,
                        gender=%s,
                        date_of_birth=%s,
                        age_years=%s,
                        contact_mobile=%s,
                        alternate_mobile=%s,
                        updated_by=%s
                    WHERE id=%s
                    """,
                    (
                        title,
                        full_name,
                        labmate_pid,
                        panel_company,
                        card_number,
                        merged_tag or None,
                        gender,
                        dob,
                        age_years,
                        contact_mobile or None,
                        alternate_mobile or None,
                        actor,
                        patient_id,
                    ),
                )

                # Keep caller contacts searchable in map table.
                self._upsert_caller_mobile(
                    cur, caller_id, contact_mobile, "PatientContact", actor_user_id=actor
                )
                self._upsert_caller_mobile(
                    cur, caller_id, alternate_mobile, "Alternate", actor_user_id=actor
                )
                if email:
                    cur.execute(
                        "UPDATE hcaller_master SET email=%s, updated_by=%s WHERE id=%s",
                        (email, actor, caller_id),
                    )

                document_result = self._save_patient_documents_with_cursor(
                    cur, patient_id, uploaded_documents or [], actor_user_id=actor
                )
                if not document_result.get("ok"):
                    conn.rollback()
                    return document_result
                old_values, new_values = self._audit_changed_values(linked_row, new_patient_values)
                if old_values:
                    source_type, booking_id, appointment_id = self._audit_source_context(audit_context)
                    self._insert_patient_address_update_audit(
                        cur,
                        source_type=source_type,
                        caller_id=caller_id,
                        booking_id=booking_id,
                        appointment_id=appointment_id,
                        patient_id=patient_id,
                        address_id=None,
                        action_type="PATIENT_UPDATE",
                        old_values=old_values,
                        new_values=new_values,
                        done_by=actor,
                    )

                conn.commit()
                return {"ok": True}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def get_selected_patients_enriched(self, caller_id: int, selected: list, session=None):
        patient_ids = [int(item["patient_id"]) for item in selected]
        if not patient_ids:
            return []

        conn = get_db_connection()
        try:
            placeholders = ",".join(["%s"] * len(patient_ids))
            with conn.cursor() as cur:
                caller_email = None
                if caller_id:
                    cur.execute("SELECT email FROM hcaller_master WHERE id=%s", (caller_id,))
                    c_row = cur.fetchone() or {}
                    caller_email = self._norm_code(c_row.get("email")) or None
                cur.execute(
                    f"""
                    SELECT id, patient_code, title, full_name, gender, date_of_birth, age_years
                         , labmate_pid
                         , panel_company
                         , card_number
                         , tag
                         , contact_mobile
                         , alternate_mobile
                         , patient_documents
                    FROM hpatient_master
                    WHERE id IN ({placeholders})
                    """,
                    patient_ids,
                )
                rows = {int(r["id"]): r for r in cur.fetchall()}

            response = []
            for item in selected:
                pid = int(item["patient_id"])
                if pid not in rows:
                    continue
                r = rows[pid]
                patient_documents = self._split_patient_documents(r.get("patient_documents"))
                response.append(
                    {
                        "patient_id": pid,
                        "patient_code": r["patient_code"],
                        "full_name": f"{(r.get('title') or '').strip()} {(r['full_name'] or '').strip()}".strip(),
                        "tag": r.get("tag"),
                        "panel_company": r.get("panel_company"),
                        "card_number": r.get("card_number"),
                        "gender": r["gender"],
                        "date_of_birth": r.get("date_of_birth").isoformat() if r.get("date_of_birth") else None,
                        "labmate_pid": r.get("labmate_pid") or None,
                        "contact_mobile": r.get("contact_mobile") or None,
                        "alternate_mobile": r.get("alternate_mobile") or None,
                        "email": caller_email,
                        "patient_documents": patient_documents,
                        "patient_document_count": len(patient_documents),
                        "prescription_file_count": len(self.get_patient_prescription_files_with_stage(pid, session=session)),
                        "staged_prescription_file_count": self.get_patient_staged_prescription_count(pid, session=session),
                        "staged_prescription_files": self.get_patient_staged_prescription_items(pid, session=session),
                        "age": hage_label(r.get("age_years"), r.get("date_of_birth")),
                    }
                )
            return response
        finally:
            conn.close()

    def get_step1_bundle(self, caller_id: int, session):
        selected = session.get("hselected_patients", [])
        if not caller_id:
            return {
                "linked_patients": [],
                "selected_patients": [],
                "addresses": [],
                "reference_addresses": [],
                "caller_history": self.get_caller_history_summary(0),
                "selected_address_id": session.get("hselected_address_id"),
            }
        return {
                "linked_patients": self.get_linked_patients(caller_id, session),
            "selected_patients": self.get_selected_patients_enriched(caller_id, selected, session=session),
            "addresses": self.get_addresses_for_caller(caller_id),
            "reference_addresses": self.get_reference_addresses_for_caller(caller_id),
            "caller_history": self.get_caller_history_summary(caller_id),
            "selected_address_id": session.get("hselected_address_id"),
        }

    def list_colonies(self, city=None):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if city:
                    cur.execute(
                        """
                        SELECT id, colony_name, pincode, route_no, city
                        FROM hcolony_master
                        WHERE is_active = 1 AND city = %s
                        ORDER BY colony_name
                        """,
                        (city,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, colony_name, pincode, route_no, city
                        FROM hcolony_master
                        WHERE is_active = 1
                        ORDER BY colony_name
                        """
                    )
                return cur.fetchall()
        finally:
            conn.close()

    def get_addresses_for_caller(self, caller_id: int):
        if not caller_id:
            return []

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT am.id, am.address_type, am.house_flat_no,
                           am.floor, am.block_tower_no, am.street_line, am.landmark, am.colony_id,
                           am.colony_name,
                           am.pincode,
                           am.route_no,
                           am.google_location, am.city, am.access_notes
                    FROM hcaller_patient_link cpl
                    INNER JOIN hpatient_address_link pal ON pal.patient_id = cpl.patient_id AND pal.is_active = 1
                    INNER JOIN haddress_master am ON am.id = pal.address_id
                    WHERE cpl.caller_id = %s AND cpl.is_active = 1
                    ORDER BY am.id DESC
                    """,
                    (caller_id,),
                )
                return [self._enrich_address_row(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_reference_addresses_for_caller(self, caller_id: int):
        if not caller_id:
            return []

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, caller_id, area, city, pincode, routename, address, status,
                           removed_by, removed_at, created_at, created_by, updated_at, updated_by
                    FROM hcaller_reference_address
                    WHERE caller_id = %s AND status = 1
                    ORDER BY id DESC
                    """,
                    (caller_id,),
                )
                return cur.fetchall()
        finally:
            conn.close()

    def finalize_reference_address_for_caller(self, caller_id: int, reference_address_id: int, actor_user_id=None):
        actor = self._actor(actor_user_id)
        if not caller_id or not reference_address_id:
            return {"ok": False, "message": "Invalid reference address"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE hcaller_reference_address
                    SET status = 2,
                        removed_by = %s,
                        removed_at = NOW(),
                        updated_by = %s
                    WHERE id = %s
                      AND caller_id = %s
                      AND status = 1
                    """,
                    (actor, actor, reference_address_id, caller_id),
                )
                if cur.rowcount <= 0:
                    conn.rollback()
                    return {"ok": False, "message": "Reference address not found"}
                conn.commit()
                return {"ok": True}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def create_address_for_patients(self, patient_ids: list, payload: dict, actor_user_id=None):
        actor = self._actor(actor_user_id)
        house_flat_no = (payload.get("house_flat_no") or "").strip()
        colony_not_found = self._as_bool_flag(payload.get("colony_not_found"))
        try:
            colony_id = int(payload.get("colony_id", 0) or 0)
        except Exception:
            colony_id = 0
        colony_name_input = (payload.get("colony_name") or "").strip()
        pincode_input = (payload.get("pincode") or "").strip()
        city = (payload.get("city") or "").strip()
        is_full_house = self._as_bool_flag(payload.get("full_house"))
        floor_value = self._normalize_floor_value(payload.get("floor"), is_full_house)
        block_tower_no = self._normalize_block_tower_no(payload.get("block_tower_no"))
        street_line = self._compose_street_line(None, payload.get("street_sector") or payload.get("street_line"))
        if not house_flat_no:
            return {"ok": False, "message": "House/Flat No is required"}
        if not colony_not_found and not colony_id:
            return {"ok": False, "message": "Colony is required"}
        if colony_not_found and not colony_name_input:
            return {"ok": False, "message": "Colony name is required"}
        if not city:
            return {"ok": False, "message": "City is required"}
        if not pincode_input:
            return {"ok": False, "message": "Pincode is required"}
        if not floor_value:
            return {"ok": False, "message": "Floor number 1-99 or floor option is required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if colony_not_found:
                    cur.execute(
                        """
                        SELECT id, route_no
                        FROM hcolony_master
                        WHERE is_active=1 AND city=%s AND pincode=%s
                        ORDER BY id
                        LIMIT 1
                        """,
                        (city, pincode_input),
                    )
                    mapped = cur.fetchone()
                    if not mapped or not self._norm_code(mapped.get("route_no")):
                        return {"ok": False, "message": "No route mapping found for selected city and pincode"}
                    colony_id = int(mapped.get("id") or 0)
                    colony_name_value = colony_name_input
                    pincode_value = pincode_input
                    route_value = self._norm_code(mapped.get("route_no"))
                else:
                    cur.execute(
                        "SELECT id, colony_name, pincode, route_no, city FROM hcolony_master WHERE id=%s AND is_active=1",
                        (colony_id,),
                    )
                    colony = cur.fetchone()
                    if not colony:
                        return {"ok": False, "message": "Invalid colony"}
                    if not self._norm_code(colony.get("pincode")) or not self._norm_code(colony.get("route_no")):
                        return {"ok": False, "message": "Selected colony is missing pincode or route"}
                    colony_name_value = colony["colony_name"]
                    pincode_value = colony["pincode"]
                    route_value = colony["route_no"]

                cur.execute(
                    """
                    INSERT INTO haddress_master
                    (address_type, house_flat_no, floor, block_tower_no, street_line, landmark,
                     colony_id, colony_name, pincode, route_no, google_location, city,
                     access_notes, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        payload.get("address_type") or "Home",
                        house_flat_no,
                        floor_value or None,
                        block_tower_no or None,
                        street_line or None,
                        payload.get("landmark") or None,
                        colony_id,
                        colony_name_value,
                        pincode_value,
                        route_value,
                        payload.get("google_location") or None,
                        city,
                        payload.get("access_notes") or None,
                        actor,
                    ),
                )
                address_id = cur.lastrowid

                for index, patient_id in enumerate(patient_ids):
                    is_default = 1 if index == 0 else 0
                    if is_default == 1:
                        cur.execute(
                            "UPDATE hpatient_address_link SET is_default = 0 WHERE patient_id = %s",
                            (patient_id,),
                        )
                    cur.execute(
                        """
                        INSERT INTO hpatient_address_link
                        (patient_id, address_id, is_default, is_active, created_by)
                        VALUES (%s,%s,%s,1,%s)
                        ON DUPLICATE KEY UPDATE is_active=1
                        """,
                        (patient_id, address_id, is_default, actor),
                    )

                conn.commit()

                snapshot = {
                    "address_id": address_id,
                    "address_type": payload.get("address_type") or "Home",
                    "house_flat_no": house_flat_no,
                    "floor": floor_value or None,
                    "is_full_house": is_full_house,
                    "block_tower_no": block_tower_no or None,
                    "street_line": street_line or None,
                    "street_sector": self._street_sector_from_street_line(street_line) or None,
                    "landmark": payload.get("landmark") or None,
                    "colony_name": colony_name_value,
                    "pincode": pincode_value,
                    "route_no": route_value,
                    "google_location": payload.get("google_location") or None,
                    "city": city,
                    "access_notes": payload.get("access_notes") or None,
                }
                return {"ok": True, "address": {"id": address_id}, "address_snapshot": snapshot}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def get_address_snapshot(self, address_id: int):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id AS address_id, address_type, house_flat_no, floor,
                           block_tower_no, street_line, landmark,
                           colony_name,
                           pincode,
                           route_no,
                           google_location, city, access_notes
                    FROM haddress_master
                    WHERE id=%s
                    """,
                    (address_id,),
                )
                return self._enrich_address_row(cur.fetchone())
        finally:
            conn.close()

    def get_address_for_caller(self, caller_id: int, address_id: int):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT am.id, am.address_type, am.house_flat_no, am.floor,
                           am.block_tower_no, am.street_line, am.landmark, am.colony_id,
                           am.colony_name,
                           am.pincode,
                           am.route_no,
                           am.google_location, am.city, am.access_notes
                    FROM hcaller_patient_link cpl
                    INNER JOIN hpatient_address_link pal ON pal.patient_id = cpl.patient_id AND pal.is_active = 1
                    INNER JOIN haddress_master am ON am.id = pal.address_id
                    WHERE cpl.caller_id = %s
                      AND cpl.is_active = 1
                      AND am.id = %s
                    LIMIT 1
                    """,
                    (caller_id, address_id),
                )
                return self._enrich_address_row(cur.fetchone())
        finally:
            conn.close()

    def update_address_for_caller(self, caller_id: int, address_id: int, payload: dict, actor_user_id=None, audit_context=None, snapshot_only: bool = False):
        actor = self._actor(actor_user_id)
        house_flat_no = (payload.get("house_flat_no") or "").strip()
        colony_not_found = self._as_bool_flag(payload.get("colony_not_found"))
        try:
            colony_id = int(payload.get("colony_id", 0) or 0)
        except Exception:
            colony_id = 0
        colony_name_input = (payload.get("colony_name") or "").strip()
        pincode_input = (payload.get("pincode") or "").strip()
        city = (payload.get("city") or "").strip()
        is_full_house = self._as_bool_flag(payload.get("full_house"))
        floor_value = self._normalize_floor_value(payload.get("floor"), is_full_house)
        block_tower_no = self._normalize_block_tower_no(payload.get("block_tower_no"))
        street_line = self._compose_street_line(None, payload.get("street_sector") or payload.get("street_line"))
        if not house_flat_no or not city:
            return {"ok": False, "message": "House/Flat, city are required"}
        if not colony_not_found and not colony_id:
            return {"ok": False, "message": "Colony is required"}
        if colony_not_found and not colony_name_input:
            return {"ok": False, "message": "Colony name is required"}
        if not pincode_input:
            return {"ok": False, "message": "Pincode is required"}
        if not floor_value:
            return {"ok": False, "message": "Floor number 1-99 or floor option is required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT am.address_type, am.house_flat_no, am.floor, am.block_tower_no,
                           am.street_line, am.landmark, am.google_location, am.colony_id,
                           am.colony_name, am.pincode, am.route_no, am.city, am.access_notes
                    FROM hcaller_patient_link cpl
                    INNER JOIN hpatient_address_link pal ON pal.patient_id = cpl.patient_id AND pal.is_active = 1
                    INNER JOIN haddress_master am ON am.id = pal.address_id
                    WHERE cpl.caller_id = %s
                      AND cpl.is_active = 1
                      AND pal.address_id = %s
                    LIMIT 1
                    """,
                    (caller_id, address_id),
                )
                old_address = cur.fetchone()
                if not old_address:
                    return {"ok": False, "message": "Address not linked with selected caller"}

                if colony_not_found:
                    cur.execute(
                        """
                        SELECT id, route_no
                        FROM hcolony_master
                        WHERE is_active=1 AND city=%s AND pincode=%s
                        ORDER BY id
                        LIMIT 1
                        """,
                        (city, pincode_input),
                    )
                    mapped = cur.fetchone()
                    if not mapped or not self._norm_code(mapped.get("route_no")):
                        return {"ok": False, "message": "No route mapping found for selected city and pincode"}
                    colony_id = int(mapped.get("id") or 0)
                    colony_name_value = colony_name_input
                    pincode_value = pincode_input
                    route_value = self._norm_code(mapped.get("route_no"))
                else:
                    cur.execute(
                        "SELECT id, colony_name, pincode, route_no FROM hcolony_master WHERE id=%s AND is_active=1",
                        (colony_id,),
                    )
                    colony = cur.fetchone()
                    if not colony:
                        return {"ok": False, "message": "Invalid colony"}
                    if not self._norm_code(colony.get("pincode")) or not self._norm_code(colony.get("route_no")):
                        return {"ok": False, "message": "Selected colony is missing pincode or route"}
                    colony_name_value = colony["colony_name"]
                    pincode_value = colony["pincode"]
                    route_value = colony["route_no"]
                new_address_values = {
                    "address_type": payload.get("address_type") or "Home",
                    "house_flat_no": house_flat_no,
                    "floor": floor_value or None,
                    "block_tower_no": block_tower_no or None,
                    "street_line": street_line or None,
                    "landmark": payload.get("landmark") or None,
                    "google_location": payload.get("google_location") or None,
                    "colony_id": colony_id,
                    "colony_name": colony_name_value,
                    "pincode": pincode_value,
                    "route_no": route_value,
                    "city": city,
                    "access_notes": payload.get("access_notes") or None,
                }
                if snapshot_only:
                    snapshot = self._enrich_address_row({
                        "id": address_id,
                        "address_id": address_id,
                        **new_address_values,
                        "is_full_house": is_full_house,
                    })
                    return {"ok": True, "address": {"id": address_id}, "address_snapshot": snapshot}

                cur.execute(
                    """
                    UPDATE haddress_master
                    SET address_type=%s,
                        house_flat_no=%s,
                        floor=%s,
                        block_tower_no=%s,
                        street_line=%s,
                        landmark=%s,
                        google_location=%s,
                        colony_id=%s,
                        colony_name=%s,
                        pincode=%s,
                        route_no=%s,
                        city=%s,
                        access_notes=%s
                    WHERE id=%s
                    """,
                    (
                        payload.get("address_type") or "Home",
                        house_flat_no,
                        floor_value or None,
                        block_tower_no or None,
                        street_line or None,
                        payload.get("landmark") or None,
                        payload.get("google_location") or None,
                        colony_id,
                        colony_name_value,
                        pincode_value,
                        route_value,
                        city,
                        payload.get("access_notes") or None,
                        address_id,
                    ),
                )
                old_values, new_values = self._audit_changed_values(old_address, new_address_values)
                if old_values:
                    source_type, booking_id, appointment_id = self._audit_source_context(audit_context)
                    self._insert_patient_address_update_audit(
                        cur,
                        source_type=source_type,
                        caller_id=caller_id,
                        booking_id=booking_id,
                        appointment_id=appointment_id,
                        patient_id=None,
                        address_id=address_id,
                        action_type="ADDRESS_UPDATE",
                        old_values=old_values,
                        new_values=new_values,
                        done_by=actor,
                    )

                conn.commit()
                snapshot = self.get_address_snapshot(address_id)
                return {"ok": True, "address": {"id": address_id}, "address_snapshot": snapshot}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def _slot_start_key(self, slot_text: str):
        text = (slot_text or "").strip()
        if not text:
            return None
        lower = text.lower()
        if " to " in lower:
            token = re.split(r"\bto\b", text, flags=re.IGNORECASE)[0].strip()
        elif "-" in text:
            token = text.split("-", 1)[0].strip()
        else:
            token = text
        token = token.replace(".", "").upper().replace(" ", "")
        match = re.match(r"^(\d{1,2})(?::(\d{2}))?(AM|PM)$", token)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if ampm == "PM" and hour != 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        return hour * 60 + minute

    def _route_sort_key(self, route_text: str):
        text = self._norm_code(route_text)
        match = re.search(r"(\d+)", text)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return 999999
        return 999999

    def route_slot_grid_data(self, visit_date: str, selected_route: str | None = None):
        if not visit_date:
            return {"ok": False, "message": "Date is required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT route_no AS route_name, color
                    FROM hcolony_master
                    WHERE is_active = 1 AND route_no IS NOT NULL AND route_no <> ''
                    ORDER BY route_no
                    """
                )
                route_rows = cur.fetchall() or []
                routes = []
                route_color_map = {}
                for row in route_rows:
                    route_name = self._norm_code((row or {}).get("route_name"))
                    if not route_name:
                        continue
                    if route_name not in routes:
                        routes.append(route_name)
                    clr = self._norm_code((row or {}).get("color"))
                    if route_name not in route_color_map and clr:
                        route_color_map[route_name] = clr

                cur.execute(
                    """
                    SELECT hcb.id, hcb.preferred_time_slot, am.route_no AS route_name,
                           am.city, am.colony_name, cm.primary_mobile
                    FROM hhome_collection_booking hcb
                    INNER JOIN haddress_master am ON am.id = hcb.selected_address_id
                    INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                    WHERE hcb.preferred_visit_date = %s
                      AND hcb.booking_status <> 4
                    ORDER BY hcb.id DESC
                    """,
                    (visit_date,),
                )
                rows = cur.fetchall()

            selected = (selected_route or "").strip()
            bookings = []
            route_set = set(routes)
            for row in rows:
                route_name = (row.get("route_name") or "UNASSIGNED").strip() or "UNASSIGNED"
                route_set.add(route_name)
                bookings.append(
                    {
                        "slot": row.get("preferred_time_slot"),
                        "slot_key": self._slot_start_key(row.get("preferred_time_slot") or ""),
                        "route": route_name,
                        "city": row.get("city") or "",
                        "area": row.get("colony_name") or "",
                        "mobile": row.get("primary_mobile") or "",
                    }
                )

            ordered = sorted([r for r in route_set if r != "UNASSIGNED"], key=str.upper)
            if selected and selected in ordered:
                ordered = [selected] + [r for r in ordered if r != selected]
            elif selected and selected != "UNASSIGNED":
                ordered = [selected] + ordered
            if "UNASSIGNED" in route_set:
                ordered.append("UNASSIGNED")

            return {
                "ok": True,
                "date": visit_date,
                "selected_route": selected,
                "routes": ordered,
                "route_colors": route_color_map,
                "bookings": bookings,
                "total_bookings": len(bookings),
            }
        except Exception as exc:
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def search_panel_companies(self, query: str, limit: int = 15, atype: str | None = None, master: bool = False):
        self.preload_panel_catalog()
        q = (query or "").strip()
        if len(q) < 2:
            return []
        limit = max(1, min(int(limit or 15), 50))
        ql = q.lower()
        candidates = self._panel_catalog["prefix2"].get(ql[:2], [])
        if not master:
            candidates = [p for p in candidates if int(p.get("Active") or 0) == 1 and int(p.get("showinHC") or 0) == 1]
        atype_code = self._norm_code(atype).upper()
        if atype_code in ("C", "D"):
            if atype_code == "C":
                candidates = [p for p in candidates if p.get("_has_c")]
            else:
                candidates = [p for p in candidates if p.get("_has_d")]
        prefix = [p for p in candidates if p["_pname_lc"].startswith(ql)]
        contains = [p for p in candidates if (ql in p["_pname_lc"]) and (not p["_pname_lc"].startswith(ql))]
        rows = (prefix + contains)[:limit]
        # Hide internal lowercase key before returning
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    def panel_companies_initial(self, limit: int = 5, master: bool = False):
        self.preload_panel_catalog()
        try:
            safe_limit = max(1, min(int(limit or 5), 50))
        except Exception:
            safe_limit = 5
        candidates = self._panel_catalog.get("panels", [])
        if not master:
            candidates = [p for p in candidates if int(p.get("Active") or 0) == 1 and int(p.get("showinHC") or 0) == 1]
        rows = sorted(
            candidates,
            key=lambda r: self._norm_code(r.get("pname")).lower(),
        )[:safe_limit]
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    def update_panel_show_mrp(self, comp_cat_id: str, panel_name: str, enabled: bool):
        comp = self._norm_code(comp_cat_id)
        pname = self._norm_code(panel_name)
        if not comp or not pname:
            return {"ok": False, "message": "Panel company and CompCatID are required"}
        value = 1 if enabled else 0
        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE address
                    SET showmrp=%s,
                        updated_at=NOW()
                    WHERE category=%s
                      AND TRIM(pname)=%s
                      AND UPPER(TRIM(Atype)) = 'C'
                    """,
                    (value, comp, pname),
                )
                affected = int(cur.rowcount or 0)
            conn.commit()
            self.refresh_panel_catalog()
            return {"ok": True, "showmrp": value, "updated_rows": affected}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def update_panel_show_in_hc(self, comp_cat_id: str, panel_name: str, enabled: bool):
        comp = self._norm_code(comp_cat_id)
        pname = self._norm_code(panel_name)
        if not comp or not pname:
            return {"ok": False, "message": "Panel company and CompCatID are required"}
        value = 1 if enabled else 0
        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE address
                    SET Active=%s,
                        showinHC=%s,
                        updated_at=NOW()
                    WHERE category=%s
                      AND TRIM(pname)=%s
                      AND UPPER(TRIM(Atype)) = 'C'
                    """,
                    (value, value, comp, pname),
                )
                affected = int(cur.rowcount or 0)
                cur.execute(
                    """
                    UPDATE panelrates
                    SET Active = CASE WHEN BookedFlag = 1 THEN %s ELSE 0 END,
                        updated_at = NOW()
                    WHERE CompCatID = %s
                    """,
                    (value, comp),
                )
                panelrates_affected = int(cur.rowcount or 0)
            conn.commit()
            self.refresh_panel_catalog()
            return {
                "ok": True,
                "showinHC": value,
                "updated_rows": affected,
                "panelrates_updated_rows": panelrates_affected,
            }
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def panel_tests_by_company(self, comp_cat_id: str):
        self.preload_panel_catalog()
        ccid = self._norm_code(comp_cat_id)
        if not ccid:
            return []
        rows = self._panel_catalog.get("tests_search_by_comp", {}).get(ccid, []) or []
        seen = set()
        out = []
        for row in rows:
            key = self._norm_code(row.get("booked_code")) or self._norm_code(row.get("testcode1")) or self._norm_code(row.get("test_code"))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "test_name": self._norm_code(row.get("description")),
                    "charge": row.get("charge"),
                    "mrp": row.get("mrp"),
                    "max_discount": row.get("max_discount"),
                    "tat_raw": self._norm_code(row.get("tat_raw")),
                    "tat_normalized": self._norm_code(row.get("tat_normalized")),
                    "booked_code": key,
                }
            )
        out.sort(key=lambda r: self._norm_code(r.get("test_name")).lower())
        return out

    def update_test_tats(self, items):
        rows = [
            (self._norm_code(x.get("tat_raw")), self._norm_code(x.get("test_code")))
            for x in (items or [])
            if self._norm_code(x.get("test_code"))
        ]
        if not rows:
            return {"ok": False, "message": "No TAT changes found"}
        conn = get_bhasin7001_connection()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    UPDATE test_tat_master
                    SET tat_raw=%s,
                        updated_at=NOW()
                    WHERE test_code=%s
                    """,
                    rows,
                )
            conn.commit()
            self.refresh_panel_catalog()
            return {"ok": True, "updated_rows": len(rows)}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def _panel_rate_lookup_key(self, comp_cat_id: str, center_id: str | None = None) -> str:
        ccid = self._norm_code(comp_cat_id)
        center = self._norm_code(center_id)
        return f"{ccid}|{center}" if ccid and center else ccid

    def panel_groups(self, comp_cat_id: str, center_id: str | None = None):
        self.preload_panel_catalog()
        ccid = self._panel_rate_lookup_key(comp_cat_id, center_id)
        if not ccid:
            return []
        groups = self._panel_catalog["groups_by_comp"].get(ccid, {})
        return [groups[k] for k in sorted(groups.keys())]

    def panel_subgroups(self, comp_cat_id: str, gcode: str, center_id: str | None = None):
        self.preload_panel_catalog()
        ccid = self._panel_rate_lookup_key(comp_cat_id, center_id)
        gc = (gcode or "").strip()
        if not ccid or not gc:
            return []
        subgroups = self._panel_catalog["subgroups_by_comp_g"].get((ccid, gc), {})
        return [subgroups[k] for k in sorted(subgroups.keys())]

    def panel_tests(self, comp_cat_id: str, gcode: str, scode: str, center_id: str | None = None):
        self.preload_panel_catalog()
        ccid = self._panel_rate_lookup_key(comp_cat_id, center_id)
        gc = (gcode or "").strip()
        sc = (scode or "").strip()
        if not ccid or not gc or not sc:
            return []
        return self._panel_catalog["tests_by_comp_g_s"].get((ccid, gc, sc), [])

    def search_panel_tests(self, comp_cat_id: str, query: str, limit: int = 50, center_id: str | None = None):
        self.preload_panel_catalog()
        ccid = self._panel_rate_lookup_key(comp_cat_id, center_id)
        q = self._norm_code(query).lower()
        if not ccid or len(q) < 2:
            return []

        try:
            safe_limit = max(1, min(int(limit or 50), 100))
        except Exception:
            safe_limit = 50

        matches = []
        rows = self._panel_catalog["tests_search_by_comp"].get(ccid, [])
        for row in rows:
            desc_lc = self._norm_code(row.get("description_lc")).lower()
            shortname_lc = self._norm_code(row.get("shortname_lc")).lower()
            if q not in desc_lc and q not in shortname_lc:
                continue
            matches.append(dict(row))

        matches.sort(key=lambda x: (
            self._norm_code(x.get("description")).lower(),
            self._norm_code(x.get("gcode")),
            self._norm_code(x.get("scode")),
            self._norm_code(x.get("booked_code")),
        ))
        return matches[:safe_limit]

    def panel_child_tests(self, parent_gcode: str, parent_scode: str, parent_test_code: str):
        self.preload_panel_catalog()
        pg = (parent_gcode or "").strip()
        ps = (parent_scode or "").strip()
        pt = (parent_test_code or "").strip()
        if not pg or not ps or not pt:
            return []

        rows = self._panel_catalog["profile_children_map"].get((pg, ps, pt), [])
        out = []
        for row in rows:
            child_key = (
                self._norm_code(row.get("gcode")),
                self._norm_code(row.get("scode")),
                self._norm_code(row.get("test_code")),
            )
            r = dict(row)
            r["has_children"] = bool(self._panel_catalog["profile_children_map"].get(child_key))
            out.append(r)
        return out

    def test_specimen_catalog(self):
        self.preload_panel_catalog()
        tests = {}
        for code1, row in self._panel_catalog["test_by_testcode1"].items():
            tc1 = self._norm_code(code1)
            if not tc1:
                continue
            tests[tc1] = {
                "description": self._norm_code(row.get("description")),
                "specimen_name": self._norm_code(row.get("specimen_name")),
            }

        children_by_testcode1 = {}
        for parent_key, rows in self._panel_catalog["profile_children_map"].items():
            parent = self._panel_catalog["test_by_g_s_testcode"].get(parent_key)
            parent_code1 = self._norm_code((parent or {}).get("testcode1"))
            if not parent_code1:
                continue
            children_by_testcode1.setdefault(parent_code1, [])
            for child in rows:
                child_code1 = self._norm_code(child.get("testcode1"))
                if child_code1 and child_code1 not in children_by_testcode1[parent_code1]:
                    children_by_testcode1[parent_code1].append(child_code1)

        return {
            "tests": tests,
            "children_by_testcode1": children_by_testcode1,
        }

    def confirm_booking(self, caller_id, selected_patients, selected_address_id, selected_snapshot, payload, actor_user_id=None, app_obj=None):
        actor = self._actor(actor_user_id)
        if not caller_id:
            return {"ok": False, "message": "Caller is required"}
        if not selected_patients:
            return {"ok": False, "message": "Select at least one patient"}
        contact_validation = self.validate_selected_patient_contacts(selected_patients)
        if not contact_validation.get("ok"):
            return contact_validation
        if not selected_address_id or not selected_snapshot:
            return {"ok": False, "message": "Select an address"}

        preferred_visit_date = payload.get("preferred_visit_date")
        preferred_time_slot = payload.get("preferred_time_slot")
        if not preferred_visit_date or not preferred_time_slot:
            return {"ok": False, "message": "Visit date and slot are required"}

        if preferred_visit_date < str(date.today()):
            return {"ok": False, "message": "Visit date cannot be in past"}

        tests_meta_map = payload.get("patient_tests_meta_map") or {}
        permanent_tags = self.sanitize_permanent_tags(payload.get("permanent_tags"))
        booking_tags = self.sanitize_transactional_tags(payload.get("booking_tags"))
        final_sub_total, credit_sub_total, paying_sub_total, _base_discount_total, additional_applied, final_discount, total_amount, patient_addl_applied = self._compute_booking_amount_components(
            tests_meta_map,
            payload.get("additional_discount_amount"),
            payload.get("additional_discount_by_patient"),
        )
        patient_final_amounts = self._compute_patient_final_amounts(tests_meta_map, patient_addl_applied)
        paying_amount_store = self._paying_amount_for_store(paying_sub_total, _base_discount_total)

        def _to_num(v):
            try:
                if v is None or v == "":
                    return None
                return float(v)
            except Exception:
                return None

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                booking_tags = self._auto_transactional_tags(
                    cur,
                    caller_id=caller_id,
                    selected_patients=selected_patients,
                    total_amount=total_amount,
                    existing_tags=booking_tags,
                    include_new_patient=bool(payload.get("_new_caller_created")),
                )
                duplicate_booking_id = self._find_recent_web_booking_duplicate_id(
                    cur,
                    caller_id=int(caller_id or 0),
                    preferred_visit_date=str(preferred_visit_date or ""),
                    preferred_time_slot=self._norm_code(preferred_time_slot),
                    created_by=actor,
                    window_minutes=10,
                )
                if duplicate_booking_id > 0:
                    conn.rollback()
                    return {
                        "ok": False,
                        "message": f"Booking already created within last 10 minutes: id = {duplicate_booking_id}",
                        "duplicate_booking_id": duplicate_booking_id,
                    }
                tmp = self._temp_code("HHCB-TMP-")
                cur.execute(
                    """
                    INSERT INTO hhome_collection_booking
                    (booking_code, caller_id, selected_address_id, address_snapshot_json,
                     preferred_visit_date, preferred_time_slot, booking_status,
                     strt_time, cmplt_time, intrnl_rfrncd_by, lead_id, remarks, assigned_phlebotomist_id,
                     booking_tags, F_Apt_Am, credit_amount, paying_amount, F_dis, Ad_dis, total_amount, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,0,NULL,NULL,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        tmp,
                        caller_id,
                        selected_address_id,
                        hto_json(selected_snapshot),
                        preferred_visit_date,
                        preferred_time_slot,
                        payload.get("intrnl_rfrncd_by") or None,
                        payload.get("lead_id") or None,
                        payload.get("remarks") or None,
                        booking_tags or None,
                        final_sub_total,
                        credit_sub_total,
                        paying_amount_store,
                        final_discount,
                        additional_applied,
                        total_amount,
                        actor,
                    ),
                )
                booking_id = cur.lastrowid
                booking_code = self._booking_code_from_id(booking_id, preferred_visit_date)
                cur.execute("UPDATE hhome_collection_booking SET booking_code=%s WHERE id=%s", (booking_code, booking_id))

                seen_patients = set()
                for item in selected_patients:
                    pid = int(item["patient_id"])
                    if pid in seen_patients:
                        continue
                    seen_patients.add(pid)
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    comp_ids_csv, charge_modes_csv, panel_names_csv = self._patient_panel_meta_csv(patient_meta)
                    cat_details_csv = self._patient_cat_details_csv(patient_meta)
                    cur.execute(
                        """
                        INSERT INTO hhome_collection_booking_patient
                        (booking_id, patient_id, cce_level_TBS, ref_by, permanent_reference,
                         selected_comp_cat_ids, selected_cat_details, selected_charge_modes, selected_panel_companies, patient_final_amount, additional_discount_amount, created_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            booking_id,
                            pid,
                            self._patient_tbs_value_for_save(patient_meta),
                            self._norm_code(patient_meta.get("referred_by")) or None,
                            self._norm_code(patient_meta.get("permanent_reference")) or None,
                            comp_ids_csv or None,
                            cat_details_csv or None,
                            charge_modes_csv or None,
                            panel_names_csv or None,
                            float(patient_final_amounts.get(pid) or 0),
                            float(patient_addl_applied.get(pid) or 0),
                            actor,
                        ),
                    )
                    booking_patient_id = cur.lastrowid

                    panel_sections = self._patient_panel_sections(patient_meta)
                    duplicate_check = self._validate_patient_test_duplicates(pid, panel_sections)
                    if not duplicate_check.get("ok"):
                        conn.rollback()
                        return duplicate_check

                    for section in panel_sections:
                        panel = section.get("panel") or {}
                        billing = section.get("billing") or {}
                        selected_charge_mode = self._selected_charge_mode(billing)
                        selected_tests = section.get("selected_tests") or []
                        show_mrp = self._section_show_mrp(section)

                        for t in selected_tests:
                            booked_code = self._norm_code(t.get("booked_code"))
                            if not booked_code:
                                continue
                            test_name = self._norm_code(t.get("description") or booked_code)
                            norm_mrp, norm_charge, norm_max_discount = self._normalized_test_pricing_for_mode(t, billing, show_mrp=show_mrp)
                            cur.execute(
                                """
                                INSERT INTO hhome_collection_booking_patient_test
                                (booking_id, booking_patient_id, patient_id, comp_cat_id, charge_mode,
                                 booked_code, test_name, charge, mrp, max_discount, test_status, created_by)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON DUPLICATE KEY UPDATE
                                comp_cat_id=VALUES(comp_cat_id),
                                charge_mode=VALUES(charge_mode),
                                booked_code=VALUES(booked_code),
                                test_name=VALUES(test_name),
                                charge=VALUES(charge),
                                mrp=VALUES(mrp),
                                max_discount=VALUES(max_discount),
                                test_status=VALUES(test_status)
                                """,
                                (
                                    booking_id,
                                    booking_patient_id,
                                    pid,
                                    self._norm_code(billing.get("comp_cat_id")),
                                    selected_charge_mode or None,
                                    booked_code,
                                    test_name,
                                    norm_charge,
                                    norm_mrp,
                                    norm_max_discount,
                                    TEST_STATUS_PENDING,
                                    actor,
                                ),
                            )

                tbs_validation = self._validate_prescription_required_for_tbs(
                    cur,
                    sorted([int(x) for x in seen_patients if int(x) > 0]),
                    tests_meta_map,
                    session_ref=payload.get("_session_ref"),
                )
                if not tbs_validation.get("ok"):
                    conn.rollback()
                    return tbs_validation

                if permanent_tags:
                    linked_ids = self._linked_patient_ids_for_caller(cur, caller_id)
                    if linked_ids:
                        placeholders = ",".join(["%s"] * len(linked_ids))
                        cur.execute(
                            f"SELECT id, tag FROM hpatient_master WHERE id IN ({placeholders})",
                            tuple(linked_ids),
                        )
                        rows = cur.fetchall() or []
                        for row in rows:
                            pid = int((row or {}).get("id") or 0)
                            if pid <= 0:
                                continue
                            merged_tag = self._merge_tag_csv((row or {}).get("tag"), permanent_tags)
                            cur.execute(
                                """
                                UPDATE hpatient_master
                                SET tag=%s, updated_by=%s
                                WHERE id=%s
                                """,
                                (merged_tag or None, actor, pid),
                            )

                patient_ids = sorted([int(x) for x in seen_patients if int(x) > 0])
                patient_rows = []
                if patient_ids:
                    placeholders = ",".join(["%s"] * len(patient_ids))
                    cur.execute(
                        f"""
                        SELECT p.title, p.full_name, p.contact_mobile, p.panel_company, bp.ref_by
                        FROM hhome_collection_booking_patient bp
                        INNER JOIN hpatient_master p ON p.id = bp.patient_id
                        WHERE bp.booking_id=%s
                          AND bp.patient_id IN ({placeholders})
                        ORDER BY p.full_name
                        """,
                        (booking_id, *tuple(patient_ids)),
                    )
                    patient_rows = cur.fetchall() or []
                local_whatsapp_targets = self._extra_local_whatsapp_targets(
                    cur,
                    patient_rows,
                    payload.get("intrnl_rfrncd_by"),
                )
                self._recalculate_followup_required(cur, booking_id)
                conn.commit()

                whatsapp_payloads = []
                for recipient in self._patient_whatsapp_recipients(patient_rows):
                    whatsapp_payloads.append(
                        {
                            "booking_id": booking_id,
                            "booking_code": booking_code,
                            "recipient": recipient,
                            "patient_names": self._format_patient_names_for_whatsapp(patient_rows),
                            "preferred_time_slot": self._norm_code(preferred_time_slot),
                            "preferred_visit_date": self._format_booking_date_for_whatsapp(preferred_visit_date),
                            "address": self._format_booking_address_for_whatsapp(selected_snapshot),
                        }
                    )
                local_whatsapp_payload = {
                    "booking_id": booking_id,
                    "booking_code": booking_code,
                    "patient_names": self._format_patient_names_for_whatsapp(patient_rows),
                    "preferred_time_slot": self._norm_code(preferred_time_slot),
                    "preferred_visit_date": self._format_booking_date_for_whatsapp(preferred_visit_date),
                    "address": self._format_booking_address_for_whatsapp(selected_snapshot),
                }

                prescription_merge = {"ok": True, "moved": 0}
                if payload.get("_session_ref") is not None:
                    prescription_merge = self.merge_staged_prescriptions(
                        payload.get("_session_ref"),
                        booking_code,
                        booking_id,
                        actor_user_id=actor,
                    )
                result = {
                    "ok": True,
                    "booking_id": booking_id,
                    "booking_code": booking_code,
                    "print_url": f"/hhome-collection/print/{booking_id}",
                }
                queued_count = 0
                for item in whatsapp_payloads:
                    if self._queue_booking_whatsapp(app_obj, item):
                        queued_count += 1
                local_queued_count = 0
                if local_whatsapp_targets:
                    local_queued_count = self._queue_local_whatsapp_message(
                        app_obj,
                        log_prefix="hc booking local whatsapp",
                        booking_id=int(booking_id),
                        targets=local_whatsapp_targets,
                        message=self._booking_local_whatsapp_message(local_whatsapp_payload),
                    )
                result["whatsapp_queued"] = bool(queued_count)
                result["whatsapp_queued_count"] = queued_count
                result["local_whatsapp_queued_count"] = local_queued_count
                if not prescription_merge.get("ok"):
                    result["prescription_warning"] = prescription_merge.get("message") or "Prescription files could not be finalized"
                return result
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def dashboard_rows(self, params):
        filters = []
        values = []
        sort_key = self._norm_code(params.get("sort_key")).lower()
        sort_dir = self._norm_code(params.get("sort_dir")).lower()
        sort_dir = "desc" if sort_dir == "desc" else "asc"

        try:
            page = int(params.get("page") or 1)
        except Exception:
            page = 1
        if page <= 0:
            page = 1

        page_size_raw = self._norm_code(params.get("page_size")).lower() or "50"
        allow_all = page_size_raw == "all"
        try:
            page_size = int(page_size_raw) if not allow_all else 0
        except Exception:
            page_size = 50
        if not allow_all:
            if page_size <= 0:
                page_size = 50
            if page_size not in (50, 100, 200):
                page_size = 50

        if params.get("date_from"):
            filters.append("hcb.preferred_visit_date >= %s")
            values.append(params["date_from"])
        if params.get("date_to"):
            filters.append("hcb.preferred_visit_date <= %s")
            values.append(params["date_to"])
        if params.get("status") is not None and str(params.get("status")).strip() != "":
            status_raw = str(params.get("status")).strip()
            status_map = {
                "Pending": 0,
                "Assigned": 1,
                "Started": 2,
                "Completed": 3,
                "Cancelled": 4,
            }
            status_val = status_map.get(status_raw, status_raw)
            filters.append("hcb.booking_status = %s")
            values.append(status_val)
        if params.get("cce_tbs") is not None and str(params.get("cce_tbs")).strip() != "":
            cce_tbs_raw = str(params.get("cce_tbs")).strip()
            filters.append(
                """EXISTS (
                    SELECT 1
                    FROM hhome_collection_booking_patient hbp_tbs
                    WHERE hbp_tbs.booking_id = hcb.id
                      AND TRIM(COALESCE(hbp_tbs.cce_level_TBS, '')) = %s
                )"""
            )
            values.append(cce_tbs_raw)
        if params.get("search"):
            filters.append(
                """(
                    CAST(hcb.id AS CHAR) LIKE %s
                    OR cm.primary_mobile LIKE %s
                    OR TRIM(COALESCE(u.name, '')) LIKE %s
                    OR TRIM(COALESCE(up.name, '')) LIKE %s
                    OR EXISTS (
                      SELECT 1
                      FROM hhome_collection_booking_patient hbp2
                      INNER JOIN hpatient_master p2 ON p2.id = hbp2.patient_id
                      WHERE hbp2.booking_id = hcb.id
                        AND CONCAT_WS(' ', p2.title, p2.full_name) LIKE %s
                    )
                )"""
            )
            values.extend([f"%{params['search']}%"] * 5)

        where_clause = "WHERE " + " AND ".join(filters) if filters else ""
        where_clause_appt = where_clause
        where_clause_appt = where_clause_appt.replace("hcb.preferred_visit_date", "ap.preferred_visit_date")
        where_clause_appt = where_clause_appt.replace("hcb.booking_status", "ap.appointment_status")
        where_clause_appt = where_clause_appt.replace("CAST(hcb.id AS CHAR)", "CAST(ap.id AS CHAR)")

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                has_appointment_table = self._table_exists(cur, "hhome_collection_booking_appointment")
                booking_select = f"""
                    SELECT
                        'BOOKING' AS row_type,
                        hcb.id AS id,
                        hcb.id AS booking_id,
                        NULL AS appointment_id,
                        NULL AS appointment_no,
                        hcb.preferred_visit_date,
                        hcb.preferred_time_slot,
                        hcb.booking_status,
                        hcb.booking_tags,
                        GROUP_CONCAT(
                          DISTINCT NULLIF(TRIM(COALESCE(hcbp.cce_level_TBS, '')), '')
                          ORDER BY p.full_name SEPARATOR ','
                        ) AS cce_tbs_values,
                        hcb.total_amount,
                        MAX(TRIM(COALESCE(u.name, ''))) AS booked_by_name,
                        MAX(TRIM(COALESCE(up.name, ''))) AS assigned_phlebo_name,
                        cm.full_name AS caller_name,
                        cm.primary_mobile,
                        am.colony_name,
                        am.route_no,
                        hcb.booking_code,
                        EXISTS (
                          SELECT 1
                          FROM hhome_collection_booking_patient hbp_tag
                          INNER JOIN hpatient_master p_tag ON p_tag.id = hbp_tag.patient_id
                          WHERE hbp_tag.booking_id = hcb.id
                            AND TRIM(COALESCE(p_tag.tag, '')) <> ''
                        ) AS has_patient_tag,
                        COUNT(hcbp.id) AS patient_count,
                        GROUP_CONCAT(
                          DISTINCT TRIM(CONCAT_WS(' ', p.title, p.full_name))
                          ORDER BY p.full_name SEPARATOR ', '
                        ) AS patient_names,
                        GROUP_CONCAT(
                          DISTINCT NULLIF(TRIM(COALESCE(p.contact_mobile, '')), '')
                          ORDER BY p.full_name SEPARATOR ', '
                        ) AS patient_primary_mobiles,
                        NULL AS selected_patient_ids_json,
                        NULL AS address_snapshot_json,
                        NULL AS appointment_tests_snapshot_json,
                        NULL AS payment_snapshot_json
                    FROM hhome_collection_booking hcb
                    INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                    INNER JOIN haddress_master am ON am.id = hcb.selected_address_id
                    LEFT JOIN users u ON u.id = hcb.created_by
                    LEFT JOIN users up ON up.id = hcb.assigned_phlebotomist_id
                    LEFT JOIN hhome_collection_booking_patient hcbp ON hcbp.booking_id = hcb.id
                    LEFT JOIN hpatient_master p ON p.id = hcbp.patient_id
                    {where_clause}
                    GROUP BY hcb.id
                """

                union_parts = [booking_select]
                if has_appointment_table:
                    appointment_select = f"""
                        SELECT
                            'APPOINTMENT' AS row_type,
                            ap.id AS id,
                            ap.booking_id AS booking_id,
                            ap.id AS appointment_id,
                            ap.appointment_no,
                            ap.preferred_visit_date,
                            ap.preferred_time_slot,
                            ap.appointment_status AS booking_status,
                            hcb.booking_tags,
                            GROUP_CONCAT(
                              DISTINCT NULLIF(TRIM(COALESCE(hcbp.cce_level_TBS, '')), '')
                              ORDER BY p.full_name SEPARATOR ','
                            ) AS cce_tbs_values,
                            hcb.total_amount,
                            MAX(TRIM(COALESCE(u.name, ''))) AS booked_by_name,
                            MAX(TRIM(COALESCE(up.name, ''))) AS assigned_phlebo_name,
                            cm.full_name AS caller_name,
                            cm.primary_mobile,
                            am.colony_name,
                            am.route_no,
                            hcb.booking_code,
                            EXISTS (
                              SELECT 1
                              FROM hhome_collection_booking_patient hbp_tag
                              INNER JOIN hpatient_master p_tag ON p_tag.id = hbp_tag.patient_id
                              WHERE hbp_tag.booking_id = hcb.id
                                AND TRIM(COALESCE(p_tag.tag, '')) <> ''
                            ) AS has_patient_tag,
                            COUNT(hcbp.id) AS patient_count,
                            GROUP_CONCAT(
                              DISTINCT TRIM(CONCAT_WS(' ', p.title, p.full_name))
                              ORDER BY p.full_name SEPARATOR ', '
                            ) AS patient_names,
                            GROUP_CONCAT(
                              DISTINCT NULLIF(TRIM(COALESCE(p.contact_mobile, '')), '')
                              ORDER BY p.full_name SEPARATOR ', '
                            ) AS patient_primary_mobiles,
                            ap.selected_patient_ids_json,
                            ap.address_snapshot_json,
                            ap.appointment_tests_snapshot_json,
                            ap.payment_snapshot_json
                        FROM hhome_collection_booking_appointment ap
                        INNER JOIN hhome_collection_booking hcb ON hcb.id = ap.booking_id
                        INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                        LEFT JOIN users u ON u.id = hcb.created_by
                        LEFT JOIN users up ON up.id = ap.assigned_phlebotomist_id
                        LEFT JOIN haddress_master am ON am.id = ap.selected_address_id
                        LEFT JOIN hhome_collection_booking_patient hcbp ON hcbp.booking_id = hcb.id
                        LEFT JOIN hpatient_master p ON p.id = hcbp.patient_id
                        {where_clause_appt}
                        GROUP BY ap.id
                    """
                    union_parts.append(appointment_select)

                union_query = "\nUNION ALL\n".join(union_parts)

                if sort_key == "route":
                    order_clause = f"""
                        ORDER BY
                            route_no {sort_dir.upper()},
                            preferred_visit_date DESC,
                            id DESC
                    """
                elif sort_key == "total_amount":
                    order_clause = f"""
                        ORDER BY total_amount {sort_dir.upper()}, preferred_visit_date DESC, id DESC
                    """
                elif sort_key == "assigned_phlebo":
                    order_clause = f"""
                        ORDER BY assigned_phlebo_name {sort_dir.upper()}, preferred_visit_date DESC, id DESC
                    """
                else:
                    order_clause = """
                        ORDER BY preferred_visit_date DESC, id DESC
                    """

                count_query = f"SELECT COUNT(*) AS total_count FROM ({union_query}) dashboard_union"
                cur.execute(count_query, values + (values if has_appointment_table else []))
                total_count = int((cur.fetchone() or {}).get("total_count") or 0)

                stats_query = f"""
                    SELECT booking_status, COUNT(*) AS row_count
                    FROM ({union_query}) dashboard_union
                    GROUP BY booking_status
                """
                cur.execute(stats_query, values + (values if has_appointment_table else []))
                status_rows = cur.fetchall() or []
                status_counts = {
                    "total": total_count,
                    "pending": 0,
                    "started": 0,
                    "cancelled": 0,
                }
                for item in status_rows:
                    code = int(item.get("booking_status") or 0)
                    count_val = int(item.get("row_count") or 0)
                    if code == 0:
                        status_counts["pending"] = count_val
                    elif code == 2:
                        status_counts["started"] = count_val
                    elif code == 4:
                        status_counts["cancelled"] = count_val

                if allow_all:
                    data_query = f"SELECT * FROM ({union_query}) dashboard_union {order_clause}"
                    current_page = 1
                    total_pages = 1 if total_count else 0
                    effective_page_size = "all"
                    data_values = values + (values if has_appointment_table else [])
                else:
                    total_pages = (total_count + page_size - 1) // page_size if total_count else 0
                    current_page = min(page, total_pages) if total_pages else 1
                    offset = (current_page - 1) * page_size
                    data_query = f"SELECT * FROM ({union_query}) dashboard_union {order_clause} LIMIT %s OFFSET %s"
                    effective_page_size = page_size
                    data_values = values + (values if has_appointment_table else []) + [page_size, offset]

                cur.execute(data_query, data_values)
                raw_rows = cur.fetchall() or []

                visible_booking_ids = sorted({
                    int((r or {}).get("booking_id") or 0)
                    for r in (raw_rows or [])
                    if int((r or {}).get("booking_id") or 0) > 0
                })
                visible_appointment_ids = sorted({
                    int((r or {}).get("appointment_id") or 0)
                    for r in (raw_rows or [])
                    if self._norm_code((r or {}).get("row_type")).upper() == "APPOINTMENT"
                    and int((r or {}).get("appointment_id") or 0) > 0
                })
                batched_booking_ids = set()
                batched_appointment_ids = set()
                if visible_booking_ids or visible_appointment_ids:
                    cur.execute(
                        """
                        SELECT booking_ids, patients_json, tubes_json
                        FROM hhome_collection_batch
                        WHERE booking_ids IS NOT NULL
                           AND TRIM(booking_ids) <> ''
                        """
                    )
                    visible_set = set(visible_booking_ids)
                    visible_appt_set = set(visible_appointment_ids)
                    for br in (cur.fetchall() or []):
                        raw_booking_ids = br.get("booking_ids")
                        try:
                            parsed = json.loads(raw_booking_ids) if isinstance(raw_booking_ids, str) else (raw_booking_ids or [])
                        except Exception:
                            parsed = []
                        if not isinstance(parsed, list):
                            parsed = []
                        for bid_raw in parsed:
                            try:
                                bid = int(bid_raw or 0)
                            except Exception:
                                bid = 0
                            if bid in visible_set:
                                batched_booking_ids.add(bid)
                        for json_key in ("patients_json", "tubes_json"):
                            raw_items = br.get(json_key)
                            try:
                                items = json.loads(raw_items) if isinstance(raw_items, str) else (raw_items or [])
                            except Exception:
                                items = []
                            if not isinstance(items, list):
                                continue
                            for item in items:
                                if not isinstance(item, dict):
                                    continue
                                if self._norm_code(item.get("source_type")).upper() != "APPOINTMENT":
                                    continue
                                try:
                                    appt_id = int(item.get("appointment_id") or 0)
                                except Exception:
                                    appt_id = 0
                                if appt_id in visible_appt_set:
                                    batched_appointment_ids.add(appt_id)

                appointment_name_overrides = {}
                appointment_rows = [dict(r) for r in raw_rows if self._norm_code(r.get("row_type")).upper() == "APPOINTMENT"]
                for ar in appointment_rows:
                    appt_id = int((ar or {}).get("appointment_id") or 0)
                    if appt_id <= 0:
                        continue
                    selected_ids = self._patient_ids_from_json((ar or {}).get("selected_patient_ids_json"))
                    if not selected_ids:
                        continue
                    placeholders = ",".join(["%s"] * len(selected_ids))
                    cur.execute(
                        f"""
                        SELECT TRIM(CONCAT_WS(' ', p.title, p.full_name)) AS patient_name
                        FROM hhome_collection_booking_patient hbp
                        INNER JOIN hpatient_master p ON p.id = hbp.patient_id
                        WHERE hbp.booking_id = %s
                          AND hbp.patient_id IN ({placeholders})
                        ORDER BY p.full_name
                        """,
                        tuple([int((ar or {}).get("booking_id") or 0)] + [int(x) for x in selected_ids]),
                    )
                    names = [self._norm_code(x.get("patient_name")) for x in (cur.fetchall() or [])]
                    names = [n for n in names if n]
                    if names:
                        appointment_name_overrides[appt_id] = ", ".join(names)

                rows = []
                for r in raw_rows:
                    x = dict(r)
                    if self._norm_code(x.get("row_type")).upper() == "APPOINTMENT":
                        x["allow_book_appointment"] = False
                        pay_summary = self._appointment_payment_summary(x.get("payment_snapshot_json"))
                        snap_total = self._compute_appointment_selected_tests_total(x.get("appointment_tests_snapshot_json"))
                        x["total_amount"] = float(snap_total or pay_summary.get("total_amount") or 0)
                        try:
                            ap_addr = json.loads(x.get("address_snapshot_json") or "{}")
                        except Exception:
                            ap_addr = {}
                        if isinstance(ap_addr, dict):
                            x["colony_name"] = self._norm_code(ap_addr.get("colony_name")) or x.get("colony_name")
                            x["route_no"] = self._norm_code(ap_addr.get("route_no")) or x.get("route_no")
                        appt_id = int(x.get("appointment_id") or 0)
                        if appt_id in appointment_name_overrides:
                            x["patient_names"] = appointment_name_overrides[appt_id]
                            x["patient_count"] = len([n for n in x["patient_names"].split(",") if n.strip()])
                        x["is_batched"] = appt_id in batched_appointment_ids
                    else:
                        x["appointment_id"] = None
                        x["appointment_no"] = None
                        x["allow_book_appointment"] = True
                        x["is_batched"] = int(x.get("booking_id") or 0) in batched_booking_ids
                    rows.append(x)

                return {
                    "rows": rows,
                    "status_counts": status_counts,
                    "total_count": total_count,
                    "page": current_page,
                    "page_size": effective_page_size,
                    "total_pages": total_pages,
                }
        finally:
            conn.close()

    def validate_selected_patient_contacts(self, selected_patients: list):
        patient_ids = [int(item.get("patient_id") or 0) for item in (selected_patients or [])]
        patient_ids = [pid for pid in patient_ids if pid > 0]
        if not patient_ids:
            return {"ok": True}

        conn = get_db_connection()
        try:
            placeholders = ",".join(["%s"] * len(patient_ids))
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, CONCAT_WS(' ', title, full_name) AS full_name, contact_mobile
                    FROM hpatient_master
                    WHERE id IN ({placeholders})
                      AND COALESCE(patient_status, 0) = 0
                    """,
                    patient_ids,
                )
                rows = cur.fetchall() or []
            rows_by_id = {int(r["id"]): r for r in rows}
            missing = []
            for pid in patient_ids:
                row = rows_by_id.get(pid)
                if not row or not self.normalize_mobile(row.get("contact_mobile")):
                    missing.append((row or {}).get("full_name") or f"Patient {pid}")
            if missing:
                names = ", ".join(missing[:5])
                more = f" +{len(missing) - 5} more" if len(missing) > 5 else ""
                return {"ok": False, "message": f"Contact No is required for selected patient: {names}{more}"}
            return {"ok": True}
        finally:
            conn.close()

    def get_booking_audit_trail(self, booking_id: int):
        bid = int(booking_id or 0)
        if bid <= 0:
            return {"ok": False, "message": "Valid booking id is required"}

        def _json_obj(raw):
            if not raw:
                return None
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                return {"raw": str(raw)}
            return data if isinstance(data, (dict, list)) else {"value": data}

        def _audit_source_label(old_values, new_values):
            for data in (new_values, old_values):
                if isinstance(data, dict) and self._norm_code(data.get("row_type")).upper() == "APPOINTMENT":
                    return "Linked Appointment"
            return "New Appointment"

        def _collect_assigned_user_ids(value, found):
            if isinstance(value, dict):
                if "assigned_phlebotomist_id" in value:
                    try:
                        uid = int(value.get("assigned_phlebotomist_id") or 0)
                    except Exception:
                        uid = 0
                    if uid > 0:
                        found.add(uid)
                for item in value.values():
                    _collect_assigned_user_ids(item, found)
            elif isinstance(value, list):
                for item in value:
                    _collect_assigned_user_ids(item, found)

        def _clean_audit_value(value, assigned_user_names):
            if isinstance(value, list):
                return [_clean_audit_value(item, assigned_user_names) for item in value]
            if not isinstance(value, dict):
                return value
            cleaned = {}
            for key, item in value.items():
                if key in {"row_type", "patient_id"}:
                    continue
                if key == "assigned_phlebotomist_id":
                    try:
                        uid = int(item or 0)
                    except Exception:
                        uid = 0
                    cleaned["assigned_phlebotomist"] = assigned_user_names.get(uid) or (f"User #{uid}" if uid else "-")
                    continue
                cleaned[key] = _clean_audit_value(item, assigned_user_names)
            return cleaned

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        b.id,
                        b.booking_code,
                        b.booking_status,
                        b.preferred_visit_date,
                        b.preferred_time_slot,
                        COALESCE(NULLIF(TRIM(u_book.name), ''), '-') AS booked_by,
                        COALESCE(NULLIF(TRIM(u_ph.name), ''), '-') AS assigned_to,
                        GROUP_CONCAT(
                            DISTINCT NULLIF(TRIM(CONCAT_WS(' ', p.title, p.full_name)), '')
                            ORDER BY p.full_name
                            SEPARATOR ', '
                        ) AS patient_names
                    FROM hhome_collection_booking b
                    LEFT JOIN users u_book ON u_book.id = b.created_by
                    LEFT JOIN users u_ph ON u_ph.id = b.assigned_phlebotomist_id
                    LEFT JOIN hhome_collection_booking_patient bp ON bp.booking_id = b.id
                    LEFT JOIN hpatient_master p ON p.id = bp.patient_id
                    WHERE b.id = %s
                    GROUP BY b.id
                    """,
                    (bid,),
                )
                booking = cur.fetchone()
                if not booking:
                    return {"ok": False, "message": "Booking not found"}

                cur.execute(
                    """
                    SELECT
                        a.id,
                        a.booking_id,
                        a.action_type,
                        a.reason_text,
                        a.old_values_json,
                        a.new_values_json,
                        a.done_by,
                        a.done_at,
                        COALESCE(NULLIF(TRIM(u.name), ''), CONCAT('User #', a.done_by), '-') AS done_by_name
                    FROM hbooking_action_audit a
                    LEFT JOIN users u ON u.id = a.done_by
                    WHERE a.booking_id = %s
                    ORDER BY a.done_at DESC, a.id DESC
                    """,
                    (bid,),
                )
                raw_audits = cur.fetchall() or []
                assigned_user_ids = set()
                parsed_audits = []
                for row in raw_audits:
                    old_values = _json_obj(row.get("old_values_json"))
                    new_values = _json_obj(row.get("new_values_json"))
                    _collect_assigned_user_ids(old_values, assigned_user_ids)
                    _collect_assigned_user_ids(new_values, assigned_user_ids)
                    parsed_audits.append((row, old_values, new_values))

                assigned_user_names = {}
                if assigned_user_ids:
                    placeholders = ",".join(["%s"] * len(assigned_user_ids))
                    cur.execute(
                        f"SELECT id, COALESCE(NULLIF(TRIM(name), ''), CONCAT('User #', id)) AS name FROM users WHERE id IN ({placeholders})",
                        tuple(sorted(assigned_user_ids)),
                    )
                    assigned_user_names = {int(r.get("id") or 0): self._norm_code(r.get("name")) for r in (cur.fetchall() or [])}

                audits = []
                for row, old_values, new_values in parsed_audits:
                    done_at = row.get("done_at")
                    audits.append(
                        {
                            "id": int(row.get("id") or 0),
                            "booking_id": int(row.get("booking_id") or 0),
                            "action_type": self._norm_code(row.get("action_type")) or "-",
                            "source_label": _audit_source_label(old_values, new_values),
                            "reason_text": self._norm_code(row.get("reason_text")) or "",
                            "done_by": int(row.get("done_by") or 0),
                            "done_by_name": self._norm_code(row.get("done_by_name")) or "-",
                            "done_at": done_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(done_at, "strftime") else self._norm_code(done_at),
                            "old_values": _clean_audit_value(old_values, assigned_user_names),
                            "new_values": _clean_audit_value(new_values, assigned_user_names),
                        }
                    )

                visit_date = booking.get("preferred_visit_date")
                booking["preferred_visit_date"] = visit_date.isoformat() if hasattr(visit_date, "isoformat") else self._norm_code(visit_date)
                booking["status_label"] = self._booking_status_label(int(booking.get("booking_status") or 0))
                return {"ok": True, "booking": booking, "audits": audits}
        finally:
            conn.close()

    def is_trf_ready_for_patient(self, booking_id: int, patient_id: int, appointment_id: int = 0) -> bool:
        booking_id = int(booking_id or 0)
        patient_id = int(patient_id or 0)
        appointment_id = int(appointment_id or 0)
        if booking_id <= 0 or patient_id <= 0:
            return False
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT patients_json
                    FROM hhome_collection_batch
                    WHERE booking_ids IS NOT NULL
                      AND TRIM(booking_ids) <> ''
                      AND booking_ids LIKE %s
                    """,
                    (f"%{booking_id}%",),
                )
                for batch_row in (cur.fetchall() or []):
                    raw_items = batch_row.get("patients_json")
                    try:
                        batch_patients = json.loads(raw_items) if isinstance(raw_items, str) else (raw_items or [])
                    except Exception:
                        batch_patients = []
                    if not isinstance(batch_patients, list):
                        continue
                    for item in batch_patients:
                        if not isinstance(item, dict):
                            continue
                        try:
                            item_booking_id = int(item.get("booking_id") or 0)
                            item_patient_id = int(item.get("patient_id") or 0)
                        except Exception:
                            continue
                        if item_booking_id != booking_id or item_patient_id != patient_id:
                            continue
                        item_appt_raw = item.get("appointment_id")
                        try:
                            item_appointment_id = int(item_appt_raw) if item_appt_raw is not None else 0
                        except Exception:
                            item_appointment_id = 0
                        if appointment_id > 0:
                            if item_appointment_id == appointment_id:
                                return True
                        elif item_appointment_id <= 0:
                            return True
                return False
        finally:
            conn.close()

    def get_booking_full(self, booking_id: int, appointment_id: int = 0):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hcb.*, cm.full_name AS caller_name, cm.primary_mobile, cm.caller_code,
                           am.house_flat_no, am.floor, am.block_tower_no, am.street_line, am.landmark, am.google_location,
                           am.colony_name, am.pincode, am.route_no,
                           am.city, am.access_notes
                    FROM hhome_collection_booking hcb
                    INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                    INNER JOIN haddress_master am ON am.id = hcb.selected_address_id
                    WHERE hcb.id=%s
                    """,
                    (booking_id,),
                )
                booking = cur.fetchone()
                if not booking:
                    return None
                selected_patient_ids = []
                appointment_patient_context = {}
                if appointment_id > 0:
                    cur.execute(
                        """
                        SELECT id, booking_id, selected_patient_ids_json, address_snapshot_json,
                               appointment_status,
                               appointment_tests_snapshot_json, payment_snapshot_json, preferred_visit_date, preferred_time_slot
                        FROM hhome_collection_booking_appointment
                        WHERE id=%s AND booking_id=%s
                        LIMIT 1
                        """,
                        (appointment_id, booking_id),
                    )
                    ap = cur.fetchone() or {}
                    appointment_status = int((ap or {}).get("appointment_status") or 0) if ap else 0
                    if ap:
                        selected_patient_ids = self._patient_ids_from_json(ap.get("selected_patient_ids_json"))
                        appointment_patient_context = self._appointment_patient_context(ap.get("payment_snapshot_json"))
                        booking["preferred_visit_date"] = ap.get("preferred_visit_date") or booking.get("preferred_visit_date")
                        booking["preferred_time_slot"] = ap.get("preferred_time_slot") or booking.get("preferred_time_slot")
                        raw_snapshot = ap.get("address_snapshot_json")
                        if raw_snapshot:
                            try:
                                snap = json.loads(raw_snapshot) if isinstance(raw_snapshot, str) else (raw_snapshot or {})
                                if isinstance(snap, dict):
                                    booking["house_flat_no"] = self._norm_code(snap.get("house_flat_no")) or booking.get("house_flat_no")
                                    booking["floor"] = self._norm_code(snap.get("floor")) or booking.get("floor")
                                    booking["block_tower_no"] = self._norm_code(snap.get("block_tower_no")) or booking.get("block_tower_no")
                                    booking["street_line"] = self._norm_code(snap.get("street_line")) or booking.get("street_line")
                                    booking["landmark"] = self._norm_code(snap.get("landmark")) or booking.get("landmark")
                                    booking["city"] = self._norm_code(snap.get("city")) or booking.get("city")
                                    booking["colony_name"] = self._norm_code(snap.get("colony_name")) or booking.get("colony_name")
                                    booking["pincode"] = self._norm_code(snap.get("pincode")) or booking.get("pincode")
                                    booking["route_no"] = self._norm_code(snap.get("route_no")) or booking.get("route_no")
                                    booking["google_location"] = self._norm_code(snap.get("google_location")) or booking.get("google_location")
                                    booking["access_notes"] = self._norm_code(snap.get("access_notes")) or booking.get("access_notes")
                            except Exception:
                                pass

                cur.execute(
                    """
                    SELECT reason_text, new_values_json
                    FROM hbooking_action_audit
                    WHERE booking_id=%s AND action_type='CANCEL'
                    ORDER BY done_at DESC, id DESC
                    LIMIT 1
                    """,
                    (booking_id,),
                )
                cancel_row = cur.fetchone() or {}
                cancel_reason = self._norm_code(cancel_row.get("reason_text"))
                reschedule_to = ""
                new_values = cancel_row.get("new_values_json")
                if new_values:
                    try:
                        new_values = json.loads(new_values) if isinstance(new_values, str) else new_values
                    except Exception:
                        new_values = {}
                    if isinstance(new_values, dict) and new_values.get("reschedule_requested"):
                        proposed_date = self._norm_code(new_values.get("proposed_visit_date"))
                        proposed_slot = self._norm_code(new_values.get("proposed_time_slot"))
                        if proposed_date and proposed_slot:
                            reschedule_to = f"{proposed_date} {proposed_slot}"
                booking["cancel_reason"] = cancel_reason
                booking["reschedule_to"] = reschedule_to

                include_cancelled_patients = bool(
                    int(booking.get("booking_status") or 0) == 4
                    or (appointment_id > 0 and int(appointment_status or 0) == 4)
                )
                patient_status_filter = "" if include_cancelled_patients else "AND COALESCE(hcbp.booking_patient_status, 0) <> 4"
                patient_cols = self._table_columns(cur, "hhome_collection_booking_patient")
                patient_photo_select = (
                    "COALESCE(hcbp.patient_photo_files, '') AS patient_photo_files"
                    if "patient_photo_files" in patient_cols
                    else "'' AS patient_photo_files"
                )
                cur.execute(
                    f"""
                    SELECT p.id AS patient_id, p.patient_code, CONCAT_WS(' ', p.title, p.full_name) AS full_name,
                           p.contact_mobile,
                           hcbp.cce_level_TBS, hcbp.selected_comp_cat_ids, hcbp.selected_charge_modes, hcbp.selected_panel_companies,
                           hcbp.payment_mode, hcbp.due_amount, hcbp.extra_amount,
                           hcbp.ref_by, hcbp.permanent_reference,
                           p.tag, COALESCE(hcbp.selected_panel_companies, '') AS panel_company,
                           p.patient_documents,
                           COALESCE(hcbp.prescription_files, '') AS prescription_files,
                           {patient_photo_select},
                           COALESCE(hcbp.pres_up_by, '') AS pres_up_by,
                           COALESCE(pres_u.name, '') AS pres_uploaded_by_name
                    FROM hhome_collection_booking_patient hcbp
                    INNER JOIN hpatient_master p ON p.id = hcbp.patient_id
                    LEFT JOIN users pres_u
                      ON pres_u.id = CAST(CASE
                        WHEN hcbp.pres_up_by LIKE 'W,%%' THEN SUBSTRING_INDEX(hcbp.pres_up_by, ',', -1)
                        ELSE hcbp.pres_up_by
                      END AS UNSIGNED)
                    WHERE hcbp.booking_id=%s
                      {patient_status_filter}
                    ORDER BY p.full_name
                    """,
                    (booking_id,),
                )
                patients = cur.fetchall() or []
                if appointment_id > 0 and selected_patient_ids:
                    allowed = set(int(x) for x in selected_patient_ids if int(x or 0) > 0)
                    patients = [p for p in patients if int(p.get("patient_id") or 0) in allowed]

                tests_by_patient = {}
                test_detail_by_patient = {}
                panels_by_patient = {}
                used_snapshot = False
                if appointment_id > 0:
                    cur.execute(
                        """
                        SELECT appointment_tests_snapshot_json
                        FROM hhome_collection_booking_appointment
                        WHERE id=%s AND booking_id=%s
                        LIMIT 1
                        """,
                        (appointment_id, booking_id),
                    )
                    snap_row = cur.fetchone() or {}
                    raw_tests_snapshot = snap_row.get("appointment_tests_snapshot_json")
                    if raw_tests_snapshot:
                        try:
                            snap_obj = json.loads(raw_tests_snapshot) if isinstance(raw_tests_snapshot, str) else raw_tests_snapshot
                            tests_map = (snap_obj or {}).get("tests_billing_map") or {}
                            pending_map = (snap_obj or {}).get("pending_tests_map") or {}
                            parent_context_map = (snap_obj or {}).get("parent_context_map") or {}
                            snapshot_flow_type = self._norm_code((snap_obj or {}).get("flow_type")).lower()
                            parent_codes_by_patient = {}
                            if not isinstance(parent_context_map, dict):
                                parent_context_map = {}
                            if not parent_context_map:
                                parent_context_map = self._derive_parent_context_map(tests_map, pending_map)
                            for pc_pid, pc_tb in (parent_context_map or {}).items():
                                try:
                                    pc_pid_i = int(pc_pid)
                                except Exception:
                                    continue
                                code_set = set()
                                panels = (pc_tb or {}).get("panels") or []
                                if not panels and isinstance(pc_tb, dict):
                                    panels = [{
                                        "selected_tests": (pc_tb or {}).get("selected_tests") or [],
                                    }]
                                for sec in panels:
                                    for t in (sec.get("selected_tests") or []):
                                        code = self._norm_code((t or {}).get("booked_code"))
                                        if code:
                                            code_set.add(code)
                                if code_set:
                                    parent_codes_by_patient[pc_pid_i] = code_set
                            # Appointment view fast-path:
                            # avoid heavy full catalog warm-up; resolve only required codes.
                            all_codes = set()
                            for tb in (pending_map or {}).values():
                                p_tests = (tb or {}).get("selected_tests") or []
                                if not p_tests and isinstance((tb or {}).get("panels"), list):
                                    for sec in ((tb or {}).get("panels") or []):
                                        if isinstance(sec, dict):
                                            p_tests.extend(sec.get("selected_tests") or [])
                                for t in p_tests:
                                    c = self._norm_code((t or {}).get("booked_code"))
                                    if c:
                                        all_codes.add(c)
                            for tb in (tests_map or {}).values():
                                for t in ((tb or {}).get("selected_tests") or []):
                                    c = self._norm_code((t or {}).get("booked_code"))
                                    if c:
                                        all_codes.add(c)
                            code_desc_map = self._fetch_test_descriptions_by_code1(all_codes)

                            def _resolve_desc_without_warmup(test_code: str, explicit_desc: str) -> str:
                                code = self._norm_code(test_code)
                                if code:
                                    desc = self._norm_code((code_desc_map or {}).get(code))
                                    if desc:
                                        return desc
                                return explicit_desc or code

                            all_keys = set(tests_map.keys()) | set(pending_map.keys())
                            # Auto follow-up appointments represent only pending child tests.
                            # Parent context remains in the snapshot for audit/modify flows, not detail view.
                            show_pending_only = bool(
                                appointment_id > 0
                                and snapshot_flow_type == "auto_followup_pending_child"
                                and pending_map
                            )
                            show_selected_only = bool(
                                appointment_id > 0
                                and appointment_status in (3, 4)
                                and not show_pending_only
                            )

                            for key in all_keys:
                                try:
                                    pid = int(key)
                                except Exception:
                                    continue
                                if selected_patient_ids and pid not in set(selected_patient_ids):
                                    continue
                                seen = set()
                                parent_codes = set()
                                parent_codes.update(parent_codes_by_patient.get(pid, set()))
                                if not show_pending_only:
                                    tnode = (tests_map.get(key) or {})
                                    panel_sections = (tnode.get("panels") or [])
                                    if not panel_sections and isinstance(tnode, dict):
                                        panel_sections = [{
                                            "panel": tnode.get("panel") or {},
                                            "billing": tnode.get("billing") or {},
                                            "selected_tests": tnode.get("selected_tests") or [],
                                        }]

                                    for sec in panel_sections:
                                        panel_name = self._norm_code(((sec or {}).get("panel") or {}).get("pname"))
                                        selected_mode = self._norm_code((((sec or {}).get("billing") or {}).get("selected_charge_mode")))
                                        if panel_name:
                                            panels_by_patient.setdefault(pid, [])
                                            if panel_name not in panels_by_patient[pid]:
                                                panels_by_patient[pid].append(panel_name)

                                        selected_tests = (sec or {}).get("selected_tests") or []
                                        for t in selected_tests:
                                            code = self._norm_code((t or {}).get("booked_code"))
                                            if not code or code in seen:
                                                continue
                                            seen.add(code)
                                            label = _resolve_desc_without_warmup(code, self._norm_code((t or {}).get("description")))
                                            tests_by_patient.setdefault(pid, []).append(label)
                                            mrp = float((t or {}).get("mrp") or 0)
                                            discount = float((t or {}).get("max_discount") or 0)
                                            final_charge = max(0, mrp - discount)
                                            test_detail_by_patient.setdefault(pid, []).append(
                                                {
                                                    "booked_code": code,
                                                    "test_name": label,
                                                    "panel_company": panel_name,
                                                    "selected_charge_mode": selected_mode,
                                                    "mrp": mrp,
                                                    "discount": discount,
                                                    "final_charge": round(final_charge, 2),
                                                }
                                            )
                                if show_selected_only:
                                    continue
                                pnode = (pending_map.get(key) or {})
                                pending_tests = (pnode.get("selected_tests") or [])
                                if not pending_tests and isinstance(pnode.get("panels"), list):
                                    for sec in (pnode.get("panels") or []):
                                        if isinstance(sec, dict):
                                            pending_tests.extend(sec.get("selected_tests") or [])
                                if not pending_tests and isinstance(pnode.get("items"), list):
                                    for item in (pnode.get("items") or []):
                                        if isinstance(item, dict):
                                            pending_tests.extend(item.get("pending") or [])
                                for t in pending_tests:
                                    code = self._norm_code(t.get("booked_code"))
                                    if not code or code in seen:
                                        continue
                                    seen.add(code)
                                    label = _resolve_desc_without_warmup(code, self._norm_code(t.get("description")))
                                    tests_by_patient.setdefault(pid, []).append(label)
                                    test_detail_by_patient.setdefault(pid, []).append(
                                        {
                                            "booked_code": code,
                                            "test_name": label,
                                            "panel_company": self._norm_code(((pending_map.get(key) or {}).get("panel") or {}).get("pname")),
                                            "selected_charge_mode": self._norm_code((((pending_map.get(key) or {}).get("billing") or {}).get("selected_charge_mode"))),
                                            "mrp": 0.0,
                                            "discount": 0.0,
                                            "final_charge": 0.0,
                                        }
                                    )
                            used_snapshot = True
                        except Exception:
                            used_snapshot = False
                if not used_snapshot:
                    patient_row_by_id = {int((pr or {}).get("patient_id") or 0): (pr or {}) for pr in (patients or [])}
                    include_cancelled_tests = include_cancelled_patients
                    visible_test_statuses = "0, 1, 2, 3" if include_cancelled_tests else "0, 1, 3"
                    cur.execute(
                        f"""
                        SELECT patient_id, comp_cat_id, charge_mode AS selected_charge_mode, booked_code, test_name, charge, mrp, max_discount
                        FROM hhome_collection_booking_patient_test
                        WHERE booking_id=%s
                          AND {self._test_status_sql('test_status')} IN ({visible_test_statuses})
                        ORDER BY id
                        """,
                        (booking_id,),
                    )
                    test_rows = cur.fetchall()
                    for row in test_rows:
                        pid = int(row.get("patient_id") or 0)
                        if pid <= 0:
                            continue
                        label = self._norm_code(row.get("test_name")) or self._norm_code(row.get("booked_code"))
                        if not label:
                            continue
                        tests_by_patient.setdefault(pid, [])
                        if label not in tests_by_patient[pid]:
                            tests_by_patient[pid].append(label)
                        mrp = float(row.get("mrp") or 0)
                        discount = float(row.get("max_discount") or 0)
                        pmeta = patient_row_by_id.get(pid) or {}
                        selected_charge_mode = self._selected_charge_mode({"selected_charge_mode": row.get("selected_charge_mode")}) or self._charge_mode_from_patient_row(pmeta, row.get("comp_cat_id"))
                        pmeta_with_mode = dict(pmeta)
                        pmeta_with_mode["selected_charge_mode"] = selected_charge_mode
                        panel_company = self._panel_name_from_patient_row(pmeta_with_mode, row.get("comp_cat_id"))
                        final_charge = mrp - discount
                        if final_charge < 0:
                            final_charge = 0
                        test_detail_by_patient.setdefault(pid, []).append(
                            {
                                "booked_code": self._norm_code(row.get("booked_code")),
                                "test_name": self._norm_code(row.get("test_name")),
                                "panel_company": panel_company,
                                "selected_charge_mode": selected_charge_mode,
                                "mrp": mrp,
                                "discount": discount,
                                "final_charge": round(final_charge, 2),
                            }
                        )
                    cur.execute(
                        f"""
                        SELECT patient_id, comp_cat_id
                        FROM hhome_collection_booking_patient_test
                        WHERE booking_id=%s
                          AND {self._test_status_sql('test_status')} IN ({visible_test_statuses})
                          AND comp_cat_id IS NOT NULL
                          AND TRIM(comp_cat_id) <> ''
                        GROUP BY patient_id, comp_cat_id
                        ORDER BY comp_cat_id
                        """,
                        (booking_id,),
                    )
                    panel_rows = cur.fetchall() or []
                    for row in panel_rows:
                        pid = int(row.get("patient_id") or 0)
                        if pid <= 0:
                            continue
                        pmeta = patient_row_by_id.get(pid) or {}
                        pname = self._panel_name_from_patient_row(pmeta, row.get("comp_cat_id"))
                        if not pname:
                            pname = self._norm_code(row.get("comp_cat_id"))
                        if not pname:
                            continue
                        panels_by_patient.setdefault(pid, [])
                        if pname not in panels_by_patient[pid]:
                            panels_by_patient[pid].append(pname)

                visible_patients = []
                for p in patients:
                    pid = int(p.get("patient_id") or 0)
                    pctx = appointment_patient_context.get(str(pid)) if appointment_id > 0 else {}
                    pctx = pctx if isinstance(pctx, dict) else {}
                    try:
                        ap_patient_status = int(pctx.get("appointment_patient_status") or 0)
                    except Exception:
                        ap_patient_status = 0
                    if appointment_id > 0 and ap_patient_status == 4:
                        continue
                    p["tests_display"] = ", ".join(tests_by_patient.get(pid, [])) if pid else ""
                    p["tests"] = test_detail_by_patient.get(pid, []) if pid else []
                    p["patient_documents"] = self._split_patient_documents(p.get("patient_documents"))
                    p["prescription_files"] = self._split_prescription_files(p.get("prescription_files"))
                    p["patient_photo_files"] = self._split_prescription_files(p.get("patient_photo_files"))
                    pres_up_by = self._norm_code(p.get("pres_up_by"))
                    pres_uploaded_by_name = self._norm_code(p.get("pres_uploaded_by_name"))
                    if pres_uploaded_by_name:
                        p["pres_uploaded_by_display"] = (
                            f"{pres_uploaded_by_name} (web)"
                            if pres_up_by.lower().startswith("w,")
                            else pres_uploaded_by_name
                        )
                    else:
                        p["pres_uploaded_by_display"] = ""
                    booking_code = self._norm_code(booking.get("booking_code"))
                    if booking_code:
                        prefix = f"{booking_code}/".lower()
                        p["prescription_files"] = [
                            name for name in (p.get("prescription_files") or [])
                            if str(name or "").lower().startswith(prefix)
                        ]
                        p["patient_photo_files"] = [
                            name for name in (p.get("patient_photo_files") or [])
                            if str(name or "").lower().startswith(prefix)
                        ]
                    p["panel_company"] = self._norm_code(p.get("panel_company"))
                    p["ref_by"] = self._norm_code(p.get("ref_by"))
                    p["permanent_reference"] = self._norm_code(p.get("permanent_reference"))
                    p["panel_companies"] = panels_by_patient.get(pid, [])
                    p["selected_charge_modes"] = self._norm_code(p.get("selected_charge_modes"))
                    p["tag"] = self._norm_code(p.get("tag"))
                    p["test_booking_status"] = self._patient_tbs_code({"cce_level_tbs": p.get("cce_level_TBS")})
                    p["appointment_patient_status"] = int(pctx.get("appointment_patient_status")) if pctx.get("appointment_patient_status") is not None else None
                    p["booking_due_amount"] = float(pctx.get("booking_due_amount") or 0) if appointment_id > 0 else float(p.get("due_amount") or 0)
                    p["booking_extra_amount"] = float(pctx.get("booking_extra_amount") or 0) if appointment_id > 0 else float(p.get("extra_amount") or 0)
                    p["booking_payment_mode"] = self._norm_code(pctx.get("booking_payment_mode")) if appointment_id > 0 else self._norm_code(p.get("payment_mode"))
                    if pid > 0:
                        patient_document_urls = [
                            f"/static/uploads/patient_documents/{name}"
                            for name in (p.get("patient_documents") or [])
                            if self._norm_code(name)
                        ]
                        booking_patient_document_urls = [
                            f"/static/uploads/booking_patient_documents/{name}"
                            for name in (p.get("patient_photo_files") or [])
                            if self._norm_code(name)
                        ]
                        p["patient_document_urls"] = patient_document_urls + booking_patient_document_urls
                        p["prescription_urls"] = [
                            f"/static/uploads/prescriptions/{name}"
                            for name in (p.get("prescription_files") or [])
                            if self._norm_code(name)
                        ]
                    else:
                        p["patient_document_urls"] = []
                        p["prescription_urls"] = []
                    visible_patients.append(p)
                patients = visible_patients

                if appointment_id > 0:
                    appt_subtotal = 0.0
                    appt_discount = 0.0
                    appt_total = 0.0
                    appt_payment_summary = self._appointment_payment_summary((ap or {}).get("payment_snapshot_json"))
                    for p in (patients or []):
                        for t in (p.get("tests") or []):
                            try:
                                appt_subtotal += float(t.get("mrp") or 0)
                            except Exception:
                                pass
                            try:
                                appt_discount += float(t.get("discount") or 0)
                            except Exception:
                                pass
                            try:
                                appt_total += float(t.get("final_charge") or 0)
                            except Exception:
                                pass
                    # Keep subtotal/total aligned with rendered appointment tests.
                    booking["F_Apt_Am"] = round(float(appt_subtotal or appt_payment_summary.get("sub_total") or 0), 2)
                    booking["F_dis"] = round(float(appt_discount or appt_payment_summary.get("final_discount") or 0), 2)
                    booking["Ad_dis"] = round(float(appt_payment_summary.get("additional_discount") or 0), 2)
                    booking["total_amount"] = round(float(appt_total or appt_payment_summary.get("total_amount") or 0), 2)

                booking["patients"] = patients
                booking.pop("referred_by", None)
                return booking
        finally:
            conn.close()

    def search_active_staff_users(self, q: str, limit: int = 20):
        query = (q or "").strip()
        if len(query) < 2:
            return []

        try:
            safe_limit = max(1, min(int(limit or 20), 20))
        except Exception:
            safe_limit = 20

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, TRIM(name) AS name
                    FROM users
                    WHERE status='Active'
                      AND name IS NOT NULL
                      AND TRIM(name) <> ''
                      AND LOWER(TRIM(name)) LIKE LOWER(%s)
                    ORDER BY name
                    LIMIT {safe_limit}
                    """,
                    (f"%{query}%",),
                )
                return cur.fetchall() or []
        finally:
            conn.close()
    def get_phlebotomists(self):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name AS full_name, contact
                    FROM users
                    WHERE status='Active'
                      AND designation='Home Collection Phlebo'
                    ORDER BY name
                    """
                )
                return cur.fetchall()
        except Exception:
            return []
        finally:
            conn.close()

    def marketing_rows(self, date_from: str = "", date_to: str = ""):
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                from_date = self._norm_code(date_from)
                to_date = self._norm_code(date_to)
                if not from_date:
                    from_date = str(date.today())
                if not to_date:
                    to_date = from_date

                cur.execute(
                    """
                    SELECT
                        b.id AS booking_id,
                        b.preferred_visit_date,
                        b.preferred_time_slot,
                        b.booking_status,
                        b.total_amount,
                        b.intrnl_rfrncd_by,
                        GROUP_CONCAT(
                            NULLIF(TRIM(CONCAT_WS(' ', p.title, p.full_name)), '')
                            ORDER BY bp.id SEPARATOR ', '
                        ) AS patient_names,
                        GROUP_CONCAT(
                            NULLIF(TRIM(COALESCE(p.contact_mobile, '')), '')
                            ORDER BY bp.id SEPARATOR ', '
                        ) AS patient_mobiles,
                        GROUP_CONCAT(
                            COALESCE(NULLIF(TRIM(p.panel_company), ''), '-')
                            ORDER BY bp.id SEPARATOR ', '
                        ) AS panel_companies,
                        GROUP_CONCAT(
                            NULLIF(TRIM(COALESCE(bp.ref_by, '')), '')
                            ORDER BY bp.id SEPARATOR ', '
                        ) AS ref_by_values,
                        GROUP_CONCAT(
                            COALESCE(bp.patient_final_amount, 0)
                            ORDER BY bp.id SEPARATOR ','
                        ) AS patient_amounts
                    FROM hhome_collection_booking b
                    LEFT JOIN hhome_collection_booking_patient bp ON bp.booking_id = b.id
                    LEFT JOIN hpatient_master p ON p.id = bp.patient_id
                    WHERE DATE(b.preferred_visit_date) >= %s
                      AND DATE(b.preferred_visit_date) <= %s
                    GROUP BY b.id
                    ORDER BY b.preferred_visit_date ASC, b.id ASC
                    """,
                    (from_date, to_date),
                )
                rows = cur.fetchall() or []
                result = []
                for r in rows:
                    visit_date = r.get("preferred_visit_date")
                    patient_amounts = [
                        float(x or 0)
                        for x in self._norm_code(r.get("patient_amounts")).split(",")
                        if self._norm_code(x) != ""
                    ]
                    total_amount = float(r.get("total_amount") or 0)
                    result.append(
                        {
                            "booking_id": int(r.get("booking_id") or 0),
                            "patient_names": self._norm_code(r.get("patient_names")) or "-",
                            "patient_mobiles": self._norm_code(r.get("patient_mobiles")) or "-",
                            "preferred_visit_date": visit_date.isoformat() if hasattr(visit_date, "isoformat") else self._norm_code(visit_date),
                            "preferred_time_slot": self._norm_code(r.get("preferred_time_slot")) or "-",
                            "panel_companies": self._norm_code(r.get("panel_companies")) or "-",
                            "ref_by": self._norm_code(r.get("ref_by_values")) or "-",
                            "internal_ref_by": self._norm_code(r.get("intrnl_rfrncd_by")) or "-",
                            "total_amount": total_amount,
                            "patient_amounts": patient_amounts,
                            "status": self._booking_status_label(int(r.get("booking_status") or 0)),
                        }
                    )
                return result
        except Exception:
            return []
        finally:
            conn.close()

    def _build_assignment_whatsapp_payloads(
        self,
        cur,
        *,
        booking_ids: list[int] | None = None,
        appointment_ids: list[int] | None = None,
    ) -> list[dict]:
        payloads = []
        booking_ids = [int(x) for x in (booking_ids or []) if int(x or 0) > 0]
        appointment_ids = [int(x) for x in (appointment_ids or []) if int(x or 0) > 0]

        if booking_ids:
            cur.execute(
                f"""
                SELECT
                    b.id AS booking_id,
                    b.booking_code,
                    b.intrnl_rfrncd_by,
                    u.name AS phlebo_name,
                    u.contact AS phlebo_mobile,
                    u.experience AS phlebo_experience,
                    p.title,
                    p.full_name,
                    p.contact_mobile,
                    p.panel_company,
                    bp.ref_by
                FROM hhome_collection_booking b
                INNER JOIN users u ON u.id = b.assigned_phlebotomist_id
                LEFT JOIN hhome_collection_booking_patient bp ON bp.booking_id = b.id
                LEFT JOIN hpatient_master p ON p.id = bp.patient_id
                WHERE b.id IN ({",".join(["%s"] * len(booking_ids))})
                ORDER BY b.id, p.full_name
                """,
                booking_ids,
            )
            grouped = {}
            for row in cur.fetchall() or []:
                bid = int(row.get("booking_id") or 0)
                if bid <= 0:
                    continue
                node = grouped.setdefault(
                    bid,
                    {
                        "booking_code": self._norm_code(row.get("booking_code")),
                        "phlebo_name": self._norm_code(row.get("phlebo_name")),
                        "phlebo_mobile": self._format_mobile_for_template(row.get("phlebo_mobile")),
                        "experience": row.get("phlebo_experience"),
                        "internal_ref": self._norm_code(row.get("intrnl_rfrncd_by")),
                        "patients": [],
                    },
                )
                node["patients"].append(row)
            for bid, node in grouped.items():
                total_exp = self._assignment_experience_value(node.get("experience"))
                local_targets = self._extra_local_whatsapp_targets(cur, node.get("patients"), node.get("internal_ref"))
                recipients = self._patient_whatsapp_recipients(node.get("patients"))
                if not recipients and local_targets:
                    recipients = [""]
                for recipient in recipients:
                    payloads.append(
                        {
                            "booking_id": bid,
                            "booking_code": node.get("booking_code"),
                            "recipient": recipient,
                            "patient_names": self._format_patient_names_for_whatsapp(node.get("patients")),
                            "phlebo_name": node.get("phlebo_name"),
                            "phlebo_mobile": node.get("phlebo_mobile"),
                            "total_experience": total_exp,
                            "_local_whatsapp_targets": local_targets,
                            "_local_only": not bool(recipient),
                        }
                    )

        if appointment_ids:
            cur.execute(
                f"""
                SELECT
                    ap.id AS appointment_id,
                    ap.booking_id,
                    b.booking_code,
                    b.intrnl_rfrncd_by,
                    u.name AS phlebo_name,
                    u.contact AS phlebo_mobile,
                    u.experience AS phlebo_experience,
                    ap.selected_patient_ids_json
                FROM hhome_collection_booking_appointment ap
                INNER JOIN hhome_collection_booking b ON b.id = ap.booking_id
                INNER JOIN users u ON u.id = ap.assigned_phlebotomist_id
                WHERE ap.id IN ({",".join(["%s"] * len(appointment_ids))})
                ORDER BY ap.id
                """,
                appointment_ids,
            )
            for row in cur.fetchall() or []:
                patient_rows = []
                selected_ids = self._patient_ids_from_json(row.get("selected_patient_ids_json"))
                if not selected_ids:
                    cur.execute(
                        """
                        SELECT patient_id
                        FROM hhome_collection_booking_patient
                        WHERE booking_id=%s
                        ORDER BY id
                        """,
                        (int(row.get("booking_id") or 0),),
                    )
                    selected_ids = [int(x.get("patient_id") or 0) for x in (cur.fetchall() or []) if int(x.get("patient_id") or 0) > 0]
                if selected_ids:
                    placeholders = ",".join(["%s"] * len(selected_ids))
                    cur.execute(
                        f"""
                        SELECT p.title, p.full_name, p.contact_mobile, p.panel_company, bp.ref_by
                        FROM hhome_collection_booking_patient bp
                        INNER JOIN hpatient_master p ON p.id = bp.patient_id
                        WHERE bp.booking_id=%s
                          AND bp.patient_id IN ({placeholders})
                        ORDER BY p.full_name
                        """,
                        (int(row.get("booking_id") or 0), *tuple(selected_ids)),
                    )
                    patient_rows = cur.fetchall() or []
                total_exp = self._assignment_experience_value(row.get("phlebo_experience"))
                local_targets = self._extra_local_whatsapp_targets(cur, patient_rows, row.get("intrnl_rfrncd_by"))
                recipients = self._patient_whatsapp_recipients(patient_rows)
                if not recipients and local_targets:
                    recipients = [""]
                for recipient in recipients:
                    payloads.append(
                        {
                            "booking_id": int(row.get("booking_id") or 0),
                            "booking_code": self._norm_code(row.get("booking_code")),
                            "recipient": recipient,
                            "patient_names": self._format_patient_names_for_whatsapp(patient_rows),
                            "phlebo_name": self._norm_code(row.get("phlebo_name")),
                            "phlebo_mobile": self._format_mobile_for_template(row.get("phlebo_mobile")),
                            "total_experience": total_exp,
                            "_local_whatsapp_targets": local_targets,
                            "_local_only": not bool(recipient),
                        }
                    )
        return payloads

    def assign_phlebotomist(self, booking_id: int, user_id: int, actor_user_id=None, appointment_id: int = 0, app_obj=None):
        if user_id <= 0 or (booking_id <= 0 and appointment_id <= 0):
            return {"ok": False, "message": "valid target and user_id are required"}

        actor = self._actor(actor_user_id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                assignment_payloads = []
                if appointment_id > 0:
                    cur.execute(
                        """
                        SELECT id, booking_id, assigned_phlebotomist_id, appointment_status, selected_patient_ids_json, payment_snapshot_json
                        FROM hhome_collection_booking_appointment
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (appointment_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return {"ok": False, "message": "Appointment not found"}
                    if int(row.get("appointment_status") or 0) not in (0, 1, 2):
                        return {"ok": False, "message": "Appointment is not assignable"}
                    old_assigned = int(row.get("assigned_phlebotomist_id") or 0)
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET assigned_phlebotomist_id=%s, appointment_status=1, updated_by=%s
                        WHERE id=%s
                        """,
                        (user_id, actor, appointment_id),
                    )
                    selected_patient_ids = self._patient_ids_from_json(row.get("selected_patient_ids_json"))
                    updated_patient_context = self._build_appointment_patient_context_from_db(
                        cur,
                        booking_id=int(row.get("booking_id") or booking_id or 0),
                        patient_ids=selected_patient_ids,
                        default_status=1,
                        existing_context=self._appointment_patient_context(row.get("payment_snapshot_json")),
                    )
                    for pid in (selected_patient_ids or []):
                        node = updated_patient_context.get(str(pid)) if isinstance(updated_patient_context.get(str(pid)), dict) else {}
                        node["appointment_patient_status"] = 1
                        updated_patient_context[str(pid)] = node
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET payment_snapshot_json=%s
                        WHERE id=%s
                        """,
                        (
                            self._appointment_payment_snapshot_json(
                                existing_raw=row.get("payment_snapshot_json"),
                                patient_context=updated_patient_context,
                            ),
                            appointment_id,
                        ),
                    )
                    if old_assigned > 0 and old_assigned != int(user_id):
                        self._insert_booking_action_audit(
                            cur,
                            booking_id=int(row.get("booking_id") or booking_id or 0),
                            action_type="REASSIGN",
                            reason_text="",
                            old_values={"row_type": "APPOINTMENT", "appointment_id": appointment_id, "assigned_phlebotomist_id": old_assigned},
                            new_values={"row_type": "APPOINTMENT", "appointment_id": appointment_id, "assigned_phlebotomist_id": int(user_id)},
                            done_by=actor,
                        )
                    assignment_payloads = self._build_assignment_whatsapp_payloads(cur, appointment_ids=[appointment_id])
                else:
                    cur.execute(
                        """
                        SELECT id, assigned_phlebotomist_id, booking_status
                        FROM hhome_collection_booking
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (booking_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return {"ok": False, "message": "Booking not found"}

                    if int(row.get("booking_status") or 0) not in (0, 1, 2):
                        return {"ok": False, "message": "Booking is not assignable"}
                    old_assigned = int(row.get("assigned_phlebotomist_id") or 0)

                    cur.execute(
                        """
                        UPDATE hhome_collection_booking
                        SET assigned_phlebotomist_id=%s, booking_status=1
                        WHERE id=%s
                        """,
                        (user_id, booking_id),
                    )
                    # Keep patient-level status in sync on booking assign.
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_patient
                        SET booking_patient_status=1
                        WHERE booking_id=%s AND booking_patient_status=0
                        """,
                        (booking_id,),
                    )
                    if old_assigned > 0 and old_assigned != int(user_id):
                        self._insert_booking_action_audit(
                            cur,
                            booking_id=booking_id,
                            action_type="REASSIGN",
                            reason_text="",
                            old_values={"row_type": "BOOKING", "assigned_phlebotomist_id": old_assigned},
                            new_values={"row_type": "BOOKING", "assigned_phlebotomist_id": int(user_id)},
                            done_by=actor,
                        )
                    assignment_payloads = self._build_assignment_whatsapp_payloads(cur, booking_ids=[booking_id])
                conn.commit()
                queued = 0
                for item in assignment_payloads:
                    if item.get("_local_only"):
                        continue
                    if self._queue_assignment_whatsapp(app_obj, item):
                        queued += 1
                local_queued = self._queue_assignment_local_whatsapp(app_obj, assignment_payloads)
                return {"ok": True, "whatsapp_queued": queued, "local_whatsapp_queued_count": local_queued}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def unassign_phlebotomist(self, booking_id: int, reason_text: str, actor_user_id=None, appointment_id: int = 0):
        reason = self._norm_code(reason_text)
        if not reason:
            return {"ok": False, "message": "Reason is required"}
        if booking_id <= 0 and appointment_id <= 0:
            return {"ok": False, "message": "target id is required"}

        actor = self._actor(actor_user_id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if appointment_id > 0:
                    cur.execute(
                        """
                        SELECT id, booking_id, assigned_phlebotomist_id, appointment_status,
                               selected_patient_ids_json, payment_snapshot_json
                        FROM hhome_collection_booking_appointment
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (appointment_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return {"ok": False, "message": "Appointment not found"}
                    old_assigned = int(row.get("assigned_phlebotomist_id") or 0)
                    if old_assigned <= 0 or int(row.get("appointment_status") or 0) != 1:
                        return {"ok": False, "message": "Appointment is not assigned"}
                    parent_booking_id = int(row.get("booking_id") or booking_id or 0)
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET assigned_phlebotomist_id=NULL, appointment_status=0, updated_by=%s
                        WHERE id=%s
                        """,
                        (actor, appointment_id),
                    )
                    selected_patient_ids = self._patient_ids_from_json(row.get("selected_patient_ids_json"))
                    patient_context = self._build_appointment_patient_context_from_db(
                        cur,
                        booking_id=parent_booking_id,
                        patient_ids=selected_patient_ids,
                        default_status=0,
                        existing_context=self._appointment_patient_context(row.get("payment_snapshot_json")),
                    )
                    for pid in selected_patient_ids:
                        node = patient_context.get(str(pid)) if isinstance(patient_context.get(str(pid)), dict) else {}
                        node["appointment_patient_status"] = 0
                        patient_context[str(pid)] = node
                    cur.execute(
                        "UPDATE hhome_collection_booking_appointment SET payment_snapshot_json=%s WHERE id=%s",
                        (
                            self._appointment_payment_snapshot_json(
                                existing_raw=row.get("payment_snapshot_json"),
                                patient_context=patient_context,
                            ),
                            appointment_id,
                        ),
                    )
                    self._insert_booking_action_audit(
                        cur,
                        booking_id=parent_booking_id,
                        action_type="UNASSIGN",
                        reason_text=reason,
                        old_values={"row_type": "APPOINTMENT", "appointment_id": appointment_id, "assigned_phlebotomist_id": old_assigned},
                        new_values={"row_type": "APPOINTMENT", "appointment_id": appointment_id, "assigned_phlebotomist_id": None},
                        done_by=actor,
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, assigned_phlebotomist_id, booking_status
                        FROM hhome_collection_booking
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (booking_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return {"ok": False, "message": "Booking not found"}
                    old_assigned = int(row.get("assigned_phlebotomist_id") or 0)
                    if old_assigned <= 0 or int(row.get("booking_status") or 0) != 1:
                        return {"ok": False, "message": "Booking is not assigned"}
                    cur.execute("UPDATE hhome_collection_booking SET assigned_phlebotomist_id=NULL, booking_status=0 WHERE id=%s", (booking_id,))
                    cur.execute("UPDATE hhome_collection_booking_patient SET booking_patient_status=0 WHERE booking_id=%s AND booking_patient_status=1", (booking_id,))
                    self._insert_booking_action_audit(
                        cur,
                        booking_id=booking_id,
                        action_type="UNASSIGN",
                        reason_text=reason,
                        old_values={"row_type": "BOOKING", "assigned_phlebotomist_id": old_assigned},
                        new_values={"row_type": "BOOKING", "assigned_phlebotomist_id": None},
                        done_by=actor,
                    )
            conn.commit()
            return {"ok": True}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def _insert_booking_action_audit(self, cur, booking_id: int, action_type: str, reason_text: str, old_values: dict | None, new_values: dict | None, done_by: int):
        cur.execute(
            """
            INSERT INTO hbooking_action_audit
            (booking_id, action_type, reason_text, old_values_json, new_values_json, done_by)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                booking_id,
                action_type,
                (reason_text or None),
                (hto_json(old_values) if old_values is not None else None),
                (hto_json(new_values) if new_values is not None else None),
                done_by,
            ),
        )

    def _insert_patient_address_update_audit(self, cur, *, source_type: str, caller_id: int | None, booking_id: int | None, appointment_id: int | None, patient_id: int | None, address_id: int | None, action_type: str, old_values: dict, new_values: dict, done_by: int | None):
        cur.execute(
            """
            INSERT INTO hpatient_address_update_audit
            (source_type, caller_id, booking_id, appointment_id, patient_id, address_id, action_type, old_values_json, new_values_json, done_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                source_type,
                caller_id,
                booking_id,
                appointment_id,
                patient_id,
                address_id,
                action_type,
                hto_json(old_values),
                hto_json(new_values),
                done_by,
            ),
        )

    def _table_columns(self, cur, table_name: str) -> set[str]:
        cached = self._table_columns_cache.get(table_name)
        if cached is not None:
            return cached
        cur.execute(f"SHOW COLUMNS FROM {table_name}")
        cols = {str(row.get("Field") or row.get("field") or "") for row in (cur.fetchall() or [])}
        self._table_columns_cache[table_name] = cols
        return cols

    def _booking_patient_photo_root(self) -> Path:
        return Path(__file__).resolve().parents[2] / "app" / "static" / "uploads" / "booking_patient_documents"

    def _copy_cancel_reschedule_docs(
        self,
        old_booking_code: str,
        new_booking_code: str,
        patient_id: int,
        prescription_files: str | None,
        patient_photo_files: str | None,
    ) -> tuple[str | None, str | None]:
        old_code = self._norm_code(old_booking_code)
        new_code = self._norm_code(new_booking_code)
        if not old_code or not new_code or int(patient_id or 0) <= 0:
            return prescription_files, patient_photo_files

        def split_csv(raw):
            return [self._norm_code(x) for x in str(raw or "").split(",") if self._norm_code(x)]

        def copy_prescriptions() -> list[str]:
            copied = []
            dst_dir = self._prescription_root() / new_code
            dst_dir.mkdir(parents=True, exist_ok=True)
            for idx, rel in enumerate(split_csv(prescription_files), start=1):
                src = self._prescription_root() / rel
                ext = src.suffix or Path(rel).suffix or ".pdf"
                dst_name = f"{new_code}_PT{int(patient_id)}_{idx}{ext}"
                dst = dst_dir / dst_name
                if src.exists():
                    shutil.copy2(src, dst)
                    copied.append(f"{new_code}/{dst_name}")
            return copied

        def copy_photos() -> list[str]:
            copied = []
            dst_dir = self._booking_patient_photo_root() / new_code / f"PT{int(patient_id)}"
            dst_dir.mkdir(parents=True, exist_ok=True)
            for idx, rel in enumerate(split_csv(patient_photo_files), start=1):
                src = self._booking_patient_photo_root() / rel
                ext = src.suffix or Path(rel).suffix or ".jpg"
                dst_name = f"{new_code}_PT{int(patient_id)}_PHOTO_{idx}{ext}"
                dst = dst_dir / dst_name
                if src.exists():
                    shutil.copy2(src, dst)
                    copied.append(f"{new_code}/PT{int(patient_id)}/{dst_name}")
            return copied

        new_prescriptions = copy_prescriptions()
        new_photos = copy_photos()
        return (
            ",".join(new_prescriptions) if new_prescriptions else None,
            ",".join(new_photos) if new_photos else None,
        )

    def _create_cancel_reschedule_booking(
        self,
        cur,
        source_booking: dict,
        proposed_visit_date: str,
        proposed_time_slot: str,
        actor: int,
    ) -> dict:
        booking_id = int(source_booking.get("id") or 0)
        booking_cols = self._table_columns(cur, "hhome_collection_booking")
        patient_cols = self._table_columns(cur, "hhome_collection_booking_patient")

        cols = [
            "booking_code", "caller_id", "selected_address_id", "address_snapshot_json",
            "preferred_visit_date", "preferred_time_slot", "booking_status",
            "assigned_phlebotomist_id", "F_Apt_Am", "credit_amount", "paying_amount",
            "F_dis", "Ad_dis", "total_amount", "created_by",
        ]
        vals = ["%s"] * len(cols)
        tmp_code = f"HC-PENDING-{booking_id}"
        params = [
            tmp_code,
            source_booking.get("caller_id"),
            source_booking.get("selected_address_id"),
            source_booking.get("address_snapshot_json") or "{}",
            proposed_visit_date,
            proposed_time_slot,
            0,
            None,
            source_booking.get("F_Apt_Am") or 0,
            source_booking.get("credit_amount") or 0,
            source_booking.get("paying_amount") or 0,
            source_booking.get("F_dis") or 0,
            source_booking.get("Ad_dis") or 0,
            source_booking.get("total_amount") or 0,
            actor,
        ]
        if "bkg_ref_flag" in booking_cols:
            cols.append("bkg_ref_flag")
            vals.append("%s")
            params.append(booking_id)
        if "booking_tags" in booking_cols:
            cols.append("booking_tags")
            vals.append("%s")
            params.append(self._with_rescheduled_tag(source_booking.get("booking_tags")))
        cur.execute(f"INSERT INTO hhome_collection_booking ({', '.join(cols)}) VALUES ({', '.join(vals)})", tuple(params))
        new_booking_id = int(cur.lastrowid or 0)
        new_booking_code = self._booking_code_from_id(new_booking_id, proposed_visit_date)
        cur.execute("UPDATE hhome_collection_booking SET booking_code=%s WHERE id=%s", (new_booking_code, new_booking_id))

        cur.execute(
            """
            SELECT bp.*, p.id AS patient_id
            FROM hhome_collection_booking_patient bp
            LEFT JOIN hpatient_master p ON p.id=bp.patient_id
            WHERE bp.booking_id=%s
            """,
            (booking_id,),
        )
        patients = cur.fetchall() or []
        for bp in patients:
            pid = int(bp.get("patient_id") or 0)
            if pid <= 0:
                continue
            bp_cols = ["booking_id", "patient_id", "booking_patient_status", "created_by"]
            bp_vals = ["%s", "%s", "%s", "%s"]
            bp_params = [new_booking_id, pid, 0, actor]
            for col in (
                "cce_level_TBS", "ref_by", "permanent_reference", "selected_comp_cat_ids", "selected_cat_details",
                "selected_charge_modes", "selected_panel_companies", "patient_final_amount",
                "additional_discount_amount",
            ):
                if col in patient_cols:
                    bp_cols.append(col)
                    bp_vals.append("%s")
                    bp_params.append(bp.get(col))
            new_prescriptions, new_photos = self._copy_cancel_reschedule_docs(
                old_booking_code=self._norm_code(source_booking.get("booking_code")),
                new_booking_code=new_booking_code,
                patient_id=pid,
                prescription_files=bp.get("prescription_files"),
                patient_photo_files=bp.get("patient_photo_files"),
            )
            if "prescription_files" in patient_cols:
                bp_cols.append("prescription_files")
                bp_vals.append("%s")
                bp_params.append(new_prescriptions)
            if "patient_photo_files" in patient_cols:
                bp_cols.append("patient_photo_files")
                bp_vals.append("%s")
                bp_params.append(new_photos)
            cur.execute(
                f"INSERT INTO hhome_collection_booking_patient ({', '.join(bp_cols)}) VALUES ({', '.join(bp_vals)})",
                tuple(bp_params),
            )
            new_bp_id = int(cur.lastrowid or 0)
            cur.execute(
                f"""
                SELECT comp_cat_id, charge_mode, booked_code, test_name, charge, mrp, max_discount
                FROM hhome_collection_booking_patient_test
                WHERE booking_id=%s AND patient_id=%s AND {self._test_status_sql('test_status')}=0
                """,
                (booking_id, pid),
            )
            for t in (cur.fetchall() or []):
                cur.execute(
                    """
                    INSERT INTO hhome_collection_booking_patient_test
                    (booking_id, booking_patient_id, patient_id, comp_cat_id, charge_mode, booked_code, test_name, charge, mrp, max_discount, test_status, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)
                    """,
                    (
                        new_booking_id,
                        new_bp_id,
                        pid,
                        t.get("comp_cat_id"),
                        t.get("charge_mode"),
                        t.get("booked_code"),
                        t.get("test_name"),
                        t.get("charge") or 0,
                        t.get("mrp") or 0,
                        t.get("max_discount") or 0,
                        actor,
                    ),
                )

        return {"booking_id": new_booking_id, "booking_code": new_booking_code}

    def _create_cancel_odt_ticket(
        self,
        cur,
        booking_id: int,
        reason: str,
        remark: str,
        actor_user_id,
        caller_mobile: str,
        patient_names: str,
    ) -> int | None:
        cur.execute("SELECT name, designation FROM users WHERE id=%s LIMIT 1", (self._actor(actor_user_id),))
        user_row = cur.fetchone() or {}
        created_by = self._norm_code(user_row.get("name")) or str(actor_user_id or "system")
        designation = self._norm_code(user_row.get("designation")) or None
        cur.execute(
            """
            SELECT p.patient_code, p.labmate_pid
            FROM hhome_collection_booking_patient bp
            LEFT JOIN hpatient_master p ON p.id=bp.patient_id
            WHERE bp.booking_id=%s
            ORDER BY bp.id ASC
            LIMIT 1
            """,
            (booking_id,),
        )
        patient_ref = cur.fetchone() or {}
        labmate_id = self._norm_code(patient_ref.get("labmate_pid")) or self._norm_code(patient_ref.get("patient_code")) or None
        additional_info = "\n".join(
            [
                f"Booking ID: {int(booking_id)}",
                f"Cancel Reason: {reason}",
                f"Remark: {remark}",
                f"Caller Mobile: {caller_mobile}",
                f"Patients: {patient_names}",
            ]
        )
        cur.execute(
            """
            INSERT INTO tickets
            (source, country_code, mobile_number, patient_name, patient_labmate_id,
             client_name, priority, whatsapp_opt_in, ticket_category, commitment_at,
             assign_to_user_id, assignment_reason, tags_json, additional_info,
             status, created_by, designation, created_at, ticket_origin)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,DATE_ADD(NOW(), INTERVAL 2 HOUR),%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
            """,
            (
                "patient",
                "+91",
                caller_mobile or None,
                patient_names or None,
                labmate_id,
                None,
                "High",
                0,
                "HC Phlebo Status",
                None,
                None,
                "[]",
                additional_info,
                "Open",
                created_by,
                designation,
                "ODT",
            ),
        )
        return int(cur.lastrowid or 0) or None

    def cancel_booking(
        self,
        booking_id: int,
        reason_text: str = "",
        remark: str = "",
        actor_user_id=None,
        appointment_id: int = 0,
        reschedule_requested: bool = False,
        new_slot_known: bool = False,
        proposed_visit_date: str = "",
        proposed_time_slot: str = "",
    ):
        if booking_id <= 0 and appointment_id <= 0:
            return {"ok": False, "message": "target id is required"}

        reason = (reason_text or "").strip()
        if not reason:
            return {"ok": False, "message": "Cancel reason is required"}

        actor = self._actor(actor_user_id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if appointment_id > 0:
                    cur.execute(
                        """
                        SELECT id, appointment_status, booking_id, selected_patient_ids_json, payment_snapshot_json
                        FROM hhome_collection_booking_appointment
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (appointment_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return {"ok": False, "message": "Appointment not found"}
                    if int(row.get("appointment_status") or 0) == 3:
                        return {"ok": False, "message": "Completed appointment cannot be cancelled"}
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET appointment_status=4, updated_by=%s, reason_text=%s
                        WHERE id=%s
                        """,
                        (actor, reason, appointment_id),
                    )
                    selected_patient_ids = self._patient_ids_from_json(row.get("selected_patient_ids_json"))
                    updated_patient_context = self._build_appointment_patient_context_from_db(
                        cur,
                        booking_id=int(row.get("booking_id") or booking_id or 0),
                        patient_ids=selected_patient_ids,
                        default_status=4,
                        existing_context=self._appointment_patient_context(row.get("payment_snapshot_json")),
                    )
                    for pid in (selected_patient_ids or []):
                        node = updated_patient_context.get(str(pid)) if isinstance(updated_patient_context.get(str(pid)), dict) else {}
                        node["appointment_patient_status"] = 4
                        updated_patient_context[str(pid)] = node
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET payment_snapshot_json=%s
                        WHERE id=%s
                        """,
                        (
                            self._appointment_payment_snapshot_json(
                                existing_raw=row.get("payment_snapshot_json"),
                                patient_context=updated_patient_context,
                            ),
                            appointment_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, booking_code, caller_id, selected_address_id, address_snapshot_json,
                               booking_status, preferred_visit_date, preferred_time_slot,
                               F_Apt_Am, credit_amount, paying_amount, F_dis, Ad_dis, total_amount,
                               booking_tags
                        FROM hhome_collection_booking
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (booking_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return {"ok": False, "message": "Booking not found"}

                    if int(row.get("booking_status") or 0) == 3:
                        return {"ok": False, "message": "Completed booking cannot be cancelled"}

                    cur.execute(
                        """
                        UPDATE hhome_collection_booking
                        SET booking_status=4
                        WHERE id=%s
                        """,
                        (booking_id,),
                    )
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_patient
                        SET booking_patient_status=4
                        WHERE booking_id=%s
                        """,
                        (booking_id,),
                    )
                    proposed_date_norm = self._norm_code(proposed_visit_date)
                    proposed_slot_norm = self._norm_code(proposed_time_slot)
                    created_reschedule_booking = None
                    if bool(reschedule_requested) and proposed_date_norm and proposed_slot_norm:
                        created_reschedule_booking = self._create_cancel_reschedule_booking(
                            cur,
                            source_booking=row,
                            proposed_visit_date=proposed_date_norm,
                            proposed_time_slot=proposed_slot_norm,
                            actor=actor,
                        )
                    cur.execute(
                        f"""
                        UPDATE hhome_collection_booking_patient_test
                        SET test_status=2,
                            dropped_at=COALESCE(dropped_at, NOW()),
                            dropped_by=COALESCE(dropped_by, %s)
                        WHERE booking_id=%s
                          AND {self._test_status_sql('test_status')}=0
                        """,
                        (actor, booking_id),
                    )

                    cur.execute(
                        """
                        SELECT cm.primary_mobile,
                               GROUP_CONCAT(DISTINCT TRIM(CONCAT_WS(' ', p.title, p.full_name)) ORDER BY p.full_name SEPARATOR ', ') AS patient_names,
                               COUNT(DISTINCT hbp.patient_id) AS patient_count
                        FROM hhome_collection_booking b
                        INNER JOIN hcaller_master cm ON cm.id=b.caller_id
                        LEFT JOIN hhome_collection_booking_patient hbp ON hbp.booking_id=b.id
                        LEFT JOIN hpatient_master p ON p.id=hbp.patient_id
                        WHERE b.id=%s
                        GROUP BY cm.primary_mobile
                        """,
                        (booking_id,),
                    )
                    lead_meta = cur.fetchone() or {}
                    mobile = self._norm_code(lead_meta.get("primary_mobile"))
                    patient_names = self._norm_code(lead_meta.get("patient_names"))
                    created_odt_ticket_id = None
                    if bool(reschedule_requested) and not (proposed_date_norm and proposed_slot_norm):
                        created_odt_ticket_id = self._create_cancel_odt_ticket(
                            cur,
                            booking_id=booking_id,
                            reason=reason,
                            remark=self._norm_code(remark),
                            actor_user_id=actor_user_id,
                            caller_mobile=mobile,
                            patient_names=patient_names,
                        )

                    self._insert_booking_action_audit(
                        cur,
                        booking_id=booking_id,
                        action_type="CANCEL",
                        reason_text=(f"{reason} | {self._norm_code(remark)}" if self._norm_code(remark) else reason),
                        old_values={
                            "preferred_visit_date": str(row.get("preferred_visit_date") or ""),
                            "preferred_time_slot": self._norm_code(row.get("preferred_time_slot")),
                        },
                        new_values={
                            "reschedule_requested": bool(reschedule_requested),
                            "new_slot_known": bool(new_slot_known),
                            "proposed_visit_date": self._norm_code(proposed_visit_date) or None,
                            "proposed_time_slot": self._norm_code(proposed_time_slot) or None,
                            "rescheduled_booking_id": (created_reschedule_booking or {}).get("booking_id"),
                            "rescheduled_booking_code": (created_reschedule_booking or {}).get("booking_code"),
                            "odt_ticket_id": created_odt_ticket_id,
                        },
                        done_by=actor,
                    )
                conn.commit()
                return {"ok": True}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def reschedule_booking(self, booking_id: int, preferred_visit_date: str, preferred_time_slot: str, reason_text: str = "", actor_user_id=None, appointment_id: int = 0):
        if booking_id <= 0:
            return {"ok": False, "message": "booking_id is required"}
        if not preferred_visit_date or not preferred_time_slot:
            return {"ok": False, "message": "Visit date and slot are required"}
        if preferred_visit_date < str(date.today()):
            return {"ok": False, "message": "Visit date cannot be in past"}
        reason = (reason_text or "").strip()
        if not reason:
            return {"ok": False, "message": "Reschedule reason is required"}

        actor = self._actor(actor_user_id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if int(appointment_id or 0) > 0:
                    cur.execute(
                        """
                        SELECT id, booking_id, appointment_status, assigned_phlebotomist_id,
                               preferred_visit_date, preferred_time_slot
                        FROM hhome_collection_booking_appointment
                        WHERE id=%s AND booking_id=%s
                        LIMIT 1
                        """,
                        (int(appointment_id), booking_id),
                    )
                    old_appt = cur.fetchone()
                    if not old_appt:
                        return {"ok": False, "message": "Appointment not found"}
                    status_code = int(old_appt.get("appointment_status") or 0)
                    if status_code not in (0, 1, 2):
                        return {"ok": False, "message": "Only Pending/Assigned/Started appointments can be rescheduled"}
                    if status_code == 3:
                        return {"ok": False, "message": "Completed appointment cannot be rescheduled"}
                    if status_code == 4:
                        return {"ok": False, "message": "Cancelled appointment cannot be rescheduled"}

                    old_date = str(old_appt.get("preferred_visit_date") or "")
                    old_slot = self._norm_code(old_appt.get("preferred_time_slot"))
                    new_date = str(preferred_visit_date or "")
                    new_slot = self._norm_code(preferred_time_slot)
                    if old_date == new_date and old_slot == new_slot:
                        return {"ok": False, "message": "No change detected in date or slot"}

                    date_changed = old_date != new_date
                    next_status = 0 if date_changed else status_code
                    next_assigned_user = None if date_changed else old_appt.get("assigned_phlebotomist_id")

                    cur.execute(
                        """
                        SELECT booking_tags
                        FROM hhome_collection_booking
                        WHERE id=%s
                        LIMIT 1
                        """,
                        (booking_id,),
                    )
                    parent_booking = cur.fetchone() or {}
                    rescheduled_tags = self._with_rescheduled_tag(parent_booking.get("booking_tags"))
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking
                        SET booking_tags=%s
                        WHERE id=%s
                        """,
                        (rescheduled_tags or None, booking_id),
                    )

                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET preferred_visit_date=%s,
                            preferred_time_slot=%s,
                            appointment_status=%s,
                            assigned_phlebotomist_id=%s,
                            updated_by=%s
                        WHERE id=%s AND booking_id=%s
                        """,
                        (
                            preferred_visit_date,
                            preferred_time_slot,
                            next_status,
                            next_assigned_user,
                            actor,
                            int(appointment_id),
                            booking_id,
                        ),
                    )

                    old_values = {
                        "appointment_id": int(appointment_id),
                        "preferred_visit_date": old_date,
                        "preferred_time_slot": old_slot,
                        "appointment_status": status_code,
                        "assigned_phlebotomist_id": int(old_appt.get("assigned_phlebotomist_id") or 0),
                    }
                    new_values = {
                        "appointment_id": int(appointment_id),
                        "preferred_visit_date": new_date,
                        "preferred_time_slot": new_slot,
                        "appointment_status": next_status,
                        "assigned_phlebotomist_id": int(next_assigned_user or 0),
                        "booking_tags": rescheduled_tags,
                    }

                    self._insert_booking_action_audit(
                        cur,
                        booking_id=booking_id,
                        action_type="RESCHEDULE_APPOINTMENT",
                        reason_text=reason,
                        old_values=old_values,
                        new_values=new_values,
                        done_by=actor,
                    )
                    conn.commit()
                    return {"ok": True, "booking_id": booking_id, "appointment_id": int(appointment_id)}

                cur.execute(
                    """
                    SELECT id, booking_code, booking_status, assigned_phlebotomist_id,
                           preferred_visit_date, preferred_time_slot, booking_tags
                    FROM hhome_collection_booking
                    WHERE id=%s
                    LIMIT 1
                    """,
                    (booking_id,),
                )
                old_booking = cur.fetchone()
                if not old_booking:
                    return {"ok": False, "message": "Booking not found"}
                status_code = int(old_booking.get("booking_status") or 0)
                if status_code not in (0, 1, 2):
                    return {"ok": False, "message": "Only Pending/Assigned/Started bookings can be rescheduled"}
                if status_code == 3:
                    return {"ok": False, "message": "Completed booking cannot be rescheduled"}
                if status_code == 4:
                    return {"ok": False, "message": "Cancelled booking cannot be rescheduled"}

                old_date = str(old_booking.get("preferred_visit_date") or "")
                old_slot = self._norm_code(old_booking.get("preferred_time_slot"))
                new_date = str(preferred_visit_date or "")
                new_slot = self._norm_code(preferred_time_slot)
                if old_date == new_date and old_slot == new_slot:
                    return {"ok": False, "message": "No change detected in date or slot"}

                date_changed = old_date != new_date
                next_status = 0 if date_changed else int(old_booking.get("booking_status") or 0)
                next_assigned_user = None if date_changed else old_booking.get("assigned_phlebotomist_id")
                rescheduled_tags = self._with_rescheduled_tag(old_booking.get("booking_tags"))

                cur.execute(
                    """
                    UPDATE hhome_collection_booking
                    SET preferred_visit_date=%s,
                        preferred_time_slot=%s,
                        booking_status=%s,
                        assigned_phlebotomist_id=%s,
                        booking_tags=%s
                    WHERE id=%s
                    """,
                    (preferred_visit_date, preferred_time_slot, next_status, next_assigned_user, rescheduled_tags or None, booking_id),
                )

                if date_changed:
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_patient
                        SET booking_patient_status=0
                        WHERE booking_id=%s
                        """,
                        (booking_id,),
                    )

                old_values = {
                    "preferred_visit_date": old_date,
                    "preferred_time_slot": old_slot,
                    "booking_status": int(old_booking.get("booking_status") or 0),
                    "assigned_phlebotomist_id": int(old_booking.get("assigned_phlebotomist_id") or 0),
                }
                new_values = {
                    "preferred_visit_date": new_date,
                    "preferred_time_slot": new_slot,
                    "booking_status": next_status,
                    "assigned_phlebotomist_id": int(next_assigned_user or 0),
                    "booking_tags": rescheduled_tags,
                }

                self._insert_booking_action_audit(
                    cur,
                    booking_id=booking_id,
                    action_type="RESCHEDULE",
                    reason_text=reason,
                    old_values=old_values,
                    new_values=new_values,
                    done_by=actor,
                )
                conn.commit()
                return {"ok": True, "booking_id": booking_id}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def begin_modify_booking_session(self, booking_id: int, reason_text: str, session, actor_user_id=None):
        if booking_id <= 0:
            return {"ok": False, "message": "booking_id is required"}

        reason = (reason_text or "").strip()
        if not reason:
            return {"ok": False, "message": "Modify reason is required"}

        booking = self.get_booking_full(booking_id)
        if not booking:
            return {"ok": False, "message": "Booking not found"}

        status_code = int(booking.get("booking_status") or 0)
        if status_code == 3:
            return {"ok": False, "message": "Completed booking cannot be modified"}
        if status_code == 4:
            return {"ok": False, "message": "Cancelled booking cannot be modified"}

        caller_id = int(booking.get("caller_id") or 0)
        address_id = int(booking.get("selected_address_id") or 0)
        if caller_id <= 0 or address_id <= 0:
            return {"ok": False, "message": "Booking context is incomplete"}

        selected_patients = []
        for p in (booking.get("patients") or []):
            pid = int(p.get("patient_id") or 0)
            if pid > 0:
                selected_patients.append({"patient_id": pid})

        tests_billing_map = {}
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT hcbp.patient_id, hcbp.cce_level_TBS, hcbp.ref_by AS referred_by, hcbp.permanent_reference,
                           hcbp.selected_comp_cat_ids, hcbp.selected_cat_details, hcbp.selected_charge_modes, hcbp.selected_panel_companies,
                           t.comp_cat_id, '' AS cat_details, t.charge_mode AS selected_charge_mode, t.booked_code, t.test_name,
                           t.charge, t.mrp, t.max_discount
                    FROM hhome_collection_booking_patient hcbp
                    LEFT JOIN hhome_collection_booking_patient_test t
                           ON t.booking_id = hcbp.booking_id
                          AND t.patient_id = hcbp.patient_id
                          AND {self._test_status_sql('t.test_status')}=0
                    WHERE hcbp.booking_id=%s
                    ORDER BY hcbp.id, t.id
                    """,
                    (booking_id,),
                )
                for row in cur.fetchall():
                    pid = str(int(row.get("patient_id") or 0))
                    if not pid or pid == "0":
                        continue
                    booked_code = self._norm_code(row.get("booked_code"))
                    if not booked_code:
                        continue
                    tbs_code = self._patient_tbs_code({"cce_level_tbs": row.get("cce_level_TBS")})
                    panel_company = self._panel_name_from_patient_row(row, row.get("comp_cat_id"))
                    selected_charge_mode = self._charge_mode_from_patient_row(row, row.get("comp_cat_id")) or self._selected_charge_mode(
                        {"selected_charge_mode": row.get("selected_charge_mode")}
                    )
                    panel_key = "|".join(
                        [
                            panel_company,
                            self._norm_code(row.get("comp_cat_id")),
                            selected_charge_mode,
                        ]
                    )
                    tb = tests_billing_map.setdefault(pid, {"panels": [], "_panel_index": {}, "cce_level_tbs": tbs_code, "referred_by": self._norm_code(row.get("referred_by")), "permanent_reference": self._norm_code(row.get("permanent_reference"))})
                    if tb.get("cce_level_tbs") is None and tbs_code is not None:
                        tb["cce_level_tbs"] = tbs_code
                    if not tb.get("referred_by"):
                        tb["referred_by"] = self._norm_code(row.get("referred_by"))
                    if not tb.get("permanent_reference"):
                        tb["permanent_reference"] = self._norm_code(row.get("permanent_reference"))
                    panel_idx = tb["_panel_index"].get(panel_key)
                    if panel_idx is None:
                        panel_idx = len(tb["panels"])
                        tb["_panel_index"][panel_key] = panel_idx
                        tb["panels"].append({
                            "panel": {"pname": panel_company},
                            "billing": {
                                "comp_cat_id": self._norm_code(row.get("comp_cat_id")),
                                "cat_details": self._cat_details_from_patient_row(row, row.get("comp_cat_id")),
                                "charge_mode_code": selected_charge_mode,
                                "selected_charge_mode": selected_charge_mode,
                            },
                            "selected_tests": [],
                        })
                    panel_section = tb["panels"][panel_idx]
                    mrp_val = float(row.get("mrp") or 0)
                    max_allowed_discount = self._max_allowed_discount_from_panelrates(
                        row.get("comp_cat_id"),
                        booked_code,
                        mrp_val,
                    )
                    panel_section["selected_tests"].append(
                        {
                            "booked_code": booked_code,
                            "description": self._norm_code(row.get("test_name")),
                            "charge": float(row.get("charge") or 0),
                            "mrp": mrp_val,
                            "max_discount": float(row.get("max_discount") or 0),
                            "max_allowed_discount": max_allowed_discount,
                        }
                    )

            for pid, tb in tests_billing_map.items():
                tb.pop("_panel_index", None)
                if tb.get("panels"):
                    first = tb["panels"][0]
                    tb["panel"] = first.get("panel") or {}
                    tb["billing"] = first.get("billing") or {}
                    tb["selected_tests"] = first.get("selected_tests") or []
        finally:
            conn.close()

        base_discount_total = 0.0
        for tb in tests_billing_map.values():
            for section in (tb.get("panels") or []):
                for t in (section.get("selected_tests") or []):
                    try:
                        base_discount_total += float(t.get("max_discount") or 0)
                    except Exception:
                        pass
        additional_discount_amount = round(self._row_additional_discount(booking), 2)

        address_snapshot = self.get_address_snapshot(address_id)
        parent_context_map = self._normalize_parent_context_map(deepcopy(tests_billing_map))
        session["hcaller_id"] = caller_id
        session["hselected_patients"] = selected_patients
        session["hselected_address_id"] = address_id
        session["hselected_address_snapshot"] = address_snapshot
        session["search_mobile"] = self._norm_code(booking.get("primary_mobile"))
        session["hmodify_context"] = {
            "booking_id": booking_id,
            "reason_text": reason,
            "flow_type": "modify_booking",
            "modify_scope": "booking",
            "appointment": {
                "preferred_visit_date": str(booking.get("preferred_visit_date") or ""),
                "preferred_time_slot": self._norm_code(booking.get("preferred_time_slot")),
                "internal_ref": self._norm_code(booking.get("intrnl_rfrncd_by")),
                "lead_id": self._norm_code(booking.get("lead_id")),
                "remarks": self._norm_code(booking.get("remarks")),
                "booking_tags": self._norm_code(booking.get("booking_tags")),
                "additional_discount_mode": "amount" if additional_discount_amount > 0 else "",
                "additional_discount_value": additional_discount_amount if additional_discount_amount > 0 else 0,
                "additional_discount_amount": additional_discount_amount,
            },
            "tests_billing_map": tests_billing_map,
            "searched_mobile": self._norm_code(booking.get("primary_mobile")),
        }
        return {"ok": True, "redirect_url": "/hhome-collection?mode=modify"}

    def begin_followup_appointment_session(self, booking_id: int, reason_text: str, session, actor_user_id=None):
        if booking_id <= 0:
            return {"ok": False, "message": "booking_id is required"}

        reason = (reason_text or "").strip()
        booking = self.get_booking_full(booking_id)
        if not booking:
            return {"ok": False, "message": "Booking not found"}
        if int(booking.get("booking_status") or 0) == 4:
            return {"ok": False, "message": "Cancelled booking cannot create follow-up"}

        caller_id = int(booking.get("caller_id") or 0)
        address_id = int(booking.get("selected_address_id") or 0)
        if caller_id <= 0 or address_id <= 0:
            return {"ok": False, "message": "Booking context is incomplete"}

        tests_billing_map = {}
        selected_patients = []
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                self.preload_panel_catalog()
                cur.execute(
                    """
                    SELECT patient_id, cce_level_TBS, ref_by AS referred_by, permanent_reference, selected_comp_cat_ids, selected_cat_details, selected_charge_modes, selected_panel_companies
                    FROM hhome_collection_booking_patient
                    WHERE booking_id=%s
                    ORDER BY id
                    """,
                    (booking_id,),
                )
                patient_rows = cur.fetchall() or []
                patient_row_by_id = {}
                for r in patient_rows:
                    pid_i = int(r.get("patient_id") or 0)
                    if pid_i > 0:
                        patient_row_by_id[pid_i] = r

                cur.execute(
                    """
                    SELECT booking_patient_id, patient_id, root_booked_code, root_test_name, pending_child_tests_json
                    FROM hcb_patient_test_pendingchildtest
                    WHERE booking_id=%s
                      AND IFNULL(pending_status, 0)=0
                      AND UPPER(TRIM(COALESCE(source_type, 'BOOKING')))='BOOKING'
                    ORDER BY id
                    """,
                    (booking_id,),
                )
                pending_rows = cur.fetchall() or []
                pending_tests_map = {}
                pending_root_codes_by_pid = {}
                pending_root_seed_by_pid = {}
                if pending_rows:
                    all_by_code = {}
                    for cc_rows in (self._panel_catalog.get("tests_search_by_comp", {}) or {}).values():
                        for x in (cc_rows or []):
                            c = self._norm_code(x.get("booked_code"))
                            if c and c not in all_by_code:
                                all_by_code[c] = x
                    for row in pending_rows:
                        pid_i = int(row.get("patient_id") or 0)
                        if pid_i <= 0:
                            continue
                        pid = str(pid_i)

                        pmeta = patient_row_by_id.get(pid_i) or {}
                        tbs_code = self._patient_tbs_code({"cce_level_tbs": pmeta.get("cce_level_TBS")})
                        tb = pending_tests_map.setdefault(pid, {"panels": [], "_panel_index": {}, "cce_level_tbs": tbs_code})
                        if tb.get("cce_level_tbs") is None and tbs_code is not None:
                            tb["cce_level_tbs"] = tbs_code

                        raw_json = row.get("pending_child_tests_json")
                        try:
                            payload = json.loads(raw_json) if raw_json else {}
                        except Exception:
                            payload = {}
                        items = payload.get("items")
                        if not isinstance(items, list):
                            items = [{"tube": payload.get("tube"), "pending": payload.get("pending") or []}]

                        root_code = self._norm_code(row.get("root_booked_code"))
                        root_name = self._norm_code(row.get("root_test_name")) or root_code
                        if root_code:
                            pending_root_codes_by_pid.setdefault(pid_i, set()).add(root_code)
                        root_comp_cat = ""
                        cur.execute(
                            """
                            SELECT comp_cat_id
                            FROM hhome_collection_booking_patient_test
                            WHERE booking_id=%s AND patient_id=%s AND UPPER(TRIM(booked_code))=%s
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (booking_id, pid_i, root_code),
                        )
                        root_row = cur.fetchone() or {}
                        root_comp_cat = self._norm_code(root_row.get("comp_cat_id"))
                        if not root_comp_cat:
                            comp_csv = self._norm_code(pmeta.get("selected_comp_cat_ids"))
                            root_comp_cat = self._norm_code((comp_csv.split(",")[0] if comp_csv else ""))
                        if root_code:
                            pending_root_seed_by_pid.setdefault(pid_i, []).append(
                                {
                                    "root_code": root_code,
                                    "root_name": root_name,
                                    "comp_cat_id": root_comp_cat,
                                }
                            )

                        panel_company = self._panel_name_from_patient_row(pmeta, root_comp_cat)
                        selected_charge_mode = self._charge_mode_from_patient_row(pmeta, root_comp_cat)
                        panel_key = "|".join([panel_company, root_comp_cat, selected_charge_mode])
                        panel_idx = tb["_panel_index"].get(panel_key)
                        if panel_idx is None:
                            panel_idx = len(tb["panels"])
                            tb["_panel_index"][panel_key] = panel_idx
                            tb["panels"].append(
                                {
                                    "panel": {"pname": panel_company or "Panel"},
                                    "billing": {
                                        "comp_cat_id": root_comp_cat,
                                        "cat_details": self._cat_details_from_patient_row(pmeta, root_comp_cat),
                                        "charge_mode_code": selected_charge_mode,
                                        "selected_charge_mode": selected_charge_mode,
                                    },
                                    "selected_tests": [],
                                }
                            )

                        tests_catalog = self._panel_catalog.get("tests_search_by_comp", {}).get(root_comp_cat, []) if root_comp_cat else []
                        by_code = {self._norm_code(x.get("booked_code")): x for x in (tests_catalog or []) if self._norm_code(x.get("booked_code"))}
                        seen_pending_codes = set()
                        for item in items:
                            pending_list = item.get("pending") or []
                            for p in pending_list:
                                booked_code = self._norm_code(p.get("booked_code"))
                                if not booked_code:
                                    continue
                                if booked_code in seen_pending_codes:
                                    continue
                                seen_pending_codes.add(booked_code)
                                meta = by_code.get(booked_code) or all_by_code.get(booked_code) or {}
                                fallback_meta = (self._panel_catalog.get("test_by_testcode1") or {}).get(booked_code) or {}
                                desc = (
                                    self._norm_code(meta.get("description"))
                                    or self._norm_code(fallback_meta.get("description"))
                                    or self._norm_code(p.get("description"))
                                    or booked_code
                                )
                                tb["panels"][panel_idx]["selected_tests"].append(
                                    {
                                        "booked_code": booked_code,
                                        "description": desc,
                                        "charge": 0.0,
                                        "mrp": 0.0,
                                        "max_discount": 0.0,
                                        "max_allowed_discount": 0.0,
                                        "parent_booked_code": self._norm_code(p.get("parent_booked_code")),
                                        "root_booked_code": root_code,
                                        "pending_carried": True,
                                    }
                                )

                if pending_root_codes_by_pid:
                    cur.execute(
                        """
                        SELECT t.patient_id, bp.cce_level_TBS,
                               bp.selected_comp_cat_ids, bp.selected_cat_details, bp.selected_charge_modes, bp.selected_panel_companies,
                               t.comp_cat_id, '' AS cat_details, t.charge_mode AS selected_charge_mode, t.booked_code, t.test_name,
                               t.charge, t.mrp, t.max_discount
                        FROM hhome_collection_booking_patient_test t
                        LEFT JOIN hhome_collection_booking_patient bp
                          ON bp.booking_id = t.booking_id AND bp.patient_id = t.patient_id
                        WHERE t.booking_id=%s
                        ORDER BY t.id
                        """,
                        (booking_id,),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT t.patient_id, bp.cce_level_TBS,
                               bp.selected_comp_cat_ids, bp.selected_cat_details, bp.selected_charge_modes, bp.selected_panel_companies,
                               t.comp_cat_id, '' AS cat_details, t.charge_mode AS selected_charge_mode, t.booked_code, t.test_name,
                               t.charge, t.mrp, t.max_discount
                        FROM hhome_collection_booking_patient_test t
                        LEFT JOIN hhome_collection_booking_patient bp
                          ON bp.booking_id = t.booking_id AND bp.patient_id = t.patient_id
                        WHERE t.booking_id=%s
                          AND {self._test_status_sql('t.test_status')}=0
                        ORDER BY t.id
                        """,
                        (booking_id,),
                    )
                rows = cur.fetchall() or []

                patient_seen = set()
                for row in rows:
                    pid_i = int(row.get("patient_id") or 0)
                    if pid_i <= 0:
                        continue
                    booked_code = self._norm_code(row.get("booked_code"))
                    if pending_root_codes_by_pid:
                        allowed_roots = pending_root_codes_by_pid.get(pid_i) or set()
                        if booked_code not in allowed_roots:
                            continue
                    pid = str(pid_i)
                    if pid_i not in patient_seen:
                        patient_seen.add(pid_i)
                        selected_patients.append({"patient_id": pid_i})
                    tbs_code = self._patient_tbs_code({"cce_level_tbs": row.get("cce_level_TBS")})
                    panel_company = self._panel_name_from_patient_row(row, row.get("comp_cat_id"))
                    selected_charge_mode = self._charge_mode_from_patient_row(row, row.get("comp_cat_id")) or self._selected_charge_mode(
                        {"selected_charge_mode": row.get("selected_charge_mode")}
                    )
                    panel_key = "|".join(
                        [
                            panel_company,
                            self._norm_code(row.get("comp_cat_id")),
                            selected_charge_mode,
                        ]
                    )
                    tb = tests_billing_map.setdefault(pid, {"panels": [], "_panel_index": {}, "cce_level_tbs": tbs_code})
                    if tb.get("cce_level_tbs") is None and tbs_code is not None:
                        tb["cce_level_tbs"] = tbs_code
                    panel_idx = tb["_panel_index"].get(panel_key)
                    if panel_idx is None:
                        panel_idx = len(tb["panels"])
                        tb["_panel_index"][panel_key] = panel_idx
                        tb["panels"].append(
                            {
                                "panel": {"pname": panel_company},
                                "billing": {
                                    "comp_cat_id": self._norm_code(row.get("comp_cat_id")),
                                    "cat_details": self._cat_details_from_patient_row(row, row.get("comp_cat_id")),
                                    "charge_mode_code": selected_charge_mode,
                                    "selected_charge_mode": selected_charge_mode,
                                },
                                "selected_tests": [],
                            }
                        )
                    if booked_code:
                        mrp_val = float(row.get("mrp") or 0)
                        max_allowed_discount = 0.0
                        tb["panels"][panel_idx]["selected_tests"].append(
                            {
                                "booked_code": booked_code,
                                "description": self._norm_code(row.get("test_name")),
                                "charge": float(row.get("charge") or 0),
                                "mrp": mrp_val,
                                "max_discount": float(row.get("max_discount") or 0),
                                "max_allowed_discount": max_allowed_discount,
                            }
                        )

                # If a pending root does not exist in booking test table (e.g. appointment-added root),
                # seed Step-3 with root test using panel catalog meta so panel/company/charge mode remain visible.
                if pending_root_seed_by_pid:
                    for pid_i, seeds in pending_root_seed_by_pid.items():
                        pid = str(pid_i)
                        if pid_i not in patient_seen:
                            patient_seen.add(pid_i)
                            selected_patients.append({"patient_id": pid_i})
                        pmeta = patient_row_by_id.get(pid_i) or {}
                        tbs_code = self._patient_tbs_code({"cce_level_tbs": pmeta.get("cce_level_TBS")})
                        tb = tests_billing_map.setdefault(pid, {"panels": [], "_panel_index": {}, "cce_level_tbs": tbs_code})
                        if tb.get("cce_level_tbs") is None and tbs_code is not None:
                            tb["cce_level_tbs"] = tbs_code
                        existing_codes = set()
                        for sec in (tb.get("panels") or []):
                            for et in (sec.get("selected_tests") or []):
                                c = self._norm_code(et.get("booked_code"))
                                if c:
                                    existing_codes.add(c)
                        for seed in (seeds or []):
                            root_code = self._norm_code(seed.get("root_code"))
                            if not root_code or root_code in existing_codes:
                                continue
                            root_comp_cat = self._norm_code(seed.get("comp_cat_id"))
                            panel_company = self._panel_name_from_patient_row(pmeta, root_comp_cat)
                            selected_charge_mode = self._charge_mode_from_patient_row(pmeta, root_comp_cat)
                            panel_key = "|".join([panel_company, root_comp_cat, selected_charge_mode])
                            panel_idx = tb["_panel_index"].get(panel_key)
                            if panel_idx is None:
                                panel_idx = len(tb["panels"])
                                tb["_panel_index"][panel_key] = panel_idx
                                tb["panels"].append(
                                    {
                                        "panel": {"pname": panel_company},
                                        "billing": {
                                            "comp_cat_id": root_comp_cat,
                                            "cat_details": self._cat_details_from_patient_row(pmeta, root_comp_cat),
                                            "charge_mode_code": selected_charge_mode,
                                            "selected_charge_mode": selected_charge_mode,
                                        },
                                        "selected_tests": [],
                                    }
                                )
                            meta = (
                                (self._panel_catalog.get("tests_search_by_comp", {}).get(root_comp_cat, []) if root_comp_cat else [])
                                or []
                            )
                            root_meta = None
                            for m in meta:
                                if self._norm_code(m.get("booked_code")) == root_code:
                                    root_meta = m
                                    break
                            mrp_val = float((root_meta or {}).get("mrp") or 0)
                            max_discount = float((root_meta or {}).get("max_discount") or 0)
                            charge_val = mrp_val - max_discount
                            if charge_val < 0:
                                charge_val = 0.0
                            tb["panels"][panel_idx]["selected_tests"].append(
                                {
                                    "booked_code": root_code,
                                    "description": self._norm_code((root_meta or {}).get("description")) or self._norm_code(seed.get("root_name")) or root_code,
                                    "charge": charge_val,
                                    "mrp": mrp_val,
                                    "max_discount": max_discount,
                                    "max_allowed_discount": 0.0,
                                }
                            )
                            existing_codes.add(root_code)

                # Follow-up appointment flow should focus only on pending-scope patients.
                # Fallback: if no pending-scope patient resolved, include all linked patients.
                if not selected_patients:
                    for r in (patient_rows or []):
                        pid_i = int(r.get("patient_id") or 0)
                        if pid_i <= 0:
                            continue
                        tbs_code = self._patient_tbs_code({"cce_level_tbs": r.get("cce_level_TBS")})
                        if pid_i not in patient_seen:
                            patient_seen.add(pid_i)
                            selected_patients.append({"patient_id": pid_i})
                        pid = str(pid_i)
                        tb = tests_billing_map.setdefault(
                            pid,
                            {
                                "panel": {},
                                "billing": {},
                                "selected_tests": [],
                                "panels": [],
                                "cce_level_tbs": tbs_code,
                                "referred_by": self._norm_code(r.get("referred_by")),
                                "permanent_reference": self._norm_code(r.get("permanent_reference")),
                            },
                        )
                        if tb.get("cce_level_tbs") is None and tbs_code is not None:
                            tb["cce_level_tbs"] = tbs_code
                        if not tb.get("referred_by"):
                            tb["referred_by"] = self._norm_code(r.get("referred_by"))
                        if not tb.get("permanent_reference"):
                            tb["permanent_reference"] = self._norm_code(r.get("permanent_reference"))

            for tb in tests_billing_map.values():
                tb.pop("_panel_index", None)
                if tb.get("panels"):
                    first = tb["panels"][0]
                    tb["panel"] = first.get("panel") or {}
                    tb["billing"] = first.get("billing") or {}
                    tb["selected_tests"] = first.get("selected_tests") or []
            for tb in pending_tests_map.values():
                tb.pop("_panel_index", None)
                if tb.get("panels"):
                    first = tb["panels"][0]
                    tb["panel"] = first.get("panel") or {}
                    tb["billing"] = first.get("billing") or {}
                    tb["selected_tests"] = first.get("selected_tests") or []
            pending_tests_map = self._normalize_pending_tests_map_zero_bill(pending_tests_map)
            parent_context_map = self._derive_parent_context_map(tests_billing_map, pending_tests_map)
        finally:
            conn.close()

        base_discount_total = 0.0
        for tb in tests_billing_map.values():
            for section in (tb.get("panels") or []):
                for t in (section.get("selected_tests") or []):
                    try:
                        base_discount_total += float(t.get("max_discount") or 0)
                    except Exception:
                        pass
        additional_discount_amount = round(self._row_additional_discount(booking), 2)

        address_snapshot = self.get_address_snapshot(address_id)
        session["hcaller_id"] = caller_id
        session["hselected_patients"] = selected_patients
        session["hselected_address_id"] = address_id
        session["hselected_address_snapshot"] = address_snapshot
        session["search_mobile"] = self._norm_code(booking.get("primary_mobile"))
        session["hmodify_context"] = {
            "booking_id": booking_id,
            "reason_text": reason,
            "flow_type": "followup_appointment",
            "modify_scope": "appointment",
            "appointment": {
                "preferred_visit_date": str(booking.get("preferred_visit_date") or ""),
                "preferred_time_slot": self._norm_code(booking.get("preferred_time_slot")),
                "internal_ref": self._norm_code(booking.get("intrnl_rfrncd_by")),
                "lead_id": self._norm_code(booking.get("lead_id")),
                "remarks": self._norm_code(booking.get("remarks")),
                "booking_tags": self._norm_code(booking.get("booking_tags")),
                "additional_discount_mode": "amount" if additional_discount_amount > 0 else "",
                "additional_discount_value": additional_discount_amount if additional_discount_amount > 0 else 0,
                "additional_discount_amount": additional_discount_amount,
            },
            "tests_billing_map": tests_billing_map,
            "pending_tests_map": pending_tests_map,
            "parent_context_map": parent_context_map,
            "searched_mobile": self._norm_code(booking.get("primary_mobile")),
        }
        return {"ok": True, "redirect_url": "/hhome-collection?mode=book-appointment"}

    def begin_modify_appointment_session(self, booking_id: int, appointment_id: int, reason_text: str, session, actor_user_id=None):
        if booking_id <= 0 or appointment_id <= 0:
            return {"ok": False, "message": "booking_id and appointment_id are required"}

        reason = (reason_text or "").strip()
        if not reason:
            return {"ok": False, "message": "Modify reason is required"}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ap.id,
                        ap.booking_id,
                        ap.selected_address_id,
                        ap.address_snapshot_json,
                        ap.appointment_tests_snapshot_json,
                        ap.payment_snapshot_json,
                        ap.selected_patient_ids_json,
                        ap.preferred_visit_date,
                        ap.preferred_time_slot,
                        ap.appointment_status,
                        ap.created_at,
                        ap.remarks,
                        ap.reason_text,
                        hcb.caller_id,
                        hcb.intrnl_rfrncd_by,
                        hcb.lead_id,
                        hcb.booking_tags,
                        hcb.selected_address_id AS booking_address_id,
                        cm.primary_mobile
                    FROM hhome_collection_booking_appointment ap
                    INNER JOIN hhome_collection_booking hcb ON hcb.id = ap.booking_id
                    INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                    WHERE ap.id=%s AND ap.booking_id=%s
                    LIMIT 1
                    """,
                    (appointment_id, booking_id),
                )
                ap = cur.fetchone()
                if not ap:
                    return {"ok": False, "message": "Appointment not found"}
                if int(ap.get("appointment_status") or 0) == 3:
                    return {"ok": False, "message": "Completed appointment cannot be modified"}
                if int(ap.get("appointment_status") or 0) == 4:
                    return {"ok": False, "message": "Cancelled appointment cannot be modified"}

                caller_id = int(ap.get("caller_id") or 0)
                address_id = int(ap.get("selected_address_id") or 0) or int(ap.get("booking_address_id") or 0)
                if caller_id <= 0 or address_id <= 0:
                    return {"ok": False, "message": "Appointment context is incomplete"}

                snapshot_from_db = None
                raw_snapshot = ap.get("address_snapshot_json")
                if raw_snapshot:
                    try:
                        snapshot_from_db = json.loads(raw_snapshot)
                    except Exception:
                        snapshot_from_db = None
                snapshot_tests_map = {}
                snapshot_pending_map = {}
                raw_tests_snapshot = ap.get("appointment_tests_snapshot_json")
                tests_snapshot_obj = {}
                if raw_tests_snapshot:
                    try:
                        tests_snapshot_obj = json.loads(raw_tests_snapshot)
                    except Exception:
                        tests_snapshot_obj = {}
                snapshot_flow_type = ""
                if isinstance(tests_snapshot_obj, dict):
                    snapshot_tests_map = tests_snapshot_obj.get("tests_billing_map") or {}
                    snapshot_pending_map = tests_snapshot_obj.get("pending_tests_map") or {}
                    snapshot_parent_context_map = tests_snapshot_obj.get("parent_context_map") or {}
                    snapshot_flow_type = self._norm_code(tests_snapshot_obj.get("flow_type")).lower()
                elif isinstance(snapshot_from_db, dict):
                    # backward compatibility for older appointments
                    snapshot_tests_map = snapshot_from_db.get("_tests_billing_map_snapshot") or {}
                    snapshot_pending_map = snapshot_from_db.get("_pending_tests_map_snapshot") or {}
                    snapshot_parent_context_map = {}
                if not isinstance(snapshot_tests_map, dict):
                    snapshot_tests_map = {}
                if not isinstance(snapshot_pending_map, dict):
                    snapshot_pending_map = {}
                if not isinstance(snapshot_parent_context_map, dict):
                    snapshot_parent_context_map = {}
                snapshot_pending_map = self._normalize_pending_tests_map_zero_bill(snapshot_pending_map)
                snapshot_pending_map = self._enrich_pending_tests_map_descriptions(snapshot_pending_map)
                if not snapshot_parent_context_map:
                    snapshot_parent_context_map = self._derive_parent_context_map(snapshot_tests_map, snapshot_pending_map)
                snapshot_parent_context_map = self._normalize_parent_context_map(snapshot_parent_context_map)

                selected_patient_ids = self._patient_ids_from_json(ap.get("selected_patient_ids_json"))
                if not selected_patient_ids and isinstance(snapshot_from_db, dict):
                    legacy = snapshot_from_db.get("_selected_patient_ids")
                    if isinstance(legacy, list):
                        selected_patient_ids = sorted({int(x) for x in legacy if int(x or 0) > 0})
                if not selected_patient_ids:
                    created_at = ap.get("created_at")
                    if created_at:
                        cur.execute(
                            """
                            SELECT DISTINCT patient_id
                            FROM hhome_collection_booking_patient_test
                            WHERE booking_id=%s
                              AND created_at IS NOT NULL
                              AND ABS(TIMESTAMPDIFF(MINUTE, created_at, %s)) <= 3
                            ORDER BY patient_id
                            """,
                            (booking_id, created_at),
                        )
                        selected_patient_ids = [
                            int(r.get("patient_id") or 0)
                            for r in (cur.fetchall() or [])
                            if int(r.get("patient_id") or 0) > 0
                        ]
                if not selected_patient_ids:
                    cur.execute(
                        """
                        SELECT patient_id
                        FROM hhome_collection_booking_patient
                        WHERE booking_id=%s
                        ORDER BY patient_id
                        """,
                        (booking_id,),
                    )
                    selected_patient_ids = [
                        int(r.get("patient_id") or 0)
                        for r in (cur.fetchall() or [])
                        if int(r.get("patient_id") or 0) > 0
                    ]

                appointment_patient_context = self._appointment_patient_context(ap.get("payment_snapshot_json"))
                if appointment_id > 0 and isinstance(appointment_patient_context, dict):
                    visible_patient_ids = []
                    for pid in selected_patient_ids:
                        pctx = appointment_patient_context.get(str(int(pid)))
                        pctx = pctx if isinstance(pctx, dict) else {}
                        try:
                            ap_patient_status = int(pctx.get("appointment_patient_status") or 0)
                        except Exception:
                            ap_patient_status = 0
                        if ap_patient_status == 4:
                            continue
                        visible_patient_ids.append(pid)
                    selected_patient_ids = visible_patient_ids

                selected_patients = [{"patient_id": pid} for pid in selected_patient_ids]
                tests_billing_map = {}
                patient_tbs_by_id = {}
                patient_row_by_id = {}

                if selected_patient_ids:
                    placeholders = ",".join(["%s"] * len(selected_patient_ids))
                    cur.execute(
                        f"""
                    SELECT patient_id, cce_level_TBS, ref_by AS referred_by, permanent_reference, selected_comp_cat_ids, selected_cat_details, selected_charge_modes, selected_panel_companies
                        FROM hhome_collection_booking_patient
                        WHERE booking_id=%s
                          AND patient_id IN ({placeholders})
                        """,
                        tuple([booking_id] + selected_patient_ids),
                    )
                    for r in (cur.fetchall() or []):
                        pid_i = int(r.get("patient_id") or 0)
                        if pid_i > 0:
                            patient_tbs_by_id[pid_i] = self._patient_tbs_code({"cce_level_tbs": r.get("cce_level_TBS")})
                            patient_row_by_id[pid_i] = r

                if snapshot_tests_map:
                    tests_billing_map = snapshot_tests_map
                elif selected_patient_ids:
                    placeholders = ",".join(["%s"] * len(selected_patient_ids))
                    cur.execute(
                        f"""
                        SELECT patient_id, comp_cat_id, '' AS cat_details, charge_mode AS selected_charge_mode, booked_code, test_name,
                               charge, mrp, max_discount
                        FROM hhome_collection_booking_patient_test
                        WHERE booking_id=%s
                          AND patient_id IN ({placeholders})
                          AND {self._test_status_sql('test_status')}=0
                        ORDER BY id
                        """,
                        tuple([booking_id] + selected_patient_ids),
                    )
                    rows = cur.fetchall() or []
                    for row in rows:
                        pid_i = int(row.get("patient_id") or 0)
                        pid = str(pid_i)
                        if not pid or pid == "0":
                            continue
                        selected_charge_mode = self._charge_mode_from_patient_row(
                            patient_row_by_id.get(pid_i) or {},
                            row.get("comp_cat_id"),
                        ) or self._selected_charge_mode(
                            {"selected_charge_mode": row.get("selected_charge_mode")}
                        )
                        pmeta_with_mode = dict(patient_row_by_id.get(pid_i) or {})
                        pmeta_with_mode["selected_charge_mode"] = selected_charge_mode
                        panel_company = self._panel_name_from_patient_row(pmeta_with_mode, row.get("comp_cat_id"))
                        panel_key = "|".join(
                            [
                                panel_company,
                                self._norm_code(row.get("comp_cat_id")),
                                selected_charge_mode,
                            ]
                        )
                        tb = tests_billing_map.setdefault(
                            pid,
                            {
                                "panels": [],
                                "_panel_index": {},
                                "cce_level_tbs": patient_tbs_by_id.get(pid_i),
                                "referred_by": self._norm_code((patient_row_by_id.get(pid_i) or {}).get("referred_by")),
                                "permanent_reference": self._norm_code((patient_row_by_id.get(pid_i) or {}).get("permanent_reference")),
                            },
                        )
                        panel_idx = tb["_panel_index"].get(panel_key)
                        if panel_idx is None:
                            panel_idx = len(tb["panels"])
                            tb["_panel_index"][panel_key] = panel_idx
                            tb["panels"].append(
                                {
                                    "panel": {"pname": panel_company},
                                    "billing": {
                                        "comp_cat_id": self._norm_code(row.get("comp_cat_id")),
                                        "cat_details": self._cat_details_from_patient_row(patient_row_by_id.get(pid_i) or {}, row.get("comp_cat_id")),
                                        "charge_mode_code": selected_charge_mode,
                                        "selected_charge_mode": selected_charge_mode,
                                    },
                                    "selected_tests": [],
                                }
                            )
                        booked_code = self._norm_code(row.get("booked_code"))
                        if booked_code:
                            mrp_val = float(row.get("mrp") or 0)
                            max_allowed_discount = self._max_allowed_discount_from_panelrates(
                                row.get("comp_cat_id"),
                                booked_code,
                                mrp_val,
                            )
                            tb["panels"][panel_idx]["selected_tests"].append(
                                {
                                    "booked_code": booked_code,
                                    "description": self._norm_code(row.get("test_name")),
                                    "charge": float(row.get("charge") or 0),
                                    "mrp": mrp_val,
                                    "max_discount": float(row.get("max_discount") or 0),
                                    "max_allowed_discount": max_allowed_discount,
                                }
                            )

                for pid in selected_patient_ids:
                    tests_billing_map.setdefault(
                        str(pid),
                        {
                            "panel": {},
                            "billing": {},
                            "selected_tests": [],
                            "panels": [],
                            "cce_level_tbs": patient_tbs_by_id.get(int(pid)),
                            "referred_by": self._norm_code((patient_row_by_id.get(int(pid)) or {}).get("referred_by")),
                            "permanent_reference": self._norm_code((patient_row_by_id.get(int(pid)) or {}).get("permanent_reference")),
                        },
                    )

                for pid, tb in tests_billing_map.items():
                    if not tb.get("referred_by"):
                        tb["referred_by"] = self._norm_code((patient_row_by_id.get(int(pid)) or {}).get("referred_by"))
                    if not tb.get("permanent_reference"):
                        tb["permanent_reference"] = self._norm_code((patient_row_by_id.get(int(pid)) or {}).get("permanent_reference"))
                    tb.pop("_panel_index", None)
                    if tb.get("panels"):
                        first = tb["panels"][0]
                        tb["panel"] = first.get("panel") or {}
                        tb["billing"] = first.get("billing") or {}
                        tb["selected_tests"] = first.get("selected_tests") or []

                snapshot = snapshot_from_db
                if not isinstance(snapshot, dict) or not snapshot:
                    snapshot = self.get_address_snapshot(address_id)
                if not snapshot:
                    return {"ok": False, "message": "Address snapshot not found"}

        finally:
            conn.close()

        base_discount_total = 0.0
        for tb in tests_billing_map.values():
            for section in (tb.get("panels") or []):
                for t in (section.get("selected_tests") or []):
                    try:
                        base_discount_total += float(t.get("max_discount") or 0)
                    except Exception:
                        pass
        payment_summary = self._appointment_payment_summary(ap.get("payment_snapshot_json"))
        additional_discount_amount = round(float(payment_summary.get("additional_discount") or 0), 2)
        final_discount_amount = round(float(payment_summary.get("final_discount") or 0), 2)

        session["hcaller_id"] = caller_id
        session["hselected_patients"] = selected_patients
        session["hselected_address_id"] = address_id
        session["hselected_address_snapshot"] = snapshot
        session["search_mobile"] = self._norm_code(ap.get("primary_mobile"))
        session["hmodify_context"] = {
            "booking_id": booking_id,
            "appointment_id": appointment_id,
            "reason_text": reason,
            "flow_type": snapshot_flow_type or "modify_appointment",
            "modify_scope": "appointment",
            "appointment": {
                "preferred_visit_date": str(ap.get("preferred_visit_date") or ""),
                "preferred_time_slot": self._norm_code(ap.get("preferred_time_slot")),
                "internal_ref": self._norm_code(ap.get("intrnl_rfrncd_by")),
                "lead_id": self._norm_code(ap.get("lead_id")),
                "remarks": self._norm_code(ap.get("remarks")),
                "booking_tags": self._norm_code(ap.get("booking_tags")),
                "additional_discount_mode": "amount" if additional_discount_amount > 0 else "",
                "additional_discount_value": additional_discount_amount if additional_discount_amount > 0 else 0,
                "additional_discount_amount": additional_discount_amount,
                "final_discount_amount": final_discount_amount,
            },
            "tests_billing_map": tests_billing_map,
            "pending_tests_map": snapshot_pending_map,
            "parent_context_map": snapshot_parent_context_map,
            "searched_mobile": self._norm_code(ap.get("primary_mobile")),
        }
        return {"ok": True, "redirect_url": "/hhome-collection?mode=modify"}

    def modify_appointment(
        self,
        booking_id,
        appointment_id,
        caller_id,
        selected_patients,
        selected_address_id,
        selected_snapshot,
        payload,
        actor_user_id=None,
    ):
        actor = self._actor(actor_user_id)
        if booking_id <= 0 or appointment_id <= 0:
            return {"ok": False, "message": "booking_id and appointment_id are required"}
        if not caller_id:
            return {"ok": False, "message": "Caller is required"}
        if not selected_patients:
            return {"ok": False, "message": "Select at least one patient"}
        if not selected_address_id or not selected_snapshot:
            return {"ok": False, "message": "Select an address"}

        preferred_visit_date = payload.get("preferred_visit_date")
        preferred_time_slot = payload.get("preferred_time_slot")
        if not preferred_visit_date or not preferred_time_slot:
            return {"ok": False, "message": "Visit date and slot are required"}
        if preferred_visit_date < str(date.today()):
            return {"ok": False, "message": "Visit date cannot be in past"}

        tests_meta_map = payload.get("patient_tests_meta_map") or {}
        reason_text = self._norm_code(payload.get("modify_reason_text") or payload.get("reason_text"))
        pending_tests_map_snapshot = payload.get("pending_tests_map_snapshot") or {}
        if not isinstance(pending_tests_map_snapshot, dict):
            pending_tests_map_snapshot = {}
        pending_tests_map_snapshot = self._normalize_pending_tests_map_zero_bill(pending_tests_map_snapshot)
        parent_context_map_snapshot = payload.get("parent_context_map_snapshot") or {}
        if not isinstance(parent_context_map_snapshot, dict):
            parent_context_map_snapshot = {}
        parent_context_map_snapshot = self._normalize_parent_context_map(parent_context_map_snapshot)
        if not parent_context_map_snapshot:
            parent_context_map_snapshot = self._derive_parent_context_map(tests_meta_map, pending_tests_map_snapshot)
        incoming_flow_type = self._norm_code(payload.get("_modify_flow_type")).lower()
        snapshot_flow_type = incoming_flow_type or "modify_appointment"
        appointment_tests_snapshot_json = hto_json(
            {
                "tests_billing_map": tests_meta_map,
                "pending_tests_map": pending_tests_map_snapshot,
                "parent_context_map": parent_context_map_snapshot,
                "flow_type": snapshot_flow_type,
            }
        )
        final_sub_total, credit_sub_total, paying_sub_total, _base_discount_total, additional_applied, final_discount, total_amount, patient_addl_applied = self._compute_booking_amount_components(
            tests_meta_map,
            payload.get("additional_discount_amount"),
            payload.get("additional_discount_by_patient"),
        )
        payment_summary = {
            "sub_total": round(float(final_sub_total or 0), 2),
            "credit_amount": round(float(credit_sub_total or 0), 2),
            "paying_amount": round(float(paying_sub_total or 0), 2),
            "base_discount": round(float(_base_discount_total or 0), 2),
            "additional_discount": round(float(additional_applied or 0), 2),
            "final_discount": round(float(final_discount or 0), 2),
            "total_amount": round(float(total_amount or 0), 2),
        }

        def _to_num(v):
            try:
                if v is None or v == "":
                    return None
                return float(v)
            except Exception:
                return None

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, booking_id, selected_address_id, address_snapshot_json, preferred_visit_date, preferred_time_slot, appointment_status,
                           assigned_phlebotomist_id, selected_patient_ids_json,
                           appointment_tests_snapshot_json, payment_snapshot_json
                    FROM hhome_collection_booking_appointment
                    WHERE id=%s AND booking_id=%s
                    LIMIT 1
                    """,
                    (appointment_id, booking_id),
                )
                ap = cur.fetchone()
                if not ap:
                    return {"ok": False, "message": "Appointment not found"}
                if int(ap.get("appointment_status") or 0) == 3:
                    return {"ok": False, "message": "Completed appointment cannot be modified"}
                if int(ap.get("appointment_status") or 0) == 4:
                    return {"ok": False, "message": "Cancelled appointment cannot be modified"}

                cur.execute(
                    "SELECT id, patient_id FROM hhome_collection_booking_patient WHERE booking_id=%s",
                    (booking_id,),
                )
                bp_map = {int(r.get("patient_id")): int(r.get("id")) for r in (cur.fetchall() or [])}
                existing_patient_ids = {pid for pid in bp_map.keys() if pid > 0}
                requested_patient_ids = {
                    int(item.get("patient_id") or 0)
                    for item in (selected_patients or [])
                    if int(item.get("patient_id") or 0) > 0
                }
                if not requested_patient_ids:
                    return {"ok": False, "message": "Select at least one patient for appointment"}
                invalid_patient_ids = sorted(requested_patient_ids - existing_patient_ids)
                if invalid_patient_ids:
                    return {
                        "ok": False,
                        "message": "Patient add is not allowed in appointment flow. Please use only existing booking patients.",
                    }
                selected_patient_ids = sorted({int(x) for x in requested_patient_ids if int(x or 0) > 0})
                old_selected_patient_ids = set(self._patient_ids_from_json(ap.get("selected_patient_ids_json")))
                all_audit_patient_ids = sorted(old_selected_patient_ids | set(selected_patient_ids))
                patient_name_by_id = {}
                if all_audit_patient_ids:
                    placeholders = ",".join(["%s"] * len(all_audit_patient_ids))
                    cur.execute(
                        f"""
                        SELECT id, TRIM(CONCAT_WS(' ', title, full_name)) AS patient_name
                        FROM hpatient_master
                        WHERE id IN ({placeholders})
                        """,
                        tuple(all_audit_patient_ids),
                    )
                    patient_name_by_id = {
                        int(r.get("id") or 0): self._norm_code(r.get("patient_name")) or f"Patient {int(r.get('id') or 0)}"
                        for r in (cur.fetchall() or [])
                    }

                def _tests_by_patient_from_meta_map(meta_map):
                    out = {}
                    for raw_pid, patient_meta in (meta_map or {}).items():
                        try:
                            pid = int(raw_pid or 0)
                        except Exception:
                            pid = 0
                        if pid <= 0:
                            continue
                        tests = {}
                        for section in self._patient_panel_sections(patient_meta or {}):
                            for test in (section.get("selected_tests") or []):
                                code = self._norm_code((test or {}).get("booked_code"))
                                if code:
                                    tests[code] = self._norm_code((test or {}).get("description")) or code
                        out[pid] = tests
                    return out

                def _appointment_tests_map(raw_snapshot):
                    snap = self._appointment_payment_snapshot_obj(raw_snapshot)
                    if not snap:
                        try:
                            snap = json.loads(raw_snapshot or "{}")
                        except Exception:
                            snap = {}
                    return _tests_by_patient_from_meta_map((snap or {}).get("tests_billing_map") or {})

                def _panel_entries_from_meta(patient_meta):
                    entries = []
                    for section in self._patient_panel_sections(patient_meta or {}):
                        panel = section.get("panel") or {}
                        billing = section.get("billing") or {}
                        name = self._norm_code(panel.get("pname"))
                        comp_id = self._norm_code(billing.get("comp_cat_id"))
                        mode = self._selected_charge_mode(billing)
                        if name or comp_id or mode:
                            entries.append((comp_id.lower(), mode.lower(), name.lower(), {
                                "selected_comp_cat_id": comp_id,
                                "selected_charge_mode": mode,
                                "selected_panel_company": name,
                            }))
                    return entries

                def _panel_map_from_meta_map(meta_map):
                    out = {}
                    for raw_pid, patient_meta in (meta_map or {}).items():
                        try:
                            pid = int(raw_pid or 0)
                        except Exception:
                            pid = 0
                        if pid > 0:
                            out[pid] = _panel_entries_from_meta(patient_meta or {})
                    return out

                old_snapshot = self._appointment_payment_snapshot_obj(ap.get("appointment_tests_snapshot_json"))
                if not old_snapshot:
                    try:
                        old_snapshot = json.loads(ap.get("appointment_tests_snapshot_json") or "{}")
                    except Exception:
                        old_snapshot = {}
                old_tests_by_patient = _appointment_tests_map(ap.get("appointment_tests_snapshot_json"))
                new_tests_by_patient = _tests_by_patient_from_meta_map(tests_meta_map)
                old_panel_map = _panel_map_from_meta_map((old_snapshot or {}).get("tests_billing_map") or {})
                new_panel_map = _panel_map_from_meta_map(tests_meta_map)

                patient_context = self._build_appointment_patient_context_from_db(
                    cur,
                    booking_id=booking_id,
                    patient_ids=selected_patient_ids,
                    default_status=int(ap.get("appointment_status") or 0),
                    existing_context=self._appointment_patient_context(ap.get("payment_snapshot_json")),
                )
                for pid in selected_patient_ids:
                    node = patient_context.get(str(pid)) if isinstance(patient_context.get(str(pid)), dict) else {}
                    node["appointment_additional_discount_amount"] = float(patient_addl_applied.get(pid) or 0)
                    patient_context[str(pid)] = node
                payment_snapshot_json = self._appointment_payment_snapshot_json(
                    existing_raw=ap.get("payment_snapshot_json"),
                    summary=payment_summary,
                    patient_context=patient_context,
                )

                desired_codes_by_pid = {}
                desired_rows = []
                for pid in selected_patient_ids:
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    if not patient_meta:
                        return {"ok": False, "message": "Patient list cannot be changed in appointment flow. Please reload and try again."}
                    panel_sections = self._patient_panel_sections(patient_meta)
                    duplicate_check = self._validate_patient_test_duplicates(pid, panel_sections)
                    if not duplicate_check.get("ok"):
                        conn.rollback()
                        return duplicate_check
                    desired_codes_by_pid[pid] = set()
                    for section in panel_sections:
                        panel = section.get("panel") or {}
                        billing = section.get("billing") or {}
                        selected_charge_mode = self._selected_charge_mode(billing)
                        for t in section.get("selected_tests") or []:
                            booked_code = self._norm_code(t.get("booked_code"))
                            if not booked_code:
                                continue
                            desired_codes_by_pid[pid].add(booked_code)
                            desired_rows.append(
                                {
                                    "pid": pid,
                                    "bp_id": bp_map.get(pid),
                                    "panel_company": self._norm_code(panel.get("pname")),
                                    "comp_cat_id": self._norm_code(billing.get("comp_cat_id")),
                                    "cat_details": self._norm_code(billing.get("cat_details")),
                                    "selected_charge_mode": selected_charge_mode,
                                    "booked_code": booked_code,
                                    "test_name": self._norm_code(t.get("description") or booked_code),
                                    "charge": _to_num(t.get("charge")),
                                    "mrp": _to_num(t.get("mrp")),
                                    "max_discount": _to_num(t.get("max_discount")),
                                }
                            )

                for pid in selected_patient_ids:
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    comp_ids_csv, charge_modes_csv, panel_names_csv = self._patient_panel_meta_csv(patient_meta)
                    cat_details_csv = self._patient_cat_details_csv(patient_meta)
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_patient
                        SET cce_level_TBS=%s,
                            ref_by=%s,
                            permanent_reference=%s,
                            selected_comp_cat_ids=%s,
                            selected_cat_details=%s,
                            selected_charge_modes=%s,
                            selected_panel_companies=%s
                        WHERE booking_id=%s AND patient_id=%s
                        """,
                        (
                            self._patient_tbs_value_for_save(patient_meta),
                            self._norm_code(patient_meta.get("referred_by")) or None,
                            self._norm_code(patient_meta.get("permanent_reference")) or None,
                            comp_ids_csv or None,
                            cat_details_csv or None,
                            charge_modes_csv or None,
                            panel_names_csv or None,
                            booking_id,
                            pid,
                        ),
                    )

                # Appointment flow: do not mutate booking test table.
                # Keep appointment-specific test state inside appointment_tests_snapshot_json only.

                old_visit_date = str(ap.get("preferred_visit_date") or "")
                reset_assignment = old_visit_date != str(preferred_visit_date or "")
                next_status = 0 if reset_assignment else int(ap.get("appointment_status") or 0)
                next_assigned_user = None if reset_assignment else ap.get("assigned_phlebotomist_id")
                cur.execute(
                    """
                    UPDATE hhome_collection_booking_appointment
                    SET selected_address_id=%s,
                        address_snapshot_json=%s,
                        appointment_tests_snapshot_json=%s,
                        payment_snapshot_json=%s,
                        selected_patient_ids_json=%s,
                        preferred_visit_date=%s,
                        preferred_time_slot=%s,
                        appointment_status=%s,
                        assigned_phlebotomist_id=%s,
                        remarks=%s,
                        reason_text=%s,
                        updated_by=%s
                    WHERE id=%s
                    """,
                    (
                        selected_address_id,
                        hto_json(selected_snapshot),
                        appointment_tests_snapshot_json,
                        payment_snapshot_json,
                        self._patient_ids_to_json(selected_patient_ids),
                        preferred_visit_date,
                        preferred_time_slot,
                        next_status,
                        next_assigned_user,
                        payload.get("remarks") or None,
                        reason_text or None,
                        actor,
                        appointment_id,
                    ),
                )

                old_audit_values = {"row_type": "APPOINTMENT", "appointment_id": int(appointment_id)}
                new_audit_values = {"row_type": "APPOINTMENT", "appointment_id": int(appointment_id)}

                old_address_id = int(ap.get("selected_address_id") or 0)
                if old_address_id != int(selected_address_id or 0):
                    old_audit_values["selected_address_id"] = old_address_id
                    new_audit_values["selected_address_id"] = int(selected_address_id or 0)
                try:
                    old_address_snapshot = json.loads(ap.get("address_snapshot_json") or "{}")
                except Exception:
                    old_address_snapshot = {}
                if hto_json(old_address_snapshot or {}) != hto_json(selected_snapshot or {}):
                    old_audit_values["address_snapshot"] = old_address_snapshot or {}
                    new_audit_values["address_snapshot"] = selected_snapshot or {}

                old_slot = self._norm_code(ap.get("preferred_time_slot"))
                new_slot = self._norm_code(preferred_time_slot)
                if old_visit_date != str(preferred_visit_date or ""):
                    old_audit_values["preferred_visit_date"] = old_visit_date
                    new_audit_values["preferred_visit_date"] = str(preferred_visit_date or "")
                if old_slot != new_slot:
                    old_audit_values["preferred_time_slot"] = old_slot
                    new_audit_values["preferred_time_slot"] = new_slot

                added_patient_ids = sorted(set(selected_patient_ids) - old_selected_patient_ids)
                removed_patient_ids = sorted(old_selected_patient_ids - set(selected_patient_ids))
                if added_patient_ids:
                    new_audit_values["added_patients"] = [
                        {"patient_id": pid, "patient_name": patient_name_by_id.get(pid) or f"Patient {pid}"}
                        for pid in added_patient_ids
                    ]
                if removed_patient_ids:
                    new_audit_values["removed_patients"] = [
                        {"patient_id": pid, "patient_name": patient_name_by_id.get(pid) or f"Patient {pid}"}
                        for pid in removed_patient_ids
                    ]

                old_additional_discount = round(float((self._appointment_payment_summary(ap.get("payment_snapshot_json")) or {}).get("additional_discount") or 0), 2)
                new_additional_discount = round(float(payment_summary.get("additional_discount") or 0), 2)
                if old_additional_discount != new_additional_discount:
                    new_audit_values["additional_discount"] = {
                        "old": old_additional_discount,
                        "new": new_additional_discount,
                    }

                added_tests = []
                removed_tests = []
                removed_patient_set = set(removed_patient_ids)
                for pid in sorted(set(old_tests_by_patient) | set(new_tests_by_patient)):
                    patient_name = patient_name_by_id.get(pid) or f"Patient {pid}"
                    old_tests = old_tests_by_patient.get(pid, {})
                    new_tests = new_tests_by_patient.get(pid, {})
                    for code in sorted(set(new_tests) - set(old_tests)):
                        added_tests.append({"patient_id": pid, "patient_name": patient_name, "booked_code": code, "test_name": new_tests.get(code) or code})
                    if pid not in removed_patient_set:
                        for code in sorted(set(old_tests) - set(new_tests)):
                            removed_tests.append({"patient_id": pid, "patient_name": patient_name, "booked_code": code, "test_name": old_tests.get(code) or code})
                if added_tests:
                    new_audit_values["added_tests"] = added_tests
                if removed_tests:
                    new_audit_values["removed_tests"] = removed_tests

                panel_added_updated = []
                for pid in sorted(set(old_panel_map) | set(new_panel_map)):
                    old_keys = {entry[0:3] for entry in old_panel_map.get(pid, [])}
                    for entry in new_panel_map.get(pid, []):
                        if entry[0:3] not in old_keys:
                            panel_added_updated.append(
                                {
                                    "patient_id": pid,
                                    "patient_name": patient_name_by_id.get(pid) or f"Patient {pid}",
                                    **entry[3],
                                }
                            )
                if panel_added_updated:
                    new_audit_values["panel_added_updated"] = panel_added_updated

                if len(old_audit_values) > 2 or len(new_audit_values) > 2:
                    self._insert_booking_action_audit(
                        cur,
                        booking_id=booking_id,
                        action_type="MODIFY",
                        reason_text=reason_text,
                        old_values=old_audit_values,
                        new_values=new_audit_values,
                        done_by=actor,
                    )

                self._recalculate_followup_required(cur, booking_id)
                conn.commit()
                return {"ok": True, "booking_id": booking_id, "appointment_id": appointment_id}
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def create_followup_appointment(self, booking_id, caller_id, selected_patients, selected_address_id, selected_snapshot, payload, actor_user_id=None):
        actor = self._actor(actor_user_id)
        if booking_id <= 0:
            return {"ok": False, "message": "booking_id is required"}
        if not caller_id:
            return {"ok": False, "message": "Caller is required"}
        if not selected_patients:
            return {"ok": False, "message": "Select at least one patient"}
        if not selected_address_id or not selected_snapshot:
            return {"ok": False, "message": "Select an address"}

        preferred_visit_date = payload.get("preferred_visit_date")
        preferred_time_slot = payload.get("preferred_time_slot")
        if not preferred_visit_date or not preferred_time_slot:
            return {"ok": False, "message": "Visit date and slot are required"}
        if preferred_visit_date < str(date.today()):
            return {"ok": False, "message": "Visit date cannot be in past"}

        tests_meta_map = payload.get("patient_tests_meta_map") or {}
        reason_text = self._norm_code(payload.get("followup_reason_text") or payload.get("reason_text"))
        pending_tests_map_snapshot = payload.get("pending_tests_map_snapshot") or {}
        if not isinstance(pending_tests_map_snapshot, dict):
            pending_tests_map_snapshot = {}
        pending_tests_map_snapshot = self._normalize_pending_tests_map_zero_bill(pending_tests_map_snapshot)
        parent_context_map_snapshot = payload.get("parent_context_map_snapshot") or {}
        if not isinstance(parent_context_map_snapshot, dict):
            parent_context_map_snapshot = {}
        parent_context_map_snapshot = self._normalize_parent_context_map(parent_context_map_snapshot)
        if not parent_context_map_snapshot:
            parent_context_map_snapshot = self._derive_parent_context_map(tests_meta_map, pending_tests_map_snapshot)
        appointment_tests_snapshot_json = hto_json(
            {
                "tests_billing_map": tests_meta_map,
                "pending_tests_map": pending_tests_map_snapshot,
                "parent_context_map": parent_context_map_snapshot,
                "flow_type": "followup_appointment",
            }
        )
        final_sub_total, credit_sub_total, paying_sub_total, _base_discount_total, additional_applied, final_discount, total_amount, patient_addl_applied = self._compute_booking_amount_components(
            tests_meta_map,
            payload.get("additional_discount_amount"),
            payload.get("additional_discount_by_patient"),
        )
        payment_snapshot_json = self._appointment_payment_snapshot_json(
            summary={
                "sub_total": round(float(final_sub_total or 0), 2),
                "credit_amount": round(float(credit_sub_total or 0), 2),
                "paying_amount": round(float(paying_sub_total or 0), 2),
                "base_discount": round(float(_base_discount_total or 0), 2),
                "additional_discount": round(float(additional_applied or 0), 2),
                "final_discount": round(float(final_discount or 0), 2),
                "total_amount": round(float(total_amount or 0), 2),
            },
            patient_context={},
        )

        def _to_num(v):
            try:
                if v is None or v == "":
                    return None
                return float(v)
            except Exception:
                return None

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, booking_code, booking_status
                    FROM hhome_collection_booking
                    WHERE id=%s
                    LIMIT 1
                    """,
                    (booking_id,),
                )
                booking = cur.fetchone()
                if not booking:
                    return {"ok": False, "message": "Booking not found"}
                if int(booking.get("booking_status") or 0) == 4:
                    return {"ok": False, "message": "Cancelled booking cannot create follow-up"}

                appointment_no = self._next_appointment_no(cur, booking_id)
                cur.execute(
                    """
                    INSERT INTO hhome_collection_booking_appointment
                    (booking_id, appointment_no, selected_address_id, address_snapshot_json, appointment_tests_snapshot_json, payment_snapshot_json, preferred_visit_date, preferred_time_slot,
                     appointment_status, assigned_phlebotomist_id, remarks, reason_text, created_by, updated_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,NULL,%s,%s,%s,%s)
                    """,
                    (
                        booking_id,
                        appointment_no,
                        selected_address_id,
                        hto_json(selected_snapshot),
                        appointment_tests_snapshot_json,
                        payment_snapshot_json,
                        preferred_visit_date,
                        preferred_time_slot,
                        payload.get("remarks") or None,
                        reason_text or None,
                        actor,
                        actor,
                    ),
                )
                appointment_id = cur.lastrowid

                cur.execute("SELECT id, patient_id FROM hhome_collection_booking_patient WHERE booking_id=%s", (booking_id,))
                bp_map = {int(r.get("patient_id")): int(r.get("id")) for r in (cur.fetchall() or [])}
                existing_patient_ids = {pid for pid in bp_map.keys() if pid > 0}
                if not existing_patient_ids:
                    return {"ok": False, "message": "No linked patients found for this booking"}
                requested_patient_ids = {
                    int(item.get("patient_id") or 0)
                    for item in (selected_patients or [])
                    if int(item.get("patient_id") or 0) > 0
                }
                if requested_patient_ids:
                    invalid_patient_ids = sorted(requested_patient_ids - existing_patient_ids)
                    if invalid_patient_ids:
                        return {
                            "ok": False,
                            "message": "Patient add is not allowed in appointment flow. Please use only existing booking patients.",
                        }
                    selected_patient_ids = sorted(requested_patient_ids)
                else:
                    selected_patient_ids = sorted(existing_patient_ids)
                if not selected_patient_ids:
                    return {"ok": False, "message": "Select at least one patient for appointment"}
                patient_context = self._build_appointment_patient_context_from_db(
                    cur,
                    booking_id=booking_id,
                    patient_ids=selected_patient_ids,
                    default_status=0,
                    existing_context={},
                )
                for pid in selected_patient_ids:
                    node = patient_context.get(str(pid)) if isinstance(patient_context.get(str(pid)), dict) else {}
                    node["appointment_additional_discount_amount"] = float(patient_addl_applied.get(pid) or 0)
                    patient_context[str(pid)] = node
                payment_snapshot_json = self._appointment_payment_snapshot_json(
                    existing_raw=payment_snapshot_json,
                    patient_context=patient_context,
                )
                selected_patient_ids = sorted({int(x) for x in selected_patient_ids if int(x or 0) > 0})
                cur.execute(
                    "UPDATE hhome_collection_booking_appointment SET selected_patient_ids_json=%s WHERE id=%s",
                    (self._patient_ids_to_json(selected_patient_ids), appointment_id),
                )

                desired_codes_by_pid = {}
                desired_rows = []
                for pid in selected_patient_ids:
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    if not patient_meta:
                        return {"ok": False, "message": "Patient list cannot be changed in appointment flow. Please reload and try again."}
                    panel_sections = self._patient_panel_sections(patient_meta)
                    duplicate_check = self._validate_patient_test_duplicates(pid, panel_sections)
                    if not duplicate_check.get("ok"):
                        conn.rollback()
                        return duplicate_check

                    desired_codes_by_pid[pid] = set()
                    for section in panel_sections:
                        panel = section.get("panel") or {}
                        billing = section.get("billing") or {}
                        selected_charge_mode = self._selected_charge_mode(billing)
                        for t in section.get("selected_tests") or []:
                            booked_code = self._norm_code(t.get("booked_code"))
                            if not booked_code:
                                continue
                            desired_codes_by_pid[pid].add(booked_code)
                            desired_rows.append(
                                {
                                    "pid": pid,
                                    "bp_id": bp_map.get(pid),
                                    "panel_company": self._norm_code(panel.get("pname")),
                                    "comp_cat_id": self._norm_code(billing.get("comp_cat_id")),
                                    "cat_details": self._norm_code(billing.get("cat_details")),
                                    "selected_charge_mode": selected_charge_mode,
                                    "booked_code": booked_code,
                                    "test_name": self._norm_code(t.get("description") or booked_code),
                                    "charge": _to_num(t.get("charge")),
                                    "mrp": _to_num(t.get("mrp")),
                                    "max_discount": _to_num(t.get("max_discount")),
                                }
                            )

                for pid in selected_patient_ids:
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    comp_ids_csv, charge_modes_csv, panel_names_csv = self._patient_panel_meta_csv(patient_meta)
                    cat_details_csv = self._patient_cat_details_csv(patient_meta)
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_patient
                        SET cce_level_TBS=%s,
                            ref_by=%s,
                            permanent_reference=%s,
                            selected_comp_cat_ids=%s,
                            selected_cat_details=%s,
                            selected_charge_modes=%s,
                            selected_panel_companies=%s
                        WHERE booking_id=%s AND patient_id=%s
                        """,
                        (
                            self._patient_tbs_value_for_save(patient_meta),
                            self._norm_code(patient_meta.get("referred_by")) or None,
                            self._norm_code(patient_meta.get("permanent_reference")) or None,
                            comp_ids_csv or None,
                            cat_details_csv or None,
                            charge_modes_csv or None,
                            panel_names_csv or None,
                            booking_id,
                            pid,
                        ),
                    )

                # Appointment flow: do not mutate booking test table.
                # Keep appointment-specific test state inside appointment_tests_snapshot_json only.

                pending_count = self._recalculate_followup_required(cur, booking_id)
                appt_status = 0
                cur.execute(
                    "UPDATE hhome_collection_booking_appointment SET appointment_status=%s, updated_by=%s WHERE id=%s",
                    (appt_status, actor, appointment_id),
                )

                conn.commit()
                return {
                    "ok": True,
                    "booking_id": booking_id,
                    "booking_code": self._norm_code(booking.get("booking_code")),
                    "appointment_id": appointment_id,
                    "appointment_no": appointment_no,
                }
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()

    def modify_booking(self, booking_id, caller_id, selected_patients, selected_address_id, selected_snapshot, payload, actor_user_id=None):
        actor = self._actor(actor_user_id)
        if booking_id <= 0:
            return {"ok": False, "message": "booking_id is required"}
        if not caller_id:
            return {"ok": False, "message": "Caller is required"}
        if not selected_patients:
            return {"ok": False, "message": "Select at least one patient"}
        if not selected_address_id or not selected_snapshot:
            return {"ok": False, "message": "Select an address"}

        preferred_visit_date = payload.get("preferred_visit_date")
        preferred_time_slot = payload.get("preferred_time_slot")
        if not preferred_visit_date or not preferred_time_slot:
            return {"ok": False, "message": "Visit date and slot are required"}

        if preferred_visit_date < str(date.today()):
            return {"ok": False, "message": "Visit date cannot be in past"}

        tests_meta_map = payload.get("patient_tests_meta_map") or {}
        permanent_tags = self.sanitize_permanent_tags(payload.get("permanent_tags"))
        booking_tags = self.sanitize_transactional_tags(payload.get("booking_tags"))
        modify_reason = (payload.get("modify_reason_text") or "").strip()
        if not modify_reason:
            return {"ok": False, "message": "Modify reason is required"}

        def _to_num(v):
            try:
                if v is None or v == "":
                    return None
                return float(v)
            except Exception:
                return None

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, booking_code, caller_id, selected_address_id, preferred_visit_date, preferred_time_slot,
                           intrnl_rfrncd_by, lead_id, remarks, booking_tags, booking_status, assigned_phlebotomist_id,
                           F_Apt_Am, credit_amount, paying_amount, F_dis, Ad_dis, total_amount
                    FROM hhome_collection_booking
                    WHERE id=%s
                    LIMIT 1
                    """,
                    (booking_id,),
                )
                old_booking = cur.fetchone()
                if not old_booking:
                    return {"ok": False, "message": "Booking not found"}
                if int(old_booking.get("booking_status") or 0) == 3:
                    return {"ok": False, "message": "Completed booking cannot be modified"}
                if int(old_booking.get("booking_status") or 0) == 4:
                    return {"ok": False, "message": "Cancelled booking cannot be modified"}

                cur.execute(
                    """
                    SELECT bp.patient_id,
                           p.full_name AS patient_name,
                           bp.selected_comp_cat_ids,
                           bp.selected_charge_modes,
                           bp.selected_panel_companies,
                           bp.additional_discount_amount
                    FROM hhome_collection_booking_patient bp
                    LEFT JOIN hpatient_master p ON p.id = bp.patient_id
                    WHERE bp.booking_id=%s
                    """,
                    (booking_id,),
                )
                old_patient_rows = cur.fetchall() or []
                old_patient_meta = {
                    int(r.get("patient_id") or 0): {
                        "patient_name": self._norm_code(r.get("patient_name")) or f"Patient {int(r.get('patient_id') or 0)}",
                        "selected_comp_cat_ids": self._norm_code(r.get("selected_comp_cat_ids")),
                        "selected_charge_modes": self._norm_code(r.get("selected_charge_modes")),
                        "selected_panel_companies": self._norm_code(r.get("selected_panel_companies")),
                        "additional_discount_amount": round(float(r.get("additional_discount_amount") or 0), 2),
                    }
                    for r in old_patient_rows
                    if int(r.get("patient_id") or 0) > 0
                }

                cur.execute(
                    f"""
                    SELECT patient_id, booked_code, test_name
                    FROM hhome_collection_booking_patient_test
                    WHERE booking_id=%s
                      AND {self._test_status_sql('test_status')} IN (0,1)
                    ORDER BY patient_id, booked_code
                    """,
                    (booking_id,),
                )
                old_test_rows = cur.fetchall() or []
                old_test_map: dict[int, dict[str, str]] = {}
                for row in old_test_rows:
                    pid = int(row.get("patient_id") or 0)
                    code = self._norm_code(row.get("booked_code"))
                    if pid <= 0 or not code:
                        continue
                    old_test_map.setdefault(pid, {})
                    old_test_map[pid][code] = self._norm_code(row.get("test_name")) or code

                needs_recalc = self._should_recalculate_on_modify_save(payload, tests_meta_map)
                if needs_recalc:
                    final_sub_total, credit_sub_total, paying_sub_total, _base_discount_total, additional_applied, final_discount, total_amount, patient_addl_applied = self._compute_booking_amount_components(
                        tests_meta_map,
                        payload.get("additional_discount_amount"),
                        payload.get("additional_discount_by_patient"),
                    )
                    patient_final_amounts = self._compute_patient_final_amounts(tests_meta_map, patient_addl_applied)
                    paying_amount_store = self._paying_amount_for_store(paying_sub_total, _base_discount_total)
                else:
                    final_sub_total = float(old_booking.get("F_Apt_Am") or 0)
                    credit_sub_total = float(old_booking.get("credit_amount") or 0)
                    paying_sub_total = float(old_booking.get("paying_amount") or 0)
                    paying_amount_store = paying_sub_total
                    final_discount = float(old_booking.get("F_dis") or 0)
                    additional_applied = float(old_booking.get("Ad_dis") or old_booking.get("Ad_Dis") or 0)
                    total_amount = float(old_booking.get("total_amount") or 0)
                    patient_addl_applied = {}
                    patient_final_amounts = {}

                booking_tags = self._auto_transactional_tags(
                    cur,
                    caller_id=caller_id,
                    selected_patients=selected_patients,
                    total_amount=total_amount,
                    existing_tags=booking_tags,
                    include_new_patient=False,
                )

                old_values = {}
                new_values = {}
                old_booking_status = int(old_booking.get("booking_status") or 0)

                def mark_change(field, old_v, new_v):
                    old_s = self._norm_code(old_v) if old_v is not None else ""
                    new_s = self._norm_code(new_v) if new_v is not None else ""
                    if field in ("selected_address_id", "caller_id", "booking_status", "assigned_phlebotomist_id"):
                        old_s = str(int(old_v or 0))
                        new_s = str(int(new_v or 0))
                    if old_s != new_s:
                        old_values[field] = old_v
                        new_values[field] = new_v

                mark_change("caller_id", old_booking.get("caller_id"), caller_id)
                mark_change("selected_address_id", old_booking.get("selected_address_id"), selected_address_id)
                mark_change("preferred_visit_date", old_booking.get("preferred_visit_date"), preferred_visit_date)
                mark_change("preferred_time_slot", old_booking.get("preferred_time_slot"), preferred_time_slot)
                mark_change("intrnl_rfrncd_by", old_booking.get("intrnl_rfrncd_by"), payload.get("intrnl_rfrncd_by") or None)
                mark_change("lead_id", old_booking.get("lead_id"), payload.get("lead_id") or None)
                mark_change("remarks", old_booking.get("remarks"), payload.get("remarks") or None)
                mark_change("booking_tags", old_booking.get("booking_tags"), booking_tags or None)

                if new_values:
                    next_booking_status = old_booking_status
                    next_assigned_user_id = old_booking.get("assigned_phlebotomist_id")
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking
                        SET caller_id=%s,
                            selected_address_id=%s,
                            address_snapshot_json=%s,
                            preferred_visit_date=%s,
                            preferred_time_slot=%s,
                            booking_status=%s,
                            intrnl_rfrncd_by=%s,
                            lead_id=%s,
                            remarks=%s,
                            booking_tags=%s,
                            F_Apt_Am=%s,
                            credit_amount=%s,
                            paying_amount=%s,
                            F_dis=%s,
                            Ad_dis=%s,
                            total_amount=%s,
                            assigned_phlebotomist_id=%s
                        WHERE id=%s
                        """,
                        (
                            caller_id,
                            selected_address_id,
                            hto_json(selected_snapshot),
                            preferred_visit_date,
                            preferred_time_slot,
                            next_booking_status,
                            payload.get("intrnl_rfrncd_by") or None,
                            payload.get("lead_id") or None,
                            payload.get("remarks") or None,
                            booking_tags or None,
                            final_sub_total,
                            credit_sub_total,
                            paying_amount_store,
                            final_discount,
                            additional_applied,
                            total_amount,
                            next_assigned_user_id,
                            booking_id,
                        ),
                    )
                else:
                    cur.execute(
                        "UPDATE hhome_collection_booking SET F_Apt_Am=%s, credit_amount=%s, paying_amount=%s, F_dis=%s, Ad_dis=%s, total_amount=%s WHERE id=%s",
                        (final_sub_total, credit_sub_total, paying_amount_store, final_discount, additional_applied, total_amount, booking_id),
                    )

                cur.execute("SELECT patient_id FROM hhome_collection_booking_patient WHERE booking_id=%s", (booking_id,))
                old_patient_ids = sorted([int(r.get("patient_id") or 0) for r in cur.fetchall() if int(r.get("patient_id") or 0) > 0])
                new_patient_ids = sorted(list({int(item.get("patient_id")) for item in selected_patients if int(item.get("patient_id") or 0) > 0}))
                old_set = set(old_patient_ids)
                new_set = set(new_patient_ids)
                to_remove = sorted(old_set - new_set)
                to_add = sorted(new_set - old_set)

                patient_name_by_id = {
                    int(pid): (meta.get("patient_name") or f"Patient {int(pid)}")
                    for pid, meta in old_patient_meta.items()
                    if int(pid) > 0
                }
                missing_patient_ids = sorted({pid for pid in new_patient_ids if pid not in patient_name_by_id})
                if missing_patient_ids:
                    placeholders = ",".join(["%s"] * len(missing_patient_ids))
                    cur.execute(
                        f"SELECT id, full_name FROM hpatient_master WHERE id IN ({placeholders})",
                        tuple(missing_patient_ids),
                    )
                    for row in (cur.fetchall() or []):
                        pid = int(row.get("id") or 0)
                        if pid > 0:
                            patient_name_by_id[pid] = self._norm_code(row.get("full_name")) or f"Patient {pid}"

                new_test_map: dict[int, dict[str, str]] = {}
                new_panel_map: dict[int, dict] = {}
                for pid in new_patient_ids:
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    comp_ids_csv, charge_modes_csv, panel_names_csv = self._patient_panel_meta_csv(patient_meta)
                    new_panel_map[pid] = {
                        "selected_comp_cat_ids": comp_ids_csv or "",
                        "selected_charge_modes": charge_modes_csv or "",
                        "selected_panel_companies": panel_names_csv or "",
                    }
                    panel_sections = self._patient_panel_sections(patient_meta)
                    new_test_map[pid] = {}
                    for section in panel_sections:
                        for t in section.get("selected_tests") or []:
                            booked_code = self._norm_code(t.get("booked_code"))
                            if not booked_code:
                                continue
                            new_test_map[pid][booked_code] = self._norm_code(t.get("description")) or booked_code

                tbs_validation = self._validate_prescription_required_for_tbs(
                    cur,
                    new_patient_ids,
                    tests_meta_map,
                    session_ref=payload.get("_session_ref"),
                )
                if not tbs_validation.get("ok"):
                    conn.rollback()
                    return tbs_validation

                if old_patient_ids != new_patient_ids:
                    old_values["patient_ids"] = old_patient_ids
                    new_values["patient_ids"] = new_patient_ids
                    if to_remove:
                        remove_placeholders = ",".join(["%s"] * len(to_remove))
                        cur.execute(
                            f"""
                            DELETE FROM hhome_collection_booking_patient_test
                            WHERE booking_id=%s
                              AND patient_id IN ({remove_placeholders})
                            """,
                            tuple([booking_id] + to_remove),
                        )
                        cur.execute(
                            f"""
                            DELETE FROM hhome_collection_booking_patient
                            WHERE booking_id=%s
                              AND patient_id IN ({remove_placeholders})
                            """,
                            tuple([booking_id] + to_remove),
                        )
                    for pid in to_add:
                        next_patient_status = old_booking_status
                        patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                        comp_ids_csv, charge_modes_csv, panel_names_csv = self._patient_panel_meta_csv(patient_meta)
                        cat_details_csv = self._patient_cat_details_csv(patient_meta)
                        cur.execute(
                            """
                            INSERT INTO hhome_collection_booking_patient
                            (booking_id, patient_id, booking_patient_status, cce_level_TBS,
                             ref_by, permanent_reference, selected_comp_cat_ids, selected_cat_details, selected_charge_modes, selected_panel_companies, patient_final_amount, additional_discount_amount, created_by)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            """,
                            (
                                booking_id,
                                pid,
                                next_patient_status,
                                self._patient_tbs_value_for_save(patient_meta),
                                self._norm_code(patient_meta.get("referred_by")) or None,
                                self._norm_code(patient_meta.get("permanent_reference")) or None,
                                comp_ids_csv or None,
                                cat_details_csv or None,
                                charge_modes_csv or None,
                                panel_names_csv or None,
                                float(patient_final_amounts.get(pid) or 0),
                                float(patient_addl_applied.get(pid) or 0),
                                actor,
                            ),
                        )
                for pid in new_patient_ids:
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    comp_ids_csv, charge_modes_csv, panel_names_csv = self._patient_panel_meta_csv(patient_meta)
                    cat_details_csv = self._patient_cat_details_csv(patient_meta)
                    cur.execute(
                        """
                        UPDATE hhome_collection_booking_patient
                        SET cce_level_TBS=%s,
                            ref_by=%s,
                            permanent_reference=%s,
                            selected_comp_cat_ids=%s,
                            selected_cat_details=%s,
                            selected_charge_modes=%s,
                            selected_panel_companies=%s,
                            patient_final_amount=%s,
                            additional_discount_amount=%s
                        WHERE booking_id=%s AND patient_id=%s
                        """,
                        (
                            self._patient_tbs_value_for_save(patient_meta),
                            self._norm_code(patient_meta.get("referred_by")) or None,
                            self._norm_code(patient_meta.get("permanent_reference")) or None,
                            comp_ids_csv or None,
                            cat_details_csv or None,
                            charge_modes_csv or None,
                            panel_names_csv or None,
                            float(patient_final_amounts.get(pid) or 0),
                            float(patient_addl_applied.get(pid) or 0),
                            booking_id,
                            pid,
                        ),
                    )

                cur.execute(
                    "SELECT id, patient_id FROM hhome_collection_booking_patient WHERE booking_id=%s",
                    (booking_id,),
                )
                bp_map = {int(r.get("patient_id")): int(r.get("id")) for r in cur.fetchall()}

                test_snapshot = {}
                for pid in new_patient_ids:
                    bp_id = bp_map.get(pid)
                    if not bp_id:
                        continue
                    patient_meta = tests_meta_map.get(str(pid)) or tests_meta_map.get(pid) or {}
                    panel_sections = self._patient_panel_sections(patient_meta)
                    duplicate_check = self._validate_patient_test_duplicates(pid, panel_sections)
                    if not duplicate_check.get("ok"):
                        conn.rollback()
                        return duplicate_check
                    selected_tests = []
                    for section in panel_sections:
                        selected_tests.extend(section.get("selected_tests") or [])
                    selected_codes = [self._norm_code(t.get("booked_code")) for t in selected_tests if self._norm_code(t.get("booked_code"))]
                    test_snapshot[str(pid)] = selected_codes

                    cur.execute(
                        """
                        SELECT booked_code
                        FROM hhome_collection_booking_patient_test
                        WHERE booking_id=%s AND patient_id=%s
                        """,
                        (booking_id, pid),
                    )
                    existing_codes = {self._norm_code(r.get("booked_code")) for r in (cur.fetchall() or []) if self._norm_code(r.get("booked_code"))}
                    selected_code_set = set(selected_codes)
                    to_delete_codes = sorted(existing_codes - selected_code_set)
                    if to_delete_codes:
                        del_placeholders = ",".join(["%s"] * len(to_delete_codes))
                        cur.execute(
                            f"""
                            UPDATE hhome_collection_booking_patient_test
                            SET test_status=2, dropped_at=NOW(), dropped_by=%s
                            WHERE booking_id=%s AND patient_id=%s
                              AND booked_code IN ({del_placeholders})
                              AND {self._test_status_sql('test_status')}=0
                            """,
                            tuple([actor, booking_id, pid] + to_delete_codes),
                        )

                    for section in panel_sections:
                        panel = section.get("panel") or {}
                        billing = section.get("billing") or {}
                        selected_charge_mode = self._selected_charge_mode(billing)
                        show_mrp = self._section_show_mrp(section)

                        for t in section.get("selected_tests") or []:
                            booked_code = self._norm_code(t.get("booked_code"))
                            if not booked_code:
                                continue
                            test_name = self._norm_code(t.get("description") or booked_code)
                            norm_mrp, norm_charge, norm_max_discount = self._normalized_test_pricing_for_mode(t, billing, show_mrp=show_mrp)
                            cur.execute(
                                """
                                INSERT INTO hhome_collection_booking_patient_test
                                (booking_id, booking_patient_id, patient_id, comp_cat_id, charge_mode,
                                 booked_code, test_name, charge, mrp, max_discount, test_status, created_by)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON DUPLICATE KEY UPDATE
                                comp_cat_id=VALUES(comp_cat_id),
                                charge_mode=VALUES(charge_mode),
                                booked_code=VALUES(booked_code),
                                test_name=VALUES(test_name),
                                charge=VALUES(charge),
                                mrp=VALUES(mrp),
                                max_discount=VALUES(max_discount),
                                test_status=VALUES(test_status),
                                dropped_at=NULL,
                                dropped_by=NULL
                                """,
                                (
                                    booking_id,
                                    bp_id,
                                    pid,
                                    self._norm_code(billing.get("comp_cat_id")),
                                    selected_charge_mode or None,
                                    booked_code,
                                    test_name,
                                    norm_charge,
                                    norm_mrp,
                                    norm_max_discount,
                                    TEST_STATUS_PENDING,
                                    actor,
                                ),
                            )

                if permanent_tags:
                    target_patient_ids = self._linked_patient_ids_for_caller(cur, caller_id)
                    if not target_patient_ids:
                        target_patient_ids = new_patient_ids or old_patient_ids
                else:
                    target_patient_ids = []
                if permanent_tags and target_patient_ids:
                    placeholders = ",".join(["%s"] * len(target_patient_ids))
                    cur.execute(
                        f"SELECT id, tag FROM hpatient_master WHERE id IN ({placeholders})",
                        tuple(target_patient_ids),
                    )
                    rows = cur.fetchall() or []
                    for row in rows:
                        pid = int((row or {}).get("id") or 0)
                        if pid <= 0:
                            continue
                        merged_tag = self._merge_tag_csv((row or {}).get("tag"), permanent_tags)
                        cur.execute(
                            """
                            UPDATE hpatient_master
                            SET tag=%s, updated_by=%s
                            WHERE id=%s
                            """,
                            (merged_tag or None, actor, pid),
                        )

                old_values["tests"] = "updated"
                new_values["tests"] = test_snapshot
                self._recalculate_followup_required(cur, booking_id)

                audit_new_values = {}
                for key in (
                    "caller_id",
                    "selected_address_id",
                    "preferred_visit_date",
                    "preferred_time_slot",
                    "intrnl_rfrncd_by",
                    "lead_id",
                    "remarks",
                    "booking_tags",
                ):
                    if key in new_values:
                        audit_new_values[key] = new_values[key]

                old_additional_discount = round(float(old_booking.get("Ad_dis") or old_booking.get("Ad_Dis") or 0), 2)
                new_additional_discount = round(float(additional_applied or 0), 2)
                if old_additional_discount != new_additional_discount:
                    audit_new_values["additional_discount"] = {
                        "old": old_additional_discount,
                        "new": new_additional_discount,
                    }

                if to_add:
                    audit_new_values["added_patients"] = [
                        {
                            "patient_id": pid,
                            "patient_name": patient_name_by_id.get(pid) or f"Patient {pid}",
                        }
                        for pid in to_add
                    ]
                if to_remove:
                    audit_new_values["removed_patients"] = [
                        {
                            "patient_id": pid,
                            "patient_name": patient_name_by_id.get(pid) or f"Patient {pid}",
                        }
                        for pid in to_remove
                    ]

                added_tests = []
                removed_tests = []
                panel_added_updated = []

                def _panel_entries(panel_meta):
                    comp_ids = [self._norm_code(x) for x in self._norm_code((panel_meta or {}).get("selected_comp_cat_ids")).split(",")]
                    modes = [self._norm_code(x) for x in self._norm_code((panel_meta or {}).get("selected_charge_modes")).split(",")]
                    names = [self._norm_code(x) for x in self._norm_code((panel_meta or {}).get("selected_panel_companies")).split(",")]
                    max_len = max(len(comp_ids), len(modes), len(names))
                    entries = []
                    for idx in range(max_len):
                        comp_id = comp_ids[idx] if idx < len(comp_ids) else ""
                        mode = modes[idx] if idx < len(modes) else ""
                        name = names[idx] if idx < len(names) else ""
                        if comp_id or mode or name:
                            entries.append(
                                {
                                    "selected_comp_cat_id": comp_id,
                                    "selected_charge_mode": mode,
                                    "selected_panel_company": name,
                                }
                            )
                    return entries

                def _panel_entry_key(entry):
                    return (
                        self._norm_code(entry.get("selected_comp_cat_id")).lower(),
                        self._norm_code(entry.get("selected_charge_mode")).lower(),
                        self._norm_code(entry.get("selected_panel_company")).lower(),
                    )

                for pid in sorted(old_set | new_set):
                    old_tests_for_pid = old_test_map.get(pid, {})
                    new_tests_for_pid = new_test_map.get(pid, {})
                    patient_name = patient_name_by_id.get(pid) or f"Patient {pid}"
                    for code in sorted(set(new_tests_for_pid) - set(old_tests_for_pid)):
                        added_tests.append({
                            "patient_id": pid,
                            "patient_name": patient_name,
                            "booked_code": code,
                            "test_name": new_tests_for_pid.get(code) or code,
                        })
                    for code in sorted(set(old_tests_for_pid) - set(new_tests_for_pid)):
                        removed_tests.append({
                            "patient_id": pid,
                            "patient_name": patient_name,
                            "booked_code": code,
                            "test_name": old_tests_for_pid.get(code) or code,
                        })

                    old_panel = old_patient_meta.get(pid) or {}
                    new_panel = new_panel_map.get(pid) or {}
                    old_panel_keys = {_panel_entry_key(x) for x in _panel_entries(old_panel)}
                    for entry in _panel_entries(new_panel):
                        if _panel_entry_key(entry) not in old_panel_keys:
                            panel_added_updated.append(
                                {
                                    "patient_id": pid,
                                    "patient_name": patient_name,
                                    **entry,
                                }
                            )

                if added_tests:
                    audit_new_values["added_tests"] = added_tests
                if removed_tests:
                    audit_new_values["removed_tests"] = removed_tests
                if panel_added_updated:
                    audit_new_values["panel_added_updated"] = panel_added_updated

                if audit_new_values:
                    self._insert_booking_action_audit(
                        cur,
                        booking_id=booking_id,
                        action_type="MODIFY",
                        reason_text=modify_reason,
                        old_values="Same As Booking",
                        new_values=audit_new_values,
                        done_by=actor,
                    )

                conn.commit()

                prescription_merge = {"ok": True, "moved": 0}
                if payload.get("_session_ref") is not None:
                    prescription_merge = self.merge_staged_prescriptions(
                        payload.get("_session_ref"),
                        self._norm_code(old_booking.get("booking_code")),
                        booking_id,
                        actor_user_id=actor,
                    )

                result = {"ok": True, "booking_id": booking_id, "booking_code": self._norm_code(old_booking.get("booking_code"))}
                if not prescription_merge.get("ok"):
                    result["prescription_warning"] = prescription_merge.get("message") or "Prescription files could not be finalized"
                return result
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()


    def assignment_planner_data(self, plan_date: str | None):
        target_date = (plan_date or "").strip()
        if not target_date:
            target_date = str(date.today())

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # Active route list from colony master (base columns for planner)
                cur.execute(
                    """
                    SELECT DISTINCT route_no
                    FROM hcolony_master
                    WHERE is_active = 1
                      AND route_no IS NOT NULL
                      AND TRIM(route_no) <> ''
                    ORDER BY route_no
                    """
                )
                base_routes = [self._norm_code(r.get("route_no")) for r in cur.fetchall() if self._norm_code(r.get("route_no"))]

                # One-shot booking grid payload (avoids N+1)
                cur.execute(
                    """
                    SELECT
                        hcb.id AS booking_id,
                        hcb.preferred_time_slot,
                        hcb.booking_status,
                        COALESCE(NULLIF(TRIM(am.route_no), ''), 'UNASSIGNED') AS route_name,
                        hcb.assigned_phlebotomist_id,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(hbp.ref_by, '')), '') ORDER BY hbp.ref_by SEPARATOR ', ') AS referred_by,
                        MAX(TRIM(COALESCE(hcb.intrnl_rfrncd_by, ''))) AS internal_referred_by,
                        MAX(COALESCE(hcb.total_amount, 0)) AS total_amount,
                        MAX(TRIM(COALESCE(hcb.booking_tags, ''))) AS booking_tags,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(p.tag, '')), '') ORDER BY p.tag SEPARATOR ', ') AS patient_tags,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(p.panel_company, '')), '') ORDER BY p.panel_company SEPARATOR ', ') AS panel_companies,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(CAST(COALESCE(hbp.cce_level_TBS, 0) AS CHAR)), '') ORDER BY hbp.cce_level_TBS SEPARATOR ',') AS test_booking_status_codes,
                        am.colony_name,
                        am.city,
                        cm.primary_mobile AS caller_mobile,
                        COUNT(DISTINCT hbp.patient_id) AS patient_count
                    FROM hhome_collection_booking hcb
                    INNER JOIN haddress_master am ON am.id = hcb.selected_address_id
                    INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                    LEFT JOIN hhome_collection_booking_patient hbp ON hbp.booking_id = hcb.id
                    LEFT JOIN hpatient_master p ON p.id = hbp.patient_id
                    WHERE hcb.preferred_visit_date = %s
                      AND hcb.booking_status IN (0, 1, 2)
                      AND (hcb.assigned_phlebotomist_id IS NULL OR hcb.assigned_phlebotomist_id = 0)
                    GROUP BY
                        hcb.id,
                        hcb.preferred_time_slot,
                        hcb.booking_status,
                        route_name,
                        hcb.assigned_phlebotomist_id,
                        am.colony_name,
                        am.city,
                        cm.primary_mobile
                    ORDER BY hcb.id DESC
                    """,
                    (target_date,),
                )
                rows = cur.fetchall() or []

                cur.execute(
                    """
                    SELECT
                        ap.id AS appointment_id,
                        ap.booking_id AS parent_booking_id,
                        ap.address_snapshot_json,
                        ap.preferred_time_slot,
                        ap.appointment_status AS booking_status,
                        COALESCE(NULLIF(TRIM(am.route_no), ''), 'UNASSIGNED') AS route_name,
                        ap.assigned_phlebotomist_id,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(hbp.ref_by, '')), '') ORDER BY hbp.ref_by SEPARATOR ', ') AS referred_by,
                        MAX(TRIM(COALESCE(hcb.intrnl_rfrncd_by, ''))) AS internal_referred_by,
                        MAX(COALESCE(hcb.total_amount, 0)) AS total_amount,
                        MAX(TRIM(COALESCE(hcb.booking_tags, ''))) AS booking_tags,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(p.tag, '')), '') ORDER BY p.tag SEPARATOR ', ') AS patient_tags,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(COALESCE(p.panel_company, '')), '') ORDER BY p.panel_company SEPARATOR ', ') AS panel_companies,
                        GROUP_CONCAT(DISTINCT NULLIF(TRIM(CAST(COALESCE(hbp.cce_level_TBS, 0) AS CHAR)), '') ORDER BY hbp.cce_level_TBS SEPARATOR ',') AS test_booking_status_codes,
                        am.colony_name,
                        am.city,
                        cm.primary_mobile AS caller_mobile,
                        COUNT(DISTINCT hbp.patient_id) AS patient_count
                    FROM hhome_collection_booking_appointment ap
                    INNER JOIN hhome_collection_booking hcb ON hcb.id = ap.booking_id
                    LEFT JOIN haddress_master am ON am.id = ap.selected_address_id
                    INNER JOIN hcaller_master cm ON cm.id = hcb.caller_id
                    LEFT JOIN hhome_collection_booking_patient hbp ON hbp.booking_id = hcb.id
                    LEFT JOIN hpatient_master p ON p.id = hbp.patient_id
                    WHERE ap.preferred_visit_date = %s
                      AND ap.appointment_status IN (0, 1, 2)
                      AND (ap.assigned_phlebotomist_id IS NULL OR ap.assigned_phlebotomist_id = 0)
                    GROUP BY
                        ap.id,
                        ap.booking_id,
                        ap.preferred_time_slot,
                        ap.appointment_status,
                        route_name,
                        ap.assigned_phlebotomist_id,
                        am.colony_name,
                        am.city,
                        cm.primary_mobile
                    ORDER BY ap.id DESC
                    """,
                    (target_date,),
                )
                ap_rows = cur.fetchall() or []

            route_set = set(base_routes)
            grid_rows = []
            for r in rows:
                route_name = self._norm_code(r.get("route_name")) or "UNASSIGNED"
                route_set.add(route_name)
                slot_text = self._norm_code(r.get("preferred_time_slot"))
                grid_rows.append(
                    {
                        "row_type": "BOOKING",
                        "booking_id": r.get("booking_id"),
                        "appointment_id": 0,
                        "slot": slot_text,
                        "slot_key": self._slot_start_key(slot_text) or 9999,
                        "route_name": route_name,
                        "booking_status": int(r.get("booking_status") or 0),
                        "assigned_user_id": r.get("assigned_phlebotomist_id"),
                        "referred_by": self._norm_code(r.get("referred_by")),
                        "internal_referred_by": self._norm_code(r.get("internal_referred_by")),
                        "total_amount": float(r.get("total_amount") or 0),
                        "booking_tags": self._norm_code(r.get("booking_tags")),
                        "patient_tags": self._norm_code(r.get("patient_tags")),
                        "panel_companies": self._norm_code(r.get("panel_companies")),
                        "test_booking_status_codes": self._norm_code(r.get("test_booking_status_codes")),
                        "colony_name": self._norm_code(r.get("colony_name")),
                        "city": self._norm_code(r.get("city")),
                        "caller_mobile": self._norm_code(r.get("caller_mobile")),
                        "patient_count": int(r.get("patient_count") or 0),
                    }
                )
            for r in ap_rows:
                try:
                    ap_addr = json.loads(r.get("address_snapshot_json") or "{}")
                except Exception:
                    ap_addr = {}
                route_name = self._norm_code((ap_addr or {}).get("route_no")) or self._norm_code(r.get("route_name")) or "UNASSIGNED"
                route_set.add(route_name)
                slot_text = self._norm_code(r.get("preferred_time_slot"))
                grid_rows.append(
                    {
                        "row_type": "APPOINTMENT",
                        "booking_id": -int(r.get("appointment_id") or 0),
                        "appointment_id": int(r.get("appointment_id") or 0),
                        "parent_booking_id": int(r.get("parent_booking_id") or 0),
                        "slot": slot_text,
                        "slot_key": self._slot_start_key(slot_text) or 9999,
                        "route_name": route_name,
                        "booking_status": int(r.get("booking_status") or 0),
                        "assigned_user_id": r.get("assigned_phlebotomist_id"),
                        "referred_by": self._norm_code(r.get("referred_by")),
                        "internal_referred_by": self._norm_code(r.get("internal_referred_by")),
                        "total_amount": float(r.get("total_amount") or 0),
                        "booking_tags": self._norm_code(r.get("booking_tags")),
                        "patient_tags": self._norm_code(r.get("patient_tags")),
                        "panel_companies": self._norm_code(r.get("panel_companies")),
                        "test_booking_status_codes": self._norm_code(r.get("test_booking_status_codes")),
                        "colony_name": self._norm_code((ap_addr or {}).get("colony_name")) or self._norm_code(r.get("colony_name")),
                        "city": self._norm_code(r.get("city")),
                        "caller_mobile": self._norm_code(r.get("caller_mobile")),
                        "patient_count": int(r.get("patient_count") or 0),
                    }
                )

            ordered_routes = sorted([x for x in route_set if x and x != "UNASSIGNED"], key=str.upper)
            if "UNASSIGNED" in route_set:
                ordered_routes.append("UNASSIGNED")

            phlebos = self.get_phlebotomists()
            return {
                "ok": True,
                "date": target_date,
                "routes": ordered_routes,
                "rows": grid_rows,
                "phlebos": phlebos,
            }
        finally:
            conn.close()






    def commit_assignment_plan(self, plan_date: str, assignments: list, actor_user_id=None, app_obj=None):
        target_date = (plan_date or "").strip()
        if not target_date:
            return {"ok": False, "message": "plan_date is required"}
        if not isinstance(assignments, list) or not assignments:
            return {"ok": False, "message": "assignments are required"}

        normalized = []
        for item in assignments:
            try:
                booking_id = int(item.get("booking_id", 0))
                appointment_id = int(item.get("appointment_id", 0))
                user_id = int(item.get("assigned_user_id", 0))
            except Exception:
                continue
            if appointment_id <= 0 and booking_id < 0:
                appointment_id = abs(booking_id)
            if user_id <= 0:
                continue
            row_type = self._norm_code(item.get("row_type")).upper()
            if appointment_id > 0 or row_type == "APPOINTMENT":
                if appointment_id <= 0:
                    continue
                normalized.append(
                    {
                        "row_type": "APPOINTMENT",
                        "booking_id": int(item.get("parent_booking_id") or 0),
                        "appointment_id": appointment_id,
                        "assigned_user_id": user_id,
                        "grouped_route": self._norm_code(item.get("grouped_route")),
                    }
                )
                continue
            if booking_id <= 0:
                continue
            normalized.append(
                {
                    "row_type": "BOOKING",
                    "booking_id": booking_id,
                    "appointment_id": 0,
                    "assigned_user_id": user_id,
                    "grouped_route": self._norm_code(item.get("grouped_route")),
                }
            )
        if not normalized:
            return {"ok": False, "message": "No valid assignments provided"}

        actor = self._actor(actor_user_id)
        booking_rows = [x for x in normalized if x.get("row_type") == "BOOKING"]
        appointment_rows = [x for x in normalized if x.get("row_type") == "APPOINTMENT"]
        booking_ids = sorted({x["booking_id"] for x in booking_rows if int(x.get("booking_id") or 0) > 0})
        appointment_ids = sorted({x["appointment_id"] for x in appointment_rows if int(x.get("appointment_id") or 0) > 0})
        booking_to_user = {x["booking_id"]: x["assigned_user_id"] for x in booking_rows if int(x.get("booking_id") or 0) > 0}
        appointment_to_user = {x["appointment_id"]: x["assigned_user_id"] for x in appointment_rows if int(x.get("appointment_id") or 0) > 0}

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                valid_booking_ids = set()
                valid_appointment_ids = set()

                if booking_ids:
                    placeholders = ",".join(["%s"] * len(booking_ids))
                    cur.execute(
                        f"""
                        SELECT id
                        FROM hhome_collection_booking
                        WHERE id IN ({placeholders})
                          AND preferred_visit_date = %s
                          AND booking_status IN (0, 1, 2)
                          AND (assigned_phlebotomist_id IS NULL OR assigned_phlebotomist_id = 0)
                        """,
                        booking_ids + [target_date],
                    )
                    valid_booking_ids = {int(r["id"]) for r in (cur.fetchall() or [])}

                if appointment_ids:
                    placeholders = ",".join(["%s"] * len(appointment_ids))
                    cur.execute(
                        f"""
                        SELECT id
                        FROM hhome_collection_booking_appointment
                        WHERE id IN ({placeholders})
                          AND preferred_visit_date = %s
                          AND appointment_status IN (0, 1, 2)
                          AND (assigned_phlebotomist_id IS NULL OR assigned_phlebotomist_id = 0)
                        """,
                        appointment_ids + [target_date],
                    )
                    valid_appointment_ids = {int(r["id"]) for r in (cur.fetchall() or [])}

                if not valid_booking_ids and not valid_appointment_ids:
                    return {"ok": False, "message": "All selected rows are already assigned or not assignable"}

                if valid_booking_ids:
                    cur.executemany(
                        """
                        UPDATE hhome_collection_booking
                        SET assigned_phlebotomist_id=%s, booking_status=1
                        WHERE id=%s
                        """,
                        [(booking_to_user[bid], bid) for bid in booking_ids if bid in valid_booking_ids],
                    )
                    cur.execute(
                        f"""
                        UPDATE hhome_collection_booking_patient
                        SET booking_patient_status = 1
                        WHERE booking_id IN ({",".join(["%s"] * len(valid_booking_ids))})
                          AND booking_patient_status = 0
                        """,
                        list(valid_booking_ids),
                    )

                if valid_appointment_ids:
                    cur.executemany(
                        """
                        UPDATE hhome_collection_booking_appointment
                        SET assigned_phlebotomist_id=%s, appointment_status=1, updated_by=%s
                        WHERE id=%s
                        """,
                        [(appointment_to_user[aid], actor, aid) for aid in appointment_ids if aid in valid_appointment_ids],
                    )

                assignment_payloads = self._build_assignment_whatsapp_payloads(
                    cur,
                    booking_ids=sorted(valid_booking_ids),
                    appointment_ids=sorted(valid_appointment_ids),
                )

            conn.commit()
            queued = 0
            for item in assignment_payloads:
                if item.get("_local_only"):
                    continue
                if self._queue_assignment_whatsapp(app_obj, item):
                    queued += 1
            local_queued = self._queue_assignment_local_whatsapp(app_obj, assignment_payloads)
            updated_count = len(valid_booking_ids) + len(valid_appointment_ids)
            return {
                "ok": True,
                "updated_count": updated_count,
                "updated_booking_count": len(valid_booking_ids),
                "updated_appointment_count": len(valid_appointment_ids),
                "skipped_assigned_count": max(len(normalized) - updated_count, 0),
                "whatsapp_queued": queued,
                "local_whatsapp_queued_count": local_queued,
            }
        except Exception as exc:
            conn.rollback()
            return {"ok": False, "message": str(exc)}
        finally:
            conn.close()



