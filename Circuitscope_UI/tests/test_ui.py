"""Automatic checks for the user interface (ui/ package). Run with:  python -m pytest

Qt draws into memory (see conftest.py), so no windows appear. The tests press
buttons through code and wait for the background thread to finish.
"""

import threading
import time

import pytest

pytest.importorskip("PySide6")        # skip these tests (not fail) if PySide6 is not installed

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from ui.background import run_in_background
from ui.connection_panel import ConnectionPanel, SIMULATED, REAL
from ui.main_window import MainWindow
from ui.quick_measure import QuickMeasurePanel


@pytest.fixture(scope="session")
def qapp():
    """One Qt application for the whole test run (Qt allows only one)."""
    return QApplication.instance() or QApplication([])


def wait_until(condition, timeout_s: float = 5.0) -> None:
    """Let Qt process events (deliver background results) until `condition()` is true."""
    end = time.time() + timeout_s
    while time.time() < end:
        QCoreApplication.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for the interface to update.")


def connect_demo(panel: ConnectionPanel) -> None:
    panel.mode_box.setCurrentText(SIMULATED)
    panel.connect_button.click()
    wait_until(lambda: panel.device is not None)


# ---------- background tasks ----------

def test_task_runs_off_the_main_thread_and_reports_back_on_it(qapp):
    seen = {}
    main_id = threading.get_ident()

    def work(x):
        seen["worker"] = threading.get_ident()
        return x * 2

    run_in_background(work, 21, on_result=lambda v: seen.update(value=v, callback=threading.get_ident()),
                      on_finished=lambda: seen.update(done=True))
    wait_until(lambda: seen.get("done"))
    assert seen["value"] == 42
    assert seen["worker"] != main_id          # the work ran on another thread (window not frozen)
    assert seen["callback"] == main_id        # but the result came back on the window's thread


def test_errors_are_reported_as_text_and_finished_is_always_called(qapp):
    seen = {}

    def boom():
        raise ValueError("bad thing")

    run_in_background(boom, on_result=lambda v: seen.update(result=v),
                      on_error=lambda m: seen.update(error=m),
                      on_finished=lambda: seen.update(done=True))
    wait_until(lambda: seen.get("done"))
    assert seen["error"] == "bad thing" and "result" not in seen


def test_a_slow_task_does_not_block_the_main_thread(qapp):
    seen = {}
    run_in_background(lambda: time.sleep(0.3), on_finished=lambda: seen.update(done=True))
    started = time.time()
    QCoreApplication.processEvents()          # the main thread is free immediately
    assert time.time() - started < 0.1 and not seen
    wait_until(lambda: seen.get("done"))


# ---------- connection panel ----------

def test_panel_starts_disconnected(qapp):
    panel = ConnectionPanel()
    assert panel.device is None
    assert panel.connect_button.text() == "Connect"
    assert panel.uid_label.text() == "UID: -"


def test_connecting_to_the_simulated_box(qapp):
    panel = ConnectionPanel()
    received = []
    panel.device_changed.connect(received.append)
    connect_demo(panel)
    assert panel.device.connected
    assert panel.uid_label.text() == "UID: 0012AB34CD56EF780011AABB"
    assert "4000 samples" in panel.limits_label.text()
    assert panel.status_label.text() == "Connected"
    assert panel.connect_button.text() == "Disconnect" and panel.connect_button.isEnabled()
    assert not panel.mode_box.isEnabled()                 # cannot change the box while connected
    assert received == [panel.device]                     # other panels were told


def test_disconnecting(qapp):
    panel = ConnectionPanel()
    received = []
    connect_demo(panel)
    port = panel.device.port
    panel.device_changed.connect(received.append)
    panel.connect_button.click()                          # now acts as "Disconnect"
    assert panel.device is None and port.closed
    assert panel.connect_button.text() == "Connect"
    assert panel.uid_label.text() == "UID: -"
    assert panel.mode_box.isEnabled()
    assert received == [None]


def test_port_list_is_only_used_for_a_real_box(qapp):
    panel = ConnectionPanel()
    panel.mode_box.setCurrentText(SIMULATED)
    assert not panel.port_box.isEnabled()
    panel.mode_box.setCurrentText(REAL)
    assert panel.port_box.isEnabled()


def test_real_box_without_a_port_shows_a_clear_error(qapp):
    panel = ConnectionPanel()
    panel.mode_box.setCurrentText(REAL)
    panel.port_box.clear()                                # no ports found
    panel.connect_button.click()
    wait_until(lambda: panel.status_label.text().startswith("Could not connect"))
    assert "Choose a serial port" in panel.status_label.text()
    assert panel.device is None and panel.connect_button.isEnabled()


def test_real_box_on_a_missing_port_fails_cleanly(qapp):
    panel = ConnectionPanel()
    panel.mode_box.setCurrentText(REAL)
    panel.port_box.addItem("COM_DOES_NOT_EXIST")
    panel.port_box.setCurrentText("COM_DOES_NOT_EXIST")
    panel.connect_button.click()
    wait_until(lambda: panel.status_label.text().startswith("Could not connect"))
    assert panel.device is None and panel.connect_button.isEnabled()


# ---------- quick measurement ----------

def test_measure_button_is_disabled_until_connected(qapp):
    quick = QuickMeasurePanel()
    assert not quick.measure_button.isEnabled()


def test_measuring_a_voltage_from_the_simulated_box(qapp):
    panel, quick = ConnectionPanel(), QuickMeasurePanel()
    panel.device_changed.connect(quick.set_device)
    connect_demo(panel)
    assert quick.measure_button.isEnabled()
    quick.measure_button.click()
    wait_until(lambda: quick.result_label.text() != "-")
    assert float(quick.result_label.text().split()[0]) == pytest.approx(3.0, abs=0.01)   # P1 is 3.0 V
    wait_until(lambda: quick.measure_button.isEnabled())


def test_measuring_between_two_points_and_reversed(qapp):
    panel, quick = ConnectionPanel(), QuickMeasurePanel()
    panel.device_changed.connect(quick.set_device)
    connect_demo(panel)
    quick.pos_box.setCurrentText("P1")
    quick.neg_box.setCurrentText("P2")                    # 3.0 V - 1.2 V
    quick.measure_button.click()
    wait_until(lambda: quick.result_label.text() != "-")
    assert float(quick.result_label.text().split()[0]) == pytest.approx(1.8, abs=0.01)


def test_same_point_twice_gives_a_message_not_a_measurement(qapp):
    panel, quick = ConnectionPanel(), QuickMeasurePanel()
    panel.device_changed.connect(quick.set_device)
    connect_demo(panel)
    quick.pos_box.setCurrentText("P2")
    quick.neg_box.setCurrentText("P2")
    quick.measure_button.click()
    assert "two different" in quick.message_label.text()
    assert quick.result_label.text() == "-"


def test_measurement_runs_off_the_main_thread_and_locks_the_button(qapp):
    class SlowDevice:
        thread = None
        def measure_voltage(self, pos, neg):
            SlowDevice.thread = threading.get_ident()
            time.sleep(0.2)
            return 1.234

    quick = QuickMeasurePanel()
    quick.set_device(SlowDevice())
    quick.measure_button.click()
    assert not quick.measure_button.isEnabled()           # locked while measuring (no double clicks)
    assert quick.message_label.text() == "Measuring..."
    wait_until(lambda: quick.result_label.text() == "1.234 V")
    assert SlowDevice.thread != threading.get_ident()
    wait_until(lambda: quick.measure_button.isEnabled())


def test_device_failure_is_shown_in_red_and_the_button_recovers(qapp):
    class BrokenDevice:
        def measure_voltage(self, pos, neg):
            raise RuntimeError("The box did not reply.")

    quick = QuickMeasurePanel()
    quick.set_device(BrokenDevice())
    quick.measure_button.click()
    wait_until(lambda: "failed" in quick.message_label.text())
    assert "did not reply" in quick.message_label.text()
    assert "b00020" in quick.message_label.styleSheet()
    wait_until(lambda: quick.measure_button.isEnabled())


# ---------- whole window ----------

def test_main_window_wires_the_panels_together(qapp, tmp_path):
    window = MainWindow(calibration_folder=tmp_path)
    assert window.windowTitle() == "Circuitscope"
    assert not window.quick.measure_button.isEnabled()
    connect_demo(window.connection)
    assert window.quick.measure_button.isEnabled()
    assert "0012AB34CD56EF780011AABB" in window.windowTitle()
    assert window.statusBar().currentMessage() == "Connected"
    window.connection.connect_button.click()              # disconnect
    assert window.windowTitle() == "Circuitscope"
    assert not window.quick.measure_button.isEnabled()


def test_closing_the_window_closes_the_box(qapp, tmp_path):
    window = MainWindow(calibration_folder=tmp_path)
    connect_demo(window.connection)
    port = window.connection.device.port
    window.close()
    assert port.closed
