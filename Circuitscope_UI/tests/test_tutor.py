"""Automatic checks for core/tutor.py (the tutor's side, no screen). Run with:  python -m pytest"""

import csv
import json

import pytest

from core.models import (Assessment, AnswerMode, Item, ItemType, Part, Quantity, Question,
                         ToleranceMode, Variable)
from core.session import Mode, StudentSession
from core.submission import save_submission
from core.tutor import FeedbackLevel, TutorSession

KEY = b"test-key"


def make_assessment() -> Assessment:
    """Q1: fixed 3.0 V (2) + theory (3). Q2: divider formula (3). Total 8."""
    fixed = Item(id="1A-1", description="Supply", marks=2, quantity=Quantity.VOLTAGE, probe_pos="P1",
                 probe_neg="P3", expected=3.0, tolerance=0.1)
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY,
                  model_answer="The resistors share the supply.")
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING,
                   quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    return Assessment(title="P1", questions=[
        Question(id="1", title="Fixed", parts=[Part("1A", "m", [fixed, theory])]),
        Question(id="2", title="Div", default_mode=AnswerMode.VARYING,
                 variables=[Variable("Vs", "Vs", "V"), Variable("R1", "R1", "ohm"), Variable("R2", "R2", "ohm")],
                 parts=[Part("2A", "m", [varying])]),
    ])


def student(assessment, number, uid="0012AB34CD56EF780011AABB", supply=3.0, divider=1.2, created=None):
    """A finished formal session for one student, ready to be saved."""
    s = StudentSession(assessment, Mode.FORMAL, number, uid)
    s.record_measurement("1A-1", supply)
    s.set_theory_answer("1A-2", f"answer from {number}")
    for name, value in (("Vs", 3.0), ("R1", 1500.0), ("R2", 1000.0)):
        s.set_variable("2", name, value)
    s.record_measurement("2A-1", divider)
    sub = s.to_submission()
    if created:
        sub.created_utc = created
    return sub


@pytest.fixture
def setup(tmp_path):
    a = make_assessment()
    tutor = TutorSession(a, KEY)

    def make_file(sub, name):
        path = str(tmp_path / name)
        save_submission(sub, path, KEY)
        return path
    return a, tutor, make_file


# ---------- loading ----------

def test_files_load_and_are_sorted_by_student_number(setup):
    a, tutor, make_file = setup
    paths = [make_file(student(a, n), f"{n}.json") for n in ("B2", "A1")]
    assert tutor.load_files(paths) == []
    assert [r.student_number for r in tutor.records] == ["A1", "B2"]


def test_a_tampered_file_is_refused_by_name_and_the_rest_still_load(setup, tmp_path):
    a, tutor, make_file = setup
    good = make_file(student(a, "A1"), "good.json")
    bad = make_file(student(a, "B2"), "bad.json")
    doc = json.load(open(bad))
    doc["payload"]["measurements"]["1A-1"] = 3.0001
    json.dump(doc, open(bad, "w"))
    messages = tutor.load_files([bad, good])
    assert len(messages) == 1 and messages[0].startswith("bad.json: not loaded.")
    assert "signature does not match" in messages[0]
    assert [r.student_number for r in tutor.records] == ["A1"]


def test_a_file_signed_with_another_key_or_not_a_submission_is_refused(setup, tmp_path):
    a, tutor, _ = setup
    other = str(tmp_path / "other.json")
    save_submission(student(a, "A1"), other, b"another-key")
    junk = tmp_path / "junk.json"
    junk.write_text("{not json")
    messages = tutor.load_files([other, str(junk), str(tmp_path / "missing.json")])
    assert len(messages) == 3 and all("not loaded" in m for m in messages)
    assert tutor.records == []


def test_a_resubmission_replaces_the_earlier_one_and_an_older_one_is_ignored(setup):
    a, tutor, _ = setup
    assert tutor.add_submission(student(a, "A1", supply=9.0, created="2026-10-07T10:00:00+00:00")) == ""
    note = tutor.add_submission(student(a, "A1", supply=3.0, created="2026-10-07T11:00:00+00:00"))
    assert "replaced the earlier submission" in note
    assert tutor.record("A1").submission.measurements["1A-1"] == 3.0
    assert "ignored" in tutor.add_submission(student(a, "A1", created="2026-10-07T09:00:00+00:00"))
    assert len(tutor.records) == 1


def test_unknown_student_is_explained(setup):
    with pytest.raises(KeyError, match="No submission from student 'Z9'"):
        setup[1].record("Z9")


# ---------- notes ----------

def test_notes_warn_about_a_shared_box_and_an_edited_assessment(setup):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1", uid="AAAA"))
    tutor.add_submission(student(a, "B2", uid="AAAA"))
    tutor.add_submission(student(a, "C3", uid="CCCC"))
    assert any("also used by: B2" in n for n in tutor.notes(tutor.record("A1")))
    assert tutor.notes(tutor.record("C3")) == []
    a.questions[0].parts[0].items[0].expected = 2.0                       # lecturer edits afterwards
    assert any("edited after this submission" in n for n in tutor.notes(tutor.record("C3")))


# ---------- theory marking ----------

def test_theory_items_start_unmarked_and_decisions_change_the_marks(setup):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    r = tutor.record("A1")
    assert tutor.pending_theory(r) == ["1A-2"] and tutor.total_pending() == 1
    assert tutor.report(r).total == pytest.approx(5)                        # 2 + 3, theory not yet counted
    tutor.set_decision("A1", "1A-2", True)
    assert tutor.pending_theory(r) == [] and tutor.report(r).total == pytest.approx(8)
    tutor.set_decision("A1", "1A-2", False)
    assert tutor.report(r).total == pytest.approx(5)
    tutor.set_decision("A1", "1A-2", None)
    assert tutor.pending_theory(r) == ["1A-2"]


def test_bad_decisions_are_explained(setup):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    with pytest.raises(ValueError, match="not a theory item"):
        tutor.set_decision("A1", "1A-1", True)
    with pytest.raises(ValueError, match="True \\(correct\\), False"):
        tutor.set_decision("A1", "1A-2", "yes")


def test_marking_can_be_saved_and_continued_even_for_students_loaded_later(setup, tmp_path):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    tutor.add_submission(student(a, "B2"))
    tutor.set_decision("A1", "1A-2", True)
    tutor.set_decision("B2", "1A-2", False)
    path = str(tmp_path / "marking.json")
    tutor.save_marking(path)
    fresh = TutorSession(a, KEY)
    fresh.load_marking(path)
    fresh.add_submission(student(a, "B2"))                                  # loaded AFTER the marking file
    assert fresh.record("B2").decisions == {"1A-2": False}
    fresh.add_submission(student(a, "A1"))
    assert fresh.record("A1").decisions == {"1A-2": True}


def test_marking_file_for_another_assessment_or_damaged_is_refused(setup, tmp_path):
    a, tutor, _ = setup
    path = str(tmp_path / "marking.json")
    tutor.save_marking(path)
    with pytest.raises(ValueError, match="different assessment"):
        TutorSession(make_assessment(), KEY).load_marking(path)
    junk = tmp_path / "junk.json"
    junk.write_text('{"x": 1}')
    with pytest.raises(ValueError, match="not a Circuitscope marking file"):
        tutor.load_marking(str(junk))
    with pytest.raises(ValueError, match="could not be read"):
        tutor.load_marking(str(tmp_path / "missing.json"))


# ---------- mark sheet ----------

def test_marksheet_has_one_row_per_student(setup, tmp_path):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    tutor.add_submission(student(a, "B2", supply=1.0))                      # wrong supply reading
    tutor.set_decision("A1", "1A-2", True)
    tutor.set_decision("B2", "1A-2", False)
    path = str(tmp_path / "marks.csv")
    tutor.write_marksheet(path)
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))[1:]       # [0] is the "sep=," hint line for Excel
    assert rows[0][:2] == ["Student number", "Device UID"]
    by_student = {r[0]: r for r in rows[1:]}
    assert by_student["A1"][-4:-2] == ["8", "8"]
    assert by_student["B2"][-4:-2] == ["3", "8"]                            # only the divider item


# ---------- feedback ----------

def feedback_setup(setup):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "B2", supply=1.0))                      # wrong supply reading
    tutor.set_decision("B2", "1A-2", False)
    return tutor, tutor.record("B2")


def test_marks_only_feedback_has_no_item_lines(setup):
    tutor, r = feedback_setup(setup)
    text = tutor.feedback_text(r, FeedbackLevel.MARKS_ONLY)
    assert "Total: 3 / 8" in text and "Question 1: Fixed  0 / 5" in text and "Question 2: Div  3 / 3" in text
    assert "1A-1" not in text


def test_correctness_feedback_hides_expected_values_and_model_answers(setup):
    tutor, r = feedback_setup(setup)
    text = tutor.feedback_text(r, FeedbackLevel.CORRECTNESS)
    assert "1A-1 Supply: Not correct (0/2)" in text
    assert "Your value was outside the allowed range." in text
    assert "Your answer was marked incorrect." in text
    assert "2A-1 Divider: Correct (3/3)" in text
    assert "Model answer" not in text and "expected" not in text.lower()
    assert "3.0" not in text and "0.1" not in text                         # neither the answer nor the tolerance


def test_full_feedback_shows_values_and_the_model_answer(setup):
    tutor, r = feedback_setup(setup)
    text = tutor.feedback_text(r, FeedbackLevel.FULL)
    assert "Measured 1, expected 3" in text
    assert "Model answer: The resistors share the supply." in text


def test_unmarked_theory_and_missing_variables_are_described(setup):
    a, tutor, _ = setup
    sub = student(a, "A1")
    sub.variables["2"].pop("R2")                                           # student never gave R2
    tutor.add_submission(sub)
    text = tutor.feedback_text(tutor.record("A1"), FeedbackLevel.CORRECTNESS)
    assert "R2" in text                                                     # the reason names the missing value


def test_feedback_files_are_written_one_per_student_with_safe_names(setup, tmp_path):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A/1"))
    tutor.add_submission(student(a, "A_1"))                                 # cleans up to the same name
    tutor.add_submission(student(a, "../evil"))
    folder = str(tmp_path / "out")
    paths = tutor.write_feedback(folder, FeedbackLevel.MARKS_ONLY)
    names = sorted(p.split("out")[-1].strip("/\\") for p in paths)
    assert len(set(paths)) == 3 and all(p.startswith(folder) for p in paths)
    assert all("/" not in n and "\\" not in n and ".." not in n for n in names)
    assert all("Student number:" in open(p, encoding="utf-8").read() for p in paths)


# ---------- unmarked theory in the feedback ----------

def test_an_unmarked_theory_answer_is_awaiting_marking_not_wrong(setup):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    record = tutor.record("A1")
    for level in (FeedbackLevel.CORRECTNESS, FeedbackLevel.FULL):
        text = tutor.feedback_text(record, level)
        assert "1A-2 Explain: Awaiting tutor marking (?/3)" in text
        assert "1A-2 Explain: Not correct" not in text
        assert "Model answer" not in text                       # nothing is explained before it is marked
    assert "1 theory answer(s) have not been marked yet" in tutor.feedback_text(record, FeedbackLevel.MARKS_ONLY)


def test_once_marked_the_theory_answer_gets_a_verdict_and_the_note_goes(setup):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    tutor.set_decision("A1", "1A-2", False)
    text = tutor.feedback_text(tutor.record("A1"), FeedbackLevel.CORRECTNESS)
    assert "1A-2 Explain: Not correct (0/3)" in text and "Awaiting" not in text
    assert "have not been marked" not in text


# ---------- the mark sheet Notes column ----------

def test_the_notes_column_lists_warnings_and_unmarked_theory(setup, tmp_path):
    a, tutor, _ = setup
    tutor.add_submission(student(a, "A1"))
    tutor.add_submission(student(a, "B2"))                       # same box as A1 (same default uid)
    tutor.add_submission(student(a, "C3", uid="00FFFFFFFFFFFFFFFFFF0001"))
    tutor.set_decision("C3", "1A-2", True)
    path = str(tmp_path / "marks.csv")
    tutor.write_marksheet(path)
    rows = {r[0]: r for r in list(csv.reader(open(path, encoding="utf-8-sig")))[2:]}
    header = list(csv.reader(open(path, encoding="utf-8-sig")))[1]
    assert header[-1] == "Notes"
    assert "also used by: B2" in rows["A1"][-1] and "1 theory answer(s) not marked yet (1A-2)" in rows["A1"][-1]
    assert "also used by: A1" in rows["B2"][-1]
    assert rows["C3"][-1] == ""                                  # nothing to tell the tutor about C3
