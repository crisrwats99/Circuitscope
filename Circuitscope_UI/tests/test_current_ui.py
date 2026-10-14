"""Automatic checks for the current screens: quick current, current calibration, and the
assessment creator's current items. Run with:  python -m pytest"""

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.models import Assessment, Quantity
from hardware.calibration_store import load_calibration
from hardware.device import SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort
from ui.assessment_editor import AssessmentEditor
from ui.current_calibration_panel import CurrentCalibrationPanel
from ui.main_window import MainWindow
from ui.quick_measure import QuickCurrentPanel

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
    board = SimulatedBoard(**board_options)
    device = SerialDevice(SimulatedPort(board))
    device.connect()
    device.board = board
    return device


# ---------- quick current ----------

def test_quick_current_is_disabled_without_a_box(qapp):
    panel = QuickCurrentPanel()
    assert not panel.measure_button.isEnabled()
    assert "Connect" in panel.message_label.text()


def test_quick_current_shows_the_current_in_ma_with_its_sign(qapp):
    device = make_device()
    panel = QuickCurrentPanel()
    panel.set_device(device)
    for ma in (4.7, -12.0):
        device.board.set_dc("I1", ma)
        panel.result_label.setText("-")
        panel.measure_button.click()
        wait_until(lambda: panel.result_label.text() != "-" and panel.measure_button.isEnabled())
        assert float(panel.result_label.text().split()[0]) == pytest.approx(ma, abs=0.05)
        assert panel.result_label.text().endswith("mA")


def test_quick_current_explains_a_current_that_is_too_big(qapp):
    device = make_device()
    device.board.set_dc("I1", 80.0)
    panel = QuickCurrentPanel()
    panel.set_device(device)
    panel.measure_button.click()
    wait_until(lambda: "failed" in panel.message_label.text())
    assert "outside what the box can measure" in panel.message_label.text()
    assert panel.result_label.text() == "-"


# ---------- current calibration ----------

def read_both(panel, device, low=0.0, high=20.0):
    device.board.set_dc("I1", low)
    panel.low_ma.setValue(low)
    panel.low_button.click()
    wait_until(lambda: panel._low is not None and panel.low_button.isEnabled())
    device.board.set_dc("I1", high)
    panel.high_ma.setValue(high)
    panel.high_button.click()
    wait_until(lambda: panel._high is not None and panel.high_button.isEnabled())


def test_current_calibration_is_disabled_without_a_box(qapp, tmp_path):
    panel = CurrentCalibrationPanel(folder=tmp_path)
    for b in (panel.low_button, panel.high_button, panel.apply_button, panel.reset_button):
        assert not b.isEnabled()


def test_calibrating_fixes_a_box_that_reads_high_and_saves_it(qapp, tmp_path):
    device = make_device(adc_gain=1.07, adc_offset_v=0.02)
    panel = CurrentCalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    read_both(panel, device)
    assert panel.apply_button.isEnabled()
    panel.apply_button.click()
    assert "Saved" in panel.status_label.text()
    device.board.set_dc("I1", 10.0)
    assert device.measure_current("I1") == pytest.approx(10.0, abs=0.05)
    assert load_calibration(device.uid, tmp_path, "I1") is not None


def test_a_saved_calibration_is_loaded_when_the_box_is_connected_again(qapp, tmp_path):
    device = make_device(adc_gain=1.07)
    panel = CurrentCalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    read_both(panel, device)
    panel.apply_button.click()
    again = make_device(adc_gain=1.07)
    panel.set_device(again)
    assert "Loaded" in panel.status_label.text()
    again.board.set_dc("I1", 10.0)
    assert again.measure_current("I1") == pytest.approx(10.0, abs=0.05)


def test_two_known_currents_too_close_together_are_refused(qapp, tmp_path):
    device = make_device()
    panel = CurrentCalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    read_both(panel, device, low=4.0, high=4.5)
    panel.apply_button.click()
    assert "at least" in panel.status_label.text()
    assert load_calibration(device.uid, tmp_path, "I1") is None


def test_a_wrong_reference_is_not_saved(qapp, tmp_path):
    device = make_device()
    panel = CurrentCalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    read_both(panel, device)
    panel.high_ma.setValue(30.0)                  # claims 30 mA but the box really saw 20 mA
    panel._high = (30.0, panel._high[1])
    panel.apply_button.click()
    assert "away from ideal" in panel.status_label.text()
    assert load_calibration(device.uid, tmp_path, "I1") is None


def test_reset_removes_the_saved_calibration(qapp, tmp_path):
    device = make_device(adc_gain=1.05)
    panel = CurrentCalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    read_both(panel, device)
    panel.apply_button.click()
    panel.reset_button.click()
    assert load_calibration(device.uid, tmp_path, "I1") is None
    assert "I1" not in device.current_calibrations


def test_main_window_has_the_current_panels(qapp, tmp_path):
    window = MainWindow(calibration_folder=tmp_path)
    assert window.findChildren(QuickCurrentPanel) and window.findChildren(CurrentCalibrationPanel)


# ---------- assessment creator ----------

@pytest.fixture
def editor(qapp):
    e = AssessmentEditor()
    e.confirm = lambda _q: True
    return e


def new_item(editor):
    editor.add_question()
    q = editor.assessment.questions[-1]
    editor.add_part()
    p = q.parts[-1]
    editor.add_item()
    return p.items[-1]


def test_switching_an_item_to_current_offers_both_current_inputs_and_hides_the_second_probe(editor):
    item = new_item(editor)
    editor.i_quantity_box.setCurrentIndex(editor.i_quantity_box.findData("current"))
    assert item.quantity == Quantity.CURRENT
    assert [editor.i_pos_box.itemData(i) for i in range(editor.i_pos_box.count())] == ["I1", "I2"]
    assert item.probe_pos == "I1" and item.probe_neg is None
    assert not editor.item_form.isRowVisible(editor.i_neg_box)
    editor.i_expected_edit.setText("4.7")
    assert editor.assessment.validate() == []


def test_switching_back_to_voltage_restores_the_voltage_inputs(editor):
    item = new_item(editor)
    editor.i_quantity_box.setCurrentIndex(editor.i_quantity_box.findData("current"))
    editor.i_quantity_box.setCurrentIndex(editor.i_quantity_box.findData("voltage"))
    assert item.quantity == Quantity.VOLTAGE
    data = [editor.i_pos_box.itemData(i) for i in range(editor.i_pos_box.count())]
    assert "P1" in data and "P5" in data and "P6" not in data and "I1" not in data
    assert editor.item_form.isRowVisible(editor.i_neg_box)


def test_validation_rejects_a_current_on_a_voltage_input_and_the_reverse(editor):
    item = new_item(editor)
    item.expected = 1.0
    item.quantity, item.probe_pos, item.probe_neg = Quantity.CURRENT, "P1", None
    assert any("current input" in p for p in editor.assessment.validate())
    item.quantity, item.probe_pos, item.probe_neg = Quantity.VOLTAGE, "I1", "P3"
    assert any("P1 to P5" in p for p in editor.assessment.validate())
    item.probe_pos = "P0"
    assert any("P1 to P5" in p for p in editor.assessment.validate())


def test_a_current_item_survives_saving_and_loading(editor, tmp_path):
    item = new_item(editor)
    editor.i_quantity_box.setCurrentIndex(editor.i_quantity_box.findData("current"))
    editor.i_expected_edit.setText("4.7")
    editor.assessment.save(str(tmp_path / "a.json"))
    back = Assessment.load(str(tmp_path / "a.json")).questions[0].parts[0].items[0]
    assert back.quantity == Quantity.CURRENT and back.probe_pos == "I1" and back.probe_neg is None
    assert back.expected == 4.7


def test_the_second_current_input_can_be_chosen_in_the_creator(editor):
    item = new_item(editor)
    editor.i_quantity_box.setCurrentIndex(editor.i_quantity_box.findData("current"))
    editor.i_pos_box.setCurrentIndex(editor.i_pos_box.findData("I2"))
    editor.i_expected_edit.setText("-12")
    assert item.probe_pos == "I2" and editor.assessment.validate() == []


def test_both_current_inputs_are_offered_in_the_quick_and_calibration_panels(qapp):
    quick, calibration = QuickCurrentPanel(), CurrentCalibrationPanel()
    for box in (quick.channel_box, calibration.channel_box):
        assert [box.itemText(i) for i in range(box.count())] == ["I1", "I2"]


def test_quick_current_reads_the_second_input(qapp):
    device = make_device()
    device.board.set_dc("I2", -12.0)
    panel = QuickCurrentPanel()
    panel.set_device(device)
    panel.channel_box.setCurrentIndex(1)
    panel.measure_button.click()
    wait_until(lambda: panel.result_label.text() != "-" and panel.measure_button.isEnabled())
    assert float(panel.result_label.text().split()[0]) == pytest.approx(-12.0, abs=0.05)


def test_calibrating_the_second_input_leaves_the_first_alone(qapp, tmp_path):
    device = make_device(adc_gain=1.07)
    panel = CurrentCalibrationPanel(folder=tmp_path)
    panel.set_device(device)
    panel.channel_box.setCurrentIndex(1)                       # I2
    for ma, which in ((0.0, "low"), (20.0, "high")):
        device.board.set_dc("I2", ma)
        (panel.low_ma if which == "low" else panel.high_ma).setValue(ma)
        (panel.low_button if which == "low" else panel.high_button).click()
        wait_until(lambda: not panel._busy)
    panel.apply_button.click()
    assert "I2 calibrated" in panel.status_label.text()
    assert load_calibration(device.uid, tmp_path, "I2") is not None
    assert load_calibration(device.uid, tmp_path, "I1") is None
    assert "I1" not in device.current_calibrations and "I2" in device.current_calibrations
