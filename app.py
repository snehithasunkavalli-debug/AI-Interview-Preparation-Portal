

import os
import json
import traceback

from flask import Flask, request, jsonify, send_from_directory
import pdfplumber

from google import genai


client = genai.Client(
    api_key = "AQ.Ab8RN6JnnhuqLExCh3or5pGXRmljwoxj9Xd2yHxrNZC1cRcGaQ"
)
MODEL_NAME = "models/gemini-2.5-flash-latest"

app = Flask(__name__, static_folder="static")

ALLOWED_EXTENSIONS = {".pdf"}
MAX_FILE_SIZE_MB = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def extract_text_from_pdf(file_stream) -> str:
    """Pull all text out of an uploaded PDF file stream."""
    text_chunks = []
    with pdfplumber.open(file_stream) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)
    return "\n".join(text_chunks).strip()


def build_prompt(cv_text: str) -> str:
    return f"""
You are an expert technical recruiter and interview coach.
Analyze the following CV/resume text and respond with ONLY valid JSON
(no markdown fences, no commentary) using exactly this schema:

{{
  "candidate_name": string,
  "experience_level": "Entry" | "Mid" | "Senior" | "Lead/Executive",
  "years_of_experience_estimate": number,
  "top_skills": [string, ...],   // up to 8
  "skill_gaps": [string, ...],   // things missing/weak for their apparent target role
  "strengths": [string, ...],    // up to 5
  "suggested_roles": [string, ...],  // up to 3 job titles this CV fits
  "likely_interview_questions": [
    {{"question": string, "why_asked": string}}
    // 5 questions, mix of technical and behavioral
  ],
  "overall_readiness_score": number // 0-100, how interview-ready this CV suggests they are
}}

CV TEXT:
\"\"\"
{cv_text}
\"\"\"
"""


def analyze_cv_with_gemini(cv_text: str) -> dict:
    response = client.models.generate_content(
        model="models/gemini-2.5-flash-latest",
        contents=build_prompt(cv_text)
    )

    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json\n", "", 1)

    return json.loads(raw)

    raw = response.text.strip()
    # Safety net in case the model wraps output in ```json fences anyway
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.replace("json\n", "", 1)

    return json.loads(raw)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/analyze-cv", methods=["POST"])
def analyze_cv():
    if "cv_file" not in request.files:
        return jsonify({"error": "No file uploaded. Use form field 'cv_file'."}), 400

    file = request.files["cv_file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Only PDF files are supported right now."}), 400

    try:
        cv_text = extract_text_from_pdf(file.stream)
        if not cv_text:
            return jsonify({"error": "Could not extract text — is this a scanned/image PDF?"}), 422

        result = analyze_cv_with_gemini(cv_text)
        return jsonify({"success": True, "analysis": result})

    except Exception as e:  # noqa: BLE001 — surface a clean error to the frontend
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
