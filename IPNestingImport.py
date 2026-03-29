import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import json
import os
import traceback


def _set_object_color(view_obj, r, g, b):
    try:
        view_obj.ShapeColor = (float(r), float(g), float(b))
    except Exception:
        pass


def apply_nesting_result(panel, result_path):
    """
    Read libnest2d_import.json and apply placements to preview objects.

    Matching:
    - placement.id matches table row label text
    - each row may contain one or more preview object names in UserRole+1
    """
    try:
        if not result_path or not os.path.exists(result_path):
            App.Console.PrintError("Nesting import file not found: %s\n" % str(result_path))
            return False

        if panel.preview_doc_name not in App.listDocuments():
            App.Console.PrintError("Preview document not found: %s\n" % str(panel.preview_doc_name))
            return False

        p_doc = App.getDocument(panel.preview_doc_name)
        if not p_doc:
            App.Console.PrintError("Failed to get preview document.\n")
            return False

        with open(result_path, "r") as f:
            data = json.load(f)

        placements = data.get("placements", [])
        if not isinstance(placements, list):
            App.Console.PrintError("Invalid placements in result JSON.\n")
            return False

        by_id = {}
        for pl in placements:
            try:
                pid = str(pl.get("id", "")).strip()
                if not pid:
                    continue
                by_id.setdefault(pid, []).append(pl)
            except Exception:
                continue

        applied = 0
        hidden = 0
        data_rows = panel.table.rowCount() - panel.control_rows

        for row in range(data_rows):
            try:
                label_item = panel.table.item(row, 0)
                if not label_item:
                    continue

                row_label = str(label_item.text()).strip()
                if not row_label:
                    continue

                row_placements = by_id.get(row_label, [])
                if not row_placements:
                    continue

                row_names = []
                try:
                    list_data = label_item.data(QtCore.Qt.UserRole + 1)
                    if list_data:
                        if isinstance(list_data, list):
                            row_names = list(list_data)
                        else:
                            row_names = json.loads(list_data)
                except Exception:
                    row_names = []

                if not row_names:
                    try:
                        primary = label_item.data(QtCore.Qt.UserRole)
                        if primary:
                            row_names = [primary]
                    except Exception:
                        row_names = []

                for idx, obj_name in enumerate(row_names):
                    try:
                        if idx >= len(row_placements):
                            continue

                        obj = p_doc.getObject(obj_name)
                        if not obj:
                            continue

                        pl = row_placements[idx]
                        placed = bool(pl.get("placed", False))
                        x = float(pl.get("x", 0.0))
                        y = float(pl.get("y", 0.0))
                        rot_deg = float(pl.get("rotation_deg", 0.0))

                        if placed:
                            base = obj.Placement.Base
                            obj.Placement = App.Placement(
                                App.Vector(x, y, base.z),
                                App.Rotation(App.Vector(0, 0, 1), rot_deg)
                            )

                            try:
                                obj.ViewObject.Visibility = True
                            except Exception:
                                pass

                            try:
                                _set_object_color(obj.ViewObject, 0.8, 0.8, 0.8)
                            except Exception:
                                pass

                            applied += 1
                        else:
                            try:
                                obj.ViewObject.Visibility = False
                            except Exception:
                                try:
                                    _set_object_color(obj.ViewObject, 1.0, 0.2, 0.2)
                                except Exception:
                                    pass
                            hidden += 1

                    except Exception:
                        App.Console.PrintError(
                            "Failed applying placement for '%s':\n%s\n"
                            % (str(obj_name), traceback.format_exc())
                        )

            except Exception:
                App.Console.PrintError(
                    "apply_nesting_result row error:\n%s\n" % traceback.format_exc()
                )

        try:
            p_doc.recompute()
        except Exception:
            pass

        try:
            Gui.setActiveDocument(p_doc)
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass

        App.Console.PrintMessage(
            "Nesting import applied. Placed: %d, hidden/unplaced: %d\n" % (applied, hidden)
        )
        return True

    except Exception:
        App.Console.PrintError("apply_nesting_result failed:\n" + traceback.format_exc())
        return False