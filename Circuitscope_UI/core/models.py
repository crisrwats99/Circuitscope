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

import base64                                       # images are stored inside the JSON as text
import hashlib                                      # fingerprinting (content hash)
import json                                         # reading/writing .json files
import uuid                                         # unique assessment ids
from dataclasses import dataclass, field, asdict    # tools for building data classes
from enum import Enum                               # fixed lists of allowed choices
from typing import Iterator                         # type hint for generator functions

from core.formulas import CONSTANTS, FUNCTIONS, check_formula   # formula checking (Stage 6)

# Version 3 added question text, model answers and images (version 1 and 2 files still load).
# Written into every saved file. When the format changes this number goes up,
# so the app can recognise (and upgrade) files saved by older versions.
SCHEMA_VERSION = 3

# Largest number of samples one waveform capture may contain. The STM32G431KB
# has limited RAM, so this is a PLACEHOLDER to be confirmed once the firmware
# exists. Having it as one constant means we change it in one place only.
MAX_WAVEFORM_POINTS = 8000

# Largest embedded image (after encoding). Images live INSIDE the assessment file so
# the practical stays a single file; the limit keeps that file (and every student's
# copy) a sensible size. The editor shrinks pasted pictures to fit.
MAX_IMAGE_BYTES = 1_500_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def check_image(image_b64: str | None) -> str | None:
    """None if `image_b64` is a usable embedded image (or empty); otherwise what is wrong."""
    if not image_b64:
        return None
    try:
        data = base64.b64decode(image_b64, validate=True)
    except (ValueError, TypeError):
        return "the image data is damaged (it is not valid base64 text). Remove the image and add it again."
    if not data.startswith(PNG_SIGNATURE):
        return "the image is not a PNG. Remove it and add it again (pasted or chosen pictures are converted to PNG)."
    if len(data) > MAX_IMAGE_BYTES:
        return (f"the image is {len(data) / 1e6:.1f} MB, over the {MAX_IMAGE_BYTES / 1e6:.1f} MB limit. "
                f"Use a smaller picture.")
    return None


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

    # --- What the student reads (added in schema version 3) ---
    prompt: str = ""                         # the full question text (description is the short title)
    image: str | None = None                 # a picture (PNG, base64 text) shown with the question
    model_answer: str = ""                   # THEORY items: what the tutor looks for when marking

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
            prompt=d.get("prompt", ""),
            image=d.get("image"),
            model_answer=d.get("model_answer", ""),
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
    # Text and a picture (e.g. the circuit diagram) shown above the question's parts.
    prompt: str = ""
    image: str | None = None

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
            prompt=d.get("prompt", ""),
            image=d.get("image"),
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
        data = asdict(self)
        # The model answer is for the tutor only (it is removed from the copy given to
        # students), so it must not change the fingerprint or students' submissions
        # would no longer match the tutor's file.
        for question in data["questions"]:
            for part in question["parts"]:
                for item in part["items"]:
                    item.pop("model_answer", None)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # --- Checking for mistakes before export ------------------------------
    def validate(self) -> list[str]:
        """
        Look for mistakes in the assessment. Returns a list of plain-English
        problems; an EMPTY list means it looks fine. Every message says WHAT is
        wrong, WHERE (question / part / item id) and HOW to fix it. (Formula SYNTAX is
        checked by core/formulas.py: every formula is parsed and its variable
        names are compared with the ones its question defines.)
        """
        problems: list[str] = []
        seen_ids: set[str] = set()          # item ids already used
        seen_question_ids: set[str] = set()
        seen_part_ids: set[str] = set()

        if not self.title.strip():
            problems.append("The assessment has no title. Type one on the top line of the tree.")
        if not self.questions:
            problems.append("The assessment has no questions yet. Click '+ Question' to add one.")

        for question in self.questions:
            qname = f"Question {question.id}" if question.id.strip() else "A question with no id"
            # --- question and part ids (marks are reported per question id) ---
            if not question.id.strip():
                problems.append(f"A question has no id (its title is '{question.title}'). Type an id such as 1.")
            elif question.id in seen_question_ids:
                problems.append(f"{qname}: the id '{question.id}' is used by more than one question. "
                                f"Give each question its own id.")
            seen_question_ids.add(question.id)
            if not any(part.items for part in question.parts):
                problems.append(f"{qname}: has no items yet. Add a part with '+ Part', then '+ Item'.")
            if (message := check_image(question.image)):
                problems.append(f"{qname}: {message}")
            for part in question.parts:
                if not part.id.strip():
                    problems.append(f"{qname}: a part has no id (its title is '{part.title}'). Type an id such as 1A.")
                elif part.id in seen_part_ids:
                    problems.append(f"Part {part.id}: the id '{part.id}' is used by more than one part. "
                                    f"Give each part its own id.")
                seen_part_ids.add(part.id)

            # --- variable checks for this question ---
            names = [v.name for v in question.variables]
            for name in set(names):
                if names.count(name) > 1:
                    problems.append(f"{qname}: the variable name '{name}' is used {names.count(name)} times. "
                                    f"Each variable needs its own name.")
            for v in question.variables:
                # A variable name must be usable inside a formula: letters, digits
                # and underscores only, not starting with a digit ("R1", "Vs", "C_load").
                if not v.name:
                    problems.append(f"{qname}: a variable has no name. Type one such as R1.")
                elif not v.name.isidentifier():
                    problems.append(f"{qname}: the variable name '{v.name}' cannot be used in a formula. "
                                    f"Use only letters, digits and _, and do not start with a digit "
                                    f"(for example R1, Vs, C_load).")
                # ...and must not clash with the built-in names (pi, e, sqrt, ...).
                elif v.name in CONSTANTS or v.name in FUNCTIONS:
                    problems.append(f"{qname}: the variable name '{v.name}' is reserved for the formula "
                                    f"language (pi, e, sqrt, ...). Choose another name.")
                if v.source == VariableSource.MEASURED:
                    missing = [what for what, value in (("a quantity (voltage or current)", v.quantity),
                                                        ("a + probe point", v.probe_pos),
                                                        ("a - probe point", v.probe_neg)) if not value]
                    if missing:
                        problems.append(f"{qname}: the measured variable '{v.name}' still needs "
                                        f"{' and '.join(missing)}.")
                    elif v.probe_pos == v.probe_neg:
                        problems.append(f"{qname}: the measured variable '{v.name}' has both probes on "
                                        f"{v.probe_pos}, so it would always read 0. Choose two different points.")

            # --- item checks ---
            for part in question.parts:
                for item in part.items:
                    where = f"Item {item.id}"          # prefix for messages

                    if not item.id.strip():
                        problems.append(f"Part {part.id}: an item has no id (its description is "
                                        f"'{item.description}'). Type an id such as {part.id}-1.")
                        where = "An item with no id"
                    elif item.id in seen_ids:
                        problems.append(f"{where}: the id '{item.id}' is used by more than one item. "
                                        f"Give each item its own id.")
                    seen_ids.add(item.id)

                    if not item.description.strip():
                        problems.append(f"{where}: has no description. Type a short title so students and "
                                        f"tutors can tell what it asks.")
                    if item.marks < 0:
                        problems.append(f"{where}: the marks are {item.marks:g}, which is negative. "
                                        f"Marks must be 0 or more.")
                    if (message := check_image(item.image)):
                        problems.append(f"{where}: {message}")

                    if item.item_type == ItemType.THEORY:
                        continue                       # nothing more to check

                    if item.tolerance < 0:
                        problems.append(f"{where}: the tolerance is {item.tolerance:g}, which is negative. "
                                        f"The tolerance must be 0 or more.")

                    mode = question.mode_for(item)     # the mode that really applies
                    has_fixed = item.expected is not None
                    has_formula = bool(item.expected_formula and item.expected_formula.strip())

                    if mode in (AnswerMode.SINGLE, AnswerMode.VARYING):
                        if not item.quantity:
                            problems.append(f"{where}: choose whether it measures voltage or current.")
                        if not item.probe_pos:
                            problems.append(f"{where}: the + probe point is not chosen. Pick a test point "
                                            f"(P0 to P5) for 'Probe + (red)'.")
                        if not item.probe_neg:
                            problems.append(f"{where}: the - probe point is not chosen. Pick a test point "
                                            f"(P0 to P5) for 'Probe - (black)'.")
                        if item.probe_pos and item.probe_pos == item.probe_neg:
                            problems.append(f"{where}: both probes are on {item.probe_pos}, so the reading "
                                            f"would always be 0. Choose two different test points.")

                    # Check the formula text itself (syntax, unsafe parts, unknown
                    # variables) using the same parser the grader will use.
                    if has_formula:
                        for message in check_formula(item.expected_formula, names):
                            problems.append(f"{where}: formula problem - {message}")

                    if mode == AnswerMode.SINGLE:
                        if not has_fixed:
                            problems.append(f"{where}: has no expected value. Type the number the student "
                                            f"should measure, e.g. 3.3.")
                        if has_formula:
                            problems.append(f"{where}: is a single-answer item but has a formula. Delete the "
                                            f"formula, or change the answer type to 'Varying (formula)'.")

                    elif mode == AnswerMode.VARYING:
                        if not has_formula:
                            problems.append(f"{where}: is a varying-answer item but has no formula. Type one "
                                            f"such as Vs * R2 / (R1 + R2) using this question's variables.")
                        if has_fixed:
                            problems.append(f"{where}: is a varying-answer item but also has a fixed expected "
                                            f"value. Delete the fixed value, or change the answer type to "
                                            f"'Single value'.")

                    elif mode == AnswerMode.WAVEFORM:
                        # Exactly ONE of fixed value / formula must be given.
                        if not has_fixed and not has_formula:
                            problems.append(f"{where}: has no expected value for the waveform feature. Type "
                                            f"a number, or a formula using this question's variables.")
                        elif has_fixed and has_formula:
                            problems.append(f"{where}: has both an expected value and a formula. Keep only one.")
                        wf = item.waveform
                        if wf is None:
                            problems.append(f"{where}: is a waveform item but has no capture setup.")
                        else:
                            if wf.feature in TWO_CHANNEL_FEATURES and not wf.channel_ref:
                                problems.append(f"{where}: '{wf.feature.value}' compares two signals, so it "
                                                f"needs a reference channel. Choose one.")
                            if wf.channel_ref and wf.channel_ref == wf.channel:
                                problems.append(f"{where}: the reference channel is the same as the signal "
                                                f"channel ({wf.channel}). Choose a different reference.")
                            if wf.sample_rate_hz <= 0:
                                problems.append(f"{where}: the sample rate is {wf.sample_rate_hz:g} Hz. "
                                                f"It must be above 0.")
                            if wf.num_points <= 0:
                                problems.append(f"{where}: the number of points is {wf.num_points}. "
                                                f"It must be at least 1.")
                            if wf.num_points > MAX_WAVEFORM_POINTS:
                                problems.append(f"{where}: {wf.num_points} points is more than the box can "
                                                f"store ({MAX_WAVEFORM_POINTS}). Use fewer points.")
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
