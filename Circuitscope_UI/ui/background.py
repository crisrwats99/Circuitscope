"""
ui/background.py

Runs slow work (talking to the box) on a BACKGROUND thread so the window never
freezes. Usage:

    run_in_background(device.measure_voltage, "P1", "P3",
                      on_result=show_value, on_error=show_problem)

* The function runs on a worker thread.
* on_result / on_error / on_finished are then called back on the MAIN (window)
  thread, which is the only thread allowed to touch buttons and labels.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _TaskSignals(QObject):
    """Carries the outcome from the worker thread back to the main thread."""
    result = Signal(object)        # the function's return value
    error = Signal(str)            # the exception text if it failed
    finished = Signal()            # always sent last

    def __init__(self, on_result, on_error, on_finished):
        super().__init__()         # created on the MAIN thread, so its slots run there
        self._on_result, self._on_error, self._on_finished = on_result, on_error, on_finished
        # Connect to methods of THIS object (not loose functions): Qt then knows
        # which thread to deliver on and queues the call onto the main thread.
        self.result.connect(self._handle_result)
        self.error.connect(self._handle_error)
        self.finished.connect(self._handle_finished)

    @Slot(object)
    def _handle_result(self, value):
        if self._on_result:
            self._on_result(value)

    @Slot(str)
    def _handle_error(self, text):
        if self._on_error:
            self._on_error(text)

    @Slot()
    def _handle_finished(self):
        if self._on_finished:
            self._on_finished()
        _active.discard(self)      # the task is over: let Python free it


class _Task(QRunnable):
    def __init__(self, fn, args, kwargs, signals: _TaskSignals):
        super().__init__()
        self._fn, self._args, self._kwargs, self.signals = fn, args, kwargs, signals

    def run(self):                 # runs on the worker thread
        try:
            value = self._fn(*self._args, **self._kwargs)
        except Exception as exc:   # never let an error kill the worker silently
            self.signals.error.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.result.emit(value)
        finally:
            self.signals.finished.emit()


# Keeps each task's signal object alive until it has delivered its results.
_active: set[_TaskSignals] = set()


def run_in_background(fn: Callable, *args, on_result: Callable | None = None,
                      on_error: Callable | None = None,
                      on_finished: Callable | None = None, **kwargs) -> None:
    """Run fn(*args, **kwargs) on a worker thread and report back on the main thread."""
    signals = _TaskSignals(on_result, on_error, on_finished)
    _active.add(signals)
    QThreadPool.globalInstance().start(_Task(fn, args, kwargs, signals))