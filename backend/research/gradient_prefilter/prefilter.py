"""
Gradient prefilter integration module.

Called by detector.py before the Gemini/Claude pipeline.
Returns a DetectionResult if the classifier is confident the video is AI-generated.
Returns None if uncertain — caller falls through to the LLM pipeline.

Shadow mode: Set PREFILTER_ENABLED=false in Railway. The logging in detector.py
will record what the prefilter would have done, so you can compare against LLM
verdicts before trusting it in production.
"""

import logging
import os
import numpy as np
import joblib

logger = logging.getLogger(__name__)

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.joblib")

_model_cache: dict | None = None


def _load_model() -> dict:
    global _model_cache
    if _model_cache is None:
        if not os.path.exists(_MODEL_PATH):
            raise FileNotFoundError(
                f"Gradient prefilter model not found at {_MODEL_PATH}. "
                "Run: uv run python research/gradient_prefilter/classifier.py"
            )
        _model_cache = joblib.load(_MODEL_PATH)
        logger.info(
            f"Gradient prefilter model loaded "
            f"(trained on {_model_cache['n_training_videos']} videos, "
            f"threshold={_model_cache['threshold']})"
        )
    return _model_cache


def run_prefilter(frame_paths: list[str]) -> "DetectionResult | None":
    """
    Run gradient feature extraction + classifier on a video's frames.

    Returns DetectionResult(verdict='AI GENERATED', confidence='HIGH', cost=0)
    if median P(AI) >= threshold, else None (fall through to LLM pipeline).

    Never returns a 'LIKELY REAL' verdict — the prefilter only short-circuits
    on confident AI detections. False negatives go to the LLM; false positives
    would harm user trust far more than spending $0.001 on Gemini.
    """
    from research.gradient_prefilter.features import extract_features

    try:
        model_data = _load_model()
    except FileNotFoundError as e:
        logger.warning(f"Prefilter disabled: {e}")
        return None

    pipeline = model_data["pipeline"]
    threshold = model_data["threshold"]

    # Extract features from all frames
    frame_features = []
    for fp in frame_paths:
        if not os.path.exists(fp):
            continue
        feats = extract_features(fp)
        if not np.any(np.isnan(feats)):
            frame_features.append(feats)

    if not frame_features:
        logger.warning("Prefilter: no valid frames to process, skipping.")
        return None

    X = np.array(frame_features)
    frame_probas = pipeline.predict_proba(X)[:, 1]  # P(AI) per frame
    median_p = float(np.median(frame_probas))
    min_p = float(np.min(frame_probas))
    max_p = float(np.max(frame_probas))

    logger.info(
        f"Prefilter: {len(frame_features)} frames, "
        f"P(AI) median={median_p:.3f} min={min_p:.3f} max={max_p:.3f} "
        f"threshold={threshold}"
    )

    if median_p >= threshold:
        from models import DetectionResult
        logger.info(f"Prefilter SHORT-CIRCUIT: AI GENERATED / HIGH (P={median_p:.3f})")
        return DetectionResult(
            verdict="AI GENERATED",
            confidence="HIGH",
            reason=(
                f"Gradient analysis detected strong AI generation artifacts "
                f"across {len(frame_features)} frames (P={median_p:.3f}). "
                f"No LLM call made."
            ),
            model_used="gradient-prefilter",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
        )

    return None
