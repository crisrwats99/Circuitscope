"""
ui/calibration_panel.py

Calibration: teach the app how THIS box's converter really behaves.

  1. Connect a known LOW voltage (e.g. 0 V = ground) to a test point, choose that
     point, press "Read low".
  2. Connect a known HIGH voltage (e.g. 3.000 V from a reference or a precise supply
     measured with a good multimeter), choose its point, press "Read high".
  3. Press "Apply and save". The app fits a straight line through the two readings
     (see hardware/calibration.py) and uses it for every measurement from then on.

The result is saved under the box's UID, so next time the same box connects it is
loaded automatically.
"""

from __future__ import annotations
from core.channels import VOLTAGE_CHANNELS

from datetime import datetime

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from hardware.calibration import Calibration, check_plausible
from hardware.calibration_store import (CalibrationError, delete_calibration, load_calibration,
                                        save_calibration)
from ui.background import run_in_background

CHANNELS = VOLTAGE_CHANNELS
MIN_SPAN_V = 1.0           # the two references must be at least this far apart


class CalibrationPanel(QGroupBox):
    def __init__(self, folder=None, parent: QWidget | None = None):
        super().__init__("3. Calibration", parent)
        self.folder = folder                  # where calibrations are saved (None = the default folder)
        self.device = None
        self._busy = False
        self._low = None                      # (volts, counts) once "Read low" has been done
        self._high = None

        self.low_volts = self._spin(0.000)
        self.low_channel = self._combo("P3")
        self.low_button = QPushButton("Read low")
        self.low_result = QLabel("not read yet")
        self.high_volts = self._spin(3.000)
        self.high_channel = self._combo("P1")
        self.high_button = QPushButton("Read high")
        self.high_result = QLabel("not read yet")
        self.apply_button = QPushButton("Apply and save")
        self.reset_button = QPushButton("Reset to ideal")
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        grid = QGridLayout()
        for col, text in enumerate(("Reference (V)", "Test point", "", "Reading")):
            grid.addWidget(QLabel(text), 0, col)
        for row, widgets in enumerate(((self.low_volts, self.low_channel, self.low_button, self.low_result),
                                       (self.high_volts, self.high_channel, self.high_button, self.high_result)), 1):
            for col, w in enumerate(widgets):
                grid.addWidget(w, row, col)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.reset_button)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.low_button.clicked.connect(lambda: self._read("low"))
        self.high_button.clicked.connect(lambda: self._read("high"))
        self.apply_button.clicked.connect(self._apply)
        self.reset_button.clicked.connect(self._reset)
        self.set_device(None)

    @staticmethod
    def _spin(value: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setDecimals(3)
        box.setRange(-0.5, 5.0)
        box.setSingleStep(0.1)
        box.setValue(value)
        return box

    @staticmethod
    def _combo(current: str) -> QComboBox:
        box = QComboBox()
        box.addItems(CHANNELS)
        box.setCurrentText(current)
        return box

    # --- state ---------------------------------------------------------------

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #b00020;" if error else "")

    def _refresh_buttons(self) -> None:
        """Enable only what makes sense right now."""
        connected = self.device is not None and not self._busy
        self.low_button.setEnabled(connected)
        self.high_button.setEnabled(connected)
        self.apply_button.setEnabled(connected and self._low is not None and self._high is not None)
        self.reset_button.setEnabled(connected)

    def set_device(self, device) -> None:
        """Called when the connection changes. Loads this box's saved calibration, if any."""
        self.device = device
        self._low = self._high = None
        self.low_result.setText("not read yet")
        self.high_result.setText("not read yet")
        if device is None:
            self._set_status("Connect to a box to calibrate it.")
        else:
            try:
                saved = load_calibration(device.uid, self.folder)
            except CalibrationError as exc:
                saved = None
                self._set_status(f"{exc} Using ideal values.", error=True)
            else:
                if saved is None:
                    self._set_status("Not calibrated: using ideal values.")
                elif saved.front_end != device.front_end:
                    # Made for a different circuit in front of the converter (e.g. before the
                    # +/-15 V front end existed): its numbers would give wrong voltages.
                    self._set_status("The saved calibration was made for a different front end, so it "
                                     "was ignored. Using ideal values: please calibrate again.", error=True)
                else:
                    device.calibration = saved.calibration
                    self._set_status(f"Loaded the calibration saved on {saved.saved_utc} "
                                     f"({self._describe(saved.calibration)}).")
        self._refresh_buttons()

    @staticmethod
    def _describe(cal: Calibration) -> str:
        return f"1 count = {cal.volts_per_count * 1000:.4f} mV, offset {cal.offset_v * 1000:.1f} mV"

    # --- reading the two reference points ------------------------------------------

    def _read(self, which: str) -> None:
        volts = (self.low_volts if which == "low" else self.high_volts).value()
        channel = (self.low_channel if which == "low" else self.high_channel).currentText()
        self._busy = True
        self._refresh_buttons()
        self._set_status(f"Reading {channel}...")
        run_in_background(self.device.read_mean_counts, channel,
                          on_result=lambda r: self._got_reading(which, volts, r),
                          on_error=lambda m: self._set_status(f"Reading failed: {m}", error=True),
                          on_finished=self._finished)

    def _got_reading(self, which: str, volts: float, result: tuple[float, float]) -> None:
        counts, noise = result
        label = self.low_result if which == "low" else self.high_result
        label.setText(f"{counts:.1f} counts (±{noise:.1f}) at {volts:.3f} V")
        if which == "low":
            self._low = (volts, counts)
        else:
            self._high = (volts, counts)
        self._set_status("Reading stored.")

    def _finished(self) -> None:
        self._busy = False
        self._refresh_buttons()

    # --- applying / resetting ----------------------------------------------------

    def _apply(self) -> None:
        (v_low, c_low), (v_high, c_high) = self._low, self._high
        if v_high - v_low < MIN_SPAN_V:
            self._set_status(f"The two references must be at least {MIN_SPAN_V:g} V apart "
                             f"(high above low).", error=True)
            return
        try:
            cal = Calibration.fit_two_point(c_low, v_low, c_high, v_high)
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        problem = check_plausible(cal, self.device.ideal_calibration,
                                  max_offset_v=0.3 / self.device.front_end.gain)
        if problem:
            self._set_status(problem, error=True)
            return
        try:
            save_calibration(self.device.uid, cal, self.folder, points=[
                {"volts": v_low, "counts": c_low}, {"volts": v_high, "counts": c_high}],
                             front_end=self.device.front_end)
        except CalibrationError as exc:
            self._set_status(str(exc), error=True)
            return
        self.device.calibration = cal                       # used for every measurement from now on
        self._set_status(f"Calibrated at {datetime.now():%H:%M:%S}: {self._describe(cal)}. Saved.")

    def _reset(self) -> None:
        self.device.reset_calibration()
        try:
            delete_calibration(self.device.uid, self.folder)
        except CalibrationError as exc:
            self._set_status(str(exc), error=True)
            return
        self._low = self._high = None
        self.low_result.setText("not read yet")
        self.high_result.setText("not read yet")
        self._set_status("Reset: using ideal values (saved calibration removed).")
        self._refresh_buttons()
