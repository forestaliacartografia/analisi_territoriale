"""Background execution: every engine call the GUI makes goes through a QgsTask.

The rule the whole plugin follows: **worker threads produce results, the main thread
applies them**. A task never touches the layer tree, the project or any widget; it returns
a serialisable result that the caller applies in ``on_success`` (which QGIS calls on the
main thread).
"""

from .runner import EngineTask, TaskBridge, run_in_background

__all__ = ["EngineTask", "TaskBridge", "run_in_background"]
