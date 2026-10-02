"""
IPNestingResult.py

Runs the nesting CLI, waits asynchronously for result.json and imports
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
from IPNestingLanguages import tr

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


def _normalize_result(data, session):
    """Adapt CLI sheets[].parts[] while retaining legacy result support."""
    import copy
    result = copy.deepcopy(data)
    if "placements" in result:
        return result
    if not isinstance(result.get("sheets"), list):
        raise ValueError("Unsupported nesting result schema")
    sources = {int(p["source_part_index"]): dict(p) for p in session.get("parts", [])}
    placements = []
    for sheet_index, sheet in enumerate(result["sheets"]):
        if "points" in sheet:
            sheet["type"] = "polygon"
            sheet["outer"] = sheet["points"]
        sheet["sheet_instance_index"] = sheet_index
        for part in sheet.get("parts", []):
            meta = part.get("_ip_nesting", {})
            source_index = meta.get("source_part_index", part.get("source"))
            if source_index is None:
                raise ValueError("A placed part has no source identity")
            source_index = int(source_index)
            source = sources.setdefault(source_index, {})
            source.update(meta)
            source["source_part_index"] = source_index
            placement = dict(part, source_part_index=source_index,
                             instance_index=len(placements), sheet_instance_index=sheet_index)
            placement["absolute_points"] = part.get("points", [])
            placement["absolute_holes"] = part.get("holes", [])
            placements.append(placement)
    if int(result.get("placed", len(placements))) != len(placements):
        raise ValueError("Result placement count does not match its geometry")
    result["placements"] = placements
    result["sourceParts"] = list(sources.values())
    result["summary"] = dict(placed_count=len(placements),
                             unplaced_count=len(result.get("unplaced", [])),
                             utilisation=result.get("utilization", 0))
    return result


# ----------------------------------------------------------------------
# General helpers
# ----------------------------------------------------------------------

# Convert a value to float, falling back to the numeric default on conversion failure.
def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


# Convert a value to int, falling back to the numeric default on conversion failure.
def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


# Read and parse a JSON file.
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


# Return a file signature used to detect when result.json is stable.
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


# Convert either:
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


# Convert XY point records to FreeCAD vectors at the supplied Z height.
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


# Rotate x/y around the local origin counter-clockwise.
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


# Apply result placement to nesting-local points.
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


# Close a FreeCAD polygon if necessary.
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


# Make a safe copy of a FreeCAD Placement.
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

# Imports result.json into Nesting_Result.
class NestingResultImporter(object):
    """
    Imports result.json into Nesting_Result.
    """

    # Store the panel and initialize result/session data and source lookup maps.
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

    # Resolve source metadata, recreate the result document, import geometry and show the result
    # summary.
    def import_result(
        self,
        result_data,
        session_data=None,
        show_summary=True
    ):
        try:
            if not isinstance(
                result_data,
                dict
            ):
                App.Console.PrintError(
                    tr('result_json_root_must_be_an_object')
                )
                return False

            previous_doc = self.result_doc
            self.result_data = _normalize_result(result_data, session_data or {})
            self.session_data = (
                session_data
                if isinstance(session_data, dict)
                else {}
            )

            self._source_shapes = {}
            self._prepare_maps()

            self.preview_doc = (
                self._get_preview_document()
            )

            if self.preview_doc is None:
                App.Console.PrintError(
                    tr('nesting_preview_document_was_not_found')
                )
                return False

            self.result_doc = (
                self._create_result_document()
            )

            if self.result_doc is None:
                return False

            sheet_count = self._import_sheets()
            imported_count = self._import_placements()
            expected = sum(1 for p in self.result_data.get("placements", []) if p.get("placed", True))
            if sheet_count != len(self.result_data.get("sheets", [])) or imported_count != expected:
                App.closeDocument(self.result_doc.Name)
                self.result_doc = previous_doc
                raise ValueError("Result import was incomplete; previous result preserved")

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

            if previous_doc is not None and previous_doc.Name in App.listDocuments():
                App.closeDocument(previous_doc.Name)
            if show_summary:
                self._show_result_summary()

            return True

        except Exception:
            App.Console.PrintError(
                tr('nestingresultimporter_import_result_failed')
                + traceback.format_exc()
            )
            return False

    # ------------------------------------------------------------------
    # Maps
    # ------------------------------------------------------------------

    # Prepare lookup maps for source parts, session parts and sheets.
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

    # Find the preview document named by result metadata, falling back to the panel document.
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

    # Return source metadata.
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

    # Find the source FreeCAD object name.
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

    # Read direct or nested source-type metadata, defaulting to 3d.
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

    # Close any existing Nesting_Result document and create a replacement.
    def _create_result_document(self):
        try:
            return App.newDocument(
                "Nesting_Result"
            )

        except Exception:
            App.Console.PrintError(
                tr('could_not_create_nesting_result')
                + traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Sheets
    # ------------------------------------------------------------------

    # Create and label a result object for each returned sheet record.
    def _import_sheets(self):
        imported_count = 0
        self.sheet_groups = {}
        display_x = 0.0
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
                    group = self.result_doc.addObject("App::Part", "SheetGroup_%d" % index)
                    group.Label = tr('sheet_d') % (index + 1)
                    group.addObject(sheet_object)
                    group.Placement.Base = App.Vector(display_x, 0, 0)
                    display_x += sheet_object.Shape.BoundBox.XLength + 50.0
                    self.sheet_groups[index] = group
                    imported_count += 1
                    sheet_object.Label = (
                        tr('sheet_d')
                        % (
                            index + 1
                        )
                    )

            except Exception:
                App.Console.PrintError(
                    tr('failed_to_import_sheet_d')
                    % index
                    + traceback.format_exc()
                )

        return imported_count

    # Create a rectangle wire or a polygon face with holes at the result document origin.
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
                tr('failed_to_create_polygon_sheet')
                + traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Parts
    # ------------------------------------------------------------------

    # Import placed 3D or 2D instances using source metadata and log the successful count.
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
                        tr('no_source_part_metadata_for_index_s')
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
                    obj = self.result_doc.getObject(self._result_object_name(placement))
                    group = self.sheet_groups.get(int(placement.get("sheet_instance_index", 0)))
                    if group is not None and obj is not None:
                        group.addObject(obj)
                    imported_count += 1

            except Exception:
                App.Console.PrintError(
                    tr('failed_to_import_placement')
                    + traceback.format_exc()
                )

        App.Console.PrintMessage(
            tr('imported_d_placed_part_s')
            % imported_count
        )

        return imported_count

    # Build an instance name from the placement ID or source/instance indices.
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

    # Import a 3D source object.
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
                    tr('preview_object_s_was_not_found')
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

            cached = self._source_shapes.get(preview_object_name)
            if cached is None:
                from IPNestingExport import (_extract_part_candidate_wires, _normalize_polygon,
                                             _polygons_same_2d, _points_min_xy)
                candidates = _extract_part_candidate_wires(source_object, source_part.get("boundary_resolution", .01))
                if source_part.get("points") and not _polygons_same_2d(
                        _normalize_polygon(candidates[0]), source_part["points"], 2e-5):
                    raise ValueError("Source geometry changed while nesting was running")
                ox, oy = _points_min_xy(candidates[0])
                cached = source_shape.copy()
                cached.translate(App.Vector(-ox, -oy, -cached.BoundBox.ZMin))
                self._source_shapes[preview_object_name] = cached
            shape = cached.copy()
            result_x = _safe_float(placement.get("x"))
            result_y = _safe_float(placement.get("y"))
            result_rotation = _safe_float(placement.get("rotation"))
            transform = App.Placement(App.Vector(result_x, result_y, 0),
                                      App.Rotation(App.Vector(0, 0, 1), result_rotation))
            shape.Placement = transform.multiply(shape.Placement)
            result_object.Shape = shape

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
                tr('import_3d_instance_failed')
                + traceback.format_exc()
            )
            return False

    # Import DXF/SVG/2D source geometry from sourceParts.points.
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

            if "absolute_points" in placement:
                contours = [placement["absolute_points"]] + placement.get("absolute_holes", [])
                wires = []
                for contour in contours:
                    vectors = _close_vectors(_points_to_vectors(contour))
                    if len(vectors) < 4:
                        return False
                    wires.append(Part.makePolygon(vectors))
                obj = self.result_doc.addObject("Part::Feature", self._result_object_name(placement))
                obj.Label = source_part.get("label", obj.Name)
                obj.Shape = Part.makeCompound(wires)
                return True

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
                tr('import_2d_instance_failed')
                + traceback.format_exc()
            )
            return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    # Show the engine-reported status, placement counts and utilisation in a message box.
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
                    tr('nesting_completed_partially'),
                    (
                        tr('nesting_completed_partially_placed_parts_d_unplaced_parts_d_utilisation_2f')
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
                    tr('nesting_failed'),
                    (
                        tr('nesting_failed_placed_parts_d_unplaced_parts_d')
                    )
                    % (
                        placed_count,
                        unplaced_count
                    )
                )

            else:
                QtGui.QMessageBox.information(
                    self.panel.form,
                    tr('nesting_completed'),
                    (
                        tr('nesting_completed_successfully_placed_parts_d_utilisation_2f')
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

# Starts the configured nesting CLI and waits asynchronously for result.json.
class NestingProcessManager(object):
    """
    Starts the configured nesting CLI and waits asynchronously for result.json.
    """

    # Initialize process paths, job identity, polling state and the completion guard.
    def __init__(self, panel):
        self.panel = panel

        self.process = None
        self.result_timer = None

        self.input_path = None
        self.result_path = None
        self.session_path = None
        self.cli_path = None

        self.job_id = None
        self.process_started_at = None

        self.last_result_signature = None
        self.stable_result_checks = 0

        self._finished = False
        self._cancelled = False
        self._imported_signature = None
        self.importer = None

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def stop_nesting(self):
        self._cancelled = True
        if self.is_running():
            process = self.process
            process.terminate()
            QtCore.QTimer.singleShot(2000, lambda: process.kill() if process.poll() is None else None)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    # Return the absolute directory containing the result-processing module.
    def _module_directory(self):
        return os.path.abspath(
            os.path.dirname(__file__)
        )

    # Return the nesting CLI executable located inside the workbench directory:
    def _find_nesting_cli_executable(self):
        """
        Return the nesting CLI executable located inside the workbench
        directory:

            <workbench>/nesting-cli/clinesting.exe
        """
        executable_path = os.path.join(
            self._module_directory(),
            "nesting-cli",
            "clinesting.exe"
        )

        if os.path.isfile(executable_path):
            return os.path.abspath(
                executable_path
            )

        return None
    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    # Launch the bundled nesting CLI and poll its result file while disabling Run
    # Nesting.
    def start_nesting(self, input_path):
        try:
            if self.process is not None:
                if self.process.poll() is None:
                    QtGui.QMessageBox.warning(
                        self.panel.form,
                        tr('nesting_already_running'),
                        tr('a_nesting_process_is_already_running')
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

            self.cli_path = (
                self._find_nesting_cli_executable()
            )

            if not self.cli_path:
                QtGui.QMessageBox.critical(
                    self.panel.form,
                    tr('nesting_error'),
                    (
                        tr('nesting_cli_executable_was_not_found_expected_location_s')
                    )
                    % os.path.join(
                        self._module_directory(),
                        "nesting-cli",
                        "clinesting.exe"
                    )
                )
                return False

            # The nesting CLI resolves relative output paths against the
            # directory containing input.json, so result.json lands next to
            # the input file, not next to the executable.
            self.result_path = os.path.join(
                work_directory,
                "result.json"
            )

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
                    tr('could_not_remove_old_result_json')
                )

            self.process_started_at = (
                QtCore.QDateTime.currentDateTime()
            )

            self.last_result_signature = None
            self.stable_result_checks = 0
            self._finished = False
            self._cancelled = False
            self._imported_signature = None
            self.importer = NestingResultImporter(self.panel)

            # Hide/disable controls while nesting is running.
            try:
                self.panel.run_btn.setEnabled(False)
                self.panel.stop_btn.setEnabled(True)
            except Exception:
                pass

            self.process = subprocess.Popen(
                [
                    self.cli_path,
                    "--input",
                    self.input_path
                ],
                cwd=work_directory,
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
                tr('nesting_cli_started')
            )

            App.Console.PrintMessage(
                tr('waiting_for_result_json')
            )

            return True

        except Exception:
            App.Console.PrintError(
                tr('nestingprocessmanager_start_nesting_failed')
                + traceback.format_exc()
            )

            self._finish_failure(
                tr('could_not_start_the_nesting_cli')
            )

            return False

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    # Wait for a stable result file, check its job ID and import it or report a detected
    # failure.
    def _check_result(self):
        try:
            if self._finished:
                return

            running = self.is_running()
            if not running and getattr(self, "_cancelled", False) and not os.path.exists(self.result_path or ""):
                self._finish_success()
                return
            if not running and self.process is not None and self.process.returncode and not getattr(self, "_cancelled", False):
                self._finish_failure("Nesting CLI exited with code %s" % self.process.returncode)
                return

            if not self.result_path:
                self._finish_failure(
                    tr('result_path_is_not_configured')
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
                            tr('nesting_cli_finished_without_creating_result_json_exit_code_s')
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

            if not isinstance(result_data, dict):
                if not running:
                    self._finish_failure("Nesting CLI produced an invalid result file")
                return

            result_job_id = result_data.get(
                "job_id"
            )

            if (
                self.job_id
                and str(result_job_id)
                != str(self.job_id)
            ):
                self._finish_failure(
                    (
                        tr('the_job_id_in_result_json_does_not_match_nesting_session_json')
                    )
                )
                return

            session_data = _load_json_file(
                self.session_path
            )

            if signature == getattr(self, "_imported_signature", None):
                if not running:
                    self._finish_success()
                return
            if self.importer is None:
                self.importer = NestingResultImporter(self.panel)
            imported = self.importer.import_result(
                result_data=result_data, session_data=session_data,
                show_summary=not running)

            if imported:
                self._imported_signature = signature
                if not running:
                    self._finish_success()
            else:
                self._finish_failure(
                    tr('could_not_import_result_json')
                )

        except Exception:
            self._finish_failure(
                tr('result_processing_failed_s')
                % traceback.format_exc()
            )

    # ------------------------------------------------------------------
    # Finish
    # ------------------------------------------------------------------

    # Stop result polling when a timer exists.
    def _stop_timer(self):
        try:
            if self.result_timer is not None:
                self.result_timer.stop()
        except Exception:
            pass

    # Stop result polling and re-enable the Run Nesting button.
    def _restore_ui(self):
        self._stop_timer()

        try:
            self.panel.run_btn.setEnabled(True)
            self.panel.stop_btn.setEnabled(False)
        except Exception:
            pass

    # Mark the job finished once, restore the UI and log successful import.
    def _finish_success(self):
        if self._finished:
            return

        self._finished = True
        self._restore_ui()

        App.Console.PrintMessage(
            tr('nesting_result_imported_successfully')
        )

    # Mark the job finished once, restore the UI and display the failure message.
    def _finish_failure(self, message):
        if self._finished:
            return

        if self.is_running():
            self.stop_nesting()
        self._finished = True
        self._restore_ui()

        App.Console.PrintError(
            tr('nesting_failed_s')
            % str(message)
        )

        try:
            QtGui.QMessageBox.critical(
                self.panel.form,
                tr('nesting_failed'),
                str(message)
            )
        except Exception:
            pass
