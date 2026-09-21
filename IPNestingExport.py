"""
IPNestingExport - Nesting execution logic extracted from IPNestingGui.
Exports nesting CLI input.json and the FreeCAD nesting_session.json mapping.
"""
from IPNestingLanguages import tr

import FreeCAD as App
from PySide import QtGui, QtCore
import FreeCADGui as Gui
import json
import os
import traceback
import math
import uuid
from datetime import datetime


# Compare two XY point pairs within tolerance; return False for invalid input.
def _points_equal_2d(a, b, tol=1e-6):
    try:
        return (
            abs(float(a[0]) - float(b[0])) <= tol
            and abs(float(a[1]) - float(b[1])) <= tol
        )
    except Exception:
        return False


# Sample an edge into rounded XY points and remove consecutive duplicates.
def _discretize_edge_2d(edge, deflection=0.01):
    pts2d = []

    try:
        try:
            pts = edge.discretize(
                Deflection=deflection
            )
        except Exception:
            pts = edge.discretize(20)

        for pt in pts:
            try:
                pts2d.append([
                    round(float(pt.x), 6),
                    round(float(pt.y), 6)
                ])
            except Exception:
                try:
                    pts2d.append([
                        round(float(pt.X), 6),
                        round(float(pt.Y), 6)
                    ])
                except Exception:
                    pass

        cleaned = []

        for p in pts2d:
            if not cleaned or not _points_equal_2d(
                cleaned[-1],
                p
            ):
                cleaned.append(p)

        return cleaned

    except Exception:
        App.Console.PrintError(
            tr('discretize_edge_2d_failed')
            + traceback.format_exc()
        )
        return []


# Stitch sampled wire edges by matching endpoints, appending unmatched chunks as a fallback.
def _extract_wire_points_ordered(wire, deflection=0.01):
    """
    Stitch sampled wire edges into an XY contour by matching endpoints.

    Edges are stitched by matching endpoints and reversed
    when necessary. If no endpoint matches, append the next chunk and
    log a warning; continuity is not guaranteed in that fallback.
    """
    try:
        edges = list(
            getattr(wire, "Edges", []) or []
        )

        if not edges:
            return []

        chunks = []

        for edge in edges:
            pts = _discretize_edge_2d(
                edge,
                deflection=deflection
            )

            if len(pts) >= 2:
                chunks.append(pts)

        if not chunks:
            return []

        ordered = list(chunks.pop(0))

        while chunks:
            last_pt = ordered[-1]
            found_idx = None
            found_pts = None

            for index, pts in enumerate(chunks):
                start_pt = pts[0]
                end_pt = pts[-1]

                if _points_equal_2d(
                    last_pt,
                    start_pt
                ):
                    found_idx = index
                    found_pts = pts
                    break

                if _points_equal_2d(
                    last_pt,
                    end_pt
                ):
                    found_idx = index
                    found_pts = list(reversed(pts))
                    break

            if found_pts is None:
                App.Console.PrintWarning(
                    tr('wire_stitching_fallback_used_edge_chain_was_not_continuous')
                )

                found_idx = 0
                found_pts = chunks[0]

            chunks.pop(found_idx)

            if (
                ordered
                and found_pts
                and _points_equal_2d(
                    ordered[-1],
                    found_pts[0]
                )
            ):
                ordered.extend(found_pts[1:])
            else:
                ordered.extend(found_pts)

        if (
            len(ordered) > 1
            and _points_equal_2d(
                ordered[0],
                ordered[-1]
            )
        ):
            ordered = ordered[:-1]

        cleaned = []

        for point in ordered:
            if (
                not cleaned
                or not _points_equal_2d(
                    cleaned[-1],
                    point
                )
            ):
                cleaned.append(point)

        return cleaned

    except Exception:
        App.Console.PrintError(
            tr('extract_wire_points_ordered_failed')
            + traceback.format_exc()
        )
        return []


# Read one integer rotation count from a table item.
def _read_rotation_count(item, default=1):
    """
    Read one integer rotation count from a table item.
    """
    try:
        if item is None:
            return int(default)

        value = int(
            float(
                str(item.text()).strip()
            )
        )

        return max(
            1,
            min(3600, value)
        )

    except Exception:
        return int(default)


# Read a positive deflection directly from panel.res text, without converting display units.
def _read_boundary_deflection(panel, default=0.01):
    """
    Read positive boundary deflection directly from panel.res text.

    This helper does not convert display units to millimetres.
    """
    try:
        if panel is None or not hasattr(panel, "res"):
            return float(default)

        txt = panel.res.text().strip()
        value = float(txt)

        if value <= 0.0:
            return float(default)

        return float(value)

    except Exception:
        return float(default)


# Remove consecutive duplicate points from a polygon.
def _remove_duplicate_points(points, tolerance=1e-6):
    """
    Remove consecutive duplicate points from a polygon.
    """
    cleaned = []

    for point in points or []:
        try:
            x = float(point[0])
            y = float(point[1])
        except Exception:
            continue

        if not cleaned:
            cleaned.append([x, y])
            continue

        previous = cleaned[-1]

        if (
            abs(previous[0] - x) > tolerance
            or abs(previous[1] - y) > tolerance
        ):
            cleaned.append([x, y])

    if len(cleaned) > 1:
        first = cleaned[0]
        last = cleaned[-1]

        if (
            abs(first[0] - last[0]) <= tolerance
            and abs(first[1] - last[1]) <= tolerance
        ):
            cleaned.pop()

    return cleaned


# Round a supplied XY pair without applying Placement; use [0, 0] for invalid input.
def _transform_point_without_translation(obj, point):
    """
    Round the supplied XY pair to six decimals without applying Placement.

    In this project the current visible orientation is already reflected
    in obj.Shape after applying grain direction or Custom angle.

    Do not apply obj.Placement here. Applying Placement again would rotate
    the already rotated geometry a second time.

    The preview-grid position is removed later by _normalize_polygon().
    Invalid input returns [0.0, 0.0].
    """
    try:
        return [
            round(float(point[0]), 6),
            round(float(point[1]), 6)
        ]

    except Exception:
        return [
            0.0,
            0.0
        ]


# Move polygon coordinates so the minimum X/Y position becomes 0/0.
def _normalize_polygon(points):
    """
    Move polygon coordinates so the minimum X/Y position becomes 0/0.

    This removes the temporary position of the object in the
    Nesting_Preview grid, while preserving its current orientation.
    """
    if not points:
        return []

    try:
        min_x = min(
            float(point[0])
            for point in points
        )

        min_y = min(
            float(point[1])
            for point in points
        )

        return [
            {
                "x": round(
                    float(point[0]) - min_x,
                    6
                ),
                "y": round(
                    float(point[1]) - min_y,
                    6
                )
            }
            for point in points
        ]

    except Exception:
        return []


# Extract the current visible 2D outer contour from a preview object.
def _extract_part_points(obj, deflection=0.1):
    """
    Extract the current visible 2D outer contour from a preview object.

    The current orientation is already contained in obj.Shape.
    Do not apply obj.Placement again because the geometry may already
    include the rotation caused by:

    - normal part rotation;
    - grain direction X;
    - grain direction Y;
    - Custom angle.

    The temporary preview-grid translation is removed by
    _normalize_polygon().
    """
    candidates = []

    try:
        if obj is None:
            return []

        shape = getattr(
            obj,
            "Shape",
            None
        )

        if shape is None:
            return []

        # Make sure the Shape is valid and current.
        try:
            if shape.isNull():
                return []
        except Exception:
            pass

        # Prefer closed wires.
        wires = list(
            getattr(shape, "Wires", []) or []
        )

        for wire in wires:
            try:
                if (
                    hasattr(wire, "isClosed")
                    and not wire.isClosed()
                ):
                    continue
            except Exception:
                continue

            points = _extract_wire_points_ordered(
                wire,
                deflection=deflection
            )

            if len(points) < 3:
                continue

            # IMPORTANT:
            # Do not apply obj.Placement here.
            # The current Shape already reflects the visible state.
            transformed = [
                [
                    round(float(point[0]), 6),
                    round(float(point[1]), 6)
                ]
                for point in points
            ]

            candidates.append(
                transformed
            )

        # Fallback to horizontal faces.
        if not candidates:
            for face in list(
                getattr(shape, "Faces", []) or []
            ):
                try:
                    normal = face.normalAt(
                        0.5,
                        0.5
                    )

                    if abs(
                        normal.dot(
                            App.Vector(0, 0, 1)
                        )
                    ) <= 0.9:
                        continue

                except Exception:
                    continue

                try:
                    points = _extract_wire_points_ordered(
                        face.OuterWire,
                        deflection=deflection
                    )
                except Exception:
                    points = []

                if len(points) < 3:
                    continue

                # IMPORTANT:
                # Do not apply obj.Placement here.
                transformed = [
                    [
                        round(float(point[0]), 6),
                        round(float(point[1]), 6)
                    ]
                    for point in points
                ]

                candidates.append(
                    transformed
                )

        if not candidates:
            return []

        # Calculate the absolute shoelace area used to select the largest projected contour.
        def polygon_area(points):
            area = 0.0

            for index in range(len(points)):
                x1, y1 = points[index]
                x2, y2 = points[
                    (index + 1) % len(points)
                ]

                area += (
                    x1 * y2
                    - x2 * y1
                )

            return abs(area) * 0.5

        # Select the largest contour as the outer contour.
        outer = max(
            candidates,
            key=polygon_area
        )

        outer = _remove_duplicate_points(
            outer
        )

        return _normalize_polygon(
            outer
        )

    except Exception:
        App.Console.PrintError(
            tr('extract_part_points_failed')
            + traceback.format_exc()
        )
        return []


# Read a non-negative floating-point value from a Qt widget.
def _read_float_widget(widget, default):
    """
    Read a non-negative floating-point value from a Qt widget.
    """
    try:
        value = float(
            str(
                widget.text()
            ).strip().replace(",", ".")
        )

        return max(
            0.0,
            value
        )

    except Exception:
        return float(default)


# Read a boolean value from the project's False/True combo box.
def _read_bool_combo(widget, default=False):
    """
    Read a boolean value from the project's False/True combo box.
    """
    try:
        return widget.currentIndex() == 1
    except Exception:
        return bool(default)


# Convert XY pairs or x/y dictionaries to rounded CLI point arrays.
def _points_to_cli_points(points):
    """
    Convert XY pairs or x/y dictionaries to rounded CLI point arrays.

    The nesting CLI accepts [x, y] arrays natively.
    """
    result = []

    for point in points or []:
        try:
            if isinstance(point, dict):
                x = float(
                    point.get("x", 0.0)
                )
                y = float(
                    point.get("y", 0.0)
                )
            else:
                if len(point) < 2:
                    continue

                x = float(point[0])
                y = float(point[1])

            result.append([
                round(x, 6),
                round(y, 6)
            ])

        except Exception:
            continue

    return result


# Return all user-selected non-outer contours as hole polygons.
def _get_selected_material_holes(material):
    """
    Return all user-selected non-outer contours as hole polygons.

    The contours list is the primary source of truth.
    The legacy 'holes' field is used only as a compatibility
    fallback.
    """
    contours = material.get("contours")

    if contours:
        selected_holes = []

        for contour in contours:
            try:
                if contour.get(
                    "is_outer",
                    False
                ):
                    continue

                if not contour.get(
                    "selected",
                    False
                ):
                    continue

                polygon = contour.get(
                    "polygon"
                ) or []

                if len(polygon) >= 3:
                    selected_holes.append(
                        polygon
                    )

            except Exception:
                continue

        return selected_holes

    return [
        hole
        for hole in (
            material.get("holes") or []
        )
        if isinstance(
            hole,
            (list, tuple)
        )
        and len(hole) >= 3
    ]


# Convert one IP-Nesting material record to a nesting CLI sheet record.
def _material_to_cli_sheet(material):
    """
    Convert one IP-Nesting material record
    to a nesting CLI sheet record.
    """
    material = material or {}

    material_type = str(
        material.get("type", "")
    ).strip().lower()

    try:
        quantity = int(
            material.get(
                "quantity",
                material.get("count", 1)
            )
        )
    except Exception:
        quantity = 1

    quantity = max(
        1,
        quantity
    )

    if material_type in (
        "rectangular",
        "rect",
        "sheet",
        "rectangle"
    ):
        return {
            "width": float(
                material.get("width", 0.0)
            ),
            "height": float(
                material.get("height", 0.0)
            ),
            "quantity": quantity
        }

    outer = material.get(
        "outer"
    ) or []

    if not outer:
        polygons = material.get(
            "polygons"
        ) or []

        if polygons:
            outer = polygons[0]

    holes = _get_selected_material_holes(
        material
    )

    return {
        "points": _points_to_cli_points(
            outer
        ),
        "holes": [
            _points_to_cli_points(
                hole
            )
            for hole in holes
        ],
        "quantity": quantity
    }

# Return a JSON-safe value.
def _safe_json_value(value, default=None):
    """
    Return a JSON-safe value.
    """
    try:
        json.dumps(value)
        return value
    except Exception:
        return default


# Safely read an optional FreeCAD object property.
def _get_object_property(obj, property_name, default=None):
    """
    Safely read an optional FreeCAD object property.
    """
    try:
        if obj is not None and hasattr(obj, property_name):
            return getattr(obj, property_name)
    except Exception:
        pass

    return default


# Read the source-type property or infer DXF/SVG from the label, defaulting to 3d.
def _get_source_type(obj):
    """
    Return 3d, dxf or svg from the source property or label.

    Unrecognized or absent metadata defaults to 3d.
    """
    source_type = _get_object_property(
        obj,
        "IPNestingSourceType",
        None
    )

    if source_type:
        value = str(source_type).strip().lower()

        if value in ("3d", "dxf", "svg"):
            return value

    label = str(
        getattr(obj, "Label", "")
        or ""
    ).lower()

    if label.startswith("dxf:"):
        return "dxf"

    if label.startswith("svg:"):
        return "svg"

    return "3d"


# Read grain/custom-angle checkboxes and axis; retain defaults for the numeric custom angle.
def _get_grain_metadata(panel, row):
    """
    Read grain and Custom angle state from one table row.
    """
    metadata = {
        "enabled": False,
        "axis": "X",
        "custom_angle_enabled": False,
        "custom_angle_deg": None,
        "normalized_axis": "X",
    }

    try:
        grain_widget = panel.table.cellWidget(
            row,
            4
        )

        if grain_widget:
            grain_checkbox = grain_widget.findChild(
                QtGui.QCheckBox
            )

            grain_combo = grain_widget.findChild(
                QtGui.QComboBox
            )

            metadata["enabled"] = bool(
                grain_checkbox
                and grain_checkbox.isChecked()
            )

            if grain_combo:
                axis = str(
                    grain_combo.currentText()
                    or "X"
                ).strip().upper()

                if axis in ("X", "Y"):
                    metadata["axis"] = axis

    except Exception:
        pass

    try:
        custom_widget = panel.table.cellWidget(
            row,
            5
        )

        if custom_widget:
            custom_checkbox = custom_widget.findChild(
                QtGui.QCheckBox
            )

            metadata["custom_angle_enabled"] = bool(
                custom_checkbox
                and custom_checkbox.isChecked()
            )

    except Exception:
        pass

    return metadata

# Write nesting CLI input.json and nesting_session.json from the panel and preview geometry.
def execute_nesting(panel):
    """
    Export the panel state to the nesting CLI input.json and nesting_session.json.

    Returns True when both files are written, or False on failure.
    Dimension text is currently read directly, while the payload declares mm.
    """
    try:
        App.Console.PrintMessage(
            tr('starting_nesting_cli_input_export')
        )
        
        job_id = str(
            uuid.uuid4()
        )

        created_at = datetime.utcnow().isoformat(
            timespec="milliseconds"
        ) + "Z"

        spacing = panel.get_dimension_value_mm(
            panel.spacing,
            0.0
        )

        sheet_margin = panel.get_dimension_value_mm(
            panel.sheet_margin,
            0.0
        )

        boundary_resolution = panel.get_dimension_value_mm(
            panel.res,
            0.1
        )

        # Hole-to-part clearance. "same" reuses the part spacing;
        # "custom" uses the offcut dialog's custom clearance value
        # (already stored in millimetres).
        try:
            if str(
                getattr(
                    panel,
                    "offcut_clearance_mode",
                    "same"
                )
            ) == "custom":
                hole_clearance = float(
                    getattr(
                        panel,
                        "offcut_custom_clearance",
                        0.0
                    ) or 0.0
                )
            else:
                hole_clearance = float(spacing)
        except Exception:
            hole_clearance = float(spacing)

        try:
            threads = max(
                1,
                int(
                    panel.cpu_cores_combo.currentText()
                )
            )
        except Exception:
            threads = 1

        # Export every added sheet and offcut.
        sheets = []

        for material in getattr(
            panel,
            "offcuts",
            []
        ) or []:
            try:
                sheet = _material_to_cli_sheet(
                    material
                )
                
                sheet["_ip_nesting"] = {
                    "source_sheet_index": len(sheets),
                    "material_id": material.get(
                        "id"
                    ),
                    "material_type": material.get(
                        "type"
                    ),
                    "label": material.get(
                        "label"
                    ),
                    "grain": material.get(
                        "grain",
                        "None"
                    ),
                    "path": material.get(
                        "path",
                        ""
                    )
                }

                if "width" in sheet:
                    if (
                        float(
                            sheet.get(
                                "width",
                                0.0
                            )
                        ) > 0.0
                        and float(
                            sheet.get(
                                "height",
                                0.0
                            )
                        ) > 0.0
                    ):
                        sheets.append(sheet)

                elif len(
                    sheet.get(
                        "points",
                        []
                    )
                ) >= 3:
                    sheets.append(sheet)

            except Exception:
                App.Console.PrintError(
                    tr('failed_to_convert_material_to_cli_sheet')
                    + traceback.format_exc()
                )

        # Get the preview document.
        p_doc = (
            App.getDocument(
                panel.preview_doc_name
            )
            if panel.preview_doc_name
            in App.listDocuments()
            else None
        )

        if not p_doc:
            App.Console.PrintError(
                tr('preview_document_not_found_s')
                % str(
                    panel.preview_doc_name
                )
            )
            return False
            
        try:
            p_doc.recompute()
        except Exception:
            App.Console.PrintError(
                tr('preview_document_recompute_failed_before_export')
                + traceback.format_exc()
            )

        try:
            App.Console.PrintMessage(
                tr('preview_document_recomputed_before_polygon_extraction')
            )
        except Exception:
            pass

        parts = []

        data_rows = max(
            0,
            panel.table.rowCount()
            - panel.control_rows
        )

        for row in range(data_rows):
            try:
                name_item = panel.table.item(
                    row,
                    0
                )

                qty_item = panel.table.item(
                    row,
                    1
                )

                rotation_item = panel.table.item(
                    row,
                    2
                )

                if not name_item:
                    continue

                try:
                    quantity = max(
                        1,
                        int(
                            str(
                                qty_item.text()
                            ).strip()
                        )
                    )
                except Exception:
                    quantity = 1

                rotations = _read_rotation_count(
                    rotation_item,
                    default=1
                )

                primary_name = name_item.data(
                    QtCore.Qt.UserRole
                )

                # Prefer the first object from the full
                # object-name list.
                try:
                    names_data = name_item.data(
                        QtCore.Qt.UserRole + 1
                    )

                    if names_data:
                        if isinstance(
                            names_data,
                            list
                        ):
                            names = names_data
                        else:
                            names = json.loads(
                                names_data
                            )

                        if names:
                            primary_name = names[0]

                except Exception:
                    pass

                if not primary_name:
                    App.Console.PrintWarning(
                        tr('part_row_d_has_no_preview_object_name')
                        % row
                    )
                    continue

                obj = p_doc.getObject(
                    primary_name
                )

                if not obj:
                    App.Console.PrintWarning(
                        tr('preview_object_not_found_s')
                        % str(
                            primary_name
                        )
                    )
                    continue

                # FIX:
                # Log the actual Placement used for export.
                try:
                    App.Console.PrintMessage(
                        tr('exporting_s_with_current_placement_s')
                        % (
                            str(primary_name),
                            str(obj.Placement)
                        )
                    )
                except Exception:
                    pass

                points = _extract_part_points(
                    obj,
                    deflection=boundary_resolution
                )

                if len(points) < 3:
                    App.Console.PrintWarning(
                        tr('no_valid_polygon_points_found_for_s')
                        % str(
                            primary_name
                        )
                    )
                    continue

                grain_metadata = _get_grain_metadata(
                    panel,
                    row
                )

                source_type = _get_source_type(
                    obj
                )

                part_id = "part_%d" % len(parts)

                parts.append({
                    # Fields consumed by the nesting CLI.
                    "id": part_id,
                    "points": points,
                    "quantity": quantity,
                    "rotations": rotations,

                    # Metadata consumed by the nesting CLI
                    # and by IPNestingResult.py.
                    "_ip_nesting": {
                        "job_id": job_id,
                        "source_part_index": len(parts),
                        "part_id": part_id,
                        "label": str(
                            name_item.text()
                        ),
                        "preview_document": str(
                            panel.preview_doc_name
                        ),
                        "preview_object_name": str(
                            primary_name
                        ),
                        "source_type": source_type,
                        "grain": grain_metadata,
                        "placement": {
                            "base": {
                                "x": float(
                                    obj.Placement.Base.x
                                ),
                                "y": float(
                                    obj.Placement.Base.y
                                ),
                                "z": float(
                                    obj.Placement.Base.z
                                )
                            },
                            "rotation": {
                                "axis": {
                                    "x": float(
                                        obj.Placement.Rotation.Axis.x
                                    ),
                                    "y": float(
                                        obj.Placement.Rotation.Axis.y
                                    ),
                                    "z": float(
                                        obj.Placement.Rotation.Axis.z
                                    )
                                },
                                "angle_rad": float(
                                    obj.Placement.Rotation.Angle
                                ),
                                "angle_deg": float(
                                    obj.Placement.Rotation.Angle
                                ) * 180.0 / math.pi
                            }
                        },
                        "shape_bbox": {
                            "min_x": float(
                                obj.Shape.BoundBox.XMin
                            ),
                            "max_x": float(
                                obj.Shape.BoundBox.XMax
                            ),
                            "min_y": float(
                                obj.Shape.BoundBox.YMin
                            ),
                            "max_y": float(
                                obj.Shape.BoundBox.YMax
                            ),
                            "min_z": float(
                                obj.Shape.BoundBox.ZMin
                            ),
                            "max_z": float(
                                obj.Shape.BoundBox.ZMax
                            )
                        },
                        "instance_prefix": (
                            part_id
                            + "_instance_"
                        )
                    }
                })

            except Exception:
                App.Console.PrintError(
                    tr('failed_to_export_part_row_d_s')
                    % (
                        row,
                        traceback.format_exc()
                    )
                )

        payload = {
            "units": "mm",
            "schema_version": 1,
            "job_id": job_id,
            "created_at": created_at,

            "_ip_nesting": {
                "application": "FreeCAD",
                "workbench": "IP-Nesting",
                "preview_document": str(
                    panel.preview_doc_name
                ),
                "result_file": "result.json",
                "units": "mm"
            },
            "config": {
                # Only the bitmap algorithm is exposed; NFP is used
                # internally for collision checking only.
                "algorithm": "bitmap",
                "mode": "first",
                # Ignore the global rotation grid; each part uses its
                # own per-part rotation.
                "perPartRotationsOnly": True,
                "resolution": 1.0,
                "threads": threads,
                "spacing": spacing,
                "partToSheet": sheet_margin,
                "partToHole": hole_clearance
            },
            "sheets": sheets,
            "parts": parts,
            "output": {
                "json": "result.json"
            }
        }

        script_dir = os.path.abspath(
            os.path.dirname(__file__)
        )

        output_path = os.path.join(
            script_dir,
            "input.json"
        )
        
        session_path = os.path.join(
            script_dir,
            "nesting_session.json"
        )

        session_payload = {
            "schema_version": 1,
            "job_id": job_id,
            "created_at": created_at,
            "input_file": output_path,
            "result_file": os.path.join(
                script_dir,
                "result.json"
            ),
            "preview_document": str(
                panel.preview_doc_name
            ),
            "parts": [
                {
                    "source_part_index": index,
                    "part_id": part.get(
                        "_ip_nesting",
                        {}
                    ).get(
                        "part_id",
                        "part_%d" % index
                    ),
                    "preview_object_name": part.get(
                        "_ip_nesting",
                        {}
                    ).get(
                        "preview_object_name"
                    ),
                    "label": part.get(
                        "_ip_nesting",
                        {}
                    ).get(
                        "label"
                    ),
                    "source_type": part.get(
                        "_ip_nesting",
                        {}
                    ).get(
                        "source_type",
                        "unknown"
                    ),
                    "quantity": part.get(
                        "quantity",
                        1
                    )
                }
                for index, part in enumerate(parts)
            ],
            "sheets": [
                sheet.get(
                    "_ip_nesting",
                    {}
                )
                for sheet in sheets
            ]
        }

        with open(
            session_path,
            "w",
            encoding="utf-8"
        ) as session_file:
            json.dump(
                session_payload,
                session_file,
                indent=2,
                ensure_ascii=False
            )
        
        with open(
            output_path,
            "w",
            encoding="utf-8"
        ) as output_file:
            json.dump(
                payload,
                output_file,
                indent=2,
                ensure_ascii=False
            )

        App.Console.PrintMessage(
            tr('nesting_cli_input_json_written_to_s')
            % output_path
        )

        return True

    except Exception:
        App.Console.PrintError(
            tr('execute_nesting_failed')
            + traceback.format_exc()
        )

        return False
