import os
import sys
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import json
import re
import uuid
import sqlite3
import database
import datetime
import math
from collections import Counter
from datetime import timedelta
import traceback
from flask import Flask, request, jsonify, send_from_directory, send_file, session, g
from werkzeug.security import generate_password_hash, check_password_hash
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

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

# Detect Vercel Serverless Runtime and Production Environment
IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
IS_PRODUCTION = IS_VERCEL or os.environ.get("FLASK_ENV") == "production" or os.environ.get("ENV") == "production"

# Configure writable temporary directory on Vercel
if IS_VERCEL:
    DEFAULT_STORAGE = "/tmp/storage"
else:
    DEFAULT_STORAGE = os.path.join(BASE_DIR, "storage")

STORAGE_DIR = os.environ.get("STORAGE_DIR", DEFAULT_STORAGE)
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
DB_PATH = os.path.join(STORAGE_DIR, "portal.db")

try:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
except OSError:
    # If default directory is read-only, safely fallback to /tmp
    STORAGE_DIR = "/tmp/storage"
    UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
    DB_PATH = os.path.join(STORAGE_DIR, "portal.db")
    os.makedirs(UPLOADS_DIR, exist_ok=True)

app = Flask(__name__, static_folder=".", static_url_path="")

# Require a strong SECRET_KEY in production; reject insecure defaults
raw_secret = os.environ.get("SECRET_KEY")
INSECURE_SECRET_FALLBACKS = {
    "dossier_secure_secret_key_admin_2026",
    "dossier_secure_secret_key_standalone_2026",
    "secret",
    "changeme",
    "password",
    "default"
}

if IS_PRODUCTION:
    if not raw_secret or raw_secret.strip() in INSECURE_SECRET_FALLBACKS or len(raw_secret.strip()) < 16:
        raise RuntimeError(
            "CRITICAL SECURITY CONFIGURATION ERROR: A strong, unique SECRET_KEY environment variable "
            "(minimum 16 characters) is required in production! "
            "Please configure SECRET_KEY in your Vercel Project Settings -> Environment Variables."
        )
    app.secret_key = raw_secret.strip()
else:
    # Local development mode: allow local key with clear developer notice
    app.secret_key = raw_secret or "dossier_local_dev_key_only_not_for_prod"

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if (os.environ.get("HTTPS") or IS_VERCEL) else 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True if (os.environ.get("HTTPS") or IS_VERCEL) else False
# Session expires on browser close and 15-minute inactivity
app.config['SESSION_PERMANENT'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15MB limit

if CORS:
    CORS(app, supports_credentials=True, resources={r"/api/*": {"origins": "*"}})

ALLOWED_EXTENSIONS = {".pdf"}

# =====================================================================
# ML Resume Category Classifier (Loaded once at application startup)
# =====================================================================
CLASSIFIER_PATH = os.path.join(BASE_DIR, "models", "resume_classifier.joblib")
classifier_pipeline = None
classifier_load_error = None

try:
    import joblib
    if os.path.isfile(CLASSIFIER_PATH):
        classifier_pipeline = joblib.load(CLASSIFIER_PATH)
        print(f"[*] ML Classifier pipeline loaded once at startup from: {CLASSIFIER_PATH}")
    else:
        # Fallback check for alternate path
        alt_path = os.path.join(os.path.dirname(BASE_DIR), "models", "resume_classifier.joblib")
        if os.path.isfile(alt_path):
            classifier_pipeline = joblib.load(alt_path)
            CLASSIFIER_PATH = alt_path
            print(f"[*] ML Classifier loaded once at startup from: {CLASSIFIER_PATH}")
        else:
            classifier_load_error = f"Model artifact not found at {CLASSIFIER_PATH}. Run train_classifier.py to train and save it."
            print(f"[!] Warning: {classifier_load_error}")
except Exception as e:
    classifier_load_error = f"Failed to load resume classifier model: {str(e)}"
    print(f"[!] Error loading classifier: {classifier_load_error}")


def predict_resume_category(cv_text: str):
    """
    Predicts the resume occupational domain category using the pre-loaded ML pipeline.
    Preserves error clarity; does NOT silently fabricate a prediction on failure.
    """
    if classifier_pipeline is None:
        return {
            "predicted_category": None,
            "confidence": None,
            "status": "model_unavailable",
            "error": classifier_load_error or "Resume classifier model is not loaded."
        }
    
    text = (cv_text or "").strip()
    if not text:
        return {
            "predicted_category": None,
            "confidence": None,
            "status": "empty_input",
            "error": "No resume text was provided for category classification."
        }
        
    try:
        pred_label = classifier_pipeline.predict([text])[0]
        confidence = None
        if hasattr(classifier_pipeline, "predict_proba"):
            probs = classifier_pipeline.predict_proba([text])[0]
            confidence = round(float(max(probs)), 4)
            
        return {
            "predicted_category": str(pred_label),
            "confidence": confidence,
            "status": "success",
            "error": None
        }
    except Exception as e:
        return {
            "predicted_category": None,
            "confidence": None,
            "status": "inference_error",
            "error": f"Prediction failed during inference: {str(e)}"
        }

# Database Setup & Connection Lifecycle (Delegated to database.py)
def get_db():
    if 'db' not in g or g.db.is_closed:
        g.db = database.create_connection()
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        if exception:
            db.rollback()
        db.close()

def init_db():
    return database.init_db(allow_unconfigured_in_prod=True)

# Run safe schema initialization at application startup
try:
    db_init_status = init_db()
    if not db_init_status.get("success"):
        print(f"[!] Database startup notice: {db_init_status.get('error')}")
except Exception as e:
    print(f"[!] Database initialization exception on startup: {e}")

def get_setting(key, default=""):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            return row["value"]
    except Exception:
        pass
    return os.environ.get(key, default)

def set_setting(key, value):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO app_settings (key, value) VALUES (?, ?)
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, value))
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

def calculate_shannon_entropy(text):
    if not text:
        return 0.0
    freq = Counter(text)
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in freq.values())

def check_fake_credential(username, email, full_name, login_count=0):
    reasons = []
    risk_score = 0
    u_lower = (username or "").strip().lower()
    e_lower = (email or "").strip().lower()
    name_lower = (full_name or "").strip().lower()
    
    # 1. Disposable / Burner Email Domains (Weight: 45)
    disposable_domains = {
        "example.com", "test.com", "fake.com", "temp.com", "mailinator.com", "tempmail.com",
        "trashmail.com", "guerrillamail.com", "10minutemail.com", "sharklasers.com", "yopmail.com",
        "throwawaymail.com", "dispostable.com", "getairmail.com", "dummy.com", "sample.com",
        "burnermail.io", "trashmail.net", "dropmail.me", "mohmal.com", "mytemp.email"
    }
    email_domain = e_lower.split("@")[-1] if "@" in e_lower else ""
    if email_domain in disposable_domains or any(email_domain.endswith("." + d) for d in disposable_domains):
        reasons.append(f"Disposable domain (@{email_domain})")
        risk_score += 45

    # 2. Shannon Entropy & Repetitive / Keyboard Walk Patterns (Weight: 35)
    email_prefix = e_lower.split("@")[0] if "@" in e_lower else e_lower
    u_entropy = calculate_shannon_entropy(u_lower)
    
    walks = ["qwerty", "asdfgh", "zxcvbn", "123456", "poiuy", "lkjhg", "mnbvc", "asdf", "qwer"]
    if any(w in u_lower or w in email_prefix for w in walks):
        reasons.append("QWERTY sequential keyboard walk")
        risk_score += 35

    if re.search(r"(.)\1{3,}", u_lower) or re.search(r"(.)\1{3,}", email_prefix):
        reasons.append("Repetitive character spam pattern")
        risk_score += 30
    elif len(u_lower) >= 6 and u_entropy < 1.9:
        reasons.append(f"Unusually low entropy string ({u_entropy:.2f} bits)")
        risk_score += 25

    if re.search(r"[bcdfghjklmnpqrstvwxyz]{5,}", u_lower) or re.search(r"[bcdfghjklmnpqrstvwxyz]{5,}", email_prefix):
        reasons.append("Consonant cluster spam pattern")
        risk_score += 30

    # 3. Identity Plausibility & Format Malformations (Weight: 25)
    if len(u_lower) < 3 and u_lower == email_prefix:
        reasons.append("Ultra-short auto-generated identity (< 3 chars)")
        risk_score += 30

    digits = sum(c.isdigit() for c in u_lower)
    if len(u_lower) >= 4 and (digits / len(u_lower)) >= 0.5:
        reasons.append(f"High numeric digit ratio ({int(digits/len(u_lower)*100)}%)")
        risk_score += 20

    if "@" not in e_lower or "." not in email_domain or len(email_domain.split(".")[-1]) < 2:
        reasons.append("Invalid or malformed email structure")
        risk_score += 35

    # 4. Privilege Impersonation Keywords (Weight: 40)
    spoof_terms = ["admin", "root", "administrator", "moderator", "superuser", "sysadmin", "support", "official_dossier"]
    if any(st in u_lower for st in spoof_terms) or any(st in name_lower for st in spoof_terms):
        if u_lower != "admin":
            reasons.append("Privilege impersonation keyword")
            risk_score += 40

    is_suspicious = risk_score >= 40
    return is_suspicious, reasons, min(100, risk_score)

# Skill Taxonomy
SKILL_TAXONOMY = {
    "Languages": ["Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "C", "Go", "Golang", "Rust", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "Dart", "HTML", "HTML5", "CSS", "CSS3", "Sass", "SQL", "Bash", "Shell"],
    "Frameworks & Libraries": ["Flask", "Django", "FastAPI", "React", "React.js", "Next.js", "Vue", "Vue.js", "Angular", "Node.js", "Express", "Express.js", "Spring", "Spring Boot", "ASP.NET", ".NET Core", "Tailwind CSS", "Bootstrap", "Redux", "PyTorch", "TensorFlow", "Keras", "Scikit-Learn", "Pandas", "NumPy", "OpenCV", "LangChain", "Hugging Face", "GraphQL"],
    "Databases & Storage": ["PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch", "Cassandra", "DynamoDB", "Firebase", "Oracle", "MSSQL", "MariaDB", "Supabase", "Neo4j"],
    "Cloud, DevOps & Tools": ["Docker", "Kubernetes", "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud", "CI/CD", "GitHub Actions", "GitLab CI", "Jenkins", "Git", "GitHub", "GitLab", "Linux", "Terraform", "Ansible", "Nginx", "Apache", "REST API", "gRPC", "Microservices", "Serverless", "Kafka", "RabbitMQ", "Celery", "Postman", "Jira"],
    "Soft Skills & Concepts": ["Agile", "Scrum", "Problem Solving", "System Design", "Object Oriented Programming", "Design Patterns", "Data Structures", "Algorithms", "Unit Testing", "TDD", "Code Review", "Team Leadership", "Cross-Functional Collaboration", "CI/CD Pipeline"]
}

SKILL_ALIASES = {
    "python": ["py", "python3"],
    "javascript": ["js", "ecmascript", "es6"],
    "typescript": ["ts"],
    "react": ["react.js", "reactjs"],
    "node.js": ["node", "nodejs"],
    "vue": ["vue.js", "vuejs"],
    "angular": ["angular.js", "angularjs"],
    "docker": ["containerization", "containers", "docker-compose"],
    "kubernetes": ["k8s", "container orchestration"],
    "postgresql": ["postgres", "psql"],
    "aws": ["amazon web services", "ec2", "s3", "lambda"],
    "gcp": ["google cloud", "google cloud platform"],
    "azure": ["microsoft azure"],
    "rest api": ["restful", "rest apis", "restful api", "api development"],
    "ci/cd": ["continuous integration", "cicd", "github actions", "gitlab ci", "jenkins"],
    "machine learning": ["ml", "deep learning", "neural networks"],
    "sql": ["structured query language", "rdbms"]
}

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", 
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", 
    "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", 
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", 
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", 
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", 
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", 
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", 
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", 
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", 
    "why", "with", "would", "you", "your", "yours", "yourself", "yourselves"
}

def tokenize_technical_text(text):
    text_clean = str(text or "")
    clean = re.sub(r"[^a-zA-Z0-9+#.\s]", " ", text_clean.lower())
    words = [w.strip() for w in clean.split() if len(w.strip()) > 1 and w.strip() not in STOPWORDS]
    ngrams = list(words)
    for i in range(len(words) - 1):
        ngrams.append(f"{words[i]} {words[i+1]}")
    for i in range(len(words) - 2):
        ngrams.append(f"{words[i]} {words[i+1]} {words[i+2]}")
    return words, ngrams

def compute_tf_idf_cosine_similarity(cv_text, jd_text):
    _, cv_ngrams = tokenize_technical_text(cv_text)
    _, jd_ngrams = tokenize_technical_text(jd_text)
    if not cv_ngrams or not jd_ngrams:
        return 0.5
    cv_counts = Counter(cv_ngrams)
    jd_counts = Counter(jd_ngrams)
    total_cv = len(cv_ngrams)
    total_jd = len(jd_ngrams)
    vocab = set(cv_counts.keys()).union(set(jd_counts.keys()))
    dot_product = 0.0
    norm_cv_sq = 0.0
    norm_jd_sq = 0.0
    for term in vocab:
        tf_cv = cv_counts.get(term, 0) / total_cv
        tf_jd = jd_counts.get(term, 0) / total_jd
        df = (1 if term in cv_counts else 0) + (1 if term in jd_counts else 0)
        idf = math.log((1 + 2) / (1 + df)) + 1.0
        w_cv = tf_cv * idf
        w_jd = tf_jd * idf
        dot_product += w_cv * w_jd
        norm_cv_sq += w_cv * w_cv
        norm_jd_sq += w_jd * w_jd
    norm_cv = math.sqrt(norm_cv_sq)
    norm_jd = math.sqrt(norm_jd_sq)
    if norm_cv == 0 or norm_jd == 0:
        return 0.5
    cosine = dot_product / (norm_cv * norm_jd)
    return max(0.0, min(1.0, cosine))

def extract_skills_from_text(text):
    found = []
    text_clean = str(text or "")
    if not text_clean:
        return found
    lower = " " + re.sub(r"[,|/()\[\]{}:;]", " ", text_clean.lower()) + " "
    for cat, skills in SKILL_TAXONOMY.items():
        for skill in skills:
            s_lower = skill.lower()
            if s_lower in ["c", "r"]:
                pat = rf"(?<=\s){s_lower}(?=\s|,|\.)"
                if re.search(pat, lower):
                    found.append(skill)
            elif s_lower in ["c++", "c#", ".net core"]:
                if s_lower in lower:
                    found.append(skill)
            else:
                pat = rf"\b{re.escape(s_lower)}\b"
                if re.search(pat, lower):
                    found.append(skill)
    return list(dict.fromkeys(found))

# -----------------------------
# Evidence Extraction & Career Tracks Recommendation
# -----------------------------
CERTIFICATION_PATTERNS = [
    (r"\b(aws\s+certified[^\n,.]*|aws\s+solutions\s+architect)\b", "AWS Certification"),
    (r"\b(azure\s+certified[^\n,.]*|microsoft\s+certified[^\n,.]*)\b", "Microsoft / Azure Certification"),
    (r"\b(google\s+cloud\s+certified[^\n,.]*|gcp\s+certified[^\n,.]*)\b", "Google Cloud Certification"),
    (r"\b(certified\s+kubernetes\s+(?:administrator|application\s+developer)|cka|ckad)\b", "Kubernetes (CKA/CKAD) Certification"),
    (r"\b(cissp|cism|comptia\s+(?:security\+|network\+|a\+)|ceh)\b", "Cybersecurity Certification"),
    (r"\b(pmp|project\s+management\s+professional|capm)\b", "PMP / Project Management Certification"),
    (r"\b(certified\s+scrum\s*master|csm|psm\s*i?)\b", "Scrum Master Certification"),
    (r"\b(cpa|certified\s+public\s+accountant|cfa|acca|cma)\b", "Accounting / Finance Certification (CPA/CFA/ACCA)"),
    (r"\b(phr|sphr|shrm-cp|shrm-scp)\b", "HR Certification (PHR/SPHR/SHRM)"),
    (r"\b(ccna|ccnp|ccie|cisco\s+certified)\b", "Cisco Networking Certification")
]

CAREER_TRACK_DEFINITIONS = [
    {
        "title": "Backend Developer",
        "skills": ["Python", "Java", "Go", "Golang", "C++", "C#", "SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "REST API", "Docker", "Microservices", "Node.js", "Express", "Django", "Flask", "FastAPI", "Spring", "Spring Boot", "gRPC"],
        "keywords": ["backend", "server", "microservices", "api", "database", "distributed systems"],
        "categories": ["INFORMATION-TECHNOLOGY", "ENGINEERING"],
        "reason_template": "Alignment with backend architecture, API design, and data persistence tools ({skills})."
    },
    {
        "title": "Frontend Developer",
        "skills": ["JavaScript", "TypeScript", "React", "React.js", "Next.js", "Vue", "Vue.js", "Angular", "HTML", "HTML5", "CSS", "CSS3", "Tailwind CSS", "Bootstrap", "Redux"],
        "keywords": ["frontend", "ui", "ux", "client-side", "responsive", "web application"],
        "categories": ["INFORMATION-TECHNOLOGY", "DESIGNER"],
        "reason_template": "Alignment with client-side engineering and responsive interface architecture using {skills}."
    },
    {
        "title": "Full-Stack Developer",
        "is_fullstack": True,
        "skills": ["React", "Vue", "Angular", "Next.js", "JavaScript", "TypeScript", "Python", "Node.js", "Express", "Flask", "Django", "SQL", "PostgreSQL", "MongoDB", "REST API"],
        "keywords": ["full stack", "fullstack", "end-to-end"],
        "categories": ["INFORMATION-TECHNOLOGY", "ENGINEERING"],
        "reason_template": "Skills span both client-side interfaces and backend API services ({skills})."
    },
    {
        "title": "DevOps & Cloud Engineer",
        "skills": ["Docker", "Kubernetes", "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud", "CI/CD", "GitHub Actions", "GitLab CI", "Jenkins", "Linux", "Terraform", "Ansible", "Nginx", "Apache"],
        "keywords": ["devops", "cloud", "infrastructure", "pipeline", "automation", "sysadmin", "cluster"],
        "categories": ["INFORMATION-TECHNOLOGY", "ENGINEERING"],
        "reason_template": "Profile includes container orchestration, cloud hosting, and deployment tooling ({skills})."
    },
    {
        "title": "Data Engineer / Machine Learning Specialist",
        "skills": ["Python", "SQL", "Pandas", "NumPy", "Scikit-Learn", "PyTorch", "TensorFlow", "Keras", "Kafka", "PostgreSQL", "Spark"],
        "keywords": ["data engineering", "machine learning", "deep learning", "nlp", "etl", "data pipeline", "model training"],
        "categories": ["INFORMATION-TECHNOLOGY", "ENGINEERING"],
        "reason_template": "Relevant skills for data pipeline development, machine learning workflows, and analytical storage ({skills})."
    },
    {
        "title": "Data & Business Intelligence Analyst",
        "skills": ["SQL", "Python", "R", "Pandas", "NumPy", "PostgreSQL", "MySQL"],
        "keywords": ["tableau", "power bi", "excel", "analytics", "reporting", "dashboard", "metrics", "data analysis"],
        "categories": ["FINANCE", "CONSULTANT", "INFORMATION-TECHNOLOGY"],
        "reason_template": "Relevant background for quantitative reporting, data extraction, and business metrics analysis ({skills})."
    },
    {
        "title": "Mobile Application Engineer",
        "skills": ["Swift", "Kotlin", "Dart", "React", "TypeScript", "JavaScript"],
        "keywords": ["ios", "android", "mobile", "flutter", "react native", "xcode", "mobile app"],
        "categories": ["INFORMATION-TECHNOLOGY"],
        "reason_template": "Relevant profile for native or cross-platform mobile application development ({skills})."
    },
    {
        "title": "Systems & Embedded Software Engineer",
        "skills": ["C", "C++", "Rust", "Linux", "Bash", "Shell"],
        "keywords": ["embedded", "rtos", "kernel", "firmware", "device driver", "microcontroller", "low-level"],
        "categories": ["ENGINEERING", "INFORMATION-TECHNOLOGY"],
        "reason_template": "Includes low-level systems programming and Unix/Linux systems tools ({skills})."
    },
    {
        "title": "QA & Test Automation Engineer",
        "skills": ["Unit Testing", "TDD", "Git", "Python", "JavaScript"],
        "keywords": ["qa", "quality assurance", "test automation", "selenium", "cypress", "pytest", "test cases", "testing"],
        "categories": ["INFORMATION-TECHNOLOGY"],
        "reason_template": "Relevant background in software verification, automated test suites, and quality assurance ({skills})."
    },
    {
        "title": "Technical Product & Project Lead",
        "skills": ["System Design", "Agile", "Scrum", "Team Leadership", "Cross-Functional Collaboration", "Code Review", "Jira"],
        "keywords": ["project manager", "product manager", "scrum master", "agile", "sprint", "roadmap", "stakeholders"],
        "categories": ["CONSULTANT", "BUSINESS-DEVELOPMENT", "INFORMATION-TECHNOLOGY"],
        "reason_template": "Background aligns with technical design coordination, agile project execution, and team leadership ({skills})."
    },
    {
        "title": "Human Resources & Talent Specialist",
        "skills": ["Team Leadership", "Cross-Functional Collaboration"],
        "keywords": ["human resources", "recruitment", "hiring", "talent acquisition", "onboarding", "payroll", "employee relations", "hris", "personnel", "staffing"],
        "categories": ["HR", "BPO"],
        "reason_template": "Profile matches organizational recruitment, workforce management, and personnel operations ({skills})."
    },
    {
        "title": "Financial Analyst & Corporate Accountant",
        "skills": ["SQL", "Python"],
        "keywords": ["accounting", "finance", "audit", "tax", "budget", "general ledger", "balance sheet", "cpa", "financial reporting", "gaap", "accounts payable"],
        "categories": ["FINANCE", "ACCOUNTANT", "BANKING"],
        "reason_template": "Background in financial compliance, ledger management, and fiscal accounting ({skills})."
    },
    {
        "title": "Healthcare & Clinical Operations Specialist",
        "skills": ["Cross-Functional Collaboration"],
        "keywords": ["healthcare", "nursing", "clinical", "patient care", "medical", "hospital", "triage", "health records"],
        "categories": ["HEALTHCARE"],
        "reason_template": "Background aligns with clinical operations and healthcare services delivery ({skills})."
    },
    {
        "title": "Sales & Business Development Representative",
        "skills": ["Team Leadership", "Cross-Functional Collaboration"],
        "keywords": ["sales", "business development", "b2b", "revenue", "account management", "lead generation", "client acquisition", "sales pipeline", "crm"],
        "categories": ["SALES", "BUSINESS-DEVELOPMENT"],
        "reason_template": "Background aligns with client acquisition, revenue growth, and account management ({skills})."
    },
    {
        "title": "Operations & Project Specialist",
        "skills": ["Agile", "Scrum", "Team Leadership", "Cross-Functional Collaboration", "Jira"],
        "keywords": ["operations", "project management", "workflow", "process improvement", "vendor management", "logistics", "contracts", "budget"],
        "categories": ["CONSULTANT", "BUSINESS-DEVELOPMENT", "BPO", "CONSTRUCTION", "AVIATION"],
        "reason_template": "Profile aligns with operational coordination, process improvement, and project delivery ({skills})."
    },
    {
        "title": "Software Engineer (Generalist)",
        "skills": ["Python", "JavaScript", "Java", "C++", "C#", "SQL", "Git", "Unit Testing"],
        "keywords": ["software engineer", "developer", "programming", "software development"],
        "categories": ["INFORMATION-TECHNOLOGY", "ENGINEERING"],
        "reason_template": "Broad software engineering foundation across programming languages and tools ({skills})."
    }
]

def extract_strengths_from_cv(cv_text, candidate_skills, matched_skills, overall_score, cosine_sim):
    """
    Generates a concise list of strengths grounded in evidence from the resume text
    and skill analysis results without fabricating achievements.
    """
    strengths = []
    cv_clean = str(cv_text or "").strip()
    cv_lower = cv_clean.lower()
    candidate_skills = list(candidate_skills) if candidate_skills else []
    matched_skills = list(matched_skills) if matched_skills else []
    overall_score = overall_score or 0
    
    # 1. Match with Company Requirements (highest relevance)
    if matched_skills:
        strengths.append(f"Job Requirement Match: Directly aligns with {len(matched_skills)} required competencies ({', '.join(matched_skills[:4])}).")
    elif overall_score >= 50:
        strengths.append(f"Competency Alignment: Achieves a {overall_score}% requirement alignment score.")
        
    # 2. Certifications & Professional Credentials (explicit evidence only)
    found_certs = []
    for pat, name in CERTIFICATION_PATTERNS:
        if re.search(pat, cv_lower):
            found_certs.append(name)
    if found_certs:
        unique_certs = list(dict.fromkeys(found_certs))[:2]
        strengths.append(f"Certifications & Credentials: {', '.join(unique_certs)} identified in resume text.")
        
    # 3. Technical Stack Breakdown from Taxonomy
    languages = [s for s in candidate_skills if s in SKILL_TAXONOMY.get("Languages", [])]
    frameworks = [s for s in candidate_skills if s in SKILL_TAXONOMY.get("Frameworks & Libraries", [])]
    cloud_tools = [s for s in candidate_skills if s in SKILL_TAXONOMY.get("Cloud, DevOps & Tools", [])]
    databases = [s for s in candidate_skills if s in SKILL_TAXONOMY.get("Databases & Storage", [])]
    concepts = [s for s in candidate_skills if s in SKILL_TAXONOMY.get("Soft Skills & Concepts", [])]
    
    if languages:
        strengths.append(f"Programming Languages: Technical skills include {', '.join(languages[:4])}.")
    if frameworks:
        strengths.append(f"Frameworks & Libraries: Applied tooling includes {', '.join(frameworks[:4])}.")
    if cloud_tools:
        strengths.append(f"Cloud & DevOps Tools: Infrastructure skills include {', '.join(cloud_tools[:3])}.")
    if databases:
        strengths.append(f"Databases & Storage: Skills include {', '.join(databases[:3])}.")
        
    # 4. Documented Years of Experience
    exp_match = re.search(r"\b(\d{1,2}(?:\.\d+)?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\b)\+?\s*(?:years?|yrs?)\b(?:\s+of)?\s+(?:experience|exp|background|track record)\b", cv_lower)
    if exp_match:
        strengths.append(f"Professional Experience: Resume references {exp_match.group(0).strip()}.")
        
    # 5. Production Depth & Practical Delivery (only if concrete engineering actions and technical skills exist)
    tech_skills_present = bool(languages or frameworks or cloud_tools or databases)
    system_markers = ["architected", "deployed", "scaled", "engineered", "production"]
    found_system = [m for m in system_markers if re.search(rf"\b{m}\b", cv_lower)]
    if tech_skills_present and len(found_system) >= 1:
        strengths.append("Systems Delivery: Resume references involvement in deploying, engineering, or delivering systems.")
        
    if concepts and len(strengths) < 6:
        strengths.append(f"Methodologies & Practices: Referenced practices include {', '.join(concepts[:3])}.")
        
    # 6. Fallback if insufficient content
    if not strengths:
        if len(cv_clean) > 30:
            strengths.append("Limited Resume Evidence: Document parsed successfully, but does not contain sufficient technical skills or credentials to highlight specific strengths.")
        else:
            strengths.append("Insufficient Resume Content: The provided resume text contains insufficient detail to extract candidate strengths. Please provide a more complete resume.")
            
    return strengths[:6]

def recommend_career_tracks(candidate_skills, cv_text, predicted_category=None):
    """
    Recommends 3-5 relevant career tracks based on the candidate's actual skills,
    resume category context, and experience, providing clear evidence-backed reasons.
    """
    cv_clean = str(cv_text or "").strip()
    cv_lower = cv_clean.lower()
    candidate_skills = list(candidate_skills) if candidate_skills else []
    scored_tracks = []
    
    # Check for frontend and backend skills for Full-Stack evaluation
    fe_skills = [s for s in candidate_skills if s in ["React", "React.js", "Vue", "Vue.js", "Angular", "Next.js", "JavaScript", "TypeScript", "HTML", "CSS", "Tailwind CSS"]]
    be_skills = [s for s in candidate_skills if s in ["Python", "Java", "Go", "C#", "C++", "Node.js", "Express", "Django", "Flask", "FastAPI", "SQL", "PostgreSQL", "MySQL", "MongoDB", "REST API"]]
    
    for track in CAREER_TRACK_DEFINITIONS:
        # Fullstack specific check
        if track.get("is_fullstack"):
            if fe_skills and be_skills:
                matched_skills = fe_skills[:2] + be_skills[:2]
                score = (len(matched_skills) * 4) + 4
                reason = f"Skills span both client-side ({', '.join(fe_skills[:2])}) and server-side services ({', '.join(be_skills[:2])})."
                scored_tracks.append({
                    "title": track["title"],
                    "score": score,
                    "reason": reason,
                    "matched_skills": matched_skills
                })
            continue
            
        matched_skills = [s for s in candidate_skills if s in track["skills"]]
        matched_kw = [k for k in track.get("keywords", []) if re.search(rf"\b{re.escape(k)}\b", cv_lower)]
        cat_match = bool(predicted_category and predicted_category.upper() in track.get("categories", []))
        
        # Scoring weight: skills are highest weight, keywords supplementary, category adds context
        score = (len(matched_skills) * 4) + (len(matched_kw) * 1.5) + (3.0 if cat_match else 0.0)
        
        # Must have at least some concrete evidence
        if len(matched_skills) > 0 or len(matched_kw) >= 2 or (cat_match and len(matched_kw) >= 1):
            evidence_items = matched_skills[:4] if matched_skills else matched_kw[:3]
            evidence_str = ", ".join(evidence_items) if evidence_items else "profile indicators"
            reason = track["reason_template"].format(skills=evidence_str)
            
            scored_tracks.append({
                "title": track["title"],
                "score": score,
                "reason": reason,
                "matched_skills": matched_skills[:5] if matched_skills else matched_kw[:3]
            })
            
    # Sort descending by score
    scored_tracks.sort(key=lambda x: x["score"], reverse=True)
    
    # Return top 3-5 recommendations (or empty if no evidence)
    final_tracks = scored_tracks[:4]
    
    # Strip internal score before returning
    for t in final_tracks:
        t.pop("score", None)
        
    return final_tracks

def analyze_cv_algorithm(cv_text, job_requirements, candidate_name="Candidate", predicted_category=None):
    cv_text = str(cv_text or "")
    job_requirements = str(job_requirements or "")
    candidate_name = str(candidate_name or "Candidate")
    
    candidate_skills = extract_skills_from_text(cv_text)
    required_skills = extract_skills_from_text(job_requirements)
    if not required_skills:
        required_skills = ["Python", "JavaScript", "SQL", "Docker", "REST API", "Git"]

    cv_text_lower = cv_text.lower()
    depth_markers = ["developed", "built", "implemented", "architected", "designed", "engineered", "deployed", "led", "managed", "production", "years", "experience"]
    
    skill_progress = []
    matched_skills = []
    gap_skills = []
    
    for req_skill in required_skills:
        s_lower = req_skill.lower()
        aliases = SKILL_ALIASES.get(s_lower, [])
        all_variants = [s_lower] + aliases
        occurrences = 0
        for var in all_variants:
            occurrences += len(re.findall(rf"\b{re.escape(var)}\b", cv_text_lower))
            
        has_depth_context = False
        if occurrences > 0:
            for marker in depth_markers:
                if re.search(rf"\b({re.escape(s_lower)}).{{0,40}}\b{marker}\b|\b{marker}\b.{{0,40}}\b{re.escape(s_lower)}\b", cv_text_lower):
                    has_depth_context = True
                    break

        if occurrences >= 2 or (occurrences >= 1 and has_depth_context):
            pct = 100
            status = "Mastered (100%)"
            status_cls = "fill-matched"
            matched_skills.append(req_skill)
        elif occurrences >= 1:
            pct = 80
            status = "Direct Match (80%)"
            status_cls = "fill-matched"
            matched_skills.append(req_skill)
        elif any(var in cv_text_lower for var in aliases):
            pct = 50
            status = "Partial Exposure (50%)"
            status_cls = "fill-partial"
            matched_skills.append(req_skill)
        else:
            pct = 0
            status = "Skill Gap (0%)"
            status_cls = "fill-gap"
            gap_skills.append(req_skill)
            
        skill_progress.append({
            "skill": req_skill,
            "percentage": pct,
            "status": status,
            "statusClass": status_cls
        })

    cosine_sim = compute_tf_idf_cosine_similarity(cv_text, job_requirements)
    avg_skill_pct = sum(s["percentage"] for s in skill_progress) / len(skill_progress) if skill_progress else 70
    overall_score = round(0.65 * avg_skill_pct + 0.35 * (cosine_sim * 100))
    overall_score = max(5, min(100, overall_score))
    
    top_skill = matched_skills[0] if matched_skills else (candidate_skills[0] if candidate_skills else "Software Engineering")
    primary_gap = gap_skills[0] if gap_skills else "Cloud Architecture & Scalability"
    
    questions = [
        {
            "question": f"Walk me through how you used {top_skill} in your recent projects to design scalable, production-ready systems.",
            "category": "Core Requirement Verification",
            "why_asked": f"Tests technical mastery and depth in {top_skill}."
        },
        {
            "question": f"Our role prioritizes {primary_gap}, which appears as a skill gap in your profile. How would you ramp up and apply best practices in {primary_gap}?",
            "category": "Skill Gap Mitigation",
            "why_asked": f"Evaluates learning agility and practical bridging of {primary_gap}."
        },
        {
            "question": f"Describe an instance where a technical deliverable using {top_skill} faced performance bottlenecks or bugs. How did you resolve it?",
            "category": "Troubleshooting & Problem Solving",
            "why_asked": "Assesses problem-solving rigor and root-cause analysis."
        }
    ]

    strengths = extract_strengths_from_cv(cv_text, candidate_skills, matched_skills, overall_score, cosine_sim)
    career_tracks = recommend_career_tracks(candidate_skills, cv_text, predicted_category)

    return {
        "candidate_name": candidate_name,
        "experience_level": "Mid-Level Professional" if overall_score >= 60 else "Junior / Emerging Professional",
        "years_of_experience_estimate": 3.5 if overall_score >= 65 else 1.5,
        "top_skills": candidate_skills[:12],
        "required_skills": required_skills,
        "skill_progress": skill_progress,
        "skill_gaps": gap_skills,
        "strengths": strengths,
        "suggested_roles": career_tracks,
        "overall_readiness_score": overall_score,
        "cosine_similarity": round(cosine_sim, 3),
        "likely_interview_questions": questions
    }

# =====================================================================
# Adaptive Follow-Up Mock Interview Engine
# =====================================================================
DEFAULT_MAX_FOLLOW_UPS = 2

def get_max_follow_ups():
    try:
        val = get_setting("MAX_FOLLOW_UPS_PER_QUESTION", DEFAULT_MAX_FOLLOW_UPS)
        return int(val)
    except Exception:
        return DEFAULT_MAX_FOLLOW_UPS

def analyze_interview_response(
    original_question: str,
    original_question_category: str,
    candidate_answer: str,
    conversation_history: list = None,
    follow_up_count: int = 0,
    max_follow_ups: int = DEFAULT_MAX_FOLLOW_UPS,
    candidate_skills: list = None,
    required_skills: list = None,
    target_role: str = "Software Engineer",
    cv_text: str = "",
    job_requirements: str = ""
):
    """
    Evaluates candidate's answer adaptively using:
    - Original interview question & category
    - Candidate's submitted answer
    - Resume context & claimed experience
    - Job requirements & target role
    - Previous conversation context
    - Configured follow-up limit
    
    Generates meaningful follow-up questions exploring technical knowledge,
    clarifying vague/unsupported claims, redirecting off-topic answers,
    or advancing smoothly when the topic is sufficiently explored or max follow-ups reached.
    """
    conversation_history = conversation_history or []
    candidate_skills = candidate_skills or []
    required_skills = required_skills or []
    ans_clean = (candidate_answer or "").strip()
    ans_lower = ans_clean.lower()
    tokens = [w for w in re.findall(r"[a-zA-Z0-9+#.]+", ans_lower) if w not in STOPWORDS]
    words_count = len(ans_clean.split())

    # Build set of previously asked assistant questions to guarantee anti-repetition
    previously_asked = set()
    for m in conversation_history:
        if isinstance(m, dict) and m.get("role") in ("assistant", "ai"):
            text_val = m.get("content", "").strip().lower()
            previously_asked.add(text_val)
            if "follow-up question:" in text_val:
                parts = text_val.split("follow-up question:")
                if len(parts) > 1:
                    previously_asked.add(parts[-1].strip().lower())

    # RULE 1: If maximum follow-up count has been reached for this original question,
    # the AI must advance to the next original question.
    if follow_up_count >= max_follow_ups:
        return {
            "action": "next_question",
            "is_follow_up": False,
            "reasoning": f"Configured maximum follow-up count ({max_follow_ups}) reached for this question.",
            "follow_up_question": None,
            "transition_prefix": "Thank you for sharing those insights. Let's move on to the next question."
        }

    # RULE 2: If live Gemini AI is configured, query Gemini 2.0 with the structured prompt
    gemini_key = (get_setting("GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")).strip()
    if gemini_key and genai is not None:
        try:
            client = genai.Client(api_key=gemini_key)
            history_str = ""
            for msg in conversation_history[-6:]:
                role_lbl = "Interviewer" if msg.get("role") in ("assistant", "ai") else "Candidate"
                history_str += f"{role_lbl}: {msg.get('content', '')}\n"

            prompt = f"""You are an expert technical interviewer evaluating a candidate for the role: {target_role}.
Job Requirements Skills: {', '.join(required_skills[:6]) if required_skills else 'Software Engineering'}
Candidate Profile Skills: {', '.join(candidate_skills[:6]) if candidate_skills else 'General Software'}

Original Question: "{original_question}"
Category: {original_question_category or 'Technical Assessment'}

Conversation History so far:
{history_str}

Candidate's Latest Answer:
"{candidate_answer}"

Follow-ups already asked for this topic: {follow_up_count}
Maximum follow-ups allowed: {max_follow_ups}

Instructions:
1. If the candidate's answer is extremely short or vague, ask a clarifying question.
2. If the candidate makes broad/unsupported claims (e.g., "optimized performance", "made it fast"), ask for specific metrics, profilers, or concrete technical changes.
3. If the candidate mentions specific technologies, tools, algorithms, or projects, ask for implementation details, architecture decisions, trade-offs, or evaluation metrics.
4. If the candidate's answer is off-topic, redirect back to {target_role} and the question topic.
5. If the candidate has already answered the follow-up with sufficient technical depth, set action to "next_question".
6. Do NOT repeat any questions already asked in the conversation history.

Respond in strict JSON with no extra markdown:
{{
  "action": "follow_up" or "next_question",
  "is_follow_up": true or false,
  "reasoning": "brief explanation",
  "follow_up_question": "the question to ask if action is follow_up, or null",
  "transition_prefix": "brief transition text if action is next_question, or null"
}}"""
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            raw_text = (response.text or "").strip()
            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                action = parsed.get("action", "follow_up")
                fq = (parsed.get("follow_up_question") or "").strip()
                if action == "follow_up" and fq and fq.lower() not in previously_asked:
                    return {
                        "action": "follow_up",
                        "is_follow_up": True,
                        "reasoning": parsed.get("reasoning", "Gemini AI adaptive evaluation"),
                        "follow_up_question": fq,
                        "transition_prefix": None
                    }
                elif action == "next_question":
                    return {
                        "action": "next_question",
                        "is_follow_up": False,
                        "reasoning": parsed.get("reasoning", "Gemini AI topic explored"),
                        "follow_up_question": None,
                        "transition_prefix": parsed.get("transition_prefix") or "Great explanation. Let's move on to the next question."
                    }
        except Exception as e:
            # Graceful fallback to deterministic intelligent engine
            print(f"[!] Notice: Gemini AI evaluation fallback ({e}). Using intelligent rule engine.")

    # RULE 3: Intelligent Rule-Based & NLP Follow-Up Evaluator (Offline & Fallback)

    # 3a. Extremely short / non-substantive answer (< 5 words or < 20 chars or one-word evasions)
    short_evasive = {"yes", "no", "yeah", "yep", "sure", "ok", "okay", "none", "nothing", "idk", "python", "used it", "done", "fine"}
    if words_count < 5 or len(ans_clean) < 20 or (words_count < 8 and ans_lower in short_evasive):
        return {
            "action": "follow_up",
            "is_follow_up": True,
            "reasoning": "Candidate answer was very brief; requesting elaboration and practical details.",
            "follow_up_question": "Your response was quite brief. Could you elaborate on your specific hands-on approach and share a concrete example from your experience?",
            "transition_prefix": None
        }

    # 3b. Vague / Unsupported claims (claims of optimization/speed/scale without concrete evidence)
    vague_claims = [
        r"\b(?:made\s+it\s+(?:very\s+)?(?:fast|faster|better|scalable|smooth))\b",
        r"\b(?:optimized\s+(?:everything|performance|the\s+system|all\s+code))\b",
        r"\b(?:improved\s+(?:performance|speed|scalability)\s+significantly)\b",
        r"\b(?:used\s+best\s+practices)\b",
        r"\b(?:handled\s+(?:everything|all\s+tasks|all\s+requirements))\b",
        r"\b(?:worked\s+on\s+many\s+projects)\b",
        r"\b(?:did\s+everything\s+according\s+to\s+spec)\b"
    ]
    has_vague_claim = any(re.search(pat, ans_lower) for pat in vague_claims)
    has_concrete_metrics = bool(re.search(r"\b(\d+[%xms]|latency|profiler|throughput|benchmark|cprofile|datadog|prometheus|new\s*relic|p99|p95|rps|qps|sla|auc|roc)\b", ans_lower))

    if has_vague_claim and not has_concrete_metrics and words_count < 35:
        return {
            "action": "follow_up",
            "is_follow_up": True,
            "reasoning": "Candidate made broad performance/scalability claims without supporting evidence.",
            "follow_up_question": "You mentioned optimizing performance and using best practices. What specific metrics or profiling tools did you use to measure the bottleneck, and what concrete technical changes delivered that improvement?",
            "transition_prefix": None
        }

    # 3c. Irrelevant / Off-topic answer
    off_topic_indicators = [
        "weather", "movie", "dinner", "lunch", "breakfast", "hobby", "football",
        "cricket", "basketball", "soccer", "recipe", "cooking", "song", "music",
        "vacation", "holiday", "dog", "cat", "pet", "joke", "politics", "president",
        "weekend", "party", "tennis"
    ]
    tech_domain_terms = {
        "python", "java", "javascript", "typescript", "c++", "c#", "go", "rust", "php", "ruby",
        "code", "coding", "software", "system", "systems", "api", "apis", "backend", "frontend",
        "database", "data", "pipeline", "service", "services", "server", "architecture", "architected",
        "performance", "optimization", "optimize", "optimized", "fast", "faster", "speed",
        "scale", "scalable", "scalability", "latency", "throughput", "algorithm", "algorithms",
        "model", "models", "ml", "ai", "machine learning", "deep learning", "classifier",
        "classification", "regression", "xgboost", "random forest", "neural", "training",
        "evaluation", "metrics", "auc", "roc", "accuracy", "precision", "recall", "sla", "onnx",
        "container", "docker", "kubernetes", "cloud", "aws", "azure", "gcp", "deploy", "deployment",
        "production", "testing", "test", "unit", "tdd", "ci/cd", "git", "framework", "library",
        "practices", "engineering", "developer", "development", "refactor", "cache", "caching",
        "redis", "sql", "nosql", "query", "queries", "schema", "table", "tables", "network",
        "http", "rest", "restful", "json", "endpoint", "security", "auth", "authentication",
        "authorization", "token", "jwt", "validation", "error", "exception", "logging", "monitor",
        "monitoring", "prometheus", "grafana", "design", "async", "queue", "celery", "kafka"
    }
    prev_q_tokens = []
    for m in reversed(conversation_history):
        if isinstance(m, dict) and m.get("role") in ("assistant", "ai"):
            prev_q_tokens = [w for w in re.findall(r"[a-zA-Z0-9+#.]+", m.get("content", "").lower()) if w not in STOPWORDS and len(w) > 2]
            break

    all_tax_skills = [s.lower() for cat in SKILL_TAXONOMY.values() for s in cat]
    has_taxonomy_mention = any(t in all_tax_skills for t in tokens) or any(s in ans_lower for s in all_tax_skills if len(s) > 2)
    has_tech_domain = any(term in ans_lower for term in tech_domain_terms)
    q_tokens = [w for w in re.findall(r"[a-zA-Z0-9+#.]+", original_question.lower()) if w not in STOPWORDS and len(w) > 2]
    context_tokens = set(q_tokens).union(set(prev_q_tokens))
    overlap_with_context = set(tokens).intersection(context_tokens)

    has_off_topic_words = any(re.search(rf"\b{re.escape(m)}\b", ans_lower) for m in off_topic_indicators)
    is_off_topic = (has_off_topic_words and not has_taxonomy_mention and not has_tech_domain) or (
        has_off_topic_words and len(overlap_with_context) == 0
    ) or (
        not has_taxonomy_mention and not has_tech_domain and len(overlap_with_context) == 0 and words_count >= 5
    )

    if is_off_topic:
        topic_term = original_question_category or "this technical area"
        return {
            "action": "follow_up",
            "is_follow_up": True,
            "reasoning": "Candidate response appears off-topic; redirecting to the question context.",
            "follow_up_question": f"That seems somewhat separate from our focus on {topic_term}. In the context of {target_role}, could you focus specifically on how you addressed the core technical requirements?",
            "transition_prefix": None
        }

    # 3d. If candidate already answered a follow-up (follow_up_count >= 1) with sufficient technical depth
    if follow_up_count >= 1 and (words_count >= 20 or (has_concrete_metrics and words_count >= 10)):
        return {
            "action": "next_question",
            "is_follow_up": False,
            "reasoning": "Candidate answered the follow-up with sufficient technical depth; topic sufficiently explored.",
            "follow_up_question": None,
            "transition_prefix": "Great explanation of your approach, technical decisions, and trade-offs. Let's move on to the next question."
        }

    # 3e. Candidate mentions specific technologies / tools / frameworks
    extracted_skills = extract_skills_from_text(candidate_answer)
    tech_candidates = []

    if any(w in ans_lower for w in ["machine learning", "ml", "algorithms", "scikit-learn", "deep learning", "neural network", "model"]):
        tech_candidates.append((
            "You mentioned using Python for machine learning. Could you explain which machine-learning algorithms you implemented and how you evaluated their performance?",
            "Exploring machine learning algorithms and performance evaluation"
        ))

    if any(w in ans_lower for w in ["rest api", "restful", "api", "apis", "fastapi", "flask", "django", "endpoints"]):
        tech_candidates.append((
            "How did you handle API authentication, error handling, and validation in that project?",
            "Exploring REST API design, authentication, and validation"
        ))

    if any(w in ans_lower for w in ["docker", "kubernetes", "k8s", "container", "containers"]):
        tech_candidates.append((
            "How did you manage container configuration, build optimization, and secret management in that deployment pipeline?",
            "Exploring container orchestration and deployment practices"
        ))

    if any(w in ans_lower for w in ["sql", "postgresql", "mysql", "database", "postgres", "queries", "indexing"]):
        tech_candidates.append((
            "How did you approach database schema design, indexing, and query optimization to handle concurrent read and write operations?",
            "Exploring database schema and query optimization"
        ))

    if any(w in ans_lower for w in ["redis", "cache", "caching", "memcached"]):
        tech_candidates.append((
            "What caching strategies (such as cache-aside or write-through) and TTL invalidation policies did you implement to maintain data consistency?",
            "Exploring caching patterns and invalidation strategies"
        ))

    if any(w in ans_lower for w in ["microservices", "microservice", "distributed", "kafka", "rabbitmq"]):
        tech_candidates.append((
            "How did you handle inter-service communication, distributed tracing, and data consistency across your microservices?",
            "Exploring distributed microservices architecture"
        ))

    if any(w in ans_lower for w in ["aws", "azure", "gcp", "google cloud", "s3", "lambda", "cloud"]):
        tech_candidates.append((
            "Which specific cloud services did you leverage, and how did you architect the system for high availability and fault tolerance?",
            "Exploring cloud infrastructure and fault tolerance"
        ))

    if any(w in ans_lower for w in ["testing", "unit test", "pytest", "tdd", "ci/cd", "test suite"]):
        tech_candidates.append((
            "What testing strategies, mock fixtures, and CI automation checks did you enforce to prevent regressions before production release?",
            "Exploring testing rigor and automation"
        ))

    if extracted_skills:
        for sk in extracted_skills:
            tech_candidates.append((
                f"You mentioned using {sk}. Could you share an example of a technical challenge you encountered while using {sk} and how you resolved it?",
                f"Exploring practical technical challenge with {sk}"
            ))

    # Select the first tech question that has not been asked yet
    for q_text, r_text in tech_candidates:
        if q_text.strip().lower() not in previously_asked:
            return {
                "action": "follow_up",
                "is_follow_up": True,
                "reasoning": r_text,
                "follow_up_question": q_text,
                "transition_prefix": None
            }

    # 3f. Detailed technical answer on initial question (follow_up_count == 0)
    # Probe deeper into architecture, scalability, trade-offs, and edge cases
    if follow_up_count == 0:
        candidates = [
            "That's a solid architectural overview. What were the most critical trade-offs or constraints you encountered in that design, and how would you handle a significant spike in traffic or data volume?",
            "Could you walk me through the error handling, edge cases, and failure recovery mechanisms you put in place for that solution?",
            "How did you verify the reliability of that system, and what production monitoring metrics did you establish?"
        ]
        for q_cand in candidates:
            if q_cand.strip().lower() not in previously_asked:
                return {
                    "action": "follow_up",
                    "is_follow_up": True,
                    "reasoning": "Detailed technical answer; probing architectural trade-offs and edge cases.",
                    "follow_up_question": q_cand,
                    "transition_prefix": None
                }

    # Default: Advance to next question
    return {
        "action": "next_question",
        "is_follow_up": False,
        "reasoning": "Topic sufficiently explored; transitioning to next original question.",
        "follow_up_question": None,
        "transition_prefix": "Thank you for those details. Let's move on to the next question."
    }

# Routes
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/health")
def health():
    db_health = database.get_db_health()
    return jsonify({
        "status": "online",
        "public_ready": True,
        "auto_logout_on_close": True,
        "session_timeout_minutes": 15,
        "environment": "vercel_serverless" if IS_VERCEL else "standalone",
        "database_type": db_health["database_type"],
        "database_configured": db_health["database_configured"],
        "persistent_database": db_health["persistent_database"],
        "storage_mode": db_health["storage_mode"],
        "is_ephemeral": False,
        "database_connected": db_health["connected"],
        "database_error": db_health.get("error"),
        "database_host": db_health.get("database_host"),
        "database_path": db_health.get("database_path"),
        "gemini_live_llm": bool(get_setting("GEMINI_API_KEY")),
        "resume_classifier_loaded": classifier_pipeline is not None,
        "resume_classifier_error": classifier_load_error if classifier_pipeline is None else None
    })

@app.route("/api/classifier/status", methods=["GET"])
def classifier_status():
    return jsonify({
        "loaded": classifier_pipeline is not None,
        "model_path": CLASSIFIER_PATH,
        "error": classifier_load_error if classifier_pipeline is None else None,
        "num_classes": len(classifier_pipeline.classes_) if classifier_pipeline and hasattr(classifier_pipeline, "classes_") else 0,
        "classes": classifier_pipeline.classes_.tolist() if classifier_pipeline and hasattr(classifier_pipeline, "classes_") else []
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
    VALUES (?, ?, ?, ?, 1) RETURNING id
    """, (username, email, pwd_hash, full_name))
    row = cursor.fetchone()
    user_id = row[0] if row else cursor.lastrowid
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
        return jsonify({"error": "Invalid credentials."}), 401

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
        return jsonify({"error": "Admin users cannot submit ratings."}), 403

    if user.get("is_banned"):
        return jsonify({"error": "Your account has been suspended."}), 403

    if user.get("has_rated"):
        return jsonify({"error": "You have already submitted a rating."}), 409

    data = request.json or {}
    raw_rating = data.get("rating")
    if not isinstance(raw_rating, int) or isinstance(raw_rating, bool):
        return jsonify({"error": "Rating must be an integer between 1 and 5."}), 400

    rating = raw_rating
    if rating < 1 or rating > 5:
        return jsonify({"error": "Rating must be an integer between 1 and 5."}), 400

    raw_comment = data.get("comment") if data.get("comment") is not None else data.get("rating_comment")
    rating_comment = (raw_comment or "").strip()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users 
    SET has_rated = 1, rating = ?, rating_comment = ?, rated_at = ? 
    WHERE id = ? AND (has_rated IS NULL OR has_rated = 0)
    """, (rating, rating_comment, now_str, user["id"]))

    if cursor.rowcount == 0:
        conn.rollback()
        return jsonify({"error": "You have already submitted a rating."}), 409

    conn.commit()

    return jsonify({
        "success": True, 
        "message": "Thank you for rating!",
        "rating": rating,
        "rating_comment": rating_comment
    })

@app.route("/api/cv/analyze", methods=["POST"])
def api_cv_analyze():
    data = request.json or {}
    cv_text = str(data.get("cv_text") or "")
    job_req = str(data.get("job_requirements") or "")
    filename = str(data.get("filename") or "resume.pdf")
    
    user = get_current_user()
    candidate_name = user.get("full_name") if user else "Candidate"
    
    # 1. Predicted resume category from trained ML classifier (supplementary domain classification)
    category_result = predict_resume_category(cv_text)
    pred_category = category_result.get("predicted_category")

    # 2. Existing skill-gap analysis, evidence-based strengths, and dynamic career tracks
    analysis = analyze_cv_algorithm(cv_text, job_req, candidate_name, predicted_category=pred_category)
    analysis["filename"] = filename
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    analysis["created_at"] = now_str
    analysis["predicted_category"] = pred_category
    analysis["category_confidence"] = category_result.get("confidence")
    analysis["category_classification"] = category_result
    
    # Save into cv_reports
    user_id = user.get("id") if user else None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO cv_reports (user_id, filename, saved_filepath, candidate_name, readiness_score, target_role, analysis_json)
    VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id
    """, (user_id, filename, filename, candidate_name, analysis["overall_readiness_score"], "Target Company Requirements", json.dumps(analysis)))
    row = cursor.fetchone()
    report_id = row[0] if row else cursor.lastrowid
    conn.commit()
    
    analysis["report_id"] = report_id
    return jsonify({"success": True, "analysis": analysis})

# -----------------------------
# Report & Session Access Control Endpoints
# -----------------------------
@app.route("/api/reports", methods=["GET"])
def get_saved_reports():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401
        
    conn = get_db()
    cursor = conn.cursor()
    
    # Strict Access Control:
    # Admin can access all candidate reports across the platform.
    # Regular users can only access their own reports alone!
    if user.get("is_admin"):
        cursor.execute("""
        SELECT r.id, r.id as report_id, r.user_id, r.filename, r.candidate_name, r.readiness_score, 
               r.target_role, r.created_at, r.analysis_json, u.username, u.email
        FROM cv_reports r
        LEFT JOIN users u ON r.user_id = u.id
        ORDER BY r.id DESC
        """)
    else:
        cursor.execute("""
        SELECT r.id, r.id as report_id, r.user_id, r.filename, r.candidate_name, r.readiness_score, 
               r.target_role, r.created_at, r.analysis_json, u.username, u.email
        FROM cv_reports r
        LEFT JOIN users u ON r.user_id = u.id
        WHERE r.user_id = ?
        ORDER BY r.id DESC
        """, (user["id"],))
        
    rows = cursor.fetchall()
    reports = [dict(r) for r in rows]
    return jsonify({"success": True, "reports": reports, "is_admin": bool(user.get("is_admin"))})

@app.route("/api/reports/<int:report_id>", methods=["GET"])
def get_single_report(report_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT r.*, u.username, u.email 
    FROM cv_reports r 
    LEFT JOIN users u ON r.user_id = u.id 
    WHERE r.id = ?
    """, (report_id,))
    row = cursor.fetchone()
    
    if not row:
        return jsonify({"error": "Report not found."}), 404
        
    report = dict(row)
    
    # Access Control Enforcement:
    # If not admin and not the owner of the report, forbid access!
    if not user.get("is_admin") and report.get("user_id") != user.get("id"):
        return jsonify({
            "error": "Forbidden: Access denied. Users cannot access other users' saved reports. Only the owner or an administrator can access this report."
        }), 403
        
    return jsonify({"success": True, "report": report})

@app.route("/api/sessions", methods=["GET"])
def get_saved_sessions():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401
        
    conn = get_db()
    cursor = conn.cursor()
    
    if user.get("is_admin"):
        cursor.execute("""
        SELECT s.*, u.username, u.email 
        FROM interview_sessions s 
        LEFT JOIN users u ON s.user_id = u.id 
        ORDER BY s.created_at DESC
        """)
    else:
        cursor.execute("""
        SELECT s.*, u.username, u.email 
        FROM interview_sessions s 
        LEFT JOIN users u ON s.user_id = u.id 
        WHERE s.user_id = ? 
        ORDER BY s.created_at DESC
        """, (user["id"],))
        
    rows = cursor.fetchall()
    sessions = [dict(s) for s in rows]
    return jsonify({"success": True, "sessions": sessions, "is_admin": bool(user.get("is_admin"))})

# -----------------------------
# Adaptive Mock Interview Endpoints
# -----------------------------
@app.route("/api/interview/start", methods=["POST"])
def interview_start():
    data = request.json or {}
    session_id = (data.get("session_id") or f"sess_{int(datetime.datetime.now().timestamp() * 1000)}").strip()
    candidate_name = (data.get("candidate_name") or "Candidate").strip()
    target_role = (data.get("target_role") or "Software Engineer").strip()
    cv_id = data.get("cv_id")
    raw_questions = data.get("questions") or []

    if not raw_questions:
        default_analysis = analyze_cv_algorithm("Python SQL Docker REST API", "Python AWS", candidate_name)
        raw_questions = default_analysis.get("likely_interview_questions", [])

    total_questions = len(raw_questions)
    user = get_current_user()
    user_id = user["id"] if user else None

    conn = get_db()
    cursor = conn.cursor()

    # Check if session already exists
    cursor.execute("SELECT session_id, current_question_index FROM interview_sessions WHERE session_id = ?", (session_id,))
    existing = cursor.fetchone()

    if not existing:
        cursor.execute("""
        INSERT INTO interview_sessions (session_id, user_id, cv_id, candidate_name, target_role, status, total_questions, current_question_index, questions_json)
        VALUES (?, ?, ?, ?, ?, 'in_progress', ?, 0, ?)
        """, (session_id, user_id, cv_id, candidate_name, target_role, total_questions, json.dumps(raw_questions)))

    first_q = raw_questions[0].get("question", "Tell me about your technical background.") if raw_questions else "Tell me about your technical background."
    welcome_content = f"👋 Welcome {candidate_name}! I'm your AI Interviewer for **{target_role}**.\n\nLet's begin with Question 1:\n\n**{first_q}**"

    cursor.execute("SELECT id FROM interview_messages WHERE session_id = ? AND role = 'assistant' AND question_index = 0", (session_id,))
    if not cursor.fetchone():
        cursor.execute("""
        INSERT INTO interview_messages (session_id, role, question_index, content, feedback_json)
        VALUES (?, 'assistant', 0, ?, ?)
        """, (session_id, welcome_content, json.dumps({"action": "original_question", "is_follow_up": False, "question_index": 0})))

    conn.commit()

    return jsonify({
        "success": True,
        "session_id": session_id,
        "current_question_index": 0,
        "follow_up_count": 0,
        "total_questions": total_questions,
        "message": welcome_content
    })


@app.route("/api/interview/message", methods=["POST"])
def interview_message():
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    candidate_answer = (data.get("answer") or "").strip()
    cv_text = str(data.get("cv_text") or "")
    job_req = str(data.get("job_requirements") or "")
    custom_max_follow_ups = data.get("max_follow_ups")

    if not session_id:
        return jsonify({"error": "session_id is required."}), 400
    if not candidate_answer:
        return jsonify({"error": "answer cannot be empty."}), 400

    max_follow_ups = int(custom_max_follow_ups) if custom_max_follow_ups is not None else get_max_follow_ups()

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,))
    session_row = cursor.fetchone()

    user = get_current_user()
    user_id = user["id"] if user else None

    if not session_row:
        cand_name = user.get("full_name") if user else "Candidate"
        default_analysis = analyze_cv_algorithm(cv_text or "Python SQL", job_req or "Python AWS", cand_name)
        questions = default_analysis.get("likely_interview_questions", [])
        cursor.execute("""
        INSERT INTO interview_sessions (session_id, user_id, candidate_name, target_role, status, total_questions, current_question_index, questions_json)
        VALUES (?, ?, ?, 'Software Engineer', 'in_progress', ?, 0, ?)
        """, (session_id, user_id, cand_name, len(questions), json.dumps(questions)))
        conn.commit()
        cursor.execute("SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,))
        session_row = cursor.fetchone()

    session_dict = dict(session_row) if session_row else {}
    current_q_index = session_dict.get("current_question_index") or 0
    raw_questions_json = session_dict.get("questions_json") or "[]"
    try:
        questions = json.loads(raw_questions_json)
    except Exception:
        questions = []

    if not questions:
        questions = analyze_cv_algorithm(cv_text or "Python SQL", job_req or "Python AWS", "Candidate").get("likely_interview_questions", [])

    total_questions = len(questions)
    current_q_index = min(current_q_index, max(0, total_questions - 1))
    current_q_obj = questions[current_q_index] if current_q_index < len(questions) else {"question": "Describe your technical background.", "category": "Technical"}

    # Fetch previous messages for this session
    cursor.execute("SELECT * FROM interview_messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    messages_rows = cursor.fetchall()
    conversation_history = [dict(m) for m in messages_rows]

    # Calculate current follow-up count for current_q_index
    current_q_follow_ups = 0
    for m in conversation_history:
        if m.get("role") == "assistant" and m.get("question_index") == current_q_index:
            fb = m.get("feedback_json") or "{}"
            try:
                fb_data = json.loads(fb) if isinstance(fb, str) else fb
                if fb_data.get("is_follow_up"):
                    current_q_follow_ups += 1
            except Exception:
                if "follow-up question" in (m.get("content") or "").lower():
                    current_q_follow_ups += 1

    # 1. Save candidate's answer into interview_messages
    cursor.execute("""
    INSERT INTO interview_messages (session_id, role, question_index, content)
    VALUES (?, 'user', ?, ?)
    """, (session_id, current_q_index, candidate_answer))
    conn.commit()

    conversation_history.append({"role": "user", "question_index": current_q_index, "content": candidate_answer})

    # 2. Run adaptive follow-up analysis
    candidate_skills = extract_skills_from_text(cv_text) if cv_text else []
    required_skills = extract_skills_from_text(job_req) if job_req else []

    eval_result = analyze_interview_response(
        original_question=current_q_obj.get("question", ""),
        original_question_category=current_q_obj.get("category", "Technical"),
        candidate_answer=candidate_answer,
        conversation_history=conversation_history,
        follow_up_count=current_q_follow_ups,
        max_follow_ups=max_follow_ups,
        candidate_skills=candidate_skills,
        required_skills=required_skills,
        target_role=session_dict.get("target_role") or "Software Engineer",
        cv_text=cv_text,
        job_requirements=job_req
    )

    action = eval_result["action"]
    reasoning = eval_result.get("reasoning", "")

    if action == "follow_up":
        new_follow_up_count = current_q_follow_ups + 1
        fq_text = eval_result["follow_up_question"]
        ai_message = f"🔍 **Follow-up Question:**\n\n{fq_text}"

        cursor.execute("""
        INSERT INTO interview_messages (session_id, role, question_index, content, feedback_json)
        VALUES (?, 'assistant', ?, ?, ?)
        """, (session_id, current_q_index, ai_message, json.dumps({
            "action": "follow_up",
            "is_follow_up": True,
            "follow_up_count": new_follow_up_count,
            "reasoning": reasoning
        })))
        conn.commit()

        return jsonify({
            "success": True,
            "action": "follow_up",
            "is_follow_up": True,
            "follow_up_count": new_follow_up_count,
            "max_follow_ups": max_follow_ups,
            "current_question_index": current_q_index,
            "total_questions": total_questions,
            "ai_message": ai_message,
            "status": "in_progress"
        })

    elif action == "next_question":
        next_q_index = current_q_index + 1
        trans_prefix = eval_result.get("transition_prefix") or "Thank you for sharing those insights. Let's move on to the next question."

        if next_q_index < total_questions:
            next_q = questions[next_q_index]
            next_q_num = next_q_index + 1
            next_q_cat = next_q.get("category", "Interview")
            next_q_text = next_q.get("question", "")
            ai_message = f"{trans_prefix}\n\n**Question {next_q_num} ({next_q_cat}):**\n\n{next_q_text}"

            cursor.execute("UPDATE interview_sessions SET current_question_index = ? WHERE session_id = ?", (next_q_index, session_id))
            cursor.execute("""
            INSERT INTO interview_messages (session_id, role, question_index, content, feedback_json)
            VALUES (?, 'assistant', ?, ?, ?)
            """, (session_id, next_q_index, ai_message, json.dumps({
                "action": "next_question",
                "is_follow_up": False,
                "follow_up_count": 0,
                "reasoning": reasoning
            })))
            conn.commit()

            return jsonify({
                "success": True,
                "action": "next_question",
                "is_follow_up": False,
                "follow_up_count": 0,
                "max_follow_ups": max_follow_ups,
                "current_question_index": next_q_index,
                "total_questions": total_questions,
                "ai_message": ai_message,
                "status": "in_progress"
            })
        else:
            # Interview Completed!
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            target_role = session_dict.get("target_role") or "Target Role"
            ai_message = (
                f"🎉 **Interview Completed!**\n\n"
                f"Thank you for completing this technical mock interview for **{target_role}**. "
                f"You have answered all {total_questions} questions and follow-ups with thoughtful technical rigor.\n\n"
                f"Your complete interview transcript has been saved to your account history."
            )
            cursor.execute("UPDATE interview_sessions SET status = 'completed', completed_at = ? WHERE session_id = ?", (now_str, session_id))
            cursor.execute("""
            INSERT INTO interview_messages (session_id, role, question_index, content, feedback_json)
            VALUES (?, 'assistant', ?, ?, ?)
            """, (session_id, current_q_index, ai_message, json.dumps({
                "action": "completed",
                "is_follow_up": False,
                "follow_up_count": 0,
                "reasoning": "All questions completed"
            })))
            conn.commit()

            return jsonify({
                "success": True,
                "action": "completed",
                "is_follow_up": False,
                "follow_up_count": 0,
                "max_follow_ups": max_follow_ups,
                "current_question_index": current_q_index,
                "total_questions": total_questions,
                "ai_message": ai_message,
                "status": "completed"
            })


@app.route("/api/interview/session/<session_id>", methods=["GET"])
def get_interview_session_details(session_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Interview session not found."}), 404

    session_data = dict(row)
    user = get_current_user()
    if user and not user.get("is_admin") and session_data.get("user_id") and session_data.get("user_id") != user.get("id"):
        return jsonify({"error": "Forbidden: Access denied to this session."}), 403

    cursor.execute("SELECT * FROM interview_messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    messages = [dict(m) for m in cursor.fetchall()]

    curr_idx = session_data.get("current_question_index") or 0
    follow_up_cnt = 0
    for m in messages:
        if m.get("role") == "assistant" and m.get("question_index") == curr_idx:
            try:
                fb = json.loads(m.get("feedback_json") or "{}")
                if fb.get("is_follow_up"):
                    follow_up_cnt += 1
            except Exception:
                if "follow-up question" in (m.get("content") or "").lower():
                    follow_up_cnt += 1

    return jsonify({
        "success": True,
        "session": session_data,
        "messages": messages,
        "current_question_index": curr_idx,
        "follow_up_count": follow_up_cnt,
        "total_questions": session_data.get("total_questions") or 5
    })


@app.route("/api/interview/session/<session_id>/transcript", methods=["GET"])
def get_interview_session_transcript(session_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Interview session not found."}), 404

    session_data = dict(row)
    user = get_current_user()
    if user and not user.get("is_admin") and session_data.get("user_id") and session_data.get("user_id") != user.get("id"):
        return jsonify({"error": "Forbidden: Access denied to this session."}), 403

    cursor.execute("SELECT * FROM interview_messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    messages = [dict(m) for m in cursor.fetchall()]

    return jsonify({
        "success": True,
        "session_id": session_id,
        "candidate_name": session_data.get("candidate_name") or "Candidate",
        "target_role": session_data.get("target_role") or "Target Role",
        "status": session_data.get("status") or "in_progress",
        "created_at": str(session_data.get("created_at") or ""),
        "completed_at": str(session_data.get("completed_at") or ""),
        "messages": messages
    })

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
    cursor.execute("SELECT rating, COUNT(*) as cnt FROM users WHERE has_rated = 1 AND rating >= 1 AND rating <= 5 AND is_admin = 0 GROUP BY rating")
    ratings_raw = {int(row["rating"]): int(row["cnt"]) for row in cursor.fetchall()}
    ratings_distribution = {star: ratings_raw.get(star, 0) for star in range(1, 6)}
    total_ratings = sum(ratings_distribution.values())
    
    cursor.execute("SELECT AVG(rating) as avg_r FROM users WHERE has_rated = 1 AND rating >= 1 AND rating <= 5 AND is_admin = 0")
    avg_row = cursor.fetchone()
    avg_rating_val = avg_row["avg_r"] if avg_row else None
    avg_rating = round(float(avg_rating_val), 1) if (avg_rating_val is not None and total_ratings > 0) else None

    # Real Candidate Readiness & Progress from cv_reports
    cursor.execute("SELECT readiness_score, analysis_json FROM cv_reports")
    cv_rows = cursor.fetchall()
    total_cv_reports = len(cv_rows)
    
    readiness_brackets = {"critical_gap": 0, "moderate": 0, "high_match": 0, "interview_ready": 0}
    skill_gap_counts = Counter()
    total_match_score_sum = 0
    
    for r in cv_rows:
        sc = r["readiness_score"] or 0
        total_match_score_sum += sc
        if sc < 45:
            readiness_brackets["critical_gap"] += 1
        elif sc < 70:
            readiness_brackets["moderate"] += 1
        elif sc < 85:
            readiness_brackets["high_match"] += 1
        else:
            readiness_brackets["interview_ready"] += 1
            
        try:
            an_data = json.loads(r["analysis_json"] or "{}")
            for gap in an_data.get("skill_gaps", []):
                skill_gap_counts[gap] += 1
        except Exception:
            pass

    avg_candidate_match = round(total_match_score_sum / total_cv_reports, 1) if total_cv_reports > 0 else 0
    top_skill_gaps = [{"skill": k, "count": v} for k, v in skill_gap_counts.most_common(6)]

    # Detailed User Accounts & Rating Feedback Logs
    cursor.execute("SELECT id, username, email, full_name, login_count, has_rated, rating, rating_comment, rated_at, is_banned, is_admin, created_at FROM users ORDER BY id DESC")
    users = [dict(u) for u in cursor.fetchall()]

    suspicious_count = 0
    rated_user_details = []
    
    for u in users:
        is_fake, reasons, risk_score = check_fake_credential(u["username"], u["email"], u["full_name"], u["login_count"])
        u["is_suspicious"] = is_fake
        u["risk_score"] = risk_score
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
                "rated_at": str(u["rated_at"] or u["created_at"])
            })

    # Precise Continuous Growth Trend Analytics (Zero-Filled Calendar Interpolation)
    def compute_precise_growth_analytics(users_list, days=30):
        today = datetime.date.today()
        start_date = today - timedelta(days=days - 1)
        
        user_date_counts = Counter()
        prior_users = 0
        
        for u in users_list:
            raw_created = u.get("created_at") or ""
            try:
                u_date = datetime.datetime.fromisoformat(raw_created.replace("Z", "+00:00")).date()
            except Exception:
                try:
                    u_date = datetime.datetime.strptime(raw_created[:10], "%Y-%m-%d").date()
                except Exception:
                    u_date = today
                    
            if u_date < start_date:
                prior_users += 1
            else:
                user_date_counts[u_date] += 1
                
        timeline = []
        cumulative = prior_users
        total_new = 0
        peak_daily = 0
        
        curr = start_date
        while curr <= today:
            new_cnt = user_date_counts.get(curr, 0)
            cumulative += new_cnt
            total_new += new_cnt
            if new_cnt > peak_daily:
                peak_daily = new_cnt
                
            timeline.append({
                "date": curr.strftime("%Y-%m-%d"),
                "label": curr.strftime("%b %d"),
                "new_users": new_cnt,
                "cumulative_users": cumulative
            })
            curr += timedelta(days=1)
            
        growth_rate = round((total_new / max(1, prior_users)) * 100, 1) if prior_users > 0 else (100.0 if total_new > 0 else 0.0)
        
        return {
            "days": days,
            "timeline": timeline,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
            "prior_users": prior_users,
            "new_users_in_period": total_new,
            "total_current_users": cumulative,
            "period_growth_rate_pct": growth_rate,
            "peak_daily_signups": peak_daily
        }

    growth_7d = compute_precise_growth_analytics(users, days=7)
    growth_30d = compute_precise_growth_analytics(users, days=30)

    return jsonify({
        "success": True,
        "summary": {
            "total_users": total_users,
            "banned_users": banned_users,
            "suspicious_users": suspicious_count,
            "total_sessions": total_sessions,
            "total_cv_reports": total_cv_reports,
            "total_ratings": total_ratings,
            "avg_rating": avg_rating,
            "avg_candidate_match": avg_candidate_match,
            "ratings_distribution": ratings_distribution,
            "readiness_brackets": readiness_brackets,
            "top_skill_gaps": top_skill_gaps
        },
        "users": users,
        "rated_user_details": rated_user_details,
        "growth_timeline": growth_30d["timeline"],
        "growth_analytics": {
            "7d": growth_7d,
            "30d": growth_30d
        }
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

@app.errorhandler(RuntimeError)
def handle_runtime_error(e):
    err_msg = str(e)
    if "DATABASE_URL" in err_msg or "PERSISTENCE" in err_msg or "database" in err_msg.lower():
        return jsonify({
            "error": "Persistent Database Not Configured",
            "message": err_msg,
            "status": "database_configuration_required"
        }), 503
    return jsonify({"error": err_msg}), 500

application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("======================================================")
    print(f"[*] AI Interview Portal with Auto-Logout on Close running on http://localhost:{port}")
    print("======================================================")
    app.run(host="0.0.0.0", port=port, debug=False)
