import math
from typing import Any, Dict, List, Optional, Tuple


def round_1(val: Optional[float]) -> Optional[float]:
    if val is None:
        return None
    return round(float(val), 1)


def compute_channel_stats(
    values: List[int],
) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """Compute (mean, min, max) over available integer samples."""
    if not values:
        return None, None, None
    mean_val = round_1(sum(values) / len(values))
    min_val = min(values)
    max_val = max(values)
    return mean_val, min_val, max_val


def detect_peaks_in_signal(
    samples_dict: Dict[int, int], min_index: int, max_index: int
) -> List[int]:
    """
    Detect peaks in Sensor 1 Channel 2 signal over range [min_index, max_index].
    Condition 1: 800 samples before i and 1 sample after i are available.
    Condition 2: sample[i] > sample[i-1] and sample[i] > sample[i+1].
    Condition 3: sample[i] > 0.5 * max(sample[i-800 : i]).
    Condition 4: at least 100 samples after previous peak.
    """
    peaks = []
    last_peak_index = -999999

    for i in range(min_index, max_index + 1):
        if i not in samples_dict:
            continue

        val_i = samples_dict[i]

        # Condition 1: Check availability of i+1 and 800 samples before i
        if (i + 1) not in samples_dict:
            continue

        prev_800 = []
        is_history_available = True
        for h in range(i - 800, i):
            if h not in samples_dict:
                is_history_available = False
                break
            prev_800.append(samples_dict[h])

        if not is_history_available:
            continue

        # Condition 2: larger than neighbours
        if not (val_i > samples_dict[i - 1] and val_i > samples_dict[i + 1]):
            continue

        # Condition 3: larger than 0.5 * max(sample[i-800:i])
        max_prev = max(prev_800)
        if val_i <= 0.5 * max_prev:
            continue

        # Condition 4: at least 100 samples after previous peak
        if (i - last_peak_index) < 100:
            continue

        peaks.append(i)
        last_peak_index = i

    return peaks


def compute_heart_rate_for_window(
    w: int, s1_ch2_dict: Dict[int, int]
) -> Optional[float]:
    """
    Rule for window w:
    1. heart_rate is null if w < 4.
    2. take samples 400*(w-4) to 400*(w+1)-1 (5 seconds = 2000 samples).
    3. null if any sample in those 5 seconds is missing.
    4. null if fewer than 2 peaks detected.
    5. heart_rate = 60 / (mean gap between consecutive peaks in seconds).
       gap = (next_peak - curr_peak) / 400.0.
    """
    if w < 4:
        return None

    start_idx = 400 * (w - 4)
    end_idx = 400 * (w + 1) - 1

    # Check that all 2000 samples in the 5-second range are available
    for idx in range(start_idx, end_idx + 1):
        if idx not in s1_ch2_dict:
            return None

    # Detect peaks. Need history from start_idx - 800 to end_idx + 1
    # We search peaks from start_idx to end_idx
    min_search = start_idx
    # Also find any prior peak before start_idx if within 100 samples
    peaks_in_window = []
    last_peak_idx = -999999

    # To maintain condition 4 properly across window boundary,
    # find peaks starting from start_idx - 100 up to end_idx
    detect_from = max(800, start_idx - 100)
    all_recent_peaks = detect_peaks_in_signal(s1_ch2_dict, detect_from, end_idx)

    peaks_in_window = [p for p in all_recent_peaks if start_idx <= p <= end_idx]

    if len(peaks_in_window) < 2:
        return None

    # Compute mean gap
    total_gap_samples = peaks_in_window[-1] - peaks_in_window[0]
    num_gaps = len(peaks_in_window) - 1
    mean_gap_seconds = (total_gap_samples / num_gaps) / 400.0

    if mean_gap_seconds <= 0:
        return None

    hr = 60.0 / mean_gap_seconds
    return round_1(hr)


def process_window(
    w: int,
    s1_samples: Dict[int, List[int]],
    s2_samples: Dict[int, List[int]],
    mode: str = "online",
    s1_history_ch2: Optional[Dict[int, int]] = None,
) -> Dict[str, Any]:
    """
    Process window w (indices 400*w to 400*w + 399).
    s1_samples: {sample_index: [ch1, ch2, ch3]} for current window
    s2_samples: {sample_index: [ch1, ch2]} for current window
    s1_history_ch2: {sample_index: ch2_val} containing 5-second range for HR
    """
    start_idx = 400 * w
    end_idx = 400 * w + 399

    window_s1 = [s1_samples[i] for i in range(start_idx, end_idx + 1) if i in s1_samples]
    window_s2 = [s2_samples[i] for i in range(start_idx, end_idx + 1) if i in s2_samples]

    missing_1 = 400 - len(window_s1)
    missing_2 = 400 - len(window_s2)
    complete = (missing_1 == 0 and missing_2 == 0)

    # Sensor 1 channels
    s1_ch1_vals = [s[0] for s in window_s1]
    s1_ch2_vals = [s[1] for s in window_s1]
    s1_ch3_vals = [s[2] for s in window_s1]

    s1_ch1_mean, s1_ch1_min, s1_ch1_max = compute_channel_stats(s1_ch1_vals)
    s1_ch2_mean, s1_ch2_min, s1_ch2_max = compute_channel_stats(s1_ch2_vals)
    s1_ch3_mean, s1_ch3_min, s1_ch3_max = compute_channel_stats(s1_ch3_vals)

    # Sensor 2 channels
    s2_ch1_vals = [s[0] for s in window_s2]
    s2_ch2_vals = [s[1] for s in window_s2]

    s2_ch1_mean, s2_ch1_min, s2_ch1_max = compute_channel_stats(s2_ch1_vals)
    s2_ch2_mean, s2_ch2_min, s2_ch2_max = compute_channel_stats(s2_ch2_vals)

    # Magnitude over sensor 2 samples: mean of sqrt(ch1^2 + ch2^2)
    if window_s2:
        mags = [math.sqrt(s[0] ** 2 + s[1] ** 2) for s in window_s2]
        magnitude = round_1(sum(mags) / len(mags))
    else:
        magnitude = None

    # Heart rate from sensor 1 channel 2
    history_dict = s1_history_ch2 if s1_history_ch2 is not None else {}
    # Make sure current window's ch2 values are also in history_dict
    for i in range(start_idx, end_idx + 1):
        if i in s1_samples and i not in history_dict:
            history_dict[i] = s1_samples[i][1]

    heart_rate = compute_heart_rate_for_window(w, history_dict)

    result = {
        "window": w,
        "mode": mode,
        "complete": complete,
        "missing_1": missing_1,
        "missing_2": missing_2,
        "magnitude": magnitude,
        "heart_rate": heart_rate,
        # Flat channel stats
        "s1_ch1_mean": s1_ch1_mean,
        "s1_ch1_min": s1_ch1_min,
        "s1_ch1_max": s1_ch1_max,
        "s1_ch2_mean": s1_ch2_mean,
        "s1_ch2_min": s1_ch2_min,
        "s1_ch2_max": s1_ch2_max,
        "s1_ch3_mean": s1_ch3_mean,
        "s1_ch3_min": s1_ch3_min,
        "s1_ch3_max": s1_ch3_max,
        "s2_ch1_mean": s2_ch1_mean,
        "s2_ch1_min": s2_ch1_min,
        "s2_ch1_max": s2_ch1_max,
        "s2_ch2_mean": s2_ch2_mean,
        "s2_ch2_min": s2_ch2_min,
        "s2_ch2_max": s2_ch2_max,
        # Grouped representation
        "channels": {
            "s1_ch1": {"mean": s1_ch1_mean, "min": s1_ch1_min, "max": s1_ch1_max},
            "s1_ch2": {"mean": s1_ch2_mean, "min": s1_ch2_min, "max": s1_ch2_max},
            "s1_ch3": {"mean": s1_ch3_mean, "min": s1_ch3_min, "max": s1_ch3_max},
            "s2_ch1": {"mean": s2_ch1_mean, "min": s2_ch1_min, "max": s2_ch1_max},
            "s2_ch2": {"mean": s2_ch2_mean, "min": s2_ch2_min, "max": s2_ch2_max},
        },
    }
    return result
