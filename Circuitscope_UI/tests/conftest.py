"""
Shared test setup (pytest runs this file automatically).

QT_QPA_PLATFORM=offscreen makes Qt draw into memory instead of opening real
windows, so the interface tests run silently, even on a machine with no screen.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
