"""
hardware/device.py

The PC-side DRIVER for the measurement box. The rest of the application only
uses this class, so it never needs to know about serial ports or text commands.

    device = SerialDevice(port)      # `port` = a real serial port, or the simulator's
    device.connect()                 # handshake: checks the box, reads its limits and UID
    device.uid                       # '0012AB34CD56EF780011AABB'  (the watermark)
    device.measure_voltage("P1", "P3")           # volts between two test points
    device.capture_waveform(item.waveform)       # raw samples for a waveform item

`port` can be anything with  write(bytes), readline() -> bytes (empty on
timeout), close()  - which a real pyserial port and the simulator both have.
That is what lets the whole driver be tested without hardware.
"""

from __future__ import annotations

import time

import numpy as np

from core.channels import CURRENT_CHANNELS, scan_lag_s
from core.models import MAX_WAVEFORM_POINTS, WaveformSpec
from core.submission import WaveformCapture, format_uid
from hardware import protocol
from hardware.calibration import CURRENT_SENSE, DIRECT, Calibration, FrontEnd
from core.waveform import align_delayed_channel
from hardware.protocol import DeviceError, DeviceTimeout, ProtocolError

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 3.0          # how long to wait for one reply line (quick commands; SAMPLE works out its own)
RECOVERY_WAIT_S = 2.5            # the box's watchdog resets a frozen firmware within about 2 seconds


def open_serial_port(port_name: str, baud: int = DEFAULT_BAUD, timeout_s: float = DEFAULT_TIMEOUT_S):
    """Open a real serial port (pyserial is only imported here, so tests do not need it)."""
    try:
        import serial
    except ImportError as exc:
        raise DeviceError("pyserial is not installed (run: pip install pyserial).") from exc
    try:
        return serial.Serial(port_name, baud, timeout=timeout_s)
    except (serial.SerialException, OSError) as exc:
        raise DeviceError(f"Could not open {port_name}: {exc}") from exc


def list_serial_ports() -> list[str]:
    """Names of the serial ports on this computer (e.g. ['COM3', 'COM5'])."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports()]


class SerialDevice:
    """Talks to the box (or the simulator) through `port`."""

    def __init__(self, port, calibration: Calibration | None = None,
                 front_end: FrontEnd = DIRECT):
        self.port = port
        self.front_end = front_end              # the analogue circuit in front of the converter
        self.calibration = calibration          # None = use the ideal one once INFO is known
        self.ideal_calibration: Calibration | None = None   # the textbook one, kept so we can reset to it
        # The current inputs (I1 ...) have their own analogue circuit, so each has its own
        # calibration, in MILLIAMPS per count. None = not calibrated, use the ideal one.
        self.current_calibrations: dict[str, Calibration] = {}
        self.ideal_current_calibration: Calibration | None = None
        self.connected = False
        self.uid = ""                           # filled in by connect()
        self.max_points = MAX_WAVEFORM_POINTS   # replaced by the box's real limits in connect()
        self.recovery_wait_s = RECOVERY_WAIT_S  # how long to wait for a frozen box to reset itself (tests set 0)
        self.max_rate_hz = 0
        self.adc_max = 4095

    # --- low-level helpers ---------------------------------------------------

    def _send(self, text: str) -> None:
        self.port.write(text.encode("ascii"))

    def _read_line(self) -> str:
        """One reply line. Timeout -> DeviceTimeout; 'ERR ...' -> DeviceError."""
        raw = self.port.readline()
        if not raw:
            raise DeviceTimeout("The box did not reply. Is it plugged in and the right port chosen?")
        line = protocol.decode_line(raw)
        protocol.raise_if_error(line)
        return line

    def _set_read_timeout(self, seconds: float):
        """Change how long the port waits for a reply line. Returns the old value (None if the port has no timeout)."""
        old = getattr(self.port, "timeout", None)
        if hasattr(self.port, "timeout"):
            self.port.timeout = seconds
        return old

    def _restore_read_timeout(self, old) -> None:
        if old is not None and hasattr(self.port, "timeout"):
            self.port.timeout = old

    def _flush(self) -> None:
        """Throw away any half-received reply so the next command starts clean."""
        reset = getattr(self.port, "reset_input_buffer", None)
        if callable(reset):
            reset()

    def _require_connection(self) -> None:
        if not self.connected:
            raise DeviceError("Not connected to the box. Call connect() first.")

    # --- connecting ----------------------------------------------------------

    def connect(self) -> None:
        """Handshake: check the protocol version, learn the box's limits, read its UID."""
        self._flush()
        try:
            self._send("INFO?\n")
            info = protocol.parse_info(self._read_line())
            if info["protocol"] != protocol.PROTOCOL_VERSION:
                raise DeviceError(f"The box speaks protocol {info['protocol']} but this program "
                                  f"needs {protocol.PROTOCOL_VERSION}. Update the firmware or the app.")
            self._send("ID?\n")
            words = protocol.parse_id(self._read_line())
        except DeviceError:
            self._flush()
            raise
        self.max_points = min(info["max_points"], MAX_WAVEFORM_POINTS)
        self.max_rate_hz = info["max_rate_hz"]
        self.adc_max = 2 ** info["adc_bits"] - 1
        self.ideal_calibration = Calibration.ideal(info["vref_v"], info["adc_bits"], self.front_end)
        self.ideal_current_calibration = Calibration.ideal(info["vref_v"], info["adc_bits"], CURRENT_SENSE)
        if self.calibration is None:
            self.calibration = self.ideal_calibration
        self.uid = format_uid(*words)
        self.connected = True

    def close(self) -> None:
        self.connected = False
        close = getattr(self.port, "close", None)
        if callable(close):
            close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_exc):
        self.close()

    # --- sampling ------------------------------------------------------------

    def sample_counts(self, channels: list[str], rate_hz: float,
                      num_points: int) -> tuple[list[np.ndarray], float]:
        """
        Sample 1 or 2 channels at the same time and return the RAW COUNTS (whole
        numbers 0..4095, one array per channel) plus the sample rate the box really used.
        Calibration works on counts, so it needs this before they become volts.
        """
        self._require_connection()
        if num_points > self.max_points:
            raise DeviceError(f"{num_points} points is more than the box can store ({self.max_points}).")
        if rate_hz > self.max_rate_hz:
            raise DeviceError(f"{rate_hz} Hz is faster than the box can sample ({self.max_rate_hz} Hz).")
        command = protocol.build_sample_command(channels, rate_hz, num_points)
        try:
            return self._sample_once(command, channels, rate_hz, num_points)
        except DeviceTimeout:
            # The box may have frozen: its watchdog resets it within about 2 seconds. Wait, check it
            # answers PING, then try the capture once more. If it still does not answer, say so.
            if not self._recovered():
                raise DeviceTimeout("The box stopped answering and did not recover. Unplug it, plug it "
                                    "back in, and connect again.") from None
            return self._sample_once(command, channels, rate_hz, num_points)

    def _sample_once(self, command: str, channels: list[str], rate_hz: float,
                     num_points: int) -> tuple[list[np.ndarray], float]:
        """Send one SAMPLE command and read the whole reply, waiting as long as this capture needs."""
        old_timeout = self._set_read_timeout(protocol.sample_timeout_s(num_points, rate_hz, len(channels)))
        try:
            self._send(command)
            data = [protocol.parse_data(self._read_line(), num_points, self.adc_max)
                    for _ in channels]
            actual_rate = protocol.parse_done(self._read_line())
        except DeviceError:
            self._flush()                    # do not let leftovers confuse the next command
            raise
        finally:
            self._restore_read_timeout(old_timeout)
        return [np.asarray(counts, dtype=float) for counts in data], actual_rate

    def _recovered(self) -> bool:
        """After a timeout: wait for the watchdog, clear the line, and see whether the box answers PING."""
        if self.recovery_wait_s > 0:
            time.sleep(self.recovery_wait_s)
        self._flush()
        try:
            self._send("PING\n")
            return self._read_line() == "PONG"
        except DeviceError:
            self._flush()
            return False

    def sample(self, channels: list[str], rate_hz: float,
               num_points: int) -> tuple[list[np.ndarray], float]:
        """
        Like sample_counts, but converted to VOLTS with the current calibration.
        Returns (one array of volts per channel, the sample rate the box really used).
        """
        counts, actual_rate = self.sample_counts(channels, rate_hz, num_points)
        return [self.calibration.counts_to_volts(c) for c in counts], actual_rate

    def read_mean_counts(self, channel: str, num_points: int = 500,
                         rate_hz: float = 20_000) -> tuple[float, float]:
        """
        Average raw count of one channel, and how much it wobbles (standard
        deviation, in counts). Used by the calibration screen.
        """
        (counts,), _rate = self.sample_counts([channel], rate_hz, num_points)
        return float(np.mean(counts)), float(np.std(counts))

    def reset_calibration(self) -> None:
        """Go back to the textbook (ideal) calibration."""
        self._require_connection()
        self.calibration = self.ideal_calibration

    def measure_voltage(self, probe_pos: str, probe_neg: str,
                        num_points: int = 200, rate_hz: float = 20_000) -> float:
        """
        Voltage of probe_pos relative to probe_neg: both are sampled at once and
        their AVERAGES subtracted (averaging 200 samples removes most noise).
        """
        if probe_pos == probe_neg:
            raise DeviceError("The two probe points are the same, so there is nothing to measure.")
        (v_pos, v_neg), _rate = self.sample([probe_pos, probe_neg], rate_hz, num_points)
        return float(np.mean(v_pos) - np.mean(v_neg))

    # --- current ---------------------------------------------------------------------

    def current_calibration(self, channel: str) -> Calibration:
        """The calibration (mA per count) in use for a current input: its own, else the ideal one."""
        return self.current_calibrations.get(channel) or self.ideal_current_calibration

    def current_range_ma(self, channel: str) -> tuple[float, float]:
        """The lowest and highest current (mA) the converter can show before it clips."""
        cal = self.current_calibration(channel)
        low, high = cal.counts_to_volts([0, self.adc_max])        # (the "volts" of a current channel are mA)
        return float(low), float(high)

    def reset_current_calibration(self, channel: str) -> None:
        self.current_calibrations.pop(channel, None)

    def measure_current(self, channel: str, num_points: int = 200, rate_hz: float = 20_000) -> float:
        """
        The current (mA) flowing through a current input, positive one way and negative the other.
        The average of `num_points` samples is used. If any sample hits the end of the converter's
        range the current is too large to measure, and the reading is refused rather than guessed.
        """
        self._require_connection()
        if channel not in CURRENT_CHANNELS:
            raise DeviceError(f"'{channel}' is not a current input (the box has: {', '.join(CURRENT_CHANNELS)}).")
        (counts,), _rate = self.sample_counts([channel], rate_hz, num_points)
        if counts.min() <= 0 or counts.max() >= self.adc_max:
            low, high = self.current_range_ma(channel)
            raise DeviceError(f"The current on {channel} is outside what the box can measure "
                              f"({low:.0f} to {high:.0f} mA). Reduce the current and try again.")
        return float(self.current_calibration(channel).counts_to_volts(np.mean(counts)))

    def capture_waveform(self, spec: WaveformSpec) -> WaveformCapture:
        """Capture the raw samples a waveform item asks for (signal, plus reference if needed)."""
        channels = [spec.channel] + ([spec.channel_ref] if spec.channel_ref else [])
        volts, actual_rate = self.sample(channels, spec.sample_rate_hz, spec.num_points)
        reference = None
        if len(volts) > 1:
            # Two channels on the same converter are read one after the other, so the reference was
            # sampled a moment after the signal. Line it up so phase between them comes out right.
            lag = scan_lag_s(channels[0], channels[1])
            reference = [float(v) for v in align_delayed_channel(volts[1], actual_rate, lag)]
        return WaveformCapture(
            samples=[float(v) for v in volts[0]],
            sample_rate_hz=actual_rate,                       # the REAL rate, not the requested one
            ref_samples=reference)
