"""Automatic checks for core/models.py. Run with:  python -m pytest"""

import json

from core.models import (
    Assessment, Question, Part, Item, Variable, WaveformSpec,
    ItemType, AnswerMode, ScoringMode, Quantity, ToleranceMode,
    VariableSource, WaveformFeature, MAX_WAVEFORM_POINTS, SCHEMA_VERSION,
)


def sample_assessment() -> Assessment:
    """A small assessment that uses all three answer modes."""
    single = Item(id="1A-1", description="V across R2", marks=2,
                  quantity=Quantity.VOLTAGE, probe_pos="P1", probe_neg="P3",
                  expected=3.3, tolerance_mode=ToleranceMode.PERCENT, tolerance=5)
    varying = Item(id="2A-1", description="Divider output", marks=3,
                   mode=AnswerMode.VARYING, quantity=Quantity.VOLTAGE,
                   probe_pos="P2", probe_neg="P3",
                   expected_formula="Vs * R2 / (R1 + R2)", tolerance=0.1)
    wave = Item(id="3A-1", description="Output frequency", marks=2,
                mode=AnswerMode.WAVEFORM, expected=1000.0,
                tolerance_mode=ToleranceMode.PERCENT, tolerance=5,
                waveform=WaveformSpec(feature=WaveformFeature.FREQUENCY,
                                      channel="P1", sample_rate_hz=20_000,
                                      num_points=4_000))
    theory = Item(id="1A-2", description="Explain", marks=3, item_type=ItemType.THEORY)
    return Assessment(title="Practical 1", questions=[
        Question(id="1", title="Fixed", parts=[Part(id="1A", title="m", items=[single, theory])]),
        Question(id="2", title="Varying", default_mode=AnswerMode.VARYING,
                 scoring=ScoringMode.ALL_OR_NOTHING,
                 variables=[Variable(name="R1", label="Resistor R1", unit="ohm"),
                            Variable(name="R2", label="Resistor R2", unit="ohm"),
                            Variable(name="Vs", label="Supply", unit="V",
                                     source=VariableSource.MEASURED,
                                     quantity=Quantity.VOLTAGE,
                                     probe_pos="P0", probe_neg="P3")],
                 parts=[Part(id="2A", title="m", items=[varying])]),
        Question(id="3", title="Wave", parts=[Part(id="3A", title="m", items=[wave])]),
    ])


def test_valid_assessment_has_no_problems():
    assert sample_assessment().validate() == []


def test_total_marks():
    assert sample_assessment().total_marks() == 10


def test_save_and_load_round_trip(tmp_path):
    original = sample_assessment()
    path = str(tmp_path / "a.json")
    original.save(path)
    assert Assessment.load(path) == original      # nothing lost, incl. enums/nesting


def test_item_mode_overrides_question_default():
    a = sample_assessment()
    q2 = a.questions[1]                            # default VARYING
    assert q2.mode_for(q2.parts[0].items[0]) == AnswerMode.VARYING
    q1 = a.questions[0]                            # default SINGLE
    assert q1.mode_for(q1.parts[0].items[0]) == AnswerMode.SINGLE
    override = Item(id="x", description="", marks=1, mode=AnswerMode.WAVEFORM)
    assert q1.mode_for(override) == AnswerMode.WAVEFORM


def test_waveform_duration():
    spec = WaveformSpec(feature=WaveformFeature.PEAK, channel="P1",
                        sample_rate_hz=20_000, num_points=4_000)
    assert spec.duration_s == 0.2


def test_version_1_file_still_loads(tmp_path):
    # Exactly the shape the OLD (version 1) save() wrote: no mode, no id, etc.
    v1 = {"title": "Old", "schema_version": 1, "questions": [
        {"id": "1", "title": "Q", "parts": [{"id": "1A", "title": "P", "items": [
            {"id": "1A-1", "description": "V", "marks": 2,
             "item_type": "measurement", "quantity": "voltage",
             "probe_pos": "P1", "probe_neg": "P3", "expected": 3.3,
             "tolerance_mode": "percent", "tolerance": 5}]}]}]}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(v1), encoding="utf-8")
    loaded = Assessment.load(str(path))
    assert loaded.schema_version == SCHEMA_VERSION          # upgraded to 2
    assert loaded.questions[0].default_mode == AnswerMode.SINGLE
    assert loaded.questions[0].scoring == ScoringMode.SUM
    assert loaded.assessment_id                              # got a new id
    assert loaded.validate() == []


def test_content_hash_detects_changes():
    a = sample_assessment()
    b = Assessment._from_dict(json.loads(json.dumps(
        __import__("dataclasses").asdict(a))))             # identical copy
    assert a.content_hash() == b.content_hash()
    b.questions[0].parts[0].items[0].tolerance = 6          # change one tolerance
    assert a.content_hash() != b.content_hash()


def _problems(a: Assessment) -> str:
    return " | ".join(a.validate())


def test_validate_catches_mistakes():
    a = sample_assessment()
    single = a.questions[0].parts[0].items[0]
    single.expected = None
    assert "needs an expected value" in _problems(a)

    a = sample_assessment()
    a.questions[1].parts[0].items[0].expected_formula = " "
    assert "needs a formula" in _problems(a)

    a = sample_assessment()
    a.questions[0].parts[0].items[0].id = "dup"
    a.questions[0].parts[0].items[1].id = "dup"
    assert "more than once" in _problems(a)

    a = sample_assessment()
    a.questions[2].parts[0].items[0].waveform.num_points = MAX_WAVEFORM_POINTS + 1
    assert "more than the box can store" in _problems(a)

    a = sample_assessment()
    a.questions[2].parts[0].items[0].waveform.feature = WaveformFeature.GAIN
    assert "needs a reference channel" in _problems(a)

    a = sample_assessment()
    a.questions[2].parts[0].items[0].waveform = None
    assert "needs a waveform setup" in _problems(a)

    a = sample_assessment()
    a.questions[1].variables[2].probe_neg = None
    assert "measured variable" in _problems(a)


# ---------- formula checking inside validate() (Stage 6) ----------

def test_validate_accepts_the_sample_formulas():
    # The sample assessment's formulas only use variables its question defines.
    assert sample_assessment().validate() == []


def test_validate_reports_a_formula_with_an_unknown_variable():
    a = sample_assessment()
    a.questions[1].parts[0].items[0].expected_formula = "Vs * R9 / (R1 + R2)"
    text = _problems(a)
    assert "formula problem" in text and "R9" in text


def test_validate_reports_a_formula_with_a_syntax_error():
    a = sample_assessment()
    a.questions[1].parts[0].items[0].expected_formula = "Vs * (R2 / R1"
    assert "formula problem" in _problems(a)


def test_validate_reports_an_unsafe_formula():
    a = sample_assessment()
    a.questions[1].parts[0].items[0].expected_formula = "__import__('os').system('x')"
    assert "formula problem" in _problems(a)


def test_validate_reports_a_waveform_formula_with_unknown_variable():
    a = sample_assessment()
    item = a.questions[2].parts[0].items[0]
    item.expected = None
    item.expected_formula = "1 / (2 * pi * Rx * Cx)"
    assert "Rx" in _problems(a)


def test_validate_rejects_reserved_and_invalid_variable_names():
    a = sample_assessment()
    a.questions[1].variables[0].name = "pi"          # clashes with the constant pi
    assert "reserved" in _problems(a)

    a = sample_assessment()
    a.questions[1].variables[0].name = "2R"          # starts with a digit
    assert "not valid" in _problems(a)

    a = sample_assessment()
    a.questions[1].variables[0].name = "R 1"         # contains a space
    assert "not valid" in _problems(a)