"""Tests for the changes that make the app match the real STM32 firmware (protocol version 1):
the channel list, the 4000-point limit, the SAMPLE timeout, recovery after a timeout, CR+LF replies,
HWFAIL, and the 1.412 microsecond lag between two channels read by the same converter.
Run with:  python -m pytest"""

import numpy as np
import pytest

from core import channels
from core.channels import ADC1_ONLY, ADC2_ONLY, ADC_EITHER, SCAN_LAG_S, scan_lag_s
from core.models import (MAX_SAMPLE_RATE_HZ, MAX_WAVEFORM_POINTS, AnswerMode, Assessment, Item, Part, Question,
                         WaveformFeature, WaveformSpec)
from core.waveform import WaveformError, align_delayed_channel, compute_feature
from hardware import protocol
from hardware.demo import make_demo_device
from hardware.device import SerialDevice
from hardware.protocol import DeviceError, DeviceTimeout
from hardware.simulator import SimulatedBoard, SimulatedPort


def make_device(**kwargs):
    board = SimulatedBoard(**kwargs)
    port = SimulatedPort(board)
    device = SerialDevice(port)
    device.recovery_wait_s = 0                   # do not really wait for the watchdog in tests
    device.connect()
    return device, board, port


def sine(freq, amp=1.0, offset=1.65):
    return lambda t: offset + amp * np.sin(2 * np.pi * freq * t)


# ---------- channels and converters ----------

def test_the_channel_lists_match_the_firmware():
    assert channels.VOLTAGE_CHANNELS == ["P1", "P2", "P3", "P4", "P5", "P6", "P7"]
    assert channels.CURRENT_CHANNELS == ["I1", "I2"]


def test_every_input_is_on_exactly_one_converter_group():
    everything = set(channels.VOLTAGE_CHANNELS) | set(channels.CURRENT_CHANNELS)
    assert ADC_EITHER | ADC1_ONLY | ADC2_ONLY == everything
    assert not (ADC_EITHER & ADC1_ONLY or ADC_EITHER & ADC2_ONLY or ADC1_ONLY & ADC2_ONLY)


@pytest.mark.parametrize("a,b", [("P1", "P3"), ("P2", "I1"), ("P1", "P2"), ("P6", "P3"), ("I1", "I2"), ("I1", "P5"),
                                 ("P3", "P1"), ("I2", "P2")])
def test_pairs_on_different_converters_are_read_at_the_same_instant(a, b):
    assert scan_lag_s(a, b) == 0.0


@pytest.mark.parametrize("a,b", [("P3", "P4"), ("P5", "I2"), ("I2", "P7"), ("P6", "I1"), ("I1", "P6"), ("P4", "P3")])
def test_pairs_on_the_same_converter_are_read_one_after_the_other(a, b):
    assert scan_lag_s(a, b) == SCAN_LAG_S == pytest.approx(1.412e-6)


def test_the_same_channel_twice_has_no_lag():
    assert scan_lag_s("P3", "P3") == 0.0


# ---------- limits ----------

def test_limits_are_the_firmwares():
    assert MAX_WAVEFORM_POINTS == 4000 and MAX_SAMPLE_RATE_HZ == 100_000


def _assessment_with_waveform(points, rate):
    wf = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1", sample_rate_hz=rate, num_points=points)
    item = Item(id="1A-1", description="Frequency", marks=2, mode=AnswerMode.WAVEFORM, waveform=wf,
                expected=1000.0, tolerance=50.0)
    return Assessment(title="Prac", questions=[Question(id="1", title="Q", parts=[Part(id="1A", title="P", items=[item])])])


def test_the_creator_flags_a_waveform_item_above_the_boxs_limits():
    assert _assessment_with_waveform(4000, 100_000).validate() == []
    problems = _assessment_with_waveform(4001, 1000).validate()
    assert any("4001 points" in p and "4000" in p for p in problems)
    problems = _assessment_with_waveform(500, 100_001).validate()
    assert any("100,000 Hz" in p for p in problems)


def test_the_simulated_box_reports_the_firmwares_limits():
    device, _board, _port = make_device()
    assert device.max_points == 4000 and device.max_rate_hz == 100_000


def test_asking_for_more_than_4000_points_is_refused_before_it_is_sent():
    device, _board, port = make_device()
    with pytest.raises(DeviceError, match="4000"):
        device.sample_counts(["P1"], 1000, 4001)
    assert "SAMPLE" not in " ".join(port.commands)


# ---------- the SAMPLE timeout ----------

def test_timeout_formula_matches_the_firmware_reference():
    # 4000 points at 1 kHz: 4 s capture + (5*4000+40)/11520 s transfer + 1 s
    assert protocol.sample_timeout_s(4000, 1000, 1) == pytest.approx(4 + 20040 / 11520 + 1)
    # two channels need twice the data
    assert protocol.sample_timeout_s(400, 20_000, 2) == pytest.approx(0.02 + 4040 / 11520 + 1)


def test_timeout_always_covers_the_capture_and_grows_with_the_request():
    assert protocol.sample_timeout_s(4000, 1, 1) > 4000          # a 1 Hz capture takes over an hour
    assert protocol.sample_timeout_s(10, 100_000, 1) >= 1.0
    assert protocol.sample_timeout_s(4000, 1000, 2) > protocol.sample_timeout_s(4000, 1000, 1)


def test_the_driver_waits_as_long_as_the_capture_needs_and_then_puts_the_timeout_back():
    device, _board, port = make_device()
    device.sample_counts(["P1"], 1000, 4000)
    assert port.timeout_log[-1] == pytest.approx(protocol.sample_timeout_s(4000, 1000, 1))
    assert port.timeout == 3.0                                   # restored for the quick commands


def test_the_timeout_is_put_back_even_when_the_capture_fails():
    device, board, port = make_device()
    board.fail_next_sample = True
    with pytest.raises(DeviceError):
        device.sample_counts(["P1"], 1000, 100)
    assert port.timeout == 3.0


# ---------- recovery after a timeout ----------

def test_a_box_that_froze_and_reset_is_retried_once_and_the_capture_works():
    device, board, port = make_device()
    board.set_dc("P1", 1.0)
    port.ignore_next = 1                                         # the box ignores the first SAMPLE
    (counts,), rate = device.sample_counts(["P1"], 1000, 50)
    assert len(counts) == 50 and rate == pytest.approx(1000, rel=1e-3)
    assert [c.split()[0] for c in port.commands[2:]] == ["SAMPLE", "PING", "SAMPLE"]


def test_a_box_that_never_answers_is_reported_as_not_recovered():
    device, _board, port = make_device()
    port.silent = True
    with pytest.raises(DeviceTimeout, match="did not recover"):
        device.sample_counts(["P1"], 1000, 50)
    assert [c.split()[0] for c in port.commands[2:]] == ["SAMPLE", "PING"]     # one retry check, no endless loop


def test_the_driver_waits_for_the_watchdog_before_checking_the_box(monkeypatch):
    device, _board, port = make_device()
    device.recovery_wait_s = 2.5
    waited = []
    monkeypatch.setattr("hardware.device.time.sleep", waited.append)
    port.ignore_next = 1
    device.sample_counts(["P1"], 1000, 50)
    assert waited == [2.5]


def test_an_error_from_the_box_is_not_retried():
    device, board, port = make_device()
    board.fail_next_sample = True
    with pytest.raises(DeviceError, match="HWFAIL"):
        device.sample_counts(["P1"], 1000, 100)
    assert [c.split()[0] for c in port.commands[2:]] == ["SAMPLE"]              # no PING, no second SAMPLE
    device.sample_counts(["P1"], 1000, 100)                                      # and the box carries on working


# ---------- what the box says ----------

def test_every_reply_line_ends_with_cr_lf_and_the_driver_copes():
    _device, _board, port = make_device()
    port.write(b"PING\n")
    assert port.readline() == b"PONG\r\n"
    port.write(b"SAMPLE P1 1000 3\n")
    lines = [port.readline() for _ in range(2)]
    assert all(line.endswith(b"\r\n") for line in lines)
    assert protocol.decode_line(lines[1]).startswith("DONE ")


def test_a_blank_line_gets_no_reply():
    _device, _board, port = make_device()
    port.write(b"\n   \n")
    assert port.readline() == b""


def test_hwfail_is_reported_with_its_message_and_the_box_recovers():
    device, board, _port = make_device()
    board.fail_next_sample = True
    with pytest.raises(DeviceError) as error:
        device.sample_counts(["P1"], 1000, 100)
    assert "HWFAIL" in str(error.value) and "TIM2" in str(error.value)
    device.sample_counts(["P1"], 1000, 100)


@pytest.mark.parametrize("line,code", [
    ("ping", "BADCMD"), ("FLY", "BADCMD"), ("X" * 65, "BADCMD"),
    ("SAMPLE P1,P1 1000 10", "BADARGS"), ("SAMPLE P1,P2,P3 1000 10", "BADARGS"), ("SAMPLE P1, 1000 10", "BADARGS"),
    ("SAMPLE P1 1000", "BADARGS"), ("SAMPLE P1 1.5 10", "BADARGS"),
    ("SAMPLE P8 1000 10", "BADCH"), ("SAMPLE p1 1000 10", "BADCH"),
    ("SAMPLE P1 0 10", "BADRATE"), ("SAMPLE P1 100001 10", "BADRATE"),
    ("SAMPLE P1 1000 0", "BADCOUNT"), ("SAMPLE P1 1000 4001", "BADCOUNT")])
def test_the_simulated_box_gives_the_firmwares_error_codes(line, code):
    assert SimulatedBoard().handle(line)[0].startswith(f"ERR {code}")


def test_the_unknown_channel_message_lists_the_valid_channels():
    reply = SimulatedBoard().handle("SAMPLE P8 1000 10")[0]
    assert all(name in reply for name in ["P1", "P7", "I1", "I2"])


def test_the_boundary_values_the_firmware_accepts_are_accepted():
    board = SimulatedBoard()
    assert board.handle("SAMPLE P1 100000 4000")[-1].startswith("DONE")
    assert board.handle("SAMPLE P1 1 1")[-1].startswith("DONE")


# ---------- the new inputs ----------

def test_the_demo_box_has_signals_on_p6_and_p7():
    device = make_demo_device()
    device.connect()
    assert device.measure_voltage("P6", "P3") == pytest.approx(2.5, abs=0.05)
    assert device.measure_voltage("P7", "P3") == pytest.approx(0.5, abs=0.05)


def test_the_voltage_pickers_offer_p6_and_p7():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.quick_measure import QuickMeasurePanel
    from ui.waveform_panel import WaveformPanel
    for panel, boxes in ((QuickMeasurePanel(), ("pos_box", "neg_box")), (WaveformPanel(), ("channel_box", "ref_box"))):
        for name in boxes:
            box = getattr(panel, name)
            assert box.findText("P6") >= 0 and box.findText("P7") >= 0


def test_the_waveform_panel_allows_1_to_100000_hz():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.waveform_panel import WaveformPanel
    panel = WaveformPanel()
    assert panel.rate_spin.minimum() == 1 and panel.rate_spin.maximum() == 100_000
    assert panel.points_spin.maximum() == 4000


# ---------- the lag between two channels on the same converter ----------

def test_align_leaves_a_channel_alone_when_there_is_no_lag():
    x = np.array([1.0, 2.0, 4.0])
    assert list(align_delayed_channel(x, 1000, 0.0)) == [1.0, 2.0, 4.0]


def test_align_steps_a_ramp_back_by_the_lag():
    # A ramp that rises 1 V per sample, sampled a quarter of a sample late: every later value was
    # read 0.25 V too high, so aligning must take 0.25 V off. The first sample has nothing before it.
    ramp = np.arange(5, dtype=float)
    out = align_delayed_channel(ramp, 1000, 0.25 / 1000)
    assert out[0] == 0.0 and list(out[1:]) == pytest.approx([0.75, 1.75, 2.75, 3.75])


def test_align_refuses_a_lag_longer_than_one_sample_gap():
    with pytest.raises(WaveformError):
        align_delayed_channel(np.arange(5.0), 1000, 0.002)


def _phase(device, pair, rate=100_000, points=4000):
    spec = WaveformSpec(feature=WaveformFeature.PHASE_DIFFERENCE, channel=pair[0], channel_ref=pair[1],
                        sample_rate_hz=rate, num_points=points)
    capture = device.capture_waveform(spec)
    return compute_feature(WaveformFeature.PHASE_DIFFERENCE, capture.samples, capture.sample_rate_hz,
                           capture.ref_samples)


def test_two_identical_signals_on_one_converter_show_no_phase_after_correction():
    device, board, _port = make_device()
    for ch in ("P3", "P4"):                                       # both only on ADC2: read one after the other
        board.set_wave(ch, sine(10_000))
    raw, _rate = device.sample(["P3", "P4"], 100_000, 4000)       # the uncorrected data
    uncorrected = compute_feature(WaveformFeature.PHASE_DIFFERENCE, raw[0], 100_000, raw[1])
    corrected = _phase(device, ("P3", "P4"))
    assert abs(uncorrected) > 3.0                                 # 360 x 10 kHz x 1.412 us = 5.1 degrees
    assert abs(corrected) < 1.0


def test_signals_on_different_converters_are_not_changed():
    device, board, _port = make_device()
    for ch in ("P1", "P3"):
        board.set_wave(ch, sine(10_000))
    spec = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1", channel_ref="P3",
                        sample_rate_hz=100_000, num_points=2000)
    capture = device.capture_waveform(spec)
    raw, _rate = device.sample(["P1", "P3"], 100_000, 2000)
    assert capture.ref_samples == pytest.approx(list(raw[1]))
    assert abs(_phase(device, ("P1", "P3"))) < 0.5


def test_a_real_phase_shift_is_still_measured_correctly_on_a_scanned_pair():
    device, board, _port = make_device()
    board.set_wave("P3", sine(1000))
    board.set_wave("P4", lambda t: 1.65 + np.sin(2 * np.pi * 1000 * t - np.radians(45)))
    assert _phase(device, ("P4", "P3"), rate=50_000) == pytest.approx(45, abs=1.5)


def test_a_slow_capture_tells_the_user_how_long_it_will_take(qtbot=None):
    import time
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from core.submission import WaveformCapture
    from ui.waveform_panel import WaveformPanel

    class SlowDevice:
        max_points, max_rate_hz = 4000, 100_000
        def capture_waveform(self, spec):
            time.sleep(0.2)
            return WaveformCapture(samples=[0.0, 1.0] * 500, sample_rate_hz=1000.0)

    panel = WaveformPanel()
    panel.set_device(SlowDevice())
    panel.points_spin.setValue(4000)
    panel.rate_spin.setValue(1000)                                # 4000 points at 1 kHz = 4 seconds
    panel.capture_button.click()
    assert panel.message_label.text() == "Capturing... about 4 seconds"
    deadline = time.time() + 3
    while panel.capture is None and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert panel.capture is not None
