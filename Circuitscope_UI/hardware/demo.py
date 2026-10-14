"""
hardware/demo.py

A ready-made SIMULATED box with a few signals attached, so the application can
be tried out (and demonstrated) without any hardware:

    P1  3.0 V steady          P3  0 V (ground)         I1  +4.70 mA steady
    P2  1.2 V steady          P4  1 kHz sine, 1.65 V centre, 1 V amplitude
                              P5  the same sine but half size and 45 degrees late
                                                   I2  -12.00 mA steady (flowing the other way)
"""

from __future__ import annotations

import numpy as np

from hardware.calibration import CIRCUITSCOPE
from hardware.device import SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort


def make_demo_board() -> SimulatedBoard:
    board = SimulatedBoard(noise_v=0.002, front_end=CIRCUITSCOPE)   # 2 mV of noise at the converter pin
    board.set_dc("P1", 3.0)
    board.set_dc("P2", 1.2)
    board.set_dc("P3", 0.0)
    board.set_dc("I1", 4.70)                    # 4.7 mA flowing through the first current input
    board.set_dc("I2", -12.00)                  # 12 mA the other way through the second one
    board.set_wave("P4", lambda t: 1.65 + 1.0 * np.sin(2 * np.pi * 1000 * t))
    board.set_wave("P5", lambda t: 1.65 + 0.5 * np.sin(2 * np.pi * 1000 * t - np.radians(45)))
    return board


def make_demo_device() -> SerialDevice:
    """A driver wired to the demo board. NOT connected yet (call .connect())."""
    return SerialDevice(SimulatedPort(make_demo_board()), front_end=CIRCUITSCOPE)
