"""
ui/main_window.py

The application window. It holds the connection panel and, on tabs, the assessment
creator, the quick measurement, waveform and calibration panels; later stages add the
student view and the tutor grading screen (probably as tabs).
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QTabWidget, QWidget

from ui.assessment_editor import AssessmentEditor
from ui.calibration_panel import CalibrationPanel
from ui.connection_panel import ConnectionPanel
from ui.quick_measure import QuickMeasurePanel
from ui.waveform_panel import WaveformPanel

APP_NAME = "Circuitscope"


class MainWindow(QMainWindow):
    def __init__(self, calibration_folder=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1300, 760)

        self.connection = ConnectionPanel()
        self.creator = AssessmentEditor()
        self.quick = QuickMeasurePanel()
        self.calibration = CalibrationPanel(folder=calibration_folder)
        self.waveform = WaveformPanel()

        # The tools sit on tabs to the right of the connection panel.
        self.tabs = QTabWidget()
        self.tabs.addTab(self.creator, "Assessment creator")
        self.tabs.addTab(self.quick, "Quick measure")
        self.tabs.addTab(self.waveform, "Waveform")
        self.tabs.addTab(self.calibration, "Calibration")

        central = QWidget()
        row = QHBoxLayout(central)
        row.addWidget(self.connection, 1)
        row.addWidget(self.tabs, 4)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Not connected")

        # The creator needs the whole width and does not use the box, so the
        # connection panel is hidden while the creator tab is showing.
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(self.tabs.currentIndex())

        # When the connection changes, tell the other panels and update the title.
        self.connection.device_changed.connect(self._device_changed)

    def _tab_changed(self, index: int) -> None:
        self.connection.setVisible(self.tabs.widget(index) is not self.creator)

    def _device_changed(self, device) -> None:
        self.calibration.set_device(device)      # first: loads the box's saved calibration
        self.quick.set_device(device)
        self.waveform.set_device(device)
        if device is None:
            self.setWindowTitle(APP_NAME)
            self.statusBar().showMessage("Not connected")
        else:
            self.setWindowTitle(f"{APP_NAME} - box {device.uid}")
            self.statusBar().showMessage("Connected")

    def closeEvent(self, event) -> None:
        """Offer to keep unsaved assessment changes, then close the box's port."""
        if not self.creator.confirm_discard():
            event.ignore()
            return
        self.connection.disconnect_device()
        super().closeEvent(event)
