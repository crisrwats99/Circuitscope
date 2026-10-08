"""
core/channels.py

The box's inputs, in ONE place. Both the app and (later) the firmware must agree
on these names, so if the hardware changes, this is the only file to edit.

    Voltage test points   P1..P5  (STM32 pins PA0, PA1, PA4, PA5, PA6)
    Current inputs        I1, I2  (STM32 pins PB0 and PA7, each with its own CURRENT_SENSE circuit)

(PA7 used to be a sixth voltage input, P6. It is a current input now. Everything else in the
app follows these two lists, so a further change to the hardware is a change here only.)

Units used throughout the app: voltages in volts, currents in MILLIAMPS.
"""

VOLTAGE_CHANNELS = ["P1", "P2", "P3", "P4", "P5"]
CURRENT_CHANNELS = ["I1", "I2"]


def voltage_range_text() -> str:
    return f"{VOLTAGE_CHANNELS[0]} to {VOLTAGE_CHANNELS[-1]}"
