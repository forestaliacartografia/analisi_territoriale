"""Generic QgsTask wrapper around an engine call."""

from __future__ import annotations

import traceback
from typing import Any, Callable, Optional

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QObject, pyqtSignal

from ..core import log
from ..core.errors import UserCancelled
from ..core.feedback import Feedback

#: Signature of the work function: it receives a Feedback and returns anything.
WorkFunction = Callable[[Feedback], Any]


class TaskBridge(QObject):
    """Signals a task emits towards the GUI (always delivered in the main thread)."""

    message = pyqtSignal(str, str)      # text, level
    step = pyqtSignal(str)              # current step label
    progress = pyqtSignal(float)        # 0-100


class _TaskFeedback(Feedback):
    """Feedback implementation that reports through the task and the bridge."""

    def __init__(self, task: "EngineTask") -> None:
        self._task = task

    def set_progress(self, percent: float) -> None:
        value = max(0.0, min(100.0, float(percent)))
        self._task.setProgress(value)
        self._task.bridge.progress.emit(value)

    def set_step(self, label: str) -> None:
        self._task.bridge.step.emit(label)
        log.debug(label)

    def push_info(self, message: str) -> None:
        log.info(message)
        self._task.bridge.message.emit(message, "INFO")

    def push_warning(self, message: str) -> None:
        log.warning(message)
        self._task.bridge.message.emit(message, "WARNING")

    def push_error(self, message: str) -> None:
        log.error(message)
        self._task.bridge.message.emit(message, "ERROR")

    def push_debug(self, message: str) -> None:
        log.debug(message)

    def is_canceled(self) -> bool:
        return bool(self._task.isCanceled())


class EngineTask(QgsTask):
    """Runs ``work(feedback)`` in a worker thread and delivers the outcome."""

    def __init__(self, description: str, work: WorkFunction, *,
                 on_success: Optional[Callable[[Any], None]] = None,
                 on_error: Optional[Callable[[BaseException], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None) -> None:
        super().__init__(description, QgsTask.Flag.CanCancel)
        self._work = work
        self._on_success = on_success
        self._on_error = on_error
        self._on_cancel = on_cancel
        self.bridge = TaskBridge()
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.cancelled = False

    def run(self) -> bool:  # pragma: no cover - runs in a worker thread
        """Execute the work function. Never raises: failures travel in ``self.error``."""
        try:
            self.result = self._work(_TaskFeedback(self))
            return True
        except UserCancelled:
            self.cancelled = True
            return False
        except Exception as exc:
            self.error = exc
            log.error(f"Task '{self.description()}' failed: {exc}\n{traceback.format_exc()}")
            return False

    def finished(self, result: bool) -> None:
        """Deliver the outcome in the main thread."""
        if self.cancelled or self.isCanceled():
            if self._on_cancel is not None:
                self._on_cancel()
            return
        if not result or self.error is not None:
            if self._on_error is not None:
                self._on_error(self.error or RuntimeError("Operazione non riuscita"))
            return
        if self._on_success is not None:
            self._on_success(self.result)


def run_in_background(description: str, work: WorkFunction, *,
                      on_success: Optional[Callable[[Any], None]] = None,
                      on_error: Optional[Callable[[BaseException], None]] = None,
                      on_cancel: Optional[Callable[[], None]] = None,
                      on_message: Optional[Callable[[str, str], None]] = None,
                      on_step: Optional[Callable[[str], None]] = None,
                      on_progress: Optional[Callable[[float], None]] = None) -> EngineTask:
    """Create, wire and start a background task. Returns it so the caller can cancel it."""
    task = EngineTask(description, work, on_success=on_success, on_error=on_error,
                      on_cancel=on_cancel)
    if on_message is not None:
        task.bridge.message.connect(on_message)
    if on_step is not None:
        task.bridge.step.connect(on_step)
    if on_progress is not None:
        task.bridge.progress.connect(on_progress)
    QgsApplication.taskManager().addTask(task)
    return task
