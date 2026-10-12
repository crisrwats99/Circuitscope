"""
hardware/protocol.py

The "language" the PC and the STM32 box speak over the USB serial cable.
Everything here is plain text, one command per line, so it can be typed into
a serial terminal to debug the firmware by hand.

The box ONLY SAMPLES. It returns RAW ADC COUNTS (whole numbers 0..4095 for a
12-bit converter). Turning counts into volts and everything else happens on
the PC (see calibration.py and core/waveform.py).

Facts about the real firmware (NUCLEO-G431KB, see the firmware interface reference):
  * USB virtual COM port, 115200 baud 8N1. The PC may end a command with LF, CR or CR+LF;
    the box ends EVERY reply line with CR+LF.
  * One command at a time: send the next only after DONE (or ERR) for the previous SAMPLE.
  * Up to 4000 samples per channel and 1..100000 samples per second. Both are reported by INFO?.
  * A SAMPLE reply only starts after the capture is finished, so a long capture needs a long
    timeout (see sample_timeout_s below).
  * Error codes: BADCMD, BADARGS, BADCH, BADRATE, BADCOUNT and HWFAIL (the capture failed in hardware).

  PC sends                      box replies
  ---------------------------   --------------------------------------------
  PING                          PONG
  INFO?                         INFO <protocol> <max_points> <max_rate_hz> <adc_bits> <vref_mv>
  ID?                           ID <word0> <word1> <word2>        (STM32 UID, 3 x 8 hex digits)
  SAMPLE <ch>[,<ch2>] <rate> <n>
                                DATA <n> <c0>,<c1>,...            (one line per channel, in order)
                                DONE <actual_rate_hz>             (the rate the timer really achieved)

  Any command can instead be answered by:   ERR <CODE> <message>

Example:   SAMPLE P1,P2 10000 2000
           DATA 2000 2048,2051,2060,...
           DATA 2000 11,10,12,...
           DONE 10000.0
"""

from __future__ import annotations

import re

PROTOCOL_VERSION = 1          # bump when the commands change; the PC refuses a box with a different number
MAX_CHANNELS_PER_SAMPLE = 2   # one signal, or a signal plus its reference

# How many bytes per second the serial link carries: 115200 baud, 10 bits per byte (start + 8 + stop).
SERIAL_BYTES_PER_SECOND = 11_520

# A channel name: a letter, then up to 7 letters/digits/underscores ("P1", "P3", "CH_A").
_CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,7}$")


class DeviceError(Exception):
    """Anything that goes wrong talking to the box."""


class DeviceTimeout(DeviceError):
    """The box did not answer in time (unplugged, hung, wrong port...)."""


class ProtocolError(DeviceError):
    """The box answered, but not in the format we expect."""


# ---------------------------------------------------------------------------
# Building commands (PC -> box)
# ---------------------------------------------------------------------------

def check_channel(name: str) -> str:
    """Return the channel name if it is well-formed, otherwise raise."""
    if not isinstance(name, str) or not _CHANNEL_RE.match(name):
        raise DeviceError(f"'{name}' is not a valid channel name (e.g. P1).")
    return name


def build_sample_command(channels: list[str], rate_hz: float, num_points: int) -> str:
    """Make the SAMPLE command text (including the final newline)."""
    if not 1 <= len(channels) <= MAX_CHANNELS_PER_SAMPLE:
        raise DeviceError(f"Sample 1 or {MAX_CHANNELS_PER_SAMPLE} channels at a time.")
    names = ",".join(check_channel(c) for c in channels)
    rate = int(round(rate_hz))                     # the box takes a whole number of Hz
    if rate <= 0 or num_points <= 0 or int(num_points) != num_points:
        raise DeviceError("Sample rate and number of points must be positive whole numbers.")
    return f"SAMPLE {names} {rate} {int(num_points)}\n"


def sample_timeout_s(num_points: int, rate_hz: float, num_channels: int) -> float:
    """
    How long to wait for the reply to a SAMPLE command, in seconds. The box captures first
    (n / rate seconds) and only then sends the data (about 5 characters per sample), so a
    fixed 3 seconds is far too short for a long capture. One extra second covers the rest.
    """
    capture = num_points / rate_hz
    transfer = (5 * num_points * num_channels + 40) / SERIAL_BYTES_PER_SECOND
    return capture + transfer + 1.0


# ---------------------------------------------------------------------------
# Reading replies (box -> PC)
# ---------------------------------------------------------------------------

def decode_line(raw: bytes) -> str:
    """Bytes from the serial port -> clean text (no newline, no spaces at the ends)."""
    return raw.decode("ascii", errors="replace").strip()


def raise_if_error(line: str) -> None:
    """If the box replied 'ERR CODE message', raise DeviceError with the details."""
    if line.startswith("ERR"):
        parts = line.split(" ", 2)
        code = parts[1] if len(parts) > 1 else "UNKNOWN"
        message = parts[2] if len(parts) > 2 else ""
        raise DeviceError(f"The box reported an error {code}: {message}".strip())


def parse_info(line: str) -> dict:
    """INFO <protocol> <max_points> <max_rate_hz> <adc_bits> <vref_mv>"""
    parts = line.split()
    if len(parts) != 6 or parts[0] != "INFO":
        raise ProtocolError(f"Unexpected reply to INFO?: '{line}'")
    try:
        protocol, max_points, max_rate, adc_bits, vref_mv = (int(p) for p in parts[1:])
    except ValueError as exc:
        raise ProtocolError(f"INFO reply has a non-numeric field: '{line}'") from exc
    return {"protocol": protocol, "max_points": max_points, "max_rate_hz": max_rate,
            "adc_bits": adc_bits, "vref_v": vref_mv / 1000.0}


def parse_id(line: str) -> tuple[int, int, int]:
    """ID <w0> <w1> <w2>  (each 8 hex digits) -> three whole numbers."""
    parts = line.split()
    if len(parts) != 4 or parts[0] != "ID" or any(len(p) != 8 for p in parts[1:]):
        raise ProtocolError(f"Unexpected reply to ID?: '{line}'")
    try:
        w0, w1, w2 = (int(p, 16) for p in parts[1:])
    except ValueError as exc:
        raise ProtocolError(f"ID reply is not hexadecimal: '{line}'") from exc
    return w0, w1, w2


def parse_data(line: str, expected_points: int, adc_max: int) -> list[int]:
    """DATA <n> c0,c1,...  -> list of counts. Checks the count and the range."""
    head = line.split(" ", 2)
    if len(head) != 3 or head[0] != "DATA":
        raise ProtocolError(f"Expected a DATA line, got '{line[:40]}'")
    try:
        declared = int(head[1])
        counts = [int(c) for c in head[2].split(",")]
    except ValueError as exc:
        raise ProtocolError("A DATA line contains something that is not a whole number.") from exc
    if declared != expected_points or len(counts) != expected_points:
        raise ProtocolError(f"Expected {expected_points} samples but received {len(counts)} "
                            f"(box said {declared}).")
    if min(counts) < 0 or max(counts) > adc_max:
        raise ProtocolError(f"A sample is outside the converter range 0..{adc_max}.")
    return counts


def parse_done(line: str) -> float:
    """DONE <actual_rate_hz> -> the sample rate the box really used."""
    parts = line.split()
    if len(parts) != 2 or parts[0] != "DONE":
        raise ProtocolError(f"Expected DONE, got '{line[:40]}'")
    try:
        rate = float(parts[1])
    except ValueError as exc:
        raise ProtocolError(f"DONE has a bad sample rate: '{line}'") from exc
    if not rate > 0:
        raise ProtocolError("The box reported a sample rate that is not positive.")
    return rate
