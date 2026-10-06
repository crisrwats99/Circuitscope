"""Automatic checks for core/waveform.py. Run with:  python -m pytest

Every test builds an ARTIFICIAL wave whose true answer we already know
(e.g. a 1000 Hz sine), runs the extractor, and checks it finds that answer.
"""

import numpy as np
import pytest

from core.models import WaveformFeature as F, WaveformSpec
from core.waveform import WaveformError, compute_feature, compute_from_spec

RATE = 100_000.0          # 100 kHz sampling in the tests (fast = accurate)


# ---------- helpers that build artificial waves ----------

def time_axis(n=5000, rate=RATE):
    """The time of each sample: 0, 1/rate, 2/rate, ..."""
    return np.arange(n) / rate


def sine(freq, amp=1.0, offset=0.0, phase_deg=0.0, n=5000, rate=RATE):
    """amp * sin(2 pi f t + phase) + offset"""
    t = time_axis(n, rate)
    return offset + amp * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))


def square(freq, low=0.0, high=3.3, duty=0.5, n=5000, rate=RATE, edge_s=0.0):
    """
    A pulse train. `duty` is the fraction of each period spent high.
    `edge_s` makes the edges ramp over that many seconds (0 = instant).
    """
    t = time_axis(n, rate)
    pos = (t * freq) % 1.0                         # position inside the cycle, 0..1
    wave = np.where(pos < duty, high, low).astype(float)
    if edge_s > 0:
        # Smooth the edges with a moving average of the right length.
        k = max(1, int(round(edge_s * rate)))
        kernel = np.ones(k) / k
        padded = np.concatenate([np.full(k, wave[0]), wave])
        wave = np.convolve(padded, kernel, mode="same")[k:]
    return wave


def triangle_ramp(freq, n=5000, rate=RATE, edge_s=1e-4):
    """A trapezoid pulse train with linear 0 -> 3 V edges lasting `edge_s`."""
    t = time_axis(n, rate)
    period = 1.0 / freq
    pos = t % period
    half = period / 2
    up = np.clip(pos / edge_s, 0, 1)                         # rising ramp at the start
    down = np.clip((pos - half) / edge_s, 0, 1)              # falling ramp at half period
    return 3.0 * (up - down)


# ---------- simple statistics ----------

def test_dc_offset_peak_peak_to_peak_and_rms_of_a_sine():
    x = sine(1000, amp=2.0, offset=1.5)
    assert compute_feature(F.DC_OFFSET, x, RATE) == pytest.approx(1.5, abs=0.01)
    assert compute_feature(F.PEAK, x, RATE) == pytest.approx(3.5, abs=0.01)
    assert compute_feature(F.PEAK_TO_PEAK, x, RATE) == pytest.approx(4.0, abs=0.01)
    # RMS includes the DC part: sqrt(1.5^2 + 2^2/2) = 2.0615...
    assert compute_feature(F.RMS, x, RATE) == pytest.approx(2.0616, abs=0.01)


def test_rms_of_a_pure_sine_is_amplitude_over_root_two():
    x = sine(1000, amp=1.0, n=5000)               # exactly 50 whole cycles
    assert compute_feature(F.RMS, x, RATE) == pytest.approx(0.7071, abs=0.001)


def test_statistics_of_a_flat_dc_signal():
    x = np.full(100, 2.5)
    assert compute_feature(F.DC_OFFSET, x, RATE) == pytest.approx(2.5)
    assert compute_feature(F.PEAK_TO_PEAK, x, RATE) == pytest.approx(0.0)
    assert compute_feature(F.RMS, x, RATE) == pytest.approx(2.5)


# ---------- frequency and period ----------

@pytest.mark.parametrize("freq", [50, 440, 1000, 2500, 10_000])
def test_frequency_of_sines(freq):
    x = sine(freq, offset=1.65, amp=1.0, n=5000)
    assert compute_feature(F.FREQUENCY, x, RATE) == pytest.approx(freq, rel=0.002)


def test_period_is_one_over_frequency():
    x = sine(1000, offset=1.65)
    assert compute_feature(F.PERIOD, x, RATE) == pytest.approx(0.001, rel=0.002)


def test_frequency_does_not_depend_on_dc_offset_or_amplitude():
    a = compute_feature(F.FREQUENCY, sine(800, amp=0.2, offset=2.9), RATE)
    b = compute_feature(F.FREQUENCY, sine(800, amp=3.0, offset=-1.0), RATE)
    assert a == pytest.approx(800, rel=0.002) and b == pytest.approx(800, rel=0.002)


def test_frequency_survives_noise():
    rng = np.random.default_rng(1)                          # fixed seed: same noise each run
    x = sine(1000, amp=1.0, n=5000) + rng.normal(0, 0.05, 5000)
    assert compute_feature(F.FREQUENCY, x, RATE) == pytest.approx(1000, rel=0.01)


def test_frequency_of_a_square_wave():
    assert compute_feature(F.FREQUENCY, square(2000), RATE) == pytest.approx(2000, rel=0.002)


# ---------- duty cycle ----------

@pytest.mark.parametrize("duty", [0.2, 0.5, 0.75])
def test_duty_cycle_of_pulse_trains(duty):
    x = square(1000, duty=duty)
    assert compute_feature(F.DUTY_CYCLE, x, RATE) == pytest.approx(duty, abs=0.01)


# ---------- rise and fall time ----------

def test_rise_and_fall_time_of_a_trapezoid():
    # Linear edges lasting 100 us from 0 to 3 V: the 10-90 % time is 80 % of that = 80 us.
    x = triangle_ramp(1000, edge_s=1e-4)
    assert compute_feature(F.RISE_TIME, x, RATE) == pytest.approx(80e-6, rel=0.1)
    assert compute_feature(F.FALL_TIME, x, RATE) == pytest.approx(80e-6, rel=0.1)


def test_slower_edges_give_longer_rise_time():
    fast = compute_feature(F.RISE_TIME, triangle_ramp(1000, edge_s=5e-5), RATE)
    slow = compute_feature(F.RISE_TIME, triangle_ramp(1000, edge_s=2e-4), RATE)
    assert slow > 2 * fast


def test_rise_and_fall_can_differ():
    # Slow rising edge, fast falling edge (a sawtooth-like pulse).
    t = time_axis()
    period = 1e-3
    pos = t % period
    x = np.where(pos < 4e-4, 3.0 * pos / 4e-4, np.where(pos < 4.5e-4, 3.0 * (1 - (pos - 4e-4) / 5e-5), 0.0))
    rise = compute_feature(F.RISE_TIME, x, RATE)
    fall = compute_feature(F.FALL_TIME, x, RATE)
    assert rise > 4 * fall


# ---------- gain and phase (two channels) ----------

def test_gain_is_output_over_reference():
    ref = sine(1000, amp=1.0, offset=1.65)
    out = sine(1000, amp=0.5, offset=1.65)
    assert compute_feature(F.GAIN, out, RATE, ref_samples=ref) == pytest.approx(0.5, abs=0.01)


@pytest.mark.parametrize("lag", [0, 30, 90, 135, -45, -120])
def test_phase_difference_of_sines(lag):
    ref = sine(1000, offset=1.65)
    out = sine(1000, offset=1.65, phase_deg=-lag)           # lagging = shifted later = negative phase angle
    result = compute_feature(F.PHASE_DIFFERENCE, out, RATE, ref_samples=ref)
    assert result == pytest.approx(lag, abs=2.0)


def test_phase_of_180_degrees_is_reported_as_magnitude_180():
    ref = sine(1000)
    out = sine(1000, phase_deg=180)
    result = compute_feature(F.PHASE_DIFFERENCE, out, RATE, ref_samples=ref)
    assert abs(result) == pytest.approx(180, abs=3.0)


def test_two_channel_features_need_a_reference():
    with pytest.raises(WaveformError):
        compute_feature(F.GAIN, sine(1000), RATE)
    with pytest.raises(WaveformError):
        compute_feature(F.PHASE_DIFFERENCE, sine(1000), RATE)


def test_gain_with_flat_reference_is_refused():
    with pytest.raises(WaveformError):
        compute_feature(F.GAIN, sine(1000), RATE, ref_samples=np.full(5000, 1.0))


# ---------- bad input is refused, not guessed at ----------

def test_flat_signal_has_no_frequency():
    with pytest.raises(WaveformError):
        compute_feature(F.FREQUENCY, np.full(1000, 1.0), RATE)


def test_less_than_two_cycles_cannot_give_a_frequency():
    x = sine(1000, n=150)                                   # only 1.5 ms = 1.5 cycles at 100 kHz
    with pytest.raises(WaveformError):
        compute_feature(F.FREQUENCY, x, RATE)


def test_too_few_samples_are_refused():
    with pytest.raises(WaveformError):
        compute_feature(F.PEAK, [1.0, 2.0], RATE)


def test_nan_samples_are_refused():
    x = sine(1000)
    x[10] = np.nan
    with pytest.raises(WaveformError):
        compute_feature(F.RMS, x, RATE)


@pytest.mark.parametrize("rate", [0, -5, float("nan"), float("inf")])
def test_bad_sample_rate_is_refused(rate):
    with pytest.raises(WaveformError):
        compute_feature(F.FREQUENCY, sine(1000), rate)


# ---------- connection to the data model ----------

def test_compute_from_spec_uses_the_items_settings():
    spec = WaveformSpec(feature=F.FREQUENCY, channel="P1", sample_rate_hz=RATE)
    assert compute_from_spec(spec, sine(1000, offset=1.65)) == pytest.approx(1000, rel=0.002)


def test_measured_feature_can_be_graded_end_to_end():
    # Waveform -> number -> grader, the whole path a waveform item will take.
    from core.models import Item, AnswerMode, ToleranceMode
    from core.grading import grade_item, Status
    spec = WaveformSpec(feature=F.FREQUENCY, channel="P1", sample_rate_hz=RATE)
    item = Item(id="w1", description="f", marks=2, mode=AnswerMode.WAVEFORM,
                expected_formula="1 / (2 * pi * R * C)", waveform=spec,
                tolerance_mode=ToleranceMode.PERCENT, tolerance=5)
    # An RC with R = 1 kOhm, C = 159.155 nF has f = 1000 Hz.
    values = {"R": 1000, "C": 1.59155e-7}
    measured = compute_from_spec(spec, sine(1000, offset=1.65))
    assert grade_item(item, measured, values).status == Status.CORRECT
    wrong = compute_from_spec(spec, sine(1300, offset=1.65))
    assert grade_item(item, wrong, values).status == Status.INCORRECT