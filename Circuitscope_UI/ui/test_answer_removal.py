"""Automatic checks for answer-free student copies (formal-only exports). Run with:  python -m pytest"""

import json
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.models import (AnswerMode, Assessment, Item, ItemType, ModePolicy, Part, Quantity, Question,
                         ToleranceMode, Variable, VariableSource, WaveformFeature, WaveformSpec)
from core.session import Mode, SessionError, StudentSession
from core.submission import DEFAULT_KEY, load_submission, save_submission
from core.tutor import TutorSession
from hardware.demo import make_demo_device
from ui.assessment_editor import AssessmentEditor
from ui.student_panel import StudentPanel
from ui.tutor_panel import TutorPanel

SECRET = 3.14159


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


def make_assessment() -> Assessment:
    fixed = Item(id="1A-1", description="Supply", marks=2, quantity=Quantity.VOLTAGE, probe_pos="P1",
                 probe_neg="P3", expected=SECRET, tolerance=0.1, tolerance_mode=ToleranceMode.PERCENT)
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY, model_answer="TOPSECRETMODEL")
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING, quantity=Quantity.VOLTAGE,
                   probe_pos="P2", probe_neg="P3", expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    wave = Item(id="3A-1", description="Frequency", marks=2, mode=AnswerMode.WAVEFORM, expected=1000.0,
                tolerance=5, tolerance_mode=ToleranceMode.PERCENT,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P4",
                                      sample_rate_hz=20_000, num_points=2000))
    variables = [Variable("Vs", "Supply", "V", VariableSource.MEASURED, Quantity.VOLTAGE, "P1", "P3"),
                 Variable("R1", "R1", "ohm"), Variable("R2", "R2", "ohm")]
    return Assessment(title="Answer-free", assessment_id="answer-free", questions=[
        Question(id="1", title="Fixed", parts=[Part("1A", "m", [fixed, theory])]),
        Question(id="2", title="Div", default_mode=AnswerMode.VARYING, variables=variables,
                 parts=[Part("2A", "m", [varying])]),
        Question(id="3", title="Wave", default_mode=AnswerMode.WAVEFORM, parts=[Part("3A", "m", [wave])]),
    ])


# ---------- the copy itself ----------

def test_a_normal_student_copy_keeps_the_answers_but_drops_the_model_answers():
    a = make_assessment()
    for policy in (ModePolicy.STUDENT_CHOOSES, ModePolicy.PRACTICE_ONLY):
        copy = a.student_copy(policy)
        assert not copy.answers_removed
        items = {i.id: i for _q, _p, i in copy.iter_items()}
        assert items["1A-1"].expected == SECRET and items["2A-1"].expected_formula
        assert items["1A-2"].model_answer == ""
        assert copy.content_hash() == a.content_hash()


def test_a_formal_only_copy_has_no_answers_in_it(tmp_path):
    a = make_assessment()
    copy = a.student_copy(ModePolicy.FORMAL_ONLY)
    assert copy.answers_removed and copy.fingerprint == a.content_hash()
    items = {i.id: i for _q, _p, i in copy.iter_items()}
    assert items["1A-1"].expected is None and items["1A-1"].tolerance == 0.0
    assert items["2A-1"].expected_formula is None and items["3A-1"].expected is None
    assert items["1A-2"].model_answer == ""
    path = tmp_path / "s.json"
    copy.save(str(path))
    text = path.read_text()
    for secret in (str(SECRET), "Vs * R2", "TOPSECRETMODEL", "1000.0"):
        assert secret not in text


def test_the_formal_only_copy_still_has_everything_the_student_needs():
    a = make_assessment()
    copy = a.student_copy(ModePolicy.FORMAL_ONLY)
    items = {i.id: i for _q, _p, i in copy.iter_items()}
    assert (items["1A-1"].probe_pos, items["1A-1"].probe_neg, items["1A-1"].marks) == ("P1", "P3", 2)
    assert items["3A-1"].waveform.channel == "P4"
    assert [v.name for v in copy.questions[1].variables] == ["Vs", "R1", "R2"]
    assert items["2A-1"].required_variables == ["R1", "R2", "Vs"]
    assert copy.validate() == []                                  # no "has no expected value" complaints
    assert a.validate() == []


def test_the_fingerprint_survives_saving_and_the_working_copy_is_untouched(tmp_path):
    a = make_assessment()
    before = a.content_hash()
    copy = a.student_copy(ModePolicy.FORMAL_ONLY)
    copy.save(str(tmp_path / "s.json"))
    back = Assessment.load(str(tmp_path / "s.json"))
    assert back.answers_removed and back.content_hash() == before
    assert a.content_hash() == before and not a.answers_removed
    assert [i.expected for _q, _p, i in a.iter_items()][0] == SECRET


def test_the_helper_field_does_not_change_the_lecturers_fingerprint():
    a = make_assessment()
    before = a.content_hash()
    next(a.iter_items())[2].required_variables = ["x"]
    assert a.content_hash() == before


# ---------- working with it ----------

def formal_session(assessment):
    return StudentSession(assessment.student_copy(ModePolicy.FORMAL_ONLY), Mode.PRACTICE, "SMTCHR001", "0012AB34CD56EF780011AABB")


def test_an_answer_free_copy_can_only_be_done_formally_even_if_the_file_is_edited():
    copy = make_assessment().student_copy(ModePolicy.FORMAL_ONLY)
    copy.mode_policy = ModePolicy.STUDENT_CHOOSES                  # a student edits the file...
    s = StudentSession(copy, Mode.PRACTICE)
    assert s.mode == Mode.FORMAL and s.forced_mode() == Mode.FORMAL    # ...and still gets formal
    with pytest.raises(SessionError):
        s.practice_report()


def test_the_student_is_still_told_which_values_a_formula_item_needs():
    s = formal_session(make_assessment())
    s.record_measurement("2A-1", 1.2)
    problems = s.problems_before_submit()
    assert any("2A-1" in p and "R1" in p and "R2" in p and "Vs" in p for p in problems)


def test_work_done_on_an_answer_free_copy_is_graded_by_the_lecturers_file(tmp_path):
    lecturer = make_assessment()
    s = formal_session(lecturer)
    s.record_measurement("1A-1", 3.14)
    s.set_theory_answer("1A-2", "because")
    for name, value in (("Vs", 3.0), ("R1", 1500.0), ("R2", 1000.0)):
        s.set_variable("2", name, value)
    s.record_measurement("2A-1", 1.2)
    from core.submission import WaveformCapture
    s.record_waveform("3A-1", WaveformCapture(samples=([0.0] * 10 + [1.0] * 10) * 50, sample_rate_hz=20_000))
    sub = s.to_submission()
    assert sub.assessment_hash == lecturer.content_hash()          # matches the full assessment
    path = str(tmp_path / "sub.json")
    save_submission(sub, path, DEFAULT_KEY)
    tutor = TutorSession(lecturer)
    assert tutor.load_files([path]) == []
    assert tutor.notes(tutor.record("SMTCHR001")) == []             # no "edited" warning
    tutor.set_decision("SMTCHR001", "1A-2", True)
    report = tutor.report(tutor.record("SMTCHR001"))
    assert {r.item_id: r.status.value for r in report.item_results}["1A-1"] == "correct"
    assert report.total == pytest.approx(10.0)


# ---------- the lecturer's tools refuse it ----------

def test_the_tutor_session_refuses_an_answer_free_copy():
    with pytest.raises(ValueError, match="answers removed"):
        TutorSession(make_assessment().student_copy(ModePolicy.FORMAL_ONLY))


def test_the_tutor_panel_refuses_an_answer_free_copy(qapp, tmp_path):
    path = tmp_path / "s.json"
    make_assessment().student_copy(ModePolicy.FORMAL_ONLY).save(str(path))
    panel = TutorPanel(marking_folder=tmp_path / "m")
    assert not panel.open_assessment(str(path))
    assert "answers removed" in panel.message_label.text() and panel.session is None


def test_the_creator_refuses_to_open_an_answer_free_copy(qapp, tmp_path):
    path = tmp_path / "s.json"
    make_assessment().student_copy(ModePolicy.FORMAL_ONLY).save(str(path))
    editor = AssessmentEditor()
    assert not editor.open_file(str(path))
    assert "answers removed" in editor.status_label.text()


# ---------- exporting from the creator ----------

@pytest.fixture
def editor(qapp):
    e = AssessmentEditor()
    e.confirm = lambda _q: True
    e.assessment = make_assessment()
    return e


def test_exporting_formal_only_removes_the_answers_and_says_so(editor, tmp_path):
    path = str(tmp_path / "s.json")
    assert editor.export_for_students(path, ModePolicy.FORMAL_ONLY)
    data = json.loads(open(path).read())
    assert data["fingerprint"] == editor.assessment.content_hash()
    assert "SECRET" not in open(path).read() and str(SECRET) not in open(path).read()
    assert "cannot be read from the file" in editor.status_label.text()


@pytest.mark.parametrize("policy", [ModePolicy.STUDENT_CHOOSES, ModePolicy.PRACTICE_ONLY])
def test_the_other_exports_keep_the_answers_for_practice_feedback(editor, tmp_path, policy):
    path = str(tmp_path / "s.json")
    assert editor.export_for_students(path, policy)
    copy = Assessment.load(path)
    assert not copy.answers_removed and next(copy.iter_items())[2].expected == SECRET
    assert "Model answers were left out." in editor.status_label.text()


# ---------- the student screen ----------

def test_a_student_can_do_and_submit_an_answer_free_copy(qapp, tmp_path):
    path = tmp_path / "s.json"
    make_assessment().student_copy(ModePolicy.FORMAL_ONLY).save(str(path))
    panel = StudentPanel(progress_folder=tmp_path / "progress")
    device = make_demo_device()
    device.connect()
    panel.set_device(device)
    assert panel.open_assessment(str(path))
    assert "Formal" in panel.mode_fixed_label.text() and panel.session.mode == Mode.FORMAL
    panel.number_edit.setText("SMTCHR001")
    panel.select_item("1A-1"); panel.measure_button.click(); wait_until(lambda: not panel._busy)
    assert "Recorded" in panel.message_label.text()
    assert panel.feedback_label.text() == "Recorded."               # no marks, no expected values
    assert "Marks so far" not in panel.marks_label.text()
