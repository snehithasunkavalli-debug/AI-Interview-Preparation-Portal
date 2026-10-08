# 🚀 Dossier — AI Interview Preparation Portal (Public & GitHub Ready)

A modern, intelligent AI Interview Preparation & Mock Technical Interview Portal. 

This app is configured to **run 100% publicly on GitHub Pages** without requiring any Python backend server or tunnels!

---

## 🌟 Key Features

1. **📄 PDF Resume Parsing**: Parse resumes directly in the browser using `pdf.js` with zero server uploads required.
2. **🎯 Skill Gap & Readiness Scoring**: Evaluates candidate skills against standard tech roles (Full Stack, Backend, AI/ML, DevOps, Data Engineer).
3. **🤖 Dual AI Engine**:
   - **Local Browser NLP (Default)**: Runs 100% offline inside your browser without API keys.
   - **Google Gemini 2.0 Live AI**: Connects directly with a free Google AI Studio key for live conversational interviews and real-time STAR evaluation.
4. **🎙️ Interactive Voice & Audio Practice**: Built-in Speech-to-Text (mic input) and Speech Synthesis (audio question playback).
5. **💾 Local Storage Persistence**: User accounts, resume reports, and mock interview transcripts are saved locally in the browser.

---

## 🚀 How to Host Publicly on GitHub Pages (Free - 1 Minute Setup)

1. Create a new repository on **[GitHub](https://github.com/new)**.
2. Push this project code to your GitHub repository:
   ```bash
   git init
   git add .
   git commit -m "Initial public commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
   git push -u origin main
   ```
3. Go to your repository on GitHub:
   - Click **Settings** ⚙️ → **Pages** (left menu).
   - Under **Build and deployment** → **Source**, select **GitHub Actions**.
4. That's it! GitHub Actions will automatically publish your portal live at:
   `https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/`

---

## 💻 Running Locally (Standalone without IDE)

If you prefer to run it locally on your computer:

```bash
# 1. Double click start.bat on Windows
# OR run manually in terminal:
pip install -r requirements.txt
python run_standalone.py
```
This automatically opens `http://localhost:8080` in your web browser.

---

## 🛠️ Project File Structure

- `index.html` — Public Web App (Single Page Application with PDF.js & client-side NLP engine)
- `.github/workflows/deploy.yml` — Automated GitHub Pages deployment workflow
- `app.py` — Optional Python Flask Backend with CORS support
- `requirements.txt` — Python dependencies
- `run_standalone.py` — Local standalone python launcher
- `start.bat` — 1-click Windows launcher
- `Procfile` / `Dockerfile` — Optional deployment files for Render / Railway / Docker
