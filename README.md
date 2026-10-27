# Dossier — AI Interview Preparation Portal

An AI-assisted resume analysis and interview preparation platform designed to help candidates identify skill gaps, evaluate alignment with job requirements, and practice technical interviews.

Dossier combines browser-based resume analysis, machine learning-based resume classification, career recommendations, mock interview tools, and user analytics within a unified interface.

## Overview

Dossier helps candidates understand how their skills align with a target role and identify areas for improvement before an interview. The project includes a web interface, a Python backend, and a trained resume classification model.

**Technology stack**
- **Frontend:** HTML, CSS, JavaScript
- **Backend:** Python, Flask
- **Local server:** Waitress
- **Machine learning:** TF-IDF and Logistic Regression
- **Document processing:** PDF resume parsing
- **Data and evaluation:** Resume dataset, classification metrics, and evaluation reports

## Key Features

### Resume Analysis and Career Preparation
- Upload and parse PDF resumes.
- Compare extracted skills against custom job descriptions or role requirements.
- Calculate company-requirement match scores.
- Identify matched skills and missing skills.
- Display candidate strengths and suggested career tracks.
- Generate targeted interview questions based on identified skill gaps.

### Machine Learning Resume Classification
- Predict a resume's category using a TF-IDF feature extraction pipeline and Logistic Regression classifier.
- Display the predicted category and model confidence.
- Train and evaluate the classifier using a reproducible stratified train-test split.
- Compare model performance against a majority-class baseline.
- Save the trained model and evaluation results for subsequent use.

### Mock Interview Practice
- Provide interview practice through the portal's available mock interview interface.
- Support browser speech capabilities where implemented and supported by the user's browser.
- Use the configured interview engine and its available features.

### Additional Portal Features
The application also includes interfaces and implementations for account-related workflows, saved reports, and administrative analytics. The availability of these features depends on the configured runtime and whether their required backend services are running.

## Machine Learning Evaluation

The current saved evaluation reports the following results:

| Metric | Result |
|---|---:|
| Total valid resume samples | 2,484 |
| Training samples | 1,987 |
| Test samples | 497 |
| Number of resume categories | 24 |
| Majority-class baseline accuracy | 4.83% |
| TF-IDF + Logistic Regression accuracy | 68.21% |
| Macro-F1 score | 0.6334 |
| Weighted-F1 score | 0.6677 |

The evaluation used a stratified 80/20 train-test split with `random_state=42`.

These metrics measure **resume-category classification performance**. They do not establish the accuracy of company-match percentages, skill-gap recommendations, career-track recommendations, or interview evaluations.

Results may vary if the dataset, preprocessing, model configuration, or evaluation procedure changes.

## Project Structure

```text
AI-Interview-Preparation-Portal/
├── app.py
├── index.html
├── run_standalone.py
├── train_classifier.py
├── requirements.txt
├── README.md
├── DATASET_CITATION.md
├── PROJECT_REPORT.md
├── models/
│   └── resume_classifier.joblib
├── results/
│   └── evaluation_results.json
├── data/
│   └── Resume.csv                 # Obtain separately if not included
└── dossier.png
```

Additional files may be present for installation, deployment, or background startup.

## Running the Full Application Locally

The full Python application is the recommended option for testing backend-dependent features and the trained ML classifier.

### Prerequisites

- Python 3.10 or another version compatible with the dependencies in `requirements.txt`.
- Git, if cloning the repository.
- The trained model file at `models/resume_classifier.joblib`.

### 1. Clone the repository

```bash
git clone https://github.com/snehithasunkavalli-debug/AI-Interview-Preparation-Portal.git
cd AI-Interview-Preparation-Portal
```

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, consult the Python documentation or use Command Prompt to activate the environment.

**macOS/Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Start the application

```bash
python run_standalone.py
```

When startup succeeds, open:

`http://127.0.0.1:8080`

Keep the terminal open while using the application. Stop the server with `Ctrl+C`.

If startup fails, check the Python version, dependency installation, model file path, and any error messages printed by the server.

## Training the Resume Classifier

Training requires the resume dataset, which may need to be downloaded separately.

1. Review `DATASET_CITATION.md` for dataset attribution and source information.
2. Obtain the dataset from its original source and verify its terms of use.
3. Place the required CSV file at the path expected by `train_classifier.py` (currently intended to be `data/Resume.csv`).
4. Install the project's Python dependencies.
5. Run:

```bash
python train_classifier.py
```

The training script is intended to save the trained model to:

`models/resume_classifier.joblib`

Evaluation results are saved to:

`results/evaluation_results.json`

Training may overwrite the existing model and evaluation results. Back up these files before retraining if you need to preserve the current evaluation.

## Persistent Cloud Database Configuration (PostgreSQL on Neon or Supabase)

### Why SQLite fails on Vercel
On Vercel and similar serverless platforms, execution environments run in stateless Lambda containers. The writable partition (`/tmp`) is ephemeral and wiped upon function cold starts, container recycling, or new deployments. If SQLite is stored in `/tmp/storage/portal.db`, user registrations and login credentials will disappear between sessions ("Invalid credentials" error).

To achieve persistent user authentication, session data, and CV reports, connect a managed PostgreSQL instance such as **Neon** or **Supabase**.

### 1. Create a Managed PostgreSQL Database (Free Tier)

#### Option A: Neon (Recommended for serverless)
1. Sign up at [neon.tech](https://neon.tech).
2. Create a new project (e.g. `dossier-portal-db`).
3. Under **Dashboard -> Connection Details**, copy the PostgreSQL connection string:
   ```text
   postgresql://[user]:[password]@[ep-host].neon.tech/neondb?sslmode=require
   ```

#### Option B: Supabase
1. Sign up at [supabase.com](https://supabase.com).
2. Create a new project.
3. Under **Project Settings -> Database -> Connection string**, copy the URI (choose **Transaction Pooler** for serverless environments):
   ```text
   postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres?sslmode=require
   ```

### 2. Configure Environment Variables

#### In Local Development
Add the following to your local `.env` file:
```env
DATABASE_URL=postgresql://user:password@ep-host.neon.tech/neondb?sslmode=require
SECRET_KEY=your_generated_32_character_secret_key_here
ADMIN_PASSWORD=your_secure_admin_password_here
```

#### In Vercel Project Settings
1. Open your Vercel Project dashboard.
2. Navigate to **Settings -> Environment Variables**.
3. Add the following variables:
   - `DATABASE_URL`: Your PostgreSQL connection string with `?sslmode=require`.
   - `SECRET_KEY`: A strong, random string (minimum 16 characters).
   - `ADMIN_PASSWORD`: A strong password for the `admin` account.
   - `ADMIN_USERNAME`: `admin` (or your chosen admin username).
   - `ADMIN_EMAIL`: `admin@dossier.portal`
   - `GEMINI_API_KEY`: *(Optional)* Your Google Gemini API key.

### 3. Migrate Existing Local Data to PostgreSQL
If you have existing candidate accounts and CV reports in local SQLite (`storage/portal.db`), run the automated migration script:

```bash
python migrate_sqlite_to_pg.py --url "postgresql://user:password@ep-host.neon.tech/neondb?sslmode=require"
```

The migration tool:
- Automatically initializes all tables, schema migrations, foreign keys, and indexes.
- Transfers existing user accounts while preserving exact password hashes (`scrypt`/`pbkdf2`).
- Transfers all CV reports and interview sessions.
- Automatically synchronizes auto-increment primary key sequences (`users_id_seq`, `cv_reports_id_seq`).
- Verifies record counts across both databases upon completion.

### 4. Verify Database Persistence via Health Endpoint
After deployment, verify that persistence is active by checking the health endpoint:

```text
GET https://your-project.vercel.app/api/health
```

Expected response when persistent PostgreSQL is configured:
```json
{
  "status": "online",
  "database_type": "postgresql",
  "database_configured": true,
  "persistent_database": true,
  "storage_mode": "persistent_cloud_postgres",
  "is_ephemeral": false,
  "database_connected": true
}
```

If `DATABASE_URL` is unset in production, the portal blocks ephemeral SQLite and reports:
```json
{
  "database_type": "none",
  "database_configured": false,
  "persistent_database": false,
  "storage_mode": "unconfigured_persistent_db",
  "is_ephemeral": false,
  "error": "DATABASE_URL is not configured in production..."
}
```

## Vercel Deployment

Deploy the application to Vercel with serverless Python runtime support:

```bash
git add .
git commit -m "Configure persistent PostgreSQL, secure credentials, and Vercel routing"
git push origin main
```

If using the Vercel CLI:
```bash
vercel --prod
```

## GitHub Pages Hosting

GitHub Pages is a static website hosting service. It serves HTML, CSS, JavaScript, images, and other static assets. **It does not execute Python, run Flask or Waitress, or provide a Python backend.**

The repository contains both a static frontend and a Python application. Therefore, GitHub Pages can host the frontend only to the extent that its features are implemented entirely in the browser.

### Deploy the static frontend

1. Open the repository on GitHub.
2. Go to **Settings → Pages**.
3. Under **Build and deployment**, select **Deploy from a branch**.
4. Select the `main` branch and the `/ (root)` folder.
5. Click **Save**.
6. Wait for GitHub Pages to finish deployment and open the URL it provides.

The `.nojekyll` file is included to disable Jekyll processing for the published static files.

The typical URL format is:

`https://snehithasunkavalli-debug.github.io/AI-Interview-Preparation-Portal/`

### GitHub Pages limitations

The following limitations apply unless the relevant feature has a separate browser-only implementation or a deployed backend:

- Flask routes and Python server-side processing will not run on GitHub Pages.
- The Python ML model cannot be loaded directly by Flask because Flask is not running there.
- Server-side account management, database persistence, and admin operations require a suitable backend.
- Any external AI integration requires its configured service and appropriate security controls.
- Browser-only features work only if their required HTML, JavaScript, libraries, and assets are available at the published paths.

A successful Pages deployment does not, by itself, prove that every feature in the full local application works on the public website.

For full functionality, run the Python application locally or deploy the backend to a suitable server and configure the frontend to communicate with it securely.

## Security and Privacy

- Never commit real API keys, passwords, access tokens, or private environment files.
- Use `.env.example` only for placeholder configuration values.
- Avoid committing private resumes or other personally identifiable information.
- Verify dataset licensing and redistribution requirements before publishing dataset files.
- Do not treat credentials embedded in client-side JavaScript as secure administrator authentication.
- Production account management, access control, session expiration, and report isolation should be enforced by a trusted backend rather than relying solely on frontend logic.
- Do not expose AI service keys in public browser code. Use a secure backend for services that require secret credentials.

## Troubleshooting

**The application does not start**
- Confirm that the virtual environment is activated.
- Install the dependencies from `requirements.txt`.
- Check the Python version and terminal error messages.

**The classifier does not load**
- Confirm that `models/resume_classifier.joblib` exists.
- Ensure the application is running from the expected project directory.
- Verify that the installed dependencies are compatible with the saved model.

**Training fails because the dataset is missing**
- Obtain the dataset from its original source.
- Follow the path and schema expected by `train_classifier.py`.
- Check `DATASET_CITATION.md`.

**The website loads on GitHub Pages but some features fail**
- Check whether those features require Flask, Python, a database, or an external service.
- Inspect browser developer tools for JavaScript errors and failed requests.
- Use the full local application or deploy a backend for server-dependent functionality.

## Contributing

Contributions and improvements are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Make focused changes and test them.
4. Submit a pull request with a description of the changes and validation performed.

## License

A license has not been specified in this README. Before redistributing the project, dataset, or model, add an appropriate project license and verify the dataset's and dependencies' applicable terms.

## Acknowledgements

- The authors and maintainers of the resume dataset used for classification.
- The open-source libraries and tools used to build and evaluate the application.

See `DATASET_CITATION.md` for dataset attribution and source details.
