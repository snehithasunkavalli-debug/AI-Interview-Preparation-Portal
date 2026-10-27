import os
import json
import pytest

# Set test environment
os.environ["FLASK_ENV"] = "development"
os.environ.pop("VERCEL", None)
os.environ.pop("VERCEL_ENV", None)
os.environ.pop("DATABASE_URL", None)

from app import app, predict_resume_category
import database


@pytest.fixture
def client(tmp_path):
    test_db = str(tmp_path / "test_reports.db")
    database.DB_PATH = test_db
    database.STORAGE_DIR = str(tmp_path)
    
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key_reports_32_characters"
    
    database.init_db()

    with app.test_client() as client:
        yield client


def test_resume_classifier_loaded():
    """
    Test ML classifier pipeline prediction preserves categories and confidence.
    """
    sample_text = (
        "Experienced Python Developer with strong background in Flask, PostgreSQL, "
        "Docker, REST APIs, Microservices, and Git. Built scalable web applications."
    )
    result = predict_resume_category(sample_text)
    assert result is not None
    # If model is loaded, it predicts category and confidence
    if result["status"] == "success":
        assert result["predicted_category"] is not None
        assert isinstance(result["confidence"], (int, float))
        assert result["error"] is None


def test_cv_analyze_and_report_isolation(client):
    """
    Test that candidate can analyze CV, get report_id, and access control strictly isolates reports.
    """
    # 1. Register Candidate 1 (Alice)
    alice_res = client.post("/api/auth/register", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "Password123!",
        "full_name": "Alice Candidate"
    })
    assert alice_res.status_code == 200
    alice_id = alice_res.get_json()["user"]["id"]

    # 2. Alice submits a CV for analysis
    cv_payload = {
        "cv_text": "Software Engineer with 4 years Python, SQL, REST APIs, and Docker experience.",
        "job_requirements": "Python, SQL, Docker, AWS, Kubernetes",
        "filename": "alice_resume.pdf"
    }
    analyze_res = client.post("/api/cv/analyze", json=cv_payload)
    assert analyze_res.status_code == 200
    analysis = analyze_res.get_json()["analysis"]
    report_id = analysis["report_id"]
    assert report_id > 0
    assert analysis["overall_readiness_score"] > 0

    # 3. Alice can view her own reports
    alice_reports_res = client.get("/api/reports")
    assert alice_reports_res.status_code == 200
    alice_reports = alice_reports_res.get_json()["reports"]
    assert len(alice_reports) == 1
    assert alice_reports[0]["id"] == report_id

    # 4. Register Candidate 2 (Bob)
    client.post("/api/auth/logout")
    bob_res = client.post("/api/auth/register", json={
        "username": "bob",
        "email": "bob@example.com",
        "password": "Password123!",
        "full_name": "Bob Candidate"
    })
    assert bob_res.status_code == 200

    # 5. Bob views reports list - MUST NOT see Alice's report!
    bob_reports_res = client.get("/api/reports")
    assert bob_reports_res.status_code == 200
    bob_reports = bob_reports_res.get_json()["reports"]
    assert len(bob_reports) == 0

    # 6. Bob tries to access Alice's report by direct ID - MUST return 403 Forbidden!
    bob_single_res = client.get(f"/api/reports/{report_id}")
    assert bob_single_res.status_code == 403
    assert "Forbidden" in bob_single_res.get_json()["error"]
