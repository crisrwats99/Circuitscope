"""
ui/main_window.py

The application window. For now it holds the connection panel and, on tabs, the quick
measurement and calibration panels; later stages add the assessment creator, the student view
and the tutor grading screen (probably as tabs).
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QTabWidget, QWidget

from ui.calibration_panel import CalibrationPanel
from ui.connection_panel import ConnectionPanel
from ui.quick_measure import QuickMeasurePanel

APP_NAME = "Circuitscope"


class MainWindow(QMainWindow):
    def __init__(self, calibration_folder=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 420)

        self.connection = ConnectionPanel()
        self.quick = QuickMeasurePanel()
        self.calibration = CalibrationPanel(folder=calibration_folder)

        # The tools sit on tabs to the right of the connection panel.
        self.tabs = QTabWidget()
        self.tabs.addTab(self.quick, "Quick measure")
        self.tabs.addTab(self.calibration, "Calibration")

        central = QWidget()
        row = QHBoxLayout(central)
        row.addWidget(self.connection, 1)
        row.addWidget(self.tabs, 2)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Not connected")

        # When the connection changes, tell the other panels and update the title.
        self.connection.device_changed.connect(self._device_changed)

    def _device_changed(self, device) -> None:
        self.calibration.set_device(device)      # first: loads the box's saved calibration
        self.quick.set_device(device)
        if device is None:
            self.setWindowTitle(APP_NAME)
            self.statusBar().showMessage("Not connected")
        else:
            self.setWindowTitle(f"{APP_NAME} - box {device.uid}")
            self.statusBar().showMessage("Connected")

    def closeEvent(self, event) -> None:
        """Close the box's port when the window closes."""
        self.connection.disconnect_device()
        super().closeEvent(event)