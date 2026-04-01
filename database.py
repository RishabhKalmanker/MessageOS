import sqlite3
import os
from datetime import datetime

DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/messageos.db")


def get_connection():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            tier TEXT NOT NULL DEFAULT 'normal',
            avg_response_minutes REAL,
            reply_probability REAL DEFAULT 50,
            health_score REAL DEFAULT 50,
            last_contacted TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone) WHERE phone IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email) WHERE email IS NOT NULL;

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER REFERENCES contacts(id),
            source TEXT NOT NULL DEFAULT 'imessage',
            direction TEXT NOT NULL,
            body TEXT,
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            replied_at TEXT,
            sla_tier TEXT,
            sla_started_at TEXT,
            sla_breached INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sla_clocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER REFERENCES messages(id),
            contact_id INTEGER REFERENCES contacts(id),
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            paused_at TEXT,
            paused_duration_seconds REAL NOT NULL DEFAULT 0,
            breached INTEGER NOT NULL DEFAULT 0,
            escalation_sent INTEGER NOT NULL DEFAULT 0,
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER REFERENCES contacts(id),
            called_at TEXT NOT NULL DEFAULT (datetime('now')),
            vibe TEXT,
            followup_needed INTEGER NOT NULL DEFAULT 0,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS commitments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER REFERENCES contacts(id),
            source_message_id INTEGER REFERENCES messages(id),
            description TEXT NOT NULL,
            due_at TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Initialized database at {DATABASE_PATH}")
