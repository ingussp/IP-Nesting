"""
IPNestingOffcuts - DXF offcut extraction utilities.

Goal:
- Import a DXF into a temporary FreeCAD document.
- Extract the largest closed contour as a 2D polygon for nesting.
- Return polygon points and bbox in document units (typically mm).

One DXF = one offcut sheet.
If DXF contains multiple closed contours, pick the one with the largest absolute area.

Important note (FreeCAD DXF import):
- Some DXF imports create Layer/LayerContainer objects (no Shape) and put actual geometry
  inside groups or elsewhere in the document structure.
- Therefore we scan the WHOLE temp document (recursively expanding Group containers)
  to find Shape-bearing objects.
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
    Convert a FreeCAD wire (or edge) to a 2D polyline.
    Prefer vertex list; fallback to discretize for curved entities.
    """
    pts = []
    try:
        # Prefer explicit vertices if present and sufficient
        try:
            vs = getattr(wire, "Vertexes", None)
            if vs and len(vs) >= 3:
                for v in vs:
                    try:
                        pts.append([float(v.X), float(v.Y)])
                    except Exception:
                        pass
        except Exception:
            pts = []

        if len(pts) >= 3:
            return pts

        # Fallback: discretize (works for curves)
        try:
            dpts = wire.discretize(Deflection=float(deflection))
            for p in dpts:
                try:
                    pts.append([float(p.x), float(p.y)])
                except Exception:
                    pass
        except Exception:
            pts = []
    except Exception:
        pts = []

    # Remove duplicated last point if equal to first (keep as non-closed list)
    try:
        if len(pts) >= 2 and pts[0][0] == pts[-1][0] and pts[0][1] == pts[-1][1]:
            pts = pts[:-1]
    except Exception:
        pass
    return pts


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
    
def extract_offcut_from_dxf(path, debug=False):
    """
    Import a DXF into a temporary document and extract
    the largest closed contour.
    """
    doc = None

    try:
        if not path or not os.path.exists(path):
            App.Console.PrintError(
                "[Offcuts] DXF file does not exist: %s\n"
                % str(path)
            )
            return None, None

        doc_name = "IPNesting_OffcutTmp"

        while doc_name in App.listDocuments():
            doc_name += "_1"

        doc = App.newDocument(doc_name)

        if not _import_dxf(path, doc.Name):
            return None, None

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
            return None, None

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

        # First use directly available closed wires.
        for obj in objects:
            try:
                shape = getattr(obj, "Shape", None)
                if shape is None:
                    continue

                for wire in list(getattr(shape, "Wires", []) or []):
                    if not wire.isClosed():
                        continue

                    poly = _wire_to_polyline_2d(wire)
                    if len(poly) >= 3:
                        area = abs(polygon_area(poly))
                        candidate_polygons.append((area, poly))

            except Exception:
                if debug:
                    App.Console.PrintWarning(
                        "[Offcuts][DEBUG] Direct wire scan failed:\n"
                        + traceback.format_exc()
                    )

        # If direct wires were not available, assemble them from edges.
        if not candidate_polygons:
            closed_wires = _closed_wires_from_edges(
                all_edges,
                debug=debug
            )

            for wire in closed_wires:
                try:
                    poly = _wire_to_polyline_2d(wire)
                    if len(poly) >= 3:
                        area = abs(polygon_area(poly))
                        candidate_polygons.append((area, poly))
                except Exception:
                    if debug:
                        App.Console.PrintWarning(
                            "[Offcuts][DEBUG] Wire conversion failed:\n"
                            + traceback.format_exc()
                        )

        if not candidate_polygons:
            App.Console.PrintError(
                "[Offcuts] No closed contours found in %s\n"
                % os.path.basename(path)
            )
            return None, None

        candidate_polygons.sort(
            key=lambda item: item[0],
            reverse=True
        )

        polygon = candidate_polygons[0][1]
        bbox = poly_bbox(polygon)

        return polygon, bbox

    except Exception:
        App.Console.PrintError(
            "[Offcuts] extract_offcut_from_dxf failed:\n"
            + traceback.format_exc()
        )
        return None, None

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
    Returns a list of closed Part.Wire objects.
    """
    wires = []

    if Part is None:
        return wires

    if not edges:
        return wires

    try:
        if debug:
            App.Console.PrintMessage(
                "[Offcuts][DEBUG] Part.sortEdges START. Edges=%d\n"
                % len(edges)
            )

        groups = Part.sortEdges(edges)

        if debug:
            App.Console.PrintMessage(
                "[Offcuts][DEBUG] Part.sortEdges END. Groups=%d\n"
                % len(groups or [])
            )

    except Exception:
        if debug:
            App.Console.PrintError(
                "[Offcuts][DEBUG] Part.sortEdges failed:\n"
                + traceback.format_exc()
            )
        return wires

    for group_index, group in enumerate(groups or []):
        try:
            if not group or len(group) < 2:
                continue

            wire = Part.Wire(group)
            closed = bool(wire.isClosed())

            if debug:
                App.Console.PrintMessage(
                    "[Offcuts][DEBUG] Wire from group#%d "
                    "closed=%s edges=%d\n"
                    % (
                        group_index,
                        str(closed),
                        len(group),
                    )
                )

            if closed:
                wires.append(wire)

        except Exception:
            if debug:
                App.Console.PrintError(
                    "[Offcuts][DEBUG] Failed to create wire "
                    "from group#%d:\n"
                    % group_index
                    + traceback.format_exc()
                )

    return wires