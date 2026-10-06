"""
core/formulas.py

Safely calculates a lecturer-written formula such as

        Vs * R2 / (R1 + R2)

using the numbers ONE particular student used, e.g. Vs = 5.0, R1 = 1000,
R2 = 2200. The result is the expected answer for THAT student.

WHY NOT JUST USE Python's eval()?
eval() runs ANY Python code. A formula lives inside a file that gets emailed
around, so someone could hide code in it that deletes files or steals data.
Instead we:
  1. PARSE the text into a tree with Python's `ast` module (parsing only
     reads the text - it does not run anything),
  2. WALK the tree ourselves and allow only plain maths: numbers, variable
     names, + - * / ^ , brackets, and a short list of safe functions.
  Anything else (imports, attributes, strings, comparisons ...) is refused.

What lecturers may write
  * numbers:      5   0.1   1e3   (1e3 means 1000)
  * variables:    any name defined on the question, e.g. R1, Vs  (CASE-SENSITIVE)
  * operators:    +  -  *  /  ^   (^ means "to the power of")
  * brackets:     ( )
  * constants:    pi, e
  * functions:    sqrt, log (natural), log10, exp, sin, cos, tan, atan (radians),
                  abs, min, max

This file has NO user-interface code and NO hardware code.
"""

from __future__ import annotations

import ast        # turns text into a tree WITHOUT running it
import math       # sqrt, log, pi ...

# Longest formula we accept. Stops absurdly long (or deeply nested) text.
MAX_FORMULA_LENGTH = 300

# Biggest power allowed, so something like 9**9**9 cannot freeze the app.
MAX_EXPONENT = 100


# ---------------------------------------------------------------------------
# Errors. Having our OWN error types lets the rest of the app catch
# "a formula problem" without also catching unrelated bugs.
# ---------------------------------------------------------------------------

class FormulaError(Exception):
    """The formula is invalid, unsafe, or cannot be calculated."""


class MissingVariableError(FormulaError):
    """The formula uses variables for which no value was supplied."""

    def __init__(self, names: list[str]):
        self.names = names     # e.g. ["R1", "R2"] - handy for showing the student
        super().__init__("Missing value(s) for: " + ", ".join(names))


# ---------------------------------------------------------------------------
# What is allowed.
# ---------------------------------------------------------------------------

# Constants usable without defining them.
CONSTANTS = {"pi": math.pi, "e": math.e}

# Allowed functions: name -> (the Python function, min arguments, max arguments).
# max of None means "any number from min upwards".
FUNCTIONS = {
    "sqrt":  (math.sqrt,  1, 1),
    "log":   (math.log,   1, 1),     # natural logarithm
    "log10": (math.log10, 1, 1),
    "exp":   (math.exp,   1, 1),
    "sin":   (math.sin,   1, 1),     # angle in RADIANS
    "cos":   (math.cos,   1, 1),
    "tan":   (math.tan,   1, 1),
    "atan":  (math.atan,  1, 1),
    "abs":   (abs,        1, 1),
    "min":   (min,        2, None),
    "max":   (max,        2, None),
}

# Allowed operator node types in the parsed tree.
_BINARY_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_UNARY_OPS = (ast.UAdd, ast.USub)


# ---------------------------------------------------------------------------
# Step 1: read the text and check that every part of it is allowed.
# ---------------------------------------------------------------------------

def _check_node(node: ast.AST, names: set[str]) -> None:
    """
    Raise FormulaError if `node` (or anything inside it) is not allowed.
    Every variable name found is added to the set `names`.
    """
    if isinstance(node, ast.Expression):            # the outer wrapper
        _check_node(node.body, names)

    elif isinstance(node, ast.Constant):            # a literal value
        # Only plain numbers. `True` counts as an int in Python, so exclude
        # bool explicitly; strings, None, complex numbers etc. are refused.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError("only plain numbers are allowed")

    elif isinstance(node, ast.Name):                # a variable or constant name
        names.add(node.id)

    elif isinstance(node, ast.BinOp):               # a + b, a * b, a ** b ...
        if not isinstance(node.op, _BINARY_OPS):
            raise FormulaError("operator not allowed (use + - * / ^ only)")
        _check_node(node.left, names)
        _check_node(node.right, names)

    elif isinstance(node, ast.UnaryOp):             # -a  or  +a
        if not isinstance(node.op, _UNARY_OPS):
            raise FormulaError("operator not allowed")
        _check_node(node.operand, names)

    elif isinstance(node, ast.Call):                # sqrt(x), max(a, b) ...
        # The function must be a plain name from our list: no obj.method(...).
        if not (isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS):
            raise FormulaError("unknown or disallowed function")
        if node.keywords:                           # name=value arguments
            raise FormulaError("named arguments are not allowed")
        _func, low, high = FUNCTIONS[node.func.id]
        count = len(node.args)
        if count < low or (high is not None and count > high):
            raise FormulaError(f"wrong number of arguments for {node.func.id}()")
        for argument in node.args:
            _check_node(argument, names)

    else:
        # Attribute access, subscripts, lists, comparisons, lambdas,
        # if-expressions, strings ... everything else lands here and is refused.
        raise FormulaError(f"'{type(node).__name__}' is not allowed in a formula")


def _parse(text: str) -> tuple[ast.Expression, set[str]]:
    """
    Turn formula text into a checked tree.
    Returns (tree, names) where `names` are the variable/constant names used.
    """
    if not isinstance(text, str) or not text.strip():
        raise FormulaError("the formula is empty")
    if len(text) > MAX_FORMULA_LENGTH:
        raise FormulaError(f"the formula is too long (maximum {MAX_FORMULA_LENGTH} characters)")

    # Lecturers write ^ for "power"; in Python ^ means something else (XOR),
    # and ** is power. Swap them before parsing.
    cleaned = text.strip().replace("^", "**")

    try:
        tree = ast.parse(cleaned, mode="eval")      # mode="eval" = a single expression
        names: set[str] = set()
        _check_node(tree, names)
    except FormulaError:
        raise                                       # already a good message
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise FormulaError(f"could not read the formula: {exc}") from exc
    return tree, names


# ---------------------------------------------------------------------------
# Step 2: calculate the (already checked) tree.
# ---------------------------------------------------------------------------

def _eval(node: ast.AST, values: dict[str, float]) -> float:
    """Calculate a node. Only ever called on a tree that passed _check_node."""
    if isinstance(node, ast.Constant):
        return float(node.value)

    if isinstance(node, ast.Name):
        # A supplied value wins; otherwise it must be a constant like pi.
        return values[node.id] if node.id in values else CONSTANTS[node.id]

    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, values)
        return -operand if isinstance(node.op, ast.USub) else operand

    if isinstance(node, ast.BinOp):
        left = _eval(node.left, values)
        right = _eval(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise FormulaError("division by zero")
            return left / right
        # Power: refuse huge exponents, and refuse results that are not real
        # numbers (a negative number to a fractional power gives a complex one).
        if abs(right) > MAX_EXPONENT:
            raise FormulaError(f"exponent too large (maximum {MAX_EXPONENT})")
        result = left ** right
        if isinstance(result, complex):
            raise FormulaError("the result is not a real number")
        return result

    if isinstance(node, ast.Call):
        function = FUNCTIONS[node.func.id][0]
        return function(*[_eval(a, values) for a in node.args])

    raise FormulaError("unsupported formula")        # unreachable after checking


# ---------------------------------------------------------------------------
# The functions the rest of the app uses.
# ---------------------------------------------------------------------------

def evaluate_formula(text: str, values: dict[str, float | None]) -> float:
    """
    Calculate `text` using `values` (variable name -> number).

        evaluate_formula("Vs * R2 / (R1 + R2)", {"Vs": 5, "R1": 1000, "R2": 2200})
        -> 3.4375

    Raises MissingVariableError if the formula needs a value that was not
    given (a value of None counts as "not given"), and FormulaError for any
    other problem.
    """
    tree, names = _parse(text)

    # Convert supplied values to floats, ignoring ones that are None.
    try:
        numbers = {k: float(v) for k, v in values.items() if v is not None}
    except (TypeError, ValueError) as exc:
        raise FormulaError(f"a supplied value is not a number: {exc}") from exc

    # Every name used must be supplied, or be a constant such as pi.
    missing = sorted(n for n in names if n not in numbers and n not in CONSTANTS)
    if missing:
        raise MissingVariableError(missing)

    try:
        result = _eval(tree.body, numbers)
    except (ZeroDivisionError, OverflowError, ValueError) as exc:
        # e.g. sqrt(-1), log(0), a number too big to hold
        raise FormulaError(f"cannot calculate: {exc}") from exc

    if not math.isfinite(result):
        raise FormulaError("the result is not a finite number")
    return float(result)


def formula_variables(text: str) -> set[str]:
    """The variable names a formula uses (constants such as pi are left out)."""
    _tree, names = _parse(text)
    return {n for n in names if n not in CONSTANTS}


def check_formula(text: str, known_names: set[str] | list[str]) -> list[str]:
    """
    Check a formula WITHOUT calculating it. Returns a list of plain-English
    problems (empty = fine). `known_names` are the variable names the
    question defines. Used by the creator screen and by Assessment checking.
    """
    try:
        _tree, names = _parse(text)
    except FormulaError as exc:
        return [str(exc)]
    known = set(known_names)
    unknown = sorted(n for n in names if n not in known and n not in CONSTANTS)
    return [f"unknown variable '{n}' (defined: {', '.join(sorted(known)) or 'none'})"
            for n in unknown]