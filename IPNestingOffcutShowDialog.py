"""
IPNestingOffcutShowDialog - Popup dialog to visualize all added DXF offcuts
and adjust their grain direction (X/Y/None) per offcut.

- Shows each offcut with a 200x200 preview.
- Grain direction is relative to DXF local axes, but user can swap X/Y or set None.
- Draws a small red arrow indicating grain direction on the preview:
    - X: horizontal arrow near the top
    - Y: vertical arrow near the left
    - None: no arrow
"""

import traceback
import math
import time
import os

from IPNestingAddSheet import AddSheetOrOffcutDialog
from IPNestingOffcuts import (extract_offcut_from_dxf,add_or_increment_material,)

import FreeCAD as App
from PySide import QtGui, QtCore


class _OffcutPreview(QtGui.QLabel):
    """A fixed-size preview image of a polygon + grain direction arrow."""
    def __init__(self, poly=None, grain="None", parent=None, size_px=200):
        super(_OffcutPreview, self).__init__(parent)
        self._size_px = int(size_px)
        self.setFixedSize(self._size_px, self._size_px)
        self.setMinimumSize(self._size_px, self._size_px)
        self.setMaximumSize(self._size_px, self._size_px)
        self.setFrameShape(QtGui.QFrame.Box)
        self.setLineWidth(1)
        self.setAlignment(QtCore.Qt.AlignCenter)

        self._poly = []
        self._grain = "None"
        self.set_poly(poly)
        self.set_grain(grain)

    def set_poly(self, poly):
        self._poly = poly or []
        self._render()

    def set_grain(self, grain):
        g = (grain or "None").strip().upper()
        if g in ("X", "Y"):
            self._grain = g
        else:
            self._grain = "None"
        self._render()

    def _draw_grain_arrow(self, painter, w, h):
        """Draw small red arrow indicating grain direction."""
        try:
            if self._grain == "None":
                return

            pen = QtGui.QPen(QtGui.QColor(200, 0, 0), 2)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(QtGui.QColor(200, 0, 0)))

            if self._grain == "X":
                # Horizontal arrow near top: left->right
                x1, y = 20, 18
                x2 = w - 20
                painter.drawLine(x1, y, x2, y)
                # Arrow head at x2
                head = QtGui.QPolygonF([
                    QtCore.QPointF(x2, y),
                    QtCore.QPointF(x2 - 8, y - 5),
                    QtCore.QPointF(x2 - 8, y + 5),
                ])
                painter.drawPolygon(head)

            elif self._grain == "Y":
                # Vertical arrow near left: top->bottom
                x, y1 = 18, 20
                y2 = h - 20
                painter.drawLine(x, y1, x, y2)
                # Arrow head at y2
                head = QtGui.QPolygonF([
                    QtCore.QPointF(x, y2),
                    QtCore.QPointF(x - 5, y2 - 8),
                    QtCore.QPointF(x + 5, y2 - 8),
                ])
                painter.drawPolygon(head)

        except Exception:
            # never fail render because of arrow
            return

    def _render(self):
        render_started = time.time()

        try:
            w = int(self._size_px)
            h = int(self._size_px)

            poly = self._poly or []

            if len(poly) > 10000:
                App.Console.PrintWarning(
                    "[OffcutPreview][DEBUG] polygon has %d points; "
                    "rendering may be slow\n" % len(poly)
                )

            # Validate polygon point data before giving it to Qt.
            valid_points = []

            for index, point in enumerate(poly):
                try:
                    if point is None or len(point) < 2:
                        App.Console.PrintWarning(
                            "[OffcutPreview][DEBUG] invalid point #%d: %s\n"
                            % (index, str(point))
                        )
                        continue

                    x = float(point[0])
                    y = float(point[1])

                    if not (math.isfinite(x) and math.isfinite(y)):
                        App.Console.PrintWarning(
                            "[OffcutPreview][DEBUG] non-finite point #%d: %s\n"
                            % (index, str(point))
                        )
                        continue

                    valid_points.append([x, y])

                except Exception:
                    App.Console.PrintWarning(
                        "[OffcutPreview][DEBUG] failed to read point #%d: %s\n"
                        % (index, traceback.format_exc())
                    )
            pm = QtGui.QPixmap(w, h)
            pm.fill(QtGui.QColor("white"))

            painter = QtGui.QPainter(pm)
            painter.setRenderHint(
                QtGui.QPainter.Antialiasing,
                True
            )

            # Draw light axes using integer coordinates.
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(235, 235, 235),
                    1
                )
            )
            painter.drawLine(0, int(h / 2), w, int(h / 2))
            painter.drawLine(int(w / 2), 0, int(w / 2), h)

            if len(valid_points) >= 2:
                xs = [p[0] for p in valid_points]
                ys = [p[1] for p in valid_points]

                min_x = min(xs)
                max_x = max(xs)
                min_y = min(ys)
                max_y = max(ys)

                dx = max(1e-9, max_x - min_x)
                dy = max(1e-9, max_y - min_y)

                pad = 10.0
                avail_w = float(w) - 2.0 * pad
                avail_h = float(h) - 2.0 * pad
                scale = min(avail_w / dx, avail_h / dy)

                scaled_w = dx * scale
                scaled_h = dy * scale
                ox = pad + 0.5 * (avail_w - scaled_w)
                oy = pad + 0.5 * (avail_h - scaled_h)

                def map_pt(x, y):
                    px = ox + (x - min_x) * scale
                    py = oy + (max_y - y) * scale
                    return QtCore.QPointF(px, py)

                qt_points = [
                    map_pt(point[0], point[1])
                    for point in valid_points
                ]

                painter.setPen(
                    QtGui.QPen(
                        QtGui.QColor(0, 0, 0),
                        2
                    )
                )

                if qt_points:
                    qt_points_closed = qt_points + [qt_points[0]]

                    

                    painter.drawPolyline(
                        QtGui.QPolygonF(qt_points_closed)
                    )

            self._draw_grain_arrow(painter, w, h)

            painter.end()

            self.setPixmap(pm)

            elapsed = time.time() - render_started

            

        except Exception:

            try:
                pm = QtGui.QPixmap(
                    int(self._size_px),
                    int(self._size_px)
                )
                pm.fill(QtGui.QColor("white"))
                self.setPixmap(pm)
            except Exception:
                pass


class OffcutShowDialog(QtGui.QDialog):
    def __init__(self, offcuts, parent=None):
        super(OffcutShowDialog, self).__init__(parent)
        self.setWindowTitle("Offcuts")
        self.setModal(True)

        # 1) Minimum dialog size 700x500
        self.setMinimumSize(700, 500)

        self._offcuts = offcuts  # list of dicts, mutated in place

        root = QtGui.QVBoxLayout(self)

        info = QtGui.QLabel(
            "Added sheets and offcuts. "
            "Grain direction is relative to the material local axes."
        )
        root.addWidget(info)

        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)

        container = QtGui.QWidget()
        v = QtGui.QVBoxLayout(container)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)

        # Build cards
        for idx, off in enumerate(self._offcuts):
            card = QtGui.QGroupBox()
            card_lay = QtGui.QHBoxLayout(card)
            card_lay.setContentsMargins(8, 8, 8, 8)
            card_lay.setSpacing(12)

            left = QtGui.QVBoxLayout()
            title = QtGui.QLabel("<b>%s</b>" % (off.get("label", "Offcut"),))
            left.addWidget(title)
            
            try:
                

                

                polygons = off.get("polygons") or []

                

                poly = polygons[0] if polygons else []
                grain = off.get("grain") or "None"

                

                prev = _OffcutPreview(
                    poly=poly,
                    grain=grain,
                    size_px=200
                )

                

                left.addWidget(prev)

            except Exception:
                App.Console.PrintError(
                    "[OffcutDialog][DEBUG] failed to build card #%d:\n"
                    % idx
                    + traceback.format_exc()
                )
                continue
            card_lay.addLayout(left)

            right = QtGui.QVBoxLayout()
            row = QtGui.QHBoxLayout()
            row.addWidget(QtGui.QLabel("Grain direction:"))

            combo = QtGui.QComboBox()
            # 2) Add None option; default must be None
            combo.addItems(["None", "X", "Y"])
            # 3) Minimum width 50px
            combo.setMinimumWidth(50)

            g = (off.get("grain") or "None").strip().upper()
            if g == "X":
                combo.setCurrentIndex(1)
            elif g == "Y":
                combo.setCurrentIndex(2)
            else:
                combo.setCurrentIndex(0)

            def _on_combo_changed(i, _idx=idx, _prev=prev):
                try:
                    if int(i) == 1:
                        self._offcuts[_idx]["grain"] = "X"
                        _prev.set_grain("X")
                    elif int(i) == 2:
                        self._offcuts[_idx]["grain"] = "Y"
                        _prev.set_grain("Y")
                    else:
                        self._offcuts[_idx]["grain"] = "None"
                        _prev.set_grain("None")
                except Exception:
                    pass

            combo.currentIndexChanged.connect(_on_combo_changed)

            row.addWidget(combo)
            row.addStretch(1)
            right.addLayout(row)

            # Show full path (optional but useful)
            path_lbl = QtGui.QLabel(off.get("path", ""))
            path_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            path_lbl.setWordWrap(True)
            right.addWidget(path_lbl)

            right.addStretch(1)
            card_lay.addLayout(right, 1)

            v.addWidget(card)

        v.addStretch(1)
        scroll.setWidget(container)

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        
class OffcutMaterialsController(object):
    """
    Controls the Sheet & Offcut Materials table and related dialogs.
    """
    
    def __init__(self, panel):
        self.panel = panel

    @property
    def offcuts(self):
        return self.panel.offcuts

    @property
    def offcuts_table(self):
        return self.panel.offcuts_table

    def add_offcut_dxf(self):
        """
        Open the add sheet/offcut dialog and process its result.
        """
        try:
            dialog = AddSheetOrOffcutDialog(
                parent=QtGui.QApplication.activeWindow()
            )

            result = dialog.exec_()

            if result != QtGui.QDialog.Accepted:
                return

            if dialog.result_type == "rectangular":
                App.Console.PrintMessage(
                    "[Offcuts][DIALOG] processing rectangular sheet\n"
                )

                self._process_rectangular_sheet_result(
                    dialog.result_data
                )

            elif dialog.result_type == "dxf":
                App.Console.PrintMessage(
                    "[Offcuts][DIALOG] processing DXF offcut\n"
                )

                self._process_dxf_offcut_result(
                    dialog.result_data
                )

            App.Console.PrintMessage(
                "[Offcuts][DIALOG] result processing finished\n"
            )

        except Exception:
            App.Console.PrintError(
                "add_offcut_dxf failed:\n"
                + traceback.format_exc()
            )
            
    def _process_dxf_offcut_result(self, data):
        """
        Import the selected DXF and add it as one grouped material row.
        Repeated imports of the same DXF increase Count.
        """
        try:
            data = data or {}

            path = os.path.abspath(
                str(data.get("path", "") or "")
            )

            try:
                quantity = int(data.get("quantity", 1))
            except Exception:
                quantity = 1

            quantity = max(1, quantity)

            if not path or not os.path.exists(path):
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "DXF offcut",
                    "The selected DXF file does not exist."
                )
                return

            poly, bbox = extract_offcut_from_dxf(
                path,
                debug=False
            )

            if not poly:
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "DXF offcut",
                    "No closed contour was found in the DXF file."
                )
                return

            offcut = {
                "id": int(self.panel._offcut_next_id),
                "type": "dxf",
                "path": path,
                "label": os.path.basename(path),
                "grain": "None",
                "polygons": [poly],
                "bbox": bbox,
                "count": quantity,
                "quantity": quantity,
                "duplicate": False,
            }

            self.panel._offcut_next_id += 1

            material, row, was_existing = add_or_increment_material(
                self.offcuts,
                offcut,
                count=quantity
            )

            self._rebuild_offcuts_table(
                selected_row=row
            )

        except Exception:
            App.Console.PrintError(
                "_process_dxf_offcut_result failed:\n"
                + traceback.format_exc()
            )
            
    def _process_rectangular_sheet_result(self, data):
        """
        Add a rectangular sheet as one grouped material row.
        Repeated sheets with the same dimensions increase Count.
        """
        try:
            data = data or {}

            try:
                width = float(data.get("width", 0.0))
                height = float(data.get("height", 0.0))
            except Exception:
                width = 0.0
                height = 0.0

            try:
                quantity = int(data.get("quantity", 1))
            except Exception:
                quantity = 1

            quantity = max(1, quantity)

            if width <= 0.0 or height <= 0.0:
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "Sheet",
                    "Sheet width and height must be greater than zero."
                )
                return

            sheet = {
                "id": int(self.panel._offcut_next_id),
                "type": "rectangular",
                "path": "",
                "label": "Sheet %.0f x %.0f mm" % (
                    width,
                    height
                ),
                "grain": "None",
                "polygons": [[
                    [0.0, 0.0],
                    [width, 0.0],
                    [width, height],
                    [0.0, height],
                ]],
                "bbox": {
                    "min_x": 0.0,
                    "min_y": 0.0,
                    "max_x": width,
                    "max_y": height,
                },
                "width": width,
                "height": height,
                "quantity": quantity,
                "count": quantity,
                "duplicate": False,
            }

            self.panel._offcut_next_id += 1

            material, row, was_existing = add_or_increment_material(
                self.offcuts,
                sheet,
                count=quantity
            )

            self._rebuild_offcuts_table(
                selected_row=row
            )

        except Exception:
            App.Console.PrintError(
                "_process_rectangular_sheet_result failed:\n"
                + traceback.format_exc()
            )
            
    def remove_offcuts(self):
        """
        Remove the currently selected material row.
        """
        try:
            selection = self.offcuts_table.selectionModel().selectedRows()

            if not selection:
                return

            rows = sorted(
                [index.row() for index in selection],
                reverse=True
            )

            for row in rows:
                if 0 <= row < len(self.offcuts):
                    del self.offcuts[row]

            self._rebuild_offcuts_table()

        except Exception:
            App.Console.PrintError(
                "remove_offcuts failed:\n"
                + traceback.format_exc()
            )
            
    def _move_offcut_row(self, row, direction):
        """
        Move one material in self.offcuts and rebuild the table.

        direction:
            -1 = up
            +1 = down
        """
        try:
            if row < 0 or row >= len(self.offcuts):
                return

            target_row = row + int(direction)

            if target_row < 0:
                return

            if target_row >= len(self.offcuts):
                return

            # Preserve the material order in the model.
            self.offcuts[row], self.offcuts[target_row] = (
                self.offcuts[target_row],
                self.offcuts[row],
            )

            self._rebuild_offcuts_table(selected_row=target_row)

        except Exception:
            App.Console.PrintError(
                "_move_offcut_row failed:\n"
                + traceback.format_exc()
            )
            
    def _rebuild_offcuts_table(self, selected_row=None):
        """
        Rebuild the table from self.offcuts.

        This guarantees that the visual order and model order
        remain identical.
        """
        try:
            self.offcuts_table.setUpdatesEnabled(False)

            self.offcuts_table.clearContents()
            self.offcuts_table.setRowCount(0)

            for row, material in enumerate(self.offcuts):
                self._append_offcut_table_row(
                    material,
                    row=row
                )

            if selected_row is not None:
                if 0 <= selected_row < self.offcuts_table.rowCount():
                    self.offcuts_table.selectRow(selected_row)
                    self.offcuts_table.setCurrentCell(
                        selected_row,
                        0
                    )

        except Exception:
            App.Console.PrintError(
                "_rebuild_offcuts_table failed:\n"
                + traceback.format_exc()
            )

        finally:
            self.offcuts_table.setUpdatesEnabled(True)
            self.offcuts_table.viewport().update()
            
    def _append_offcut_table_row(self, material, row=None):
        """
        Add one material row to the table.

        Table order follows self.offcuts order.
        Repeated identical materials are represented by Count.
        """
        try:
            if row is None:
                row = self.offcuts_table.rowCount()

            self.offcuts_table.insertRow(row)

            # Column 0: Material
            label = str(
                material.get("label", "Sheet or Offcut")
            )

            material_item = QtGui.QTableWidgetItem(label)
            material_item.setFlags(
                QtCore.Qt.ItemIsEnabled |
                QtCore.Qt.ItemIsSelectable
            )

            if "id" in material:
                try:
                    material_item.setData(
                        QtCore.Qt.UserRole,
                        int(material.get("id"))
                    )
                except Exception:
                    pass

            if bool(material.get("duplicate", False)):
                material_item.setForeground(
                    QtGui.QBrush(QtGui.QColor("red"))
                )

            self.offcuts_table.setItem(row, 0, material_item)

            # Column 1: Count
            try:
                count = max(1, int(material.get("count", 1)))
            except Exception:
                count = 1

            count_item = QtGui.QTableWidgetItem(str(count))
            count_item.setFlags(
                QtCore.Qt.ItemIsEnabled |
                QtCore.Qt.ItemIsSelectable
            )
            count_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.offcuts_table.setItem(row, 1, count_item)

            # Column 2: Grain
            grain = str(
                material.get("grain", "None") or "None"
            ).strip().upper()

            if grain not in ("X", "Y"):
                grain = "None"

            grain_item = QtGui.QTableWidgetItem(grain)
            grain_item.setFlags(
                QtCore.Qt.ItemIsEnabled |
                QtCore.Qt.ItemIsSelectable
            )
            grain_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.offcuts_table.setItem(row, 2, grain_item)

            # Column 3: Move buttons
            move_widget = QtGui.QWidget()
            move_layout = QtGui.QHBoxLayout(move_widget)
            move_layout.setContentsMargins(2, 0, 2, 0)
            move_layout.setSpacing(2)

            up_button = QtGui.QToolButton()
            up_button.setText("↑")
            up_button.setToolTip("Move material up")
            up_button.setAutoRaise(True)
            up_button.setFixedWidth(28)

            down_button = QtGui.QToolButton()
            down_button.setText("↓")
            down_button.setToolTip("Move material down")
            down_button.setAutoRaise(True)
            down_button.setFixedWidth(28)

            up_button.clicked.connect(
                lambda checked=False, r=row:
                    self._move_offcut_row(r, -1)
            )

            down_button.clicked.connect(
                lambda checked=False, r=row:
                    self._move_offcut_row(r, 1)
            )

            move_layout.addWidget(up_button)
            move_layout.addWidget(down_button)

            self.offcuts_table.setCellWidget(row, 3, move_widget)

        except Exception:
            App.Console.PrintError(
                "_append_offcut_table_row failed:\n"
                + traceback.format_exc()
            )
            
    def _refresh_offcut_grain_column(self):
        try:
            for row in range(self.offcuts_table.rowCount()):
                item = self.offcuts_table.item(row, 0)
                if not item:
                    continue

                material_id = item.data(QtCore.Qt.UserRole)

                material = None
                for entry in self.offcuts:
                    if int(entry.get("id", -1)) == int(material_id):
                        material = entry
                        break

                if material is None:
                    continue

                grain_value = str(
                    material.get("grain", "None") or "None"
                ).strip().upper()

                if grain_value not in ("X", "Y"):
                    grain_value = "None"
                    
                grain_item = self.offcuts_table.item(row, 2)

                if grain_item is None:
                    grain_item = QtGui.QTableWidgetItem()
                    grain_item.setFlags(
                        QtCore.Qt.ItemIsEnabled
                        | QtCore.Qt.ItemIsSelectable
                    )
                    grain_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.offcuts_table.setItem(row, 2, grain_item)

                grain_item.setText(grain_value)

        except Exception:
            App.Console.PrintError(
                "_refresh_offcut_grain_column failed:\n"
                + traceback.format_exc()
            )
            
    def show_offcuts_popup(self):
        """Show popup with ALL offcuts and allow changing grain X/Y per offcut."""
        try:
            if not self.offcuts:
                QtGui.QMessageBox.information(None, "Offcuts", "No offcuts added.")
                return
            parent = QtGui.QApplication.activeWindow()
            dlg = OffcutShowDialog(self.offcuts, parent=parent)
            dlg.exec_()
            self._refresh_offcut_grain_column()
        except Exception:
            App.Console.PrintError("show_offcuts_popup failed:\n" + traceback.format_exc())