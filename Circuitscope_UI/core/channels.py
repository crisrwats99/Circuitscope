"""
core/channels.py

The box's inputs, in ONE place. Both the app and (later) the firmware must agree
on these names, so if the hardware changes, this is the only file to edit.

    Voltage test points   P1..P6  (STM32 pins PA0, PA1, PA4, PA5, PA6, PA7)
    Current inputs        I1      (STM32 pin PB0, the CURRENT_SENSE circuit)

To make PA7 a second current input: remove "P6" from VOLTAGE_CHANNELS and add "I2"
to CURRENT_CHANNELS. (Everything else in the app follows these two lists.)

Units used throughout the app: voltages in volts, currents in MILLIAMPS.
"""

VOLTAGE_CHANNELS = ["P1", "P2", "P3", "P4", "P5", "P6"]
CURRENT_CHANNELS = ["I1"]


def voltage_range_text() -> str:
    return f"{VOLTAGE_CHANNELS[0]} to {VOLTAGE_CHANNELS[-1]}"
