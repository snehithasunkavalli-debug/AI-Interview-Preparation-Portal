import os
import json
import traceback

from flask import Flask, request, jsonify, send_from_directory
import pdfplumber
from google import genai

# -----------------------------
# Gemini Configuration
# -----------------------------
client = genai.Client(
    api_key="AQ.Ab8RN6JnnhuqLExCh3or5pGXRmljwoxj9Xd2yHxrNZC1cRcGaQ"
)

MODEL_NAME = "gemini-2.5-flash"

app = Flask(__name__, static_folder="static")

ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE_MB = 10


# -----------------------------
# Helpers
# -----------------------------
def extract_text_from_pdf(file_stream):
    text_chunks = []

    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            text_chunks.append(text)

    return "\n".join(text_chunks).strip()


def build_prompt(cv_text):
    return f"""
You are an expert technical recruiter and interview coach.

Analyze the following CV/resume text.

Return ONLY valid JSON.

Schema:

{{
  "candidate_name": "",
  "experience_level": "",
  "years_of_experience_estimate": 0,
  "top_skills": [],
  "skill_gaps": [],
  "strengths": [],
  "suggested_roles": [],
  "likely_interview_questions": [
    {{
      "question":"",
      "why_asked":""
    }}
  ],
  "overall_readiness_score": 0
}}

CV:

{cv_text}
"""


def analyze_cv_with_gemini(cv_text):

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=build_prompt(cv_text)
    )

    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.replace("```json", "")
        raw = raw.replace("```", "")
        raw = raw.strip()

    return json.loads(raw)


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/analyze-cv", methods=["POST"])
def analyze_cv():

    if "cv_file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    file = request.files["cv_file"]

    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only PDF files are supported."}), 400

    try:

        cv_text = extract_text_from_pdf(file.stream)

        if not cv_text:
            return jsonify({
                "error": "Could not extract text from PDF."
            }), 422

        result = analyze_cv_with_gemini(cv_text)

        return jsonify({
            "success": True,
            "analysis": result
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "error": str(e)
        }), 500


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 8080))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=True
    )
