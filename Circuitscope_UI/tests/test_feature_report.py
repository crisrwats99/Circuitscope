"""Automatic checks for compute_all / format_feature (core/waveform.py) and write_waveform_csv
(core/export.py). Run with:  python -m pytest"""

import csv

import numpy as np
import pytest

from core.export import write_waveform_csv
from core.models import WaveformFeature as F
from core.waveform import FeatureResult, compute_all, format_feature

RATE = 100_000.0


def sine(freq=1000.0, amp=1.0, offset=1.65, phase_deg=0.0, n=5000):
    t = np.arange(n) / RATE
    return offset + amp * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))


def by_feature(results):
    return {r.feature: r for r in results}


# ---------- compute_all ----------

def test_compute_all_reports_every_feature_in_order():
    results = compute_all(sine(), RATE)
    assert [r.feature for r in results] == list(F)             # all eleven, in the enum's order
    assert all(isinstance(r, FeatureResult) for r in results)


def test_single_channel_capture_measures_everything_except_two_channel_features():
    r = by_feature(compute_all(sine(1000, amp=1.0, offset=1.65), RATE))
    assert r[F.FREQUENCY].value == pytest.approx(1000, rel=0.002)
    assert r[F.PERIOD].value == pytest.approx(0.001, rel=0.002)
    assert r[F.PEAK_TO_PEAK].value == pytest.approx(2.0, abs=0.01)
    assert r[F.DC_OFFSET].value == pytest.approx(1.65, abs=0.01)
    assert r[F.DUTY_CYCLE].value == pytest.approx(0.5, abs=0.02)
    # With no reference channel these two are explained, not guessed.
    for feature in (F.GAIN, F.PHASE_DIFFERENCE):
        assert r[feature].value is None and "reference" in r[feature].error


def test_with_a_reference_channel_gain_and_phase_are_measured():
    ref = sine(1000, amp=1.0)
    out = sine(1000, amp=0.5, phase_deg=-45)
    r = by_feature(compute_all(out, RATE, ref))
    assert r[F.GAIN].value == pytest.approx(0.5, abs=0.02)
    assert r[F.PHASE_DIFFERENCE].value == pytest.approx(45, abs=3)


def test_one_failing_feature_does_not_stop_the_others():
    flat = np.full(1000, 2.0)                                   # a DC level: no cycles, no edges
    r = by_feature(compute_all(flat, RATE))
    assert r[F.DC_OFFSET].value == pytest.approx(2.0)
    assert r[F.RMS].value == pytest.approx(2.0)
    assert r[F.FREQUENCY].value is None and "flat" in r[F.FREQUENCY].error
    assert r[F.RISE_TIME].value is None and r[F.RISE_TIME].error


def test_noise_on_a_dc_level_is_not_mistaken_for_a_wave():
    rng = np.random.default_rng(3)
    noisy_dc = 3.0 + rng.normal(0, 0.022, 2000)                 # 3.000 V with 22 mV of noise (2 mV at the converter pin / 0.09)
    r = by_feature(compute_all(noisy_dc, RATE))
    assert r[F.DC_OFFSET].value == pytest.approx(3.0, abs=0.001)
    for feature in (F.FREQUENCY, F.PERIOD, F.DUTY_CYCLE, F.RISE_TIME, F.FALL_TIME):
        assert r[feature].value is None and "flat" in r[feature].error


def test_a_small_but_real_signal_is_still_measured():
    wave = sine(1000, amp=0.5)                                  # 1 V peak-to-peak: well above the noise floor
    assert by_feature(compute_all(wave, RATE))[F.FREQUENCY].value == pytest.approx(1000, rel=0.005)


def test_compute_all_on_unusable_samples_explains_every_feature():
    results = compute_all([1.0, 2.0], RATE)                     # far too short
    assert all(r.value is None and r.error for r in results)


# ---------- format_feature ----------

@pytest.mark.parametrize("feature,value,text", [
    (F.FREQUENCY, 998.74, "998.7 Hz"),
    (F.PERIOD, 0.001, "1.000 ms"),
    (F.PEAK, 3.3, "3.300 V"),
    (F.PEAK_TO_PEAK, 2.0, "2.000 V"),
    (F.RMS, 0.7071, "0.707 V"),
    (F.DC_OFFSET, 1.65, "1.650 V"),
    (F.DUTY_CYCLE, 0.5, "50.0 %"),
    (F.GAIN, 0.5, "0.500"),
    (F.PHASE_DIFFERENCE, 45.0, "45.0 °"),
    (F.RISE_TIME, 80e-6, "80.0 µs"),
    (F.FALL_TIME, 1.5e-6, "1.5 µs"),
])
def test_feature_values_are_shown_with_units(feature, value, text):
    assert format_feature(feature, value) == text


def test_missing_value_is_shown_as_a_dash():
    assert format_feature(F.FREQUENCY, None) == "-"


# ---------- CSV of a capture ----------

def test_waveform_csv_has_time_and_volts(tmp_path):
    path = tmp_path / "cap.csv"
    write_waveform_csv(str(path), [1.0, 1.5, 2.0], 1000.0)
    rows = list(csv.reader(open(path, encoding="utf-8", newline="")))
    assert rows[0] == ["time_s", "volts"]
    assert rows[1:] == [["0", "1.000000"], ["0.001", "1.500000"], ["0.002", "2.000000"]]


def test_waveform_csv_with_a_reference_channel(tmp_path):
    path = tmp_path / "cap.csv"
    write_waveform_csv(str(path), [1.0, 2.0], 500.0, ref_samples=[0.1, 0.2])
    rows = list(csv.reader(open(path, encoding="utf-8", newline="")))
    assert rows[0] == ["time_s", "volts", "ref_volts"]
    assert rows[2] == ["0.002", "2.000000", "0.200000"]
