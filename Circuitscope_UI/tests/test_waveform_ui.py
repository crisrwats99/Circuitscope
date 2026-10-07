"""Automatic checks for the waveform viewer (ui/waveform_panel.py). Run with:  python -m pytest"""

import csv
import threading
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.submission import WaveformCapture
from hardware.demo import make_demo_device
from ui.main_window import MainWindow
from ui.waveform_panel import FEATURE_NAMES, NO_REFERENCE, WaveformPanel


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


def demo_device():
    device = make_demo_device()          # P4: 1 kHz sine; P5: same sine, half size, 45 degrees late
    device.connect()
    return device


def row_of(panel: WaveformPanel, name: str) -> tuple[str, str, str]:
    """The (feature, value, note) texts of the table row called `name`."""
    for r in range(panel.table.rowCount()):
        if panel.table.item(r, 0).text() == name:
            return tuple(panel.table.item(r, c).text() for c in range(3))
    raise AssertionError(f"no row called {name}")


def plotted_points(curve) -> int:
    """How many points a pyqtgraph curve holds (it returns None when empty)."""
    x, _y = curve.getData()
    return 0 if x is None else len(x)


def capture_and_wait(panel: WaveformPanel) -> None:
    panel.capture_button.click()
    wait_until(lambda: panel.capture is not None and panel.capture_button.isEnabled())


# ---------- starting state ----------

def test_panel_is_disabled_without_a_box(qapp):
    panel = WaveformPanel()
    assert not panel.capture_button.isEnabled() and not panel.save_button.isEnabled()
    assert "Connect to a box" in panel.message_label.text()
    assert panel.table.rowCount() == len(FEATURE_NAMES) == 11
    assert row_of(panel, "Frequency")[1] == "-"


def test_connecting_sets_the_limits_from_the_box(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    assert panel.capture_button.isEnabled() and not panel.save_button.isEnabled()
    assert panel.points_spin.maximum() == 8000 and panel.rate_spin.maximum() == 200_000


# ---------- capturing ----------

def test_capture_plots_the_signal_and_fills_the_feature_table(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())                   # defaults: P4, no reference, 50 kHz, 500 points (10 ms = ten 1 kHz cycles)
    capture_and_wait(panel)
    x, y = panel.curve_signal.getData()
    assert len(x) == len(y) == 500
    assert x[-1] == pytest.approx(9.98, abs=0.05)     # 500 samples at 50 kHz last 10 ms
    assert plotted_points(panel.curve_ref) == 0
    assert float(row_of(panel, "Frequency")[1].split()[0]) == pytest.approx(1000, rel=0.01)
    assert float(row_of(panel, "Peak-to-peak")[1].split()[0]) == pytest.approx(2.0, abs=0.15)
    assert float(row_of(panel, "DC offset")[1].split()[0]) == pytest.approx(1.65, abs=0.02)
    assert "Captured 500 samples at 50,000 Hz" in panel.message_label.text()
    assert panel.save_button.isEnabled()


def test_without_a_reference_gain_and_phase_say_why(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    capture_and_wait(panel)
    for name in ("Gain", "Phase difference"):
        _feature, value, note = row_of(panel, name)
        assert value == "-" and "reference" in note


def test_with_a_reference_gain_and_phase_are_shown_and_both_traces_plotted(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.channel_box.setCurrentText("P5")            # the output: half size, 45 degrees late
    panel.ref_box.setCurrentText("P4")                # the input
    capture_and_wait(panel)
    assert plotted_points(panel.curve_ref) == 500
    assert float(row_of(panel, "Gain")[1]) == pytest.approx(0.5, abs=0.03)
    assert float(row_of(panel, "Phase difference")[1].split()[0]) == pytest.approx(45, abs=4)


def test_a_dc_signal_shows_statistics_but_explains_the_missing_features(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.channel_box.setCurrentText("P1")            # a steady 3.0 V
    capture_and_wait(panel)
    assert float(row_of(panel, "DC offset")[1].split()[0]) == pytest.approx(3.0, abs=0.01)
    _f, value, note = row_of(panel, "Frequency")
    assert value == "-" and "flat" in note


def test_the_real_sample_rate_is_what_is_shown(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.rate_spin.setValue(30_000)                  # the box's timer cannot do exactly this
    panel.points_spin.setValue(3000)
    capture_and_wait(panel)
    assert panel.capture.sample_rate_hz != 30_000
    assert panel.capture.sample_rate_hz == pytest.approx(30_000, rel=0.001)
    assert "Captured 3000 samples at 29,998 Hz" in panel.message_label.text()


def test_reference_equal_to_the_signal_is_refused(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.channel_box.setCurrentText("P4")
    panel.ref_box.setCurrentText("P4")
    panel.capture_button.click()
    assert "different" in panel.message_label.text()
    assert panel.capture is None and panel.capture_button.isEnabled()


# ---------- background behaviour and errors ----------

def test_capture_runs_off_the_main_thread_and_locks_the_buttons(qapp):
    class SlowDevice:
        max_points, max_rate_hz = 8000, 200_000
        thread = None
        def capture_waveform(self, spec):
            SlowDevice.thread = threading.get_ident()
            time.sleep(0.2)
            return WaveformCapture(samples=[0.0, 1.0] * 500, sample_rate_hz=10_000.0)

    panel = WaveformPanel()
    panel.set_device(SlowDevice())
    panel.capture_button.click()
    assert not panel.capture_button.isEnabled()
    assert panel.message_label.text() == "Capturing..."
    wait_until(lambda: panel.capture is not None and panel.capture_button.isEnabled())
    assert SlowDevice.thread != threading.get_ident()


def test_failed_capture_is_shown_in_red_and_the_button_recovers(qapp):
    class BrokenDevice:
        max_points, max_rate_hz = 8000, 200_000
        def capture_waveform(self, spec):
            raise RuntimeError("The box did not reply.")

    panel = WaveformPanel()
    panel.set_device(BrokenDevice())
    panel.capture_button.click()
    wait_until(lambda: "failed" in panel.message_label.text())
    assert "did not reply" in panel.message_label.text() and "b00020" in panel.message_label.styleSheet()
    wait_until(lambda: panel.capture_button.isEnabled())
    assert panel.capture is None


def test_disconnecting_clears_the_plot_and_table(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    capture_and_wait(panel)
    panel.set_device(None)
    assert panel.capture is None and plotted_points(panel.curve_signal) == 0
    assert row_of(panel, "Frequency")[1] == "-"
    assert not panel.capture_button.isEnabled() and not panel.save_button.isEnabled()


# ---------- saving ----------

def test_save_csv_writes_the_capture(qapp, tmp_path):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.points_spin.setValue(100)
    capture_and_wait(panel)
    path = str(tmp_path / "cap.csv")
    panel.save_csv(path)
    rows = list(csv.reader(open(path, encoding="utf-8", newline="")))
    assert rows[0] == ["time_s", "volts"] and len(rows) == 101
    assert "Saved 100 samples" in panel.message_label.text()


def test_saving_to_an_unwritable_place_shows_an_error(qapp, tmp_path):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.points_spin.setValue(100)
    capture_and_wait(panel)
    panel.save_csv(str(tmp_path / "no_such_folder" / "cap.csv"))
    assert "could not save" in panel.message_label.text()


# ---------- inside the whole window ----------

def test_window_has_a_waveform_tab_that_works_end_to_end(qapp, tmp_path):
    from ui.connection_panel import SIMULATED
    window = MainWindow(calibration_folder=tmp_path)
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == \
        ["Quick measure", "Waveform", "Calibration"]
    assert not window.waveform.capture_button.isEnabled()
    window.connection.mode_box.setCurrentText(SIMULATED)
    window.connection.connect_button.click()
    wait_until(lambda: window.connection.device is not None)
    assert window.waveform.capture_button.isEnabled()
    capture_and_wait(window.waveform)
    assert float(row_of(window.waveform, "Frequency")[1].split()[0]) == pytest.approx(1000, rel=0.01)


# ---------- the legend names each trace ----------

def test_legend_names_the_signal_and_the_reference(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    assert panel.legend_labels() == []                       # nothing captured yet
    panel.channel_box.setCurrentText("P5")
    panel.ref_box.setCurrentText("P4")
    capture_and_wait(panel)
    assert panel.legend_labels() == ["P5 (signal)", "P4 (reference)"]


def test_legend_follows_the_latest_capture(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    panel.channel_box.setCurrentText("P5")
    panel.ref_box.setCurrentText("P4")
    capture_and_wait(panel)
    panel.capture = None                                     # so we can wait for the next one
    panel.channel_box.setCurrentText("P4")
    panel.ref_box.setCurrentText(NO_REFERENCE)
    capture_and_wait(panel)
    assert panel.legend_labels() == ["P4 (signal)"]          # no stale 'reference' entry left behind


def test_legend_is_cleared_when_the_box_disconnects(qapp):
    panel = WaveformPanel()
    panel.set_device(demo_device())
    capture_and_wait(panel)
    assert panel.legend_labels() == ["P4 (signal)"]
    panel.set_device(None)
    assert panel.legend_labels() == []
