import base64
import json
import os
import re
import shutil
import subprocess
import sqlite3
import threading
import time
import traceback
import html
import gc
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
# Honor Render's X-Forwarded-Proto/X-Forwarded-Host so externally generated
# OAuth URLs use the public HTTPS address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-in-render")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("RENDER", "").lower() == "true":
    app.config["SESSION_COOKIE_SECURE"] = True

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "tasks.db"
DB_BACKUP_DIR = DATA_DIR / "db-backups"
DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
DB_STARTUP_ERROR = ""
DB_LAST_QUICK_CHECK = []

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

GMAIL_SYNC_QUERY = os.environ.get(
    "GMAIL_SYNC_QUERY",
    "newer_than:30d -in:sent -in:drafts -in:spam -in:trash "
    "-category:promotions -category:social -category:forums",
)
AUTO_GMAIL_SYNC_MINUTES = max(0, int(os.environ.get("AUTO_GMAIL_SYNC_MINUTES", "15") or "15"))
GMAIL_SCAN_MAX_MESSAGES = max(25, int(os.environ.get("GMAIL_SCAN_MAX_MESSAGES", "500") or "500"))
GMAIL_ANALYZE_MAX_NEW = max(5, int(os.environ.get("GMAIL_ANALYZE_MAX_NEW", "75") or "75"))
OPENAI_EMAIL_BODY_CHARS = max(2000, int(os.environ.get("OPENAI_EMAIL_BODY_CHARS", "12000") or "12000"))
EMAIL_RESEARCH_MAX_MESSAGES = max(50, int(os.environ.get("EMAIL_RESEARCH_MAX_MESSAGES", "500") or "500"))
EMAIL_DISCOVERY_MAX_MESSAGES = max(10, int(os.environ.get("EMAIL_DISCOVERY_MAX_MESSAGES", "60") or "60"))
EMAIL_DISCOVERY_LOOKBACK_DAYS = max(0, int(os.environ.get("EMAIL_DISCOVERY_LOOKBACK_DAYS", "0") or "0"))
SENT_MONITOR_LOOKBACK_DAYS = max(7, int(os.environ.get("SENT_MONITOR_LOOKBACK_DAYS", "30") or "30"))
SENT_FOLLOWUP_AFTER_DAYS = max(1, int(os.environ.get("SENT_FOLLOWUP_AFTER_DAYS", "3") or "3"))
SENT_SCAN_MAX_MESSAGES = max(25, int(os.environ.get("SENT_SCAN_MAX_MESSAGES", "300") or "300"))
CHAT_SYNC_LOOKBACK_DAYS = max(7, int(os.environ.get("CHAT_SYNC_LOOKBACK_DAYS", "30") or "30"))
CHAT_SCAN_MAX_SPACES = max(10, int(os.environ.get("CHAT_SCAN_MAX_SPACES", "100") or "100"))
CHAT_SCAN_MAX_MESSAGES_PER_SPACE = max(20, int(os.environ.get("CHAT_SCAN_MAX_MESSAGES_PER_SPACE", "100") or "100"))
WATCH_DOMAIN_LOOKBACK_DAYS = max(30, int(os.environ.get("WATCH_DOMAIN_LOOKBACK_DAYS", "90") or "90"))
AI_CONTEXT_CHAR_BUDGET = max(30000, int(os.environ.get("AI_CONTEXT_CHAR_BUDGET", "90000") or "90000"))
SYNC_LOCK = threading.Lock()
MANUAL_SYNC_STATE_LOCK = threading.Lock()
MANUAL_SYNC_STATE = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "error": "",
    "result": {},
}

# Google user authorization for Gmail plus read-only Google Chat.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CHAT_READ_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
]
CHAT_SEND_SCOPE = "https://www.googleapis.com/auth/chat.messages.create"
CHAT_SCOPES = [*CHAT_READ_SCOPES, CHAT_SEND_SCOPE]
GOOGLE_SCOPES = [GMAIL_SCOPE, *CHAT_SCOPES]

SEED_TASKS = [
    ("payment", "BB Direct", "Pay overdue BB Direct invoice — $1,223.37", "2026-06-30", "urgent", "Open",
     "Invoice is substantially past due.", 1223.37, "USD", "", ""),
    ("client", "L. Grace Brands / MCN", "Provide MCN impression, click and traffic verification", "2026-08-01", "urgent", "Open",
     "Confirm impression and click totals, traffic quality, source providers and corrected UTM / redirect routing.", 0, "USD", "", ""),
    ("client", "Pillar Media / Dan Watts", "Resolve reporting and campaign delivery issues", "2026-08-05", "urgent", "Open",
     "Provide the detailed reporting, pacing and campaign-delivery update previously promised.", 0, "USD", "", ""),
    ("payment", "The Trade Desk", "Send funding documents and close AR follow-up", "2026-08-06", "urgent", "Open",
     "Funding documents and AR follow-up remain open.", 0, "USD", "", ""),
    ("payment", "WCMH / Nexstar", "Resolve remaining Ohio RV & Boat Show balance", "2026-08-07", "urgent", "Open",
     "Verify current balance after partial payment and finish remaining amount.", 10000, "USD", "", ""),
    ("payment", "Extend / Advertising Platforms", "Verify advertising card declines are fully resolved", "2026-08-07", "urgent", "Open",
     "Confirm funding and payment methods so campaigns are not interrupted.", 0, "USD", "", ""),
    ("payment", "HighLevel / Smart 1 Suite", "Fix billing card issue blocking WordPress hosting setup", "2026-08-11", "urgent", "Open",
     "Verify the billing issue is fully resolved.", 10.80, "USD", "", ""),
    ("payment", "Capitalize Group", "Resolve settlement balance shown as $33,165.32", "2026-08-11", "urgent", "Open",
     "Reconcile settlement balance and payment proof.", 33165.32, "USD", "", ""),
    ("client", "TrimGlow", "Set up TrimGlow Google Business Profile (GMB)", "2026-08-12", "urgent", "Open",
     "Google Business Profile setup is due now.", 0, "USD", "", ""),
    ("payment", "TriNet Payroll Funding", "Fund Aug. 14 payroll wires — $55,910.22 total", "2026-08-14", "urgent", "Open",
     "Payroll funding required for Aug. 14 check dates.", 55910.22, "USD", "", ""),
    ("payment", "Erie Insurance / Haughn", "Pay $3,132.94 manually and restore Auto-Pay", "2026-08-21", "urgent", "Open",
     "Payment required to avoid policy cancellation.", 3132.94, "USD", "", ""),
    ("client", "Miracle Motor Mart", "Complete Google Ads transition before Dealer.com pause", "2026-09-01", "high", "Working",
     "Confirm access, campaign build and clean Smart 1 takeover.", 0, "USD", "", ""),
    ("client", "L. Grace Brands", "Apply $11,905.72 July underdelivery credit to August invoice", "", "urgent", "Open",
     "Apply requested underdelivery credit to the August invoice.", 0, "USD", "", ""),
    ("client", "Schmidt's", "Confirm Marketing Scorecard is loaded into Smart 1 Suite", "", "high", "Open",
     "Confirm the marketing scorecard is loaded and ready.", 0, "USD", "", ""),
    ("client", "NC / Erik Accounts", "Resolve July underperformance and manage August makegoods", "", "urgent", "Working",
     "Manage makegoods and invoice review follow-up.", 0, "USD", "", ""),
    ("client", "Schmidt's", "Finish menu website update and confirm completion", "", "high", "Open",
     "Confirm menu update is live and notify client.", 0, "USD", "", ""),
    ("client", "MSU-Northern / New Media Broadcasters", "Send 2025–26 campaign statistics", "", "high", "Open",
     "Provide campaign statistics or reporting link.", 0, "USD", "", ""),
    ("payment", "AWS", "Verify past-due AWS account is cured", "", "urgent", "Open",
     "Verify balance and payment method are current.", 0, "USD", "", ""),
    ("payment", "OnDeck", "Confirm ACH authorization and account-current status", "", "high", "Waiting",
     "Verify catch-up payment clears and account returns to current.", 1428.69, "USD", "", ""),
    ("client", "Icon Solar", "Set up GPT Ads for Icon Solar", "", "high", "Open",
     "Build and configure the GPT Ads initiative for Icon Solar.", 0, "USD", "", ""),
    ("client", "Icon Solar", "Fix Wanda videos for Icon Solar", "", "high", "Open",
     "Review and correct the Wanda video assets for Icon Solar.", 0, "USD", "", ""),
    ("client", "Text Doctor", "Set up Text Doctor", "", "normal", "Open", "Task added manually.", 0, "USD", "", ""),
    ("client", "Home Loan", "Set up Home Loan", "", "normal", "Open", "Task added manually.", 0, "USD", "", ""),
]


def connect_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = FULL")
    con.execute("PRAGMA wal_autocheckpoint = 500")
    return con


def column_names(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_column(con, table, definition):
    name = definition.split()[0]
    if name not in column_names(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_db():
    with connect_db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'client',
            party TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT DEFAULT '',
            due_date TEXT DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'Open',
            email_url TEXT DEFAULT '',
            email_to TEXT DEFAULT '',
            email_subject TEXT DEFAULT '',
            gmail_message_id TEXT DEFAULT '',
            gmail_thread_id TEXT DEFAULT '',
            amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            invoice_number TEXT DEFAULT '',
            invoice_sent INTEGER NOT NULL DEFAULT 0,
            invoice_sent_at TEXT DEFAULT '',
            suggested_reply TEXT DEFAULT '',
            ai_confidence TEXT DEFAULT '',
            completed INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS gmail_processed (
            gmail_message_id TEXT PRIMARY KEY,
            gmail_thread_id TEXT DEFAULT '',
            classification TEXT DEFAULT '',
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gmail_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT NOT NULL UNIQUE,
            gmail_thread_id TEXT DEFAULT '',
            sender_name TEXT DEFAULT '',
            sender_email TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            snippet TEXT DEFAULT '',
            received_at TEXT DEFAULT '',
            suggested_title TEXT DEFAULT '',
            suggested_category TEXT NOT NULL DEFAULT 'client',
            suggested_priority TEXT NOT NULL DEFAULT 'normal',
            suggested_due_date TEXT DEFAULT '',
            suggested_summary TEXT DEFAULT '',
            suggested_reply TEXT DEFAULT '',
            payment_amount REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'USD',
            invoice_number TEXT DEFAULT '',
            invoice_sent INTEGER NOT NULL DEFAULT 0,
            confidence TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            analyzer TEXT DEFAULT '',
            email_url TEXT DEFAULT '',
            state TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS task_research_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            confidence TEXT DEFAULT '',
            sources_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_email_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            gmail_message_id TEXT NOT NULL UNIQUE,
            gmail_thread_id TEXT DEFAULT '',
            sender_name TEXT DEFAULT '',
            sender_email TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            snippet TEXT DEFAULT '',
            received_at TEXT DEFAULT '',
            email_url TEXT DEFAULT '',
            match_method TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS watch_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE,
            label TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS not_task_training (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL DEFAULT 'gmail',
            source_id TEXT DEFAULT '',
            sender_name TEXT DEFAULT '',
            sender_email TEXT DEFAULT '',
            sender_domain TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            excerpt TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ignore_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL DEFAULT 'gmail',
            rule_type TEXT NOT NULL DEFAULT 'domain',
            value TEXT NOT NULL,
            note TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_type, rule_type, value)
        );

        CREATE TABLE IF NOT EXISTS gpt_help_suppressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_domain TEXT DEFAULT '',
            subject_pattern TEXT NOT NULL,
            note TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sender_domain, subject_pattern)
        );

        CREATE TABLE IF NOT EXISTS task_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            source TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id,email),
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS task_resolution_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'gmail',
            source_id TEXT DEFAULT '',
            summary TEXT NOT NULL,
            confidence TEXT DEFAULT '',
            sources_json TEXT DEFAULT '[]',
            state TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_at TEXT DEFAULT '',
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sent_monitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT NOT NULL UNIQUE,
            gmail_thread_id TEXT DEFAULT '',
            task_id INTEGER DEFAULT 0,
            recipients TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            sent_at TEXT DEFAULT '',
            followup_due TEXT DEFAULT '',
            state TEXT NOT NULL DEFAULT 'monitoring',
            reason TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chat_processed (
            message_name TEXT PRIMARY KEY,
            space_name TEXT DEFAULT '',
            classification TEXT DEFAULT '',
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chat_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_name TEXT NOT NULL UNIQUE,
            space_name TEXT DEFAULT '',
            space_display_name TEXT DEFAULT '',
            sender_user_name TEXT DEFAULT '',
            sender_display_name TEXT DEFAULT '',
            message_text TEXT DEFAULT '',
            create_time TEXT DEFAULT '',
            suggested_title TEXT DEFAULT '',
            suggested_category TEXT NOT NULL DEFAULT 'client',
            suggested_priority TEXT NOT NULL DEFAULT 'normal',
            suggested_due_date TEXT DEFAULT '',
            suggested_summary TEXT DEFAULT '',
            suggested_reply TEXT DEFAULT '',
            confidence TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            gpt_can_help INTEGER NOT NULL DEFAULT 0,
            gpt_help_prompt TEXT DEFAULT '',
            gpt_help_reason TEXT DEFAULT '',
            state TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS task_chat_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            message_name TEXT NOT NULL UNIQUE,
            space_name TEXT DEFAULT '',
            space_display_name TEXT DEFAULT '',
            sender_display_name TEXT DEFAULT '',
            message_text TEXT DEFAULT '',
            create_time TEXT DEFAULT '',
            match_method TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS meeting_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_message_id TEXT NOT NULL UNIQUE,
            gmail_thread_id TEXT DEFAULT '',
            meeting_title TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            tasks_json TEXT DEFAULT '[]',
            email_url TEXT DEFAULT '',
            received_at TEXT DEFAULT '',
            analyzer TEXT DEFAULT '',
            state TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Safe migrations from older versions.
        for definition in [
            "gmail_message_id TEXT DEFAULT ''",
            "gmail_thread_id TEXT DEFAULT ''",
            "email_to TEXT DEFAULT ''",
            "email_subject TEXT DEFAULT ''",
            "amount REAL NOT NULL DEFAULT 0",
            "currency TEXT NOT NULL DEFAULT 'USD'",
            "invoice_number TEXT DEFAULT ''",
            "invoice_sent INTEGER NOT NULL DEFAULT 0",
            "invoice_sent_at TEXT DEFAULT ''",
            "suggested_reply TEXT DEFAULT ''",
            "ai_confidence TEXT DEFAULT ''",
            "gpt_can_help INTEGER NOT NULL DEFAULT 0",
            "gpt_help_prompt TEXT DEFAULT ''",
            "gpt_help_reason TEXT DEFAULT ''",
            "recipient_count INTEGER NOT NULL DEFAULT 0",
            "assignee TEXT DEFAULT ''",
            "source_kind TEXT DEFAULT 'manual'",
            "chat_space_name TEXT DEFAULT ''",
            "chat_thread_name TEXT DEFAULT ''",
            "chat_message_name TEXT DEFAULT ''",
            "chat_space_uri TEXT DEFAULT ''",
            "paid_at TEXT DEFAULT ''",
            "paid_amount REAL NOT NULL DEFAULT 0",
            "payment_reference TEXT DEFAULT ''",
            "payment_note TEXT DEFAULT ''",
            "source_received_at TEXT DEFAULT ''",
        ]:
            ensure_column(con, "tasks", definition)

        for definition in [
            "suggested_title TEXT DEFAULT ''",
            "suggested_summary TEXT DEFAULT ''",
            "suggested_reply TEXT DEFAULT ''",
            "payment_amount REAL NOT NULL DEFAULT 0",
            "currency TEXT NOT NULL DEFAULT 'USD'",
            "invoice_number TEXT DEFAULT ''",
            "invoice_sent INTEGER NOT NULL DEFAULT 0",
            "confidence TEXT DEFAULT ''",
            "analyzer TEXT DEFAULT ''",
            "gpt_can_help INTEGER NOT NULL DEFAULT 0",
            "gpt_help_prompt TEXT DEFAULT ''",
            "gpt_help_reason TEXT DEFAULT ''",
            "recipient_count INTEGER NOT NULL DEFAULT 0",
            "participants_json TEXT DEFAULT '[]'",
            "related_task_id INTEGER NOT NULL DEFAULT 0",
        ]:
            ensure_column(con, "gmail_suggestions", definition)

        for definition in [
            "direction TEXT DEFAULT 'incoming'",
            "to_emails TEXT DEFAULT ''",
            "cc_emails TEXT DEFAULT ''",
        ]:
            ensure_column(con, "task_email_updates", definition)

        for definition in [
            "thread_name TEXT DEFAULT ''",
            "space_uri TEXT DEFAULT ''",
            "gpt_help_reason TEXT DEFAULT ''",
            "payment_amount REAL NOT NULL DEFAULT 0",
            "currency TEXT NOT NULL DEFAULT 'USD'",
            "invoice_number TEXT DEFAULT ''",
        ]:
            ensure_column(con, "chat_suggestions", definition)

        for definition in [
            "thread_name TEXT DEFAULT ''",
            "space_uri TEXT DEFAULT ''",
            "direction TEXT DEFAULT 'incoming'",
        ]:
            ensure_column(con, "task_chat_updates", definition)

        for definition in [
            "email_url TEXT DEFAULT ''",
            "party TEXT DEFAULT ''",
            "summary TEXT DEFAULT ''",
            "priority TEXT DEFAULT 'high'",
            "last_response_at TEXT DEFAULT ''",
            "recipient_count INTEGER NOT NULL DEFAULT 0",
        ]:
            ensure_column(con, "sent_monitors", definition)

        for definition in [
            "source_url TEXT DEFAULT ''",
        ]:
            ensure_column(con, "task_resolution_reviews", definition)

        if con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0:
            con.executemany("""
                INSERT INTO tasks
                (category,party,title,due_date,priority,status,detail,amount,currency,invoice_number,email_url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, SEED_TASKS)

        for key, value in {
            "gmail_auto_add": "0",
            "gmail_last_sync": "",
            "gmail_last_error": "",
            "openai_last_error": "",
            "chat_last_sync": "",
            "chat_last_error": "",
            "sent_last_sync": "",
            "sent_last_error": "",
            "meeting_last_sync": "",
            "meeting_last_error": "",
            "auto_add_invoices": "1",
        }.items():
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))

        # Keep these automations permanently enabled; they no longer need main-page switches.
        con.execute(
            "INSERT INTO settings(key,value) VALUES('gmail_auto_add','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        con.execute(
            "INSERT INTO settings(key,value) VALUES('auto_add_invoices','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )

        # Explicit user training: xwf.google.com is never a task source.
        con.execute(
            """
            INSERT OR IGNORE INTO ignore_sources(source_type,rule_type,value,note,enabled)
            VALUES('gmail','domain','xwf.google.com','Explicitly marked by user as not a task source',1)
            """
        )

        con.execute(
            """
            INSERT OR IGNORE INTO ignore_sources(source_type,rule_type,value,note,enabled)
            VALUES('chat','space','sales team to me','Explicitly removed by user',1)
            """
        )

        con.execute(
            """
            UPDATE chat_suggestions
            SET state='trained_not_task',updated_at=CURRENT_TIMESTAMP
            WHERE lower(trim(space_display_name))='sales team to me'
              AND state='new'
            """
        )

        con.execute(
            """
            INSERT INTO notes(task_id,body)
            SELECT id,'Removed from open tasks because the Google Chat space "Sales Team to Me" is ignored.'
            FROM tasks
            WHERE completed=0 AND source_kind='chat' AND lower(trim(party))='sales team to me'
            """
        )
        con.execute(
            """
            UPDATE tasks
            SET completed=1,completed_at=CURRENT_TIMESTAMP,status='Completed',updated_at=CURRENT_TIMESTAMP
            WHERE completed=0 AND source_kind='chat' AND lower(trim(party))='sales team to me'
            """
        )
        con.execute(
            """
            UPDATE gmail_suggestions
            SET state='trained_not_task',updated_at=CURRENT_TIMESTAMP
            WHERE lower(sender_email) LIKE '%@xwf.google.com'
              AND state='new'
            """
        )

        # Backfill "when this task came in" for older tasks.
        # Prefer the original Gmail/Chat source timestamp; otherwise use task creation time.
        con.execute(
            """
            UPDATE tasks
            SET source_received_at = COALESCE(
                NULLIF((
                    SELECT gs.received_at
                    FROM gmail_suggestions gs
                    WHERE gs.gmail_message_id = tasks.gmail_message_id
                    LIMIT 1
                ), ''),
                NULLIF((
                    SELECT MIN(teu.received_at)
                    FROM task_email_updates teu
                    WHERE teu.task_id = tasks.id
                      AND teu.received_at <> ''
                ), ''),
                NULLIF((
                    SELECT cs.create_time
                    FROM chat_suggestions cs
                    WHERE cs.message_name = tasks.chat_message_name
                    LIMIT 1
                ), ''),
                NULLIF((
                    SELECT MIN(tcu.create_time)
                    FROM task_chat_updates tcu
                    WHERE tcu.task_id = tasks.id
                      AND tcu.create_time <> ''
                ), ''),
                created_at
            )
            WHERE COALESCE(source_received_at, '') = ''
            """
        )


def database_quick_check(path=None):
    """Read-only integrity probe. Returns a list of SQLite quick_check messages."""
    target = Path(path or DB_PATH)
    if not target.exists():
        return ["missing"]
    uri = f"file:{target}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            rows = con.execute("PRAGMA quick_check").fetchall()
            return [str(r[0]) for r in rows]
        finally:
            con.close()
    except Exception as exc:
        return [f"{type(exc).__name__}: {exc}"]


def database_is_healthy(path=None):
    result = database_quick_check(path)
    return result == ["ok"]


def copy_database_artifacts(reason="manual"):
    """Copy DB/WAL/SHM before any recovery attempt."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    folder = DB_BACKUP_DIR / f"{stamp}-{reason}"
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for suffix in ("", "-wal", "-shm"):
        source = Path(str(DB_PATH) + suffix)
        if source.exists():
            dest = folder / source.name
            shutil.copy2(source, dest)
            copied.append(str(dest))
    return folder, copied


def consistent_database_backup(force=False):
    """Create a logical SQLite backup from a healthy live DB and retain recent copies."""
    if not DB_PATH.exists() or not database_is_healthy():
        return None

    now = datetime.now().astimezone()
    existing = sorted(DB_BACKUP_DIR.glob("healthy-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if existing and not force:
        age_seconds = now.timestamp() - existing[0].stat().st_mtime
        if age_seconds < 6 * 60 * 60:
            return existing[0]

    dest = DB_BACKUP_DIR / f"healthy-{now.strftime('%Y%m%d-%H%M%S')}.db"
    source_con = sqlite3.connect(DB_PATH, timeout=30)
    backup_con = sqlite3.connect(dest)
    try:
        source_con.backup(backup_con)
    finally:
        backup_con.close()
        source_con.close()

    # Keep the newest 14 logical backups.
    backups = sorted(DB_BACKUP_DIR.glob("healthy-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[14:]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def safe_init_db():
    global DB_STARTUP_ERROR, DB_LAST_QUICK_CHECK
    try:
        if DB_PATH.exists():
            DB_LAST_QUICK_CHECK = database_quick_check()
            if DB_LAST_QUICK_CHECK != ["ok"]:
                DB_STARTUP_ERROR = "SQLite integrity check failed: " + " | ".join(DB_LAST_QUICK_CHECK[:10])
                app.logger.error(DB_STARTUP_ERROR)
                return False

        init_db()
        DB_LAST_QUICK_CHECK = database_quick_check()
        if DB_LAST_QUICK_CHECK != ["ok"]:
            DB_STARTUP_ERROR = "SQLite integrity check failed after initialization: " + " | ".join(DB_LAST_QUICK_CHECK[:10])
            app.logger.error(DB_STARTUP_ERROR)
            return False

        DB_STARTUP_ERROR = ""
        try:
            consistent_database_backup()
        except Exception:
            app.logger.exception("Could not create startup SQLite backup")
        return True
    except sqlite3.DatabaseError as exc:
        DB_STARTUP_ERROR = f"{type(exc).__name__}: {exc}"
        app.logger.exception("SQLite startup failed; entering recovery mode")
        return False
    except Exception as exc:
        DB_STARTUP_ERROR = f"{type(exc).__name__}: {exc}"
        app.logger.exception("Database startup failed; entering recovery mode")
        return False


def native_sqlite3_path():
    return shutil.which("sqlite3")


def attempt_native_sqlite_recovery():
    """Use SQLite's official .recover command when the native CLI is available."""
    global DB_STARTUP_ERROR, DB_LAST_QUICK_CHECK

    cli = native_sqlite3_path()
    if not cli:
        return {
            "ok": False,
            "error": "The native sqlite3 CLI is not installed on this Render runtime. Use a Render disk snapshot or run recovery from a machine with the sqlite3 CLI.",
        }

    backup_folder, copied = copy_database_artifacts("pre-recovery")
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    sql_path = backup_folder / f"recover-{stamp}.sql"
    recovered_path = DATA_DIR / f"tasks.recovered-{stamp}.db"

    try:
        with sql_path.open("wb") as out:
            proc = subprocess.run(
                [cli, str(DB_PATH), ".recover --ignore-freelist"],
                stdout=out,
                stderr=subprocess.PIPE,
                timeout=180,
            )
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": proc.stderr.decode("utf-8", "replace")[-4000:],
                "backup_folder": str(backup_folder),
                "copied": copied,
            }

        with sql_path.open("rb") as source_sql:
            rebuild = subprocess.run(
                [cli, str(recovered_path)],
                stdin=source_sql,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
            )

        # sqlite3 may report salvage warnings but still produce a usable DB.
        check = database_quick_check(recovered_path)
        if check != ["ok"]:
            return {
                "ok": False,
                "error": "Recovered database did not pass quick_check: " + " | ".join(check[:20]),
                "sqlite_stderr": rebuild.stderr.decode("utf-8", "replace")[-4000:],
                "backup_folder": str(backup_folder),
                "recovered_path": str(recovered_path),
            }

        # Quarantine the corrupt live artifacts, then atomically install recovered DB.
        quarantine = backup_folder / "quarantined-live"
        quarantine.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            current = Path(str(DB_PATH) + suffix)
            if current.exists():
                shutil.move(str(current), str(quarantine / current.name))

        os.replace(recovered_path, DB_PATH)

        if not safe_init_db():
            return {
                "ok": False,
                "error": DB_STARTUP_ERROR,
                "backup_folder": str(backup_folder),
            }

        consistent_database_backup(force=True)
        return {
            "ok": True,
            "message": "SQLite recovery completed and the recovered database passed quick_check.",
            "backup_folder": str(backup_folder),
            "quick_check": database_quick_check(),
        }
    except Exception as exc:
        app.logger.exception("Native SQLite recovery failed")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "backup_folder": str(backup_folder),
        }


safe_init_db()


def get_setting(key, default=""):
    if DB_STARTUP_ERROR:
        return default
    with connect_db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    if DB_STARTUP_ERROR:
        return
    with connect_db() as con:
        con.execute("""
            INSERT INTO settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if APP_PASSWORD and not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


# ---------------- OAuth / Gmail helpers ----------------

def gmail_configured():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def redirect_uri():
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    return request.url_root.rstrip("/") + url_for("gmail_callback")


def oauth_client_config():
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri()],
        }
    }


def save_credentials(creds):
    set_setting("gmail_credentials", creds.to_json())


def load_credentials():
    raw = get_setting("gmail_credentials", "")
    if not raw:
        return None
    try:
        info = json.loads(raw)
        # Preserve the scopes Google actually granted. Passing new scopes here would make an old token
        # look more capable than it really is after this app adds Google Chat.
        creds = Credentials.from_authorized_user_info(info)
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleAuthRequest())
            save_credentials(creds)
        return creds if creds.valid else None
    except Exception as exc:
        set_setting("gmail_last_error", f"Credential error: {exc}")
        return None


def credentials_have_scopes(creds, scopes):
    if not creds:
        return False
    try:
        return creds.has_scopes(scopes)
    except Exception:
        granted = set(getattr(creds, "scopes", None) or []) | set(getattr(creds, "granted_scopes", None) or [])
        return set(scopes).issubset(granted)


def gmail_service():
    creds = load_credentials()
    if not creds or not credentials_have_scopes(creds, [GMAIL_SCOPE]):
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def chat_service(require_send=False):
    """Build Google Chat with only the permission needed for the requested action."""
    creds = load_credentials()
    required = CHAT_SCOPES if require_send else CHAT_READ_SCOPES
    if not creds or not credentials_have_scopes(creds, required):
        return None
    return build("chat", "v1", credentials=creds, cache_discovery=False)


def header_value(headers, name):
    lname = name.lower()
    for h in headers or []:
        if h.get("name", "").lower() == lname:
            return h.get("value", "")
    return ""


def received_datetime(date_header):
    try:
        dt = parsedate_to_datetime(date_header)
        return dt.astimezone() if dt.tzinfo else dt
    except Exception:
        return datetime.now().astimezone()


def decode_gmail_data(data):
    if not data:
        return ""
    try:
        pad = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_text_part(payload):
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        data = body.get("data", "")
        if data:
            text = decode_gmail_data(data)
            if mime == "text/plain":
                plain.append(text)
            elif mime == "text/html":
                html.append(text)
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    if plain:
        return "\n".join(plain)
    if html:
        text = "\n".join(html)
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text)
    return ""


def sender_domain(email):
    email = (email or "").strip().lower()
    return email.rsplit("@", 1)[1] if "@" in email else ""


def normalize_domain(domain):
    domain = (domain or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.strip(" .")


def watch_domains(enabled_only=True):
    with connect_db() as con:
        if enabled_only:
            rows = con.execute("SELECT * FROM watch_domains WHERE enabled=1 ORDER BY domain").fetchall()
        else:
            rows = con.execute("SELECT * FROM watch_domains ORDER BY domain").fetchall()
        return [dict(r) for r in rows]


def ignored_source(source_type, sender_email="", domain=""):
    source_type = (source_type or "").strip().lower()
    sender_email = (sender_email or "").strip().lower()
    domain = normalize_domain(domain or sender_domain(sender_email))
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT rule_type,value FROM ignore_sources
            WHERE source_type=? AND enabled=1
            """,
            (source_type,)
        ).fetchall()
    for row in rows:
        value = (row["value"] or "").strip().lower()
        if row["rule_type"] == "email" and sender_email and sender_email == value:
            return True
        if row["rule_type"] == "domain" and domain:
            watched = normalize_domain(value)
            if domain == watched or domain.endswith("." + watched):
                return True
    return False


def ignored_chat_space(space):
    display_name = (space.get("displayName") or "").strip().lower()
    resource_name = (space.get("name") or "").strip().lower()
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT value FROM ignore_sources
            WHERE source_type='chat' AND rule_type='space' AND enabled=1
            """
        ).fetchall()
    for row in rows:
        value = (row["value"] or "").strip().lower()
        if value and (display_name == value or resource_name == value):
            return True
    return False


def not_task_examples(source_type="gmail", limit=20):
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT source_type,sender_name,sender_email,sender_domain,subject,excerpt,reason
            FROM not_task_training
            WHERE active=1 AND source_type=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (source_type, int(limit))
        ).fetchall()
    return [dict(r) for r in rows]


def not_task_training_prompt(source_type="gmail", limit=20):
    examples = not_task_examples(source_type, limit)
    if not examples:
        return "No user-trained NOT A TASK examples are available yet."
    lines = [
        "USER TRAINING — messages below were explicitly marked NOT A TASK.",
        "Use them as negative examples. Similar automated/informational messages should normally be ignored.",
        "Do not ignore a genuinely new, explicit request merely because it shares a broad company domain.",
    ]
    for item in examples:
        sender = item.get("sender_email") or item.get("sender_name") or "unknown"
        subject = (item.get("subject") or "").replace("\n", " ")[:160]
        excerpt = (item.get("excerpt") or "").replace("\n", " ")[:220]
        lines.append(
            f"- Sender: {sender}; Domain: {item.get('sender_domain','')}; "
            f"Subject: {subject}; Example: {excerpt}"
        )
    return "\n".join(lines)



def normalized_subject_pattern(value):
    value = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", value or "", flags=re.I).lower()
    value = re.sub(r"\b\d+\b", "#", value)
    value = re.sub(r"[^a-z0-9#]+", " ", value)
    return " ".join(value.split())[:160]


def gpt_help_suppressed(sender_email="", subject=""):
    domain = normalize_domain(sender_domain(sender_email or ""))
    pattern = normalized_subject_pattern(subject)
    if not pattern:
        return False
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT sender_domain,subject_pattern
            FROM gpt_help_suppressions
            WHERE enabled=1
            """
        ).fetchall()
    for row in rows:
        row_domain = normalize_domain(row["sender_domain"] or "")
        if row["subject_pattern"] != pattern:
            continue
        if not row_domain or not domain or row_domain == domain:
            return True
    return False


def save_gpt_help_suppression(sender_email="", subject="", note="User requested no GPT help for this email type"):
    domain = normalize_domain(sender_domain(sender_email or ""))
    pattern = normalized_subject_pattern(subject)
    if not pattern:
        raise RuntimeError("This item does not have enough email subject information to train a type.")
    with connect_db() as con:
        con.execute(
            """
            INSERT INTO gpt_help_suppressions(sender_domain,subject_pattern,note,enabled)
            VALUES(?,?,?,1)
            ON CONFLICT(sender_domain,subject_pattern)
            DO UPDATE SET enabled=1,note=excluded.note
            """,
            (domain, pattern, note)
        )
    return {"sender_domain": domain, "subject_pattern": pattern}


def store_not_task_training(
    source_type,
    source_id="",
    sender_name="",
    sender_email="",
    subject="",
    excerpt="",
    reason="User marked Not a Task",
):
    sender_email = (sender_email or "").strip().lower()
    domain = normalize_domain(sender_domain(sender_email))
    with connect_db() as con:
        con.execute(
            """
            INSERT INTO not_task_training
            (source_type,source_id,sender_name,sender_email,sender_domain,subject,excerpt,reason)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                source_type, source_id, sender_name or "", sender_email, domain,
                subject or "", (excerpt or "")[:1000], reason or "User marked Not a Task"
            )
        )


def is_watched_domain(domain):
    domain = normalize_domain(domain)
    if not domain:
        return False
    for item in watch_domains(True):
        watched = normalize_domain(item["domain"])
        if domain == watched or domain.endswith("." + watched):
            return True
    return False


def gmail_list_refs(service, q, limit):
    refs, token = [], None
    while len(refs) < limit:
        resp = service.users().messages().list(
            userId="me",
            q=q,
            maxResults=min(100, limit - len(refs)),
            pageToken=token,
        ).execute()
        refs.extend(resp.get("messages", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return refs[:limit]


def parse_address_header(value):
    return [
        {"name": (name or "").strip(), "email": (email or "").strip().lower()}
        for name, email in getaddresses([value or ""]) if email
    ]


def gmail_parse_message(msg):
    message_id = msg.get("id", "")
    headers = msg.get("payload", {}).get("headers", [])
    sender_raw = header_value(headers, "Reply-To") or header_value(headers, "From")
    sender_name, sender_email = parseaddr(sender_raw)
    to_addresses = parse_address_header(header_value(headers, "To"))
    cc_addresses = parse_address_header(header_value(headers, "Cc"))
    bcc_addresses = parse_address_header(header_value(headers, "Bcc"))
    from_addresses = parse_address_header(header_value(headers, "From"))
    participants = []
    seen = set()
    for item in from_addresses + to_addresses + cc_addresses + bcc_addresses:
        email = item.get("email", "").lower()
        if email and email not in seen:
            seen.add(email)
            participants.append(item)
    labels = set(msg.get("labelIds", []) or [])
    return {
        "message_id": message_id,
        "thread_id": msg.get("threadId", ""),
        "subject": header_value(headers, "Subject"),
        "sender_name": sender_name,
        "sender_email": (sender_email or "").lower(),
        "received": received_datetime(header_value(headers, "Date")),
        "snippet": msg.get("snippet", "") or "",
        "body": extract_text_part(msg.get("payload", {})),
        "url": f"https://mail.google.com/mail/u/0/#all/{message_id}",
        "to_addresses": to_addresses,
        "cc_addresses": cc_addresses,
        "bcc_addresses": bcc_addresses,
        "participants": participants,
        "recipient_count": len(to_addresses) + len(cc_addresses) + len(bcc_addresses),
        "is_sent": "SENT" in labels,
    }


def gmail_get_full(service, message_id):
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return gmail_parse_message(msg)


def gmail_get_thread_emails(service, thread_id, limit=30):
    if not thread_id:
        return []
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    emails = [gmail_parse_message(m) for m in thread.get("messages", [])]
    emails.sort(key=lambda e: e["received"])
    return emails[-limit:]


def gmail_thread_context(service, thread_id, limit=12, char_budget=14000):
    """Compact conversation context used to avoid missing requests in multi-recipient chains."""
    if not thread_id:
        return ""
    try:
        emails = gmail_get_thread_emails(service, thread_id, limit=limit)
    except Exception:
        return ""
    blocks = []
    for e in emails:
        direction = "SENT" if e.get("is_sent") else "RECEIVED"
        content = (e.get("body") or e.get("snippet") or "")[:2200]
        blocks.append(
            f"{direction} {e['received'].isoformat()}\n"
            f"FROM: {e.get('sender_name','')} <{e.get('sender_email','')}>\n"
            f"TO: {', '.join(x.get('email','') for x in e.get('to_addresses',[]))}\n"
            f"CC: {', '.join(x.get('email','') for x in e.get('cc_addresses',[]))}\n"
            f"SUBJECT: {e.get('subject','')}\n{content}"
        )
    text = "\n\n--- THREAD MESSAGE ---\n\n".join(blocks)
    return text[-char_budget:]


# ---------------- OpenAI structured helpers ----------------

_OPENAI_CLIENT = None
_OPENAI_CLIENT_LOCK = threading.Lock()


def openai_client():
    """Reuse one OpenAI HTTP connection pool instead of one client per AI call."""
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None:
        with _OPENAI_CLIENT_LOCK:
            if _OPENAI_CLIENT is None:
                _OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
    return _OPENAI_CLIENT


def openai_json(prompt, schema, name):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    response = openai_client().responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        store=False,
        text={
            "format": {
                "type": "json_schema",
                "name": name,
                "strict": True,
                "schema": schema,
            }
        },
    )
    return json.loads(response.output_text)


MEETING_TASK_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "assignee": {"type": "string"},
        "due_date": {"type": "string"},
        "priority": {"type": "string", "enum": ["urgent", "high", "normal"]},
        "gpt_can_help": {"type": "boolean"},
        "gpt_help_prompt": {"type": "string"},
        "gpt_help_reason": {"type": "string"}
    },
    "required": ["title", "summary", "assignee", "due_date", "priority", "gpt_can_help", "gpt_help_prompt", "gpt_help_reason"],
    "additionalProperties": False
}

EMAIL_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "actionable": {"type": "boolean"},
        "category": {"type": "string", "enum": ["client", "payment", "ignore"]},
        "task_title": {"type": "string"},
        "summary": {"type": "string"},
        "priority": {"type": "string", "enum": ["urgent", "high", "normal"]},
        "due_date": {"type": "string"},
        "payment_amount": {"type": "number"},
        "currency": {"type": "string"},
        "invoice_number": {"type": "string"},
        "invoice_sent": {"type": "boolean"},
        "suggested_reply": {"type": "string"},
        "gpt_can_help": {"type": "boolean"},
        "gpt_help_prompt": {"type": "string"},
        "gpt_help_reason": {"type": "string"},
        "is_gemini_meeting_summary": {"type": "boolean"},
        "meeting_title": {"type": "string"},
        "meeting_summary": {"type": "string"},
        "meeting_tasks": {"type": "array", "items": MEETING_TASK_ITEM_SCHEMA},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": [
        "actionable", "category", "task_title", "summary", "priority", "due_date",
        "payment_amount", "currency", "invoice_number", "invoice_sent",
        "suggested_reply", "gpt_can_help", "gpt_help_prompt", "gpt_help_reason",
        "is_gemini_meeting_summary", "meeting_title", "meeting_summary", "meeting_tasks",
        "confidence", "reason"
    ],
    "additionalProperties": False,
}


def openai_analyze_email(email, watched=False):
    body = (email["body"] or email["snippet"] or "")[:OPENAI_EMAIL_BODY_CHARS]
    watched_text = (
        "This sender domain is on the HIGH WATCH list. If there is a reasonable request, "
        "follow-up, commitment, delay, deliverable, decision, payment issue, or unresolved item, "
        "treat it as actionable. Any actionable watched-domain item must be priority high or urgent."
        if watched else
        "This sender domain is not specially watched. Be conservative and ignore informational mail with no action."
    )
    prompt = f"""
You are the action-item classifier for Smart 1 Marketing's private Gmail task dashboard.
Current local date: {datetime.now().astimezone().date().isoformat()}
Received: {email['received'].isoformat()}
Sender: {email['sender_name']} <{email['sender_email']}>
Subject: {email['subject']}
Recipients: {email.get('recipient_count',0)}
To: {', '.join(x.get('email','') for x in email.get('to_addresses',[]))}
Cc: {', '.join(x.get('email','') for x in email.get('cc_addresses',[]))}

{watched_text}

{not_task_training_prompt("gmail", 25)}

Classify only work Todd/Smart 1 needs to perform.
- client: answer, fix, deliver, create, schedule, investigate, update, approve, follow up, or otherwise handle for a customer/prospect/vendor.
- payment: money Smart 1 needs to pay, fund, reconcile, cure, or prevent from becoming delinquent/suspended. Extract amount/invoice information if present.
- ignore: newsletter, receipt with no action, normal confirmation, promotion, or information with no follow-up.

Do not classify money owed TO Smart 1 as payment unless Smart 1 itself must pay something; collections from a client are client tasks.
Use YYYY-MM-DD only when a deadline is explicit or reliably relative to the received date. Do not invent dates.
Suggested reply should be brief and useful, or empty when no reply is needed.
Multiple-recipient email is especially important: preserve the broader chain context and do not dismiss a request merely because several people are included. If two or more people are in To/Cc/Bcc, an actionable item should normally be at least High priority unless clearly routine.
Detect Google Meet / Gemini “Take notes for me” recap emails. For those set is_gemini_meeting_summary=true, extract meeting_title, meeting_summary, and EVERY concrete Suggested next step/action into meeting_tasks. Preserve any named assignee. Do not collapse several meeting action items into one. For non-meeting email return false, empty meeting strings, and an empty meeting_tasks array.
Set gpt_can_help=true when a capable GPT could materially help complete the requested work itself (for example drafting content, analyzing information, producing a plan, writing code, creating a prompt for a technical fix, summarizing, researching supplied information, or preparing structured output).
gpt_help_reason should say briefly why GPT can help.
IMPORTANT COST CONTROL: always return gpt_help_prompt as an empty string during automatic email classification. The full prompt will be generated only if Todd clicks Prepare GPT Prompt.
Do not claim something has been completed.

EMAIL:
---BEGIN---
{body}
---END---

MULTI-RECIPIENT THREAD CONTEXT (when available):
{email.get('thread_context','') or 'Not loaded for this message.'}
""".strip()
    result = openai_json(prompt, EMAIL_ANALYSIS_SCHEMA, "gmail_action_analysis")
    if gpt_help_suppressed(email.get("sender_email", ""), email.get("subject", "")):
        result["gpt_can_help"] = False
        result["gpt_help_prompt"] = ""
        result["gpt_help_reason"] = ""
    if watched and result.get("actionable") and result.get("category") != "ignore" and result.get("priority") == "normal":
        result["priority"] = "high"
    if int(email.get("recipient_count", 0) or 0) >= 2 and result.get("actionable") and result.get("category") != "ignore" and result.get("priority") == "normal":
        result["priority"] = "high"
    return result


PAYMENT_TERMS = [
    "past due", "overdue", "payment failed", "payment was unsuccessful", "card declined",
    "transaction declined", "amount due", "balance due", "invoice due", "wire funding",
    "fund payroll", "payroll funding", "payment required", "auto-pay", "autopay",
    "suspension", "will cancel", "settlement balance", "collections", "failed charge",
]
CLIENT_TERMS = [
    "can you", "could you", "please send", "please provide", "please update", "following up",
    "follow up", "waiting on", "when can", "please fix", "need this", "requested",
    "sample agreement", "campaign stats", "statistics", "scorecard", "makegood", "underdelivery",
    "credit to invoice", "setup", "set up", "launch",
]
URGENT_TERMS = [
    "urgent", "immediately", "asap", "today", "past due", "overdue", "suspend", "suspension",
    "cancel", "cancellation", "declined", "failed", "payroll", "eod",
]


def fallback_analyze(email, watched=False):
    combined = f"{email['subject']} {email['snippet']} {email['body'][:3000]}".lower()
    payments = [x for x in PAYMENT_TERMS if x in combined]
    clients = [x for x in CLIENT_TERMS if x in combined]
    if not payments and not clients:
        return {
            "actionable": False, "category": "ignore", "task_title": "", "summary": "",
            "priority": "normal", "due_date": "", "payment_amount": 0, "currency": "USD",
            "invoice_number": "", "invoice_sent": False, "suggested_reply": "",
            "gpt_can_help": False, "gpt_help_prompt": "", "gpt_help_reason": "",
            "is_gemini_meeting_summary": False, "meeting_title": "", "meeting_summary": "", "meeting_tasks": [],
            "confidence": "low", "reason": "Fallback classifier found no action terms."
        }
    category = "payment" if payments else "client"
    priority = "urgent" if any(x in combined for x in URGENT_TERMS) else ("high" if watched else "high")
    amount = 0
    if category == "payment":
        m = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", combined)
        if m:
            try:
                amount = float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    clean_subject = re.sub(r"^\s*(re|fw|fwd)\s*:\s*", "", email["subject"] or "", flags=re.I).strip()
    return {
        "actionable": True,
        "category": category,
        "task_title": clean_subject or "Email follow-up",
        "summary": email["snippet"][:500],
        "priority": priority,
        "due_date": "",
        "payment_amount": amount,
        "currency": "USD",
        "invoice_number": "",
        "invoice_sent": category == "payment" and "invoice" in combined,
        "suggested_reply": "",
        "gpt_can_help": False,
        "gpt_help_prompt": "",
        "gpt_help_reason": "",
        "is_gemini_meeting_summary": False,
        "meeting_title": "",
        "meeting_summary": "",
        "meeting_tasks": [],
        "confidence": "low",
        "reason": "Fallback keyword classifier used."
    }


def analyze_email(email, watched=False):
    if OPENAI_API_KEY:
        try:
            result = openai_analyze_email(email, watched)
            set_setting("openai_last_error", "")
            return result, "openai"
        except Exception as exc:
            set_setting("openai_last_error", str(exc))
    return fallback_analyze(email, watched), "fallback"


RELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "related": {"type": "boolean"},
        "task_id": {"type": "integer"},
        "material_update": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["related", "task_id", "material_update", "reason"],
    "additionalProperties": False,
}



def email_newer_than_task(email, task):
    try:
        created = datetime.fromisoformat(task["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        received = email["received"]
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        return received > created - timedelta(minutes=5)
    except Exception:
        return True

def candidate_tasks_for_sender(con, email):
    sender = (email["sender_email"] or "").lower()
    domain = sender_domain(sender)
    name = (email["sender_name"] or "").strip().lower()
    watched_label = ""
    for item in watch_domains(True):
        watched = normalize_domain(item["domain"])
        if domain == watched or (watched and domain.endswith("." + watched)):
            watched_label = (item.get("label") or "").strip().lower()
            break
    domain_stem = (domain.split(".", 1)[0] if domain else "").replace("-", "").replace("_", "")
    rows = con.execute("SELECT * FROM tasks WHERE completed=0 ORDER BY updated_at DESC LIMIT 150").fetchall()
    out = []
    for row in rows:
        t = dict(row)
        if not email_newer_than_task(email, t):
            continue
        task_email = (t.get("email_to") or "").lower()
        task_domain = sender_domain(task_email)
        party = (t.get("party") or "").lower()
        party_compact = re.sub(r"[^a-z0-9]", "", party)
        participant_emails = task_participant_emails(con, t["id"])
        current_participants = {
            (x.get("email") or "").lower() for x in email.get("participants", []) if x.get("email")
        }
        participant_overlap = bool(participant_emails.intersection(current_participants))
        same_sender = sender and task_email and sender == task_email
        same_domain = domain and task_domain and domain == task_domain
        name_match = name and len(name) >= 4 and name in party
        watched_label_match = watched_label and watched_label in party
        domain_stem_match = len(domain_stem) >= 5 and domain_stem in party_compact
        if same_sender or same_domain or name_match or watched_label_match or domain_stem_match or participant_overlap:
            out.append(t)
    return out[:15]


def ai_match_related_task(email, candidates, analysis):
    if not OPENAI_API_KEY or not candidates:
        return None
    candidate_text = "\n".join(
        f"Task {t['id']}: party={t['party']}; title={t['title']}; detail={t['detail']}; status={t['status']}"
        for t in candidates
    )
    prompt = f"""
Decide whether this new Gmail message materially adds information to ONE existing open task.
Only return related=true when the email is clearly about the same issue/deliverable/payment/follow-up.
A loose company match is not enough. material_update=true when the email adds a request, changed facts, urgency,
new deadline, problem, correction, approval, amount, or follow-up that should be logged on that task.
If uncertain, return related=false and task_id=0.

NEW EMAIL
Direction: {"SENT" if email.get("is_sent") else "INCOMING"}
From: {email['sender_name']} <{email['sender_email']}>
Subject: {email['subject']}
AI summary: {analysis.get('summary','')}
Snippet: {email['snippet'][:1200]}
Participants: {', '.join(x.get('email','') for x in email.get('participants', []))}
Recipient count: {email.get('recipient_count',0)}

CANDIDATE TASKS
{candidate_text}
""".strip()
    try:
        result = openai_json(prompt, RELATION_SCHEMA, "related_task_match")
        if result.get("related") and result.get("task_id") in {t["id"] for t in candidates}:
            return result
    except Exception as exc:
        set_setting("openai_last_error", str(exc))
    return None


def processed_message(con, message_id):
    return bool(con.execute("SELECT 1 FROM gmail_processed WHERE gmail_message_id=?", (message_id,)).fetchone())


def record_processed(con, message_id, thread_id, classification):
    con.execute("""
        INSERT OR IGNORE INTO gmail_processed(gmail_message_id,gmail_thread_id,classification)
        VALUES (?,?,?)
    """, (message_id, thread_id, classification))


def task_exists_for_message(con, message_id):
    return bool(con.execute("SELECT 1 FROM tasks WHERE gmail_message_id=? LIMIT 1", (message_id,)).fetchone())


def add_task_participants(con, task_id, participants, source="email"):
    for item in participants or []:
        email = (item.get("email") or "").strip().lower()
        if not email:
            continue
        try:
            con.execute(
                "INSERT OR IGNORE INTO task_participants(task_id,email,display_name,source) VALUES(?,?,?,?)",
                (task_id, email, (item.get("name") or "").strip(), source),
            )
        except sqlite3.IntegrityError:
            pass


def task_participant_emails(con, task_id):
    return {
        (r["email"] or "").lower()
        for r in con.execute("SELECT email FROM task_participants WHERE task_id=?", (task_id,)).fetchall()
        if r["email"]
    }


def attach_email_update(con, task_id, email, match_method="thread", make_urgent=True):
    try:
        con.execute("""
            INSERT INTO task_email_updates
            (task_id,gmail_message_id,gmail_thread_id,sender_name,sender_email,subject,snippet,received_at,email_url,match_method,direction,to_emails,cc_emails)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            task_id, email["message_id"], email["thread_id"], email["sender_name"], email["sender_email"],
            email["subject"], email["snippet"][:1200], email["received"].isoformat(), email["url"], match_method,
            "sent" if email.get("is_sent") else "incoming",
            ", ".join(x.get("email", "") for x in email.get("to_addresses", [])),
            ", ".join(x.get("email", "") for x in email.get("cc_addresses", [])),
        ))
    except sqlite3.IntegrityError:
        return False
    add_task_participants(con, task_id, email.get("participants", []), "sent email" if email.get("is_sent") else "incoming email")
    if make_urgent and not email.get("is_sent"):
        con.execute("""
            UPDATE tasks
            SET priority='urgent', status=CASE WHEN status='Waiting' THEN 'Open' ELSE status END,
                recipient_count=MAX(recipient_count, ?), updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (int(email.get("recipient_count", 0) or 0), task_id))
        con.execute(
            "INSERT INTO notes(task_id,body) VALUES(?,?)",
            (task_id, f"New related email received; task automatically changed to URGENT. Subject: {email['subject']}")
        )
    else:
        con.execute(
            "UPDATE tasks SET recipient_count=MAX(recipient_count, ?),updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(email.get("recipient_count", 0) or 0), task_id),
        )
    return True


def insert_task_from_suggestion(con, s):
    cur = con.execute("""
        INSERT INTO tasks
        (category,party,title,detail,due_date,priority,status,email_url,email_to,email_subject,
         gmail_message_id,gmail_thread_id,amount,currency,invoice_number,invoice_sent,invoice_sent_at,
         suggested_reply,ai_confidence,gpt_can_help,gpt_help_prompt,gpt_help_reason,recipient_count,source_kind,source_received_at)
        VALUES (?,?,?,?,?,?,'Open',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'gmail',?)
    """, (
        s["suggested_category"],
        s["sender_name"] or s["sender_email"] or "Gmail",
        s["suggested_title"] or s["subject"] or "Gmail follow-up",
        s["suggested_summary"] or s["snippet"],
        s["suggested_due_date"],
        s["suggested_priority"],
        s["email_url"],
        s["sender_email"],
        f"Re: {s['subject']}" if s["subject"] else "Re: Follow-up",
        s["gmail_message_id"],
        s["gmail_thread_id"],
        float(s["payment_amount"] or 0),
        s["currency"] or "USD",
        s["invoice_number"] or "",
        int(s["invoice_sent"] or 0),
        datetime.now().astimezone().isoformat(timespec="seconds") if s["invoice_sent"] else "",
        s["suggested_reply"] or "",
        s["confidence"] or "",
        int(s["gpt_can_help"] or 0) if "gpt_can_help" in s.keys() else 0,
        s["gpt_help_prompt"] or "" if "gpt_help_prompt" in s.keys() else "",
        s["gpt_help_reason"] or "" if "gpt_help_reason" in s.keys() else "",
        int(s["recipient_count"] or 0) if "recipient_count" in s.keys() else 0,
        s["received_at"] or "",
    ))
    task_id = cur.lastrowid
    try:
        participants = json.loads(s["participants_json"] or "[]") if "participants_json" in s.keys() else []
    except Exception:
        participants = []
    add_task_participants(con, task_id, participants, "source email")
    if s["sender_email"]:
        add_task_participants(con, task_id, [{"email": s["sender_email"], "name": s["sender_name"] or ""}], "source email")
    con.execute("UPDATE gmail_suggestions SET state='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (s["id"],))
    return task_id


def list_matching_gmail_refs(service):
    # Base scan plus explicit high-watch domain scans. Dedupe by Gmail message ID.
    merged = {}
    for ref in gmail_list_refs(service, GMAIL_SYNC_QUERY, GMAIL_SCAN_MAX_MESSAGES):
        merged[ref["id"]] = ref

    for item in watch_domains(True):
        domain = normalize_domain(item["domain"])
        if not domain:
            continue
        q = (
            f"newer_than:{WATCH_DOMAIN_LOOKBACK_DAYS}d from:{domain} "
            "-in:sent -in:drafts -in:spam -in:trash"
        )
        for ref in gmail_list_refs(service, q, min(200, GMAIL_SCAN_MAX_MESSAGES)):
            merged[ref["id"]] = ref
    return list(merged.values())[:GMAIL_SCAN_MAX_MESSAGES]


def sync_gmail():
    service = gmail_service()
    if not service:
        return {"connected": False, "checked": 0, "analyzed": 0, "added": 0, "auto_added": 0, "updates": 0}

    refs = list_matching_gmail_refs(service)
    analyzed = added = auto_added = updates = 0

    for ref in refs:
        if analyzed >= GMAIL_ANALYZE_MAX_NEW:
            break
        message_id = ref["id"]
        with connect_db() as con:
            if processed_message(con, message_id) or task_exists_for_message(con, message_id):
                continue

        email = gmail_get_full(service, message_id)
        if int(email.get("recipient_count", 0) or 0) >= 2 and email.get("thread_id"):
            email["thread_context"] = gmail_thread_context(service, email["thread_id"], limit=14, char_budget=16000)
        domain = sender_domain(email["sender_email"])

        # Hard ignore rules run before task matching or OpenAI analysis.
        # This prevents explicitly trained automated sources such as xwf.google.com
        # from creating tasks or making an existing task urgent.
        if ignored_source("gmail", email.get("sender_email", ""), domain):
            with connect_db() as con:
                record_processed(con, message_id, email["thread_id"], "trained_not_task_source")
            continue

        watched = is_watched_domain(domain)

        # Exact Gmail thread match: any new incoming message on an existing task makes it urgent.
        exact_task_id = 0
        exact_attached = False
        with connect_db() as con:
            thread_task = None
            if email["thread_id"]:
                thread_task = con.execute(
                    "SELECT * FROM tasks WHERE completed=0 AND gmail_thread_id=? ORDER BY updated_at DESC LIMIT 1",
                    (email["thread_id"],)
                ).fetchone()
            if thread_task and thread_task["gmail_message_id"] != message_id:
                if email_newer_than_task(email, thread_task):
                    exact_task_id = thread_task["id"]
                    exact_attached = attach_email_update(con, exact_task_id, email, "same Gmail thread")
                    if exact_attached:
                        updates += 1
                    record_processed(con, message_id, email["thread_id"], "task_update")
                else:
                    record_processed(con, message_id, email["thread_id"], "historical_thread_message")
        if thread_task and thread_task["gmail_message_id"] != message_id:
            if exact_attached:
                mark_sent_monitors_responded(email["thread_id"], email)
                maybe_create_resolution_review(exact_task_id, "gmail_incoming", message_id, email["url"])
            continue

        analysis, analyzer = analyze_email(email, watched)
        analyzed += 1
        classification = analysis.get("category", "ignore")

        if analysis.get("is_gemini_meeting_summary"):
            with connect_db() as con:
                record_processed(con, message_id, email["thread_id"], "gemini_meeting_summary")
            store_meeting_review(email, analysis, analyzer)
            continue

        related_task_id = 0
        related_attached = False
        suggestion_id = 0
        with connect_db() as con:
            # Cross-thread related-message match for same sender/domain/participant tasks.
            if analysis.get("actionable") and classification != "ignore":
                candidates = candidate_tasks_for_sender(con, email)
                relation = ai_match_related_task(email, candidates, analysis)
                if relation and relation.get("material_update"):
                    related_task_id = relation["task_id"]
                    related_attached = attach_email_update(con, related_task_id, email, "AI related-task match")
                    if related_attached:
                        updates += 1
                    record_processed(con, message_id, email["thread_id"], "task_update")

            if not related_task_id:
                record_processed(con, message_id, email["thread_id"], classification)
                if analysis.get("actionable") and classification != "ignore":
                    try:
                        cur = con.execute("""
                            INSERT INTO gmail_suggestions
                            (gmail_message_id,gmail_thread_id,sender_name,sender_email,subject,snippet,received_at,
                             suggested_title,suggested_category,suggested_priority,suggested_due_date,suggested_summary,
                             suggested_reply,payment_amount,currency,invoice_number,invoice_sent,confidence,reason,
                             analyzer,email_url,gpt_can_help,gpt_help_prompt,gpt_help_reason,recipient_count,participants_json,state)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')
                        """, (
                            message_id, email["thread_id"], email["sender_name"], email["sender_email"], email["subject"],
                            email["snippet"], email["received"].isoformat(), analysis.get("task_title", ""), classification,
                            analysis.get("priority", "normal"), analysis.get("due_date", ""), analysis.get("summary", ""),
                            analysis.get("suggested_reply", ""), float(analysis.get("payment_amount", 0) or 0),
                            analysis.get("currency", "USD") or "USD", analysis.get("invoice_number", ""),
                            1 if analysis.get("invoice_sent") else 0, analysis.get("confidence", ""),
                            ("HIGH WATCH DOMAIN. " if watched else "") + analysis.get("reason", ""), analyzer, email["url"],
                            1 if analysis.get("gpt_can_help") else 0, analysis.get("gpt_help_prompt", ""),
                            analysis.get("gpt_help_reason", ""), int(email.get("recipient_count", 0) or 0),
                            json.dumps(email.get("participants", []))
                        ))
                        suggestion_id = cur.lastrowid
                        added += 1
                        should_auto_add = get_setting("gmail_auto_add", "0") == "1"
                        invoice_auto_add = (
                            get_setting("auto_add_invoices", "1") == "1"
                            and classification == "payment"
                            and bool(analysis.get("invoice_sent"))
                            and analysis.get("confidence", "") in {"high", "medium"}
                        )
                        if should_auto_add or invoice_auto_add:
                            srow = con.execute("SELECT * FROM gmail_suggestions WHERE id=?", (suggestion_id,)).fetchone()
                            task_id = insert_task_from_suggestion(con, srow)
                            if invoice_auto_add:
                                con.execute(
                                    "INSERT INTO notes(task_id,body) VALUES(?,?)",
                                    (task_id, "Invoice automatically added to Finances / Bills to Pay from Gmail.")
                                )
                            auto_added += 1
                    except sqlite3.IntegrityError:
                        pass

        if related_task_id:
            if related_attached:
                mark_sent_monitors_responded(email["thread_id"], email)
                maybe_create_resolution_review(related_task_id, "gmail_incoming", message_id, email["url"])
            continue

    set_setting("gmail_last_sync", datetime.now().astimezone().isoformat())
    set_setting("gmail_last_error", "")
    return {
        "connected": True,
        "checked": len(refs),
        "analyzed": analyzed,
        "added": added,
        "auto_added": auto_added,
        "updates": updates,
        "backlog_possible": analyzed >= GMAIL_ANALYZE_MAX_NEW,
    }


# ---------------- Email research inside a task ----------------

SEARCH_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "gmail_query": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["gmail_query", "reason"],
    "additionalProperties": False,
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "message_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "sender": {"type": "string"},
                    "date": {"type": "string"},
                },
                "required": ["fact", "message_id", "subject", "sender", "date"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "confidence", "facts"],
    "additionalProperties": False,
}


def task_search_query(task, question):
    # Ask Email searches the user's entire Gmail history. The query is focused by company/person/task,
    # but deliberately does not add a date limit.
    if OPENAI_API_KEY:
        prompt = f"""
Create a Gmail search query to answer a business question using email related to this existing task.
Use Gmail search operators only. Keep it broad enough to find relevant emails, but not the entire mailbox.
Search the entire mailbox history: DO NOT add newer_than:, older_than:, after:, or before:.
Always include -in:spam -in:trash. Prefer exact email/domain operators when available. Do not include unsupported syntax.

Task party: {task['party']}
Task title: {task['title']}
Task detail: {task['detail']}
Known correspondent email: {task['email_to']}
Question: {question}
""".strip()
        try:
            result = openai_json(prompt, SEARCH_PLAN_SCHEMA, "gmail_task_search_plan")
            q = (result.get("gmail_query") or "").strip()
            if q:
                return q
        except Exception as exc:
            set_setting("openai_last_error", str(exc))

    email_to = (task["email_to"] or "").strip()
    if email_to:
        return f"{{from:{email_to} to:{email_to}}} -in:spam -in:trash"
    party = re.sub(r"[^A-Za-z0-9 .&'-]", " ", task["party"] or "").strip()
    token = party.split("/")[0].strip()
    return f'"{token}" -in:spam -in:trash'


def research_task_email(task, question):
    service = gmail_service()
    if not service:
        raise RuntimeError("Gmail is not connected.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for Ask Email research.")

    q = task_search_query(task, question)
    refs = gmail_list_refs(service, q, EMAIL_RESEARCH_MAX_MESSAGES)
    emails = [gmail_get_full(service, r["id"]) for r in refs]
    if not emails:
        result = {"answer": "No matching emails were found for that question.", "confidence": "low", "facts": []}
    else:
        email_text = []
        context_chars = 0
        for e in emails:
            content = (e["body"] or e["snippet"] or "")[:5000]
            block = (
                f"MESSAGE_ID: {e['message_id']}\nDATE: {e['received'].isoformat()}\nFROM: {e['sender_name']} <{e['sender_email']}>\n"
                f"SUBJECT: {e['subject']}\nCONTENT:\n{content}"
            )
            if email_text and context_chars + len(block) > AI_CONTEXT_CHAR_BUDGET:
                break
            email_text.append(block)
            context_chars += len(block)
        prompt = f"""
Answer the user's question using ONLY the supplied Gmail messages. If the answer is not established by these messages,
say what is known and what is missing. Do not invent facts. Cite the supporting messages in the facts array by exact MESSAGE_ID.
Be concise but useful for a business task log.

TASK
Party: {task['party']}
Title: {task['title']}
Detail: {task['detail']}

QUESTION
{question}

EMAILS
{'\n\n---\n\n'.join(email_text)}
""".strip()
        result = openai_json(prompt, RESEARCH_SCHEMA, "task_email_research")

    source_map = {e["message_id"]: e for e in emails}
    sources = []
    for fact in result.get("facts", []):
        src = source_map.get(fact.get("message_id"))
        if src:
            sources.append({
                "message_id": src["message_id"],
                "url": src["url"],
                "subject": src["subject"],
                "sender": f"{src['sender_name']} <{src['sender_email']}>".strip(),
                "date": src["received"].isoformat(),
                "fact": fact.get("fact", ""),
            })

    with connect_db() as con:
        con.execute("""
            INSERT INTO task_research_logs(task_id,question,answer,confidence,sources_json)
            VALUES (?,?,?,?,?)
        """, (task["id"], question, result.get("answer", ""), result.get("confidence", ""), json.dumps(sources)))
        con.execute("UPDATE tasks SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (task["id"],))
    return {"query": q, "answer": result.get("answer", ""), "confidence": result.get("confidence", ""), "sources": sources}


# ---------------- Global natural-language Gmail task discovery ----------------

DISCOVERY_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "gmail_query": {"type": "string"},
        "company": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["gmail_query", "company", "reason"],
    "additionalProperties": False,
}

DISCOVERY_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "party": {"type": "string"},
                    "category": {"type": "string", "enum": ["client", "payment"]},
                    "priority": {"type": "string", "enum": ["urgent", "high", "normal"]},
                    "due_date": {"type": "string"},
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "invoice_sent": {"type": "boolean"},
                    "suggested_reply": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "gpt_can_help": {"type": "boolean"},
                    "gpt_help_prompt": {"type": "string"},
                    "gpt_help_reason": {"type": "string"},
                    "primary_message_id": {"type": "string"},
                    "evidence_message_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "title", "summary", "party", "category", "priority", "due_date", "amount", "currency",
                    "invoice_number", "invoice_sent", "suggested_reply", "confidence",
                    "gpt_can_help", "gpt_help_prompt", "gpt_help_reason", "primary_message_id",
                    "evidence_message_ids"
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["tasks"],
    "additionalProperties": False,
}


def discover_tasks_from_gmail(user_query):
    service = gmail_service()
    if not service:
        raise RuntimeError("Gmail is not connected.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for natural-language Gmail task discovery.")

    plan_prompt = f"""
Turn this user's natural-language request into a Gmail search query.
They want to find possible unfinished business tasks in their own email, often for a company.
Use Gmail search operators only. Search the entire Gmail history unless the user explicitly gives a time range.
Always include -in:spam -in:trash. Do not add newer_than:, older_than:, after:, or before: unless the user asks for a date/time range.
Do not exclude sent mail unless the user clearly wants only incoming mail, because sent replies can reveal promises and commitments.
If a company/domain/person is named, focus on it. Keep the query broad enough to capture multiple threads.

USER REQUEST: {user_query}
""".strip()
    plan = openai_json(plan_prompt, DISCOVERY_PLAN_SCHEMA, "gmail_discovery_search_plan")
    q = plan["gmail_query"].strip()
    refs = gmail_list_refs(service, q, EMAIL_DISCOVERY_MAX_MESSAGES)
    emails = [gmail_get_full(service, r["id"]) for r in refs]
    if not emails:
        return {"query": q, "company": plan.get("company", ""), "tasks": []}

    chunks = []
    context_chars = 0
    for e in emails:
        content = (e["body"] or e["snippet"] or "")[:4500]
        block = (
            f"MESSAGE_ID: {e['message_id']}\nTHREAD_ID: {e['thread_id']}\nDATE: {e['received'].isoformat()}\n"
            f"FROM: {e['sender_name']} <{e['sender_email']}>\nSUBJECT: {e['subject']}\nCONTENT:\n{content}"
        )
        if chunks and context_chars + len(block) > AI_CONTEXT_CHAR_BUDGET:
            break
        chunks.append(block)
        context_chars += len(block)

    task_prompt = f"""
From these Gmail messages, identify possible OPEN or UNRESOLVED business tasks that Todd/Smart 1 may need to act on.
The user will review your candidates and choose which ones to add. Deduplicate repeated emails about the same issue into one task.
Do not include tasks that the email evidence clearly shows were completed/resolved. Do not invent deadlines, amounts, or invoice numbers.
Use category payment only when Smart 1 needs to pay/fund/reconcile money. Money owed TO Smart 1 should be client.
For each candidate, choose the strongest supporting email as primary_message_id and list all supporting evidence IDs.
Party should be the company/client/vendor name you can infer from the evidence.
If GPT can materially help complete the task itself, set gpt_can_help=true and create a ready-to-use prompt.

USER REQUEST
{user_query}

EMAILS
{'\n\n---\n\n'.join(chunks)}
""".strip()
    result = openai_json(task_prompt, DISCOVERY_RESULT_SCHEMA, "gmail_discovered_tasks")

    by_id = {e["message_id"]: e for e in emails}
    tasks = []
    for item in result.get("tasks", []):
        primary = by_id.get(item.get("primary_message_id"))
        if not primary:
            continue
        evidence = [mid for mid in item.get("evidence_message_ids", []) if mid in by_id]
        tasks.append({
            **item,
            "primary_message_id": primary["message_id"],
            "gmail_thread_id": primary["thread_id"],
            "email_url": primary["url"],
            "email_to": primary["sender_email"],
            "email_subject": f"Re: {primary['subject']}" if primary["subject"] else "Re: Follow-up",
            "participants": primary.get("participants", []),
            "recipient_count": primary.get("recipient_count", 0),
            "evidence": [
                {
                    "message_id": mid,
                    "url": by_id[mid]["url"],
                    "subject": by_id[mid]["subject"],
                    "date": by_id[mid]["received"].isoformat(),
                    "sender": by_id[mid]["sender_email"],
                }
                for mid in evidence
            ],
        })
    return {"query": q, "company": plan.get("company", ""), "tasks": tasks}


def add_discovered_task(candidate):
    with connect_db() as con:
        cur = con.execute("""
            INSERT INTO tasks
            (category,party,title,detail,due_date,priority,status,email_url,email_to,email_subject,
             gmail_message_id,gmail_thread_id,amount,currency,invoice_number,invoice_sent,invoice_sent_at,
             suggested_reply,ai_confidence,gpt_can_help,gpt_help_prompt,gpt_help_reason,recipient_count,source_kind)
            VALUES (?,?,?,?,?,?,'Open',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'gmail')
        """, (
            candidate.get("category", "client"),
            candidate.get("party", "Gmail") or "Gmail",
            candidate.get("title", "Gmail task"),
            candidate.get("summary", ""),
            candidate.get("due_date", ""),
            candidate.get("priority", "normal"),
            candidate.get("email_url", ""),
            candidate.get("email_to", ""),
            candidate.get("email_subject", ""),
            candidate.get("primary_message_id", ""),
            candidate.get("gmail_thread_id", ""),
            float(candidate.get("amount", 0) or 0),
            candidate.get("currency", "USD") or "USD",
            candidate.get("invoice_number", ""),
            1 if candidate.get("invoice_sent") else 0,
            datetime.now().astimezone().isoformat(timespec="seconds") if candidate.get("invoice_sent") else "",
            candidate.get("suggested_reply", ""),
            candidate.get("confidence", ""),
            1 if candidate.get("gpt_can_help") else 0,
            candidate.get("gpt_help_prompt", ""),
            candidate.get("gpt_help_reason", ""),
            int(candidate.get("recipient_count", 0) or 0),
        ))
        task_id = cur.lastrowid
        add_task_participants(con, task_id, candidate.get("participants", []) or [], "Gmail discovery")
        evidence = candidate.get("evidence", []) or []
        if evidence:
            con.execute(
                "INSERT INTO notes(task_id,body) VALUES(?,?)",
                (task_id, f"Added from Gmail discovery using {len(evidence)} supporting message(s).")
            )
        return task_id



# ---------------- Resolution review / Sent monitoring / Google Chat ----------------

def store_meeting_review(email, analysis, analyzer):
    tasks = analysis.get("meeting_tasks") or []
    if not tasks:
        return False
    with connect_db() as con:
        try:
            con.execute("""
                INSERT INTO meeting_reviews(source_message_id,gmail_thread_id,meeting_title,summary,tasks_json,email_url,received_at,analyzer,state)
                VALUES (?,?,?,?,?,?,?,?,'new')
            """, (email["message_id"], email["thread_id"], analysis.get("meeting_title", "") or email.get("subject", "Meeting summary"), analysis.get("meeting_summary", ""), json.dumps(tasks), email.get("url", ""), email["received"].isoformat(), analyzer))
            return True
        except sqlite3.IntegrityError:
            return False


def looks_like_meeting_recap(email):
    text = f"{email.get('subject','')} {email.get('snippet','')} {(email.get('body') or '')[:5000]}".lower()
    signals = [
        "take notes for me", "take notes with gemini", "notes by gemini", "gemini meeting",
        "meeting recap", "meeting notes", "suggested next steps", "summary so far"
    ]
    return sum(1 for x in signals if x in text) >= 1 and (
        "meeting" in text or "meet" in text or "gemini" in text or "suggested next steps" in text
    )


def sync_meeting_recaps():
    """Re-scan likely Gemini/Meet recap mail even if older code already marked the email processed."""
    service = gmail_service()
    if not service:
        return {"connected": False, "checked": 0, "candidates": 0, "added": 0}
    refs = gmail_list_refs(service, GMAIL_SYNC_QUERY, GMAIL_SCAN_MAX_MESSAGES)
    checked = candidates = added = 0
    for ref in refs:
        message_id = ref.get("id", "")
        if not message_id:
            continue
        with connect_db() as con:
            if con.execute("SELECT 1 FROM meeting_reviews WHERE source_message_id=?", (message_id,)).fetchone():
                continue
        try:
            email = gmail_get_full(service, message_id)
        except Exception:
            continue
        checked += 1
        if not looks_like_meeting_recap(email):
            continue
        candidates += 1
        try:
            analysis, analyzer = analyze_email(email, is_watched_domain(sender_domain(email.get("sender_email", ""))))
        except Exception as exc:
            set_setting("meeting_last_error", str(exc))
            continue
        if analysis.get("is_gemini_meeting_summary") and (analysis.get("meeting_tasks") or []):
            if store_meeting_review(email, analysis, analyzer):
                added += 1
            with connect_db() as con:
                con.execute(
                    "UPDATE gmail_processed SET classification='gemini_meeting_summary' WHERE gmail_message_id=?",
                    (message_id,)
                )
    set_setting("meeting_last_sync", datetime.now().astimezone().isoformat())
    set_setting("meeting_last_error", "")
    return {"connected": True, "checked": checked, "candidates": candidates, "added": added}


RESOLUTION_SCHEMA = {
    "type": "object",
    "properties": {
        "likely_resolved": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
        "waiting_on_reply": {"type": "boolean"},
        "followup_needed": {"type": "boolean"},
        "followup_date": {"type": "string"},
    },
    "required": [
        "likely_resolved", "confidence", "summary", "reason",
        "waiting_on_reply", "followup_needed", "followup_date"
    ],
    "additionalProperties": False,
}

SENT_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_followup": {"type": "boolean"},
        "party": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "priority": {"type": "string", "enum": ["urgent", "high", "normal"]},
        "followup_date": {"type": "string"},
        "reason": {"type": "string"},
        "gpt_can_help": {"type": "boolean"},
        "gpt_help_prompt": {"type": "string"},
    },
    "required": [
        "needs_followup", "party", "title", "summary", "priority",
        "followup_date", "reason", "gpt_can_help", "gpt_help_prompt"
    ],
    "additionalProperties": False,
}

CHAT_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "actionable": {"type": "boolean"},
        "category": {"type": "string", "enum": ["client", "payment", "ignore"]},
        "task_title": {"type": "string"},
        "summary": {"type": "string"},
        "priority": {"type": "string", "enum": ["urgent", "high", "normal"]},
        "due_date": {"type": "string"},
        "payment_amount": {"type": "number"},
        "currency": {"type": "string"},
        "invoice_number": {"type": "string"},
        "suggested_reply": {"type": "string"},
        "gpt_can_help": {"type": "boolean"},
        "gpt_help_prompt": {"type": "string"},
        "gpt_help_reason": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": [
        "actionable", "category", "task_title", "summary", "priority", "due_date",
        "payment_amount", "currency", "invoice_number", "suggested_reply",
        "gpt_can_help", "gpt_help_prompt", "gpt_help_reason", "confidence", "reason"
    ],
    "additionalProperties": False,
}

CHAT_RELATION_SCHEMA = {
    "type": "object",
    "properties": {
        "related": {"type": "boolean"},
        "task_id": {"type": "integer"},
        "material_update": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["related", "task_id", "material_update", "reason"],
    "additionalProperties": False,
}

GPT_HELP_SCHEMA = {
    "type": "object",
    "properties": {
        "can_help": {"type": "boolean"},
        "reason": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "required": ["can_help", "reason", "prompt"],
    "additionalProperties": False,
}


def iso_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def task_resolution_context(task, max_updates=15):
    blocks = [
        f"TASK PARTY: {task['party']}",
        f"TASK TITLE: {task['title']}",
        f"TASK DETAIL: {task['detail']}",
        f"TASK STATUS: {task['status']}",
    ]
    with connect_db() as con:
        participants = con.execute(
            "SELECT email,display_name,source FROM task_participants WHERE task_id=? ORDER BY id",
            (task["id"],)
        ).fetchall()
        if participants:
            blocks.append("PARTICIPANTS: " + ", ".join(
                f"{r['display_name']} <{r['email']}>" if r['display_name'] else r['email']
                for r in participants
            ))
        email_updates = con.execute(
            "SELECT direction,sender_name,sender_email,subject,snippet,received_at,email_url "
            "FROM task_email_updates WHERE task_id=? ORDER BY received_at DESC LIMIT ?",
            (task["id"], max_updates)
        ).fetchall()
        chat_updates = con.execute(
            "SELECT direction,sender_display_name,message_text,create_time,space_display_name "
            "FROM task_chat_updates WHERE task_id=? ORDER BY create_time DESC LIMIT ?",
            (task["id"], max_updates)
        ).fetchall()
    for r in reversed(email_updates):
        blocks.append(
            f"EMAIL {r['direction'].upper()} {r['received_at']} FROM {r['sender_name']} <{r['sender_email']}> "
            f"SUBJECT {r['subject']}\n{r['snippet']}"
        )
    for r in reversed(chat_updates):
        blocks.append(
            f"GOOGLE CHAT {str(r['direction'] or 'incoming').upper()} {r['create_time']} "
            f"IN {r['space_display_name']} FROM {r['sender_display_name']}\n{r['message_text']}"
        )

    # For Gmail-backed tasks, the whole Gmail thread is the strongest context, especially for multi-recipient chains.
    if task["gmail_thread_id"]:
        service = gmail_service()
        if service:
            try:
                emails = gmail_get_thread_emails(service, task["gmail_thread_id"], limit=25)
                for e in emails:
                    direction = "SENT" if e.get("is_sent") else "RECEIVED"
                    blocks.append(
                        f"THREAD {direction} {e['received'].isoformat()} FROM {e['sender_name']} <{e['sender_email']}> "
                        f"TO {', '.join(x.get('email','') for x in e.get('to_addresses',[]))} "
                        f"CC {', '.join(x.get('email','') for x in e.get('cc_addresses',[]))}\n"
                        f"SUBJECT {e['subject']}\n{(e['body'] or e['snippet'])[:3500]}"
                    )
            except Exception:
                pass
    text = "\n\n---\n\n".join(blocks)
    return text[-AI_CONTEXT_CHAR_BUDGET:]


def maybe_create_resolution_review(task_id, source_type, source_id, source_url=""):
    if not OPENAI_API_KEY or not source_id:
        return None
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=? AND completed=0", (task_id,)).fetchone()
        if not task:
            return None
        exists = con.execute(
            "SELECT 1 FROM task_resolution_reviews WHERE task_id=? AND source_type=? AND source_id=? LIMIT 1",
            (task_id, source_type, source_id)
        ).fetchone()
        if exists:
            return None

    context = task_resolution_context(task)
    prompt = f"""
Review this open business task and its communication chain. Decide whether the latest communication makes it LOOK resolved.
Do not automatically close anything. The user must confirm.

A likely resolution can be:
- Todd/Smart 1 sent the promised deliverable or stated the fix was completed;
- the client/vendor approved, accepted, confirmed, or explicitly said the issue is resolved;
- a payment was confirmed and the obligation appears cured;
- the chain clearly reaches the requested outcome.

A message that merely says "working on it", "will send", "checking", or asks another question is NOT resolved.
For multi-recipient chains, use the entire chain and account for all participants, not only the latest sender.
If Todd sent something that appears to satisfy the request, summarize exactly what he appears to have delivered and ask-worthy evidence.
If unresolved but Todd is waiting on the other party, set waiting_on_reply=true.
followup_date must be YYYY-MM-DD only when explicitly stated or clearly useful from the evidence; otherwise empty.

LATEST SOURCE TYPE: {source_type}
LATEST SOURCE ID: {source_id}

TASK / COMMUNICATION CONTEXT
{context}
""".strip()
    try:
        result = openai_json(prompt, RESOLUTION_SCHEMA, "task_resolution_assessment")
    except Exception as exc:
        set_setting("openai_last_error", str(exc))
        return None

    with connect_db() as con:
        if result.get("waiting_on_reply"):
            con.execute(
                "UPDATE tasks SET status='Waiting',updated_at=CURRENT_TIMESTAMP WHERE id=? AND completed=0",
                (task_id,)
            )
        if result.get("likely_resolved") and result.get("confidence") in {"high", "medium"}:
            con.execute(
                """
                INSERT INTO task_resolution_reviews
                (task_id,source_type,source_id,summary,confidence,sources_json,state,source_url)
                VALUES (?,?,?,?,?,?,'pending',?)
                """,
                (task_id, source_type, source_id, result.get("summary", ""), result.get("confidence", ""),
                 json.dumps([{"type": source_type, "id": source_id, "url": source_url}]), source_url)
            )
            con.execute(
                "INSERT INTO notes(task_id,body) VALUES(?,?)",
                (task_id, "Possible resolution detected. Waiting for your confirmation before completing the task.")
            )
            return result
    return result


def mark_sent_monitors_responded(thread_id, response_email=None):
    if not thread_id:
        return 0
    stamp = response_email["received"].isoformat() if response_email else datetime.now().astimezone().isoformat()
    with connect_db() as con:
        cur = con.execute(
            """
            UPDATE sent_monitors
            SET state='responded',last_response_at=?
            WHERE gmail_thread_id=? AND state='monitoring'
            """,
            (stamp, thread_id)
        )
        return cur.rowcount


def analyze_sent_email(email):
    recipients = email.get("to_addresses", []) + email.get("cc_addresses", []) + email.get("bcc_addresses", [])
    recipient_text = ", ".join(
        f"{x.get('name','')} <{x.get('email','')}>".strip() for x in recipients
    )
    default_followup = (email["received"].date() + timedelta(days=SENT_FOLLOWUP_AFTER_DAYS)).isoformat()
    body = (email.get("body") or email.get("snippet") or "")[:OPENAI_EMAIL_BODY_CHARS]
    if not OPENAI_API_KEY:
        combined = f"{email.get('subject','')} {body}".lower()
        needs = any(x in combined for x in ["please let me know", "let me know", "can you", "could you", "i'll send", "i will send", "following up", "follow up"])
        return {
            "needs_followup": needs,
            "party": recipients[0].get("name") or recipients[0].get("email") if recipients else "Sent email",
            "title": email.get("subject") or "Sent follow-up",
            "summary": email.get("snippet", "")[:500],
            "priority": "high" if needs else "normal",
            "followup_date": default_followup if needs else "",
            "reason": "Fallback sent-mail monitor.",
            "gpt_can_help": False,
            "gpt_help_prompt": "",
        }
    prompt = f"""
Analyze this SENT Gmail message from Todd/Smart 1. The purpose is to identify things Todd should follow up on.
Create a follow-up monitor when Todd:
- asks the recipient for information/approval/confirmation;
- promises to send, fix, prepare, check, or complete something later;
- sends a proposal/deliverable that reasonably needs a response;
- says he will circle back or otherwise leaves an open loop.
Do NOT create a follow-up for simple acknowledgments, receipts, routine forwards, or messages that clearly close the issue.
If there is no explicit follow-up date but a response is reasonably expected, use {default_followup}.
If the email itself contains a task GPT could materially help Todd complete, set gpt_can_help=true and provide a ready-to-use prompt.

Sent: {email['received'].isoformat()}
Recipients: {recipient_text}
Recipient count: {email.get('recipient_count',0)}
Subject: {email.get('subject','')}

MESSAGE
{body}

MULTI-RECIPIENT THREAD CONTEXT (when available)
{email.get('thread_context','') or 'Not loaded for this message.'}
""".strip()
    return openai_json(prompt, SENT_ANALYSIS_SCHEMA, "sent_followup_analysis")


def sync_sent_mail():
    service = gmail_service()
    if not service:
        return {"connected": False, "checked": 0, "new_monitors": 0, "linked": 0, "resolution_reviews": 0}
    q = f"in:sent newer_than:{SENT_MONITOR_LOOKBACK_DAYS}d -in:drafts"
    refs = gmail_list_refs(service, q, SENT_SCAN_MAX_MESSAGES)
    new_monitors = linked = resolution_reviews = 0

    for ref in refs:
        message_id = ref["id"]
        with connect_db() as con:
            if con.execute("SELECT 1 FROM sent_monitors WHERE gmail_message_id=?", (message_id,)).fetchone():
                continue
        email = gmail_get_full(service, message_id)
        if int(email.get("recipient_count", 0) or 0) >= 2 and email.get("thread_id"):
            email["thread_context"] = gmail_thread_context(service, email["thread_id"], limit=14, char_budget=16000)
        analysis = None
        related_task = None
        match_method = ""

        with connect_db() as con:
            if email["thread_id"]:
                related_task = con.execute(
                    "SELECT * FROM tasks WHERE completed=0 AND gmail_thread_id=? ORDER BY updated_at DESC LIMIT 1",
                    (email["thread_id"],)
                ).fetchone()

        if related_task:
            with connect_db() as con:
                attach_email_update(con, related_task["id"], email, "sent email in task thread", make_urgent=False)
            linked += 1
            try:
                if maybe_create_resolution_review(related_task["id"], "gmail_sent", message_id, email["url"]):
                    resolution_reviews += 1
            except Exception:
                pass
            analysis = analyze_sent_email(email)
            match_method = "same Gmail thread"
        else:
            analysis = analyze_sent_email(email)
            # For sent mail, candidate matching relies heavily on the To/Cc participants.
            with connect_db() as con:
                candidates = candidate_tasks_for_sender(con, email)
            relation = ai_match_related_task(email, candidates, {"summary": analysis.get("summary", "")}) if candidates else None
            if relation and relation.get("related"):
                with connect_db() as con:
                    related_task = con.execute("SELECT * FROM tasks WHERE id=?", (relation["task_id"],)).fetchone()
                    if related_task:
                        attach_email_update(con, related_task["id"], email, "sent email participant/AI match", make_urgent=False)
                if related_task:
                    linked += 1
                    match_method = "participant/AI match"
                    try:
                        if maybe_create_resolution_review(related_task["id"], "gmail_sent", message_id, email["url"]):
                            resolution_reviews += 1
                    except Exception:
                        pass

        followup_due = analysis.get("followup_date", "") if analysis else ""
        needs_followup = bool(analysis and analysis.get("needs_followup"))
        if needs_followup and int(email.get("recipient_count", 0) or 0) >= 2 and analysis.get("priority") == "normal":
            analysis["priority"] = "high"
        state = "monitoring" if needs_followup else ("linked" if related_task else "ignored")
        recipients = email.get("to_addresses", []) + email.get("cc_addresses", []) + email.get("bcc_addresses", [])
        recipient_text = ", ".join(x.get("email", "") for x in recipients if x.get("email"))
        party = (analysis or {}).get("party", "") or (recipients[0].get("name") or recipients[0].get("email") if recipients else "Sent email")
        with connect_db() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO sent_monitors
                (gmail_message_id,gmail_thread_id,task_id,recipients,subject,sent_at,followup_due,state,reason,email_url,party,summary,priority,recipient_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (message_id, email["thread_id"], related_task["id"] if related_task else 0,
                 recipient_text, email["subject"], email["received"].isoformat(), followup_due, state,
                 (analysis or {}).get("reason", "") + (f" Match: {match_method}." if match_method else ""),
                 email["url"], party, (analysis or {}).get("summary", email.get("snippet", "")),
                 (analysis or {}).get("priority", "normal"), int(email.get("recipient_count", 0) or 0))
            )
            if needs_followup:
                new_monitors += 1
                if related_task:
                    con.execute(
                        "UPDATE tasks SET status='Waiting',updated_at=CURRENT_TIMESTAMP WHERE id=? AND completed=0",
                        (related_task["id"],)
                    )

    # Check open follow-ups for replies in their Gmail threads.
    with connect_db() as con:
        active = con.execute(
            "SELECT * FROM sent_monitors WHERE state='monitoring' AND gmail_thread_id<>'' ORDER BY sent_at DESC LIMIT 120"
        ).fetchall()
    for monitor in active:
        try:
            emails = gmail_get_thread_emails(service, monitor["gmail_thread_id"], limit=40)
            sent_at = iso_dt(monitor["sent_at"])
            responses = [e for e in emails if sent_at and e["received"] > sent_at and not e.get("is_sent")]
            if responses:
                mark_sent_monitors_responded(monitor["gmail_thread_id"], responses[-1])
        except Exception:
            continue

    set_setting("sent_last_sync", datetime.now().astimezone().isoformat())
    set_setting("sent_last_error", "")
    return {
        "connected": True, "checked": len(refs), "new_monitors": new_monitors,
        "linked": linked, "resolution_reviews": resolution_reviews,
    }


def granted_scope_list(creds):
    return sorted(
        set(getattr(creds, "granted_scopes", None) or [])
        | set(getattr(creds, "scopes", None) or [])
    )


def chat_diagnostics_report():
    """Test Google Chat directly. This does not call OpenAI."""
    creds = load_credentials()
    granted = granted_scope_list(creds) if creds else []
    read_ok = credentials_have_scopes(creds, CHAT_READ_SCOPES)
    send_ok = credentials_have_scopes(creds, [CHAT_SEND_SCOPE])

    result = {
        "credentials_present": bool(creds),
        "read_scope_ok": read_ok,
        "send_scope_ok": send_ok,
        "granted_scopes": granted,
        "space_count": 0,
        "space_types": {},
        "recent_message_samples": 0,
        "sampled_spaces": [],
        "errors": [],
        "lookback_days": CHAT_SYNC_LOOKBACK_DAYS,
    }

    if not read_ok:
        result["errors"].append(
            "Google authorization does not include both Chat read scopes."
        )
        return result

    service = chat_service()
    if not service:
        result["errors"].append("Could not build Google Chat API service.")
        return result

    try:
        # Deliberately use no filter in diagnostics. Google should return every
        # visible space type for the authenticated user.
        resp = service.spaces().list(pageSize=100).execute()
        all_spaces = resp.get("spaces", [])
        ignored_names = [
            (s.get("displayName") or s.get("name") or "Unnamed")
            for s in all_spaces if ignored_chat_space(s)
        ]
        spaces = [s for s in all_spaces if not ignored_chat_space(s)]
        result["ignored_spaces"] = ignored_names
        result["space_count"] = len(spaces)
        for space in spaces:
            stype = space.get("spaceType", "UNKNOWN")
            result["space_types"][stype] = result["space_types"].get(stype, 0) + 1

        cutoff = datetime.now(timezone.utc) - timedelta(days=CHAT_SYNC_LOOKBACK_DAYS)
        cutoff_text = cutoff.isoformat().replace("+00:00", "Z")

        # Sample at most 12 spaces and 5 messages each so this remains light.
        for space in spaces[:12]:
            row = {
                "name": space.get("name", ""),
                "display_name": space.get("displayName", ""),
                "space_type": space.get("spaceType", ""),
                "recent_messages_visible": 0,
                "error": "",
            }
            try:
                msg_resp = service.spaces().messages().list(
                    parent=space.get("name", ""),
                    pageSize=5,
                    filter=f'createTime > "{cutoff_text}"',
                    orderBy="createTime DESC",
                ).execute()
                count = len(msg_resp.get("messages", []))
                row["recent_messages_visible"] = count
                result["recent_message_samples"] += count
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                result["errors"].append(
                    f"{space.get('displayName') or space.get('name')}: "
                    f"{type(exc).__name__}: {exc}"
                )
            result["sampled_spaces"].append(row)
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")

    return result


def chat_list_spaces(service):
    spaces = []
    token = None
    filter_value = 'spaceType = "SPACE" OR spaceType = "GROUP_CHAT" OR spaceType = "DIRECT_MESSAGE"'
    while len(spaces) < CHAT_SCAN_MAX_SPACES:
        resp = service.spaces().list(
            pageSize=min(100, CHAT_SCAN_MAX_SPACES - len(spaces)),
            pageToken=token,
            filter=filter_value,
        ).execute()
        spaces.extend(resp.get("spaces", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return spaces[:CHAT_SCAN_MAX_SPACES]


def chat_list_messages(service, space_name):
    cutoff = datetime.now(timezone.utc) - timedelta(days=CHAT_SYNC_LOOKBACK_DAYS)
    cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
    messages = []
    token = None
    while len(messages) < CHAT_SCAN_MAX_MESSAGES_PER_SPACE:
        resp = service.spaces().messages().list(
            parent=space_name,
            pageSize=min(100, CHAT_SCAN_MAX_MESSAGES_PER_SPACE - len(messages)),
            pageToken=token,
            filter=f'createTime > "{cutoff_text}"',
            orderBy="createTime ASC",
        ).execute()
        messages.extend(resp.get("messages", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return messages[:CHAT_SCAN_MAX_MESSAGES_PER_SPACE]


def chat_record_processed(con, name, space_name, classification):
    con.execute(
        "INSERT OR IGNORE INTO chat_processed(message_name,space_name,classification) VALUES(?,?,?)",
        (name, space_name, classification)
    )


def chat_message_text(msg):
    return (msg.get("text") or msg.get("formattedText") or msg.get("argumentText") or msg.get("fallbackText") or "").strip()


def chat_thread_name(msg):
    return (msg.get("thread") or {}).get("name", "")


def analyze_chat_message(msg, space):
    if not OPENAI_API_KEY:
        text = chat_message_text(msg)
        low = text.lower()
        actionable = any(k in low for k in ["can you", "could you", "please", "need", "follow up", "follow-up", "todo", "to do", "fix", "send"])
        return {
            "actionable": actionable, "category": "client" if actionable else "ignore",
            "task_title": (space.get("displayName") or "Google Chat") + " follow-up",
            "summary": text[:500], "priority": "high" if actionable else "normal", "due_date": "",
            "payment_amount": 0, "currency": "USD", "invoice_number": "", "suggested_reply": "",
            "gpt_can_help": False, "gpt_help_prompt": "", "gpt_help_reason": "",
            "confidence": "low", "reason": "Fallback Chat classifier."
        }
    sender = msg.get("sender", {}) or {}
    text = chat_message_text(msg)[:OPENAI_EMAIL_BODY_CHARS]
    prompt = f"""
Classify this Google Chat message for Todd/Smart 1's private task dashboard.
The authenticated user is reading a space they belong to. Google Chat user-auth may only expose canonical sender IDs,
so do not rely on knowing which participant is Todd. Use the message content and space context.

Create an actionable task when the message contains a request, commitment, deadline, problem, approval step, payment issue,
or other open loop Smart 1 may need to handle. Ignore casual conversation and informational chat with no action.
Payment means Smart 1 needs to pay/fund/reconcile money; money owed TO Smart 1 is a client task.
Do not invent deadlines or amounts.
Set gpt_can_help=true when GPT can materially help perform the requested work and provide a ready-to-use prompt.

Space: {space.get('displayName') or space.get('spaceType') or space.get('name')}
Space type: {space.get('spaceType','')}
Sender resource: {sender.get('name','')}
Created: {msg.get('createTime','')}
Thread: {chat_thread_name(msg)}

MESSAGE
{text}
""".strip()
    return openai_json(prompt, CHAT_ANALYSIS_SCHEMA, "chat_action_analysis")


def chat_candidate_tasks(con, space_name):
    rows = con.execute(
        "SELECT * FROM tasks WHERE completed=0 ORDER BY updated_at DESC LIMIT 150"
    ).fetchall()
    return [dict(r) for r in rows if r["chat_space_name"] == space_name or not r["chat_space_name"]][:20]


def ai_match_chat_task(msg, space, candidates, analysis):
    if not OPENAI_API_KEY or not candidates:
        return None
    candidate_text = "\n".join(
        f"Task {t['id']}: party={t['party']}; title={t['title']}; detail={t['detail']}; status={t['status']}; chat_space={t.get('chat_space_name','')}"
        for t in candidates
    )
    prompt = f"""
Decide whether this Google Chat message materially updates ONE existing open task.
A loose company/space match is not enough. It must be the same issue, deliverable, payment, decision, or follow-up.
If uncertain return related=false.

CHAT SPACE: {space.get('displayName') or space.get('name')}
MESSAGE: {chat_message_text(msg)[:1800]}
AI SUMMARY: {analysis.get('summary','')}

CANDIDATE TASKS
{candidate_text}
""".strip()
    try:
        result = openai_json(prompt, CHAT_RELATION_SCHEMA, "chat_related_task_match")
        if result.get("related") and result.get("task_id") in {t["id"] for t in candidates}:
            return result
    except Exception as exc:
        set_setting("openai_last_error", str(exc))
    return None


def attach_chat_update(con, task_id, msg, space, match_method="chat thread", make_urgent=True):
    name = msg.get("name", "")
    sender = msg.get("sender", {}) or {}
    try:
        con.execute(
            """
            INSERT INTO task_chat_updates
            (task_id,message_name,space_name,space_display_name,sender_display_name,message_text,create_time,match_method,thread_name,space_uri,direction)
            VALUES (?,?,?,?,?,?,?,?,?,?,'incoming')
            """,
            (task_id, name, space.get("name", ""), space.get("displayName", "") or space.get("spaceType", ""),
             sender.get("displayName", "") or sender.get("name", ""), chat_message_text(msg)[:5000],
             msg.get("createTime", ""), match_method, chat_thread_name(msg), space.get("spaceUri", ""))
        )
    except sqlite3.IntegrityError:
        return False
    if make_urgent:
        con.execute(
            "UPDATE tasks SET priority='urgent',status=CASE WHEN status='Waiting' THEN 'Open' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (task_id,)
        )
        con.execute(
            "INSERT INTO notes(task_id,body) VALUES(?,?)",
            (task_id, f"New related Google Chat message received; task changed to URGENT. Space: {space.get('displayName') or space.get('name')}")
        )
    return True


def insert_task_from_chat_suggestion(con, s):
    cur = con.execute(
        """
        INSERT INTO tasks
        (category,party,title,detail,due_date,priority,status,email_url,amount,currency,invoice_number,
         suggested_reply,ai_confidence,gpt_can_help,gpt_help_prompt,gpt_help_reason,source_kind,
         chat_space_name,chat_thread_name,chat_message_name,chat_space_uri,source_received_at)
        VALUES (?,?,?,?,?,?,'Open',?,?,?,?,?,?,?,?,?,'chat',?,?,?,?,?)
        """,
        (s["suggested_category"], s["space_display_name"] or s["sender_display_name"] or "Google Chat",
         s["suggested_title"] or "Google Chat task", s["suggested_summary"] or s["message_text"],
         s["suggested_due_date"], s["suggested_priority"], s["space_uri"], float(s["payment_amount"] or 0),
         s["currency"] or "USD", s["invoice_number"] or "", s["suggested_reply"] or "", s["confidence"] or "",
         int(s["gpt_can_help"] or 0), s["gpt_help_prompt"] or "", s["gpt_help_reason"] or "",
         s["space_name"], s["thread_name"], s["message_name"], s["space_uri"], s["create_time"] or "")
    )
    task_id = cur.lastrowid
    con.execute("UPDATE chat_suggestions SET state='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (s["id"],))
    return task_id


def sync_google_chat():
    service = chat_service()
    if not service:
        return {"connected": False, "spaces": 0, "checked": 0, "analyzed": 0, "added": 0, "updates": 0}
    spaces = chat_list_spaces(service)
    checked = analyzed = added = updates = 0
    space_errors = []
    ignored_spaces = 0
    for space in spaces:
        if ignored_chat_space(space):
            ignored_spaces += 1
            continue
        try:
            messages = chat_list_messages(service, space.get("name", ""))
        except Exception as exc:
            space_errors.append(
                f"{space.get('displayName') or space.get('name')}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        for msg in messages:
            name = msg.get("name", "")
            text = chat_message_text(msg)
            if not name or not text:
                continue
            checked += 1
            with connect_db() as con:
                if con.execute("SELECT 1 FROM chat_processed WHERE message_name=?", (name,)).fetchone():
                    continue
                exact_task = None
                thread_name = chat_thread_name(msg)
                if thread_name:
                    exact_task = con.execute(
                        "SELECT * FROM tasks WHERE completed=0 AND chat_thread_name=? ORDER BY updated_at DESC LIMIT 1",
                        (thread_name,)
                    ).fetchone()
            if exact_task:
                with connect_db() as con:
                    if attach_chat_update(con, exact_task["id"], msg, space, "same Google Chat thread", make_urgent=True):
                        updates += 1
                    chat_record_processed(con, name, space.get("name", ""), "task_update")
                maybe_create_resolution_review(exact_task["id"], "google_chat", name, space.get("spaceUri", ""))
                continue

            try:
                analysis = analyze_chat_message(msg, space)
            except Exception as exc:
                set_setting("chat_last_error", str(exc))
                continue
            analyzed += 1
            classification = analysis.get("category", "ignore")

            with connect_db() as con:
                candidates = chat_candidate_tasks(con, space.get("name", ""))
            relation = ai_match_chat_task(msg, space, candidates, analysis) if analysis.get("actionable") else None
            if relation and relation.get("material_update"):
                with connect_db() as con:
                    if attach_chat_update(con, relation["task_id"], msg, space, "AI Google Chat task match", make_urgent=True):
                        updates += 1
                    chat_record_processed(con, name, space.get("name", ""), "task_update")
                maybe_create_resolution_review(relation["task_id"], "google_chat", name, space.get("spaceUri", ""))
                continue

            with connect_db() as con:
                chat_record_processed(con, name, space.get("name", ""), classification)
                if not analysis.get("actionable") or classification == "ignore":
                    continue
                sender = msg.get("sender", {}) or {}
                try:
                    con.execute(
                        """
                        INSERT INTO chat_suggestions
                        (message_name,space_name,space_display_name,sender_user_name,sender_display_name,message_text,
                         create_time,suggested_title,suggested_category,suggested_priority,suggested_due_date,
                         suggested_summary,suggested_reply,confidence,reason,gpt_can_help,gpt_help_prompt,gpt_help_reason,
                         thread_name,space_uri,payment_amount,currency,invoice_number,state)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')
                        """,
                        (name, space.get("name", ""), space.get("displayName", "") or space.get("spaceType", ""),
                         sender.get("name", ""), sender.get("displayName", "") or sender.get("name", ""), text,
                         msg.get("createTime", ""), analysis.get("task_title", ""), classification,
                         analysis.get("priority", "normal"), analysis.get("due_date", ""), analysis.get("summary", ""),
                         analysis.get("suggested_reply", ""), analysis.get("confidence", ""), analysis.get("reason", ""),
                         1 if analysis.get("gpt_can_help") else 0, analysis.get("gpt_help_prompt", ""),
                         analysis.get("gpt_help_reason", ""), chat_thread_name(msg), space.get("spaceUri", ""),
                         float(analysis.get("payment_amount", 0) or 0), analysis.get("currency", "USD") or "USD",
                         analysis.get("invoice_number", ""))
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    pass
    set_setting("chat_last_sync", datetime.now().astimezone().isoformat())
    set_setting("chat_last_error", space_errors[0] if space_errors else "")
    return {
        "connected": True,
        "spaces": len(spaces),
        "checked": checked,
        "analyzed": analyzed,
        "added": added,
        "updates": updates,
        "ignored_spaces": ignored_spaces,
        "space_errors": space_errors[:10],
    }


def create_or_update_gpt_help(task_id):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required.")
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise RuntimeError("Task not found.")
    prompt = f"""
Decide whether GPT can materially help Todd complete this business task itself, rather than merely remind him.
Examples: draft or rewrite content, analyze information, research a defined topic, write code, troubleshoot from supplied evidence,
prepare a marketing plan, produce a client response, create structured instructions, or create a technical prompt for another coding agent.
If yes, produce a ready-to-use prompt that includes the task context, constraints, and desired deliverable.
If the task requires a physical action, a payment transaction, credentials you do not have, or a human-only external action, can_help=false.

Party: {task['party']}
Title: {task['title']}
Detail: {task['detail']}
""".strip()
    result = openai_json(prompt, GPT_HELP_SCHEMA, "task_gpt_help")
    with connect_db() as con:
        con.execute(
            "UPDATE tasks SET gpt_can_help=?,gpt_help_prompt=?,gpt_help_reason=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (1 if result.get("can_help") else 0, result.get("prompt", ""), result.get("reason", ""), task_id)
        )
    return result


def sync_all_communications():
    # Avoid overlapping manual, browser, and background syncs.
    if not SYNC_LOCK.acquire(blocking=False):
        return {"busy": True}
    try:
        result = {"busy": False}
        try:
            result["gmail"] = sync_gmail()
        except Exception as exc:
            set_setting("gmail_last_error", str(exc))
            result["gmail"] = {"error": str(exc)}
        finally:
            gc.collect()

        try:
            result["meetings"] = sync_meeting_recaps()
        except Exception as exc:
            set_setting("meeting_last_error", str(exc))
            result["meetings"] = {"error": str(exc)}
        finally:
            gc.collect()

        try:
            result["sent"] = sync_sent_mail()
        except Exception as exc:
            set_setting("sent_last_error", str(exc))
            result["sent"] = {"error": str(exc)}
        finally:
            gc.collect()

        try:
            result["chat"] = sync_google_chat()
        except Exception as exc:
            set_setting("chat_last_error", str(exc))
            result["chat"] = {"error": str(exc)}
        finally:
            gc.collect()

        try:
            consistent_database_backup()
        except Exception:
            app.logger.exception("Post-sync SQLite backup failed")
        return result
    finally:
        SYNC_LOCK.release()

def manual_sync_worker():
    with MANUAL_SYNC_STATE_LOCK:
        MANUAL_SYNC_STATE.update({
            "running": True,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "finished_at": "",
            "error": "",
            "result": {},
        })
    try:
        result = sync_all_communications()
        with MANUAL_SYNC_STATE_LOCK:
            MANUAL_SYNC_STATE["result"] = result
            if result.get("busy"):
                MANUAL_SYNC_STATE["error"] = "A background sync was already running."
    except Exception as exc:
        app.logger.exception("Manual communications sync failed")
        with MANUAL_SYNC_STATE_LOCK:
            MANUAL_SYNC_STATE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with MANUAL_SYNC_STATE_LOCK:
            MANUAL_SYNC_STATE["running"] = False
            MANUAL_SYNC_STATE["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        gc.collect()


# ---------------- Background sync ----------------

def background_sync_loop():
    time.sleep(30)
    while True:
        try:
            if get_setting("gmail_credentials", ""):
                sync_all_communications()
        except Exception as exc:
            set_setting("gmail_last_error", str(exc))
        finally:
            gc.collect()
        time.sleep(max(60, AUTO_GMAIL_SYNC_MINUTES * 60))


if AUTO_GMAIL_SYNC_MINUTES > 0 and os.environ.get("DISABLE_BACKGROUND_GMAIL_SYNC", "0") != "1":
    threading.Thread(target=background_sync_loop, daemon=True).start()


# ---------------- Serialization ----------------

def serialize_task(row, con):
    notes = con.execute(
        "SELECT id,body,created_at FROM notes WHERE task_id=? ORDER BY id DESC LIMIT 8", (row["id"],)
    ).fetchall()
    research = con.execute(
        "SELECT id,question,answer,confidence,sources_json,created_at FROM task_research_logs WHERE task_id=? ORDER BY id DESC LIMIT 4",
        (row["id"],)
    ).fetchall()
    updates = con.execute(
        "SELECT id,gmail_message_id,gmail_thread_id,sender_name,sender_email,subject,snippet,received_at,email_url,match_method,direction,to_emails,cc_emails,created_at "
        "FROM task_email_updates WHERE task_id=? ORDER BY id DESC LIMIT 6",
        (row["id"],)
    ).fetchall()
    chat_updates = con.execute(
        "SELECT id,message_name,space_name,space_display_name,sender_display_name,message_text,create_time,match_method,thread_name,space_uri,direction,created_at "
        "FROM task_chat_updates WHERE task_id=? ORDER BY id DESC LIMIT 8",
        (row["id"],)
    ).fetchall()
    participants = con.execute(
        "SELECT email,display_name,source FROM task_participants WHERE task_id=? ORDER BY id",
        (row["id"],)
    ).fetchall()
    resolution_rows = con.execute(
        "SELECT id,source_type,source_id,summary,confidence,sources_json,state,source_url,created_at,decided_at "
        "FROM task_resolution_reviews WHERE task_id=? ORDER BY id DESC LIMIT 8",
        (row["id"],)
    ).fetchall()
    item = dict(row)
    if not item.get("source_received_at"):
        item["source_received_at"] = item.get("created_at", "")
    item["notes"] = [dict(n) for n in notes]
    item["research_logs"] = []
    for r in research:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.pop("sources_json") or "[]")
        except Exception:
            d["sources"] = []
            d.pop("sources_json", None)
        item["research_logs"].append(d)
    item["email_updates"] = [dict(u) for u in updates]
    item["chat_updates"] = [dict(u) for u in chat_updates]
    item["participants"] = [dict(u) for u in participants]
    item["resolution_reviews"] = []
    for r in resolution_rows:
        d = dict(r)
        try:
            d["sources"] = json.loads(d.pop("sources_json") or "[]")
        except Exception:
            d["sources"] = []
            d.pop("sources_json", None)
        item["resolution_reviews"].append(d)
    return item


# ---------------- Web routes ----------------

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "database_ok": not bool(DB_STARTUP_ERROR),
        "database_error": DB_STARTUP_ERROR,
    })


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        session["authenticated"] = True
        return redirect(url_for("index"))
    error = ""
    if request.method == "POST":
        if request.form.get("password", "") == APP_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    if DB_STARTUP_ERROR:
        return redirect(url_for("database_recovery_page"))
    return render_template("index.html")


@app.get("/database-recovery")
@login_required
def database_recovery_page():
    status = {
        "error": DB_STARTUP_ERROR,
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "quick_check": database_quick_check(),
        "sqlite3_cli": native_sqlite3_path() or "",
        "backups": [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            }
            for p in sorted(DB_BACKUP_DIR.glob("**/*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if p.is_file()
        ][:30],
    }
    return render_template("recovery.html", status=status)


@app.get("/api/database/recovery/status")
@login_required
def database_recovery_status():
    return jsonify({
        "database_error": DB_STARTUP_ERROR,
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists(),
        "db_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "quick_check": database_quick_check(),
        "sqlite3_cli": native_sqlite3_path() or "",
    })


@app.post("/api/database/recovery/backup")
@login_required
def database_recovery_backup():
    try:
        folder, copied = copy_database_artifacts("manual-backup")
        return jsonify({"ok": True, "folder": str(folder), "copied": copied})
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@app.post("/api/database/recovery/attempt")
@login_required
def database_recovery_attempt():
    result = attempt_native_sqlite_recovery()
    return jsonify(result), (200 if result.get("ok") else 400)


def google_oauth_error_page(title, detail, status=400):
    safe_title = html.escape(str(title))
    safe_detail = html.escape(str(detail))
    safe_redirect = html.escape(redirect_uri())
    safe_scopes = "<br>".join(html.escape(s) for s in GOOGLE_SCOPES)
    return f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>{safe_title}</title>
      <style>
        body{{font-family:Arial,sans-serif;background:#f5f7fb;color:#18202a;margin:0;padding:32px}}
        .card{{max-width:860px;margin:auto;background:#fff;border:1px solid #dce2ea;border-radius:16px;padding:24px}}
        h1{{margin-top:0;color:#b91c1c}} code{{background:#eef2f7;padding:2px 5px;border-radius:5px}}
        .box{{background:#f8fafc;border:1px solid #dce2ea;border-radius:10px;padding:12px;margin:12px 0;word-break:break-word}}
        a{{color:#2563eb}}
      </style>
    </head>
    <body><div class="card">
      <h1>{safe_title}</h1>
      <p>The Google connection did not complete. The actual error is shown below instead of a generic Internal Server Error.</p>
      <div class="box"><strong>Error</strong><br>{safe_detail}</div>
      <p><strong>Expected redirect URI</strong></p>
      <div class="box"><code>{safe_redirect}</code></div>
      <p><strong>Scopes requested by this app</strong></p>
      <div class="box">{safe_scopes}</div>
      <p><a href="/gmail/connect">Try Google connection again</a> &nbsp; | &nbsp; <a href="/">Return to Action Center</a></p>
    </div></body></html>
    """, status


@app.get("/gmail/connect")
@login_required
def gmail_connect():
    if not gmail_configured():
        return google_oauth_error_page(
            "Google OAuth is not configured",
            "GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET is missing in Render.",
            400,
        )

    try:
        # Use PKCE explicitly. google-auth-oauthlib generates a one-time
        # code_verifier when authorization_url() is called. Because the OAuth
        # callback is handled by a NEW Flow object on a later HTTP request, the
        # verifier must be preserved and restored for the token exchange.
        flow = Flow.from_client_config(
            oauth_client_config(),
            scopes=GOOGLE_SCOPES,
            redirect_uri=redirect_uri(),
            autogenerate_code_verifier=True,
        )

        # IMPORTANT:
        # This application may share a Google project/client with other Smart 1
        # utilities that have previously requested Ads, Analytics, GTM, etc.
        # Keep this OAuth grant isolated to the Gmail + Chat scopes.
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="false",
            prompt="consent",
        )

        session["google_oauth_state"] = state
        session["google_oauth_code_verifier"] = flow.code_verifier
        session.modified = True
        return redirect(authorization_url)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        set_setting("gmail_last_error", f"OAuth start failed: {detail}")
        app.logger.exception("Google OAuth start failed")
        return google_oauth_error_page("Google Connection Failed", detail, 500)


@app.get("/gmail/callback")
@login_required
def gmail_callback():
    google_error = request.args.get("error")
    if google_error:
        description = request.args.get("error_description", "")
        detail = f"{google_error}: {description}".strip(": ")
        set_setting("gmail_last_error", f"Google authorization denied: {detail}")
        session.pop("google_oauth_state", None)
        session.pop("google_oauth_code_verifier", None)
        session.modified = True
        return google_oauth_error_page("Google Authorization Was Not Completed", detail, 400)

    expected_state = session.get("google_oauth_state")
    returned_state = request.args.get("state")
    code_verifier = session.get("google_oauth_code_verifier")

    if not expected_state:
        detail = (
            "The OAuth session state was missing when Google returned to the app. "
            "This can happen if the browser session/cookie changed during authorization. "
            "Start the Google connection again from the Action Center."
        )
        set_setting("gmail_last_error", detail)
        return google_oauth_error_page("Google OAuth Session Lost", detail, 400)

    if returned_state != expected_state:
        detail = (
            "The OAuth state returned by Google did not match the state stored by the app. "
            "Start the connection again in the same browser tab/session."
        )
        set_setting("gmail_last_error", detail)
        return google_oauth_error_page("Google OAuth State Mismatch", detail, 400)

    if not code_verifier:
        detail = (
            "The PKCE code verifier was not present in the browser session when Google "
            "returned to the app. Start the Google connection again from the Action Center "
            "in the same browser session."
        )
        set_setting("gmail_last_error", detail)
        return google_oauth_error_page("Google OAuth PKCE Session Lost", detail, 400)

    try:
        # Restore the exact PKCE verifier that was used to create the
        # code_challenge in /gmail/connect.
        flow = Flow.from_client_config(
            oauth_client_config(),
            scopes=GOOGLE_SCOPES,
            state=expected_state,
            redirect_uri=redirect_uri(),
            code_verifier=code_verifier,
            autogenerate_code_verifier=False,
        )
        flow.fetch_token(authorization_response=request.url)

        creds = flow.credentials
        granted = set(getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None) or [])
        missing = [scope for scope in GOOGLE_SCOPES if scope not in granted]

        if missing:
            detail = "Google connected but did not grant all required scopes. Missing: " + ", ".join(missing)
            set_setting("gmail_last_error", detail)
            # Keep the credentials so Gmail-only access can still be inspected,
            # but show the user exactly which Chat permission was not granted.
            save_credentials(creds)
            return google_oauth_error_page("Google Permissions Incomplete", detail, 400)

        save_credentials(creds)
        set_setting("gmail_last_error", "")
        session.pop("google_oauth_state", None)
        session.pop("google_oauth_code_verifier", None)
        session.modified = True
        return redirect(url_for("index"))

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        set_setting("gmail_last_error", f"OAuth callback failed: {detail}")
        app.logger.error(
            "Google OAuth callback failed:\n%s",
            traceback.format_exc(),
        )
        return google_oauth_error_page("Google Connection Failed", detail, 400)


@app.get("/api/chat/diagnostics")
@login_required
def chat_diagnostics():
    try:
        return jsonify(chat_diagnostics_report())
    except Exception as exc:
        app.logger.exception("Chat diagnostics failed")
        return jsonify({
            "error": f"{type(exc).__name__}: {exc}",
            "errors": [f"{type(exc).__name__}: {exc}"],
        }), 500


@app.get("/api/system/status")
@login_required
def system_status():
    creds = load_credentials()
    gmail_connected = credentials_have_scopes(creds, [GMAIL_SCOPE])
    chat_connected = credentials_have_scopes(creds, CHAT_READ_SCOPES)
    chat_send_enabled = credentials_have_scopes(creds, [CHAT_SEND_SCOPE])
    return jsonify({
        "google_configured": gmail_configured(),
        "gmail_connected": gmail_connected,
        "chat_connected": chat_connected,
        "chat_send_enabled": chat_send_enabled,
        "chat_send_scope_upgrade_needed": bool(creds and chat_connected and not chat_send_enabled),
        "google_scope_upgrade_needed": bool(creds and gmail_connected and not chat_connected),
        "gmail_last_sync": get_setting("gmail_last_sync", ""),
        "gmail_last_error": get_setting("gmail_last_error", ""),
        "chat_last_sync": get_setting("chat_last_sync", ""),
        "chat_last_error": get_setting("chat_last_error", ""),
        "sent_last_sync": get_setting("sent_last_sync", ""),
        "sent_last_error": get_setting("sent_last_error", ""),
        "meeting_last_sync": get_setting("meeting_last_sync", ""),
        "meeting_last_error": get_setting("meeting_last_error", ""),
        "gmail_auto_add": get_setting("gmail_auto_add", "0") == "1",
        "gmail_query": GMAIL_SYNC_QUERY,
        "auto_sync_minutes": AUTO_GMAIL_SYNC_MINUTES,
        "openai_configured": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_MODEL,
        "openai_last_error": get_setting("openai_last_error", ""),
        "redirect_uri": redirect_uri(),
        "new_task_window_days": 30,
        "research_searches_all_history": True,
        "auto_add_invoices": get_setting("auto_add_invoices", "1") == "1",
        "not_task_training_count": len(not_task_examples("gmail", 500)) + len(not_task_examples("chat", 500)),
        "xwf_google_ignored": ignored_source("gmail", "notice@xwf.google.com", "xwf.google.com"),
    })


@app.patch("/api/settings")
@login_required
def update_settings():
    payload = request.get_json(force=True)
    if "gmail_auto_add" in payload:
        set_setting("gmail_auto_add", "1" if payload["gmail_auto_add"] else "0")
    if "auto_add_invoices" in payload:
        set_setting("auto_add_invoices", "1" if payload["auto_add_invoices"] else "0")
    return system_status()


@app.post("/api/gmail/disconnect")
@login_required
def gmail_disconnect():
    set_setting("gmail_credentials", "")
    set_setting("gmail_last_sync", "")
    return jsonify({"ok": True})


@app.post("/api/gmail/sync")
@login_required
def gmail_sync_endpoint():
    if not gmail_service():
        return jsonify({"error": "Gmail is not connected."}), 400
    try:
        return jsonify(sync_gmail())
    except Exception as exc:
        set_setting("gmail_last_error", str(exc))
        return jsonify({"error": str(exc)}), 500


@app.post("/api/communications/sync")
@login_required
def communications_sync_endpoint():
    with MANUAL_SYNC_STATE_LOCK:
        if MANUAL_SYNC_STATE["running"]:
            return jsonify({
                "busy": True,
                "running": True,
                "message": "A manual communications sync is already running.",
            }), 202

    if SYNC_LOCK.locked():
        return jsonify({
            "busy": True,
            "running": False,
            "message": "The scheduled background sync is already running.",
        }), 202

    threading.Thread(target=manual_sync_worker, daemon=True).start()
    return jsonify({
        "started": True,
        "running": True,
        "message": "Sync started in the background.",
    }), 202


@app.get("/api/communications/sync/status")
@login_required
def communications_sync_status():
    with MANUAL_SYNC_STATE_LOCK:
        return jsonify(dict(MANUAL_SYNC_STATE))


@app.post("/api/chat/sync")
@login_required
def chat_sync_endpoint():
    if not chat_service():
        return jsonify({"error": "Google Chat is not authorized. Reconnect Google to grant the Chat read scopes."}), 400
    try:
        return jsonify(sync_google_chat())
    except Exception as exc:
        set_setting("chat_last_error", str(exc))
        return jsonify({"error": str(exc)}), 500


@app.get("/api/gmail/suggestions")
@login_required
def gmail_suggestions():
    state = request.args.get("state", "new")
    with connect_db() as con:
        rows = con.execute("""
            SELECT * FROM gmail_suggestions
            WHERE state=?
            ORDER BY CASE WHEN suggested_due_date='' THEN 1 ELSE 0 END,
                     suggested_due_date ASC, received_at DESC
        """, (state,)).fetchall()
        return jsonify([dict(r) for r in rows])


@app.post("/api/gmail/suggestions/<int:suggestion_id>/approve")
@login_required
def approve_suggestion(suggestion_id):
    payload = request.get_json(silent=True) or {}
    with connect_db() as con:
        s = con.execute("SELECT * FROM gmail_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not s:
            return jsonify({"error": "Suggestion not found"}), 404
        if task_exists_for_message(con, s["gmail_message_id"]):
            con.execute("UPDATE gmail_suggestions SET state='approved' WHERE id=?", (suggestion_id,))
            return jsonify({"ok": True, "already_exists": True})
        editable = dict(s)
        mapping = {
            "category": "suggested_category", "priority": "suggested_priority",
            "due_date": "suggested_due_date", "title": "suggested_title", "party": "sender_name",
            "amount": "payment_amount", "invoice_number": "invoice_number", "invoice_sent": "invoice_sent",
        }
        for src, dst in mapping.items():
            if src in payload:
                editable[dst] = payload[src]
        task_id = insert_task_from_suggestion(con, editable)
        return jsonify({"ok": True, "task_id": task_id})


@app.post("/api/gmail/suggestions/<int:suggestion_id>/dismiss")
@login_required
def dismiss_suggestion(suggestion_id):
    with connect_db() as con:
        con.execute(
            "UPDATE gmail_suggestions SET state='dismissed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (suggestion_id,)
        )
    return jsonify({"ok": True})


@app.post("/api/gmail/suggestions/<int:suggestion_id>/not-task")
@login_required
def train_gmail_not_task(suggestion_id):
    payload = request.get_json(silent=True) or {}
    with connect_db() as con:
        s = con.execute(
            "SELECT * FROM gmail_suggestions WHERE id=?",
            (suggestion_id,)
        ).fetchone()
        if not s:
            return jsonify({"error": "Suggestion not found"}), 404

        store_not_task_training(
            "gmail",
            source_id=s["gmail_message_id"],
            sender_name=s["sender_name"],
            sender_email=s["sender_email"],
            subject=s["subject"],
            excerpt=s["snippet"] or s["suggested_summary"],
            reason=(payload.get("reason") or "User marked Gmail suggestion Not a Task"),
        )

        con.execute(
            """
            UPDATE gmail_suggestions
            SET state='trained_not_task',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (suggestion_id,)
        )
        record_processed(
            con,
            s["gmail_message_id"],
            s["gmail_thread_id"],
            "user_trained_not_task"
        )

    return jsonify({
        "ok": True,
        "trained": True,
        "message": "Saved as a NOT A TASK training example."
    })



# Google Chat review queue.
@app.get("/api/chat/suggestions")
@login_required
def chat_suggestions():
    state = request.args.get("state", "new")
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT * FROM chat_suggestions WHERE state=?
            ORDER BY CASE WHEN suggested_due_date='' THEN 1 ELSE 0 END,
                     suggested_due_date ASC, create_time DESC
            """,
            (state,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.post("/api/chat/suggestions/<int:suggestion_id>/approve")
@login_required
def approve_chat_suggestion(suggestion_id):
    with connect_db() as con:
        srow = con.execute("SELECT * FROM chat_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not srow:
            return jsonify({"error": "Chat suggestion not found"}), 404
        existing = con.execute("SELECT id FROM tasks WHERE chat_message_name=? LIMIT 1", (srow["message_name"],)).fetchone()
        if existing:
            con.execute("UPDATE chat_suggestions SET state='approved' WHERE id=?", (suggestion_id,))
            return jsonify({"ok": True, "already_exists": True, "task_id": existing["id"]})
        task_id = insert_task_from_chat_suggestion(con, srow)
        return jsonify({"ok": True, "task_id": task_id})


@app.post("/api/chat/suggestions/<int:suggestion_id>/dismiss")
@login_required
def dismiss_chat_suggestion(suggestion_id):
    with connect_db() as con:
        con.execute(
            "UPDATE chat_suggestions SET state='dismissed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (suggestion_id,)
        )
    return jsonify({"ok": True})


@app.post("/api/chat/suggestions/<int:suggestion_id>/not-task")
@login_required
def train_chat_not_task(suggestion_id):
    payload = request.get_json(silent=True) or {}
    with connect_db() as con:
        s = con.execute(
            "SELECT * FROM chat_suggestions WHERE id=?",
            (suggestion_id,)
        ).fetchone()
        if not s:
            return jsonify({"error": "Chat suggestion not found"}), 404

        store_not_task_training(
            "chat",
            source_id=s["message_name"],
            sender_name=s["sender_display_name"],
            sender_email="",
            subject=s["suggested_title"] or s["space_display_name"],
            excerpt=s["message_text"] or s["suggested_summary"],
            reason=(payload.get("reason") or "User marked Chat suggestion Not a Task"),
        )

        con.execute(
            """
            UPDATE chat_suggestions
            SET state='trained_not_task',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (suggestion_id,)
        )
        chat_record_processed(
            con,
            s["message_name"],
            s["space_name"],
            "user_trained_not_task"
        )

    return jsonify({
        "ok": True,
        "trained": True,
        "message": "Saved as a NOT A TASK training example."
    })


# Sent-mail follow-up dashboard.
@app.get("/api/sent-followups")
@login_required
def list_sent_followups():
    state = request.args.get("state", "monitoring")
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT * FROM sent_monitors
            WHERE state=?
            ORDER BY CASE WHEN followup_due='' THEN 1 ELSE 0 END, followup_due ASC, sent_at DESC
            """,
            (state,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])


@app.post("/api/sent-followups/<int:monitor_id>/dismiss")
@login_required
def dismiss_sent_followup(monitor_id):
    with connect_db() as con:
        con.execute("UPDATE sent_monitors SET state='dismissed' WHERE id=?", (monitor_id,))
    return jsonify({"ok": True})


@app.post("/api/sent-followups/<int:monitor_id>/create-task")
@login_required
def create_task_from_sent_followup(monitor_id):
    with connect_db() as con:
        monitor = con.execute("SELECT * FROM sent_monitors WHERE id=?", (monitor_id,)).fetchone()
        if not monitor:
            return jsonify({"error": "Follow-up item not found"}), 404
        if monitor["task_id"]:
            return jsonify({"ok": True, "task_id": monitor["task_id"], "already_linked": True})
        cur = con.execute(
            """
            INSERT INTO tasks
            (category,party,title,detail,due_date,priority,status,email_url,gmail_thread_id,source_kind)
            VALUES ('client',?,?,?,?,?,'Open',?,?,'gmail')
            """,
            (
                monitor["party"] or "Sent email follow-up",
                monitor["subject"] or "Follow up on sent email",
                monitor["summary"] or monitor["reason"],
                monitor["followup_due"],
                monitor["priority"] or "high",
                monitor["email_url"],
                monitor["gmail_thread_id"],
            )
        )
        task_id = cur.lastrowid
        con.execute("UPDATE sent_monitors SET task_id=? WHERE id=?", (task_id, monitor_id))
        con.execute(
            "INSERT INTO notes(task_id,body) VALUES(?,?)",
            (task_id, "Created from Sent Follow-ups monitoring.")
        )
        return jsonify({"ok": True, "task_id": task_id})


# Task resolution confirmations.
@app.post("/api/tasks/<int:task_id>/resolution/<int:review_id>")
@login_required
def decide_resolution(task_id, review_id):
    payload = request.get_json(force=True)
    resolved = bool(payload.get("resolved"))
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect_db() as con:
        review = con.execute(
            "SELECT * FROM task_resolution_reviews WHERE id=? AND task_id=?",
            (review_id, task_id)
        ).fetchone()
        if not review:
            return jsonify({"error": "Resolution review not found"}), 404
        state = "confirmed" if resolved else "rejected"
        con.execute(
            "UPDATE task_resolution_reviews SET state=?,decided_at=? WHERE id=?",
            (state, stamp, review_id)
        )
        if resolved:
            con.execute(
                "UPDATE tasks SET completed=1,completed_at=?,status='Completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (stamp, task_id)
            )
            con.execute(
                "INSERT INTO notes(task_id,body) VALUES(?,?)",
                (task_id, f"Resolution confirmed by Todd: {review['summary']}")
            )
        else:
            con.execute(
                "UPDATE tasks SET completed=0,status=CASE WHEN status='Completed' THEN 'Open' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (task_id,)
            )
            con.execute(
                "INSERT INTO notes(task_id,body) VALUES(?,?)",
                (task_id, f"Possible resolution rejected; task remains open. AI summary: {review['summary']}")
            )
    return jsonify({"ok": True, "completed": resolved})


@app.post("/api/tasks/<int:task_id>/check-resolution")
@login_required
def check_task_resolution(task_id):
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=? AND completed=0", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Open task not found"}), 404
        last_email = con.execute(
            "SELECT gmail_message_id,email_url FROM task_email_updates WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,)
        ).fetchone()
        last_chat = con.execute(
            "SELECT message_name,space_uri FROM task_chat_updates WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,)
        ).fetchone()
    if last_email:
        result = maybe_create_resolution_review(task_id, "gmail_manual_check", last_email["gmail_message_id"], last_email["email_url"])
    elif last_chat:
        result = maybe_create_resolution_review(task_id, "chat_manual_check", last_chat["message_name"], last_chat["space_uri"])
    elif task["gmail_message_id"]:
        result = maybe_create_resolution_review(task_id, "gmail_source", task["gmail_message_id"], task["email_url"])
    elif task["chat_message_name"]:
        result = maybe_create_resolution_review(task_id, "chat_source", task["chat_message_name"], task["chat_space_uri"])
    else:
        return jsonify({"error": "No communication is attached to this task yet."}), 400
    return jsonify({"ok": True, "assessment": result or {}})



@app.post("/api/tasks/<int:task_id>/gpt-help/suppress")
@login_required
def suppress_task_gpt_help(task_id):
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Task not found"}), 404
    try:
        trained = save_gpt_help_suppression(
            task["email_to"] or "",
            task["email_subject"] or task["title"] or "",
            "Todd selected Don't suggest GPT help for this email type."
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    with connect_db() as con:
        con.execute(
            """
            UPDATE tasks
            SET gpt_can_help=0,gpt_help_prompt='',gpt_help_reason='',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (task_id,)
        )
        con.execute(
            "INSERT INTO notes(task_id,body) VALUES(?,?)",
            (task_id, "Trained: do not suggest GPT help for future emails of this type.")
        )
    return jsonify({"ok": True, "trained": trained})


@app.post("/api/gmail/suggestions/<int:suggestion_id>/gpt-help/suppress")
@login_required
def suppress_suggestion_gpt_help(suggestion_id):
    with connect_db() as con:
        s = con.execute("SELECT * FROM gmail_suggestions WHERE id=?", (suggestion_id,)).fetchone()
        if not s:
            return jsonify({"error": "Suggestion not found"}), 404
    try:
        trained = save_gpt_help_suppression(
            s["sender_email"] or "",
            s["subject"] or s["suggested_title"] or "",
            "Todd selected Don't suggest GPT help for this email type from Gmail Review."
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    with connect_db() as con:
        con.execute(
            """
            UPDATE gmail_suggestions
            SET gpt_can_help=0,gpt_help_prompt='',gpt_help_reason='',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (suggestion_id,)
        )
    return jsonify({"ok": True, "trained": trained})


@app.post("/api/tasks/<int:task_id>/gpt-help")
@login_required
def task_gpt_help(task_id):
    try:
        return jsonify(create_or_update_gpt_help(task_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# Gemini / Google Meet recap review queue. Meeting tasks are never auto-added.
@app.get("/api/meetings/reviews")
@login_required
def list_meeting_reviews():
    state = request.args.get("state", "new")
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT * FROM meeting_reviews WHERE state=?
            ORDER BY received_at DESC, id DESC
            """,
            (state,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["tasks"] = json.loads(item.pop("tasks_json") or "[]")
            except Exception:
                item["tasks"] = []
                item.pop("tasks_json", None)
            result.append(item)
        return jsonify(result)


@app.post("/api/meetings/reviews/<int:review_id>/add")
@login_required
def add_meeting_review_tasks(review_id):
    payload = request.get_json(force=True)
    selections = payload.get("tasks", []) or []
    if not selections:
        return jsonify({"error": "Choose at least one meeting task to add."}), 400
    with connect_db() as con:
        review = con.execute("SELECT * FROM meeting_reviews WHERE id=?", (review_id,)).fetchone()
        if not review:
            return jsonify({"error": "Meeting review not found."}), 404
        try:
            proposed = json.loads(review["tasks_json"] or "[]")
        except Exception:
            proposed = []
        added = []
        for selection in selections:
            try:
                index = int(selection.get("index"))
            except Exception:
                continue
            if index < 0 or index >= len(proposed):
                continue
            task = dict(proposed[index])
            assignee = (selection.get("assignee") or task.get("assignee") or "").strip()
            cur = con.execute(
                """
                INSERT INTO tasks
                (category,party,title,detail,due_date,priority,status,email_url,gmail_thread_id,
                 assignee,gpt_can_help,gpt_help_prompt,gpt_help_reason,source_kind)
                VALUES ('client',?,?,?,?,?,'Open',?,?,?,?,?,?,'meeting')
                """,
                (
                    review["meeting_title"] or "Meeting follow-up",
                    task.get("title") or "Meeting action item",
                    task.get("summary") or review["summary"] or "",
                    task.get("due_date") or "",
                    task.get("priority") or "high",
                    review["email_url"] or "",
                    review["gmail_thread_id"] or "",
                    assignee,
                    1 if task.get("gpt_can_help") else 0,
                    task.get("gpt_help_prompt") or "",
                    task.get("gpt_help_reason") or "",
                )
            )
            task_id = cur.lastrowid
            con.execute(
                "INSERT INTO notes(task_id,body) VALUES(?,?)",
                (task_id, f"Added from Gemini/Google Meet recap: {review['meeting_title']}." + (f" Assigned to {assignee}." if assignee else ""))
            )
            added.append(task_id)
        con.execute(
            "UPDATE meeting_reviews SET state='added',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (review_id,)
        )
    return jsonify({"ok": True, "added": len(added), "task_ids": added})


@app.post("/api/meetings/reviews/<int:review_id>/dismiss")
@login_required
def dismiss_meeting_review(review_id):
    with connect_db() as con:
        con.execute(
            "UPDATE meeting_reviews SET state='dismissed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (review_id,)
        )
    return jsonify({"ok": True})


# Natural-language Gmail discovery.
@app.post("/api/gmail/discover-tasks")
@login_required
def gmail_discover_tasks():
    payload = request.get_json(force=True)
    query = (payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Ask what company or work you want me to look for."}), 400
    try:
        return jsonify(discover_tasks_from_gmail(query))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/gmail/discover-add")
@login_required
def gmail_discover_add():
    payload = request.get_json(force=True)
    candidates = payload.get("tasks", []) or []
    ids = []
    for candidate in candidates:
        try:
            ids.append(add_discovered_task(candidate))
        except Exception:
            continue
    return jsonify({"ok": True, "task_ids": ids, "added": len(ids)})


# Watch domains.
@app.get("/api/watch-domains")
@login_required
def list_watch_domains():
    return jsonify(watch_domains(False))


@app.post("/api/watch-domains")
@login_required
def add_watch_domain():
    payload = request.get_json(force=True)
    domain = normalize_domain(payload.get("domain", ""))
    label = (payload.get("label") or "").strip()
    if not domain or "." not in domain:
        return jsonify({"error": "Enter a valid domain such as iconsolar.com"}), 400
    with connect_db() as con:
        con.execute("""
            INSERT INTO watch_domains(domain,label,enabled) VALUES(?,?,1)
            ON CONFLICT(domain) DO UPDATE SET label=excluded.label,enabled=1
        """, (domain, label))
        row = con.execute("SELECT * FROM watch_domains WHERE domain=?", (domain,)).fetchone()
        return jsonify(dict(row)), 201


@app.delete("/api/watch-domains/<int:domain_id>")
@login_required
def delete_watch_domain(domain_id):
    with connect_db() as con:
        con.execute("DELETE FROM watch_domains WHERE id=?", (domain_id,))
    return jsonify({"ok": True})


@app.patch("/api/watch-domains/<int:domain_id>")
@login_required
def toggle_watch_domain(domain_id):
    payload = request.get_json(force=True)
    enabled = 1 if payload.get("enabled", True) else 0
    with connect_db() as con:
        con.execute("UPDATE watch_domains SET enabled=? WHERE id=?", (enabled, domain_id))
        row = con.execute("SELECT * FROM watch_domains WHERE id=?", (domain_id,)).fetchone()
        if not row:
            return jsonify({"error": "Domain not found"}), 404
        return jsonify(dict(row))


# Task APIs.
@app.get("/api/tasks")
@login_required
def list_tasks():
    completed = 1 if request.args.get("completed", "0") == "1" else 0
    category = request.args.get("category", "")
    invoice_only = request.args.get("invoice_only", "0") == "1"
    where, params = ["completed=?"], [completed]
    if category:
        where.append("category=?")
        params.append(category)
    if invoice_only:
        where.extend(["category='payment'", "invoice_sent=1"])
    with connect_db() as con:
        rows = con.execute(f"""
            SELECT * FROM tasks
            WHERE {' AND '.join(where)}
            ORDER BY CASE WHEN due_date='' THEN 1 ELSE 0 END, due_date ASC, party ASC
        """, params).fetchall()
        return jsonify([serialize_task(r, con) for r in rows])



@app.get("/api/invoices")
@login_required
def invoice_register():
    """Complete invoice register: open + paid invoice records."""
    with connect_db() as con:
        rows = con.execute(
            """
            SELECT * FROM tasks
            WHERE category='payment'
              AND (invoice_sent=1 OR COALESCE(invoice_number,'') <> '')
            ORDER BY
              completed ASC,
              CASE WHEN due_date='' THEN 1 ELSE 0 END,
              due_date ASC,
              source_received_at DESC,
              id DESC
            """
        ).fetchall()
        return jsonify([serialize_task(r, con) for r in rows])



@app.get("/api/dashboard/counts")
@login_required
def dashboard_counts():
    with connect_db() as con:
        return jsonify({
            "client": con.execute(
                "SELECT COUNT(*) FROM tasks WHERE completed=0 AND category='client'"
            ).fetchone()[0],
            "payment": con.execute(
                "SELECT COUNT(*) FROM tasks WHERE completed=0 AND category='payment'"
            ).fetchone()[0],
            "invoice": con.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE category='payment' AND (invoice_sent=1 OR COALESCE(invoice_number,'')<>'')
                """
            ).fetchone()[0],
            "sent": con.execute(
                "SELECT COUNT(*) FROM sent_monitors WHERE state='monitoring'"
            ).fetchone()[0],
            "gmail": con.execute(
                "SELECT COUNT(*) FROM gmail_suggestions WHERE state='new'"
            ).fetchone()[0],
            "chat": con.execute(
                """
                SELECT COUNT(*) FROM chat_suggestions
                WHERE state='new' AND lower(trim(space_display_name))<>'sales team to me'
                """
            ).fetchone()[0],
            "meetings": con.execute(
                "SELECT COUNT(*) FROM meeting_reviews WHERE state='new'"
            ).fetchone()[0],
            "completed": con.execute(
                "SELECT COUNT(*) FROM tasks WHERE completed=1"
            ).fetchone()[0],
        })


@app.get("/api/payment-summary")
@login_required
def payment_summary():
    with connect_db() as con:
        row = con.execute("""
            SELECT
                   SUM(CASE WHEN completed=0 THEN 1 ELSE 0 END) AS count_all,
                   COALESCE(SUM(CASE WHEN completed=0 AND amount>0 THEN amount ELSE 0 END),0) AS total_known,
                   SUM(CASE WHEN completed=0 AND invoice_sent=1 THEN 1 ELSE 0 END) AS invoice_count,
                   COALESCE(SUM(CASE WHEN completed=0 AND invoice_sent=1 AND amount>0 THEN amount ELSE 0 END),0) AS invoice_total,
                   SUM(CASE WHEN completed=0 AND due_date<>'' AND due_date<date('now') THEN 1 ELSE 0 END) AS overdue_count,
                   COALESCE(SUM(CASE WHEN completed=0 AND due_date<>'' AND due_date<date('now') AND amount>0 THEN amount ELSE 0 END),0) AS overdue_total,
                   SUM(CASE WHEN (invoice_sent=1 OR COALESCE(invoice_number,'')<>'') THEN 1 ELSE 0 END) AS invoice_register_count,
                   SUM(CASE WHEN completed=0 AND (invoice_sent=1 OR COALESCE(invoice_number,'')<>'') THEN 1 ELSE 0 END) AS invoice_unpaid_count,
                   SUM(CASE WHEN completed=1 AND (invoice_sent=1 OR COALESCE(invoice_number,'')<>'') THEN 1 ELSE 0 END) AS invoice_paid_count,
                   COALESCE(SUM(CASE WHEN completed=1 AND (invoice_sent=1 OR COALESCE(invoice_number,'')<>'') AND paid_amount>0 THEN paid_amount ELSE 0 END),0) AS invoice_paid_total
            FROM tasks WHERE category='payment'
        """).fetchone()
        return jsonify(dict(row))


@app.post("/api/tasks")
@login_required
def create_task():
    payload = request.get_json(force=True)
    title = (payload.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    with connect_db() as con:
        cur = con.execute("""
            INSERT INTO tasks
            (category,party,title,detail,due_date,priority,status,email_url,email_to,email_subject,
             amount,currency,invoice_number,invoice_sent,invoice_sent_at,assignee,source_received_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            payload.get("category", "client"), payload.get("party", "Unassigned").strip() or "Unassigned",
            title, (payload.get("detail") or "").strip(), payload.get("due_date", ""),
            payload.get("priority", "normal"), payload.get("status", "Open"), payload.get("email_url", ""),
            payload.get("email_to", ""), payload.get("email_subject", ""), float(payload.get("amount", 0) or 0),
            payload.get("currency", "USD") or "USD", payload.get("invoice_number", ""),
            1 if payload.get("invoice_sent") else 0,
            datetime.now().astimezone().isoformat(timespec="seconds") if payload.get("invoice_sent") else "",
            (payload.get("assignee") or "").strip(),
            datetime.now().astimezone().isoformat(timespec="seconds"),
        ))
        row = con.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(serialize_task(row, con)), 201


@app.patch("/api/tasks/<int:task_id>")
@login_required
def update_task(task_id):
    payload = request.get_json(force=True)
    allowed = {
        "category", "party", "title", "detail", "due_date", "priority", "status",
        "email_url", "email_to", "email_subject", "amount", "currency", "invoice_number",
        "suggested_reply", "ai_confidence", "assignee"
    }
    fields, values = [], []
    for key in allowed:
        if key in payload:
            fields.append(f"{key}=?")
            values.append(payload[key])
    if "invoice_sent" in payload:
        flag = 1 if payload["invoice_sent"] else 0
        fields.extend(["invoice_sent=?", "invoice_sent_at=?"])
        values.extend([flag, datetime.now().astimezone().isoformat(timespec="seconds") if flag else ""])
    if not fields:
        return jsonify({"error": "No changes supplied"}), 400
    fields.append("updated_at=CURRENT_TIMESTAMP")
    values.append(task_id)
    with connect_db() as con:
        con.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return jsonify({"error": "Task not found"}), 404
        return jsonify(serialize_task(row, con))


@app.post("/api/tasks/<int:task_id>/notes")
@login_required
def add_note(task_id):
    body = (request.get_json(force=True).get("body") or "").strip()
    if not body:
        return jsonify({"error": "Note cannot be blank"}), 400
    with connect_db() as con:
        if not con.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
            return jsonify({"error": "Task not found"}), 404
        con.execute("INSERT INTO notes(task_id,body) VALUES(?,?)", (task_id, body))
        con.execute("UPDATE tasks SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return jsonify(serialize_task(row, con))


@app.post("/api/tasks/<int:task_id>/email-research")
@login_required
def task_email_research(task_id):
    payload = request.get_json(force=True)
    question = (payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Enter a question to search your email."}), 400
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "Task not found"}), 404
    try:
        return jsonify(research_task_email(task, question))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@app.post("/api/tasks/<int:task_id>/chat/reply")
@login_required
def send_chat_reply(task_id):
    service = chat_service(require_send=True)
    if not service:
        return jsonify({"error": "Google Chat is not connected with send permission. Reconnect Google after adding chat.messages.create."}), 400

    payload = request.get_json(force=True)
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Enter a Chat message."}), 400

    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Task not found"}), 404

    space_name = task["chat_space_name"]
    thread_name = task["chat_thread_name"]
    if not space_name:
        return jsonify({"error": "This task is not linked to a Google Chat space."}), 400

    message_body = {"text": text}
    create_args = {"parent": space_name, "body": message_body}
    if thread_name:
        message_body["thread"] = {"name": thread_name}
        create_args["messageReplyOption"] = "REPLY_MESSAGE_OR_FAIL"

    try:
        sent = service.spaces().messages().create(**create_args).execute()
    except Exception as exc:
        return jsonify({"error": f"Google Chat send failed: {exc}"}), 500

    message_name = sent.get("name", "")
    sent_thread = (sent.get("thread") or {}).get("name", "") or thread_name
    create_time = sent.get("createTime", "") or datetime.now().astimezone().isoformat()
    space_uri = task["chat_space_uri"]

    with connect_db() as con:
        stored_name = message_name or f"local-sent-{task_id}-{int(time.time())}"
        try:
            con.execute(
                """
                INSERT INTO task_chat_updates
                (task_id,message_name,space_name,space_display_name,sender_display_name,message_text,
                 create_time,match_method,thread_name,space_uri,direction)
                VALUES (?,?,?,?,?,?,?,?,?,?,'outgoing')
                """,
                (task_id, stored_name, space_name, task["party"] or "Google Chat",
                 "Todd / Smart 1", text, create_time, "sent from Action Center",
                 sent_thread, space_uri)
            )
        except sqlite3.IntegrityError:
            pass
        if message_name:
            chat_record_processed(con, message_name, space_name, "outgoing_task_reply")
        con.execute(
            """
            UPDATE tasks
            SET status='Waiting',
                chat_thread_name=CASE WHEN chat_thread_name='' THEN ? ELSE chat_thread_name END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (sent_thread, task_id)
        )
        con.execute(
            "INSERT INTO notes(task_id,body) VALUES(?,?)",
            (task_id, "Google Chat reply sent from Action Center. Task moved to Waiting.")
        )

    review = None
    if message_name:
        review = maybe_create_resolution_review(task_id, "google_chat_sent", message_name, space_uri)

    return jsonify({
        "ok": True,
        "message_name": message_name,
        "thread_name": sent_thread,
        "resolution_assessment": review or {},
    })


@app.post("/api/tasks/<int:task_id>/mark-paid")
@login_required
def mark_task_paid(task_id):
    payload = request.get_json(silent=True) or {}
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Task not found"}), 404
        if task["category"] != "payment":
            return jsonify({"error": "Only payment items can be marked paid."}), 400

        paid_amount = float(payload.get("paid_amount", task["amount"] or 0) or 0)
        reference = (payload.get("payment_reference") or "").strip()
        note = (payload.get("payment_note") or "").strip()

        con.execute(
            """
            UPDATE tasks
            SET completed=1,completed_at=?,status='Completed',paid_at=?,paid_amount=?,
                payment_reference=?,payment_note=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (stamp, stamp, paid_amount, reference, note, task_id)
        )
        summary = f"Marked paid: {paid_amount:.2f} {task['currency'] or 'USD'}"
        if reference:
            summary += f"; reference {reference}"
        if note:
            summary += f"; {note}"
        con.execute("INSERT INTO notes(task_id,body) VALUES(?,?)", (task_id, summary))
    return jsonify({"ok": True, "paid_at": stamp})



@app.post("/api/tasks/<int:task_id>/not-task")
@login_required
def train_live_task_not_task(task_id):
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return jsonify({"error": "Task not found"}), 404
        if task["source_kind"] not in {"gmail", "chat"}:
            return jsonify({"error": "Only Gmail- or Chat-created tasks can train this rule."}), 400

        if task["source_kind"] == "gmail":
            source = con.execute(
                "SELECT * FROM gmail_suggestions WHERE gmail_message_id=? ORDER BY id DESC LIMIT 1",
                (task["gmail_message_id"],)
            ).fetchone()
            sender_name = source["sender_name"] if source else task["party"]
            sender_email = source["sender_email"] if source else task["email_to"]
            subject = source["subject"] if source else task["email_subject"]
            excerpt = (source["snippet"] if source else task["detail"]) or task["detail"]
            source_id = task["gmail_message_id"]
        else:
            source = con.execute(
                "SELECT * FROM chat_suggestions WHERE message_name=? ORDER BY id DESC LIMIT 1",
                (task["chat_message_name"],)
            ).fetchone()
            sender_name = source["sender_display_name"] if source else task["party"]
            sender_email = ""
            subject = (source["suggested_title"] if source else task["title"]) or task["title"]
            excerpt = (source["message_text"] if source else task["detail"]) or task["detail"]
            source_id = task["chat_message_name"]

    store_not_task_training(
        task["source_kind"],
        source_id=source_id or "",
        sender_name=sender_name or "",
        sender_email=sender_email or "",
        subject=subject or task["title"],
        excerpt=excerpt or task["detail"],
        reason="User marked an approved/live task as Not a Task and trained this type.",
    )

    with connect_db() as con:
        con.execute(
            """
            UPDATE tasks
            SET completed=1,completed_at=CURRENT_TIMESTAMP,status='Completed',updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (task_id,)
        )
        con.execute(
            """
            INSERT INTO notes(task_id,body)
            VALUES(?, 'Removed from open tasks and saved as NOT A TASK training for similar future communications.')
            """,
            (task_id,)
        )
        if task["source_kind"] == "gmail" and task["gmail_message_id"]:
            con.execute(
                "UPDATE gmail_suggestions SET state='trained_not_task',updated_at=CURRENT_TIMESTAMP WHERE gmail_message_id=?",
                (task["gmail_message_id"],)
            )
        if task["source_kind"] == "chat" and task["chat_message_name"]:
            con.execute(
                "UPDATE chat_suggestions SET state='trained_not_task',updated_at=CURRENT_TIMESTAMP WHERE message_name=?",
                (task["chat_message_name"],)
            )

    return jsonify({"ok": True, "trained": True})


@app.post("/api/tasks/<int:task_id>/complete")
@login_required
def complete_task(task_id):
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect_db() as con:
        con.execute(
            "UPDATE tasks SET completed=1,completed_at=?,status='Completed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (stamp, task_id)
        )
    return jsonify({"ok": True})


@app.post("/api/tasks/<int:task_id>/restore")
@login_required
def restore_task(task_id):
    with connect_db() as con:
        con.execute(
            "UPDATE tasks SET completed=0,completed_at='',status='Open',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (task_id,)
        )
    return jsonify({"ok": True})


@app.delete("/api/tasks/<int:task_id>")
@login_required
def delete_task(task_id):
    with connect_db() as con:
        con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    return jsonify({"ok": True})


# Gmail draft/send.
def build_reply_message(service, task, to_addr, subject, body):
    message = MIMEText(body, "plain", "utf-8")
    message["To"] = to_addr
    message["Subject"] = subject
    resource = {}
    if task["gmail_message_id"]:
        try:
            original = service.users().messages().get(
                userId="me", id=task["gmail_message_id"], format="metadata", metadataHeaders=["Message-ID"]
            ).execute()
            rfc_id = header_value(original.get("payload", {}).get("headers", []), "Message-ID")
            if rfc_id:
                message["In-Reply-To"] = rfc_id
                message["References"] = rfc_id
            if task["gmail_thread_id"]:
                resource["threadId"] = task["gmail_thread_id"]
        except HttpError:
            pass
    resource["raw"] = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return resource


@app.post("/api/tasks/<int:task_id>/gmail/draft")
@login_required
def create_gmail_draft(task_id):
    service = gmail_service()
    if not service:
        return jsonify({"error": "Gmail is not connected."}), 400
    payload = request.get_json(force=True)
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "Task not found"}), 404
    to_addr = (payload.get("to") or task["email_to"] or "").strip()
    subject = (payload.get("subject") or task["email_subject"] or f"Re: {task['title']}").strip()
    body = (payload.get("body") or "").strip()
    if not to_addr or not body:
        return jsonify({"error": "Recipient and message are required."}), 400
    resource = build_reply_message(service, task, to_addr, subject, body)
    draft = service.users().drafts().create(userId="me", body={"message": resource}).execute()
    return jsonify({"ok": True, "draft_id": draft.get("id", ""), "gmail_drafts_url": "https://mail.google.com/mail/u/0/#drafts"})


@app.post("/api/tasks/<int:task_id>/gmail/send")
@login_required
def send_gmail_reply(task_id):
    service = gmail_service()
    if not service:
        return jsonify({"error": "Gmail is not connected."}), 400
    payload = request.get_json(force=True)
    with connect_db() as con:
        task = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return jsonify({"error": "Task not found"}), 404
    to_addr = (payload.get("to") or task["email_to"] or "").strip()
    subject = (payload.get("subject") or task["email_subject"] or f"Re: {task['title']}").strip()
    body = (payload.get("body") or "").strip()
    if not to_addr or not body:
        return jsonify({"error": "Recipient and message are required."}), 400
    resource = build_reply_message(service, task, to_addr, subject, body)
    sent = service.users().messages().send(userId="me", body=resource).execute()
    with connect_db() as con:
        con.execute("INSERT INTO notes(task_id,body) VALUES(?,?)", (task_id, f"Email sent from Action Center: {subject}"))
        con.execute("UPDATE tasks SET status='Waiting',updated_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
    return jsonify({"ok": True, "message_id": sent.get("id", "")})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
