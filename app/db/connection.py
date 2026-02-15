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
    "host": os.getenv("WA_HOST", "192.168.0.167"),
    "user": os.getenv("WA_USER", "sahil"),
    "password": os.getenv("WA_PASSWORD", "sahil@123"),
    "database": os.getenv("WA_NAME", "creoianw_bhasin"),
}

FAIL_MSG_DB = {
    "host": os.getenv("FAIL_HOST", "192.168.0.167"),
    "user": os.getenv("FAIL_USER", "sahil"),
    "password": os.getenv("FAIL_PASSWORD", "sahil@123"),
    "database": os.getenv("FAIL_NAME", "labmaterecod"),
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


# ---------------- Fail Message DB ----------------
def get_fail_message_connection():
    """Connection helper for fail message DB (labmatewhats)."""
    return mysql.connector.connect(**FAIL_MSG_DB)


def get_whatsapp_groups_connection():
    """Connection for whatsapp_groups table in whatsapp_group_id database."""
    return pymysql.connect(
        host=MAIN_DB["host"],      # Same server
        user=MAIN_DB["user"],      # Same user  
        password=MAIN_DB["password"],  # Same password
        database="whatsapp_group_id",  # ✅ SPECIFIC DATABASE
        cursorclass=pymysql.cursors.DictCursor
    )