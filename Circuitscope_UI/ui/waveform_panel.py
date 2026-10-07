"""
ui/waveform_panel.py

The waveform viewer: capture a signal from the box, see it plotted, and read all
eleven features (frequency, RMS, phase ...). Useful for debugging a circuit, for
choosing sensible capture settings for an assessment item, and for report figures.

  Channel          the signal to capture
  Reference        optional second channel, captured at the same moment
                   (needed for gain and phase difference)
  Sample rate / points   how fast and how many (the capture lasts points / rate seconds)

The capture runs on a background thread (ui/background.py) so the window stays responsive.
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from core.export import write_waveform_csv
from core.models import MAX_WAVEFORM_POINTS, WaveformFeature, WaveformSpec
from core.waveform import compute_all, format_feature
from ui.background import run_in_background

CHANNELS = ["P0", "P1", "P2", "P3", "P4", "P5"]
NO_REFERENCE = "None"

# Trace colours, used on the plot AND on the matching labels, so you can tell at a glance
# which trace is which.
SIGNAL_COLOR = "#ffd54f"      # yellow: the signal channel
REF_COLOR = "#4dd0e1"         # cyan: the reference channel

# Plain-English feature names for the table.
FEATURE_NAMES = {
    WaveformFeature.FREQUENCY: "Frequency", WaveformFeature.PERIOD: "Period",
    WaveformFeature.PEAK: "Peak", WaveformFeature.PEAK_TO_PEAK: "Peak-to-peak",
    WaveformFeature.RMS: "RMS", WaveformFeature.DC_OFFSET: "DC offset",
    WaveformFeature.DUTY_CYCLE: "Duty cycle", WaveformFeature.GAIN: "Gain",
    WaveformFeature.PHASE_DIFFERENCE: "Phase difference",
    WaveformFeature.RISE_TIME: "Rise time", WaveformFeature.FALL_TIME: "Fall time",
}


class WaveformPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("4. Waveform viewer", parent)
        self.device = None
        self.capture = None                  # the last WaveformCapture (None until one is taken)
        self._captured_channels = ("", None)  # (signal channel, reference channel) of the capture in progress

        self.channel_box = QComboBox()
        self.channel_box.addItems(CHANNELS)
        self.channel_box.setCurrentText("P4")
        self.ref_box = QComboBox()
        self.ref_box.addItems([NO_REFERENCE] + CHANNELS)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(100, 200_000)
        self.rate_spin.setValue(50_000)
        self.rate_spin.setSuffix(" Hz")
        self.rate_spin.setGroupSeparatorShown(True)
        self.points_spin = QSpinBox()
        self.points_spin.setRange(10, MAX_WAVEFORM_POINTS)
        self.points_spin.setValue(500)
        self.capture_button = QPushButton("Capture")
        self.save_button = QPushButton("Save capture (CSV)...")
        self.message_label = QLabel("Connect to a box first.")
        self.message_label.setWordWrap(True)

        # The plot: time in milliseconds along the bottom, volts up the side.
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel("bottom", "Time", units="ms")
        self.plot_widget.setLabel("left", "Voltage", units="V")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        # The legend (top right of the plot) names each trace, e.g. "P5 (signal)" in yellow.
        self.legend = self.plot_widget.addLegend(offset=(-10, 10))
        self.curve_signal = self.plot_widget.plot(pen=pg.mkPen(SIGNAL_COLOR, width=2))
        self.curve_ref = self.plot_widget.plot(pen=pg.mkPen(REF_COLOR, width=2))
        self.legend.clear()                  # nothing to name until a capture exists

        # The feature table: one row per feature.
        self.table = QTableWidget(len(FEATURE_NAMES), 3)
        self.table.setHorizontalHeaderLabels(["Feature", "Value", "Note"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumWidth(440)
        self._clear_table()

        # The labels are tinted with their trace's colour (yellow = signal, cyan = reference).
        channel_label = QLabel("Channel:")
        channel_label.setStyleSheet(f"color: {SIGNAL_COLOR}; font-weight: bold;")
        ref_label = QLabel("Reference:")
        ref_label.setStyleSheet(f"color: {REF_COLOR}; font-weight: bold;")

        form = QFormLayout()
        form.addRow(channel_label, self.channel_box)
        form.addRow(ref_label, self.ref_box)
        form.addRow("Sample rate:", self.rate_spin)
        form.addRow("Points:", self.points_spin)
        controls = QVBoxLayout()
        controls.addLayout(form)
        controls.addWidget(self.capture_button)
        controls.addWidget(self.save_button)
        controls.addWidget(self.message_label)
        controls.addWidget(self.table, 1)

        layout = QHBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.plot_widget, 1)

        self.capture_button.clicked.connect(self._capture)
        self.save_button.clicked.connect(self._ask_and_save)
        self.set_device(None)

    # --- state ---------------------------------------------------------------

    def _clear_table(self) -> None:
        for row, feature in enumerate(FEATURE_NAMES):
            for col, text in enumerate((FEATURE_NAMES[feature], "-", "")):
                item = QTableWidgetItem(text)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def _clear_capture(self) -> None:
        self.capture = None
        self.curve_signal.setData([], [])
        self.curve_ref.setData([], [])
        self.legend.clear()
        self._clear_table()

    def _refresh_buttons(self, busy: bool = False) -> None:
        connected = self.device is not None
        self.capture_button.setEnabled(connected and not busy)
        self.save_button.setEnabled(self.capture is not None and not busy)

    def set_device(self, device) -> None:
        """Called when the connection changes (device, or None)."""
        self.device = device
        self._clear_capture()
        if device is None:
            self.message_label.setText("Connect to a box first.")
        else:
            # The box's own limits decide what can be requested.
            self.points_spin.setMaximum(min(device.max_points, MAX_WAVEFORM_POINTS))
            self.rate_spin.setMaximum(int(device.max_rate_hz))
            self.message_label.setText("Ready.")
        self.message_label.setStyleSheet("")
        self._refresh_buttons()

    # --- capturing -----------------------------------------------------------------

    def _capture(self) -> None:
        channel = self.channel_box.currentText()
        reference = self.ref_box.currentText()
        reference = None if reference == NO_REFERENCE else reference
        if reference == channel:
            self._show_error("Choose a reference channel different from the signal channel.")
            return
        # A WaveformSpec describes a capture; the 'feature' is irrelevant here (we measure all).
        spec = WaveformSpec(feature=WaveformFeature.FREQUENCY, channel=channel, channel_ref=reference,
                            sample_rate_hz=self.rate_spin.value(), num_points=self.points_spin.value())
        self._captured_channels = (channel, reference)   # remembered so the legend names the right pins
        self._refresh_buttons(busy=True)
        self.message_label.setText("Capturing...")
        self.message_label.setStyleSheet("")
        run_in_background(self.device.capture_waveform, spec,
                          on_result=self._show_capture, on_error=self._show_error,
                          on_finished=lambda: self._refresh_buttons())

    def _show_capture(self, capture) -> None:
        """Runs on the window thread once the capture has arrived."""
        self.capture = capture
        channel, reference = self._captured_channels
        rate = capture.sample_rate_hz
        n = len(capture.samples)
        t_ms = np.arange(n) / rate * 1000.0                      # sample number -> milliseconds
        self.curve_signal.setData(t_ms, capture.samples)
        if capture.ref_samples is not None:
            self.curve_ref.setData(t_ms, capture.ref_samples)
        else:
            self.curve_ref.setData([], [])
        self.plot_widget.autoRange()
        self._update_legend(channel, reference)

        for row, result in enumerate(compute_all(capture.samples, rate, capture.ref_samples)):
            self.table.item(row, 1).setText(format_feature(result.feature, result.value))
            note = self.table.item(row, 2)
            note.setText("" if result.value is not None else result.error)
            note.setToolTip(result.error)
        self.table.resizeColumnsToContents()
        self.message_label.setText(f"Captured {n} samples at {rate:,.0f} Hz ({n / rate * 1000:.1f} ms).")
        self.message_label.setStyleSheet("")

    def _update_legend(self, channel: str, reference: str | None) -> None:
        """Name each plotted trace in the legend, e.g. 'P5 (signal)' and 'P4 (reference)'."""
        self.legend.clear()
        self.legend.addItem(self.curve_signal, f"{channel} (signal)")
        if reference is not None:
            self.legend.addItem(self.curve_ref, f"{reference} (reference)")

    def legend_labels(self) -> list[str]:
        """The texts currently shown in the legend (used by the tests)."""
        return [label.text for _sample, label in self.legend.items]

    def _show_error(self, message: str) -> None:
        self.message_label.setText(f"Capture failed: {message}")
        self.message_label.setStyleSheet("color: #b00020;")

    # --- saving ------------------------------------------------------------------

    def _ask_and_save(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "Save capture", "capture.csv", "CSV files (*.csv)")
        if path:
            self.save_csv(path)

    def save_csv(self, path: str) -> None:
        """Write the last capture to `path`."""
        if self.capture is None:
            return
        try:
            write_waveform_csv(path, self.capture.samples, self.capture.sample_rate_hz,
                               self.capture.ref_samples)
        except OSError as exc:
            self._show_error(f"could not save the file ({exc})")
            return
        self.message_label.setText(f"Saved {len(self.capture.samples)} samples to {path}")
        self.message_label.setStyleSheet("")
