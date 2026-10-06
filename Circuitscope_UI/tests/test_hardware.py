"""Automatic checks for the hardware package (protocol, calibration, driver, simulator).
Run with:  python -m pytest

No real hardware is needed: the driver talks to a SimulatedBoard through a SimulatedPort.
"""

import numpy as np
import pytest

from core.models import WaveformSpec, WaveformFeature, Item, AnswerMode, ToleranceMode
from core.waveform import compute_from_spec, compute_feature
from core.grading import grade_item, Status
from hardware import protocol
from hardware.calibration import Calibration
from hardware.device import SerialDevice
from hardware.protocol import DeviceError, DeviceTimeout, ProtocolError
from hardware.simulator import SimulatedBoard, SimulatedPort, TIMER_CLOCK_HZ


# ---------- helpers ----------

def make_device(board=None, **kwargs):
    """A connected device wired to a simulated board. Returns (device, board, port)."""
    board = board or SimulatedBoard(**kwargs)
    port = SimulatedPort(board)
    device = SerialDevice(port)
    device.connect()
    return device, board, port


def sine(freq, amp=1.0, offset=1.65, phase_deg=0.0):
    """A function of time (array) that gives a sine wave in volts."""
    return lambda t: offset + amp * np.sin(2 * np.pi * freq * t + np.radians(phase_deg))


# ---------- calibration ----------

def test_ideal_calibration_maps_full_scale_to_vref():
    cal = Calibration.ideal(3.3, 12)
    assert cal.counts_to_volts(0) == pytest.approx(0.0)
    assert cal.counts_to_volts(4095) == pytest.approx(3.3)
    assert list(cal.counts_to_volts([0, 4095])) == pytest.approx([0.0, 3.3])


def test_two_point_calibration_corrects_gain_and_offset():
    # This box reads 12 counts at a true 0.00 V and 3722 counts at a true 3.00 V.
    cal = Calibration.fit_two_point(12, 0.0, 3722, 3.0)
    assert cal.counts_to_volts(12) == pytest.approx(0.0, abs=1e-9)
    assert cal.counts_to_volts(3722) == pytest.approx(3.0, abs=1e-9)
    assert cal.counts_to_volts(1867) == pytest.approx(1.5, abs=0.01)


def test_two_point_calibration_rejects_nonsense():
    with pytest.raises(ValueError):
        Calibration.fit_two_point(100, 1.0, 100, 2.0)          # same count twice
    with pytest.raises(ValueError):
        Calibration.fit_two_point(100, 2.0, 200, 1.0)          # higher voltage gave lower count


def test_calibration_round_trips_through_a_dictionary():
    cal = Calibration.fit_two_point(12, 0.0, 3722, 3.0)
    again = Calibration.from_dict(cal.to_dict())
    assert again.volts_per_count == cal.volts_per_count and again.offset_v == cal.offset_v


# ---------- protocol text ----------

def test_sample_command_text():
    assert protocol.build_sample_command(["P1"], 10_000, 2000) == "SAMPLE P1 10000 2000\n"
    assert protocol.build_sample_command(["P1", "P2"], 9999.6, 50) == "SAMPLE P1,P2 10000 50\n"


@pytest.mark.parametrize("channels,rate,n", [
    ([], 1000, 10), (["P1", "P2", "P3"], 1000, 10),             # wrong number of channels
    (["1P"], 1000, 10), (["P 1"], 1000, 10), (["P1;"], 1000, 10),   # bad names
    (["P1"], 0, 10), (["P1"], -5, 10), (["P1"], 1000, 0), (["P1"], 1000, 2.5),
])
def test_bad_sample_requests_are_refused(channels, rate, n):
    with pytest.raises(DeviceError):
        protocol.build_sample_command(channels, rate, n)


def test_info_and_id_replies_are_parsed():
    info = protocol.parse_info("INFO 1 8000 200000 12 3300")
    assert info == {"protocol": 1, "max_points": 8000, "max_rate_hz": 200000,
                    "adc_bits": 12, "vref_v": 3.3}
    assert protocol.parse_id("ID 0012AB34 CD56EF78 0011AABB") == (0x0012AB34, 0xCD56EF78, 0x0011AABB)


@pytest.mark.parametrize("line", ["", "INFO 1 2 3", "INFO a b c d e", "HELLO"])
def test_bad_info_replies_are_refused(line):
    with pytest.raises(ProtocolError):
        protocol.parse_info(line)


@pytest.mark.parametrize("line", ["ID 1 2 3", "ID 0012AB34 CD56EF78", "ID ZZZZZZZZ 00000000 00000000"])
def test_bad_id_replies_are_refused(line):
    with pytest.raises(ProtocolError):
        protocol.parse_id(line)


def test_data_replies_are_checked():
    assert protocol.parse_data("DATA 3 10,20,30", 3, 4095) == [10, 20, 30]
    for line, n in (("DATA 3 10,20", 3),            # too few samples
                    ("DATA 3 10,20,x", 3),          # not a number
                    ("DATA 3 10,20,5000", 3),       # beyond 12 bits
                    ("DATA 3 -1,2,3", 3),           # negative count
                    ("DATA 2 1,2", 3),              # box counted differently than we asked
                    ("PONG", 3)):                   # not a DATA line at all
        with pytest.raises(ProtocolError):
            protocol.parse_data(line, n, 4095)


def test_done_and_error_lines():
    assert protocol.parse_done("DONE 9999.5") == 9999.5
    for bad in ("DONE", "DONE x", "DONE -5", "OK 1"):
        with pytest.raises(ProtocolError):
            protocol.parse_done(bad)
    with pytest.raises(DeviceError, match="BADCH"):
        protocol.raise_if_error("ERR BADCH Unknown channel P9")
    protocol.raise_if_error("PONG")                          # a normal line does not raise


# ---------- connecting ----------

def test_connect_reads_the_uid_and_limits():
    device, _board, port = make_device()
    assert device.connected
    assert device.uid == "0012AB34CD56EF780011AABB"          # three words joined: the watermark
    assert device.max_points == 8000 and device.max_rate_hz == 200_000
    assert port.commands[:2] == ["INFO?", "ID?"]


def test_firmware_with_a_different_protocol_version_is_refused():
    port = SimulatedPort(SimulatedBoard(protocol_version=2))
    with pytest.raises(DeviceError, match="protocol"):
        SerialDevice(port).connect()


def test_silent_box_gives_a_timeout():
    port = SimulatedPort(SimulatedBoard())
    port.silent = True
    device = SerialDevice(port)
    with pytest.raises(DeviceTimeout):
        device.connect()
    assert not device.connected


def test_commands_before_connecting_are_refused():
    device = SerialDevice(SimulatedPort(SimulatedBoard()))
    with pytest.raises(DeviceError, match="connect"):
        device.sample(["P1"], 1000, 10)


def test_context_manager_connects_and_closes():
    port = SimulatedPort(SimulatedBoard())
    with SerialDevice(port) as device:
        assert device.connected
    assert port.closed and not device.connected


# ---------- measuring voltages ----------

def test_dc_voltage_measurement():
    device, board, _ = make_device()
    board.set_dc("P1", 3.0)
    board.set_dc("P3", 0.0)
    assert device.measure_voltage("P1", "P3") == pytest.approx(3.0, abs=0.002)   # within ~2 counts


def test_voltage_between_two_points_is_a_difference():
    device, board, _ = make_device()
    board.set_dc("P1", 2.0)
    board.set_dc("P2", 0.5)
    assert device.measure_voltage("P1", "P2") == pytest.approx(1.5, abs=0.002)
    assert device.measure_voltage("P2", "P1") == pytest.approx(-1.5, abs=0.002)


def test_averaging_reduces_noise():
    device, board, _ = make_device(noise_v=0.02)
    board.set_dc("P1", 1.0)
    board.set_dc("P3", 0.0)
    # One sample would be off by ~20 mV; the average of 200 is much closer.
    assert device.measure_voltage("P1", "P3") == pytest.approx(1.0, abs=0.01)


def test_converter_clips_outside_zero_to_vref():
    device, board, _ = make_device()
    board.set_dc("P1", 5.0)          # more than the 3.3 V the converter can see
    board.set_dc("P2", -1.0)         # below 0 V
    (high, low), _ = device.sample(["P1", "P2"], 1000, 10)
    assert high.max() == pytest.approx(3.3, abs=0.001)
    assert low.max() == pytest.approx(0.0, abs=0.001)


def test_measuring_a_point_against_itself_is_refused():
    device, _board, _ = make_device()
    with pytest.raises(DeviceError, match="same"):
        device.measure_voltage("P1", "P1")


def test_calibration_is_applied_to_readings():
    board = SimulatedBoard()
    board.set_dc("P1", 1.0)
    cal = Calibration(volts_per_count=2 * 3.3 / 4095)         # a front end that doubles the scale
    device = SerialDevice(SimulatedPort(board), calibration=cal)
    device.connect()
    (volts,), _ = device.sample(["P1"], 1000, 5)
    assert volts.mean() == pytest.approx(2.0, abs=0.005)


# ---------- errors from the box ----------

def test_box_errors_become_device_errors_and_the_link_stays_usable():
    device, board, _ = make_device()
    with pytest.raises(DeviceError, match="BADCH"):
        device.sample(["P9"], 1000, 10)                      # P9 does not exist on the box
    board.set_dc("P1", 1.0)
    (volts,), _ = device.sample(["P1"], 1000, 10)            # the next command still works
    assert volts.mean() == pytest.approx(1.0, abs=0.002)


def test_requests_beyond_the_boxs_limits_are_refused_before_sending():
    device, _board, port = make_device()
    sent = len(port.commands)
    with pytest.raises(DeviceError, match="store"):
        device.sample(["P1"], 1000, 9000)
    with pytest.raises(DeviceError, match="faster"):
        device.sample(["P1"], 500_000, 100)
    assert len(port.commands) == sent                        # nothing reached the box


def test_corrupt_reply_is_reported_and_leftovers_are_discarded():
    class FaultyBoard(SimulatedBoard):
        faulty = False
        def handle(self, line):
            if self.faulty and line.startswith("SAMPLE"):
                return ["DATA 5 1,2,3", "DATA 5 1,2,3,4,5", "DONE 1000"]   # wrong count
            return super().handle(line)

    board = FaultyBoard()
    device, _b, _p = make_device(board)
    board.faulty = True
    with pytest.raises(ProtocolError):
        device.sample(["P1", "P2"], 1000, 5)
    board.faulty = False
    board.set_dc("P1", 1.0)
    (volts,), _ = device.sample(["P1"], 1000, 5)             # no stale lines left to confuse it
    assert volts.mean() == pytest.approx(1.0, abs=0.002)


def test_simulator_answers_basic_commands():
    board = SimulatedBoard()
    assert board.handle("PING") == ["PONG"]
    assert board.handle("FLY")[0].startswith("ERR BADCMD")
    assert board.handle("")[0].startswith("ERR")
    assert board.handle("SAMPLE P1 1000")[0].startswith("ERR BADARGS")
    assert board.handle("SAMPLE P1 x y")[0].startswith("ERR BADARGS")
    assert board.handle("SAMPLE P1 0 10")[0].startswith("ERR BADRATE")
    assert board.handle("SAMPLE P1 1000 99999")[0].startswith("ERR BADCOUNT")


# ---------- capturing waveforms ----------

def test_capture_waveform_returns_samples_and_the_real_rate():
    device, board, _ = make_device()
    board.set_wave("P1", sine(1000))
    spec = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1",
                        sample_rate_hz=30_000, num_points=3000)
    capture = device.capture_waveform(spec)
    assert len(capture.samples) == 3000 and capture.ref_samples is None
    # The box's timer cannot make exactly 30 000 Hz: it divides 170 MHz by a whole number.
    assert capture.sample_rate_hz == pytest.approx(TIMER_CLOCK_HZ / round(TIMER_CLOCK_HZ / 30_000))
    assert capture.sample_rate_hz != 30_000


def test_captured_sine_gives_the_right_frequency_and_amplitude():
    device, board, _ = make_device(noise_v=0.005)
    board.set_wave("P1", sine(1000, amp=1.0, offset=1.65))
    spec = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1",
                        sample_rate_hz=20_000, num_points=4000)
    capture = device.capture_waveform(spec)
    assert compute_feature(WaveformFeature.FREQUENCY, capture.samples, capture.sample_rate_hz) \
        == pytest.approx(1000, rel=0.005)
    assert compute_feature(WaveformFeature.PEAK_TO_PEAK, capture.samples, capture.sample_rate_hz) \
        == pytest.approx(2.0, abs=0.05)
    assert compute_feature(WaveformFeature.DC_OFFSET, capture.samples, capture.sample_rate_hz) \
        == pytest.approx(1.65, abs=0.01)


def test_two_channel_capture_gives_gain_and_phase():
    device, board, _ = make_device()
    board.set_wave("P1", sine(1000, amp=1.0))                       # reference (input)
    board.set_wave("P2", sine(1000, amp=0.5, phase_deg=-45))        # output: half size, 45 degrees late
    spec = WaveformSpec(feature=WaveformFeature.GAIN, channel="P2", channel_ref="P1",
                        sample_rate_hz=50_000, num_points=5000)
    capture = device.capture_waveform(spec)
    assert capture.ref_samples is not None and len(capture.ref_samples) == 5000
    gain = compute_feature(WaveformFeature.GAIN, capture.samples, capture.sample_rate_hz, capture.ref_samples)
    phase = compute_feature(WaveformFeature.PHASE_DIFFERENCE, capture.samples,
                            capture.sample_rate_hz, capture.ref_samples)
    assert gain == pytest.approx(0.5, abs=0.02)
    assert phase == pytest.approx(45, abs=3)


def test_measure_then_grade_end_to_end():
    # An RC filter stage: expected cutoff f = 1/(2 pi R C) = 1000 Hz for these parts.
    device, board, _ = make_device()
    board.set_wave("P1", sine(1000))
    spec = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel="P1",
                        sample_rate_hz=20_000, num_points=4000)
    item = Item(id="w1", description="f", marks=2, mode=AnswerMode.WAVEFORM,
                expected_formula="1 / (2 * pi * R * C)", waveform=spec,
                tolerance_mode=ToleranceMode.PERCENT, tolerance=5)
    values = {"R": 1000, "C": 1.59155e-7}
    measured = compute_from_spec(spec, device.capture_waveform(spec).samples)
    assert grade_item(item, measured, values).status == Status.CORRECT
    board.set_wave("P1", sine(1400))                                 # the student's circuit is wrong
    wrong = compute_from_spec(spec, device.capture_waveform(spec).samples)
    assert grade_item(item, wrong, values).status == Status.INCORRECT