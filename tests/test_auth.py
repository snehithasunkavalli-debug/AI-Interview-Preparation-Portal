import os
import json
import pytest
import tempfile
import sqlite3

# Set test environment
os.environ["FLASK_ENV"] = "development"
os.environ.pop("VERCEL", None)
os.environ.pop("VERCEL_ENV", None)
os.environ.pop("DATABASE_URL", None)

from app import app, get_db
import database


@pytest.fixture
def client(tmp_path):
    # Configure temporary test storage
    test_db = str(tmp_path / "test_portal.db")
    database.DB_PATH = test_db
    database.STORAGE_DIR = str(tmp_path)
    
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key_for_unit_tests_only_32_chars"
    
    # Initialize clean database
    database.init_db()

    with app.test_client() as client:
        yield client


def test_registration_followed_by_login_across_requests(client):
    """
    Test registering a user, then logging in via a separate request,
    and verifying persistent session across separate requests.
    """
    # Request 1: Register new candidate
    reg_payload = {
        "username": "alex_candidate",
        "email": "alex@example.org",
        "password": "CorrectPassword123!",
        "full_name": "Alex Candidate"
    }
    reg_res = client.post("/api/auth/register", json=reg_payload)
    assert reg_res.status_code == 200
    reg_data = reg_res.get_json()
    assert reg_data["success"] is True
    assert reg_data["user"]["username"] == "alex_candidate"
    assert reg_data["user"]["email"] == "alex@example.org"
    user_id = reg_data["user"]["id"]
    assert user_id > 0

    # Request 2: Log out to clear session
    logout_res = client.post("/api/auth/logout")
    assert logout_res.status_code == 200

    # Request 3: Verify user is logged out
    me_logged_out = client.get("/api/auth/me")
    assert me_logged_out.status_code == 200
    assert me_logged_out.get_json()["user"] is None

    # Request 4: Log in across separate request with credentials
    login_payload = {
        "username": "alex_candidate",
        "password": "CorrectPassword123!",
        "login_type": "user"
    }
    login_res = client.post("/api/auth/login", json=login_payload)
    assert login_res.status_code == 200
    login_data = login_res.get_json()
    assert login_data["success"] is True
    assert login_data["user"]["username"] == "alex_candidate"
    assert login_data["user"]["id"] == user_id
    assert login_data["user"]["login_count"] == 2  # Incremented from 1 to 2

    # Request 5: Log in using email address
    client.post("/api/auth/logout")
    email_login_res = client.post("/api/auth/login", json={
        "username": "alex@example.org",
        "password": "CorrectPassword123!",
        "login_type": "user"
    })
    assert email_login_res.status_code == 200
    assert email_login_res.get_json()["user"]["username"] == "alex_candidate"

    # Request 6: Check /api/auth/me retains session
    me_res = client.get("/api/auth/me")
    assert me_res.status_code == 200
    me_data = me_res.get_json()
    assert me_data["success"] is True
    assert me_data["user"]["username"] == "alex_candidate"


def test_login_error_does_not_expose_admin_credentials(client):
    """
    CRITICAL: Verify that failed login returns generic 'Invalid credentials.'
    and DOES NOT leak 'admin' / 'aiint' credentials in the error message.
    """
    bad_login_res = client.post("/api/auth/login", json={
        "username": "nonexistent_user",
        "password": "wrong_password",
        "login_type": "user"
    })
    assert bad_login_res.status_code == 401
    bad_data = bad_login_res.get_json()
    error_msg = bad_data["error"]

    assert error_msg == "Invalid credentials."
    assert "admin" not in error_msg.lower()
    assert "aiint" not in error_msg.lower()


def test_duplicate_registration_rejected(client):
    """
    Verify duplicate username or email registration returns 409.
    """
    payload = {
        "username": "unique_user",
        "email": "unique@example.org",
        "password": "Password123!"
    }
    res1 = client.post("/api/auth/register", json=payload)
    assert res1.status_code == 200

    # Same username
    res2 = client.post("/api/auth/register", json={
        "username": "unique_user",
        "email": "other@example.org",
        "password": "Password123!"
    })
    assert res2.status_code == 409
    assert "already registered" in res2.get_json()["error"]

    # Same email
    res3 = client.post("/api/auth/register", json={
        "username": "other_user",
        "email": "unique@example.org",
        "password": "Password123!"
    })
    assert res3.status_code == 409
    assert "already registered" in res3.get_json()["error"]


def test_regular_user_cannot_access_admin_endpoints(client):
    """
    Verify regular user cannot log in as admin or access admin endpoints.
    """
    client.post("/api/auth/register", json={
        "username": "regular_joe",
        "email": "joe@example.org",
        "password": "JoePassword123!"
    })

    # Attempt login as admin
    admin_login_res = client.post("/api/auth/login", json={
        "username": "regular_joe",
        "password": "JoePassword123!",
        "login_type": "admin"
    })
    assert admin_login_res.status_code == 403
    assert "Access Denied" in admin_login_res.get_json()["error"]

    # Attempt accessing admin stats
    stats_res = client.get("/api/admin/stats")
    assert stats_res.status_code == 403
