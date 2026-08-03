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


class _HoleGraphicsItem(QtGui.QGraphicsPolygonItem):
    """
    Clickable hole polygon.
    Active hole = forbidden area for nesting.
    """

    def __init__(
        self,
        hole_index,
        polygon,
        selected,
        callback,
        parent=None
    ):
        super(_HoleGraphicsItem, self).__init__(
            QtGui.QPolygonF(polygon),
            parent
        )

        self.hole_index = int(hole_index)
        self.selected = bool(selected)
        self.callback = callback

        self.setAcceptedMouseButtons(
            QtCore.Qt.LeftButton
        )

        self.update_style()

    def update_style(self):
        if self.selected:
            self.setBrush(
                QtGui.QBrush(
                    QtGui.QColor(255, 120, 120)
                )
            )
            self.setPen(
                QtGui.QPen(
                    QtGui.QColor(180, 0, 0),
                    2
                )
            )
        else:
            self.setBrush(
                QtGui.QBrush(
                    QtGui.QColor(220, 220, 220)
                )
            )
            self.setPen(
                QtGui.QPen(
                    QtGui.QColor(100, 100, 100),
                    2
                )
            )

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.selected = not self.selected
            self.update_style()

            if self.callback:
                self.callback(
                    self.hole_index,
                    self.selected
                )

            event.accept()
            return

        super(_HoleGraphicsItem, self).mousePressEvent(event)


class _OffcutPreview(QtGui.QGraphicsView):
    """
    Proportional sheet/offcut preview with:

    - full outer contour;
    - all holes;
    - mouse-wheel zoom;
    - horizontal and vertical scrolling;
    - clickable holes.
    """

    def __init__(
        self,
        outer=None,
        holes=None,
        selected_holes=None,
        on_hole_clicked=None,
        grain="None",
        parent=None,
        size_px=700
    ):
        super(_OffcutPreview, self).__init__(parent)

        self._outer = list(outer or [])
        self._holes = [
            list(hole or [])
            for hole in (holes or [])
        ]

        self._selected_holes = list(
            selected_holes or []
        )

        while len(self._selected_holes) < len(
            self._holes
        ):
            self._selected_holes.append(True)

        self._on_hole_clicked = on_hole_clicked
        self._grain = "None"
        self._zoom = 1.0
        self._has_user_zoom = False

        self._scene = QtGui.QGraphicsScene(self)
        self.setScene(self._scene)

        self.setMinimumSize(500, 400)
        self.setSizePolicy(
            QtGui.QSizePolicy.Expanding,
            QtGui.QSizePolicy.Expanding
        )

        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded
        )
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded
        )

        self.setTransformationAnchor(
            QtGui.QGraphicsView.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QtGui.QGraphicsView.AnchorViewCenter
        )

        self.setRenderHint(
            QtGui.QPainter.Antialiasing,
            True
        )

        self.setBackgroundBrush(
            QtGui.QBrush(QtGui.QColor("white"))
        )

        self.set_grain(grain)
        self._rebuild_scene()

    def set_grain(self, grain):
        value = str(
            grain or "None"
        ).strip().upper()

        if value not in ("X", "Y"):
            value = "None"

        self._grain = value
        self._rebuild_scene(
            preserve_view=True
        )

    def _valid_points(self, polygon):
        result = []

        for point in polygon or []:
            try:
                if point is None or len(point) < 2:
                    continue

                x = float(point[0])
                y = float(point[1])

                if (
                    math.isfinite(x)
                    and math.isfinite(y)
                ):
                    result.append(
                        QtCore.QPointF(x, -y)
                    )

            except Exception:
                continue

        return result

    def _rebuild_scene(self, preserve_view=False):
        old_center = None

        try:
            if preserve_view:
                old_center = self.mapToScene(
                    self.viewport().rect().center()
                )
        except Exception:
            old_center = None

        self._scene.clear()

        outer_points = self._valid_points(
            self._outer
        )

        if len(outer_points) >= 3:
            outer_item = self._scene.addPolygon(
                QtGui.QPolygonF(outer_points),
                QtGui.QPen(
                    QtGui.QColor(0, 0, 0),
                    2
                ),
                QtGui.QBrush(
                    QtGui.QColor(245, 245, 245)
                )
            )

            outer_item.setZValue(0)

        for index, hole in enumerate(self._holes):
            hole_points = self._valid_points(hole)

            if len(hole_points) < 3:
                continue

            selected = True

            if index < len(
                self._selected_holes
            ):
                selected = bool(
                    self._selected_holes[index]
                )

            hole_item = _HoleGraphicsItem(
                hole_index=index,
                polygon=hole_points,
                selected=selected,
                callback=self._hole_clicked,
            )

            hole_item.setZValue(1)
            self._scene.addItem(hole_item)

        # Add a small margin around the whole model.
        bounds = self._scene.itemsBoundingRect()

        if not bounds.isNull():
            margin = max(
                10.0,
                max(
                    bounds.width(),
                    bounds.height()
                ) * 0.03
            )

            self._scene.setSceneRect(
                bounds.adjusted(
                    -margin,
                    -margin,
                    margin,
                    margin
                )
            )

        if not self._has_user_zoom:
            self.fitInView(
                self._scene.sceneRect(),
                QtCore.Qt.KeepAspectRatio
            )

        if old_center is not None:
            try:
                self.centerOn(old_center)
            except Exception:
                pass

    def _hole_clicked(self, hole_index, enabled):
        while len(self._selected_holes) <= hole_index:
            self._selected_holes.append(True)

        self._selected_holes[hole_index] = bool(
            enabled
        )

        if self._on_hole_clicked:
            self._on_hole_clicked(
                hole_index,
                bool(enabled)
            )

    def wheelEvent(self, event):
        """
        Mouse wheel zoom.

        Scroll up:
            zoom in

        Scroll down:
            zoom out
        """
        try:
            delta = event.angleDelta().y()

            if delta == 0:
                event.ignore()
                return

            if delta > 0:
                factor = 1.20
            else:
                factor = 1.0 / 1.20

            new_zoom = self._zoom * factor
            new_zoom = max(
                0.15,
                min(12.0, new_zoom)
            )

            factor = new_zoom / self._zoom
            self._zoom = new_zoom
            self._has_user_zoom = True

            self.scale(
                factor,
                factor
            )

            event.accept()

        except Exception:
            App.Console.PrintError(
                "OffcutPreview.wheelEvent failed:\n"
                + traceback.format_exc()
            )
            event.ignore()

    def mouseDoubleClickEvent(self, event):
        """
        Double-click resets the preview to fit the whole sheet.
        """
        try:
            if event.button() == QtCore.Qt.LeftButton:
                self._zoom = 1.0
                self._has_user_zoom = False
                self.resetTransform()

                self.fitInView(
                    self._scene.sceneRect(),
                    QtCore.Qt.KeepAspectRatio
                )

                event.accept()
                return

        except Exception:
            pass

        super(_OffcutPreview, self).mouseDoubleClickEvent(
            event
        )

    def resizeEvent(self, event):
        super(_OffcutPreview, self).resizeEvent(event)

        if not self._has_user_zoom:
            try:
                self.fitInView(
                    self._scene.sceneRect(),
                    QtCore.Qt.KeepAspectRatio
                )
            except Exception:
                pass


class OffcutShowDialog(QtGui.QDialog):
    def __init__(self, offcuts, parent=None):
        super(OffcutShowDialog, self).__init__(parent)
        self.setWindowTitle("Offcuts")
        self.setModal(True)
        
        self.setSizeGripEnabled(True)

        # 1) Minimum dialog size 900x700
        self.resize(1100, 850)
        self.setMinimumSize(900, 700)

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
            card_lay = QtGui.QVBoxLayout(card)
            card_lay.setContentsMargins(10, 10, 10, 10)
            card_lay.setSpacing(8)

            label = str(off.get("label", "Offcut"))

            try:
                count = max(
                    1,
                    int(
                        off.get(
                            "count",
                            off.get("quantity", 1)
                        )
                    )
                )
            except Exception:
                count = 1

            # -------------------------
            # Controls above the model
            # -------------------------
            header_lay = QtGui.QHBoxLayout()
            header_lay.setSpacing(12)

            title = QtGui.QLabel(
                "<b>%s</b>  |  Count: %d"
                % (label, count)
            )
            header_lay.addWidget(title)

            header_lay.addStretch(1)

            header_lay.addWidget(
                QtGui.QLabel("Grain direction:")
            )

            combo = QtGui.QComboBox()
            combo.addItems(["None", "X", "Y"])
            combo.setMinimumWidth(75)

            grain = str(
                off.get("grain", "None")
                or "None"
            ).strip().upper()

            if grain == "X":
                combo.setCurrentIndex(1)
            elif grain == "Y":
                combo.setCurrentIndex(2)
            else:
                combo.setCurrentIndex(0)

            header_lay.addWidget(combo)
            card_lay.addLayout(header_lay)

            # DXF path below the header, still above the model
            path = str(off.get("path", "") or "")

            if path:
                path_label = QtGui.QLabel(path)
                path_label.setWordWrap(True)
                path_label.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                )
                card_lay.addWidget(path_label)

            # -------------------------
            # Outer contour and holes
            # -------------------------
            outer = off.get("outer") or []

            # Compatibility with older records
            if not outer:
                polygons = off.get("polygons") or []
                outer = polygons[0] if polygons else []

            holes = list(off.get("holes") or [])

            selected_holes = off.get("selected_holes")

            if selected_holes is None:
                selected_holes = [True for _ in holes]
                off["selected_holes"] = selected_holes
            else:
                selected_holes = list(selected_holes)

            while len(selected_holes) < len(holes):
                selected_holes.append(True)

            off["selected_holes"] = selected_holes

            # -------------------------
            # Hole controls above model
            # -------------------------
            hole_controls = QtGui.QHBoxLayout()

            if holes:
                hole_controls.addWidget(
                    QtGui.QLabel("Holes:")
                )

                for hole_index in range(len(holes)):
                    hole_checkbox = QtGui.QCheckBox(
                        "Hole %d" % (hole_index + 1)
                    )
                    hole_checkbox.setChecked(
                        bool(selected_holes[hole_index])
                    )

                    def _on_hole_checkbox_changed(
                        state,
                        _idx=hole_index,
                        _off=off
                    ):
                        try:
                            selected = _off.setdefault(
                                "selected_holes",
                                [
                                    True
                                    for _ in (
                                        _off.get("holes")
                                        or []
                                    )
                                ]
                            )

                            while len(selected) <= _idx:
                                selected.append(True)

                            selected[_idx] = bool(state)

                            preview = _off.get(
                                "_preview_widget"
                            )

                            if preview is not None:
                                preview._render()

                        except Exception:
                            App.Console.PrintError(
                                "Failed to update hole checkbox:\n"
                                + traceback.format_exc()
                            )

                    hole_checkbox.stateChanged.connect(
                        _on_hole_checkbox_changed
                    )

                    hole_controls.addWidget(
                        hole_checkbox
                    )

                hole_controls.addStretch(1)
                card_lay.addLayout(hole_controls)

            # -------------------------
            # Large preview
            # -------------------------
            def _on_hole_clicked(
                hole_index,
                enabled,
                _off=off
            ):
                try:
                    selected = _off.setdefault(
                        "selected_holes",
                        [
                            True
                            for _ in (
                                _off.get("holes")
                                or []
                            )
                        ]
                    )

                    while len(selected) <= hole_index:
                        selected.append(True)

                    selected[hole_index] = bool(enabled)

                except Exception:
                    App.Console.PrintError(
                        "Failed to update hole state:\n"
                        + traceback.format_exc()
                    )

            preview = _OffcutPreview(
                outer=outer,
                holes=holes,
                selected_holes=selected_holes,
                on_hole_clicked=_on_hole_clicked,
                grain=grain,
                parent=card,
                size_px=700
            )

            # Store preview so checkbox callbacks can refresh it.
            off["_preview_widget"] = preview

            card_lay.addWidget(
                preview,
                1
            )

            def _on_combo_changed(
                index,
                _off=off,
                _preview=preview
            ):
                try:
                    if int(index) == 1:
                        _off["grain"] = "X"
                        _preview.set_grain("X")
                    elif int(index) == 2:
                        _off["grain"] = "Y"
                        _preview.set_grain("Y")
                    else:
                        _off["grain"] = "None"
                        _preview.set_grain("None")
                except Exception:
                    pass

            combo.currentIndexChanged.connect(
                _on_combo_changed
            )

            v.addWidget(card, 1)

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
        self._count_update_guard = False
        self._table_rebuild_guard = False

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

            outer, holes, bbox = extract_offcut_from_dxf(
                path,
                debug=False
            )
            
            App.Console.PrintMessage(
                "[Offcuts] Outer points: %d, holes: %d\n"
                % (
                    len(outer or []),
                    len(holes or [])
                )
            )
            
            if not outer:
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "DXF offcut",
                    "No closed outer contour was found in the DXF file."
                )
                return

            offcut = {
                "id": int(self.panel._offcut_next_id),
                "type": "dxf",
                "path": path,
                "label": os.path.basename(path),
                "grain": "None",

                "outer": outer,
                "holes": holes,
                "selected_holes": [
                    True for _ in holes
                ],

                # Compatibility with old code
                "polygons": [outer],

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

                "outer": [
                    [0.0, 0.0],
                    [width, 0.0],
                    [width, height],
                    [0.0, height],
                ],

                "holes": [],
                "selected_holes": [],

                # Compatibility
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
            
    def _set_count_cell_text(self, item, value):
        """
        Safely restore or update a Count cell without triggering
        recursive Count processing.
        """
        try:
            self._count_update_guard = True
            item.setText(str(max(1, int(value))))
        except Exception:
            pass
        finally:
            self._count_update_guard = False
    
    def on_offcut_count_changed(self, item):
        """
        Update the material count when the Count cell is edited.
        """
        if self._count_update_guard:
            return

        if item is None:
            return

        # Count is column 1.
        if item.column() != 1:
            return

        row = item.row()

        if row < 0 or row >= len(self.offcuts):
            return

        material = self.offcuts[row]

        try:
            text = str(item.text()).strip()

            # Empty or invalid input restores the previous value.
            new_count = int(text)

            if new_count < 1:
                new_count = 1

            if new_count > 100000:
                new_count = 100000

        except Exception:
            try:
                old_count = int(
                    material.get(
                        "count",
                        material.get("quantity", 1)
                    )
                )
            except Exception:
                old_count = 1

            self._set_count_cell_text(item, old_count)
            return

        try:
            self._count_update_guard = True

            material["count"] = new_count
            material["quantity"] = new_count

            if item.text() != str(new_count):
                item.setText(str(new_count))

        except Exception:
            App.Console.PrintError(
                "on_offcut_count_changed failed:\n"
                + traceback.format_exc()
            )

        finally:
            self._count_update_guard = False
    
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
        Rebuild the materials table from self.offcuts.
        """
        if self._table_rebuild_guard:
            return

        try:
            self._table_rebuild_guard = True
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
            self._table_rebuild_guard = False
            
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
                QtCore.Qt.ItemIsEnabled
                | QtCore.Qt.ItemIsSelectable
                | QtCore.Qt.ItemIsEditable
            )
            count_item.setTextAlignment(QtCore.Qt.AlignCenter)
            count_item.setToolTip("Enter the number of sheets or offcuts.")
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
            
    def _get_selected_offcuts(self):
        """
        Return material records corresponding to selected table rows.
        The order follows the table/model order.
        """
        selected = []

        try:
            indexes = self.offcuts_table.selectionModel().selectedRows()

            rows = sorted(
                set(index.row() for index in indexes)
            )

            for row in rows:
                if 0 <= row < len(self.offcuts):
                    selected.append(self.offcuts[row])

        except Exception:
            App.Console.PrintError(
                "_get_selected_offcuts failed:\n"
                + traceback.format_exc()
            )

        return selected
    
    def show_offcuts_popup(self):
        """
        Show previews only for the selected sheet/offcut rows.
        """
        try:
            selected_offcuts = self._get_selected_offcuts()

            if not selected_offcuts:
                QtGui.QMessageBox.information(
                    self.panel.form,
                    "Sheets and Offcuts",
                    "Select at least one sheet or offcut first."
                )
                return

            parent = QtGui.QApplication.activeWindow()

            dlg = OffcutShowDialog(
                selected_offcuts,
                parent=parent
            )

            dlg.exec_()

            # Grain values are updated in-place by OffcutShowDialog.
            self._refresh_offcut_grain_column()

        except Exception:
            App.Console.PrintError(
                "show_offcuts_popup failed:\n"
                + traceback.format_exc()
            )