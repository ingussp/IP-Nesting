from IPNestingLanguages import tr, ui_call, ui_widget, register_window
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
from IPNestingExport import execute_nesting as execute_nesting_impl, normalize_rotation_text
from IPNestingGrainUI import GrainUIController
from IPNestingPreviewDoc import PreviewDocManager
from IPNestingGrainAngleDialog import GrainAngleDialog
from IPNestingImport2D import import_dxf_to_preview, import_svg_to_preview
from IPNestingOffcutShowDialog import OffcutMaterialsController
from IPNestingResult import NestingProcessManager

MM_PER_INCH = 25.4


try:
    from IPNestingImport import apply_nesting_result
except Exception:
    try:
        from .IPNestingImport import apply_nesting_result
    except Exception:
        App.Console.PrintError(tr('failed_to_import_ipnestingimport') + traceback.format_exc())
        apply_nesting_result = None
try:
    from IPNestingImportSheets import import_nesting_sheets
except Exception:
    try:
        from .IPNestingImportSheets import import_nesting_sheets
    except Exception:
        App.Console.PrintError(tr('failed_to_import_ipnestingimportsheets') + traceback.format_exc())
        import_nesting_sheets = None


try:
    from IPNestingRotate import NestingRotator
except Exception:
    try:
        from .IPNestingRotate import NestingRotator
    except Exception:
        App.Console.PrintError(tr('failed_to_import_nestingrotator_ipnestingrotate_py') + traceback.format_exc())
        NestingRotator = None

# Grain preparer integration (attempt import; fallback to None)
try:
    from IPNestingGrain import GrainPreparer
except Exception:
    GrainPreparer = None

# Coordinate nesting settings, material/part tables, preview editing and nesting CLI execution.
class NestingTaskPanel:
    # Synchronize preview selection with table rows and unregister when the table is destroyed.
    class _SelectionObserver:
        # Store the panel reference and mark the selection observer as active.
        def __init__(self, panel):
            self.panel = panel
            self._alive = True

        # Remove this selection observer once and mark it inactive.
        def _unregister(self):
            if self._alive:
                try:
                    Gui.Selection.removeObserver(self)
                except Exception:
                    pass
                finally:
                    self._alive = False

        # Check whether the panel table is usable and unregister after its Qt object is
        # destroyed.
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

        # Select and scroll to the row whose primary object matches the added preview selection.
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
                            App.Console.PrintError(tr('selectionobserver_addselection_per_row_error') + traceback.format_exc())
            except Exception:
                App.Console.PrintError(tr('selectionobserver_addselection_error') + traceback.format_exc())

        # Attempt to clear table selection when no preview objects remain selected.
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
                App.Console.PrintError(tr('selectionobserver_removeselection_error') + traceback.format_exc())

        # Clear table selection when FreeCAD clears selection in the preview document.
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
                App.Console.PrintError(tr('selectionobserver_clearselection_error') + traceback.format_exc())

    # Build the task panel, create its controllers and restore saved settings.
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
        sheet_box = ui_widget(QtGui.QGroupBox, tr('sheet_settings'))
        sheet_lay = QtGui.QVBoxLayout(sheet_box)

        self.sheet_margin, self.sheet_margin_label = (
            self.create_input_in_layout(
                sheet_lay,
                tr('sheet_margin_mm'),
                "5.00",
                tr('distance_from_the_sheet_edge')
            )
        )

        self.spacing, self.spacing_label = (
            self.create_input_in_layout(
                sheet_lay,
                tr('part_spacing_mm'),
                "6.00",
                tr('minimum_distance_between_parts')
            )
        )

        # NEW: Offcuts (DXF) (LEFT, row 1)
        offcut_box = ui_widget(QtGui.QGroupBox, tr('sheet_offcut_materials'))
        offcut_lay = QtGui.QVBoxLayout(offcut_box)

        self.offcuts_table = QtGui.QTableWidget(0, 4)
        ui_call(self.offcuts_table, 'setHorizontalHeaderLabels', [
            tr('material'),
            tr('count'),
            tr('grain'),
            tr('move'),
        ])
        ui_call(self.offcuts_table.horizontalHeaderItem(1), 'setToolTip', tr('number_of_sheets_or_offcuts'))
        
        self.offcuts_table.setSelectionMode(QtGui.QAbstractItemView.SingleSelection)
        self.offcuts_table.setEditTriggers( QtGui.QAbstractItemView.DoubleClicked | QtGui.QAbstractItemView.EditKeyPressed)
        ui_call(self.offcuts_table, 'setToolTip', tr('add_rectangular_sheets_or_dxf_offcuts_for_nesting'))
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
        self.offcut_add_btn = ui_widget(QtGui.QPushButton, tr('add'))
        self.offcut_show_btn = ui_widget(QtGui.QPushButton, tr('show'))
        self.offcut_remove_btn = ui_widget(QtGui.QPushButton, tr('remove'))
        ui_call(self.offcut_add_btn, 'setToolTip', tr('add_a_rectangular_sheet_or_a_dxf_offcut'))
        ui_call(self.offcut_show_btn, 'setToolTip', tr('show_all_added_offcuts_and_adjust_grain_x_y_per_offcut'))
        ui_call(self.offcut_remove_btn, 'setToolTip', tr('remove_the_selected_material_from_the_list'))
        self.offcut_add_btn.clicked.connect(self.offcut_controller.add_offcut_dxf)
        self.offcut_show_btn.clicked.connect(self.offcut_controller.show_offcuts_popup)
        self.offcut_remove_btn.clicked.connect(self.offcut_controller.remove_offcuts)
        off_btns.addWidget(self.offcut_add_btn)
        off_btns.addWidget(self.offcut_show_btn)
        off_btns.addWidget(self.offcut_remove_btn)
        off_btns.addStretch()
        offcut_lay.addLayout(off_btns)

        # General Parameters (LEFT, row 2)  (shifted down by 1)
        general_box = ui_widget(QtGui.QGroupBox, tr('general_parameters'))
        general_lay = QtGui.QVBoxLayout(general_box)
        self.res, self.res_label = (
            self.create_input_in_layout(
                general_lay,
                tr('boundary_resolution_mm'),
                "0.1",
                tr('maximum_deviation_used_when_curved_geometry_is_converted_to_line_segments_smaller_values_c')
            )
        )

        # Display Units (RIGHT, row 0)
        units_box = ui_widget(QtGui.QGroupBox, tr('units'))
        units_lay = QtGui.QVBoxLayout(units_box)

        self.units_combo = QtGui.QComboBox()
        ui_call(self.units_combo, 'addItems', [
            "mm",
            "inch",
        ])
        self.units_combo.setCurrentIndex(0)
        ui_call(
            self.units_combo, 'setToolTip', tr('display_and_input_units_for_dimensions_internal_geometry_remains_in_millimetres')
        )

        units_lay.addWidget(self.units_combo)

        self.units_combo.currentIndexChanged.connect(
            self._on_units_changed
        )

        # CPU Cores (RIGHT, row 3)
        cpu_box = ui_widget(
            QtGui.QGroupBox, tr('cpu_cores')
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

        ui_call(
            self.cpu_cores_combo, 'setToolTip', tr('number_of_cpu_worker_cores_available_to_the_nesting_calculation_the_list_is_based_on_the_l')
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
        self.layout.addWidget(ui_widget(QtGui.QLabel, tr('b_selected_parts_preview_mode_b')))
        self.table = QtGui.QTableWidget(self.control_rows, 6)  # reserve control_rows initially
        ui_call(self.table, 'setHorizontalHeaderLabels', [
            tr('body'), tr('qty'), tr('rotations'), tr('select_for_rotation'), tr('grain_direction'), tr('custom_angle')
        ])
        try:
            ui_call(
                self.table.horizontalHeaderItem(3), 'setToolTip', tr('select_which_parts_will_be_rotated_in_the_xy_plane_when_parts_are_added_the_alignment_algo')
            )
        except Exception:
            pass
        try:
            ui_call(
                self.table.horizontalHeaderItem(5), 'setToolTip', tr('enable_this_checkbox_to_allow_the_set_custom_angle_command_to_modify_this_part_grain_direc')
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
        
        # Manages nesting CLI execution, result.json waiting,
        # and Nesting_Result document creation.
        self._nesting_manager = NestingProcessManager(self)

        # Add / Remove buttons (larger, with top/bottom margin 5px)
        self.btn_layout = QtGui.QHBoxLayout()
        self.btn_layout.setContentsMargins(0, 5, 0, 5)
        self.add_btn = ui_widget(QtGui.QPushButton, tr('add_selected'))
        self.rem_btn = ui_widget(QtGui.QPushButton, tr('remove_selected'))
        self.add_btn.setFixedHeight(32)
        self.rem_btn.setFixedHeight(32)
        self.add_btn.setMinimumWidth(140)
        self.rem_btn.setMinimumWidth(140)
        self.add_btn.clicked.connect(self.add_selected_objects)
        # Remove Selected behaves like Qty -> 0 for selected rows
        self.rem_btn.clicked.connect(self.remove_selected_rows)

        # Import 2D buttons (DXF/SVG)
        self.import_dxf_btn = ui_widget(QtGui.QPushButton, tr('import_dxf'))
        self.import_svg_btn = ui_widget(QtGui.QPushButton, tr('import_svg'))
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
        self.run_btn = ui_widget(QtGui.QPushButton, tr('run_nesting'))
        self.run_btn.setStyleSheet("background-color: #CF3519; color: white; font-weight: bold; height: 35px;")
        self.run_btn.clicked.connect(self.execute_nesting)
        self.layout.addWidget(self.run_btn)
        
        self.debug_export_btn = ui_widget(QtGui.QPushButton, tr('debug_export_polygons'))
        ui_call(self.debug_export_btn, 'setToolTip', tr('draw_exported_polygons_in_a_separate_document_to_inspect_what_is_sent_to_the_exe'))
        self.debug_export_btn.clicked.connect(self.debug_export_polygons)
        self.layout.addWidget(self.debug_export_btn)
        
        self._grain_angle_dialog_open = False

        try:
            self._selection_observer = NestingTaskPanel._SelectionObserver(self)
            Gui.Selection.addObserver(self._selection_observer)
        except Exception:
            App.Console.PrintError(tr('failed_to_add_selection_observer') + traceback.format_exc())

        self._load_settings_from_prefs()
        self._connect_settings_persistence()
        
        # Initialize preview document manager
        self._preview = PreviewDocManager(self)

        # Initialize nesting CLI process/result manager
        self._nesting_manager = NestingProcessManager(
            self
        )
        register_window(self.form)

    # Recompute the preview and display a textual diagnostic report.
    def debug_export_polygons(self):
        """
        Recompute the preview and display a textual diagnostic report.

        Includes placements, shape topology, table state and selection, with
        clipboard and file-save actions. Does not generate input.json or
        explicitly change object placements.
        """
        try:
            lines = []

            # Append one stringified line to the diagnostic report.
            def add(text=""):
                lines.append(str(text))

            # Format a numeric diagnostic value to nine decimal places, otherwise stringify it.
            def safe_float(value):
                try:
                    return "%.9f" % float(value)
                except Exception:
                    return str(value)

            # Format vector components using lowercase or uppercase coordinate attributes.
            def vector_text(vector):
                try:
                    return (
                        tr('x_s_y_s_z_s')
                        % (
                            safe_float(vector.x),
                            safe_float(vector.y),
                            safe_float(vector.z),
                        )
                    )
                except Exception:
                    try:
                        return (
                            tr('x_s_y_s_z_s_b630f9')
                            % (
                                safe_float(vector.X),
                                safe_float(vector.Y),
                                safe_float(vector.Z),
                            )
                        )
                    except Exception:
                        return str(vector)

            # Format a rotation axis and angle in radians and degrees.
            def rotation_text(rotation):
                try:
                    axis = rotation.Axis
                    angle = rotation.Angle

                    try:
                        angle_deg = float(angle) * 180.0 / math.pi
                    except Exception:
                        angle_deg = angle

                    return (
                        tr('axis_s_angle_rad_s_angle_deg_s')
                        % (
                            vector_text(axis),
                            safe_float(angle),
                            safe_float(angle_deg),
                        )
                    )
                except Exception:
                    return str(rotation)

            # Format a placement translation and rotation for the report.
            def placement_text(placement):
                try:
                    return (
                        tr('base_s_rotation_s')
                        % (
                            vector_text(placement.Base),
                            rotation_text(placement.Rotation),
                        )
                    )
                except Exception:
                    return str(placement)

            # Format an XY point pair for the report.
            def point2d_text(point):
                try:
                    return (
                        tr('x_s_y_s')
                        % (
                            safe_float(point[0]),
                            safe_float(point[1]),
                        )
                    )
                except Exception:
                    return str(point)

            # Format the lowercase XYZ attributes of a point for the report.
            def point3d_text(point):
                try:
                    return (
                        tr('x_s_y_s_z_s')
                        % (
                            safe_float(point.x),
                            safe_float(point.y),
                            safe_float(point.z),
                        )
                    )
                except Exception:
                    return str(point)

            # Apply obj.Placement to the supplied point for diagnostic comparison.
            def get_transformed_point(obj, point):
                """
                Apply obj.Placement to the supplied point for diagnostic comparison.

                The caller must know the point coordinate frame; Shape points may
                already include placement, so this is not necessarily the visible point.
                """
                try:
                    return obj.Placement.multVec(point)
                except Exception:
                    return None

            # Format bounding-box extrema and dimensions for the report.
            def bbox_text(bbox):
                try:
                    return (
                        tr('xmin_s_xmax_s_ymin_s_ymax_s_zmin_s_zmax_s_width_s_height_s_depth_s')
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

            # List all exposed object properties and mark unreadable values.
            def property_text(obj):
                try:
                    properties = obj.PropertiesList
                except Exception:
                    properties = []

                if not properties:
                    return tr('no_custom_properties')

                result = []

                for property_name in properties:
                    try:
                        value = getattr(obj, property_name)
                        result.append(
                            tr('s_s')
                            % (
                                property_name,
                                str(value),
                            )
                        )
                    except Exception:
                        result.append(
                            tr('s_unreadable')
                            % property_name
                        )

                return "\n".join(result)

            # Report topology and vertices as read from Shape and after applying Placement.
            def dump_shape(obj):
                """
                Report topology and vertices as read from Shape and after applying Placement.

                The extra transformation is diagnostic and can reapply an existing placement.
                """
                shape = getattr(obj, "Shape", None)

                if shape is None:
                    add(tr('shape_none'))
                    return

                add(tr('shape_object_s') % str(shape))

                try:
                    add(tr('shape_isnull_s') % str(shape.isNull()))
                except Exception:
                    add(tr('shape_isnull_unavailable'))

                try:
                    add(tr('shape_volume_s') % safe_float(shape.Volume))
                except Exception:
                    add(tr('shape_volume_unavailable'))

                try:
                    add(tr('shape_area_s') % safe_float(shape.Area))
                except Exception:
                    add(tr('shape_area_unavailable'))

                try:
                    add(
                        tr('topology_counts_solids_d_shells_d_faces_d_wires_d_edges_d_vertices_d')
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
                    add(tr('topology_counts_unavailable'))

                try:
                    local_bbox = shape.BoundBox
                    add(tr('shape_local_boundbox'))
                    add("  " + bbox_text(local_bbox))
                except Exception:
                    add(tr('shape_local_boundbox_unavailable'))

                try:
                    vertices = list(
                        getattr(shape, "Vertexes", []) or []
                    )

                    add(
                        tr('vertices_d')
                        % len(vertices)
                    )

                    if not vertices:
                        add(tr('none'))

                    for index, vertex in enumerate(vertices):
                        try:
                            local_point = vertex.Point
                        except Exception:
                            add(
                                tr('vertex_d_point_unavailable')
                                % index
                            )
                            continue

                        transformed_point = get_transformed_point(
                            obj,
                            local_point
                        )

                        add(
                            tr('vertex_d_local_s_transformed_s')
                            % (
                                index,
                                point3d_text(local_point),
                                (
                                    point3d_text(transformed_point)
                                    if transformed_point is not None
                                    else tr('transformation_failed')
                                ),
                            )
                        )

                except Exception:
                    add(tr('vertices_unavailable'))

                try:
                    wires = list(
                        getattr(shape, "Wires", []) or []
                    )

                    add(
                        tr('wires_d')
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
                            tr('wire_d_closed_s_edges_d')
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
                                tr('edge_d_vertices_d')
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
                                    tr('v_d_local_s_transformed_s')
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
                    add(tr('wires_unavailable'))

            # Read the row object-name list, falling back to its primary name.
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
            add(tr('ip_nesting_current_state_debug'))
            add("=" * 100)
            add(tr('this_report_only_reads_the_current_freecad_state'))
            add(tr('no_input_json_was_generated_by_this_debug_function'))
            add(tr('no_object_placement_was_modified'))
            add("")

            add(tr('python_file'))
            add(tr('s') % os.path.abspath(__file__))
            add("")

            add("FreeCAD:")
            try:
                add(tr('version_s') % str(App.Version()))
            except Exception:
                add(tr('version_unavailable'))

            add(tr('documents_s') % str(App.listDocuments()))
            add(tr('preview_document_name_s') % self.preview_doc_name)
            add(tr('display_units_s') % str(self.display_units))
            add("")

            # ---------------------------------------------------------
            # Preview document
            # ---------------------------------------------------------

            if self.preview_doc_name not in App.listDocuments():
                add(tr('error_preview_document_does_not_exist'))
            else:
                p_doc = App.getDocument(
                    self.preview_doc_name
                )

                if p_doc is None:
                    add(tr('error_could_not_get_preview_document'))
                else:
                    try:
                        p_doc.recompute()
                        add(
                            tr('preview_document_recompute_completed')
                        )
                    except Exception:
                        add(
                            tr('preview_document_recompute_failed')
                        )

                    add(
                        tr('preview_document_objects_d')
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
                                tr('preview_object_d')
                                % object_index
                            )
                            add("-" * 100)
                            add(tr('name_s') % str(name))
                            add(tr('label_s') % str(label))
                            add(tr('typeid_s') % str(type_id))

                            try:
                                add(
                                    tr('visibility_s')
                                    % str(
                                        obj.ViewObject.Visibility
                                    )
                                )
                            except Exception:
                                add(
                                    tr('visibility_unavailable')
                                )

                            try:
                                add(
                                    "Placement:"
                                )
                                add(
                                    tr('s')
                                    % placement_text(
                                        obj.Placement
                                    )
                                )
                            except Exception:
                                add(
                                    tr('placement_unavailable')
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
                                    tr('properties_unavailable')
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
                                    tr('this_object_is_recognized_as_a_grain_arrow')
                                )

                        except Exception:
                            add(
                                tr('failed_to_dump_preview_object_d_s')
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
            add(tr('table_state'))
            add("=" * 100)

            try:
                total_rows = self.table.rowCount()
                data_rows = max(
                    0,
                    total_rows - self.control_rows
                )

                add(tr('total_table_rows_d') % total_rows)
                add(tr('control_rows_d') % self.control_rows)
                add(tr('data_rows_d') % data_rows)
                add("")

                for row in range(data_rows):
                    add("-" * 100)
                    add(tr('table_data_row_d') % row)
                    add("-" * 100)

                    name_item = self.table.item(row, 0)
                    qty_item = self.table.item(row, 1)
                    rotation_item = self.table.item(row, 2)

                    if name_item is None:
                        add(tr('name_item_none'))
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

                    add(tr('label_s') % str(row_label))
                    add(tr('quantity_s') % str(quantity))
                    add(tr('allowed_rotations_s') % str(rotations))
                    add(
                        tr('associated_preview_names_s')
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
                                tr('grain_checkbox_checked_s')
                                % str(
                                    bool(
                                        grain_checkbox
                                        and grain_checkbox.isChecked()
                                    )
                                )
                            )

                            add(
                                tr('grain_axis_s')
                                % str(
                                    grain_combo.currentText()
                                    if grain_combo
                                    else "<unavailable>"
                                )
                            )

                    except Exception:
                        add(
                            tr('grain_widget_state_unavailable')
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
                                tr('rotation_checkbox_checked_s')
                                % str(
                                    bool(
                                        rotation_checkbox
                                        and rotation_checkbox.isChecked()
                                    )
                                )
                            )

                    except Exception:
                        add(
                            tr('rotation_widget_state_unavailable')
                        )

            except Exception:
                add(
                    tr('failed_to_dump_table_state_s')
                    % traceback.format_exc()
                )

            # ---------------------------------------------------------
            # Current grain state
            # ---------------------------------------------------------

            add("")
            add("=" * 100)
            add(tr('panel_grain_state'))
            add("=" * 100)

            try:
                grain_controller = getattr(
                    self,
                    "_grain",
                    None
                )

                if grain_controller is None:
                    add(tr('grain_controller_none'))
                else:
                    add(
                        tr('last_applied_grain_state_s')
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
                            tr('current_grain_checkbox_state_s')
                            % str(current_state)
                        )

                    except Exception:
                        add(
                            tr('current_grain_checkbox_state_unavailable')
                        )

            except Exception:
                add(
                    tr('panel_grain_state_unavailable')
                )

            # ---------------------------------------------------------
            # Current FreeCAD selection
            # ---------------------------------------------------------

            add("")
            add("=" * 100)
            add(tr('freecad_selection'))
            add("=" * 100)

            try:
                selection = Gui.Selection.getSelection()

                add(
                    tr('selected_objects_d')
                    % len(selection)
                )

                for index, selected_obj in enumerate(
                    selection
                ):
                    add(
                        tr('d_name_s_label_s_document_s')
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
                    tr('selection_unavailable')
                )

            # ---------------------------------------------------------
            # Create popup dialog
            # ---------------------------------------------------------

            dialog = QtGui.QDialog(
                QtGui.QApplication.activeWindow()
            )

            ui_call(
                dialog, 'setWindowTitle', tr('ip_nesting_debug_export_current_state')
            )

            dialog.resize(
                1200,
                800
            )

            layout = QtGui.QVBoxLayout(
                dialog
            )

            info_label = ui_widget(
                QtGui.QLabel, tr('detailed_current_state_debug_the_report_includes_local_and_placement_transformed_geometry_')
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

            copy_button = ui_widget(
                QtGui.QPushButton, tr('copy')
            )

            save_button = ui_widget(
                QtGui.QPushButton, tr('save_debug_text')
            )

            close_button = ui_widget(
                QtGui.QPushButton, tr('close')
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

            # Copy the displayed diagnostic report to the clipboard.
            def copy_debug_text():
                try:
                    ui_call(
                        QtGui.QApplication.clipboard(), 'setText', text_edit.toPlainText()
                    )
                except Exception:
                    App.Console.PrintError(
                        tr('failed_to_copy_debug_text')
                        + traceback.format_exc()
                    )

            # Prompt for a path and save the displayed report as UTF-8 text.
            def save_debug_text():
                try:
                    path, _ = QtGui.QFileDialog.getSaveFileName(
                        dialog,
                        tr('save_ip_nesting_debug_text'),
                        "",
                        tr('text_files_txt_all_files')
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
                        tr('ip_nesting_debug_text_saved_to_s')
                        % path
                    )

                except Exception:
                    App.Console.PrintError(
                        tr('failed_to_save_debug_text')
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
                tr('debug_export_polygons_failed')
                + traceback.format_exc()
            )

            try:
                QtGui.QMessageBox.critical(
                    None,
                    tr('ip_nesting_debug'),
                    tr('failed_to_create_debug_window_s')
                    % traceback.format_exc()
                )
            except Exception:
                pass
    
    # Append a labelled text input to a layout and return the input and label widgets.
    def create_input_in_layout(self,parent_layout,label,default,tooltip):
        row = QtGui.QHBoxLayout()

        label_widget = ui_widget(QtGui.QLabel, label)
        edit = QtGui.QLineEdit(default)
        ui_call(edit, 'setToolTip', tooltip)

        row.addWidget(label_widget)
        row.addWidget(edit)

        parent_layout.addLayout(row)

        return edit, label_widget
    
    # Append a labelled False/True combo box and return it.
    def _create_boolean_setting(
        self,
        parent_layout,
        label,
        default=False,
        tooltip=""
    ):
        row = QtGui.QHBoxLayout()

        label_widget = ui_widget(
            QtGui.QLabel, label
        )

        combo = QtGui.QComboBox()
        ui_call(combo, 'addItems', [
            tr('false'),
            tr('true'),
        ])

        combo.setCurrentIndex(
            1 if bool(default) else 0
        )

        if tooltip:
            ui_call(
                label_widget, 'setToolTip', tooltip
            )
            ui_call(
                combo, 'setToolTip', tooltip
            )

        row.addWidget(label_widget)
        row.addWidget(combo)

        parent_layout.addLayout(row)

        return combo
    
    # Append a labelled text input to the main panel and return the input widget.
    def create_input(self, label, default, tooltip):
        row = QtGui.QHBoxLayout()
        edit = QtGui.QLineEdit(default)
        ui_call(edit, 'setToolTip', tooltip)
        row.addWidget(ui_widget(QtGui.QLabel, label))
        row.addWidget(edit)
        self.layout.addLayout(row)
        return edit
        
    # Return the number of logical CPU cores available to Python.
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
    # Timer callback - delegates to grain controller.
    def _on_apply_blink_tick(self):
        """Timer callback - delegates to grain controller."""
        self._grain._on_apply_blink_tick()
    
    # Start blinking - delegates to grain controller.
    def _start_apply_blink(self):
        """Start blinking - delegates to grain controller."""
        self._grain._start_apply_blink()
    
    # Stop blinking - delegates to grain controller.
    def _stop_apply_blink(self):
        """Stop blinking - delegates to grain controller."""
        self._grain._stop_apply_blink()
    
    # Update blinking state - delegates to grain controller.
    def _update_apply_blink_state(self):
        """Update blinking state - delegates to grain controller."""
        self._grain._update_apply_blink_state()

    # --- Grain arrow helpers (delegated to GrainUIController) ---
    # Callback for per-row grain checkbox - delegates to grain controller.
    def _on_grain_checkbox_state_changed(self, preview_obj_name, grain_cb, grain_combo, state):
        """Callback for per-row grain checkbox - delegates to grain controller."""
        self._grain._on_grain_checkbox_state_changed(preview_obj_name, grain_cb, grain_combo, state)
            
    # Callback for per-row grain axis combobox - delegates to grain controller.
    def _on_grain_axis_changed(self, preview_obj_name, grain_cb, grain_combo, index):
        """Callback for per-row grain axis combobox - delegates to grain controller."""
        self._grain._on_grain_axis_changed(preview_obj_name, grain_cb, grain_combo, index)

    # Wire per-row grain widgets - delegates to grain controller.
    def _connect_grain_widgets(self, grain_cb, grain_combo, preview_obj_name):
        """Wire per-row grain widgets - delegates to grain controller."""
        self._grain._connect_grain_widgets(grain_cb, grain_combo, preview_obj_name)

    # Create two control rows at the bottom: - row (table.rowCount()-2): Rotate controls - row
    # (table.rowCount()-1): Change grain direction controls
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

            htop.addWidget(ui_widget(QtGui.QLabel, tr('rotate_7b41f1')))

            self.bulk_angle_combo = QtGui.QComboBox()
            ui_call(self.bulk_angle_combo, 'addItems', ["90°", "180°"])
            self.bulk_angle_combo.setCurrentIndex(1)
            self.bulk_angle_combo.setFixedWidth(100)
            htop.addWidget(self.bulk_angle_combo)

            self.bulk_axis_combo = QtGui.QComboBox()
            ui_call(self.bulk_axis_combo, 'addItems', ["X", "Y"])
            self.bulk_axis_combo.setCurrentIndex(0)
            self.bulk_axis_combo.setFixedWidth(85)
            htop.addWidget(self.bulk_axis_combo)

            self.bulk_rotate_btn = ui_widget(QtGui.QPushButton, tr('rotate'))
            self.bulk_rotate_btn.setMinimumWidth(80)
            self.bulk_rotate_btn.clicked.connect(self.apply_bulk_rotate)
            htop.addWidget(self.bulk_rotate_btn)

            self.clear_all_btn = ui_widget(QtGui.QPushButton, tr('clear_all'))
            ui_call(self.clear_all_btn, 'setToolTip', tr('uncheck_all_selection_checkboxes_in_the_table'))
            self.clear_all_btn.clicked.connect(self.clear_all_checks)
            htop.addWidget(self.clear_all_btn)

            htop.addStretch()

            self.table.setCellWidget(top_idx, 0, container_top)
            self.table.setSpan(top_idx, 0, 1, self.table.columnCount())
            control_item = ui_widget(QtGui.QTableWidgetItem, "")
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

            hbot.addWidget(ui_widget(QtGui.QLabel, tr('change_grain_direction')))

            self.bulk_grain_combo = QtGui.QComboBox()
            ui_call(self.bulk_grain_combo, 'addItems', ["X", "Y"])
            self.bulk_grain_combo.setCurrentIndex(0)
            self.bulk_grain_combo.setFixedWidth(70)
            hbot.addWidget(self.bulk_grain_combo)

            # Connect bulk combobox changes to apply immediately (but only for checked rows)
            try:
                self.bulk_grain_combo.currentIndexChanged.connect(self._on_bulk_grain_changed)
            except Exception:
                pass

            self.bulk_grain_apply_btn = ui_widget(QtGui.QPushButton, tr('apply_grain'))
            self.bulk_grain_apply_btn.setMinimumWidth(100)
            self.bulk_grain_apply_btn.clicked.connect(self.apply_change_grain)
            self.set_angle_btn = ui_widget(QtGui.QPushButton, tr('set_custom_angle'))
            self.set_angle_btn.setMinimumWidth(160)
            ui_call(self.set_angle_btn, 'setToolTip', tr('set_grain_angle_for_selected_grainarrow_objects'))
            hbot.addWidget(self.bulk_grain_apply_btn)
            hbot.addWidget(self.set_angle_btn)
            
            try:
                self.set_angle_btn.clicked.connect(self._on_set_angle_clicked)
            except Exception:
                pass

            hbot.addStretch()

            self.table.setCellWidget(bottom_idx, 0, container_bot)
            self.table.setSpan(bottom_idx, 0, 1, self.table.columnCount())
            control_item2 = ui_widget(QtGui.QTableWidgetItem, "")
            control_item2.setFlags(QtCore.Qt.NoItemFlags)
            self.table.setItem(bottom_idx, 0, control_item2)

        except Exception:
            App.Console.PrintError(tr('failed_to_create_control_rows') + traceback.format_exc())

    # Open the Custom angle dialog only for rows where both Grain Direction and Custom angle are
    # enabled.
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
                        tr('failed_to_inspect_custom_angle_selection_in_row_d_s')
                        % (
                            row,
                            traceback.format_exc()
                        )
                    )

            # No Grain Direction selected at all.
            if not grain_selected:
                QtGui.QMessageBox.warning(
                    self.form,
                    tr('custom_angle'),
                    tr('select_at_least_one_part_in_the_grain_direction_column_first')
                )
                return

            # Grain Direction is selected, but Custom angle is not.
            if not custom_angle_selected:
                QtGui.QMessageBox.warning(
                    self.form,
                    tr('custom_angle'),
                    tr('to_set_a_custom_angle_select_the_custom_angle_checkbox_for_the_part_s_you_want_to_modify')
                )
                return

            # Both checkboxes are selected, but no arrow exists.
            if not valid_arrow_names:
                QtGui.QMessageBox.warning(
                    self.form,
                    tr('custom_angle'),
                    tr('no_valid_grain_arrow_was_found_for_the_selected_custom_angle_part_s')
                )
                return

            self._open_grain_angle_dialog_for_arrows(
                valid_arrow_names
            )

        except Exception:
            App.Console.PrintError(
                tr('on_set_angle_clicked_failed')
                + traceback.format_exc()
            )
    
    # Return list of GrainArrow_<previewObjName> for ALL rows where Grain Direction checkbox is
    # checked.
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
    
    # When bulk grain combobox changes - delegates to grain controller.
    def _on_bulk_grain_changed(self, index):
        """When bulk grain combobox changes - delegates to grain controller."""
        self._grain._on_bulk_grain_changed(index)

    # Compute rotation to align object - delegates to preview manager.
    def align_to_largest_face(self, obj):
        """Compute rotation to align object - delegates to preview manager."""
        return self._preview.align_to_largest_face(obj)

    # Ensure preview document exists - delegates to preview manager.
    def ensure_preview_doc(self, reset_counters_if_new=True):
        """Ensure preview document exists - delegates to preview manager."""
        return self._preview.ensure_preview_doc(reset_counters_if_new)

    # Delete preview objects - delegates to preview manager.
    def delete_preview_objects(self, names):
        """Delete preview objects - delegates to preview manager."""
        return self._preview.delete_preview_objects(names)

    # Round-trip geometry through a temporary STEP file to obtain an independent Shape.
    def _serialize_shape_to_avoid_hash_issues(self, shape):
        """
        Round-trip geometry through a temporary STEP file to obtain an independent Shape.

        Return the imported Shape, or shape.copy() if STEP serialization fails.
        Always attempt to remove the temporary file.
        """
        temp_step_path = None
        try:
            # Create temporary STEP file
            temp_step_file = tempfile.NamedTemporaryFile(suffix=".step", delete=False)
            temp_step_path = temp_step_file.name
            temp_step_file.close()
            
            # Export shape to STEP
            App.Console.PrintMessage(tr("debug.serialize_shape") % temp_step_path)
            shape.exportStep(temp_step_path)
            
            # Re-import from STEP to get clean, independent shape
            imported_shape = Part.Shape()
            imported_shape.read(temp_step_path)
            
            App.Console.PrintMessage(tr('shape_successfully_serialized_and_re_imported'))
            return imported_shape
            
        except Exception as e:
            App.Console.PrintWarning(tr("debug.serialize_failed") % e)
            # Fallback to direct copy if serialization fails
            return shape.copy()
            
        finally:
            # Clean up temporary file
            if temp_step_path is not None:
                try:
                    os.unlink(temp_step_path)
                except Exception as e:
                    App.Console.PrintWarning(tr("debug.cleanup_step") % (temp_step_path, e))

    # Copy selected geometry into the preview, align it and add part rows before arranging
    # groups.
    def add_selected_objects(self):
        selection = Gui.Selection.getSelection()
        if not selection:
            App.Console.PrintMessage(tr('no_selection_to_add'))
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
                        App.Console.PrintError(tr('failed_to_create_preview_object_for_s') % getattr(target, "Label", "<unknown>"))
                        continue
                    
                    # --- FIX: PartDesign::Body in preview may have invalid Shape/BoundBox; use Tip.Shape as fallback ---
                    try:
                        p_doc.recompute()
                    except Exception:
                        pass

                    # Check that a bounding box has positive X and Y extents.
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
                        App.Console.PrintWarning(tr("debug.shape_hash") % e)

                    bbox = new_obj.Shape.BoundBox

                    # FIX: if bbox invalid, create a stable Part::Feature from Body.Tip.Shape and use it for layout/preview
                    try:
                        if not (bbox.XMax > bbox.XMin and bbox.YMax > bbox.YMin):
                            # only try fallback for PartDesign::Body
                            if getattr(new_obj, "TypeId", "") == "PartDesign::Body":
                                App.Console.PrintMessage(tr('add_selected_objects_invalid_bbox_for_s_trying_tip_shape_fallback') % new_obj.Name)

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
                                    tr('add_selected_objects_still_invalid_bbox_for_s_s_skipping_grid_placement') %
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
                    name_item = ui_widget(QtGui.QTableWidgetItem, new_obj.Label)
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
                    qty_item = ui_widget(QtGui.QTableWidgetItem, "1")
                    qty_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.table.setItem(insert_pos, 1, qty_item)
                    # Column 2: Rotation degree defaults (centered)
                    rot_item = ui_widget(QtGui.QTableWidgetItem, "1")
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
                    ui_call(
                        checkbox, 'setToolTip', tr('select_which_parts_will_be_rotated_in_the_xy_plane_when_parts_are_added_the_alignment_algo')
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
                    ui_call(grain_cb, 'setToolTip', tr('enable_custom_grain_direction_for_this_part'))
                    grain_layout.addWidget(grain_cb)
                    grain_combo = QtGui.QComboBox()
                    ui_call(grain_combo, 'addItems', ["X", "Y"])
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
                    ui_call(
                        custom_angle_cb, 'setToolTip', tr('enable_custom_angle_for_this_part_the_set_custom_angle_command_can_only_modify_parts_where')
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
                        App.Console.PrintError(tr('failed_to_connect_grain_widget_signals_for_s') % (new_obj.Name,) + traceback.format_exc())

                    # ensure column width remains (in case header auto-resize changed it)
                    try:
                        self.table.setColumnWidth(0, 250)
                    except Exception:
                        pass

                except Exception:
                    App.Console.PrintError(tr('failed_to_add_object_to_preview') + traceback.format_exc())

        finally:
            self._suppress_qty_update = False

        try:
            p_doc.recompute()
            Gui.setActiveDocument(p_doc)
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            App.Console.PrintError(tr('error_during_final_recompute_view_update') + traceback.format_exc())
            
        # TRIGGER LAYOUT UPDATE
        self.update_grain_layout_and_perimeters()

        # Include perimeters and arrows in the final centered view.
        self._fit_all_views()

        # update Apply Grain blink state (in case added rows have checked boxes programmatically)
        try:
            self._update_apply_blink_state()
        except Exception:
            pass
            
    # Update grain layout and perimeters - delegates to grain controller.
    def update_grain_layout_and_perimeters(self):
        """Update grain layout and perimeters - delegates to grain controller."""
        self._grain.update_grain_layout_and_perimeters()

    # Fit every visible object in the active FreeCAD view after preview geometry changes.
    def _fit_all_views(self):
        """Run FreeCAD's Std_ViewFitAll command for the preview document."""
        try:
            if self.preview_doc_name in App.listDocuments():
                Gui.setActiveDocument(self.preview_doc_name)
            Gui.runCommand("Std_ViewFitAll", 0)
        except Exception:
            try:
                active = Gui.activeDocument()
                if active is not None:
                    active.activeView().fitAll()
            except Exception:
                try:
                    Gui.SendMsgToActiveView("ViewFit")
                except Exception:
                    App.Console.PrintError(
                        tr('failed_to_fit_all_visible_nesting_objects')
                        + traceback.format_exc()
                    )


    # Select preview objects for row - delegates to preview manager.
    def select_preview_objects_for_row(self, row):
        """Select preview objects for row - delegates to preview manager."""
        self._preview.select_preview_objects_for_row(row)

    # Select the preview objects for a clicked data row, ignoring control rows.
    def on_cell_clicked(self, row, col):
        # ignore clicks on control rows
        if row >= self.table.rowCount() - self.control_rows:
            return

        try:
            # select all preview objects associated with this row
            self.select_preview_objects_for_row(row)
        except Exception:
            App.Console.PrintError(tr('on_cell_clicked_failed') + traceback.format_exc())

    # Handle Qty and Rotation edits.
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
                        tr('failed_to_delete_preview_object_when_qty_was_set_to_zero')
                        + traceback.format_exc()
                    )

                try:
                    self.table.removeRow(row)
                except Exception:
                    App.Console.PrintError(
                        tr('failed_to_remove_table_row')
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
                    ui_call(
                        item, 'setText', normalized_text
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
                tr('on_item_changed_failed')
                + traceback.format_exc()
            )

    # Delegation wrappers to NestingRotator
    # Read angle and axis from control row and delegate to rotator.
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
                App.Console.PrintMessage(tr('rotation_module_not_available'))
                return
            # pass sheet grain and algorithm if needed later (algorithm available in UI)
            self._rotator.apply_bulk_rotate(self.table, p_doc, angle, axis_char=axis)

            # update grain perimeter after bulk rotate
            try:
                if GrainPreparer is not None:
                    try:
                        self.update_grain_layout_and_perimeters()
                    except Exception:
                        App.Console.PrintError(tr('failed_to_update_grain_layout_perimeters_after_apply_change_grain') + traceback.format_exc())
            except Exception:
                App.Console.PrintError(tr('failed_to_draw_grain_perimeter_after_apply_bulk_rotate') + traceback.format_exc())
            
            # TRIGGER LAYOUT UPDATE
            self.update_grain_layout_and_perimeters()

            # Recenter the scene after the selected parts were rotated.
            self._fit_all_views()
            
        except Exception:
            App.Console.PrintError(tr('apply_bulk_rotate_wrapper_failed') + traceback.format_exc())

    # Apply checked grain rows using their current axes/angles, then fit the rebuilt preview.
    def apply_change_grain(self):
        """Normalize and pack grain-enabled rows once, preserving per-row grain settings."""
        try:
            changed = 0
            data_rows = self.table.rowCount() - self.control_rows
            for row in range(data_rows):
                grain_widget = self.table.cellWidget(row, 4)
                if grain_widget is None:
                    continue
                checkbox = grain_widget.findChild(QtGui.QCheckBox)
                if checkbox is not None and checkbox.isChecked():
                    changed += 1

            # The controller reads current Grain Direction checkboxes on every apply.
            # It also normalizes angles, redraws all arrows/perimeters and saves the
            # applied state. A second pass would process already-normalized geometry.
            self.update_grain_layout_and_perimeters()
            self._fit_all_views()
            self._update_apply_blink_state()
            App.Console.PrintMessage(
                tr('apply_change_grain_applied_grain_to_d_rows') % changed
            )
        except Exception:
            App.Console.PrintError(tr('apply_change_grain_failed') + traceback.format_exc())

    # Uncheck every per-row 'Select for rotation' checkbox (column 3) - does not modify grain
    # states.
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
                    App.Console.PrintError(tr('clear_all_checks_per_row_error') + traceback.format_exc())
            App.Console.PrintMessage(tr('clear_all_checks_all_select_for_rotation_checkboxes_cleared'))
            # update blinking state
            try:
                self._update_apply_blink_state()
            except Exception:
                pass
        except Exception:
            App.Console.PrintError(tr('clear_all_checks_failed') + traceback.format_exc())
            
    # Remove selected table rows and delete their preview objects. Behavior matches entering Qty
    # = 0 for the selected rows.
    def remove_selected_rows(self):
        """Remove selected table rows and delete their preview objects.
           Behavior matches entering Qty = 0 for the selected rows.
        """
        indices = self.table.selectionModel().selectedRows()
        if not indices:
            App.Console.PrintMessage(tr('no_rows_selected_for_removal'))
            return

        control_start = self.table.rowCount() - self.control_rows
        rows = sorted([index.row() for index in indices if index.row() < control_start], reverse=True)
        if not rows:
            App.Console.PrintMessage(tr('no_data_rows_selected_for_removal'))
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
                        App.Console.PrintError(tr('remove_selected_rows_delete_preview_objects_failed_for_row_d_s') % (r, traceback.format_exc()))
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
                App.Console.PrintError(tr('error_removing_row_d_s') % (r, traceback.format_exc()))

        try:
            if p_doc:
                p_doc.recompute()
        except Exception:
            App.Console.PrintError(tr('error_recomputing_preview_doc_after_removals') + traceback.format_exc())

        # update grain perimeter after row removals
        try:
            if GrainPreparer is not None:
                try:
                    self.update_grain_layout_and_perimeters()
                except Exception:
                    App.Console.PrintError(tr('failed_to_update_grain_layout_perimeters_after_apply_change_grain') + traceback.format_exc())
        except Exception:
            App.Console.PrintError(tr('failed_to_draw_grain_perimeter_after_remove_selected_rows') + traceback.format_exc())
        
        # TRIGGER LAYOUT UPDATE
        self.update_grain_layout_and_perimeters()

        # The grain perimeters and arrows may extend the scene after relayout.
        self._fit_all_views()

        # update Apply Grain blink state
        try:
            self._update_apply_blink_state()
        except Exception:
            pass

    # Validate material/part rows, export the job and start asynchronous nesting CLI result
    # processing.
    def execute_nesting(self):
        """
        Validate material/part rows, write the job files and start the nesting CLI.

        The process manager polls result.json and imports the completed result.
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
                    tr('at_least_one_sheet_or_offcut_must_be_added')
                )

            if data_rows == 0:
                missing_items.append(
                    tr('at_least_one_part_must_be_added')
                )

            if missing_items:
                message = "\n".join(
                    tr('s_344808') % item
                    for item in missing_items
                )

                QtGui.QMessageBox.warning(
                    self.form,
                    tr('cannot_start_nesting'),
                    tr('cannot_start_nesting_until_s')
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

            generation_ok = execute_nesting_impl(
                self
            )

            if generation_ok is not True:
                return

            if not os.path.exists(input_path):
                QtGui.QMessageBox.critical(
                    self.form,
                    tr('input_generation_failed'),
                    tr('the_input_json_file_was_not_created')
                )
                return

            # Start the nesting CLI and wait asynchronously for result.json.
            try:
                started = self._nesting_manager.start_nesting(
                    input_path=input_path
                )

                if not started:
                    return

            except Exception:
                App.Console.PrintError(
                    tr('failed_to_start_nesting_process')
                    + traceback.format_exc()
                )

                QtGui.QMessageBox.critical(
                    self.form,
                    tr('nesting_start_error'),
                    tr('failed_to_start_the_nesting_process')
                )

            except Exception:
                App.Console.PrintError(
                    tr('failed_to_start_nesting_process')
                    + traceback.format_exc()
                )

                QtGui.QMessageBox.critical(
                    self.form,
                    tr('nesting_start_error'),
                    tr('failed_to_start_the_nesting_process')
                )

        except Exception:
            App.Console.PrintError(
                tr('execute_nesting_failed')
                + traceback.format_exc()
            )

            QtGui.QMessageBox.critical(
                self.form,
                tr('input_generation_error'),
                tr('failed_to_generate_input_json')
            )

    # Return the Cancel button flag expected by the FreeCAD task-panel API.
    def getStandardButtons(self):
        buttons = QtGui.QDialogButtonBox.Cancel
        try:
            # PySide6 enum values require explicit access through .value
            return int(buttons.value)
        except AttributeError:
            # Compatibility with older PySide/FreeCAD versions
            return int(buttons)
        
    # Preview arrow rotations in a modal dialog, then apply the grain angle or restore cancelled
    # changes.
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

                    part_label = tr('unknown_part')
                    if arrow_name.startswith("GrainArrow_"):
                        part_name = arrow_name[len("GrainArrow_"):]
                        part_obj = p_doc.getObject(part_name) if p_doc else None
                        if part_obj:
                            part_label = getattr(part_obj, "Label", "") or getattr(part_obj, "Name", part_name)

                    part_labels.append(tr('s_cd3af8') % (part_label))
                except Exception:
                    part_labels.append(str(ao))

            parent = QtGui.QApplication.activeWindow()
            last_ui_angle = int(initial_angle) % 360
            
            # Apply the incremental dial-angle change around each arrow bounding-box centre.
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

            # Queue the latest angle for the throttled preview update.
            def _on_angle_changed(a):
                nonlocal pending_angle
                pending_angle = a

            # Apply the pending arrow angle once per timer tick.
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
                                            tr('absolute_grain_angle_in_degrees_vs_x')
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

                    # Recenter all remaining parts after rotation and grain layout changes.
                    self._fit_all_views()
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
            App.Console.PrintError(tr('ipnesting_debug_open_grain_angle_dialog_for_selected_arrows_failed') + traceback.format_exc())
        finally:
            try:
                self._grain_angle_dialog_open = False
                App.Console.PrintMessage(tr('ipnesting_debug_set_grain_angle_dialog_open_false_finally'))
            except Exception:
                pass
            
    # Rotate named parts about their bounding-box centres around Z and recompute the document.
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
            App.Console.PrintError(tr('rotate_preview_parts_about_z_failed') + traceback.format_exc())
            
    # Recreate X-axis grain arrows for the named preview parts.
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
            App.Console.PrintError(tr('redraw_grain_arrows_for_parts_failed') + traceback.format_exc())
            
    # Return the IP-Nesting FreeCAD preference group, or None when unavailable.
    def _prefs(self):
        # helper so we can call it anywhere
        try:
            return App.ParamGet("User parameter:BaseApp/Preferences/Mod/IPNesting")
        except Exception:
            return None

    # Parse decimal input using dot or comma.
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

    # Format a dimension in the currently selected display units.
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

    # Convert a value from the active display unit to mm.
    def _display_to_mm(self, value):
        """
        Convert a value from the active display unit to mm.
        """
        if self.display_units == "inch":
            return float(value) * MM_PER_INCH

        return float(value)

    # Convert a millimetre value to the active display unit.
    def _mm_to_display(self, value_mm):
        """
        Convert a millimetre value to the active display unit.
        """
        if self.display_units == "inch":
            return float(value_mm) / MM_PER_INCH

        return float(value_mm)

    # Normalize canonical dimension values stored in mm. Boundary resolution is kept to two
    # decimal places.
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
    
    # Map a dimension widget to its canonical millimetre-storage key.
    def _dimension_field_key(self, line_edit):
        if line_edit is self.sheet_margin:
            return "sheet_margin"

        if line_edit is self.spacing:
            return "spacing"

        if line_edit is self.res:
            return "boundary_resolution"

        return None
    
    # Return all dimension QLineEdit fields in the main panel.
    def _dimension_fields(self):
        """
        Return all dimension QLineEdit fields in the main panel.
        """
        return [
            self.sheet_margin,
            self.spacing,
            self.res,
        ]

    # Read one dimension field and return mm.
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

    # Store the canonical value in mm and display it using the current units.
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
            ui_call(
                line_edit, 'setText', self._format_dimension(value_mm)
            )
        finally:
            line_edit.blockSignals(False)

    # Refresh margin, spacing and boundary-resolution labels with the display units.
    def _update_dimension_labels(self):
        suffix = (
            "inch"
            if self.display_units == "inch"
            else "mm"
        )

        ui_call(
            self.sheet_margin_label, 'setText', tr('sheet_margin_s') % suffix
        )

        ui_call(
            self.spacing_label, 'setText', tr('part_spacing_s') % suffix
        )

        ui_call(
            self.res_label, 'setText', tr('boundary_resolution_s') % suffix
        )
    
    # Change display units using canonical mm values.
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
                tr('on_units_changed_failed')
                + traceback.format_exc()
            )
    
    # Read one visible dimension and return its value in mm.
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
    
    # Restore dimensions, display units, CPU selection and nesting CLI settings.
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
                self.cpu_cores_combo,
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
            ui_call(
                self.sheet_margin, 'setText', self._format_dimension(
                    sheet_margin_mm
                )
            )

            ui_call(
                self.spacing, 'setText', self._format_dimension(
                    spacing_mm
                )
            )

            ui_call(
                self.res, 'setText', self._format_dimension(
                    boundary_resolution_mm
                )
            )
            
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
    
    # Persist canonical dimensions, display units and CPU selection.
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

        except Exception:
            App.Console.PrintError(
                tr('save_settings_to_prefs_failed')
                + traceback.format_exc()
            )

    # Read one field in the current display units and save its canonical value in mm.
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
                tr('update_dimension_value_from_field_failed')
                + traceback.format_exc()
            )

    # Return the stored canonical boundary resolution in millimetres, defaulting to 0.1.
    def get_boundary_resolution_mm(self):
        return float(
            self._dimension_values_mm.get(
                "boundary_resolution",
                0.1
            )
        )

    # Normalize one field and update its canonical mm value.
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
                    ui_call(
                        line_edit, 'setText', normalized
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
                tr('normalize_decimal_field_failed')
                + traceback.format_exc()
            )
    
    # Connect setting edits to decimal normalization and preference persistence.
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
                self.cpu_cores_combo.currentIndexChanged.connect(
                    self._save_settings_to_prefs
                )
            except Exception:
                pass
        except Exception:
            pass
            
    # Add an existing preview object (by Name) into the table as a new data row. Mirrors the row
    # structure used by add_selected_objects().
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

            name_item = ui_widget(QtGui.QTableWidgetItem, obj.Label)
            name_item.setData(QtCore.Qt.UserRole, obj.Name)
            try:
                name_item.setData(QtCore.Qt.UserRole + 1, json.dumps([obj.Name]))
            except Exception:
                name_item.setData(QtCore.Qt.UserRole + 1, [obj.Name])
            self.table.setItem(insert_pos, 0, name_item)

            qty_item = ui_widget(QtGui.QTableWidgetItem, "1")
            qty_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(insert_pos, 1, qty_item)

            rot_item = ui_widget(QtGui.QTableWidgetItem, "1")
            rot_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.table.setItem(insert_pos, 2, rot_item)

            # Column 3: Select for rotation checkbox (centered)
            container_sel = QtGui.QWidget()
            cell_layout_sel = QtGui.QHBoxLayout(container_sel)
            cell_layout_sel.setContentsMargins(0, 0, 0, 0)
            cell_layout_sel.setSpacing(0)
            cell_layout_sel.addStretch()
            checkbox = QtGui.QCheckBox()
            ui_call(
                checkbox, 'setToolTip', tr('select_which_parts_will_be_rotated_in_the_xy_plane_when_parts_are_added_the_alignment_algo')
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
            ui_call(grain_cb, 'setToolTip', tr('enable_custom_grain_direction_for_this_part'))
            grain_layout.addWidget(grain_cb)
            grain_combo = QtGui.QComboBox()
            ui_call(grain_combo, 'addItems', ["X", "Y"])
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
            ui_call(
                custom_angle_cb, 'setToolTip', tr('enable_custom_angle_for_this_part_the_set_custom_angle_command_can_only_modify_parts_where')
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
            App.Console.PrintError(tr('add_preview_object_to_table_failed') + traceback.format_exc())
            return False

    # Choose a DXF, import wire geometry into the preview and add the resulting part row.
    def import_dxf_2d(self):
        try:
            if import_dxf_to_preview is None:
                QtGui.QMessageBox.warning(None, tr('import_dxf_8bce56'), tr('dxf_import_module_not_available'))
                return

            path, _ = QtGui.QFileDialog.getOpenFileName(
                None, tr('import_dxf_8bce56'), "", tr('dxf_files_dxf_dxf_all_files')
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
                QtGui.QMessageBox.warning(None, tr('import_dxf_8bce56'), tr('no_usable_geometry_imported'))
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

                # Recenter the imported part together with its perimeters and arrows.
                self._fit_all_views()
            except Exception:
                pass
            try:
                self._update_apply_blink_state()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError(tr('import_dxf_2d_failed') + traceback.format_exc())

    # Choose an SVG, import wire geometry into the preview and add the resulting part row.
    def import_svg_2d(self):
        try:
            if import_svg_to_preview is None:
                QtGui.QMessageBox.warning(None, tr('import_svg_084c0a'), tr('svg_import_module_not_available'))
                return

            path, _ = QtGui.QFileDialog.getOpenFileName(
                None, tr('import_svg_084c0a'), "", tr('svg_files_svg_svg_all_files')
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
                QtGui.QMessageBox.warning(None, tr('import_svg_084c0a'), tr('no_usable_geometry_imported'))
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

                # Recenter the imported part together with its perimeters and arrows.
                self._fit_all_views()
            except Exception:
                pass
            try:
                self._update_apply_blink_state()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError(tr('import_svg_2d_failed') + traceback.format_exc())
    
    # Normalize the rotation count field.
    def _clamp_rotation_degrees_text(self, txt):
        """
        Normalize the rotation count field.

        Accepts a plain rotation count (1..3600), a degree step in
        parentheses such as "(90)", or an explicit angle list in brackets
        such as "[45, 90]".
        """
        return normalize_rotation_text(txt)


    # Normalize the rotation count cell for a given data row.
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
                    ui_call(item, 'setText', new)
                finally:
                    self._suppress_qty_update = False
        except Exception:
            pass

