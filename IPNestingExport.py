"""
IPNestingExport - Nesting execution logic extracted from IPNestingGui.
Exports nesting configuration to libnest2d_export.json.
"""

import FreeCAD as App
from PySide import QtGui, QtCore
import json
import os
import traceback
import math


def _points_equal_2d(a, b, tol=1e-6):
    try:
        return abs(float(a[0]) - float(b[0])) <= tol and abs(float(a[1]) - float(b[1])) <= tol
    except Exception:
        return False


def _discretize_edge_2d(edge, deflection=0.01):
    pts2d = []
    try:
        try:
            pts = edge.discretize(Deflection=deflection)
        except Exception:
            pts = edge.discretize(20)

        for pt in pts:
            try:
                pts2d.append([round(float(pt.x), 6), round(float(pt.y), 6)])
            except Exception:
                try:
                    pts2d.append([round(float(pt.X), 6), round(float(pt.Y), 6)])
                except Exception:
                    pass

        # remove consecutive duplicates
        cleaned = []
        for p in pts2d:
            if not cleaned or not _points_equal_2d(cleaned[-1], p):
                cleaned.append(p)
        return cleaned
    except Exception:
        App.Console.PrintError("_discretize_edge_2d failed:\n" + traceback.format_exc())
        return []


def _extract_wire_points_ordered(wire, deflection=0.01):
    """
    Build a properly ordered 2D contour from wire edges.
    Edges are stitched by matching endpoints; reversed when needed.
    """
    try:
        edges = list(getattr(wire, "Edges", []) or [])
        if not edges:
            return []

        # discretize all edges first
        chunks = []
        for e in edges:
            pts = _discretize_edge_2d(e, deflection=deflection)
            if len(pts) >= 2:
                chunks.append(pts)

        if not chunks:
            return []

        ordered = list(chunks.pop(0))

        while chunks:
            last_pt = ordered[-1]
            found_idx = None
            found_pts = None

            for i, pts in enumerate(chunks):
                start_pt = pts[0]
                end_pt = pts[-1]

                if _points_equal_2d(last_pt, start_pt):
                    found_idx = i
                    found_pts = pts
                    break

                if _points_equal_2d(last_pt, end_pt):
                    found_idx = i
                    found_pts = list(reversed(pts))
                    break

            if found_pts is None:
                # fallback: append nearest chunk as-is to avoid total failure
                # but log it because topology is suspicious
                App.Console.PrintWarning("Wire stitching fallback used: edge chain was not continuous.\n")
                found_idx = 0
                found_pts = chunks[0]

            chunks.pop(found_idx)

            if ordered and found_pts and _points_equal_2d(ordered[-1], found_pts[0]):
                ordered.extend(found_pts[1:])
            else:
                ordered.extend(found_pts)

        # remove duplicate closing point if present
        if len(ordered) > 1 and _points_equal_2d(ordered[0], ordered[-1]):
            ordered = ordered[:-1]

        # remove consecutive duplicates again
        cleaned = []
        for p in ordered:
            if not cleaned or not _points_equal_2d(cleaned[-1], p):
                cleaned.append(p)

        return cleaned

    except Exception:
        App.Console.PrintError("_extract_wire_points_ordered failed:\n" + traceback.format_exc())
        return []

def _read_rotation_count(item, default=1):
    """
    Read one integer rotation count from a table item.
    """
    try:
        if item is None:
            return int(default)

        value = int(float(str(item.text()).strip()))

        # Keep the broad existing range for now.
        # A Deepnest-specific maximum can be added later.
        return max(1, min(5000, value))

    except Exception:
        return int(default)
        
def _read_boundary_deflection(panel, default=0.01):
    """
    Read boundary resolution/deflection from the UI.
    Returns a positive float, falling back to default if invalid.
    """
    try:
        if panel is None or not hasattr(panel, "res"):
            return float(default)

        txt = panel.res.text().strip()
        val = float(txt)

        if val <= 0.0:
            return float(default)

        return float(val)
    except Exception:
        return float(default)


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


def _transform_point_without_translation(obj, point):
    """
    Apply the preview object's rotation to a local 2D point.

    The preview object's translation is intentionally ignored because
    Deepnest must receive local part geometry, not the temporary preview
    grid position.
    """
    try:
        vector = App.Vector(
            float(point[0]),
            float(point[1]),
            0.0
        )

        transformed = obj.Placement.Rotation.multVec(vector)

        return [
            round(float(transformed.x), 6),
            round(float(transformed.y), 6)
        ]

    except Exception:
        return [
            round(float(point[0]), 6),
            round(float(point[1]), 6)
        ]


def _normalize_polygon(points):
    """
    Move polygon coordinates so the minimum X/Y position becomes 0/0.
    """
    if not points:
        return []

    min_x = min(float(point[0]) for point in points)
    min_y = min(float(point[1]) for point in points)

    return [
        {
            "x": round(float(point[0]) - min_x, 6),
            "y": round(float(point[1]) - min_y, 6)
        }
        for point in points
    ]

def _extract_part_points(obj, deflection=0.1):
    """
    Extract the main 2D outer contour from a preview object.

    The object is assumed to have already been oriented in FreeCAD.
    Placement translation is ignored; placement rotation is applied.
    """
    candidates = []

    try:
        shape = getattr(obj, "Shape", None)

        if shape is None:
            return []

        # Prefer wires because DXF/SVG imports may contain wires
        # without usable faces.
        wires = list(getattr(shape, "Wires", []) or [])

        for wire in wires:
            try:
                if hasattr(wire, "isClosed") and not wire.isClosed():
                    continue
            except Exception:
                continue

            points = _extract_wire_points_ordered(
                wire,
                deflection=deflection
            )

            if len(points) >= 3:
                transformed = [
                    _transform_point_without_translation(obj, point)
                    for point in points
                ]

                candidates.append(transformed)

        # Fallback to horizontal faces.
        if not candidates:
            for face in list(getattr(shape, "Faces", []) or []):
                try:
                    normal = face.normalAt(0.5, 0.5)

                    if abs(normal.dot(App.Vector(0, 0, 1))) <= 0.9:
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

                if len(points) >= 3:
                    transformed = [
                        _transform_point_without_translation(obj, point)
                        for point in points
                    ]

                    candidates.append(transformed)

        if not candidates:
            return []

        # Use the largest contour as the outer part contour.
        def polygon_area(points):
            area = 0.0

            for index in range(len(points)):
                x1, y1 = points[index]
                x2, y2 = points[(index + 1) % len(points)]
                area += x1 * y2 - x2 * y1

            return abs(area) * 0.5

        outer = max(
            candidates,
            key=polygon_area
        )

        outer = _remove_duplicate_points(outer)

        return _normalize_polygon(outer)

    except Exception:
        App.Console.PrintError(
            "_extract_part_points failed:\n"
            + traceback.format_exc()
        )
        return []

def _read_float_widget(widget, default):
    """
    Read a non-negative floating-point value from a Qt widget.
    """
    try:
        value = float(
            str(widget.text()).strip().replace(",", ".")
        )

        return max(0.0, value)

    except Exception:
        return float(default)


def _read_bool_combo(widget, default=False):
    """
    Read a boolean value from the project's False/True combo box.
    """
    try:
        return widget.currentIndex() == 1
    except Exception:
        return bool(default)


def _material_to_deepnest_sheet(material):
    """
    Convert one IP-Nesting material record to a Deepnest sheet record.
    """
    material_type = str(
        material.get("type", "")
    ).strip().lower()

    quantity = max(
        1,
        int(
            material.get(
                "quantity",
                material.get("count", 1)
            )
        )
    )

    if material_type in (
        "rectangular",
        "rect",
        "sheet",
        "rectangle"
    ):
        return {
            "type": "rect",
            "width": float(material.get("width", 0.0)),
            "height": float(material.get("height", 0.0)),
            "quantity": quantity
        }

    outer = material.get("outer") or []

    if not outer:
        polygons = material.get("polygons") or []

        if polygons:
            outer = polygons[0]

    holes = material.get("holes") or []

    return {
        "type": "polygon",
        "outer": [
            {
                "x": round(float(point[0]), 6),
                "y": round(float(point[1]), 6)
            }
            for point in outer
            if len(point) >= 2
        ],
        "holes": [
            [
                {
                    "x": round(float(point[0]), 6),
                    "y": round(float(point[1]), 6)
                }
                for point in hole
                if len(point) >= 2
            ]
            for hole in holes
        ],
        "quantity": quantity
    }


def execute_nesting(panel):
    """
    Export the current FreeCAD nesting state to Deepnest input.json.
    """
    try:
        App.Console.PrintMessage(
            "Starting Deepnest input export...\n"
        )

        spacing = _read_float_widget(
            panel.spacing,
            0.0
        )

        sheet_margin = _read_float_widget(
            panel.sheet_margin,
            0.0
        )

        boundary_resolution = _read_float_widget(
            panel.res,
            0.1
        )

        threads = 1

        try:
            threads = max(
                1,
                int(panel.cpu_cores_combo.currentText())
            )
        except Exception:
            pass

        time_ratio = _read_float_widget(
            panel.deepnest_time_ratio,
            0.5
        )

        population_size = max(
            1,
            int(
                float(
                    str(
                        panel.deepnest_population_size.text()
                    ).strip()
                )
            )
        )

        mutation_rate = max(
            0,
            int(
                float(
                    str(
                        panel.deepnest_mutation_rate.text()
                    ).strip()
                )
            )
        )

        export_with_sheet_boundaries = _read_bool_combo(
            panel.deepnest_export_sheet_boundaries,
            False
        )

        export_with_sheets_space = _read_bool_combo(
            panel.deepnest_export_sheets_space,
            False
        )

        sheets = []

        for material in getattr(panel, "offcuts", []) or []:
            try:
                sheet = _material_to_deepnest_sheet(material)

                if sheet.get("type") == "rect":
                    if (
                        sheet["width"] > 0.0
                        and sheet["height"] > 0.0
                    ):
                        sheets.append(sheet)
                else:
                    if len(sheet.get("outer", [])) >= 3:
                        sheets.append(sheet)

            except Exception:
                App.Console.PrintError(
                    "Failed to convert material to Deepnest sheet:\n"
                    + traceback.format_exc()
                )

        p_doc = (
            App.getDocument(panel.preview_doc_name)
            if panel.preview_doc_name in App.listDocuments()
            else None
        )

        if not p_doc:
            App.Console.PrintMessage(
                "No preview document found; nothing to export.\n"
            )
            return

        parts = []

        data_rows = (
            panel.table.rowCount()
            - panel.control_rows
        )

        for row in range(data_rows):
            try:
                name_item = panel.table.item(row, 0)
                qty_item = panel.table.item(row, 1)
                rotation_item = panel.table.item(row, 2)

                if not name_item:
                    continue

                quantity = 1

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
                    pass

                rotations = _read_rotation_count(
                    rotation_item,
                    default=1
                )

                primary_name = name_item.data(
                    QtCore.Qt.UserRole
                )

                try:
                    names_data = name_item.data(
                        QtCore.Qt.UserRole + 1
                    )

                    if names_data:
                        if isinstance(names_data, list):
                            names = names_data
                        else:
                            names = json.loads(names_data)

                        if names:
                            primary_name = names[0]

                except Exception:
                    pass

                obj = (
                    p_doc.getObject(primary_name)
                    if primary_name
                    else None
                )

                if not obj:
                    App.Console.PrintMessage(
                        "Preview object not found: %s\n"
                        % str(primary_name)
                    )
                    continue

                points = _extract_part_points(
                    obj,
                    deflection=boundary_resolution
                )

                if len(points) < 3:
                    App.Console.PrintMessage(
                        "No valid polygon points found for: %s\n"
                        % str(primary_name)
                    )
                    continue

                parts.append({
                    "points": points,
                    "quantity": quantity,
                    "rotations": rotations
                })

            except Exception:
                App.Console.PrintError(
                    "Failed to export part row %d:\n%s\n"
                    % (
                        row,
                        traceback.format_exc()
                    )
                )

        payload = {
            "settings": {
                "units": "mm",
                "spacing": spacing,
                "partToSheet": sheet_margin,
                "partToHole": 0.0,
                "curveTolerance": boundary_resolution,
                "placementType": "gravity",
                "simplify": False,
                "threads": threads,
                "useSvgPreProcessor": False,
                "scale": 1.0,
                "endpointTolerance": boundary_resolution,
                "dxfImportScale": 1.0,
                "dxfExportScale": 1.0,
                "exportWithSheetBoundboarders": export_with_sheet_boundaries,
                "exportWithSheetsSpace": export_with_sheets_space,
                "exportWithSheetsSpaceValue": _read_float_widget(
                    panel.deepnest_export_sheets_space_value,
                    0.13888
                ),
                "mergeLines": True,
                "timeRatio": time_ratio,
                "populationSize": population_size,
                "mutationRate": mutation_rate,
                "useQuantityFromFileName": False
            },
            "sheets": sheets,
            "parts": parts,
            "autoStart": True,
            "output": {
                "resultJson": "result.json"
            }
        }

        script_dir = os.path.abspath(
            os.path.dirname(__file__)
        )

        output_path = os.path.join(
            script_dir,
            "input.json"
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
            "Deepnest input written to: %s\n"
            % output_path
        )

    except Exception:
        App.Console.PrintError(
            "execute_nesting failed:\n"
            + traceback.format_exc()
        )