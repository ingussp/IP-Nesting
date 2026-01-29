# UI Definition for IP - Nesting Task Panel (final)
# Uses NestingRotator in IPNestingRotate.py for rotate/flip operations
# Integrates GrainPreparer from IPNestingGrain.py for grain perimeter and arrows.
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import json
import os
import math
import traceback
import time
import re
from IPNestingRelayout import NestingRelayoutManager
from functools import partial
from IPNestingExport import execute_nesting as execute_nesting_impl
from IPNestingGrainUI import GrainUIController
from IPNestingPreviewDoc import PreviewDocManager

try:
    from IPNestingRotate import NestingRotator
except Exception:
    try:
        from .IPNestingRotate import NestingRotator
    except Exception:
        App.Console.PrintError("Failed to import NestingRotator (IPNestingRotate.py):\n" + traceback.format_exc())
        NestingRotator = None

# Grain preparer integration (attempt import; fallback to None)
try:
    from IPNestingGrain import GrainPreparer
except Exception:
    GrainPreparer = None

class NestingTaskPanel:
    class _SelectionObserver:
        def __init__(self, panel):
            self.panel = panel
            self._alive = True

        def _unregister(self):
            if self._alive:
                try:
                    Gui.Selection.removeObserver(self)
                except Exception:
                    pass
                finally:
                    self._alive = False

        def _panel_table_alive(self):
            try:
                if not self.panel:
                    self._unregister()
                    return False
                tbl = getattr(self.panel, "table", None)
                if tbl is None:
                    self._unregister()
                    return False
                _ = tbl.rowCount()
                return True
            except RuntimeError:
                self._unregister()
                return False
            except Exception:
                return True

        def addSelection(self, doc, obj, sub, pos=None):
            try:
                if not self._panel_table_alive():
                    return
                if getattr(self.panel, "_suppress_selection_update", False):
                    return
                if doc == self.panel.preview_doc_name:
                    # Only iterate data rows (exclude control rows)
                    for r in range(self.panel.table.rowCount() - self.panel.control_rows):
                        try:
                            item = self.panel.table.item(r, 0)
                            # item.data(QtCore.Qt.UserRole) contains the primary preview object name
                            if item and item.data(QtCore.Qt.UserRole) == obj:
                                try:
                                    self.panel._suppress_selection_update = True
                                    self.panel.table.selectRow(r)
                                    self.panel.table.scrollToItem(item)
                                finally:
                                    self.panel._suppress_selection_update = False
                                break
                        except RuntimeError:
                            self._unregister()
                            return
                        except Exception:
                            App.Console.PrintError("SelectionObserver.addSelection per-row error:\n" + traceback.format_exc())
            except Exception:
                App.Console.PrintError("SelectionObserver.addSelection error:\n" + traceback.format_exc())

        def removeSelection(self, doc, obj, sub):
            try:
                if not self._panel_table_alive():
                    return
                if getattr(self.panel, "_suppress_selection_update", False):
                    return
                try:
                    sel = Gui.Selection.getSelectionEx()
                    still_has = any(s.Doc.Name == self.panel.preview_doc_name for s in sel)
                    if not still_has:
                        try:
                            self.panel.table.clearSelection()
                        except RuntimeError:
                            self._unregister()
                except RuntimeError:
                    self._unregister()
                except Exception:
                    pass
            except Exception:
                App.Console.PrintError("SelectionObserver.removeSelection error:\n" + traceback.format_exc())

        def clearSelection(self, doc):
            try:
                if not self._panel_table_alive():
                    return
                if getattr(self.panel, "_suppress_selection_update", False):
                    return
                if doc == self.panel.preview_doc_name:
                    try:
                        self.panel.table.clearSelection()
                    except RuntimeError:
                        self._unregister()
            except Exception:
                App.Console.PrintError("SelectionObserver.clearSelection error:\n" + traceback.format_exc())

    def __init__(self):
        self.preview_doc_name = "Nesting_Preview"
        self.added_count = 0
        self.grid_cols = 4
        self.grid_spacing = 250

        # Number of control rows at bottom of table (now two separate rows)
        self.control_rows = 2

        self._suppress_selection_update = False
        self._suppress_qty_update = False

        self.form = QtGui.QWidget()
        self.layout = QtGui.QVBoxLayout(self.form)

        # Sheet Settings
        self.layout.addWidget(QtGui.QLabel("<b>Sheet Settings</b>"))
        self.sheet_w = self.create_input("Sheet Width (mm):", "2500.00", "Total width.")
        self.sheet_h = self.create_input("Sheet Height (mm):", "1250.00", "Total height.")
        self.sheet_margin = self.create_input("Sheet Margin (mm):", "5.00", "Edge margin.")
        # New: Sheet grain direction
        row = QtGui.QHBoxLayout()
        row.addWidget(QtGui.QLabel("Sheet grain direction:"))
        self.sheet_grain_combo = QtGui.QComboBox()
        self.sheet_grain_combo.addItems(["Width", "Height", "None"])
        self.sheet_grain_combo.setCurrentIndex(2)
        self.sheet_grain_combo.setToolTip("Sheet grain direction: Width/Height/None")
        row.addWidget(self.sheet_grain_combo)
        self.layout.addLayout(row)

        # General Parameters
        self.layout.addWidget(QtGui.QLabel("<b>General Parameters</b>"))
        self.spacing = self.create_input("Part Spacing (mm):", "2.00", "Part gap.")
        self.res = self.create_input("Boundary Resolution:", "0.1", "Curve resolution.")

        # Nesting Strategy and Algorithm
        self.layout.addWidget(QtGui.QLabel("<b>Nesting Strategy</b>"))
        self.select_strategy = QtGui.QComboBox()
        self.select_strategy.addItems(["Largest Area First", "Smallest Area First", "None"])
        self.layout.addWidget(self.select_strategy)

        # New: Nesting Algorithm (for libnest2d)
        self.layout.addWidget(QtGui.QLabel("<b>Nesting Algorithm</b>"))
        self.nesting_algorithm = QtGui.QComboBox()
        # Typical algorithms; adjust as needed to match libnest2d supported algorithms
        self.nesting_algorithm.addItems([
            "Bottom-Left",
            "Guillotine",
            "Extreme Points",
            "Genetic",
            "No-Fit-Polygon",
            "None"
        ])
        self.nesting_algorithm.setCurrentIndex(0)
        self.layout.addWidget(self.nesting_algorithm)

        # Optimization
        self.layout.addWidget(QtGui.QLabel("<b>Optimization</b>"))
        self.gen = self.create_input("Generations:", "5", "Iterations.")
        self.pop = self.create_input("Population Size:", "20", "Solutions.")

        # Table (with control_rows at the bottom)
        self.layout.addWidget(QtGui.QLabel("<b>Selected Parts (Preview Mode)</b>"))
        self.table = QtGui.QTableWidget(self.control_rows, 5)  # reserve control_rows initially
        self.table.setHorizontalHeaderLabels([
            "Body", "Qty", "Rotation degree", "Select for rotation", "Grain Direction"
        ])
        self.table.setMinimumHeight(400)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.table.cellClicked.connect(self.on_cell_clicked)
        # Listen for item changes (Qty edits)
        self.table.itemChanged.connect(self.on_item_changed)

        # Ensure header won't auto-stretch the first column; make it fixed and set width to 250
        try:
            header = self.table.horizontalHeader()
            if hasattr(header, "setSectionResizeMode"):
                header.setSectionResizeMode(0, QtGui.QHeaderView.Fixed)
            else:
                header.setResizeMode(0, QtGui.QHeaderView.Fixed)
        except Exception:
            pass
        try:
            self.table.setColumnWidth(0, 250)
            # Set Qty column width to 40px as requested
            self.table.setColumnWidth(1, 40)
        except Exception:
            pass

        self.layout.addWidget(self.table)

        # rotator instance
        self._rotator = NestingRotator(self.preview_doc_name) if NestingRotator is not None else None

        # Create two control rows
        self._create_control_rows()

        # Initialize grain UI controller (handles blinking and grain operations)
        self._grain = GrainUIController(self)
        
        # Initialize preview document manager (handles preview operations)
        self._preview = PreviewDocManager(self)

        # Add / Remove buttons (larger, with top/bottom margin 5px)
        self.btn_layout = QtGui.QHBoxLayout()
        self.btn_layout.setContentsMargins(0, 5, 0, 5)
        self.add_btn = QtGui.QPushButton("Add Selected")
        self.rem_btn = QtGui.QPushButton("Remove Selected")
        self.add_btn.setFixedHeight(32)
        self.rem_btn.setFixedHeight(32)
        self.add_btn.setFixedWidth(140)
        self.rem_btn.setFixedWidth(140)
        self.add_btn.clicked.connect(self.add_selected_objects)
        # Remove Selected behaves like Qty -> 0 for selected rows
        self.rem_btn.clicked.connect(self.remove_selected_rows)
        self.btn_layout.addWidget(self.add_btn)
        self.btn_layout.addWidget(self.rem_btn)
        self.layout.addLayout(self.btn_layout)

        # Run button
        self.run_btn = QtGui.QPushButton("Run Nesting")
        self.run_btn.setStyleSheet("background-color: #CF3519; color: white; font-weight: bold; height: 35px;")
        self.run_btn.clicked.connect(self.execute_nesting)
        self.layout.addWidget(self.run_btn)

        try:
            self._selection_observer = NestingTaskPanel._SelectionObserver(self)
            Gui.Selection.addObserver(self._selection_observer)
        except Exception:
            App.Console.PrintError("Failed to add selection observer:\n" + traceback.format_exc())

    def create_input(self, label, default, tooltip):
        row = QtGui.QHBoxLayout()
        edit = QtGui.QLineEdit(default)
        edit.setToolTip(tooltip)
        row.addWidget(QtGui.QLabel(label))
        row.addWidget(edit)
        self.layout.addLayout(row)
        return edit

    # --- Apply Grain blinking helpers (delegated to GrainUIController) ---
    def _on_apply_blink_tick(self):
        """Timer callback - delegates to grain controller."""
        self._grain._on_apply_blink_tick()
    
    def _start_apply_blink(self):
        """Start blinking - delegates to grain controller."""
        self._grain._start_apply_blink()
    
    def _stop_apply_blink(self):
        """Stop blinking - delegates to grain controller."""
        self._grain._stop_apply_blink()
    
    def _update_apply_blink_state(self):
        """Update blinking state - delegates to grain controller."""
        self._grain._update_apply_blink_state()

    # --- Grain arrow helpers (delegated to GrainUIController) ---
    def _on_grain_checkbox_state_changed(self, preview_obj_name, grain_cb, grain_combo, state):
        """Callback for per-row grain checkbox - delegates to grain controller."""
        self._grain._on_grain_checkbox_state_changed(preview_obj_name, grain_cb, grain_combo, state)
            
    def _on_grain_axis_changed(self, preview_obj_name, grain_cb, grain_combo, index):
        """Callback for per-row grain axis combobox - delegates to grain controller."""
        self._grain._on_grain_axis_changed(preview_obj_name, grain_cb, grain_combo, index)

    def _connect_grain_widgets(self, grain_cb, grain_combo, preview_obj_name):
        """Wire per-row grain widgets - delegates to grain controller."""
        self._grain._connect_grain_widgets(grain_cb, grain_combo, preview_obj_name)

    def _create_control_rows(self):
        """Create two control rows at the bottom:
           - row (table.rowCount()-2): Rotate controls
           - row (table.rowCount()-1): Change grain direction controls
        """
        try:
            total_rows = self.table.rowCount()
            # ensure we have exactly control_rows rows reserved at bottom; they are already created at init
            # Top control row index:
            top_idx = total_rows - self.control_rows
            bottom_idx = total_rows - 1

            # --- Top control row: Rotate controls ---
            # Clean existing cell widgets/items in that row
            for c in range(self.table.columnCount()):
                itm = self.table.item(top_idx, c)
                if itm:
                    self.table.setItem(top_idx, c, None)
                w = self.table.cellWidget(top_idx, c)
                if w is not None:
                    w.setParent(None)

            container_top = QtGui.QWidget()
            htop = QtGui.QHBoxLayout(container_top)
            htop.setContentsMargins(5, 2, 5, 2)
            htop.setSpacing(6)

            htop.addWidget(QtGui.QLabel("Rotate:"))

            self.bulk_angle_combo = QtGui.QComboBox()
            self.bulk_angle_combo.addItems(["90°", "180°"])
            self.bulk_angle_combo.setCurrentIndex(1)
            self.bulk_angle_combo.setFixedWidth(70)
            htop.addWidget(self.bulk_angle_combo)

            self.bulk_axis_combo = QtGui.QComboBox()
            self.bulk_axis_combo.addItems(["X", "Y"])
            self.bulk_axis_combo.setCurrentIndex(0)
            self.bulk_axis_combo.setFixedWidth(55)
            htop.addWidget(self.bulk_axis_combo)

            self.bulk_rotate_btn = QtGui.QPushButton("Rotate")
            self.bulk_rotate_btn.setFixedWidth(80)
            self.bulk_rotate_btn.clicked.connect(self.apply_bulk_rotate)
            htop.addWidget(self.bulk_rotate_btn)

            self.clear_all_btn = QtGui.QPushButton("Clear All")
            self.clear_all_btn.setToolTip("Uncheck all selection checkboxes in the table")
            self.clear_all_btn.clicked.connect(self.clear_all_checks)
            htop.addWidget(self.clear_all_btn)

            htop.addStretch()

            self.table.setCellWidget(top_idx, 0, container_top)
            self.table.setSpan(top_idx, 0, 1, self.table.columnCount())
            control_item = QtGui.QTableWidgetItem("")
            control_item.setFlags(QtCore.Qt.NoItemFlags)
            self.table.setItem(top_idx, 0, control_item)

            # --- Bottom control row: Change grain direction ---
            for c in range(self.table.columnCount()):
                itm = self.table.item(bottom_idx, c)
                if itm:
                    self.table.setItem(bottom_idx, c, None)
                w = self.table.cellWidget(bottom_idx, c)
                if w is not None:
                    w.setParent(None)

            container_bot = QtGui.QWidget()
            hbot = QtGui.QHBoxLayout(container_bot)
            hbot.setContentsMargins(5, 2, 5, 2)
            hbot.setSpacing(6)

            hbot.addWidget(QtGui.QLabel("Change grain direction:"))

            self.bulk_grain_combo = QtGui.QComboBox()
            self.bulk_grain_combo.addItems(["X", "Y"])
            self.bulk_grain_combo.setCurrentIndex(0)
            self.bulk_grain_combo.setFixedWidth(55)
            hbot.addWidget(self.bulk_grain_combo)

            # Connect bulk combobox changes to apply immediately (but only for checked rows)
            try:
                self.bulk_grain_combo.currentIndexChanged.connect(self._on_bulk_grain_changed)
            except Exception:
                pass

            self.bulk_grain_apply_btn = QtGui.QPushButton("Apply Grain")
            self.bulk_grain_apply_btn.setFixedWidth(100)
            self.bulk_grain_apply_btn.clicked.connect(self.apply_change_grain)
            hbot.addWidget(self.bulk_grain_apply_btn)

            hbot.addStretch()

            self.table.setCellWidget(bottom_idx, 0, container_bot)
            self.table.setSpan(bottom_idx, 0, 1, self.table.columnCount())
            control_item2 = QtGui.QTableWidgetItem("")
            control_item2.setFlags(QtCore.Qt.NoItemFlags)
            self.table.setItem(bottom_idx, 0, control_item2)

        except Exception:
            App.Console.PrintError("Failed to create control rows:\n" + traceback.format_exc())

    def _on_bulk_grain_changed(self, index):
        """When bulk grain combobox changes - delegates to grain controller."""
        self._grain._on_bulk_grain_changed(index)

    def align_to_largest_face(self, obj):
        """Compute rotation to align object - delegates to preview manager."""
        return self._preview.align_to_largest_face(obj)

    def ensure_preview_doc(self, reset_counters_if_new=True):
        """Ensure preview document exists - delegates to preview manager."""
        return self._preview.ensure_preview_doc(reset_counters_if_new)

    def delete_preview_objects(self, names):
        """Delete preview objects - delegates to preview manager."""
        return self._preview.delete_preview_objects(names)

    def add_selected_objects(self):
        selection = Gui.Selection.getSelection()
        if not selection:
            App.Console.PrintMessage("No selection to add.\n")
            return

        p_doc = self.ensure_preview_doc()

        current_x = 0.0
        current_y = 0.0
        max_row_height = 0.0
        padding = 50.0

        # suppress qty-change reactions while we programmatically fill table
        self._suppress_qty_update = True
        try:
            for target_obj in selection:
                try:
                    target = target_obj
                    if hasattr(target_obj, "Parent") and target_obj.Parent and target_obj.Parent.isDerivedFrom("PartDesign::Body"):
                        target = target_obj.Parent
                    elif hasattr(target_obj, "InList"):
                        for p in target_obj.InList:
                            if p.isDerivedFrom("PartDesign::Body"):
                                target = p
                                break

                    new_obj = p_doc.copyObject(target, False)
                    new_obj.Label = target.Label

                    alignment_rot = self.align_to_largest_face(new_obj)
                    new_obj.Placement = App.Placement(App.Vector(0, 0, 0), alignment_rot)

                    p_doc.recompute()

                    bbox = new_obj.Shape.BoundBox
                    part_w = bbox.XMax - bbox.XMin
                    part_h = bbox.YMax - bbox.YMin

                    if self.added_count > 0 and (self.added_count % self.grid_cols == 0):
                        current_x = 0.0
                        current_y += max_row_height + padding
                        max_row_height = 0.0

                    offset_x = -bbox.XMin
                    offset_y = -bbox.YMin
                    offset_z = -bbox.ZMin

                    final_pos = App.Vector(current_x + offset_x, current_y + offset_y, offset_z)
                    new_obj.Placement.Base = final_pos

                    current_x += part_w + padding
                    if part_h > max_row_height:
                        max_row_height = part_h

                    self.added_count += 1

                    insert_pos = max(0, self.table.rowCount() - self.control_rows)
                    self.table.insertRow(insert_pos)

                    # Column 0: Body name (keep first column width)
                    name_item = QtGui.QTableWidgetItem(new_obj.Label)
                    # Keep compatibility: primary UserRole holds primary preview object name (string)
                    name_item.setData(QtCore.Qt.UserRole, new_obj.Name)
                    # Store full list of preview object names in UserRole+1 as JSON string
                    try:
                        name_item.setData(QtCore.Qt.UserRole + 1, json.dumps([new_obj.Name]))
                    except Exception:
                        # fallback: store Python list (PySide may allow)
                        name_item.setData(QtCore.Qt.UserRole + 1, [new_obj.Name])
                    self.table.setItem(insert_pos, 0, name_item)
                    # Column 1: Qty (defaults to 1)
                    qty_item = QtGui.QTableWidgetItem("1")
                    qty_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.table.setItem(insert_pos, 1, qty_item)
                    # Column 2: Rotation degree defaults (centered)
                    rot_item = QtGui.QTableWidgetItem("0,90,180,270")
                    rot_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.table.setItem(insert_pos, 2, rot_item)
                    # Column 3: Select for rotation (checkbox) -- center the checkbox
                    container_sel = QtGui.QWidget()
                    cell_layout_sel = QtGui.QHBoxLayout(container_sel)
                    cell_layout_sel.setContentsMargins(3, 0, 3, 0)  # small left/right margins
                    cell_layout_sel.setSpacing(0)
                    # center: add stretch both sides
                    cell_layout_sel.addStretch()
                    checkbox = QtGui.QCheckBox()
                    checkbox.setToolTip("Select this part for bulk Rotate")
                    cell_layout_sel.addWidget(checkbox)
                    cell_layout_sel.addStretch()
                    self.table.setCellWidget(insert_pos, 3, container_sel)
                    # Column 4: Grain Direction: checkbox + combobox (X/Y), center both
                    container_grain = QtGui.QWidget()
                    grain_layout = QtGui.QHBoxLayout(container_grain)
                    grain_layout.setContentsMargins(0, 0, 0, 0)
                    grain_layout.setSpacing(4)
                    grain_layout.addStretch()
                    grain_cb = QtGui.QCheckBox()
                    grain_cb.setToolTip("Enable custom grain direction for this part")
                    grain_layout.addWidget(grain_cb)
                    grain_combo = QtGui.QComboBox()
                    grain_combo.addItems(["X", "Y"])
                    grain_combo.setCurrentIndex(0)
                    grain_combo.setFixedWidth(50)
                    grain_layout.addWidget(grain_combo)
                    grain_layout.addStretch()
                    self.table.setCellWidget(insert_pos, 4, container_grain)

                    # connect per-row grain widgets so checking /  axis-change draws arrow
                    try:
                        # use preview object name stored in new_obj.Name
                        self._connect_grain_widgets(grain_cb, grain_combo, new_obj.Name)
                    except Exception:
                        App.Console.PrintError("Failed to connect grain widget signals for '%s':\n" % (new_obj.Name,) + traceback.format_exc())

                    # ensure column width remains (in case header auto-resize changed it)
                    try:
                        self.table.setColumnWidth(0, 250)
                    except Exception:
                        pass

                except Exception:
                    App.Console.PrintError("Failed to add object to preview:\n" + traceback.format_exc())

        finally:
            self._suppress_qty_update = False

        try:
            p_doc.recompute()
            Gui.setActiveDocument(p_doc)
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            App.Console.PrintError("Error during final recompute/view update:\n" + traceback.format_exc())
            
        # TRIGGER LAYOUT UPDATE
        self.update_grain_layout_and_perimeters()

        # update Apply Grain blink state (in case added rows have checked boxes programmatically)
        try:
            self._update_apply_blink_state()
        except Exception:
            pass
            
    def update_grain_layout_and_perimeters(self):
        """Update grain layout and perimeters - delegates to grain controller."""
        self._grain.update_grain_layout_and_perimeters()


    def select_preview_objects_for_row(self, row):
        """Select preview objects for row - delegates to preview manager."""
        self._preview.select_preview_objects_for_row(row)

    def on_cell_clicked(self, row, col):
        # ignore clicks on control rows
        if row >= self.table.rowCount() - self.control_rows:
            return

        try:
            # select all preview objects associated with this row
            self.select_preview_objects_for_row(row)
        except Exception:
            App.Console.PrintError("on_cell_clicked failed:\n" + traceback.format_exc())

    def on_item_changed(self, item):
        """Handle edits to table items. We use this to react to Qty column changes (column 1)."""
        
        mgr = NestingRelayoutManager(preview_doc_name=self.preview_doc_name, grid_cols=self.grid_cols, padding=50.0)
        
        try:
            if getattr(self, "_suppress_qty_update", False):
                return
            if not item:
                return
            col = item.column()
            row = item.row()
            # ignore changes in control rows
            if row >= self.table.rowCount() - self.control_rows:
                return
            if col != 1:
                return

            # Validate and clamp qty
            txt = item.text().strip()
            try:
                val = int(txt)
            except Exception:
                val = 0
            if val < 0:
                val = 0
            if val > 5000:
                val = 5000

            # If clamped/changed, update cell without re-entering handler
            if str(val) != txt:
                try:
                    self._suppress_qty_update = True
                    item.setText(str(val))
                finally:
                    self._suppress_qty_update = False

            # Now ensure preview doc has exactly val copies for this row
            p_doc = App.getDocument(self.preview_doc_name) if self.preview_doc_name in App.listDocuments() else None
            if not p_doc:
                return

            name_item = self.table.item(row, 0)
            if not name_item:
                return

            # primary name stored at UserRole (string) for compatibility
            primary_name = name_item.data(QtCore.Qt.UserRole)
            list_data = name_item.data(QtCore.Qt.UserRole + 1)

            obj_names = []
            if list_data:
                try:
                    if isinstance(list_data, list):
                        obj_names = list(list_data)
                    else:
                        obj_names = json.loads(list_data)
                except Exception:
                    # fallback: use primary if present
                    if primary_name:
                        obj_names = [primary_name]
            else:
                if primary_name:
                    obj_names = [primary_name]

            current_count = len(obj_names)
            desired = val

            if desired == current_count:
                # after Qty edit, keep selection consistent: select all copies for row
                try:
                    self.select_preview_objects_for_row(row)
                except Exception:
                    pass
                return

            # Case (a): Desired <= 0 -> delete all preview objects for this row and remove the table row
            if desired <= 0:
                try:
                    # delete all preview objects associated with this row
                    names_to_delete = list(obj_names) if obj_names else []
                    removed = self.delete_preview_objects(names_to_delete)

                    # Remove widgets in both select and grain columns
                    try:
                        w = self.table.cellWidget(row, 3)
                        if w is not None:
                            w.setParent(None)
                    except Exception:
                        pass
                    try:
                        w2 = self.table.cellWidget(row, 4)
                        if w2 is not None:
                            w2.setParent(None)
                    except Exception:
                        pass

                    # remove the table row
                    self.table.removeRow(row)
                    # adjust added_count already performed in delete_preview_objects
                    try:
                        if self.added_count < 0:
                            self.added_count = 0
                    except Exception:
                        pass

                except Exception:
                    App.Console.PrintError("Failed to remove row when Qty set to 0:\n" + traceback.format_exc())
                finally:
                    try:
                        self._update_apply_blink_state()
                    except Exception:
                        pass
                return
                
            # If need to increase quantity: copy base object
            if desired > current_count:
                base_name = obj_names[0] if obj_names else primary_name
                base_obj = p_doc.getObject(base_name) if base_name else None
                if not base_obj:
                    App.Console.PrintMessage("No base object available to copy for row %d\n" % (row,))
                    # update stored names to current state (maybe empty) and exit
                    try:
                        name_item.setData(QtCore.Qt.UserRole + 1, json.dumps(obj_names))
                    except Exception:
                        name_item.setData(QtCore.Qt.UserRole + 1, obj_names)
                    return

                created_names = []
                for i in range(desired - current_count):
                    try:
                        new_obj = p_doc.copyObject(base_obj, False)
                        try:
                            new_obj.Label = base_obj.Label
                        except Exception:
                            pass
                        created_names.append(new_obj.Name)
                        obj_names.append(new_obj.Name)
                    except Exception:
                        App.Console.PrintError("Failed to create copy in Qty change:\n" + traceback.format_exc())
                        break
                try:
                    p_doc.recompute()
                    mgr.run(copy_selection=True)
                    mgr.run(copy_selection=True)
                except Exception:
                    App.Console.PrintError("Recompute failed after adding copies:\n" + traceback.format_exc())

                # update stored list
                try:
                    name_item.setData(QtCore.Qt.UserRole + 1, json.dumps(obj_names))
                except Exception:
                    name_item.setData(QtCore.Qt.UserRole + 1, obj_names)
                # ensure primary remains the first
                if obj_names:
                    try:
                        name_item.setData(QtCore.Qt.UserRole, obj_names[0])
                    except Exception:
                        pass

                # If grain checkbox for this row is checked, draw arrows for all newly created copies
                try:
                    grain_widget = self.table.cellWidget(row, 4)
                    if grain_widget:
                        cb = grain_widget.findChild(QtGui.QCheckBox)
                        combo = grain_widget.findChild(QtGui.QComboBox)
                        axis = combo.currentText() if combo and hasattr(combo, "currentText") else "X"
                        if cb and cb.isChecked() and created_names:
                            for n in created_names:
                                try:
                                    if GrainPreparer is not None:
                                        GrainPreparer.update_grain_arrow(self.preview_doc_name, n, enable=True, axis=axis)
                                except Exception:
                                    App.Console.PrintError("Failed to draw arrow for new copy '%s':\n" % (str(n),) + traceback.format_exc())
                except Exception:
                    App.Console.PrintError("Failed to update arrows after qty increase:\n" + traceback.format_exc())
                
                # TRIGGER LAYOUT UPDATE
                self.update_grain_layout_and_perimeters()

                # after adding copies, update blink state (in case checkboxes present)
                try:
                    self._update_apply_blink_state()
                except Exception:
                    pass

            # Case (b): decrease but desired >= 1 -> remove highest-numbered copies first when numeric 3-digit suffixes present
            else:
                to_remove = current_count - desired
                try:
                    removed_list = []
                    # If original had >1, try to remove suffixed copies with 3-digit numeric endings
                    suffix_matches = []
                    non_suffix = []
                    # Prefer to detect a base prefix. If primary_name is present and other names start with primary_name, use that.
                    prefix = primary_name or ""
                    for n in obj_names:
                        if prefix and n == prefix:
                            # base object, treat as number 0
                            continue
                        m = re.match(r'^(.*?)(\d{3})$', n)
                        if m:
                            # capture numeric suffix
                            try:
                                num = int(m.group(2))
                            except Exception:
                                num = None
                            suffix_matches.append((n, num))
                        else:
                            non_suffix.append(n)

                    # sort suffix matches by numeric desc (largest first)
                    suffix_matches_sorted = sorted([s for s in suffix_matches if s[1] is not None], key=lambda x: x[1], reverse=True)

                    # remove from largest numeric suffixes first
                    for (name_to_remove, num) in suffix_matches_sorted:
                        if to_remove <= 0:
                            break
                        try:
                            obj = p_doc.getObject(name_to_remove)
                            if obj:
                                # remove possible grain arrow tied to this preview object before removal
                                try:
                                    if GrainPreparer is not None:
                                        GrainPreparer.remove_grain_arrow(self.preview_doc_name, name_to_remove)
                                except Exception:
                                    pass
                                p_doc.removeObject(name_to_remove)
                                removed_list.append(name_to_remove)
                                to_remove -= 1
                            else:
                                # maybe name exists but getObject returned None (rare) - skip
                                to_remove -= 1
                                removed_list.append(name_to_remove)
                        except Exception:
                            App.Console.PrintError("Failed to remove object '%s' during selective decrease:\n%s\n" % (name_to_remove, traceback.format_exc()))
                            continue

                    # if still need to remove more, remove from the end of obj_names (avoid removing primary if possible)
                    if to_remove > 0:
                        remaining = [n for n in obj_names if n not in removed_list]
                        # try to avoid removing primary_name; remove other instances first
                        remaining_sorted = list(reversed(remaining))
                        for name_to_remove in remaining_sorted:
                            if to_remove <= 0:
                                break
                            if name_to_remove == primary_name:
                                # skip primary if others available
                                others = [r for r in remaining_sorted if r != primary_name]
                                if others:
                                    continue
                            try:
                                obj = p_doc.getObject(name_to_remove)
                                if obj:
                                    try:
                                        if GrainPreparer is not None:
                                            GrainPreparer.remove_grain_arrow(self.preview_doc_name, name_to_remove)
                                    except Exception:
                                        pass
                                    p_doc.removeObject(name_to_remove)
                                removed_list.append(name_to_remove)
                                to_remove -= 1
                            except Exception:
                                App.Console.PrintError("Failed to remove object '%s' in fallback removal:\n%s\n" % (name_to_remove, traceback.format_exc()))
                                continue

                    # update obj_names by removing removed_list entries preserving order
                    obj_names = [n for n in obj_names if n not in removed_list]

                    try:
                        p_doc.recompute()
                        mgr.run(copy_selection=True)
                        mgr.run(copy_selection=True)
                    except Exception:
                        App.Console.PrintError("Recompute failed after selective removals:\n" + traceback.format_exc())

                except Exception:
                    App.Console.PrintError("Error while removing copies during Qty decrease:\n" + traceback.format_exc())

                # TRIGGER LAYOUT UPDATE
                self.update_grain_layout_and_perimeters()

                # after removals, update blink state
                try:
                    self._update_apply_blink_state()
                except Exception:
                    pass

            # Update stored list and primary UserRole (keep primary as first or blank)
            try:
                name_item.setData(QtCore.Qt.UserRole + 1, json.dumps(obj_names))
            except Exception:
                name_item.setData(QtCore.Qt.UserRole + 1, obj_names)

            if obj_names:
                try:
                    name_item.setData(QtCore.Qt.UserRole, obj_names[0])
                except Exception:
                    pass
            else:
                try:
                    name_item.setData(QtCore.Qt.UserRole, "")
                except Exception:
                    pass

            # After qty change, select all preview objects for the row to keep UI consistent
            try:
                self.select_preview_objects_for_row(row)
            except Exception:
                pass

            # Keep view focused on preview doc
            try:
                Gui.setActiveDocument(p_doc)
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass

            # update grain perimeter after qty change
            try:
                if GrainPreparer is not None:
                    try:
                        self.update_grain_layout_and_perimeters()
                    except Exception:
                        App.Console.PrintError("Failed to update grain layout/perimeters after apply_change_grain:\n" + traceback.format_exc())
            except Exception:
                App.Console.PrintError("Failed to draw grain perimeter after qty change:\n" + traceback.format_exc())

        except Exception:
            App.Console.PrintError("on_item_changed failed:\n" + traceback.format_exc())

    # Delegation wrappers to NestingRotator
    def apply_bulk_rotate(self):
        """Read angle and axis from control row and delegate to rotator."""
        try:
            angle_text = self.bulk_angle_combo.currentText()
            try:
                angle = int(angle_text.replace("°", "").strip())
            except Exception:
                angle = 180
            axis = self.bulk_axis_combo.currentText() if hasattr(self, "bulk_axis_combo") else "X"
            p_doc = App.getDocument(self.preview_doc_name) if self.preview_doc_name in App.listDocuments() else None
            if self._rotator is None:
                App.Console.PrintMessage("Rotation module not available.\n")
                return
            # pass sheet grain and algorithm if needed later (algorithm available in UI)
            self._rotator.apply_bulk_rotate(self.table, p_doc, angle, axis_char=axis)

            # update grain perimeter after bulk rotate
            try:
                if GrainPreparer is not None:
                    try:
                        self.update_grain_layout_and_perimeters()
                    except Exception:
                        App.Console.PrintError("Failed to update grain layout/perimeters after apply_change_grain:\n" + traceback.format_exc())
            except Exception:
                App.Console.PrintError("Failed to draw grain perimeter after apply_bulk_rotate:\n" + traceback.format_exc())
            
            # TRIGGER LAYOUT UPDATE
            self.update_grain_layout_and_perimeters()
            
        except Exception:
            App.Console.PrintError("apply_bulk_rotate wrapper failed:\n" + traceback.format_exc())

    def apply_change_grain(self):
        """Apply selected grain direction (bulk_grain_combo) to all rows where 'Select for rotation' checkbox is checked."""
        try:
            axis = self.bulk_grain_combo.currentText() if hasattr(self, "bulk_grain_combo") else "X"
            changed = 0
            data_rows = self.table.rowCount() - self.control_rows
            for r in range(data_rows):
                try:
                    widget = self.table.cellWidget(r, 3)
                    if not widget:
                        continue
                    # find selection checkbox
                    sel_cb = None
                    try:
                        lay = widget.layout()
                        if lay and lay.count() > 0:
                            candidate = lay.itemAt(0).widget()
                            if isinstance(candidate, QtGui.QCheckBox):
                                sel_cb = candidate
                    except Exception:
                        try:
                            sel_cb = widget.findChild(QtGui.QCheckBox)
                        except Exception:
                            sel_cb = None
                    if not sel_cb or not sel_cb.isChecked():
                        continue
                    # set per-row grain combobox (column 4)
                    grain_widget = self.table.cellWidget(r, 4)
                    if not grain_widget:
                        continue
                    try:
                        g_lay = grain_widget.layout()
                        # combobox expected (we placed it as second widget)
                        if g_lay and g_lay.count() > 1:
                            # Our layout has stretch , checkbox, combobox, stretch => combobox likely at index 2
                            # Use findChild fallback to be robust
                            cb_widget = grain_widget.findChild(QtGui.QComboBox)
                            if isinstance(cb_widget, QtGui.QComboBox):
                                idx = 0 if axis.upper() == "X" else 1
                                cb_widget.setCurrentIndex(idx)
                                changed += 1
                                continue
                    except Exception:
                        pass
                    # fallback: findChild
                    try:
                        cb_widget = grain_widget.findChild(QtGui.QComboBox)
                        if cb_widget:
                            idx = 0 if axis.upper() == "X" else 1
                            cb_widget.setCurrentIndex(idx)
                            changed += 1
                    except Exception:
                        pass
                except Exception:
                    App.Console.PrintError("apply_change_grain per-row error:\n" + traceback.format_exc())
            App.Console.PrintMessage("apply_change_grain: applied grain '%s' to %d rows.\n" % (axis, changed))

            # update grain perimeter after grain change
            try:
                if GrainPreparer is not None:
                    try:
                        self.update_grain_layout_and_perimeters()
                    except Exception:
                        App.Console.PrintError("Failed to update grain layout/perimeters after apply_change_grain:\n" + traceback.format_exc())
            except Exception:
                App.Console.PrintError("Failed to draw grain perimeter after apply_change_grain:\n" + traceback.format_exc())

            # Ensure GrainArrow objects match new per-row states (draw or remove arrows)
            try:
                if GrainPreparer is not None:
                    data_rows = self.table.rowCount() - self.control_rows
                    for r in range(data_rows):
                        try:
                            name_item = self.table.item(r, 0)
                            if not name_item:
                                continue
                            primary = name_item.data(QtCore.Qt.UserRole)
                            try:
                                list_data = name_item.data(QtCore.Qt.UserRole + 1)
                                if list_data:
                                    if isinstance(list_data, list):
                                        names_list = list_data
                                    else:
                                        names_list = json.loads(list_data)
                                    if names_list:
                                        primary = names_list[0]
                            except Exception:
                                pass
                            obj_name = primary
                            grain_widget = self.table.cellWidget(r, 4)
                            if not grain_widget:
                                continue
                            cb = grain_widget.findChild(QtGui.QCheckBox)
                            combo = grain_widget.findChild(QtGui.QComboBox)
                            if cb and cb.isChecked() and combo:
                                GrainPreparer.update_grain_arrow(self.preview_doc_name, obj_name, enable=True, axis=combo.currentText())
                            else:
                                GrainPreparer.remove_grain_arrow(self.preview_doc_name, obj_name)
                        except Exception:
                            pass
            except Exception:
                App.Console.PrintError("apply_change_grain: failed to update arrows:\n" + traceback.format_exc())
            
            # TRIGGER LAYOUT UPDATE
            self.update_grain_layout_and_perimeters()

            # update blinking state (checkboxes may be unchanged, but keep consistent)
            try:
                self._update_apply_blink_state()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError("apply_change_grain failed:\n" + traceback.format_exc())

    def clear_all_checks(self):
        """Uncheck every per-row 'Select for rotation' checkbox (column 3) - does not modify grain states."""
        try:
            data_rows = self.table.rowCount() - self.control_rows
            for r in range(data_rows):
                try:
                    widget = self.table.cellWidget(r, 3)
                    if not widget:
                        continue
                    try:
                        lay = widget.layout()
                        if lay and lay.count() > 0:
                            candidate = lay.itemAt(0).widget()
                            if isinstance(candidate, QtGui.QCheckBox):
                                candidate.setChecked(False)
                                continue
                    except Exception:
                        pass
                    try:
                        cb = widget.findChild(QtGui.QCheckBox)
                        if cb:
                            cb.setChecked(False)
                    except Exception:
                        pass
                except RuntimeError:
                    continue
                except Exception:
                    App.Console.PrintError("clear_all_checks per-row error:\n" + traceback.format_exc())
            App.Console.PrintMessage("clear_all_checks: all 'Select for rotation' checkboxes cleared.\n")
            # update blinking state
            try:
                self._update_apply_blink_state()
            except Exception:
                pass
        except Exception:
            App.Console.PrintError("clear_all_checks failed:\n" + traceback.format_exc())
            
    def remove_selected_rows(self):
        """Remove selected table rows and delete their preview objects.
           Behavior matches entering Qty = 0 for the selected rows.
        """
        indices = self.table.selectionModel().selectedRows()
        if not indices:
            App.Console.PrintMessage("No rows selected for removal.\n")
            return

        control_start = self.table.rowCount() - self.control_rows
        rows = sorted([index.row() for index in indices if index.row() < control_start], reverse=True)
        if not rows:
            App.Console.PrintMessage("No data rows selected for removal.\n")
            return

        p_doc = App.getDocument(self.preview_doc_name) if self.preview_doc_name in App.listDocuments() else None
        for r in rows:
            try:
                item = self.table.item(r, 0)
                if item:
                    # collect preview object names associated with this row (UserRole+1 or primary)
                    names_json = item.data(QtCore.Qt.UserRole + 1)
                    names = []
                    if names_json:
                        try:
                            if isinstance(names_json, list):
                                names = list(names_json)
                            else:
                                names = json.loads(names_json)
                        except Exception:
                            primary = item.data(QtCore.Qt.UserRole)
                            if primary:
                                names = [primary]
                    else:
                        primary = item.data(QtCore.Qt.UserRole)
                        if primary:
                            names = [primary]

                    # delete preview objects using the shared deletion helper
                    try:
                        removed = self.delete_preview_objects(names)
                    except Exception:
                        App.Console.PrintError("remove_selected_rows: delete_preview_objects failed for row %d:\n%s\n" % (r, traceback.format_exc()))
                        removed = []

                # Remove widgets in both select and grain columns
                try:
                    w = self.table.cellWidget(r, 3)
                    if w is not None:
                        w.setParent(None)
                except Exception:
                    pass
                try:
                    w2 = self.table.cellWidget(r, 4)
                    if w2 is not None:
                        w2.setParent(None)
                except Exception:
                    pass

                # remove table row
                self.table.removeRow(r)
                # added_count already adjusted by delete_preview_objects (if any removed)
                if self.added_count < 0:
                    self.added_count = 0

            except Exception:
                App.Console.PrintError("Error removing row %d:\n%s\n" % (r, traceback.format_exc()))

        try:
            if p_doc:
                p_doc.recompute()
        except Exception:
            App.Console.PrintError("Error recomputing preview doc after removals:\n" + traceback.format_exc())

        # update grain perimeter after row removals
        try:
            if GrainPreparer is not None:
                try:
                    self.update_grain_layout_and_perimeters()
                except Exception:
                    App.Console.PrintError("Failed to update grain layout/perimeters after apply_change_grain:\n" + traceback.format_exc())
        except Exception:
            App.Console.PrintError("Failed to draw grain perimeter after remove_selected_rows:\n" + traceback.format_exc())
        
        # TRIGGER LAYOUT UPDATE
        self.update_grain_layout_and_perimeters()

        # update Apply Grain blink state
        try:
            self._update_apply_blink_state()
        except Exception:
            pass

    def execute_nesting(self):
        """Execute nesting - delegates to IPNestingExport module."""
        execute_nesting_impl(self)

    def getStandardButtons(self):
        return int(QtGui.QDialogButtonBox.Cancel)