"""
ui/connection_panel.py

The "Connect to the box" panel: choose the simulated demo box or a real box on a
USB port, press Connect, and see its UID (the watermark) and limits.

The panel only handles widgets. Talking to the box happens in the background
(ui/background.py) so the window stays responsive while connecting.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from hardware.demo import make_demo_device
from hardware.device import SerialDevice, list_serial_ports, open_serial_port
from ui.background import run_in_background

SIMULATED = "Simulated box (demo, no hardware needed)"
REAL = "Real box (USB cable)"


def open_and_connect(simulated: bool, port_name: str) -> SerialDevice:
    """Create the driver and do the handshake. Runs on a worker thread (it can take seconds)."""
    if simulated:
        device = make_demo_device()
    else:
        if not port_name:
            raise RuntimeError("Choose a serial port first (and press Refresh if it is not listed).")
        device = SerialDevice(open_serial_port(port_name))
    try:
        device.connect()
    except Exception:
        device.close()             # do not leave a half-open port behind
        raise
    return device


class ConnectionPanel(QGroupBox):
    # Sent when the connection changes: the connected device, or None when disconnected.
    device_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("1. Connect to the box", parent)
        self.device: SerialDevice | None = None

        self.mode_box = QComboBox()
        self.mode_box.addItems([SIMULATED, REAL])
        self.port_box = QComboBox()
        self.refresh_button = QPushButton("Refresh ports")
        self.connect_button = QPushButton("Connect")
        self.status_label = QLabel("Not connected")
        self.uid_label = QLabel("UID: -")
        self.limits_label = QLabel("")
        self.status_label.setWordWrap(True)

        port_row = QHBoxLayout()
        port_row.addWidget(self.port_box, 1)
        port_row.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        for w in (QLabel("Box:"), self.mode_box):
            layout.addWidget(w)
        layout.addWidget(QLabel("Serial port (real box only):"))
        layout.addLayout(port_row)
        for w in (self.connect_button, self.status_label, self.uid_label, self.limits_label):
            layout.addWidget(w)
        layout.addStretch(1)

        self.mode_box.currentIndexChanged.connect(self._update_enabled)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self._connect_or_disconnect)
        self.refresh_ports()
        self._update_enabled()

    # --- helpers ---------------------------------------------------------------

    def is_simulated(self) -> bool:
        return self.mode_box.currentText() == SIMULATED

    def refresh_ports(self) -> None:
        """Fill the port list with the serial ports found on this computer."""
        self.port_box.clear()
        self.port_box.addItems(list_serial_ports())

    def _update_enabled(self) -> None:
        """Only allow changing settings while disconnected; the port list only matters for a real box."""
        idle = self.device is None
        self.mode_box.setEnabled(idle)
        self.port_box.setEnabled(idle and not self.is_simulated())
        self.refresh_button.setEnabled(idle and not self.is_simulated())

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #b00020;" if error else "")

    # --- connecting / disconnecting -------------------------------------------

    def _connect_or_disconnect(self) -> None:
        if self.device is not None:
            self.disconnect_device()
            return
        # Lock the button while the worker thread connects (prevents double clicks).
        self.connect_button.setEnabled(False)
        self._set_status("Connecting...")
        run_in_background(open_and_connect, self.is_simulated(), self.port_box.currentText(),
                          on_result=self._connected, on_error=self._connect_failed,
                          on_finished=lambda: self.connect_button.setEnabled(True))

    def _connected(self, device: SerialDevice) -> None:
        self.device = device
        self.connect_button.setText("Disconnect")
        self._set_status("Connected")
        self.uid_label.setText(f"UID: {device.uid}")
        self.limits_label.setText(f"Up to {device.max_points} samples at {device.max_rate_hz:,} Hz")
        self._update_enabled()
        self.device_changed.emit(device)

    def _connect_failed(self, message: str) -> None:
        self._set_status(f"Could not connect: {message}", error=True)

    def disconnect_device(self) -> None:
        if self.device is not None:
            self.device.close()
        self.device = None
        self.connect_button.setText("Connect")
        self._set_status("Not connected")
        self.uid_label.setText("UID: -")
        self.limits_label.setText("")
        self._update_enabled()
        self.device_changed.emit(None)