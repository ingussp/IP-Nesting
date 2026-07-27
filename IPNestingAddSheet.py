"""
IPNestingAddSheet

Dialog for adding rectangular sheets or DXF offcuts.
The dialog only collects and validates user input.
The parent panel is responsible for storing the result.
"""

import os

from PySide import QtGui, QtCore


class AddSheetOrOffcutDialog(QtGui.QDialog):
    """
    Dialog for adding either:
      - a rectangular sheet;
      - a DXF offcut.

    Result is available through:
      result_type
      result_data
    """

    def __init__(self, parent=None):
        super(AddSheetOrOffcutDialog, self).__init__(parent)

        self.result_type = None
        self.result_data = None

        self.setWindowTitle("Add Sheet or Offcut")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._build_ui()

    def _build_ui(self):
        layout = QtGui.QVBoxLayout(self)

        info = QtGui.QLabel(
            "Add a rectangular sheet or import a DXF offcut."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._build_rectangular_sheet_section(layout)
        self._build_dxf_section(layout)

        buttons = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Cancel
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_rectangular_sheet_section(self, parent_layout):
        sheet_box = QtGui.QGroupBox("Rectangular sheet")
        sheet_layout = QtGui.QVBoxLayout(sheet_box)

        width_row = QtGui.QHBoxLayout()
        width_row.addWidget(
            QtGui.QLabel("Sheet width (X) (mm):")
        )

        self.width_edit = QtGui.QLineEdit("2000.00")
        self.width_edit.setValidator(
            QtGui.QDoubleValidator(0.0, 1000000.0, 3, self)
        )
        width_row.addWidget(self.width_edit)
        sheet_layout.addLayout(width_row)

        height_row = QtGui.QHBoxLayout()
        height_row.addWidget(
            QtGui.QLabel("Sheet height (Y) (mm):")
        )

        self.height_edit = QtGui.QLineEdit("2800.00")
        self.height_edit.setValidator(
            QtGui.QDoubleValidator(0.0, 1000000.0, 3, self)
        )
        height_row.addWidget(self.height_edit)
        sheet_layout.addLayout(height_row)

        quantity_row = QtGui.QHBoxLayout()
        quantity_row.addWidget(
            QtGui.QLabel("Quantity:")
        )

        self.quantity_spin = QtGui.QSpinBox()
        self.quantity_spin.setMinimum(1)
        self.quantity_spin.setMaximum(100000)
        self.quantity_spin.setValue(1)
        quantity_row.addWidget(self.quantity_spin)
        sheet_layout.addLayout(quantity_row)

        self.add_rectangular_btn = QtGui.QPushButton(
            "Add rectangular sheet"
        )
        self.add_rectangular_btn.clicked.connect(
            self._on_add_rectangular_clicked
        )
        sheet_layout.addWidget(self.add_rectangular_btn)

        parent_layout.addWidget(sheet_box)

    def _build_dxf_section(self, parent_layout):
        dxf_box = QtGui.QGroupBox("DXF offcut")
        dxf_layout = QtGui.QVBoxLayout(dxf_box)

        info = QtGui.QLabel(
            "Import a DXF file and use its closed contour as an offcut."
        )
        info.setWordWrap(True)
        dxf_layout.addWidget(info)

        dxf_quantity_row = QtGui.QHBoxLayout()
        dxf_quantity_row.addWidget(
            QtGui.QLabel("Quantity:")
        )

        self.dxf_quantity_spin = QtGui.QSpinBox()
        self.dxf_quantity_spin.setMinimum(1)
        self.dxf_quantity_spin.setMaximum(100000)
        self.dxf_quantity_spin.setValue(1)
        dxf_quantity_row.addWidget(self.dxf_quantity_spin)

        dxf_layout.addLayout(dxf_quantity_row)

        self.add_dxf_btn = QtGui.QPushButton(
            "Add DXF offcut"
        )
        self.add_dxf_btn.clicked.connect(
            self._on_add_dxf_clicked
        )
        dxf_layout.addWidget(self.add_dxf_btn)

        parent_layout.addWidget(dxf_box)

    def _on_add_rectangular_clicked(self):
        try:
            width = float(self.width_edit.text().strip())
            height = float(self.height_edit.text().strip())
            quantity = int(self.quantity_spin.value())

            if width <= 0.0:
                self._show_warning(
                    "Sheet width must be greater than zero."
                )
                return

            if height <= 0.0:
                self._show_warning(
                    "Sheet height must be greater than zero."
                )
                return

            if quantity < 1:
                self._show_warning(
                    "Quantity must be at least 1."
                )
                return

            self.result_type = "rectangular"
            self.result_data = {
                "width": width,
                "height": height,
                "quantity": quantity,
            }

            self.accept()

        except Exception:
            self._show_warning(
                "Enter valid numeric values for sheet width and height."
            )

    def _on_add_dxf_clicked(self):
        path, _ = QtGui.QFileDialog.getOpenFileName(
            self,
            "Select DXF offcut",
            "",
            "DXF files (*.dxf *.DXF);;All files (*.*)"
        )

        if not path:
            return

        path = os.path.abspath(str(path))

        if not os.path.exists(path):
            self._show_warning(
                "The selected DXF file does not exist."
            )
            return

        quantity = int(self.dxf_quantity_spin.value())

        self.result_type = "dxf"
        self.result_data = {
            "path": path,
            "quantity": quantity,
        }

        self.accept()

    def _show_warning(self, message):
        QtGui.QMessageBox.warning(
            self,
            "Add Sheet or Offcut",
            message
        )