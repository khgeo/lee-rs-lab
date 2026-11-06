"""Generic background task that runs fn(task, *args) and calls back on the main thread."""
import traceback

from qgis.core import QgsApplication, QgsMessageLog, QgsTask

from .compat import can_cancel

_live = []


class FuncTask(QgsTask):
    def __init__(self, title, fn, args=(), kwargs=None, on_done=None):
        super().__init__(title, can_cancel())
        self.fn, self.args, self.kwargs = fn, args, kwargs or {}
        self.on_done = on_done
        self.result, self.error = None, None

    def run(self):
        try:
            self.result = self.fn(self, *self.args, **self.kwargs)
            return self.result is not None or not self.isCanceled()
        except Exception as e:  # noqa: BLE001
            self.error = "%s" % e
            QgsMessageLog.logMessage(traceback.format_exc(), "RS Lab")
            return False

    def finished(self, ok):
        try:
            if self.on_done:
                self.on_done(ok and self.error is None, self.result, self.error)
        finally:
            if self in _live:
                _live.remove(self)


def start(task):
    _live.append(task)
    QgsApplication.taskManager().addTask(task)
    return task


def run(title, fn, args=(), kwargs=None, on_done=None):
    return start(FuncTask(title, fn, args, kwargs, on_done))
