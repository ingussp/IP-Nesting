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

def _parse_allowed_rotations_float(allowed_str, default=None):
    """
    Parse comma-separated rotations into floats, clamp to [0.1 .. 359],
    ensure 0.1 is present (acts like '0' but respects the min constraint).
    """
    if default is None:
        default = [90.0]

    try:
        s = (allowed_str or "").replace("°", "")
        parts = [p.strip() for p in s.split(",") if p.strip()]
        out = []
        for p in parts:
            try:
                v = float(p)
            except Exception:
                continue
            if v < 0.1:
                v = 0.1
            if v > 359.0:
                v = 359.0
            out.append(float(v))

        if not out:
            out = list(default)

        # Ensure 0.1 exists (instead of 0)
        # if not any(abs(x - 0.1) < 1e-9 for x in out):
            # out.insert(0, 0.1)

        # De-duplicate with tolerance, preserve order
        uniq = []
        for v in out:
            if any(abs(v - u) < 1e-9 for u in uniq):
                continue
            uniq.append(float(v))
        return uniq
    except Exception:
        return list(default)
        
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


def execute_nesting(panel):
    """
    Execute nesting operation by exporting configuration to JSON.

    Args:
        panel: NestingTaskPanel instance with UI widgets and state
    """
    try:
        App.Console.PrintMessage("Starting Nesting Export...\n")
        try:
            sheet_w = float(panel.sheet_w.text())
            sheet_h = float(panel.sheet_h.text())
            sheet_margin = float(panel.sheet_margin.text())
        except Exception:
            App.Console.PrintMessage("Invalid sheet inputs, using defaults.\n")
            sheet_w = float(panel.sheet_w.text()) if panel.sheet_w.text() else 2500.0
            sheet_h = float(panel.sheet_h.text()) if panel.sheet_h.text() else 1250.0
            sheet_margin = float(panel.sheet_margin.text()) if panel.sheet_margin.text() else 5.0
            
        boundary_deflection = _read_boundary_deflection(panel, default=0.01)

        payload = {
            "sheet": {
                "width": sheet_w,
                "height": sheet_h,
                "margin": sheet_margin,
                "grain": panel.sheet_grain_combo.currentText(),
            },
            "placement_strategy": panel.select_strategy.currentText() if hasattr(panel, "select_strategy") else "Largest Area First",
            "placement_algorithm": panel.nesting_algorithm.currentText() if hasattr(panel, "nesting_algorithm") else "Bottom Left",
            "geometry_engine": panel.geometry_engine.currentText() if hasattr(panel, "geometry_engine") else "NFP",
            "optimization": panel.optimization_combo.currentText() if hasattr(panel, "optimization_combo") else "None",
            "gpu": panel.gpu_combo.currentText() if hasattr(panel, "gpu_combo") else "None",
            "parts": [],
            "offcuts": [],
        }

        # Export offcuts (duplicates allowed)
        try:
            offcuts = getattr(panel, "offcuts", None)
            if offcuts:
                for off in offcuts:
                    try:
                        polys = off.get("polygons") or []
                        bbox = off.get("bbox") or {}
                        if not polys or not isinstance(polys, list):
                            App.Console.PrintMessage("Warning: offcut missing polygons; skipping: %s\n" % str(off.get("path")))
                            continue
                        payload["offcuts"].append({
                            "label": off.get("label") or os.path.basename(str(off.get("path") or "")),
                            "path": off.get("path") or "",
                            "grain": (off.get("grain") or "None"),
                            "polygons": polys,
                            "bbox": bbox,
                        })
                    except Exception:
                        App.Console.PrintError("Failed exporting an offcut:\n" + traceback.format_exc())
        except Exception:
            App.Console.PrintError("Offcut export failed:\n" + traceback.format_exc())

        p_doc = App.getDocument(panel.preview_doc_name) if panel.preview_doc_name in App.listDocuments() else None
        if not p_doc:
            App.Console.PrintMessage("No preview document found; nothing to export.\n")
            return

        data_rows = panel.table.rowCount() - panel.control_rows
        for row in range(data_rows):
            try:
                name_item = panel.table.item(row, 0)
                qty_item = panel.table.item(row, 1)
                allowed_item = panel.table.item(row, 2)
                if not name_item:
                    continue

                # Qty from table cell (already validated)
                qty = int(qty_item.text()) if qty_item and qty_item.text().isdigit() else 1

                # NEW: allowed rotations as floats (supports 0.1)
                allowed_str = allowed_item.text() if allowed_item else ""
                allowed_rots = _parse_allowed_rotations_float(allowed_str)

                # Grain direction from per-row widget (column 4): if checkbox enabled, read combobox, else None
                grain_value = None
                try:
                    grain_widget = panel.table.cellWidget(row, 4)
                    if grain_widget:
                        cb = grain_widget.findChild(QtGui.QCheckBox)
                        combo = grain_widget.findChild(QtGui.QComboBox)
                        if cb and cb.isChecked() and combo:
                            grain_value = combo.currentText()
                except Exception:
                    grain_value = None

                # Primary preview object name stored at UserRole (keeps compatibility)
                primary_name = name_item.data(QtCore.Qt.UserRole)
                # If list of preview objects exists, prefer first in list
                try:
                    names_json = name_item.data(QtCore.Qt.UserRole + 1)
                    if names_json:
                        if isinstance(names_json, list):
                            names_list = names_json
                        else:
                            names_list = json.loads(names_json)
                        if names_list:
                            primary_name = names_list[0]
                except Exception:
                    pass

                obj_name = primary_name
                obj = p_doc.getObject(obj_name) if obj_name and p_doc else None
                if not obj:
                    App.Console.PrintMessage("Warning: preview object '%s' not found in document; skipping.\n" % str(obj_name))
                    continue

                base = obj.Placement.Base
                rot = obj.Placement.Rotation
                try:
                    axis = rot.Axis
                    angle = rot.Angle
                    rotation_info = {
                        "axis": [axis.x, axis.y, axis.z],
                        "angle_degrees": math.degrees(angle)
                    }
                except Exception:
                    rotation_info = {"axis": [0, 0, 1], "angle_degrees": 0}

                try:
                    bbox = obj.Shape.BoundBox
                    bbox_w = bbox.XMax - bbox.XMin
                    bbox_h = bbox.YMax - bbox.YMin
                except Exception:
                    bbox_w = 0.0
                    bbox_h = 0.0

                # polygon extraction
                polygons = []
                try:
                    for face in obj.Shape.Faces:
                        try:
                            umin, umax, vmin, vmax = face.ParameterRange
                            u_mid = umin + (umax - umin) / 2.0
                            v_mid = vmin + (vmax - vmin) / 2.0
                        except Exception:
                            u_mid = 0.5
                            v_mid = 0.5
                        try:
                            normal = face.normalAt(u_mid, v_mid)
                        except Exception:
                            normal = App.Vector(0, 0, 1)

                        if abs(normal.dot(App.Vector(0, 0, 1))) > 0.9:
                            try:
                                wire = face.OuterWire
                                verts = _extract_wire_points_ordered(wire, deflection=boundary_deflection)
                                if verts and len(verts) >= 2:
                                    polygons.append(verts)
                            except Exception:
                                try:
                                    pts = face.discretize()
                                    poly = []
                                    for pt in pts:
                                        poly.append([round(pt.x, 6), round(pt.y, 6)])
                                    if poly:
                                        polygons.append(poly)
                                except Exception:
                                    pass
                    if not polygons:
                        polys = [
                            [
                                [round(base.x + 0, 6), round(base.y + 0, 6)],
                                [round(base.x + bbox_w, 6), round(base.y + 0, 6)],
                                [round(base.x + bbox_w, 6), round(base.y + bbox_h, 6)],
                                [round(base.x + 0, 6), round(base.y + bbox_h, 6)]
                            ]
                        ]
                        polygons = polys
                except Exception:
                    App.Console.PrintError("Failed to extract polygon for %s:\n%s\n" % (obj_name, traceback.format_exc()))
                    polygons = []

                part_entry = {
                    "label": obj.Label,
                    "name": obj_name,
                    "qty": qty,
                    "allowed_rotations": allowed_rots,  # floats now
                    "placement": {"x": base.x, "y": base.y, "z": base.z},
                    "rotation": rotation_info,
                    "bbox": {"width": bbox_w, "height": bbox_h},
                    "polygons": polygons,
                    "grain": grain_value,
                }
                payload["parts"].append(part_entry)
            except Exception:
                App.Console.PrintError("Error preparing row %d for export:\n%s\n" % (row, traceback.format_exc()))
                continue

        try:
            script_dir = os.path.abspath(os.path.dirname(__file__))
            out_path = os.path.join(script_dir, "libnest2d_export.json")
            with open(out_path, "w") as f:
                json.dump(payload, f, indent=2)
            App.Console.PrintMessage("Nesting JSON written to: %s\n" % out_path)
        except Exception:
            App.Console.PrintError("Failed to write JSON file:\n" + traceback.format_exc())

    except Exception:
        App.Console.PrintError("execute_nesting failed:\n" + traceback.format_exc())