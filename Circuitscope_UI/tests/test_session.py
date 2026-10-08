"""Automatic checks for core/session.py (the student's side, no screen). Run with:  python -m pytest"""

import json

import numpy as np
import pytest

from core.grading import Status
from core.models import (Assessment, AnswerMode, Item, ItemType, Part, Quantity, Question,
                         ToleranceMode, Variable, WaveformFeature, WaveformSpec)
from core.session import (ItemKind, ItemState, Mode, SessionError, StudentSession)
from core.submission import (SubmissionError, WaveformCapture, check_matches, grade_submission,
                             load_submission, save_submission)

KEY = b"test-key"
UID = "0012AB34CD56EF780011AABB"


def make_assessment() -> Assessment:
    """Q1: fixed voltage + theory + current. Q2: divider with variables. Q3: waveform frequency."""
    fixed = Item(id="1A-1", description="V", marks=2, quantity=Quantity.VOLTAGE, probe_pos="P1",
                 probe_neg="P3", expected=3.3, tolerance=0.1)
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY)
    current = Item(id="1A-3", description="I", marks=1, quantity=Quantity.CURRENT, probe_pos="I1",
                   expected=4.7, tolerance=0.2)
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING,
                   quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    wave = Item(id="3A-1", description="f", marks=2, mode=AnswerMode.WAVEFORM, expected=1000.0,
                tolerance_mode=ToleranceMode.PERCENT, tolerance=5,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1",
                                      sample_rate_hz=20_000, num_points=4000))
    q2_vars = [Variable("Vs", "Supply", "V"), Variable("R1", "R1", "ohm"), Variable("R2", "R2", "ohm")]
    return Assessment(title="P1", questions=[
        Question(id="1", title="Fixed", parts=[Part("1A", "m", [fixed, theory, current])]),
        Question(id="2", title="Varying", default_mode=AnswerMode.VARYING, variables=q2_vars,
                 parts=[Part("2A", "m", [varying])]),
        Question(id="3", title="Wave", default_mode=AnswerMode.WAVEFORM, parts=[Part("3A", "m", [wave])]),
    ])


def sine_capture(freq=1000.0) -> WaveformCapture:
    t = np.arange(4000) / 20_000
    return WaveformCapture(samples=list(1.65 + np.sin(2 * np.pi * freq * t)), sample_rate_hz=20_000)


def finished_session(mode=Mode.FORMAL) -> StudentSession:
    s = StudentSession(make_assessment(), mode, "SMTCHR001", UID)
    s.record_measurement("1A-1", 3.31)
    s.record_measurement("1A-3", 4.7)
    s.set_theory_answer("1A-2", "Because of the voltage divider rule")
    for name, value in (("Vs", 5.0), ("R1", 1000.0), ("R2", 1000.0)):
        s.set_variable("2", name, value)
    s.record_measurement("2A-1", 2.5)
    s.record_waveform("3A-1", sine_capture())
    return s


# ---------- what each item needs ----------

def test_item_kinds():
    s = StudentSession(make_assessment())
    assert s.kind("1A-1") == ItemKind.MEASURE
    assert s.kind("1A-2") == ItemKind.THEORY
    assert s.kind("1A-3") == ItemKind.MEASURE      # a current is measured like anything else (in mA)
    assert s.kind("2A-1") == ItemKind.MEASURE      # varying is still one measured number
    assert s.kind("3A-1") == ItemKind.WAVEFORM


def test_unknown_item_is_explained():
    with pytest.raises(SessionError, match="no item 'ZZ'"):
        StudentSession(make_assessment()).kind("ZZ")


def test_variables_needed_and_missing():
    s = StudentSession(make_assessment())
    assert s.variables_needed("2A-1") == ["R1", "R2", "Vs"]
    assert s.variables_needed("1A-1") == []
    assert s.missing_variables("2A-1") == ["R1", "R2", "Vs"]
    s.set_variable("2", "R1", 1000)
    assert s.missing_variables("2A-1") == ["R2", "Vs"]


def test_states_and_progress():
    s = StudentSession(make_assessment())
    assert s.state("1A-3") == ItemState.TODO
    assert s.progress() == (0, 5)
    s.record_measurement("1A-1", 3.3)
    s.set_theory_answer("1A-2", "   ")                  # blank text is not an answer
    assert s.state("1A-1") == ItemState.DONE
    assert s.state("1A-2") == ItemState.TODO
    assert s.progress() == (1, 5)


# ---------- recording ----------

def test_wrong_kind_of_data_is_refused_with_a_reason():
    s = StudentSession(make_assessment())
    with pytest.raises(SessionError, match="not a voltage measurement"):
        s.record_measurement("1A-2", 1.0)
    with pytest.raises(SessionError, match="not a waveform item"):
        s.record_waveform("1A-1", sine_capture())
    with pytest.raises(SessionError, match="not a theory item"):
        s.set_theory_answer("1A-1", "x")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "3.3", True, None])
def test_unusable_numbers_are_refused(bad):
    s = StudentSession(make_assessment())
    with pytest.raises(SessionError, match="not a usable number"):
        s.record_measurement("1A-1", bad)
    with pytest.raises(SessionError, match="not a usable number"):
        s.set_variable("2", "R1", bad)


def test_variable_must_belong_to_the_question():
    s = StudentSession(make_assessment())
    with pytest.raises(SessionError, match="no variable 'R9'.*Vs, R1, R2"):
        s.set_variable("2", "R9", 1)
    with pytest.raises(SessionError, match="no question '9'"):
        s.set_variable("9", "R1", 1)


def test_waveform_with_too_few_samples_is_refused():
    s = StudentSession(make_assessment())
    with pytest.raises(SessionError, match="only 1 samples"):
        s.record_waveform("3A-1", WaveformCapture(samples=[1.0], sample_rate_hz=1000))


def test_clear_item_allows_redoing():
    s = finished_session()
    s.clear_item("1A-1")
    assert s.state("1A-1") == ItemState.TODO


# ---------- finishing ----------

def test_cannot_submit_until_everything_is_done():
    s = StudentSession(make_assessment(), Mode.FORMAL)
    problems = s.problems_before_submit()
    assert "Type your student number." in problems
    assert any("Connect to the box" in p for p in problems)
    assert "Item 1A-1: measure it." in problems
    assert "Item 1A-2: type an answer." in problems
    assert "Item 3A-1: capture the waveform." in problems
    assert "Item 1A-3: measure it." in problems          # the current item must be done too
    with pytest.raises(SessionError, match="cannot be submitted yet"):
        s.to_submission()


def test_missing_variable_blocks_submission():
    s = finished_session()
    s.variables["2"].pop("R2")
    assert "Item 2A-1: give a value for R2." in s.problems_before_submit()


def test_finished_work_becomes_a_submission_that_signs_loads_and_grades(tmp_path):
    s = finished_session()
    assert s.problems_before_submit() == []
    sub = s.to_submission()
    assert sub.student_number == "SMTCHR001" and sub.device_uid == UID
    assert sub.theory_answers == {"1A-2": "Because of the voltage divider rule"}
    path = str(tmp_path / "sub.json")
    save_submission(sub, path, KEY)
    loaded = load_submission(path, KEY)
    assert loaded.theory_answers == sub.theory_answers          # typed text survives signing
    assert check_matches(s.assessment, loaded) == []
    report = grade_submission(s.assessment, loaded, {"1A-2": True})
    assert report.total == pytest.approx(2 + 3 + 3 + 2 + 1)     # fixed, theory, divider, waveform, current
    assert report.total_available == 11


def test_editing_the_typed_answer_breaks_the_signature(tmp_path):
    path = str(tmp_path / "sub.json")
    save_submission(finished_session().to_submission(), path, KEY)
    doc = json.load(open(path))
    doc["payload"]["theory_answers"]["1A-2"] = "something better"
    json.dump(doc, open(path, "w"))
    with pytest.raises(SubmissionError, match="signature does not match"):
        load_submission(path, KEY)


def test_old_submission_without_theory_answers_still_loads(tmp_path):
    path = str(tmp_path / "sub.json")
    save_submission(finished_session().to_submission(), path, KEY)
    from core.submission import sign_payload
    doc = json.load(open(path))
    del doc["payload"]["theory_answers"]
    doc["signature"] = sign_payload(doc["payload"], KEY)
    json.dump(doc, open(path, "w"))
    assert load_submission(path, KEY).theory_answers == {}


# ---------- practice mode ----------

def test_practice_report_marks_what_is_done():
    s = finished_session(Mode.PRACTICE)
    report = s.practice_report()
    status = {r.item_id: r.status for r in report.item_results}
    assert status["1A-1"] == Status.CORRECT
    assert status["2A-1"] == Status.CORRECT
    assert status["3A-1"] == Status.CORRECT
    assert status["1A-2"] == Status.NOT_ANSWERED                # theory waits for a tutor


def test_practice_report_works_with_partial_work_and_no_student_number():
    s = StudentSession(make_assessment(), Mode.PRACTICE)
    s.record_measurement("1A-1", 9.9)                           # wrong on purpose
    report = s.practice_report()
    assert {r.item_id: r.status for r in report.item_results}["1A-1"] == Status.INCORRECT


def test_formal_mode_never_shows_marks():
    with pytest.raises(SessionError, match="only shown in practice mode"):
        finished_session(Mode.FORMAL).practice_report()


# ---------- autosave and resume ----------

def test_progress_round_trip(tmp_path):
    path = str(tmp_path / "draft.json")
    s = finished_session()
    s.save_progress(path)
    fresh = StudentSession(s.assessment)
    fresh.load_progress(path)
    assert fresh.student_number == "SMTCHR001" and fresh.mode == Mode.FORMAL
    assert fresh.measurements == s.measurements and fresh.variables == s.variables
    assert fresh.theory_answers == s.theory_answers
    assert fresh.waveforms["3A-1"].samples == s.waveforms["3A-1"].samples
    assert fresh.problems_before_submit() == []


def test_progress_for_another_assessment_is_refused(tmp_path):
    path = str(tmp_path / "draft.json")
    finished_session().save_progress(path)
    with pytest.raises(SessionError, match="different assessment"):
        StudentSession(make_assessment()).load_progress(path)   # new assessment = new id


def test_progress_after_the_assessment_changed_is_refused(tmp_path):
    path = str(tmp_path / "draft.json")
    s = finished_session()
    s.save_progress(path)
    s.assessment.questions[0].parts[0].items[0].expected = 4.4
    with pytest.raises(SessionError, match="was changed"):
        StudentSession(s.assessment).load_progress(path)


def test_bad_progress_files_are_explained(tmp_path):
    s = StudentSession(make_assessment())
    with pytest.raises(SessionError, match="could not be read"):
        s.load_progress(str(tmp_path / "missing.json"))
    junk = tmp_path / "junk.json"
    junk.write_text('{"hello": 1}')
    with pytest.raises(SessionError, match="not a Circuitscope progress file"):
        s.load_progress(str(junk))
