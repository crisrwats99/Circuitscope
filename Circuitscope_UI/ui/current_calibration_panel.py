"""
ui/current_calibration_panel.py

Calibration of a CURRENT input (I1 ...). The current circuit (1 ohm shunt, gain 47, 1.75 V bias)
is not exact: the bias follows the USB supply and the resistors have tolerances, so the bench
readings were 5-9 % off the design values. Two readings put that right for THIS box:

  1. LOW:  no current (leave the input open, 0 mA). This finds the zero point (the bias).
  2. HIGH: a known current, e.g. 5 V across a known resistor, checked with a multimeter in series.
           This finds the true sensitivity.

Then "Apply and save": a straight line is fitted (milliamps = counts x slope + offset) and saved under
the box's UID, separately for each current input. Same idea as the voltage calibration tab.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from core.channels import CURRENT_CHANNELS
from hardware.calibration import CURRENT_SENSE, Calibration, check_plausible
from hardware.calibration_store import (CalibrationError, delete_calibration, load_calibration,
                                        save_calibration)
from ui.background import run_in_background

MIN_SPAN_MA = 2.0           # the two references must be at least this far apart


class CurrentCalibrationPanel(QGroupBox):
    def __init__(self, folder=None, parent: QWidget | None = None):
        super().__init__("3b. Current calibration", parent)
        self.folder = folder
        self.device = None
        self._busy = False
        self._low = None                 # (mA, counts)
        self._high = None

        self.channel_box = QComboBox()
        self.channel_box.addItems(CURRENT_CHANNELS)
        self.low_ma = self._spin(0.0)
        self.low_button = QPushButton("Read low")
        self.low_result = QLabel("not read yet")
        self.high_ma = self._spin(5.0)
        self.high_button = QPushButton("Read high")
        self.high_result = QLabel("not read yet")
        self.apply_button = QPushButton("Apply and save")
        self.reset_button = QPushButton("Reset to ideal")
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        grid = QGridLayout()
        for col, text in enumerate(("Known current (mA)", "", "Reading")):
            grid.addWidget(QLabel(text), 0, col)
        for row, widgets in enumerate(((self.low_ma, self.low_button, self.low_result),
                                       (self.high_ma, self.high_button, self.high_result)), 1):
            for col, w in enumerate(widgets):
                grid.addWidget(w, row, col)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Current input:"))
        layout.addWidget(self.channel_box)
        layout.addLayout(grid)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.reset_button)
        layout.addWidget(self.status_label)

        self.low_button.clicked.connect(lambda: self._read("low"))
        self.high_button.clicked.connect(lambda: self._read("high"))
        self.apply_button.clicked.connect(self._apply)
        self.reset_button.clicked.connect(self._reset)
        self.channel_box.currentIndexChanged.connect(lambda _i: self._clear_readings())
        self.set_device(None)

    @staticmethod
    def _spin(value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setDecimals(3)
        box.setRange(-40.0, 40.0)
        box.setSingleStep(0.5)
        box.setValue(value)
        return box

    def _channel(self) -> str:
        return self.channel_box.currentText()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #b00020;" if error else "")

    def _refresh_buttons(self) -> None:
        connected = self.device is not None and not self._busy
        self.low_button.setEnabled(connected)
        self.high_button.setEnabled(connected)
        self.apply_button.setEnabled(connected and self._low is not None and self._high is not None)
        self.reset_button.setEnabled(connected)

    def _clear_readings(self) -> None:
        self._low = self._high = None
        self.low_result.setText("not read yet")
        self.high_result.setText("not read yet")
        self._load_saved()
        self._refresh_buttons()

    @staticmethod
    def _describe(cal: Calibration) -> str:
        return f"1 count = {cal.volts_per_count * 1000:.2f} uA, zero at {-cal.offset_v / cal.volts_per_count:.0f} counts"

    def set_device(self, device) -> None:
        """Called when the connection changes; loads this box's saved current calibration."""
        self.device = device
        self._low = self._high = None
        self.low_result.setText("not read yet")
        self.high_result.setText("not read yet")
        if device is None:
            self._set_status("Connect to a box to calibrate its current inputs.")
        else:
            self._load_saved()
        self._refresh_buttons()

    def _load_saved(self) -> None:
        if self.device is None:
            return
        channel = self._channel()
        try:
            saved = load_calibration(self.device.uid, self.folder, channel)
        except CalibrationError as exc:
            self._set_status(f"{exc} Using ideal values.", error=True)
            return
        if saved is None:
            self._set_status(f"{channel} is not calibrated: using the design values (47 mV per mA).")
        elif saved.front_end != CURRENT_SENSE:
            self._set_status("The saved calibration was made for a different current circuit, so it "
                             "was ignored. Please calibrate again.", error=True)
        else:
            self.device.current_calibrations[channel] = saved.calibration
            self._set_status(f"Loaded the {channel} calibration saved on {saved.saved_utc} "
                             f"({self._describe(saved.calibration)}).")

    def _read(self, which: str) -> None:
        known = (self.low_ma if which == "low" else self.high_ma).value()
        channel = self._channel()
        self._busy = True
        self._refresh_buttons()
        self._set_status(f"Reading {channel}...")
        run_in_background(self.device.read_mean_counts, channel,
                          on_result=lambda r: self._got_reading(which, known, r),
                          on_error=lambda m: self._set_status(f"Reading failed: {m}", error=True),
                          on_finished=self._finished)

    def _got_reading(self, which: str, known: float, result: tuple[float, float]) -> None:
        counts, noise = result
        if which == "low":
            self._low = (known, counts)
            self.low_result.setText(f"{counts:.1f} counts (±{noise:.1f}) at {known:.3f} mA")
        else:
            self._high = (known, counts)
            self.high_result.setText(f"{counts:.1f} counts (±{noise:.1f}) at {known:.3f} mA")
        self._set_status("Reading stored.")

    def _finished(self) -> None:
        self._busy = False
        self._refresh_buttons()

    def _apply(self) -> None:
        (i_low, c_low), (i_high, c_high) = self._low, self._high
        if i_high - i_low < MIN_SPAN_MA:
            self._set_status(f"The two known currents must be at least {MIN_SPAN_MA:g} mA apart "
                             f"(high above low).", error=True)
            return
        try:
            cal = Calibration.fit_two_point(c_low, i_low, c_high, i_high, unit="mA")
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        problem = check_plausible(cal, self.device.ideal_current_calibration,
                                  max_offset_v=0.3 / CURRENT_SENSE.gain, unit="mA")
        if problem:
            self._set_status(problem, error=True)
            return
        channel = self._channel()
        try:
            save_calibration(self.device.uid, cal, self.folder,
                             points=[{"mA": i_low, "counts": c_low}, {"mA": i_high, "counts": c_high}],
                             front_end=CURRENT_SENSE, channel=channel)
        except CalibrationError as exc:
            self._set_status(str(exc), error=True)
            return
        self.device.current_calibrations[channel] = cal
        self._set_status(f"{channel} calibrated at {datetime.now():%H:%M:%S}: {self._describe(cal)}. Saved.")

    def _reset(self) -> None:
        channel = self._channel()
        self.device.reset_current_calibration(channel)
        try:
            delete_calibration(self.device.uid, self.folder, channel)
        except CalibrationError as exc:
            self._set_status(str(exc), error=True)
            return
        self._low = self._high = None
        self.low_result.setText("not read yet")
        self.high_result.setText("not read yet")
        self._set_status(f"{channel} reset: using the design values (saved calibration removed).")
        self._refresh_buttons()
