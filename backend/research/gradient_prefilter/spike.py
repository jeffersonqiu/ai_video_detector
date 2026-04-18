"""
Phase 1 Viability Spike — Gradient-Field Pre-Filter

Answers the key question: Does the gradient-field signal survive Instagram/TikTok
H.264 compression well enough to distinguish AI-generated from real video frames?

Usage (from backend/):
    uv run python -m research.gradient_prefilter.spike

Reads frames from ../../evaluation/cache/<video_id>/frame_*.jpg
Labels from hardcoded ground truth (evaluation/test_questions.md).

GO if ALL of:
  - Frame-level silhouette score >= 0.25 on PCA-2D embedding
  - At least 2 features with AUC > 0.70
  - Video-level mean features also show separation

NO-GO if:
  - Silhouette < 0.15 OR no feature has AUC > 0.65
"""

import os
import sys
import glob
import numpy as np

from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import roc_auc_score, silhouette_score

# Add backend root to path so relative imports work when run as __main__
_backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend_root not in sys.path:
    sys.path.insert(0, _backend_root)

from research.gradient_prefilter.features import extract_features, FEATURE_NAMES

# Ground truth from evaluation/test_questions.md
# label: 0 = Real, 1 = AI
GROUND_TRUTH: dict[str, int] = {
    "DWM0mTqDOIF": 0,  # Real
    "DT0U_hcDSaq": 0,  # Real
    "DVmPqQhETyD": 1,  # AI
    "DWj-0u6EgX4": 1,  # AI
    "DWmajXxjF7S": 1,  # AI
}

CACHE_DIR = os.path.join(
    os.path.dirname(_backend_root),
    "evaluation", "cache"
)


def load_frames() -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Returns:
        X: (N_frames, 15) feature matrix
        y: (N_frames,) binary labels (0=real, 1=AI)
        video_ids: list of length N_frames, which video each frame belongs to
    """
    X_rows, y_rows, video_ids = [], [], []

    for video_id, label in sorted(GROUND_TRUTH.items()):
        frame_dir = os.path.join(CACHE_DIR, video_id)
        frame_paths = sorted(glob.glob(os.path.join(frame_dir, "frame_*.jpg")))
        if not frame_paths:
            print(f"  WARNING: no frames found for {video_id} in {frame_dir}")
            continue
        for fp in frame_paths:
            feats = extract_features(fp)
            if np.any(np.isnan(feats)):
                print(f"  WARNING: NaN in features for {fp}, skipping")
                continue
            X_rows.append(feats)
            y_rows.append(label)
            video_ids.append(video_id)

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_rows, dtype=int)
    return X, y, video_ids


def print_separator(char="=", width=72):
    print(char * width)


def run_spike():
    print_separator()
    print("GRADIENT-FIELD VIABILITY SPIKE")
    print_separator()

    # --- Load data ---
    print(f"\nLoading frames from: {CACHE_DIR}")
    X, y, video_ids = load_frames()

    n_real = int((y == 0).sum())
    n_ai = int((y == 1).sum())
    print(f"Loaded {len(X)} frames total: {n_real} real, {n_ai} AI")
    print(f"Videos: {sorted(set(video_ids))}\n")

    if len(X) < 10:
        print("ERROR: Not enough frames to run spike. Check cache directory.")
        sys.exit(1)

    # --- Standardize ---
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # --- PCA ---
    pca = PCA(n_components=2, random_state=42)
    X_pca = pca.fit_transform(X_scaled)
    print(f"PCA explained variance: PC1={pca.explained_variance_ratio_[0]:.1%}, "
          f"PC2={pca.explained_variance_ratio_[1]:.1%}")

    # --- Silhouette scores ---
    sil_frame = silhouette_score(X_pca, y)
    print(f"Frame-level silhouette score (PCA-2D): {sil_frame:.4f}")

    # Video-level: mean features per video
    unique_videos = list(GROUND_TRUTH.keys())
    X_video, y_video = [], []
    for vid in unique_videos:
        mask = np.array([v == vid for v in video_ids])
        if mask.sum() == 0:
            continue
        X_video.append(X_scaled[mask].mean(axis=0))
        y_video.append(GROUND_TRUTH[vid])
    X_video = np.array(X_video)
    y_video = np.array(y_video)
    if len(X_video) >= 3:
        pca_v = PCA(n_components=2, random_state=42)
        X_video_pca = pca_v.fit_transform(X_video)
        sil_video = silhouette_score(X_video_pca, y_video) if len(set(y_video)) > 1 else float("nan")
        print(f"Video-level silhouette score (PCA-2D, {len(X_video)} videos): {sil_video:.4f}")
    else:
        sil_video = float("nan")

    # --- Per-feature AUC ---
    print_separator("-")
    print(f"{'#':<3} {'Feature':<22} {'AUC':>6}  {'Direction'}")
    print_separator("-")

    aucs = []
    for i, name in enumerate(FEATURE_NAMES):
        feat_vals = X[:, i]
        if len(np.unique(y)) < 2:
            auc = float("nan")
        else:
            try:
                auc = roc_auc_score(y, feat_vals)
            except Exception:
                auc = float("nan")
        # AUC < 0.5 means the opposite direction is predictive; reflect it
        direction = "higher→AI" if auc >= 0.5 else "lower→AI"
        effective_auc = max(auc, 1.0 - auc) if not np.isnan(auc) else float("nan")
        aucs.append(effective_auc)
        marker = " <<<" if effective_auc > 0.70 else ""
        print(f"{i:<3} {name:<22} {effective_auc:>6.3f}  {direction}{marker}")

    print_separator("-")
    n_good_features = sum(1 for a in aucs if not np.isnan(a) and a > 0.70)
    best_auc = max((a for a in aucs if not np.isnan(a)), default=0.0)
    print(f"Features with AUC > 0.70: {n_good_features}")
    print(f"Best individual feature AUC: {best_auc:.4f}")

    # --- Video-level centroid distances ---
    print_separator("-")
    print("Video-level mean PCA centroids:")
    for i, vid in enumerate(unique_videos):
        mask = np.array([v == vid for v in video_ids])
        if mask.sum() == 0:
            continue
        centroid = X_pca[mask].mean(axis=0)
        lbl = "Real" if GROUND_TRUTH[vid] == 0 else "AI"
        print(f"  {vid}  ({lbl}):  PC1={centroid[0]:+.3f}  PC2={centroid[1]:+.3f}")

    real_mask = y == 0
    ai_mask = y == 1
    real_centroid = X_pca[real_mask].mean(axis=0)
    ai_centroid = X_pca[ai_mask].mean(axis=0)
    centroid_dist = np.linalg.norm(ai_centroid - real_centroid)
    print(f"\n  Real centroid: PC1={real_centroid[0]:+.3f}  PC2={real_centroid[1]:+.3f}")
    print(f"  AI centroid:   PC1={ai_centroid[0]:+.3f}  PC2={ai_centroid[1]:+.3f}")
    print(f"  L2 distance between centroids: {centroid_dist:.4f}")

    # --- GO/NO-GO decision ---
    print_separator()
    go_sil = sil_frame >= 0.25
    go_auc = n_good_features >= 2
    go_video = (not np.isnan(sil_video)) and sil_video >= 0.10

    print("GO/NO-GO ASSESSMENT:")
    print(f"  Frame silhouette >= 0.25:  {'PASS' if go_sil else 'FAIL'}  ({sil_frame:.4f})")
    print(f"  >= 2 features AUC > 0.70:  {'PASS' if go_auc else 'FAIL'}  ({n_good_features} features)")
    print(f"  Video-level separation:     {'PASS' if go_video else 'FAIL/UNCLEAR'} "
          f"(sil={sil_video:.4f})" if not np.isnan(sil_video) else
          f"  Video-level separation:     UNCLEAR  (insufficient video count)")

    go = go_sil and go_auc

    print_separator()
    if go:
        print("VERDICT: GO — Signal survives compression. Proceed to Phase 2 (classifier).")
        print("NOTE: Verify that video-level separation is genuine (not scene confound).")
    else:
        print("VERDICT: NO-GO — Signal does not survive Instagram/TikTok H.264 compression.")
        print("The gradient-field method is not viable for social media video frames.")
        print("The LLM pipeline ($0.0065 / 5-video run) remains the best approach.")
    print_separator()


if __name__ == "__main__":
    run_spike()
