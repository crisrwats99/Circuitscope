"""
hardware/simulator.py

A FAKE measurement box, so the whole application can be built and tested before
the real STM32 firmware exists (and later, for demos and student practice
without a box).

  SimulatedBoard  - behaves like the firmware: receives a command line, returns
                    the reply lines (see protocol.py). You tell it what voltage
                    is on each test point.
  SimulatedPort   - looks like a serial port to the driver (write / readline),
                    but connects to a SimulatedBoard.

    board = SimulatedBoard()
    board.set_dc("P1", 3.3)                                   # a steady 3.3 V
    board.set_wave("P2", lambda t: 1.65 + np.sin(2*np.pi*1000*t))   # a 1 kHz sine
    device = SerialDevice(SimulatedPort(board))

Because the driver talks to it exactly as to real hardware, everything in
hardware/device.py is exercised by the same code path.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

import numpy as np

from hardware import protocol
from core.channels import CURRENT_CHANNELS, VOLTAGE_CHANNELS, scan_lag_s
from hardware.calibration import CURRENT_SENSE, DIRECT, FrontEnd

TIMER_CLOCK_HZ = 170_000_000      # the G431 timer clock: sample rates are TIMER_CLOCK / whole number
MAX_LINE_LENGTH = 64              # the firmware discards a command line longer than this


class SimulatedBoard:
    def __init__(self, uid_words=(0x0012AB34, 0xCD56EF78, 0x0011AABB), *,
                 max_points: int = 4000, max_rate_hz: int = 100_000,
                 adc_bits: int = 12, vref_v: float = 3.3,
                 noise_v: float = 0.0, seed: int = 1, protocol_version: int | None = None,
                 adc_gain: float = 1.0, adc_offset_v: float = 0.0,
                 front_end: FrontEnd = DIRECT):
        self.uid_words = uid_words
        self.max_points = max_points
        self.max_rate_hz = max_rate_hz
        self.adc_bits = adc_bits
        self.vref_v = vref_v
        self.noise_v = noise_v                      # random noise (volts, standard deviation)
        self.adc_gain = adc_gain                    # imperfect converter: reads true volts x gain ...
        self.adc_offset_v = adc_offset_v            # ... plus this offset (what calibration must correct)
        self.front_end = front_end                  # the analogue circuit in front of the converter
        self.protocol_version = protocol_version or protocol.PROTOCOL_VERSION
        self._rng = np.random.default_rng(seed)     # fixed seed -> same noise every run
        self._signals: dict[str, float | Callable] = {}
        self.fail_next_sample = False               # True = the next SAMPLE fails in hardware (ERR HWFAIL)
        # The inputs the board answers to. The current inputs have their own analogue circuit
        # (a signal on I1 is in MILLIAMPS), so each channel can have its own front end.
        self.known_channels = set(VOLTAGE_CHANNELS) | set(CURRENT_CHANNELS)
        # every current input has its own copy of the current circuit
        self.channel_front_ends: dict[str, FrontEnd] = {ch: CURRENT_SENSE for ch in CURRENT_CHANNELS}

    # --- tell the board what is connected to each test point ---------------

    def set_dc(self, channel: str, volts: float) -> None:
        """A steady voltage on a test point (or, for a current input such as I1, a steady current in mA)."""
        self._signals[channel] = float(volts)

    def set_wave(self, channel: str, function: Callable[[np.ndarray], np.ndarray]) -> None:
        """A changing voltage: `function` takes an array of times (seconds), returns volts."""
        self._signals[channel] = function

    # --- the firmware's behaviour -------------------------------------------

    def handle(self, line: str) -> list[str]:
        """One command line in -> the list of reply lines out."""
        if len(line) > MAX_LINE_LENGTH:             # the firmware discards over-long lines
            return [f"ERR BADCMD line is longer than {MAX_LINE_LENGTH} characters"]
        parts = line.split()
        if not parts:
            return []                               # a blank line is ignored: no reply at all
        command = parts[0]

        if command == "PING":
            return ["PONG"]
        if command == "INFO?":
            return [f"INFO {self.protocol_version} {self.max_points} {self.max_rate_hz} "
                    f"{self.adc_bits} {int(round(self.vref_v * 1000))}"]
        if command == "ID?":
            return ["ID " + " ".join(f"{w:08X}" for w in self.uid_words)]
        if command == "SAMPLE":
            return self._sample(parts[1:])
        return [f"ERR BADCMD Unknown command {command}"]

    def _sample(self, args: list[str]) -> list[str]:
        if len(args) != 3:
            return ["ERR BADARGS SAMPLE needs: channels rate count"]
        channels = args[0].split(",")
        if "" in channels or len(channels) > protocol.MAX_CHANNELS_PER_SAMPLE or len(set(channels)) != len(channels):
            return [f"ERR BADARGS use one or two different channels, e.g. P1 or P1,P2 (got '{args[0]}')"]
        try:
            rate, count = int(args[1]), int(args[2])
        except ValueError:
            return ["ERR BADARGS rate and count must be whole numbers"]
        for ch in channels:
            if ch not in self.known_channels:
                return [f"ERR BADCH unknown channel '{ch}'; valid channels are "
                        f"{' '.join(sorted(self.known_channels))} (upper-case)"]
        if not 1 <= rate <= self.max_rate_hz:
            return [f"ERR BADRATE Rate must be 1..{self.max_rate_hz} Hz"]
        if not 1 <= count <= self.max_points:
            return [f"ERR BADCOUNT Count must be 1..{self.max_points}"]

        # The hardware timer can only divide its clock by a whole number, so the
        # real sample rate is slightly different from the one asked for.
        divider = max(1, round(TIMER_CLOCK_HZ / rate))
        actual_rate = TIMER_CLOCK_HZ / divider
        t = np.arange(count) / actual_rate                    # time of each sample
        if self.fail_next_sample:                             # a hardware failure, for testing the app's reaction
            self.fail_next_sample = False
            return ["ERR HWFAIL capture timed out after 5000 ms; check TIM2 TRGO = Update Event"]

        full_scale = 2 ** self.adc_bits - 1
        replies = []
        for index, ch in enumerate(channels):
            signal = self._signals.get(ch, 0.0)               # unconnected point reads 0 V
            # Two channels on the same converter are read one after the other: the second is taken
            # a moment later than the first, exactly as the real box does.
            lag = scan_lag_s(channels[0], ch) if index == 1 else 0.0
            volts = signal(t + lag) if callable(signal) else np.full(count, signal)
            # input volts -> front end -> voltage at the converter pin -> imperfect converter
            front_end = self.channel_front_ends.get(ch, self.front_end)
            volts = front_end.to_pin(volts) * self.adc_gain + self.adc_offset_v
            if self.noise_v:
                volts = volts + self._rng.normal(0.0, self.noise_v, count)
            # The converter can only see 0..vref and only whole counts.
            counts = np.clip(np.round(volts / self.vref_v * full_scale), 0, full_scale).astype(int)
            replies.append(f"DATA {count} " + ",".join(str(c) for c in counts))
        replies.append(f"DONE {actual_rate:.3f}")
        return replies


class SimulatedPort:
    """Looks like a serial port; the other end is a SimulatedBoard."""

    def __init__(self, board: SimulatedBoard):
        self.board = board
        self._to_pc: deque[bytes] = deque()      # reply lines waiting to be read
        self._partial = b""                      # command bytes received so far (no newline yet)
        self.silent = False                      # True = the box never answers (simulates unplugged/hung)
        self.timeout = 3.0                       # like pyserial: how long the driver will wait for a reply line
        self.timeout_log: list[float] = []       # the timeout in force when each command arrived (for tests)
        self.ignore_next = 0                     # the next N commands get NO reply (a box that froze, then reset)
        self.closed = False
        self.commands: list[str] = []            # every command received (handy in tests)

    def write(self, data: bytes) -> int:
        self._partial += data
        while b"\n" in self._partial:
            raw, self._partial = self._partial.split(b"\n", 1)
            line = raw.decode("ascii", errors="replace").strip()
            self.commands.append(line)
            self.timeout_log.append(self.timeout)
            if self.ignore_next > 0:
                self.ignore_next -= 1
                continue
            if not self.silent:
                for reply in self.board.handle(line):
                    self._to_pc.append((reply + "\r\n").encode("ascii"))     # the box ends every reply line with CR+LF
        return len(data)

    def readline(self) -> bytes:
        """The next reply line, or b'' when there is none (a real port returns b'' on timeout)."""
        return self._to_pc.popleft() if self._to_pc else b""

    def reset_input_buffer(self) -> None:
        self._to_pc.clear()

    def close(self) -> None:
        self.closed = True
