"""
database.py — psycopg2 connection helper + schema init for Supabase PostgreSQL.
"""
import os
import re
import logging
import psycopg2
import psycopg2.extras
from urllib.parse import unquote

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    os.getenv("SUPABASE_URL", ""),  # fall back to SUPABASE_URL for compatibility
)

# Regex that captures user, raw password, host, optional port, and dbname from
# a PostgreSQL URL without percent-decoding the password component.
# psycopg2's own URL parser rejects passwords that contain literal '%' (e.g.
# "Met%hum%n12345") because it tries to interpret them as percent-encoded bytes.
# Extracting the parts ourselves and connecting via keyword arguments sidesteps
# that restriction entirely.
_URL_RE = re.compile(
    r"postgres(?:ql)?://([^:@]+):(.+)@([^:/]+)(?::(\d+))?/([^?]+)"
)


def get_connection() -> psycopg2.extensions.connection:
    """
    Open and return a new psycopg2 connection using RealDictCursor so
    every row is accessible as a plain dict (same interface the rest of
    the code expects).

    Parses DATABASE_URL with a regex instead of delegating to psycopg2's
    URL parser so that passwords containing literal '%' characters are
    handled correctly.
    """
    m = _URL_RE.match(DATABASE_URL)
    if not m:
        # Last resort: pass the URL directly and hope for the best
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

    user, password, host, port, dbname = m.groups()
    return psycopg2.connect(
        host=host,
        port=int(port or 5432),
        user=unquote(user),
        password=password,   # intentionally NOT unquoted — contains literal %
        dbname=unquote(dbname),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def init_db():
    """Create all tables if they don't already exist."""
    conn = get_connection()
    cur = conn.cursor()

    statements = [
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id               SERIAL PRIMARY KEY,
            name             TEXT NOT NULL,
            phone            TEXT,
            email            TEXT,
            tier             TEXT NOT NULL DEFAULT 'normal',
            avg_response_minutes REAL,
            reply_probability    REAL DEFAULT 50,
            health_score         REAL DEFAULT 50,
            last_contacted       TIMESTAMPTZ,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        # Partial unique indexes mirror the SQLite behaviour: NULLs are allowed
        # to repeat (each contact may lack a phone or email), but non-NULL
        # values must be unique.
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_phone
            ON contacts(phone) WHERE phone IS NOT NULL
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email
            ON contacts(email) WHERE email IS NOT NULL
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id            SERIAL PRIMARY KEY,
            contact_id    INTEGER REFERENCES contacts(id),
            source        TEXT NOT NULL DEFAULT 'imessage',
            direction     TEXT NOT NULL,
            body          TEXT,
            received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            replied_at    TIMESTAMPTZ,
            sla_tier      TEXT,
            sla_started_at TIMESTAMPTZ,
            sla_breached  BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sla_clocks (
            id               SERIAL PRIMARY KEY,
            message_id       INTEGER REFERENCES messages(id),
            contact_id       INTEGER REFERENCES contacts(id),
            started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            paused_at        TIMESTAMPTZ,
            paused_duration_seconds REAL NOT NULL DEFAULT 0,
            breached         BOOLEAN DEFAULT FALSE,
            escalation_sent  BOOLEAN DEFAULT FALSE,
            closed_at        TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS call_logs (
            id              SERIAL PRIMARY KEY,
            contact_id      INTEGER REFERENCES contacts(id),
            called_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            vibe            TEXT,
            followup_needed BOOLEAN DEFAULT FALSE,
            note            TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS commitments (
            id                SERIAL PRIMARY KEY,
            contact_id        INTEGER REFERENCES contacts(id),
            source_message_id INTEGER REFERENCES messages(id),
            description       TEXT NOT NULL,
            due_at            TIMESTAMPTZ,
            resolved          BOOLEAN DEFAULT FALSE,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ]

    for stmt in statements:
        cur.execute(stmt)

    conn.commit()
    conn.close()
    logger.info("[DB] Supabase PostgreSQL — tables verified/created")
