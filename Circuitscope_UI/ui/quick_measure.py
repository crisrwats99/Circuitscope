"""
ui/quick_measure.py

A small "try it" panel: pick two test points and read the voltage between them.
It proves the whole chain (window -> background thread -> driver -> box) works,
and doubles as a handy tool when debugging a circuit.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from core.channels import CURRENT_CHANNELS, VOLTAGE_CHANNELS
from ui.background import run_in_background

CHANNELS = VOLTAGE_CHANNELS            # the box's voltage test points (see core/channels.py)


class QuickMeasurePanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("2. Quick voltage measurement", parent)
        self.device = None

        self.pos_box = QComboBox()
        self.pos_box.addItems(CHANNELS)
        self.pos_box.setCurrentText("P1")
        self.neg_box = QComboBox()
        self.neg_box.addItems(CHANNELS)
        self.neg_box.setCurrentText("P3")
        self.measure_button = QPushButton("Measure voltage")
        self.result_label = QLabel("-")
        self.result_label.setStyleSheet("font-size: 28px; font-weight: bold;")
        self.message_label = QLabel("Connect to a box first.")
        self.message_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Probe + (red):", self.pos_box)
        form.addRow("Probe - (black):", self.neg_box)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        for w in (self.measure_button, self.result_label, self.message_label):
            layout.addWidget(w)
        layout.addStretch(1)

        self.measure_button.clicked.connect(self._measure)
        self.set_device(None)

    def set_device(self, device) -> None:
        """Called when the connection changes (device, or None)."""
        self.device = device
        self.measure_button.setEnabled(device is not None)
        self.result_label.setText("-")
        self.message_label.setText("Ready." if device else "Connect to a box first.")
        self.message_label.setStyleSheet("")

    def _measure(self) -> None:
        pos, neg = self.pos_box.currentText(), self.neg_box.currentText()
        if pos == neg:
            self._show_error("Choose two different test points.")
            return
        self.measure_button.setEnabled(False)            # one measurement at a time
        self.message_label.setText("Measuring...")
        self.message_label.setStyleSheet("")
        run_in_background(self.device.measure_voltage, pos, neg,
                          on_result=self._show_value, on_error=self._show_error,
                          on_finished=self._finished)

    def _show_value(self, volts: float) -> None:
        self.result_label.setText(f"{volts:.3f} V")
        self.message_label.setText(f"{self.pos_box.currentText()} relative to {self.neg_box.currentText()}")
        self.message_label.setStyleSheet("")

    def _show_error(self, message: str) -> None:
        self.result_label.setText("-")
        self.message_label.setText(f"Measurement failed: {message}")
        self.message_label.setStyleSheet("color: #b00020;")

    def _finished(self) -> None:
        self.measure_button.setEnabled(self.device is not None)


class QuickCurrentPanel(QGroupBox):
    """Read the current through one of the box's current inputs (in milliamps)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("2b. Quick current measurement", parent)
        self.device = None
        self.channel_box = QComboBox()
        self.channel_box.addItems(CURRENT_CHANNELS)
        self.measure_button = QPushButton("Measure current")
        self.result_label = QLabel("-")
        self.result_label.setStyleSheet("font-size: 28px; font-weight: bold;")
        self.message_label = QLabel("Connect to a box first.")
        self.message_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Current input:", self.channel_box)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        for w in (self.measure_button, self.result_label, self.message_label):
            layout.addWidget(w)
        self.measure_button.clicked.connect(self._measure)
        self.set_device(None)

    def set_device(self, device) -> None:
        self.device = device
        self.measure_button.setEnabled(device is not None)
        self.result_label.setText("-")
        self.message_label.setText("Put your circuit in series with the current input, then press Measure."
                                   if device else "Connect to a box first.")
        self.message_label.setStyleSheet("")

    def _measure(self) -> None:
        self.measure_button.setEnabled(False)
        self.message_label.setText("Measuring...")
        self.message_label.setStyleSheet("")
        run_in_background(self.device.measure_current, self.channel_box.currentText(),
                          on_result=self._show_value, on_error=self._show_error,
                          on_finished=lambda: self.measure_button.setEnabled(self.device is not None))

    def _show_value(self, milliamps: float) -> None:
        self.result_label.setText(f"{milliamps:.3f} mA")
        self.message_label.setText("Positive and negative show the direction of the current.")

    def _show_error(self, message: str) -> None:
        self.result_label.setText("-")
        self.message_label.setText(f"Measurement failed: {message}")
        self.message_label.setStyleSheet("color: #b00020;")
