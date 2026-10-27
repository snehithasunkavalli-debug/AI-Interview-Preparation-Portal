import os
import pytest
import sqlite3
import database


def test_sql_adapter_placeholder_translation():
    """
    Test adapt_sql translates '?' to '%s' for PostgreSQL while preserving string literals.
    """
    # Simple query
    q1 = "SELECT * FROM users WHERE username = ? OR email = ?"
    adapted1 = database.adapt_sql(q1, "postgresql")
    assert adapted1 == "SELECT * FROM users WHERE username = %s OR email = %s"

    # Query with ? inside quotes
    q2 = "SELECT * FROM users WHERE note = 'Is this a question?' AND id = ?"
    adapted2 = database.adapt_sql(q2, "postgresql")
    assert adapted2 == "SELECT * FROM users WHERE note = 'Is this a question?' AND id = %s"

    # SQLite query unmodified
    adapted_sqlite = database.adapt_sql(q1, "sqlite")
    assert adapted_sqlite == q1


def test_sql_adapter_insert_or_replace():
    """
    Test adapt_sql converts legacy INSERT OR REPLACE to ON CONFLICT for PostgreSQL.
    """
    q = "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)"
    adapted = database.adapt_sql(q, "postgresql")
    assert "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value" in adapted
    assert "%s, %s" in adapted


def test_database_url_normalization():
    """
    Test normalize_database_url converts postgres:// to postgresql:// and adds sslmode=require.
    """
    raw1 = "postgres://user:pass@ep-host.neon.tech/neondb"
    norm1 = database.normalize_database_url(raw1)
    assert norm1.startswith("postgresql://")
    assert "sslmode=require" in norm1

    # Localhost doesn't force sslmode
    raw2 = "postgresql://user:pass@localhost:5432/testdb"
    norm2 = database.normalize_database_url(raw2)
    assert "sslmode" not in norm2


def test_ephemeral_fallback_blocked_in_production(monkeypatch):
    """
    CRITICAL: Verify that when running in production/Vercel without DATABASE_URL,
    the application refuses to fall back to ephemeral SQLite storage.
    """
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    backend = database.get_database_backend()
    assert backend == "unconfigured"

    health = database.get_db_health()
    assert health["database_configured"] is False
    assert health["persistent_database"] is False
    assert health["is_ephemeral"] is False
    assert health["storage_mode"] == "unconfigured_persistent_db"
    assert "blocked" in health["error"]

    with pytest.raises(RuntimeError) as exc_info:
        database.create_connection()
    assert "CRITICAL PERSISTENCE CONFIGURATION ERROR" in str(exc_info.value)


def test_schema_initialization_idempotent(tmp_path):
    """
    Verify init_db can be called repeatedly without errors or data destruction.
    """
    test_db = str(tmp_path / "idempotent.db")
    database.DB_PATH = test_db
    database.STORAGE_DIR = str(tmp_path)

    res1 = database.init_db()
    assert res1["success"] is True

    # Seed a row
    conn = database.create_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
        ("idempotent_user", "idem@example.org", "dummy_hash")
    )
    conn.commit()
    conn.close()

    # Re-run init_db (should be safe and preserve data)
    res2 = database.init_db()
    assert res2["success"] is True

    conn2 = database.create_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT username FROM users WHERE username = ?", ("idempotent_user",))
    assert cur2.fetchone()["username"] == "idempotent_user"
    conn2.close()
