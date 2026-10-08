"""Tests for the built-in signing key (core/signing_key.py) and how submissions use it."""

import json

import pytest

from core.models import (Assessment, AnswerMode, Item, ItemType, ModePolicy, Part, Quantity, Question, Variable)
from core.session import Mode, StudentSession
from core.signing_key import SIGNING_KEY
from core.submission import (DEFAULT_KEY, SubmissionError, load_submission, save_submission,
                             sign_payload, verify_payload)
from core.tutor import TutorSession

# ---- small helpers (copied from test_tutor_panel so this file stands alone) ----

def make_assessment() -> Assessment:
    fixed = Item(id="1A-1", description="Supply", marks=2, quantity=Quantity.VOLTAGE, probe_pos="P1",
                 probe_neg="P3", expected=3.0, tolerance=0.1)
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY,
                  model_answer="The resistors share the supply.")
    varying = Item(id="2A-1", description="Divider", marks=3, mode=AnswerMode.VARYING,
                   quantity=Quantity.VOLTAGE, probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    return Assessment(title="Practical 1", questions=[
        Question(id="1", title="Fixed", parts=[Part("1A", "m", [fixed, theory])]),
        Question(id="2", title="Div", default_mode=AnswerMode.VARYING,
                 variables=[Variable("Vs", "Vs", "V"), Variable("R1", "R1", "ohm"), Variable("R2", "R2", "ohm")],
                 parts=[Part("2A", "m", [varying])]),
    ])


def submission(assessment, number, uid="0012AB34CD56EF780011AABB", supply=3.0):
    s = StudentSession(assessment, Mode.FORMAL, number, uid)
    s.record_measurement("1A-1", supply)
    s.set_theory_answer("1A-2", f"Answer typed by {number}")
    for name, value in (("Vs", 3.0), ("R1", 1500.0), ("R2", 1000.0)):
        s.set_variable("2", name, value)
    s.record_measurement("2A-1", 1.2)
    return s.to_submission()




def saved_file(tmp_path, number="A1"):
    path = str(tmp_path / f"{number}.json")
    save_submission(submission(make_assessment(), number), path)
    return path


def test_the_key_is_long_and_is_not_the_old_placeholder():
    assert isinstance(SIGNING_KEY, bytes) and len(SIGNING_KEY) >= 32
    assert SIGNING_KEY != b"change-this-key-before-use"


def test_older_name_still_points_at_the_same_key():
    assert DEFAULT_KEY is SIGNING_KEY


def test_signature_is_64_hex_characters_and_repeatable():
    sig = sign_payload({"x": 1})
    assert len(sig) == 64 and int(sig, 16) >= 0
    assert sig == sign_payload({"x": 1})


def test_changing_one_value_breaks_the_signature():
    sig = sign_payload({"reading": 2.1})
    assert verify_payload({"reading": 2.1}, sig)
    assert not verify_payload({"reading": 3.3}, sig)


def test_a_different_key_does_not_verify():
    sig = sign_payload({"x": 1}, b"some-other-key-0123456789")
    assert not verify_payload({"x": 1}, sig)


def test_save_then_load_needs_no_setup(tmp_path):
    sub = load_submission(saved_file(tmp_path))
    assert sub.student_number == "A1"


def test_editing_a_reading_in_the_file_is_refused(tmp_path):
    path = saved_file(tmp_path)
    text = open(path, encoding="utf-8").read()
    assert "3.0" in text
    open(path, "w", encoding="utf-8").write(text.replace("3.0", "3.3", 1))
    with pytest.raises(SubmissionError):
        load_submission(path)


def test_editing_the_student_number_is_refused(tmp_path):
    path = saved_file(tmp_path)
    document = json.load(open(path, encoding="utf-8"))
    document["payload"]["student_number"] = "Z9"
    json.dump(document, open(path, "w", encoding="utf-8"))
    with pytest.raises(SubmissionError):
        load_submission(path)


def test_a_missing_or_garbage_signature_is_refused(tmp_path):
    path = saved_file(tmp_path)
    for bad in ("", "abc", None):
        document = json.load(open(path, encoding="utf-8"))
        if bad is None:
            del document["signature"]
        else:
            document["signature"] = bad
        json.dump(document, open(path, "w", encoding="utf-8"))
        with pytest.raises(SubmissionError):
            load_submission(path)


def test_the_tutor_loads_a_student_file_with_no_setup(tmp_path):
    t = TutorSession(make_assessment())
    assert t.load_files([saved_file(tmp_path)]) == []
    assert [r.student_number for r in t.records] == ["A1"]


def test_the_tutor_refuses_a_file_signed_with_another_key(tmp_path):
    path = str(tmp_path / "x.json")
    save_submission(submission(make_assessment(), "A1"), path, b"some-other-key-0123456789")
    messages = TutorSession(make_assessment()).load_files([path])
    assert messages and "not loaded" in messages[0]


def test_the_key_is_never_written_into_a_submission_file(tmp_path):
    text = open(saved_file(tmp_path), encoding="utf-8").read()
    assert SIGNING_KEY.decode() not in text


def test_the_key_is_never_written_into_an_exported_student_copy(tmp_path):
    a = make_assessment()
    for policy in ModePolicy:
        path = str(tmp_path / f"{policy.value}.json")
        a.student_copy(policy).save(path)
        assert SIGNING_KEY.decode() not in open(path, encoding="utf-8").read()
