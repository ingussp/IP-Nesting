"""Non-blocking nesting progress window; closing it requests cancellation."""
from PySide import QtGui, QtCore
from IPNestingLanguages import tr, ui_widget, ui_call, register_window


class NestingWaitDialog(QtGui.QDialog):
    def __init__(self, cancel, parent=None, message=None, title=None):
        super().__init__(parent)
        self._cancel = cancel
        self._finished = False
        self._cancelling = False
        self._after_paint = None
        self._painted = False
        self.setWindowTitle(title or tr('run_nesting'))
        self.setMinimumWidth(320)
        layout = QtGui.QVBoxLayout(self)
        self.message = ui_widget(QtGui.QLabel, message or tr('waiting_for_result_json'))
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        progress = QtGui.QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.cancel_button = ui_widget(QtGui.QPushButton, tr('common.cancel'))
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        register_window(self)

    def set_nesting_mode(self, mode, time_limit_seconds=0):
        if mode == 'timed':
            seconds = format(float(time_limit_seconds), '.15g')
            message = tr('nesting_wait_timed_seconds_s') % seconds
        elif mode == 'continuous':
            message = tr('nesting_wait_continuous_cancel_s') % tr('common.cancel')
        else:
            message = tr('waiting_for_result_json')
        ui_call(self.message, 'setText', message)

    def start_after_paint(self, callback):
        """Queue work only after the progress window has actually been painted."""
        if self._painted:
            QtCore.QTimer.singleShot(0, lambda: callback() if not self._finished and not self._cancelling else None)
        else:
            self._after_paint = callback
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        self._painted = True
        if self._after_paint is not None:
            callback, self._after_paint = self._after_paint, None
            QtCore.QTimer.singleShot(0, lambda: callback() if not self._finished and not self._cancelling else None)

    def reject(self):
        if self._finished:
            super().reject()
        elif not self._cancelling:
            self._cancelling = True
            self.cancel_button.setEnabled(False)
            self._cancel()

    def closeEvent(self, event):
        if self._finished:
            event.accept()
        else:
            self.reject()
            event.ignore()

    def finish(self):
        self._finished = True
        self._after_paint = None
        self.done(QtGui.QDialog.Rejected if self._cancelling else QtGui.QDialog.Accepted)
