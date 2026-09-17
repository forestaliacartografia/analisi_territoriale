"""Progress / cancellation abstraction.

Engines must run identically from the GUI (``QgsTask``), from Processing
(``QgsProcessingFeedback``) and from plain scripts/tests. They therefore never talk to
either API directly: they receive a :class:`Feedback`.
"""

from __future__ import annotations

from typing import Any, Optional

from . import log


class Feedback:
    """Minimal progress/cancel/log sink."""

    def set_progress(self, percent: float) -> None:
        """Report overall progress in the 0-100 range."""

    def push_info(self, message: str) -> None:
        """Report a user-visible informational message."""
        log.info(message)

    def push_warning(self, message: str) -> None:
        """Report a user-visible warning (non fatal)."""
        log.warning(message)

    def push_error(self, message: str) -> None:
        """Report a user-visible error (non fatal for the whole run)."""
        log.error(message)

    def push_debug(self, message: str) -> None:
        """Report a developer-oriented message."""
        log.debug(message)

    def is_canceled(self) -> bool:
        """Return ``True`` when the user asked to stop."""
        return False

    def set_step(self, label: str) -> None:
        """Report the name of the step currently running."""
        self.push_info(label)


class NullFeedback(Feedback):
    """Feedback that swallows progress and logs nothing (used in tests)."""

    def push_info(self, message: str) -> None:
        pass

    def push_warning(self, message: str) -> None:
        pass

    def push_error(self, message: str) -> None:
        pass

    def push_debug(self, message: str) -> None:
        pass


class ProcessingFeedback(Feedback):
    """Adapter over a ``QgsProcessingFeedback``."""

    def __init__(self, feedback: Any) -> None:
        self._fb = feedback

    def set_progress(self, percent: float) -> None:
        self._fb.setProgress(max(0.0, min(100.0, float(percent))))

    def push_info(self, message: str) -> None:
        self._fb.pushInfo(message)

    def push_warning(self, message: str) -> None:
        pusher = getattr(self._fb, "pushWarning", None)
        pusher(message) if pusher else self._fb.pushInfo(f"WARNING: {message}")

    def push_error(self, message: str) -> None:
        self._fb.reportError(message, False)

    def push_debug(self, message: str) -> None:
        self._fb.pushDebugInfo(message)

    def is_canceled(self) -> bool:
        return bool(self._fb.isCanceled())


class TaskFeedback(Feedback):
    """Adapter over a ``QgsTask`` running in a worker thread.

    Messages are logged (thread-safe) and optionally forwarded to a callback that the
    task marshals to the GUI thread.
    """

    def __init__(self, task: Any, *, on_message: Optional[callable] = None) -> None:
        self._task = task
        self._on_message = on_message

    def set_progress(self, percent: float) -> None:
        self._task.setProgress(max(0.0, min(100.0, float(percent))))

    def _forward(self, message: str, level: str) -> None:
        if self._on_message is not None:
            self._on_message(message, level)

    def push_info(self, message: str) -> None:
        log.info(message)
        self._forward(message, "INFO")

    def push_warning(self, message: str) -> None:
        log.warning(message)
        self._forward(message, "WARNING")

    def push_error(self, message: str) -> None:
        log.error(message)
        self._forward(message, "ERROR")

    def push_debug(self, message: str) -> None:
        log.debug(message)

    def is_canceled(self) -> bool:
        return bool(self._task.isCanceled())


class ChildFeedback(Feedback):
    """Maps a child operation onto a slice of the parent progress range.

    Lets every engine report 0-100 internally while the orchestrator keeps a single
    global progress bar.
    """

    def __init__(self, parent: Feedback, start: float, end: float, *, prefix: str = "") -> None:
        self._parent = parent
        self._start = float(start)
        self._end = float(end)
        self._prefix = prefix

    def _decorate(self, message: str) -> str:
        return f"{self._prefix}{message}" if self._prefix else message

    def set_progress(self, percent: float) -> None:
        span = self._end - self._start
        self._parent.set_progress(self._start + span * max(0.0, min(100.0, percent)) / 100.0)

    def push_info(self, message: str) -> None:
        self._parent.push_info(self._decorate(message))

    def push_warning(self, message: str) -> None:
        self._parent.push_warning(self._decorate(message))

    def push_error(self, message: str) -> None:
        self._parent.push_error(self._decorate(message))

    def push_debug(self, message: str) -> None:
        self._parent.push_debug(self._decorate(message))

    def is_canceled(self) -> bool:
        return self._parent.is_canceled()
