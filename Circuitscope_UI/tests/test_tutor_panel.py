"""Automatic checks for the tutor grading screen (ui/tutor_panel.py). Run with:  python -m pytest"""

import csv
import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from core.models import (Assessment, AnswerMode, Item, ItemType, Part, Quantity, Question, Variable)
from core.session import Mode, StudentSession
from core.submission import DEFAULT_KEY, save_submission
from ui.main_window import MainWindow
from ui.tutor_panel import TutorPanel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def make_assessment() -> Assessment:
    fixed = Item(id="1A-1", description="Supply", marks=2, quantity=Quantity.VOLTAGE, probe_pos="P1",
                 probe_neg="P3", expected=3.0, tolerance=0.1)
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY,
                  model_answer="The resistors share the supply.")
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING,
                   quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    return Assessment(title="Practical 1", questions=[
        Question(id="1", title="Fixed", parts=[Part("1A", "m", [fixed, theory])]),
        Question(id="2", title="Div", default_mode=AnswerMode.VARYING,
                 variables=[Variable("Vs", "Vs", "V"), Variable("R1", "R1", "ohm"), Variable("R2", "R2", "ohm")],
                 parts=[Part("2A", "m", [varying])]),
    ])


def submission(assessment, number, uid="0012AB34CD56EF780011AABB", supply=3.0):
    s = StudentSession(assessment, Mode.FORMAL, number, uid)
    s.record_measurement("1A-1", supply)
    s.set_theory_answer("1A-2", f"Answer typed by {number}")
    for name, value in (("Vs", 3.0), ("R1", 1500.0), ("R2", 1000.0)):
        s.set_variable("2", name, value)
    s.record_measurement("2A-1", 1.2)
    return s.to_submission()


@pytest.fixture
def world(tmp_path):
    """The lecturer's file, three students' signed files, and a panel that has not opened anything."""
    a = make_assessment()
    assert a.validate() == []
    lecturer = str(tmp_path / "lecturer.json")
    a.save(lecturer)
    paths = {}
    for number, supply in (("A1", 3.0), ("B2", 1.0), ("C3", 3.0)):
        paths[number] = str(tmp_path / f"{number}.json")
        save_submission(submission(a, number, supply=supply), paths[number], DEFAULT_KEY)
    panel = TutorPanel(marking_folder=tmp_path / "marking")
    return a, lecturer, paths, panel, tmp_path


def loaded(world):
    a, lecturer, paths, panel, tmp_path = world
    assert panel.open_assessment(lecturer)
    panel.add_submissions(list(paths.values()))
    return panel


def column(table, col):
    return [table.item(r, col).text() for r in range(table.rowCount())]


# ---------- opening and loading ----------

def test_starts_locked(qapp, world):
    panel = world[3]
    assert panel.open_button.isEnabled() and not panel.add_button.isEnabled()
    assert not panel.marksheet_button.isEnabled() and not panel.feedback_button.isEnabled()


def test_adding_before_opening_the_assessment_is_explained(qapp, world):
    panel = world[3]
    panel.add_submissions(list(world[2].values()))
    assert "Open the assessment first" in panel.message_label.text()


def test_bad_assessment_files_are_explained(qapp, world):
    panel, tmp_path = world[3], world[4]
    assert not panel.open_assessment(str(tmp_path / "missing.json"))
    assert "does not exist" in panel.message_label.text()
    bad = make_assessment()
    bad.questions[0].parts[0].items[0].marks = -1
    bad.save(str(tmp_path / "bad.json"))
    assert not panel.open_assessment(str(tmp_path / "bad.json"))
    assert "Fix it in the Assessment creator" in panel.message_label.text()


def test_students_are_listed_with_marks_and_theory_still_to_mark(qapp, world):
    panel = loaded(world)
    assert column(panel.students, 0) == ["A1", "B2", "C3"]
    assert column(panel.students, 2) == ["5 / 8", "3 / 8", "5 / 8"]       # theory not counted yet
    assert column(panel.students, 3) == ["1", "1", "1"]
    assert panel.pending_label.text() == "Theory answers still to mark: 3"
    assert panel.marksheet_button.isEnabled() and "Loaded 3 new submission(s)" in panel.message_label.text()


def test_a_tampered_file_is_listed_in_red_and_the_others_load(qapp, world):
    a, lecturer, paths, panel, tmp_path = world
    doc = json.load(open(paths["B2"]))
    doc["payload"]["measurements"]["1A-1"] = 3.0
    json.dump(doc, open(paths["B2"], "w"))
    panel.open_assessment(lecturer)
    panel.add_submissions(list(paths.values()))
    assert column(panel.students, 0) == ["A1", "C3"]
    assert not panel.load_log.isHidden()
    assert "B2.json: not loaded" in panel.load_log.toPlainText()
    assert "signature does not match" in panel.load_log.toPlainText()
    assert "need attention" in panel.message_label.text()


def test_a_resubmission_is_reported(qapp, world):
    a, lecturer, paths, panel, tmp_path = world
    panel.open_assessment(lecturer)
    panel.add_submissions([paths["A1"]])
    newer = submission(a, "A1", supply=2.0)
    newer.created_utc = "2999-01-01T00:00:00+00:00"
    path = str(tmp_path / "A1_again.json")
    save_submission(newer, path, DEFAULT_KEY)
    panel.add_submissions([path])
    assert "replaced the earlier submission" in panel.load_log.toPlainText()
    assert column(panel.students, 0) == ["A1"]


def test_a_box_used_by_two_students_is_flagged(qapp, world):
    a, lecturer, paths, panel, tmp_path = world
    save_submission(submission(a, "D4", uid="AAAA"), str(tmp_path / "D4.json"), DEFAULT_KEY)
    save_submission(submission(a, "E5", uid="AAAA"), str(tmp_path / "E5.json"), DEFAULT_KEY)
    panel.open_assessment(lecturer)
    panel.add_submissions([str(tmp_path / "D4.json"), str(tmp_path / "E5.json"), paths["A1"]])
    warnings = dict(zip(column(panel.students, 0), column(panel.students, 4)))
    assert "also used by: E5" in warnings["D4"] and warnings["A1"] == ""


# ---------- looking at one student ----------

def test_selecting_a_student_shows_every_item_with_the_reason(qapp, world):
    panel = loaded(world)
    panel.select_student("B2")                                            # supply read 1.0 V
    assert column(panel.items, 0) == ["1A-1", "1A-2", "2A-1"]
    verdicts = dict(zip(column(panel.items, 0), column(panel.items, 5)))
    assert verdicts == {"1A-1": "✘ Not correct", "1A-2": "To mark", "2A-1": "✔ Correct"}
    details = dict(zip(column(panel.items, 0), column(panel.items, 6)))
    assert "expected 3" in details["1A-1"]
    assert column(panel.items, 2)[0] == "1" and column(panel.items, 3)[0] == "3"      # measured, expected


# ---------- marking theory ----------

def test_theory_answer_is_shown_beside_the_model_answer_and_can_be_marked(qapp, world):
    panel = loaded(world)
    panel.select_student("A1")
    assert panel.theory_box.isHidden()                                    # not a theory item yet
    panel.select_item("1A-1")
    assert panel.theory_box.isHidden()
    panel.select_item("1A-2")
    assert not panel.theory_box.isHidden()
    assert panel.answer_view.toPlainText() == "Answer typed by A1"
    assert panel.model_view.toPlainText() == "The resistors share the supply."
    panel.correct_button.click()
    assert panel.current_student == "A1" and panel.current_item == "1A-2"      # selection kept
    assert column(panel.students, 2)[0] == "8 / 8" and column(panel.students, 3)[0] == "-"
    verdicts = dict(zip(column(panel.items, 0), column(panel.items, 5)))
    assert verdicts["1A-2"] == "✔ Correct"
    panel.incorrect_button.click()
    assert column(panel.students, 2)[0] == "5 / 8"
    panel.clear_button.click()
    assert column(panel.students, 3)[0] == "1"
    assert panel.pending_label.text() == "Theory answers still to mark: 3"


def test_decisions_are_saved_and_restored_in_a_new_session(qapp, world):
    a, lecturer, paths, panel, tmp_path = world
    loaded(world)
    panel.select_student("C3"); panel.select_item("1A-2"); panel.correct_button.click()
    fresh = TutorPanel(marking_folder=tmp_path / "marking")                 # the app is restarted
    fresh.open_assessment(lecturer)
    fresh.add_submissions(list(paths.values()))
    marks = dict(zip(column(fresh.students, 0), column(fresh.students, 2)))
    assert marks["C3"] == "8 / 8" and marks["A1"] == "5 / 8"


# ---------- exporting ----------

def test_marksheet_is_written_and_warns_about_unmarked_theory(qapp, world):
    panel, tmp_path = loaded(world), world[4]
    out = str(tmp_path / "marks.csv")
    assert panel.export_marksheet(out)
    assert "3 theory answer(s) are not marked yet" in panel.message_label.text()
    rows = list(csv.reader(open(out, encoding="utf-8-sig")))[1:]       # [0] is the "sep=," hint line
    assert [r[0] for r in rows[1:]] == ["A1", "B2", "C3"]
    panel.select_student("A1"); panel.select_item("1A-2"); panel.correct_button.click()
    panel.select_student("B2"); panel.select_item("1A-2"); panel.incorrect_button.click()
    panel.select_student("C3"); panel.select_item("1A-2"); panel.correct_button.click()
    assert panel.export_marksheet(out)
    assert "not marked yet" not in panel.message_label.text()
    rows = {r[0]: r for r in list(csv.reader(open(out, encoding="utf-8-sig")))[1:]}
    assert rows["A1"][-4] == "8" and rows["B2"][-4] == "3"


def test_marksheet_to_an_unwritable_place_is_explained(qapp, world):
    panel, tmp_path = loaded(world), world[4]
    assert not panel.export_marksheet(str(tmp_path / "no_such_folder" / "marks.csv"))
    assert "Could not save the mark sheet" in panel.message_label.text()


def test_feedback_files_follow_the_chosen_level(qapp, world):
    panel, tmp_path = loaded(world), world[4]
    panel.select_student("B2"); panel.select_item("1A-2"); panel.incorrect_button.click()
    panel.level_box.setCurrentIndex(1)                                      # correct / incorrect
    folder = str(tmp_path / "feedback")
    assert panel.export_feedback(folder)
    text = open(tmp_path / "feedback" / "B2.txt", encoding="utf-8").read()
    assert "Not correct" in text and "Model answer" not in text and "expected" not in text.lower()
    panel.level_box.setCurrentIndex(2)                                      # full detail
    panel.export_feedback(folder)
    text = open(tmp_path / "feedback" / "B2.txt", encoding="utf-8").read()
    assert "Model answer: The resistors share the supply." in text and "expected 3" in text
    assert len(list((tmp_path / "feedback").glob("*.txt"))) == 3


def test_exports_need_submissions(qapp, world):
    panel = world[3]
    panel.open_assessment(world[1])
    assert not panel.export_marksheet("x.csv") and "no submissions" in panel.message_label.text()
    assert not panel.export_feedback("x") and "no submissions" in panel.message_label.text()


# ---------- in the whole window ----------

def test_window_has_a_tutor_tab_without_the_box_panel(qapp, world):
    a, lecturer, paths, _panel, tmp_path = world
    window = MainWindow(calibration_folder=tmp_path / "cal", progress_folder=tmp_path / "prog",
                        marking_folder=tmp_path / "marking")
    assert window.tabs.tabText(2) == "Tutor grading"
    window.tabs.setCurrentWidget(window.tutor)
    assert window.connection.isHidden()
    window.tabs.setCurrentWidget(window.student)
    assert not window.connection.isHidden()
    window.tutor.open_assessment(lecturer)
    window.tutor.add_submissions([paths["A1"]])
    assert column(window.tutor.students, 0) == ["A1"]
