"""
core/grading.py   (version 2)

The grading logic. Given an Assessment (from models.py) and ONE student's
answers, it works out the marks.

How each kind of item is graded
  * THEORY item       - the tutor decides: True (correct) / False (incorrect).
  * MEASUREMENT item  - a number (volts, amps, or a waveform FEATURE such as a
    frequency) is compared with the EXPECTED value, allowing for the tolerance.
    The expected value is either
        - a fixed number (item.expected), or
        - calculated from the item's formula using THIS student's own variable
          values, e.g. "Vs * R2 / (R1 + R2)" (see formulas.py).
    A formula wins if an item has both (Assessment.validate() reports that).

    For WAVEFORM items the grader receives the finished feature number
    (e.g. 998.7 Hz). Turning raw samples into that number is the job of the
    waveform module (a later stage); grading it is identical to any other
    measurement.

Each QUESTION is then scored as the SUM of its items' marks or ALL-OR-NOTHING
(see ScoringMode in models.py).

Like the other core files, this has NO user-interface and NO hardware code:
numbers in, marks out, so it is easy to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from core.formulas import FormulaError, MissingVariableError, evaluate_formula
from core.models import Assessment, Item, ItemType, ScoringMode, ToleranceMode

# Floating-point numbers are not exact (e.g. 3.3 * 5 / 100 may come out as
# 0.16500000000000001). Without a tiny allowance, a value sitting exactly on
# the edge of the tolerance could be marked wrong by rounding error alone.
EPSILON = 1e-9

# Short error codes saved with each result. Feedback and exports use these, so
# keep them stable (they are like the numbered error codes in Miller's grading
# circuit): one code per kind of problem.
OUT_OF_TOLERANCE = "OUT_OF_TOLERANCE"      # measured, but outside the allowed range
THEORY_INCORRECT = "THEORY_INCORRECT"      # tutor marked it incorrect
NOT_ANSWERED = "NOT_ANSWERED"              # nothing recorded for this item
MISSING_VARIABLE = "MISSING_VARIABLE"      # formula needs a value that is missing
FORMULA_ERROR = "FORMULA_ERROR"            # formula could not be calculated
NO_EXPECTED_VALUE = "NO_EXPECTED_VALUE"    # item has neither a value nor a formula
BAD_ANSWER = "BAD_ANSWER"                  # answer is the wrong type (e.g. NaN, text)


class Status(str, Enum):
    """What happened when an item was graded."""
    CORRECT = "correct"
    INCORRECT = "incorrect"            # answered, but wrong
    NOT_ANSWERED = "not_answered"      # nothing (or a needed variable) missing
    CANNOT_GRADE = "cannot_grade"      # a problem the tutor must look at (bad formula etc.)


# ---------------------------------------------------------------------------
# Data going IN: everything recorded for one student.
# ---------------------------------------------------------------------------

@dataclass
class StudentAnswers:
    """Everything recorded for ONE student."""
    # item id -> the measured number (measurement items) or True/False (theory).
    item_answers: dict[str, float | bool | None] = field(default_factory=dict)
    # question id -> {variable name -> value}, e.g. {"2": {"R1": 1000, "Vs": 5.02}}.
    # Variables belong to a question, so the same name (R1) can differ per question.
    variables: dict[str, dict[str, float | None]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Data coming OUT: the results.
# ---------------------------------------------------------------------------

@dataclass
class ItemResult:
    """The outcome of grading ONE item."""
    item_id: str
    awarded: float                  # marks this item earned on its own
    available: float                # marks the item was worth
    status: Status
    error_code: str = ""            # one of the codes above ("" when correct)
    message: str = ""               # human-readable explanation for feedback
    measured: float | None = None   # the value that was measured
    expected: float | None = None   # the expected value for THIS student


@dataclass
class QuestionResult:
    """The outcome for one question, after applying its scoring mode."""
    question_id: str
    awarded: float
    available: float
    scoring: ScoringMode


@dataclass
class GradeReport:
    """The outcome of grading a whole assessment for one student."""
    item_results: list[ItemResult] = field(default_factory=list)
    question_results: list[QuestionResult] = field(default_factory=list)
    total: float = 0.0              # overall mark
    total_available: float = 0.0    # what the assessment was out of

    @property
    def question_marks(self) -> dict[str, float]:
        """question id -> marks, handy for the CSV export (one column each)."""
        return {q.question_id: q.awarded for q in self.question_results}


# ---------------------------------------------------------------------------
# Grading one item.
# ---------------------------------------------------------------------------

def tolerance_limits(expected: float, mode: ToleranceMode,
                     tolerance: float) -> tuple[float, float]:
    """
    The lowest and highest value that counts as correct.

    ABSOLUTE: expected 5.0, tolerance 0.2 -> (4.8, 5.2)
    PERCENT:  expected 5.0, tolerance 5   -> (4.75, 5.25)
    """
    if mode == ToleranceMode.PERCENT:
        # abs() so a negative expected value (e.g. -5 V) still gives a
        # positive-sized band centred on `expected`.
        allowed_error = abs(expected) * tolerance / 100.0
    else:
        allowed_error = tolerance
    return expected - allowed_error, expected + allowed_error


def grade_item(item: Item, answer: float | bool | None,
               variables: dict[str, float | None] | None = None) -> ItemResult:
    """
    Grade ONE item.

    `answer`    the measured number, the tutor's True/False, or None.
    `variables` this student's values for the question's variables
                (only needed when the item has a formula).
    """
    variables = variables or {}

    def result(awarded: float, status: Status, code: str = "", message: str = "",
               measured: float | None = None,
               expected: float | None = None) -> ItemResult:
        """Small helper so each return below stays short and readable."""
        return ItemResult(item.id, awarded, item.marks, status, code, message,
                          measured, expected)

    # --- Nothing recorded: no marks, flagged so the tutor can see it ---
    if answer is None:
        return result(0.0, Status.NOT_ANSWERED, NOT_ANSWERED,
                      "No measurement or decision recorded.")

    # --- THEORY: the tutor already decided, so just award or withhold ---
    if item.item_type == ItemType.THEORY:
        if not isinstance(answer, bool):            # must be exactly True/False
            return result(0.0, Status.CANNOT_GRADE, BAD_ANSWER,
                          "A theory item needs a True/False decision.")
        if answer:
            return result(item.marks, Status.CORRECT)
        return result(0.0, Status.INCORRECT, THEORY_INCORRECT,
                      "Marked incorrect by the tutor.")

    # --- MEASUREMENT: first make sure the answer really is a usable number ---
    # (bool is a kind of int in Python, so True would sneak through as 1.0;
    #  reject it explicitly.)
    if (isinstance(answer, bool) or not isinstance(answer, (int, float))
            or not math.isfinite(answer)):
        return result(0.0, Status.CANNOT_GRADE, BAD_ANSWER,
                      "The measured value is not a valid number.")
    measured = float(answer)

    # --- Work out the expected value for THIS student ---
    if item.expected_formula and item.expected_formula.strip():
        try:
            expected = evaluate_formula(item.expected_formula, variables)
        except MissingVariableError as exc:          # a needed value is missing
            return result(0.0, Status.NOT_ANSWERED, MISSING_VARIABLE,
                          str(exc), measured)
        except FormulaError as exc:                  # e.g. division by zero
            return result(0.0, Status.CANNOT_GRADE, FORMULA_ERROR,
                          f"The formula could not be calculated: {exc}", measured)
    elif item.expected is not None:
        expected = item.expected                     # a fixed number
    else:
        return result(0.0, Status.CANNOT_GRADE, NO_EXPECTED_VALUE,
                      "The item has no expected value or formula.", measured)

    # --- Compare with the allowed range ---
    low, high = tolerance_limits(expected, item.tolerance_mode, item.tolerance)
    if low - EPSILON <= measured <= high + EPSILON:
        return result(item.marks, Status.CORRECT, measured=measured, expected=expected)

    return result(
        0.0, Status.INCORRECT, OUT_OF_TOLERANCE,
        f"Measured {measured:.4g}, expected {expected:.4g} "
        f"(allowed {low:.4g} to {high:.4g}).",
        measured, expected,
    )


# ---------------------------------------------------------------------------
# Grading a whole assessment.
# ---------------------------------------------------------------------------

def grade_assessment(assessment: Assessment, answers: StudentAnswers) -> GradeReport:
    """Grade every item, then score every question, then add up the total."""
    report = GradeReport()

    for question in assessment.questions:
        # This student's values for THIS question's variables (R1, Vs ...).
        variables = answers.variables.get(question.id, {})

        # Grade every item in the question.
        results: list[ItemResult] = []
        for part in question.parts:
            for item in part.items:
                results.append(grade_item(item, answers.item_answers.get(item.id),
                                          variables))
        report.item_results.extend(results)

        # Turn the item results into ONE mark for the question.
        available = sum(r.available for r in results)
        if question.scoring == ScoringMode.ALL_OR_NOTHING:
            # Full marks only if EVERY item is correct, otherwise zero.
            all_correct = all(r.status == Status.CORRECT for r in results)
            awarded = available if all_correct else 0.0
        else:
            # SUM: just add up what each item earned.
            awarded = sum(r.awarded for r in results)

        report.question_results.append(
            QuestionResult(question.id, awarded, available, question.scoring))
        report.total += awarded
        report.total_available += available

    return report