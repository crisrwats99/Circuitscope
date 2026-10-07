"""
core/waveform.py

Turns RAW SAMPLES (a list of voltages taken by the STM32 at a fixed rate) into
ONE number, such as "frequency = 998.7 Hz". That number is then graded by
grading.py exactly like any other measurement.

The eleven features (see WaveformFeature in models.py):
    dc_offset, peak, peak_to_peak, rms      - simple statistics of the samples
    frequency, period, duty_cycle           - found by locating where the wave crosses its middle level
    rise_time, fall_time                    - how long the 10 % -> 90 % (or 90 % -> 10 %) edge takes
    gain, phase_difference                  - compare TWO signals (output vs reference)

Like the other core files this has NO user-interface and NO hardware code:
numbers in, a number out, so it can be tested with artificial (synthetic) waves.

Conventions (write these in the lab sheet so students and lecturers agree):
    * PEAK is the highest sample value (not the amplitude about zero).
    * RMS is the true RMS of the whole signal INCLUDING any DC offset.
    * Phase is in degrees; POSITIVE means the measured signal LAGS the reference.
    * Gain is a plain ratio (output / reference), not decibels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.models import TWO_CHANNEL_FEATURES, WaveformFeature, WaveformSpec

# A capture must contain at least this many samples to be worth analysing.
MIN_SAMPLES = 8

# A signal whose peak-to-peak swing is smaller than this is treated as "no signal"
# when we need to find cycles or edges. Without it, the few millivolts of ADC noise on
# a steady DC level would be "measured" as a wave with a meaningless frequency.
# The box measures +/-15 V with a 12-bit converter, so one count is about 9 mV of
# INPUT voltage and 2 mV of noise at the converter pin is about 22 mV at the input.
# 0.25 V (about 28 counts) is well above that noise.
MIN_SWING_V = 0.25

# Fraction of the peak-to-peak swing used as hysteresis when detecting crossings.
# The signal must move this far past the middle level before a crossing counts,
# so small noise wiggles around the middle are not counted as extra cycles.
HYSTERESIS_FRACTION = 0.1


class WaveformError(Exception):
    """The samples cannot give the requested feature (flat line, too short, ...)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_array(samples) -> np.ndarray:
    """Convert the samples to a 1-D float array and make sure they are usable."""
    x = np.asarray(samples, dtype=float)
    if x.ndim != 1 or x.size < MIN_SAMPLES:
        raise WaveformError(f"A waveform needs at least {MIN_SAMPLES} samples in one list of numbers, but {x.size if x.ndim == 1 else 'a table of numbers'} were given. Capture more points.")
    if not np.all(np.isfinite(x)):
        raise WaveformError(f"{int(np.sum(~np.isfinite(x)))} of the {x.size} samples are NaN or infinite, so the waveform cannot be analysed. Capture it again.")
    return x


def _check_rate(sample_rate_hz: float) -> float:
    """The sample rate must be a positive, finite number."""
    if not isinstance(sample_rate_hz, (int, float)) or not math.isfinite(sample_rate_hz) \
            or sample_rate_hz <= 0:
        raise WaveformError("The sample rate must be a positive number.")
    return float(sample_rate_hz)


def _mid_level(x: np.ndarray) -> float:
    """The middle of the signal: halfway between its lowest and highest sample."""
    return (float(x.max()) + float(x.min())) / 2.0


def _crossings(x: np.ndarray) -> tuple[list[float], list[float]]:
    """
    Find where the signal crosses its middle level.

    Returns (rising, falling): two lists of positions, measured in SAMPLES
    (they are fractional, e.g. 12.37, because we interpolate between the two
    samples either side of the crossing for better accuracy).

    A small hysteresis is used: the signal must first go clearly BELOW the middle
    before a rising crossing counts (and clearly ABOVE before a falling one), so
    noise near the middle cannot create false crossings.
    """
    swing = float(x.max() - x.min())
    if swing < MIN_SWING_V:
        raise WaveformError("The signal is flat: there is no wave to analyse.")

    mid = _mid_level(x)
    margin = HYSTERESIS_FRACTION * swing / 2.0
    low_gate, high_gate = mid - margin, mid + margin

    # Pass 1: rising edges (clearly low, then clearly high).
    rising: list[float] = []
    state = None                       # None until the signal clearly commits high or low
    for i, v in enumerate(x):          # i = sample number, v = its voltage
        if v <= low_gate:
            state = "low"
        elif v >= high_gate:
            if state == "low":         # was low, now high -> a rising edge happened
                rising.append(_interpolate(x, i, mid, going_up=True))
            state = "high"

    # Pass 2: falling edges (clearly high, then clearly low).
    falling: list[float] = []
    state = None
    for i, v in enumerate(x):
        if v >= high_gate:
            state = "high"
        elif v <= low_gate:
            if state == "high":        # was high, now low -> a falling edge happened
                falling.append(_interpolate(x, i, mid, going_up=False))
            state = "low"
    return rising, falling


def _interpolate(x: np.ndarray, i: int, level: float, going_up: bool) -> float:
    """
    Sample `i` is the first one clearly past `level`. Return the exact (fractional)
    position where the edge crosses `level`.

    Joining just the two samples either side of the level is easily thrown off by
    noise (it made a noisy phase read several degrees low), so the position is found
    by fitting a straight line to ALL the samples on the middle part of the edge
    (from a quarter-swing below the level to a quarter-swing above it) and seeing
    where that line passes through the level. A very sharp edge has too few samples
    for a fit, so it falls back to the simple two-sample method.
    """
    half = (float(x.max()) - float(x.min())) / 2.0
    low_edge, high_edge = level - 0.5 * half, level + 0.5 * half
    start_bound, end_bound = (low_edge, high_edge) if going_up else (high_edge, low_edge)

    # The edge region: walk back to where the edge left its starting side, then
    # forward to where it reaches the far side.
    a = i
    while a > 0 and (x[a] > start_bound if going_up else x[a] < start_bound):
        a -= 1
    b = i
    while b < len(x) - 1 and (x[b] < end_bound if going_up else x[b] > end_bound):
        b += 1
    if b - a + 1 >= 4:
        slope, intercept = np.polyfit(np.arange(a, b + 1), x[a:b + 1], 1)
        if (slope > 0) == going_up and slope != 0:
            return float((level - intercept) / slope)

    # Fallback: two samples either side of the level.
    j = i
    if going_up:
        while j > 0 and x[j - 1] >= level:     # step back until the previous sample is below
            j -= 1
    else:
        while j > 0 and x[j - 1] <= level:     # step back until the previous sample is above
            j -= 1
    if j == 0:
        return 0.0
    lo, hi = float(x[j - 1]), float(x[j])      # the two samples either side of the level
    if hi == lo:
        return float(j)
    return (j - 1) + (level - lo) / (hi - lo)  # fraction of the way from j-1 to j


def _frequency(x: np.ndarray, rate: float) -> float:
    """Average frequency from the time between FIRST and LAST rising crossing."""
    rising, _ = _crossings(x)
    if len(rising) < 2:
        raise WaveformError("Fewer than two cycles were captured, so the frequency "
                            "cannot be measured. Capture for longer.")
    cycles = len(rising) - 1                    # N edges bound N-1 whole cycles
    seconds = (rising[-1] - rising[0]) / rate   # samples -> seconds
    return cycles / seconds


def _duty_cycle(x: np.ndarray) -> float:
    """Fraction (0..1) of each cycle spent above the middle level, averaged."""
    rising, falling = _crossings(x)
    if len(rising) < 2:
        raise WaveformError("Fewer than two cycles were captured, so the duty cycle "
                            "cannot be measured.")
    fractions = []
    for start, end in zip(rising[:-1], rising[1:]):        # one full cycle each
        # The falling edge that lies inside this cycle.
        inside = [f for f in falling if start < f < end]
        if inside:
            fractions.append((inside[0] - start) / (end - start))
    if not fractions:
        raise WaveformError("No complete cycle was found.")
    return float(np.mean(fractions))


def _edge_time(x: np.ndarray, rate: float, rising: bool) -> float:
    """
    Average 10 % -> 90 % rise time (or 90 % -> 10 % fall time), in seconds.

    The "low" and "high" levels are the MEDIAN of the samples below / above the
    middle, so a single noisy spike or overshoot does not distort them.
    A falling edge is measured by flipping the signal upside down.
    """
    if float(x.max() - x.min()) < MIN_SWING_V:
        raise WaveformError("The signal is flat: there is no edge to measure.")
    y = x if rising else -x
    mid = _mid_level(y)
    base = float(np.median(y[y < mid]))         # typical "low" level
    top = float(np.median(y[y >= mid]))         # typical "high" level
    lo = base + 0.1 * (top - base)              # 10 % point
    hi = base + 0.9 * (top - base)              # 90 % point
    if top - base < MIN_SWING_V:
        raise WaveformError("The signal is flat: there is no edge to measure.")

    edge_starts, _ = _crossings(y)              # one position per rising edge
    times = []
    for pos in edge_starts:
        m = int(pos)
        # Walk back from the edge to the last sample at or below the 10 % point...
        a = m
        while a > 0 and y[a] > lo:
            a -= 1
        # ...and forward to the first sample at or above the 90 % point.
        b = m
        while b < len(y) - 1 and y[b] < hi:
            b += 1
        if y[a] > lo or y[b] < hi or a >= b:
            continue                            # edge cut off by the start/end of the capture
        t_lo = _level_position(y, a, lo)
        t_hi = _level_position(y, b - 1, hi)
        times.append((t_hi - t_lo) / rate)
    if not times:
        raise WaveformError("No complete edge was captured. Capture a longer or "
                            "better-triggered waveform.")
    return float(np.mean(times))


def _level_position(y: np.ndarray, i: int, level: float) -> float:
    """Fractional sample position where the line from y[i] to y[i+1] reaches `level`."""
    a, b = float(y[i]), float(y[i + 1])
    if b == a:
        return float(i)
    return i + (level - a) / (b - a)


def _gain(x: np.ndarray, ref: np.ndarray) -> float:
    """Output peak-to-peak divided by reference peak-to-peak."""
    swing_ref = float(ref.max() - ref.min())
    if swing_ref < MIN_SWING_V:
        raise WaveformError("The reference signal is flat, so the gain is undefined.")
    return float(x.max() - x.min()) / swing_ref


def _phase_difference(x: np.ndarray, ref: np.ndarray) -> float:
    """
    Phase of `x` relative to `ref`, in degrees (-180 to +180).
    Positive = x lags (happens later than) the reference.

    Method: for each rising edge of the reference, find the next rising edge of
    x and convert the time gap into a fraction of the period. The gaps are then
    averaged as angles on a circle, so +179 and -179 average to 180, not 0.
    """
    ref_edges, _ = _crossings(ref)
    x_edges, _ = _crossings(x)
    if len(ref_edges) < 2 or len(x_edges) < 1:
        raise WaveformError("Not enough cycles were captured to measure the phase.")
    period = (ref_edges[-1] - ref_edges[0]) / (len(ref_edges) - 1)   # in samples

    angles = []
    for r in ref_edges:
        # delay (in samples) to the nearest x edge, folded into one period
        delay = min(((e - r) % period) for e in x_edges)
        angles.append(2 * math.pi * delay / period)
    # Circular mean: average the angles as points on a circle.
    mean = math.atan2(np.mean(np.sin(angles)), np.mean(np.cos(angles)))
    return math.degrees(mean)               # already in the range -180..+180


# ---------------------------------------------------------------------------
# The public function the rest of the application calls.
# ---------------------------------------------------------------------------

def compute_feature(feature: WaveformFeature, samples, sample_rate_hz: float,
                    ref_samples=None) -> float:
    """
    Calculate ONE feature of a captured waveform.

    `feature`         which of the eleven features to calculate.
    `samples`         the measured voltages (a list or numpy array).
    `sample_rate_hz`  how fast they were sampled (needed for any time value).
    `ref_samples`     the reference channel, only for GAIN and PHASE_DIFFERENCE.

    Raises WaveformError if the samples cannot give that feature.
    """
    rate = _check_rate(sample_rate_hz)
    x = _as_array(samples)

    # Two-signal features need a reference channel of the same length.
    if feature in TWO_CHANNEL_FEATURES:
        if ref_samples is None:
            raise WaveformError(f"{feature.value.replace('_', ' ').capitalize()} needs a reference channel.")
        ref = _as_array(ref_samples)
        n = min(len(x), len(ref))           # use the part both captures share
        x, ref = x[:n], ref[:n]
        if feature == WaveformFeature.GAIN:
            return _gain(x, ref)
        return _phase_difference(x, ref)

    if feature == WaveformFeature.DC_OFFSET:
        return float(x.mean())
    if feature == WaveformFeature.PEAK:
        return float(x.max())
    if feature == WaveformFeature.PEAK_TO_PEAK:
        return float(x.max() - x.min())
    if feature == WaveformFeature.RMS:
        return float(np.sqrt(np.mean(x ** 2)))
    if feature == WaveformFeature.FREQUENCY:
        return _frequency(x, rate)
    if feature == WaveformFeature.PERIOD:
        return 1.0 / _frequency(x, rate)
    if feature == WaveformFeature.DUTY_CYCLE:
        return _duty_cycle(x)
    if feature == WaveformFeature.RISE_TIME:
        return _edge_time(x, rate, rising=True)
    if feature == WaveformFeature.FALL_TIME:
        return _edge_time(x, rate, rising=False)
    raise WaveformError(f"Unknown feature: {feature}")


def compute_from_spec(spec: WaveformSpec, samples, ref_samples=None) -> float:
    """Same as compute_feature, but takes the settings from an item's WaveformSpec."""
    return compute_feature(spec.feature, samples, spec.sample_rate_hz, ref_samples)


# ---------------------------------------------------------------------------
# Measuring EVERY feature at once (for the waveform viewer and tutor screens)
# ---------------------------------------------------------------------------

@dataclass
class FeatureResult:
    """One feature of a capture: its value, or the reason it could not be measured."""
    feature: WaveformFeature
    value: float | None = None       # None when it could not be measured
    error: str = ""                  # the reason (shown to the user)


def compute_all(samples, sample_rate_hz: float, ref_samples=None) -> list[FeatureResult]:
    """
    Try all eleven features and report each one. A feature that cannot be measured
    (e.g. frequency of a flat line, or gain with no reference channel) gets an
    explanation instead of stopping the others.
    """
    results = []
    for feature in WaveformFeature:                       # the enum lists them in a sensible order
        try:
            results.append(FeatureResult(feature, compute_feature(
                feature, samples, sample_rate_hz, ref_samples)))
        except WaveformError as exc:
            results.append(FeatureResult(feature, None, str(exc)))
    return results


def format_feature(feature: WaveformFeature, value: float | None) -> str:
    """A feature value as text with sensible units, e.g. 998.7 Hz, 1.000 ms, 50.0 %."""
    if value is None:
        return "-"
    if feature == WaveformFeature.FREQUENCY:
        return f"{value:.1f} Hz"
    if feature == WaveformFeature.PERIOD:
        return f"{value * 1000:.3f} ms"
    if feature == WaveformFeature.DUTY_CYCLE:
        return f"{value * 100:.1f} %"
    if feature == WaveformFeature.GAIN:
        return f"{value:.3f}"                             # a plain ratio
    if feature == WaveformFeature.PHASE_DIFFERENCE:
        return f"{value:.1f} \u00b0"                      # degree sign
    if feature in (WaveformFeature.RISE_TIME, WaveformFeature.FALL_TIME):
        return f"{value * 1e6:.1f} \u00b5s"               # micro sign
    return f"{value:.3f} V"                               # peak, peak-to-peak, RMS, DC offset
