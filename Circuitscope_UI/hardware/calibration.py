"""
hardware/calibration.py

Turns raw ADC counts into volts.

    volts = counts * volts_per_count + offset_v

Ideal converter: volts_per_count = vref / full_scale (3.3 / 4095) and offset 0.
Real hardware is slightly off (reference tolerance, op-amp offset...), so the
calibration screen will measure two known voltages (e.g. 0 V and 3.0 V) and
call fit_two_point() to find the true values for THIS box.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Calibration:
    volts_per_count: float          # slope: volts that ONE count is worth
    offset_v: float = 0.0           # volts read when the count is 0

    @classmethod
    def ideal(cls, vref_v: float = 3.3, adc_bits: int = 12) -> "Calibration":
        """The textbook calibration: 0 -> 0 V and full scale -> vref."""
        return cls(volts_per_count=vref_v / (2 ** adc_bits - 1))

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