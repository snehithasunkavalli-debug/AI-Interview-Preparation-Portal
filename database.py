"""
Database abstraction module for AI Interview Preparation Portal (Dossier).
Provides seamless support for persistent PostgreSQL (Neon, Supabase, etc.)
and local disk SQLite for local development.

Strictly enforces database persistence: In production or Vercel serverless
environments, ephemeral SQLite fallback (/tmp) is BLOCKED to prevent silent data loss.
"""

import os
import re
import datetime
import urllib.parse
from werkzeug.security import generate_password_hash, check_password_hash

# Try importing PostgreSQL driver
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    PSYCOPG2_AVAILABLE = False

import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def is_vercel():
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

def is_production():
    return is_vercel() or os.environ.get("FLASK_ENV") == "production" or os.environ.get("ENV") == "production"

# Configured storage and database URL
def get_storage_dir():
    if is_vercel():
        default_storage = "/tmp/storage"
    else:
        default_storage = os.path.join(BASE_DIR, "storage")
    return os.environ.get("STORAGE_DIR", default_storage)

STORAGE_DIR = get_storage_dir()
DB_PATH = os.path.join(STORAGE_DIR, "portal.db")


def normalize_database_url(raw_url: str) -> str:
    """
    Normalizes database URL for psycopg2:
    - Replaces postgres:// with postgresql://
    - Enforces sslmode=require for remote cloud databases (Neon, Supabase)
    """
    if not raw_url:
        return ""
    
    url = raw_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
        
    try:
        parsed = urllib.parse.urlparse(url)
        # Check if remote host and sslmode not set
        if parsed.hostname and parsed.hostname not in ("localhost", "127.0.0.1"):
            query_params = urllib.parse.parse_qs(parsed.query)
            if "sslmode" not in query_params:
                delimiter = "&" if parsed.query else "?"
                url = f"{url}{delimiter}sslmode=require"
    except Exception:
        pass
        
    return url


def get_database_backend():
    """
    Determines the active database backend:
    - 'postgresql': If DATABASE_URL is set and psycopg2 is available
    - 'unconfigured': If in production/Vercel and DATABASE_URL is NOT set (ephemeral SQLite blocked!)
    - 'sqlite': In local development mode when DATABASE_URL is not set
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return "postgresql"
    elif is_production() or is_vercel():
        return "unconfigured"
    else:
        return "sqlite"


def adapt_sql(sql: str, db_type: str) -> str:
    """
    Translates SQL query syntax between SQLite and PostgreSQL:
    - Translates '?' placeholders to '%s' for PostgreSQL/psycopg2.
    - Preserves '?' inside quoted string literals.
    - Adapts 'INSERT OR REPLACE INTO' to PostgreSQL ON CONFLICT if needed.
    """
    if db_type != "postgresql":
        return sql

    tokens = []
    in_quote = False
    quote_char = ""
    i = 0
    n = len(sql)
    
    while i < n:
        ch = sql[i]
        if not in_quote:
            if ch in ("'", '"'):
                in_quote = True
                quote_char = ch
                tokens.append(ch)
            elif ch == "?":
                tokens.append("%s")
            else:
                tokens.append(ch)
        else:
            tokens.append(ch)
            if ch == quote_char:
                if i + 1 < n and sql[i + 1] == quote_char:
                    tokens.append(quote_char)
                    i += 1
                else:
                    in_quote = False
        i += 1
        
    adapted = "".join(tokens)
    
    # Handle legacy INSERT OR REPLACE for PostgreSQL
    if "INSERT OR REPLACE INTO" in adapted.upper():
        adapted = re.sub(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+app_settings\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)",
            r"INSERT INTO app_settings (\1) VALUES (\2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            adapted,
            flags=re.IGNORECASE
        )
        
    return adapted


class DBCursor:
    """
    Unified cursor wrapper providing consistent dictionary/row access
    and lastrowid handling across PostgreSQL and SQLite.
    """
    def __init__(self, raw_cursor, db_type: str):
        self._cursor = raw_cursor
        self.db_type = db_type
        self.lastrowid = None
        self._returned_row = None

    def execute(self, sql: str, params=None):
        adapted = adapt_sql(sql, self.db_type)
        if params is None:
            self._cursor.execute(adapted)
        else:
            if isinstance(params, list):
                params = tuple(params)
            self._cursor.execute(adapted, params)
            
        # If query has RETURNING id or SQLite generated lastrowid:
        if self.db_type == "postgresql":
            if "RETURNING" in adapted.upper() and ("ID" in adapted.upper() or "REPORT_ID" in adapted.upper()):
                try:
                    row = self._cursor.fetchone()
                    if row:
                        self.lastrowid = row[0]
                        self._returned_row = row
                except Exception:
                    pass
        else:
            if hasattr(self._cursor, "lastrowid"):
                self.lastrowid = self._cursor.lastrowid
                
        return self

    def fetchone(self):
        if self._returned_row is not None:
            row = self._returned_row
            self._returned_row = None
            return row
        return self._cursor.fetchone()

    def fetchall(self):
        if self._returned_row is not None:
            first = self._returned_row
            self._returned_row = None
            rest = self._cursor.fetchall()
            return [first] + rest
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __iter__(self):
        return iter(self.fetchall())


class DBConnection:
    """
    Unified database connection wrapper supporting both psycopg2 and sqlite3.
    """
    def __init__(self, raw_conn, db_type: str):
        self._conn = raw_conn
        self.db_type = db_type
        self._closed = False

    def cursor(self):
        return DBCursor(self._conn.cursor(), self.db_type)

    def commit(self):
        if not self._closed:
            self._conn.commit()

    def rollback(self):
        if not self._closed:
            try:
                self._conn.rollback()
            except Exception:
                pass

    def close(self):
        if not self._closed:
            try:
                self._conn.close()
            except Exception:
                pass
            self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def create_connection():
    """
    Creates a new connection based on current environment configuration.
    Raises RuntimeError if database is unconfigured in production.
    """
    backend = get_database_backend()
    
    if backend == "postgresql":
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "psycopg2-binary is required for PostgreSQL connection. "
                "Please run: pip install psycopg2-binary"
            )
        raw_url = os.environ.get("DATABASE_URL", "").strip()
        normalized_url = normalize_database_url(raw_url)
        raw_conn = psycopg2.connect(
            normalized_url, 
            cursor_factory=psycopg2.extras.DictCursor,
            connect_timeout=10
        )
        return DBConnection(raw_conn, "postgresql")
        
    elif backend == "unconfigured":
        raise RuntimeError(
            "CRITICAL PERSISTENCE CONFIGURATION ERROR: A persistent PostgreSQL DATABASE_URL "
            "is required in production (Vercel serverless environment)! Ephemeral SQLite storage "
            "causes silent data loss across serverless container restarts. "
            "Please configure DATABASE_URL in your Vercel Project Settings -> Environment Variables "
            "using your Neon or Supabase connection string."
        )
        
    else: # sqlite
        os.makedirs(STORAGE_DIR, exist_ok=True)
        raw_conn = sqlite3.connect(DB_PATH, timeout=10)
        raw_conn.row_factory = sqlite3.Row
        return DBConnection(raw_conn, "sqlite")


# Schema definitions
PG_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(255) UNIQUE NOT NULL,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name VARCHAR(255),
        login_count INTEGER DEFAULT 1,
        has_rated INTEGER DEFAULT 0,
        rating INTEGER DEFAULT 0,
        rating_comment TEXT,
        rated_at TIMESTAMP,
        is_banned INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS cv_reports (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        filename TEXT NOT NULL,
        saved_filepath TEXT NOT NULL,
        candidate_name TEXT,
        candidate_email TEXT,
        candidate_phone TEXT,
        experience_level TEXT,
        years_exp REAL,
        readiness_score INTEGER,
        target_role TEXT,
        analysis_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS interview_sessions (
        session_id VARCHAR(255) PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        cv_id INTEGER REFERENCES cv_reports(id) ON DELETE SET NULL,
        candidate_name TEXT,
        target_role TEXT,
        status VARCHAR(50) DEFAULT 'in_progress',
        total_questions INTEGER DEFAULT 5,
        current_question_index INTEGER DEFAULT 0,
        overall_score REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        questions_json TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS interview_messages (
        id SERIAL PRIMARY KEY,
        session_id VARCHAR(255) NOT NULL REFERENCES interview_sessions(session_id) ON DELETE CASCADE,
        role VARCHAR(50) NOT NULL,
        question_index INTEGER,
        content TEXT NOT NULL,
        feedback_json TEXT,
        score REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key VARCHAR(255) PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version VARCHAR(50) PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_cv_reports_user_id ON cv_reports(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_interview_sessions_user_id ON interview_sessions(user_id);"
]

SQLITE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        login_count INTEGER DEFAULT 1,
        has_rated INTEGER DEFAULT 0,
        rating INTEGER DEFAULT 0,
        rating_comment TEXT,
        rated_at TIMESTAMP,
        is_banned INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS cv_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        filename TEXT NOT NULL,
        saved_filepath TEXT NOT NULL,
        candidate_name TEXT,
        candidate_email TEXT,
        candidate_phone TEXT,
        experience_level TEXT,
        years_exp REAL,
        readiness_score INTEGER,
        target_role TEXT,
        analysis_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS interview_sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER,
        cv_id INTEGER,
        candidate_name TEXT,
        target_role TEXT,
        status TEXT DEFAULT 'in_progress',
        total_questions INTEGER DEFAULT 5,
        current_question_index INTEGER DEFAULT 0,
        overall_score REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        questions_json TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(cv_id) REFERENCES cv_reports(id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS interview_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        question_index INTEGER,
        content TEXT NOT NULL,
        feedback_json TEXT,
        score REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(session_id) REFERENCES interview_sessions(session_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
]


def init_db(allow_unconfigured_in_prod=False):
    """
    Safely initializes tables, schema migrations, and admin provisioning.
    Returns a dict with status information.
    """
    backend = get_database_backend()
    
    if backend == "unconfigured":
        msg = (
            "CRITICAL PERSISTENCE WARNING: DATABASE_URL is not set in production/Vercel. "
            "Ephemeral SQLite fallback is blocked to prevent data loss. "
            "Set DATABASE_URL in Vercel project environment variables."
        )
        print(f"[!] {msg}")
        return {
            "success": False,
            "backend": "unconfigured",
            "error": msg,
            "persistent": False
        }

    conn = create_connection()
    cursor = conn.cursor()
    
    try:
        statements = PG_SCHEMA if conn.db_type == "postgresql" else SQLITE_SCHEMA
        for stmt in statements:
            cursor.execute(stmt)
            
        # Record baseline schema migration
        cursor.execute("SELECT version FROM schema_migrations WHERE version = ?", ("001_initial_schema",))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO schema_migrations (version) VALUES (?)", ("001_initial_schema",))

        # Secure Administrator Provisioning
        admin_username = os.environ.get("ADMIN_USERNAME", "admin").strip()
        admin_password = os.environ.get("ADMIN_PASSWORD")
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@dossier.portal").strip()

        cursor.execute("SELECT id FROM users WHERE username = ?", (admin_username,))
        existing_admin = cursor.fetchone()

        if admin_password and admin_password.strip():
            pwd_hash = generate_password_hash(admin_password.strip())
            if existing_admin:
                admin_id = existing_admin[0] if isinstance(existing_admin, tuple) else existing_admin["id"]
                cursor.execute(
                    "UPDATE users SET password_hash = ?, is_admin = 1 WHERE id = ?",
                    (pwd_hash, admin_id)
                )
                print(f"[*] Admin user '{admin_username}' credentials synchronized from environment.")
            else:
                cursor.execute(
                    "INSERT INTO users (username, email, password_hash, full_name, is_admin) "
                    "VALUES (?, ?, ?, 'Portal Administrator', 1)",
                    (admin_username, admin_email, pwd_hash)
                )
                print(f"[*] Secure admin user '{admin_username}' provisioned from environment variable.")
        elif not is_production() and not existing_admin:
            # Local development mode: seed dev credentials for out-of-the-box local testing
            dev_pwd = os.environ.get("DEV_ADMIN_PASSWORD", "aiint")
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, full_name, is_admin) "
                "VALUES ('admin', 'admin@dossier.portal', ?, 'Portal Administrator (Local Dev)', 1)",
                (generate_password_hash(dev_pwd),)
            )
            print("[*] [LOCAL DEV] Seeded local admin account. (Note: In production, configure ADMIN_PASSWORD).")
        elif is_production() and not existing_admin:
            print("[!] [PRODUCTION NOTICE] No ADMIN_PASSWORD provided. Default insecure admin creation was skipped for security.")

        conn.commit()
        return {
            "success": True,
            "backend": conn.db_type,
            "persistent": True,
            "error": None
        }
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_db_health():
    """
    Returns comprehensive database health and persistence metrics.
    Sanitizes database URLs to hide credentials.
    """
    backend = get_database_backend()
    
    if backend == "unconfigured":
        return {
            "database_type": "none",
            "database_configured": False,
            "persistent_database": False,
            "storage_mode": "unconfigured_persistent_db",
            "is_ephemeral": False,
            "connected": False,
            "error": "DATABASE_URL is not configured in production. Ephemeral SQLite storage is blocked to prevent data loss. Configure DATABASE_URL in Vercel project settings.",
            "database_host": None
        }
        
    elif backend == "postgresql":
        raw_url = os.environ.get("DATABASE_URL", "").strip()
        sanitized_host = "remote_postgres"
        try:
            parsed = urllib.parse.urlparse(raw_url)
            sanitized_host = f"{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
        except Exception:
            pass
            
        is_connected = False
        conn_err = None
        try:
            conn = create_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            is_connected = True
            conn.close()
        except Exception as e:
            conn_err = str(e)
            
        return {
            "database_type": "postgresql",
            "database_configured": True,
            "persistent_database": True,
            "storage_mode": "persistent_cloud_postgres",
            "is_ephemeral": False,
            "connected": is_connected,
            "error": conn_err,
            "database_host": sanitized_host
        }
        
    else: # sqlite
        is_connected = False
        conn_err = None
        try:
            conn = create_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            is_connected = True
            conn.close()
        except Exception as e:
            conn_err = str(e)
            
        return {
            "database_type": "sqlite",
            "database_configured": True,
            "persistent_database": True, # Persistent on local disk
            "storage_mode": "local_disk",
            "is_ephemeral": False,
            "connected": is_connected,
            "error": conn_err,
            "database_path": DB_PATH
        }
