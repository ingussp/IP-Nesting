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


def _import_dxf2(path, doc_name):
    """Import DXF into a FreeCAD document."""
    try:
        import importDXF
        importDXF.insert(path, doc_name)
        return True
    except Exception:
        pass

    try:
        import ImportGui
        ImportGui.insert(path, doc_name)
        return True
    except Exception:
        pass

    return False

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

        App.Console.PrintMessage(
            "[Offcuts] Importing DXF using "
            "module_io.OpenInsertObject(): %s\n" % str(path)
        )

        module_io.OpenInsertObject(
            "importDXF",
            str(path),
            "insert",
            str(doc_name)
        )

        App.Console.PrintMessage(
            "[Offcuts] DXF import completed using module_io.\n"
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

        App.Console.PrintMessage(
            "[Offcuts] Falling back to importDXF.insert(): %s\n"
            % str(path)
        )

        importDXF.insert(
            str(path),
            str(doc_name)
        )

        App.Console.PrintMessage(
            "[Offcuts] DXF import completed using importDXF.insert().\n"
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


def _closed_wires_from_edges(edges, debug=True):
    """
    Try to assemble closed wires from a flat list of edges.
    Returns list of Part.Wire objects that are closed.
    """
    wires = []
    if Part is None:
        return wires
    try:
        if not edges:
            return wires

        groups = []
        try:
            App.Console.PrintMessage(
                "[Offcuts][DEBUG] Part.sortEdges START. "
                "Edges=%d\n"
                % len(edges)
            )

            groups = Part.sortEdges(edges)

            App.Console.PrintMessage(
                "[Offcuts][DEBUG] Part.sortEdges END. "
                "Groups=%d\n"
                % len(groups or [])
            )

        except Exception as e:
            if debug:
                App.Console.PrintError("[Offcuts][DEBUG] Part.sortEdges failed: %s\n" % str(e))
            groups = []

        for gi, g in enumerate(groups or []):
            try:
                if not g or len(g) < 2:
                    continue
                w = Part.Wire(g)
                is_closed = False
                try:
                    is_closed = bool(w.isClosed())
                except Exception:
                    is_closed = False

                if debug:
                    try:
                        App.Console.PrintMessage("[Offcuts][DEBUG] Wire from group#%d closed=%s edges=%d\n" %
                                                 (gi, str(is_closed), len(getattr(w, "Edges", []) or [])))
                    except Exception:
                        pass

                if is_closed:
                    wires.append(w)
            except Exception:
                continue

    except Exception:
        App.Console.PrintError("_closed_wires_from_edges failed:\n" + traceback.format_exc())

    return wires


def extract_offcut_from_dxf(path, temp_doc_name="IPNesting_OffcutTmp", debug=False):
    """
    Extract largest closed contour polygon from DXF.

    Strategy:
      1) Import DXF into a temporary document.
      2) Scan the whole document (recursively) for objects that have Shape.
      3) Prefer closed Wires directly (if any).
      4) Otherwise collect all Edges, sort/join them into closed Wires via Part.sortEdges + Part.Wire.
      5) Convert each closed wire to 2D polyline and pick the largest by absolute area.

    Returns:
        (poly, bbox) where:
          poly: [[x,y], ...] (non-closed list)
          bbox: {"min_x","min_y","max_x","max_y"}
        If extraction fails -> (None, None)
    """
    doc = None
    try:
        if not path or not os.path.exists(path):
            App.Console.PrintError("IPNestingOffcuts: DXF not found: %s\n" % str(path))
            return None, None

        # Create temp doc (unique name if needed)
        base = temp_doc_name
        name = base
        idx = 1
        while name in App.listDocuments():
            idx += 1
            name = "%s_%d" % (base, idx)

        doc = App.newDocument(name)

        ok = _import_dxf(path, doc.Name)
        if not ok:
            App.Console.PrintError("IPNestingOffcuts: DXF import failed: %s\n" % str(path))
            return None, None

        try:
            doc.recompute()
        except Exception:
            pass

        App.Console.PrintMessage(
            "[Offcuts] Temporary document '%s' objects after import: %d\n"
            % (doc.Name, len(doc.Objects))
        )

        if not doc.Objects:
            App.Console.PrintError(
                "[Offcuts] DXF importer returned successfully, "
                "but temporary document contains no objects.\n"
            )
            return None, None

        # NEW: scan all objects recursively; don't rely on "new objects" detection
        App.Console.PrintMessage(
            "[Offcuts][DEBUG] Starting recursive document scan...\n"
        )

        all_objs = list(_iter_doc_objects_recursive(doc))

        App.Console.PrintMessage(
            "[Offcuts][DEBUG] Recursive document scan finished. Objects=%d\n"
            % len(all_objs)
        )
        if debug:
            App.Console.PrintMessage("[Offcuts][DEBUG] Imported DXF: %s\n" % os.path.basename(path))
            App.Console.PrintMessage("[Offcuts][DEBUG] Doc objects total (recursive): %d\n" % len(all_objs))
            try:
                for o in all_objs:
                    try:
                        ec = _shape_edge_count(o)
                        if ec > 0:
                            App.Console.PrintMessage("[Offcuts][DEBUG] Obj=%s Type=%s edges=%d\n" %
                                                     (getattr(o, "Name", "?"), getattr(o, "TypeId", "?"), ec))
                    except Exception:
                        continue
            except Exception:
                pass

        candidate_polys = []
        App.Console.PrintMessage(
            "[Offcuts][DEBUG] Starting direct wire scan...\n"
        )

        # ---- 1) Try direct closed wires
        total_wires = 0
        for o in all_objs:
            try:
                shp = getattr(o, "Shape", None)
                if shp is None:
                    continue

                try:
                    wires = list(getattr(shp, "Wires", [])) or []
                except Exception:
                    wires = []

                total_wires += len(wires)

                for wi, w in enumerate(wires):
                    try:
                        closed = False
                        try:
                            closed = bool(w.isClosed())
                        except Exception:
                            closed = False

                        if debug and wires:
                            try:
                                App.Console.PrintMessage("[Offcuts][DEBUG] Wire: obj=%s wire#%d closed=%s edges=%d\n" %
                                                         (getattr(o, "Name", "?"), wi, str(closed), len(getattr(w, "Edges", []) or [])))
                            except Exception:
                                pass

                        if not closed:
                            continue

                        poly = _wire_to_polyline_2d(w)
                        if poly and len(poly) >= 3:
                            candidate_polys.append((abs(polygon_area(poly)), poly))
                    except Exception:
                        continue
            except Exception:
                continue

        App.Console.PrintMessage(
            "[Offcuts][DEBUG] Direct wire scan finished. "
            "Wires=%d candidates=%d\n"
            % (total_wires, len(candidate_polys))
        )
        
        if debug:
            App.Console.PrintMessage("[Offcuts][DEBUG] Total wires seen in doc: %d; candidate polys from wires: %d\n" %
                                     (total_wires, len(candidate_polys)))

        # ---- 2) Assemble from edges if needed (ARC-only DXF etc.)
        if not candidate_polys and Part is not None:
            all_edges = []
            for o in all_objs:
                try:
                    shp = getattr(o, "Shape", None)
                    if shp is None:
                        continue
                    all_edges.extend(_collect_edges_from_shape(shp))
                except Exception:
                    continue

            if debug:
                App.Console.PrintMessage("[Offcuts][DEBUG] Total edges collected from doc: %d\n" % len(all_edges))
                try:
                    for i, e in enumerate(all_edges[:10]):
                        v1 = e.Vertexes[0].Point
                        v2 = e.Vertexes[-1].Point
                        App.Console.PrintMessage("[Offcuts][DEBUG] Edge#%d endpoints: (%.6f,%.6f) -> (%.6f,%.6f)\n" %
                                                 (i, v1.x, v1.y, v2.x, v2.y))
                except Exception:
                    pass

            App.Console.PrintMessage(
                "[Offcuts][DEBUG] Before _closed_wires_from_edges(). "
                "Edges=%d\n"
                % len(all_edges)
            )

            closed_wires = _closed_wires_from_edges(
                all_edges,
                debug=True
            )

            App.Console.PrintMessage(
                "[Offcuts][DEBUG] After _closed_wires_from_edges(). "
                "Closed wires=%d\n"
                % len(closed_wires)
            )
            if debug:
                App.Console.PrintMessage("[Offcuts][DEBUG] Closed wires from edges: %d\n" % len(closed_wires))

            for wire_index, w in enumerate(closed_wires):
                try:
                    App.Console.PrintMessage(
                        "[Offcuts][DEBUG] Converting closed wire #%d to polyline...\n"
                        % wire_index
                    )

                    poly = _wire_to_polyline_2d(w)

                    App.Console.PrintMessage(
                        "[Offcuts][DEBUG] Wire #%d converted. Points=%d\n"
                        % (wire_index, len(poly or []))
                    )

                    if poly and len(poly) >= 3:
                        area = abs(polygon_area(poly))

                        App.Console.PrintMessage(
                            "[Offcuts][DEBUG] Wire #%d area=%f\n"
                            % (wire_index, area)
                        )

                        candidate_polys.append((area, poly))

                except Exception:
                    App.Console.PrintError(
                        "[Offcuts][DEBUG] Failed to convert wire #%d:\n"
                        % wire_index
                        + traceback.format_exc()
                    )

        if not candidate_polys:
            App.Console.PrintError("IPNestingOffcuts: no closed contours found in %s\n" % os.path.basename(path))
            return None, None

        candidate_polys.sort(key=lambda t: t[0], reverse=True)
        App.Console.PrintMessage(
            "[Offcuts][DEBUG] Candidate polygons=%d\n"
            % len(candidate_polys)
        )

        poly = candidate_polys[0][1]
        bbox = poly_bbox(poly)

        App.Console.PrintMessage(
            "[Offcuts][DEBUG] Selected polygon. Points=%d bbox=%s\n"
            % (len(poly), str(bbox))
        )

        if debug:
            App.Console.PrintMessage("[Offcuts][DEBUG] Selected polygon points=%d bbox=%s\n" % (len(poly), str(bbox)))

        App.Console.PrintMessage(
            "[Offcuts][DEBUG] extract_offcut_from_dxf RETURN\n"
        )
        return poly, bbox

    except Exception:
        App.Console.PrintError("extract_offcut_from_dxf failed:\n" + traceback.format_exc())
        return None, None

    finally:
        try:
            if doc is not None:
                App.closeDocument(doc.Name)
        except Exception:
            pass