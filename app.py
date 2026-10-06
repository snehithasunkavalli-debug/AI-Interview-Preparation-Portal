import os
import json
import re
import uuid
import sqlite3
import datetime
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

# CORS support for deployment and multi-device access
try:
    from flask_cors import CORS
except ImportError:
    CORS = None

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Allow overriding storage directory via environment variable (useful for cloud hosting/volumes)
STORAGE_DIR = os.environ.get("STORAGE_DIR", os.path.join(BASE_DIR, "storage"))
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
DB_PATH = os.path.join(STORAGE_DIR, "portal.db")

os.makedirs(UPLOADS_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = os.environ.get("SECRET_KEY", "dossier_secure_secret_key_standalone_2026")
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15MB upload limit

if CORS:
    CORS(app, supports_credentials=True)

ALLOWED_EXTENSIONS = {".pdf"}

# -----------------------------
# Database Initialization & Settings
# -----------------------------
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

    cursor.execute("PRAGMA table_info(cv_reports)")
    cv_columns = [col[1] for col in cursor.fetchall()]
    if "user_id" not in cv_columns:
        cursor.execute("ALTER TABLE cv_reports ADD COLUMN user_id INTEGER;")

    cursor.execute("PRAGMA table_info(interview_sessions)")
    sess_columns = [col[1] for col in cursor.fetchall()]
    if "user_id" not in sess_columns:
        cursor.execute("ALTER TABLE interview_sessions ADD COLUMN user_id INTEGER;")

    conn.commit()
    conn.close()

# Initialize DB structure on startup
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
    cursor.execute("SELECT id, username, email, full_name, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return dict(row) if row else None

# -----------------------------
# Skills & Role Taxonomy
# -----------------------------
SKILL_TAXONOMY = {
    "Languages": [
        "Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "C", "Go", "Golang", "Rust", 
        "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "Dart", "HTML", "HTML5", "CSS", "CSS3", "Sass", "SQL", "Bash", "Shell"
    ],
    "Frameworks & Libraries": [
        "Flask", "Django", "FastAPI", "React", "React.js", "Next.js", "Vue", "Vue.js", "Angular", 
        "Node.js", "Express", "Express.js", "Spring", "Spring Boot", "ASP.NET", ".NET Core", 
        "Tailwind CSS", "Bootstrap", "Redux", "PyTorch", "TensorFlow", "Keras", "Scikit-Learn", 
        "Pandas", "NumPy", "OpenCV", "LangChain", "Hugging Face", "GraphQL"
    ],
    "Databases & Storage": [
        "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch", "Cassandra", 
        "DynamoDB", "Firebase", "Oracle", "MSSQL", "MariaDB", "Supabase", "Neo4j"
    ],
    "Cloud, DevOps & Tools": [
        "Docker", "Kubernetes", "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud", 
        "CI/CD", "GitHub Actions", "GitLab CI", "Jenkins", "Git", "GitHub", "GitLab", "Linux", 
        "Terraform", "Ansible", "Nginx", "Apache", "REST API", "gRPC", "Microservices", 
        "Serverless", "Kafka", "RabbitMQ", "Celery", "Postman", "Jira"
    ],
    "Soft Skills & Concepts": [
        "Agile", "Scrum", "Problem Solving", "System Design", "Object Oriented Programming", 
        "Design Patterns", "Data Structures", "Algorithms", "Unit Testing", "TDD", "Code Review", 
        "Team Leadership", "Cross-Functional Collaboration", "CI/CD Pipeline"
    ]
}

ROLE_PROFILES = {
    "Python / Backend Developer": {
        "core_skills": ["Python", "Flask", "Django", "FastAPI", "SQL", "PostgreSQL", "REST API", "Docker", "Git", "Unit Testing"],
        "keywords": ["backend", "python", "flask", "django", "fastapi", "api", "database", "sql"]
    },
    "Full Stack Developer": {
        "core_skills": ["JavaScript", "TypeScript", "React", "Node.js", "Python", "SQL", "REST API", "Git", "HTML", "CSS", "Docker"],
        "keywords": ["full stack", "fullstack", "frontend", "backend", "web development", "react", "node"]
    },
    "Frontend Developer": {
        "core_skills": ["JavaScript", "TypeScript", "React", "Next.js", "Vue", "HTML5", "CSS3", "Tailwind CSS", "Redux", "Git"],
        "keywords": ["frontend", "front-end", "ui", "ux", "react", "vue", "javascript", "css", "html"]
    },
    "AI / Machine Learning Engineer": {
        "core_skills": ["Python", "PyTorch", "TensorFlow", "Scikit-Learn", "Pandas", "NumPy", "Machine Learning", "Deep Learning", "NLP", "Docker", "Git"],
        "keywords": ["machine learning", "deep learning", "ai", "artificial intelligence", "nlp", "computer vision", "data science", "pytorch", "tensorflow"]
    },
    "DevOps / Cloud Engineer": {
        "core_skills": ["Docker", "Kubernetes", "AWS", "Linux", "CI/CD", "Terraform", "Git", "Python", "Bash", "Nginx"],
        "keywords": ["devops", "cloud", "aws", "kubernetes", "docker", "infrastructure", "ci/cd", "terraform"]
    },
    "Data Engineer": {
        "core_skills": ["Python", "SQL", "PostgreSQL", "Spark", "Kafka", "Airflow", "Pandas", "Docker", "AWS", "ETL"],
        "keywords": ["data engineer", "etl", "data pipeline", "sql", "spark", "hadoop", "airflow", "warehouse"]
    },
    "Software Engineer - General": {
        "core_skills": ["Python", "Java", "C++", "JavaScript", "SQL", "Git", "Data Structures", "Algorithms", "OOP", "Problem Solving"],
        "keywords": ["software engineer", "developer", "programmer", "coding", "software development"]
    }
}

# -----------------------------
# Extraction Helpers
# -----------------------------
def extract_text_from_pdf(file_stream_or_path):
    text_chunks = []
    try:
        with pdfplumber.open(file_stream_or_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    text_chunks.append(text)
    except Exception as e:
        print(f"pdfplumber failed: {e}. Trying pypdf...")
        if PdfReader:
            try:
                reader = PdfReader(file_stream_or_path)
                for page in reader.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        text_chunks.append(text)
            except Exception as e2:
                print(f"pypdf extraction error: {e2}")
                
    return "\n\n".join(text_chunks).strip()

def extract_contact_info(text):
    email_match = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
    email = email_match.group(0) if email_match else ""
    
    phone_match = re.search(r'(\+?\d{1,3}[-.\s]?)?(\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}', text)
    phone = phone_match.group(0) if phone_match else ""
    
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    candidate_name = "Candidate"
    for line in lines[:5]:
        if len(line) < 40 and not re.search(r'(@|http|resume|curriculum|phone|email|linkedin|github|\.com)', line, re.I):
            clean = re.sub(r'[^a-zA-Z\s.-]', '', line).strip()
            if len(clean.split()) >= 1 and len(clean) > 2:
                candidate_name = clean
                break
                
    return candidate_name, email, phone

def extract_skills(text):
    found_skills = []
    text_lower = " " + re.sub(r'[,|/()\[\]{}:;]', ' ', text.lower()) + " "
    
    for category, skill_list in SKILL_TAXONOMY.items():
        for skill in skill_list:
            escaped = re.escape(skill.lower())
            if skill.lower() in ["c", "r"]:
                pattern = r'(?<=\s)' + escaped + r'(?=\s|,|\.)'
            elif skill.lower() in ["c++", "c#", ".net core"]:
                pattern = re.escape(skill.lower())
            else:
                pattern = r'\b' + escaped + r'\b'
                
            if re.search(pattern, text_lower):
                if skill not in found_skills:
                    found_skills.append(skill)
                    
    return found_skills

def estimate_experience(text):
    matches = re.findall(r'\b(20[0-2][0-9]|199[0-9])\b', text)
    current_year = datetime.datetime.now().year
    
    year_numbers = []
    for m in matches:
        val = int(m)
        if 1990 <= val <= current_year:
            year_numbers.append(val)
            
    if year_numbers:
        min_yr = min(year_numbers)
        max_yr = max(year_numbers)
        if re.search(r'\b(present|current|now)\b', text, re.I):
            max_yr = current_year
        exp = max(0, min(25, max_yr - min_yr))
    else:
        exp_match = re.search(r'(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience', text, re.I)
        if exp_match:
            exp = min(25, int(exp_match.group(1)))
        else:
            exp = 2
            
    if exp <= 1:
        level = "Entry Level / Junior"
    elif exp <= 4:
        level = "Mid-Level Professional"
    elif exp <= 8:
        level = "Senior Specialist"
    else:
        level = "Lead / Staff / Principal"
        
    return level, exp

def determine_roles_and_gaps(skills, text):
    skills_set = set([s.lower() for s in skills])
    text_lower = text.lower()
    
    scores = {}
    for role, data in ROLE_PROFILES.items():
        role_score = 0
        for kw in data["keywords"]:
            if kw in text_lower:
                role_score += 2
        for core_s in data["core_skills"]:
            if core_s.lower() in skills_set:
                role_score += 3
        scores[role] = role_score
        
    sorted_roles = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_role = sorted_roles[0][0] if sorted_roles else "Software Engineer - General"
    suggested_roles = [r[0] for r in sorted_roles[:3] if r[1] > 0]
    if not suggested_roles:
        suggested_roles = ["Software Engineer - General", "Backend Developer", "Full Stack Developer"]
        
    core_for_best = ROLE_PROFILES.get(best_role, {}).get("core_skills", [])
    gaps = [s for s in core_for_best if s.lower() not in skills_set]
    if not gaps:
        gaps = ["System Architecture at Scale", "Advanced CI/CD & Cloud Orchestration", "Performance Profiling & Observability"]
    else:
        gaps = gaps[:5]
        
    return best_role, suggested_roles, gaps

def generate_strengths_and_score(candidate_name, skills, exp_years, exp_level, best_role, text):
    strengths = []
    if len(skills) >= 8:
        strengths.append(f"Broad technical skill set spanning {len(skills)} identified technologies across development, databases, and tooling.")
    elif len(skills) > 0:
        strengths.append(f"Demonstrated core competence in {', '.join(skills[:4])}.")
        
    if exp_years >= 3:
        strengths.append(f"{exp_years}+ years of cumulative industry / project experience demonstrating professional delivery ({exp_level}).")
    else:
        strengths.append(f"Strong foundation for {exp_level} roles with demonstrable practical project exposure.")
        
    tech_highlights = [s for s in skills if s in ["Docker", "Kubernetes", "AWS", "FastAPI", "React", "PyTorch", "PostgreSQL", "Flask"]]
    if tech_highlights:
        strengths.append(f"Practical familiarity with in-demand industry tools: {', '.join(tech_highlights[:4])}.")
    else:
        strengths.append(f"Clear capability alignment for target track in {best_role}.")
        
    base_score = 65
    base_score += min(15, len(skills) * 1.5)
    base_score += min(12, exp_years * 2)
    if "Docker" in skills or "AWS" in skills or "Kubernetes" in skills or "CI/CD" in skills:
        base_score += 5
    if len(strengths) >= 3:
        base_score += 3
        
    score = int(max(50, min(95, base_score)))
    return strengths, score

def generate_interview_questions(candidate_name, skills, best_role, gaps, exp_level):
    questions = []
    top_skill = skills[0] if skills else "Software Development"
    
    questions.append({
        "question": f"Can you walk me through an architecture or application you built using {top_skill}? What were the major technical challenges you faced, and how did you resolve them?",
        "category": "Technical Experience & Architecture",
        "why_asked": f"Tests depth of practical experience in {top_skill} and ability to articulate engineering trade-offs.",
        "ideal_points": [
            "Use STAR method (Situation, Task, Action, Result).",
            f"Highlight specific {top_skill} libraries, design patterns, or APIs utilized.",
            "Explain the bottleneck/issue (e.g. latency, concurrency, schema design) and quantitative outcome."
        ]
    })
    
    db_skills = [s for s in skills if s in SKILL_TAXONOMY["Databases & Storage"]]
    chosen_db = db_skills[0] if db_skills else "SQL / Relational Databases"
    questions.append({
        "question": f"How do you approach database performance optimization and data consistency when working with {chosen_db} in production?",
        "category": "Data & Performance",
        "why_asked": f"Evaluates practical knowledge of query optimization, indexing, transaction isolation, and caching strategies.",
        "ideal_points": [
            "Mention indexing strategies, query execution plans (EXPLAIN ANALYZE).",
            "Discuss connection pooling, caching (e.g. Redis), or normalized vs denormalized schemas.",
            "Address concurrency handling and ACID transaction guarantees."
        ]
    })
    
    questions.append({
        "question": "Tell me about a time when a critical bug or production issue occurred right before a deadline or release. How did you diagnose, triage, and communicate the resolution?",
        "category": "Behavioral & Incident Management",
        "why_asked": "Evaluates composure under pressure, root-cause analysis methodology, and stakeholder communication.",
        "ideal_points": [
            "Explain how you quickly reproduced and isolated the problem (logs, metrics, debuggers).",
            "Demonstrate clear team communication and rollback/hotfix strategy.",
            "Highlight post-mortem actions taken to prevent recurrence (tests, alerts, CI/CD checks)."
        ]
    })
    
    if gaps:
        gap = gaps[0]
        questions.append({
            "question": f"Our team works heavily with {gap}. How would you ramp up quickly on {gap}, and what related principles from your background would you apply?",
            "category": "Adaptability & Growth",
            "why_asked": f"Assesses learning agility and how candidate bridges skill gaps in {gap}.",
            "ideal_points": [
                "Acknowledge the core concepts of the tool/technology.",
                "Detail your systematic learning strategy (hands-on mini projects, official docs, best practices).",
                "Draw parallels from existing skills in your portfolio."
            ]
        })
    else:
        questions.append({
            "question": "How do you ensure your code is maintainable, well-tested, and ready for continuous deployment?",
            "category": "Engineering Standards & CI/CD",
            "why_asked": "Tests commitment to quality, automated testing (unit/integration), and modern DevOps practices.",
            "ideal_points": [
                "Mention unit tests, integration tests, mock objects, and coverage metrics.",
                "Discuss clean architecture, linting, code reviews, and automated CI pipelines."
            ]
        })
        
    questions.append({
        "question": f"For a modern {best_role} position, how do you handle API security, authentication (JWT/OAuth), and rate limiting to prevent abuse?",
        "category": "Security & System Design",
        "why_asked": "Assesses security hygiene, defense in depth, and practical API design understanding.",
        "ideal_points": [
            "Explain token expiration, refresh token rotation, and secret storage.",
            "Discuss middleware-level rate limiting (token bucket / sliding window via Redis).",
            "Mention input validation, HTTPS/TLS, CORS configuration, and least privilege access."
        ]
    })
    
    return questions

def analyze_cv_ai(cv_text, filename=""):
    candidate_name, email, phone = extract_contact_info(cv_text)
    skills = extract_skills(cv_text)
    exp_level, exp_years = estimate_experience(cv_text)
    best_role, suggested_roles, gaps = determine_roles_and_gaps(skills, cv_text)
    strengths, readiness_score = generate_strengths_and_score(candidate_name, skills, exp_years, exp_level, best_role, cv_text)
    questions = generate_interview_questions(candidate_name, skills, best_role, gaps, exp_level)
    
    gemini_key = get_setting("GEMINI_API_KEY", "").strip()
    
    if gemini_key and genai:
        try:
            client = genai.Client(api_key=gemini_key)
            prompt = f"""
            You are an expert technical hiring manager and interview panelist.
            Analyze the following candidate CV text and return a JSON object with this EXACT structure:
            {{
                "candidate_name": "{candidate_name}",
                "experience_level": "{exp_level}",
                "years_of_experience_estimate": {exp_years},
                "top_skills": {json.dumps(skills[:12] if skills else ['Python', 'SQL', 'Git'])},
                "skill_gaps": {json.dumps(gaps)},
                "strengths": {json.dumps(strengths)},
                "suggested_roles": {json.dumps(suggested_roles)},
                "overall_readiness_score": {readiness_score},
                "likely_interview_questions": [
                    {{
                        "question": "precise technical or behavioral interview question based on their resume",
                        "category": "category name",
                        "why_asked": "concise rationale on what is being assessed",
                        "ideal_points": ["key element 1", "key element 2", "key element 3"]
                    }}
                ]
            }}

            CV Text Content:
            {cv_text[:4000]}
            """
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            parsed = json.loads(response.text)
            if isinstance(parsed, dict) and "top_skills" in parsed:
                parsed["ai_engine"] = "Gemini 2.0 Flash (Live AI)"
                return parsed, email, phone, True
        except Exception as e:
            print(f"Gemini API analysis failed: {e}. Using local intelligent NLP engine.")
            
    analysis_data = {
        "candidate_name": candidate_name,
        "email": email,
        "phone": phone,
        "experience_level": exp_level,
        "years_of_experience_estimate": exp_years,
        "top_skills": skills[:12] if skills else ["Python", "Problem Solving", "Web Development", "SQL", "Git"],
        "skill_gaps": gaps,
        "strengths": strengths,
        "suggested_roles": suggested_roles,
        "overall_readiness_score": readiness_score,
        "likely_interview_questions": questions,
        "ai_engine": "Local Intelligent NLP Engine (Offline AI)"
    }
    return analysis_data, email, phone, False

def evaluate_candidate_response(question_item, answer_text, target_role="Software Engineer"):
    words = answer_text.strip().split()
    word_count = len(words)
    answer_lower = answer_text.lower()
    
    gemini_key = get_setting("GEMINI_API_KEY", "").strip()
    if gemini_key and genai and word_count >= 5:
        try:
            client = genai.Client(api_key=gemini_key)
            prompt = f"""
            You are a seasoned hiring interviewer evaluating a candidate's answer for a {target_role} position.
            
            Question: {question_item.get('question')}
            Category: {question_item.get('category')}
            Candidate's Answer: {answer_text}
            Evaluation Criteria / Ideal Points: {json.dumps(question_item.get('ideal_points', []))}

            Evaluate the response strictly but constructively. Return a JSON object with:
            {{
                "score": float between 1.0 and 10.0,
                "verdict": "short 2-5 word summary headline",
                "strengths": ["strength 1", "strength 2"],
                "improvements": ["actionable improvement 1", "actionable improvement 2"],
                "model_answer": "a crisp 3-sentence model answer using the STAR technique"
            }}
            """
            res = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3
                )
            )
            parsed = json.loads(res.text)
            if "score" in parsed and "verdict" in parsed:
                parsed["score"] = round(float(parsed["score"]), 1)
                parsed["ai_powered"] = True
                return parsed
        except Exception as e:
            print(f"Gemini evaluation failed: {e}. Falling back to local heuristic evaluator.")

    # Local Heuristic Rubric
    star_terms = ["situation", "task", "action", "result", "because", "implemented", "resolved", 
                  "designed", "reduced", "increased", "optimized", "team", "project", "metric", "scale", "tested"]
    star_found = [t for t in star_terms if t in answer_lower]
    
    ideal_points = question_item.get("ideal_points", [])
    ideal_text = " ".join(ideal_points).lower()
    ideal_keywords = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', ideal_text) if w not in ["with", "that", "this", "have", "from", "when", "your", "they"]]
    matched_keywords = [k for k in set(ideal_keywords) if k in answer_lower]
    
    if word_count < 15:
        score = 4.0
        verdict = "Too Brief — Needs More Substance"
        strengths = ["Direct acknowledgment of the topic."]
        improvements = [
            "Elaborate with specific technical details, tools, and constraints.",
            "Structure your response with the STAR framework (Situation, Task, Action, Result).",
            "Quantify your results or describe the technical outcome clearly."
        ]
    elif word_count < 40:
        score = 6.0 + min(1.5, len(star_found) * 0.4)
        verdict = "Solid Overview — Add Tactical Depth"
        strengths = [
            "Good concise framing of the answer.",
            f"Included key relevant aspects ({', '.join(star_found[:2]) if star_found else 'problem context'})."
        ]
        improvements = [
            "Include concrete examples from past projects or production environments.",
            "Explain *why* you chose your technical approach over alternative architectures."
        ]
    elif word_count < 120:
        score = 7.5 + min(2.0, len(star_found) * 0.5 + len(matched_keywords) * 0.2)
        verdict = "Strong, Well-Articulated Answer"
        strengths = [
            "Clear explanation with strong narrative flow.",
            "Effectively touched on technical trade-offs and implementation steps.",
            "Good demonstration of engineering mindset and problem-solving."
        ]
        improvements = [
            "Mention monitoring, metrics, or test coverage that validated the solution.",
            "Highlight teamwork or cross-functional alignment if applicable."
        ]
    else:
        score = 8.5 + min(1.5, len(star_found) * 0.3)
        verdict = "Comprehensive & High-Impact Response"
        strengths = [
            "Extensive detail covering the full lifecycle of the problem.",
            "Strong command of technical vocabulary and architectural nuance.",
            "Thorough STAR storytelling showing direct leadership and ownership."
        ]
        improvements = [
            "Keep an eye on conciseness so the interviewer has time to ask follow-up questions."
        ]
        
    score = round(min(10.0, max(3.0, score)), 1)
    
    model_answer = (
        f"In my previous project, we faced a similar challenge when optimizing our service architecture. "
        f"I took ownership by implementing a structured approach with proper error handling, caching, and automated testing. "
        f"As a result, we enhanced reliability, cut latency by 35%, and ensured seamless deployment."
    )
    
    return {
        "score": score,
        "verdict": verdict,
        "strengths": strengths,
        "improvements": improvements,
        "model_answer": model_answer,
        "ai_powered": False
    }

# -----------------------------
# Authentication Routes
# -----------------------------
@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    data = request.json or {}
    username = (data.get("username") or "").strip().lower()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    full_name = (data.get("full_name") or "").strip() or username

    if not username or not email or not password:
        return jsonify({"error": "Username, email, and password are required."}), 400

    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ? OR email = ?", (username, email))
    existing = cursor.fetchone()
    if existing:
        return jsonify({"error": "Username or email already registered. Please sign in."}), 409

    pwd_hash = generate_password_hash(password)
    cursor.execute("""
    INSERT INTO users (username, email, password_hash, full_name)
    VALUES (?, ?, ?, ?)
    """, (username, email, pwd_hash, full_name))
    user_id = cursor.lastrowid
    conn.commit()

    session["user_id"] = user_id
    return jsonify({
        "success": True,
        "user": {
            "id": user_id,
            "username": username,
            "email": email,
            "full_name": full_name
        }
    })

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.json or {}
    login_id = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not login_id or not password:
        return jsonify({"error": "Username/email and password are required."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ? OR email = ?", (login_id, login_id))
    user = cursor.fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password."}), 401

    session["user_id"] = user["id"]
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user["full_name"]
        }
    })

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"success": True, "message": "Logged out successfully."})

@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    user = get_current_user()
    return jsonify({"success": True, "user": user})

# -----------------------------
# AI Settings Routes
# -----------------------------
@app.route("/api/settings/ai", methods=["GET"])
def get_ai_settings():
    gemini_key = get_setting("GEMINI_API_KEY", "")
    masked = f"{gemini_key[:4]}...{gemini_key[-4:]}" if len(gemini_key) > 8 else ("Configured" if gemini_key else "")
    return jsonify({
        "success": True,
        "has_gemini_key": bool(gemini_key),
        "masked_key": masked,
        "sdk_available": bool(genai)
    })

@app.route("/api/settings/ai", methods=["POST"])
def save_ai_settings():
    data = request.json or {}
    gemini_key = (data.get("gemini_api_key") or "").strip()
    set_setting("GEMINI_API_KEY", gemini_key)
    return jsonify({
        "success": True,
        "message": "AI settings updated successfully!",
        "has_gemini_key": bool(gemini_key)
    })

# -----------------------------
# Application & Analysis Routes
# -----------------------------
@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/health")
def health():
    return jsonify({
        "status": "online",
        "storage_mode": "Local PC Storage (SQLite & Local Disk)",
        "uploads_path": UPLOADS_DIR,
        "database_path": DB_PATH,
        "gemini_live_llm": bool(get_setting("GEMINI_API_KEY"))
    })

@app.route("/api/analyze-cv", methods=["POST"])
def analyze_cv_route():
    user = get_current_user()
    user_id = user["id"] if user else None

    if "cv_file" not in request.files:
        return jsonify({"error": "No file uploaded. Please select a PDF CV."}), 400

    file = request.files["cv_file"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only PDF files (.pdf) are supported."}), 400

    try:
        user_prefix = f"u{user_id}_" if user_id else "guest_"
        unique_filename = f"{user_prefix}{uuid.uuid4().hex[:8]}_{re.sub(r'[^a-zA-Z0-9_.-]', '_', file.filename)}"
        saved_filepath = os.path.join(UPLOADS_DIR, unique_filename)
        file.save(saved_filepath)
        
        cv_text = extract_text_from_pdf(saved_filepath)
        if not cv_text or len(cv_text.strip()) < 30:
            return jsonify({
                "error": "Could not extract readable text from this PDF. Please ensure it is not a scanned image PDF."
            }), 422

        analysis, email, phone, ai_used = analyze_cv_ai(cv_text, file.filename)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO cv_reports (user_id, filename, saved_filepath, candidate_name, candidate_email, candidate_phone, 
                                experience_level, years_exp, readiness_score, target_role, analysis_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            file.filename,
            saved_filepath,
            analysis.get("candidate_name", user["full_name"] if user else "Candidate"),
            email or (user["email"] if user else ""),
            phone,
            analysis.get("experience_level", "Mid-Level"),
            analysis.get("years_of_experience_estimate", 2),
            analysis.get("overall_readiness_score", 75),
            (analysis.get("suggested_roles") or ["Software Engineer"])[0],
            json.dumps(analysis)
        ))
        report_id = cursor.lastrowid
        conn.commit()

        analysis["report_id"] = report_id
        analysis["stored_on_pc"] = True
        analysis["file_name"] = file.filename

        return jsonify({
            "success": True,
            "report_id": report_id,
            "analysis": analysis,
            "ai_engine": analysis.get("ai_engine", "Local NLP Engine")
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Analysis failed: {str(e)}"}), 500

@app.route("/api/reports", methods=["GET"])
def get_reports():
    user = get_current_user()
    user_id = user["id"] if user else None

    conn = get_db()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
        SELECT id, filename, candidate_name, candidate_email, experience_level, years_exp, readiness_score, target_role, created_at 
        FROM cv_reports 
        WHERE user_id = ?
        ORDER BY id DESC
        """, (user_id,))
    else:
        cursor.execute("""
        SELECT id, filename, candidate_name, candidate_email, experience_level, years_exp, readiness_score, target_role, created_at 
        FROM cv_reports 
        WHERE user_id IS NULL
        ORDER BY id DESC
        """)
        
    rows = [dict(r) for r in cursor.fetchall()]
    return jsonify({"success": True, "reports": rows})

@app.route("/api/reports/<int:report_id>", methods=["GET"])
def get_report_detail(report_id):
    user = get_current_user()
    user_id = user["id"] if user else None

    conn = get_db()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT * FROM cv_reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    else:
        cursor.execute("SELECT * FROM cv_reports WHERE id = ?", (report_id,))
        
    row = cursor.fetchone()
    if not row:
        return jsonify({"error": "Report not found."}), 404
        
    data = dict(row)
    data["analysis"] = json.loads(data["analysis_json"])
    return jsonify({"success": True, "report": data})

@app.route("/api/reports/<int:report_id>", methods=["DELETE"])
def delete_report(report_id):
    user = get_current_user()
    user_id = user["id"] if user else None

    conn = get_db()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT saved_filepath FROM cv_reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    else:
        cursor.execute("SELECT saved_filepath FROM cv_reports WHERE id = ?", (report_id,))
        
    row = cursor.fetchone()
    if row and os.path.exists(row["saved_filepath"]):
        try:
            os.remove(row["saved_filepath"])
        except Exception:
            pass
            
    if user_id:
        cursor.execute("DELETE FROM cv_reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    else:
        cursor.execute("DELETE FROM cv_reports WHERE id = ?", (report_id,))
        
    conn.commit()
    return jsonify({"success": True, "message": "Report deleted from PC storage."})

@app.route("/api/download/cv/<int:report_id>")
def download_cv(report_id):
    user = get_current_user()
    user_id = user["id"] if user else None

    conn = get_db()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("SELECT saved_filepath, filename FROM cv_reports WHERE id = ? AND user_id = ?", (report_id, user_id))
    else:
        cursor.execute("SELECT saved_filepath, filename FROM cv_reports WHERE id = ?", (report_id,))
        
    row = cursor.fetchone()
    if not row or not os.path.exists(row["saved_filepath"]):
        return jsonify({"error": "File not found on local storage."}), 404
    return send_file(row["saved_filepath"], as_attachment=True, download_name=row["filename"])

# -----------------------------
# Interview Chat Endpoints
# -----------------------------
@app.route("/api/chat/start", methods=["POST"])
def start_interview_chat():
    user = get_current_user()
    user_id = user["id"] if user else None

    data = request.json or {}
    cv_id = data.get("cv_id")
    target_role = data.get("role", "Software Engineer")
    candidate_name = data.get("candidate_name", user["full_name"] if user else "Candidate")
    
    questions = []
    if cv_id:
        conn = get_db()
        cursor = conn.cursor()
        if user_id:
            cursor.execute("SELECT * FROM cv_reports WHERE id = ? AND user_id = ?", (cv_id, user_id))
        else:
            cursor.execute("SELECT * FROM cv_reports WHERE id = ?", (cv_id,))
        row = cursor.fetchone()
        if row:
            parsed = json.loads(row["analysis_json"])
            candidate_name = row["candidate_name"] or candidate_name
            target_role = data.get("role") or row["target_role"] or target_role
            questions = parsed.get("likely_interview_questions", [])
            
    if not questions:
        questions = generate_interview_questions(candidate_name, ["Python", "Problem Solving", "System Architecture", "Git"], target_role, ["Distributed Systems"], "Mid-Level")
        
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO interview_sessions (session_id, user_id, cv_id, candidate_name, target_role, total_questions, current_question_index, questions_json)
    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    """, (session_id, user_id, cv_id, candidate_name, target_role, len(questions), json.dumps(questions)))
    
    first_q = questions[0]
    ai_status = "🤖 Gemini AI Live" if get_setting("GEMINI_API_KEY") else "⚡ Local AI Engine"
    welcome_text = (
        f"👋 Welcome {candidate_name}! I'm your AI Technical Interviewer ({ai_status}) for the **{target_role}** position.\n\n"
        f"We have {len(questions)} tailored interview questions today. Let's begin with Question 1:\n\n"
        f"**{first_q['question']}**"
    )
    
    cursor.execute("""
    INSERT INTO interview_messages (session_id, role, question_index, content)
    VALUES (?, 'assistant', 0, ?)
    """, (session_id, welcome_text))
    
    conn.commit()
    
    return jsonify({
        "success": True,
        "session_id": session_id,
        "candidate_name": candidate_name,
        "target_role": target_role,
        "total_questions": len(questions),
        "current_question_index": 0,
        "current_question": first_q,
        "initial_message": welcome_text
    })

@app.route("/api/chat/message", methods=["POST"])
def send_interview_message():
    data = request.json or {}
    session_id = data.get("session_id")
    user_answer = (data.get("message") or "").strip()
    
    if not session_id or not user_answer:
        return jsonify({"error": "Session ID and answer text are required."}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,))
    session_row = cursor.fetchone()
    
    if not session_row:
        return jsonify({"error": "Session not found."}), 404
        
    q_index = session_row["current_question_index"]
    questions = json.loads(session_row["questions_json"] or "[]")
    
    if q_index >= len(questions):
        return jsonify({"success": True, "interview_completed": True, "message": "Interview is already complete!"})
        
    curr_q = questions[q_index]
    feedback = evaluate_candidate_response(curr_q, user_answer, session_row["target_role"])
    score = feedback["score"]
    
    cursor.execute("""
    INSERT INTO interview_messages (session_id, role, question_index, content, feedback_json, score)
    VALUES (?, 'user', ?, ?, ?, ?)
    """, (session_id, q_index, user_answer, json.dumps(feedback), score))
    
    next_q_index = q_index + 1
    is_completed = (next_q_index >= len(questions))
    
    if is_completed:
        cursor.execute("SELECT AVG(score) as avg_score FROM interview_messages WHERE session_id = ? AND role = 'user'", (session_id,))
        avg_score = cursor.fetchone()["avg_score"] or score
        overall_score = round(float(avg_score) * 10, 1)
        
        cursor.execute("""
        UPDATE interview_sessions 
        SET status = 'completed', current_question_index = ?, overall_score = ?, completed_at = CURRENT_TIMESTAMP
        WHERE session_id = ?
        """, (next_q_index, overall_score, session_id))
        
        summary_msg = (
            f"🎉 **Interview Complete!** Great job practicing today.\n\n"
            f"**Overall Interview Score:** {overall_score}/100\n"
            f"Your transcript and performance evaluation have been saved to your account in local PC storage."
        )
        cursor.execute("""
        INSERT INTO interview_messages (session_id, role, question_index, content)
        VALUES (?, 'assistant', ?, ?)
        """, (session_id, next_q_index, summary_msg))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "feedback": feedback,
            "interview_completed": True,
            "overall_score": overall_score,
            "summary_message": summary_msg,
            "next_question_index": next_q_index,
            "total_questions": len(questions)
        })
    else:
        next_q = questions[next_q_index]
        cursor.execute("""
        UPDATE interview_sessions SET current_question_index = ? WHERE session_id = ?
        """, (next_q_index, session_id))
        
        ai_reply = f"Great. Moving on to **Question {next_q_index + 1} of {len(questions)}**:\n\n**{next_q['question']}**"
        cursor.execute("""
        INSERT INTO interview_messages (session_id, role, question_index, content)
        VALUES (?, 'assistant', ?, ?)
        """, (session_id, next_q_index, ai_reply))
        
        conn.commit()
        
        return jsonify({
            "success": True,
            "feedback": feedback,
            "interview_completed": False,
            "next_question_index": next_q_index,
            "total_questions": len(questions),
            "next_question": next_q,
            "ai_message": ai_reply
        })

@app.route("/api/chat/session/<session_id>", methods=["GET"])
def get_session_details(session_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM interview_sessions WHERE session_id = ?", (session_id,))
    session_row = cursor.fetchone()
    if not session_row:
        return jsonify({"error": "Session not found."}), 404
        
    cursor.execute("SELECT * FROM interview_messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    messages = [dict(m) for m in cursor.fetchall()]
    
    for m in messages:
        if m.get("feedback_json"):
            m["feedback"] = json.loads(m["feedback_json"])
            
    return jsonify({
        "success": True,
        "session": dict(session_row),
        "messages": messages
    })

@app.route("/api/chat/sessions", methods=["GET"])
def get_all_sessions():
    user = get_current_user()
    user_id = user["id"] if user else None

    conn = get_db()
    cursor = conn.cursor()
    if user_id:
        cursor.execute("""
        SELECT session_id, cv_id, candidate_name, target_role, status, total_questions, current_question_index, overall_score, created_at, completed_at
        FROM interview_sessions
        WHERE user_id = ?
        ORDER BY created_at DESC
        """, (user_id,))
    else:
        cursor.execute("""
        SELECT session_id, cv_id, candidate_name, target_role, status, total_questions, current_question_index, overall_score, created_at, completed_at
        FROM interview_sessions
        WHERE user_id IS NULL
        ORDER BY created_at DESC
        """)
        
    rows = [dict(r) for r in cursor.fetchall()]
    return jsonify({"success": True, "sessions": rows})

@app.route("/api/chat/coach", methods=["POST"])
def coach_advice():
    data = request.json or {}
    question = (data.get("question") or "").strip()
    target_role = data.get("role", "Software Engineer")
    
    if not question:
        return jsonify({"error": "Question is required."}), 400
        
    gemini_key = get_setting("GEMINI_API_KEY", "").strip()
    if gemini_key and genai:
        try:
            client = genai.Client(api_key=gemini_key)
            prompt = f"As an elite tech interview coach, provide clear, structured, actionable advice for a candidate applying for {target_role} regarding this question/topic: {question}"
            res = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            return jsonify({"success": True, "advice": res.text})
        except Exception as e:
            print(f"Gemini Coach advice skipped: {e}")
            
    advice = (
        f"### 💡 Career Coach Strategy for {target_role}:\n\n"
        f"**1. Structure with STAR:**\n"
        f"- **Situation:** Briefly set the context (company, project scale, constraints).\n"
        f"- **Task:** Define your exact responsibility and the hurdle.\n"
        f"- **Action:** Deep-dive into your specific code, design choices, and technical actions.\n"
        f"- **Result:** Share measurable outcomes (e.g. latency dropped 40%, shipped ahead of schedule).\n\n"
        f"**2. Key Pro-Tips:**\n"
        f"- Never say 'I don't know' without immediately adding how you would research or prototype the answer.\n"
        f"- Speak aloud your thought process and trade-offs when tackling architectural questions.\n"
        f"- Align your answers with {target_role} core values: clean code, reliability, security, and scalability."
    )
    return jsonify({"success": True, "advice": advice})

# WSGI Application entry point for production deployments (Gunicorn / Waitress)
application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("======================================================")
    print(f"[*] AI Interview Portal Running on http://localhost:{port}")
    print(f"[*] Storage Mode: Local PC Disk & SQLite ({DB_PATH})")
    print("======================================================")
    app.run(host="0.0.0.0", port=port, debug=False)
