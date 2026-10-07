"""
hardware/calibration.py

Turns raw ADC counts into volts.

    volts = counts * volts_per_count + offset_v

The box does not see the student's circuit directly. A front-end circuit
(see FrontEnd below) squeezes the +/-15 V input into the converter's 0-3.3 V:

    V_adc = bias + gain * V_in            (design: 1.65 V + 0.09 * V_in)

so the PC works backwards: V_in = (V_adc - bias) / gain. Because V_adc is just
counts * (vref / full_scale), the whole thing is still ONE straight line from
counts to input volts, i.e. the same formula as above with different numbers.

Ideal converter + front end: volts_per_count = vref / full_scale / gain and
offset_v = -bias / gain. Real hardware is slightly off (reference tolerance,
resistor tolerance, op-amp offset...), so the calibration screen measures two
known voltages (e.g. 0 V and 10 V) and calls fit_two_point() to find the true
values for THIS box, which replaces the theoretical ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FrontEnd:
    """
    The analogue circuit between the student's circuit and the converter pin:
        V_adc = bias_v + gain * V_in
    """
    bias_v: float = 0.0       # converter-pin voltage when the input is 0 V
    gain: float = 1.0         # converter-pin volts per input volt (0.09 = input scaled down)

    def to_pin(self, v_in):
        """Input volts -> volts at the converter pin (used by the simulator)."""
        return self.bias_v + self.gain * np.asarray(v_in, dtype=float)


# No front end: the converter sees the input directly (0 - 3.3 V range).
DIRECT = FrontEnd()
# The real Circuitscope front end (Voltage Measurement Design document):
# 180 k / 220 k bias divider and 1 M input resistor, LM358 follower.
# -15 V -> about 0.30 V, 0 V -> 1.65 V, +15 V -> about 3.00 V.
CIRCUITSCOPE = FrontEnd(bias_v=1.65, gain=0.09)


@dataclass
class Calibration:
    volts_per_count: float          # slope: volts that ONE count is worth
    offset_v: float = 0.0           # volts read when the count is 0

    @classmethod
    def ideal(cls, vref_v: float = 3.3, adc_bits: int = 12,
              front_end: FrontEnd = DIRECT) -> "Calibration":
        """
        The textbook calibration from the design values.
        No front end: 0 -> 0 V and full scale -> vref.
        With the front end: V_in = (V_adc - bias) / gain.
        """
        pin_volts_per_count = vref_v / (2 ** adc_bits - 1)
        return cls(volts_per_count=pin_volts_per_count / front_end.gain,
                   offset_v=-front_end.bias_v / front_end.gain)

    @classmethod
    def fit_two_point(cls, counts_low: float, volts_low: float,
                      counts_high: float, volts_high: float) -> "Calibration":
        """
        Calibration from two known points (straight line through them).
        e.g. the box read 12 counts at a real 0.00 V and 3722 counts at a real 3.00 V.
        """
        if counts_high == counts_low:
            raise ValueError("The two calibration readings are identical; use two different voltages.")
        slope = (volts_high - volts_low) / (counts_high - counts_low)
        if slope <= 0:
            raise ValueError("A higher voltage must give a higher count.")
        return cls(volts_per_count=slope, offset_v=volts_low - slope * counts_low)

    def counts_to_volts(self, counts) -> np.ndarray:
        """Convert a count (or a list/array of counts) to volts."""
        return np.asarray(counts, dtype=float) * self.volts_per_count + self.offset_v

    def to_dict(self) -> dict:
        return {"volts_per_count": self.volts_per_count, "offset_v": self.offset_v}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(volts_per_count=float(d["volts_per_count"]), offset_v=float(d.get("offset_v", 0.0)))


def check_plausible(cal: Calibration, ideal: Calibration, max_slope_error: float = 0.2,
                    max_offset_v: float = 0.3) -> str | None:
    """
    Sanity-check a freshly fitted calibration against the ideal one.
    Returns None if it looks believable, otherwise a message explaining why not.
    A real box is within a few percent of ideal; a calibration far from ideal almost
    always means a wiring mistake (wrong channel, reference not connected, low/high swapped).
    """
    offset_error_v = abs(cal.offset_v - ideal.offset_v)       # how far from the design offset
    slope_error = abs(cal.volts_per_count / ideal.volts_per_count - 1.0)
    if slope_error > max_slope_error:
        return (f"The fitted gain is {slope_error:.0%} away from ideal, which is more than a real "
                f"box should be. Check the reference voltages and which channel they are on.")
    if offset_error_v > max_offset_v:
        return (f"The fitted offset is {offset_error_v * 1000:.0f} mV from the design value, more than a real box should "
                f"have. Check the low reference is really connected to the chosen channel.")
    return None
