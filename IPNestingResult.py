"""
IPNestingResult.py

Runs deepnest.exe, waits asynchronously for result.json and imports
the result into a new FreeCAD document named Nesting_Result.

Expected files:
    input.json
    result.json
    nesting_session.json

Expected result.json structure:
    {
        "schema_version": 1,
        "job_id": "...",
        "success": true,
        "status": "success",
        "sourceParts": [...],
        "sheets": [...],
        "placements": [...],
        "unplaced": [...]
    }
"""

import FreeCAD as App
import FreeCADGui as Gui

from PySide import QtGui, QtCore

import json
import math
import os
import subprocess
import traceback


try:
    import Part
except Exception:
    Part = None


# ----------------------------------------------------------------------
# General helpers
# ----------------------------------------------------------------------

def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def _load_json_file(path):
    """
    Read and parse a JSON file.

    Returns:
        dict/list on success
        None on failure
    """
    try:
        if not path:
            return None

        if not os.path.exists(path):
            return None

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as json_file:
            return json.load(json_file)

    except Exception:
        return None


def _read_file_signature(path):
    """
    Return a file signature used to detect when result.json is stable.
    """
    try:
        stat = os.stat(path)

        return (
            int(stat.st_size),
            int(stat.st_mtime_ns)
        )

    except Exception:
        return None


def _point_xy(point):
    """
    Convert either:

        {"x": 1, "y": 2}

    or:

        [1, 2]

    into an x/y tuple.
    """
    try:
        if isinstance(point, dict):
            return (
                _safe_float(point.get("x")),
                _safe_float(point.get("y"))
            )

        return (
            _safe_float(point[0]),
            _safe_float(point[1])
        )

    except Exception:
        return 0.0, 0.0


def _points_to_vectors(points, z=0.0):
    result = []

    for point in points or []:
        try:
            x, y = _point_xy(point)
            result.append(
                App.Vector(
                    x,
                    y,
                    float(z)
                )
            )
        except Exception:
            continue

    return result


def _rotate_xy(x, y, angle_deg):
    """
    Rotate x/y around the local origin counter-clockwise.
    """
    angle_rad = math.radians(
        _safe_float(angle_deg)
    )

    cosine = math.cos(angle_rad)
    sine = math.sin(angle_rad)

    return (
        x * cosine - y * sine,
        x * sine + y * cosine
    )


def _transform_points(points, x, y, rotation):
    """
    Apply result placement to nesting-local points.
    """
    transformed = []

    for point in points or []:
        px, py = _point_xy(point)
        rx, ry = _rotate_xy(
            px,
            py,
            rotation
        )

        transformed.append(
            App.Vector(
                rx + _safe_float(x),
                ry + _safe_float(y),
                0.0
            )
        )

    return transformed


def _close_vectors(points):
    """
    Close a FreeCAD polygon if necessary.
    """
    if not points:
        return []

    result = list(points)

    try:
        first = result[0]
        last = result[-1]

        if (
            abs(first.x - last.x) > 1e-9
            or abs(first.y - last.y) > 1e-9
            or abs(first.z - last.z) > 1e-9
        ):
            result.append(
                App.Vector(
                    first.x,
                    first.y,
                    first.z
                )
            )

    except Exception:
        pass

    return result


def _copy_placement(placement):
    """
    Make a safe copy of a FreeCAD Placement.
    """
    try:
        return App.Placement(
            App.Vector(
                placement.Base.x,
                placement.Base.y,
                placement.Base.z
            ),
            App.Rotation(
                App.Vector(
                    placement.Rotation.Axis.x,
                    placement.Rotation.Axis.y,
                    placement.Rotation.Axis.z
                ),
                math.degrees(
                    placement.Rotation.Angle
                )
            )
        )

    except Exception:
        return App.Placement()


# ----------------------------------------------------------------------
# Result importer
# ----------------------------------------------------------------------

class NestingResultImporter(object):
    """
    Imports result.json into Nesting_Result.
    """

    def __init__(self, panel):
        self.panel = panel
        self.preview_doc = None
        self.result_doc = None
        self.session_data = {}
        self.result_data = {}

        self.source_parts_by_index = {}
        self.session_parts_by_index = {}
        self.sheets_by_index = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def import_result(
        self,
        result_data,
        session_data=None
    ):
        try:
            if not isinstance(
                result_data,
                dict
            ):
                App.Console.PrintError(
                    "result.json root must be an object.\n"
                )
                return False

            self.result_data = result_data
            self.session_data = (
                session_data
                if isinstance(session_data, dict)
                else {}
            )

            self._prepare_maps()

            self.preview_doc = (
                self._get_preview_document()
            )

            if self.preview_doc is None:
                App.Console.PrintError(
                    "Nesting preview document was not found.\n"
                )
                return False

            self.result_doc = (
                self._create_result_document()
            )

            if self.result_doc is None:
                return False

            self._import_sheets()
            self._import_placements()

            try:
                self.result_doc.recompute()
            except Exception:
                pass

            try:
                Gui.activateDocument(
                    self.result_doc.Name
                )

                Gui.activeDocument().activeView().viewTop()
                Gui.SendMsgToActiveView(
                    "ViewFit"
                )

            except Exception:
                pass

            self._show_result_summary()

            return True

        except Exception:
            App.Console.PrintError(
                "NestingResultImporter.import_result failed:\n"
                + traceback.format_exc()
            )
            return False

    # ------------------------------------------------------------------
    # Maps
    # ------------------------------------------------------------------

    def _prepare_maps(self):
        """
        Prepare lookup maps for source parts, session parts and sheets.
        """
        self.source_parts_by_index = {}
        self.session_parts_by_index = {}
        self.sheets_by_index = {}

        for part in (
            self.result_data.get(
                "sourceParts",
                []
            )
            or []
        ):
            try:
                index = _safe_int(
                    part.get(
                        "source_part_index"
                    )
                )

                self.source_parts_by_index[index] = part

            except Exception:
                continue

        for part in (
            self.session_data.get(
                "parts",
                []
            )
            or []
        ):
            try:
                index = _safe_int(
                    part.get(
                        "source_part_index"
                    )
                )

                self.session_parts_by_index[index] = part

            except Exception:
                continue

        for sheet in (
            self.result_data.get(
                "sheets",
                []
            )
            or []
        ):
            try:
                index = _safe_int(
                    sheet.get(
                        "source_sheet_index",
                        sheet.get(
                            "sheet_instance_index",
                            0
                        )
                    )
                )

                self.sheets_by_index[index] = sheet

            except Exception:
                continue

    def _get_preview_document(self):
        try:
            document_name = (
                self.result_data.get(
                    "_ip_nesting",
                    {}
                ).get(
                    "preview_document",
                    self.panel.preview_doc_name
                )
            )

            if document_name in App.listDocuments():
                return App.getDocument(
                    document_name
                )

        except Exception:
            pass

        try:
            if self.panel.preview_doc_name in App.listDocuments():
                return App.getDocument(
                    self.panel.preview_doc_name
                )

        except Exception:
            pass

        return None

    def _get_source_part(self, source_index):
        """
        Return source metadata.

        Priority:
            result.json sourceParts
            nesting_session.json parts
        """
        source_index = _safe_int(
            source_index
        )

        source_part = self.source_parts_by_index.get(
            source_index
        )

        if source_part:
            return source_part

        return self.session_parts_by_index.get(
            source_index,
            {}
        )

    def _get_preview_object_name(
        self,
        source_part
    ):
        """
        Find the source FreeCAD object name.
        """
        if not source_part:
            return None

        name = source_part.get(
            "preview_object_name"
        )

        if name:
            return str(name)

        nested = source_part.get(
            "_ip_nesting",
            {}
        )

        name = nested.get(
            "preview_object_name"
        )

        if name:
            return str(name)

        return None

    def _get_source_type(self, source_part):
        if not source_part:
            return "3d"

        source_type = source_part.get(
            "source_type"
        )

        if not source_type:
            source_type = source_part.get(
                "_ip_nesting",
                {}
            ).get(
                "source_type",
                "3d"
            )

        return str(
            source_type
        ).strip().lower()

    # ------------------------------------------------------------------
    # Result document
    # ------------------------------------------------------------------

    def _create_result_document(self):
        try:
            if "Nesting_Result" in App.listDocuments():
                try:
                    App.closeDocument(
                        "Nesting_Result"
                    )
                except Exception:
                    pass

            return App.newDocument(
                "Nesting_Result"
            )

        except Exception:
            App.Console.PrintError(
                "Could not create Nesting_Result:\n"
                + traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Sheets
    # ------------------------------------------------------------------

    def _import_sheets(self):
        for index, sheet in enumerate(
            self.result_data.get(
                "sheets",
                []
            )
            or []
        ):
            try:
                sheet_name = (
                    "Sheet_%d"
                    % index
                )

                sheet_object = (
                    self._create_sheet_object(
                        sheet_name,
                        sheet
                    )
                )

                if sheet_object:
                    sheet_object.Label = (
                        "Sheet %d"
                        % (
                            index + 1
                        )
                    )

            except Exception:
                App.Console.PrintError(
                    "Failed to import sheet %d:\n"
                    % index
                    + traceback.format_exc()
                )

    def _create_sheet_object(self, name, sheet):
        if Part is None:
            return None

        sheet_type = str(
            sheet.get(
                "type",
                "rect"
            )
        ).lower()

        if sheet_type in (
            "rect",
            "rectangle",
            "rectangular"
        ):
            width = _safe_float(
                sheet.get(
                    "width"
                )
            )

            height = _safe_float(
                sheet.get(
                    "height"
                )
            )

            if width <= 0.0 or height <= 0.0:
                return None

            points = [
                App.Vector(0, 0, 0),
                App.Vector(width, 0, 0),
                App.Vector(width, height, 0),
                App.Vector(0, height, 0),
                App.Vector(0, 0, 0)
            ]

            wire = Part.makePolygon(
                points
            )

            feature = self.result_doc.addObject(
                "Part::Feature",
                name
            )

            feature.Shape = wire

            try:
                feature.ViewObject.LineColor = (
                    0.2,
                    0.2,
                    0.2
                )
                feature.ViewObject.LineWidth = 3.0
                feature.ViewObject.DisplayMode = (
                    "Wireframe"
                )
            except Exception:
                pass

            return feature

        outer_points = _points_to_vectors(
            sheet.get(
                "outer",
                []
            )
        )

        outer_points = _close_vectors(
            outer_points
        )

        if len(outer_points) < 4:
            return None

        try:
            outer_wire = Part.makePolygon(
                outer_points
            )

            sheet_shape = Part.Face(
                outer_wire
            )

            holes = sheet.get(
                "holes",
                []
            ) or []

            for hole in holes:
                hole_points = _points_to_vectors(
                    hole
                )

                hole_points = _close_vectors(
                    hole_points
                )

                if len(hole_points) < 4:
                    continue

                hole_wire = Part.makePolygon(
                    hole_points
                )

                hole_face = Part.Face(
                    hole_wire
                )

                sheet_shape = sheet_shape.cut(
                    hole_face
                )

            feature = self.result_doc.addObject(
                "Part::Feature",
                name
            )

            feature.Shape = sheet_shape

            try:
                feature.ViewObject.ShapeColor = (
                    0.75,
                    0.75,
                    0.75
                )
                feature.ViewObject.Transparency = 80
                feature.ViewObject.LineColor = (
                    0.2,
                    0.2,
                    0.2
                )
                feature.ViewObject.LineWidth = 3.0
            except Exception:
                pass

            return feature

        except Exception:
            App.Console.PrintError(
                "Failed to create polygon sheet:\n"
                + traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Parts
    # ------------------------------------------------------------------

    def _import_placements(self):
        placements = (
            self.result_data.get(
                "placements",
                []
            )
            or []
        )

        imported_count = 0

        for placement in placements:
            try:
                if not placement.get(
                    "placed",
                    True
                ):
                    continue

                source_index = placement.get(
                    "source_part_index"
                )

                if source_index is None:
                    continue

                source_part = self._get_source_part(
                    source_index
                )

                if not source_part:
                    App.Console.PrintWarning(
                        "No source part metadata for index %s.\n"
                        % str(source_index)
                    )
                    continue

                source_type = self._get_source_type(
                    source_part
                )

                if source_type == "3d":
                    ok = self._import_3d_instance(
                        source_part,
                        placement
                    )
                else:
                    ok = self._import_2d_instance(
                        source_part,
                        placement
                    )

                if ok:
                    imported_count += 1

            except Exception:
                App.Console.PrintError(
                    "Failed to import placement:\n"
                    + traceback.format_exc()
                )

        App.Console.PrintMessage(
            "Imported %d placed part(s).\n"
            % imported_count
        )

    def _result_object_name(self, placement):
        object_id = placement.get(
            "id"
        )

        if object_id:
            safe_id = str(
                object_id
            ).replace(
                " ",
                "_"
            )

            return (
                "Nesting_%s"
                % safe_id
            )

        source_index = _safe_int(
            placement.get(
                "source_part_index"
            )
        )

        instance_index = _safe_int(
            placement.get(
                "instance_index"
            )
        )

        return (
            "Nesting_part_%d_instance_%d"
            % (
                source_index,
                instance_index
            )
        )

    def _import_3d_instance(
        self,
        source_part,
        placement
    ):
        """
        Import a 3D source object.

        Transformation order:

        1. Copy source Shape.
        2. Apply nesting-to-source-shape offset
           to the copied Shape.
        3. Preserve source object's Placement.
        4. Apply result rotation around local origin.
        5. Apply result x/y translation.
        """
        try:
            if self.preview_doc is None:
                return False

            preview_object_name = (
                self._get_preview_object_name(
                    source_part
                )
            )

            if not preview_object_name:
                return False

            source_object = (
                self.preview_doc.getObject(
                    preview_object_name
                )
            )

            if source_object is None:
                App.Console.PrintWarning(
                    "Preview object '%s' was not found.\n"
                    % preview_object_name
                )
                return False

            source_shape = getattr(
                source_object,
                "Shape",
                None
            )

            if source_shape is None:
                return False

            if source_shape.isNull():
                return False

            result_name = (
                self._result_object_name(
                    placement
                )
            )

            result_object = (
                self.result_doc.addObject(
                    "Part::Feature",
                    result_name
                )
            )

            result_object.Label = (
                source_part.get(
                    "label",
                    result_name
                )
            )

            result_object.Shape = (
                source_shape.copy()
            )

            geometry_transform = (
                source_part.get(
                    "geometry_transform",
                    {}
                )
            )

            if not geometry_transform:
                geometry_transform = (
                    source_part.get(
                        "_ip_nesting",
                        {}
                    ).get(
                        "geometry_transform",
                        {}
                    )
                )

            offset = (
                geometry_transform.get(
                    "nesting_to_source_shape_offset",
                    {}
                )
            )

            offset_x = _safe_float(
                offset.get(
                    "x"
                )
            )

            offset_y = _safe_float(
                offset.get(
                    "y"
                )
            )

            offset_z = _safe_float(
                offset.get(
                    "z"
                )
            )

            # Move source geometry from source_shape_coordinates
            # into nesting_local coordinates.
            if (
                abs(offset_x) > 1e-12
                or abs(offset_y) > 1e-12
                or abs(offset_z) > 1e-12
            ):
                try:
                    result_object.Shape.translate(
                        App.Vector(
                            -offset_x,
                            -offset_y,
                            -offset_z
                        )
                    )
                except Exception:
                    App.Console.PrintWarning(
                        "Could not apply source-shape offset "
                        "for '%s'.\n"
                        % preview_object_name
                    )

            source_placement = _copy_placement(
                source_object.Placement
            )

            result_x = _safe_float(
                placement.get(
                    "x"
                )
            )

            result_y = _safe_float(
                placement.get(
                    "y"
                )
            )

            result_rotation = _safe_float(
                placement.get(
                    "rotation"
                )
            )

            # Preserve the original source placement first.
            result_object.Placement = (
                source_placement
            )

            # Apply the nesting result in the XY plane.
            result_rotation_placement = (
                App.Placement(
                    App.Vector(
                        result_x,
                        result_y,
                        0.0
                    ),
                    App.Rotation(
                        App.Vector(
                            0,
                            0,
                            1
                        ),
                        result_rotation
                    )
                )
            )

            result_object.Placement = (
                result_rotation_placement.multiply(
                    result_object.Placement
                )
            )

            try:
                result_object.addProperty(
                    "App::PropertyString",
                    "SourcePartId",
                    "IPNesting"
                )

                result_object.SourcePartId = str(
                    source_part.get(
                        "part_id",
                        ""
                    )
                )

                result_object.addProperty(
                    "App::PropertyInteger",
                    "SourcePartIndex",
                    "IPNesting"
                )

                result_object.SourcePartIndex = (
                    _safe_int(
                        placement.get(
                            "source_part_index"
                        )
                    )
                )

                result_object.addProperty(
                    "App::PropertyInteger",
                    "InstanceIndex",
                    "IPNesting"
                )

                result_object.InstanceIndex = (
                    _safe_int(
                        placement.get(
                            "instance_index"
                        )
                    )
                )

                result_object.addProperty(
                    "App::PropertyFloat",
                    "NestingRotation",
                    "IPNesting"
                )

                result_object.NestingRotation = (
                    result_rotation
                )

            except Exception:
                pass

            return True

        except Exception:
            App.Console.PrintError(
                "_import_3d_instance failed:\n"
                + traceback.format_exc()
            )
            return False

    def _import_2d_instance(
        self,
        source_part,
        placement
    ):
        """
        Import DXF/SVG/2D source geometry from sourceParts.points.
        """
        try:
            if Part is None:
                return False

            points = source_part.get(
                "points",
                []
            )

            if not points:
                return False

            transformed_points = (
                _transform_points(
                    points,
                    placement.get(
                        "x"
                    ),
                    placement.get(
                        "y"
                    ),
                    placement.get(
                        "rotation"
                    )
                )
            )

            transformed_points = _close_vectors(
                transformed_points
            )

            if len(transformed_points) < 4:
                return False

            result_name = (
                self._result_object_name(
                    placement
                )
            )

            result_object = (
                self.result_doc.addObject(
                    "Part::Feature",
                    result_name
                )
            )

            result_object.Label = (
                source_part.get(
                    "label",
                    result_name
                )
            )

            result_object.Shape = Part.makePolygon(
                transformed_points
            )

            try:
                result_object.ViewObject.LineColor = (
                    0.0,
                    0.0,
                    1.0
                )
                result_object.ViewObject.LineWidth = 2.0
                result_object.ViewObject.DisplayMode = (
                    "Wireframe"
                )
            except Exception:
                pass

            return True

        except Exception:
            App.Console.PrintError(
                "_import_2d_instance failed:\n"
                + traceback.format_exc()
            )
            return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _show_result_summary(self):
        try:
            summary = (
                self.result_data.get(
                    "summary",
                    {}
                )
            )

            status = str(
                self.result_data.get(
                    "status",
                    "success"
                )
            )

            placed_count = _safe_int(
                summary.get(
                    "placed_count"
                )
            )

            unplaced_count = _safe_int(
                summary.get(
                    "unplaced_count"
                )
            )

            utilisation = _safe_float(
                summary.get(
                    "utilisation"
                )
            )

            if status == "partial" or unplaced_count > 0:
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "Nesting completed partially",
                    (
                        "Nesting completed partially.\n\n"
                        "Placed parts: %d\n"
                        "Unplaced parts: %d\n"
                        "Utilisation: %.2f %%"
                    )
                    % (
                        placed_count,
                        unplaced_count,
                        utilisation
                    )
                )

            elif status == "failed":
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "Nesting failed",
                    (
                        "Nesting failed.\n\n"
                        "Placed parts: %d\n"
                        "Unplaced parts: %d"
                    )
                    % (
                        placed_count,
                        unplaced_count
                    )
                )

            else:
                QtGui.QMessageBox.information(
                    self.panel.form,
                    "Nesting completed",
                    (
                        "Nesting completed successfully.\n\n"
                        "Placed parts: %d\n"
                        "Utilisation: %.2f %%"
                    )
                    % (
                        placed_count,
                        utilisation
                    )
                )

        except Exception:
            pass


# ----------------------------------------------------------------------
# Process manager
# ----------------------------------------------------------------------

class NestingProcessManager(object):
    """
    Starts deepnest.exe and waits asynchronously for result.json.
    """

    def __init__(self, panel):
        self.panel = panel

        self.process = None
        self.result_timer = None

        self.input_path = None
        self.result_path = None
        self.session_path = None
        self.deepnest_path = None

        self.job_id = None
        self.process_started_at = None

        self.last_result_signature = None
        self.stable_result_checks = 0

        self._finished = False

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _module_directory(self):
        return os.path.abspath(
            os.path.dirname(__file__)
        )

    def _find_deepnest_executable(self):
        """
        Return the Deepnest executable located inside the workbench
        directory:

            <workbench>/deepnest/deepnest-v1.5.6.exe
        """
        executable_path = os.path.join(
            self._module_directory(),
            "deepnest",
            "deepnest-v1.5.6.exe"
        )

        if os.path.isfile(executable_path):
            return os.path.abspath(
                executable_path
            )

        return None
    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    def start_nesting(self, input_path):
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    QtGui.QMessageBox.warning(
                        self.panel.form,
                        "Nesting already running",
                        "A nesting process is already running."
                    )
                    return False

            self.input_path = os.path.abspath(
                input_path
            )

            # Workbench directory contains input.json and nesting_session.json.
            work_directory = os.path.dirname(
                self.input_path
            )

            self.session_path = os.path.join(
                work_directory,
                "nesting_session.json"
            )

            self.deepnest_path = (
                self._find_deepnest_executable()
            )

            if not self.deepnest_path:
                QtGui.QMessageBox.critical(
                    self.panel.form,
                    "Nesting error",
                    (
                        "Deepnest executable was not found.\n\n"
                        "Expected location:\n%s"
                    )
                    % os.path.join(
                        self._module_directory(),
                        "deepnest",
                        "deepnest-v1.5.6.exe"
                    )
                )
                return False

            # Deepnest writes result.json into its own directory.
            deepnest_directory = os.path.dirname(
                self.deepnest_path
            )

            self.result_path = os.path.join(
                deepnest_directory,
                "result.json"
            )

            if not self.deepnest_path:
                QtGui.QMessageBox.critical(
                    self.panel.form,
                    "Nesting error",
                    (
                        "deepnest.exe was not found.\n\n"
                        "Expected location:\n%s"
                    )
                    % os.path.join(
                        self._module_directory(),
                        "deepnest.exe"
                    )
                )
                return False

            session_data = _load_json_file(
                self.session_path
            )

            if isinstance(
                session_data,
                dict
            ):
                self.job_id = session_data.get(
                    "job_id"
                )

            # Delete old result before launching a new job.
            try:
                if os.path.exists(
                    self.result_path
                ):
                    os.remove(
                        self.result_path
                    )
            except Exception:
                App.Console.PrintWarning(
                    "Could not remove old result.json.\n"
                )

            self.process_started_at = (
                QtCore.QDateTime.currentDateTime()
            )

            self.last_result_signature = None
            self.stable_result_checks = 0
            self._finished = False

            # Hide/disable controls while nesting is running.
            try:
                self.panel.run_btn.setEnabled(
                    False
                )
            except Exception:
                pass

            self.process = subprocess.Popen(
                [
                    self.deepnest_path,
                    self.input_path
                ],
                cwd=deepnest_directory,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT
            )

            self.result_timer = QtCore.QTimer(
                self.panel.form
            )

            self.result_timer.setInterval(
                500
            )

            self.result_timer.timeout.connect(
                self._check_result
            )

            self.result_timer.start()

            App.Console.PrintMessage(
                "deepnest.exe started.\n"
            )

            App.Console.PrintMessage(
                "Waiting for result.json...\n"
            )

            return True

        except Exception:
            App.Console.PrintError(
                "NestingProcessManager.start_nesting failed:\n"
                + traceback.format_exc()
            )

            self._finish_failure(
                "Could not start deepnest.exe."
            )

            return False

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _check_result(self):
        try:
            if self._finished:
                return

            if not self.result_path:
                self._finish_failure(
                    "Result path is not configured."
                )
                return

            if not os.path.exists(
                self.result_path
            ):
                if (
                    self.process is not None
                    and self.process.poll() is not None
                ):
                    return_code = self.process.returncode

                    self._finish_failure(
                        (
                            "deepnest.exe finished without "
                            "creating result.json.\n\n"
                            "Exit code: %s"
                        )
                        % str(return_code)
                    )

                return

            # Ignore result.json from before this process.
            try:
                result_mtime = os.path.getmtime(
                    self.result_path
                )

                started_timestamp = (
                    self.process_started_at.toSecsSinceEpoch()
                )

                if result_mtime < started_timestamp:
                    return

            except Exception:
                pass

            signature = _read_file_signature(
                self.result_path
            )

            if signature is None:
                return

            if signature != self.last_result_signature:
                self.last_result_signature = signature
                self.stable_result_checks = 0
                return

            self.stable_result_checks += 1

            # Wait until the file has remained unchanged
            # for at least two polling cycles.
            if self.stable_result_checks < 2:
                return

            result_data = _load_json_file(
                self.result_path
            )

            if not isinstance(
                result_data,
                dict
            ):
                return

            result_job_id = result_data.get(
                "job_id"
            )

            if (
                self.job_id
                and result_job_id
                and str(result_job_id)
                != str(self.job_id)
            ):
                self._finish_failure(
                    (
                        "The job_id in result.json does not "
                        "match nesting_session.json."
                    )
                )
                return

            session_data = _load_json_file(
                self.session_path
            )

            importer = NestingResultImporter(
                self.panel
            )

            imported = importer.import_result(
                result_data=result_data,
                session_data=session_data
            )

            if imported:
                self._finish_success()
            else:
                self._finish_failure(
                    "Could not import result.json."
                )

        except Exception:
            self._finish_failure(
                "Result processing failed:\n%s"
                % traceback.format_exc()
            )

    # ------------------------------------------------------------------
    # Finish
    # ------------------------------------------------------------------

    def _stop_timer(self):
        try:
            if self.result_timer is not None:
                self.result_timer.stop()
        except Exception:
            pass

    def _restore_ui(self):
        self._stop_timer()

        try:
            self.panel.run_btn.setEnabled(
                True
            )
        except Exception:
            pass

    def _finish_success(self):
        if self._finished:
            return

        self._finished = True
        self._restore_ui()

        App.Console.PrintMessage(
            "Nesting result imported successfully.\n"
        )

    def _finish_failure(self, message):
        if self._finished:
            return

        self._finished = True
        self._restore_ui()

        App.Console.PrintError(
            "Nesting failed: %s\n"
            % str(message)
        )

        try:
            QtGui.QMessageBox.critical(
                self.panel.form,
                "Nesting failed",
                str(message)
            )
        except Exception:
            pass