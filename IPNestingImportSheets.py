from IPNestingLanguages import tr
import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtGui
import json
import os
import traceback


# Replace unsupported name characters with underscores for generated document and sketch names.
def _safe_doc_name(name):
    out = []
    for ch in str(name):
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


# Return a closed copy of the point sequence, leaving empty input unchanged.
def _close_polygon(points):
    if not points:
        return points
    if points[0] != points[-1]:
        return list(points) + [points[0]]
    return list(points)


# Convert absolute export polygon coordinates to local coordinates by shifting so min x/y
# becomes 0/0.
def _normalize_polygon(poly):
    """
    Convert absolute export polygon coordinates to local coordinates
    by shifting so min x/y becomes 0/0.
    """
    if not poly:
        return []

    xs = [float(p[0]) for p in poly if len(p) >= 2]
    ys = [float(p[1]) for p in poly if len(p) >= 2]
    if not xs or not ys:
        return []

    min_x = min(xs)
    min_y = min(ys)

    return [[float(p[0]) - min_x, float(p[1]) - min_y] for p in poly if len(p) >= 2]


# Create an XY sketch from a closed point sequence, returning None on failure.
def _create_sketch_with_polygon(doc, sketch_name, label, points):
    try:
        sk = doc.addObject("Sketcher::SketchObject", sketch_name)
        sk.Label = label

        pts = _close_polygon(points)
        if len(pts) < 2:
            return sk

        geoms = []
        for i in range(len(pts) - 1):
            p1 = pts[i]
            p2 = pts[i + 1]
            v1 = App.Vector(float(p1[0]), float(p1[1]), 0.0)
            v2 = App.Vector(float(p2[0]), float(p2[1]), 0.0)
            geoms.append(Part.LineSegment(v1, v2))

        if geoms:
            sk.addGeometry(geoms, False)

        return sk
    except Exception:
        App.Console.PrintError(tr('create_sketch_with_polygon_failed') + traceback.format_exc())
        return None


# Draw legacy polygons in per-bin documents using XY translations without placement rotation.
def import_nesting_sheets(export_path, import_path):
    """
    Import legacy parts[].polygons and placements into per-bin documents.

    Only the first contour is drawn, normalized to its minimum XY and
    translated by placement x/y. Placement rotation and holes are ignored.
    This helper does not consume the current nesting CLI parts[].points format.
    """
    try:
        if not os.path.exists(export_path):
            App.Console.PrintError(tr('export_json_not_found_s') % export_path)
            return False

        if not os.path.exists(import_path):
            App.Console.PrintError(tr('import_json_not_found_s') % import_path)
            return False

        with open(export_path, "r", encoding="utf-8") as f:
            export_data = json.load(f)

        with open(import_path, "r", encoding="utf-8") as f:
            import_data = json.load(f)

        export_parts = export_data.get("parts", [])
        placements = import_data.get("placements", [])
        sheet = import_data.get("sheet", {})

        sheet_w = float(sheet.get("width", 0.0))
        sheet_h = float(sheet.get("height", 0.0))
        sheet_margin = float(sheet.get("margin", 0.0))

        # Build export map by source_part_index
        export_by_index = {}
        for idx, part in enumerate(export_parts):
            export_by_index[idx] = part

        # Group placements by bin_id
        bins = {}
        for pl in placements:
            try:
                if not bool(pl.get("placed", False)):
                    continue
                bin_id = int(pl.get("bin_id", -1))
                if bin_id < 0:
                    continue
                bins.setdefault(bin_id, []).append(pl)
            except Exception:
                continue

        if not bins:
            App.Console.PrintMessage(tr('no_placed_parts_found_to_import_into_sheet_documents'))
            return False

        created_docs = []

        for bin_id in sorted(bins.keys()):
            doc_name = _safe_doc_name("Nesting_Bin_%d" % bin_id)

            # If doc already exists, remove it first
            try:
                if doc_name in App.listDocuments():
                    App.closeDocument(doc_name)
            except Exception:
                pass

            doc = App.newDocument(doc_name)
            created_docs.append(doc_name)

            # Optional: draw sheet boundary as sketch
            try:
                if sheet_w > 0 and sheet_h > 0:
                    boundary = [
                        [0.0, 0.0],
                        [sheet_w, 0.0],
                        [sheet_w, sheet_h],
                        [0.0, sheet_h]
                    ]
                    _create_sketch_with_polygon(
                        doc,
                        "SheetBoundary",
                        tr('sheet_boundary'),
                        boundary
                    )
            except Exception:
                App.Console.PrintError(tr('failed_to_draw_sheet_boundary') + traceback.format_exc())

            for idx, pl in enumerate(bins[bin_id]):
                try:
                    src_idx = int(pl.get("source_part_index", -1))
                    if src_idx < 0:
                        continue

                    part = export_by_index.get(src_idx)
                    if not part:
                        continue

                    part_label = str(pl.get("id", "Part_%d" % idx))
                    place_x = float(pl.get("x", 0.0))
                    place_y = float(pl.get("y", 0.0))

                    polygons = part.get("polygons", [])
                    if not polygons or not isinstance(polygons, list):
                        continue

                    # use outer contour only for now
                    outer = polygons[0]
                    local_poly = _normalize_polygon(outer)
                    if not local_poly:
                        continue

                    placed_poly = []
                    for pt in local_poly:
                        placed_poly.append([
                            place_x + float(pt[0]),
                            place_y + float(pt[1])
                        ])

                    sketch_name = _safe_doc_name("Sketch_%02d_%s" % (idx, part_label))
                    _create_sketch_with_polygon(
                        doc,
                        sketch_name,
                        part_label,
                        placed_poly
                    )

                except Exception:
                    App.Console.PrintError(
                        tr('failed_to_import_placement_into_bin_d_s')
                        % (bin_id, traceback.format_exc())
                    )

            try:
                doc.recompute()
            except Exception:
                pass

        # Activate first created doc
        try:
            if created_docs:
                Gui.activateWorkbench("SketcherWorkbench")
                Gui.setActiveDocument(created_docs[0])
                Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass

        App.Console.PrintMessage(
            tr('created_d_nesting_sheet_document_s_s')
            % (len(created_docs), ", ".join(created_docs))
        )
        return True

    except Exception:
        App.Console.PrintError(tr('import_nesting_sheets_failed') + traceback.format_exc())
        return False
