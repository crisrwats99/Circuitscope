"""
hardware/demo.py

A ready-made SIMULATED box with a few signals attached, so the application can
be tried out (and demonstrated) without any hardware:

    P1  3.0 V steady          P3  0 V (ground)
    P2  1.2 V steady          P4  1 kHz sine, 1.65 V centre, 1 V amplitude
                              P5  the same sine but half size and 45 degrees late
"""

from __future__ import annotations

import numpy as np

from hardware.device import SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort


def make_demo_board() -> SimulatedBoard:
    board = SimulatedBoard(noise_v=0.002)               # 2 mV of noise, like a real converter
    board.set_dc("P1", 3.0)
    board.set_dc("P2", 1.2)
    board.set_dc("P3", 0.0)
    board.set_wave("P4", lambda t: 1.65 + 1.0 * np.sin(2 * np.pi * 1000 * t))
    board.set_wave("P5", lambda t: 1.65 + 0.5 * np.sin(2 * np.pi * 1000 * t - np.radians(45)))
    return board


def make_demo_device() -> SerialDevice:
    """A driver wired to the demo board. NOT connected yet (call .connect())."""
    return SerialDevice(SimulatedPort(make_demo_board()))