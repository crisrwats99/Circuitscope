"""Automatic checks for calibration: saving per box, sanity checks, and correcting a real-ish converter.
Run with:  python -m pytest
"""

import json

import pytest

from hardware.calibration import Calibration, check_plausible
from hardware.calibration_store import (CalibrationError, delete_calibration, load_calibration,
                                        save_calibration)
from hardware.device import SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort

UID = "0012AB34CD56EF780011AABB"


def connected(board: SimulatedBoard) -> SerialDevice:
    device = SerialDevice(SimulatedPort(board))
    device.connect()
    return device


# ---------- saving and loading per box ----------

def test_calibration_round_trips_through_a_file(tmp_path):
    cal = Calibration(volts_per_count=0.000811, offset_v=0.0123)
    save_calibration(UID, cal, tmp_path, points=[{"volts": 0.0, "counts": 15.0}], when="2026-10-07T10:00:00+00:00")
    saved = load_calibration(UID, tmp_path)
    assert saved.calibration.volts_per_count == cal.volts_per_count
    assert saved.calibration.offset_v == cal.offset_v
    assert saved.saved_utc == "2026-10-07T10:00:00+00:00"
    assert saved.points == [{"volts": 0.0, "counts": 15.0}]
    assert (tmp_path / f"{UID}.json").exists()                 # file is named after the box


def test_box_that_was_never_calibrated_gives_none(tmp_path):
    assert load_calibration(UID, tmp_path) is None


def test_each_box_has_its_own_file(tmp_path):
    other = "FFFFFFFFFFFFFFFFFFFFFFFF"
    save_calibration(UID, Calibration(0.001, 0.0), tmp_path)
    save_calibration(other, Calibration(0.002, 0.1), tmp_path)
    assert load_calibration(UID, tmp_path).calibration.volts_per_count == 0.001
    assert load_calibration(other, tmp_path).calibration.volts_per_count == 0.002


def test_lowercase_uid_finds_the_same_file(tmp_path):
    save_calibration(UID.lower(), Calibration(0.001, 0.0), tmp_path)
    assert load_calibration(UID, tmp_path) is not None


@pytest.mark.parametrize("bad", ["", "../../evil", "0012AB34", UID + "0", "G" * 24, "..\\x" * 8, None])
def test_unsafe_or_malformed_uids_are_refused(bad, tmp_path):
    with pytest.raises(CalibrationError):
        save_calibration(bad, Calibration(0.001, 0.0), tmp_path)
    with pytest.raises(CalibrationError):
        load_calibration(bad, tmp_path)


def test_damaged_file_is_reported_not_crashed_on(tmp_path):
    (tmp_path / f"{UID}.json").write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(CalibrationError, match="damaged"):
        load_calibration(UID, tmp_path)


def test_file_for_a_different_box_is_refused(tmp_path):
    save_calibration(UID, Calibration(0.001, 0.0), tmp_path)
    path = tmp_path / f"{UID}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["uid"] = "FFFFFFFFFFFFFFFFFFFFFFFF"               # someone copied another box's file here
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CalibrationError, match="different box"):
        load_calibration(UID, tmp_path)


def test_file_with_nonsense_gain_is_refused(tmp_path):
    save_calibration(UID, Calibration(0.001, 0.0), tmp_path)
    path = tmp_path / f"{UID}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["calibration"]["volts_per_count"] = -1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CalibrationError):
        load_calibration(UID, tmp_path)


def test_delete_calibration(tmp_path):
    save_calibration(UID, Calibration(0.001, 0.0), tmp_path)
    assert delete_calibration(UID, tmp_path) is True
    assert load_calibration(UID, tmp_path) is None
    assert delete_calibration(UID, tmp_path) is False          # nothing left to delete


# ---------- sanity check ----------

def test_a_realistic_calibration_is_plausible():
    ideal = Calibration.ideal()
    assert check_plausible(Calibration(ideal.volts_per_count * 1.03, 0.04), ideal) is None
    assert check_plausible(ideal, ideal) is None


def test_implausible_calibrations_are_rejected_with_a_reason():
    ideal = Calibration.ideal()
    assert "gain" in check_plausible(Calibration(ideal.volts_per_count * 0.5, 0.0), ideal)
    assert "gain" in check_plausible(Calibration(ideal.volts_per_count * 1.5, 0.0), ideal)
    assert "offset" in check_plausible(Calibration(ideal.volts_per_count, 0.5), ideal)


# ---------- the driver's counts and reset ----------

def test_read_mean_counts_of_a_steady_voltage():
    board = SimulatedBoard()
    board.set_dc("P1", 1.65)                                   # half of 3.3 V = about 2048 counts
    mean, spread = connected(board).read_mean_counts("P1")
    assert mean == pytest.approx(2047.5, abs=1.0)
    assert spread == pytest.approx(0.0, abs=0.01)              # no noise, so no wobble


def test_read_mean_counts_reports_the_noise():
    board = SimulatedBoard(noise_v=0.01)                       # 10 mV is about 12 counts
    board.set_dc("P1", 1.65)
    _mean, spread = connected(board).read_mean_counts("P1", num_points=2000)
    assert 8 < spread < 17


def test_sample_counts_returns_whole_counts_within_range():
    board = SimulatedBoard()
    board.set_dc("P1", 5.0)                                    # clips at full scale
    (counts,), _rate = connected(board).sample_counts(["P1"], 1000, 10)
    assert set(counts) == {4095.0}


def test_reset_calibration_returns_to_ideal():
    device = connected(SimulatedBoard())
    device.calibration = Calibration(0.002, 0.3)
    device.reset_calibration()
    assert device.calibration == device.ideal_calibration


# ---------- calibrating a converter that is NOT ideal ----------

def test_two_point_calibration_corrects_gain_and_offset_errors():
    # This box's converter reads 3 % too high and adds 50 mV.
    board = SimulatedBoard(adc_gain=1.03, adc_offset_v=0.05)
    board.set_dc("P3", 0.0)               # a true 0 V reference
    board.set_dc("P1", 3.0)               # a true 3.000 V reference
    board.set_dc("P2", 1.5)               # an unknown voltage we will check afterwards
    device = connected(board)

    (before,), _ = device.sample(["P2"], 1000, 200)
    assert before.mean() == pytest.approx(1.5 * 1.03 + 0.05, abs=0.005)       # wrong: 1.595 V

    low, _ = device.read_mean_counts("P3")
    high, _ = device.read_mean_counts("P1")
    cal = Calibration.fit_two_point(low, 0.0, high, 3.0)
    assert check_plausible(cal, device.ideal_calibration) is None
    device.calibration = cal

    (after,), _ = device.sample(["P2"], 1000, 200)
    assert after.mean() == pytest.approx(1.5, abs=0.005)                       # corrected