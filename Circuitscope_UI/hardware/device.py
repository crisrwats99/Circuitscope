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

import numpy as np

from core.models import MAX_WAVEFORM_POINTS, WaveformSpec
from core.submission import WaveformCapture, format_uid
from hardware import protocol
from hardware.calibration import Calibration
from hardware.protocol import DeviceError, DeviceTimeout, ProtocolError

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 3.0          # how long to wait for one reply line


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

    def __init__(self, port, calibration: Calibration | None = None):
        self.port = port
        self.calibration = calibration          # None = use the ideal one once INFO is known
        self.connected = False
        self.uid = ""                           # filled in by connect()
        self.max_points = MAX_WAVEFORM_POINTS   # replaced by the box's real limits in connect()
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
        if self.calibration is None:
            self.calibration = Calibration.ideal(info["vref_v"], info["adc_bits"])
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

    def sample(self, channels: list[str], rate_hz: float,
               num_points: int) -> tuple[list[np.ndarray], float]:
        """
        Sample 1 or 2 channels at the same time.
        Returns (one array of VOLTS per channel, the sample rate the box really used).
        """
        self._require_connection()
        if num_points > self.max_points:
            raise DeviceError(f"{num_points} points is more than the box can store ({self.max_points}).")
        if rate_hz > self.max_rate_hz:
            raise DeviceError(f"{rate_hz} Hz is faster than the box can sample ({self.max_rate_hz} Hz).")
        command = protocol.build_sample_command(channels, rate_hz, num_points)

        try:
            self._send(command)
            data = [protocol.parse_data(self._read_line(), num_points, self.adc_max)
                    for _ in channels]
            actual_rate = protocol.parse_done(self._read_line())
        except DeviceError:
            self._flush()                    # do not let leftovers confuse the next command
            raise
        volts = [self.calibration.counts_to_volts(counts) for counts in data]
        return volts, actual_rate

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

    def capture_waveform(self, spec: WaveformSpec) -> WaveformCapture:
        """Capture the raw samples a waveform item asks for (signal, plus reference if needed)."""
        channels = [spec.channel] + ([spec.channel_ref] if spec.channel_ref else [])
        volts, actual_rate = self.sample(channels, spec.sample_rate_hz, spec.num_points)
        return WaveformCapture(
            samples=[float(v) for v in volts[0]],
            sample_rate_hz=actual_rate,                       # the REAL rate, not the requested one
            ref_samples=[float(v) for v in volts[1]] if len(volts) > 1 else None)