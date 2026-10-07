"""Automatic checks for the calibration panel (ui/calibration_panel.py). Run with:  python -m pytest"""

import threading
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from hardware.calibration import CIRCUITSCOPE, Calibration
from hardware.calibration_store import load_calibration, save_calibration
from hardware.device import SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort
from ui.calibration_panel import CalibrationPanel
from ui.main_window import MainWindow

UID = "0012AB34CD56EF780011AABB"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(condition, timeout_s: float = 5.0) -> None:
    end = time.time() + timeout_s
    while time.time() < end:
        QCoreApplication.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for the interface to update.")


def make_device(**board_options) -> SerialDevice:
    """A connected simulated box: P1 = true 3.000 V, P3 = true 0 V, P2 = 1.5 V."""
    board = SimulatedBoard(**board_options)
    board.set_dc("P1", 3.0)
    board.set_dc("P2", 1.5)
    board.set_dc("P3", 0.0)
    device = SerialDevice(SimulatedPort(board))
    device.connect()
    return device


def read_both(panel: CalibrationPanel) -> None:
    """Press 'Read low' (P3, 0 V) and 'Read high' (P1, 3 V) and wait for each."""
    panel.low_button.click()
    wait_until(lambda: panel._low is not None and panel.low_button.isEnabled())
    panel.high_button.click()
    wait_until(lambda: panel._high is not None and panel.high_button.isEnabled())


# ---------- starting state ----------

def test_everything_is_disabled_without_a_box(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    for button in (panel.low_button, panel.high_button, panel.apply_button, panel.reset_button):
        assert not button.isEnabled()
    assert "Connect to a box" in panel.status_label.text()


def test_new_box_starts_with_ideal_values(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    device = make_device()
    panel.set_device(device)
    assert "Not calibrated" in panel.status_label.text()
    assert panel.low_button.isEnabled() and panel.high_button.isEnabled()
    assert not panel.apply_button.isEnabled()            # needs both readings first
    assert device.calibration == device.ideal_calibration


# ---------- the full procedure ----------

def test_full_calibration_corrects_a_box_with_gain_and_offset_error(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    device = make_device(adc_gain=1.03, adc_offset_v=0.05)
    panel.set_device(device)
    read_both(panel)
    assert "counts" in panel.low_result.text() and "counts" in panel.high_result.text()
    assert panel.apply_button.isEnabled()
    panel.apply_button.click()
    assert panel.status_label.text().startswith("Calibrated")
    (volts,), _ = device.sample(["P2"], 1000, 200)       # the 1.5 V point, never used for calibrating
    assert volts.mean() == pytest.approx(1.5, abs=0.005)
    saved = load_calibration(UID, tmp_path)               # and it was saved under this box's UID
    assert saved.calibration == device.calibration
    assert len(saved.points) == 2


def test_saved_calibration_is_loaded_when_the_same_box_reconnects(qapp, tmp_path):
    first = CalibrationPanel(folder=tmp_path)
    first.set_device(make_device(adc_gain=1.03, adc_offset_v=0.05))
    read_both(first)
    first.apply_button.click()
    calibrated = first.device.calibration

    second = CalibrationPanel(folder=tmp_path)            # a new session: new panel, new connection
    device = make_device(adc_gain=1.03, adc_offset_v=0.05)
    second.set_device(device)
    assert "Loaded" in second.status_label.text()
    assert device.calibration == calibrated


def test_reset_removes_the_saved_calibration(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    device = make_device(adc_gain=1.03, adc_offset_v=0.05)
    panel.set_device(device)
    read_both(panel)
    panel.apply_button.click()
    panel.reset_button.click()
    assert device.calibration == device.ideal_calibration
    assert load_calibration(UID, tmp_path) is None
    assert "Reset" in panel.status_label.text()
    assert not panel.apply_button.isEnabled()            # readings were cleared too


# ---------- refusing bad calibrations ----------

def test_references_too_close_together_are_refused(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    panel.set_device(make_device())
    read_both(panel)
    panel.high_volts.setValue(0.5)                        # but the readings were taken at 0 V and 3 V ...
    panel._high = (0.5, panel._high[1])                   # ... pretend the user typed 0.5 V for the high one
    panel.apply_button.click()
    assert "at least" in panel.status_label.text() and "b00020" in panel.status_label.styleSheet()
    assert load_calibration(UID, tmp_path) is None


def test_implausible_references_are_refused_and_not_saved(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    device = make_device()
    panel.set_device(device)
    panel.high_volts.setValue(1.5)                        # claims the 3 V point is 1.5 V: wrong gain
    read_both(panel)
    panel.apply_button.click()
    assert "away from ideal" in panel.status_label.text()
    assert device.calibration == device.ideal_calibration
    assert load_calibration(UID, tmp_path) is None


def test_reading_the_same_voltage_twice_is_refused(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    panel.set_device(make_device())
    panel.low_channel.setCurrentText("P1")                # both readings are of the 3 V point
    read_both(panel)
    panel.high_volts.setValue(3.0)
    panel.low_volts.setValue(0.0)
    panel.apply_button.click()                            # identical counts for a 3 V span
    assert "b00020" in panel.status_label.styleSheet()
    assert load_calibration(UID, tmp_path) is None


def test_damaged_saved_file_is_reported_and_ideal_values_are_used(qapp, tmp_path):
    (tmp_path / f"{UID}.json").write_text("garbage", encoding="utf-8")
    panel = CalibrationPanel(folder=tmp_path)
    device = make_device()
    panel.set_device(device)
    assert "damaged" in panel.status_label.text() and "b00020" in panel.status_label.styleSheet()
    assert device.calibration == device.ideal_calibration


# ---------- background behaviour ----------

def test_reading_runs_off_the_main_thread_and_locks_the_buttons(qapp, tmp_path):
    class SlowDevice:
        uid = UID
        calibration = None
        ideal_calibration = Calibration.ideal()
        thread = None
        def read_mean_counts(self, channel):
            SlowDevice.thread = threading.get_ident()
            time.sleep(0.2)
            return 100.0, 0.5

    panel = CalibrationPanel(folder=tmp_path)
    panel.set_device(SlowDevice())
    panel.low_button.click()
    assert not panel.low_button.isEnabled() and not panel.high_button.isEnabled()
    wait_until(lambda: panel._low is not None)
    assert SlowDevice.thread != threading.get_ident()
    wait_until(lambda: panel.low_button.isEnabled())


def test_a_failed_reading_is_shown_in_red_and_the_buttons_recover(qapp, tmp_path):
    class BrokenDevice:
        uid = UID
        calibration = None
        ideal_calibration = Calibration.ideal()
        def read_mean_counts(self, channel):
            raise RuntimeError("The box did not reply.")

    panel = CalibrationPanel(folder=tmp_path)
    panel.set_device(BrokenDevice())
    panel.low_button.click()
    wait_until(lambda: "failed" in panel.status_label.text())
    assert "did not reply" in panel.status_label.text()
    wait_until(lambda: panel.low_button.isEnabled())
    assert panel._low is None


def test_disconnecting_clears_the_panel(qapp, tmp_path):
    panel = CalibrationPanel(folder=tmp_path)
    panel.set_device(make_device())
    read_both(panel)
    panel.set_device(None)
    assert panel._low is None and panel._high is None
    assert not panel.low_button.isEnabled()
    assert panel.low_result.text() == "not read yet"


# ---------- inside the whole window ----------

def test_window_loads_the_saved_calibration_when_the_box_connects(qapp, tmp_path):
    from ui.connection_panel import SIMULATED
    saved = Calibration(volts_per_count=0.00081, offset_v=0.01)
    save_calibration(UID, saved, tmp_path, front_end=CIRCUITSCOPE)   # the demo box has this UID and front end
    window = MainWindow(calibration_folder=tmp_path)
    window.connection.mode_box.setCurrentText(SIMULATED)
    window.connection.connect_button.click()
    wait_until(lambda: window.connection.device is not None)
    assert window.connection.device.calibration == saved
    assert "Loaded" in window.calibration.status_label.text()
    assert window.tabs.count() == 5


def test_a_calibration_from_before_the_front_end_existed_is_ignored(qapp, tmp_path):
    # Saved with no front end recorded (the old 0-3.3 V scale), then the box connects
    # WITH the +/-15 V front end: using it would read 1.2 V as 0.108 V.
    from ui.connection_panel import SIMULATED
    save_calibration(UID, Calibration(volts_per_count=0.000806, offset_v=0.0), tmp_path)
    window = MainWindow(calibration_folder=tmp_path)
    window.connection.mode_box.setCurrentText(SIMULATED)
    window.connection.connect_button.click()
    wait_until(lambda: window.connection.device is not None)
    device = window.connection.device
    assert device.calibration == device.ideal_calibration           # ideal values, not the stale ones
    assert "different front end" in window.calibration.status_label.text()
    assert device.measure_voltage("P2", "P3") == pytest.approx(1.2, abs=0.05)


def test_a_new_calibration_remembers_the_front_end_it_was_made_for(qapp, tmp_path):
    device = make_device()
    panel = CalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    read_both(panel)
    panel.apply_button.click()
    assert load_calibration(UID, tmp_path).front_end == device.front_end
