import FreeCAD as App
from PySide import QtGui, QtCore

# Provide synchronized dial and numeric controls for selecting a grain angle.
class GrainAngleDialog(QtGui.QDialog):
    angleChanged = QtCore.Signal(int)
    # Build the part-label list, angle controls and dialog buttons.
    def __init__(self, parent=None, part_labels=None, initial_angle=0):
        super(GrainAngleDialog, self).__init__(parent)
        self.setWindowTitle("Grain angle")
        self.setModal(True)

        part_labels = part_labels or []

        layout = QtGui.QVBoxLayout(self)

        lbl = QtGui.QLabel("Selected parts / arrows:")
        layout.addWidget(lbl)

        self.listw = QtGui.QListWidget()
        self.listw.addItems([str(x) for x in part_labels])
        self.listw.setMinimumHeight(120)
        self.setMinimumWidth(500)
        self.resize(500, self.height())
        layout.addWidget(self.listw)

        angle_row = QtGui.QHBoxLayout()
        layout.addLayout(angle_row)

        angle_row.addWidget(QtGui.QLabel("Angle (°):"))

        self.dial = QtGui.QDial()
        self.dial.setMinimum(0)
        self.dial.setMaximum(359)
        try:
            self.dial.setInvertedAppearance(True)
            self.dial.setInvertedControls(True)
        except Exception:
            pass
        self.dial.setNotchesVisible(True)
        self.dial.setMinimumSize(200, 200)
        self.dial.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Expanding)
        self.dial.setNotchesVisible(True)
        self.dial.setWrapping(True)
        self.dial.setValue(int(initial_angle) % 360)
        angle_row.addWidget(self.dial)

        self.spin = QtGui.QSpinBox()
        self.spin.setMinimum(0)
        self.spin.setMaximum(359)
        self.spin.setValue(int(initial_angle) % 360)
        angle_row.addWidget(self.spin)

        # Copy the dial angle to the spin box and emit angleChanged while avoiding signal
        # recursion.
        def _dial_changed(v):
            try:
                self.spin.blockSignals(True)
                self.spin.setValue(int(v))
            finally:
                self.spin.blockSignals(False)
            try:
                self.angleChanged.emit(int(v) % 360)
            except Exception:
                pass

        # Copy the spin-box angle to the dial and emit angleChanged while avoiding signal
        # recursion.
        def _spin_changed(v):
            try:
                self.dial.blockSignals(True)
                self.dial.setValue(int(v))
            finally:
                self.dial.blockSignals(False)
            try:
                self.angleChanged.emit(int(v) % 360)
            except Exception:
                pass

        self.dial.valueChanged.connect(_dial_changed)
        self.spin.valueChanged.connect(_spin_changed)

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # Return the selected spin-box angle as an integer.
    def angle_degrees(self):
        try:
            return int(self.spin.value()) % 360
        except Exception:
            return 0