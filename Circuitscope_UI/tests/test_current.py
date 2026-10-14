"""Automatic checks for current measurement (device, calibration, storage, channel lists). Run with:  python -m pytest"""

import pytest

from core import channels
from hardware.calibration import CURRENT_SENSE, Calibration, check_plausible
from hardware.calibration_store import (CalibrationError, delete_calibration, load_calibration,
                                        save_calibration)
from hardware.device import DeviceError, SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort

UID = "0012AB34CD56EF780011AABB"


def make_device(**board_options) -> SerialDevice:
    board = SimulatedBoard(**board_options)
    device = SerialDevice(SimulatedPort(board))
    device.connect()
    device.board = board
    return device


# ---------- channel lists ----------

def test_channel_lists_match_the_hardware():
    assert channels.VOLTAGE_CHANNELS == ["P1", "P2", "P3", "P4", "P5"]
    assert channels.CURRENT_CHANNELS == ["I1", "I2"]
    assert not set(channels.VOLTAGE_CHANNELS) & set(channels.CURRENT_CHANNELS)


# ---------- measuring ----------

@pytest.mark.parametrize("ma", [0.0, 4.7, 20.0, 30.0, -10.0, -30.0])
def test_measure_current_reads_positive_and_negative_currents(ma):
    device = make_device()
    device.board.set_dc("I1", ma)
    assert device.measure_current("I1") == pytest.approx(ma, abs=0.05)


def test_the_current_range_is_about_minus_37_to_plus_33_ma():
    low, high = make_device().current_range_ma("I1")
    assert low == pytest.approx(-37.2, abs=0.5)
    assert high == pytest.approx(33.0, abs=0.5)


def test_a_current_beyond_the_range_is_refused_not_guessed():
    device = make_device()
    device.board.set_dc("I1", 60.0)
    with pytest.raises(DeviceError, match="outside what the box can measure"):
        device.measure_current("I1")
    device.board.set_dc("I1", -60.0)
    with pytest.raises(DeviceError, match="outside"):
        device.measure_current("I1")


@pytest.mark.parametrize("channel", ["P1", "I9", "", "i1x"])
def test_only_current_inputs_can_be_measured_as_current(channel):
    with pytest.raises(DeviceError, match="not a current input"):
        make_device().measure_current(channel)


# ---------- calibration ----------

def two_point(device, low_ma=0.0, high_ma=20.0):
    device.board.set_dc("I1", low_ma)
    c_low, _ = device.read_mean_counts("I1")
    device.board.set_dc("I1", high_ma)
    c_high, _ = device.read_mean_counts("I1")
    return Calibration.fit_two_point(c_low, low_ma, c_high, high_ma, unit="mA")


def test_calibration_corrects_a_box_with_gain_and_offset_errors():
    device = make_device(adc_gain=1.07, adc_offset_v=0.02)        # a box ~7 % high, as on the bench
    device.board.set_dc("I1", 10.0)
    assert abs(device.measure_current("I1") - 10.0) > 0.4        # uncalibrated: visibly wrong
    device.current_calibrations["I1"] = two_point(device)
    device.board.set_dc("I1", 10.0)                                # (two_point left the board at 20 mA)
    assert device.measure_current("I1") == pytest.approx(10.0, abs=0.05)
    device.board.set_dc("I1", -15.0)
    assert device.measure_current("I1") == pytest.approx(-15.0, abs=0.1)


def test_reset_goes_back_to_the_ideal_calibration():
    device = make_device(adc_gain=1.05)
    device.current_calibrations["I1"] = two_point(device)
    device.reset_current_calibration("I1")
    assert device.current_calibration("I1") is device.ideal_current_calibration
    device.reset_current_calibration("I1")                         # resetting twice is harmless


def test_two_point_fit_rejects_equal_or_swapped_readings():
    with pytest.raises(ValueError, match="mA"):
        Calibration.fit_two_point(500, 0.0, 400, 5.0, unit="mA")
    with pytest.raises(ValueError):
        Calibration.fit_two_point(500, 0.0, 500, 5.0, unit="mA")


def test_plausibility_check_uses_ma_and_catches_a_disconnected_reference():
    device = make_device()
    ideal = device.ideal_current_calibration
    assert check_plausible(ideal, ideal, max_offset_v=0.3 / CURRENT_SENSE.gain, unit="mA") is None
    wrong = Calibration(volts_per_count=ideal.volts_per_count * 1.5, offset_v=ideal.offset_v)
    assert "away from ideal" in check_plausible(wrong, ideal, max_offset_v=0.3 / CURRENT_SENSE.gain, unit="mA")
    shifted = Calibration(volts_per_count=ideal.volts_per_count, offset_v=ideal.offset_v + 20.0)
    message = check_plausible(shifted, ideal, max_offset_v=0.3 / CURRENT_SENSE.gain, unit="mA")
    assert message and "mA" in message


# ---------- storage ----------

def test_current_calibration_is_saved_per_channel_and_apart_from_the_voltage_one(tmp_path):
    current = Calibration(volts_per_count=0.017, offset_v=-37.0)
    volts = Calibration(volts_per_count=0.0008, offset_v=-0.1)
    save_calibration(UID, current, tmp_path, front_end=CURRENT_SENSE, channel="I1")
    save_calibration(UID, volts, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{UID}.json", f"{UID}_I1.json"]
    loaded = load_calibration(UID, tmp_path, "I1")
    assert loaded.calibration == current and loaded.front_end == CURRENT_SENSE
    assert load_calibration(UID, tmp_path).calibration == volts
    assert load_calibration(UID, tmp_path, "I2") is None


def test_delete_only_removes_the_chosen_channel(tmp_path):
    cal = Calibration(volts_per_count=0.017, offset_v=-37.0)
    save_calibration(UID, cal, tmp_path, front_end=CURRENT_SENSE, channel="I1")
    save_calibration(UID, cal, tmp_path)
    assert delete_calibration(UID, tmp_path, "I1") is True
    assert delete_calibration(UID, tmp_path, "I1") is False
    assert load_calibration(UID, tmp_path) is not None


@pytest.mark.parametrize("channel", ["../x", "I1/..", "a b", "TOOLONGNAME", ""])
def test_unsafe_channel_names_are_refused(tmp_path, channel):
    with pytest.raises(CalibrationError):
        save_calibration(UID, Calibration(0.017, -37.0), tmp_path, channel=channel)
    assert list(tmp_path.iterdir()) == []


def test_a_damaged_current_file_gives_a_clear_error(tmp_path):
    (tmp_path / f"{UID}_I1.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CalibrationError, match="damaged"):
        load_calibration(UID, tmp_path, "I1")


# ---------- the second current input (PA7) ----------

def test_the_two_current_inputs_read_independently():
    device = make_device()
    device.board.set_dc("I1", 4.7)
    device.board.set_dc("I2", -12.0)
    assert device.measure_current("I1") == pytest.approx(4.7, abs=0.05)
    assert device.measure_current("I2") == pytest.approx(-12.0, abs=0.05)


def test_an_input_that_does_not_exist_is_refused():
    device = make_device()
    with pytest.raises(Exception, match="P8"):
        device.measure_voltage("P8", "P3")


def test_each_current_input_has_its_own_calibration():
    device = make_device(adc_gain=1.07)
    device.board.set_dc("I2", 0.0)
    c_low, _ = device.read_mean_counts("I2")
    device.board.set_dc("I2", 20.0)
    c_high, _ = device.read_mean_counts("I2")
    device.current_calibrations["I2"] = Calibration.fit_two_point(c_low, 0.0, c_high, 20.0, unit="mA")
    device.board.set_dc("I1", 10.0)
    device.board.set_dc("I2", 10.0)
    assert device.measure_current("I2") == pytest.approx(10.0, abs=0.05)         # calibrated
    assert abs(device.measure_current("I1") - 10.0) > 0.4                        # I1 is not


def test_both_current_calibrations_are_saved_apart(tmp_path):
    cal1, cal2 = Calibration(0.017, -37.0), Calibration(0.0172, -37.3)
    save_calibration(UID, cal1, tmp_path, front_end=CURRENT_SENSE, channel="I1")
    save_calibration(UID, cal2, tmp_path, front_end=CURRENT_SENSE, channel="I2")
    assert load_calibration(UID, tmp_path, "I1").calibration == cal1
    assert load_calibration(UID, tmp_path, "I2").calibration == cal2
