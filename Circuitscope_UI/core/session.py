"""
core/session.py

The STUDENT's side of an assessment, with no screen code in it: it remembers what
the student has done so far and turns it into a submission. The student screen
(next stage) is just buttons and labels on top of this class, which keeps the
rules testable without opening a window.

Two modes:
    PRACTICE  the student sees marks and feedback straight away (nothing is sent).
    FORMAL    no marks are shown; the finished work becomes a signed submission
              file for the lecturer.

What each kind of item needs from the student:
    measurement (single / varying)  one measured number from the box
    waveform                        one captured waveform (raw samples)
    theory                          typed text, marked later by the tutor
    variables (R1, Vs ...)          typed in, or measured, before a 'varying' item
                                    can be graded

Current measurement is not built yet, so a Current item is reported as
"unsupported" and does not stop the student from finishing the rest.

AUTOSAVE: save_progress() / load_progress() write a small JSON file so a student
whose computer crashes can carry on. It is NOT signed (it is only a draft); the
signature is added when the real submission is saved.
"""

from __future__ import annotations

import json
import math
from enum import Enum

from core.formulas import FormulaError, formula_variables
from core.grading import GradeReport, StudentAnswers, grade_assessment
from core.models import Assessment, AnswerMode, ItemType, Quantity
from core.submission import (Submission, WaveformCapture,
                             MAX_STUDENT_NUMBER_LENGTH)

PROGRESS_FORMAT = "circuitscope-progress"
PROGRESS_VERSION = 1


class Mode(str, Enum):
    PRACTICE = "practice"
    FORMAL = "formal"


class ItemKind(str, Enum):
    """What the student has to DO for an item."""
    MEASURE = "measure"          # read a voltage with the box
    WAVEFORM = "waveform"        # capture a waveform with the box
    THEORY = "theory"            # type an answer
    UNSUPPORTED = "unsupported"  # cannot be done yet (current measurement)


class ItemState(str, Enum):
    TODO = "todo"
    DONE = "done"
    UNSUPPORTED = "unsupported"


class SessionError(Exception):
    """The student did something the session cannot accept (message says what and why)."""


class StudentSession:
    def __init__(self, assessment: Assessment, mode: Mode = Mode.PRACTICE,
                 student_number: str = "", device_uid: str = ""):
        self.assessment = assessment
        self.mode = mode
        self.student_number = student_number
        self.device_uid = device_uid
        self.measurements: dict[str, float] = {}
        self.variables: dict[str, dict[str, float]] = {}
        self.waveforms: dict[str, WaveformCapture] = {}
        self.theory_answers: dict[str, str] = {}
        self._items = {item.id: (q, item) for q, _p, item in assessment.iter_items()}

    # ---- looking at the items ---------------------------------------------------

    def _item(self, item_id: str) -> tuple:
        if item_id not in self._items:
            raise SessionError(f"There is no item '{item_id}' in this assessment.")
        return self._items[item_id]

    def kind(self, item_id: str) -> ItemKind:
        question, item = self._item(item_id)
        if item.item_type == ItemType.THEORY:
            return ItemKind.THEORY
        if item.quantity == Quantity.CURRENT:
            return ItemKind.UNSUPPORTED
        if question.mode_for(item) == AnswerMode.WAVEFORM:
            return ItemKind.WAVEFORM
        return ItemKind.MEASURE

    def state(self, item_id: str) -> ItemState:
        kind = self.kind(item_id)
        if kind == ItemKind.UNSUPPORTED:
            return ItemState.UNSUPPORTED
        done = {
            ItemKind.MEASURE: item_id in self.measurements,
            ItemKind.WAVEFORM: item_id in self.waveforms,
            ItemKind.THEORY: bool(self.theory_answers.get(item_id, "").strip()),
        }[kind]
        return ItemState.DONE if done else ItemState.TODO

    def variables_needed(self, item_id: str) -> list[str]:
        """Names of the variables this item's formula uses (empty for fixed items)."""
        _q, item = self._item(item_id)
        if not (item.expected_formula and item.expected_formula.strip()):
            return []
        try:
            return sorted(formula_variables(item.expected_formula))
        except FormulaError:
            return []                      # a broken formula is the lecturer's problem, not the student's

    def missing_variables(self, item_id: str) -> list[str]:
        """Variables the item needs that the student has not given a value yet."""
        question, _item = self._item(item_id)
        given = self.variables.get(question.id, {})
        return [n for n in self.variables_needed(item_id) if n not in given]

    def progress(self) -> tuple[int, int]:
        """(items done, items that can be done). Unsupported items are not counted."""
        states = [self.state(i) for i in self._items]
        doable = [s for s in states if s != ItemState.UNSUPPORTED]
        return sum(s == ItemState.DONE for s in doable), len(doable)

    # ---- recording what the student did -------------------------------------------

    def record_measurement(self, item_id: str, value: float) -> None:
        if self.kind(item_id) != ItemKind.MEASURE:
            raise SessionError(f"Item {item_id} is not a voltage measurement, so a measured "
                               f"value cannot be recorded for it.")
        self.measurements[item_id] = _finite(value, f"The measurement for item {item_id}")

    def record_waveform(self, item_id: str, capture: WaveformCapture) -> None:
        if self.kind(item_id) != ItemKind.WAVEFORM:
            raise SessionError(f"Item {item_id} is not a waveform item, so a waveform cannot be recorded for it.")
        if len(capture.samples) < 2:
            raise SessionError(f"The waveform for item {item_id} has only {len(capture.samples)} samples; capture it again.")
        self.waveforms[item_id] = capture

    def set_theory_answer(self, item_id: str, text: str) -> None:
        if self.kind(item_id) != ItemKind.THEORY:
            raise SessionError(f"Item {item_id} is not a theory item, so it takes no typed answer.")
        self.theory_answers[item_id] = text

    def set_variable(self, question_id: str, name: str, value: float) -> None:
        """Give a question's variable a value (e.g. R1 = 1000). Only names the question defines are allowed."""
        question = next((q for q in self.assessment.questions if q.id == question_id), None)
        if question is None:
            raise SessionError(f"There is no question '{question_id}'.")
        defined = [v.name for v in question.variables]
        if name not in defined:
            raise SessionError(f"Question {question_id} has no variable '{name}' "
                               f"(it defines: {', '.join(defined) or 'none'}).")
        self.variables.setdefault(question_id, {})[name] = _finite(value, f"The value of {name}")

    def clear_item(self, item_id: str) -> None:
        """Forget what was recorded for an item so it can be redone."""
        self._item(item_id)
        self.measurements.pop(item_id, None)
        self.waveforms.pop(item_id, None)
        self.theory_answers.pop(item_id, None)

    # ---- practice feedback ------------------------------------------------------------

    def practice_report(self) -> GradeReport:
        """
        Marks for what has been done so far (practice mode only). Theory items are
        left out of the marks: they need a tutor.
        """
        if self.mode != Mode.PRACTICE:
            raise SessionError("Marks are only shown in practice mode. In a formal assessment "
                               "the lecturer marks your submission.")
        # Reuse the lecturer-side grading on a submission built from the current work.
        from core.submission import grade_submission
        return grade_submission(self.assessment, self._build(check_student=False))

    # ---- finishing ------------------------------------------------------------------------

    def problems_before_submit(self) -> list[str]:
        """Plain-English reasons the work cannot be submitted yet (empty = ready)."""
        problems: list[str] = []
        number = self.student_number.strip()
        if not number:
            problems.append("Type your student number.")
        elif len(number) > MAX_STUDENT_NUMBER_LENGTH:
            problems.append(f"The student number is longer than {MAX_STUDENT_NUMBER_LENGTH} characters.")
        if not self.device_uid.strip():
            problems.append("Connect to the box (its ID is saved with your work).")
        for item_id in self._items:
            state = self.state(item_id)
            if state == ItemState.TODO:
                what = {ItemKind.MEASURE: "measure it", ItemKind.WAVEFORM: "capture the waveform",
                        ItemKind.THEORY: "type an answer"}[self.kind(item_id)]
                problems.append(f"Item {item_id}: {what}.")
        for item_id in self._items:
            missing = self.missing_variables(item_id)
            if missing and self.state(item_id) == ItemState.DONE:
                problems.append(f"Item {item_id}: give a value for {', '.join(missing)}.")
        return problems

    def _build(self, check_student: bool = True) -> Submission:
        return Submission(
            assessment_id=self.assessment.assessment_id,
            assessment_hash=self.assessment.content_hash(),
            student_number=self.student_number,
            device_uid=self.device_uid,
            measurements=dict(self.measurements),
            variables={q: dict(v) for q, v in self.variables.items()},
            waveforms=dict(self.waveforms),
            theory_answers={k: v for k, v in self.theory_answers.items() if v.strip()},
        )

    def to_submission(self) -> Submission:
        """The finished work, ready for save_submission(). Refuses if anything is missing."""
        problems = self.problems_before_submit()
        if problems:
            raise SessionError("The work cannot be submitted yet:\n- " + "\n- ".join(problems))
        return self._build()

    # ---- autosave / resume ----------------------------------------------------------------------

    def save_progress(self, path: str) -> None:
        data = {
            "format": PROGRESS_FORMAT, "version": PROGRESS_VERSION,
            "assessment_id": self.assessment.assessment_id,
            "assessment_hash": self.assessment.content_hash(),
            "mode": self.mode.value, "student_number": self.student_number,
            "device_uid": self.device_uid,
            "measurements": self.measurements, "variables": self.variables,
            "theory_answers": self.theory_answers,
            "waveforms": {i: {"samples": c.samples, "sample_rate_hz": c.sample_rate_hz,
                              "ref_samples": c.ref_samples} for i, c in self.waveforms.items()},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, allow_nan=False)

    def load_progress(self, path: str) -> None:
        """Carry on from a saved draft. Refuses a draft made for a different assessment."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"The saved progress could not be read ({exc}).") from exc
        if not isinstance(d, dict) or d.get("format") != PROGRESS_FORMAT:
            raise SessionError("This is not a Circuitscope progress file.")
        if d.get("assessment_id") != self.assessment.assessment_id:
            raise SessionError("This saved progress belongs to a different assessment.")
        if d.get("assessment_hash") != self.assessment.content_hash():
            raise SessionError("The assessment was changed after this progress was saved, "
                               "so it cannot be continued. Start again.")
        try:
            self.mode = Mode(d.get("mode", "practice"))
            self.student_number = d.get("student_number", "")
            self.device_uid = d.get("device_uid", "")
            self.measurements = {k: float(v) for k, v in d.get("measurements", {}).items()}
            self.variables = {q: {n: float(x) for n, x in v.items()} for q, v in d.get("variables", {}).items()}
            self.theory_answers = {k: str(v) for k, v in d.get("theory_answers", {}).items()}
            self.waveforms = {i: WaveformCapture(samples=list(c["samples"]),
                                                 sample_rate_hz=c["sample_rate_hz"],
                                                 ref_samples=c.get("ref_samples"))
                              for i, c in d.get("waveforms", {}).items()}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise SessionError(f"The saved progress is damaged ({exc}).") from exc


def _finite(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SessionError(f"{what} is '{value}', which is not a usable number.")
    return float(value)
