"""
main.py - START THE APPLICATION HERE.

Press the play button on this file (or run:  python main.py  from the project root).
"""

import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)     # the Qt "engine": exactly one per program
    window = MainWindow()
    window.show()
    return app.exec()                # runs until the window is closed


if __name__ == "__main__":
    sys.exit(main())