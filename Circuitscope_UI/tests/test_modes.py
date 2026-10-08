"""Automatic checks for Stage 17: the lecturer-set mode, "Start again", and practice feedback.
Run with:  python -m pytest"""

import json
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.models import (Assessment, Item, ItemType, ModePolicy, Part, PracticeFeedback, Quantity,
                         Question)
from core.session import Mode, SessionError, StudentSession
from core.submission import load_submission
from hardware.demo import make_demo_device
from ui.assessment_editor import AssessmentEditor
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


def make_assessment(policy=ModePolicy.STUDENT_CHOOSES, feedback=PracticeFeedback.DETAILED) -> Assessment:
    volts = Item(id="1A-1", description="Supply voltage", marks=2, quantity=Quantity.VOLTAGE,
                 probe_pos="P1", probe_neg="P3", expected=3.0, tolerance=0.1)
    amps = Item(id="1A-2", description="Current", marks=2, quantity=Quantity.CURRENT,
                probe_pos="I1", expected=4.7, tolerance=0.2)
    theory = Item(id="1A-3", description="Explain", marks=1, item_type=ItemType.THEORY, prompt="Why?")
    return Assessment(title="Modes", questions=[Question(id="1", title="Q", parts=[Part("1A", "m", [volts, amps, theory])])],
                      assessment_id="modes-test", mode_policy=policy, practice_feedback=feedback)


# ---------- the settings in the file ----------

def test_defaults_are_student_chooses_and_detailed():
    a = make_assessment()
    assert a.mode_policy == ModePolicy.STUDENT_CHOOSES and a.practice_feedback == PracticeFeedback.DETAILED


def test_the_settings_survive_saving_and_loading(tmp_path):
    make_assessment(ModePolicy.FORMAL_ONLY, PracticeFeedback.CORRECT_ONLY).save(str(tmp_path / "a.json"))
    back = Assessment.load(str(tmp_path / "a.json"))
    assert back.mode_policy == ModePolicy.FORMAL_ONLY and back.practice_feedback == PracticeFeedback.CORRECT_ONLY


def test_an_old_file_without_the_settings_gets_the_defaults(tmp_path):
    path = tmp_path / "a.json"
    make_assessment().save(str(path))
    data = json.loads(path.read_text())
    del data["mode_policy"], data["practice_feedback"]
    path.write_text(json.dumps(data))
    back = Assessment.load(str(path))
    assert back.mode_policy == ModePolicy.STUDENT_CHOOSES and back.practice_feedback == PracticeFeedback.DETAILED


def test_a_nonsense_setting_falls_back_to_the_default(tmp_path):
    path = tmp_path / "a.json"
    make_assessment().save(str(path))
    data = json.loads(path.read_text())
    data["mode_policy"], data["practice_feedback"] = "banana", 7
    path.write_text(json.dumps(data))
    back = Assessment.load(str(path))
    assert back.mode_policy == ModePolicy.STUDENT_CHOOSES and back.practice_feedback == PracticeFeedback.DETAILED


def test_the_settings_do_not_change_the_fingerprint():
    base = make_assessment().content_hash()
    assert make_assessment(ModePolicy.FORMAL_ONLY, PracticeFeedback.CORRECT_ONLY).content_hash() == base


# ---------- the session ----------

def test_formal_only_overrides_a_practice_request():
    s = StudentSession(make_assessment(ModePolicy.FORMAL_ONLY), Mode.PRACTICE)
    assert s.mode == Mode.FORMAL and s.forced_mode() == Mode.FORMAL


def test_practice_only_overrides_a_formal_request():
    s = StudentSession(make_assessment(ModePolicy.PRACTICE_ONLY), Mode.FORMAL)
    assert s.mode == Mode.PRACTICE


def test_the_forced_mode_cannot_be_changed_but_a_free_one_can():
    forced = StudentSession(make_assessment(ModePolicy.FORMAL_ONLY))
    with pytest.raises(SessionError, match="only be done in formal mode"):
        forced.set_mode(Mode.PRACTICE)
    free = StudentSession(make_assessment())
    free.set_mode(Mode.FORMAL)
    free.set_mode(Mode.PRACTICE)
    assert free.forced_mode() is None


def test_clear_all_removes_the_work_but_keeps_the_student_number():
    s = StudentSession(make_assessment(), student_number="SMTCHR001")
    s.record_measurement("1A-1", 3.0)
    s.set_theory_answer("1A-3", "because")
    assert s.has_work()
    s.clear_all()
    assert not s.has_work() and s.student_number == "SMTCHR001"
    assert s.progress()[0] == 0


def test_a_draft_made_in_a_mode_the_assessment_no_longer_allows_is_refused(tmp_path):
    path = str(tmp_path / "draft.json")
    s = StudentSession(make_assessment(), Mode.PRACTICE)
    s.record_measurement("1A-1", 3.0)
    s.save_progress(path)
    with pytest.raises(SessionError, match="can only be done in formal mode"):
        StudentSession(make_assessment(ModePolicy.FORMAL_ONLY)).load_progress(path)


def test_detailed_feedback_shows_measured_and_expected_with_units():
    s = StudentSession(make_assessment())
    s.record_measurement("1A-1", 2.1)
    s.record_measurement("1A-2", 9.0)
    report = s.practice_report()
    volts, ok = s.practice_item_feedback(report, "1A-1")
    assert not ok and "Measured 2.1 V but expected 3 V" in volts and "(allowed" in volts
    amps, _ok = s.practice_item_feedback(report, "1A-2")
    assert "Measured 9 mA but expected 4.7 mA" in amps


def test_correct_only_feedback_hides_the_numbers():
    s = StudentSession(make_assessment(feedback=PracticeFeedback.CORRECT_ONLY))
    s.record_measurement("1A-1", 2.1)
    s.record_measurement("1A-2", 4.7)
    report = s.practice_report()
    text, ok = s.practice_item_feedback(report, "1A-1")
    assert not ok and text.startswith("Not correct") and "2.1" not in text and "expected" not in text
    right, ok = s.practice_item_feedback(report, "1A-2")
    assert ok and right.startswith("Correct")


# ---------- the creator ----------

@pytest.fixture
def editor(qapp):
    e = AssessmentEditor()
    e.confirm = lambda _q: True
    return e


def ready_editor(editor):
    editor.assessment = make_assessment()
    editor.assessment.questions[0].parts[0].items[2].model_answer = "secret"
    editor._rebuild_tree() if hasattr(editor, "_rebuild_tree") else None
    return editor


def test_export_writes_the_chosen_policy_only_into_the_student_copy(editor, tmp_path):
    ready_editor(editor)
    path = str(tmp_path / "s.json")
    assert editor.export_for_students(path, ModePolicy.FORMAL_ONLY)
    copy = Assessment.load(path)
    assert copy.mode_policy == ModePolicy.FORMAL_ONLY
    assert editor.assessment.mode_policy == ModePolicy.STUDENT_CHOOSES            # the working copy is unchanged
    assert copy.content_hash() == editor.assessment.content_hash()               # so submissions still match
    assert "formal only" in editor.status_label.text()


def test_export_without_a_choice_keeps_the_assessments_own_policy(editor, tmp_path):
    ready_editor(editor)
    editor.assessment.mode_policy = ModePolicy.PRACTICE_ONLY
    path = str(tmp_path / "s.json")
    assert editor.export_for_students(path)
    assert Assessment.load(path).mode_policy == ModePolicy.PRACTICE_ONLY


def test_cancelling_the_mode_choice_exports_nothing(editor, tmp_path):
    ready_editor(editor)
    editor.choose_mode_policy = lambda: None
    editor._export_clicked()                           # no file dialog may open: it would hang the test
    assert list(tmp_path.iterdir()) == []


def test_the_practice_feedback_setting_is_edited_on_the_assessment_page(editor):
    editor.tree.setCurrentItem(editor.tree.topLevelItem(0))
    editor.feedback_box.setCurrentIndex(editor.feedback_box.findData("correct_only"))
    assert editor.assessment.practice_feedback == PracticeFeedback.CORRECT_ONLY
    assert editor.dirty


# ---------- the student screen ----------

def new_panel(tmp_path, policy=ModePolicy.STUDENT_CHOOSES, feedback=PracticeFeedback.DETAILED, mode_index=0):
    path = tmp_path / "a.json"
    make_assessment(policy, feedback).save(str(path))
    panel = StudentPanel(progress_folder=tmp_path / "progress")
    panel.confirm = lambda _q: True
    device = make_demo_device()
    device.connect()
    panel.set_device(device)
    panel.mode_box.setCurrentIndex(mode_index)
    assert panel.open_assessment(str(path))
    return panel


def measure(panel, item_id):
    panel.select_item(item_id)
    panel.measure_button.click()
    wait_until(lambda: not panel._busy)


def finish(panel):
    panel.number_edit.setText("SMTCHR001")
    measure(panel, "1A-1")
    measure(panel, "1A-2")
    panel.select_item("1A-3")
    panel.answer_edit.setPlainText("Because.")


def test_a_free_assessment_shows_the_mode_box(qapp, tmp_path):
    panel = new_panel(tmp_path)
    assert not panel.mode_box.isHidden() and panel.mode_fixed_label.isHidden()


def test_formal_only_hides_the_box_and_the_student_can_submit(qapp, tmp_path):
    panel = new_panel(tmp_path, ModePolicy.FORMAL_ONLY, mode_index=0)     # asked for practice, overridden
    assert panel.mode_box.isHidden() and not panel.mode_fixed_label.isHidden()
    assert "Formal" in panel.mode_fixed_label.text() and "set by your lecturer" in panel.mode_fixed_label.text()
    assert panel.session.mode == Mode.FORMAL
    finish(panel)
    assert panel.problems_label.text() == "" and panel.submit_button.isEnabled()
    assert "not shown" in panel.marks_label.text()
    out = str(tmp_path / "sub.json")
    assert panel.submit(out) and load_submission(out).student_number == "SMTCHR001"


def test_practice_only_shows_marks_and_cannot_submit(qapp, tmp_path):
    panel = new_panel(tmp_path, ModePolicy.PRACTICE_ONLY, mode_index=1)   # asked for formal, overridden
    assert panel.session.mode == Mode.PRACTICE and "Practice" in panel.mode_fixed_label.text()
    finish(panel)
    assert "Marks so far" in panel.marks_label.text() and not panel.submit_button.isEnabled()


def test_start_again_is_disabled_until_there_is_work(qapp, tmp_path):
    panel = new_panel(tmp_path)
    assert not panel.clear_button.isEnabled()
    measure(panel, "1A-1")
    assert panel.clear_button.isEnabled()


def test_the_mode_locks_with_work_and_start_again_unlocks_it(qapp, tmp_path):
    panel = new_panel(tmp_path, mode_index=0)
    measure(panel, "1A-1")
    assert not panel.mode_box.isEnabled() and "Start again" in panel.mode_box.toolTip()
    panel.clear_button.click()
    assert panel.mode_box.isEnabled() and not panel.session.has_work()
    assert panel.reading_label.text() == "-" and panel.feedback_label.text() == ""
    assert "cleared" in panel.message_label.text()


def test_practice_then_start_again_then_formal_can_be_submitted(qapp, tmp_path):
    panel = new_panel(tmp_path, mode_index=0)
    finish(panel)
    assert not panel.submit_button.isEnabled()                 # practice cannot be submitted
    panel.clear_button.click()
    panel.mode_box.setCurrentIndex(1)                          # now formal
    assert panel.session.mode == Mode.FORMAL
    assert panel.number_edit.text() == "SMTCHR001"             # the number was kept
    finish(panel)
    assert panel.submit_button.isEnabled() and panel.submit(str(tmp_path / "sub.json"))


def test_formal_then_start_again_then_practice_works_too(qapp, tmp_path):
    panel = new_panel(tmp_path, mode_index=1)
    finish(panel)
    panel.clear_button.click()
    panel.mode_box.setCurrentIndex(0)
    assert panel.session.mode == Mode.PRACTICE
    measure(panel, "1A-1")
    assert "Correct" in panel.feedback_label.text()


def test_declining_the_question_keeps_the_work(qapp, tmp_path):
    panel = new_panel(tmp_path)
    measure(panel, "1A-1")
    panel.confirm = lambda _q: False
    panel.clear_button.click()
    assert panel.session.has_work() and not panel.mode_box.isEnabled()


def test_start_again_is_remembered_by_the_autosave(qapp, tmp_path):
    panel = new_panel(tmp_path)
    measure(panel, "1A-1")
    panel.clear_button.click()
    again = StudentPanel(progress_folder=tmp_path / "progress")
    again.set_device(panel.device)
    again.open_assessment(str(tmp_path / "a.json"))
    assert not again.session.has_work()


def test_a_practice_draft_does_not_block_a_formal_only_file(qapp, tmp_path):
    panel = new_panel(tmp_path, mode_index=0)
    measure(panel, "1A-1")                                     # a practice draft now exists
    # The lecturer re-exports the same practical (same id, same fingerprint) as formal-only:
    make_assessment(ModePolicy.FORMAL_ONLY).save(str(tmp_path / "a.json"))
    fresh = StudentPanel(progress_folder=tmp_path / "progress")
    fresh.set_device(panel.device)
    assert fresh.open_assessment(str(tmp_path / "a.json"))
    assert fresh.session.mode == Mode.FORMAL and not fresh.session.has_work()
    assert "was done in practice mode" in fresh.message_label.text()


def test_detailed_feedback_in_the_panel_shows_the_numbers(qapp, tmp_path):
    panel = new_panel(tmp_path)
    panel.device.port.board.set_dc("P1", 2.1)
    measure(panel, "1A-1")
    assert "Measured 2.1" in panel.feedback_label.text() and "expected 3 V" in panel.feedback_label.text()


def test_correct_only_feedback_in_the_panel_hides_the_numbers(qapp, tmp_path):
    panel = new_panel(tmp_path, feedback=PracticeFeedback.CORRECT_ONLY)
    panel.device.port.board.set_dc("P1", 2.1)
    measure(panel, "1A-1")
    text = panel.feedback_label.text()
    assert text.startswith("Not correct") and "2.1" not in text and "expected" not in text
