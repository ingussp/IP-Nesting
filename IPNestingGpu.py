"""Explicit GPU discovery with a painted progress window and cancellable worker."""
import os
import subprocess
import time
from PySide import QtCore, QtGui
from IPNestingAsync import BackgroundCall
from IPNestingLanguages import tr
from IPNestingWaitDialog import NestingWaitDialog


def query_devices(executable, cancelled):
    with subprocess.Popen(
        [executable, "--list-gpus"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as process:
        deadline = time.monotonic() + 15
        try:
            while True:
                if cancelled.is_set():
                    process.kill()
                    process.communicate()
                    return []
                if time.monotonic() >= deadline:
                    raise RuntimeError("GPU discovery exceeded 15 seconds")
                try:
                    output, _ = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            text = output.decode("utf-8", errors="replace")
            if process.returncode:
                raise RuntimeError(text.strip() or "GPU discovery failed")
            devices = []
            for line in text.splitlines():
                number, separator, label = line.strip().partition(":")
                if separator and number.strip().isdigit():
                    devices.append((int(number.strip()), label.strip()))
            return devices
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


class GpuDiscovery:
    def __init__(self, panel):
        self.panel = panel
        self.dialog = None
        self.task = None
        self.cancelled = False
        self.timer = QtCore.QTimer(panel.form)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._poll)
        panel.form.destroyed.connect(self.cancel)

    def start(self):
        if self.dialog is not None:
            return
        self.cancelled = False
        self.panel.show_gpus_btn.setEnabled(False)
        self.dialog = NestingWaitDialog(
            self.cancel, self.panel.form,
            message=tr('looking_for_graphic_cards'), title=tr('gpu'))
        self.dialog.show()
        self.dialog.start_after_paint(self._begin)

    def _begin(self):
        if self.cancelled:
            return
        try:
            executable = self.panel._nesting_cli_executable()
            if not executable:
                raise RuntimeError(tr('failed_to_start_the_nesting_process'))
            self.task = BackgroundCall(lambda cancelled: query_devices(executable, cancelled))
            self.task.start()
            self.timer.start()
        except Exception as exc:
            self._finish(error=str(exc))

    def _poll(self):
        result = self.task.poll() if self.task is not None else None
        if result is not None:
            devices, error = result
            self._finish(devices, error)

    def _finish(self, devices=None, error=None):
        self.timer.stop()
        if not self.cancelled and not error:
            self.panel._refresh_gpu_devices(devices or [])
        if self.dialog is not None:
            self.dialog.finish()
            self.dialog.deleteLater()
            self.dialog = None
        self.panel.show_gpus_btn.setEnabled(True)
        if error and not self.cancelled:
            QtGui.QMessageBox.warning(self.panel.form, tr('gpu'), error)

    def cancel(self, *args):
        self.cancelled = True
        if self.task is not None:
            self.task.cancel()
        try:
            self._finish()
        except RuntimeError:
            pass  # The parent window may already have destroyed its Qt children.
