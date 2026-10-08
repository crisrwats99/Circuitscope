"""Automatic checks for the assessment creator screen (ui/assessment_editor.py).
Run with:  python -m pytest

Each test drives the screen like a lecturer would: it presses the buttons and types
into the fields through code (Qt draws into memory, see conftest.py), then checks
BOTH what the screen shows and what the underlying Assessment object contains.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from core.grading import Status, grade_item
from core.models import AnswerMode, Assessment, ItemType, ScoringMode, ToleranceMode
from ui.assessment_editor import AssessmentEditor
from ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp):
    e = AssessmentEditor()
    e.confirm = lambda _question: True            # never show a real dialog in tests
    return e


def select(editor, obj):
    """Click the tree node that holds `obj`."""
    for key, (_kind, o, _q, _p) in editor._objects.items():
        if o is obj:
            editor.tree.setCurrentItem(editor._tree_items[key])
            return
    raise AssertionError("not in the tree")


def build_basic(editor):
    """One question, one part, one item. Returns (question, part, item)."""
    editor.add_question()
    q = editor.assessment.questions[-1]
    editor.add_part()
    p = q.parts[-1]
    editor.add_item()
    return q, p, p.items[-1]


# ---------- starting state ----------

def test_starts_with_a_blank_assessment_and_says_what_is_missing(editor):
    assert editor.assessment.title == "Untitled practical"
    assert editor.assessment.questions == []
    assert any("no questions" in editor.problems_list.item(i).text()
               for i in range(editor.problems_list.count()))
    assert not editor.add_part_button.isEnabled()          # nothing to add a part to yet


def test_editing_the_title_updates_the_assessment_and_the_tree(editor):
    editor.title_edit.setText("Practical 3 - Filters")
    assert editor.assessment.title == "Practical 3 - Filters"
    assert "Practical 3 - Filters" in editor.tree.topLevelItem(0).text(0)
    assert editor.dirty


# ---------- adding things ----------

def test_adding_question_part_and_item_gives_unique_default_ids(editor):
    q, p, item = build_basic(editor)
    assert (q.id, p.id, item.id) == ("1", "1A", "1A-1")
    editor.add_item()                                       # a second item in the same part
    editor.add_part()                                       # a second part
    editor.add_question()                                   # a second question
    assert [i.id for i in p.items] == ["1A-1", "1A-2"]
    assert [x.id for x in q.parts] == ["1A", "1B"]
    assert [x.id for x in editor.assessment.questions] == ["1", "2"]


def test_buttons_follow_what_is_selected(editor):
    q, p, item = build_basic(editor)
    select(editor, q)
    assert editor.add_part_button.isEnabled() and not editor.add_item_button.isEnabled()
    select(editor, item)
    assert editor.add_item_button.isEnabled()
    select(editor, editor.assessment)
    assert not editor.add_part_button.isEnabled() and not editor.remove_button.isEnabled()


def test_a_new_item_asks_for_its_expected_value(editor):
    build_basic(editor)
    problems = [editor.problems_list.item(i).text() for i in range(editor.problems_list.count())]
    assert any("has no expected value" in p for p in problems)
    assert "has no expected value" in editor.i_problem_label.text()      # also shown under the fields


# ---------- editing an item ----------

def test_item_fields_are_written_to_the_model(editor):
    _q, _p, item = build_basic(editor)
    editor.i_desc_edit.setText("Voltage across R2")
    editor.i_marks_spin.setValue(2.5)
    editor.i_expected_edit.setText("3.3")
    editor.i_tol_mode_box.setCurrentIndex(editor.i_tol_mode_box.findData("percent"))
    editor.i_tol_spin.setValue(5)
    editor.i_pos_box.setCurrentIndex(editor.i_pos_box.findData("P2"))
    assert (item.description, item.marks, item.expected) == ("Voltage across R2", 2.5, 3.3)
    assert (item.tolerance_mode, item.tolerance, item.probe_pos) == (ToleranceMode.PERCENT, 5.0, "P2")
    assert editor.assessment.validate() == []
    assert "No problems" in editor.problems_label.text()


def test_a_number_that_is_not_a_number_is_flagged(editor):
    _q, _p, item = build_basic(editor)
    editor.i_expected_edit.setText("3.3x")
    assert item.expected is None
    assert "b00020" in editor.i_expected_edit.styleSheet()
    listed = [editor.problems_list.item(i).text() for i in range(editor.problems_list.count())]
    mine = [p for p in listed if p.startswith(f"Item {item.id}:")]
    assert len(mine) == 1                                              # one specific message, not two
    assert "'3.3x' has letters in it" in mine[0] and "Type only the number" in mine[0]
    assert "Type only the number" in editor.i_problem_label.text()     # also under the fields


def test_tree_labels_show_marks_and_follow_edits(editor):
    q, _p, item = build_basic(editor)
    editor.i_marks_spin.setValue(4)
    assert "(4)" in editor._tree_items[[k for k, v in editor._objects.items() if v[1] is item][0]].text(0)
    assert "4 marks" in editor.tree.topLevelItem(0).text(0)


# ---------- answer types ----------

def test_switching_to_varying_hides_expected_and_clears_it(editor):
    _q, _p, item = build_basic(editor)
    editor.i_expected_edit.setText("3.3")
    editor.i_mode_box.setCurrentIndex(editor.i_mode_box.findData("varying"))
    assert item.mode == AnswerMode.VARYING and item.expected is None
    assert editor.i_expected_edit.text() == ""
    assert not editor.i_expected_edit.isVisibleTo(editor.item_page)
    assert editor.i_formula_edit.isVisibleTo(editor.item_page)


def test_formula_mistakes_appear_live_and_clear_when_fixed(editor):
    q, _p, item = build_basic(editor)
    select(editor, q)
    editor.add_variable()                                    # R1
    editor.add_variable()                                    # R2
    select(editor, item)
    editor.i_mode_box.setCurrentIndex(editor.i_mode_box.findData("varying"))
    editor.i_formula_edit.setText("Vs * R2 / (R1 + R2)")     # Vs is not defined yet
    assert "Vs" in editor.i_problem_label.text()
    editor.i_formula_edit.setText("5 * R2 / (R1 + R2)")
    assert editor.assessment.validate() == []
    assert editor.i_problem_label.text() == ""
    editor.i_formula_edit.setText("R2 /* (")                 # broken syntax
    assert "formula problem" in editor.i_problem_label.text()


def test_the_item_follows_the_questions_default_answer_type(editor):
    q, _p, item = build_basic(editor)
    select(editor, q)
    editor.q_mode_box.setCurrentIndex(editor.q_mode_box.findData("varying"))
    assert q.default_mode == AnswerMode.VARYING
    select(editor, item)
    assert editor.i_mode_box.itemText(0) == "Same as question (Varying (formula))"
    assert editor.i_formula_edit.isVisibleTo(editor.item_page)


def test_waveform_item_gets_a_capture_setup_and_a_reference_for_gain(editor):
    _q, _p, item = build_basic(editor)
    editor.i_mode_box.setCurrentIndex(editor.i_mode_box.findData("waveform"))
    assert item.mode == AnswerMode.WAVEFORM and item.waveform is not None
    assert item.waveform.feature.value == "frequency" and item.waveform.channel_ref is None
    assert editor.wave_group.isVisibleTo(editor.item_page)
    assert not editor.w_ref_box.isVisibleTo(editor.wave_group)       # frequency needs no reference

    editor.w_feature_box.setCurrentIndex(editor.w_feature_box.findData("gain"))
    assert editor.w_ref_box.isVisibleTo(editor.wave_group)
    assert any("needs a reference channel" in editor.problems_list.item(i).text()
               for i in range(editor.problems_list.count()))
    editor.w_ref_box.setCurrentIndex(editor.w_ref_box.findData("P4"))
    assert item.waveform.channel_ref == "P4"
    editor.w_rate_spin.setValue(20_000)
    editor.w_points_spin.setValue(1000)
    editor.i_expected_edit.setText("2")
    assert (item.waveform.sample_rate_hz, item.waveform.num_points) == (20_000, 1000)
    assert editor.assessment.validate() == []


def test_too_many_waveform_points_is_reported(editor):
    build_basic(editor)
    editor.i_mode_box.setCurrentIndex(editor.i_mode_box.findData("waveform"))
    editor.i_expected_edit.setText("1000")
    editor.w_points_spin.setValue(50_000)
    assert any("more than the box can store" in editor.problems_list.item(i).text()
               for i in range(editor.problems_list.count()))


def test_theory_item_hides_the_measurement_fields_and_needs_nothing_else(editor):
    _q, _p, item = build_basic(editor)
    editor.i_type_box.setCurrentIndex(editor.i_type_box.findData("theory"))
    assert item.item_type == ItemType.THEORY and item.probe_pos is None and item.expected is None
    for widget in (editor.i_pos_box, editor.i_expected_edit, editor.i_tol_spin):
        assert not widget.isVisibleTo(editor.item_page)
    assert editor.assessment.validate() == []
    assert "[theory]" in editor._tree_items[[k for k, v in editor._objects.items() if v[1] is item][0]].text(0)


# ---------- questions and variables ----------

def test_question_settings_are_written_to_the_model(editor):
    q, _p, _item = build_basic(editor)
    select(editor, q)
    editor.q_title_edit.setText("Voltage divider")
    editor.q_scoring_box.setCurrentIndex(editor.q_scoring_box.findData("all_or_nothing"))
    assert q.title == "Voltage divider" and q.scoring == ScoringMode.ALL_OR_NOTHING


def test_variables_can_be_added_renamed_measured_and_removed(editor):
    q, _p, _item = build_basic(editor)
    select(editor, q)
    editor.add_variable()
    editor.add_variable()
    assert [v.name for v in q.variables] == ["R1", "R2"]
    editor.var_table.cellWidget(0, 0).setText("Vs")
    editor.var_table.cellWidget(0, 1).setText("Supply voltage")
    assert q.variables[0].name == "Vs" and q.variables[0].label == "Supply voltage"
    # A measured variable switches on its quantity / probe columns.
    assert not editor.var_table.cellWidget(0, 4).isEnabled()
    source = editor.var_table.cellWidget(0, 3)
    source.setCurrentIndex(source.findData("measured"))
    assert editor.var_table.cellWidget(0, 4).isEnabled()
    assert q.variables[0].quantity is not None and q.variables[0].probe_pos == "P1"
    editor.var_table.selectRow(1)
    editor.remove_variable()
    assert [v.name for v in q.variables] == ["Vs"]


def test_reserved_and_duplicate_variable_names_are_reported(editor):
    q, _p, _item = build_basic(editor)
    select(editor, q)
    editor.add_variable()
    editor.add_variable()
    editor.var_table.cellWidget(0, 0).setText("pi")
    editor.var_table.cellWidget(1, 0).setText("pi")
    text = " ".join(editor.problems_list.item(i).text() for i in range(editor.problems_list.count()))
    assert "reserved" in text and "used 2 times" in text


# ---------- removing and moving ----------

def test_remove_asks_first_when_it_would_delete_things(editor):
    q, p, item = build_basic(editor)
    asked = []
    editor.confirm = lambda text: asked.append(text) or False
    select(editor, q)
    editor.remove_selected()
    assert asked and editor.assessment.questions == [q]                 # refused: nothing removed
    editor.confirm = lambda text: True
    editor.remove_selected()
    assert editor.assessment.questions == []


def test_remove_an_item_selects_a_neighbour(editor):
    _q, p, first = build_basic(editor)
    editor.add_item()
    second = p.items[-1]
    select(editor, second)
    editor.remove_selected()
    assert p.items == [first]
    assert editor._current[1] is first


def test_move_up_and_down_reorders_siblings(editor):
    _q, p, first = build_basic(editor)
    editor.add_item()
    second = p.items[-1]
    select(editor, second)
    editor.move_selected(-1)
    assert p.items == [second, first]
    editor.move_selected(+1)
    assert p.items == [first, second]
    editor.move_selected(+1)                                            # already last: nothing happens
    assert p.items == [first, second]


def test_duplicate_ids_are_reported(editor):
    q, p, first = build_basic(editor)
    editor.add_item()
    editor.i_id_edit.setText(first.id)
    assert any("more than one item" in editor.problems_list.item(i).text()
               for i in range(editor.problems_list.count()))


# ---------- files ----------

def test_save_and_open_round_trip(editor, tmp_path):
    q, p, item = build_basic(editor)
    editor.i_desc_edit.setText("Divider output")
    editor.i_expected_edit.setText("2.5")
    editor.i_tol_spin.setValue(0.1)
    path = str(tmp_path / "practical.json")
    assert editor.save_file(path)
    assert not editor.dirty and editor.path == path

    other = AssessmentEditor()
    assert other.open_file(path)
    loaded = other.assessment
    assert loaded.assessment_id == editor.assessment.assessment_id
    assert loaded.content_hash() == editor.assessment.content_hash()
    assert not other.dirty
    assert "Opened" in other.status_label.text()


def test_a_bad_file_is_refused_and_the_current_work_is_kept(editor, tmp_path):
    build_basic(editor)
    before = editor.assessment
    broken = tmp_path / "broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    assert not editor.open_file(str(broken))
    assert editor.assessment is before
    assert "Could not open" in editor.status_label.text()
    assert not editor.open_file(str(tmp_path / "missing.json"))
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"hello": 1}', encoding="utf-8")                  # valid JSON, not an assessment
    assert not editor.open_file(str(wrong))
    assert editor.assessment is before


def test_saving_to_an_unwritable_place_shows_an_error(editor, tmp_path):
    build_basic(editor)
    assert not editor.save_file(str(tmp_path / "no_such_folder" / "x.json"))
    assert "Could not save" in editor.status_label.text() and editor.dirty


def test_new_and_open_protect_unsaved_work(editor, tmp_path):
    build_basic(editor)
    editor.confirm = lambda _text: False                                # the user says "keep my work"
    assert not editor.new_assessment()
    assert editor.assessment.questions
    editor.confirm = lambda _text: True
    assert editor.new_assessment()
    assert editor.assessment.questions == [] and not editor.dirty


def test_a_saved_draft_with_problems_can_be_reopened(editor, tmp_path):
    build_basic(editor)                                                 # has a problem: no expected value
    path = str(tmp_path / "draft.json")
    assert editor.save_file(path)
    assert "problem" in editor.status_label.text()
    other = AssessmentEditor()
    other.open_file(path)
    assert other.problems_list.count() >= 1


# ---------- the whole point: what the lecturer builds can be graded ----------

def test_an_assessment_built_on_screen_grades_correctly(editor, tmp_path):
    q, _p, item = build_basic(editor)
    select(editor, q)
    editor.add_variable()
    editor.add_variable()                                               # R1 and R2
    select(editor, item)
    editor.i_desc_edit.setText("Divider output")
    editor.i_mode_box.setCurrentIndex(editor.i_mode_box.findData("varying"))
    editor.i_formula_edit.setText("5 * R2 / (R1 + R2)")
    editor.i_tol_mode_box.setCurrentIndex(editor.i_tol_mode_box.findData("percent"))
    editor.i_tol_spin.setValue(2)
    assert editor.assessment.validate() == []

    path = str(tmp_path / "p.json")
    editor.save_file(path)
    loaded = Assessment.load(path)
    graded = loaded.questions[0].parts[0].items[0]
    values = {"R1": 1000, "R2": 1000}                                   # expected 2.5 V
    assert grade_item(graded, 2.52, values).status == Status.CORRECT
    assert grade_item(graded, 3.0, values).status == Status.INCORRECT


# ---------- inside the whole window ----------

def test_the_window_has_the_creator_tab_and_protects_unsaved_work(qapp, tmp_path):
    window = MainWindow(calibration_folder=tmp_path)
    assert window.tabs.tabText(0) == "Assessment creator"
    window.creator.add_question()
    window.creator.confirm = lambda _text: False                        # user cancels the close
    from PySide6.QtGui import QCloseEvent
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    window.creator.confirm = lambda _text: True
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_the_connection_panel_is_hidden_only_on_the_creator_tab(qapp, tmp_path):
    window = MainWindow(calibration_folder=tmp_path)
    window.show()
    qapp.processEvents()
    assert window.tabs.currentWidget() is window.creator
    assert not window.connection.isVisible()
    window.tabs.setCurrentWidget(window.quick.parentWidget())      # (the quick-measure tab holds two panels)
    assert window.connection.isVisible()
    window.tabs.setCurrentWidget(window.creator)
    assert not window.connection.isVisible()
    window.creator.dirty = False
    window.close()


# ---------- informative messages for numbers typed into a number box ----------

from ui.assessment_editor import describe_load_error, parse_number


@pytest.mark.parametrize("text, must_contain", [
    ("abc", "has letters in it"),
    ("3.3V", "Type only the number"),
    ("3,3", "uses a comma"),
    ("3,3", "3.3"),
    ("1..2", "is not a number"),
    ("--5", "is not a number"),
    ("inf", "finite"),
    ("nan", "finite"),
])
def test_text_that_is_not_a_number_is_explained(text, must_contain):
    value, why = parse_number(text)
    assert value is None and must_contain in why
    assert f"'{text}'" in why                                          # quotes back what was typed


@pytest.mark.parametrize("text, expected", [("3.3", 3.3), (" -0.5 ", -0.5), ("1e3", 1000.0), ("5", 5.0)])
def test_valid_numbers_are_accepted(text, expected):
    assert parse_number(text) == (expected, None)


def test_an_empty_box_is_not_an_error_by_itself():
    assert parse_number("  ") == (None, None)


def test_the_typed_text_is_remembered_when_you_come_back_to_the_item(editor):
    q, p, item = build_basic(editor)
    editor.i_expected_edit.setText("3,3")
    editor.add_item()                                                  # selects another item
    select(editor, item)
    assert editor.i_expected_edit.text() == "3,3"                      # not silently blanked
    assert "uses a comma" in editor.i_expected_edit.toolTip()
    assert any("uses a comma" in editor.problems_list.item(i).text()
               for i in range(editor.problems_list.count()))
    editor.i_expected_edit.setText("3.3")                              # fixing it clears the problem
    assert item.expected == 3.3
    assert not any("comma" in editor.problems_list.item(i).text()
                   for i in range(editor.problems_list.count()))


def test_removing_an_item_forgets_its_bad_text(editor):
    _q, _p, item = build_basic(editor)
    editor.i_expected_edit.setText("abc")
    editor.remove_selected()
    assert editor._bad_expected == {} or all(v[0] is not item for v in editor._bad_expected.values())
    assert not any("expected value" in editor.problems_list.item(i).text()
                   for i in range(editor.problems_list.count()))


# ---------- informative messages for files ----------

def test_open_errors_say_what_is_wrong_with_the_file(editor, tmp_path):
    missing = editor.open_file(str(tmp_path / "nope.json"))
    assert not missing and "does not exist" in editor.status_label.text()

    broken = tmp_path / "broken.json"
    broken.write_text('{"title": "x",\n "questions": [', encoding="utf-8")
    editor.open_file(str(broken))
    assert "line 2" in editor.status_label.text() and "JSON" in editor.status_label.text()

    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"hello": 1}', encoding="utf-8")
    editor.open_file(str(wrong))
    assert "required field 'title' is missing" in editor.status_label.text()

    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    editor.open_file(str(listy))
    assert "layout of an assessment file" in editor.status_label.text()

    odd = tmp_path / "odd.json"
    odd.write_text('{"title": "x", "questions": [{"id": "1", "title": "q", "default_mode": "banana", "parts": []}]}',
                   encoding="utf-8")
    editor.open_file(str(odd))
    assert "does not understand" in editor.status_label.text()


def test_save_errors_name_the_place_and_the_fix(editor, tmp_path):
    build_basic(editor)
    editor.save_file(str(tmp_path / "missing_folder" / "x.json"))
    text = editor.status_label.text()
    assert "missing_folder" in text and "folder exists" in text


# ---------- question text, pictures and model answers ----------

from PySide6.QtGui import QColor, QImage

from core.models import MAX_IMAGE_BYTES, check_image


def small_picture(width=60, height=40, color="#2a7"):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor(color))
    return image


def test_a_theory_item_can_hold_question_text_a_picture_and_a_model_answer(editor, tmp_path):
    _q, _p, item = build_basic(editor)
    editor.i_type_box.setCurrentIndex(editor.i_type_box.findData("theory"))
    assert editor.i_prompt_edit.isVisibleTo(editor.item_page)
    assert editor.i_model_edit.isVisibleTo(editor.item_page)           # model answer only for theory
    editor.i_prompt_edit.setPlainText("Explain why the output voltage falls when a load is connected.")
    editor.i_model_edit.setPlainText("The load is in parallel with R2, lowering the lower resistance.")
    assert editor.i_image.set_qimage(small_picture())
    assert item.prompt.startswith("Explain why") and item.model_answer.startswith("The load")
    assert item.image and check_image(item.image) is None
    assert editor.assessment.validate() == []

    path = str(tmp_path / "t.json")
    editor.save_file(path)
    loaded = Assessment.load(path).questions[0].parts[0].items[0]
    assert loaded.prompt == item.prompt and loaded.image == item.image and loaded.model_answer == item.model_answer


def test_model_answer_is_hidden_and_cleared_for_measurement_items(editor):
    _q, _p, item = build_basic(editor)
    editor.i_type_box.setCurrentIndex(editor.i_type_box.findData("theory"))
    editor.i_model_edit.setPlainText("secret marking notes")
    editor.i_type_box.setCurrentIndex(editor.i_type_box.findData("measurement"))
    assert item.model_answer == "" and not editor.i_model_edit.isVisibleTo(editor.item_page)


def test_pasting_a_picture_into_the_question_text_attaches_it(editor):
    from PySide6.QtCore import QMimeData
    _q, _p, item = build_basic(editor)
    mime = QMimeData()
    mime.setImageData(small_picture())
    assert editor.i_prompt_edit.canInsertFromMimeData(mime)
    editor.i_prompt_edit.insertFromMimeData(mime)
    assert item.image is not None
    assert editor.i_prompt_edit.toPlainText() == ""                    # the picture is not typed in as text
    editor.i_image.clear()
    assert item.image is None


def test_questions_can_have_text_and_a_circuit_picture(editor):
    q, _p, _item = build_basic(editor)
    select(editor, q)
    editor.q_prompt_edit.setPlainText("Build the circuit in Figure 1.")
    editor.q_image.set_qimage(small_picture(200, 100))
    assert q.prompt == "Build the circuit in Figure 1." and q.image
    select(editor, editor.assessment)
    select(editor, q)                                                  # reloads from the model
    assert editor.q_prompt_edit.toPlainText() == "Build the circuit in Figure 1."
    assert editor.q_image.image_b64 == q.image


def test_a_big_picture_is_shrunk_to_fit_the_limit(editor):
    from ui.image_picker import MAX_SIDE_PX, qimage_to_b64
    import base64
    big = QImage(4000, 3000, QImage.Format_RGB32)
    big.fill(QColor("#123456"))
    b64 = qimage_to_b64(big)
    assert check_image(b64) is None and len(b64) <= MAX_IMAGE_BYTES
    picture = QImage.fromData(base64.b64decode(b64))
    assert max(picture.width(), picture.height()) <= MAX_SIDE_PX


def test_picture_problems_are_explained(editor, tmp_path):
    picker = editor.i_image
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().clear()
    assert not picker.paste_from_clipboard()
    assert "clipboard has no picture" in picker.message.text() and "Win+Shift+S" in picker.message.text()
    bad = tmp_path / "notes.png"
    bad.write_text("not a picture", encoding="utf-8")
    assert not picker.load_file(str(bad))
    assert "Could not read" in picker.message.text() and "PNG, JPG" in picker.message.text()
    assert not picker.set_qimage(QImage())
    assert "empty" in picker.message.text()


def test_damaged_or_oversized_image_data_is_reported_with_the_fix():
    assert check_image(None) is None and check_image("") is None
    assert "damaged" in check_image("%%%not base64%%%")
    import base64
    assert "not a PNG" in check_image(base64.b64encode(b"GIF89a....").decode())
    huge = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_IMAGE_BYTES + 10)).decode()
    assert "over the" in check_image(huge) and "smaller picture" in check_image(huge)


def test_the_explanation_appears_right_under_the_expected_value_box(editor):
    _q, _p, item = build_basic(editor)
    editor.i_expected_edit.setText("3.3V")
    assert "Type only the number" in editor.i_expected_msg.text()
    assert editor.i_expected_msg.isVisibleTo(editor.item_page)
    editor.i_expected_edit.setText("3.3")
    assert editor.i_expected_msg.text() == ""


# ---------- exporting for students ----------

def test_export_is_locked_while_there_are_problems_and_says_why(editor):
    assert not editor.export_button.isEnabled()                        # a blank assessment has problems
    assert "Fix these" in editor.export_button.toolTip()
    build_basic(editor)                                                # still: no expected value
    assert not editor.export_button.isEnabled()
    assert "has no expected value" in editor.export_button.toolTip()
    editor.i_expected_edit.setText("3.3")
    assert editor.export_button.isEnabled()
    assert "Model answers are left out" in editor.export_button.toolTip()
    editor.i_expected_edit.setText("3.3V")                             # an error locks it again
    assert not editor.export_button.isEnabled()


def test_save_stays_available_with_problems(editor, tmp_path):
    build_basic(editor)
    assert editor.save_button.isEnabled() and editor.save_as_button.isEnabled()
    assert editor.save_file(str(tmp_path / "draft.json"))


def test_export_refuses_when_there_are_problems(editor, tmp_path):
    build_basic(editor)
    path = tmp_path / "students.json"
    assert not editor.export_for_students(str(path))
    assert not path.exists() and "Not exported" in editor.status_label.text()


def test_the_student_copy_has_no_model_answers_but_the_same_fingerprint(editor, tmp_path):
    _q, _p, item = build_basic(editor)
    editor.i_type_box.setCurrentIndex(editor.i_type_box.findData("theory"))
    editor.i_prompt_edit.setPlainText("Explain loading.")
    editor.i_model_edit.setPlainText("Parallel resistance lowers R2.")
    assert editor.export_button.isEnabled()
    editor.save_file(str(tmp_path / "tutor.json"))
    dirty_before, path_before = editor.dirty, editor.path

    student_path = str(tmp_path / "students.json")
    assert editor.export_for_students(student_path)
    student = Assessment.load(student_path)
    tutor = Assessment.load(str(tmp_path / "tutor.json"))
    assert student.questions[0].parts[0].items[0].model_answer == ""
    assert tutor.questions[0].parts[0].items[0].model_answer == "Parallel resistance lowers R2."
    assert student.questions[0].parts[0].items[0].prompt == "Explain loading."
    assert student.content_hash() == tutor.content_hash()             # submissions still match the tutor's file
    assert (editor.dirty, editor.path) == (dirty_before, path_before)  # the open file is untouched
    assert item.model_answer == "Parallel resistance lowers R2."       # and so is the work on screen


def test_export_to_an_unwritable_place_is_explained(editor, tmp_path):
    _q, _p, _item = build_basic(editor)
    editor.i_expected_edit.setText("3.3")
    assert not editor.export_for_students(str(tmp_path / "no_folder" / "s.json"))
    assert "no_folder" in editor.status_label.text() and "folder exists" in editor.status_label.text()
