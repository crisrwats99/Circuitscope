"""Automatic checks for the student screen (ui/student_panel.py). Run with:  python -m pytest"""

import json
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from core.models import (Assessment, AnswerMode, Item, ItemType, Part, Quantity, Question,
                         ToleranceMode, Variable, VariableSource, WaveformFeature, WaveformSpec)
from core.session import Mode
from core.submission import grade_submission, load_submission
from hardware.demo import make_demo_device
from ui.image_picker import qimage_to_b64
from ui.main_window import MainWindow
from ui.student_panel import StudentPanel


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
    device = make_demo_device()          # P1 3.0 V, P2 1.2 V, P3 0 V, P4 1 kHz sine
    device.connect()
    return device


def make_assessment(with_picture=False) -> Assessment:
    fixed = Item(id="1A-1", description="Supply voltage", marks=2, quantity=Quantity.VOLTAGE,
                 probe_pos="P1", probe_neg="P3", expected=3.0, tolerance=0.1, prompt="Measure the supply.")
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY, prompt="Why?")
    current = Item(id="1A-3", description="Current", marks=1, quantity=Quantity.CURRENT,
                   probe_pos="P1", probe_neg="P3", expected=0.001, tolerance=0.0001)
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING,
                   quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    wave = Item(id="3A-1", description="Frequency", marks=2, mode=AnswerMode.WAVEFORM, expected=1000.0,
                tolerance_mode=ToleranceMode.PERCENT, tolerance=5,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P4",
                                      sample_rate_hz=20_000, num_points=2000))
    variables = [Variable("Vs", "Supply", "V", VariableSource.MEASURED, Quantity.VOLTAGE, "P1", "P3"),
                 Variable("R1", "Resistor R1", "ohm"), Variable("R2", "Resistor R2", "ohm")]
    q1 = Question(id="1", title="Basics", prompt="Question one text.",
                  parts=[Part("1A", "m", [fixed, theory, current])])
    if with_picture:
        image = QImage(20, 10, QImage.Format_RGB32)
        image.fill(QColor("red"))
        q1.image = qimage_to_b64(image)
    return Assessment(title="Practical 1", questions=[
        q1,
        Question(id="2", title="Divider", default_mode=AnswerMode.VARYING, variables=variables,
                 parts=[Part("2A", "m", [varying])]),
        Question(id="3", title="Wave", default_mode=AnswerMode.WAVEFORM, parts=[Part("3A", "m", [wave])]),
    ])


@pytest.fixture
def files(tmp_path):
    a = make_assessment()
    assert a.validate() == []
    path = tmp_path / "practical.json"
    a.save(str(path))
    return a, str(path)


def new_panel(tmp_path, device=True):
    panel = StudentPanel(progress_folder=tmp_path / "progress")
    if device:
        panel.set_device(demo_device())
    return panel


def measure_current(panel):
    panel.measure_button.click()
    wait_until(lambda: not panel._busy)


def do_everything(panel):
    """Finish every doable item of the test assessment."""
    panel.number_edit.setText("SMTCHR001")
    panel.select_item("1A-1"); measure_current(panel)
    panel.select_item("1A-2"); panel.answer_edit.setPlainText("Voltage divider rule.")
    panel.select_item("2A-1")
    panel._var_widgets["R1"].setText("1500"); panel._var_widgets["R1"].editingFinished.emit()
    panel._var_widgets["R2"].setText("1000"); panel._var_widgets["R2"].editingFinished.emit()
    panel._var_widgets["Vs"].click(); wait_until(lambda: not panel._busy)
    measure_current(panel)
    panel.select_item("3A-1")
    panel.capture_button.click(); wait_until(lambda: not panel._busy)


# ---------- opening ----------

def test_starts_empty_and_locked(qapp, tmp_path):
    panel = new_panel(tmp_path, device=False)
    assert panel.open_button.isEnabled()
    assert not panel.submit_button.isEnabled() and not panel.number_edit.isEnabled()
    assert panel.title_label.text() == "No assessment open."


def test_bad_files_are_explained(qapp, tmp_path):
    panel = new_panel(tmp_path)
    assert not panel.open_assessment(str(tmp_path / "missing.json"))
    assert "does not exist" in panel.message_label.text()
    junk = tmp_path / "junk.json"
    junk.write_text("{not json")
    assert not panel.open_assessment(str(junk))
    assert "not a valid assessment file" in panel.message_label.text()


def test_an_assessment_with_problems_is_refused(qapp, tmp_path):
    a = make_assessment()
    a.questions[0].parts[0].items[0].marks = -1
    path = tmp_path / "bad.json"
    a.save(str(path))
    panel = new_panel(tmp_path)
    assert not panel.open_assessment(str(path))
    assert "cannot be used" in panel.message_label.text()
    assert panel.session is None


def test_opening_shows_the_items(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    assert panel.open_assessment(files[1])
    assert panel.title_label.text() == "Practical 1"
    assert set(panel._tree_items) == {"1A-1", "1A-2", "1A-3", "2A-1", "3A-1"}
    assert panel._tree_items["1A-1"].text(0).startswith("○ 1A-1")      # not done yet
    assert panel._tree_items["1A-3"].text(0).startswith("⊘ 1A-3")      # unsupported
    assert panel.progress_label.text() == "Progress: 0 of 4 items done"
    assert not panel.submit_button.isEnabled()


# ---------- measuring in practice mode ----------

def test_measuring_records_the_reading_and_shows_feedback(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("1A-1")
    assert "red probe to P1 and the black probe to P3" in panel.probe_hint.text()
    measure_current(panel)
    assert panel.reading_label.text() == "3.000 V"
    assert panel.session.measurements["1A-1"] == pytest.approx(3.0, abs=0.01)
    assert panel._tree_items["1A-1"].text(0).startswith("✔")
    assert panel.feedback_label.text().startswith("Correct: 2 / 2 marks")
    assert panel.marks_label.text().startswith("Marks so far: 2 / 10")      # the current item (1 mark) is left out


def test_without_a_box_measuring_is_blocked(qapp, tmp_path, files):
    panel = new_panel(tmp_path, device=False)
    panel.open_assessment(files[1])
    panel.select_item("1A-1")
    assert not panel.measure_button.isEnabled()
    panel.set_device(demo_device())
    assert panel.measure_button.isEnabled()
    assert panel.session.device_uid != ""


def test_buttons_lock_while_the_box_is_being_read(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("1A-1")
    panel.measure_button.click()
    assert not panel.measure_button.isEnabled() and not panel.open_button.isEnabled()
    wait_until(lambda: not panel._busy)
    assert panel.measure_button.isEnabled()


def test_a_failed_reading_is_explained(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("1A-1")
    panel.device.measure_voltage = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cable unplugged"))
    measure_current(panel)
    assert "could not be read: cable unplugged" in panel.message_label.text()
    assert "1A-1" not in panel.session.measurements


# ---------- other kinds of item ----------

def test_theory_answer_is_saved_as_typed(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("1A-2")
    assert panel.pages.currentIndex() == 2
    panel.answer_edit.setPlainText("Because of Ohm's law")
    assert panel.session.theory_answers["1A-2"] == "Because of Ohm's law"
    assert "tutor marks" in panel.feedback_label.text()
    panel.select_item("1A-1"); panel.select_item("1A-2")                     # leave and come back
    assert panel.answer_edit.toPlainText() == "Because of Ohm's law"


def test_current_item_is_skipped_with_an_explanation(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("1A-3")
    assert panel.pages.currentIndex() == 3
    assert "not available" in panel.unsupported_label.text()


def test_waveform_capture_is_recorded_and_plotted(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("3A-1")
    assert "probe to P4" in panel.wave_hint.text()
    panel.capture_button.click()
    wait_until(lambda: not panel._busy)
    assert len(panel.session.waveforms["3A-1"].samples) == 2000
    x, y = panel.signal_curve.getData()
    assert len(x) == 2000
    assert panel.feedback_label.text().startswith("Correct: 2 / 2 marks")


def test_variables_typed_and_measured(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    panel.select_item("2A-1")
    assert set(panel._var_widgets) == {"R1", "R2", "Vs"}
    edit = panel._var_widgets["R1"]
    edit.setText("1k")
    edit.editingFinished.emit()
    assert "has letters in it" in panel.variable_message.text()
    assert "R1" not in panel.session.variables.get("2", {})
    edit.setText("1500")
    edit.editingFinished.emit()
    assert panel.session.variables["2"]["R1"] == 1500 and panel.variable_message.text() == ""
    panel._var_widgets["Vs"].click()
    wait_until(lambda: not panel._busy)
    assert panel.session.variables["2"]["Vs"] == pytest.approx(3.0, abs=0.01)
    assert "Vs = 3" in panel._var_widgets["Vs"].text()                       # button now shows the reading


def test_varying_item_is_graded_from_the_students_own_values(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    do_everything(panel)
    panel.select_item("2A-1")
    assert panel.feedback_label.text().startswith("Correct: 3 / 3 marks")   # 3 V * 1000 / 2500 = 1.2 V


def test_question_heading_shows_its_text_and_picture(qapp, tmp_path):
    a = make_assessment(with_picture=True)
    path = tmp_path / "pic.json"
    a.save(str(path))
    panel = new_panel(tmp_path)
    panel.open_assessment(str(path))
    panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
    assert panel.context_label.text() == "Question one text."
    assert not panel.picture_label.isHidden() and panel.picture_label.pixmap().width() == 20
    assert panel.pages.isHidden()


# ---------- formal mode and submitting ----------

def test_formal_mode_hides_marks_and_requires_everything(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.mode_box.setCurrentIndex(1)
    panel.open_assessment(files[1])
    assert panel.session.mode == Mode.FORMAL
    panel.select_item("1A-1")
    measure_current(panel)
    assert "marks are not shown" in panel.marks_label.text()
    assert panel.feedback_label.text() == "Recorded."
    assert not panel.submit_button.isEnabled()
    text = panel.problems_label.text()
    assert "Type your student number." in text and "Item 3A-1: capture the waveform." in text
    assert "1A-3" not in text


def test_a_finished_formal_submission_is_saved_signed_and_gradable(qapp, tmp_path, files):
    assessment, path = files
    panel = new_panel(tmp_path)
    panel.mode_box.setCurrentIndex(1)
    panel.open_assessment(path)
    do_everything(panel)
    assert panel.problems_label.text() == ""
    assert panel.submit_button.isEnabled()
    out = str(tmp_path / "SMTCHR001.json")
    assert panel.submit(out)
    sub = load_submission(out)                                               # signature checks out
    assert sub.student_number == "SMTCHR001" and sub.device_uid == panel.device.uid
    assert sub.theory_answers["1A-2"] == "Voltage divider rule."
    report = grade_submission(Assessment.load(path), sub, {"1A-2": True})
    assert report.total == pytest.approx(2 + 3 + 3 + 2)


def test_submit_reports_a_folder_that_cannot_be_written(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.mode_box.setCurrentIndex(1)
    panel.open_assessment(files[1])
    do_everything(panel)
    assert not panel.submit(str(tmp_path / "no_such_folder" / "x.json"))
    assert "Could not save" in panel.message_label.text()


def test_practice_mode_cannot_submit(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    do_everything(panel)
    assert not panel.submit_button.isEnabled()
    assert "Formal mode" in panel.submit_button.toolTip()


def test_mode_is_locked_once_work_exists(qapp, tmp_path, files):
    panel = new_panel(tmp_path)
    panel.open_assessment(files[1])
    assert panel.mode_box.isEnabled()
    panel.select_item("1A-1"); measure_current(panel)
    assert not panel.mode_box.isEnabled()


# ---------- autosave and resume ----------

def test_work_is_restored_when_the_assessment_is_opened_again(qapp, tmp_path, files):
    first = new_panel(tmp_path)
    first.mode_box.setCurrentIndex(1)
    first.open_assessment(files[1])
    first.number_edit.setText("SMTCHR001")
    first.select_item("1A-1"); measure_current(first)
    first.select_item("1A-2"); first.answer_edit.setPlainText("typed")
    first.autosave()                                                        # (normally on a 0.6 s timer)

    second = new_panel(tmp_path)                                            # a fresh start of the app
    second.open_assessment(files[1])
    assert "restored" in second.message_label.text()
    assert second.number_edit.text() == "SMTCHR001"
    assert second.mode_box.currentIndex() == 1
    assert second.session.measurements == first.session.measurements
    assert second.session.theory_answers["1A-2"] == "typed"
    assert second._tree_items["1A-1"].text(0).startswith("✔")


def test_a_draft_for_an_edited_assessment_is_ignored(qapp, tmp_path, files):
    assessment, path = files
    first = new_panel(tmp_path)
    first.open_assessment(path)
    first.select_item("1A-1"); measure_current(first)
    assessment.questions[0].parts[0].items[0].expected = 2.0
    assessment.save(path)
    second = new_panel(tmp_path)
    assert second.open_assessment(path)
    assert "old saved draft was ignored" in second.message_label.text()
    assert second.session.measurements == {}


# ---------- in the whole window ----------

def test_window_has_a_working_student_tab(qapp, tmp_path, files):
    from ui.connection_panel import SIMULATED
    window = MainWindow(calibration_folder=tmp_path / "cal", progress_folder=tmp_path / "progress")
    assert window.tabs.tabText(1) == "Student"
    window.tabs.setCurrentWidget(window.student)
    assert not window.connection.isHidden()                                 # the box is needed here
    window.connection.mode_box.setCurrentText(SIMULATED)
    window.connection.connect_button.click()
    wait_until(lambda: window.connection.device is not None)
    window.student.open_assessment(files[1])
    window.student.select_item("1A-1")
    window.student.measure_button.click()
    wait_until(lambda: "1A-1" in window.student.session.measurements)
    assert window.student.session.device_uid == window.connection.device.uid
