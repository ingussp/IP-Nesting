"""
IPNestingExport - Nesting execution logic extracted from IPNestingGui.
Exports nesting configuration to libnest2d_export.json.
"""

import FreeCAD as App
from PySide import QtGui, QtCore
import FreeCADGui as Gui
import json
import os
import traceback
import math


def _points_equal_2d(a, b, tol=1e-6):
    try:
        return (
            abs(float(a[0]) - float(b[0])) <= tol
            and abs(float(a[1]) - float(b[1])) <= tol
        )
    except Exception:
        return False


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
            "_discretize_edge_2d failed:\n"
            + traceback.format_exc()
        )
        return []


def _extract_wire_points_ordered(wire, deflection=0.01):
    """
    Build a properly ordered 2D contour from wire edges.

    Edges are stitched by matching endpoints and reversed
    when necessary.
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
                    "Wire stitching fallback used: "
                    "edge chain was not continuous.\n"
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
            "_extract_wire_points_ordered failed:\n"
            + traceback.format_exc()
        )
        return []


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
            min(5000, value)
        )

    except Exception:
        return int(default)


def _read_boundary_deflection(panel, default=0.01):
    """
    Read boundary resolution/deflection from the UI.
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
    Return the point exactly as it exists in the current preview Shape.

    In this project the current visible orientation is already reflected
    in obj.Shape after applying grain direction or Custom angle.

    Do not apply obj.Placement here. Applying Placement again would rotate
    the already rotated geometry a second time.

    The preview-grid position is removed later by _normalize_polygon().
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


def _read_bool_combo(widget, default=False):
    """
    Read a boolean value from the project's False/True combo box.
    """
    try:
        return widget.currentIndex() == 1
    except Exception:
        return bool(default)


def _points_to_deepnest_points(points):
    """
    Convert [x, y] point pairs to Deepnest point objects.
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

            result.append({
                "x": round(x, 6),
                "y": round(y, 6)
            })

        except Exception:
            continue

    return result


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


def _material_to_deepnest_sheet(material):
    """
    Convert one IP-Nesting material record
    to a Deepnest sheet record.
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
            "type": "rect",
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
        "type": "polygon",
        "outer": _points_to_deepnest_points(
            outer
        ),
        "holes": [
            _points_to_deepnest_points(
                hole
            )
            for hole in holes
        ],
        "quantity": quantity
    }


def execute_nesting(panel):
    """
    Export the current FreeCAD nesting state
    to Deepnest input.json.
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

        try:
            threads = max(
                1,
                int(
                    panel.cpu_cores_combo.currentText()
                )
            )
        except Exception:
            threads = 1

        try:
            time_ratio = max(
                0.0,
                float(
                    str(
                        panel.deepnest_time_ratio.text()
                    ).strip().replace(",", ".")
                )
            )
        except Exception:
            time_ratio = 0.5

        try:
            population_size = max(
                1,
                int(
                    float(
                        str(
                            panel.deepnest_population_size.text()
                        ).strip().replace(",", ".")
                    )
                )
            )
        except Exception:
            population_size = 10

        try:
            mutation_rate = max(
                0,
                int(
                    float(
                        str(
                            panel.deepnest_mutation_rate.text()
                        ).strip().replace(",", ".")
                    )
                )
            )
        except Exception:
            mutation_rate = 10

        export_with_sheet_boundaries = _read_bool_combo(
            panel.deepnest_export_sheet_boundaries,
            False
        )

        export_with_sheets_space = _read_bool_combo(
            panel.deepnest_export_sheets_space,
            False
        )

        try:
            export_with_sheets_space_value = max(
                0.0,
                float(
                    str(
                        panel.deepnest_export_sheets_space_value.text()
                    ).strip().replace(",", ".")
                )
            )
        except Exception:
            export_with_sheets_space_value = 0.13888

        # Export every added sheet and offcut.
        sheets = []

        for material in getattr(
            panel,
            "offcuts",
            []
        ) or []:
            try:
                sheet = _material_to_deepnest_sheet(
                    material
                )

                if sheet.get(
                    "type"
                ) == "rect":
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

                elif sheet.get(
                    "type"
                ) == "polygon":
                    if len(
                        sheet.get(
                            "outer",
                            []
                        )
                    ) >= 3:
                        sheets.append(sheet)

            except Exception:
                App.Console.PrintError(
                    "Failed to convert material "
                    "to Deepnest sheet:\n"
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
                "Preview document not found: %s\n"
                % str(
                    panel.preview_doc_name
                )
            )
            return False

        # FIX:
        # Ensure that the latest visible state has been
        # applied before extracting polygons.
        #
        # This is especially important after:
        # - X grain direction;
        # - Y grain direction;
        # - Custom angle;
        # - bulk rotation.
        

        # FIX:
        # Recompute after all possible Placement changes.
        try:
            p_doc.recompute()
        except Exception:
            App.Console.PrintError(
                "Preview document recompute failed "
                "before export:\n"
                + traceback.format_exc()
            )

        try:
            App.Console.PrintMessage(
                "Preview document recomputed before "
                "polygon extraction.\n"
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
                        "Part row %d has no preview "
                        "object name.\n"
                        % row
                    )
                    continue

                obj = p_doc.getObject(
                    primary_name
                )

                if not obj:
                    App.Console.PrintWarning(
                        "Preview object not found: %s\n"
                        % str(
                            primary_name
                        )
                    )
                    continue

                # FIX:
                # Log the actual Placement used for export.
                try:
                    App.Console.PrintMessage(
                        "Exporting '%s' with current "
                        "Placement: %s\n"
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
                        "No valid polygon points found "
                        "for: %s\n"
                        % str(
                            primary_name
                        )
                    )
                    continue

                parts.append({
                    "points": points,
                    "quantity": quantity,
                    "rotations": rotations
                })

            except Exception:
                App.Console.PrintError(
                    "Failed to export part row "
                    "%d:\n%s\n"
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
                "exportWithSheetBoundboarders": (
                    export_with_sheet_boundaries
                ),
                "exportWithSheetsSpace": (
                    export_with_sheets_space
                ),
                "exportWithSheetsSpaceValue": (
                    export_with_sheets_space_value
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
            "Deepnest input JSON written to: %s\n"
            % output_path
        )

        return True

    except Exception:
        App.Console.PrintError(
            "execute_nesting failed:\n"
            + traceback.format_exc()
        )

        return False