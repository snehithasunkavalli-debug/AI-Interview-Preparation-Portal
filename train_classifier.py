#!/usr/bin/env python3
"""
train_classifier.py - Resume Category Classification Model Training & Evaluation

Trains a TF-IDF + Logistic Regression pipeline on resume text (Resume_str)
to predict the target job category (Category) from the Kaggle Resume Dataset.

Key features:
1. Stratified 80/20 train/test split with random_state=42.
2. Baseline comparison against DummyClassifier(strategy="most_frequent").
3. Evaluation on held-out test set: Accuracy, Macro-F1, Weighted-F1, Classification Report, Confusion Matrix.
4. Serializes trained pipeline to models/resume_classifier.joblib.
5. Saves evaluation metrics to results/evaluation_results.json.
6. Clean, robust error handling and reproducible execution.
"""

import os
import sys
import json
import time
import argparse
from typing import Dict, Any, Tuple

import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    classification_report,
    confusion_matrix
)


def find_default_data_path(base_dir: str) -> str:
    """Find Resume.csv checking typical locations."""
    candidates = [
        os.path.join(base_dir, "data", "Resume.csv"),
        os.path.join(base_dir, "AI-Interview-Preparation-Portal-main", "data", "Resume.csv"),
        os.path.join(os.path.dirname(base_dir), "data", "Resume.csv"),
        os.path.join(os.path.dirname(base_dir), "AI-Interview-Preparation-Portal-main", "data", "Resume.csv"),
        os.path.join(os.getcwd(), "data", "Resume.csv"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return os.path.join(base_dir, "data", "Resume.csv")


def load_dataset(csv_path: str) -> pd.DataFrame:
    """Safely loads and validates the CSV dataset."""
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"Resume dataset not found at: {csv_path}")
    
    print(f"[*] Loading dataset from: {csv_path}")
    df = pd.read_csv(csv_path)
    
    required_cols = {"Resume_str", "Category"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"Dataset is missing required columns: {missing_cols}. Available columns: {list(df.columns)}")
    
    initial_count = len(df)
    df = df.dropna(subset=["Resume_str", "Category"])
    dropped_count = initial_count - len(df)
    if dropped_count > 0:
        print(f"[!] Warning: Dropped {dropped_count} rows with missing Resume_str or Category.")
        
    if len(df) == 0:
        raise ValueError("Dataset contains 0 valid samples after dropping missing values.")
        
    print(f"[*] Valid samples loaded: {len(df)} across {df['Category'].nunique()} categories.")
    return df


def train_and_evaluate(
    df: pd.DataFrame,
    test_size: float = 0.2,
    random_state: int = 42
) -> Tuple[Pipeline, Dict[str, Any]]:
    """
    Executes stratified split, trains baseline and Logistic Regression pipeline,
    and returns the fitted pipeline alongside detailed evaluation metrics.
    """
    X = df["Resume_str"]
    y = df["Category"]
    
    # 1. Stratified 80/20 train/test split
    print(f"\n[*] Performing stratified split: {int((1-test_size)*100)}% train / {int(test_size*100)}% test (random_state={random_state})...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=test_size,
        stratify=y,
        random_state=random_state
    )
    print(f"    Train size: {len(X_train)} samples")
    print(f"    Test size:  {len(X_test)} samples")
    
    classes = sorted(list(y.unique()))
    
    # 2. Baseline Model: DummyClassifier(strategy="most_frequent")
    print("\n[*] Evaluating Baseline: DummyClassifier(strategy='most_frequent')...")
    dummy = DummyClassifier(strategy="most_frequent", random_state=random_state)
    dummy.fit(X_train, y_train)
    y_pred_dummy = dummy.predict(X_test)
    
    dummy_acc = float(accuracy_score(y_test, y_pred_dummy))
    dummy_macro_f1 = float(f1_score(y_test, y_pred_dummy, average="macro", zero_division=0))
    dummy_weighted_f1 = float(f1_score(y_test, y_pred_dummy, average="weighted", zero_division=0))
    
    print(f"    Baseline Accuracy:    {dummy_acc:.4f} ({dummy_acc * 100:.2f}%)")
    print(f"    Baseline Macro-F1:    {dummy_macro_f1:.4f}")
    print(f"    Baseline Weighted-F1: {dummy_weighted_f1:.4f}")
    
    # 3. Trained Model: TF-IDF + Logistic Regression Pipeline
    print("\n[*] Training Model Pipeline: TF-IDF + LogisticRegression...")
    t_start = time.time()
    
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            stop_words="english",
            max_features=25000,
            ngram_range=(1, 2),
            sublinear_tf=True
        )),
        ("clf", LogisticRegression(
            max_iter=1000,
            random_state=random_state,
            C=5.0,
            class_weight="balanced"
        ))
    ])
    
    pipeline.fit(X_train, y_train)
    t_duration = time.time() - t_start
    print(f"    Pipeline training finished in {t_duration:.2f}s.")
    
    # 4. Evaluation on held-out test set
    print("\n[*] Evaluating Pipeline on held-out test set...")
    y_pred = pipeline.predict(X_test)
    
    model_acc = float(accuracy_score(y_test, y_pred))
    model_macro_f1 = float(f1_score(y_test, y_pred, average="macro", zero_division=0))
    model_weighted_f1 = float(f1_score(y_test, y_pred, average="weighted", zero_division=0))
    
    clf_report_text = classification_report(y_test, y_pred, labels=classes, zero_division=0)
    clf_report_dict = classification_report(y_test, y_pred, labels=classes, output_dict=True, zero_division=0)
    conf_matrix = confusion_matrix(y_test, y_pred, labels=classes).tolist()
    
    print("\n" + "=" * 68)
    print("                CLASSIFICATION REPORT (HELD-OUT TEST SET)")
    print("=" * 68)
    print(clf_report_text)
    print("=" * 68)
    
    print("\n" + "=" * 68)
    print("                       MODEL COMPARISON SUMMARY")
    print("=" * 68)
    print(f"{'Metric':<20} | {'Dummy Baseline':<18} | {'TF-IDF + LogisticReg':<20}")
    print("-" * 68)
    print(f"{'Accuracy':<20} | {dummy_acc * 100:>15.2f}%   | {model_acc * 100:>17.2f}%")
    print(f"{'Macro-F1':<20} | {dummy_macro_f1:>17.4f}   | {model_macro_f1:>19.4f}")
    print(f"{'Weighted-F1':<20} | {dummy_weighted_f1:>17.4f}   | {model_weighted_f1:>19.4f}")
    acc_lift = ((model_acc - dummy_acc) / dummy_acc * 100) if dummy_acc > 0 else 0.0
    macro_f1_lift = ((model_macro_f1 - dummy_macro_f1) / dummy_macro_f1 * 100) if dummy_macro_f1 > 0 else 0.0
    print(f"{'Accuracy Lift':<20} | {'-':>18} | {acc_lift:>+18.1f}%")
    print(f"{'Macro-F1 Lift':<20} | {'-':>18} | {macro_f1_lift:>+18.1f}%")
    print("=" * 68)
    
    evaluation_results = {
        "dataset": {
            "total_samples": len(df),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "num_classes": len(classes),
            "classes": classes
        },
        "split": {
            "test_size": test_size,
            "stratified": True,
            "random_state": random_state
        },
        "baseline": {
            "model": "DummyClassifier(strategy='most_frequent')",
            "accuracy": round(dummy_acc, 4),
            "macro_f1": round(dummy_macro_f1, 4),
            "weighted_f1": round(dummy_weighted_f1, 4)
        },
        "trained_model": {
            "model": "Pipeline(TfidfVectorizer + LogisticRegression)",
            "hyperparameters": {
                "tfidf__max_features": 25000,
                "tfidf__ngram_range": [1, 2],
                "tfidf__sublinear_tf": True,
                "tfidf__stop_words": "english",
                "clf__C": 5.0,
                "clf__class_weight": "balanced",
                "clf__max_iter": 1000,
                "clf__random_state": random_state
            },
            "training_time_seconds": round(t_duration, 2),
            "accuracy": round(model_acc, 4),
            "macro_f1": round(model_macro_f1, 4),
            "weighted_f1": round(model_weighted_f1, 4)
        },
        "comparison": {
            "accuracy_lift_percent": round(acc_lift, 2),
            "macro_f1_lift_percent": round(macro_f1_lift, 2)
        },
        "classification_report": clf_report_dict,
        "confusion_matrix": conf_matrix
    }
    
    return pipeline, evaluation_results


def main():
    parser = argparse.ArgumentParser(description="Train Resume Category Classifier")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    default_data = find_default_data_path(base_dir)
    default_model = os.path.join(base_dir, "models", "resume_classifier.joblib")
    default_results = os.path.join(base_dir, "results", "evaluation_results.json")
    
    parser.add_argument("--data-path", default=default_data, help="Path to Resume.csv")
    parser.add_argument("--model-path", default=default_model, help="Path to output .joblib model")
    parser.add_argument("--results-path", default=default_results, help="Path to output evaluation JSON")
    parser.add_argument("--random-state", type=int, default=42, help="Random state seed")
    parser.add_argument("--test-size", type=float, default=0.2, help="Held-out test split fraction")
    
    args = parser.parse_args()
    
    try:
        df = load_dataset(args.data_path)
        pipeline, results = train_and_evaluate(
            df=df,
            test_size=args.test_size,
            random_state=args.random_state
        )
        
        # Save trained model pipeline
        os.makedirs(os.path.dirname(os.path.abspath(args.model_path)), exist_ok=True)
        print(f"\n[*] Saving trained pipeline to: {args.model_path}")
        joblib.dump(pipeline, args.model_path, compress=3)
        file_size_mb = os.path.getsize(args.model_path) / (1024 * 1024)
        print(f"    Saved model size: {file_size_mb:.2f} MB")
        
        # Save evaluation metrics
        os.makedirs(os.path.dirname(os.path.abspath(args.results_path)), exist_ok=True)
        print(f"[*] Saving evaluation results to: {args.results_path}")
        with open(args.results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
            
        print("\n[SUCCESS] Model training and evaluation successfully completed!")
        
    except Exception as e:
        print(f"\n[ERROR] Training failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
