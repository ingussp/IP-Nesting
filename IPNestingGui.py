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
import tempfile
import Part
from IPNestingRelayout import NestingRelayoutManager
from functools import partial
from IPNestingExport import execute_nesting as execute_nesting_impl
from IPNestingGrainUI import GrainUIController
from IPNestingPreviewDoc import PreviewDocManager
from IPNestingGrainAngleDialog import GrainAngleDialog
from IPNestingImport2D import import_dxf_to_preview, import_svg_to_preview
from IPNestingOffcuts import extract_offcut_from_dxf
from IPNestingOffcutShowDialog import OffcutShowDialog


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

        # NEW: offcuts model
        self.offcuts = []
        self._offcut_next_id = 1

        self.form = QtGui.QWidget()
        self.layout = QtGui.QVBoxLayout(self.form)

        # NEW: two-column configuration area
        cfg = QtGui.QWidget()
        cfg_grid = QtGui.QGridLayout(cfg)
        cfg_grid.setContentsMargins(0, 0, 0, 0)
        cfg_grid.setHorizontalSpacing(12)
        cfg_grid.setVerticalSpacing(8)

        self.layout.addWidget(cfg)

        # -------------------------
        # Two-column configuration
        # -------------------------

        # Sheet Settings (LEFT, row 0)
        sheet_box = QtGui.QGroupBox("Sheet Settings")
        sheet_lay = QtGui.QVBoxLayout(sheet_box)

        self.sheet_w = self.create_input_in_layout(sheet_lay, "Sheet Width (mm):", "2500.00", "Total width.")
        self.sheet_h = self.create_input_in_layout(sheet_lay, "Sheet Height (mm):", "1250.00", "Total height.")
        self.sheet_margin = self.create_input_in_layout(sheet_lay, "Sheet Margin (mm):", "5.00", "Edge margin.")

        row = QtGui.QHBoxLayout()
        row.addWidget(QtGui.QLabel("Sheet grain direction:"))
        self.sheet_grain_combo = QtGui.QComboBox()
        self.sheet_grain_combo.addItems(["Width", "Height", "None"])
        self.sheet_grain_combo.setCurrentIndex(2)
        self.sheet_grain_combo.setToolTip("Sheet grain direction: Width/Height/None")
        row.addWidget(self.sheet_grain_combo)
        sheet_lay.addLayout(row)

        # NEW: Offcuts (DXF) (LEFT, row 1)
        offcut_box = QtGui.QGroupBox("Offcuts (DXF)")
        offcut_lay = QtGui.QVBoxLayout(offcut_box)

        self.offcuts_table = QtGui.QTableWidget(0, 2)
        self.offcuts_table.setHorizontalHeaderLabels(["Offcut", ""])
        self.offcuts_table.setToolTip("DXF offcuts: one DXF = one offcut sheet (largest closed contour).")
        self.offcuts_table.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.offcuts_table.setMinimumHeight(120)

        # Column sizing
        try:
            header = self.offcuts_table.horizontalHeader()
            if hasattr(header, "setSectionResizeMode"):
                header.setSectionResizeMode(0, QtGui.QHeaderView.Stretch)
                header.setSectionResizeMode(1, QtGui.QHeaderView.Fixed)
            else:
                header.setResizeMode(0, QtGui.QHeaderView.Stretch)
                header.setResizeMode(1, QtGui.QHeaderView.Fixed)
        except Exception:
            pass
        try:
            self.offcuts_table.setColumnWidth(1, 60)
        except Exception:
            pass

        offcut_lay.addWidget(self.offcuts_table)

        off_btns = QtGui.QHBoxLayout()
        self.offcut_add_btn = QtGui.QPushButton("Add")
        self.offcut_show_btn = QtGui.QPushButton("Show")
        self.offcut_remove_btn = QtGui.QPushButton("Remove")
        self.offcut_add_btn.setToolTip("Add a DXF offcut (largest closed contour will be used).")
        self.offcut_show_btn.setToolTip("Show all added offcuts and adjust grain X/Y per offcut.")
        self.offcut_remove_btn.setToolTip("Remove checked offcuts from the list.")
        self.offcut_add_btn.clicked.connect(self.add_offcut_dxf)
        self.offcut_show_btn.clicked.connect(self.show_offcuts_popup)
        self.offcut_remove_btn.clicked.connect(self.remove_offcuts)
        off_btns.addWidget(self.offcut_add_btn)
        off_btns.addWidget(self.offcut_show_btn)
        off_btns.addWidget(self.offcut_remove_btn)
        off_btns.addStretch()
        offcut_lay.addLayout(off_btns)

        # General Parameters (LEFT, row 2)  (shifted down by 1)
        general_box = QtGui.QGroupBox("General Parameters")
        general_lay = QtGui.QVBoxLayout(general_box)
        self.spacing = self.create_input_in_layout(general_lay, "Part Spacing (mm):", "2.00", "Part gap.")
        self.res = self.create_input_in_layout(general_lay, "Boundary Resolution:", "0.1", "Curve resolution.")

        # Nesting Strategy (RIGHT, row 0)
        strategy_box = QtGui.QGroupBox("Nesting Strategy")
        strategy_lay = QtGui.QVBoxLayout(strategy_box)
        self.select_strategy = QtGui.QComboBox()
        self.select_strategy.addItems(["Largest Area First", "Smallest Area First", "None"])
        strategy_lay.addWidget(self.select_strategy)

        # Nesting Algorithm (RIGHT, row 1)
        algo_box = QtGui.QGroupBox("Nesting Algorithm")
        algo_lay = QtGui.QVBoxLayout(algo_box)
        self.nesting_algorithm = QtGui.QComboBox()
        self.nesting_algorithm.addItems([
            "Bottom-Left",
            "Guillotine",
            "Extreme Points",
            "Genetic",
            "No-Fit-Polygon",
            "None"
        ])
        self.nesting_algorithm.setCurrentIndex(0)
        algo_lay.addWidget(self.nesting_algorithm)

        # Optimization (RIGHT, row 2)
        opt_box = QtGui.QGroupBox("Optimization")
        opt_lay = QtGui.QVBoxLayout(opt_box)
        self.gen = self.create_input_in_layout(opt_lay, "Generations:", "5", "Iterations.")
        self.pop = self.create_input_in_layout(opt_lay, "Population Size:", "20", "Solutions.")

        # Place boxes in 2-column grid
        cfg_grid.addWidget(sheet_box,   0, 0)
        cfg_grid.addWidget(offcut_box,  1, 0)     # NEW
        cfg_grid.addWidget(general_box, 2, 0)     # shifted

        cfg_grid.addWidget(strategy_box, 0, 1)
        cfg_grid.addWidget(algo_box,     1, 1)
        cfg_grid.addWidget(opt_box,      2, 1)

        # Make columns expand nicely
        try:
            cfg_grid.setColumnStretch(0, 1)
            cfg_grid.setColumnStretch(1, 1)
        except Exception:
            pass

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

        # Import 2D buttons (DXF/SVG)
        self.import_dxf_btn = QtGui.QPushButton("Import DXF…")
        self.import_svg_btn = QtGui.QPushButton("Import SVG…")
        self.import_dxf_btn.setFixedHeight(32)
        self.import_svg_btn.setFixedHeight(32)
        self.import_dxf_btn.setFixedWidth(140)
        self.import_svg_btn.setFixedWidth(140)

        self.import_dxf_btn.clicked.connect(self.import_dxf_2d)
        self.import_svg_btn.clicked.connect(self.import_svg_2d)

        self.btn_layout.addWidget(self.import_dxf_btn)
        self.btn_layout.addWidget(self.import_svg_btn)
        self.btn_layout.addWidget(self.add_btn)
        self.btn_layout.addWidget(self.rem_btn)
        self.layout.addLayout(self.btn_layout)

        # Run button
        self.run_btn = QtGui.QPushButton("Run Nesting")
        self.run_btn.setStyleSheet("background-color: #CF3519; color: white; font-weight: bold; height: 35px;")
        self.run_btn.clicked.connect(self.execute_nesting)
        self.layout.addWidget(self.run_btn)
        self._grain_angle_dialog_open = False

        try:
            self._selection_observer = NestingTaskPanel._SelectionObserver(self)
            Gui.Selection.addObserver(self._selection_observer)
        except Exception:
            App.Console.PrintError("Failed to add selection observer:\n" + traceback.format_exc())

        self._load_settings_from_prefs()
        self._connect_settings_persistence()

    def _is_offcut_duplicate_path(self, path):
        try:
            ap = os.path.abspath(str(path))
            for off in self.offcuts:
                try:
                    if os.path.abspath(str(off.get("path", ""))) == ap:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _add_offcut_row(self, offcut):
        """Add one offcut entry to offcuts_table."""
        try:
            r = self.offcuts_table.rowCount()
            self.offcuts_table.insertRow(r)

            label = offcut.get("label") or os.path.basename(str(offcut.get("path", "")))
            is_dup = self._is_offcut_duplicate_path(offcut.get("path"))

            # Note: _is_offcut_duplicate_path will see itself if called after append.
            # So we compute duplicate status before append in add_offcut_dxf and pass in flag instead.
        except Exception:
            pass

    def add_offcut_dxf(self):
        """Add an offcut DXF. One DXF = one offcut sheet (largest closed contour)."""
        try:
            path, _ = QtGui.QFileDialog.getOpenFileName(
                None, "Add offcut (DXF)", "", "DXF files (*.dxf *.DXF);;All files (*.*)"
            )
            if not path:
                return

            abs_path = os.path.abspath(path)
            is_dup = self._is_offcut_duplicate_path(abs_path)

            poly, bbox = extract_offcut_from_dxf(abs_path, debug=False)
            if not poly:
                QtGui.QMessageBox.warning(None, "Offcuts", "No closed contour found in DXF.\nCannot use as offcut.")
                return

            offcut_id = int(self._offcut_next_id)
            self._offcut_next_id += 1

            label = os.path.basename(abs_path)
            off = {
                "id": offcut_id,
                "path": abs_path,
                "label": label,
                "grain": "X",
                "polygons": [poly],
                "bbox": bbox,
                "duplicate": bool(is_dup),
            }
            # store (duplicates allowed)
            self.offcuts.append(off)

            # UI row
            r = self.offcuts_table.rowCount()
            self.offcuts_table.insertRow(r)

            txt = label + (" (duplicate)" if is_dup else "")
            name_item = QtGui.QTableWidgetItem(txt)
            name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
            name_item.setData(QtCore.Qt.UserRole, offcut_id)
            if is_dup:
                try:
                    name_item.setForeground(QtGui.QBrush(QtGui.QColor("red")))
                except Exception:
                    pass
            self.offcuts_table.setItem(r, 0, name_item)

            # Checkbox widget for Remove selection
            cb = QtGui.QCheckBox()
            cb.setToolTip("Select for removal")
            w = QtGui.QWidget()
            lay = QtGui.QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addStretch()
            lay.addWidget(cb)
            lay.addStretch()
            self.offcuts_table.setCellWidget(r, 1, w)

        except Exception:
            App.Console.PrintError("add_offcut_dxf failed:\n" + traceback.format_exc())

    def remove_offcuts(self):
        """Remove offcuts that are checked in the offcuts_table (checkbox only used for Remove)."""
        try:
            rows_to_remove = []
            ids_to_remove = []

            for r in range(self.offcuts_table.rowCount()):
                try:
                    w = self.offcuts_table.cellWidget(r, 1)
                    cb = w.findChild(QtGui.QCheckBox) if w else None
                    if cb and cb.isChecked():
                        item = self.offcuts_table.item(r, 0)
                        off_id = item.data(QtCore.Qt.UserRole) if item else None
                        if off_id is not None:
                            ids_to_remove.append(int(off_id))
                        rows_to_remove.append(r)
                except Exception:
                    continue

            if not rows_to_remove:
                return

            # remove from model
            ids_set = set(ids_to_remove)
            self.offcuts = [o for o in self.offcuts if int(o.get("id", -1)) not in ids_set]

            # remove rows from UI bottom-up
            for r in sorted(rows_to_remove, reverse=True):
                try:
                    w = self.offcuts_table.cellWidget(r, 1)
                    if w is not None:
                        w.setParent(None)
                except Exception:
                    pass
                try:
                    self.offcuts_table.removeRow(r)
                except Exception:
                    pass

        except Exception:
            App.Console.PrintError("remove_offcuts failed:\n" + traceback.format_exc())

    def show_offcuts_popup(self):
        """Show popup with ALL offcuts and allow changing grain X/Y per offcut."""
        try:
            if not self.offcuts:
                QtGui.QMessageBox.information(None, "Offcuts", "No offcuts added.")
                return
            parent = QtGui.QApplication.activeWindow()
            dlg = OffcutShowDialog(self.offcuts, parent=parent)
            dlg.exec_()
        except Exception:
            App.Console.PrintError("show_offcuts_popup failed:\n" + traceback.format_exc())
    
    def create_input_in_layout(self, parent_layout, label, default, tooltip):
        row = QtGui.QHBoxLayout()
        edit = QtGui.QLineEdit(default)
        edit.setToolTip(tooltip)
        row.addWidget(QtGui.QLabel(label))
        row.addWidget(edit)
        parent_layout.addLayout(row)
        return edit
    
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
            self.set_angle_btn = QtGui.QPushButton("Set custom angle")
            self.set_angle_btn.setMinimumWidth(160)
            self.set_angle_btn.setToolTip("Set grain angle for selected GrainArrow objects")
            hbot.addWidget(self.bulk_grain_apply_btn)
            hbot.addWidget(self.set_angle_btn)
            
            try:
                self.set_angle_btn.clicked.connect(self._on_set_angle_clicked)
            except Exception:
                pass

            hbot.addStretch()

            self.table.setCellWidget(bottom_idx, 0, container_bot)
            self.table.setSpan(bottom_idx, 0, 1, self.table.columnCount())
            control_item2 = QtGui.QTableWidgetItem("")
            control_item2.setFlags(QtCore.Qt.NoItemFlags)
            self.table.setItem(bottom_idx, 0, control_item2)

        except Exception:
            App.Console.PrintError("Failed to create control rows:\n" + traceback.format_exc())

    def _on_set_angle_clicked(self):
        try:
            arrow_names = self._collect_grain_arrow_names_from_table()
            if not arrow_names:
                QtGui.QMessageBox.information(
                    None,
                    "Set custom angle",
                    "No grain parts selected.\nCheck 'Grain direction' checkbox in the table first."
                )
                return

            self._open_grain_angle_dialog_for_arrows(arrow_names)

        except Exception:
            App.Console.PrintError("_on_set_angle_clicked failed:\n" + traceback.format_exc())
    
    def _collect_grain_arrow_names_from_table(self):
        """Return list of GrainArrow_<previewObjName> for ALL rows where Grain Direction checkbox is checked."""
        names = []
        try:
            if self.preview_doc_name not in App.listDocuments():
                return names
            p_doc = App.getDocument(self.preview_doc_name)
            if not p_doc:
                return names

            data_rows = self.table.rowCount() - self.control_rows
            seen = set()

            for r in range(data_rows):
                try:
                    # grain checkbox in column 4
                    grain_widget = self.table.cellWidget(r, 4)
                    if not grain_widget:
                        continue
                    cb = grain_widget.findChild(QtGui.QCheckBox)
                    if not (cb and cb.isChecked()):
                        continue

                    name_item = self.table.item(r, 0)
                    if not name_item:
                        continue

                    row_obj_names = []
                    try:
                        list_data = name_item.data(QtCore.Qt.UserRole + 1)
                        if list_data:
                            if isinstance(list_data, list):
                                row_obj_names = list(list_data)
                            else:
                                row_obj_names = json.loads(list_data)
                        else:
                            primary = name_item.data(QtCore.Qt.UserRole)
                            if primary:
                                row_obj_names = [primary]
                    except Exception:
                        row_obj_names = []

                    for obj_name in row_obj_names:
                        if not obj_name:
                            continue
                        arrow_name = "GrainArrow_" + str(obj_name)
                        # include only if arrow object exists
                        try:
                            if p_doc.getObject(arrow_name) and arrow_name not in seen:
                                seen.add(arrow_name)
                                names.append(arrow_name)
                        except Exception:
                            pass

                except Exception:
                    continue

        except Exception:
            pass
        return names
    
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

    def _serialize_shape_to_avoid_hash_issues(self, shape):
        """
        Serialize shape to STEP and re-import to break hash chain.
        
        This prevents "hasher mismatch" errors when copying PartDesign::Body
        objects by fully serializing the geometry through STEP format.
        
        Args:
            shape: The shape to serialize
            
        Returns:
            A new independent Shape or the original shape if serialization fails
        """
        temp_step_path = None
        try:
            # Create temporary STEP file
            temp_step_file = tempfile.NamedTemporaryFile(suffix=".step", delete=False)
            temp_step_path = temp_step_file.name
            temp_step_file.close()
            
            # Export shape to STEP
            App.Console.PrintMessage(f"Serializing shape via STEP to avoid hash issues: {temp_step_path}\n")
            shape.exportStep(temp_step_path)
            
            # Re-import from STEP to get clean, independent shape
            imported_shape = Part.Shape()
            imported_shape.read(temp_step_path)
            
            App.Console.PrintMessage("Shape successfully serialized and re-imported\n")
            return imported_shape
            
        except Exception as e:
            App.Console.PrintWarning(f"Shape serialization failed, using direct copy: {e}\n")
            # Fallback to direct copy if serialization fails
            return shape.copy()
            
        finally:
            # Clean up temporary file
            if temp_step_path is not None:
                try:
                    os.unlink(temp_step_path)
                except Exception as e:
                    App.Console.PrintWarning(f"Failed to clean up temporary STEP file {temp_step_path}: {e}\n")

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

                    # Prefer stable preview geometry for PartDesign::Body: use Tip.Shape as Part::Feature
                    new_obj = None
                    try:
                        if getattr(target, "TypeId", "") == "PartDesign::Body":
                            tip = getattr(target, "Tip", None)
                            tip_shape = getattr(tip, "Shape", None) if tip else None

                            if tip_shape is not None:
                                # Serialize shape to avoid hash mismatch issues
                                clean_shape = self._serialize_shape_to_avoid_hash_issues(tip_shape)
                                new_obj = p_doc.addObject("Part::Feature", "PreviewShape")
                                new_obj.Label = target.Label
                                new_obj.Shape = clean_shape
                            else:
                                # fallback to copying if Tip.Shape not available
                                new_obj = p_doc.copyObject(target, False)
                                new_obj.Label = target.Label
                        else:
                            new_obj = p_doc.copyObject(target, False)
                            new_obj.Label = target.Label
                    except Exception:
                        # final fallback
                        try:
                            new_obj = p_doc.copyObject(target, False)
                            new_obj.Label = target.Label
                        except Exception:
                            new_obj = None

                    if new_obj is None:
                        App.Console.PrintError("Failed to create preview object for '%s'\n" % getattr(target, "Label", "<unknown>"))
                        continue
                    
                    # --- FIX: PartDesign::Body in preview may have invalid Shape/BoundBox; use Tip.Shape as fallback ---
                    try:
                        p_doc.recompute()
                    except Exception:
                        pass

                    def _bbox_is_valid(bb):
                        try:
                            # valid bbox must have positive extents in XY at least
                            return (bb.XMax > bb.XMin) and (bb.YMax > bb.YMin)
                        except Exception:
                            return False

                    try:
                        if getattr(new_obj, "TypeId", "") == "PartDesign::Body":
                            shp = getattr(new_obj, "Shape", None)
                            bb = shp.BoundBox if shp else None

                            if (shp is None) or (bb is None) or (not _bbox_is_valid(bb)):
                                tip = getattr(new_obj, "Tip", None)
                                tip_shape = getattr(tip, "Shape", None) if tip else None

                                if tip_shape is not None:
                                    # Serialize shape to avoid hash mismatch issues
                                    clean_shape = self._serialize_shape_to_avoid_hash_issues(tip_shape)
                                    feat = p_doc.addObject("Part::Feature", "PreviewShape_" + new_obj.Name)
                                    feat.Label = new_obj.Label
                                    feat.Shape = clean_shape

                                    # Hide the broken Body container copy (optional, but keeps preview clean)
                                    try:
                                        new_obj.ViewObject.Visibility = False
                                    except Exception:
                                        pass

                                    # IMPORTANT: from now on treat this Part::Feature as the preview object
                                    new_obj = feat

                                    try:
                                        p_doc.recompute()
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    # --- end fix ---

                    alignment_rot = self.align_to_largest_face(new_obj)
                    new_obj.Placement = App.Placement(App.Vector(0, 0, 0), alignment_rot)
                    
                    p_doc.recompute()

                    # Access Shape properties to trigger hash recomputation and validate stability
                    try:
                        _ = new_obj.Shape.BoundBox
                        _ = new_obj.Shape.Volume
                    except Exception as e:
                        App.Console.PrintWarning(f"Failed to access shape properties for hash validation: {e}\n")

                    bbox = new_obj.Shape.BoundBox

                    # FIX: if bbox invalid, create a stable Part::Feature from Body.Tip.Shape and use it for layout/preview
                    try:
                        if not (bbox.XMax > bbox.XMin and bbox.YMax > bbox.YMin):
                            # only try fallback for PartDesign::Body
                            if getattr(new_obj, "TypeId", "") == "PartDesign::Body":
                                App.Console.PrintMessage("add_selected_objects: invalid bbox for %s; trying Tip.Shape fallback\n" % new_obj.Name)

                                tip = getattr(new_obj, "Tip", None)
                                tip_shape = getattr(tip, "Shape", None) if tip else None

                                if tip_shape is not None:
                                    # Serialize shape to avoid hash mismatch issues
                                    clean_shape = self._serialize_shape_to_avoid_hash_issues(tip_shape)
                                    feat = p_doc.addObject("Part::Feature", "PreviewShape_" + new_obj.Name)
                                    feat.Label = new_obj.Label
                                    feat.Shape = clean_shape

                                    # hide broken body copy
                                    try:
                                        new_obj.ViewObject.Visibility = False
                                    except Exception:
                                        pass

                                    # switch to the new stable preview object
                                    new_obj = feat

                                    try:
                                        p_doc.recompute()
                                    except Exception:
                                        pass

                                    bbox = new_obj.Shape.BoundBox  # refresh bbox after fallback

                            # If still invalid after fallback -> skip placement safely
                            if not (bbox.XMax > bbox.XMin and bbox.YMax > bbox.YMin):
                                App.Console.PrintMessage(
                                    "add_selected_objects: still invalid bbox for %s (%s); skipping grid placement\n" %
                                    (getattr(new_obj, "Name", "<unknown>"), getattr(new_obj, "TypeId", ""))
                                )
                                continue
                    except Exception:
                        continue
                        
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
                        if not new_obj:
                            continue

                        # keep label consistent (optional but nice)
                        try:
                            new_obj.Label = base_obj.Label
                        except Exception:
                            pass

                        # IMPORTANT: always track the new copy in row list + created list
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
        
    def _open_grain_angle_dialog_for_arrows(self, arrow_names):
        try:

            if getattr(self, "_grain_angle_dialog_open", False):
                return

            self._grain_angle_dialog_open = True

            if self.preview_doc_name not in App.listDocuments():
                return

            p_doc = App.getDocument(self.preview_doc_name)
            
            arrow_objs = []
            for nm in arrow_names:
                try:
                    o = p_doc.getObject(nm)
                    if o:
                        arrow_objs.append(o)
                except Exception:
                    pass

            if not arrow_objs:
                return
                
            arrow_names = [n for n in arrow_names if isinstance(n, str) and n.startswith("GrainArrow_")]

            if not arrow_names:
                return
                
            initial_angle = 0
                
            initial_placements = {}
            for nm in arrow_names:
                try:
                    o = p_doc.getObject(nm)
                    if o:
                        initial_placements[nm] = o.Placement
                except Exception:
                    pass
                    
            pivot_centers = {}
            for nm in arrow_names:
                try:
                    o = p_doc.getObject(nm)
                    if not o or not hasattr(o, "Shape") or o.Shape is None:
                        continue
                    bb = o.Shape.BoundBox
                    pivot_centers[nm] = App.Vector(
                        0.5 * (bb.XMin + bb.XMax),
                        0.5 * (bb.YMin + bb.YMax),
                        0.5 * (bb.ZMin + bb.ZMax),
                    )
                except Exception:
                    pass       

            # Prepare list for dialog
            part_labels = []
            for ao in arrow_objs:
                try:
                    arrow_name = getattr(ao, "Name", "") or ""
                    arrow_label = getattr(ao, "Label", "") or ""

                    part_label = "<unknown part>"
                    if arrow_name.startswith("GrainArrow_"):
                        part_name = arrow_name[len("GrainArrow_"):]
                        part_obj = p_doc.getObject(part_name) if p_doc else None
                        if part_obj:
                            part_label = getattr(part_obj, "Label", "") or getattr(part_obj, "Name", part_name)

                    part_labels.append("%s " % (part_label))
                except Exception:
                    part_labels.append(str(ao))

            parent = QtGui.QApplication.activeWindow()
            last_ui_angle = int(initial_angle) % 360
            
            def _apply_angle_to_arrows(angle_deg):
                nonlocal last_ui_angle
                try:
                    ui_angle = int(angle_deg) % 360
                except Exception:
                    ui_angle = 0

                delta_ui = ui_angle - last_ui_angle
                if delta_ui > 180:
                    delta_ui -= 360
                elif delta_ui < -180:
                    delta_ui += 360

                last_ui_angle = ui_angle

                rotZ = App.Rotation(App.Vector(0, 0, 1), float(delta_ui))

                for nm in arrow_names:
                    try:
                        o = p_doc.getObject(nm)
                        if not o:
                            continue

                        center = pivot_centers.get(nm)
                        if center is None:
                            continue

                        P_move = App.Placement(App.Vector(-center.x, -center.y, -center.z), App.Rotation())
                        P_rot  = App.Placement(App.Vector(0, 0, 0), rotZ)
                        P_back = App.Placement(center, App.Rotation())

                        o.Placement = P_back.multiply(P_rot.multiply(P_move.multiply(o.Placement)))
                    except Exception:
                        continue

                try:
                    Gui.updateGui()
                except Exception:
                    pass
                    
            dlg = GrainAngleDialog(parent=parent, part_labels=part_labels, initial_angle=initial_angle)

            # --- THROTTLE: apply at most every 50ms ---
            pending_angle = None

            def _on_angle_changed(a):
                nonlocal pending_angle
                pending_angle = a

            def _on_apply_tick():
                nonlocal pending_angle
                if pending_angle is None:
                    return
                a = pending_angle
                pending_angle = None
                _apply_angle_to_arrows(a)

            apply_timer = QtCore.QTimer()
            apply_timer.setInterval(50)
            apply_timer.timeout.connect(_on_apply_tick)
            apply_timer.start()

            try:
                dlg.angleChanged.connect(_on_angle_changed)
            except Exception:
                pass

            res = dlg.exec_()

            # stop timer after dialog closes
            try:
                apply_timer.stop()
            except Exception:
                pass
            
            if res == QtGui.QDialog.Accepted:
                try:
                    final_angle = int(dlg.angle_degrees()) % 360
                except Exception:
                    final_angle = 0

                for nm in arrow_names:
                    # NEW: also save angle on the PART object referenced by this GrainArrow_<partName>
                    try:
                        if isinstance(nm, str) and nm.startswith("GrainArrow_"):
                            part_name = nm[len("GrainArrow_"):]
                            part_obj = p_doc.getObject(part_name) if p_doc else None
                            if part_obj:
                                if not hasattr(part_obj, "GrainAngleDeg"):
                                    try:
                                        part_obj.addProperty(
                                            "App::PropertyInteger",
                                            "GrainAngleDeg",
                                            "IPNesting",
                                            "Absolute grain angle in degrees vs +X"
                                        )
                                    except Exception:
                                        pass
                                try:
                                    part_obj.GrainAngleDeg = int(final_angle) % 360
                                except Exception:
                                    pass
                    except Exception:
                        pass
                        
                # Run the same pipeline as "Apply Grain"
                try:
                    self.update_grain_layout_and_perimeters()
                except Exception:
                    pass
            
            try:
                if res != QtGui.QDialog.Accepted:
                    # restore original placements on cancel/close
                    for nm, pl in initial_placements.items():
                        try:
                            o = p_doc.getObject(nm)
                            if o:
                                o.Placement = pl
                        except Exception:
                            pass
                    try:
                        Gui.updateGui()
                    except Exception:
                        pass
            except Exception:
                pass

            # NOTE: for now do nothing else

        except Exception:
            App.Console.PrintError("[IPNesting][DEBUG] _open_grain_angle_dialog_for_selected_arrows FAILED:\n" + traceback.format_exc())
        finally:
            try:
                self._grain_angle_dialog_open = False
                App.Console.PrintMessage("[IPNesting][DEBUG] Set _grain_angle_dialog_open=False (finally)\n")
            except Exception:
                pass
            
    def _rotate_preview_parts_about_z(self, p_doc, part_names, delta_deg):
        try:
            axis = App.Vector(0, 0, 1)
            rot = App.Rotation(axis, float(delta_deg))

            for nm in part_names or []:
                try:
                    o = p_doc.getObject(nm)
                    if not o or not hasattr(o, "Shape") or o.Shape is None:
                        continue

                    bb = o.Shape.BoundBox
                    cx = 0.5 * (bb.XMin + bb.XMax)
                    cy = 0.5 * (bb.YMin + bb.YMax)
                    cz = 0.5 * (bb.ZMin + bb.ZMax)
                    center = App.Vector(cx, cy, cz)

                    P_move = App.Placement(App.Vector(-center.x, -center.y, -center.z), App.Rotation())
                    P_rot = App.Placement(App.Vector(0, 0, 0), rot)
                    P_back = App.Placement(center, App.Rotation())

                    new_pl = P_back.multiply(P_rot.multiply(P_move.multiply(o.Placement)))
                    o.Placement = new_pl
                except Exception:
                    continue

            try:
                p_doc.recompute()
            except Exception:
                pass

            # pēc rotācijas pārkārtošanu var gribēt:
            # self.update_grain_layout_and_perimeters()
        except Exception:
            App.Console.PrintError("_rotate_preview_parts_about_z failed:\n" + traceback.format_exc())
            
    def _redraw_grain_arrows_for_parts(self, part_names):
        try:
            if GrainPreparer is None:
                return
            if self.preview_doc_name not in App.listDocuments():
                return
            p_doc = App.getDocument(self.preview_doc_name)
            if not p_doc:
                return

            for nm in part_names or []:
                try:
                    o = p_doc.getObject(nm)
                    if not o:
                        continue

                    # Te vajag "current grain angle" – minimāli var paņemt no objekta Placement
                    # un bultu zīmēt pēc objekta rotācijas (skat. 3.2)
                    GrainPreparer.update_grain_arrow(self.preview_doc_name, nm, enable=True, axis='X')
                except Exception:
                    continue
        except Exception:
            App.Console.PrintError("_redraw_grain_arrows_for_parts failed:\n" + traceback.format_exc())
            
    def _prefs(self):
        # helper so we can call it anywhere
        try:
            return App.ParamGet("User parameter:BaseApp/Preferences/Mod/IPNesting")
        except Exception:
            return None

    def _load_settings_from_prefs(self):
        p = self._prefs()
        if not p:
            return
        try:
            # block signals while setting values
            widgets = [
                self.sheet_w, self.sheet_h, self.sheet_margin,
                self.sheet_grain_combo,
                self.spacing, self.res,
                self.select_strategy,
                self.nesting_algorithm,
                self.gen, self.pop,
            ]
            for w in widgets:
                try:
                    w.blockSignals(True)
                except Exception:
                    pass

            # QLineEdits
            self.sheet_w.setText(str(p.GetString("SheetWidth", self.sheet_w.text())))
            self.sheet_h.setText(str(p.GetString("SheetHeight", self.sheet_h.text())))
            self.sheet_margin.setText(str(p.GetString("SheetMargin", self.sheet_margin.text())))
            self.spacing.setText(str(p.GetString("PartSpacing", self.spacing.text())))
            self.res.setText(str(p.GetString("BoundaryResolution", self.res.text())))
            self.gen.setText(str(p.GetString("Generations", self.gen.text())))
            self.pop.setText(str(p.GetString("PopulationSize", self.pop.text())))

            # Combos: store by index (robust) or by text (more readable)
            try:
                self.sheet_grain_combo.setCurrentIndex(int(p.GetInt("SheetGrainIndex", self.sheet_grain_combo.currentIndex())))
            except Exception:
                pass
            try:
                self.select_strategy.setCurrentIndex(int(p.GetInt("StrategyIndex", self.select_strategy.currentIndex())))
            except Exception:
                pass
            try:
                self.nesting_algorithm.setCurrentIndex(int(p.GetInt("AlgorithmIndex", self.nesting_algorithm.currentIndex())))
            except Exception:
                pass

        finally:
            for w in widgets:
                try:
                    w.blockSignals(False)
                except Exception:
                    pass

    def _save_settings_to_prefs(self):
        p = self._prefs()
        if not p:
            return
        try:
            p.SetString("SheetWidth", self.sheet_w.text().strip())
            p.SetString("SheetHeight", self.sheet_h.text().strip())
            p.SetString("SheetMargin", self.sheet_margin.text().strip())
            p.SetInt("SheetGrainIndex", int(self.sheet_grain_combo.currentIndex()))

            p.SetString("PartSpacing", self.spacing.text().strip())
            p.SetString("BoundaryResolution", self.res.text().strip())

            p.SetInt("StrategyIndex", int(self.select_strategy.currentIndex()))
            p.SetInt("AlgorithmIndex", int(self.nesting_algorithm.currentIndex()))

            p.SetString("Generations", self.gen.text().strip())
            p.SetString("PopulationSize", self.pop.text().strip())
        except Exception:
            pass

    def _connect_settings_persistence(self):
        # Save on change
        try:
            # line edits
            for le in [self.sheet_w, self.sheet_h, self.sheet_margin, self.spacing, self.res, self.gen, self.pop]:
                try:
                    le.editingFinished.connect(self._save_settings_to_prefs)
                except Exception:
                    pass

            # combos
            try:
                self.sheet_grain_combo.currentIndexChanged.connect(self._save_settings_to_prefs)
            except Exception:
                pass
            try:
                self.select_strategy.currentIndexChanged.connect(self._save_settings_to_prefs)
            except Exception:
                pass
            try:
                self.nesting_algorithm.currentIndexChanged.connect(self._save_settings_to_prefs)
            except Exception:
                pass
        except Exception:
            pass
            
    def _add_preview_object_to_table(self, p_doc, obj_name):
        """
        Add an existing preview object (by Name) into the table as a new data row.
        Mirrors the row structure used by add_selected_objects().
        """
        try:
            if not p_doc or not obj_name:
                return False
            obj = p_doc.getObject(obj_name)
            if not obj:
                return False

            insert_pos = max(0, self.table.rowCount() - self.control_rows)
            self.table.insertRow(insert_pos)

            name_item = QtGui.QTableWidgetItem(obj.Label)
            name_item.setData(QtCore.Qt.UserRole, obj.Name)
            try:
                name_item.setData(QtCore.Qt.UserRole + 1, json.dumps([obj.Name]))
            except Exception:
                name_item.setData(QtCore.Qt.UserRole + 1, [obj.Name])
            self.table.setItem(insert_pos, 0, name_item)

            qty_item = QtGui.QTableWidgetItem("1")
            qty_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(insert_pos, 1, qty_item)

            rot_item = QtGui.QTableWidgetItem("0,90,180,270")
            rot_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(insert_pos, 2, rot_item)

            # Column 3: Select for rotation checkbox (centered)
            container_sel = QtGui.QWidget()
            cell_layout_sel = QtGui.QHBoxLayout(container_sel)
            cell_layout_sel.setContentsMargins(3, 0, 3, 0)
            cell_layout_sel.setSpacing(0)
            cell_layout_sel.addStretch()
            checkbox = QtGui.QCheckBox()
            checkbox.setToolTip("Select this part for bulk Rotate")
            cell_layout_sel.addWidget(checkbox)
            cell_layout_sel.addStretch()
            self.table.setCellWidget(insert_pos, 3, container_sel)

            # Column 4: Grain Direction checkbox + combobox
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

            try:
                self._connect_grain_widgets(grain_cb, grain_combo, obj.Name)
            except Exception:
                pass

            # keep UI widths stable
            try:
                self.table.setColumnWidth(0, 250)
                self.table.setColumnWidth(1, 40)
            except Exception:
                pass

            self.added_count += 1
            return True

        except Exception:
            App.Console.PrintError("_add_preview_object_to_table failed:\n" + traceback.format_exc())
            return False

    def import_dxf_2d(self):
        try:
            if import_dxf_to_preview is None:
                QtGui.QMessageBox.warning(None, "Import DXF", "DXF import module not available.")
                return

            path, _ = QtGui.QFileDialog.getOpenFileName(
                None, "Import DXF", "", "DXF files (*.dxf *.DXF);;All files (*.*)"
            )
            if not path:
                return

            p_doc = self.ensure_preview_doc()
            created = import_dxf_to_preview(
                self,
                path,
                make_faces_if_possible=False,
                group_into_single_object=True  # set False if you want each entity as separate part
            )

            if not created:
                QtGui.QMessageBox.warning(None, "Import DXF", "No usable geometry imported.")
                return

            # Add created objects to the table (so they can be rotated like others)
            for nm in created:
                self._add_preview_object_to_table(p_doc, nm)

            try:
                p_doc.recompute()
                Gui.setActiveDocument(p_doc)
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass

            # update perimeters/layout
            try:
                self.update_grain_layout_and_perimeters()
            except Exception:
                pass
            try:
                self._update_apply_blink_state()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError("import_dxf_2d failed:\n" + traceback.format_exc())

    def import_svg_2d(self):
        try:
            if import_svg_to_preview is None:
                QtGui.QMessageBox.warning(None, "Import SVG", "SVG import module not available.")
                return

            path, _ = QtGui.QFileDialog.getOpenFileName(
                None, "Import SVG", "", "SVG files (*.svg *.SVG);;All files (*.*)"
            )
            if not path:
                return

            p_doc = self.ensure_preview_doc()
            created = import_svg_to_preview(
                self,
                path,
                make_faces_if_possible=False,
                group_into_single_object=True
            )

            if not created:
                QtGui.QMessageBox.warning(None, "Import SVG", "No usable geometry imported.")
                return

            for nm in created:
                self._add_preview_object_to_table(p_doc, nm)

            try:
                p_doc.recompute()
                Gui.setActiveDocument(p_doc)
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass

            try:
                self.update_grain_layout_and_perimeters()
            except Exception:
                pass
            try:
                self._update_apply_blink_state()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError("import_svg_2d failed:\n" + traceback.format_exc())