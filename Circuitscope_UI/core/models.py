"""
core/models.py   (schema version 2)

Data model for a Circuitscope practical assessment, as created by a lecturer.

Structure (each level holds a list of the next level down):

    Assessment -> Question -> Part -> Item

What is new in version 2
  * Every measurement Item has an ANSWER MODE:
        SINGLE   - one fixed expected value
        VARYING  - expected value comes from a lecturer FORMULA that uses the
                   student's own parameters (e.g. their resistor values)
        WAVEFORM - the box samples a signal; we grade a FEATURE of the
                   waveform (frequency, peak-to-peak, RMS ...)
    A Question sets a default mode; an Item may override it.
  * A Question has a SCORING mode (sum of items, or all-or-nothing) and a list
    of named VARIABLES (R1, R2, Vs ...) that its formulas can use.
  * An Assessment has a unique id and a content hash, so a student's
    submission can be matched to the exact version of the practical.
  * validate() reports mistakes in an assessment BEFORE it is exported.

This file contains NO user-interface code, NO hardware code and does NOT
evaluate formulas (that is the job of core/formulas.py). It only describes
the data and how to save/load it as JSON.
"""

from __future__ import annotations  # lets us write "X | None" type hints

import hashlib                                      # fingerprinting (content hash)
import json                                         # reading/writing .json files
import uuid                                         # unique assessment ids
from dataclasses import dataclass, field, asdict    # tools for building data classes
from enum import Enum                               # fixed lists of allowed choices
from typing import Iterator                         # type hint for generator functions

from core.formulas import CONSTANTS, FUNCTIONS, check_formula   # formula checking (Stage 6)

# Written into every saved file. When the format changes this number goes up,
# so the app can recognise (and upgrade) files saved by older versions.
SCHEMA_VERSION = 2

# Largest number of samples one waveform capture may contain. The STM32G431KB
# has limited RAM, so this is a PLACEHOLDER to be confirmed once the firmware
# exists. Having it as one constant means we change it in one place only.
MAX_WAVEFORM_POINTS = 8000


# ---------------------------------------------------------------------------
# Enums: fixed sets of allowed values.
# Inheriting from `str` as well means they save to JSON as plain text,
# e.g. "voltage" instead of something unreadable.
# ---------------------------------------------------------------------------

class ItemType(str, Enum):
    MEASUREMENT = "measurement"  # graded automatically from the box's data
    THEORY = "theory"            # graded by the tutor as correct/incorrect


class AnswerMode(str, Enum):
    SINGLE = "single"            # compare with one fixed expected value
    VARYING = "varying"          # expected value calculated from a formula
    WAVEFORM = "waveform"        # grade a feature of a sampled waveform


class ScoringMode(str, Enum):
    SUM = "sum"                          # question mark = total of its items' marks
    ALL_OR_NOTHING = "all_or_nothing"    # full marks only if EVERY item is correct


class Quantity(str, Enum):
    VOLTAGE = "voltage"          # measured in volts
    CURRENT = "current"          # measured in amps


class ToleranceMode(str, Enum):
    ABSOLUTE = "absolute"        # e.g. expected 5.0 V, tolerance 0.2 -> accept 4.8 to 5.2
    PERCENT = "percent"          # e.g. expected 5.0 V, tolerance 5   -> accept 4.75 to 5.25


class VariableSource(str, Enum):
    ENTERED = "entered"          # the student types the value in (e.g. a resistor value)
    MEASURED = "measured"        # the box measures it (e.g. the supply voltage)


class WaveformFeature(str, Enum):
    FREQUENCY = "frequency"                  # Hz
    PERIOD = "period"                        # seconds
    PEAK = "peak"                            # maximum value
    PEAK_TO_PEAK = "peak_to_peak"            # maximum minus minimum
    RMS = "rms"                              # root mean square
    DC_OFFSET = "dc_offset"                  # mean value
    DUTY_CYCLE = "duty_cycle"                # fraction of the period spent "high"
    GAIN = "gain"                            # output / reference (needs 2 channels)
    PHASE_DIFFERENCE = "phase_difference"    # degrees (needs 2 channels)
    RISE_TIME = "rise_time"                  # 10 % -> 90 % transition time
    FALL_TIME = "fall_time"                  # 90 % -> 10 % transition time


# Features that compare TWO signals, so they need a reference channel.
TWO_CHANNEL_FEATURES = frozenset({WaveformFeature.GAIN,
                                  WaveformFeature.PHASE_DIFFERENCE})


# ---------------------------------------------------------------------------
# Small helper pieces used inside Items and Questions.
# Every class has a from_dict() that rebuilds it from the plain dictionary
# JSON gives us. Each class knows how to rebuild ITSELF, so the big loader at
# the bottom just asks each level to do its own part.
# `.get(key, default)` is used for fields added in version 2, so an old
# version 1 file (which lacks them) still loads.
# ---------------------------------------------------------------------------

@dataclass
class Variable:
    """A named value a question's formulas can use, e.g. R1 or Vs."""
    name: str                                  # used inside formulas, e.g. "R1"
    label: str                                 # shown to the student, e.g. "Resistor R1"
    unit: str = ""                             # e.g. "ohm", "V" (display only)
    source: VariableSource = VariableSource.ENTERED   # who supplies the value
    # Only used when source is MEASURED: what the box should measure.
    quantity: Quantity | None = None
    probe_pos: str | None = None
    probe_neg: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Variable":
        return cls(
            name=d["name"],
            label=d["label"],
            unit=d.get("unit", ""),
            source=VariableSource(d.get("source", "entered")),   # text -> Enum
            quantity=Quantity(d["quantity"]) if d.get("quantity") else None,
            probe_pos=d.get("probe_pos"),
            probe_neg=d.get("probe_neg"),
        )


@dataclass
class WaveformSpec:
    """How to capture a waveform and which feature of it to grade."""
    feature: WaveformFeature                   # what to extract, e.g. PEAK_TO_PEAK
    channel: str                               # test point to sample, e.g. "P1"
    channel_ref: str | None = None             # reference input (GAIN / PHASE only)
    sample_rate_hz: float = 10_000.0           # samples per second
    num_points: int = 2_000                    # how many samples to take
    trigger_level: float | None = None         # volts; None = automatic (mean level)

    @property
    def duration_s(self) -> float:
        """Length of the capture in seconds = samples / samples-per-second."""
        return self.num_points / self.sample_rate_hz

    @classmethod
    def from_dict(cls, d: dict) -> "WaveformSpec":
        return cls(
            feature=WaveformFeature(d["feature"]),
            channel=d["channel"],
            channel_ref=d.get("channel_ref"),
            sample_rate_hz=d.get("sample_rate_hz", 10_000.0),
            num_points=d.get("num_points", 2_000),
            trigger_level=d.get("trigger_level"),
        )


# ---------------------------------------------------------------------------
# The four levels of the assessment.
# `field(default_factory=list)` gives each object its OWN empty list.
# (Writing `= []` directly would make every object share one list - a classic
# Python bug, which dataclasses refuse to allow.)
# ---------------------------------------------------------------------------

@dataclass
class Item:
    """One gradable thing, e.g. 'Voltage across R2'."""
    id: str                      # unique label, e.g. "1A-1"
    description: str             # text shown to the student/tutor
    marks: float                 # marks available for this item

    # MEASUREMENT (graded from the box) or THEORY (tutor decides).
    item_type: ItemType = ItemType.MEASUREMENT

    # Answer mode for this item. None means "use the question's default_mode".
    mode: AnswerMode | None = None

    # --- Measurement settings (used by SINGLE and VARYING modes) ---
    quantity: Quantity | None = None     # voltage or current; None for theory items
    probe_pos: str | None = None         # test point for the + probe, e.g. "P1"
    probe_neg: str | None = None         # test point for the - probe, e.g. "P3"

    # --- The correct answer: EITHER a fixed number OR a formula ---
    expected: float | None = None            # fixed value (SINGLE, or fixed WAVEFORM target)
    expected_formula: str | None = None      # e.g. "Vs * R2 / (R1 + R2)" (VARYING / WAVEFORM)

    # --- How close the measured value must be ---
    tolerance_mode: ToleranceMode = ToleranceMode.ABSOLUTE   # absolute or percent
    tolerance: float = 0.0                   # size of the allowed error

    # --- Only used in WAVEFORM mode ---
    waveform: WaveformSpec | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        wf = d.get("waveform")                     # a dict, or None
        return cls(
            id=d["id"],
            description=d["description"],
            marks=d["marks"],
            item_type=ItemType(d.get("item_type", "measurement")),
            # IF a mode was saved convert the text to an Enum, ELSE keep None.
            mode=AnswerMode(d["mode"]) if d.get("mode") else None,
            quantity=Quantity(d["quantity"]) if d.get("quantity") else None,
            probe_pos=d.get("probe_pos"),
            probe_neg=d.get("probe_neg"),
            expected=d.get("expected"),
            expected_formula=d.get("expected_formula"),
            tolerance_mode=ToleranceMode(d.get("tolerance_mode", "absolute")),
            tolerance=d.get("tolerance", 0.0),
            waveform=WaveformSpec.from_dict(wf) if wf else None,
        )


@dataclass
class Part:
    """A sub-question such as 1A. Holds one or more items."""
    id: str                                          # e.g. "1A"
    title: str                                       # e.g. "Measurements"
    items: list[Item] = field(default_factory=list)  # starts empty

    @classmethod
    def from_dict(cls, d: dict) -> "Part":
        # Ask the Item class to rebuild each item dictionary.
        return cls(id=d["id"], title=d["title"],
                   items=[Item.from_dict(i) for i in d["items"]])


@dataclass
class Question:
    """A numbered question such as 1. Holds parts, variables and scoring rules."""
    id: str                                          # e.g. "1"
    title: str                                       # e.g. "Resistor network"
    parts: list[Part] = field(default_factory=list)  # starts empty

    # Default answer mode for items in this question (an item can override it).
    default_mode: AnswerMode = AnswerMode.SINGLE
    # How the question's mark is made from its items' marks.
    scoring: ScoringMode = ScoringMode.SUM
    # Named values (R1, Vs ...) that this question's formulas can use.
    variables: list[Variable] = field(default_factory=list)

    def mode_for(self, item: Item) -> AnswerMode:
        """The mode that really applies to `item`: its own, else the question's."""
        return item.mode or self.default_mode

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        return cls(
            id=d["id"],
            title=d["title"],
            parts=[Part.from_dict(p) for p in d["parts"]],
            default_mode=AnswerMode(d.get("default_mode", "single")),
            scoring=ScoringMode(d.get("scoring", "sum")),
            variables=[Variable.from_dict(v) for v in d.get("variables", [])],
        )


@dataclass
class Assessment:
    """A whole practical, created by a lecturer."""
    title: str                                           # e.g. "Practical 1 - Voltage Dividers"
    questions: list[Question] = field(default_factory=list)
    # Unique id made once when the assessment is created and kept for life.
    # default_factory runs the lambda each time, giving every new assessment its own id.
    assessment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = SCHEMA_VERSION                 # file-format version

    # --- Walking through the structure ------------------------------------
    def iter_items(self) -> Iterator[tuple[Question, Part, Item]]:
        """Visit every item in the assessment, yielding (question, part, item)."""
        # `yield` hands back one result at a time instead of building a list.
        for question in self.questions:
            for part in question.parts:
                for item in part.items:
                    yield question, part, item

    def total_marks(self) -> float:
        """Add up the marks of every item in the whole assessment."""
        return sum(item.marks for _q, _p, item in self.iter_items())

    # --- Fingerprint ------------------------------------------------------
    def content_hash(self) -> str:
        """
        A fingerprint of the full contents. Change any mark, tolerance or
        formula and the hash changes, so a submission can prove which exact
        version of the practical it was made for.
        """
        # sort_keys + fixed separators make the text identical every time
        # for identical content (so identical content -> identical hash).
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # --- Checking for mistakes before export ------------------------------
    def validate(self) -> list[str]:
        """
        Look for mistakes in the assessment. Returns a list of plain-English
        problems; an EMPTY list means it looks fine. (Formula SYNTAX is
        checked by core/formulas.py: every formula is parsed and its variable
        names are compared with the ones its question defines.)
        """
        problems: list[str] = []
        seen_ids: set[str] = set()          # item ids already used

        for question in self.questions:
            # --- variable checks for this question ---
            names = [v.name for v in question.variables]
            for name in set(names):
                if names.count(name) > 1:
                    problems.append(f"Question {question.id}: variable '{name}' is defined more than once.")
            for v in question.variables:
                # A variable name must be usable inside a formula: letters, digits
                # and underscores only, not starting with a digit ("R1", "Vs", "C_load").
                if not v.name.isidentifier():
                    problems.append(f"Question {question.id}: variable name '{v.name}' is not valid (use letters, digits and _, not starting with a digit).")
                # ...and must not clash with the built-in names (pi, e, sqrt, ...).
                elif v.name in CONSTANTS or v.name in FUNCTIONS:
                    problems.append(f"Question {question.id}: variable name '{v.name}' is reserved for the formula language (pi, e, sqrt, ...). Choose another name.")
                if v.source == VariableSource.MEASURED and not (
                        v.quantity and v.probe_pos and v.probe_neg):
                    problems.append(f"Question {question.id}: measured variable '{v.name}' needs a quantity and two probe points.")

            # --- item checks ---
            for part in question.parts:
                for item in part.items:
                    where = f"Item {item.id}"          # prefix for messages

                    if item.id in seen_ids:
                        problems.append(f"{where}: id is used more than once.")
                    seen_ids.add(item.id)

                    if item.marks < 0:
                        problems.append(f"{where}: marks cannot be negative.")

                    if item.item_type == ItemType.THEORY:
                        continue                       # nothing more to check

                    if item.tolerance < 0:
                        problems.append(f"{where}: tolerance cannot be negative.")

                    mode = question.mode_for(item)     # the mode that really applies
                    has_fixed = item.expected is not None
                    has_formula = bool(item.expected_formula and item.expected_formula.strip())

                    if mode in (AnswerMode.SINGLE, AnswerMode.VARYING):
                        if not item.quantity:
                            problems.append(f"{where}: choose voltage or current.")
                        if not (item.probe_pos and item.probe_neg):
                            problems.append(f"{where}: both probe points are required.")

                    # Check the formula text itself (syntax, unsafe parts, unknown
                    # variables) using the same parser the grader will use.
                    if has_formula:
                        for message in check_formula(item.expected_formula, names):
                            problems.append(f"{where}: formula problem - {message}")

                    if mode == AnswerMode.SINGLE:
                        if not has_fixed:
                            problems.append(f"{where}: a single-answer item needs an expected value.")
                        if has_formula:
                            problems.append(f"{where}: a single-answer item must not have a formula (use varying mode).")

                    elif mode == AnswerMode.VARYING:
                        if not has_formula:
                            problems.append(f"{where}: a varying-answer item needs a formula.")
                        if has_fixed:
                            problems.append(f"{where}: a varying-answer item must not also have a fixed expected value.")

                    elif mode == AnswerMode.WAVEFORM:
                        # Exactly ONE of fixed value / formula must be given.
                        if has_fixed == has_formula:
                            problems.append(f"{where}: a waveform item needs either an expected value or a formula (not both, not neither).")
                        wf = item.waveform
                        if wf is None:
                            problems.append(f"{where}: a waveform item needs a waveform setup.")
                        else:
                            if wf.feature in TWO_CHANNEL_FEATURES and not wf.channel_ref:
                                problems.append(f"{where}: {wf.feature.value} needs a reference channel.")
                            if wf.sample_rate_hz <= 0 or wf.num_points <= 0:
                                problems.append(f"{where}: sample rate and number of points must be positive.")
                            if wf.num_points > MAX_WAVEFORM_POINTS:
                                problems.append(f"{where}: {wf.num_points} points is more than the box can store ({MAX_WAVEFORM_POINTS}).")
        return problems

    # --- Saving -----------------------------------------------------------
    def save(self, path: str) -> None:
        """Write this assessment to a JSON file at `path`."""
        # "w" = open for writing; `with` closes the file automatically afterwards.
        with open(path, "w", encoding="utf-8") as f:
            # asdict(self) turns this Assessment and ALL the objects nested
            # inside it into plain dictionaries and lists (which JSON can store).
            json.dump(asdict(self), f, indent=2)

    # --- Loading ----------------------------------------------------------
    @staticmethod
    def load(path: str) -> "Assessment":
        """Read an assessment back from a JSON file at `path`."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)               # a plain dictionary, NOT an Assessment yet
        return Assessment._from_dict(data)

    @staticmethod
    def _from_dict(data: dict) -> "Assessment":
        """Rebuild an Assessment from the plain dictionary read from JSON."""
        return Assessment(
            title=data["title"],
            # Each Question rebuilds itself (and, in turn, its Parts and Items).
            questions=[Question.from_dict(q) for q in data["questions"]],
            # Version 1 files have no id: make a new one so every loaded
            # assessment has one.
            assessment_id=data.get("assessment_id") or str(uuid.uuid4()),
            # Whatever the file said, the object now follows the CURRENT format,
            # so saving it again upgrades an old file to version 2.
            schema_version=SCHEMA_VERSION,
        )