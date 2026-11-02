import os
import json
import pytest

# Ensure test environment
os.environ["FLASK_ENV"] = "development"
os.environ.pop("VERCEL", None)
os.environ.pop("VERCEL_ENV", None)
os.environ.pop("DATABASE_URL", None)

from app import app, analyze_interview_response, get_max_follow_ups
import database


@pytest.fixture
def client(tmp_path):
    test_db = str(tmp_path / "test_interview.db")
    database.DB_PATH = test_db
    database.STORAGE_DIR = str(tmp_path)

    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret_key_interview_portal_32_chars"

    database.init_db()

    with app.test_client() as client:
        yield client


SAMPLE_QUESTIONS = [
    {
        "question": "Explain how you used Python in your recent projects to design scalable, production-ready systems.",
        "category": "Core Requirement Verification",
        "why_asked": "Tests technical mastery and depth in Python."
    },
    {
        "question": "Our role prioritizes Docker and Kubernetes, which appears as a gap in your profile. How would you containerize microservices?",
        "category": "Skill Gap Mitigation",
        "why_asked": "Evaluates containerization ramp-up."
    },
    {
        "question": "Describe an instance where a technical deliverable faced performance bottlenecks. How did you resolve it?",
        "category": "Troubleshooting & Problem Solving",
        "why_asked": "Assesses problem-solving rigor."
    }
]


def test_interview_start(client):
    """
    Starting an interview session must initialize DB records,
    set current_question_index=0, and record initial welcome message.
    """
    res = client.post("/api/interview/start", json={
        "session_id": "sess_test_100",
        "candidate_name": "Rohan Sai",
        "target_role": "Backend Engineer",
        "questions": SAMPLE_QUESTIONS
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["session_id"] == "sess_test_100"
    assert data["current_question_index"] == 0
    assert data["follow_up_count"] == 0
    assert data["total_questions"] == 3
    assert "Explain how you used Python" in data["message"]


def test_scenario_1_detailed_technical_answer_triggers_deeper_followup(client):
    """
    Scenario 1: Candidate provides a detailed technical answer to initial question.
    AI must ask a deeper relevant follow-up exploring architectural trade-offs and edge cases.
    Original question count must not be incremented.
    """
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_1",
        "candidate_name": "Rohan Sai",
        "target_role": "Backend Engineer",
        "questions": SAMPLE_QUESTIONS
    })

    # Detailed technical answer
    detailed_answer = (
        "In our recent project, I designed a distributed data ingestion engine using Python and FastAPI. "
        "We used Celery with Redis for asynchronous task processing and PostgreSQL with connection pooling. "
        "I implemented schema migrations with Alembic, partitioned high-volume time-series tables, "
        "and added Prometheus metrics to monitor API latency and database query throughput."
    )

    res = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_1",
        "answer": detailed_answer,
        "max_follow_ups": 2
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["action"] == "follow_up"
    assert data["is_follow_up"] is True
    assert data["follow_up_count"] == 1
    assert data["current_question_index"] == 0  # Does NOT increment original question count
    assert data["total_questions"] == 3
    assert "Follow-up Question:" in data["ai_message"]


def test_scenario_2_technology_mention_explored(client):
    """
    Scenario 2: Candidate mentions a specific technology (e.g. machine learning or REST APIs).
    AI explores that technology with specific questions on algorithms, metrics, or validation.
    """
    # 2a. Machine Learning mention
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_2a",
        "candidate_name": "Rohan Sai",
        "target_role": "Python Developer",
        "questions": SAMPLE_QUESTIONS
    })

    ml_answer = "I used Python for data processing, machine learning, and backend development."
    res = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_2a",
        "answer": ml_answer,
        "max_follow_ups": 2
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "follow_up"
    assert data["is_follow_up"] is True
    assert data["follow_up_count"] == 1
    # Must explore machine learning algorithms and performance evaluation
    assert "machine learning" in data["ai_message"].lower() or "machine-learning" in data["ai_message"].lower()
    assert "algorithm" in data["ai_message"].lower() or "performance" in data["ai_message"].lower()

    # 2b. REST API mention
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_2b",
        "candidate_name": "Rohan Sai",
        "target_role": "Python Developer",
        "questions": SAMPLE_QUESTIONS
    })

    api_answer = "I developed REST APIs using Python."
    res2 = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_2b",
        "answer": api_answer,
        "max_follow_ups": 2
    })
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2["action"] == "follow_up"
    assert data2["is_follow_up"] is True
    assert "authentication" in data2["ai_message"].lower() or "validation" in data2["ai_message"].lower() or "error" in data2["ai_message"].lower()


def test_scenario_3_vague_answer_asks_clarification(client):
    """
    Scenario 3: Candidate gives a vague, unsupported claim without metrics or evidence.
    AI must identify the vague claim and ask for clarification, metrics, or profiling tools.
    """
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_3",
        "candidate_name": "Rohan Sai",
        "target_role": "Python Developer",
        "questions": SAMPLE_QUESTIONS
    })

    vague_answer = "I optimized performance and made everything very fast using best practices."
    res = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_3",
        "answer": vague_answer,
        "max_follow_ups": 2
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "follow_up"
    assert data["is_follow_up"] is True
    assert "metric" in data["ai_message"].lower() or "profil" in data["ai_message"].lower() or "measure" in data["ai_message"].lower() or "clarif" in data["ai_message"].lower()


def test_scenario_4_irrelevant_answer_redirects(client):
    """
    Scenario 4: Candidate provides an irrelevant, off-topic answer.
    AI must recognize the off-topic response and redirect appropriately back to the topic.
    """
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_4",
        "candidate_name": "Rohan Sai",
        "target_role": "Python Developer",
        "questions": SAMPLE_QUESTIONS
    })

    off_topic_answer = "I love watching soccer matches on the weekend and cooking Italian pasta with dinner."
    res = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_4",
        "answer": off_topic_answer,
        "max_follow_ups": 2
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["action"] == "follow_up"
    assert data["is_follow_up"] is True
    assert "separate" in data["ai_message"].lower() or "focus" in data["ai_message"].lower() or "context" in data["ai_message"].lower()


def test_scenario_5_sufficient_followup_answer_advances_to_next_question(client):
    """
    Scenario 5: Candidate answers the follow-up question sufficiently.
    AI must recognize that the topic has been explored and advance to Question 2.
    """
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_5",
        "candidate_name": "Rohan Sai",
        "target_role": "Python Developer",
        "questions": SAMPLE_QUESTIONS
    })

    # Step 1: Initial answer triggers follow-up
    res1 = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_5",
        "answer": "I used Python for data processing, machine learning, and backend development.",
        "max_follow_ups": 2
    })
    assert res1.get_json()["action"] == "follow_up"
    assert res1.get_json()["follow_up_count"] == 1
    assert res1.get_json()["current_question_index"] == 0

    # Step 2: Candidate answers the follow-up with concrete technical depth
    followup_answer = (
        "We implemented XGBoost and Random Forest classifiers for fraud detection. "
        "We evaluated performance using precision-recall AUC and cross-validation, achieving 0.94 ROC-AUC. "
        "To meet the 50ms latency SLA, we exported models to ONNX and deployed them inside a lightweight container."
    )
    res2 = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_5",
        "answer": followup_answer,
        "max_follow_ups": 2
    })
    data2 = res2.get_json()
    assert data2["success"] is True
    assert data2["action"] == "next_question"
    assert data2["is_follow_up"] is False
    assert data2["current_question_index"] == 1  # Successfully advanced to Question 2
    assert data2["follow_up_count"] == 0         # Reset for new question
    assert "Question 2" in data2["ai_message"]
    assert "Docker and Kubernetes" in data2["ai_message"]


def test_scenario_6_max_followup_limit_advances_without_further_followup(client):
    """
    Scenario 6: Maximum follow-up limit (default 2) is reached.
    AI must advance to next question without asking further follow-ups.
    """
    client.post("/api/interview/start", json={
        "session_id": "sess_scenario_6",
        "candidate_name": "Rohan Sai",
        "target_role": "Python Developer",
        "questions": SAMPLE_QUESTIONS
    })

    # Follow-up 1
    res1 = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_6",
        "answer": "I worked on APIs and web services.",
        "max_follow_ups": 2
    })
    assert res1.get_json()["action"] == "follow_up"
    assert res1.get_json()["follow_up_count"] == 1

    # Follow-up 2
    res2 = client.post("/api/interview/message", json={
        "session_id": "sess_scenario_6",
        "answer": "We used JWT tokens and handled validation with Pydantic.",
        "max_follow_ups": 2
    })
    # Could be follow-up 2 or next_question depending on depth. If follow_up:
    if res2.get_json()["action"] == "follow_up":
        assert res2.get_json()["follow_up_count"] == 2
        # Now limit is reached (2 follow-ups asked). Next answer MUST advance!
        res3 = client.post("/api/interview/message", json={
            "session_id": "sess_scenario_6",
            "answer": "We also had error handler middleware for custom status codes.",
            "max_follow_ups": 2
        })
        data3 = res3.get_json()
        assert data3["action"] == "next_question"
        assert data3["is_follow_up"] is False
        assert data3["current_question_index"] == 1
        assert data3["follow_up_count"] == 0
    else:
        # If it advanced directly
        assert res2.get_json()["current_question_index"] == 1


def test_scenario_7_page_refresh_preserves_interview_context(client):
    """
    Scenario 7: Candidate refreshes the page during an active interview.
    Session state, conversation history, question index, and follow-up count are preserved.
    """
    session_id = "sess_refresh_test_7"
    client.post("/api/interview/start", json={
        "session_id": session_id,
        "candidate_name": "Alice Developer",
        "target_role": "Backend Engineer",
        "questions": SAMPLE_QUESTIONS
    })

    # Submit an answer
    client.post("/api/interview/message", json={
        "session_id": session_id,
        "answer": "I used Python for REST API development using FastAPI and PostgreSQL.",
        "max_follow_ups": 2
    })

    # Simulate page refresh by fetching session state from backend endpoint
    res = client.get(f"/api/interview/session/{session_id}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["session"]["session_id"] == session_id
    assert data["session"]["target_role"] == "Backend Engineer"
    assert data["current_question_index"] == 0
    assert data["follow_up_count"] == 1
    assert data["total_questions"] == 3

    # Check conversation messages list
    messages = data["messages"]
    assert len(messages) >= 3  # Initial welcome + candidate answer + follow-up question
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles
    assert any("REST API" in m["content"] or "API" in m["content"] for m in messages if m["role"] == "user")


def test_scenario_8_api_failure_does_not_lose_candidate_answer(client, monkeypatch):
    """
    Scenario 8: If AI LLM provider fails (e.g. Gemini API error or timeout),
    candidate's submitted answer is safely persisted in the database and
    the system falls back gracefully without losing data.
    """
    session_id = "sess_failure_test_8"
    client.post("/api/interview/start", json={
        "session_id": session_id,
        "candidate_name": "Alice Developer",
        "target_role": "Backend Engineer",
        "questions": SAMPLE_QUESTIONS
    })

    # Monkeypatch genai Client to simulate an unexpected external API failure
    import app as app_module
    class MockFailingGenai:
        class Client:
            def __init__(self, *args, **kwargs):
                pass
            @property
            def models(self):
                class Models:
                    def generate_content(self, *a, **k):
                        raise RuntimeError("Simulated Google Gemini API 503 Service Unavailable")
                return Models()

    monkeypatch.setattr(app_module, "genai", MockFailingGenai)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy_test_key_that_fails")

    answer_text = "I developed distributed microservices using Python and Docker."
    res = client.post("/api/interview/message", json={
        "session_id": session_id,
        "answer": answer_text,
        "max_follow_ups": 2
    })

    # Request must succeed via fallback
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    # Candidate answer MUST be safely stored in database
    detail_res = client.get(f"/api/interview/session/{session_id}")
    saved_messages = detail_res.get_json()["messages"]
    user_contents = [m["content"] for m in saved_messages if m["role"] == "user"]
    assert answer_text in user_contents


def test_scenario_9_question_count_and_completion(client):
    """
    Scenario 9: Completing all original questions marks session completed.
    Ensure total_questions is strictly the original question count (3),
    and follow-up questions do not increase the original question count.
    """
    session_id = "sess_complete_test_9"
    two_questions = SAMPLE_QUESTIONS[:2]

    client.post("/api/interview/start", json={
        "session_id": session_id,
        "candidate_name": "Charlie",
        "target_role": "Cloud Architect",
        "questions": two_questions
    })

    # Question 1: Answer with 0 max follow-ups to fast-forward
    res1 = client.post("/api/interview/message", json={
        "session_id": session_id,
        "answer": "I built production Python backends with caching and high-availability PostgreSQL.",
        "max_follow_ups": 0
    })
    assert res1.get_json()["action"] == "next_question"
    assert res1.get_json()["current_question_index"] == 1
    assert res1.get_json()["total_questions"] == 2

    # Question 2: Answer with 0 max follow-ups to reach end
    res2 = client.post("/api/interview/message", json={
        "session_id": session_id,
        "answer": "I use Docker multi-stage builds and deploy on Kubernetes with Helm charts.",
        "max_follow_ups": 0
    })
    assert res2.get_json()["action"] == "completed"
    assert res2.get_json()["status"] == "completed"
    assert "Interview Completed" in res2.get_json()["ai_message"]

    # Verify session status in DB
    detail_res = client.get(f"/api/interview/session/{session_id}")
    assert detail_res.get_json()["session"]["status"] == "completed"
