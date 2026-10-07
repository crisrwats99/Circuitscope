"""
ui/tutor_panel.py

The TUTOR grading screen. The tutor opens the lecturer's copy of the assessment,
adds the submission files students handed in, and:

    * sees every student with their mark, how many theory answers are still to mark,
      and any warning (assessment edited, one box used by several students)
    * clicks a student to see every item: measured value, expected value, mark, reason
    * reads each typed theory answer next to the model answer and marks it Correct or Incorrect
    * exports the CSV mark sheet, and one feedback file per student at a chosen level

Submissions whose signature fails are refused and listed by file name. The tutor's
theory decisions are saved automatically, so marking can be stopped and continued.
All the rules are in core/tutor.py; this file is only layout and buttons.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QAbstractItemView)

from core.grading import Status
from core.models import Assessment, ItemType
from core.tutor import FeedbackLevel, TutorSession
from ui.assessment_editor import describe_load_error

RED = "color: #b00020;"
GREEN = "color: #2e7d32;"
DEFAULT_MARKING_FOLDER = Path.home() / ".circuitscope" / "marking"

STUDENT_COLUMNS = ["Student number", "Box UID", "Mark", "Theory to mark", "Warnings"]
ITEM_COLUMNS = ["Item", "Description", "Measured", "Expected", "Marks", "Result", "Details"]
LEVELS = [("Marks only", FeedbackLevel.MARKS_ONLY),
          ("Correct / incorrect (no expected values)", FeedbackLevel.CORRECTNESS),
          ("Full detail (values and model answers)", FeedbackLevel.FULL)]


def _fmt(x) -> str:
    return "" if x is None else f"{x:.4g}"


class TutorPanel(QWidget):
    def __init__(self, parent: QWidget | None = None, marking_folder=None):
        super().__init__(parent)
        self.marking_folder = Path(marking_folder) if marking_folder else DEFAULT_MARKING_FOLDER
        self.session: TutorSession | None = None
        self.current_student: str | None = None
        self.current_item: str | None = None

        # ---- top row ----
        self.open_button = QPushButton("Open assessment (lecturer copy)...")
        self.add_button = QPushButton("Add submissions...")
        self.title_label = QLabel("No assessment open.")
        self.title_label.setStyleSheet("font-weight: bold;")
        top = QHBoxLayout()
        for w in (self.open_button, self.add_button, self.title_label):
            top.addWidget(w)
        top.addStretch(1)

        self.load_log = QPlainTextEdit()
        self.load_log.setReadOnly(True)
        self.load_log.setMaximumHeight(60)
        self.load_log.setStyleSheet(RED)
        self.load_log.hide()

        # ---- students (left) ----
        self.students = QTableWidget(0, len(STUDENT_COLUMNS))
        self.students.setHorizontalHeaderLabels(STUDENT_COLUMNS)
        self._configure(self.students)

        # ---- one student's items (right) ----
        self.items = QTableWidget(0, len(ITEM_COLUMNS))
        self.items.setHorizontalHeaderLabels(ITEM_COLUMNS)
        self._configure(self.items)

        self.theory_box = QGroupBox("Theory answer")
        self.answer_view = QPlainTextEdit()
        self.answer_view.setReadOnly(True)
        self.answer_view.setPlaceholderText("The student's answer")
        self.model_view = QPlainTextEdit()
        self.model_view.setReadOnly(True)
        self.model_view.setPlaceholderText("No model answer was written for this item.")
        self.correct_button = QPushButton("Correct")
        self.incorrect_button = QPushButton("Incorrect")
        self.clear_button = QPushButton("Clear")
        buttons = QHBoxLayout()
        for b in (self.correct_button, self.incorrect_button, self.clear_button):
            buttons.addWidget(b)
        buttons.addStretch(1)
        columns = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("Student's answer:"))
        left.addWidget(self.answer_view)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Model answer:"))
        right_col.addWidget(self.model_view)
        columns.addLayout(left)
        columns.addLayout(right_col)
        box_layout = QVBoxLayout(self.theory_box)
        box_layout.addLayout(columns)
        box_layout.addLayout(buttons)
        self.theory_box.hide()

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.items, 2)
        rl.addWidget(self.theory_box, 1)
        splitter = QSplitter()
        splitter.addWidget(self.students)
        splitter.addWidget(right)
        splitter.setSizes([560, 700])             # wide enough that Mark and Warnings are visible
        splitter.setStretchFactor(1, 1)

        # ---- bottom ----
        self.pending_label = QLabel("")
        self.level_box = QComboBox()
        for text, level in LEVELS:
            self.level_box.addItem(text, level.value)
        self.marksheet_button = QPushButton("Export mark sheet...")
        self.feedback_button = QPushButton("Export feedback files...")
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(self.pending_label)
        bottom.addStretch(1)
        bottom.addWidget(QLabel("Feedback level:"))
        bottom.addWidget(self.level_box)
        bottom.addWidget(self.marksheet_button)
        bottom.addWidget(self.feedback_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.load_log)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        layout.addWidget(self.message_label)

        self.open_button.clicked.connect(self._open_clicked)
        self.add_button.clicked.connect(self._add_clicked)
        self.students.itemSelectionChanged.connect(self._student_selected)
        self.items.itemSelectionChanged.connect(self._item_selected)
        self.correct_button.clicked.connect(lambda: self.mark_theory(True))
        self.incorrect_button.clicked.connect(lambda: self.mark_theory(False))
        self.clear_button.clicked.connect(lambda: self.mark_theory(None))
        self.marksheet_button.clicked.connect(self._marksheet_clicked)
        self.feedback_button.clicked.connect(self._feedback_clicked)
        self._refresh()

    @staticmethod
    def _configure(table: QTableWidget) -> None:
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().hide()
        table.horizontalHeader().setStretchLastSection(True)

    # ====================================================================
    # Loading
    # ====================================================================

    def open_assessment(self, path: str) -> bool:
        try:
            assessment = Assessment.load(path)
        except Exception as exc:
            self._say(describe_load_error(path, exc), error=True)
            return False
        problems = assessment.validate()
        if problems:
            self._say(f"This assessment has {len(problems)} problem(s): {problems[0]} Fix it in the "
                      f"Assessment creator first.", error=True)
            return False
        self.session = TutorSession(assessment)
        self.current_student = self.current_item = None
        marking = self._marking_path()
        if marking.exists():
            try:
                self.session.load_marking(str(marking))
            except ValueError as exc:
                self._say(f"Earlier marking was ignored: {exc}", error=True)
        self.title_label.setText(assessment.title)
        self.load_log.clear()
        self.load_log.hide()
        self._refresh()
        self._say("Add the students' submission files to begin.")
        return True

    def add_submissions(self, paths: list[str]) -> None:
        if self.session is None:
            self._say("Open the assessment first.", error=True)
            return
        before = len(self.session.records)
        messages = self.session.load_files(paths)
        self.load_log.setPlainText("\n".join(messages))
        self.load_log.setVisible(bool(messages))
        self._refresh()
        added = len(self.session.records) - before
        self._say(f"Loaded {added} new submission(s); {len(self.session.records)} in total."
                  + (f" {len(messages)} file(s) need attention (see the red box)." if messages else ""),
                  error=bool(messages))

    def _marking_path(self) -> Path:
        return self.marking_folder / f"{self.session.assessment.assessment_id}.json"

    def _autosave(self) -> None:
        try:
            self.marking_folder.mkdir(parents=True, exist_ok=True)
            self.session.save_marking(str(self._marking_path()))
        except OSError as exc:
            self._say(f"Your marking could not be saved automatically ({exc.strerror or exc}).", error=True)

    # ====================================================================
    # Marking
    # ====================================================================

    def select_student(self, student_number: str) -> None:
        for row in range(self.students.rowCount()):
            if self.students.item(row, 0).text() == student_number:
                self.students.selectRow(row)
                return
        raise KeyError(student_number)

    def select_item(self, item_id: str) -> None:
        for row in range(self.items.rowCount()):
            if self.items.item(row, 0).text() == item_id:
                self.items.selectRow(row)
                return
        raise KeyError(item_id)

    def mark_theory(self, correct: bool | None) -> None:
        """Mark the selected theory answer (True, False, or None to clear)."""
        if self.session is None or self.current_student is None or self.current_item is None:
            return
        self.session.set_decision(self.current_student, self.current_item, correct)
        self._autosave()
        student, item = self.current_student, self.current_item
        self._refresh()
        self.select_student(student)
        self.select_item(item)

    def _student_selected(self) -> None:
        rows = self.students.selectionModel().selectedRows()
        self.current_student = self.students.item(rows[0].row(), 0).text() if rows else None
        self.current_item = None
        self._refresh_items()

    def _item_selected(self) -> None:
        rows = self.items.selectionModel().selectedRows()
        self.current_item = self.items.item(rows[0].row(), 0).text() if rows else None
        self._refresh_theory()

    # ====================================================================
    # Exporting
    # ====================================================================

    def export_marksheet(self, path: str) -> bool:
        if self.session is None or not self.session.records:
            self._say("There are no submissions to put in a mark sheet.", error=True)
            return False
        try:
            self.session.write_marksheet(path)
        except OSError as exc:
            self._say(f"Could not save the mark sheet to '{path}': {exc.strerror or exc}. "
                      f"Close it in Excel if it is open, or choose another place.", error=True)
            return False
        pending = self.session.total_pending()
        warning = f" Warning: {pending} theory answer(s) are not marked yet and count as 0." if pending else ""
        self._say(f"Mark sheet saved to {path}.{warning}", error=bool(pending))
        return True

    def export_feedback(self, folder: str) -> bool:
        if self.session is None or not self.session.records:
            self._say("There are no submissions to write feedback for.", error=True)
            return False
        level = FeedbackLevel(self.level_box.currentData())
        try:
            paths = self.session.write_feedback(folder, level)
        except OSError as exc:
            self._say(f"Could not write the feedback files to '{folder}': {exc.strerror or exc}.", error=True)
            return False
        pending = self.session.total_pending()
        warning = f" Warning: {pending} theory answer(s) are not marked yet and count as 0." if pending else ""
        self._say(f"Wrote {len(paths)} feedback file(s) to {folder}.{warning}", error=bool(pending))
        return True

    def _open_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open the lecturer's assessment", "", "Assessment (*.json)")
        if path:
            self.open_assessment(path)

    def _add_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add submissions", "", "Submissions (*.json)")
        if paths:
            self.add_submissions(paths)

    def _marksheet_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save the mark sheet", "marks.csv", "CSV (*.csv)")
        if path:
            self.export_marksheet(path)

    def _feedback_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for the feedback files")
        if folder:
            self.export_feedback(folder)

    # ====================================================================
    # Drawing
    # ====================================================================

    def _say(self, text: str, error: bool = False) -> None:
        self.message_label.setText(text)
        self.message_label.setStyleSheet(RED if error else "")

    def _refresh(self) -> None:
        """Rebuild the student list (and what depends on it)."""
        s = self.session
        have = s is not None and bool(s.records)
        self.add_button.setEnabled(s is not None)
        self.marksheet_button.setEnabled(have)
        self.feedback_button.setEnabled(have)
        self.students.blockSignals(True)
        self.students.setRowCount(0)
        if s is not None:
            for record in s.records:
                report = s.report(record)
                pending = len(s.pending_theory(record))
                notes = s.notes(record)
                values = [record.student_number, record.submission.device_uid,
                          f"{report.total:g} / {report.total_available:g}",
                          str(pending) if pending else "-", "; ".join(notes)]
                row = self.students.rowCount()
                self.students.insertRow(row)
                for col, text in enumerate(values):
                    cell = QTableWidgetItem(text)
                    if col == 4 and notes:
                        cell.setForeground(Qt.red)
                        cell.setToolTip("\n".join(notes))     # the full warnings, since the column is narrow
                    self.students.setItem(row, col, cell)
        self.students.blockSignals(False)
        self.students.resizeColumnsToContents()
        pending_total = s.total_pending() if s else 0
        self.pending_label.setText(f"Theory answers still to mark: {pending_total}" if s and s.records else "")
        self._refresh_items()

    def _refresh_items(self) -> None:
        s = self.session
        self.items.blockSignals(True)
        self.items.setRowCount(0)
        record = None
        if s is not None and self.current_student is not None:
            record = s.record(self.current_student)
        if record is not None:
            results = {r.item_id: r for r in s.report(record).item_results}
            for _q, _p, item in s.assessment.iter_items():
                r = results[item.id]
                theory = item.item_type == ItemType.THEORY
                if theory and item.id not in record.decisions:
                    verdict = "To mark"
                elif r.status == Status.CORRECT:
                    verdict = "✔ Correct"
                elif r.status == Status.INCORRECT:
                    verdict = "✘ Not correct"
                elif r.status == Status.NOT_ANSWERED:
                    verdict = "? Not answered"
                else:
                    verdict = "! Cannot grade"
                details = "" if theory and item.id not in record.decisions else r.message
                values = [item.id, item.description, _fmt(r.measured), _fmt(r.expected),
                          f"{r.awarded:g} / {r.available:g}", verdict, details]
                row = self.items.rowCount()
                self.items.insertRow(row)
                for col, text in enumerate(values):
                    self.items.setItem(row, col, QTableWidgetItem(text))
            self.items.resizeColumnsToContents()
        self.items.blockSignals(False)
        self._refresh_theory()

    def _refresh_theory(self) -> None:
        s, number, item_id = self.session, self.current_student, self.current_item
        show = False
        if s is not None and number is not None and item_id is not None and item_id in s.theory_items():
            show = True
            record = s.record(number)
            item = next(i for _q, _p, i in s.assessment.iter_items() if i.id == item_id)
            self.answer_view.setPlainText(record.submission.theory_answers.get(item_id, ""))
            self.model_view.setPlainText(item.model_answer)
            self.theory_box.setTitle(f"Theory answer: {item.id} {item.description} ({item.marks:g} marks)")
        self.theory_box.setVisible(show)
