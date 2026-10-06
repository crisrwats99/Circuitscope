"""Automatic checks for core/grading.py. Run with:  python -m pytest"""

import math

from core.models import (
    Assessment, Question, Part, Item, Variable, WaveformSpec,
    ItemType, AnswerMode, ScoringMode, Quantity, ToleranceMode, WaveformFeature,
)
from core.grading import (
    grade_item, grade_assessment, tolerance_limits, StudentAnswers, Status,
    OUT_OF_TOLERANCE, THEORY_INCORRECT, NOT_ANSWERED, MISSING_VARIABLE,
    FORMULA_ERROR, NO_EXPECTED_VALUE, BAD_ANSWER,
)


# ---------- helpers that keep each test short ----------

def fixed_item(expected=5.0, tol=0.2, mode=ToleranceMode.ABSOLUTE, marks=2, id="1A-1"):
    """A fixed-value voltage item."""
    return Item(id=id, description="V", marks=marks, quantity=Quantity.VOLTAGE,
                probe_pos="P1", probe_neg="P3", expected=expected,
                tolerance_mode=mode, tolerance=tol)


def formula_item(formula="Vs * R2 / (R1 + R2)", tol=0.1, marks=3, id="2A-1"):
    """A varying-answer item (expected value comes from a formula)."""
    return Item(id=id, description="Divider", marks=marks, mode=AnswerMode.VARYING,
                quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                expected_formula=formula, tolerance=tol)


def theory_item(marks=3, id="T-1"):
    return Item(id=id, description="Explain", marks=marks, item_type=ItemType.THEORY)


def one_question(items, scoring=ScoringMode.SUM, qid="1", variables=None):
    """A one-question assessment holding `items`."""
    return Assessment(title="P", questions=[
        Question(id=qid, title="Q", scoring=scoring, variables=variables or [],
                 parts=[Part(id=qid + "A", title="P", items=items)])])


# ---------- tolerance limits ----------

def test_absolute_and_percent_limits():
    low, high = tolerance_limits(5.0, ToleranceMode.ABSOLUTE, 0.2)
    assert (round(low, 6), round(high, 6)) == (4.8, 5.2)
    low, high = tolerance_limits(5.0, ToleranceMode.PERCENT, 5)
    assert (round(low, 6), round(high, 6)) == (4.75, 5.25)


# ---------- single (fixed value) items ----------

def test_inside_tolerance_gets_full_marks():
    assert grade_item(fixed_item(), 5.1).awarded == 2


def test_outside_tolerance_gets_zero_with_explanation():
    r = grade_item(fixed_item(), 5.5)
    assert r.awarded == 0 and r.status == Status.INCORRECT
    assert r.error_code == OUT_OF_TOLERANCE
    assert "5.5" in r.message and "5" in r.message


def test_value_exactly_on_the_edge_is_accepted():
    # 3.3 +/- 5 % has an upper limit of 3.465; rounding must not reject it.
    item = fixed_item(expected=3.3, tol=5, mode=ToleranceMode.PERCENT)
    assert grade_item(item, 3.465).status == Status.CORRECT


def test_negative_expected_value_with_percent_tolerance():
    item = fixed_item(expected=-5.0, tol=10, mode=ToleranceMode.PERCENT)
    assert grade_item(item, -4.6).status == Status.CORRECT
    assert grade_item(item, -5.6).status == Status.INCORRECT


def test_missing_answer_is_flagged():
    r = grade_item(fixed_item(), None)
    assert r.status == Status.NOT_ANSWERED and r.error_code == NOT_ANSWERED


def test_invalid_measured_values_cannot_be_graded():
    for bad in (math.nan, math.inf, "5.0", True):
        r = grade_item(fixed_item(), bad)
        assert r.status == Status.CANNOT_GRADE and r.error_code == BAD_ANSWER


def test_item_without_expected_value_cannot_be_graded():
    item = Item(id="x", description="", marks=1, quantity=Quantity.VOLTAGE)
    r = grade_item(item, 1.0)
    assert r.status == Status.CANNOT_GRADE and r.error_code == NO_EXPECTED_VALUE


# ---------- theory items ----------

def test_theory_items_follow_the_tutor_decision():
    assert grade_item(theory_item(), True).awarded == 3
    r = grade_item(theory_item(), False)
    assert r.awarded == 0 and r.error_code == THEORY_INCORRECT


def test_theory_item_needs_a_true_false_decision():
    r = grade_item(theory_item(), 1.0)
    assert r.status == Status.CANNOT_GRADE and r.error_code == BAD_ANSWER


# ---------- varying items (formula per student) ----------

def test_each_student_is_graded_against_their_own_expected_value():
    item = formula_item()
    a = {"Vs": 5.0, "R1": 1000, "R2": 2200}     # expected 3.4375
    b = {"Vs": 5.0, "R1": 4700, "R2": 4700}     # expected 2.5
    assert grade_item(item, 3.44, a).status == Status.CORRECT
    assert grade_item(item, 2.50, b).status == Status.CORRECT
    # The SAME measured value is right for one student and wrong for the other.
    assert grade_item(item, 3.44, b).status == Status.INCORRECT
    assert grade_item(item, 3.44, a).expected == 3.4375


def test_missing_variable_is_reported_by_name():
    r = grade_item(formula_item(), 3.4, {"Vs": 5.0})
    assert r.status == Status.NOT_ANSWERED and r.error_code == MISSING_VARIABLE
    assert "R1" in r.message and "R2" in r.message


def test_formula_that_cannot_be_calculated_is_flagged_for_the_tutor():
    item = formula_item(formula="Vs / (R1 - R2)")
    r = grade_item(item, 1.0, {"Vs": 5, "R1": 100, "R2": 100})   # divides by zero
    assert r.status == Status.CANNOT_GRADE and r.error_code == FORMULA_ERROR
    assert r.awarded == 0


# ---------- waveform items (graded on the finished feature value) ----------

def test_waveform_item_with_fixed_expected_value():
    item = Item(id="3A-1", description="f", marks=2, mode=AnswerMode.WAVEFORM,
                expected=1000.0, tolerance_mode=ToleranceMode.PERCENT, tolerance=5,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1"))
    assert grade_item(item, 1004.0).status == Status.CORRECT
    assert grade_item(item, 1100.0).status == Status.INCORRECT


def test_waveform_item_with_formula_from_design_parameters():
    # RC filter: f = 1 / (2 pi R C)  (about 159.15 Hz for 1 kOhm and 1 uF)
    item = Item(id="3A-2", description="cutoff", marks=2, mode=AnswerMode.WAVEFORM,
                expected_formula="1 / (2 * pi * R * C)",
                tolerance_mode=ToleranceMode.PERCENT, tolerance=5,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1"))
    values = {"R": 1000, "C": 1e-6}
    assert grade_item(item, 160.0, values).status == Status.CORRECT
    assert grade_item(item, 200.0, values).status == Status.INCORRECT


# ---------- question scoring ----------

def test_sum_scoring_gives_partial_credit():
    a = one_question([fixed_item(id="a"), fixed_item(id="b"), fixed_item(id="c")])
    report = grade_assessment(a, StudentAnswers(item_answers={"a": 5.0, "b": 9.0, "c": 5.0}))
    assert report.question_marks["1"] == 4 and report.total == 4
    assert report.total_available == 6


def test_all_or_nothing_gives_zero_if_any_item_is_wrong():
    a = one_question([fixed_item(id="a"), fixed_item(id="b")], ScoringMode.ALL_OR_NOTHING)
    report = grade_assessment(a, StudentAnswers(item_answers={"a": 5.0, "b": 9.0}))
    assert report.question_marks["1"] == 0
    # ...but the per-item results still show which item was right.
    assert [r.status for r in report.item_results] == [Status.CORRECT, Status.INCORRECT]


def test_all_or_nothing_gives_full_marks_if_every_item_is_correct():
    a = one_question([fixed_item(id="a"), theory_item(id="t")], ScoringMode.ALL_OR_NOTHING)
    report = grade_assessment(a, StudentAnswers(item_answers={"a": 5.0, "t": True}))
    assert report.question_marks["1"] == 5


def test_all_or_nothing_with_an_unanswered_item_gives_zero():
    a = one_question([fixed_item(id="a"), fixed_item(id="b")], ScoringMode.ALL_OR_NOTHING)
    report = grade_assessment(a, StudentAnswers(item_answers={"a": 5.0}))
    assert report.question_marks["1"] == 0


# ---------- whole assessments ----------

def test_variables_belong_to_their_own_question():
    # The name R1 means different things in question 1 and question 2.
    q1 = Question(id="1", title="Q", parts=[Part(id="1A", title="P", items=[
        formula_item(formula="R1 * 2", id="i1", marks=1, tol=0.01)])])
    q2 = Question(id="2", title="Q", parts=[Part(id="2A", title="P", items=[
        formula_item(formula="R1 * 2", id="i2", marks=1, tol=0.01)])])
    a = Assessment(title="P", questions=[q1, q2])
    answers = StudentAnswers(
        item_answers={"i1": 20.0, "i2": 60.0},
        variables={"1": {"R1": 10}, "2": {"R1": 30}})
    report = grade_assessment(a, answers)
    assert report.question_marks == {"1": 1, "2": 1}


def test_a_realistic_mixed_assessment_end_to_end(tmp_path):
    q1 = Question(id="1", title="Fixed", parts=[Part(id="1A", title="P", items=[
        fixed_item(id="1A-1", marks=2), theory_item(id="1A-2", marks=3)])])
    q2 = Question(id="2", title="Varying", scoring=ScoringMode.ALL_OR_NOTHING,
                  variables=[Variable(name="Vs", label="Supply"),
                             Variable(name="R1", label="R1"), Variable(name="R2", label="R2")],
                  parts=[Part(id="2A", title="P", items=[formula_item(id="2A-1", marks=3)])])
    original = Assessment(title="Practical 1", questions=[q1, q2])

    # Save and reload first, as a lecturer's file really travels.
    path = str(tmp_path / "p1.json")
    original.save(path)
    a = Assessment.load(path)

    answers = StudentAnswers(
        item_answers={"1A-1": 5.05, "1A-2": False, "2A-1": 3.44},
        variables={"2": {"Vs": 5.0, "R1": 1000, "R2": 2200}})
    report = grade_assessment(a, answers)
    assert report.question_marks == {"1": 2, "2": 3}      # theory missed, divider right
    assert report.total == 5 and report.total_available == 8