"""Non-blocking nesting progress window; closing it requests cancellation."""
from PySide import QtGui
from IPNestingLanguages import tr, ui_widget, register_window


class NestingWaitDialog(QtGui.QDialog):
    def __init__(self, cancel, parent=None):
        super().__init__(parent)
        self._cancel = cancel
        self._finished = False
        self._cancelling = False
        self.setWindowTitle(tr('run_nesting'))
        self.setMinimumWidth(320)
        layout = QtGui.QVBoxLayout(self)
        self.message = ui_widget(QtGui.QLabel, tr('waiting_for_result_json'))
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        progress = QtGui.QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        self.cancel_button = ui_widget(QtGui.QPushButton, tr('common.cancel'))
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        register_window(self)

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
        self.done(QtGui.QDialog.Rejected if self._cancelling else QtGui.QDialog.Accepted)
