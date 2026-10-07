"""Automatic checks for core/formulas.py. Run with:  python -m pytest"""

import pytest   # `pytest.raises`, `pytest.approx` and `parametrize` come from here

from core.formulas import (
    evaluate_formula, formula_variables, check_formula,
    FormulaError, MissingVariableError, MAX_FORMULA_LENGTH,
)


# ---------- normal calculations ----------

def test_voltage_divider_for_two_different_students():
    formula = "Vs * R2 / (R1 + R2)"
    a = evaluate_formula(formula, {"Vs": 5.0, "R1": 1000, "R2": 2200})
    b = evaluate_formula(formula, {"Vs": 5.0, "R1": 4700, "R2": 4700})
    assert a == pytest.approx(3.4375)    # approx: floating-point numbers are never exact
    assert b == pytest.approx(2.5)


def test_rc_cutoff_frequency_uses_pi():
    f = evaluate_formula("1 / (2 * pi * R * C)", {"R": 1000, "C": 1e-6})
    assert f == pytest.approx(159.1549, rel=1e-4)


def test_caret_means_power():
    assert evaluate_formula("R ^ 2", {"R": 3}) == 9


def test_operator_precedence_and_brackets():
    assert evaluate_formula("2 + 3 * 4", {}) == 14
    assert evaluate_formula("(2 + 3) * 4", {}) == 20


def test_unary_minus_and_scientific_notation():
    assert evaluate_formula("-R1 + 1e3", {"R1": 400}) == 600


def test_allowed_functions():
    assert evaluate_formula("sqrt(16)", {}) == 4
    assert evaluate_formula("log10(1000)", {}) == pytest.approx(3)
    assert evaluate_formula("abs(-3)", {}) == 3
    assert evaluate_formula("max(1, X, 3)", {"X": 7}) == 7


def test_variable_names_are_case_sensitive():
    with pytest.raises(MissingVariableError):
        evaluate_formula("vs * 2", {"Vs": 5})


# ---------- missing values ----------

def test_missing_variables_are_reported_by_name():
    with pytest.raises(MissingVariableError) as info:
        evaluate_formula("Vs * R2 / (R1 + R2)", {"Vs": 5})
    assert info.value.names == ["R1", "R2"]


def test_none_counts_as_missing():
    with pytest.raises(MissingVariableError):
        evaluate_formula("R1 * 2", {"R1": None})


# ---------- maths that cannot be calculated ----------

@pytest.mark.parametrize("formula", [
    "1 / 0",              # division by zero
    "1 / (R1 - R1)",      # division by zero via variables
    "sqrt(0 - 1)",        # square root of a negative
    "log(0)",             # log of zero
    "(0 - 8) ^ (1 / 3)",  # not a real number
    "2 ^ 10000",          # exponent too large
    "9 ^ 9 ^ 9",          # would freeze a naive evaluator
])
def test_impossible_maths_raises_formula_error(formula):
    with pytest.raises(FormulaError):
        evaluate_formula(formula, {"R1": 5})


# ---------- bad or malicious text must be REFUSED ----------

@pytest.mark.parametrize("formula", [
    "__import__('os').system('echo hacked')",   # run a command
    "open('secret.txt').read()",                # read a file
    "R1.real",                                  # attribute access
    "[1, 2, 3]",                                # a list
    "'abc'",                                    # a string
    "R1 if R1 else 2",                          # if-expression
    "lambda: 1",                                # a function
    "R1 < 2",                                   # comparison
    "True",                                     # a boolean
    "R1 // 2",                                  # operator not on the list
    "R1 % 2",                                   # operator not on the list
    "sqrt(x=4)",                                # named argument
    "sqrt(1, 2)",                               # wrong number of arguments
    "R1[0]",                                    # subscript
    "R1 +",                                     # incomplete
    "",                                         # empty
    "   ",                                      # blank
])
def test_unsafe_or_invalid_formulas_are_refused(formula):
    with pytest.raises(FormulaError):
        evaluate_formula(formula, {"R1": 5})


def test_overlong_formula_is_refused():
    with pytest.raises(FormulaError):
        evaluate_formula("1+" * MAX_FORMULA_LENGTH + "1", {})


def test_non_numeric_value_is_refused():
    with pytest.raises(FormulaError):
        evaluate_formula("R1 * 2", {"R1": "ten"})


# ---------- helpers used by the creator / validation ----------

def test_formula_variables_lists_names_but_not_constants():
    assert formula_variables("Vs * R2 / (R1 + R2) + pi") == {"Vs", "R1", "R2"}


def test_check_formula_accepts_good_formula():
    assert check_formula("Vs * R2 / (R1 + R2)", {"Vs", "R1", "R2"}) == []


def test_check_formula_reports_unknown_variable():
    problems = check_formula("Vs * R3", {"Vs", "R1", "R2"})
    assert len(problems) == 1 and "R3" in problems[0]


def test_check_formula_reports_syntax_and_function_errors():
    assert check_formula("R1 +", {"R1"})            # syntax error -> a message
    assert check_formula("sqr(4)", set())           # misspelt function -> a message


# ---------- error messages say what is wrong and how to fix it ----------

@pytest.mark.parametrize("formula, must_contain", [
    ("5 *", "ends with the operator '*'"),
    ("sqrt(R1", "never closed"),
    ("R1 + R2)", "no matching '('"),
    ("2R1", "Put * between them"),
    ("R1 × R2", "Use * to multiply"),
    ("foo(R1)", "'foo' is not a known function"),
    ("foo(R1)", "sqrt"),
    ("sin()", "sin() needs 1 argument but 0 were given"),
    ("max(R1)", "at least 2 arguments"),
    ("R1 // 2", "'//' is not allowed"),
    ("R1 == 2", "comparison"),
    ("3,3 * R1", "use a point"),
    ("'abc'", "text"),
    ("x.y", "dot"),
])
def test_formula_problems_are_explained(formula, must_contain):
    messages = check_formula(formula, {"R1", "R2"})
    assert messages and must_contain in " ".join(messages)


def test_calculation_failures_are_explained():
    with pytest.raises(FormulaError, match="negative number"):
        evaluate_formula("sqrt(R1 - 5)", {"R1": 1})
    with pytest.raises(FormulaError, match="division by zero"):
        evaluate_formula("1 / R1", {"R1": 0})
