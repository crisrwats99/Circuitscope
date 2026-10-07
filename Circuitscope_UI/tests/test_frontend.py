"""Automatic checks for the analogue front end (V_in = (V_adc - 1.65) / 0.09).
Run with:  python -m pytest

The box measures +/-15 V by squeezing it into 0-3.3 V at the converter pin:
    V_adc = 1.65 + 0.09 * V_in        (design values, Voltage Measurement Design doc)
The PC works backwards from the raw counts.
"""

import numpy as np
import pytest

from core.models import WaveformFeature as F
from core.waveform import compute_feature
from hardware.calibration import CIRCUITSCOPE, DIRECT, Calibration, check_plausible
from hardware.demo import make_demo_board, make_demo_device
from hardware.device import SerialDevice
from hardware.simulator import SimulatedBoard, SimulatedPort


def device_for(board, front_end=CIRCUITSCOPE):
    device = SerialDevice(SimulatedPort(board), front_end=front_end)
    device.connect()
    return device


# ---------- the formula itself ----------

def test_ideal_calibration_is_the_inverse_of_the_front_end():
    cal = Calibration.ideal(3.3, 12, CIRCUITSCOPE)
    # a pin voltage of 1.65 V is count 2047.5; -15 V, 0 V, +15 V should land on 0.305 / 1.65 / 3.00 V
    for volts_in, pin in [(-15, 0.30), (0, 1.65), (15, 3.00)]:
        counts = pin / 3.3 * 4095
        assert float(cal.counts_to_volts(counts)) == pytest.approx(volts_in, abs=0.02)


def test_the_formula_matches_the_design_document_exactly():
    cal = Calibration.ideal(3.3, 12, CIRCUITSCOPE)
    for counts in (0, 500, 2048, 3000, 4095):
        v_adc = counts * 3.3 / 4095
        assert float(cal.counts_to_volts(counts)) == pytest.approx((v_adc - 1.65) / 0.09)


def test_without_a_front_end_nothing_changes():
    assert Calibration.ideal(3.3, 12) == Calibration.ideal(3.3, 12, DIRECT)
    assert Calibration.ideal().offset_v == 0.0


# ---------- the simulated box applies the front end ----------

def test_demo_box_reads_the_right_input_voltages_with_no_calibration():
    device = make_demo_device()
    device.connect()
    assert device.measure_voltage("P1", "P3") == pytest.approx(3.0, abs=0.05)
    assert device.measure_voltage("P2", "P3") == pytest.approx(1.2, abs=0.05)


def test_negative_voltages_are_measured():
    board = SimulatedBoard(front_end=CIRCUITSCOPE)
    board.set_dc("P1", -12.0)
    board.set_dc("P2", 14.0)
    device = device_for(board)
    assert device.measure_voltage("P1", "P3") == pytest.approx(-12.0, abs=0.05)
    assert device.measure_voltage("P2", "P3") == pytest.approx(14.0, abs=0.05)


def test_beyond_the_range_the_reading_saturates():
    board = SimulatedBoard(front_end=CIRCUITSCOPE)
    board.set_dc("P1", 40.0)                                   # far more than +15 V
    device = device_for(board)
    assert device.measure_voltage("P1", "P3") < 20.5           # clipped at the top of the converter
    assert device.measure_voltage("P1", "P3") > 15.0


def test_resolution_is_about_nine_millivolts_of_input():
    cal = Calibration.ideal(3.3, 12, CIRCUITSCOPE)
    assert cal.volts_per_count == pytest.approx(0.00895, abs=0.0001)


# ---------- calibration on top of the front end ----------

def test_calibration_corrects_a_front_end_whose_parts_are_off():
    # resistors 3 % off and a 30 mV shift at the converter pin
    board = SimulatedBoard(front_end=CIRCUITSCOPE, adc_gain=1.03, adc_offset_v=0.03)
    board.set_dc("P1", 0.0)
    board.set_dc("P2", 10.0)
    board.set_dc("P3", 5.0)
    device = device_for(board)
    before = device.measure_voltage("P3", "P1")
    assert abs(before - 5.0) > 0.1                              # the box really is off
    low, _ = device.read_mean_counts("P1")
    high, _ = device.read_mean_counts("P2")
    device.calibration = Calibration.fit_two_point(low, 0.0, high, 10.0)
    assert device.measure_voltage("P3", "P1") == pytest.approx(5.0, abs=0.03)


def test_plausibility_is_judged_against_the_design_offset_not_zero():
    ideal = Calibration.ideal(3.3, 12, CIRCUITSCOPE)           # offset is about -18.3 V
    good = Calibration(ideal.volts_per_count * 1.02, ideal.offset_v + 0.1)
    assert check_plausible(good, ideal, max_offset_v=0.3 / CIRCUITSCOPE.gain) is None
    wrong = Calibration(ideal.volts_per_count, ideal.offset_v + 6.0)
    assert "offset" in check_plausible(wrong, ideal, max_offset_v=0.3 / CIRCUITSCOPE.gain)


def test_the_real_app_connection_uses_the_design_front_end():
    from ui.connection_panel import open_and_connect
    device = open_and_connect(True, "")
    assert device.front_end == CIRCUITSCOPE
    device.close()


# ---------- waveforms with the extra noise the front end brings ----------

def test_demo_waveform_features_survive_the_front_end_noise():
    device = make_demo_device()
    device.connect()
    (p5, p4), rate = device.sample(["P5", "P4"], 50_000, 500)
    assert compute_feature(F.FREQUENCY, p4, rate) == pytest.approx(1000, rel=0.005)
    assert compute_feature(F.GAIN, p5, rate, ref_samples=p4) == pytest.approx(0.5, abs=0.05)
    assert compute_feature(F.PHASE_DIFFERENCE, p5, rate, ref_samples=p4) == pytest.approx(45, abs=4)


def test_steady_dc_through_the_front_end_is_not_mistaken_for_a_wave():
    device = make_demo_device()
    device.connect()
    (p1,), rate = device.sample(["P1"], 50_000, 2000)
    assert float(np.mean(p1)) == pytest.approx(3.0, abs=0.02)
    with pytest.raises(Exception, match="flat"):
        compute_feature(F.FREQUENCY, p1, rate)


def test_phase_is_not_biased_by_noise_on_a_small_signal():
    # The old two-sample edge finder read about 3 degrees low on a noisy half-size sine.
    results = []
    for seed in range(12):
        board = make_demo_board()
        board._rng = np.random.default_rng(seed)
        device = device_for(board)
        (p5, p4), rate = device.sample(["P5", "P4"], 50_000, 500)
        results.append(compute_feature(F.PHASE_DIFFERENCE, p5, rate, ref_samples=p4))
    assert np.mean(results) == pytest.approx(45, abs=2.0)
