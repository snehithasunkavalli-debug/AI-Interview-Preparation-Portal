# Dossier — AI Interview Preparation Portal (Public GitHub Pages Ready)

A modern, intelligent AI Interview Preparation & Mock Technical Interview Portal with complete client-side PDF parsing, TF-IDF skill gap analysis, CARSA malicious user detection, session timeout, isolated reports, and real-data admin dashboard visualizations.

This app is configured to **run 100% publicly on GitHub Pages via "Deploy from a branch"** without requiring any server or custom workflow!

---

## 🌟 Key Features

1. **📄 PDF Resume Parsing**: Parse resumes directly in the browser using `pdf.js` with zero server uploads required.
2. **🎯 Skill Gap & Readiness Scoring**: Evaluates candidate skills against custom company requirements or standard tech roles (Full Stack, Backend, AI/ML, DevOps, Data Engineer).
3. **🤖 Dual AI Engine**:
   - **Local Browser NLP (Default)**: Runs 100% offline inside your browser without API keys.
   - **Google Gemini 2.0 Live AI**: Connects directly with a free Google AI Studio key for live conversational interviews and real-time STAR evaluation.
4. **🎙️ Interactive Voice & Audio Practice**: Built-in Speech-to-Text (mic input) and Speech Synthesis (audio question playback).
5. **🛡️ CARSA (Credential Anomaly & Risk Scoring Algorithm)**: Identifies suspicious / fake accounts with Shannon entropy, disposable email domain checks, QWERTY walk patterns, and consonant cluster analysis.
6. **📊 Admin Dashboard with Real Analytics**: Login velocity dual-axis charts, star rating distribution bar charts, user management, and suspicious user flagging with 1-click ban.
7. **🔒 Privacy & Isolation**: Users only see their own reports. Admin (`admin` / `aiint`) has full cross-system visibility.
8. **⏱️ Session Timeout**: Automatic 15-minute inactivity timeout and window close detection.

---

## 🚀 How to Host Publicly on GitHub Pages via "Deploy from a branch"

### Step 1: Files Needed in Your Branch
Make sure your branch (e.g. `main` or `snehithasunkavalli-debug`) contains:
- `index.html` (The full client-side application)
- `.nojekyll` (Critical: tells GitHub Pages to bypass Jekyll processing so builds never time out or cancel)
- `dossier.png` (App logo and favicon)

*(Note: Do not include a `.github/workflows/deploy.yml` file when using "Deploy from a branch", as it causes GitHub to attempt two conflicting builds at the same time!)*

### Step 2: Configure GitHub Repository Settings
1. Go to your GitHub repository in your browser.
2. Click **Settings** ⚙️ (top navigation bar).
3. In the left sidebar, click **Pages** (under the "Code and automation" section).
4. Under **Build and deployment**:
   - **Source**: Select **"Deploy from a branch"**.
   - **Branch**: Select your branch (e.g. `snehithasunkavalli-debug` or `main`).
   - **Folder**: Select `/ (root)`.
   - Click **Save**.
5. Refresh the page after ~30 seconds. You will see a banner at the top of the Pages settings with your live site URL:
   `https://<your-username>.github.io/<your-repo-name>/`

---

## 💻 Running Locally (Optional)

If you prefer to run it locally on your machine:
```bash
pip install -r requirements.txt
python run_standalone.py
```
This automatically opens `http://localhost:8080` in your web browser.

---

## 🛠️ Credentials
- **Admin Username**: `admin`
- **Admin Password**: `aiint`
