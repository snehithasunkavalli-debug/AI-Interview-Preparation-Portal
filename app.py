import os
import json
import re
import uuid
import sqlite3
import datetime
from datetime import timedelta
import traceback
from flask import Flask, request, jsonify, send_from_directory, send_file, session, g
from werkzeug.security import generate_password_hash, check_password_hash
import pdfplumber

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

# Gemini AI support
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

# CORS support for public deployment
try:
    from flask_cors import CORS
except ImportError:
    CORS = None

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.environ.get("STORAGE_DIR", os.path.join(BASE_DIR, "storage"))
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
DB_PATH = os.path.join(STORAGE_DIR, "portal.db")

os.makedirs(UPLOADS_DIR, exist_ok=True)

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "dossier_secure_secret_key_admin_2026")
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if os.environ.get("HTTPS") else 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True if os.environ.get("HTTPS") else False
# Session expires on browser close and 15-minute inactivity
app.config['SESSION_PERMANENT'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15MB limit

if CORS:
    CORS(app, supports_credentials=True, resources={r"/api/*": {"origins": "*"}})

ALLOWED_EXTENSIONS = {".pdf"}

# Database Setup & Migrations
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()
    
    cursor.execute("""
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
    """)

    cursor.execute("""
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
    """)

    cursor.execute("""
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
    """)

    cursor.execute("""
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
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    # Seed Admin User: username='admin', password='aiint'
    cursor.execute("SELECT id FROM users WHERE username = 'admin'")
    admin_user = cursor.fetchone()
    if not admin_user:
        admin_pwd_hash = generate_password_hash("aiint")
        cursor.execute("""
        INSERT INTO users (username, email, password_hash, full_name, is_admin)
        VALUES ('admin', 'admin@dossier.portal', ?, 'Portal Administrator', 1)
        """, (admin_pwd_hash,))
        print("[*] Admin account created: username='admin', password='aiint'")

    conn.commit()
    conn.close()

init_db()

def get_setting(key, default=""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row:
        return row["value"]
    return os.environ.get(key, default)

def set_setting(key, value):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email, full_name, login_count, has_rated, rating, rating_comment, rated_at, is_banned, is_admin, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

def check_fake_credential(username, email, full_name):
    reasons = []
    u_lower = (username or "").lower()
    e_lower = (email or "").lower()
    
    suspicious_domains = ["example.com", "test.com", "fake.com", "temp.com", "mailinator.com", "tempmail.com", "trashmail.com"]
    for dom in suspicious_domains:
        if e_lower.endswith("@" + dom) or ("@" + dom) in e_lower:
            reasons.append(f"Suspicious domain (@{dom})")
            
    spam_patterns = [r"asdf", r"qwer", r"12345", r"testtest", r"fakemail", r"aaaaa"]
    for pat in spam_patterns:
        if re.search(pat, u_lower) or re.search(pat, e_lower):
            reasons.append("Suspicious string pattern")
            
    if u_lower == e_lower.split("@")[0] and len(u_lower) < 4:
        reasons.append("Auto-generated short username")
        
    return bool(reasons), reasons

# Skill Taxonomy
SKILL_TAXONOMY = {
    "Languages": ["Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "C", "Go", "Golang", "Rust", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "Dart", "HTML", "HTML5", "CSS", "CSS3", "Sass", "SQL", "Bash", "Shell"],
    "Frameworks & Libraries": ["Flask", "Django", "FastAPI", "React", "React.js", "Next.js", "Vue", "Vue.js", "Angular", "Node.js", "Express", "Express.js", "Spring", "Spring Boot", "ASP.NET", ".NET Core", "Tailwind CSS", "Bootstrap", "Redux", "PyTorch", "TensorFlow", "Keras", "Scikit-Learn", "Pandas", "NumPy", "OpenCV", "LangChain", "Hugging Face", "GraphQL"],
    "Databases & Storage": ["PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch", "Cassandra", "DynamoDB", "Firebase", "Oracle", "MSSQL", "MariaDB", "Supabase", "Neo4j"],
    "Cloud, DevOps & Tools": ["Docker", "Kubernetes", "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud", "CI/CD", "GitHub Actions", "GitLab CI", "Jenkins", "Git", "GitHub", "GitLab", "Linux", "Terraform", "Ansible", "Nginx", "Apache", "REST API", "gRPC", "Microservices", "Serverless", "Kafka", "RabbitMQ", "Celery", "Postman", "Jira"],
    "Soft Skills & Concepts": ["Agile", "Scrum", "Problem Solving", "System Design", "Object Oriented Programming", "Design Patterns", "Data Structures", "Algorithms", "Unit Testing", "TDD", "Code Review", "Team Leadership", "Cross-Functional Collaboration", "CI/CD Pipeline"]
}

# Routes
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/health")
def health():
    return jsonify({
        "status": "online",
        "public_ready": True,
        "auto_logout_on_close": True,
        "session_timeout_minutes": 15,
        "gemini_live_llm": bool(get_setting("GEMINI_API_KEY"))
    })

# -----------------------------
# Authentication & User Routes
# -----------------------------
@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.json or {}
    username = (data.get("username") or "").strip().lower()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    full_name = (data.get("full_name") or "").strip() or username

    if not username or not email or not password:
        return jsonify({"error": "Username, email, and password required."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", (username, email))
    if cursor.fetchone():
        return jsonify({"error": "Username or email already registered."}), 409

    pwd_hash = generate_password_hash(password)
    cursor.execute("""
    INSERT INTO users (username, email, password_hash, full_name, login_count) 
    VALUES (?, ?, ?, ?, 1)
    """, (username, email, pwd_hash, full_name))
    user_id = cursor.lastrowid
    conn.commit()

    session.permanent = False
    session["user_id"] = user_id
    user_data = {
        "id": user_id, "username": username, "email": email, "full_name": full_name, 
        "login_count": 1, "has_rated": 0, "rating": 0, "is_banned": 0, "is_admin": 0
    }
    return jsonify({"success": True, "user": user_data, "should_prompt_rating": False})

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.json or {}
    login_id = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    login_type = data.get("login_type", "user")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? OR email = ?", (login_id, login_id))
    user = cursor.fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid credentials. For Admin use username: admin, password: aiint"}), 401

    if login_type == "admin" and not user["is_admin"]:
        return jsonify({"error": "Access Denied. Account does not have Administrator privileges."}), 403

    if user["is_banned"]:
        return jsonify({"error": "This account has been banned due to security violations."}), 403

    # Increment Login Count
    new_login_count = (user["login_count"] or 0) + 1
    cursor.execute("UPDATE users SET login_count = ? WHERE id = ?", (new_login_count, user["id"]))
    conn.commit()

    session.permanent = False
    session["user_id"] = user["id"]
    
    # Exclude Admin from rating prompt (Only Regular Users on 5th login)
    should_prompt_rating = (not user["has_rated"]) and (not user["is_admin"]) and (new_login_count % 5 == 0)

    user_data = {
        "id": user["id"], "username": user["username"], "email": user["email"], 
        "full_name": user["full_name"], "login_count": new_login_count, 
        "has_rated": user["has_rated"], "rating": user["rating"], 
        "is_banned": user["is_banned"], "is_admin": user["is_admin"]
    }
    return jsonify({"success": True, "user": user_data, "should_prompt_rating": should_prompt_rating})

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"success": True})

@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    user = get_current_user()
    return jsonify({"success": True, "user": user})

@app.route("/api/user/rate", methods=["POST"])
def submit_rating():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401

    if user.get("is_admin"):
        return jsonify({"error": "Admin users cannot submit ratings."}), 400
        
    data = request.json or {}
    rating = int(data.get("rating", 5))
    rating_comment = (data.get("comment") or "").strip()
    rating = max(1, min(5, rating))
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users SET has_rated = 1, rating = ?, rating_comment = ?, rated_at = ? WHERE id = ?
    """, (rating, rating_comment, now_str, user["id"]))
    conn.commit()
    
    return jsonify({"success": True, "message": "Thank you for rating!"})

# -----------------------------
# Detailed Admin Dashboard Endpoints
# -----------------------------
@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    admin = get_current_user()
    if not admin or not admin.get("is_admin"):
        return jsonify({"error": "Admin access required."}), 403

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total, SUM(CASE WHEN is_banned = 1 THEN 1 ELSE 0 END) as banned FROM users WHERE is_admin = 0")
    user_counts = cursor.fetchone()
    total_users = user_counts["total"] or 0
    banned_users = user_counts["banned"] or 0

    cursor.execute("SELECT COUNT(*) as total_sessions FROM interview_sessions")
    total_sessions = cursor.fetchone()["total_sessions"] or 0

    # Ratings Summary (Excludes Admin)
    cursor.execute("SELECT rating, COUNT(*) as cnt FROM users WHERE has_rated = 1 AND is_admin = 0 GROUP BY rating")
    ratings_raw = {row["rating"]: row["cnt"] for row in cursor.fetchall()}
    ratings_distribution = {star: ratings_raw.get(star, 0) for star in range(1, 6)}
    
    cursor.execute("SELECT AVG(rating) as avg_r FROM users WHERE has_rated = 1 AND rating > 0 AND is_admin = 0")
    avg_rating_val = cursor.fetchone()["avg_r"]
    avg_rating = round(float(avg_rating_val), 1) if avg_rating_val else 5.0

    # Detailed User Accounts & Rating Feedback Logs
    cursor.execute("SELECT id, username, email, full_name, login_count, has_rated, rating, rating_comment, rated_at, is_banned, is_admin, created_at FROM users ORDER BY id DESC")
    users = [dict(u) for u in cursor.fetchall()]

    suspicious_count = 0
    rated_user_details = []
    
    for u in users:
        is_fake, reasons = check_fake_credential(u["username"], u["email"], u["full_name"])
        u["is_suspicious"] = is_fake
        u["risk_reasons"] = reasons
        if is_fake and not u["is_admin"]:
            suspicious_count += 1
            
        if u["has_rated"] and not u["is_admin"]:
            rated_user_details.append({
                "user_id": u["id"],
                "username": u["username"],
                "email": u["email"],
                "full_name": u["full_name"],
                "rating": u["rating"],
                "rating_comment": u["rating_comment"] or "No comment provided",
                "rated_at": u["rated_at"] or u["created_at"]
            })

    # Registration Growth Trend
    cursor.execute("""
    SELECT DATE(created_at) as reg_date, COUNT(*) as cnt 
    FROM users 
    WHERE created_at >= DATE('now', '-30 days')
    GROUP BY DATE(created_at)
    ORDER BY reg_date ASC
    """)
    growth_daily = [dict(r) for r in cursor.fetchall()]

    return jsonify({
        "success": True,
        "summary": {
            "total_users": total_users,
            "banned_users": banned_users,
            "suspicious_users": suspicious_count,
            "total_sessions": total_sessions,
            "avg_rating": avg_rating,
            "ratings_distribution": ratings_distribution
        },
        "users": users,
        "rated_user_details": rated_user_details,
        "growth_daily": growth_daily
    })

@app.route("/api/admin/ban", methods=["POST"])
def admin_ban_user():
    admin = get_current_user()
    if not admin or not admin.get("is_admin"):
        return jsonify({"error": "Admin access required."}), 403

    data = request.json or {}
    target_user_id = data.get("user_id")
    ban_status = 1 if data.get("ban", True) else 0

    if not target_user_id:
        return jsonify({"error": "User ID required."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT is_admin FROM users WHERE id = ?", (target_user_id,))
    target = cursor.fetchone()
    if not target or target["is_admin"]:
        return jsonify({"error": "Cannot ban this account."}), 400

    cursor.execute("UPDATE users SET is_banned = ? WHERE id = ?", (ban_status, target_user_id))
    conn.commit()

    action = "banned" if ban_status else "unbanned"
    return jsonify({"success": True, "message": f"User successfully {action}."})

application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("======================================================")
    print(f"[*] AI Interview Portal with Auto-Logout on Close running on http://localhost:{port}")
    print("======================================================")
    app.run(host="0.0.0.0", port=port, debug=False)
