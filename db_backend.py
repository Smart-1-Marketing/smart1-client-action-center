import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
MIGRATE_SQLITE_TO_POSTGRES = os.environ.get("MIGRATE_SQLITE_TO_POSTGRES", "0") == "1"

try:
    import psycopg
except ImportError:
    psycopg = None


SERIAL_TABLES = {
    "tasks",
    "notes",
    "gmail_suggestions",
    "task_research_logs",
    "task_email_updates",
    "watch_domains",
    "not_task_training",
    "ignore_sources",
    "gpt_help_suppressions",
    "task_participants",
    "task_resolution_reviews",
    "sent_monitors",
    "chat_suggestions",
    "task_chat_updates",
    "meeting_reviews",
    "sync_jobs",
}


class CompatRow:
    """sqlite3.Row-like object supporting both row[0] and row['column']."""

    __slots__ = ("_keys", "_values", "_map")

    def __init__(self, keys, values):
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._map = dict(zip(self._keys, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._keys)

    def items(self):
        return self._map.items()

    def get(self, key, default=None):
        return self._map.get(key, default)

    def __repr__(self):
        return repr(self._map)


def compat_row_factory(cursor):
    columns = [col.name for col in cursor.description]

    def make_row(values):
        return CompatRow(columns, values)

    return make_row


def using_postgres():
    return bool(DATABASE_URL)


def _replace_qmark_placeholders(sql):
    # SQL in this app uses parameters for data values, so '?' tokens in SQL text
    # are placeholders and can be safely translated to psycopg's %s.
    return sql.replace("?", "%s")


def translate_sql(sql):
    """Translate the small SQLite SQL dialect used by this application."""
    statement = sql.strip()
    if not statement:
        return statement

    # SQLite schema syntax -> PostgreSQL identity sequence.
    statement = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        statement,
        flags=re.I,
    )

    # Keep timestamp/date columns as TEXT because the rest of the application
    # intentionally stores/compares ISO date strings.
    statement = re.sub(
        r"\bCURRENT_TIMESTAMP\b",
        "(CURRENT_TIMESTAMP::text)",
        statement,
        flags=re.I,
    )
    statement = re.sub(
        r"\bdate\s*\(\s*'now'\s*\)",
        "(CURRENT_DATE::text)",
        statement,
        flags=re.I,
    )

    ignored_insert = bool(re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", statement, re.I))
    if ignored_insert:
        statement = re.sub(
            r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            statement,
            count=1,
            flags=re.I,
        )
        if "ON CONFLICT" not in statement.upper():
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    return _replace_qmark_placeholders(statement)


class EmptyCursor:
    rowcount = 0
    lastrowid = None

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class PgCursor:
    def __init__(self, connection, cursor, insert_table=""):
        self.connection = connection
        self.cursor = cursor
        self.insert_table = insert_table

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        if not self.insert_table or self.rowcount == 0:
            return None
        if self.insert_table not in SERIAL_TABLES:
            return None
        # currval is connection/session-local and this executes in the same
        # transaction immediately after the INSERT.
        with self.connection.raw.cursor() as c:
            c.execute(
                "SELECT currval(pg_get_serial_sequence(%s, 'id'))",
                (self.insert_table,),
            )
            row = c.fetchone()
            return int(row[0]) if row else None

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class PgConnection:
    def __init__(self):
        if psycopg is None:
            raise RuntimeError(
                "DATABASE_URL is configured but psycopg is not installed. "
                "Install psycopg[binary]."
            )
        self.raw = psycopg.connect(DATABASE_URL, row_factory=compat_row_factory)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.raw.commit()
            else:
                self.raw.rollback()
        finally:
            self.raw.close()
        return False

    def execute(self, sql, params=()):
        stripped = sql.strip()

        # Preserve the app's existing safe-migration helper.
        pragma_match = re.match(
            r"^\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$",
            stripped,
            re.I,
        )
        if pragma_match:
            table = pragma_match.group(1)
            cur = self.raw.cursor()
            cur.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            return PgCursor(self, cur)

        # Other SQLite PRAGMAs are connection-tuning no-ops on PostgreSQL.
        if re.match(r"^\s*PRAGMA\b", stripped, re.I):
            return EmptyCursor()

        translated = translate_sql(sql)
        insert_match = re.match(
            r"^\s*INSERT(?:\s+OR\s+IGNORE)?\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
            sql,
            re.I,
        )
        insert_table = insert_match.group(1).lower() if insert_match else ""

        cur = self.raw.cursor()
        cur.execute(translated, tuple(params or ()))
        return PgCursor(self, cur, insert_table=insert_table)

    def executemany(self, sql, seq_of_params):
        translated = translate_sql(sql)
        cur = self.raw.cursor()
        cur.executemany(translated, seq_of_params)
        return PgCursor(self, cur)

    def executescript(self, script):
        # The application's schema/index scripts contain no procedural blocks,
        # so splitting on semicolons is sufficient.
        result = EmptyCursor()
        for piece in script.split(";"):
            if piece.strip():
                result = self.execute(piece)
        return result


def connect_db(sqlite_path=None):
    if using_postgres():
        return PgConnection()

    path = Path(sqlite_path or os.environ.get("DATA_DIR", "./data")) / "tasks.db"
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA cache_size = -4000")
    con.execute("PRAGMA temp_store = FILE")
    con.execute("PRAGMA mmap_size = 0")
    return con


def _postgres_migration_complete():
    if not using_postgres() or psycopg is None:
        return False
    try:
        with psycopg.connect(DATABASE_URL) as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT value FROM settings WHERE key='sqlite_migration_complete'"
                )
                row = cur.fetchone()
                return bool(row and row[0] == "1")
    except Exception:
        return False


def _set_postgres_marker(key, value):
    with psycopg.connect(DATABASE_URL) as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings(key,value) VALUES(%s,%s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
                """,
                (key, str(value)),
            )


def migration_ready():
    if not using_postgres():
        return True
    return _postgres_migration_complete()


def migrate_sqlite_to_postgres_if_needed(sqlite_path):
    """One-time live-disk -> Render Postgres migration.

    This runs from the WEB service at runtime, because Render persistent disks
    are only accessible by the service instance they are attached to.
    """
    if not using_postgres() or not MIGRATE_SQLITE_TO_POSTGRES:
        return {"needed": False, "reason": "disabled"}

    if psycopg is None:
        raise RuntimeError("psycopg is required for PostgreSQL migration.")

    if _postgres_migration_complete():
        return {"needed": False, "reason": "already_complete"}

    sqlite_path = Path(sqlite_path)
    if not sqlite_path.exists() or sqlite_path.stat().st_size == 0:
        raise RuntimeError(
            f"PostgreSQL migration requested but SQLite file was not found at {sqlite_path}. "
            "Keep the Render disk attached for the first PostgreSQL deployment."
        )

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    try:
        sqlite_tables = [
            r["name"]
            for r in src.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]

        # Parent tables before children.
        preferred_order = [
            "tasks",
            "settings",
            "gmail_processed",
            "gmail_suggestions",
            "chat_processed",
            "chat_suggestions",
            "sent_monitors",
            "meeting_reviews",
            "watch_domains",
            "not_task_training",
            "ignore_sources",
            "gpt_help_suppressions",
            "notes",
            "task_research_logs",
            "task_email_updates",
            "task_chat_updates",
            "task_participants",
            "task_resolution_reviews",
        ]
        ordered = [t for t in preferred_order if t in sqlite_tables]
        ordered += [t for t in sqlite_tables if t not in ordered]

        migrated_counts = {}

        with psycopg.connect(DATABASE_URL) as dst:
            with dst.cursor() as cur:
                # Determine which destination tables actually exist.
                cur.execute(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema='public'
                    """
                )
                pg_tables = {r[0] for r in cur.fetchall()}

                data_tables = [t for t in ordered if t in pg_tables and t != "sync_jobs"]

                if data_tables:
                    quoted = ", ".join(f'"{t}"' for t in data_tables)
                    cur.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")

                for table in data_tables:
                    src_cols = [
                        r["name"]
                        for r in src.execute(f'PRAGMA table_info("{table}")').fetchall()
                    ]
                    cur.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=%s
                        ORDER BY ordinal_position
                        """,
                        (table,),
                    )
                    pg_cols = [r[0] for r in cur.fetchall()]
                    cols = [c for c in src_cols if c in pg_cols]
                    if not cols:
                        continue

                    rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
                    if not rows:
                        migrated_counts[table] = 0
                        continue

                    col_sql = ", ".join(f'"{c}"' for c in cols)
                    placeholders = ", ".join(["%s"] * len(cols))
                    insert_sql = (
                        f'INSERT INTO "{table}" ({col_sql}) '
                        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                    )
                    values = [tuple(row[c] for c in cols) for row in rows]
                    cur.executemany(insert_sql, values)
                    migrated_counts[table] = len(values)

                # Reset serial sequences after preserving SQLite IDs.
                for table in SERIAL_TABLES:
                    if table not in pg_tables:
                        continue
                    cur.execute(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=%s AND column_name='id'
                        """,
                        (table,),
                    )
                    if not cur.fetchone():
                        continue
                    cur.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"')
                    max_id = int(cur.fetchone()[0] or 0)
                    cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
                    seq = cur.fetchone()[0]
                    if seq:
                        cur.execute(
                            "SELECT setval(%s, %s, %s)",
                            (seq, max(max_id, 1), max_id > 0),
                        )

                now = datetime.now(timezone.utc).isoformat()
                cur.execute(
                    """
                    INSERT INTO settings(key,value) VALUES('sqlite_migration_complete','1')
                    ON CONFLICT(key) DO UPDATE SET value='1'
                    """
                )
                cur.execute(
                    """
                    INSERT INTO settings(key,value) VALUES('sqlite_migrated_at',%s)
                    ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
                    """,
                    (now,),
                )
                cur.execute(
                    """
                    INSERT INTO settings(key,value) VALUES('sqlite_migration_counts',%s)
                    ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
                    """,
                    (str(migrated_counts),),
                )

        return {
            "needed": True,
            "completed": True,
            "tables": migrated_counts,
        }
    finally:
        src.close()
