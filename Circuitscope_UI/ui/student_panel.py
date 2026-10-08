"""
ui/student_panel.py

The STUDENT screen. A student opens the assessment file the lecturer gave them,
types their student number, and works through the items one by one:

    measurement   connect the probes, press "Measure", the reading is recorded
    waveform      press "Capture", the signal is recorded and plotted
    theory        type the answer
    variables     (R1, Vs ...) typed in, or measured, when a formula needs them

PRACTICE mode shows marks and feedback straight away (nothing is sent anywhere).
FORMAL mode shows no marks; when every item is done the student saves a signed
submission file to hand in.

All the rules (what counts as done, what blocks submitting, autosave) live in
core/session.py. This file is only the buttons, labels and layout on top of it.
Work is autosaved after every step, so a crash does not lose it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.models import Assessment, Quantity, VariableSource
from core.session import ItemKind, ItemState, Mode, SessionError, StudentSession
from core.submission import SubmissionError, save_submission
from ui.assessment_editor import describe_load_error, parse_number
from ui.background import run_in_background
from ui.image_picker import b64_to_pixmap

RED = "color: #b00020;"
GREEN = "color: #2e7d32;"
MAX_PICTURE_WIDTH = 480
AUTOSAVE_DELAY_MS = 600          # wait this long after typing stops before saving the draft
DEFAULT_PROGRESS_FOLDER = Path.home() / ".circuitscope" / "progress"

MARKS = {ItemState.DONE: "✔ ", ItemState.TODO: "○ ", ItemState.UNSUPPORTED: "⊘ "}
PAGE_OF = {ItemKind.MEASURE: 0, ItemKind.WAVEFORM: 1, ItemKind.THEORY: 2, ItemKind.UNSUPPORTED: 3}


class StudentPanel(QWidget):
    def __init__(self, parent: QWidget | None = None, progress_folder=None):
        super().__init__(parent)
        self.progress_folder = Path(progress_folder) if progress_folder else DEFAULT_PROGRESS_FOLDER
        self.device = None
        self.session: StudentSession | None = None
        self.current_item: str | None = None
        self._busy = False                 # True while the box is being read
        self._loading = False              # True while the screen is filled in by code (ignore edits)
        self._var_widgets: dict[str, QWidget] = {}
        self._tree_items: dict[str, QTreeWidgetItem] = {}

        # ---- top row: file, mode, student number ----
        self.open_button = QPushButton("Open assessment...")
        self.title_label = QLabel("No assessment open.")
        self.title_label.setStyleSheet("font-weight: bold;")
        self.mode_box = QComboBox()
        self.mode_box.addItem("Practice (shows marks)", Mode.PRACTICE)
        self.mode_box.addItem("Formal (marked by the lecturer)", Mode.FORMAL)
        self.number_edit = QLineEdit()
        self.number_edit.setPlaceholderText("Student number")
        self.number_edit.setMaximumWidth(180)
        top = QHBoxLayout()
        for w in (self.open_button, self.title_label):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(QLabel("Mode:"))
        top.addWidget(self.mode_box)
        top.addWidget(QLabel("Student number:"))
        top.addWidget(self.number_edit)

        # ---- left: the list of items ----
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(260)

        # ---- right: the selected item ----
        self.context_label = QLabel("")           # question text
        self.context_label.setWordWrap(True)
        self.context_label.setStyleSheet("color: #555;")
        self.picture_label = QLabel("")
        self.item_title = QLabel("")
        self.item_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.item_title.setWordWrap(True)
        self.prompt_label = QLabel("")
        self.prompt_label.setWordWrap(True)
        self.prompt_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.variables_box = QGroupBox("Values for this question")
        self.variables_form = QFormLayout(self.variables_box)
        self.variable_message = QLabel("")
        self.variable_message.setWordWrap(True)

        # page 0: measure
        self.probe_hint = QLabel("")
        self.probe_hint.setWordWrap(True)
        self.measure_button = QPushButton("Measure")
        self.reading_label = QLabel("-")
        self.reading_label.setStyleSheet("font-size: 26px; font-weight: bold;")
        page0 = QWidget()
        l0 = QVBoxLayout(page0)
        for w in (self.probe_hint, self.measure_button, self.reading_label):
            l0.addWidget(w)
        # page 1: waveform
        self.wave_hint = QLabel("")
        self.wave_hint.setWordWrap(True)
        self.capture_button = QPushButton("Capture waveform")
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(160)
        self.plot.setMaximumHeight(240)         # leave room for the feedback underneath
        self.plot.setLabel("bottom", "Time", units="ms")
        self.plot.setLabel("left", "Voltage", units="V")
        self.signal_curve = self.plot.plot(pen=pg.mkPen("#f9a825", width=2))
        self.ref_curve = self.plot.plot(pen=pg.mkPen("#00acc1", width=2))
        page1 = QWidget()
        l1 = QVBoxLayout(page1)
        for w in (self.wave_hint, self.capture_button, self.plot):
            l1.addWidget(w)
        # page 2: theory
        self.answer_edit = QPlainTextEdit()
        self.answer_edit.setPlaceholderText("Type your answer here.")
        page2 = QWidget()
        l2 = QVBoxLayout(page2)
        l2.addWidget(self.answer_edit)
        # page 3: unsupported
        self.unsupported_label = QLabel("This item cannot be done with this version of the app, so it is "
                                        "skipped. It does not stop you submitting.")
        self.unsupported_label.setWordWrap(True)
        page3 = QWidget()
        QVBoxLayout(page3).addWidget(self.unsupported_label)

        self.pages = QStackedWidget()
        for p in (page0, page1, page2, page3):
            self.pages.addWidget(p)
        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)

        right = QWidget()
        rl = QVBoxLayout(right)
        for w in (self.context_label, self.picture_label, self.item_title, self.prompt_label,
                  self.variables_box, self.variable_message, self.pages, self.feedback_label):
            rl.addWidget(w)
        rl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(right)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)

        # ---- bottom: progress, problems, submit ----
        self.progress_label = QLabel("")
        self.marks_label = QLabel("")
        self.problems_label = QLabel("")
        self.problems_label.setWordWrap(True)
        self.problems_label.setStyleSheet(RED)
        self.submit_button = QPushButton("Save submission...")
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.progress_label)
        bottom.addWidget(self.marks_label)
        bottom.addStretch(1)
        bottom.addWidget(self.submit_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        layout.addWidget(self.problems_label)
        layout.addWidget(self.message_label)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self.autosave)

        self.open_button.clicked.connect(self._open_clicked)
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.number_edit.textChanged.connect(self._number_changed)
        self.tree.currentItemChanged.connect(self._tree_selection_changed)
        self.measure_button.clicked.connect(self._measure_clicked)
        self.capture_button.clicked.connect(self._capture_clicked)
        self.answer_edit.textChanged.connect(self._theory_changed)
        self.submit_button.clicked.connect(self._submit_clicked)
        self._refresh()

    # ====================================================================
    # Opening an assessment
    # ====================================================================

    def open_assessment(self, path: str) -> bool:
        """Open the file the lecturer gave out. Returns False (and says why) if it cannot be used."""
        try:
            assessment = Assessment.load(path)
        except Exception as exc:                                  # any problem reading the file
            self._say(describe_load_error(path, exc), error=True)
            return False
        problems = assessment.validate()
        if problems:
            self._say(f"This assessment has {len(problems)} problem(s) and cannot be used: {problems[0]} "
                      f"Ask your lecturer for a corrected file.", error=True)
            return False
        if not list(assessment.iter_items()):
            self._say("This assessment has no items, so there is nothing to do.", error=True)
            return False

        self.session = StudentSession(assessment, Mode(self.mode_box.currentData()),
                                      self.number_edit.text(), self._uid())
        resumed, warning = self._try_resume()
        self._build_tree()
        self.title_label.setText(assessment.title)
        self._loading = True
        self.number_edit.setText(self.session.student_number)
        self.mode_box.setCurrentIndex(0 if self.session.mode == Mode.PRACTICE else 1)
        self._loading = False
        self.session.device_uid = self._uid()
        first = next(iter(self._tree_items))
        self.tree.setCurrentItem(self._tree_items[first])
        self._refresh()
        if resumed:
            self._say("Welcome back: your earlier work on this assessment was restored.")
        elif warning:
            self._say(f"{warning} Starting fresh. Choose an item on the left to begin.", error=True)
        else:
            self._say("Choose an item on the left to begin.")
        return True

    def _draft_path(self) -> Path:
        return self.progress_folder / f"{self.session.assessment.assessment_id}.json"

    def _try_resume(self) -> tuple[bool, str]:
        """(restored?, warning). The warning explains a draft that had to be ignored."""
        path = self._draft_path()
        if not path.exists():
            return False, ""
        try:
            self.session.load_progress(str(path))
            return True, ""
        except SessionError as exc:
            return False, f"An old saved draft was ignored: {exc}"

    def autosave(self) -> None:
        """Write the draft (silently; autosave must never get in the student's way)."""
        if self.session is None:
            return
        self._autosave_timer.stop()
        try:
            self.progress_folder.mkdir(parents=True, exist_ok=True)
            self.session.save_progress(str(self._draft_path()))
        except OSError as exc:
            self._say(f"Your work could not be autosaved ({exc.strerror or exc}). "
                      f"Save a submission before closing.", error=True)

    def _uid(self) -> str:
        return getattr(self.device, "uid", "") if self.device is not None else ""

    def set_device(self, device) -> None:
        self.device = device
        if self.session is not None:
            self.session.device_uid = self._uid()
        self._refresh()

    # ====================================================================
    # The list of items
    # ====================================================================

    def _build_tree(self) -> None:
        self.tree.clear()
        self._tree_items = {}
        for question in self.session.assessment.questions:
            q_node = QTreeWidgetItem([f"Question {question.id}: {question.title}"])
            q_node.setData(0, Qt.UserRole, ("q", question.id))
            self.tree.addTopLevelItem(q_node)
            for part in question.parts:
                p_node = QTreeWidgetItem([f"Part {part.id}: {part.title}"])
                p_node.setData(0, Qt.UserRole, ("q", question.id))
                q_node.addChild(p_node)
                for item in part.items:
                    node = QTreeWidgetItem([""])
                    node.setData(0, Qt.UserRole, ("i", item.id))
                    p_node.addChild(node)
                    self._tree_items[item.id] = node
            q_node.setExpanded(True)
            for i in range(q_node.childCount()):
                q_node.child(i).setExpanded(True)

    def select_item(self, item_id: str) -> None:
        self.tree.setCurrentItem(self._tree_items[item_id])

    def _tree_selection_changed(self, node, _previous) -> None:
        if self.session is None or node is None:
            return
        kind, ident = node.data(0, Qt.UserRole)
        if kind == "i":
            self._show_item(ident)
        else:
            self._show_question(ident)

    # ====================================================================
    # Showing a question or an item
    # ====================================================================

    def _show_picture(self, image_b64) -> None:
        pixmap = b64_to_pixmap(image_b64) if image_b64 else None
        if pixmap is not None and not pixmap.isNull():
            self.picture_label.setPixmap(pixmap.scaledToWidth(min(MAX_PICTURE_WIDTH, pixmap.width()),
                                                              Qt.SmoothTransformation))
            self.picture_label.show()
        else:
            self.picture_label.clear()
            self.picture_label.hide()

    def _show_question(self, question_id: str) -> None:
        """A question heading was clicked: show its text and picture only."""
        self.current_item = None
        question = next(q for q in self.session.assessment.questions if q.id == question_id)
        self.context_label.setText(question.prompt)
        self._show_picture(question.image)
        self.item_title.setText(f"Question {question.id}: {question.title}")
        self.prompt_label.setText("Choose an item below this question to work on it.")
        self.variables_box.hide()
        self.variable_message.setText("")
        self.pages.hide()
        self.feedback_label.setText("")

    def _find(self, item_id: str):
        for q, _p, item in self.session.assessment.iter_items():
            if item.id == item_id:
                return q, item
        raise KeyError(item_id)

    def _show_item(self, item_id: str) -> None:
        self.current_item = item_id
        question, item = self._find(item_id)
        kind = self.session.kind(item_id)
        self._loading = True
        self.context_label.setText(question.prompt)
        self._show_picture(item.image or question.image)
        self.item_title.setText(f"{item.id}: {item.description}   ({item.marks:g} mark{'s' if item.marks != 1 else ''})")
        self.prompt_label.setText(item.prompt)
        self.pages.show()
        self.pages.setCurrentIndex(PAGE_OF[kind])
        self.variable_message.setText("")
        self._build_variable_rows(question, item)
        if kind == ItemKind.MEASURE:
            if item.quantity == Quantity.CURRENT:
                self.probe_hint.setText(f"Connect your circuit through the {item.probe_pos} current input, "
                                        f"in series, then press Measure.")
            else:
                self.probe_hint.setText(f"Connect the red probe to {item.probe_pos} and the black probe to "
                                        f"{item.probe_neg}, then press Measure.")
        elif kind == ItemKind.WAVEFORM:
            spec = item.waveform
            ref = f" and the reference to {spec.channel_ref}" if spec.channel_ref else ""
            self.wave_hint.setText(f"Connect the probe to {spec.channel}{ref}, then press Capture "
                                   f"({spec.num_points} samples at {spec.sample_rate_hz:g} Hz).")
        elif kind == ItemKind.THEORY:
            self.answer_edit.setPlainText(self.session.theory_answers.get(item_id, ""))
        self._loading = False
        self._refresh()

    def _build_variable_rows(self, question, item) -> None:
        """One row per variable the item's formula needs (typed in, or measured with the box)."""
        while self.variables_form.rowCount():
            self.variables_form.removeRow(0)
        self._var_widgets = {}
        names = self.session.variables_needed(item.id)
        self.variables_box.setVisible(bool(names))
        by_name = {v.name: v for v in question.variables}
        for name in names:
            variable = by_name.get(name)
            if variable is None:
                continue
            unit = f" ({variable.unit})" if variable.unit else ""
            current = self.session.variables.get(question.id, {}).get(name)
            if variable.source == VariableSource.MEASURED:
                text = f"Measure {name}" if current is None else f"{name} = {current:.4g}  (measure again)"
                button = QPushButton(text)
                where = (f"in series through the {variable.probe_pos} current input"
                         if variable.quantity == Quantity.CURRENT
                         else f"red on {variable.probe_pos}, black on {variable.probe_neg}")
                button.setToolTip(where)
                button.clicked.connect(lambda _=False, q=question.id, v=variable: self._measure_variable(q, v))
                button.setEnabled(self.device is not None and not self._busy)
                self.variables_form.addRow(f"{variable.label}{unit}: {where}", button)
                self._var_widgets[name] = button
            else:
                edit = QLineEdit("" if current is None else f"{current:g}")
                edit.setPlaceholderText("type the value")
                edit.editingFinished.connect(lambda q=question.id, n=name, e=edit: self._variable_typed(q, n, e))
                self.variables_form.addRow(f"{variable.label}{unit}:", edit)
                self._var_widgets[name] = edit

    # ====================================================================
    # What the student does
    # ====================================================================

    def _number_changed(self, text: str) -> None:
        if self.session is not None and not self._loading:
            self.session.student_number = text
            self._autosave_timer.start(AUTOSAVE_DELAY_MS)
            self._refresh()

    def _mode_changed(self, _index: int) -> None:
        if self.session is not None and not self._loading:
            self.session.mode = Mode(self.mode_box.currentData())
            self.autosave()
            self._refresh()

    def _variable_typed(self, question_id: str, name: str, edit: QLineEdit) -> None:
        if self._loading or self.session is None:
            return
        value, error = parse_number(edit.text())
        if error:
            self.variable_message.setText(f"{name}: {error}")
            self.variable_message.setStyleSheet(RED)
            return
        if value is None:
            return                                   # left empty: nothing to record
        self.variable_message.setText("")
        try:
            self.session.set_variable(question_id, name, value)
        except SessionError as exc:
            self.variable_message.setText(str(exc))
            self.variable_message.setStyleSheet(RED)
            return
        self.autosave()
        self._refresh()

    def _measure_variable(self, question_id: str, variable) -> None:
        def done(volts: float) -> None:
            try:
                self.session.set_variable(question_id, variable.name, volts)
            except SessionError as exc:
                self._say(str(exc), error=True)
                return
            self.autosave()
            if self.current_item:                    # redraw the rows so the button shows the new value
                self._show_item(self.current_item)
        if variable.quantity == Quantity.CURRENT:
            self._run(self.device.measure_current, variable.probe_pos, on_result=done)
        else:
            self._run(self.device.measure_voltage, variable.probe_pos, variable.probe_neg, on_result=done)

    def _measure_clicked(self) -> None:
        item_id = self.current_item
        _q, item = self._find(item_id)

        unit = self._unit(item)

        def done(value: float) -> None:
            try:
                self.session.record_measurement(item_id, value)
            except SessionError as exc:
                self._say(str(exc), error=True)
                return
            self.autosave()
            self._say(f"Recorded {value:.3f} {unit} for item {item_id}.")
            self._refresh()
        if item.quantity == Quantity.CURRENT:
            self._run(self.device.measure_current, item.probe_pos, on_result=done)
        else:
            self._run(self.device.measure_voltage, item.probe_pos, item.probe_neg, on_result=done)

    @staticmethod
    def _unit(item) -> str:
        """Voltages are shown in V, currents in mA."""
        return "mA" if item.quantity == Quantity.CURRENT else "V"

    def _capture_clicked(self) -> None:
        item_id = self.current_item
        _q, item = self._find(item_id)

        def done(capture) -> None:
            try:
                self.session.record_waveform(item_id, capture)
            except SessionError as exc:
                self._say(str(exc), error=True)
                return
            self.autosave()
            self._say(f"Waveform recorded for item {item_id} ({len(capture.samples)} samples).")
            self._refresh()
        self._run(self.device.capture_waveform, item.waveform, on_result=done)

    def _theory_changed(self) -> None:
        if self._loading or self.session is None or self.current_item is None:
            return
        if self.session.kind(self.current_item) != ItemKind.THEORY:
            return
        self.session.set_theory_answer(self.current_item, self.answer_edit.toPlainText())
        self._autosave_timer.start(AUTOSAVE_DELAY_MS)
        self._refresh()

    def _run(self, function, *args, on_result) -> None:
        """Run a box reading on a background thread, locking the buttons until it is finished."""
        if self.device is None:
            self._say("Connect to the box first (left of the window).", error=True)
            return
        self._busy = True
        self._say("Reading from the box...")
        self._refresh()

        def failed(message: str) -> None:
            self._say(f"The box could not be read: {message}", error=True)

        def finished() -> None:
            self._busy = False
            self._refresh()
        run_in_background(function, *args, on_result=on_result, on_error=failed, on_finished=finished)

    # ====================================================================
    # Submitting
    # ====================================================================

    def submit(self, path: str) -> bool:
        """Save the signed submission file. Returns False (and says why) if it cannot be made."""
        try:
            save_submission(self.session.to_submission(), path)
        except (SessionError, SubmissionError) as exc:
            self._say(str(exc), error=True)
            return False
        except OSError as exc:
            self._say(f"Could not save to '{path}': {exc.strerror or exc}. "
                      f"Choose another folder.", error=True)
            return False
        self.autosave()
        self._say(f"Submission saved to {path}. Hand in this file.")
        return True

    def _open_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open the assessment", "", "Assessment (*.json)")
        if path:
            self.open_assessment(path)

    def _submit_clicked(self) -> None:
        number = self.session.student_number.strip() or "student"
        title = self.session.assessment.title.replace(" ", "_")
        path, _ = QFileDialog.getSaveFileName(self, "Save your submission", f"{number}_{title}.json",
                                              "Submission (*.json)")
        if path:
            self.submit(path)

    # ====================================================================
    # Keeping the screen up to date
    # ====================================================================

    def _say(self, text: str, error: bool = False) -> None:
        self.message_label.setText(text)
        self.message_label.setStyleSheet(RED if error else "")

    def _has_work(self) -> bool:
        s = self.session
        return bool(s and (s.measurements or s.waveforms or s.variables
                           or any(t.strip() for t in s.theory_answers.values())))

    def _refresh(self) -> None:
        """Redraw everything that depends on the session: list marks, buttons, progress, marks."""
        s = self.session
        have_box = self.device is not None and not self._busy
        self.open_button.setEnabled(not self._busy)
        self.measure_button.setEnabled(have_box and self.current_item is not None)
        self.capture_button.setEnabled(have_box and self.current_item is not None)
        for widget in self._var_widgets.values():
            if isinstance(widget, QPushButton):
                widget.setEnabled(have_box)
        self.number_edit.setEnabled(s is not None)
        # The mode is locked once work exists, so it cannot be flipped to peek at marks and redo.
        self.mode_box.setEnabled(s is not None and not self._has_work())
        if s is None:
            self.submit_button.setEnabled(False)
            self.progress_label.setText("")
            self.marks_label.setText("")
            self.problems_label.setText("")
            return

        for item_id, node in self._tree_items.items():
            _q, item = self._find(item_id)
            node.setText(0, f"{MARKS[s.state(item_id)]}{item_id}  {item.description}")
        done, total = s.progress()
        self.progress_label.setText(f"Progress: {done} of {total} items done")

        report = s.practice_report() if s.mode == Mode.PRACTICE else None
        if report is not None:
            # Items that cannot be done yet (current) are left out of "out of".
            skipped = sum(self._find(i)[1].marks for i in self._tree_items
                          if s.state(i) == ItemState.UNSUPPORTED)
            self.marks_label.setText(f"Marks so far: {report.total:g} / {report.total_available - skipped:g} "
                                     f"(theory items are marked by the tutor)")
        else:
            self.marks_label.setText("Formal mode: marks are not shown. Your lecturer marks the submission.")

        problems = s.problems_before_submit()
        self.problems_label.setText("To do before you can submit:\n" + "\n".join(f"- {p}" for p in problems)
                                    if problems else "")
        self.submit_button.setEnabled(s.mode == Mode.FORMAL and not problems)
        self.submit_button.setToolTip("" if s.mode == Mode.FORMAL else
                                      "Practice work is not submitted. Switch to Formal mode before starting.")

        self._refresh_item(report)

    def _refresh_item(self, report) -> None:
        """The reading, plot and feedback for the item on show."""
        s, item_id = self.session, self.current_item
        if item_id is None:
            return
        kind = s.kind(item_id)
        if kind == ItemKind.MEASURE:
            value = s.measurements.get(item_id)
            unit = self._unit(self._find(item_id)[1])
            self.reading_label.setText("-" if value is None else f"{value:.3f} {unit}")
        elif kind == ItemKind.WAVEFORM:
            capture = s.waveforms.get(item_id)
            if capture is None:
                self.signal_curve.setData([], [])
                self.ref_curve.setData([], [])
            else:
                t_ms = np.arange(len(capture.samples)) / capture.sample_rate_hz * 1000
                self.signal_curve.setData(t_ms, capture.samples)
                if capture.ref_samples:
                    self.ref_curve.setData(t_ms, capture.ref_samples)
                else:
                    self.ref_curve.setData([], [])

        feedback, error = "", False
        if s.state(item_id) == ItemState.DONE:
            if kind == ItemKind.THEORY:
                feedback = "Answer saved. The tutor marks theory answers."
            elif report is not None:
                result = next(r for r in report.item_results if r.item_id == item_id)
                if result.status.value == "correct":
                    feedback = f"Correct: {result.awarded:g} / {result.available:g} marks."
                else:
                    feedback = f"{result.awarded:g} / {result.available:g} marks. {result.message}"
                    error = True
            else:
                feedback = "Recorded."
        self.feedback_label.setText(feedback)
        self.feedback_label.setStyleSheet(RED if error else GREEN)
