import os
import json
import pytest

# Ensure test environment
os.environ["FLASK_ENV"] = "development"
os.environ.pop("VERCEL", None)
os.environ.pop("VERCEL_ENV", None)
os.environ.pop("DATABASE_URL", None)

from app import app
import database


@pytest.fixture
def client(tmp_path):
    test_db = str(tmp_path / "test_ratings.db")
    database.DB_PATH = test_db
    database.STORAGE_DIR = str(tmp_path)

    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key_ratings_portal_32_chars"

    database.init_db()

    with app.test_client() as client:
        yield client


def test_unauthenticated_user_cannot_rate(client):
    """
    Submitting rating without an active session must return 401 Unauthorized.
    """
    res = client.post("/api/user/rate", json={"rating": 5, "comment": "Great!"})
    assert res.status_code == 401
    assert "Authentication required" in res.get_json()["error"]


def test_admin_user_cannot_rate(client):
    """
    Admin accounts are explicitly prohibited from submitting ratings (403 Forbidden).
    """
    # Login as admin
    login_res = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "change_this_to_a_secure_admin_password_in_production" if database.is_production() else "aiint",
        "login_type": "admin"
    })
    assert login_res.status_code == 200

    rate_res = client.post("/api/user/rate", json={"rating": 5, "comment": "Admin test"})
    assert rate_res.status_code == 403
    assert "Admin users cannot submit ratings" in rate_res.get_json()["error"]


def test_rating_validation_rejects_invalid_values(client):
    """
    Rating values must be validated strictly as integers between 1 and 5.
    Out-of-range (0, 6, -1), non-integers, booleans, and nulls must return 400 Bad Request.
    """
    # Register candidate
    reg_res = client.post("/api/auth/register", json={
        "username": "tester_val",
        "email": "tester_val@example.org",
        "password": "Password123!",
        "full_name": "Validation Tester"
    })
    assert reg_res.status_code == 200

    invalid_cases = [
        0,
        6,
        -1,
        10,
        "5",     # string representation
        "bad",   # non-numeric string
        True,    # boolean
        False,   # boolean
        3.5,     # float
        None,    # null
    ]

    for val in invalid_cases:
        res = client.post("/api/user/rate", json={"rating": val, "comment": "Testing invalid"})
        assert res.status_code == 400, f"Expected 400 for rating={val!r}, got {res.status_code}"
        assert "Rating must be an integer between 1 and 5" in res.get_json()["error"]


def test_submit_valid_ratings_1_3_5_and_persistence(client):
    """
    Test submitting valid ratings of 1, 3, and 5 stars.
    Verify that each valid rating is correctly saved and committed in the database.
    """
    ratings_to_test = [
        (1, "candidate_one", "one@example.org", "Needs substantial improvement"),
        (3, "candidate_three", "three@example.org", "Decent platform with good features"),
        (5, "candidate_five", "five@example.org", "Outstanding mock interview practice!")
    ]

    for rating_val, uname, email, comment in ratings_to_test:
        # Register and log in
        reg_res = client.post("/api/auth/register", json={
            "username": uname,
            "email": email,
            "password": "Password123!",
            "full_name": uname.title()
        })
        assert reg_res.status_code == 200
        user_id = reg_res.get_json()["user"]["id"]

        # Submit star rating
        rate_res = client.post("/api/user/rate", json={
            "rating": rating_val,
            "comment": comment
        })
        assert rate_res.status_code == 200
        rate_data = rate_res.get_json()
        assert rate_data["success"] is True
        assert rate_data["rating"] == rating_val
        assert "Thank you for rating" in rate_data["message"]

        # Direct database verification
        conn = database.create_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, username, has_rated, rating, rating_comment, rated_at FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        assert row is not None
        assert row["has_rated"] == 1
        assert row["rating"] == rating_val
        assert row["rating_comment"] == comment
        assert row["rated_at"] is not None
        conn.close()

        # Logout to prepare next candidate
        client.post("/api/auth/logout")


def test_prevent_duplicate_rating_submission(client):
    """
    Ensure a user cannot submit duplicate ratings or overwrite an existing valid rating.
    """
    reg_res = client.post("/api/auth/register", json={
        "username": "duplicate_tester",
        "email": "dupe@example.org",
        "password": "Password123!",
        "full_name": "Dupe Tester"
    })
    assert reg_res.status_code == 200
    user_id = reg_res.get_json()["user"]["id"]

    # First rating submission (valid: 4 stars)
    res1 = client.post("/api/user/rate", json={"rating": 4, "comment": "First impression was good"})
    assert res1.status_code == 200
    assert res1.get_json()["success"] is True

    # Second rating submission attempt (should be rejected with 409)
    res2 = client.post("/api/user/rate", json={"rating": 5, "comment": "Overwriting to 5 stars"})
    assert res2.status_code == 409
    assert "already submitted a rating" in res2.get_json()["error"]

    # Verify original rating is preserved in database
    conn = database.create_connection()
    cur = conn.cursor()
    cur.execute("SELECT rating, rating_comment FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    assert row["rating"] == 4
    assert row["rating_comment"] == "First impression was good"
    conn.close()


def test_admin_stats_distribution_and_averages(client):
    """
    Verify that the admin API returns the correct counts for all five categories (1 to 5 stars),
    the correct total_ratings, and the correct calculated average.
    """
    # Seed candidate ratings:
    # 1 Star: 1 user
    # 2 Stars: 0 users
    # 3 Stars: 2 users
    # 4 Stars: 0 users
    # 5 Stars: 1 user
    # Total ratings = 4. Average = (1 + 3 + 3 + 5) / 4 = 12 / 4 = 3.0
    seeded = [
        ("user_s1", "s1@example.org", 1, "Poor"),
        ("user_s3_a", "s3a@example.org", 3, "Average"),
        ("user_s3_b", "s3b@example.org", 3, "Okay"),
        ("user_s5", "s5@example.org", 5, "Great")
    ]

    for uname, email, star, cmt in seeded:
        client.post("/api/auth/register", json={
            "username": uname,
            "email": email,
            "password": "Password123!",
            "full_name": uname
        })
        client.post("/api/user/rate", json={"rating": star, "comment": cmt})
        client.post("/api/auth/logout")

    # Log in as admin
    admin_login_res = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "change_this_to_a_secure_admin_password_in_production" if database.is_production() else "aiint",
        "login_type": "admin"
    })
    assert admin_login_res.status_code == 200

    # Fetch admin stats
    stats_res = client.get("/api/admin/stats")
    assert stats_res.status_code == 200
    data = stats_res.get_json()
    summary = data["summary"]

    # Verify distribution includes all five categories
    dist = summary["ratings_distribution"]
    # Handle both integer or string keys from JSON
    cnt1 = dist.get(1) if 1 in dist else dist.get("1", 0)
    cnt2 = dist.get(2) if 2 in dist else dist.get("2", 0)
    cnt3 = dist.get(3) if 3 in dist else dist.get("3", 0)
    cnt4 = dist.get(4) if 4 in dist else dist.get("4", 0)
    cnt5 = dist.get(5) if 5 in dist else dist.get("5", 0)

    assert cnt1 == 1
    assert cnt2 == 0  # Zero ratings category preserved!
    assert cnt3 == 2
    assert cnt4 == 0  # Zero ratings category preserved!
    assert cnt5 == 1

    assert summary["total_ratings"] == 4
    assert summary["avg_rating"] == 3.0

    # Verify rated user details
    rated_details = data["rated_user_details"]
    assert len(rated_details) == 4
    usernames = {d["username"] for d in rated_details}
    assert usernames == {"user_s1", "user_s3_a", "user_s3_b", "user_s5"}


def test_admin_stats_empty_state_when_no_ratings(client):
    """
    Verify admin API response when no ratings exist:
    - Counts for all 1..5 stars are 0
    - total_ratings is 0
    - avg_rating is None (not defaulting to 5.0!)
    - rated_user_details is empty
    """
    # Log in as admin on clean database
    client.post("/api/auth/login", json={
        "username": "admin",
        "password": "change_this_to_a_secure_admin_password_in_production" if database.is_production() else "aiint",
        "login_type": "admin"
    })

    stats_res = client.get("/api/admin/stats")
    assert stats_res.status_code == 200
    data = stats_res.get_json()
    summary = data["summary"]

    dist = summary["ratings_distribution"]
    for star in [1, 2, 3, 4, 5]:
        val = dist.get(star) if star in dist else dist.get(str(star), 0)
        assert val == 0

    assert summary["total_ratings"] == 0
    assert summary["avg_rating"] is None
    assert data["rated_user_details"] == []


def test_sql_adapter_rating_queries_for_postgresql():
    """
    Verify adapt_sql correctly converts '?' to '%s' for PostgreSQL in rating queries.
    """
    update_q = "UPDATE users SET has_rated = 1, rating = ?, rating_comment = ?, rated_at = ? WHERE id = ? AND (has_rated IS NULL OR has_rated = 0)"
    adapted_update = database.adapt_sql(update_q, "postgresql")
    assert "%s" in adapted_update
    assert "?" not in adapted_update
    assert adapted_update.count("%s") == 4
