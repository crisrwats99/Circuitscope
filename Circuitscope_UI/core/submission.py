"""
core/submission.py

The file a STUDENT sends back to the lecturer in formal (assessment) mode, and
the lecturer-side code that checks and grades it.

What a submission contains (all RAW DATA, no marks - the lecturer's copy of the
app calculates the marks, so a student cannot simply type in a mark):
    * which assessment it is for (its id and its content fingerprint/hash)
    * the student number typed in, and the STM32 UID of the box used (the watermark)
    * the measured numbers, the student's variable values (R1, Vs ...), and the
      raw waveform samples
    * when it was made

TAMPER PROTECTION (a "tier 2" signature):
    The data is signed with HMAC-SHA256 using a secret key that lives inside the
    app. Change one character of the data (a measurement, the student number...)
    and the signature no longer matches, so loading the file fails.
    Honest limit: a key stored inside a program can be extracted by a determined
    person. It stops casual editing, not a skilled attacker. A later stage can
    move the key into the STM32 so the box itself signs the data.

File layout (JSON):
    {"format": "circuitscope-submission", "version": 1,
     "payload": { ...the data... },
     "signature": "<64 hex characters>"}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.grading import GradeReport, StudentAnswers, grade_assessment
from core.signing_key import SIGNING_KEY
from core.models import Assessment, ItemType
from core.waveform import WaveformError, compute_feature

FILE_FORMAT = "circuitscope-submission"
FILE_VERSION = 1

# The secret signing key is built into the app (core/signing_key.py).
# DEFAULT_KEY is the same value under its older name.
DEFAULT_KEY = SIGNING_KEY

MAX_STUDENT_NUMBER_LENGTH = 32


class SubmissionError(Exception):
    """The submission is missing, damaged, tampered with, or invalid."""


# ---------------------------------------------------------------------------
# The STM32 UID watermark
# ---------------------------------------------------------------------------

def format_uid(word0: int, word1: int, word2: int) -> str:
    """
    The STM32 has a 96-bit unique ID, read from the chip as three 32-bit numbers.
    Join them into one 24-character hex text, e.g. '0012AB34...'.
    """
    for w in (word0, word1, word2):
        if not isinstance(w, int) or not 0 <= w <= 0xFFFFFFFF:
            raise SubmissionError(f"A UID word must be a whole number from 0 to 4294967295, but got {w!r}.")
    return f"{word0:08X}{word1:08X}{word2:08X}"


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

@dataclass
class WaveformCapture:
    """The raw samples taken for one waveform item."""
    samples: list[float]                       # voltages, one per sample
    sample_rate_hz: float                      # how fast they were taken
    ref_samples: list[float] | None = None     # reference channel (gain / phase only)


@dataclass
class Submission:
    """Everything one student sends back."""
    assessment_id: str                         # which assessment (its uuid)
    assessment_hash: str                       # fingerprint of the exact version used
    student_number: str                        # typed in by the student
    device_uid: str                            # STM32 UID (the watermark)
    created_utc: str = ""                      # ISO timestamp, filled in on save if empty
    measurements: dict[str, float] = field(default_factory=dict)       # item id -> value
    variables: dict[str, dict[str, float]] = field(default_factory=dict)  # question id -> {name: value}
    waveforms: dict[str, WaveformCapture] = field(default_factory=dict)   # item id -> capture
    theory_answers: dict[str, str] = field(default_factory=dict)          # item id -> what the student typed

    # --- converting to / from plain dictionaries (so they can become JSON) ---

    def to_payload(self) -> dict:
        """Check the data, then turn it into plain dictionaries and lists."""
        number = self.student_number.strip()
        if not number:
            raise SubmissionError("The student number is empty. Type your student number before submitting.")
        if len(number) > MAX_STUDENT_NUMBER_LENGTH:
            raise SubmissionError(f"The student number is {len(number)} characters long; the maximum is "
                                  f"{MAX_STUDENT_NUMBER_LENGTH}. Check that you typed only your student number.")
        if any(ord(c) < 32 for c in number):
            raise SubmissionError("The student number contains a control character (such as a tab or "
                                  "line break). Retype it using only letters and digits.")
        if not self.device_uid.strip():
            raise SubmissionError("The box's UID is missing, so the submission cannot be tied to a box. "
                                  "Connect to the box and try again.")

        def finite(x, what: str = "a value") -> float:
            """Every number must be real: JSON cannot hold NaN or infinity."""
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
                raise SubmissionError(f"{what} is '{x}', which is not a usable number (it must be an "
                                      f"ordinary number, not text, NaN or infinity).")
            return float(x)

        return {
            "assessment_id": self.assessment_id,
            "assessment_hash": self.assessment_hash,
            "student_number": number,
            "device_uid": self.device_uid.strip(),
            "created_utc": self.created_utc,
            "measurements": {k: finite(v, f"The measurement for item {k}") for k, v in self.measurements.items()},
            "variables": {q: {n: finite(v, f"Variable {n} of question {q}") for n, v in vals.items()}
                          for q, vals in self.variables.items()},
            "theory_answers": {k: str(v) for k, v in self.theory_answers.items()},
            "waveforms": {
                item_id: {
                    "sample_rate_hz": finite(c.sample_rate_hz, f"The sample rate of the waveform for item {item_id}"),
                    "samples": [finite(s, f"A sample of the waveform for item {item_id}") for s in c.samples],
                    "ref_samples": None if c.ref_samples is None
                    else [finite(s, f"A reference sample of the waveform for item {item_id}")
                          for s in c.ref_samples],
                } for item_id, c in self.waveforms.items()
            },
        }

    @classmethod
    def from_payload(cls, p: dict) -> "Submission":
        """The reverse of to_payload (.get / .. default so a missing part cannot crash it)."""
        try:
            return cls(
                assessment_id=p["assessment_id"],
                assessment_hash=p["assessment_hash"],
                student_number=p["student_number"],
                device_uid=p["device_uid"],
                created_utc=p.get("created_utc", ""),
                measurements=dict(p.get("measurements", {})),
                variables={q: dict(v) for q, v in p.get("variables", {}).items()},
                theory_answers={k: str(v) for k, v in p.get("theory_answers", {}).items()},
                waveforms={
                    item_id: WaveformCapture(
                        samples=list(c["samples"]),
                        sample_rate_hz=c["sample_rate_hz"],
                        ref_samples=c.get("ref_samples"))
                    for item_id, c in p.get("waveforms", {}).items()},
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise SubmissionError(f"The submission is damaged: the required field {exc} is missing or has the wrong type.") from exc


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _canonical(payload: dict) -> bytes:
    """
    The payload as ONE exact piece of text: keys sorted, no spaces. The same
    data always gives the same bytes, which is essential - the signature is
    calculated from these bytes, and must be re-calculated identically later.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sign_payload(payload: dict, key: bytes = SIGNING_KEY) -> str:
    """HMAC-SHA256 of the payload, as 64 hex characters."""
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def verify_payload(payload: dict, signature: str, key: bytes = SIGNING_KEY) -> bool:
    """True only if the signature matches this exact payload and key."""
    expected = sign_payload(payload, key)
    # compare_digest takes the same time however many characters match,
    # which stops an attacker guessing a signature one character at a time.
    return hmac.compare_digest(expected, str(signature))


# ---------------------------------------------------------------------------
# Saving and loading
# ---------------------------------------------------------------------------

def save_submission(sub: Submission, path: str, key: bytes = SIGNING_KEY) -> None:
    """Sign the submission and write it to `path` as a .json file."""
    if not sub.created_utc:
        sub.created_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = sub.to_payload()
    document = {"format": FILE_FORMAT, "version": FILE_VERSION,
                "payload": payload, "signature": sign_payload(payload, key)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=1, ensure_ascii=False)


def load_submission(path: str, key: bytes = SIGNING_KEY) -> Submission:
    """Read a submission and REFUSE it if the signature does not match."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            document = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmissionError(f"The file could not be read ({exc}).") from exc

    if not isinstance(document, dict) or document.get("format") != FILE_FORMAT:
        raise SubmissionError("This is not a Circuitscope submission file.")
    if document.get("version") != FILE_VERSION:
        raise SubmissionError(f"Unsupported submission version {document.get('version')}.")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise SubmissionError("The submission has no data.")
    if not verify_payload(payload, document.get("signature", ""), key):
        raise SubmissionError("The signature does not match: the file was changed "
                              "after it was created, or was signed with a different key.")
    return Submission.from_payload(payload)


# ---------------------------------------------------------------------------
# Lecturer side: does it match the assessment, and what mark does it get?
# ---------------------------------------------------------------------------

def check_matches(assessment: Assessment, sub: Submission) -> list[str]:
    """Plain-English problems if this submission does not belong to `assessment`."""
    problems: list[str] = []
    if sub.assessment_id != assessment.assessment_id:
        problems.append("This submission is for a different assessment.")
    elif sub.assessment_hash != assessment.content_hash():
        problems.append("The assessment was edited after this submission was made "
                        "(version fingerprint differs). Marks may not be reliable.")
    known = {item.id for _q, _p, item in assessment.iter_items()}
    for item_id in list(sub.measurements) + list(sub.waveforms) + list(sub.theory_answers):
        if item_id not in known:
            problems.append(f"The submission has data for unknown item '{item_id}'.")
    return problems


def grade_submission(assessment: Assessment, sub: Submission,
                     tutor_decisions: dict[str, bool] | None = None) -> GradeReport:
    """
    Turn a submission into marks.

      1. waveform items: calculate the feature from the raw samples
      2. other measurements: use the recorded number
      3. theory items: use the tutor's True/False (tutor_decisions)
      4. hand all of that to grade_assessment()

    A waveform that cannot be analysed (flat line, under two cycles ...) is
    graded as CANNOT_GRADE with the reason in the message, never silently as zero.
    """
    tutor_decisions = tutor_decisions or {}
    answers = StudentAnswers(variables=sub.variables)
    waveform_errors: dict[str, str] = {}

    for _question, _part, item in assessment.iter_items():
        if item.item_type == ItemType.THEORY:
            answers.item_answers[item.id] = tutor_decisions.get(item.id)
        elif item.id in sub.waveforms:
            capture = sub.waveforms[item.id]
            try:
                if item.waveform is None:
                    raise WaveformError("The item has no waveform setup.")
                answers.item_answers[item.id] = compute_feature(
                    item.waveform.feature, capture.samples,
                    capture.sample_rate_hz, capture.ref_samples)
            except WaveformError as exc:
                waveform_errors[item.id] = f"Waveform could not be analysed: {exc}"
                answers.item_answers[item.id] = math.nan   # makes the grader flag CANNOT_GRADE
        else:
            answers.item_answers[item.id] = sub.measurements.get(item.id)

    report = grade_assessment(assessment, answers)

    # Replace the generic message with the real reason for waveform failures.
    for result in report.item_results:
        if result.item_id in waveform_errors:
            result.message = waveform_errors[result.item_id]
    return report
