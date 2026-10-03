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

                return []

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

        if not _points_equal_2d(ordered[0], ordered[-1]):
            return []

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


# Parse one rotation cell value into a nesting CLI rotation rule.
def parse_rotation_spec(text):
    """
    Parse one rotation cell value into a nesting CLI rotation rule.

    The cell accepts three formats (0 degrees is always the start
    orientation):
      - plain count  "32"        -> {"rotations": 32}
      - degree step  "(90)"      -> {"allowedAngles": [0, 90, 180, 270]}
      - angle list   "[45, 90]"  -> {"allowedAngles": [0, 45, 90]}

    Returns None for empty, incomplete or invalid input.
    """
    if text is None:
        return None

    value = str(text).strip()

    if not value:
        return None

    # Plain integer count: a uniform orientation grid (1..3600 states).
    if value[0] not in "([":
        try:
            number = float(value)
            count = int(number)
        except (ValueError, OverflowError):
            return None

        if not math.isfinite(number) or number != count:
            return None

        return {
            "rotations": max(1, min(3600, count))
        }

    # Parenthesized degree step: 0, step, 2*step, ... below 360.
    if value[0] == "(":
        if not value.endswith(")"):
            return None

        try:
            step = float(value[1:-1].strip())
        except (ValueError, OverflowError):
            return None

        if not math.isfinite(step) or step <= 0 or step >= 360:
            return None

        # The CLI accepts at most 3600 permitted angles per part.
        if math.ceil(360.0 / step) > 3600:
            return None

        angles = []
        angle = 0.0
        while angle < 360.0 - 1e-6:
            angles.append(round(angle, 6))
            angle += step

        return {
            "allowedAngles": angles
        }

    # Bracketed explicit angle list with 0 always included.
    if not value.endswith("]"):
        return None

    inner = value[1:-1].strip()

    if not inner:
        return None

    angles = [0.0]

    for part in inner.split(","):
        part = part.strip()

        if not part:
            return None

        try:
            angle = float(part)
        except (ValueError, OverflowError):
            return None

        if not math.isfinite(angle):
            return None

        angle = round(angle % 360.0, 6)

        if angle not in angles:
            angles.append(angle)

    return {
        "allowedAngles": angles
    }


# Read one rotation cell into a rule dict, falling back to a single state.
def _read_rotation_rule(item):
    """
    Read one rotation cell into a rule dict, falling back to a single state.
    """
    try:
        if item is None:
            return {"rotations": 1}

        rule = parse_rotation_spec(
            str(item.text())
        )

        if rule is None:
            return {"rotations": 1}

        return rule

    except Exception:
        return {"rotations": 1}


# Characters permitted while a rotation cell is still being typed.
_ROTATION_SPEC_CHARS = set("0123456789.,+- ()[]\t")


# Return True when text looks like an unfinished (or ) rotation cell.
def _is_incomplete_rotation_spec(value):
    """Return True when text looks like an unfinished (or ) rotation cell."""
    if not value:
        return False

    if value[0] not in "([":
        return False

    if any(ch not in _ROTATION_SPEC_CHARS for ch in value):
        return False

    closer = ")" if value[0] == "(" else "]"

    return closer not in value


# Normalize a rotation cell value for display without disrupting editing.
def normalize_rotation_text(text):
    """
    Normalize a rotation cell value for display without disrupting editing.

    Returns "1" for empty or invalid input, a clamped integer for a plain
    count, the trimmed text for a valid degree step or angle list, and the
    original text while the user is still typing a bracket or parenthesis
    value.
    """
    if text is None:
        return "1"

    value = str(text)
    stripped = value.strip()

    if not stripped:
        return "1"

    rule = parse_rotation_spec(stripped)

    if rule is not None:
        if "rotations" in rule:
            return str(rule["rotations"])

        return stripped

    if _is_incomplete_rotation_spec(stripped):
        return value

    return "1"


# Read the boundary deflection in millimetres from the panel.
def _read_boundary_deflection(panel, default=0.01):
    """
    Read the boundary deflection in millimetres from the panel.

    The value is pinned in the panel and returned as a canonical
    millimetre value.
    """
    try:
        if panel is None or not hasattr(panel, "get_boundary_resolution_mm"):
            return float(default)

        value = float(panel.get_boundary_resolution_mm())

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


# Move polygon coordinates so the minimum X/Y position becomes 0/0.
def _normalize_polygon(points):
    """
    Move polygon coordinates so the minimum X/Y position becomes 0/0.

    This removes the temporary position of the object in the
    Nesting_Preview grid, while preserving its current orientation.

    Input points are [x, y] pairs and the output is always [x, y] pairs
    to match the sheet export format.
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
            [
                round(
                    float(point[0]) - min_x,
                    6
                ),
                round(
                    float(point[1]) - min_y,
                    6
                )
            ]
            for point in points
        ]

    except Exception:
        return []


# Compute the absolute shoelace area of an XY polygon.
def _polygon_area(points):
    """
    Absolute shoelace area of an [x, y] polygon.
    """
    try:
        if not points or len(points) < 3:
            return 0.0

        area = 0.0
        n = len(points)

        for index in range(n):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % n]
            area += (x1 * y2 - x2 * y1)

        return abs(area) * 0.5

    except Exception:
        return 0.0


# Return the axis-aligned bounding box of an XY polygon as a 4-tuple.
def _polygon_bbox(points):
    try:
        xs = [float(point[0]) for point in points or []]
        ys = [float(point[1]) for point in points or []]

        if not xs:
            return None

        return (
            min(xs),
            min(ys),
            max(xs),
            max(ys),
        )

    except Exception:
        return None


# Compare two XY polygons by area and bounding box (best-effort duplicate check).
def _polygons_same_2d(poly_a, poly_b, tolerance=1e-6):
    """Compare complete cyclic boundaries, allowing reversed winding."""
    a = _remove_duplicate_points(poly_a)
    b = _remove_duplicate_points(poly_b)
    if not a or len(a) != len(b):
        return False
    for start, point in enumerate(b):
        if _points_equal_2d(a[0], point, tolerance):
            for direction in (1, -1):
                if all(_points_equal_2d(p, b[(start + direction*i) % len(b)], tolerance)
                       for i, p in enumerate(a)):
                    return True
    return False


# Extract all candidate closed-wire polygons from a preview object, rotated into
# on-screen orientation but not yet normalized/translated.
def _extract_part_candidate_wires(obj, deflection=0.01):
    """Extract one planar profile, never projected side-wall wires.

    Shape already includes the object's Placement. Only flat profiles and
    machined panels normal to XY are supported when their planar profile
    encloses all material; reject shapes whose silhouette cannot be proven.
    """
    import Part
    shape = obj.Shape
    if shape.isNull() or not shape.isValid():
        raise ValueError("The part has no valid shape")
    flat_faces = [f for f in shape.Faces if f.BoundBox.ZLength < 1e-7]
    if shape.Solids:
        if not flat_faces:
            raise ValueError("Align the part with the XY plane before nesting")
        # Combine horizontal faces: pocket floors and the opposite panel face
        # fill blind machining, while genuine through holes remain empty.
        caps = []
        for face in flat_faces:
            cap = face.copy()
            cap.translate(App.Vector(0, 0, -cap.BoundBox.ZMin))
            u0, u1, v0, v1 = cap.ParameterRange
            if cap.normalAt((u0 + u1) / 2, (v0 + v1) / 2).z < 0:
                cap.reverse()
            caps.append(cap)
        # Boolean unions of coplanar faces can retain overlapping face
        # partitions. Fuse thin solids instead, then take their bottom cap.
        projected = caps[0].extrude(App.Vector(0, 0, 1))
        for cap in caps[1:]:
            projected = projected.fuse(cap.extrude(App.Vector(0, 0, 1)))
        projected = projected.removeSplitter()
        bottom = [f for f in projected.Faces
                  if f.BoundBox.ZLength < 1e-7 and abs(f.BoundBox.ZMin) < 1e-7]
        if not bottom:
            raise ValueError("A part must have one connected planar profile")
        # Pocket floors may form islands inside an apparent hole. The column
        # check below fills these holes; separate external solids will fail
        # final containment instead of being silently omitted.
        profile = max(bottom, key=lambda face: face.Area)
        profile.reverse()  # bottom solid face points down; extrusion must point up
        projected = profile.extrude(App.Vector(0, 0, 1))
        thickness = shape.BoundBox.ZLength
        volume_tolerance = max(1e-6, abs(shape.Volume) * 1e-9)
        # A blind hole can have a curved floor, absent from flat_faces. Only
        # expose a hole to nesting if its entire column contains no material.
        cap_face = profile
        for wire in cap_face.Wires:
            if wire.isSame(cap_face.OuterWire):
                continue
            fill = Part.Face(wire)
            column = fill.extrude(App.Vector(0, 0, thickness))
            column.translate(App.Vector(0, 0, shape.BoundBox.ZMin))
            if shape.common(column).Volume > volume_tolerance:
                projected = projected.fuse(fill.extrude(App.Vector(0, 0, 1))).removeSplitter()
        bottom = [f for f in projected.Faces
                  if f.BoundBox.ZLength < 1e-7 and abs(f.BoundBox.ZMin) < 1e-7]
        if len(bottom) != 1:
            raise ValueError("A part must have one connected planar profile")
        profile = bottom[0]
        profile.reverse()
        envelope = profile.extrude(App.Vector(0, 0, thickness))
        envelope.translate(App.Vector(0, 0, shape.BoundBox.ZMin))
        # Comparing equal volumes rejects legitimate grooves/drilling. Instead
        # prove that no solid material lies outside the exported XY footprint.
        if shape.cut(envelope).Volume > volume_tolerance:
            raise ValueError("The part extends outside its planar nesting profile; align it with XY")
        wires = profile.Faces[0].Wires
    else:
        if shape.BoundBox.ZLength > 1e-7:
            raise ValueError("The part profile must be parallel to XY")
        wires = shape.Wires
    candidates = []
    for wire in wires:
        if not wire.isClosed():
            raise ValueError("The part contains an open contour")
        points = _extract_wire_points_ordered(wire, deflection)
        if len(points) < 3 or not all(math.isfinite(v) for p in points for v in p):
            raise ValueError("The part contains an invalid contour")
        polygon = Part.makePolygon([App.Vector(x, y, 0) for x, y in points + points[:1]])
        face = Part.Face(polygon)
        if not face.isValid() or face.Area <= 1e-9:
            raise ValueError("The part contains a self-intersecting or empty contour")
        if not any(_polygons_same_2d(points, old) for old in candidates):
            candidates.append(points)
    if not candidates:
        raise ValueError("The part has no closed profile")
    candidates.sort(key=_polygon_area, reverse=True)
    def as_face(poly):
        return Part.Face(Part.makePolygon([App.Vector(x, y, 0) for x, y in poly + poly[:1]]))
    outer = as_face(candidates[0])
    inner_faces = []
    for poly in candidates[1:]:
        inner = as_face(poly)
        if (abs(outer.common(inner).Area - inner.Area) > max(1e-7, inner.Area*1e-7)
                or outer.OuterWire.distToShape(inner.OuterWire)[0] < 1e-7):
            raise ValueError("Disconnected or touching part contours are unsupported")
        if any(inner.common(other).Area > 1e-7 for other in inner_faces):
            raise ValueError("Overlapping or nested hole contours are unsupported")
        inner_faces.append(inner)
    return candidates


def remember_hole_selection(panel, obj, holes, deflection=0.01):
    """Keep selected boundaries in object-local coordinates across rotations."""
    candidates = _extract_part_candidate_wires(obj, deflection)
    ox, oy = _points_min_xy(candidates[0])
    inverse = obj.Placement.inverse()
    z = obj.Shape.BoundBox.ZMin
    local = [[inverse.multVec(App.Vector(x + ox, y + oy, z)) for x, y in hole]
             for hole in holes]
    if not hasattr(panel, "_part_hole_local"):
        panel._part_hole_local = {}
    panel._part_hole_local[obj.Name] = local
    panel._part_holes[obj.Name] = holes


def current_selected_holes(panel, obj, deflection=0.01, candidates=None):
    selected = getattr(panel, "_part_holes", {}).get(obj.Name) or []
    if not selected:
        return []
    local = getattr(panel, "_part_hole_local", {}).get(obj.Name)
    if local is None:
        raise ValueError("Please mark the part holes again before nesting")
    if candidates is None:
        candidates = _extract_part_candidate_wires(obj, deflection)
    ox, oy = _points_min_xy(candidates[0])
    current = []
    for hole in local:
        world = [obj.Placement.multVec(p) for p in hole]
        transformed = [[p.x - ox, p.y - oy] for p in world]
        available = [_translate_points(p, (ox, oy)) for p in candidates[1:]]
        match = next((p for p in available if _polygons_same_2d(p, transformed, 2e-5)), None)
        if match is None:
            raise ValueError("Part geometry or sampling changed; please mark its holes again")
        current.append(match)
    return current


def _extract_part_points(obj, deflection=0.01, candidates=None):
    """
    Extract the current visible 2D outer contour from a preview object.

    Shape coordinates already include the preview object's rotation.

    The temporary preview-grid translation is removed by
    _normalize_polygon().
    """
    if candidates is None:
        candidates = _extract_part_candidate_wires(obj, deflection)

    if not candidates:
        return []

    # Select the largest contour as the outer contour.
    outer = max(
        candidates,
        key=_polygon_area
    )

    outer = _remove_duplicate_points(
        outer
    )

    return _normalize_polygon(
        outer
    )


# Extract the part's outer contour and all candidate inner (hole) contours.
def _extract_part_contours(obj, deflection=0.01):
    """
    Extract the part's outer contour and its candidate hole contours.

    Returns:
        outer        -- normalized [x, y] outer polygon (lower-left at 0/0).
        holes        -- list of candidate inner [x, y] polygons, translated by
                        the same offset as the outer contour so their position
                        is relative to the part boundary.
        contour_info -- list of contour records, outer first, each carrying
                        index/polygon/area/is_outer/selected keys.
    """
    candidates = _extract_part_candidate_wires(obj, deflection)

    if not candidates:
        return [], [], []

    # Remove duplicates produced by opposite faces of a solid sharing the same
    # projected outline (top/bottom faces and through holes).
    unique = []

    for polygon in candidates:
        if not polygon or len(polygon) < 3:
            continue

        if _polygon_area(polygon) <= 1e-9:
            continue

        duplicate = any(
            _polygons_same_2d(existing, polygon)
            for existing in unique
        )

        if not duplicate:
            unique.append(polygon)

    if not unique:
        return [], [], []

    unique.sort(
        key=_polygon_area,
        reverse=True
    )

    outer_raw = _remove_duplicate_points(
        unique[0]
    )

    offset = _points_min_xy(outer_raw)
    outer = _translate_points(outer_raw, offset)

    holes = []
    contour_info = []

    contour_info.append({
        "index": 0,
        "polygon": outer,
        "area": float(_polygon_area(outer)),
        "bbox": _polygon_bbox(outer),
        "is_outer": True,
        "selected": False,
    })

    for index, polygon in enumerate(unique[1:], start=1):
        hole = _remove_duplicate_points(polygon)
        hole = _translate_points(hole, offset)

        if len(hole) < 3:
            continue

        holes.append(hole)

        contour_info.append({
            "index": int(index),
            "polygon": hole,
            "area": float(_polygon_area(hole)),
            "bbox": _polygon_bbox(hole),
            "is_outer": False,
            "selected": False,
        })

    return outer, holes, contour_info


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


# Parse XY pairs or {"x", "y"} dictionaries into [x, y] pairs.
def _clean_sheet_points(points):
    cleaned = []

    for point in points or []:
        try:
            if isinstance(point, dict):
                x = float(point.get("x", 0.0))
                y = float(point.get("y", 0.0))
            else:
                x = float(point[0])
                y = float(point[1])
        except Exception:
            continue

        cleaned.append([x, y])

    return cleaned


def _rotate_points_90_cw(points):
    return [
        [round(y, 6), round(-x, 6)]
        for x, y in points
    ]


def _points_min_xy(points):
    if not points:
        return 0.0, 0.0

    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
    )


def _translate_points(points, offset):
    return [
        [
            round(point[0] - offset[0], 6),
            round(point[1] - offset[1], 6),
        ]
        for point in points
    ]


# Rotate a sheet polygon so a Y (vertical) grain axis becomes horizontal.
def _orient_sheet_polygon(points, grain, offset=None):
    """
    Return the polygon with a Y grain rotated so its texture runs horizontally.

    X and None grains leave the polygon unchanged. A Y grain is rotated 90
    degrees clockwise so the former +Y texture axis maps to +X. When offset is
    provided it is the (min_x, min_y) translation applied to every point;
    otherwise the polygon is shifted by its own minimum so its lower-left
    corner lands at 0/0. Input points may be [x, y] pairs or {"x", "y"}
    dictionaries; output is always [x, y] pairs.
    """
    cleaned = _clean_sheet_points(points)

    if str(grain or "None").strip().upper() == "Y":
        cleaned = _rotate_points_90_cw(cleaned)

        if offset is None:
            offset = _points_min_xy(cleaned)

        cleaned = _translate_points(cleaned, offset)

    return cleaned


# Return the translation used to normalize a Y-grain polygon, or None.
def _grain_offset(points, grain):
    """
    Return the (min_x, min_y) translation applied to a Y-grain polygon.

    The offset is the minimum corner of the rotated polygon, i.e. the amount
    by which the polygon is shifted so its lower-left corner lands at 0/0.
    Non-Y grains return None.
    """
    if str(grain or "None").strip().upper() != "Y":
        return None

    return _points_min_xy(
        _rotate_points_90_cw(
            _clean_sheet_points(points)
        )
    )


# Convert one IP-Nesting material record to a nesting CLI sheet record.
def _material_to_cli_sheet(material):
    """
    Convert one IP-Nesting material record
    to a nesting CLI sheet record.

    Every sheet, including rectangular sheets, is exported as a polygon.
    A vertical (Y) grain is rotated so the texture direction is horizontal.
    """
    material = material or {}

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

    grain = str(
        material.get("grain", "None") or "None"
    ).strip().upper()

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

    # Compute the outer's normalization offset once so every hole is
    # translated by the same amount and keeps its position relative to
    # the outer boundary instead of collapsing to the origin.
    offset = _grain_offset(outer, grain)

    outer = _orient_sheet_polygon(outer, grain, offset)
    holes = [
        _orient_sheet_polygon(hole, grain, offset)
        for hole in holes
    ]

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


# Read the grain direction from one table row as a sheet-compatible string.
def _get_grain_direction(panel, row):
    """
    Return the grain direction for one table row as "X", "Y" or "None".

    The value mirrors the sheet grain representation so parts and sheets
    share the same grain field format in input.json.
    """
    try:
        grain_widget = panel.table.cellWidget(
            row,
            4
        )

        if grain_widget:
            grain_checkbox = grain_widget.findChild(
                QtGui.QCheckBox
            )

            if not (
                grain_checkbox
                and grain_checkbox.isChecked()
            ):
                return "None"

            grain_combo = grain_widget.findChild(
                QtGui.QComboBox
            )

            if grain_combo:
                axis = str(
                    grain_combo.currentText()
                    or "X"
                ).strip().upper()

                if axis in ("X", "Y"):
                    return axis

        return "None"

    except Exception:
        return "None"

# Coerce a numeric value into [lo, hi], falling back to default on parse failure.
def _coerce_number(value, default, lo=None, hi=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    if lo is not None and number < lo:
        number = float(lo)
    if hi is not None and number > hi:
        number = float(hi)
    return number


# Coerce an integer value into [lo, hi], falling back to default on parse failure.
def _coerce_int(value, default, lo, hi):
    return int(round(_coerce_number(value, default, lo, hi)))


# Build the nesting CLI `config` object, coercing every setting to its
# supported range and applying mode-specific time-budget rules. Kept as a
# pure function so it can be exercised without FreeCAD or PySide.
def build_nesting_config(
    mode="first",
    time_limit_seconds=0.0,
    continuous_round_seconds=30.0,
    trials=2,
    resolution=1.0,
    step=1,
    curve_tolerance=0.3,
    cache_rejects=True,
    gpu_enabled=False,
    gpu_device=-1,
    gpu_batch_size=65536,
    threads=1,
    spacing=0.0,
    sheet_margin=0.0,
    hole_clearance=0.0,
):
    if mode not in ("first", "timed", "continuous"):
        mode = "first"

    trials = _coerce_int(trials, 2, 1, 4)
    resolution = _coerce_number(resolution, 1.0, lo=1e-6)
    step = _coerce_int(step, 1, 1, 100000)
    curve = _coerce_number(curve_tolerance, 0.3, lo=0.0, hi=1000000.0)
    threads = _coerce_int(threads, 1, 1, 256)
    spacing = _coerce_number(spacing, 0.0, lo=0.0)
    sheet_margin = _coerce_number(sheet_margin, 0.0, lo=0.0)
    hole_clearance = _coerce_number(hole_clearance, 0.0, lo=0.0)

    gpu_device = _coerce_int(gpu_device, -1, -1, 1024)
    gpu_batch = _coerce_int(gpu_batch_size, 65536, 256, 262144)
    round_seconds = _coerce_number(
        continuous_round_seconds, 30.0, lo=0.01, hi=86400.0
    )

    # Timed mode needs a positive budget, first mode requires exactly zero,
    # and continuous mode ignores timeLimitSeconds entirely.
    if mode == "timed":
        limit = _coerce_number(time_limit_seconds, 0.0, lo=0.0, hi=86400.0)
        if limit <= 0.0:
            limit = 60.0
    else:
        limit = 0.0

    config = {
        "algorithm": "bitmap",
        "mode": mode,
        "resolution": resolution,
        "threads": threads,
        "trials": trials,
        "curveTolerance": curve,
        "cacheRejects": bool(cache_rejects),
        "bitmapSearchStepPx": step,
        "spacing": spacing,
        "partToSheet": sheet_margin,
        "partToHole": hole_clearance,
        "timeLimitSeconds": limit,
        "continuousRoundSeconds": round_seconds,
        "gpu": {
            "enabled": bool(gpu_enabled),
            "device": gpu_device,
            "batchSize": gpu_batch,
        },
    }
    return config

# Read one panel line-edit as a float, falling back to default.
def _read_line_edit_float(panel, attr, default):
    try:
        widget = getattr(panel, attr, None)
        text = str(widget.text() if widget is not None else "").strip().replace(",", ".")
        if not text:
            return float(default)
        return float(text)
    except Exception:
        return float(default)


# Read one panel line-edit as an int, falling back to default.
def _read_line_edit_int(panel, attr, default):
    try:
        return int(round(_read_line_edit_float(panel, attr, default)))
    except Exception:
        return int(default)


# Read one panel combo box as its current text, falling back to default.
def _read_combo_text(panel, attr, default):
    try:
        widget = getattr(panel, attr, None)
        return str(widget.currentText()) if widget is not None else str(default)
    except Exception:
        return str(default)


# Read one panel combo box as an int, falling back to default.
def _read_combo_int(panel, attr, default):
    try:
        return int(_read_combo_text(panel, attr, default))
    except Exception:
        return int(default)


# Read one panel combo box's item data as an int, falling back to default.
def _read_combo_data_int(panel, attr, default):
    try:
        widget = getattr(panel, attr, None)
        value = widget.currentData() if widget is not None else None
        return int(value if value is not None else default)
    except Exception:
        return int(default)


# Read one False/True combo box as a boolean, falling back to default.
def _read_combo_bool(panel, attr, default):
    try:
        widget = getattr(panel, attr, None)
        return bool(widget.currentIndex() == 1) if widget is not None else bool(default)
    except Exception:
        return bool(default)

# Write nesting CLI input.json and nesting_session.json from the panel and preview geometry.
def _validate_material(sheet):
    """Fail before launching the CLI if a stock boundary is unusable."""
    import Part
    contours = [sheet.get("points", [])] + (sheet.get("holes", []) or [])
    faces = []
    for polygon in contours:
        if len(polygon) < 3 or not all(math.isfinite(float(v)) for p in polygon for v in p):
            raise ValueError("Invalid material contour")
        vectors = [App.Vector(float(x), float(y), 0) for x, y in polygon]
        wire = Part.makePolygon(vectors + vectors[:1])
        face = Part.Face(wire)
        if not face.isValid() or face.Area <= 1e-9:
            raise ValueError("Invalid material contour")
        faces.append(face)
    for index, hole in enumerate(faces[1:], 1):
        if abs(faces[0].common(hole).Area - hole.Area) > max(1e-7, hole.Area*1e-7):
            raise ValueError("A material hole lies outside its boundary")
        if any(hole.common(other).Area > 1e-7 for other in faces[1:index]):
            raise ValueError("Material holes overlap")
    if int(sheet.get("quantity", 1)) < 1:
        raise ValueError("Material quantity must be positive")


def execute_nesting(panel):
    """Synchronous compatibility wrapper for scripts and geometry tests."""
    steps = export_nesting_steps(panel)
    while True:
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value


def export_nesting_steps(panel):
    """
    Export the panel state to the nesting CLI input.json and nesting_session.json.

    Yields between FreeCAD operations; returns True after both files are written,
    or False on failure.
    Dimensions are read as canonical millimetre values and the payload
    declares mm.
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

        boundary_resolution = panel.get_boundary_resolution_mm()

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

        # Collect every nesting CLI search and GPU setting from the panel.
        mode = _read_combo_text(panel, "mode_combo", "first")
        time_limit_seconds = _read_line_edit_float(
            panel, "time_limit_edit", 0.0
        )
        continuous_round_seconds = _read_line_edit_float(
            panel, "round_seconds_edit", 30.0
        )
        trials = _read_combo_int(panel, "trials_combo", 2)
        resolution = _read_line_edit_float(panel, "resolution_edit", 1.0)
        step = _read_line_edit_int(panel, "step_edit", 1)
        curve_tolerance = _read_line_edit_float(panel, "curve_edit", 0.3)
        cache_rejects = _read_combo_bool(panel, "cache_combo", True)
        gpu_enabled = _read_combo_bool(panel, "gpu_enabled_combo", False)
        gpu_device = _read_combo_data_int(panel, "gpu_device_combo", -1)
        gpu_batch_size = _read_line_edit_int(panel, "gpu_batch_edit", 65536)

        config = build_nesting_config(
            mode=mode,
            time_limit_seconds=time_limit_seconds,
            continuous_round_seconds=continuous_round_seconds,
            trials=trials,
            resolution=resolution,
            step=step,
            curve_tolerance=curve_tolerance,
            cache_rejects=cache_rejects,
            gpu_enabled=gpu_enabled,
            gpu_device=gpu_device,
            gpu_batch_size=gpu_batch_size,
            threads=threads,
            spacing=spacing,
            sheet_margin=sheet_margin,
            hole_clearance=hole_clearance,
        )

        # Validate the entire order before writing either job file.
        errors = []
        sheets = []

        for material in getattr(
            panel,
            "offcuts",
            []
        ) or []:
            yield None
            try:
                sheet = _material_to_cli_sheet(
                    material
                )
                
                _validate_material(sheet)
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

        if len(sheets) != len(getattr(panel, "offcuts", []) or []) or not sheets:
            errors.append("One or more materials have invalid geometry or dimensions")

        yield None  # Let Qt paint and handle cancellation before recomputing.

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

        yield None
        parts = []
        snapshots = {}

        data_rows = max(
            0,
            panel.table.rowCount()
            - panel.control_rows
        )

        for row in range(data_rows):
            yield None  # FreeCAD geometry remains on the main thread.
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
                    raise ValueError("Missing part name")

                try:
                    quantity = int(str(qty_item.text()).strip())
                    if quantity < 1:
                        raise ValueError("Quantity must be positive")
                except Exception:
                    raise ValueError("Invalid part quantity")

                rotation_rule = _read_rotation_rule(
                    rotation_item
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
                    raise ValueError("Missing or invalid part geometry")

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
                    raise ValueError("Missing or invalid part geometry")

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

                candidates = _extract_part_candidate_wires(obj, boundary_resolution)
                points = _extract_part_points(obj, boundary_resolution, candidates=candidates)

                if len(points) < 3:
                    App.Console.PrintWarning(
                        tr('no_valid_polygon_points_found_for_s')
                        % str(
                            primary_name
                        )
                    )
                    raise ValueError("Missing or invalid part geometry")

                grain = _get_grain_direction(
                    panel,
                    row
                )

                source_type = _get_source_type(
                    obj
                )

                part_id = "part_%d" % len(parts)

                # Selected inner contours (holes) marked in the part dialog.
                selected_holes = current_selected_holes(panel, obj, boundary_resolution, candidates=candidates)

                from IPNestingRuntime import snapshot_part
                snapshots[part_id] = snapshot_part(obj, boundary_resolution, candidates=candidates)

                # A grain-restricted part may only rotate 0 or 180 degrees so
                # the texture direction is preserved during nesting.
                if grain != "None":
                    rotation_rule = {
                        "allowedAngles": [0.0, 180.0]
                    }

                parts.append({
                    # Fields consumed by the nesting CLI.
                    "id": part_id,
                    "points": points,
                    "holes": selected_holes,
                    "quantity": quantity,
                    **rotation_rule,

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
                        "grain": grain,
                        "instance_prefix": (
                            part_id
                            + "_instance_"
                        )
                    }
                })

            except Exception as exc:
                errors.append("Row %d: %s" % (row + 1, exc))
                App.Console.PrintError(
                    tr('failed_to_export_part_row_d_s')
                    % (
                        row,
                        traceback.format_exc()
                    )
                )

        yield None
        if errors or not parts:
            message = "\n".join(errors or ["No parts to export"])
            App.Console.PrintError(message + "\n")
            try:
                QtGui.QMessageBox.warning(panel.form, tr('cannot_start_nesting'), message)
            except Exception:
                pass
            return False

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
            "config": config,
            "sheets": sheets,
            "parts": parts,
            "output": {
                "json": "result.json",
                "cancelFile": ".clinesting-cancel-" + job_id
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
                    "quantity": part.get("quantity", 1),
                    "points": part["points"],
                    "boundary_resolution": boundary_resolution,
                    **snapshots[part["id"]]
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

        import tempfile
        for path, data in ((session_path, session_payload), (output_path, payload)):
            fd, temporary = tempfile.mkstemp(prefix=".nesting-", dir=script_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, indent=2, ensure_ascii=False)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)

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
