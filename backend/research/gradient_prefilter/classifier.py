"""
Phase 2 — Gradient Prefilter Classifier

Trains a logistic regression classifier on gradient features from the eval cache.
Uses leave-one-video-out cross-validation (critical: frames within a video are
correlated, so splitting at frame level would leak data).

Usage (from backend/):
    uv run python -m research.gradient_prefilter.classifier

Outputs:
    research/gradient_prefilter/model.joblib — trained Pipeline(StandardScaler, LogReg)
    Prints: LOOCV metrics, per-video decision, threshold analysis
"""

import os
import sys
import glob
import warnings
import numpy as np
import joblib

from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    roc_auc_score, precision_score, recall_score,
    confusion_matrix, classification_report,
)

_backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from research.gradient_prefilter.features import extract_features, FEATURE_NAMES
from research.gradient_prefilter.spike import GROUND_TRUTH, CACHE_DIR

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.joblib")

# Threshold: per-video median P(AI) >= this → short-circuit as AI GENERATED
# Conservative: high precision matters more than recall for a personal bot.
# False positive (labeling real content as AI) hurts trust far more than
# false negative (sending to LLM when prefilter could have caught it).
AI_CONFIDENCE_THRESHOLD = 0.80


def load_video_features() -> dict[str, tuple[np.ndarray, int]]:
    """
    Returns dict: video_id → (features_matrix (N_frames, 15), label)
    """
    data = {}
    for video_id, label in sorted(GROUND_TRUTH.items()):
        frame_dir = os.path.join(CACHE_DIR, video_id)
        frame_paths = sorted(glob.glob(os.path.join(frame_dir, "frame_*.jpg")))
        if not frame_paths:
            print(f"  WARNING: no frames for {video_id}")
            continue
        feats = []
        for fp in frame_paths:
            f = extract_features(fp)
            if not np.any(np.isnan(f)):
                feats.append(f)
        if feats:
            data[video_id] = (np.array(feats), label)
    return data


def _aggregate_video_proba(frame_probas: np.ndarray) -> float:
    """Per-video AI probability: median of per-frame P(AI)."""
    return float(np.median(frame_probas))


def leave_one_video_out_cv(data: dict) -> dict:
    """
    Leave-one-video-out cross-validation.

    Returns:
        dict with video_id → {true_label, pred_label, pred_proba, correct}
    """
    results = {}
    video_ids = list(data.keys())

    for held_out in video_ids:
        # Train on all other videos
        train_X, train_y = [], []
        for vid, (X_vid, y_vid) in data.items():
            if vid == held_out:
                continue
            train_X.append(X_vid)
            train_y.extend([y_vid] * len(X_vid))

        X_train = np.vstack(train_X)
        y_train = np.array(train_y)

        if len(np.unique(y_train)) < 2:
            # Can't train without both classes
            results[held_out] = {
                "true_label": data[held_out][1],
                "pred_label": -1,
                "pred_proba": 0.5,
                "correct": False,
                "note": "insufficient training classes",
            }
            continue

        # Train pipeline
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(C=1.0, class_weight="balanced",
                                        max_iter=1000, random_state=42)),
        ])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pipe.fit(X_train, y_train)

        # Predict on held-out video
        X_test, y_true = data[held_out]
        frame_probas = pipe.predict_proba(X_test)[:, 1]  # P(AI) per frame
        video_proba = _aggregate_video_proba(frame_probas)

        pred_label = 1 if video_proba >= AI_CONFIDENCE_THRESHOLD else 0

        results[held_out] = {
            "true_label": y_true,
            "pred_label": pred_label,
            "pred_proba": video_proba,
            "correct": pred_label == y_true,
            "frame_probas": frame_probas.tolist(),
        }

    return results


def train_final_model(data: dict) -> Pipeline:
    """Train on ALL available videos for deployment."""
    all_X, all_y = [], []
    for vid, (X_vid, y_vid) in data.items():
        all_X.append(X_vid)
        all_y.extend([y_vid] * len(X_vid))

    X = np.vstack(all_X)
    y = np.array(all_y)

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(C=1.0, class_weight="balanced",
                                    max_iter=1000, random_state=42)),
    ])
    pipe.fit(X, y)
    return pipe


def print_separator(char="=", width=72):
    print(char * width)


def run_classifier():
    print_separator()
    print("GRADIENT PREFILTER — PHASE 2 CLASSIFIER")
    print_separator()

    data = load_video_features()
    print(f"\nLoaded {len(data)} videos:")
    for vid, (X_vid, label) in sorted(data.items()):
        lbl = "Real" if label == 0 else "AI"
        print(f"  {vid}  ({lbl}):  {len(X_vid)} frames")

    n_videos = len(data)
    if n_videos < 3:
        print("\nERROR: Need at least 3 videos for LOOCV. Collect more data.")
        sys.exit(1)

    # --- Leave-one-video-out CV ---
    print(f"\n{'─'*72}")
    print(f"Leave-one-video-out CV  (threshold={AI_CONFIDENCE_THRESHOLD})")
    print(f"{'─'*72}")
    print(f"{'Video':<20} {'True':>6}  {'P(AI)':>7}  {'Pred':>6}  {'Result'}")
    print(f"{'─'*72}")

    cv_results = leave_one_video_out_cv(data)

    y_true_all, y_pred_all, y_proba_all = [], [], []
    for vid, res in sorted(cv_results.items()):
        true_lbl = "Real" if res["true_label"] == 0 else "AI"
        pred_lbl = "Real" if res["pred_label"] == 0 else ("AI" if res["pred_label"] == 1 else "?")
        ok = "CORRECT" if res["correct"] else "WRONG"
        note = f"  ({res.get('note', '')})" if res.get("note") else ""
        print(f"{vid:<20} {true_lbl:>6}  {res['pred_proba']:>7.3f}  {pred_lbl:>6}  {ok}{note}")
        if res["pred_label"] != -1:
            y_true_all.append(res["true_label"])
            y_pred_all.append(res["pred_label"])
            y_proba_all.append(res["pred_proba"])

    y_true_all = np.array(y_true_all)
    y_pred_all = np.array(y_pred_all)
    y_proba_all = np.array(y_proba_all)

    n_correct = sum(1 for r in cv_results.values() if r.get("correct", False))
    print_separator("-")
    print(f"LOOCV accuracy: {n_correct}/{n_videos}")

    if len(np.unique(y_true_all)) > 1:
        try:
            auc = roc_auc_score(y_true_all, y_proba_all)
            print(f"LOOCV AUC: {auc:.4f}")
        except Exception:
            pass

    if len(y_pred_all) >= 2 and len(np.unique(y_true_all)) > 1:
        prec_ai = precision_score(y_true_all, y_pred_all, pos_label=1, zero_division=0)
        rec_ai = recall_score(y_true_all, y_pred_all, pos_label=1, zero_division=0)
        print(f"AI-class precision: {prec_ai:.3f}  recall: {rec_ai:.3f}")
        print(f"\nConfusion matrix:\n{confusion_matrix(y_true_all, y_pred_all)}")

    # --- GO/NO-GO for Phase 3 ---
    print_separator()
    prec_ai_val = precision_score(y_true_all, y_pred_all, pos_label=1, zero_division=0) if len(y_pred_all) >= 2 else 0
    rec_ai_val = recall_score(y_true_all, y_pred_all, pos_label=1, zero_division=0) if len(y_pred_all) >= 2 else 0

    go_prec = prec_ai_val >= 0.95
    go_rec = rec_ai_val >= 0.40

    print("PHASE 3 GO/NO-GO:")
    print(f"  AI precision >= 0.95:  {'PASS' if go_prec else 'FAIL'}  ({prec_ai_val:.3f})")
    print(f"  AI recall >= 0.40:     {'PASS' if go_rec else 'FAIL'}  ({rec_ai_val:.3f})")

    if go_prec and go_rec:
        print("\n  GO → Training final model and saving to model.joblib")
        final_model = train_final_model(data)
        joblib.dump({
            "pipeline": final_model,
            "threshold": AI_CONFIDENCE_THRESHOLD,
            "feature_names": FEATURE_NAMES,
            "n_training_videos": n_videos,
        }, MODEL_PATH)
        size_kb = os.path.getsize(MODEL_PATH) / 1024
        print(f"  Saved: {MODEL_PATH} ({size_kb:.1f} KB)")
    else:
        print("\n  NO-GO — Classifier doesn't meet precision/recall bar.")
        print("  Collect more labeled videos (target: 15 real + 15 AI) and re-run.")

    print_separator()


if __name__ == "__main__":
    run_classifier()
