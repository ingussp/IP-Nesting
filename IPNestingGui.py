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
import subprocess
import Part
from IPNestingRelayout import NestingRelayoutManager
from functools import partial
from IPNestingExport import execute_nesting as execute_nesting_impl
from IPNestingGrainUI import GrainUIController
from IPNestingPreviewDoc import PreviewDocManager
from IPNestingGrainAngleDialog import GrainAngleDialog
from IPNestingImport2D import import_dxf_to_preview, import_svg_to_preview
from IPNestingOffcutShowDialog import OffcutMaterialsController

MM_PER_INCH = 25.4


try:
    from IPNestingImport import apply_nesting_result
except Exception:
    try:
        from .IPNestingImport import apply_nesting_result
    except Exception:
        App.Console.PrintError("Failed to import IPNestingImport:\n" + traceback.format_exc())
        apply_nesting_result = None
try:
    from IPNestingImportSheets import import_nesting_sheets
except Exception:
    try:
        from .IPNestingImportSheets import import_nesting_sheets
    except Exception:
        App.Console.PrintError("Failed to import IPNestingImportSheets:\n" + traceback.format_exc())
        import_nesting_sheets = None


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
        
        # Display units. Geometry and nesting calculations remain in mm.
        self.display_units = "mm"
        self._units_change_guard = False
        
        # Canonical dimension values. Always stored in mm.
        self._dimension_values_mm = {
            "sheet_margin": 5.0,
            "spacing": 6.0,
            "boundary_resolution": 0.1,
        }

        # NEW: offcuts model
        self.offcuts = []
        self._offcut_next_id = 1
        
        # Shared Hole-to-part clearance for all sheets/offcuts.
        # This is intentionally reset every time the workbench opens.
        self.offcut_clearance_mode = "same"
        self.offcut_custom_clearance = 0.0
        
        self.offcut_controller = OffcutMaterialsController(self)

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

        self.sheet_margin, self.sheet_margin_label = (
            self.create_input_in_layout(
                sheet_lay,
                "Sheet Margin (mm):",
                "5.00",
                "Distance from the sheet edge."
            )
        )

        self.spacing, self.spacing_label = (
            self.create_input_in_layout(
                sheet_lay,
                "Part Spacing (mm):",
                "6.00",
                "Minimum distance between parts."
            )
        )

        # NEW: Offcuts (DXF) (LEFT, row 1)
        offcut_box = QtGui.QGroupBox("Sheet && Offcut Materials")
        offcut_lay = QtGui.QVBoxLayout(offcut_box)

        self.offcuts_table = QtGui.QTableWidget(0, 4)
        self.offcuts_table.setHorizontalHeaderLabels([
            "Material",
            "Count",
            "Grain",
            "Move",
        ])
        self.offcuts_table.horizontalHeaderItem(1).setToolTip("Number of sheets or offcuts.")
        
        self.offcuts_table.setSelectionMode(QtGui.QAbstractItemView.SingleSelection)
        self.offcuts_table.setEditTriggers( QtGui.QAbstractItemView.DoubleClicked | QtGui.QAbstractItemView.EditKeyPressed)
        self.offcuts_table.setToolTip("Add rectangular sheets or DXF offcuts for nesting.")
        self.offcuts_table.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.offcuts_table.itemChanged.connect(self.offcut_controller.on_offcut_count_changed)
        self.offcuts_table.setMinimumHeight(200)

        # Column sizing
        try:
            header = self.offcuts_table.horizontalHeader()
            if hasattr(header, "setSectionResizeMode"):
                header.setSectionResizeMode(0, QtGui.QHeaderView.Stretch)
                header.setSectionResizeMode(1, QtGui.QHeaderView.Fixed)
                header.setSectionResizeMode(2, QtGui.QHeaderView.Fixed)
            else:
                header.setResizeMode(0, QtGui.QHeaderView.Stretch)
                header.setResizeMode(1, QtGui.QHeaderView.Fixed)
                header.setResizeMode(2, QtGui.QHeaderView.Fixed)
        except Exception:
            pass
        try:
            self.offcuts_table.setColumnWidth(0, 220)  # Material
            self.offcuts_table.setColumnWidth(1, 65)   # Count
            self.offcuts_table.setColumnWidth(2, 70)   # Grain
            self.offcuts_table.setColumnWidth(3, 70)   # Move
        except Exception:
            pass

        offcut_lay.addWidget(self.offcuts_table)

        off_btns = QtGui.QHBoxLayout()
        self.offcut_add_btn = QtGui.QPushButton("Add")
        self.offcut_show_btn = QtGui.QPushButton("Show")
        self.offcut_remove_btn = QtGui.QPushButton("Remove")
        self.offcut_add_btn.setToolTip("Add a rectangular sheet or a DXF offcut.")
        self.offcut_show_btn.setToolTip("Show all added offcuts and adjust grain X/Y per offcut.")
        self.offcut_remove_btn.setToolTip("Remove the selected material from the list.")
        self.offcut_add_btn.clicked.connect(self.offcut_controller.add_offcut_dxf)
        self.offcut_show_btn.clicked.connect(self.offcut_controller.show_offcuts_popup)
        self.offcut_remove_btn.clicked.connect(self.offcut_controller.remove_offcuts)
        off_btns.addWidget(self.offcut_add_btn)
        off_btns.addWidget(self.offcut_show_btn)
        off_btns.addWidget(self.offcut_remove_btn)
        off_btns.addStretch()
        offcut_lay.addLayout(off_btns)

        # General Parameters (LEFT, row 2)  (shifted down by 1)
        general_box = QtGui.QGroupBox("General Parameters")
        general_lay = QtGui.QVBoxLayout(general_box)
        self.res, self.res_label = (
            self.create_input_in_layout(
                general_lay,
                "Boundary Resolution (mm):",
                "0.1",
                "Maximum deviation used when curved geometry is converted to line segments. Smaller values create more accurate but heavier geometry."
            )
        )

        # Display Units (RIGHT, row 0)
        units_box = QtGui.QGroupBox("Units")
        units_lay = QtGui.QVBoxLayout(units_box)

        self.units_combo = QtGui.QComboBox()
        self.units_combo.addItems([
            "mm",
            "inch",
        ])
        self.units_combo.setCurrentIndex(0)
        self.units_combo.setToolTip(
            "Display and input units for dimensions. "
            "Internal geometry remains in millimetres."
        )

        units_lay.addWidget(self.units_combo)

        self.units_combo.currentIndexChanged.connect(
            self._on_units_changed
        )

        # Deepnest Settings (RIGHT, row 1)
        deepnest_box = QtGui.QGroupBox(
            "Deepnest settings"
        )
        deepnest_lay = QtGui.QVBoxLayout(
            deepnest_box
        )

        self.deepnest_time_ratio = (
            self.create_input_in_layout(
                deepnest_lay,
                "Time ratio:",
                "0.5",
                (
                    "Controls how much of the available nesting "
                    "time is used for optimization. Higher values "
                    "allow more optimization time and may improve "
                    "the result, but can make nesting slower."
                )
            )[0]
        )

        self.deepnest_population_size = (
            self.create_input_in_layout(
                deepnest_lay,
                "Population size:",
                "10",
                (
                    "Number of candidate nesting solutions kept "
                    "during genetic optimization. Higher values "
                    "can improve the result, but require more "
                    "calculation time."
                )
            )[0]
        )

        self.deepnest_mutation_rate = (
            self.create_input_in_layout(
                deepnest_lay,
                "Mutation rate:",
                "10",
                (
                    "Percentage controlling how often candidate "
                    "solutions are randomly changed during "
                    "optimization. Higher values increase variation "
                    "but can make the result less stable."
                )
            )[0]
        )

        self.deepnest_export_sheet_boundaries = (
            self._create_boolean_setting(
                deepnest_lay,
                "Export sheet boundaries:",
                False,
                (
                    "If enabled, the outer boundaries of sheets "
                    "are included in the exported nesting data. "
                    "Enable this only when the nesting engine "
                    "needs explicit sheet boundary geometry."
                )
            )
        )

        self.deepnest_export_sheets_space = (
            self._create_boolean_setting(
                deepnest_lay,
                "Export sheet spacing:",
                False,
                (
                    "If enabled, an additional spacing value is "
                    "applied between exported sheets. This is "
                    "useful when several sheets are exported "
                    "together."
                )
            )
        )

        self.deepnest_export_sheets_space_value = (
            self.create_input_in_layout(
                deepnest_lay,
                "Sheet spacing value:",
                "0.13888",
                (
                    "Distance between exported sheets when "
                    "'Export sheet spacing' is enabled. "
                    "The value is interpreted in the internal "
                    "geometry units, normally millimetres."
                )
            )[0]
        )

        cfg_grid.addWidget(
            deepnest_box,
            1,
            1
        )

        # Placement Strategy (RIGHT, row 2)
        placement_box = QtGui.QGroupBox(
            "Placement strategy"
        )
        placement_lay = QtGui.QVBoxLayout(
            placement_box
        )

        self.placement_strategy = QtGui.QComboBox()
        self.placement_strategy.addItems([
            "Gravity",
            "Bounding box",
            "Squeeze",
        ])
        self.placement_strategy.setCurrentIndex(0)

        self.placement_strategy.setItemData(
            0,
            (
                "Minimize the width of the nest. "
                "Good when using a rectangular sheet and "
                "the leftover material can be used for another cut."
            ),
            QtCore.Qt.ToolTipRole
        )

        self.placement_strategy.setItemData(
            1,
            (
                "Reduce the overall rectangular bounds. "
                "Best for conserving material when only a small "
                "portion of the sheet is used."
            ),
            QtCore.Qt.ToolTipRole
        )

        self.placement_strategy.setItemData(
            2,
            (
                "Reduce the overall area. This may produce nests "
                "that are not rectangular. Best for irregularly "
                "shaped sheets or when unused space is not important."
            ),
            QtCore.Qt.ToolTipRole
        )

        self.placement_strategy.setToolTip(
            "Controls how placed parts are packed together. "
            "Gravity minimizes nest width, Bounding box minimizes "
            "the rectangular bounds, and Squeeze minimizes the "
            "overall occupied area."
        )

        placement_lay.addWidget(
            self.placement_strategy
        )

        # CPU Cores (RIGHT, row 3)
        cpu_box = QtGui.QGroupBox(
            "CPU cores"
        )
        cpu_lay = QtGui.QVBoxLayout(
            cpu_box
        )

        self.cpu_cores_combo = QtGui.QComboBox()

        detected_cores = self._detect_cpu_core_count()

        # Use at least one core and cap the selectable value at 16.
        max_cpu_cores = max(
            1,
            min(
                detected_cores,
                16
            )
        )

        for core_count in range(
            1,
            max_cpu_cores + 1
        ):
            self.cpu_cores_combo.addItem(
                str(core_count)
            )

        default_cpu_cores = max(
            1,
            min(
                detected_cores,
                16
            )
        )

        self.cpu_cores_combo.setCurrentText(
            str(default_cpu_cores)
        )

        self.cpu_cores_combo.setToolTip(
            "Number of CPU worker cores available to the "
            "nesting calculation.\n\n"
            "The list is based on the logical CPU cores detected "
            "on this computer and is limited to 16 choices. "
            "This limit is intentional: nesting performance is "
            "not guaranteed to improve when more than 16 workers "
            "are used, especially when the calculation contains "
            "serial operations, memory traffic, synchronization, "
            "or a single-threaded geometry step.\n\n"
            "Recommended values:\n"
            "• 1–4 cores: safer for older or low-power computers.\n"
            "• 4–8 cores: good general-purpose setting.\n"
            "• 8–16 cores: useful for complex nesting or large "
            "part collections, if the nesting engine supports "
            "parallel workers.\n"
            "• More than 16 cores: not offered by this UI because "
            "the expected benefit is uncertain and CPU, RAM, and "
            "synchronization overhead may increase.\n\n"
            "This setting only affects the calculation if the "
            "external nesting engine receives and uses the value."
        )

        cpu_lay.addWidget(
            self.cpu_cores_combo
        )

        cfg_grid.addWidget(
            cpu_box,
            3,
            1
        )



        # Place boxes in 2-column grid
        cfg_grid.addWidget(
            sheet_box,
            0,
            0
        )

        # Offcut table spans rows 1 and 2.
        cfg_grid.addWidget(
            offcut_box,
            1,
            0,
            2,
            1
        )

        cfg_grid.addWidget(
            general_box,
            3,
            0
        )

        cfg_grid.addWidget(units_box, 0, 1)
        cfg_grid.addWidget(placement_box,2, 1)
        cfg_grid.addWidget(
            cpu_box,
            3,
            1
        )

        # Make columns expand nicely
        try:
            # Left column = 2/3, right column = 1/3.
            cfg_grid.setColumnStretch(0, 2)
            cfg_grid.setColumnStretch(1, 1)
        except Exception:
            pass

        # Table (with control_rows at the bottom)
        self.layout.addWidget(QtGui.QLabel("<b>Selected Parts (Preview Mode)</b>"))
        self.table = QtGui.QTableWidget(self.control_rows, 6)  # reserve control_rows initially
        self.table.setHorizontalHeaderLabels([
            "Body", "Qty", "Rotations", "Select for rotation", "Grain Direction", "Custom angle"
        ])
        try:
            self.table.horizontalHeaderItem(3).setToolTip(
                "Select which parts will be rotated in the XY plane.\n\n"
                "When parts are added, the alignment algorithm selects the "
                "largest face and turns it upward. For some parts, the intended "
                "top face may be smaller than a side or bottom face.\n\n"
                "Rotating a part by 90 degrees makes a side face become the "
                "top face. Rotating it by 180 degrees makes the bottom face "
                "become the top face."
            )
        except Exception:
            pass
        try:
            self.table.horizontalHeaderItem(5).setToolTip(
                "Enable this checkbox to allow the "
                "'Set custom angle' command to modify this part. "
                "Grain Direction must also be enabled."
            )
        except Exception:
            pass
        self.table.setMinimumHeight(400)
        self.table.horizontalHeader().setStretchLastSection(False)
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
            
            # Preserve the existing Rotation degree width and reuse it for Grain Direction.
            rotation_width = self.table.columnWidth(2)

            # Keep Body, Qty and Rotation degree widths unchanged.
            self.table.setColumnWidth(0, 250)
            self.table.setColumnWidth(1, 40)
            self.table.setColumnWidth(2, rotation_width)

            # Select for rotation: slightly wider, without unnecessary margins.
            self.table.setColumnWidth(3, rotation_width + 20)

            # Grain Direction: same width as Rotation degree.
            self.table.setColumnWidth(4, rotation_width + 40)
            
            # Custom angle
            self.table.setColumnWidth(5, rotation_width + 30)
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
        
        self.debug_export_btn = QtGui.QPushButton("Debug Export Polygons")
        self.debug_export_btn.setToolTip("Draw exported polygons in a separate document to inspect what is sent to the exe")
        self.debug_export_btn.clicked.connect(self.debug_export_polygons)
        self.layout.addWidget(self.debug_export_btn)
        
        self._grain_angle_dialog_open = False

        try:
            self._selection_observer = NestingTaskPanel._SelectionObserver(self)
            Gui.Selection.addObserver(self._selection_observer)
        except Exception:
            App.Console.PrintError("Failed to add selection observer:\n" + traceback.format_exc())

        self._load_settings_from_prefs()
        self._connect_settings_persistence()

    def debug_export_polygons(self):
        """
        Show detailed textual debug information about the current
        Nesting_Preview state.

        This function deliberately does not generate input.json and does not
        modify object placements. It only reads the current state visible in
        FreeCAD and displays diagnostic information.
        """
        try:
            lines = []

            def add(text=""):
                lines.append(str(text))

            def safe_float(value):
                try:
                    return "%.9f" % float(value)
                except Exception:
                    return str(value)

            def vector_text(vector):
                try:
                    return (
                        "(x=%s, y=%s, z=%s)"
                        % (
                            safe_float(vector.x),
                            safe_float(vector.y),
                            safe_float(vector.z),
                        )
                    )
                except Exception:
                    try:
                        return (
                            "(X=%s, Y=%s, Z=%s)"
                            % (
                                safe_float(vector.X),
                                safe_float(vector.Y),
                                safe_float(vector.Z),
                            )
                        )
                    except Exception:
                        return str(vector)

            def rotation_text(rotation):
                try:
                    axis = rotation.Axis
                    angle = rotation.Angle

                    try:
                        angle_deg = float(angle) * 180.0 / math.pi
                    except Exception:
                        angle_deg = angle

                    return (
                        "Axis=%s, Angle(rad)=%s, Angle(deg)=%s"
                        % (
                            vector_text(axis),
                            safe_float(angle),
                            safe_float(angle_deg),
                        )
                    )
                except Exception:
                    return str(rotation)

            def placement_text(placement):
                try:
                    return (
                        "Base=%s | Rotation={%s}"
                        % (
                            vector_text(placement.Base),
                            rotation_text(placement.Rotation),
                        )
                    )
                except Exception:
                    return str(placement)

            def point2d_text(point):
                try:
                    return (
                        "(x=%s, y=%s)"
                        % (
                            safe_float(point[0]),
                            safe_float(point[1]),
                        )
                    )
                except Exception:
                    return str(point)

            def point3d_text(point):
                try:
                    return (
                        "(x=%s, y=%s, z=%s)"
                        % (
                            safe_float(point.x),
                            safe_float(point.y),
                            safe_float(point.z),
                        )
                    )
                except Exception:
                    return str(point)

            def get_transformed_point(obj, point):
                """
                Transform a local Shape point using the complete current
                Placement. This is the exact operation that should be used
                to inspect the current visible orientation.
                """
                try:
                    return obj.Placement.multVec(point)
                except Exception:
                    return None

            def bbox_text(bbox):
                try:
                    return (
                        "XMin=%s, XMax=%s, YMin=%s, YMax=%s, "
                        "ZMin=%s, ZMax=%s | Width=%s, Height=%s, Depth=%s"
                        % (
                            safe_float(bbox.XMin),
                            safe_float(bbox.XMax),
                            safe_float(bbox.YMin),
                            safe_float(bbox.YMax),
                            safe_float(bbox.ZMin),
                            safe_float(bbox.ZMax),
                            safe_float(bbox.XMax - bbox.XMin),
                            safe_float(bbox.YMax - bbox.YMin),
                            safe_float(bbox.ZMax - bbox.ZMin),
                        )
                    )
                except Exception:
                    return str(bbox)

            def property_text(obj):
                try:
                    properties = obj.PropertiesList
                except Exception:
                    properties = []

                if not properties:
                    return "  No custom properties."

                result = []

                for property_name in properties:
                    try:
                        value = getattr(obj, property_name)
                        result.append(
                            "  %s = %s"
                            % (
                                property_name,
                                str(value),
                            )
                        )
                    except Exception:
                        result.append(
                            "  %s = <unreadable>"
                            % property_name
                        )

                return "\n".join(result)

            def dump_shape(obj):
                """
                Dump shape topology and all available vertices in both:
                - local Shape coordinates;
                - current Placement-transformed coordinates.
                """
                shape = getattr(obj, "Shape", None)

                if shape is None:
                    add("Shape: None")
                    return

                add("Shape object: %s" % str(shape))

                try:
                    add("Shape.isNull(): %s" % str(shape.isNull()))
                except Exception:
                    add("Shape.isNull(): <unavailable>")

                try:
                    add("Shape.Volume: %s" % safe_float(shape.Volume))
                except Exception:
                    add("Shape.Volume: <unavailable>")

                try:
                    add("Shape.Area: %s" % safe_float(shape.Area))
                except Exception:
                    add("Shape.Area: <unavailable>")

                try:
                    add(
                        "Topology counts: Solids=%d, Shells=%d, "
                        "Faces=%d, Wires=%d, Edges=%d, Vertices=%d"
                        % (
                            len(getattr(shape, "Solids", []) or []),
                            len(getattr(shape, "Shells", []) or []),
                            len(getattr(shape, "Faces", []) or []),
                            len(getattr(shape, "Wires", []) or []),
                            len(getattr(shape, "Edges", []) or []),
                            len(getattr(shape, "Vertexes", []) or []),
                        )
                    )
                except Exception:
                    add("Topology counts: <unavailable>")

                try:
                    local_bbox = shape.BoundBox
                    add("Shape local BoundBox:")
                    add("  " + bbox_text(local_bbox))
                except Exception:
                    add("Shape local BoundBox: <unavailable>")

                try:
                    vertices = list(
                        getattr(shape, "Vertexes", []) or []
                    )

                    add(
                        "Vertices (%d):"
                        % len(vertices)
                    )

                    if not vertices:
                        add("  <none>")

                    for index, vertex in enumerate(vertices):
                        try:
                            local_point = vertex.Point
                        except Exception:
                            add(
                                "  Vertex %d: <point unavailable>"
                                % index
                            )
                            continue

                        transformed_point = get_transformed_point(
                            obj,
                            local_point
                        )

                        add(
                            "  Vertex %d local=%s transformed=%s"
                            % (
                                index,
                                point3d_text(local_point),
                                (
                                    point3d_text(transformed_point)
                                    if transformed_point is not None
                                    else "<transformation failed>"
                                ),
                            )
                        )

                except Exception:
                    add("Vertices: <unavailable>")

                try:
                    wires = list(
                        getattr(shape, "Wires", []) or []
                    )

                    add(
                        "Wires (%d):"
                        % len(wires)
                    )

                    for wire_index, wire in enumerate(wires):
                        try:
                            is_closed = wire.isClosed()
                        except Exception:
                            is_closed = "<unavailable>"

                        try:
                            wire_edges = list(
                                getattr(wire, "Edges", []) or []
                            )
                        except Exception:
                            wire_edges = []

                        add(
                            "  Wire %d: closed=%s, edges=%d"
                            % (
                                wire_index,
                                str(is_closed),
                                len(wire_edges),
                            )
                        )

                        for edge_index, edge in enumerate(wire_edges):
                            try:
                                edge_vertices = list(
                                    getattr(edge, "Vertexes", []) or []
                                )
                            except Exception:
                                edge_vertices = []

                            add(
                                "    Edge %d: vertices=%d"
                                % (
                                    edge_index,
                                    len(edge_vertices),
                                )
                            )

                            for vertex_index, vertex in enumerate(
                                edge_vertices
                            ):
                                try:
                                    local_point = vertex.Point
                                except Exception:
                                    continue

                                transformed_point = (
                                    get_transformed_point(
                                        obj,
                                        local_point
                                    )
                                )

                                add(
                                    "      V%d local=%s transformed=%s"
                                    % (
                                        vertex_index,
                                        point3d_text(local_point),
                                        (
                                            point3d_text(
                                                transformed_point
                                            )
                                            if transformed_point is not None
                                            else "<failed>"
                                        ),
                                    )
                                )

                except Exception:
                    add("Wires: <unavailable>")

            def get_row_object_names(item):
                names = []

                if item is None:
                    return names

                try:
                    list_data = item.data(
                        QtCore.Qt.UserRole + 1
                    )

                    if list_data:
                        if isinstance(
                            list_data,
                            list
                        ):
                            names = list(list_data)
                        else:
                            names = json.loads(
                                list_data
                            )

                        if not isinstance(
                            names,
                            list
                        ):
                            names = [names]

                except Exception:
                    names = []

                if not names:
                    try:
                        primary = item.data(
                            QtCore.Qt.UserRole
                        )

                        if primary:
                            names = [primary]

                    except Exception:
                        names = []

                return [
                    str(name)
                    for name in names
                    if name
                ]

            # ---------------------------------------------------------
            # General information
            # ---------------------------------------------------------

            add("=" * 100)
            add("IP-NESTING CURRENT STATE DEBUG")
            add("=" * 100)
            add("This report only reads the current FreeCAD state.")
            add("No input.json was generated by this debug function.")
            add("No object Placement was modified.")
            add("")

            add("Python file:")
            add("  %s" % os.path.abspath(__file__))
            add("")

            add("FreeCAD:")
            try:
                add("  Version: %s" % str(App.Version()))
            except Exception:
                add("  Version: <unavailable>")

            add("  Documents: %s" % str(App.listDocuments()))
            add("  Preview document name: %s" % self.preview_doc_name)
            add("  Display units: %s" % str(self.display_units))
            add("")

            # ---------------------------------------------------------
            # Preview document
            # ---------------------------------------------------------

            if self.preview_doc_name not in App.listDocuments():
                add("ERROR: Preview document does not exist.")
            else:
                p_doc = App.getDocument(
                    self.preview_doc_name
                )

                if p_doc is None:
                    add("ERROR: Could not get preview document.")
                else:
                    try:
                        p_doc.recompute()
                        add(
                            "Preview document recompute: completed"
                        )
                    except Exception:
                        add(
                            "Preview document recompute: FAILED"
                        )

                    add(
                        "Preview document objects: %d"
                        % len(p_doc.Objects)
                    )
                    add("")

                    for object_index, obj in enumerate(
                        p_doc.Objects
                    ):
                        try:
                            name = getattr(
                                obj,
                                "Name",
                                "<unknown>"
                            )

                            label = getattr(
                                obj,
                                "Label",
                                "<no label>"
                            )

                            type_id = getattr(
                                obj,
                                "TypeId",
                                "<unknown>"
                            )

                            add("-" * 100)
                            add(
                                "PREVIEW OBJECT %d"
                                % object_index
                            )
                            add("-" * 100)
                            add("Name: %s" % str(name))
                            add("Label: %s" % str(label))
                            add("TypeId: %s" % str(type_id))

                            try:
                                add(
                                    "Visibility: %s"
                                    % str(
                                        obj.ViewObject.Visibility
                                    )
                                )
                            except Exception:
                                add(
                                    "Visibility: <unavailable>"
                                )

                            try:
                                add(
                                    "Placement:"
                                )
                                add(
                                    "  %s"
                                    % placement_text(
                                        obj.Placement
                                    )
                                )
                            except Exception:
                                add(
                                    "Placement: <unavailable>"
                                )

                            try:
                                add(
                                    "Properties:"
                                )
                                add(
                                    property_text(obj)
                                )
                            except Exception:
                                add(
                                    "Properties: <unavailable>"
                                )

                            dump_shape(obj)

                            # Special information for grain arrows.
                            if (
                                str(name).startswith(
                                    "GrainArrow_"
                                )
                                or str(label).startswith(
                                    "GrainArrow_"
                                )
                            ):
                                add("")
                                add(
                                    "This object is recognized as "
                                    "a grain arrow."
                                )

                        except Exception:
                            add(
                                "Failed to dump preview object %d:\n%s"
                                % (
                                    object_index,
                                    traceback.format_exc()
                                )
                            )

            # ---------------------------------------------------------
            # Table rows and their object associations
            # ---------------------------------------------------------

            add("")
            add("=" * 100)
            add("TABLE STATE")
            add("=" * 100)

            try:
                total_rows = self.table.rowCount()
                data_rows = max(
                    0,
                    total_rows - self.control_rows
                )

                add("Total table rows: %d" % total_rows)
                add("Control rows: %d" % self.control_rows)
                add("Data rows: %d" % data_rows)
                add("")

                for row in range(data_rows):
                    add("-" * 100)
                    add("TABLE DATA ROW %d" % row)
                    add("-" * 100)

                    name_item = self.table.item(row, 0)
                    qty_item = self.table.item(row, 1)
                    rotation_item = self.table.item(row, 2)

                    if name_item is None:
                        add("Name item: None")
                        continue

                    try:
                        row_label = name_item.text()
                    except Exception:
                        row_label = "<unavailable>"

                    try:
                        quantity = qty_item.text()
                    except Exception:
                        quantity = "<unavailable>"

                    try:
                        rotations = rotation_item.text()
                    except Exception:
                        rotations = "<unavailable>"

                    names = get_row_object_names(
                        name_item
                    )

                    add("Label: %s" % str(row_label))
                    add("Quantity: %s" % str(quantity))
                    add("Allowed rotations: %s" % str(rotations))
                    add(
                        "Associated preview names: %s"
                        % str(names)
                    )

                    try:
                        grain_widget = self.table.cellWidget(
                            row,
                            4
                        )

                        if grain_widget is not None:
                            grain_checkbox = (
                                grain_widget.findChild(
                                    QtGui.QCheckBox
                                )
                            )

                            grain_combo = (
                                grain_widget.findChild(
                                    QtGui.QComboBox
                                )
                            )

                            add(
                                "Grain checkbox checked: %s"
                                % str(
                                    bool(
                                        grain_checkbox
                                        and grain_checkbox.isChecked()
                                    )
                                )
                            )

                            add(
                                "Grain axis: %s"
                                % str(
                                    grain_combo.currentText()
                                    if grain_combo
                                    else "<unavailable>"
                                )
                            )

                    except Exception:
                        add(
                            "Grain widget state: <unavailable>"
                        )

                    try:
                        rotation_widget = self.table.cellWidget(
                            row,
                            3
                        )

                        if rotation_widget is not None:
                            rotation_checkbox = (
                                rotation_widget.findChild(
                                    QtGui.QCheckBox
                                )
                            )

                            add(
                                "Rotation checkbox checked: %s"
                                % str(
                                    bool(
                                        rotation_checkbox
                                        and rotation_checkbox.isChecked()
                                    )
                                )
                            )

                    except Exception:
                        add(
                            "Rotation widget state: <unavailable>"
                        )

            except Exception:
                add(
                    "Failed to dump table state:\n%s"
                    % traceback.format_exc()
                )

            # ---------------------------------------------------------
            # Current grain state
            # ---------------------------------------------------------

            add("")
            add("=" * 100)
            add("PANEL GRAIN STATE")
            add("=" * 100)

            try:
                grain_controller = getattr(
                    self,
                    "_grain",
                    None
                )

                if grain_controller is None:
                    add("Grain controller: None")
                else:
                    add(
                        "Last applied grain state: %s"
                        % str(
                            getattr(
                                grain_controller,
                                "_last_applied_grain_state",
                                None
                            )
                        )
                    )

                    try:
                        current_state = (
                            grain_controller
                            ._get_current_grain_state()
                        )

                        add(
                            "Current grain checkbox state: %s"
                            % str(current_state)
                        )

                    except Exception:
                        add(
                            "Current grain checkbox state: "
                            "<unavailable>"
                        )

            except Exception:
                add(
                    "Panel grain state: <unavailable>"
                )

            # ---------------------------------------------------------
            # Current FreeCAD selection
            # ---------------------------------------------------------

            add("")
            add("=" * 100)
            add("FREECAD SELECTION")
            add("=" * 100)

            try:
                selection = Gui.Selection.getSelection()

                add(
                    "Selected objects: %d"
                    % len(selection)
                )

                for index, selected_obj in enumerate(
                    selection
                ):
                    add(
                        "  %d: Name=%s, Label=%s, Document=%s"
                        % (
                            index,
                            str(
                                getattr(
                                    selected_obj,
                                    "Name",
                                    "<unknown>"
                                )
                            ),
                            str(
                                getattr(
                                    selected_obj,
                                    "Label",
                                    "<unknown>"
                                )
                            ),
                            str(
                                getattr(
                                    getattr(
                                        selected_obj,
                                        "Document",
                                        None
                                    ),
                                    "Name",
                                    "<unknown>"
                                )
                            ),
                        )
                    )

            except Exception:
                add(
                    "Selection: <unavailable>"
                )

            # ---------------------------------------------------------
            # Create popup dialog
            # ---------------------------------------------------------

            dialog = QtGui.QDialog(
                QtGui.QApplication.activeWindow()
            )

            dialog.setWindowTitle(
                "IP-Nesting Debug Export - Current State"
            )

            dialog.resize(
                1200,
                800
            )

            layout = QtGui.QVBoxLayout(
                dialog
            )

            info_label = QtGui.QLabel(
                "Detailed current-state debug. "
                "The report includes local and Placement-transformed "
                "geometry coordinates."
            )

            info_label.setWordWrap(True)
            layout.addWidget(info_label)

            text_edit = QtGui.QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setLineWrapMode(
                QtGui.QTextEdit.NoWrap
            )
            text_edit.setPlainText(
                "\n".join(lines)
            )

            layout.addWidget(
                text_edit
            )

            buttons_layout = QtGui.QHBoxLayout()

            copy_button = QtGui.QPushButton(
                "Copy"
            )

            save_button = QtGui.QPushButton(
                "Save debug text..."
            )

            close_button = QtGui.QPushButton(
                "Close"
            )

            buttons_layout.addWidget(
                copy_button
            )

            buttons_layout.addWidget(
                save_button
            )

            buttons_layout.addStretch()

            buttons_layout.addWidget(
                close_button
            )

            layout.addLayout(
                buttons_layout
            )

            def copy_debug_text():
                try:
                    QtGui.QApplication.clipboard().setText(
                        text_edit.toPlainText()
                    )
                except Exception:
                    App.Console.PrintError(
                        "Failed to copy debug text:\n"
                        + traceback.format_exc()
                    )

            def save_debug_text():
                try:
                    path, _ = QtGui.QFileDialog.getSaveFileName(
                        dialog,
                        "Save IP-Nesting debug text",
                        "",
                        "Text files (*.txt);;All files (*.*)"
                    )

                    if not path:
                        return

                    with open(
                        path,
                        "w",
                        encoding="utf-8"
                    ) as debug_file:
                        debug_file.write(
                            text_edit.toPlainText()
                        )

                    App.Console.PrintMessage(
                        "IP-Nesting debug text saved to: %s\n"
                        % path
                    )

                except Exception:
                    App.Console.PrintError(
                        "Failed to save debug text:\n"
                        + traceback.format_exc()
                    )

            copy_button.clicked.connect(
                copy_debug_text
            )

            save_button.clicked.connect(
                save_debug_text
            )

            close_button.clicked.connect(
                dialog.accept
            )

            dialog.exec_()

        except Exception:
            App.Console.PrintError(
                "debug_export_polygons failed:\n"
                + traceback.format_exc()
            )

            try:
                QtGui.QMessageBox.critical(
                    None,
                    "IP-Nesting Debug",
                    "Failed to create debug window:\n%s"
                    % traceback.format_exc()
                )
            except Exception:
                pass
    
    def create_input_in_layout(self,parent_layout,label,default,tooltip):
        row = QtGui.QHBoxLayout()

        label_widget = QtGui.QLabel(label)
        edit = QtGui.QLineEdit(default)
        edit.setToolTip(tooltip)

        row.addWidget(label_widget)
        row.addWidget(edit)

        parent_layout.addLayout(row)

        return edit, label_widget
    
    def _create_boolean_setting(
        self,
        parent_layout,
        label,
        default=False,
        tooltip=""
    ):
        row = QtGui.QHBoxLayout()

        label_widget = QtGui.QLabel(
            label
        )

        combo = QtGui.QComboBox()
        combo.addItems([
            "False",
            "True",
        ])

        combo.setCurrentIndex(
            1 if bool(default) else 0
        )

        if tooltip:
            label_widget.setToolTip(
                tooltip
            )
            combo.setToolTip(
                tooltip
            )

        row.addWidget(label_widget)
        row.addWidget(combo)

        parent_layout.addLayout(row)

        return combo
    
    def create_input(self, label, default, tooltip):
        row = QtGui.QHBoxLayout()
        edit = QtGui.QLineEdit(default)
        edit.setToolTip(tooltip)
        row.addWidget(QtGui.QLabel(label))
        row.addWidget(edit)
        self.layout.addLayout(row)
        return edit
        
    def _detect_cpu_core_count(self):
        """
        Return the number of logical CPU cores available to Python.

        The result is always at least 1. The UI later limits the
        selectable value to 16.
        """
        try:
            # Prefer the process affinity mask on supported systems.
            # This reflects cores actually available to the process.
            if hasattr(
                os,
                "sched_getaffinity"
            ):
                count = len(
                    os.sched_getaffinity(0)
                )
            else:
                count = os.cpu_count()

            if count is None:
                count = 1

            return max(
                1,
                int(count)
            )

        except Exception:
            try:
                return max(
                    1,
                    int(os.cpu_count() or 1)
                )
            except Exception:
                return 1


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
            self.bulk_angle_combo.setFixedWidth(100)
            htop.addWidget(self.bulk_angle_combo)

            self.bulk_axis_combo = QtGui.QComboBox()
            self.bulk_axis_combo.addItems(["X", "Y"])
            self.bulk_axis_combo.setCurrentIndex(0)
            self.bulk_axis_combo.setFixedWidth(85)
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
            self.bulk_grain_combo.setFixedWidth(70)
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
        """
        Open the Custom angle dialog only for rows where both
        Grain Direction and Custom angle are enabled.
        """
        try:
            grain_selected = False
            custom_angle_selected = False
            valid_arrow_names = []

            data_rows = (
                self.table.rowCount()
                - self.control_rows
            )

            for row in range(data_rows):
                try:
                    grain_widget = self.table.cellWidget(
                        row,
                        4
                    )

                    custom_angle_widget = self.table.cellWidget(
                        row,
                        5
                    )

                    grain_checkbox = None
                    custom_angle_checkbox = None

                    if grain_widget is not None:
                        grain_checkbox = grain_widget.findChild(
                            QtGui.QCheckBox
                        )

                    if custom_angle_widget is not None:
                        custom_angle_checkbox = (
                            custom_angle_widget.findChild(
                                QtGui.QCheckBox
                            )
                        )

                    grain_checked = bool(
                        grain_checkbox
                        and grain_checkbox.isChecked()
                    )

                    custom_angle_checked = bool(
                        custom_angle_checkbox
                        and custom_angle_checkbox.isChecked()
                    )

                    if grain_checked:
                        grain_selected = True

                    if custom_angle_checked:
                        custom_angle_selected = True

                    # Custom angle requires both checkboxes.
                    if not grain_checked or not custom_angle_checked:
                        continue

                    name_item = self.table.item(
                        row,
                        0
                    )

                    if not name_item:
                        continue

                    row_names = []

                    try:
                        names_data = name_item.data(
                            QtCore.Qt.UserRole + 1
                        )

                        if names_data:
                            if isinstance(
                                names_data,
                                list
                            ):
                                row_names = list(
                                    names_data
                                )
                            else:
                                row_names = json.loads(
                                    names_data
                                )

                            if not isinstance(
                                row_names,
                                list
                            ):
                                row_names = [
                                    row_names
                                ]

                    except Exception:
                        row_names = []

                    if not row_names:
                        try:
                            primary_name = name_item.data(
                                QtCore.Qt.UserRole
                            )

                            if primary_name:
                                row_names = [
                                    primary_name
                                ]

                        except Exception:
                            row_names = []

                    if not row_names:
                        continue

                    if self.preview_doc_name not in App.listDocuments():
                        continue

                    p_doc = App.getDocument(
                        self.preview_doc_name
                    )

                    if not p_doc:
                        continue

                    for object_name in row_names:
                        try:
                            arrow_name = (
                                "GrainArrow_"
                                + str(object_name)
                            )

                            if (
                                p_doc.getObject(arrow_name)
                                and arrow_name
                                not in valid_arrow_names
                            ):
                                valid_arrow_names.append(
                                    arrow_name
                                )

                        except Exception:
                            continue

                except Exception:
                    App.Console.PrintError(
                        "Failed to inspect Custom angle "
                        "selection in row %d:\n%s\n"
                        % (
                            row,
                            traceback.format_exc()
                        )
                    )

            # No Grain Direction selected at all.
            if not grain_selected:
                QtGui.QMessageBox.warning(
                    self.form,
                    "Custom angle",
                    "Select at least one part in the "
                    "'Grain Direction' column first."
                )
                return

            # Grain Direction is selected, but Custom angle is not.
            if not custom_angle_selected:
                QtGui.QMessageBox.warning(
                    self.form,
                    "Custom angle",
                    "To set a custom angle, select the "
                    "'Custom angle' checkbox for the part(s) "
                    "you want to modify."
                )
                return

            # Both checkboxes are selected, but no arrow exists.
            if not valid_arrow_names:
                QtGui.QMessageBox.warning(
                    self.form,
                    "Custom angle",
                    "No valid grain arrow was found for the "
                    "selected Custom angle part(s)."
                )
                return

            self._open_grain_angle_dialog_for_arrows(
                valid_arrow_names
            )

        except Exception:
            App.Console.PrintError(
                "_on_set_angle_clicked failed:\n"
                + traceback.format_exc()
            )
    
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
                    rot_item = QtGui.QTableWidgetItem("1")
                    rot_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.table.setItem(insert_pos, 2, rot_item)
                    # Column 3: Select for rotation (checkbox) -- center the checkbox
                    container_sel = QtGui.QWidget()
                    cell_layout_sel = QtGui.QHBoxLayout(container_sel)
                    cell_layout_sel.setContentsMargins(0, 0, 0, 0)  # small left/right margins
                    cell_layout_sel.setSpacing(0)
                    # center: add stretch both sides
                    cell_layout_sel.addStretch()
                    checkbox = QtGui.QCheckBox()
                    checkbox.setToolTip(
                        "Select which parts will be rotated in the XY plane.\n\n"
                        "When parts are added, the alignment algorithm selects the "
                        "largest face and turns it upward. For some parts, the intended "
                        "top face may be smaller than a side or bottom face.\n\n"
                        "Rotating a part by 90 degrees makes a side face become the "
                        "top face. Rotating it by 180 degrees makes the bottom face "
                        "become the top face."
                    )
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
                    grain_combo.setFixedWidth(70)
                    grain_layout.addWidget(grain_combo)
                    grain_layout.addStretch()
                    self.table.setCellWidget(insert_pos, 4, container_grain)

                    # Column 5: Custom angle checkbox
                    container_custom_angle = QtGui.QWidget()
                    custom_angle_layout = QtGui.QHBoxLayout(
                        container_custom_angle
                    )

                    custom_angle_layout.setContentsMargins(
                        0,
                        0,
                        0,
                        0
                    )

                    custom_angle_layout.setSpacing(0)
                    custom_angle_layout.addStretch()

                    custom_angle_cb = QtGui.QCheckBox()
                    custom_angle_cb.setToolTip(
                        "Enable Custom angle for this part. "
                        "The Set custom angle command can only modify parts "
                        "where both Grain Direction and Custom angle are enabled."
                    )

                    custom_angle_layout.addWidget(
                        custom_angle_cb
                    )

                    custom_angle_layout.addStretch()

                    self.table.setCellWidget(
                        insert_pos,
                        5,
                        container_custom_angle
                    )

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
        """
        Handle Qty and Rotation edits.

        Qty changes no longer create physical FreeCAD copies.
        Each table row keeps one preview object; the Qty value is
        exported separately as the requested nesting quantity.
        """
        try:
            if getattr(
                self,
                "_suppress_qty_update",
                False
            ):
                return

            if item is None:
                return

            row = item.row()
            col = item.column()

            # Ignore control rows.
            if row >= (
                self.table.rowCount()
                - self.control_rows
            ):
                return

            # Rotation degree column.
            if col == 2:
                self._clamp_rotation_cell(row)
                return

            # Only Qty column is handled below.
            if col != 1:
                return

            text = str(
                item.text()
            ).strip()

            try:
                quantity = int(text)
            except Exception:
                quantity = 0

            # Keep the existing allowed range.
            quantity = max(
                0,
                min(
                    5000,
                    quantity
                )
            )

            # Qty = 0 removes the complete table row and its
            # single preview object.
            if quantity == 0:
                name_item = self.table.item(
                    row,
                    0
                )

                names = []

                if name_item is not None:
                    try:
                        names_json = name_item.data(
                            QtCore.Qt.UserRole + 1
                        )

                        if names_json:
                            if isinstance(
                                names_json,
                                list
                            ):
                                names = list(
                                    names_json
                                )
                            else:
                                names = json.loads(
                                    names_json
                                )
                    except Exception:
                        names = []

                    if not names:
                        try:
                            primary_name = name_item.data(
                                QtCore.Qt.UserRole
                            )

                            if primary_name:
                                names = [
                                    primary_name
                                ]
                        except Exception:
                            pass

                try:
                    if names:
                        self.delete_preview_objects(
                            names
                        )
                except Exception:
                    App.Console.PrintError(
                        "Failed to delete preview object "
                        "when Qty was set to zero:\n"
                        + traceback.format_exc()
                    )

                try:
                    self.table.removeRow(row)
                except Exception:
                    App.Console.PrintError(
                        "Failed to remove table row:\n"
                        + traceback.format_exc()
                    )

                try:
                    if self.added_count < 0:
                        self.added_count = 0
                except Exception:
                    pass

                try:
                    self._update_apply_blink_state()
                except Exception:
                    pass

                return

            # Normalize the Qty cell without recursively triggering
            # this handler.
            normalized_text = str(
                quantity
            )

            if text != normalized_text:
                try:
                    self._suppress_qty_update = True
                    item.setText(
                        normalized_text
                    )
                finally:
                    self._suppress_qty_update = False

            # Keep exactly one preview object associated with the row.
            # The exported JSON reads the quantity from this table cell.
            name_item = self.table.item(
                row,
                0
            )

            if name_item is not None:
                names = []

                try:
                    names_json = name_item.data(
                        QtCore.Qt.UserRole + 1
                    )

                    if names_json:
                        if isinstance(
                            names_json,
                            list
                        ):
                            names = list(
                                names_json
                            )
                        else:
                            names = json.loads(
                                names_json
                            )
                except Exception:
                    names = []

                # Remove any legacy duplicate references from the
                # row, keeping only the first preview object.
                if names:
                    primary_name = names[0]

                    name_item.setData(
                        QtCore.Qt.UserRole,
                        primary_name
                    )

                    try:
                        name_item.setData(
                            QtCore.Qt.UserRole + 1,
                            json.dumps([
                                primary_name
                            ])
                        )
                    except Exception:
                        name_item.setData(
                            QtCore.Qt.UserRole + 1,
                            [
                                primary_name
                            ]
                        )

            try:
                self._update_apply_blink_state()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError(
                "on_item_changed failed:\n"
                + traceback.format_exc()
            )

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
                                # The final Apply Grain layout normalizes the grain direction
                                # to global X, therefore the displayed arrow must be horizontal.
                                GrainPreparer.update_grain_arrow(
                                    self.preview_doc_name,
                                    obj_name,
                                    enable=True,
                                    axis="X"
                                )
                            else:
                                GrainPreparer.remove_grain_arrow(
                                    self.preview_doc_name,
                                    obj_name
                                )
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
                    
                try:
                    w3 = self.table.cellWidget(r, 5)
                    if w3 is not None:
                        w3.setParent(None)
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
        """
        Generate input.json after validating that sheets and parts exist.
        """
        try:
            data_rows = max(
                0,
                self.table.rowCount() - self.control_rows
            )

            sheet_count = len(
                getattr(self, "offcuts", []) or []
            )

            missing_items = []

            if sheet_count == 0:
                missing_items.append(
                    "At least one sheet or offcut must be added."
                )

            if data_rows == 0:
                missing_items.append(
                    "At least one part must be added."
                )

            if missing_items:
                message = "\n".join(
                    "- %s" % item
                    for item in missing_items
                )

                QtGui.QMessageBox.warning(
                    self.form,
                    "Cannot start nesting",
                    "Cannot start nesting until:\n\n%s"
                    % message
                )

                return

            script_dir = os.path.abspath(
                os.path.dirname(__file__)
            )

            input_path = os.path.join(
                script_dir,
                "input.json"
            )

            if os.path.exists(input_path):
                try:
                    os.remove(input_path)
                except Exception:
                    pass

            generation_ok = execute_nesting_impl(self)

            if generation_ok is not True:
                return

            if not os.path.exists(input_path):
                QtGui.QMessageBox.critical(
                    self.form,
                    "Input generation failed",
                    "The input.json file was not created."
                )

                return

            QtGui.QMessageBox.information(
                self.form,
                "Input generated",
                "The input.json file was generated successfully."
            )

        except Exception:
            App.Console.PrintError(
                "execute_nesting failed:\n"
                + traceback.format_exc()
            )

            QtGui.QMessageBox.critical(
                self.form,
                "Input generation error",
                "Failed to generate input.json."
            )

    def getStandardButtons(self):
        buttons = QtGui.QDialogButtonBox.Cancel
        try:
            # PySide6 enum values require explicit access through .value
            return int(buttons.value)
        except AttributeError:
            # Compatibility with older PySide/FreeCAD versions
            return int(buttons)
        
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

    def _parse_decimal_input(self, text):
        """
        Parse decimal input using dot or comma.

        Fractions such as 1/4 are deliberately rejected.
        """
        try:
            value = str(text or "").strip()

            if not value:
                return None

            if "/" in value:
                return None

            value = value.replace(",", ".")

            return float(value)

        except Exception:
            return None

    def _format_dimension(self, value):
        """
        Format a dimension in the currently selected display units.

        Internal value is always millimetres.
        """
        try:
            value_mm = float(value)

            if self.display_units == "inch":
                value_display = value_mm / MM_PER_INCH
            else:
                value_display = value_mm

            # Keep enough precision for values such as 12.9
            # and smaller custom values.
            text = "%.6f" % value_display

            return (
                text.rstrip("0").rstrip(".")
                or "0"
            )

        except Exception:
            return "0"

    def _display_to_mm(self, value):
        """
        Convert a value from the active display unit to mm.
        """
        if self.display_units == "inch":
            return float(value) * MM_PER_INCH

        return float(value)

    def _mm_to_display(self, value_mm):
        """
        Convert a millimetre value to the active display unit.
        """
        if self.display_units == "inch":
            return float(value_mm) / MM_PER_INCH

        return float(value_mm)

    def _normalize_dimension_mm(
        self,
        key,
        value_mm
    ):
        """
        Normalize canonical dimension values stored in mm.
        Boundary resolution is kept to two decimal places.
        """
        value_mm = float(value_mm)

        if key == "boundary_resolution":
            return round(value_mm, 2)

        return value_mm
    
    def _dimension_field_key(self, line_edit):
        if line_edit is self.sheet_margin:
            return "sheet_margin"

        if line_edit is self.spacing:
            return "spacing"

        if line_edit is self.res:
            return "boundary_resolution"

        return None
    
    def _dimension_fields(self):
        """
        Return all dimension QLineEdit fields in the main panel.
        """
        return [
            self.sheet_margin,
            self.spacing,
            self.res,
        ]

    def _read_dimension_field_mm(self, line_edit):
        """
        Read one dimension field and return mm.
        """
        value = self._parse_decimal_input(
            line_edit.text()
        )

        if value is None or value < 0.0:
            return None

        return self._display_to_mm(value)

    def _write_dimension_field_mm(
        self,
        line_edit,
        value_mm
    ):
        """
        Store the canonical value in mm and display it
        using the current units.
        """
        key = self._dimension_field_key(
            line_edit
        )

        value_mm = self._normalize_dimension_mm(
            key,
            value_mm
        )

        if key is not None:
            self._dimension_values_mm[key] = (
                value_mm
            )

        line_edit.blockSignals(True)

        try:
            line_edit.setText(
                self._format_dimension(value_mm)
            )
        finally:
            line_edit.blockSignals(False)

    def _update_dimension_labels(self):
        suffix = (
            "inch"
            if self.display_units == "inch"
            else "mm"
        )

        self.sheet_margin_label.setText(
            "Sheet Margin (%s):" % suffix
        )

        self.spacing_label.setText(
            "Part Spacing (%s):" % suffix
        )

        self.res_label.setText(
            "Boundary Resolution (%s):" % suffix
        )
    
    def _on_units_changed(self, index):
        """
        Change display units using canonical mm values.

        Values are never converted from the current display text.
        This prevents mm/inch conversion drift.
        """
        if self._units_change_guard:
            return

        try:
            new_units = (
                "inch"
                if int(index) == 1
                else "mm"
            )

            if new_units == self.display_units:
                return

            self._units_change_guard = True

            try:
                self.display_units = new_units

                for field in self._dimension_fields():
                    key = self._dimension_field_key(
                        field
                    )

                    if key is None:
                        continue

                    value_mm = self._dimension_values_mm.get(
                        key
                    )

                    if value_mm is None:
                        continue

                    self._write_dimension_field_mm(
                        field,
                        value_mm
                    )

                self._update_dimension_labels()

                # Save only the unit selection.
                self._save_settings_to_prefs()

                try:
                    self.offcut_controller.\
                        _refresh_offcut_material_labels()
                except Exception:
                    pass
                
                popup = getattr(
                    self,
                    "_active_offcut_dialog",
                    None
                )

                if popup is not None:
                    try:
                        popup.set_display_units(
                            self.display_units
                        )
                    except Exception:
                        pass

            finally:
                self._units_change_guard = False

        except Exception:
            self._units_change_guard = False

            App.Console.PrintError(
                "_on_units_changed failed:\n"
                + traceback.format_exc()
            )
    
    def get_dimension_value_mm(
        self,
        line_edit,
        default_mm=0.0
    ):
        """
        Read one visible dimension and return its value in mm.
        """
        try:
            value = self._parse_decimal_input(
                line_edit.text()
            )

            if value is None or value < 0.0:
                return float(default_mm)

            return self._display_to_mm(value)

        except Exception:
            return float(default_mm)
    
    def _load_deepnest_settings(self, prefs):
        text_fields = {
            "DeepnestTimeRatio": (
                self.deepnest_time_ratio,
                "0.5"
            ),
            "DeepnestPopulationSize": (
                self.deepnest_population_size,
                "10"
            ),
            "DeepnestMutationRate": (
                self.deepnest_mutation_rate,
                "10"
            ),
            "DeepnestExportSheetsSpaceValue": (
                self.deepnest_export_sheets_space_value,
                "0.13888"
            ),
        }

        for key, data in text_fields.items():
            widget, default = data

            try:
                widget.setText(
                    str(
                        prefs.GetString(
                            key,
                            default
                        )
                    )
                )
            except Exception:
                widget.setText(
                    default
                )

        boolean_fields = {
            "DeepnestExportWithSheetBoundaries": (
                self.deepnest_export_sheet_boundaries,
                False
            ),
            "DeepnestExportWithSheetsSpace": (
                self.deepnest_export_sheets_space,
                False
            ),
        }

        for key, data in boolean_fields.items():
            widget, default = data

            try:
                value = prefs.GetBool(
                    key,
                    bool(default)
                )

                widget.setCurrentIndex(
                    1 if value else 0
                )

            except Exception:
                widget.setCurrentIndex(
                    1 if default else 0
                )
    
    def _load_settings_from_prefs(self):
        self.display_units = "mm"
        p = self._prefs()
        if not p:
            return
        try:
            # block signals while setting values
            widgets = [
                self.sheet_margin,
                self.spacing,
                self.res,
                self.units_combo,
                self.placement_strategy,
                self.cpu_cores_combo,
                self.deepnest_export_sheet_boundaries,
                self.deepnest_export_sheets_space,
                self.deepnest_export_sheets_space_value,
                self.deepnest_time_ratio,
                self.deepnest_population_size,
                self.deepnest_mutation_rate,
            ]
            for w in widgets:
                try:
                    w.blockSignals(True)
                except Exception:
                    pass
            
            # Load canonical dimension values from preferences.
            # Preferences always store values in millimetres.
            try:
                sheet_margin_mm = float(
                    str(
                        p.GetString(
                            "SheetMargin",
                            "5.0"
                        )
                    ).replace(",", ".")
                )
            except Exception:
                sheet_margin_mm = 5.0

            try:
                spacing_mm = float(
                    str(
                        p.GetString(
                            "PartSpacing",
                            "6.0"
                        )
                    ).replace(",", ".")
                )
            except Exception:
                spacing_mm = 6.0

            try:
                boundary_resolution_mm = float(
                    str(
                        p.GetString(
                            "BoundaryResolution",
                            "0.1"
                        )
                    ).replace(",", ".")
                )
            except Exception:
                boundary_resolution_mm = 0.1

            self._dimension_values_mm = {
                "sheet_margin": (
                    self._normalize_dimension_mm(
                        "sheet_margin",
                        sheet_margin_mm
                    )
                ),
                "spacing": (
                    self._normalize_dimension_mm(
                        "spacing",
                        spacing_mm
                    )
                ),
                "boundary_resolution": (
                    self._normalize_dimension_mm(
                        "boundary_resolution",
                        boundary_resolution_mm
                    )
                ),
            }
            
            sheet_margin_mm = (
                self._dimension_values_mm[
                    "sheet_margin"
                ]
            )

            spacing_mm = (
                self._dimension_values_mm[
                    "spacing"
                ]
            )

            boundary_resolution_mm = (
                self._dimension_values_mm[
                    "boundary_resolution"
                ]
            )

            # Load saved display units.
            saved_units = str(
                p.GetString(
                    "DisplayUnits",
                    "mm"
                )
            ).strip().lower()

            if saved_units == "inch":
                self.display_units = "inch"
                self.units_combo.setCurrentIndex(1)
            else:
                self.display_units = "mm"
                self.units_combo.setCurrentIndex(0)

            # Display the stored mm values in the selected units.
            self.sheet_margin.setText(
                self._format_dimension(
                    sheet_margin_mm
                )
            )

            self.spacing.setText(
                self._format_dimension(
                    spacing_mm
                )
            )

            self.res.setText(
                self._format_dimension(
                    boundary_resolution_mm
                )
            )
            
            self._load_deepnest_settings(p)
            
            try:
                self.placement_strategy.setCurrentIndex(int(p.GetInt("PlacementStrategyIndex",self.placement_strategy.currentIndex())))
            except Exception:
                pass
            try:
                saved_cpu_cores = int(
                    p.GetInt(
                        "CpuCores",
                        self.cpu_cores_combo.currentText()
                    )
                )

                max_index = (
                    self.cpu_cores_combo.count()
                    - 1
                )

                saved_cpu_cores = max(
                    1,
                    min(
                        saved_cpu_cores,
                        max_index + 1
                    )
                )

                self.cpu_cores_combo.setCurrentText(
                    str(saved_cpu_cores)
                )

            except Exception:
                pass


        finally:
            for w in widgets:
                try:
                    w.blockSignals(False)
                except Exception:
                    pass
            self._update_dimension_labels()
    
    def _save_deepnest_settings(self, prefs):
        text_fields = {
            "DeepnestTimeRatio": (
                self.deepnest_time_ratio
            ),
            "DeepnestPopulationSize": (
                self.deepnest_population_size
            ),
            "DeepnestMutationRate": (
                self.deepnest_mutation_rate
            ),
            "DeepnestExportSheetsSpaceValue": (
                self.deepnest_export_sheets_space_value
            ),
        }

        for key, widget in text_fields.items():
            try:
                prefs.SetString(
                    key,
                    widget.text().strip()
                )
            except Exception:
                pass

        boolean_fields = {
            "DeepnestExportWithSheetBoundaries": (
                self.deepnest_export_sheet_boundaries
            ),
            "DeepnestExportWithSheetsSpace": (
                self.deepnest_export_sheets_space
            ),
        }

        for key, widget in boolean_fields.items():
            try:
                prefs.SetBool(
                    key,
                    widget.currentIndex() == 1
                )
            except Exception:
                pass
    
    def _save_settings_to_prefs(self):
        p = self._prefs()

        if not p:
            return

        try:
            p.SetString(
                "SheetMargin",
                "%.6f" % self._dimension_values_mm[
                    "sheet_margin"
                ]
            )

            p.SetString(
                "PartSpacing",
                "%.6f" % self._dimension_values_mm[
                    "spacing"
                ]
            )

            boundary_resolution_mm = (
                self._normalize_dimension_mm(
                    "boundary_resolution",
                    self._dimension_values_mm[
                        "boundary_resolution"
                    ]
                )
            )

            self._dimension_values_mm[
                "boundary_resolution"
            ] = boundary_resolution_mm

            p.SetString(
                "BoundaryResolution",
                "%.2f" % boundary_resolution_mm
            )

            p.SetString(
                "DisplayUnits",
                self.display_units
            )

            p.SetInt(
                "PlacementStrategyIndex",
                int(
                    self.placement_strategy.currentIndex()
                )
            )

            try:
                p.SetInt(
                    "CpuCores",
                    int(
                        self.cpu_cores_combo.currentText()
                    )
                )
            except Exception:
                p.SetInt(
                    "CpuCores",
                    1
                )
                
            self._save_deepnest_settings(p)

        except Exception:
            App.Console.PrintError(
                "_save_settings_to_prefs failed:\n"
                + traceback.format_exc()
            )

    def _update_dimension_value_from_field(
        self,
        line_edit
    ):
        """
        Read one field in the current display units
        and save its canonical value in mm.
        """
        try:
            key = self._dimension_field_key(
                line_edit
            )

            if key is None:
                return

            value = self._parse_decimal_input(
                line_edit.text()
            )

            if value is None or value < 0.0:
                return

            value_mm = self._display_to_mm(
                value
            )

            self._dimension_values_mm[key] = (
                self._normalize_dimension_mm(
                    key,
                    value_mm
                )
            )

        except Exception:
            App.Console.PrintError(
                "_update_dimension_value_from_field failed:\n"
                + traceback.format_exc()
            )

    def get_boundary_resolution_mm(self):
        return float(
            self._dimension_values_mm.get(
                "boundary_resolution",
                0.1
            )
        )

    def _normalize_decimal_field(self, line_edit):
        """
        Normalize one field and update its canonical mm value.
        """
        try:
            if line_edit is None:
                return

            text = str(
                line_edit.text()
            ).strip()

            if not text:
                return

            normalized = text.replace(",", ".")

            if normalized != text:
                cursor_pos = line_edit.cursorPosition()

                line_edit.blockSignals(True)

                try:
                    line_edit.setText(
                        normalized
                    )
                    line_edit.setCursorPosition(
                        min(
                            cursor_pos,
                            len(normalized)
                        )
                    )
                finally:
                    line_edit.blockSignals(False)

            self._update_dimension_value_from_field(
                line_edit
            )

        except Exception:
            App.Console.PrintError(
                "_normalize_decimal_field failed:\n"
                + traceback.format_exc()
            )
    
    def _connect_settings_persistence(self):
        # Save on change
        try:
            # Decimal fields: normalize only the field being edited,
            # then save preferences.
            for le in [
                self.sheet_margin,
                self.spacing,
                self.res,
            ]:
                try:
                    le.editingFinished.connect(
                        partial(
                            self._normalize_decimal_field,
                            le
                        )
                    )
                except Exception:
                    pass

                try:
                    le.editingFinished.connect(
                        self._save_settings_to_prefs
                    )
                except Exception:
                    pass

            # combos
            try:
                self.placement_strategy.currentIndexChanged.connect(self._save_settings_to_prefs)
            except Exception:
                pass
            try:
                self.cpu_cores_combo.currentIndexChanged.connect(
                    self._save_settings_to_prefs
                )
            except Exception:
                pass
                
            for widget in [
                self.deepnest_export_sheet_boundaries,
                self.deepnest_export_sheets_space,
                self.deepnest_export_sheets_space_value,
                self.deepnest_time_ratio,
                self.deepnest_population_size,
                self.deepnest_mutation_rate,
            ]:
                try:
                    if isinstance(
                        widget,
                        QtGui.QComboBox
                    ):
                        widget.currentIndexChanged.connect(
                            self._save_settings_to_prefs
                        )
                    else:
                        widget.editingFinished.connect(
                            self._save_settings_to_prefs
                        )
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

            rot_item = QtGui.QTableWidgetItem("1")
            rot_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(insert_pos, 2, rot_item)

            # Column 3: Select for rotation checkbox (centered)
            container_sel = QtGui.QWidget()
            cell_layout_sel = QtGui.QHBoxLayout(container_sel)
            cell_layout_sel.setContentsMargins(0, 0, 0, 0)
            cell_layout_sel.setSpacing(0)
            cell_layout_sel.addStretch()
            checkbox = QtGui.QCheckBox()
            checkbox.setToolTip(
                "Select which parts will be rotated in the XY plane.\n\n"
                "When parts are added, the alignment algorithm selects the "
                "largest face and turns it upward. For some parts, the intended "
                "top face may be smaller than a side or bottom face.\n\n"
                "Rotating a part by 90 degrees makes a side face become the "
                "top face. Rotating it by 180 degrees makes the bottom face "
                "become the top face."
            )
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
            grain_combo.setFixedWidth(70)
            grain_layout.addWidget(grain_combo)
            grain_layout.addStretch()
            self.table.setCellWidget(insert_pos, 4, container_grain)

            # Column 5: Custom angle checkbox
            container_custom_angle = QtGui.QWidget()
            custom_angle_layout = QtGui.QHBoxLayout(
                container_custom_angle
            )

            custom_angle_layout.setContentsMargins(
                0,
                0,
                0,
                0
            )

            custom_angle_layout.setSpacing(0)
            custom_angle_layout.addStretch()

            custom_angle_cb = QtGui.QCheckBox()
            custom_angle_cb.setToolTip(
                "Enable Custom angle for this part. "
                "The Set custom angle command can only modify parts "
                "where both Grain Direction and Custom angle are enabled."
            )

            custom_angle_layout.addWidget(
                custom_angle_cb
            )

            custom_angle_layout.addStretch()

            self.table.setCellWidget(
                insert_pos,
                5,
                container_custom_angle
            )
            
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
    
    def _clamp_rotation_degrees_text(self, txt):
        """
        Normalize the rotation count field.

        The value represents the number of allowed Deepnest rotation states,
        not a comma-separated list of angles.
        """
        try:
            if txt is None:
                return "1"

            value = str(txt).strip()

            if not value:
                return "1"

            # Accept decimal-looking input but store an integer.
            rotations = int(float(value))

            # Keep the existing broad range for now.
            # The Deepnest maximum can be restricted later.
            rotations = max(1, min(5000, rotations))

            return str(rotations)

        except Exception:
            return "1"


    def _clamp_rotation_cell(self, row):
        """Normalize the rotation count cell for a given data row."""
        try:
            if row is None:
                return
            # ignore control rows
            if row >= self.table.rowCount() - self.control_rows:
                return
            item = self.table.item(row, 2)
            if not item:
                return

            old = item.text()
            new = self._clamp_rotation_degrees_text(old)
            if new != old:
                # prevent recursive triggers via itemChanged
                try:
                    self._suppress_qty_update = True  # reuse existing suppression flag
                    item.setText(new)
                finally:
                    self._suppress_qty_update = False
        except Exception:
            pass