"""
ui/assessment_editor.py

The ASSESSMENT CREATOR: the screen a lecturer uses to build a practical.

    +-----------------------+---------------------------------------------+
    | the tree              | the editor for whatever is selected         |
    |   Practical           |   (assessment / question / part / item)     |
    |    Q1 Resistors       |                                             |
    |     1A Measurements   |                                             |
    |      1A-1 V across R2 |                                             |
    +-----------------------+---------------------------------------------+
    | Problems: a live list from Assessment.validate()                    |
    +---------------------------------------------------------------------+

How it works (the idea, in plain words):
  * The editor holds ONE core.models.Assessment object. The widgets show and
    change that object directly; nothing is copied. Save writes it as JSON.
  * Every time the lecturer changes a field, the matching part of the object is
    updated, the tree labels are refreshed and Assessment.validate() is run again,
    so formula mistakes and missing values show up immediately in the Problems list.
  * Fields that do not apply are hidden (a theory item has no probes; a single-answer
    item has no formula), and their old contents are cleared so nothing invisible can
    make the assessment invalid.

Not here yet (later stages): admin login, creation/debug modes, a "try this formula
with sample values" box, and exporting a locked copy for students.
"""

from __future__ import annotations

import json
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QStackedWidget, QTableWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ui.image_picker import ImagePicker, PromptEdit
from core.models import (TWO_CHANNEL_FEATURES, AnswerMode, Assessment, Item, ItemType, Part,
                         Quantity, Question, ScoringMode, ToleranceMode, Variable,
                         VariableSource, WaveformFeature, WaveformSpec)

CHANNELS = ["P0", "P1", "P2", "P3", "P4", "P5"]      # the box's test points
RED = "color: #b00020;"

# Page numbers of the right-hand stack.
PAGE_ASSESSMENT, PAGE_QUESTION, PAGE_PART, PAGE_ITEM = range(4)

# Friendly names for the choices (shown in the drop-downs).
MODE_NAMES = {AnswerMode.SINGLE: "Single value", AnswerMode.VARYING: "Varying (formula)",
              AnswerMode.WAVEFORM: "Waveform"}
SCORING_NAMES = {ScoringMode.SUM: "Add up the items", ScoringMode.ALL_OR_NOTHING: "All or nothing"}
FEATURE_NAMES = {f: f.value.replace("_", " ").capitalize() for f in WaveformFeature}


def _number(x: float) -> str:
    """3.0 -> '3', 2.5 -> '2.5' (for labels)."""
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def _combo(entries, data_is_value: bool = True) -> QComboBox:
    """A drop-down from a list of (text, data) pairs."""
    box = QComboBox()
    for text, data in entries:
        box.addItem(text, data)
    return box


def _channel_combo(allow_none: bool = False) -> QComboBox:
    box = QComboBox()
    if allow_none:
        box.addItem("(none)", None)
    for ch in CHANNELS:
        box.addItem(ch, ch)
    return box


def parse_number(text: str) -> tuple[float | None, str | None]:
    """
    Read a number the lecturer typed. Returns (value, None) when it is fine, (None, None)
    when the box is empty, and (None, explanation) when it is not a usable number. The
    explanation says what is wrong with THIS text and how to fix it.
    """
    text = text.strip()
    if not text:
        return None, None
    try:
        value = float(text)
    except ValueError:
        if "," in text and text.replace(",", ".", 1).replace(".", "", 1).lstrip("+-").isdigit():
            return None, (f"'{text}' uses a comma. Use a decimal point instead "
                          f"(for example {text.replace(',', '.', 1)}).")
        if any(c.isalpha() for c in text):
            return None, (f"'{text}' has letters in it, so it is not a number. Type only the number "
                          f"(for example 3.3); the unit comes from the 'Measures' choice. "
                          f"1e3 style numbers are fine.")
        return None, (f"'{text}' is not a number. Type digits with an optional minus sign and "
                      f"decimal point, for example 3.3 or -0.5.")
    if not math.isfinite(value):
        return None, f"'{text}' is not an ordinary number. Type a finite number such as 3.3."
    return value, None


def describe_load_error(path: str, exc: Exception) -> str:
    """A plain-English reason why an assessment file could not be opened."""
    if isinstance(exc, FileNotFoundError):
        return f"'{path}' does not exist. Check the file name and folder."
    if isinstance(exc, PermissionError):
        return f"'{path}' cannot be read (permission denied). Close it in other programs or choose another copy."
    if isinstance(exc, OSError):
        return f"'{path}' could not be read: {exc.strerror or exc}."
    if isinstance(exc, json.JSONDecodeError):
        return (f"'{path}' is not a valid assessment file: the JSON is broken at line {exc.lineno}, "
                f"column {exc.colno} ({exc.msg}). Was it edited by hand or saved by another program?")
    if isinstance(exc, KeyError):
        return (f"'{path}' is not a Circuitscope assessment, or it is damaged: "
                f"the required field {exc} is missing.")
    if isinstance(exc, ValueError):
        return (f"'{path}' contains a value this version of Circuitscope does not understand ({exc}). "
                f"It may have been made by a newer version.")
    return (f"'{path}' does not have the layout of an assessment file. "
            f"Choose a file that was saved from the Assessment creator.")


class AssessmentEditor(QWidget):
    changed = Signal()          # sent after every edit (the window can show "unsaved")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.assessment = Assessment(title="Untitled practical")
        self.path: str | None = None            # the file it was opened from / saved to
        self.dirty = False                      # True when there are unsaved changes
        self._objects: dict[int, tuple] = {}    # tree key -> (kind, object, question, part)
        self._tree_items: dict[int, QTreeWidgetItem] = {}
        self._current: tuple | None = None      # what is selected right now
        self._loading = False                   # True while we FILL widgets (so that does not count as an edit)
        self._next_key = 0
        # Expected-value text that is not a number: id(item) -> (item, text, explanation).
        # The model only holds a number, so what was typed would otherwise be forgotten.
        self._bad_expected: dict[int, tuple] = {}

        self._build_ui()
        self._rebuild_tree()

    # ======================================================================
    # Building the screen
    # ======================================================================

    def _build_ui(self) -> None:
        # --- top bar: file buttons ---
        self.new_button = QPushButton("New")
        self.open_button = QPushButton("Open...")
        self.save_button = QPushButton("Save")
        self.save_as_button = QPushButton("Save as...")
        self.export_button = QPushButton("Export for students...")
        self.file_label = QLabel()
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        top = QHBoxLayout()
        for b in (self.new_button, self.open_button, self.save_button, self.save_as_button):
            top.addWidget(b)
        top.addSpacing(20)
        top.addWidget(self.export_button)
        top.addWidget(self.file_label, 1)

        # --- left: the tree and the buttons that change its structure ---
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.add_question_button = QPushButton("+ Question")
        self.add_part_button = QPushButton("+ Part")
        self.add_item_button = QPushButton("+ Item")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        for b in (self.add_question_button, self.add_part_button, self.add_item_button):
            row1.addWidget(b)
        for b in (self.remove_button, self.up_button, self.down_button):
            row2.addWidget(b)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.tree, 1)
        left_layout.addLayout(row1)
        left_layout.addLayout(row2)

        # --- right: one page per kind of thing that can be selected ---
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_assessment_page())
        self.stack.addWidget(self._build_question_page())
        self.stack.addWidget(self._build_part_page())
        self.stack.addWidget(self._build_item_page())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.stack)

        top_split = QSplitter(Qt.Horizontal)
        top_split.addWidget(left)
        top_split.addWidget(scroll)
        top_split.setStretchFactor(0, 1)
        top_split.setStretchFactor(1, 2)
        top_split.setSizes([340, 760])

        # --- bottom: the live list of problems ---
        self.problems_label = QLabel()
        self.problems_list = QListWidget()
        self.problems_list.setMaximumHeight(110)
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addWidget(self.problems_label)
        bottom_layout.addWidget(self.problems_list)

        outer = QVBoxLayout(self)
        outer.addLayout(top)
        outer.addWidget(self.status_label)
        outer.addWidget(top_split, 1)
        outer.addWidget(bottom)

        # --- wiring ---
        self.new_button.clicked.connect(self._new_clicked)
        self.open_button.clicked.connect(self._open_clicked)
        self.save_button.clicked.connect(lambda: self.save_file())
        self.save_as_button.clicked.connect(self._save_as_clicked)
        self.export_button.clicked.connect(self._export_clicked)
        self.tree.currentItemChanged.connect(self._selection_changed)
        self.add_question_button.clicked.connect(self.add_question)
        self.add_part_button.clicked.connect(self.add_part)
        self.add_item_button.clicked.connect(self.add_item)
        self.remove_button.clicked.connect(self.remove_selected)
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(+1))

    # ----- the four editor pages ------------------------------------------

    def _build_assessment_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.title_edit = QLineEdit()
        self.info_label = QLabel()
        self.id_label = QLabel()
        self.id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Title:", self.title_edit)
        form.addRow("Contents:", self.info_label)
        form.addRow("Assessment id:", self.id_label)
        self.title_edit.textChanged.connect(self._apply_assessment)
        return page

    def _build_question_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.q_id_edit = QLineEdit()
        self.q_title_edit = QLineEdit()
        self.q_mode_box = _combo([(name, mode.value) for mode, name in MODE_NAMES.items()])
        self.q_scoring_box = _combo([(name, s.value) for s, name in SCORING_NAMES.items()])
        form.addRow("Question id:", self.q_id_edit)
        form.addRow("Title:", self.q_title_edit)
        form.addRow("Default answer type:", self.q_mode_box)
        form.addRow("Question mark:", self.q_scoring_box)
        self.q_prompt_edit = PromptEdit()
        self.q_prompt_edit.setPlaceholderText("Text shown above this question's parts, e.g. 'Build the circuit in Figure 1.' "
                                              "(optional; you can also paste a picture here)")
        self.q_prompt_edit.setFixedHeight(70)
        self.q_image = ImagePicker()
        form.addRow("Text for the student:", self.q_prompt_edit)
        form.addRow("Picture (e.g. the circuit):", self.q_image)
        layout.addLayout(form)

        group = QGroupBox("Variables (the student's own values, usable in this question's formulas)")
        group_layout = QVBoxLayout(group)
        self.var_table = QTableWidget(0, 7)
        self.var_table.setHorizontalHeaderLabels(
            ["Name", "Label", "Unit", "Value comes from", "Measures", "Probe +", "Probe -"])
        self.var_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.var_table.setMinimumHeight(140)
        for column, width in enumerate((90, 150, 60, 150, 90, 70, 70)):
            self.var_table.setColumnWidth(column, width)
        self.add_var_button = QPushButton("+ Variable")
        self.remove_var_button = QPushButton("Remove variable")
        buttons = QHBoxLayout()
        buttons.addWidget(self.add_var_button)
        buttons.addWidget(self.remove_var_button)
        buttons.addStretch(1)
        group_layout.addWidget(self.var_table)
        group_layout.addLayout(buttons)
        layout.addWidget(group, 1)

        for w in (self.q_id_edit, self.q_title_edit):
            w.textChanged.connect(self._apply_question)
        for w in (self.q_mode_box, self.q_scoring_box):
            w.currentIndexChanged.connect(self._apply_question)
        self.q_prompt_edit.textChanged.connect(self._apply_question)
        self.q_prompt_edit.image_pasted.connect(self.q_image.set_qimage)
        self.q_image.changed.connect(self._apply_question)
        self.add_var_button.clicked.connect(self.add_variable)
        self.remove_var_button.clicked.connect(self.remove_variable)
        return page

    def _build_part_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.p_id_edit = QLineEdit()
        self.p_title_edit = QLineEdit()
        form.addRow("Part id:", self.p_id_edit)
        form.addRow("Title:", self.p_title_edit)
        for w in (self.p_id_edit, self.p_title_edit):
            w.textChanged.connect(self._apply_part)
        return page

    def _build_item_page(self) -> QWidget:
        page = QWidget()
        self.item_page = page
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.item_form = form

        self.i_id_edit = QLineEdit()
        self.i_desc_edit = QLineEdit()
        self.i_marks_spin = QDoubleSpinBox()
        self.i_marks_spin.setRange(0, 1000)
        self.i_marks_spin.setDecimals(2)
        self.i_type_box = _combo([("Measurement (graded by the box)", ItemType.MEASUREMENT.value),
                                  ("Theory (tutor marks True/False)", ItemType.THEORY.value)])
        self.i_mode_box = QComboBox()                      # filled in _load_item (the first entry names the question's default)
        self.i_quantity_box = _combo([("Voltage", Quantity.VOLTAGE.value), ("Current", Quantity.CURRENT.value)])
        self.i_pos_box = _channel_combo(allow_none=True)
        self.i_neg_box = _channel_combo(allow_none=True)
        self.i_expected_edit = QLineEdit()
        self.i_expected_edit.setPlaceholderText("a number, e.g. 3.3")
        self.i_expected_msg = QLabel("")               # why the typed expected value is not usable
        self.i_expected_msg.setStyleSheet(RED)
        self.i_expected_msg.setWordWrap(True)
        self.i_formula_edit = QLineEdit()
        self.i_formula_edit.setPlaceholderText("e.g. Vs * R2 / (R1 + R2)")
        self.i_vars_label = QLabel()
        self.i_vars_label.setStyleSheet("color: #777;")
        self.i_tol_mode_box = _combo([("Absolute (same units as the answer)", ToleranceMode.ABSOLUTE.value),
                                      ("Percent of the expected value", ToleranceMode.PERCENT.value)])
        self.i_tol_spin = QDoubleSpinBox()
        self.i_tol_spin.setRange(0, 1e9)
        self.i_tol_spin.setDecimals(4)

        form.addRow("Item id:", self.i_id_edit)
        form.addRow("Short title:", self.i_desc_edit)
        self.i_prompt_edit = PromptEdit()
        self.i_prompt_edit.setPlaceholderText("The full question the student reads (optional for measurements; "
                                              "for theory, type the question here or paste a picture)")
        self.i_prompt_edit.setFixedHeight(90)
        self.i_image = ImagePicker()
        self.i_model_edit = PromptEdit()
        self.i_model_edit.setPlaceholderText("What a correct answer should contain. Only the tutor sees this.")
        self.i_model_edit.setFixedHeight(70)
        form.addRow("Question text:", self.i_prompt_edit)
        form.addRow("Picture:", self.i_image)
        form.addRow("Model answer (tutor only):", self.i_model_edit)
        form.addRow("Marks:", self.i_marks_spin)
        form.addRow("Item type:", self.i_type_box)
        form.addRow("Answer type:", self.i_mode_box)
        form.addRow("Measures:", self.i_quantity_box)
        form.addRow("Probe + (red):", self.i_pos_box)
        form.addRow("Probe - (black):", self.i_neg_box)
        form.addRow("Expected value:", self.i_expected_edit)
        form.addRow("", self.i_expected_msg)
        form.addRow("Expected formula:", self.i_formula_edit)
        form.addRow("", self.i_vars_label)
        form.addRow("Tolerance type:", self.i_tol_mode_box)
        form.addRow("Tolerance:", self.i_tol_spin)
        layout.addLayout(form)

        # --- waveform settings (only shown for waveform items) ---
        self.wave_group = QGroupBox("Waveform capture")
        wave_form = QFormLayout(self.wave_group)
        self.w_feature_box = _combo([(name, f.value) for f, name in FEATURE_NAMES.items()])
        self.w_channel_box = _channel_combo()
        self.w_ref_box = _channel_combo(allow_none=True)
        self.w_rate_spin = QDoubleSpinBox()
        self.w_rate_spin.setRange(1, 1_000_000)
        self.w_rate_spin.setDecimals(0)
        self.w_rate_spin.setSuffix(" Hz")
        self.w_points_spin = QSpinBox()
        self.w_points_spin.setRange(1, 100_000)
        wave_form.addRow("Feature to grade:", self.w_feature_box)
        wave_form.addRow("Channel:", self.w_channel_box)
        wave_form.addRow("Reference channel:", self.w_ref_box)
        wave_form.addRow("Sample rate:", self.w_rate_spin)
        wave_form.addRow("Points:", self.w_points_spin)
        self.wave_form = wave_form
        layout.addWidget(self.wave_group)

        # --- this item's own problems, in red, right under the fields ---
        self.i_problem_label = QLabel()
        self.i_problem_label.setStyleSheet(RED)
        self.i_problem_label.setWordWrap(True)
        layout.addWidget(self.i_problem_label)
        layout.addStretch(1)

        for w in (self.i_id_edit, self.i_desc_edit, self.i_expected_edit, self.i_formula_edit):
            w.textChanged.connect(self._apply_item)
        for w in (self.i_prompt_edit, self.i_model_edit):
            w.textChanged.connect(self._apply_item)
        self.i_prompt_edit.image_pasted.connect(self.i_image.set_qimage)
        self.i_image.changed.connect(self._apply_item)
        for w in (self.i_marks_spin, self.i_tol_spin, self.w_rate_spin, self.w_points_spin):
            w.valueChanged.connect(self._apply_item)
        for w in (self.i_type_box, self.i_mode_box, self.i_quantity_box, self.i_pos_box,
                  self.i_neg_box, self.i_tol_mode_box, self.w_feature_box, self.w_channel_box,
                  self.w_ref_box):
            w.currentIndexChanged.connect(self._apply_item)
        return page

    # ======================================================================
    # The tree
    # ======================================================================

    def _new_key(self, kind, obj, question=None, part=None) -> int:
        self._next_key += 1
        self._objects[self._next_key] = (kind, obj, question, part)
        return self._next_key

    def _label_for(self, kind: str, obj) -> str:
        """The text shown in the tree for each node."""
        if kind == "assessment":
            total = obj.total_marks()
            return f"{obj.title or '(no title)'}  -  {_number(total)} mark{'' if total == 1 else 's'}"
        if kind == "question":
            marks = sum(i.marks for p in obj.parts for i in p.items)
            return f"Q{obj.id}: {obj.title}  ({_number(marks)})"
        if kind == "part":
            return f"{obj.id}: {obj.title}"
        theory = "  [theory]" if obj.item_type == ItemType.THEORY else ""
        return f"{obj.id}: {obj.description}  ({_number(obj.marks)}){theory}"

    def _rebuild_tree(self, select=None) -> None:
        """Draw the whole tree again from the assessment (used after structural changes)."""
        self.tree.blockSignals(True)
        self.tree.clear()
        self._objects.clear()
        self._tree_items.clear()

        def add(parent_widget, kind, obj, question=None, part=None):
            node = QTreeWidgetItem(parent_widget)
            key = self._new_key(kind, obj, question, part)
            node.setData(0, Qt.UserRole, key)
            self._tree_items[key] = node
            return node

        root = add(self.tree, "assessment", self.assessment)
        for q in self.assessment.questions:
            q_node = add(root, "question", q, q)
            for p in q.parts:
                p_node = add(q_node, "part", p, q, p)
                for it in p.items:
                    add(p_node, "item", it, q, p)
        self.tree.expandAll()
        self._refresh_labels()
        self.tree.blockSignals(False)

        # Reselect what was selected (found by object identity), else the top.
        target = root
        if select is not None:
            for key, (_kind, obj, _q, _p) in self._objects.items():
                if obj is select:
                    target = self._tree_items[key]
                    break
        self.tree.setCurrentItem(target)
        self._selection_changed(target, None)
        self._refresh_problems()

    def _refresh_labels(self) -> None:
        for key, node in self._tree_items.items():
            kind, obj, _q, _p = self._objects[key]
            node.setText(0, self._label_for(kind, obj))

    def _selection_changed(self, node, _previous) -> None:
        """A different node was clicked: show its editor page and load its values."""
        if node is None:
            self._current = None
            return
        self._current = self._objects[node.data(0, Qt.UserRole)]
        kind, obj, question, _part = self._current
        self._loading = True
        try:
            if kind == "assessment":
                self.title_edit.setText(obj.title)
                self.id_label.setText(obj.assessment_id)
                self._refresh_info()
                self.stack.setCurrentIndex(PAGE_ASSESSMENT)
            elif kind == "question":
                self.q_id_edit.setText(obj.id)
                self.q_title_edit.setText(obj.title)
                self.q_mode_box.setCurrentIndex(self.q_mode_box.findData(obj.default_mode.value))
                self.q_scoring_box.setCurrentIndex(self.q_scoring_box.findData(obj.scoring.value))
                self.q_prompt_edit.setPlainText(obj.prompt)
                self.q_image.set_image_b64(obj.image)
                self._fill_variable_table(obj)
                self.stack.setCurrentIndex(PAGE_QUESTION)
            elif kind == "part":
                self.p_id_edit.setText(obj.id)
                self.p_title_edit.setText(obj.title)
                self.stack.setCurrentIndex(PAGE_PART)
            else:
                self._load_item(obj, question)
                self.stack.setCurrentIndex(PAGE_ITEM)
        finally:
            self._loading = False
        self._refresh_buttons()
        self._refresh_item_extras()

    def _refresh_info(self) -> None:
        a = self.assessment
        n_items = sum(1 for _ in a.iter_items())
        self.info_label.setText(f"{len(a.questions)} question(s), {n_items} item(s), "
                                f"{_number(a.total_marks())} marks in total")

    def _refresh_buttons(self) -> None:
        kind = self._current[0] if self._current else None
        self.add_part_button.setEnabled(kind in ("question", "part", "item"))
        self.add_item_button.setEnabled(kind in ("part", "item"))
        self.remove_button.setEnabled(kind in ("question", "part", "item"))
        self.up_button.setEnabled(kind in ("question", "part", "item"))
        self.down_button.setEnabled(kind in ("question", "part", "item"))

    # ======================================================================
    # Loading an item into its widgets / reading it back
    # ======================================================================

    def _effective_mode(self, item: Item, question: Question) -> AnswerMode:
        return question.mode_for(item)

    def _load_item(self, item: Item, question: Question) -> None:
        """Fill the item page from `item` (called with self._loading = True)."""
        self.i_id_edit.setText(item.id)
        self.i_desc_edit.setText(item.description)
        self.i_marks_spin.setValue(item.marks)
        self.i_type_box.setCurrentIndex(self.i_type_box.findData(item.item_type.value))

        # The first entry of the mode box means "use the question's setting" and names it.
        self.i_mode_box.clear()
        self.i_mode_box.addItem(f"Same as question ({MODE_NAMES[question.default_mode]})", None)
        for mode, name in MODE_NAMES.items():
            self.i_mode_box.addItem(name, mode.value)
        self.i_mode_box.setCurrentIndex(
            0 if item.mode is None else self.i_mode_box.findData(item.mode.value))

        self.i_quantity_box.setCurrentIndex(
            self.i_quantity_box.findData(item.quantity.value if item.quantity else Quantity.VOLTAGE.value))
        self.i_pos_box.setCurrentIndex(self.i_pos_box.findData(item.probe_pos))
        self.i_neg_box.setCurrentIndex(self.i_neg_box.findData(item.probe_neg))
        self.i_prompt_edit.setPlainText(item.prompt)
        self.i_image.set_image_b64(item.image)
        self.i_model_edit.setPlainText(item.model_answer)
        bad = self._bad_expected.get(id(item))
        if bad is not None:                                   # show what was typed, not a blank
            self.i_expected_edit.setText(bad[1])
            self.i_expected_edit.setStyleSheet("border: 1px solid #b00020;")
            self.i_expected_edit.setToolTip(bad[2])
            self.i_expected_msg.setText(f"The expected value {bad[2]}")
        else:
            self.i_expected_msg.setText("")
            self.i_expected_edit.setText("" if item.expected is None else repr(item.expected))
            self.i_expected_edit.setStyleSheet("")
            self.i_expected_edit.setToolTip("")
        self.i_formula_edit.setText(item.expected_formula or "")
        self.i_tol_mode_box.setCurrentIndex(self.i_tol_mode_box.findData(item.tolerance_mode.value))
        self.i_tol_spin.setValue(item.tolerance)

        # A waveform item needs a capture setup: make a sensible one if it has none yet.
        if self._effective_mode(item, question) == AnswerMode.WAVEFORM and item.waveform is None:
            item.waveform = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1")
        wf = item.waveform or WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1")
        self.w_feature_box.setCurrentIndex(self.w_feature_box.findData(wf.feature.value))
        self.w_channel_box.setCurrentIndex(self.w_channel_box.findData(wf.channel))
        self.w_ref_box.setCurrentIndex(self.w_ref_box.findData(wf.channel_ref))
        self.w_rate_spin.setValue(wf.sample_rate_hz)
        self.w_points_spin.setValue(wf.num_points)
        self._refresh_item_visibility(item, question)

    def _refresh_item_visibility(self, item: Item, question: Question) -> None:
        """Show only the fields that apply to this kind of item."""
        theory = item.item_type == ItemType.THEORY
        mode = self._effective_mode(item, question)
        measured = not theory
        show = {
            self.i_mode_box: measured,
            self.i_quantity_box: measured and mode != AnswerMode.WAVEFORM,
            self.i_pos_box: measured and mode != AnswerMode.WAVEFORM,
            self.i_neg_box: measured and mode != AnswerMode.WAVEFORM,
            self.i_expected_edit: measured and mode in (AnswerMode.SINGLE, AnswerMode.WAVEFORM),
            self.i_expected_msg: measured and mode in (AnswerMode.SINGLE, AnswerMode.WAVEFORM),
            self.i_formula_edit: measured and mode in (AnswerMode.VARYING, AnswerMode.WAVEFORM),
            self.i_vars_label: measured and mode in (AnswerMode.VARYING, AnswerMode.WAVEFORM),
            self.i_tol_mode_box: measured,
            self.i_tol_spin: measured,
        }
        for widget, visible in show.items():
            self.item_form.setRowVisible(widget, visible)
        self.item_form.setRowVisible(self.i_model_edit, theory)
        self.wave_group.setVisible(measured and mode == AnswerMode.WAVEFORM)
        feature = WaveformFeature(self.w_feature_box.currentData())
        self.wave_form.setRowVisible(self.w_ref_box, feature in TWO_CHANNEL_FEATURES)
        names = ", ".join(v.name for v in question.variables) or "none defined yet"
        self.i_vars_label.setText(f"Variables you can use: {names}   (also pi, e, sqrt, sin ...)")
        self.i_tol_spin.setSuffix(" %" if self.i_tol_mode_box.currentData() == "percent" else "")

    def _apply_item(self, *_args) -> None:
        """Copy the item widgets into the Item object (called on every change)."""
        if self._loading or not self._current or self._current[0] != "item":
            return
        _kind, item, question, _part = self._current

        item.id = self.i_id_edit.text().strip()
        item.description = self.i_desc_edit.text()
        item.marks = self.i_marks_spin.value()
        item.item_type = ItemType(self.i_type_box.currentData())
        mode_text = self.i_mode_box.currentData()
        item.mode = AnswerMode(mode_text) if mode_text else None
        item.quantity = Quantity(self.i_quantity_box.currentData())
        item.probe_pos = self.i_pos_box.currentData()
        item.probe_neg = self.i_neg_box.currentData()
        item.tolerance_mode = ToleranceMode(self.i_tol_mode_box.currentData())
        item.tolerance = self.i_tol_spin.value()

        item.prompt = self.i_prompt_edit.toPlainText()
        item.image = self.i_image.image_b64
        item.model_answer = self.i_model_edit.toPlainText()

        # The expected value: empty -> None; text that is not a number -> None, a red box
        # and an explanation (remembered so it can be shown again and listed as a problem).
        text = self.i_expected_edit.text()
        value, why = parse_number(text)
        item.expected = value
        if why:
            self._bad_expected[id(item)] = (item, text.strip(), why)
            self.i_expected_edit.setStyleSheet("border: 1px solid #b00020;")
            self.i_expected_edit.setToolTip(why)
            self.i_expected_msg.setText(f"The expected value {why}")
        else:
            self.i_expected_msg.setText("")
            self._bad_expected.pop(id(item), None)
            self.i_expected_edit.setStyleSheet("")
            self.i_expected_edit.setToolTip("")
        formula = self.i_formula_edit.text().strip()
        item.expected_formula = formula or None

        # Remove what does not apply, so hidden leftovers cannot make the item invalid.
        mode = self._effective_mode(item, question)
        if item.item_type == ItemType.THEORY:
            item.mode = None
            item.quantity = item.probe_pos = item.probe_neg = None
            item.expected = item.expected_formula = None
            item.waveform = None
            self._bad_expected.pop(id(item), None)
            self._clear_text(self.i_expected_edit)
            self._clear_text(self.i_formula_edit)
        else:
            if item.model_answer:                      # only theory items have a model answer
                item.model_answer = ""
                self.i_model_edit.blockSignals(True)
                self.i_model_edit.setPlainText("")
                self.i_model_edit.blockSignals(False)
            if mode == AnswerMode.SINGLE:
                item.expected_formula = None
                self._clear_text(self.i_formula_edit)
            elif mode == AnswerMode.VARYING:
                item.expected = None
                self._bad_expected.pop(id(item), None)
                self._clear_text(self.i_expected_edit)
            if mode == AnswerMode.WAVEFORM:
                item.quantity = item.probe_pos = item.probe_neg = None
                feature = WaveformFeature(self.w_feature_box.currentData())
                item.waveform = WaveformSpec(
                    feature=feature,
                    channel=self.w_channel_box.currentData(),
                    channel_ref=self.w_ref_box.currentData() if feature in TWO_CHANNEL_FEATURES else None,
                    sample_rate_hz=self.w_rate_spin.value(),
                    num_points=self.w_points_spin.value())
            else:
                item.waveform = None

        self._refresh_item_visibility(item, question)
        self._touch()

    def _clear_text(self, edit: QLineEdit) -> None:
        """Empty a line edit without it counting as another edit."""
        edit.blockSignals(True)
        edit.setText("")
        edit.blockSignals(False)

    # ----- assessment / question / part pages ----------------------------------

    def _apply_assessment(self, *_args) -> None:
        if self._loading or not self._current or self._current[0] != "assessment":
            return
        self.assessment.title = self.title_edit.text()
        self._touch()

    def _apply_question(self, *_args) -> None:
        if self._loading or not self._current or self._current[0] != "question":
            return
        question = self._current[1]
        question.id = self.q_id_edit.text().strip()
        question.title = self.q_title_edit.text()
        question.default_mode = AnswerMode(self.q_mode_box.currentData())
        question.scoring = ScoringMode(self.q_scoring_box.currentData())
        question.prompt = self.q_prompt_edit.toPlainText()
        question.image = self.q_image.image_b64
        self._touch()

    def _apply_part(self, *_args) -> None:
        if self._loading or not self._current or self._current[0] != "part":
            return
        part = self._current[1]
        part.id = self.p_id_edit.text().strip()
        part.title = self.p_title_edit.text()
        self._touch()

    # ----- the variables table ---------------------------------------------------

    def _fill_variable_table(self, question: Question) -> None:
        self.var_table.setRowCount(0)
        for variable in question.variables:
            self._add_variable_row(variable)

    def _add_variable_row(self, v: Variable) -> None:
        row = self.var_table.rowCount()
        self.var_table.insertRow(row)
        name, label, unit = QLineEdit(v.name), QLineEdit(v.label), QLineEdit(v.unit)
        source = _combo([("Student types it", VariableSource.ENTERED.value),
                         ("Box measures it", VariableSource.MEASURED.value)])
        source.setCurrentIndex(source.findData(v.source.value))
        quantity = _combo([("Voltage", Quantity.VOLTAGE.value), ("Current", Quantity.CURRENT.value)])
        quantity.setCurrentIndex(quantity.findData(v.quantity.value if v.quantity else Quantity.VOLTAGE.value))
        pos, neg = _channel_combo(), _channel_combo()
        pos.setCurrentIndex(pos.findData(v.probe_pos or "P1"))
        neg.setCurrentIndex(neg.findData(v.probe_neg or "P3"))
        for col, widget in enumerate((name, label, unit, source, quantity, pos, neg)):
            self.var_table.setCellWidget(row, col, widget)
        for w in (name, label, unit):
            w.textChanged.connect(self._apply_variables)
        for w in (source, quantity, pos, neg):
            w.currentIndexChanged.connect(self._apply_variables)
        self._sync_variable_row_enabled(row)

    def _sync_variable_row_enabled(self, row: int) -> None:
        """The measuring columns only matter for a variable the box measures."""
        measured = self.var_table.cellWidget(row, 3).currentData() == VariableSource.MEASURED.value
        for col in (4, 5, 6):
            self.var_table.cellWidget(row, col).setEnabled(measured)

    def _apply_variables(self, *_args) -> None:
        """Copy the whole table into the question's variable list."""
        if self._loading or not self._current or self._current[0] != "question":
            return
        question = self._current[1]
        variables = []
        for row in range(self.var_table.rowCount()):
            self._sync_variable_row_enabled(row)
            cell = lambda c: self.var_table.cellWidget(row, c)       # noqa: E731
            measured = cell(3).currentData() == VariableSource.MEASURED.value
            variables.append(Variable(
                name=cell(0).text().strip(), label=cell(1).text(), unit=cell(2).text(),
                source=VariableSource(cell(3).currentData()),
                quantity=Quantity(cell(4).currentData()) if measured else None,
                probe_pos=cell(5).currentData() if measured else None,
                probe_neg=cell(6).currentData() if measured else None))
        question.variables = variables
        self._touch()

    def add_variable(self) -> None:
        if not self._current or self._current[0] != "question":
            return
        question = self._current[1]
        existing = {v.name for v in question.variables}
        n = len(existing) + 1
        while f"R{n}" in existing:
            n += 1
        variable = Variable(name=f"R{n}", label=f"R{n}")
        question.variables.append(variable)
        self._loading = True
        self._add_variable_row(variable)
        self._loading = False
        self._touch()

    def remove_variable(self) -> None:
        if not self._current or self._current[0] != "question":
            return
        row = self.var_table.currentRow()
        if row < 0:
            return
        question = self._current[1]
        del question.variables[row]
        self.var_table.removeRow(row)
        self._touch()

    # ======================================================================
    # Adding, removing and moving things
    # ======================================================================

    def _unique_id(self, taken: set[str], candidates) -> str:
        for candidate in candidates:
            if candidate not in taken:
                return candidate
        return "new"

    def add_question(self) -> None:
        taken = {q.id for q in self.assessment.questions}
        n = len(taken) + 1
        qid = self._unique_id(taken, (str(i) for i in range(n, n + 1000)))
        question = Question(id=qid, title="New question")
        self.assessment.questions.append(question)
        self._structure_changed(select=question)

    def add_part(self) -> None:
        """Add a part to the selected question (or to the question of the selected part/item)."""
        if not self._current or self._current[0] == "assessment":
            return
        question = self._current[2]
        taken = {p.id for p in question.parts}
        letters = (f"{question.id}{chr(65 + i)}" for i in range(26))
        part = Part(id=self._unique_id(taken, letters), title="New part")
        question.parts.append(part)
        self._structure_changed(select=part)

    def add_item(self) -> None:
        """Add an item to the selected part (or to the part of the selected item)."""
        if not self._current or self._current[3] is None:
            return
        part = self._current[3]
        taken = {i.id for _q, _p, i in self.assessment.iter_items()}
        ids = (f"{part.id}-{n}" for n in range(1, 1000))
        item = Item(id=self._unique_id(taken, ids), description="New item", marks=1.0,
                    quantity=Quantity.VOLTAGE, probe_pos="P1", probe_neg="P3")
        part.items.append(item)
        self._structure_changed(select=item)

    def _container_of(self):
        """(the list holding the selected thing, the selected thing) or None."""
        if not self._current:
            return None
        kind, obj, question, part = self._current
        if kind == "question":
            return self.assessment.questions, obj
        if kind == "part":
            return question.parts, obj
        if kind == "item":
            return part.items, obj
        return None

    def remove_selected(self) -> None:
        found = self._container_of()
        if found is None:
            return
        container, obj = found
        kind = self._current[0]
        has_children = (kind == "question" and any(p.items for p in obj.parts)) or \
                       (kind == "part" and obj.items) or (kind == "question" and obj.parts)
        if has_children and not self.confirm(f"Remove this {kind} and everything inside it?"):
            return
        index = container.index(obj)
        container.remove(obj)
        # Select a neighbour (or the one before) afterwards.
        neighbour = container[min(index, len(container) - 1)] if container else None
        self._structure_changed(select=neighbour)

    def move_selected(self, direction: int) -> None:
        found = self._container_of()
        if found is None:
            return
        container, obj = found
        i = container.index(obj)
        j = i + direction
        if 0 <= j < len(container):
            container[i], container[j] = container[j], container[i]
            self._structure_changed(select=obj)

    def _structure_changed(self, select=None) -> None:
        self._rebuild_tree(select=select)
        self._touch(rebuild=False)

    # ======================================================================
    # Problems list and "unsaved" tracking
    # ======================================================================

    def _touch(self, rebuild: bool = True) -> None:
        """Something was edited: mark unsaved, refresh labels and problems."""
        self.dirty = True
        if rebuild:
            self._refresh_labels()
        self._refresh_problems()
        self._refresh_file_label()
        self.changed.emit()

    def _all_problems(self) -> list[str]:
        """validate()'s list, with the generic 'no expected value' replaced by what was actually typed."""
        problems = self.assessment.validate()
        present = {id(i): i for _q, _p, i in self.assessment.iter_items()}
        for key, (item, _text, why) in list(self._bad_expected.items()):
            if present.get(key) is not item:                  # the item was removed
                del self._bad_expected[key]
                continue
            prefix = f"Item {item.id}:"
            problems = [p for p in problems if not (p.startswith(prefix) and "has no expected value" in p)]
            problems.append(f"{prefix} the expected value {why}")
        return problems

    def _refresh_problems(self) -> None:
        problems = self._all_problems()
        self.problems_list.clear()
        self.problems_list.addItems(problems)
        if problems:
            self.problems_label.setText(f"Problems to fix before this can be given to students: {len(problems)}")
            self.problems_label.setStyleSheet(RED)
        else:
            self.problems_label.setText("No problems found.")
            self.problems_label.setStyleSheet("color: #2e7d32;")
        self._refresh_export_button(problems)
        self._refresh_item_extras(problems)
        if self._current and self._current[0] == "assessment":
            self._refresh_info()

    def _refresh_export_button(self, problems: list[str]) -> None:
        """Students can only be given an assessment with no problems; say why when it is locked."""
        self.export_button.setEnabled(not problems)
        if problems:
            shown = "\n".join(f"- {p}" for p in problems[:5])
            more = f"\n...and {len(problems) - 5} more" if len(problems) > 5 else ""
            self.export_button.setToolTip(f"Fix these {len(problems)} problem(s) first:\n{shown}{more}")
        else:
            self.export_button.setToolTip("Save a copy for students. Model answers are left out of it.")

    def _refresh_item_extras(self, problems: list[str] | None = None) -> None:
        """The red text under an item's fields: only the problems about THIS item."""
        if not self._current or self._current[0] != "item":
            self.i_problem_label.setText("")
            return
        if problems is None:
            problems = self._all_problems()
        prefix = f"Item {self._current[1].id}:"
        mine = [p[len(prefix):].strip() for p in problems if p.startswith(prefix)]
        self.i_problem_label.setText("\n".join(mine))

    def _refresh_file_label(self) -> None:
        name = self.path if self.path else "not saved yet"
        self.file_label.setText(f"{name}{'  *unsaved changes' if self.dirty else ''}")

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(RED if error else "")

    # ======================================================================
    # Files
    # ======================================================================

    def confirm(self, question: str) -> bool:
        """Ask a yes/no question. (Tests replace this so no dialog appears.)"""
        answer = QMessageBox.question(self, "Circuitscope", question)
        return answer == QMessageBox.Yes

    def confirm_discard(self) -> bool:
        """True if it is fine to throw away the current assessment (it is saved, or the user agrees)."""
        return (not self.dirty) or self.confirm("There are unsaved changes. Discard them?")

    def _load_assessment(self, assessment: Assessment, path: str | None) -> None:
        self.assessment = assessment
        self.path = path
        self.dirty = False
        self._current = None
        self._bad_expected.clear()
        self._rebuild_tree()
        self._refresh_file_label()

    def new_assessment(self) -> bool:
        if not self.confirm_discard():
            return False
        self._load_assessment(Assessment(title="Untitled practical"), None)
        self._set_status("New assessment.")
        return True

    def open_file(self, path: str) -> bool:
        """Load an assessment from a .json file. A bad file leaves the current one untouched."""
        try:
            loaded = Assessment.load(path)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:   # JSON errors are ValueErrors
            self._set_status(f"Could not open: {describe_load_error(path, exc)}", error=True)
            return False
        self._load_assessment(loaded, path)
        problems = len(self._all_problems())
        self._set_status(f"Opened {path}" + (f" ({problems} problem(s) to fix)." if problems else "."))
        return True

    def save_file(self, path: str | None = None) -> bool:
        """Save to `path` (or the current file; asks for a name the first time)."""
        path = path or self.path
        if not path:
            return self._save_as_clicked()
        try:
            self.assessment.save(path)
        except OSError as exc:
            self._set_status(f"Could not save to '{path}': {exc.strerror or exc}. "
                             f"Check that the folder exists and that you are allowed to write there.", error=True)
            return False
        self.path = path
        self.dirty = False
        self._refresh_file_label()
        n = len(self._all_problems())
        self._set_status(f"Saved to {path}." + (f" Note: {n} problem(s) still to fix." if n else ""))
        return True

    def export_for_students(self, path: str) -> bool:
        """
        Write the copy students receive. Refused while there are problems. The tutor-only model
        answers are left out; everything else (and the fingerprint) is identical to the tutor's file.
        This does not change which file is open, or whether it has unsaved changes.
        """
        problems = self._all_problems()
        if problems:
            self._set_status(f"Not exported: {len(problems)} problem(s) must be fixed first. "
                             f"The first is: {problems[0]}", error=True)
            return False
        import copy
        student_copy = copy.deepcopy(self.assessment)
        for _q, _p, item in student_copy.iter_items():
            item.model_answer = ""
        try:
            student_copy.save(path)
        except OSError as exc:
            self._set_status(f"Could not export to '{path}': {exc.strerror or exc}. "
                             f"Check that the folder exists and that you are allowed to write there.", error=True)
            return False
        self._set_status(f"Exported the student copy to {path}. Model answers were left out.")
        return True

    # ----- the buttons that show file dialogs -------------------------------------

    def _new_clicked(self) -> None:
        self.new_assessment()

    def _open_clicked(self) -> None:
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open assessment", "", "Assessment (*.json)")
        if path:
            self.open_file(path)

    def _export_clicked(self) -> None:
        suggested = f"{self.assessment.title.strip() or 'assessment'}_students.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export for students", suggested, "Assessment (*.json)")
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
            self.export_for_students(path)

    def _save_as_clicked(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save assessment", "", "Assessment (*.json)")
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        return self.save_file(path)
