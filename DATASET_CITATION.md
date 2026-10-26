# Dataset Citation & Methodology

## 1. Dataset Source & Metadata

- **Dataset Name**: Resume Dataset
- **Curator / Author**: Snehaan Bhawal
- **Hosting Platform**: Kaggle
- **Dataset URL**: [https://www.kaggle.com/datasets/snehaanbhawal/resume-dataset](https://www.kaggle.com/datasets/snehaanbhawal/resume-dataset)
- **Dataset File**: `data/Resume.csv` (Size: ~56.3 MB)
- **Total Records**: 2,484 resumes
- **Features / Columns**:
  - `ID`: Unique identifier for each resume PDF.
  - `Resume_str`: Extracted plain-text content of the resume.
  - `Resume_html`: Raw HTML content from the original web source.
  - `Category`: Target job domain / occupational category label (24 unique classes).
- **Origin**: Public resume profiles scraped from LiveCareer across 24 job categories.

## 2. Dataset License

- **License**: **CC0: Public Domain** ([Creative Commons Zero v1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/))
- **Terms**: Dedicated to the public domain. The dataset may be freely copied, modified, distributed, and used for commercial or non-commercial purposes without explicit permission.

## 3. Data Preprocessing

1. **Validation & Quality Check**:
   - The CSV was inspected using safe readers without modifying the original data.
   - Verified 0 missing values across all columns (`ID`, `Resume_str`, `Resume_html`, `Category`).
   - Category distribution encompasses 24 classes ranging from Information-Technology (120 samples, 4.83%) to BPO (22 samples, 0.89%).
2. **Text Feature Extraction**:
   - Source feature: `Resume_str` (mean length: ~6,295 characters).
   - Scikit-learn `TfidfVectorizer` pipeline step:
     - Stop words: English standard stop words.
     - N-gram range: `(1, 2)` (captures single words and domain collocations like "machine learning", "human resources", "project management").
     - Vocabulary size: Capped at top 25,000 features (`max_features=25000`).
     - Sublinear TF scaling (`sublinear_tf=True`) using $1 + \log(\text{tf})$ to dampen the impact of highly repetitive words.

## 4. Evaluation Methodology

1. **Held-out Split**:
   - Stratified train/test split: **80% training** (1,987 samples) and **20% held-out test** (497 samples).
   - Seeded with `random_state=42` for exact reproducibility.
   - Stratification preserves class proportions across all 24 categories, essential for minority classes like BPO (4 test samples) and Automobile (7 test samples).
2. **Baseline Model**:
   - `DummyClassifier(strategy="most_frequent", random_state=42)`
   - Predicts the majority class for all inputs, establishing an empirical floor for a 24-class task.
3. **Classification Model**:
   - `LogisticRegression(C=5.0, class_weight='balanced', max_iter=1000, random_state=42)`
   - Balanced class weighting adjusts inverse class frequencies to prevent bias toward larger classes.
4. **Evaluation Metrics Measured on Test Set**:
   - Test Accuracy
   - Macro-Averaged F1-Score (unweighted mean across 24 classes)
   - Weighted-Averaged F1-Score
   - Per-class Precision, Recall, and F1-score (Classification Report)
   - 24 × 24 Confusion Matrix
   - Results are automatically archived to `results/evaluation_results.json` and model pipeline saved to `models/resume_classifier.joblib`.

## 5. Empirical Performance Summary

| Metric | Dummy Baseline (Most Frequent) | TF-IDF + Logistic Regression | Relative Lift |
| :--- | :---: | :---: | :---: |
| **Accuracy** | **4.83%** (0.0483) | **68.21%** (0.6821) | **+1,312.5%** |
| **Macro-F1** | **0.0038** | **0.6334** | **+16,399.1%** |
| **Weighted-F1** | **0.0044** | **0.6677** | **+15,075.0%** |

## 6. Scope, Limitations & Ethical Usage Note

> [!NOTE]
> **Important Distinction on Scope:**
> This dataset labels broad occupational domains (e.g., *Information-Technology*, *Healthcare*, *Sales*, *Finance*). It classifies the general domain of a candidate's background. It does **not** evaluate candidate competence, resume quality, or candidate-to-job-description relevance.
>
> Therefore, this category classifier is integrated as a supplementary domain tag. It does **not** alter or claim to improve the candidate-job skill-gap analysis or readiness score calculated by the portal.
