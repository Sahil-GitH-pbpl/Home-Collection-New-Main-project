import json
import os
from datetime import datetime

from app.db.connection import get_whatsapp_panel_connection


WHATSAPP_OUTGOING_TABLE = os.getenv("WA_PANEL_OUTGOING_TABLE", "ofc_waba_outgoing")


def _json_text(value):
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def log_whatsapp_send(
    *,
    action_type,
    api_type,
    related_id=None,
    related_code=None,
    recipient="",
    message_text=None,
    template_name=None,
    payload_json=None,
    media_url=None,
    is_success=False,
    error_text=None,
):
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO whatsapp_send_logs
                  (action_type, api_type, related_id, related_code, recipient, message_text,
                   template_name, payload_json, media_url, is_success, error_text, sent_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    action_type,
                    api_type,
                    str(related_id or "") or None,
                    str(related_code or "") or None,
                    str(recipient or ""),
                    message_text,
                    template_name,
                    _json_text(payload_json),
                    media_url,
                    1 if is_success else 0,
                    None if is_success else (str(error_text or "")[:2000] or None),
                    datetime.now() if is_success else None,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def save_patient_chat_message(
    *,
    mobile,
    message_text,
    empname,
    media_url=None,
    media_filename=None,
    provider_message_id=None,
    delivery_status=None,
    delivery_status_remark=None,
):
    conn = get_whatsapp_panel_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO `{WHATSAPP_OUTGOING_TABLE}`
                  (mobile, msg, img, pdff, docid, imgid, empname,
                   provider_message_id, delivery_status, delivery_status_remark)
                VALUES (%s,%s,'',%s,%s,'',%s,%s,%s,%s)
                """,
                (
                    str(mobile or ""),
                    message_text or "",
                    media_filename or "",
                    media_url or "",
                    (empname or "System")[:225],
                    provider_message_id,
                    delivery_status,
                    (delivery_status_remark or "")[:1000],
                ),
            )
        conn.commit()
    finally:
        conn.close()
