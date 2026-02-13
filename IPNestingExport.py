"""
IPNestingExport - Nesting execution logic extracted from IPNestingGui.
Exports nesting configuration to libnest2d_export.json.
"""

import FreeCAD as App
from PySide import QtGui, QtCore
import json
import os
import traceback


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

        payload = {
            "sheet": {
                "width": sheet_w,
                "height": sheet_h,
                "margin": sheet_margin,
                "grain": panel.sheet_grain_combo.currentText(),
            },
            "algorithm": panel.nesting_algorithm.currentText() if hasattr(panel, "nesting_algorithm") else "None",
            "parts": [],
            # NEW: offcuts (DXF) additional sheets. EXE should try these before full sheets.
            "offcuts": [],
        }

        # NEW: export offcuts if present on panel (duplicates allowed)
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
                            "grain": (off.get("grain") or "X"),
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

                allowed_str = allowed_item.text() if allowed_item else "0,90,180,270"
                allowed_rots = []
                for token in allowed_str.replace("°", "").split(","):
                    token = token.strip()
                    if token:
                        try:
                            allowed_rots.append(int(token))
                        except Exception:
                            pass
                if 0 not in allowed_rots:
                    allowed_rots.insert(0, 0)

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
                    rotation_info = {"axis": [axis.x, axis.y, axis.z], "angle_degrees": angle}
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
                                verts = []
                                for v in wire.Vertexes:
                                    verts.append([round(v.X, 6), round(v.Y, 6)])
                                if verts:
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
                    "allowed_rotations": allowed_rots,
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