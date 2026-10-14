"""
core/channels.py

The box's inputs, in ONE place. Both the app and the firmware must agree on
these names, so if the hardware changes, this is the only file to edit.

    Voltage test points   P1..P5  (STM32 pins PA0, PA1, PA4, PA5, PA6; PF0/PF1 are not on the Nucleo headers)
    Current inputs        I1, I2  (STM32 pins PB0 and PA7, each with its own CURRENT_SENSE circuit)

The STM32 has TWO converters (ADC1 and ADC2) and not every pin can use both. Two channels asked for
together are converted at the SAME instant if they sit on different converters, but ONE AFTER THE
OTHER (the second 1.412 microseconds later) if they can only use the same one. That matters only for
phase between two signals, and is handled in hardware/device.py (see scan_lag_s below).

Units used throughout the app: voltages in volts, currents in MILLIAMPS.
"""

VOLTAGE_CHANNELS = ["P1", "P2", "P3", "P4", "P5"]
CURRENT_CHANNELS = ["I1", "I2"]

# Which converter each pin can use (from the firmware interface reference).
ADC_EITHER = {"P1", "P2"}                       # can use ADC1 or ADC2, so pairs with any channel at once
ADC1_ONLY = {"I1"}
ADC2_ONLY = {"P3", "P4", "P5", "I2"}

# One conversion takes 60 ADC cycles at 42.5 MHz. The second channel of a scanned pair is read this much later.
SCAN_LAG_S = 1.412e-6


def voltage_range_text() -> str:
    return f"{VOLTAGE_CHANNELS[0]} to {VOLTAGE_CHANNELS[-1]}"


def scan_lag_s(first: str, second: str) -> float:
    """
    How much later (seconds) the SECOND channel is sampled than the first, when both are
    requested in one SAMPLE command. 0.0 when they are converted at the same instant.
    """
    if first == second or first in ADC_EITHER or second in ADC_EITHER:
        return 0.0
    same_adc = (first in ADC1_ONLY and second in ADC1_ONLY) or (first in ADC2_ONLY and second in ADC2_ONLY)
    return SCAN_LAG_S if same_adc else 0.0
