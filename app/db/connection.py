import os
import pymysql
import mysql.connector


# Centralized DB configs (env-first, fallback to previous defaults)
MAIN_DB = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "lead_management"),
}

LABMATE_DB = {
    "host": os.getenv("LABMATE_HOST", "localhost"),
    "user": os.getenv("LABMATE_USER", "root"),
    "password": os.getenv("LABMATE_PASSWORD", ""),
    "database": os.getenv("LABMATE_NAME", "labmate_data"),
}

WHATSAPP_DB = {
    "host": os.getenv("WA_HOST", "10.1.1.51"),
    "user": os.getenv("WA_USER", "sahil"),
    "password": os.getenv("WA_PASSWORD", "sahil@123"),
    "database": os.getenv("WA_NAME", "creoianw_bhasin"),
}

WHATSAPP_PANEL_DB = {
    "host": os.getenv("WA_PANEL_HOST", "localhost"),
    "user": os.getenv("WA_PANEL_USER", "root"),
    "password": os.getenv("WA_PANEL_PASSWORD", ""),
    "database": os.getenv("WA_PANEL_NAME", "whatsapp"),
}

FAIL_MSG_DB = {
    "host": os.getenv("FAIL_HOST", "10.1.1.51"),
    "user": os.getenv("FAIL_USER", "sahil"),
    "password": os.getenv("FAIL_PASSWORD", "sahil@123"),
    "database": os.getenv("FAIL_NAME", "labmaterecod"),
}

COMPLAINT_DB = {
    "host": os.getenv("COMPLAINT_HOST", "10.1.1.53"),
    "port": int(os.getenv("COMPLAINT_PORT", "3308")),
    "user": os.getenv("COMPLAINT_USER", "arpra"),
    "password": os.getenv("COMPLAINT_PASSWORD", "arpra"),
    "database": os.getenv("COMPLAINT_NAME", "arpra_voc"),
    "connection_timeout": int(os.getenv("COMPLAINT_TIMEOUT", "5")),
}

VENE_DB = {
    "host": os.getenv("VENE_HOST", "10.1.1.53"),
    "port": int(os.getenv("VENE_PORT", "8091")),
    "user": os.getenv("VENE_USER", "root"),
    "password": os.getenv("VENE_PASSWORD", "example"),
    "database": os.getenv("VENE_NAME", "hiccup_ticket"),
    "connection_timeout": int(os.getenv("VENE_TIMEOUT", "5")),
}

BHASIN7001_DB = {
    "host": os.getenv("B7001_HOST", "localhost"),
    "user": os.getenv("B7001_USER", "root"),
    "password": os.getenv("B7001_PASSWORD", ""),
    "database": os.getenv("B7001_NAME", "bhasin_7001_new"),
}


# ---------------- Lead Management DB ----------------
def get_db_connection():
    """Connection helper for lead_management DB (main CRM / tickets DB)."""
    return pymysql.connect(**MAIN_DB, cursorclass=pymysql.cursors.DictCursor)


# ---------------- Labmate Data DB ----------------
def get_labmate_connection():
    """Connection helper for labmate_data DB (read-only Labmate LIMS data)."""
    return pymysql.connect(**LABMATE_DB, cursorclass=pymysql.cursors.DictCursor)


# ---------------- WhatsApp/WABA DB ----------------
def get_whatsapp_connection():
    """Connection helper for WhatsApp engagement DB."""
    return pymysql.connect(**WHATSAPP_DB, cursorclass=pymysql.cursors.DictCursor)


def get_whatsapp_panel_connection():
    """Connection helper for the Home Collection WhatsApp panel database."""
    return pymysql.connect(**WHATSAPP_PANEL_DB, cursorclass=pymysql.cursors.DictCursor)


# ---------------- Fail Message DB ----------------
def get_fail_message_connection():
    """Connection helper for fail message DB (labmatewhats)."""
    return mysql.connector.connect(**FAIL_MSG_DB)


def get_complaint_connection():
    """Connection helper for complaint counter DB."""
    return mysql.connector.connect(**COMPLAINT_DB)


def get_venepunchre_connection():
    """Connection helper for venepunchre hiccup_ticket DB."""
    conn = mysql.connector.connect(**VENE_DB)
    cur = conn.cursor()
    try:
        cur.execute("SET time_zone = '+05:30'")
    finally:
        cur.close()
    return conn


def get_bhasin7001_connection():
    """Connection helper for Home Collection panel/test catalog DB."""
    return pymysql.connect(**BHASIN7001_DB, cursorclass=pymysql.cursors.DictCursor)


def get_whatsapp_groups_connection():
    """Connection for whatsapp_groups table in whatsapp_group_id database."""
    return pymysql.connect(
        host=MAIN_DB["host"],      # Same server
        user=MAIN_DB["user"],      # Same user  
        password=MAIN_DB["password"],  # Same password
        database="whatsapp_group_id",  # ✅ SPECIFIC DATABASE
        cursorclass=pymysql.cursors.DictCursor
    )
