"""
IPNestingOffcuts - DXF offcut extraction utilities.

Goal:
- Import a DXF into a temporary FreeCAD document.
- Extract all valid closed contours as 2D polygons.
- Treat the largest contour as the outer sheet contour.
- Preserve every other contour for user selection in the Show dialog.
- Return:
    outer,
    holes,
    bbox,
    contour_info

`holes` is initially empty. The user-selected contours are converted
to holes later by the UI/export pipeline.
"""

import os
import traceback

import FreeCAD as App

try:
    import Part
except Exception:
    Part = None

def _import_dxf(path, doc_name):
    """
    Import DXF into an existing FreeCAD document.

    FreeCAD 1.1.x:
        Uses freecad.module_io.OpenInsertObject(...)

    Older FreeCAD versions:
        Falls back to importDXF.insert(...)
    """
    if not path or not os.path.exists(path):
        App.Console.PrintError(
            "[Offcuts] DXF file does not exist: %s\n" % str(path)
        )
        return False

    # Preferred importer for FreeCAD 1.1.x.
    try:
        from freecad import module_io

        module_io.OpenInsertObject(
            "importDXF",
            str(path),
            "insert",
            str(doc_name)
        )
        return True

    except Exception:
        App.Console.PrintWarning(
            "[Offcuts] module_io.OpenInsertObject() failed:\n"
            + traceback.format_exc()
        )

    # Compatibility fallback for older FreeCAD versions.
    try:
        import importDXF

        importDXF.insert(
            str(path),
            str(doc_name)
        )
        return True

    except Exception:
        App.Console.PrintError(
            "[Offcuts] DXF import failed with both importers:\n"
            + traceback.format_exc()
        )

    return False


def polygon_area(poly):
    """Signed area (shoelace). poly is list of [x,y]."""
    try:
        if not poly or len(poly) < 3:
            return 0.0
        a = 0.0
        n = len(poly)
        for i in range(n):
            x1, y1 = float(poly[i][0]), float(poly[i][1])
            x2, y2 = float(poly[(i + 1) % n][0]), float(poly[(i + 1) % n][1])
            a += (x1 * y2 - x2 * y1)
        return 0.5 * a
    except Exception:
        return 0.0


def poly_bbox(poly):
    """BBox dict for poly."""
    xs = [float(p[0]) for p in poly or []]
    ys = [float(p[1]) for p in poly or []]
    if not xs or not ys:
        return {"min_x": 0.0, "min_y": 0.0, "max_x": 0.0, "max_y": 0.0}
    return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}


def _wire_to_polyline_2d(wire, deflection=0.5):
    """
    Convert a FreeCAD wire or closed edge to a 2D polyline.

    Straight contours use their vertices.
    Curves and circles are discretized.
    """
    pts = []

    try:
        # First try explicit ordered vertices.
        vertices = list(getattr(wire, "OrderedVertexes", []) or [])

        if not vertices:
            vertices = list(getattr(wire, "Vertexes", []) or [])

        if len(vertices) >= 3:
            for vertex in vertices:
                try:
                    point = vertex.Point
                    pts.append([
                        float(point.x),
                        float(point.y),
                    ])
                except Exception:
                    pass

        # For circles and other curved edges, discretize each edge.
        if len(pts) < 3:
            pts = []

            edges = list(
                getattr(wire, "Edges", []) or []
            )

            if not edges:
                edges = [wire]

            for edge in edges:
                discretized = None

                try:
                    discretized = edge.discretize(
                        Deflection=float(deflection)
                    )
                except Exception:
                    try:
                        discretized = edge.discretize(
                            deflection=float(deflection)
                        )
                    except Exception:
                        pass

                if not discretized:
                    continue

                for point in discretized:
                    try:
                        pts.append([
                            float(point.x),
                            float(point.y),
                        ])
                    except Exception:
                        pass

    except Exception:
        pts = []

    # Remove consecutive duplicate points.
    cleaned = []

    for point in pts:
        if not cleaned:
            cleaned.append(point)
            continue

        previous = cleaned[-1]

        if (
            abs(previous[0] - point[0]) > 1e-9
            or abs(previous[1] - point[1]) > 1e-9
        ):
            cleaned.append(point)

    # Remove repeated closing point.
    if len(cleaned) >= 2:
        first = cleaned[0]
        last = cleaned[-1]

        if (
            abs(first[0] - last[0]) <= 1e-9
            and abs(first[1] - last[1]) <= 1e-9
        ):
            cleaned.pop()

    return cleaned


# -------------------------
# NEW: recursive doc scan helpers
# -------------------------
def _iter_doc_objects_recursive(doc):
    """
    Yield all objects in a document, expanding group/part containers when present.
    This is needed because DXF import may put geometry inside Layer containers/groups.
    """
    seen = set()
    stack = list(doc.Objects) if doc else []
    while stack:
        o = stack.pop(0)
        if o is None:
            continue

        try:
            name = getattr(o, "Name", None)
            key = name if name else id(o)
        except Exception:
            key = id(o)

        if key in seen:
            continue
        seen.add(key)

        yield o

        # Expand groups/parts
        try:
            grp = getattr(o, "Group", None)
            if grp:
                for child in grp:
                    stack.append(child)
        except Exception:
            pass


def _shape_edge_count(obj):
    """Best-effort count of edges in obj.Shape."""
    try:
        shp = getattr(obj, "Shape", None)
        if shp is None:
            return 0
        try:
            edges = getattr(shp, "Edges", None)
            return len(edges) if edges else 0
        except Exception:
            return 0
    except Exception:
        return 0


def _collect_edges_from_shape(shp):
    edges = []
    try:
        es = list(getattr(shp, "Edges", [])) or []
        for e in es:
            try:
                if hasattr(e, "Length") and float(e.Length) <= 1e-9:
                    continue
            except Exception:
                pass
            edges.append(e)
    except Exception:
        pass
    return edges
    
def extract_offcut_from_dxf(path, debug=False, deflection=0.1):
    """
    Import a DXF into a temporary document and extract
    the largest closed contour.
    """
    doc = None
    
    try:
        deflection = float(deflection)
    except Exception:
        deflection = 0.1

    deflection = max(
        0.001,
        min(100.0, deflection)
    )

    try:
        if not path or not os.path.exists(path):
            App.Console.PrintError(
                "[Offcuts] DXF file does not exist: %s\n"
                % str(path)
            )
            return None, None, None, []

        doc_name = "IPNesting_OffcutTmp"

        while doc_name in App.listDocuments():
            doc_name += "_1"

        doc = App.newDocument(doc_name)

        if not _import_dxf(path, doc.Name):
            return None, None, None, []

        try:
            doc.recompute()
        except Exception:
            if debug:
                App.Console.PrintWarning(
                    "[Offcuts][DEBUG] recompute failed:\n"
                    + traceback.format_exc()
                )

        objects = list(_iter_doc_objects_recursive(doc))

        if not objects:
            App.Console.PrintError(
                "[Offcuts] Temporary DXF document contains no objects.\n"
            )
            return None, None, None, []

        all_edges = []

        for obj in objects:
            try:
                shape = getattr(obj, "Shape", None)
                if shape is None:
                    continue

                all_edges.extend(_collect_edges_from_shape(shape))

            except Exception:
                if debug:
                    App.Console.PrintWarning(
                        "[Offcuts][DEBUG] Failed to collect edges:\n"
                        + traceback.format_exc()
                    )

        if debug:
            App.Console.PrintMessage(
                "[Offcuts][DEBUG] Total edges collected: %d\n"
                % len(all_edges)
            )

        candidate_polygons = []

        # -------------------------------------------------
        # 1. Direct closed wires and edges.
        # -------------------------------------------------
        for obj in objects:
            try:
                shape = getattr(obj, "Shape", None)

                if shape is None:
                    continue

                processed_edge_keys = set()

                # Closed wires.
                for wire in list(
                    getattr(shape, "Wires", []) or []
                ):
                    try:
                        if not wire.isClosed():
                            continue
                    except Exception:
                        continue

                    polygon = _wire_to_polyline_2d(wire,deflection=deflection)

                    _append_unique_contour(
                        candidate_polygons,
                        polygon
                    )

                    try:
                        for edge in wire.Edges:
                            processed_edge_keys.add(
                                edge.hashCode()
                            )
                    except Exception:
                        pass

                # Single closed edges, such as circles.
                for edge in list(
                    getattr(shape, "Edges", []) or []
                ):
                    try:
                        edge_key = edge.hashCode()

                        if edge_key in processed_edge_keys:
                            continue

                    except Exception:
                        edge_key = None

                    is_closed = False

                    try:
                        is_closed = bool(edge.isClosed())
                    except Exception:
                        pass

                    if not is_closed:
                        try:
                            is_closed = (
                                len(edge.Vertexes) == 1
                            )
                        except Exception:
                            is_closed = False

                    if not is_closed:
                        continue

                    polygon = _wire_to_polyline_2d(edge,deflection=deflection)

                    _append_unique_contour(
                        candidate_polygons,
                        polygon
                    )

            except Exception:
                if debug:
                    App.Console.PrintWarning(
                        "[Offcuts][DEBUG] "
                        "Direct contour scan failed:\n"
                        + traceback.format_exc()
                    )

        # -------------------------------------------------
        # 2. Always assemble contours from all edges.
        # -------------------------------------------------
        try:
            closed_wires = _closed_wires_from_edges(
                all_edges,
                debug=debug
            )

            for wire in closed_wires:
                polygon = _wire_to_polyline_2d(wire,deflection=deflection)

                _append_unique_contour(
                    candidate_polygons,
                    polygon
                )

        except Exception:
            if debug:
                App.Console.PrintWarning(
                    "[Offcuts][DEBUG] "
                    "Assembled contour scan failed:\n"
                    + traceback.format_exc()
                )

        # If direct wires were not available, assemble them from edges.
        if not candidate_polygons:
            App.Console.PrintError(
                "[Offcuts] No closed contours found in %s\n"
                % os.path.basename(path)
            )
            return None, None, None, []

        outer, holes, bbox, contour_info = (
            _classify_contours(
                candidate_polygons
            )
        )

        if not outer:
            App.Console.PrintError(
                "[Offcuts] No valid outer contour found in %s\n"
                % os.path.basename(path)
            )
            return None, None, None, []

        return (
            outer,
            holes,
            bbox,
            contour_info
        )

    except Exception:
        App.Console.PrintError(
            "[Offcuts] extract_offcut_from_dxf failed:\n"
            + traceback.format_exc()
        )
        return None, None, None, []

    finally:
        if doc is not None:
            try:
                doc_name = doc.Name

                if doc_name in App.listDocuments():
                    App.closeDocument(doc_name)

            except Exception:
                pass

def _closed_wires_from_edges(edges, debug=False):
    """
    Assemble closed wires from a flat list of edges.

    Supports:
    - multi-edge closed contours;
    - single closed edges, such as circles.
    """
    wires = []

    if Part is None or not edges:
        return wires

    try:
        groups = Part.sortEdges(edges)
    except Exception:
        if debug:
            App.Console.PrintError(
                "[Offcuts][DEBUG] Part.sortEdges failed:\n"
                + traceback.format_exc()
            )
        return wires

    for group_index, group in enumerate(groups or []):
        try:
            if not group:
                continue

            # A circle is often returned as one closed edge.
            if len(group) == 1:
                edge = group[0]

                try:
                    if bool(edge.isClosed()):
                        wires.append(Part.Wire([edge]))
                        continue
                except Exception:
                    pass

                # Some FreeCAD versions expose the closed state
                # through the number of vertices.
                try:
                    if len(edge.Vertexes) == 1:
                        wires.append(Part.Wire([edge]))
                        continue
                except Exception:
                    pass

                continue

            wire = Part.Wire(group)

            try:
                if wire.isClosed():
                    wires.append(wire)
            except Exception:
                pass

        except Exception:
            if debug:
                App.Console.PrintError(
                    "[Offcuts][DEBUG] Failed to create wire "
                    "from group#%d:\n"
                    % group_index
                    + traceback.format_exc()
                )

    return wires
    
def material_identity_key(material):
    """
    Return a stable identity key for a sheet or DXF offcut.

    Grain is deliberately excluded because it is a mutable
    property of the material row.
    """
    material = material or {}

    material_type = str(
        material.get("type", "")
    ).strip().lower()

    def number(value, default=0.0):
        try:
            return round(float(value), 6)
        except Exception:
            return default

    if material_type == "dxf":
        path = os.path.abspath(
            str(material.get("path", "") or "")
        )

        return (
            "dxf",
            os.path.normcase(path),
        )

    if material_type in (
        "rectangular",
        "sheet",
        "rectangle",
    ):
        material_name = str(
            material.get("material", "")
            or material.get("material_name", "")
            or ""
        ).strip().lower()

        return (
            "rectangular",
            number(material.get("width", 0.0)),
            number(material.get("height", 0.0)),
            number(material.get("thickness", 0.0)),
            material_name,
        )

    return (
        material_type,
        str(
            material.get("label", "")
        ).strip().lower(),
    )


def find_existing_material(materials, material):
    """
    Find an existing material with the same identity.
    """
    key = material_identity_key(material)

    for existing in materials or []:
        if material_identity_key(existing) == key:
            return existing

    return None


def add_or_increment_material(materials, material, count=1):
    """
    Add a new material or increase the count of an existing one.

    Returns:
        tuple(existing_or_new_material, row_index, was_existing)
    """
    try:
        increment = max(1, int(count))
    except Exception:
        increment = 1

    existing = find_existing_material(materials, material)

    if existing is not None:
        try:
            old_count = int(
                existing.get(
                    "count",
                    existing.get("quantity", 1)
                )
            )
        except Exception:
            old_count = 1

        new_count = max(1, old_count) + increment

        existing["count"] = new_count
        existing["quantity"] = new_count

        return existing, materials.index(existing), True

    material["count"] = increment
    material["quantity"] = increment
    materials.append(material)

    return material, len(materials) - 1, False
    
def _point_inside_polygon(point, polygon):
    """
    Return True if point lies inside polygon.
    Uses the ray-casting algorithm.
    """
    try:
        x = float(point[0])
        y = float(point[1])
    except Exception:
        return False

    inside = False
    n = len(polygon or [])

    if n < 3:
        return False

    j = n - 1

    for i in range(n):
        try:
            xi = float(polygon[i][0])
            yi = float(polygon[i][1])
            xj = float(polygon[j][0])
            yj = float(polygon[j][1])

            intersects = (
                ((yi > y) != (yj > y))
                and (
                    x
                    < (xj - xi) * (y - yi) / ((yj - yi) or 1e-30)
                    + xi
                )
            )

            if intersects:
                inside = not inside

        except Exception:
            pass

        j = i

    return inside
    
def _make_contour_record(index, polygon, area, is_outer=False):
    """
    Create a normalized contour record.

    `selected` is only meaningful for non-outer contours.
    The outer contour can never be selected as a hole.
    """
    return {
        "index": int(index),
        "polygon": list(polygon or []),
        "area": float(abs(area)),
        "bbox": poly_bbox(polygon),
        "is_outer": bool(is_outer),
        "selected": False,
    }

def _polygons_are_same(poly_a, poly_b, tolerance=1e-6):
    """
    Best-effort duplicate contour detection using area and bbox.
    """
    try:
        if not poly_a or not poly_b:
            return False

        area_a = abs(polygon_area(poly_a))
        area_b = abs(polygon_area(poly_b))

        if abs(area_a - area_b) > max(
            tolerance,
            max(area_a, area_b) * 1e-6
        ):
            return False

        bbox_a = poly_bbox(poly_a)
        bbox_b = poly_bbox(poly_b)

        for key in (
            "min_x",
            "min_y",
            "max_x",
            "max_y",
        ):
            if abs(
                float(bbox_a[key])
                - float(bbox_b[key])
            ) > tolerance:
                return False

        return True

    except Exception:
        return False

def _append_unique_contour(candidate_polygons, polygon):
    """
    Append a contour only if it is valid and not already present.
    """
    try:
        if not polygon or len(polygon) < 3:
            return

        area = abs(polygon_area(polygon))

        if area <= 1e-9:
            return

        for existing_area, existing_polygon in candidate_polygons:
            if _polygons_are_same(
                existing_polygon,
                polygon
            ):
                return

        candidate_polygons.append(
            (area, polygon)
        )

    except Exception:
        return

def _classify_contours(contours):
    """
    Return all detected contours.

    The largest contour is automatically assigned as outer.
    Every other contour remains selectable by the user.

    Returns:
        outer,
        holes,
        bbox,
        contour_info
    """
    valid = []

    for area, polygon in contours or []:
        try:
            if not polygon or len(polygon) < 3:
                continue

            area = abs(float(area))

            if area <= 1e-9:
                continue

            valid.append(
                (
                    area,
                    list(polygon)
                )
            )

        except Exception:
            continue

    if not valid:
        return None, [], None, []

    valid.sort(
        key=lambda item: item[0],
        reverse=True
    )

    outer_area, outer = valid[0]
    bbox = poly_bbox(outer)

    contour_info = []

    for index, (area, polygon) in enumerate(valid):
        is_outer = index == 0

        contour_info.append(
            _make_contour_record(
                index=index,
                polygon=polygon,
                area=area,
                is_outer=is_outer
            )
        )

    # No holes are selected automatically.
    # The user chooses all non-outer contours in Show.
    holes = []

    return outer, holes, bbox, contour_info