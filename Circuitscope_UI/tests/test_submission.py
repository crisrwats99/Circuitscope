"""Automatic checks for core/submission.py and core/export.py. Run with:  python -m pytest"""

import csv
import json
import math

import numpy as np
import pytest

from core.models import (
    Assessment, Question, Part, Item, WaveformSpec,
    ItemType, AnswerMode, Quantity, ToleranceMode, WaveformFeature,
)
from core.grading import Status
from core.submission import (
    Submission, WaveformCapture, SubmissionError, format_uid, sign_payload,
    verify_payload, save_submission, load_submission, check_matches, grade_submission,
)
from core.export import MarkRow, marksheet_rows, write_marksheet, question_columns

KEY = b"test-key-1234"


# ---------- helpers ----------

def make_assessment() -> Assessment:
    """Q1 fixed + theory (5 marks), Q2 varying (3), Q3 waveform frequency (2) = 10."""
    single = Item(id="1A-1", description="V", marks=2, quantity=Quantity.VOLTAGE,
                  probe_pos="P1", probe_neg="P3", expected=3.3,
                  tolerance_mode=ToleranceMode.PERCENT, tolerance=5)
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY)
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING,
                   quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    wave = Item(id="3A-1", description="f", marks=2, mode=AnswerMode.WAVEFORM,
                expected=1000.0, tolerance_mode=ToleranceMode.PERCENT, tolerance=5,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1",
                                      sample_rate_hz=20_000, num_points=4000))
    return Assessment(title="Practical 1", questions=[
        Question(id="1", title="Fixed", parts=[Part(id="1A", title="m", items=[single, theory])]),
        Question(id="2", title="Varying", parts=[Part(id="2A", title="m", items=[varying])]),
        Question(id="3", title="Wave", parts=[Part(id="3A", title="m", items=[wave])]),
    ])


def sine_capture(freq=1000.0, rate=20_000.0, n=4000) -> WaveformCapture:
    t = np.arange(n) / rate
    return WaveformCapture(samples=list(1.65 + np.sin(2 * np.pi * freq * t)), sample_rate_hz=rate)


def make_submission(a: Assessment, student="STU001", uid="0012AB34CD56EF780011AABB") -> Submission:
    return Submission(
        assessment_id=a.assessment_id, assessment_hash=a.content_hash(),
        student_number=student, device_uid=uid,
        measurements={"1A-1": 3.31, "2A-1": 3.44},
        variables={"2": {"Vs": 5.0, "R1": 1000.0, "R2": 2200.0}},
        waveforms={"3A-1": sine_capture()})


# ---------- UID ----------

def test_format_uid_joins_three_words_into_24_hex_characters():
    assert format_uid(0x0012AB34, 0xCD56EF78, 0x0011AABB) == "0012AB34CD56EF780011AABB"


def test_format_uid_rejects_bad_words():
    for bad in (-1, 2**32, 1.5, "abc"):
        with pytest.raises(SubmissionError):
            format_uid(bad, 0, 0)


# ---------- signing ----------

def test_signature_is_stable_and_depends_on_data_and_key():
    p = {"a": 1, "b": [1.5, 2]}
    assert sign_payload(p, KEY) == sign_payload({"b": [1.5, 2], "a": 1}, KEY)   # key order irrelevant
    assert sign_payload(p, KEY) != sign_payload({"a": 2, "b": [1.5, 2]}, KEY)   # data changed
    assert sign_payload(p, KEY) != sign_payload(p, b"other-key")                # key changed
    assert len(sign_payload(p, KEY)) == 64


def test_verify_payload():
    p = {"a": 1}
    assert verify_payload(p, sign_payload(p, KEY), KEY)
    assert not verify_payload({"a": 2}, sign_payload(p, KEY), KEY)
    assert not verify_payload(p, "nonsense", KEY)


# ---------- saving and loading ----------

def test_submission_round_trip(tmp_path):
    a = make_assessment()
    path = str(tmp_path / "s.json")
    save_submission(make_submission(a), path, KEY)
    loaded = load_submission(path, KEY)
    assert loaded.student_number == "STU001"
    assert loaded.device_uid == "0012AB34CD56EF780011AABB"
    assert loaded.measurements == {"1A-1": 3.31, "2A-1": 3.44}
    assert loaded.created_utc.endswith("+00:00")                 # timestamp filled in (UTC)
    assert len(loaded.waveforms["3A-1"].samples) == 4000


def test_editing_a_measurement_in_the_file_is_detected(tmp_path):
    a = make_assessment()
    path = tmp_path / "s.json"
    save_submission(make_submission(a), str(path), KEY)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["payload"]["measurements"]["1A-1"] = 3.30                # a "helpful" edit
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SubmissionError, match="signature"):
        load_submission(str(path), KEY)


def test_editing_the_student_number_or_uid_is_detected(tmp_path):
    a = make_assessment()
    path = tmp_path / "s.json"
    save_submission(make_submission(a), str(path), KEY)
    for field_name, new in (("student_number", "STU999"), ("device_uid", "FFFFFFFFFFFFFFFFFFFFFFFF")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["payload"][field_name] = new
        bad = tmp_path / f"{field_name}.json"
        bad.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(SubmissionError, match="signature"):
            load_submission(str(bad), KEY)


def test_wrong_key_is_refused(tmp_path):
    path = str(tmp_path / "s.json")
    save_submission(make_submission(make_assessment()), path, KEY)
    with pytest.raises(SubmissionError, match="signature"):
        load_submission(path, b"a-different-key")


def test_damaged_or_foreign_files_are_refused(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("not json at all", encoding="utf-8")
    with pytest.raises(SubmissionError):
        load_submission(str(p), KEY)
    p.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    with pytest.raises(SubmissionError, match="not a Circuitscope"):
        load_submission(str(p), KEY)
    with pytest.raises(SubmissionError):
        load_submission(str(tmp_path / "missing.json"), KEY)


@pytest.mark.parametrize("student", ["", "   ", "x" * 40, "ab\ncd"])
def test_bad_student_numbers_are_refused(student, tmp_path):
    sub = make_submission(make_assessment(), student=student)
    with pytest.raises(SubmissionError):
        save_submission(sub, str(tmp_path / "s.json"), KEY)


def test_missing_uid_and_non_finite_numbers_are_refused(tmp_path):
    a = make_assessment()
    with pytest.raises(SubmissionError):
        save_submission(make_submission(a, uid=" "), str(tmp_path / "a.json"), KEY)
    sub = make_submission(a)
    sub.measurements["1A-1"] = math.nan
    with pytest.raises(SubmissionError):
        save_submission(sub, str(tmp_path / "b.json"), KEY)


# ---------- matching the assessment ----------

def test_matching_submission_has_no_problems():
    a = make_assessment()
    assert check_matches(a, make_submission(a)) == []


def test_submission_for_a_different_assessment_is_reported():
    a = make_assessment()
    sub = make_submission(a)
    sub.assessment_id = "something-else"
    assert "different assessment" in " ".join(check_matches(a, sub))


def test_assessment_edited_after_the_submission_is_reported():
    a = make_assessment()
    sub = make_submission(a)
    a.questions[0].parts[0].items[0].tolerance = 9          # lecturer changes a tolerance
    assert "edited" in " ".join(check_matches(a, sub))


def test_unknown_item_ids_are_reported():
    a = make_assessment()
    sub = make_submission(a)
    sub.measurements["9Z-9"] = 1.0
    assert "9Z-9" in " ".join(check_matches(a, sub))


# ---------- grading a submission ----------

def test_full_marks_submission_end_to_end(tmp_path):
    a = make_assessment()
    path = str(tmp_path / "s.json")
    save_submission(make_submission(a), path, KEY)
    report = grade_submission(a, load_submission(path, KEY), tutor_decisions={"1A-2": True})
    assert report.question_marks == {"1": 5, "2": 3, "3": 2}
    assert report.total == 10 and report.total_available == 10


def test_theory_item_without_tutor_decision_is_not_answered():
    a = make_assessment()
    report = grade_submission(a, make_submission(a))             # tutor has not marked yet
    theory = next(r for r in report.item_results if r.item_id == "1A-2")
    assert theory.status == Status.NOT_ANSWERED and report.total == 7


def test_wrong_waveform_frequency_loses_the_marks():
    a = make_assessment()
    sub = make_submission(a)
    sub.waveforms["3A-1"] = sine_capture(freq=1500.0)
    report = grade_submission(a, sub, {"1A-2": True})
    assert report.question_marks["3"] == 0


def test_unusable_waveform_is_flagged_with_its_reason():
    a = make_assessment()
    sub = make_submission(a)
    sub.waveforms["3A-1"] = WaveformCapture(samples=[1.0] * 4000, sample_rate_hz=20_000)   # flat line
    report = grade_submission(a, sub, {"1A-2": True})
    wave = next(r for r in report.item_results if r.item_id == "3A-1")
    assert wave.status == Status.CANNOT_GRADE
    assert "flat" in wave.message


# ---------- CSV mark sheet ----------

def test_question_columns_are_generated_from_the_assessment():
    cols = question_columns(make_assessment())
    assert cols == [("1", "Q1 (/5)"), ("2", "Q2 (/3)"), ("3", "Q3 (/2)")]


def test_marksheet_contents():
    a = make_assessment()
    good = grade_submission(a, make_submission(a), {"1A-2": True})
    partial = grade_submission(a, make_submission(a))          # theory not marked
    table = marksheet_rows(a, [MarkRow("STU001", "UID1", good), MarkRow("STU002", "UID2", partial)])
    assert table[0] == ["Student number", "Device UID", "Q1 (/5)", "Q2 (/3)", "Q3 (/2)",
                        "Total", "Out of", "Needs review"]
    assert table[1] == ["STU001", "UID1", "5", "3", "2", "10", "10", "0"]
    assert table[2] == ["STU002", "UID2", "2", "3", "2", "7", "10", "1"]


def test_csv_file_is_written_and_opens_in_excel_friendly_form(tmp_path):
    a = make_assessment()
    report = grade_submission(a, make_submission(a), {"1A-2": True})
    path = tmp_path / "marks.csv"
    write_marksheet(str(path), a, [MarkRow("STU001", "UID1", report)])
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                      # the Excel-friendly marker
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2 and rows[1][-3] == "10"


def test_spreadsheet_formula_in_a_student_number_is_neutralised():
    a = make_assessment()
    report = grade_submission(a, make_submission(a), {"1A-2": True})
    table = marksheet_rows(a, [MarkRow("=2+2", "-cmd", report)])
    assert table[1][0] == "'=2+2" and table[1][1] == "'-cmd"


def test_decimal_marks_have_no_float_noise():
    from core.export import _number
    assert _number(0.1 + 0.2) == "0.3" and _number(3.0) == "3" and _number(2.5) == "2.5"
    assert _number(-0.0) == "0"