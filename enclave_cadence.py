"""Auxiliary typing comparison for the service worker."""

import math
import statistics

BIOMETRIC_THRESHOLDS = {1: 65.0, 2: 75.0, 3: 50.0}
# A valid V4 passphrase is limited to 4,096 UTF-8 bytes.
MAX_INTERVALS = 4096


def measure_sample(sample):
    """Derive timing statistics from the intervals and reject inconsistent metadata."""
    if not isinstance(sample, dict):
        return None
    intervals = sample.get("intervals")
    if not isinstance(intervals, (list, tuple)) or not 2 <= len(intervals) <= MAX_INTERVALS:
        return None
    try:
        if any(type(value) not in (int, float) or value <= 0 or not math.isfinite(value)
               for value in intervals):
            return None
        mean = statistics.fmean(intervals)
        std = statistics.pstdev(intervals)
        if not math.isfinite(mean) or not math.isfinite(std) or mean < 12:
            return None
        for field, calculated in (("mean", mean), ("std", std)):
            if field in sample:
                reported = sample[field]
                if (type(reported) not in (int, float) or not math.isfinite(reported)
                        or reported < 0 or (field == "mean" and reported == 0)
                        or not math.isclose(reported, calculated, rel_tol=1e-9, abs_tol=1e-6)):
                    return None
    except (OverflowError, ValueError):
        return None
    return mean, std


def evaluate_cadence(train_mean, train_sig, sample, level):
    """Compare client timing with a stored profile as an auxiliary check."""
    if (level not in BIOMETRIC_THRESHOLDS or not 12 <= train_mean <= 6553.5
            or not isinstance(train_sig, bytes) or len(train_sig) != 8):
        return False, 0.0
    measured = measure_sample(sample)
    if measured is None:
        return False, 0.0
    test_mean, _test_std = measured
    intervals = sample["intervals"]
    n = min(len(train_sig), len(intervals))
    test_sig = [min(255, max(0, round((x / test_mean) * 64))) for x in intervals[:n]]
    train_vec = list(train_sig[:n])

    # Compare the shapes of the normalized timing vectors.
    m_train = sum(train_vec) / n
    m_test = sum(test_sig) / n
    u = [x - m_train for x in train_vec]
    v = [y - m_test for y in test_sig]
    dot = sum(u[i] * v[i] for i in range(n))
    mag_u = math.sqrt(sum(x * x for x in u))
    mag_v = math.sqrt(sum(y * y for y in v))

    if mag_u == 0 or mag_v == 0:
        cos_sim = 0.0
    else:
        cos_sim = dot / (mag_u * mag_v)

    corr_score = max(0.0, cos_sim)

    rel_diffs = [abs(test_sig[i] - train_vec[i]) / max(train_vec[i], 15.0) for i in range(n)]
    avg_rel_diff = sum(rel_diffs) / n
    shape_score = max(0.0, 1.0 - (avg_rel_diff * 1.6))

    speed_ratio = min(train_mean, test_mean) / max(train_mean, test_mean, 1.0)
    speed_factor = min(1.0, speed_ratio * 1.40)

    # Weighted composite biometric score:
    # Correlation 60%, Shape 25%, Speed Factor 15%
    score = (corr_score * 0.60 + shape_score * 0.25 + speed_factor * 0.15) * 100.0

    return score >= BIOMETRIC_THRESHOLDS[level], score
