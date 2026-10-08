"""
hardware/calibration_store.py

Saves each box's calibration to a small JSON file named after the box's UID, so a
box is calibrated once and remembered, and two boxes never share a calibration.

    <your home folder>/.circuitscope/calibration/0012AB34CD56EF780011AABB.json        (voltage)
    <your home folder>/.circuitscope/calibration/0012AB34CD56EF780011AABB_I1.json     (current input I1)

The UID comes from the box itself, so it is checked to be 24 hex digits before it
is used in a file name (nothing odd can send a file to another folder).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hardware.calibration import DIRECT, Calibration, FrontEnd

_UID_RE = re.compile(r"^[0-9A-Fa-f]{24}$")
_CHANNEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,3}$")


class CalibrationError(Exception):
    """A saved calibration could not be written or read."""


@dataclass
class SavedCalibration:
    calibration: Calibration
    saved_utc: str                                  # when it was saved (ISO text)
    points: list[dict] = field(default_factory=list)  # the two reference readings used (for the record)
    # The analogue front end the box had when this was made. A calibration is only valid
    # for that front end: counts mean different volts if the circuit changes. Files saved
    # before front ends existed have none recorded, so they count as DIRECT (0-3.3 V).
    front_end: FrontEnd = DIRECT


def default_calibration_folder() -> Path:
    """Where calibrations live unless told otherwise: a hidden folder in the user's home."""
    return Path.home() / ".circuitscope" / "calibration"


def _path(uid: str, folder, channel: str | None = None) -> Path:
    """File for a box's voltage calibration, or (with `channel`, e.g. 'I1') for one current input."""
    if not isinstance(uid, str) or not _UID_RE.match(uid):
        raise CalibrationError(f"The box UID must be exactly 24 hexadecimal digits (0-9, A-F), but it is {uid!r}.")
    suffix = ""
    if channel is not None:
        if not isinstance(channel, str) or not _CHANNEL_RE.match(channel):
            raise CalibrationError(f"The channel name {channel!r} cannot be used (letters and digits only, up to 4 characters).")
        suffix = f"_{channel.upper()}"
    return Path(folder if folder is not None else default_calibration_folder()) / f"{uid.upper()}{suffix}.json"


def save_calibration(uid: str, cal: Calibration, folder=None, points: list[dict] | None = None,
                     when: str | None = None, front_end: FrontEnd = DIRECT,
                     channel: str | None = None) -> Path:
    """Write the calibration for box `uid` (for one current input if `channel` is given). Returns the file path."""
    path = _path(uid, folder, channel)
    document = {
        "uid": uid.upper(),
        "channel": channel,
        "saved_utc": when or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calibration": cal.to_dict(),
        "front_end": {"bias_v": front_end.bias_v, "gain": front_end.gain},
        "points": points or [],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)          # create the folder the first time
        path.write_text(json.dumps(document, indent=1), encoding="utf-8")
    except OSError as exc:
        raise CalibrationError(f"Could not save the calibration ({exc}).") from exc
    return path


def load_calibration(uid: str, folder=None, channel: str | None = None) -> SavedCalibration | None:
    """The saved calibration for box `uid` (or one current input), or None if never calibrated."""
    path = _path(uid, folder, channel)
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if str(document["uid"]).upper() != uid.upper():
            raise CalibrationError("The calibration file belongs to a different box.")
        cal = Calibration.from_dict(document["calibration"])
        if not (cal.volts_per_count > 0):
            raise CalibrationError("The saved calibration has an invalid gain.")
        fe = document.get("front_end")
        front_end = DIRECT if fe is None else FrontEnd(float(fe["bias_v"]), float(fe["gain"]))
        return SavedCalibration(cal, str(document.get("saved_utc", "")),
                                list(document.get("points", [])), front_end)
    except CalibrationError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:      # JSONDecodeError is a ValueError
        raise CalibrationError(f"The saved calibration file is damaged ({exc}).") from exc


def delete_calibration(uid: str, folder=None, channel: str | None = None) -> bool:
    """Remove the saved calibration (back to ideal). True if there was one."""
    path = _path(uid, folder, channel)
    if path.exists():
        path.unlink()
        return True
    return False
