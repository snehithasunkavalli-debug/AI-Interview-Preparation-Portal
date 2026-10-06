import os
import json
import re
import uuid
import sqlite3
import datetime
import traceback
from flask import Flask, request, jsonify, send_from_directory, send_file, session
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

from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
DB_PATH = os.path.join(STORAGE_DIR, "portal.db")

os.makedirs(UPLOADS_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dossier_secure_secret_key_local_2026")
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE_MB = 15

# -----------------------------
# Database Initialization & Settings
# -----------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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

init_db()

def get_setting(key, default=""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["value"]
    return os.environ.get(key, default)

def set_setting(key, value):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, email, full_name, created_at FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
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
    ai_used = False
    
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
        conn.close()
        return jsonify({"error": "Username or email already registered. Please sign in."}), 409

    pwd_hash = generate_password_hash(password)
    cursor.execute("""
    INSERT INTO users (username, email, password_hash, full_name)
    VALUES (?, ?, ?, ?)
    """, (username, email, pwd_hash, full_name))
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()

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
    conn.close()

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
    return send_from_directory(".", "index.html")

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
        conn.close()

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
    conn.close()
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
    conn.close()
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
    conn.close()
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
    conn.close()
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
        conn.close()
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
    conn.close()
    
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
        conn.close()
        return jsonify({"error": "Session not found."}), 404
        
    q_index = session_row["current_question_index"]
    questions = json.loads(session_row["questions_json"] or "[]")
    
    if q_index >= len(questions):
        conn.close()
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
        conn.close()
        
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
        conn.close()
        
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
        conn.close()
        return jsonify({"error": "Session not found."}), 404
        
    cursor.execute("SELECT * FROM interview_messages WHERE session_id = ? ORDER BY id ASC", (session_id,))
    messages = [dict(m) for m in cursor.fetchall()]
    conn.close()
    
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
    conn.close()
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("======================================================")
    print(f"[*] AI Interview Portal Running on http://localhost:{port}")
    print(f"[*] Storage Mode: Local PC Disk & SQLite ({DB_PATH})")
    print("======================================================")
    app.run(host="0.0.0.0", port=port, debug=True)  index.html is <!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Dossier — AI Interview Prep &amp; Mock Interview Portal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {
    --ink: #0F1E24;
    --ink-dark: #0A1418;
    --ink-light: #182C35;
    --paper: #F8F4EC;
    --paper-dim: #ECE4D3;
    --paper-card: #FDFCFA;
    --teal: #286F63;
    --teal-dim: #1A4E45;
    --teal-light: #E7F3F0;
    --gold: #D69E2E;
    --gold-dim: #B7791F;
    --gold-light: #FEFCF5;
    --text: #1C1917;
    --text-soft: #57534E;
    --text-muted: #8C827A;
    --border: #D6CEBE;
    --danger: #C53030;
    --success: #2F855A;
    --radius: 8px;
    --shadow: 0 12px 36px -8px rgba(0,0,0,0.4);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--ink);
    color: var(--paper);
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  header {
    background: var(--ink-dark);
    border-bottom: 1px solid rgba(214, 206, 190, 0.12);
    padding: 14px 28px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 100;
    gap: 16px;
    flex-wrap: wrap;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand-logo {
    width: 32px;
    height: 32px;
    background: var(--gold);
    color: var(--ink);
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'Space Mono', monospace;
    font-weight: 700;
    font-size: 16px;
  }
  .brand-title {
    font-family: 'Fraunces', serif;
    font-size: 20px;
    font-weight: 700;
    color: var(--paper);
    letter-spacing: -0.02em;
  }
  .brand-badge {
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    background: rgba(214, 158, 46, 0.18);
    color: var(--gold);
    padding: 3px 8px;
    border-radius: 12px;
    border: 1px solid rgba(214, 158, 46, 0.3);
  }

  nav {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .nav-btn {
    background: transparent;
    border: 1px solid transparent;
    color: #C7C1B2;
    padding: 8px 14px;
    border-radius: 6px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .nav-btn:hover {
    color: var(--paper);
    background: rgba(255,255,255,0.06);
  }
  .nav-btn.active {
    background: var(--teal);
    color: var(--paper);
    border-color: var(--teal);
  }

  /* User Auth Area in Header */
  .header-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .ai-status-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 14px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    background: rgba(40, 111, 99, 0.25);
    border: 1px solid rgba(40, 111, 99, 0.5);
    color: #8CE0D0;
  }
  .ai-status-chip.live-ai {
    background: rgba(214, 158, 46, 0.2);
    border-color: rgba(214, 158, 46, 0.6);
    color: var(--gold);
  }

  .user-badge {
    display: flex;
    align-items: center;
    gap: 10px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.12);
    padding: 4px 12px;
    border-radius: 20px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
  }
  .user-badge .user-name {
    color: var(--gold);
    font-weight: 700;
  }
  .btn-logout {
    background: transparent;
    border: none;
    color: #C7C1B2;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
  }
  .btn-logout:hover { color: var(--danger); }

  .wrap {
    max-width: 1040px;
    width: 100%;
    margin: 0 auto;
    padding: 32px 20px 80px;
    flex: 1;
  }

  /* ----------------- AUTH SCREEN ----------------- */
  #viewAuth {
    max-width: 460px;
    margin: 40px auto;
  }
  .auth-card {
    background: var(--paper);
    color: var(--text);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .auth-tabs {
    display: flex;
    border-bottom: 1px solid var(--border);
    background: var(--paper-dim);
  }
  .auth-tab {
    flex: 1;
    text-align: center;
    padding: 14px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    cursor: pointer;
    background: transparent;
    border: none;
    color: var(--text-soft);
  }
  .auth-tab.active {
    background: var(--paper);
    color: var(--teal-dim);
    border-bottom: 2px solid var(--teal);
  }
  .auth-body { padding: 28px; }
  .form-group { margin-bottom: 16px; }
  .form-group label {
    display: block;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    margin-bottom: 6px;
    color: var(--text-soft);
  }
  .form-group input {
    width: 100%;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-family: 'Inter', sans-serif;
    font-size: 14px;
    background: var(--paper-card);
    color: var(--text);
  }
  .form-group input:focus {
    outline: none;
    border-color: var(--teal);
  }

  /* ----------------- AI SETTINGS MODAL ----------------- */
  .modal-backdrop {
    position: fixed;
    top: 0; left: 0; width: 100vw; height: 100vh;
    background: rgba(0,0,0,0.7);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    backdrop-filter: blur(4px);
  }
  .modal-backdrop.show { display: flex; }
  .modal-box {
    background: var(--paper);
    color: var(--text);
    border-radius: var(--radius);
    padding: 28px;
    max-width: 500px;
    width: 90%;
    box-shadow: var(--shadow);
  }
  .modal-box h3 {
    margin: 0 0 12px;
    font-family: 'Fraunces', serif;
    font-size: 22px;
    color: var(--ink-dark);
  }
  .modal-box p {
    font-size: 13px;
    line-height: 1.5;
    color: var(--text-soft);
    margin-bottom: 18px;
  }

  .hero { margin-bottom: 32px; }
  .eyebrow {
    font-family: 'Space Mono', monospace;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-size: 12px;
    color: var(--gold);
    margin-bottom: 10px;
  }
  h1 {
    font-family: 'Fraunces', serif;
    font-weight: 700;
    font-size: clamp(28px, 4vw, 42px);
    line-height: 1.15;
    margin: 0 0 12px;
    color: var(--paper);
  }
  .lede {
    color: #C7C1B2;
    font-size: 15px;
    line-height: 1.6;
    max-width: 680px;
    margin: 0;
  }

  .storage-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(40, 111, 99, 0.25);
    border: 1px solid rgba(40, 111, 99, 0.5);
    padding: 4px 10px;
    border-radius: 20px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    color: #8CE0D0;
    margin-top: 12px;
  }

  .dossier {
    background: var(--paper);
    color: var(--text);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    overflow: hidden;
    margin-bottom: 32px;
  }
  .dossier-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 24px;
    border-bottom: 1px dashed var(--border);
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-soft);
    background: var(--paper-dim);
  }
  .dossier-body { padding: 32px 24px; }

  .dropzone {
    border: 2px dashed #B9AE8E;
    border-radius: 6px;
    padding: 40px 20px;
    text-align: center;
    cursor: pointer;
    transition: all 0.2s ease;
    background: repeating-linear-gradient(135deg, transparent, transparent 10px, rgba(0,0,0,0.015) 10px, rgba(0,0,0,0.015) 20px);
    display: block;
  }
  .dropzone:hover, .dropzone.drag {
    border-color: var(--teal);
    background: #EAF2EF;
  }
  .dropzone h3 {
    font-family: 'Fraunces', serif;
    font-size: 22px;
    margin: 8px 0 4px;
    color: var(--text);
  }
  .dropzone p { color: var(--text-soft); font-size: 13px; margin: 0; }
  .dropzone input { display: none; }
  .file-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
    padding: 6px 14px;
    background: var(--teal-dim);
    color: var(--paper);
    border-radius: 20px;
    font-size: 12px;
    font-family: 'Space Mono', monospace;
  }

  button.btn-primary {
    width: 100%;
    margin-top: 20px;
    padding: 14px;
    background: var(--teal);
    color: var(--paper);
    border: none;
    border-radius: 6px;
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  button.btn-primary:hover:not(:disabled) {
    background: var(--teal-dim);
    box-shadow: 0 4px 12px rgba(40,111,99,0.35);
  }
  button.btn-primary:disabled {
    background: #A79E88;
    cursor: not-allowed;
    opacity: 0.7;
  }

  .report-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-top: 24px;
  }
  @media(max-width: 768px) { .report-grid { grid-template-columns: 1fr; } }

  .panel {
    background: var(--paper-card);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 20px;
  }
  .panel h4 {
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--teal);
    margin: 0 0 12px;
  }
  .full { grid-column: 1 / -1; }

  .tag {
    display: inline-block;
    background: var(--paper-dim);
    border: 1px solid #D8D0B8;
    border-radius: 16px;
    padding: 4px 11px;
    font-size: 12px;
    margin: 0 5px 6px 0;
    color: var(--text);
    font-weight: 500;
  }
  .gap-tag {
    background: #FDF0ED;
    border-color: #F3C4B8;
    color: #9B2C15;
  }
  .strength-item {
    font-size: 13px;
    line-height: 1.5;
    margin-bottom: 8px;
    padding-left: 18px;
    position: relative;
  }
  .strength-item::before {
    content: "✦";
    position: absolute;
    left: 0;
    color: var(--gold-dim);
  }

  .gauge-panel {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 28px;
    background: var(--teal-dim);
    color: var(--paper);
    border: none;
  }
  .gauge-panel h4 { color: var(--gold); }
  .gauge-panel .name {
    font-family: 'Fraunces', serif;
    font-size: 28px;
    font-weight: 700;
    margin: 2px 0 4px;
  }
  .gauge-panel .meta { color: #D5CFBE; font-size: 13px; font-family: 'Space Mono', monospace; }

  .qcard {
    background: var(--paper-dim);
    border-left: 4px solid var(--gold);
    border-radius: 4px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .qcard .qnum {
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    color: var(--gold-dim);
    font-weight: 700;
  }
  .qcard .qtext {
    font-family: 'Fraunces', serif;
    font-size: 16px;
    margin: 4px 0 6px;
    line-height: 1.4;
  }
  .qcard .why { font-size: 12px; color: var(--text-soft); }

  .action-banner {
    grid-column: 1 / -1;
    background: linear-gradient(135deg, var(--ink-light), var(--ink-dark));
    border: 1px solid rgba(214, 158, 46, 0.4);
    border-radius: 6px;
    padding: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: gap;
    gap: 16px;
    color: var(--paper);
  }
  .action-banner h3 {
    margin: 0 0 4px;
    font-family: 'Fraunces', serif;
    font-size: 20px;
    color: var(--paper);
  }
  .action-banner p { margin: 0; font-size: 13px; color: #C7C1B2; }
  .btn-gold {
    background: var(--gold);
    color: var(--ink);
    border: none;
    border-radius: 6px;
    padding: 12px 24px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    white-space: nowrap;
    transition: all 0.2s ease;
  }
  .btn-gold:hover {
    background: #E5AC3A;
    box-shadow: 0 4px 16px rgba(214, 158, 46, 0.4);
  }

  /* ----------------- CHAT BOX STYLING ----------------- */
  .chat-container {
    background: var(--paper);
    color: var(--text);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    height: 75vh;
    min-height: 600px;
    overflow: hidden;
  }
  .chat-header {
    background: var(--ink-dark);
    color: var(--paper);
    padding: 16px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid rgba(255,255,255,0.1);
  }
  .chat-title {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .avatar {
    width: 36px;
    height: 36px;
    background: var(--teal);
    color: var(--paper);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
  }
  .chat-info h3 { margin: 0; font-size: 15px; font-family: 'Fraunces', serif; }
  .chat-info p { margin: 2px 0 0; font-size: 11px; font-family: 'Space Mono', monospace; color: #A6A092; }

  .chat-progress {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .progress-pill {
    background: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 12px;
    padding: 4px 12px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    color: var(--gold);
  }

  .chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    background: #F5EFE4;
  }

  .msg-row {
    display: flex;
    gap: 12px;
    max-width: 85%;
  }
  .msg-row.ai { align-self: flex-start; }
  .msg-row.user { align-self: flex-end; flex-direction: row-reverse; }

  .bubble {
    padding: 16px 18px;
    border-radius: 8px;
    font-size: 14px;
    line-height: 1.5;
  }
  .msg-row.ai .bubble {
    background: var(--paper-card);
    color: var(--text);
    border: 1px solid var(--border);
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
  }
  .msg-row.user .bubble {
    background: var(--teal-dim);
    color: var(--paper);
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  }

  .feedback-box {
    margin-top: 10px;
    background: #FFFDF9;
    border: 1px solid #E2D9C8;
    border-radius: 6px;
    padding: 14px;
    font-size: 12px;
  }
  .feedback-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .score-badge {
    font-family: 'Space Mono', monospace;
    font-weight: 700;
    padding: 3px 8px;
    border-radius: 12px;
    font-size: 11px;
  }
  .score-high { background: #DEF7EC; color: #03543F; border: 1px solid #BCF0DA; }
  .score-mid { background: #FEF08A; color: #713F12; border: 1px solid #FDE047; }
  .score-low { background: #FDE8E8; color: #9B1C1C; border: 1px solid #F8B4B4; }

  .fb-section { margin-top: 6px; }
  .fb-label { font-weight: 700; font-family: 'Space Mono', monospace; font-size: 10px; color: var(--text-soft); text-transform: uppercase; }

  .chat-controls {
    background: var(--paper-card);
    border-top: 1px solid var(--border);
    padding: 16px 20px;
  }
  .input-wrapper {
    display: flex;
    gap: 10px;
    align-items: flex-end;
  }
  textarea.chat-input {
    flex: 1;
    min-height: 54px;
    max-height: 120px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-family: 'Inter', sans-serif;
    font-size: 14px;
    resize: none;
    background: var(--paper);
    color: var(--text);
  }
  textarea.chat-input:focus {
    outline: none;
    border-color: var(--teal);
  }
  .btn-icon {
    background: var(--paper-dim);
    border: 1px solid var(--border);
    border-radius: 6px;
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    font-size: 18px;
    transition: all 0.2s ease;
  }
  .btn-icon:hover { background: #E2D9C8; }
  .btn-icon.recording {
    background: var(--danger);
    color: white;
    animation: pulse 1.2s infinite;
  }
  @keyframes pulse {
    0% { transform: scale(1); }
    50% { transform: scale(1.06); }
    100% { transform: scale(1); }
  }

  .btn-send {
    background: var(--teal);
    color: var(--paper);
    border: none;
    border-radius: 6px;
    padding: 0 20px;
    height: 48px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .btn-send:hover { background: var(--teal-dim); }

  .coach-bar {
    display: flex;
    gap: 8px;
    margin-top: 10px;
    flex-wrap: wrap;
  }
  .chip-coach {
    background: var(--paper-dim);
    border: 1px solid var(--border);
    padding: 4px 10px;
    border-radius: 14px;
    font-size: 11px;
    font-family: 'Space Mono', monospace;
    cursor: pointer;
    color: var(--text-soft);
  }
  .chip-coach:hover { background: #DFD7C4; color: var(--text); }

  /* ----------------- HISTORY VIEW ----------------- */
  .table-card {
    background: var(--paper);
    color: var(--text);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 24px;
    margin-bottom: 24px;
  }
  .table-card h3 {
    font-family: 'Fraunces', serif;
    margin: 0 0 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  table.history-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  table.history-table th {
    text-align: left;
    padding: 10px 12px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
    color: var(--text-soft);
    border-bottom: 2px solid var(--border);
  }
  table.history-table td {
    padding: 12px;
    border-bottom: 1px solid var(--border);
  }
  table.history-table tr:hover { background: rgba(0,0,0,0.02); }

  .btn-sm {
    padding: 4px 10px;
    border-radius: 4px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    border: 1px solid var(--border);
    background: var(--paper-dim);
    cursor: pointer;
    margin-right: 4px;
  }
  .btn-sm:hover { background: #D9D0BD; }
  .btn-danger-sm { color: var(--danger); border-color: #F8B4B4; }
  .btn-danger-sm:hover { background: #FDE8E8; }

  /* Error & Loading */
  .error-box {
    margin-top: 16px;
    padding: 12px 16px;
    border-left: 3px solid var(--danger);
    background: #FBEAE4;
    color: #7A2E17;
    font-size: 13px;
    border-radius: 2px;
    display: none;
  }
  .loading-spinner {
    display: none;
    margin: 20px 0;
    text-align: center;
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    color: var(--gold);
  }
  .loading-spinner.show { display: block; }
</style>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-logo">AI</div>
    <div>
      <span class="brand-title">Dossier</span>
      <span class="brand-badge">Local PC Storage</span>
    </div>
  </div>
  
  <nav id="mainNav" style="display:none;">
    <button class="nav-btn active" id="tabCvBtn" onclick="switchTab('cv')">📄 CV Intake</button>
    <button class="nav-btn" id="tabChatBtn" onclick="switchTab('chat')">💬 Mock Interview</button>
    <button class="nav-btn" id="tabHistBtn" onclick="switchTab('history')">💾 Saved Reports</button>
  </nav>

  <div class="header-actions">
    <div class="ai-status-chip" id="aiStatusBadge" onclick="openAiSettingsModal()">
      <span>🤖 AI Engine: Local NLP</span>
    </div>

    <button class="nav-btn active" id="authNavBtn" onclick="switchTab('auth')">🔑 Sign In</button>
    <div id="loggedInUserBadge" class="user-badge" style="display:none;">
      <span>👤 <span id="headerUserName" class="user-name">User</span></span>
      <button class="btn-logout" onclick="logoutUser()">Logout</button>
    </div>
  </div>
</header>

<!-- ================= AI SETTINGS MODAL ================= -->
<div class="modal-backdrop" id="aiSettingsModal">
  <div class="modal-box">
    <h3>⚙️ AI Model Configuration</h3>
    <p>
      The portal is equipped with a <strong>Dual-Mode AI Engine</strong>:
      <br>1. <strong>Local Intelligent NLP Engine (Default)</strong>: Runs 100% offline on your PC with zero API keys.
      <br>2. <strong>Google Gemini 2.0 Live AI</strong>: Connects with a free Google AI Studio key for live conversational reasoning and real-time candidate feedback.
    </p>
    <div class="form-group">
      <label>Google Gemini API Key (Optional)</label>
      <input type="password" id="geminiApiKeyInput" placeholder="AIzaSy..." />
      <small style="display:block;margin-top:4px;font-size:11px;color:var(--text-soft);">
        Get a free API key at <a href="https://aistudio.google.com/app/apikey" target="_blank" style="color:var(--teal);">Google AI Studio</a> (Zero GCP / billing needed).
      </small>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px;">
      <button class="btn-primary" style="margin:0;" onclick="saveAiSettings()">Save Settings</button>
      <button class="btn-sm" style="padding:10px 16px;" onclick="closeAiSettingsModal()">Close</button>
    </div>
  </div>
</div>

<div class="wrap">
  
  <!-- ================= TAB 0: AUTH VIEW ================= -->
  <div id="viewAuth">
    <div class="hero" style="text-align:center;">
      <div class="eyebrow">Personalized AI Interview Workspace</div>
      <h1>Sign In to Dossier</h1>
      <p class="lede" style="margin: 0 auto;">Your resumes, tailored interview questions, and mock interview transcripts will be securely stored under your private account on this PC.</p>
    </div>

    <div class="auth-card">
      <div class="auth-tabs">
        <button class="auth-tab active" id="tabLoginBtn" onclick="setAuthMode('login')">Sign In</button>
        <button class="auth-tab" id="tabRegisterBtn" onclick="setAuthMode('register')">Create Account</button>
      </div>
      <div class="auth-body">
        <form id="authForm" onsubmit="handleAuthSubmit(event)">
          <div class="form-group" id="fullNameGroup" style="display:none;">
            <label>Full Name</label>
            <input type="text" id="authFullName" placeholder="e.g. Jane Doe" />
          </div>
          <div class="form-group">
            <label>Username</label>
            <input type="text" id="authUsername" placeholder="e.g. janedoe" required />
          </div>
          <div class="form-group" id="emailGroup" style="display:none;">
            <label>Email Address</label>
            <input type="email" id="authEmail" placeholder="e.g. jane@example.com" />
          </div>
          <div class="form-group">
            <label>Password</label>
            <input type="password" id="authPassword" placeholder="••••••••" required />
          </div>
          <button type="submit" class="btn-primary" id="authSubmitBtn">Sign In →</button>
          <div class="error-box" id="authErrorBox"></div>
        </form>
      </div>
    </div>
  </div>

  <!-- ================= TAB 1: CV INTAKE & ANALYSIS ================= -->
  <div id="viewCv" style="display:none;">
    <div class="hero">
      <div class="eyebrow">Local CV Intake &amp; AI Evaluation</div>
      <h1>Feed your CV.<br>Master the technical interview.</h1>
      <p class="lede">
        Upload a PDF resume. The built-in intelligent engine evaluates your skills, identifies gaps, calculates your readiness score, and prepares tailored technical questions — saved to your private profile.
      </p>
      <div class="storage-chip">
        💾 Zero Cloud Dependencies · Stored locally in <code>./storage/portal.db</code>
      </div>
    </div>

    <div class="dossier">
      <div class="dossier-header">
        <span>Intake Document</span>
        <span id="statusLabel">Awaiting PDF</span>
      </div>
      <div class="dossier-body">
        <label class="dropzone" id="dropzone">
          <input type="file" id="fileInput" accept="application/pdf" />
          <h3 id="dzTitle">Drop CV here, or click to browse</h3>
          <p>Standard PDF format, up to 15MB</p>
          <div id="fileChip" style="display:none;" class="file-chip">📄 <span id="fileName"></span></div>
        </label>
        <button class="btn-primary" id="analyzeBtn" disabled>Analyze CV &amp; Generate Questions</button>
        <div class="error-box" id="errorBox"></div>
        <div class="loading-spinner" id="loadingSpinner">⚡ Parsing PDF &amp; Running AI Analysis...</div>
      </div>
    </div>

    <div id="results" style="display:none;">
      <div class="report-grid">
        
        <div class="panel gauge-panel">
          <svg width="96" height="96" viewBox="0 0 96 96">
            <circle cx="48" cy="48" r="42" fill="none" stroke="#1B3D37" stroke-width="8"/>
            <circle id="gaugeArc" cx="48" cy="48" r="42" fill="none" stroke="#D69E2E" stroke-width="8"
                    stroke-linecap="round" stroke-dasharray="264" stroke-dashoffset="264"
                    transform="rotate(-90 48 48)"/>
            <text id="gaugeNum" x="48" y="54" text-anchor="middle" font-family="Space Mono" font-size="22" fill="#F8F4EC">0</text>
          </svg>
          <div>
            <h4>Readiness Score</h4>
            <div class="name" id="resCandidateName">Candidate</div>
            <div class="meta" id="resCandidateMeta">—</div>
          </div>
        </div>

        <div class="action-banner">
          <div>
            <h3>🚀 Ready to test your knowledge?</h3>
            <p>Start a live interactive mock interview based on this CV's tailored questions.</p>
          </div>
          <button class="btn-gold" id="startInterviewBtn" onclick="launchInterviewFromCV()">Start Mock Interview →</button>
        </div>

        <div class="panel">
          <h4>Top Detected Skills</h4>
          <div id="topSkills"></div>
        </div>

        <div class="panel">
          <h4>Identified Skill Gaps</h4>
          <div id="skillGaps"></div>
        </div>

        <div class="panel full">
          <h4>Key Strengths</h4>
          <div id="strengths"></div>
        </div>

        <div class="panel full">
          <h4>Suggested Career Tracks</h4>
          <div id="suggestedRoles"></div>
        </div>

        <div class="panel full">
          <h4>Generated Interview Questions</h4>
          <div id="questions"></div>
        </div>

      </div>
    </div>
  </div>

  <!-- ================= TAB 2: INTERVIEW CHAT BOX ================= -->
  <div id="viewChat" style="display:none;">
    <div class="chat-container">
      <div class="chat-header">
        <div class="chat-title">
          <div class="avatar">🤖</div>
          <div class="chat-info">
            <h3 id="chatHeaderRole">AI Technical Interviewer</h3>
            <p id="chatHeaderCandidate">Interviewing: Candidate</p>
          </div>
        </div>
        <div class="chat-progress">
          <div class="progress-pill" id="chatProgressPill">Question 1 of 5</div>
          <button class="btn-sm" onclick="startNewCustomInterview()" style="background:rgba(255,255,255,0.15);color:white;border:none;">🔄 New Session</button>
        </div>
      </div>

      <div class="chat-messages" id="chatMessages">
      </div>

      <div class="chat-controls">
        <div class="input-wrapper">
          <textarea class="chat-input" id="chatInput" placeholder="Type your answer using the STAR method (or click the 🎙️ mic to speak)..." rows="2"></textarea>
          <button class="btn-icon" id="micBtn" title="Voice Input (Speech-to-Text)" onclick="toggleVoiceInput()">🎙️</button>
          <button class="btn-icon" id="ttsBtn" title="Toggle Question Audio" onclick="toggleAudioPlayback()">🔊</button>
          <button class="btn-send" id="sendBtn" onclick="sendAnswer()">Submit</button>
        </div>
        <div class="coach-bar">
          <span style="font-family:'Space Mono',monospace;font-size:11px;color:var(--text-soft);align-self:center;">Career Coach:</span>
          <div class="chip-coach" onclick="askCoach('How should I structure a STAR answer for technical questions?')">💡 STAR Framework Tips</div>
          <div class="chip-coach" onclick="askCoach('How do I answer when I don\'t know the exact technology?')">💡 Handling Unknown Tech</div>
          <div class="chip-coach" onclick="askCoach('Give me tips to explain my skill gaps positively.')">💡 Explaining Gaps</div>
        </div>
      </div>
    </div>
  </div>

  <!-- ================= TAB 3: LOCAL STORAGE & HISTORY ================= -->
  <div id="viewHistory" style="display:none;">
    <div class="hero" style="margin-bottom: 20px;">
      <div class="eyebrow">Your Private Account Data</div>
      <h1>Your Saved CV Reports &amp; Interview Sessions</h1>
      <p class="lede">All reports and mock interview records below belong strictly to your account and are saved in local SQLite storage (<code>./storage/portal.db</code>).</p>
    </div>

    <div class="table-card">
      <h3>
        <span>📁 Saved CV Analyses</span>
        <button class="btn-sm" onclick="loadSavedReports()">🔄 Refresh</button>
      </h3>
      <table class="history-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Candidate</th>
            <th>Target Role</th>
            <th>Exp Level</th>
            <th>Score</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="reportsTableBody">
          <tr><td colspan="6" style="text-align:center;color:var(--text-soft);">Loading saved reports...</td></tr>
        </tbody>
      </table>
    </div>

    <div class="table-card">
      <h3>
        <span>🎙️ Past Mock Interview Sessions</span>
        <button class="btn-sm" onclick="loadSavedSessions()">🔄 Refresh</button>
      </h3>
      <table class="history-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Candidate</th>
            <th>Role</th>
            <th>Status</th>
            <th>Score</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="sessionsTableBody">
          <tr><td colspan="6" style="text-align:center;color:var(--text-soft);">Loading past sessions...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
let currentUser = null;
let currentCVAnalysis = null;
let currentSessionId = null;
let currentQuestionIndex = 0;
let totalQuestions = 5;
let currentQuestionData = null;
let selectedFile = null;
let speechRecognition = null;
let isRecording = false;
let autoSpeakAudio = true;
let authMode = 'login';

window.addEventListener('DOMContentLoaded', async () => {
  await checkAiEngineStatus();
  try {
    const res = await fetch('/api/auth/me');
    const data = await res.json();
    if (data.user) {
      setLoggedInUser(data.user);
    } else {
      switchTab('auth');
    }
  } catch (err) {
    switchTab('auth');
  }
});

async function checkAiEngineStatus() {
  try {
    const res = await fetch('/api/settings/ai');
    const data = await res.json();
    const badge = document.getElementById('aiStatusBadge');
    if (data.has_gemini_key) {
      badge.className = 'ai-status-chip live-ai';
      badge.innerHTML = '<span>🤖 Gemini 2.0 Live AI</span>';
    } else {
      badge.className = 'ai-status-chip';
      badge.innerHTML = '<span>⚡ AI Engine: Local NLP</span>';
    }
  } catch (e) {}
}

function openAiSettingsModal() {
  document.getElementById('aiSettingsModal').classList.add('show');
}
function closeAiSettingsModal() {
  document.getElementById('aiSettingsModal').classList.remove('show');
}
async function saveAiSettings() {
  const key = document.getElementById('geminiApiKeyInput').value.trim();
  try {
    await fetch('/api/settings/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gemini_api_key: key })
    });
    closeAiSettingsModal();
    await checkAiEngineStatus();
    alert('AI Settings updated successfully!');
  } catch (e) {
    alert('Failed to save settings.');
  }
}

function setLoggedInUser(user) {
  currentUser = user;
  document.getElementById('mainNav').style.display = 'flex';
  document.getElementById('authNavBtn').style.display = 'none';
  document.getElementById('loggedInUserBadge').style.display = 'inline-flex';
  document.getElementById('headerUserName').textContent = user.full_name || user.username;
  switchTab('cv');
}

async function logoutUser() {
  await fetch('/api/auth/logout', { method: 'POST' });
  currentUser = null;
  currentCVAnalysis = null;
  currentSessionId = null;
  document.getElementById('mainNav').style.display = 'none';
  document.getElementById('authNavBtn').style.display = 'block';
  document.getElementById('loggedInUserBadge').style.display = 'none';
  switchTab('auth');
}

function setAuthMode(mode) {
  authMode = mode;
  document.getElementById('tabLoginBtn').classList.toggle('active', mode === 'login');
  document.getElementById('tabRegisterBtn').classList.toggle('active', mode === 'register');
  document.getElementById('fullNameGroup').style.display = mode === 'register' ? 'block' : 'none';
  document.getElementById('emailGroup').style.display = mode === 'register' ? 'block' : 'none';
  document.getElementById('authSubmitBtn').textContent = mode === 'login' ? 'Sign In →' : 'Create Account →';
  document.getElementById('authErrorBox').style.display = 'none';
}

async function handleAuthSubmit(e) {
  e.preventDefault();
  const errorBox = document.getElementById('authErrorBox');
  errorBox.style.display = 'none';

  const username = document.getElementById('authUsername').value.trim();
  const password = document.getElementById('authPassword').value;
  const fullName = document.getElementById('authFullName').value.trim();
  const email = document.getElementById('authEmail').value.trim();

  const endpoint = authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
  const body = authMode === 'register' 
    ? { username, email, password, full_name: fullName }
    : { username, password };

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      errorBox.textContent = data.error || 'Authentication failed.';
      errorBox.style.display = 'block';
      return;
    }
    setLoggedInUser(data.user);
  } catch (err) {
    errorBox.textContent = 'Could not connect to server.';
    errorBox.style.display = 'block';
  }
}

function switchTab(tabId) {
  document.getElementById('viewAuth').style.display = tabId === 'auth' ? 'block' : 'none';
  document.getElementById('viewCv').style.display = tabId === 'cv' ? 'block' : 'none';
  document.getElementById('viewChat').style.display = tabId === 'chat' ? 'block' : 'none';
  document.getElementById('viewHistory').style.display = tabId === 'history' ? 'block' : 'none';

  if (document.getElementById('tabCvBtn')) {
    document.getElementById('tabCvBtn').classList.toggle('active', tabId === 'cv');
    document.getElementById('tabChatBtn').classList.toggle('active', tabId === 'chat');
    document.getElementById('tabHistBtn').classList.toggle('active', tabId === 'history');
  }

  if (tabId === 'history') {
    loadSavedReports();
    loadSavedSessions();
  }
}

// ----------------- CV DROPZONE & UPLOAD -----------------
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const analyzeBtn = document.getElementById('analyzeBtn');
const dzTitle = document.getElementById('dzTitle');
const fileChip = document.getElementById('fileChip');
const fileName = document.getElementById('fileName');
const statusLabel = document.getElementById('statusLabel');
const errorBox = document.getElementById('errorBox');
const loadingSpinner = document.getElementById('loadingSpinner');
const resultsDiv = document.getElementById('results');

['dragenter', 'dragover'].forEach(evt => {
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.add('drag'); });
});
['dragleave', 'drop'].forEach(evt => {
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.remove('drag'); });
});
dropzone.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0];
  if (f) handleFileSelection(f);
});
fileInput.addEventListener('change', e => {
  if (e.target.files[0]) handleFileSelection(e.target.files[0]);
});

function handleFileSelection(f) {
  if (!f.name.toLowerCase().endsWith('.pdf')) {
    showError('Only PDF files are supported.');
    return;
  }
  selectedFile = f;
  fileName.textContent = f.name;
  fileChip.style.display = 'inline-flex';
  dzTitle.textContent = 'Ready to analyze';
  statusLabel.textContent = 'Document ready';
  analyzeBtn.disabled = false;
  errorBox.style.display = 'none';
}

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.style.display = 'block';
}

analyzeBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  errorBox.style.display = 'none';
  resultsDiv.style.display = 'none';
  loadingSpinner.classList.add('show');
  analyzeBtn.disabled = true;

  const formData = new FormData();
  formData.append('cv_file', selectedFile);

  try {
    const res = await fetch('/api/analyze-cv', { method: 'POST', body: formData });
    const data = await res.json();
    loadingSpinner.classList.remove('show');
    analyzeBtn.disabled = false;

    if (!res.ok || data.error) {
      showError(data.error || 'Failed to analyze CV.');
      return;
    }

    currentCVAnalysis = data.analysis;
    renderAnalysis(data.analysis);
  } catch (err) {
    loadingSpinner.classList.remove('show');
    analyzeBtn.disabled = false;
    showError('Could not reach backend service. Make sure python app.py is running.');
  }
});

function tag(text, cls) {
  const span = document.createElement('span');
  span.className = 'tag' + (cls ? ' ' + cls : '');
  span.textContent = text;
  return span;
}

function renderAnalysis(a) {
  document.getElementById('resCandidateName').textContent = a.candidate_name || (currentUser ? currentUser.full_name : 'Candidate');
  document.getElementById('resCandidateMeta').textContent =
    `${a.experience_level || 'Mid-Level'} · ~${a.years_of_experience_estimate ?? 2} yrs experience · Target: ${(a.suggested_roles || ['Software Engineer'])[0]}`;

  const score = Math.max(0, Math.min(100, a.overall_readiness_score ?? 75));
  const circumference = 264;
  const offset = circumference - (circumference * score / 100);
  document.getElementById('gaugeArc').style.strokeDashoffset = offset;
  document.getElementById('gaugeNum').textContent = score;

  const topSkills = document.getElementById('topSkills');
  topSkills.innerHTML = '';
  (a.top_skills || []).forEach(s => topSkills.appendChild(tag(s)));

  const skillGaps = document.getElementById('skillGaps');
  skillGaps.innerHTML = '';
  (a.skill_gaps || []).forEach(s => skillGaps.appendChild(tag(s, 'gap-tag')));

  const strengths = document.getElementById('strengths');
  strengths.innerHTML = '';
  (a.strengths || []).forEach(s => {
    const div = document.createElement('div');
    div.className = 'strength-item';
    div.textContent = s;
    strengths.appendChild(div);
  });

  const roles = document.getElementById('suggestedRoles');
  roles.innerHTML = '';
  (a.suggested_roles || []).forEach(s => roles.appendChild(tag(s)));

  const qWrap = document.getElementById('questions');
  qWrap.innerHTML = '';
  (a.likely_interview_questions || []).forEach((q, i) => {
    const card = document.createElement('div');
    card.className = 'qcard';
    card.innerHTML = `
      <div class="qnum">Question ${i + 1} · ${q.category || 'Technical'}</div>
      <div class="qtext">${q.question}</div>
      <div class="why"><strong>Why asked:</strong> ${q.why_asked}</div>
    `;
    qWrap.appendChild(card);
  });

  resultsDiv.style.display = 'block';
  resultsDiv.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ----------------- MOCK INTERVIEW CHAT BOX -----------------
function launchInterviewFromCV() {
  if (!currentCVAnalysis) return;
  const name = currentCVAnalysis.candidate_name || (currentUser ? currentUser.full_name : "Candidate");
  startNewInterviewSession(currentCVAnalysis.report_id, name, (currentCVAnalysis.suggested_roles || ['Software Engineer'])[0]);
}

function startNewCustomInterview() {
  const defaultRole = "Software Engineer";
  const role = prompt("Enter target role for interview practice (e.g. Python Developer, Full Stack, DevOps):", defaultRole);
  if (role) {
    startNewInterviewSession(null, currentUser ? currentUser.full_name : "Candidate", role);
  }
}

async function startNewInterviewSession(cvId, name, role) {
  switchTab('chat');
  const chatMessages = document.getElementById('chatMessages');
  chatMessages.innerHTML = '<div style="text-align:center;font-family:\'Space Mono\',monospace;font-size:12px;color:var(--text-soft);margin-top:20px;">⚡ Initializing AI Mock Interview session...</div>';

  try {
    const res = await fetch('/api/chat/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cv_id: cvId, candidate_name: name, role: role })
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      alert(data.error || 'Failed to start interview.');
      return;
    }

    currentSessionId = data.session_id;
    currentQuestionIndex = 0;
    totalQuestions = data.total_questions;
    currentQuestionData = data.current_question;

    document.getElementById('chatHeaderRole').textContent = `AI Interviewer · ${data.target_role}`;
    document.getElementById('chatHeaderCandidate').textContent = `Candidate: ${data.candidate_name}`;
    document.getElementById('chatProgressPill').textContent = `Question 1 of ${data.total_questions}`;

    chatMessages.innerHTML = '';
    appendAIMessage(data.initial_message);
    if (autoSpeakAudio) speakText(data.current_question.question);
  } catch (err) {
    alert('Error connecting to interview engine.');
  }
}

function appendAIMessage(content) {
  const chatMessages = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row ai';
  
  let formatted = content.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  formatted = formatted.replace(/\n\n/g, '<br><br>').replace(/\n/g, '<br>');

  row.innerHTML = `
    <div class="avatar">🤖</div>
    <div class="bubble">${formatted}</div>
  `;
  chatMessages.appendChild(row);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendUserMessage(content, feedback) {
  const chatMessages = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row user';
  
  let feedbackHtml = '';
  if (feedback) {
    let scoreClass = feedback.score >= 8 ? 'score-high' : (feedback.score >= 6 ? 'score-mid' : 'score-low');
    feedbackHtml = `
      <div class="feedback-box">
        <div class="feedback-header">
          <strong style="color:var(--ink-dark);">${feedback.verdict || 'Evaluation'}</strong>
          <span class="score-badge ${scoreClass}">Score: ${feedback.score}/10 ${feedback.ai_powered ? '✨ AI' : ''}</span>
        </div>
        <div class="fb-section">
          <div class="fb-label">What was good:</div>
          <ul style="margin:4px 0 6px;padding-left:18px;color:var(--text);">
            ${(feedback.strengths || []).map(s => `<li>${s}</li>`).join('')}
          </ul>
        </div>
        <div class="fb-section">
          <div class="fb-label">Improvement Tips:</div>
          <ul style="margin:4px 0 6px;padding-left:18px;color:var(--text);">
            ${(feedback.improvements || []).map(i => `<li>${i}</li>`).join('')}
          </ul>
        </div>
        ${feedback.model_answer ? `
        <details style="margin-top:6px;cursor:pointer;">
          <summary style="font-family:'Space Mono',monospace;font-size:10px;color:var(--teal-dim);font-weight:700;">VIEW SAMPLE STAR ANSWER</summary>
          <div style="margin-top:6px;padding:8px;background:var(--paper-dim);border-radius:4px;font-style:italic;color:var(--text);">
            "${feedback.model_answer}"
          </div>
        </details>` : ''}
      </div>
    `;
  }

  row.innerHTML = `
    <div class="avatar" style="background:var(--ink-light);">👤</div>
    <div>
      <div class="bubble">${content.replace(/\n/g, '<br>')}</div>
      ${feedbackHtml}
    </div>
  `;
  chatMessages.appendChild(row);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function sendAnswer() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text) return;

  if (!currentSessionId) {
    await startNewInterviewSession(null, currentUser ? currentUser.full_name : "Candidate", "Software Engineer");
  }

  input.value = '';
  const sendBtn = document.getElementById('sendBtn');
  sendBtn.disabled = true;
  sendBtn.textContent = 'Thinking...';

  try {
    const res = await fetch('/api/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: currentSessionId, message: text })
    });
    const data = await res.json();
    sendBtn.disabled = false;
    sendBtn.textContent = 'Submit';

    if (!res.ok || data.error) {
      alert(data.error || 'Failed to submit answer.');
      return;
    }

    appendUserMessage(text, data.feedback);

    if (data.interview_completed) {
      document.getElementById('chatProgressPill').textContent = 'Completed 🎉';
      appendAIMessage(data.summary_message);
      if (autoSpeakAudio) speakText("Interview complete! Great job practicing today.");
    } else {
      currentQuestionIndex = data.next_question_index;
      currentQuestionData = data.next_question;
      document.getElementById('chatProgressPill').textContent = `Question ${data.next_question_index + 1} of ${data.total_questions}`;
      appendAIMessage(data.ai_message);
      if (autoSpeakAudio) speakText(data.next_question.question);
    }
  } catch (err) {
    sendBtn.disabled = false;
    sendBtn.textContent = 'Submit';
    alert('Failed to send response.');
  }
}

document.getElementById('chatInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendAnswer();
  }
});

// Coach Advice
async function askCoach(question) {
  const role = document.getElementById('chatHeaderRole').textContent.replace('AI Interviewer · ', '') || 'Software Engineer';
  try {
    const res = await fetch('/api/chat/coach', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question, role: role })
    });
    const data = await res.json();
    if (data.advice) {
      appendAIMessage(`💡 **Career Coach Advice:**\n\n${data.advice}`);
    }
  } catch (err) {
    alert('Coach currently unavailable.');
  }
}

// Voice Input
function toggleVoiceInput() {
  const micBtn = document.getElementById('micBtn');
  const input = document.getElementById('chatInput');

  if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
    alert('Speech recognition is not supported in this browser. Please use Chrome or Edge.');
    return;
  }

  if (isRecording) {
    speechRecognition.stop();
    isRecording = false;
    micBtn.classList.remove('recording');
    return;
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  speechRecognition = new SpeechRecognition();
  speechRecognition.continuous = true;
  speechRecognition.interimResults = true;
  speechRecognition.lang = 'en-US';

  speechRecognition.onstart = () => {
    isRecording = true;
    micBtn.classList.add('recording');
  };

  speechRecognition.onresult = (event) => {
    let transcript = '';
    for (let i = event.resultIndex; i < event.results.length; ++i) {
      transcript += event.results[i][0].transcript;
    }
    input.value = transcript;
  };

  speechRecognition.onerror = () => {
    isRecording = false;
    micBtn.classList.remove('recording');
  };

  speechRecognition.onend = () => {
    isRecording = false;
    micBtn.classList.remove('recording');
  };

  speechRecognition.start();
}

function toggleAudioPlayback() {
  autoSpeakAudio = !autoSpeakAudio;
  const btn = document.getElementById('ttsBtn');
  btn.style.opacity = autoSpeakAudio ? '1' : '0.4';
}

function speakText(text) {
  if (!('speechSynthesis' in window) || !autoSpeakAudio) return;
  window.speechSynthesis.cancel();
  const clean = text.replace(/[*#]/g, '');
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = 1.0;
  window.speechSynthesis.speak(utterance);
}

// ----------------- LOCAL STORAGE & HISTORY -----------------
async function loadSavedReports() {
  const tbody = document.getElementById('reportsTableBody');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">Loading your saved reports...</td></tr>';
  try {
    const res = await fetch('/api/reports');
    const data = await res.json();
    if (!data.reports || data.reports.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-soft);">No saved CV reports under your account yet. Upload a CV in Tab 1!</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    data.reports.forEach(r => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${r.created_at ? r.created_at.substring(0, 16) : '—'}</td>
        <td><strong>${r.candidate_name || 'Candidate'}</strong></td>
        <td>${r.target_role || 'Software Engineer'}</td>
        <td>${r.experience_level || 'Mid-Level'}</td>
        <td><span class="tag" style="background:#EBF5F3;color:#1A4E45;font-weight:700;">${r.readiness_score}/100</span></td>
        <td>
          <button class="btn-sm" onclick="loadReportDetail(${r.id})">🔍 View</button>
          <button class="btn-sm" onclick="startInterviewForReport(${r.id}, '${r.candidate_name}', '${r.target_role}')">🎙️ Interview</button>
          <a href="/api/download/cv/${r.id}" class="btn-sm" style="text-decoration:none;">📥 PDF</a>
          <button class="btn-sm btn-danger-sm" onclick="deleteReport(${r.id})">🗑️</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--danger);">Error loading saved reports.</td></tr>';
  }
}

async function loadSavedSessions() {
  const tbody = document.getElementById('sessionsTableBody');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">Loading past sessions...</td></tr>';
  try {
    const res = await fetch('/api/chat/sessions');
    const data = await res.json();
    if (!data.sessions || data.sessions.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-soft);">No mock interview sessions recorded yet for your account.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    data.sessions.forEach(s => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${s.created_at ? s.created_at.substring(0, 16) : '—'}</td>
        <td><strong>${s.candidate_name || 'Candidate'}</strong></td>
        <td>${s.target_role || 'Software Engineer'}</td>
        <td><span class="tag">${s.status}</span></td>
        <td><strong style="color:var(--teal);">${s.overall_score ? s.overall_score + '/100' : '—'}</strong></td>
        <td>
          <button class="btn-sm" onclick="viewSessionTranscript('${s.session_id}')">📜 Transcript</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--danger);">Error loading sessions.</td></tr>';
  }
}

async function loadReportDetail(reportId) {
  try {
    const res = await fetch(`/api/reports/${reportId}`);
    const data = await res.json();
    if (data.report && data.report.analysis) {
      currentCVAnalysis = data.report.analysis;
      switchTab('cv');
      renderAnalysis(data.report.analysis);
    }
  } catch (err) {
    alert('Failed to load report.');
  }
}

function startInterviewForReport(id, name, role) {
  startNewInterviewSession(id, name, role);
}

async function deleteReport(id) {
  if (!confirm('Are you sure you want to delete this CV and report from your PC storage?')) return;
  try {
    await fetch(`/api/reports/${id}`, { method: 'DELETE' });
    loadSavedReports();
  } catch (err) {
    alert('Delete failed.');
  }
}

async function viewSessionTranscript(sessionId) {
  try {
    const res = await fetch(`/api/chat/session/${sessionId}`);
    const data = await res.json();
    if (data.session && data.messages) {
      switchTab('chat');
      currentSessionId = sessionId;
      document.getElementById('chatHeaderRole').textContent = `AI Interviewer · ${data.session.target_role}`;
      document.getElementById('chatHeaderCandidate').textContent = `Candidate: ${data.session.candidate_name}`;
      document.getElementById('chatProgressPill').textContent = data.session.status === 'completed' ? 'Completed 🎉' : 'In Progress';
      
      const chatMessages = document.getElementById('chatMessages');
      chatMessages.innerHTML = '';
      data.messages.forEach(m => {
        if (m.role === 'assistant') {
          appendAIMessage(m.content);
        } else if (m.role === 'user') {
          appendUserMessage(m.content, m.feedback);
        }
      });
    }
  } catch (err) {
    alert('Failed to load transcript.');
  }
}
</script>
</body>
</html>       these are the codes for my ai interviw preparation portal but it is opening only if the antigravity is open in my frnds laptop like a tunnel i dont want that so modify the code
app.py
index.html
requirements.txt
run_standalone.py
start.bat
Procfile
Dockerfile
.env.example
…\ai-interview-portal > python -c "import app; print('App initialized successfully!')"
Allow testing Flask app initialization?
python -c "import app; print('App initialized successfully!')"
No
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
