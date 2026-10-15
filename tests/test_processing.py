import math
import pytest
from pipeline.processing import (
    compute_channel_stats,
    compute_heart_rate_for_window,
    detect_peaks_in_signal,
    process_window,
)


def test_channel_stats():
    values = [10, 20, 30, 40]
    mean_val, min_val, max_val = compute_channel_stats(values)
    assert mean_val == 25.0
    assert min_val == 10
    assert max_val == 40


def test_empty_channel_stats():
    mean_val, min_val, max_val = compute_channel_stats([])
    assert mean_val is None
    assert min_val is None
    assert max_val is None


def test_window_completeness():
    # 400 samples for window 0
    s1 = {i: [100, 200, 300] for i in range(400)}
    s2 = {i: [400000, 150000] for i in range(400)}

    res = process_window(0, s1, s2, mode="online")
    assert res["complete"] is True
    assert res["missing_1"] == 0
    assert res["missing_2"] == 0
    assert res["magnitude"] is not None
    assert res["heart_rate"] is None  # w < 4


def test_window_missing_samples():
    # Only 390 samples for sensor 1
    s1 = {i: [100, 200, 300] for i in range(390)}
    s2 = {i: [400000, 150000] for i in range(400)}

    res = process_window(0, s1, s2, mode="offline")
    assert res["complete"] is False
    assert res["missing_1"] == 10
    assert res["missing_2"] == 0
    assert res["mode"] == "offline"


def test_heart_rate_calculation_60bpm():
    # At 60 bpm and 400 Hz: 1 beat every 400 samples
    # For window w = 4: range is 400*(4-4)=0 to 400*(4+1)-1 = 1999 (2000 samples)
    # Peak spacing = 400 samples -> HR = 60 / (400/400) = 60.0 bpm
    s1_dict = {}
    for i in range(2000):
        # Baseline = 1000
        # Peak at 200, 600, 1000, 1400, 1800
        val = 1000
        phase = i % 400
        if phase == 200:
            val = 3000
        elif phase in (199, 201):
            val = 1500
        s1_dict[i] = val

    hr = compute_heart_rate_for_window(4, s1_dict)
    assert hr is not None
    assert abs(hr - 60.0) < 1.0
