import FreeCAD as App
import FreeCADGui as Gui
import Part
import json
import os
import traceback


def _safe_name(s):
    out = []
    for ch in str(s):
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def _close_poly(points):
    if not points:
        return []
    pts = list(points)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _bbox_of_points(points):
    xs = [float(p[0]) for p in points if len(p) >= 2]
    ys = [float(p[1]) for p in points if len(p) >= 2]
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _translate_poly(points, dx, dy):
    """
    Translate Deepnest point objects into debug sketch coordinates.
    """
    return [
        [
            float(point.get("x", 0.0)) + dx,
            float(point.get("y", 0.0)) + dy
        ]
        for point in points or []
        if isinstance(point, dict)
    ]


def _add_poly_sketch(doc, name, label, poly):
    try:
        sk = doc.addObject("Sketcher::SketchObject", name)
        sk.Label = label

        pts = _close_poly(poly)
        if len(pts) < 2:
            return sk

        geoms = []
        for i in range(len(pts) - 1):
            p1 = pts[i]
            p2 = pts[i + 1]
            geoms.append(
                Part.LineSegment(
                    App.Vector(float(p1[0]), float(p1[1]), 0.0),
                    App.Vector(float(p2[0]), float(p2[1]), 0.0)
                )
            )

        if geoms:
            sk.addGeometry(geoms, False)

        return sk
    except Exception:
        App.Console.PrintError("_add_poly_sketch failed:\n" + traceback.format_exc())
        return None


def debug_draw_export_polygons(export_path):
    """
    Draw exported polygons exactly as they appear in libnest2d_export.json,
    arranged in a separate debug document so we can inspect what is sent to the exe.
    """
    try:
        if not export_path or not os.path.exists(export_path):
            App.Console.PrintError("Export JSON not found: %s\n" % str(export_path))
            return False

        with open(export_path, "r") as f:
            data = json.load(f)

        parts = data.get("parts", [])
        if not isinstance(parts, list) or not parts:
            App.Console.PrintError("No parts found in export JSON.\n")
            return False

        doc_name = "Nesting_Export_Debug"
        try:
            if doc_name in App.listDocuments():
                App.closeDocument(doc_name)
        except Exception:
            pass

        doc = App.newDocument(doc_name)

        spacing_x = 300.0
        spacing_y = 300.0
        current_x = 0.0
        current_y = 0.0
        row_max_h = 0.0
        cols = 4
        col_idx = 0

        for i, part in enumerate(parts):
            try:
                label = str(part.get("label", "Part_%d" % i))
                points = part.get("points", [])
                if not points or not isinstance(points, list):
                    continue

                # Collect all polygon points to estimate bbox for layout
                all_pts = []
                all_pts = [
                    [point.get("x", 0.0), point.get("y", 0.0)]
                    for point in points
                    if isinstance(point, dict)
                ]

                bb = _bbox_of_points(all_pts)
                if bb is None:
                    continue

                min_x, min_y, max_x, max_y = bb
                width = max_x - min_x
                height = max_y - min_y

                # Place this part into a grid cell, preserving original polygon topology
                dx = current_x - min_x
                dy = current_y - min_y

                # Draw all polygons for this part
                moved = _translate_poly(
                    points,
                    dx,
                    dy
                )

                sketch_name = _safe_name(
                    "Dbg_%02d_%s" % (i, label)
                )

                sketch_label = "%s [points]" % label

                _add_poly_sketch(
                    doc,
                    sketch_name,
                    sketch_label,
                    moved
                )

                # Advance layout
                row_max_h = max(row_max_h, height)
                col_idx += 1
                if col_idx >= cols:
                    col_idx = 0
                    current_x = 0.0
                    current_y += row_max_h + spacing_y
                    row_max_h = 0.0
                else:
                    current_x += width + spacing_x

            except Exception:
                App.Console.PrintError(
                    "Failed drawing export polygon for part %d:\n%s\n"
                    % (i, traceback.format_exc())
                )

        try:
            doc.recompute()
        except Exception:
            pass

        try:
            Gui.activateWorkbench("SketcherWorkbench")
            Gui.setActiveDocument(doc.Name)
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass

        App.Console.PrintMessage(
            "Export polygon debug document created: %s\n" % doc.Name
        )
        return True

    except Exception:
        App.Console.PrintError("debug_draw_export_polygons failed:\n" + traceback.format_exc())
        return False