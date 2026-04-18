"""
Gradient-field feature extraction for AI video detection.

Per-frame pipeline:
  1. Load JPEG as grayscale (Rec. 709 approximation via cv2 default)
  2. Compute Sobel gradients (Gx, Gy)
  3. Derive 15 statistical features from the gradient distribution

All features are deterministic and CPU-only. Target: <50ms per 720px frame.
"""

import numpy as np
import cv2
from scipy.stats import skew, kurtosis, circmean, circstd

FEATURE_NAMES = [
    "grad_mag_mean",       # 0  Mean gradient magnitude
    "grad_mag_std",        # 1  Std of gradient magnitudes
    "grad_mag_skew",       # 2  Skewness of magnitudes
    "grad_mag_kurtosis",   # 3  Kurtosis of magnitudes
    "grad_mag_median",     # 4  Median magnitude
    "dir_entropy",         # 5  Entropy of 16-bin direction histogram
    "cov_00",              # 6  Covariance C[0,0] (Gx variance)
    "cov_01",              # 7  Covariance C[0,1] (Gx-Gy covariance)
    "cov_11",              # 8  Covariance C[1,1] (Gy variance)
    "eigenval_1",          # 9  Largest eigenvalue of covariance
    "eigenval_2",          # 10 Smallest eigenvalue
    "anisotropy_ratio",    # 11 λ1/λ2 — isotropic (real) vs anisotropic (AI)
    "fft_hf_ratio",        # 12 High-frequency energy ratio (>0.25 Nyquist)
    "laplacian_mean",      # 13 Mean absolute Laplacian (sharpness)
    "edge_density",        # 14 Fraction of pixels above magnitude threshold
]

N_FEATURES = len(FEATURE_NAMES)  # 15


def extract_features(frame_path: str) -> np.ndarray:
    """
    Extract 15 gradient-based features from a JPEG frame.

    Args:
        frame_path: Path to a JPEG image file.

    Returns:
        float64 array of shape (15,). NaN values indicate a degenerate frame
        (e.g. pure black). Callers should handle NaN before feeding to classifier.
    """
    img = cv2.imread(frame_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return np.full(N_FEATURES, np.nan)

    img_f = img.astype(np.float32)

    # Sobel gradients
    gx = cv2.Sobel(img_f, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(img_f, cv2.CV_64F, 0, 1, ksize=3)

    # Gradient magnitude and angle
    mag = np.sqrt(gx ** 2 + gy ** 2).ravel()
    angle = np.arctan2(gy, gx).ravel()

    features = np.empty(N_FEATURES, dtype=np.float64)

    # --- Magnitude statistics (features 0-4) ---
    features[0] = np.mean(mag)
    features[1] = np.std(mag)
    features[2] = float(skew(mag))
    features[3] = float(kurtosis(mag))
    features[4] = np.median(mag)

    # --- Direction entropy (feature 5) ---
    hist, _ = np.histogram(angle, bins=16, range=(-np.pi, np.pi), density=False)
    hist_norm = hist.astype(np.float64) / (hist.sum() + 1e-12)
    # Shannon entropy: -sum(p * log2(p)), ignoring zero bins
    nonzero = hist_norm > 0
    features[5] = -np.sum(hist_norm[nonzero] * np.log2(hist_norm[nonzero]))

    # --- Covariance matrix (features 6-8) ---
    stack = np.stack([gx.ravel(), gy.ravel()], axis=0)  # (2, N)
    cov = np.cov(stack)  # (2, 2)
    features[6] = cov[0, 0]
    features[7] = cov[0, 1]
    features[8] = cov[1, 1]

    # --- Eigenvalues and anisotropy (features 9-11) ---
    eigenvals = np.linalg.eigvalsh(cov)  # ascending order
    lam2, lam1 = eigenvals[0], eigenvals[1]  # lam1 >= lam2
    features[9] = lam1
    features[10] = lam2
    features[11] = lam1 / (lam2 + 1e-12)  # anisotropy ratio

    # --- FFT high-frequency energy ratio (feature 12) ---
    fft2 = np.fft.fft2(img_f)
    fft_shifted = np.fft.fftshift(fft2)
    power = np.abs(fft_shifted) ** 2
    h, w = power.shape
    cy, cx = h // 2, w // 2
    # Radial distance from centre, normalised by Nyquist (half image size)
    y_idx, x_idx = np.ogrid[:h, :w]
    dist = np.sqrt(((y_idx - cy) / cy) ** 2 + ((x_idx - cx) / cx) ** 2)
    total_power = power.sum() + 1e-12
    hf_power = power[dist > 0.25].sum()
    features[12] = hf_power / total_power

    # --- Laplacian mean (feature 13) ---
    # CV_32F → CV_32F required; cast after
    lap = cv2.Laplacian(img_f, cv2.CV_32F)
    features[13] = np.mean(np.abs(lap))

    # --- Edge density (feature 14) ---
    # Threshold: mean + 0.5 * std of magnitude
    threshold = features[0] + 0.5 * features[1]
    features[14] = np.mean(mag > threshold)

    return features
