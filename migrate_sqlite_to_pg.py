"""
Migration Utility: SQLite to Persistent PostgreSQL (Neon / Supabase)
Transfers user accounts, passwords, CV reports, interview sessions, and settings
from local SQLite (storage/portal.db) to persistent PostgreSQL.

Usage:
  python migrate_sqlite_to_pg.py [--url DATABASE_URL] [--sqlite-path PATH]
"""

import os
import sys
import sqlite3
import argparse
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("[!] Error: psycopg2-binary is not installed. Please run:")
    print("    pip install psycopg2-binary")
    sys.exit(1)

from database import normalize_database_url, PG_SCHEMA


def migrate(sqlite_path="storage/portal.db", pg_url=None):
    if not pg_url:
        pg_url = os.environ.get("DATABASE_URL")
        
    if not pg_url:
        print("=" * 70)
        print("[!] ERROR: No PostgreSQL DATABASE_URL provided!")
        print("=" * 70)
        print("Please configure DATABASE_URL in your .env file or pass --url:")
        print("Example for Neon:")
        print("  DATABASE_URL=postgresql://user:pass@ep-host.neon.tech/neondb?sslmode=require")
        print("\nExample for Supabase:")
        print("  DATABASE_URL=postgresql://postgres:pass@db.xxxx.supabase.co:5432/postgres?sslmode=require")
        print("\nThen run:")
        print("  python migrate_sqlite_to_pg.py --url <DATABASE_URL>")
        print("=" * 70)
        return False

    if not os.path.isfile(sqlite_path):
        print(f"[!] Warning: SQLite database file not found at: {sqlite_path}")
        print("    If you don't have existing local data to migrate, tables will be initialized empty.")
        sqlite_conn = None
    else:
        print(f"[*] Opening SQLite database from: {sqlite_path}")
        sqlite_conn = sqlite3.connect(sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row

    normalized_url = normalize_database_url(pg_url)
    try:
        parsed = urllib.parse.urlparse(normalized_url)
        print(f"[*] Connecting to PostgreSQL host: {parsed.hostname} (database: {parsed.path.lstrip('/')})")
        pg_conn = psycopg2.connect(normalized_url, cursor_factory=psycopg2.extras.DictCursor)
    except Exception as e:
        print(f"[!] Failed to connect to PostgreSQL: {e}")
        return False

    pg_cursor = pg_conn.cursor()

    try:
        # Step 1: Ensure Schema Exists
        print("[*] Initializing PostgreSQL schema tables and indexes...")
        for stmt in PG_SCHEMA:
            pg_cursor.execute(stmt)
        pg_conn.commit()
        print("[+] Schema initialized successfully.")

        if not sqlite_conn:
            print("[*] No SQLite database to migrate. Schema is ready in PostgreSQL.")
            pg_conn.close()
            return True

        sqlite_cursor = sqlite_conn.cursor()

        # Step 2: Migrate Users
        print("[*] Migrating 'users' table...")
        sqlite_cursor.execute("SELECT * FROM users ORDER BY id ASC")
        users = sqlite_cursor.fetchall()
        migrated_users = 0
        for u in users:
            u_dict = dict(u)
            pg_cursor.execute("""
            INSERT INTO users (
                id, username, email, password_hash, full_name, login_count, 
                has_rated, rating, rating_comment, rated_at, is_banned, is_admin, created_at
            ) VALUES (
                %(id)s, %(username)s, %(email)s, %(password_hash)s, %(full_name)s, %(login_count)s,
                %(has_rated)s, %(rating)s, %(rating_comment)s, %(rated_at)s, %(is_banned)s, %(is_admin)s, %(created_at)s
            )
            ON CONFLICT (username) DO UPDATE SET
                email = EXCLUDED.email,
                password_hash = EXCLUDED.password_hash,
                full_name = EXCLUDED.full_name,
                login_count = EXCLUDED.login_count,
                has_rated = EXCLUDED.has_rated,
                rating = EXCLUDED.rating,
                rating_comment = EXCLUDED.rating_comment,
                rated_at = EXCLUDED.rated_at,
                is_banned = EXCLUDED.is_banned,
                is_admin = EXCLUDED.is_admin
            """, u_dict)
            migrated_users += 1

        # Advance users serial sequence
        pg_cursor.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), COALESCE(MAX(id), 1)) FROM users")
        pg_conn.commit()
        print(f"[+] Migrated {migrated_users} user account(s) to PostgreSQL.")

        # Step 3: Migrate CV Reports
        print("[*] Migrating 'cv_reports' table...")
        sqlite_cursor.execute("SELECT * FROM cv_reports ORDER BY id ASC")
        reports = sqlite_cursor.fetchall()
        migrated_reports = 0
        for r in reports:
            r_dict = dict(r)
            pg_cursor.execute("""
            INSERT INTO cv_reports (
                id, user_id, filename, saved_filepath, candidate_name, 
                candidate_email, candidate_phone, experience_level, years_exp, 
                readiness_score, target_role, analysis_json, created_at
            ) VALUES (
                %(id)s, %(user_id)s, %(filename)s, %(saved_filepath)s, %(candidate_name)s,
                %(candidate_email)s, %(candidate_phone)s, %(experience_level)s, %(years_exp)s,
                %(readiness_score)s, %(target_role)s, %(analysis_json)s, %(created_at)s
            )
            ON CONFLICT (id) DO NOTHING
            """, r_dict)
            migrated_reports += 1

        # Advance cv_reports serial sequence
        pg_cursor.execute("SELECT setval(pg_get_serial_sequence('cv_reports', 'id'), COALESCE(MAX(id), 1)) FROM cv_reports")
        pg_conn.commit()
        print(f"[+] Migrated {migrated_reports} CV report(s) to PostgreSQL.")

        # Step 4: Migrate Interview Sessions
        print("[*] Migrating 'interview_sessions' table...")
        sqlite_cursor.execute("SELECT * FROM interview_sessions ORDER BY created_at ASC")
        sessions = sqlite_cursor.fetchall()
        migrated_sessions = 0
        for s in sessions:
            s_dict = dict(s)
            pg_cursor.execute("""
            INSERT INTO interview_sessions (
                session_id, user_id, cv_id, candidate_name, target_role, status,
                total_questions, current_question_index, overall_score, created_at,
                completed_at, questions_json
            ) VALUES (
                %(session_id)s, %(user_id)s, %(cv_id)s, %(candidate_name)s, %(target_role)s, %(status)s,
                %(total_questions)s, %(current_question_index)s, %(overall_score)s, %(created_at)s,
                %(completed_at)s, %(questions_json)s
            )
            ON CONFLICT (session_id) DO NOTHING
            """, s_dict)
            migrated_sessions += 1
        pg_conn.commit()
        print(f"[+] Migrated {migrated_sessions} interview session(s).")

        # Step 5: Migrate Interview Messages
        print("[*] Migrating 'interview_messages' table...")
        sqlite_cursor.execute("SELECT * FROM interview_messages ORDER BY id ASC")
        messages = sqlite_cursor.fetchall()
        migrated_messages = 0
        for m in messages:
            m_dict = dict(m)
            pg_cursor.execute("""
            INSERT INTO interview_messages (
                id, session_id, role, question_index, content, feedback_json, score, created_at
            ) VALUES (
                %(id)s, %(session_id)s, %(role)s, %(question_index)s, %(content)s, %(feedback_json)s, %(score)s, %(created_at)s
            )
            ON CONFLICT (id) DO NOTHING
            """, m_dict)
            migrated_messages += 1

        if migrated_messages > 0:
            pg_cursor.execute("SELECT setval(pg_get_serial_sequence('interview_messages', 'id'), COALESCE(MAX(id), 1)) FROM interview_messages")
        pg_conn.commit()
        print(f"[+] Migrated {migrated_messages} interview message(s).")

        # Step 6: Migrate App Settings
        print("[*] Migrating 'app_settings' table...")
        sqlite_cursor.execute("SELECT * FROM app_settings")
        settings = sqlite_cursor.fetchall()
        migrated_settings = 0
        for st in settings:
            st_dict = dict(st)
            pg_cursor.execute("""
            INSERT INTO app_settings (key, value) VALUES (%(key)s, %(value)s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, st_dict)
            migrated_settings += 1
        pg_conn.commit()
        print(f"[+] Migrated {migrated_settings} app setting(s).")

        # Verification
        print("=" * 70)
        print("[*] MIGRATION VERIFICATION:")
        pg_cursor.execute("SELECT COUNT(*) FROM users")
        pg_users = pg_cursor.fetchone()[0]
        pg_cursor.execute("SELECT COUNT(*) FROM cv_reports")
        pg_reports = pg_cursor.fetchone()[0]
        print(f"    Total Users in PostgreSQL:      {pg_users}")
        print(f"    Total CV Reports in PostgreSQL: {pg_reports}")
        print("[+] SUCCESS: Data migration completed successfully!")
        print("=" * 70)

        return True

    except Exception as e:
        pg_conn.rollback()
        print(f"[!] Migration failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if sqlite_conn:
            sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL")
    parser.add_argument("--url", default=None, help="PostgreSQL connection URL (or set DATABASE_URL)")
    parser.add_argument("--sqlite-path", default="storage/portal.db", help="Path to source SQLite file")
    args = parser.parse_args()

    success = migrate(sqlite_path=args.sqlite_path, pg_url=args.url)
    sys.exit(0 if success else 1)
