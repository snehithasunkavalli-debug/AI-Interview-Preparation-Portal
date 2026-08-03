# CV Analysis Service — Week 1 (AI Interview Prep Portal)

Flask + Gemini (Vertex AI) backend, plain HTML/JS frontend. Upload a CV PDF,
get back extracted skills, gaps, suggested roles, and likely interview
questions.

## Files
- `app.py` — Flask backend: receives the PDF, extracts text (pdfplumber),
  sends it to Gemini via Vertex AI, returns structured JSON.
- `index.html` — upload UI + results dashboard (no framework, just fetch()).
- `requirements.txt` — Python dependencies.

## 1. Local setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Auth so the Vertex AI SDK can find your GCP credentials locally
gcloud auth application-default login

export GOOGLE_CLOUD_PROJECT=your-gcp-project-id
export GOOGLE_CLOUD_LOCATION=us-central1

python app.py
```

Open http://localhost:8080 — upload a PDF CV and click **Analyze CV**.

## 2. Enable the required GCP APIs (one-time, per project)

```bash
gcloud services enable aiplatform.googleapis.com
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
```

## 3. Deploy to Cloud Run

```bash
gcloud run deploy cv-analysis-service \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_CLOUD_PROJECT=your-gcp-project-id,GOOGLE_CLOUD_LOCATION=us-central1
```

Cloud Run will build the container from source (using the `requirements.txt`
and a default Python buildpack — no Dockerfile needed) and give you a public
URL. The service account Cloud Run runs as needs the **Vertex AI User** IAM
role:

```bash
gcloud projects add-iam-policy-binding your-gcp-project-id \
  --member="serviceAccount:YOUR-CLOUD-RUN-SERVICE-ACCOUNT" \
  --role="roles/aiplatform.user"
```

## 4. Where each piece of your Week 1 work fits

| Task | Where it happens |
|---|---|
| Upload UI | `index.html` (dropzone + fetch call) |
| PDF text extraction | `extract_text_from_pdf()` in `app.py` (pdfplumber) |
| AI analysis (skills, gaps, questions) | `analyze_cv_with_gemini()` in `app.py` (Vertex AI Gemini) |
| Hosting/running the code | Cloud Shell (dev) → Cloud Run (deployed) |
| File storage (once you add persistence) | Cloud Storage bucket + Firestore for saved reports |

## 5. Natural next steps (later weeks)
- Store uploaded CVs in a Cloud Storage bucket instead of processing in-memory only.
- Save each analysis to Firestore so a user can revisit past reports.
- Add authentication (Firebase Auth) so each user only sees their own CVs.
- Turn `likely_interview_questions` into an actual mock-interview chat flow using Gemini's chat/session API.
