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
            source.update({key: value for key, value in meta.items()
                           if key not in ("shape_brep", "normalization_offset", "display")})
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

            if self.preview_doc is None and not all(p.get("shape_brep") for p in self.source_parts_by_index.values()):
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

            if previous_doc is not None and previous_doc.Name in App.listDocuments():
                App.closeDocument(previous_doc.Name)
            self._show_result_view()
            if show_summary:
                self._show_result_summary()

            return True

        except Exception:
            App.Console.PrintError(
                tr('nestingresultimporter_import_result_failed')
                + traceback.format_exc()
            )
            return False

    def _show_result_view(self):
        """Fit the result's own view after activation and viewport layout."""
        name = self.result_doc.Name
        try:
            Gui.activateDocument(name)
            view = Gui.getDocument(name).activeView()
            view.viewTop()
            view.fitAll()
            if QtGui.QApplication.instance() is not None:
                QtCore.QTimer.singleShot(0, lambda: self._fit_result_view(name))
        except Exception:
            pass  # Geometry import also supports headless FreeCAD.

    def _fit_result_view(self, name):
        if name not in App.listDocuments() or self.result_doc is None:
            return  # A newer continuous result or a closed document superseded it.
        try:
            if self.result_doc.Name != name:
                return
            Gui.getDocument(name).activeView().fitAll()
        except Exception:
            pass

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

                if source_part.get("shape_brep") or source_type == "3d":
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
        """Place the captured BREP using only normalization and a Z rotation."""
        try:
            preview_object_name = self._get_preview_object_name(source_part)
            cache_key = source_part.get("part_id", preview_object_name)
            cached = self._source_shapes.get(cache_key)
            if cached is None:
                if source_part.get("shape_brep"):
                    cached = Part.Shape()
                    cached.importBrepFromString(source_part["shape_brep"])
                    if cached.isNull() or not cached.isValid():
                        raise ValueError("Invalid job geometry snapshot")
                    offset = source_part["normalization_offset"]
                    cached.translate(App.Vector(*[-float(v) for v in offset]))
                else:
                    # Compatibility with older sessions that did not capture BREP.
                    if self.preview_doc is None or not preview_object_name:
                        return False
                    source_object = self.preview_doc.getObject(preview_object_name)
                    if source_object is None:
                        return False
                    from IPNestingExport import (_extract_part_candidate_wires, _normalize_polygon,
                                                 _polygons_same_2d, _points_min_xy)
                    candidates = _extract_part_candidate_wires(source_object, source_part.get("boundary_resolution", .01))
                    if source_part.get("points") and not _polygons_same_2d(
                            _normalize_polygon(candidates[0]), source_part["points"], 2e-5):
                        raise ValueError("Source geometry changed while nesting was running")
                    ox, oy = _points_min_xy(candidates[0])
                    cached = source_object.Shape.copy()
                    cached.translate(App.Vector(-ox, -oy, -cached.BoundBox.ZMin))
                self._source_shapes[cache_key] = cached
            shape = cached.copy()
            result_name = self._result_object_name(placement)
            result_object = self.result_doc.addObject("Part::Feature", result_name)
            result_object.Label = source_part.get("label", result_name)
            result_x = _safe_float(placement.get("x"))
            result_y = _safe_float(placement.get("y"))
            result_rotation = _safe_float(placement.get("rotation"))
            transform = App.Placement(App.Vector(result_x, result_y, 0),
                                      App.Rotation(App.Vector(0, 0, 1), result_rotation))
            shape.Placement = transform.multiply(shape.Placement)
            result_object.Shape = shape
            display = source_part.get("display", {})
            try:
                view = result_object.ViewObject
                if "shape_color" in display:
                    view.ShapeColor = tuple(display["shape_color"])
                if "line_color" in display:
                    view.LineColor = tuple(display["line_color"])
                if "transparency" in display:
                    view.Transparency = display["transparency"]
                if display.get("face_colors"):
                    view.DiffuseColor = [tuple(color) for color in display["face_colors"]]
            except (AttributeError, TypeError):
                pass

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
    """One asynchronous workbench job, with strict identity and cooperative stop."""
    def __init__(self, panel):
        self.panel = panel
        self.process = None
        self.result_timer = None
        self.wait_dialog = None
        self.input_path = self.result_path = self.session_path = self.cli_path = None
        self.cancel_path = None
        self.job_id = None
        self.session_data = {}
        self.last_result_signature = None
        self.stable_result_checks = 0
        self._imported_signature = None
        self._finished = False
        self._cancelled = False
        self._job_lock = None
        self._log = None
        self.importer = None
        self._preparing = False
        self._export_steps = None
        self._launch_task = None
        self._launch_timer = None
        self._job_generation = 0
        form = getattr(panel, 'form', None)
        if form is not None and hasattr(form, 'destroyed'):
            form.destroyed.connect(self.shutdown)

    def _module_directory(self):
        return os.path.abspath(os.path.dirname(__file__))

    def _find_nesting_cli_executable(self):
        from IPNestingRuntime import find_executable
        return find_executable(self._module_directory())

    def _process_alive(self):
        return self.process is not None and self.process.poll() is None

    def is_running(self):
        return (self._preparing or self._process_alive()
                or (self._launch_task is not None and not self._launch_task.finished.is_set()))

    def _show_wait(self):
        from IPNestingWaitDialog import NestingWaitDialog
        if self.wait_dialog is None:
            self.wait_dialog = NestingWaitDialog(self.stop_nesting, getattr(self.panel, "form", None))
            self.wait_dialog.setWindowModality(QtCore.Qt.WindowModal)
            self.wait_dialog.show()
        self._update_wait_message()
        try:
            self.panel.run_btn.setEnabled(False)
            self.panel.stop_btn.setEnabled(True)
        except AttributeError:
            pass

    def _update_wait_message(self, config=None):
        if self.wait_dialog is None:
            return
        if config is None:
            from IPNestingExport import build_nesting_config, _read_search_mode, _read_line_edit_float
            config = build_nesting_config(
                mode=_read_search_mode(self.panel),
                time_limit_seconds=_read_line_edit_float(self.panel, 'time_limit_edit', 0))
        self.wait_dialog.set_nesting_mode(config.get('mode', 'first'), config.get('timeLimitSeconds', 0))

    def _queue_job_callback(self, callback, after_paint=False):
        generation = self._job_generation
        def guarded():
            if generation == self._job_generation and not self._finished and not self._cancelled:
                callback()
        if after_paint:
            self.wait_dialog.start_after_paint(guarded)
        else:
            QtCore.QTimer.singleShot(0, guarded)

    def prepare_and_start(self, input_path):
        """Paint progress first, then export one part per Qt event-loop turn."""
        if self.is_running() or not self.prepare_job():
            return False
        self._job_generation += 1
        self._finished = self._cancelled = False
        self._preparing = True
        self._show_wait()
        self._queue_job_callback(lambda: self._begin_export(input_path), after_paint=True)
        return True

    def _begin_export(self, input_path):
        if self._finished or self._cancelled:
            return
        from IPNestingExport import export_nesting_steps
        self._export_steps = export_nesting_steps(self.panel)
        self._advance_export(input_path)

    def _advance_export(self, input_path):
        if self._finished or self._cancelled:
            return
        try:
            next(self._export_steps)
        except StopIteration as done:
            self._export_steps = None
            if self._finished or self._cancelled:
                return
            if done.value is True:
                self.start_nesting(input_path)
            else:
                self._preparing = False
                self._finish_failure(tr('failed_to_generate_input_json'))
            return
        except Exception as exc:
            self._finish_failure(str(exc))
            return
        self._queue_job_callback(lambda: self._advance_export(input_path))

    def prepare_job(self):
        """Lock before the exporter writes input/session in the workbench."""
        if self.is_running():
            return False
        if self._job_lock is not None:
            return True
        lock = QtCore.QLockFile(os.path.join(self._module_directory(), ".ipnesting-job.lock"))
        if not lock.tryLock(0):
            QtGui.QMessageBox.warning(self.panel.form, tr('nesting_already_running'),
                                      tr('a_nesting_process_is_already_running'))
            return False
        self._job_lock = lock
        return True

    def release_job(self):
        if self.is_running():
            return
        if self._job_lock is not None:
            self._job_lock.unlock()
            self._job_lock = None

    def _release_after_exit(self):
        if self.is_running():
            QtCore.QTimer.singleShot(100, self._release_after_exit)
        else:
            self.release_job()
            self._close_log()
            self._remove_cancel_marker()

    def _close_log(self):
        if self._log is not None:
            self._log.close()
            self._log = None

    def _remove_cancel_marker(self):
        if self.cancel_path and os.path.isfile(self.cancel_path):
            try:
                os.remove(self.cancel_path)
            except OSError:
                pass

    def stop_nesting(self):
        if self._cancelled:
            return
        self._cancelled = True
        if self._launch_task is not None:
            self._launch_task.cancel()
        if self._export_steps is not None:
            try:
                self._export_steps.close()
            except ValueError:
                pass  # A nested error dialog may still be inside the generator.
            self._export_steps = None
        self._preparing = False
        if not self._process_alive():
            if not self._finished:
                self._finish_success()
            return
        if self._process_alive():
            process = self.process
            try:
                with open(self.cancel_path, "w", encoding="utf-8") as stream:
                    stream.write(self.job_id)
            except (OSError, TypeError):
                process.terminate()
            # Native file cancellation also works with CREATE_NO_WINDOW.
            QtCore.QTimer.singleShot(5000, lambda: process.kill() if process.poll() is None else None)

    def shutdown(self, *args):
        """A closed task panel must not leave a worker or file lock behind."""
        self.stop_nesting()
        try:
            if self.result_timer is not None:
                self.result_timer.stop()
            if self.wait_dialog is not None:
                self.wait_dialog.finish()
                self.wait_dialog = None
        except RuntimeError:
            pass  # Qt parent may already have destroyed its child widgets.
        self._release_after_exit()

    def start_nesting(self, input_path):
        if self._process_alive() or (self._launch_task is not None and not self._launch_task.finished.is_set()):
            return False
        if self._job_lock is None and not self.prepare_job():
            return False
        if self._cancelled and self._preparing:
            return False
        if not self._preparing:
            self._job_generation += 1
        self._finished = self._cancelled = False
        self._preparing = True
        self._show_wait()
        self._queue_job_callback(lambda: self._start_cli(input_path), after_paint=True)
        return True

    def _start_cli(self, input_path):
        try:
            if self._finished or self._cancelled:
                return
            self.input_path = os.path.abspath(input_path)
            directory = self._module_directory()
            if self.input_path != os.path.join(directory, "input.json"):
                raise ValueError("input.json must be in the workbench directory")
            self.session_path = os.path.join(directory, "nesting_session.json")
            self.result_path = os.path.join(directory, "result.json")
            input_data = _load_json_file(self.input_path)
            self.session_data = _load_json_file(self.session_path)
            if not isinstance(input_data, dict) or not isinstance(self.session_data, dict):
                raise ValueError("The nesting job files could not be read")
            self.job_id = input_data.get("job_id")
            import re
            if (not isinstance(self.job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.job_id)
                    or self.session_data.get("job_id") != self.job_id):
                raise ValueError("Input and session job identifiers do not match")
            expected_cancel = ".clinesting-cancel-" + self.job_id
            output = input_data.get("output", {})
            if output.get("json") != "result.json" or output.get("cancelFile") != expected_cancel:
                raise ValueError("Unexpected workbench result/cancellation paths")
            self._update_wait_message(input_data.get('config', {}))
            self.cancel_path = os.path.join(directory, expected_cancel)
            self.cli_path = self._find_nesting_cli_executable()
            if not self.cli_path:
                from IPNestingRuntime import executable_candidates
                raise RuntimeError(tr('nesting_cli_executable_was_not_found_expected_location_s')
                                   % executable_candidates(directory)[0])
            # Only remove this job's marker and the old root snapshot. The CLI
            # locks and clears results history when continuous mode starts.
            self._remove_cancel_marker()
            if os.path.lexists(self.result_path):
                if os.path.islink(self.result_path):
                    raise ValueError("result.json must not be a filesystem link")
                os.remove(self.result_path)
            self.last_result_signature = None
            self.stable_result_checks = 0
            self._imported_signature = None
            self._finished = self._cancelled = False
            self.importer = NestingResultImporter(self.panel)
            self._log = open(os.path.join(directory, "clinesting.log"), "wb")
            from IPNestingAsync import BackgroundCall, discard_process
            command = [self.cli_path, "--input", self.input_path]
            log = self._log
            self._launch_task = BackgroundCall(lambda cancelled: subprocess.Popen(
                command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                discard=discard_process)
            self._launch_task.start()
            self._launch_timer = QtCore.QTimer(getattr(self.panel, "form", None))
            self._launch_timer.setInterval(20)
            self._launch_timer.timeout.connect(self._poll_launch)
            self._launch_timer.start()
        except Exception as exc:
            App.Console.PrintError(traceback.format_exc())
            self._finish_failure(str(exc))
            return False

    def _poll_launch(self):
        outcome = self._launch_task.poll() if self._launch_task is not None else None
        if outcome is None or self._finished or self._cancelled:
            return
        self._launch_timer.stop()
        process, error = outcome
        self._launch_task = None
        if error:
            self._finish_failure(error)
            return
        self.process = process
        self._preparing = False
        if self.wait_dialog is not None:
            self.wait_dialog.hide()
            self.wait_dialog.setWindowModality(QtCore.Qt.NonModal)
            self.wait_dialog.show()
        self.result_timer = QtCore.QTimer(getattr(self.panel, "form", None))
        self.result_timer.setInterval(250)
        self.result_timer.timeout.connect(self._check_result)
        self.result_timer.start()
        App.Console.PrintMessage(tr('nesting_cli_started'))

    def _failure_detail(self):
        try:
            with open(os.path.join(self._module_directory(), "clinesting.log"), "rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 4096))
                return stream.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def _check_result(self):
        try:
            if self._finished:
                return
            running = self.is_running()
            if not running:
                self._close_log()
                if self.process is not None and self.process.returncode and not self._cancelled:
                    self._finish_failure("Nesting CLI exited with code %s\n%s" %
                                         (self.process.returncode, self._failure_detail()))
                    return
            if not self.result_path or not os.path.exists(self.result_path):
                if not running:
                    if self._cancelled:
                        self._finish_success()
                    else:
                        self._finish_failure(tr('nesting_cli_finished_without_creating_result_json_exit_code_s')
                                             % str(self.process.returncode if self.process else "unknown"))
                return
            signature = _read_file_signature(self.result_path)
            if signature is None:
                return
            if signature != self.last_result_signature:
                self.last_result_signature = signature
                self.stable_result_checks = 0
                return
            self.stable_result_checks += 1
            if self.stable_result_checks < 2:
                return
            if signature == self._imported_signature:
                if not running:
                    self._finish_success()
                return
            result_data = _load_json_file(self.result_path)
            if not isinstance(result_data, dict):
                if not running:
                    self._finish_failure("Nesting CLI produced an invalid result file")
                return
            if self.job_id and result_data.get("job_id") != self.job_id:
                # A foreign file must never replace this job's displayed result.
                if not running:
                    self._finish_failure(tr('the_job_id_in_result_json_does_not_match_nesting_session_json'))
                return
            if self.importer is None:
                self.importer = NestingResultImporter(self.panel)
            session = self.session_data or _load_json_file(self.session_path)
            if not self.importer.import_result(result_data=result_data, session_data=session, show_summary=False):
                self._finish_failure(tr('could_not_import_result_json'))
                return
            self._imported_signature = signature
            if not running:
                self._finish_success()
        except Exception:
            self._finish_failure(tr('result_processing_failed_s') % traceback.format_exc())

    def _restore_ui(self):
        try:
            if self._launch_timer is not None:
                self._launch_timer.stop()
            if self.result_timer is not None:
                self.result_timer.stop()
            if self.wait_dialog is not None:
                self.wait_dialog.finish()
                self.wait_dialog.deleteLater()
                self.wait_dialog = None
            self.panel.run_btn.setEnabled(True)
            self.panel.stop_btn.setEnabled(False)
        except (AttributeError, RuntimeError):
            pass  # A destroyed Qt parent must still cancel/reap its worker.
        self._release_after_exit()

    def _finish_success(self):
        if self._finished:
            return
        self._finished = True
        self._restore_ui()
        if self._imported_signature is not None:
            App.Console.PrintMessage(tr('nesting_result_imported_successfully'))
        elif self._cancelled:
            App.Console.PrintMessage(tr('common.cancel') + "\n")

    def _finish_failure(self, message):
        if self._finished:
            return
        self._finished = True
        self.stop_nesting()
        self._restore_ui()
        App.Console.PrintError(tr('nesting_failed_s') % str(message))
        try:
            QtGui.QMessageBox.critical(self.panel.form, tr('nesting_failed'), str(message))
        except (AttributeError, TypeError):
            pass
