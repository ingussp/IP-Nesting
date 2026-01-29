
# Rotation helper for IP - Nesting
# Provides NestingRotator to perform bulk rotations of preview objects
#
# Updated: robust checkbox detection and fixed control-row detection bug:
# - control rows are detected with `name_item.flags() == QtCore.Qt.NoItemFlags`
#   (NoItemFlags == 0, must use equality check, not bitwise AND)
# - checks layout.itemAt(...) and widget.findChildren for QCheckBox
# - reads full copy list from UserRole+1 when available (so all copies are rotated)
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import traceback
import json

class NestingRotator:
    def __init__(self, preview_doc_name):
        self.preview_doc_name = preview_doc_name

    def _find_checkbox_in_widget(self, widget):
        """Return first QCheckBox descendant found in widget (search recursively)."""
        try:
            # try layout first (common case)
            try:
                lay = widget.layout()
                if lay and lay.count() > 0:
                    for i in range(lay.count()):
                        try:
                            w = lay.itemAt(i).widget()
                            if w is None:
                                continue
                            if isinstance(w, QtGui.QCheckBox):
                                return w
                            # if this is a container, examine its descendants
                            desc = w.findChildren(QtGui.QCheckBox)
                            if desc:
                                return desc[0]
                        except Exception:
                            continue
            except Exception:
                pass
            # fallback: findChildren (recursive)
            try:
                chk = widget.findChildren(QtGui.QCheckBox)
                if chk:
                    return chk[0]
            except Exception:
                pass
        except Exception:
            App.Console.PrintError("_find_checkbox_in_widget error:\n" + traceback.format_exc())
        return None

    def _get_checked_object_names_from_table(self, table):
        """Return list of preview object names for all checked rows.

        For each checked row, prefer the full list stored in UserRole+1 (JSON or list).
        Fallback to primary name in UserRole if list missing.
        Robust detection of checkbox: supports widget-contained checkboxes and QTableWidgetItem checkState.
        Skips control rows by checking name_item.flags() == QtCore.Qt.NoItemFlags (== 0).
        """
        names = []
        try:
            row_count = max(0, table.rowCount())
            for r in range(row_count):
                try:
                    # name item at column 0
                    name_item = table.item(r, 0)
                    if not name_item:
                        continue
                    # Skip control rows marked with NoItemFlags (these rows are not data rows)
                    try:
                        if name_item.flags() == QtCore.Qt.NoItemFlags:
                            continue
                    except Exception:
                        pass

                    # Primary method: find checkbox widget in column 3 and check isChecked()
                    widget = table.cellWidget(r, 3)
                    checked = False
                    if widget:
                        cb = self._find_checkbox_in_widget(widget)
                        if cb is not None:
                            try:
                                if cb.isChecked():
                                    checked = True
                            except Exception:
                                pass

                    # Fallback: maybe the checkbox was placed as a table item with checkState in column 3
                    if not checked:
                        try:
                            item3 = table.item(r, 3)
                            if item3:
                                try:
                                    if item3.checkState() == QtCore.Qt.Checked:
                                        checked = True
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    if not checked:
                        continue

                    # gather all preview object names for this row
                    row_names = []
                    try:
                        list_data = name_item.data(QtCore.Qt.UserRole + 1)
                        if list_data:
                            if isinstance(list_data, list):
                                row_names = list(list_data)
                            else:
                                try:
                                    parsed = json.loads(list_data)
                                    if isinstance(parsed, list):
                                        row_names = parsed
                                    else:
                                        row_names = [parsed]
                                except Exception:
                                    row_names = []
                    except Exception:
                        row_names = []

                    if not row_names:
                        try:
                            primary = name_item.data(QtCore.Qt.UserRole)
                            if primary:
                                row_names = [primary]
                        except Exception:
                            row_names = []

                    # append preserving order, avoid duplicates
                    for n in row_names:
                        if n and n not in names:
                            names.append(n)

                except Exception:
                    # per-row exceptions shouldn't break entire loop
                    continue
        except Exception:
            App.Console.PrintError("_get_checked_object_names_from_table failed:\n" + traceback.format_exc())
        return names

    def _axis_vector(self, axis_char):
        a = axis_char.upper() if isinstance(axis_char, str) and axis_char else "Z"
        if a == "X":
            return App.Vector(1, 0, 0)
        if a == "Y":
            return App.Vector(0, 1, 0)
        return App.Vector(0, 0, 1)

    def _bbox_dict(self, bb):
        return {
            "XMin": bb.XMin, "XMax": bb.XMax,
            "YMin": bb.YMin, "YMax": bb.YMax,
            "ZMin": bb.ZMin, "ZMax": bb.ZMax
        }

    def _bbox_center(self, bbox):
        cx = 0.5 * (bbox["XMin"] + bbox["XMax"])
        cy = 0.5 * (bbox["YMin"] + bbox["YMax"])
        cz = 0.5 * (bbox["ZMin"] + bbox["ZMax"])
        return App.Vector(cx, cy, cz)

    def _detect_widest_side(self, bbox):
        """Return 'X' if width >= depth (X >= Y), else 'Y'."""
        width_x = bbox["XMax"] - bbox["XMin"]
        width_y = bbox["YMax"] - bbox["YMin"]
        return "X" if width_x >= width_y else "Y"

    def apply_bulk_rotate(self, table, p_doc, angle_degrees, axis_char="X"):
        """Rotate all checked objects by angle_degrees around axis_char at each object's bbox center.

        Ensures rotation stays inside the part's original bbox footprint where possible
        and aligns tops (ZMax) to a common target (max original ZMax).
        """
        try:
            if not p_doc:
                App.Console.PrintMessage("NestingRotator.apply_bulk_rotate: no preview document provided.\n")
                return

            names = self._get_checked_object_names_from_table(table)
            if not names:
                App.Console.PrintMessage("NestingRotator.apply_bulk_rotate: no rows selected (checkbox).\n")
                return

            axis_vec = self._axis_vector(axis_char)
            angle = int(angle_degrees)

            # Save original bounding boxes and determine widest side & group top
            orig_bboxes = {}
            widest_side = {}
            group_target_top = -1e99
            for name in names:
                try:
                    o = p_doc.getObject(name)
                    if not o:
                        continue
                    bb = o.Shape.BoundBox
                    orig = self._bbox_dict(bb)
                    orig_bboxes[name] = orig
                    widest_side[name] = self._detect_widest_side(orig)
                    if orig["ZMax"] > group_target_top:
                        group_target_top = orig["ZMax"]
                except Exception:
                    orig_bboxes[name] = {"XMin":0,"XMax":0,"YMin":0,"YMax":0,"ZMin":0,"ZMax":0}
                    widest_side[name] = "X"

            # Apply rotation about each object's bbox center (compose placements)
            for name in names:
                try:
                    o = p_doc.getObject(name)
                    if not o:
                        continue
                    orig = orig_bboxes.get(name)
                    if not orig:
                        continue
                    center = self._bbox_center(orig)
                    # Build placement: translate to origin, rotate, translate back
                    rot = App.Rotation(axis_vec, angle)
                    P_move = App.Placement(App.Vector(-center.x, -center.y, -center.z), App.Rotation())
                    P_rot = App.Placement(App.Vector(0,0,0), rot)
                    P_back = App.Placement(center, App.Rotation())
                    # Compose carefully: new = P_back * P_rot * P_move * old
                    new_placement = P_back.multiply(P_rot.multiply(P_move.multiply(o.Placement)))
                    o.Placement = new_placement
                except Exception:
                    App.Console.PrintError("apply_bulk_rotate: error applying rotation for %s:\n%s\n" % (name, traceback.format_exc()))
                    continue

            # Recompute once after rotations (update shapes)
            try:
                p_doc.recompute()
            except Exception:
                App.Console.PrintError("apply_bulk_rotate: recompute failed after rotations:\n%s\n" % (traceback.format_exc(),))

            # Containment correction: ensure XY footprint fits within original XY bbox and align Z tops
            try:
                for name in names:
                    try:
                        o = p_doc.getObject(name)
                        if not o:
                            continue
                        orig = orig_bboxes.get(name)
                        if not orig:
                            continue
                        try:
                            newbb = o.Shape.BoundBox
                            new = self._bbox_dict(newbb)
                        except Exception:
                            # if shape missing, skip
                            continue

                        delta_x = 0.0
                        delta_y = 0.0
                        delta_z = 0.0

                        # if new bbox exceeds original on left
                        if new["XMin"] < orig["XMin"]:
                            delta_x = orig["XMin"] - new["XMin"]
                        # if new bbox exceeds original on right
                        if new["XMax"] > orig["XMax"]:
                            # shift left (negative)
                            d = orig["XMax"] - new["XMax"]
                            # if both left and right exceed, choose average move
                            if delta_x != 0.0:
                                delta_x = (delta_x + d) / 2.0
                            else:
                                delta_x = d

                        # Y adjustments
                        if new["YMin"] < orig["YMin"]:
                            delta_y = orig["YMin"] - new["YMin"]
                        if new["YMax"] > orig["YMax"]:
                            d = orig["YMax"] - new["YMax"]
                            if delta_y != 0.0:
                                delta_y = (delta_y + d) / 2.0
                            else:
                                delta_y = d

                        # Z alignment: bring new top to group target top (lift/lower)
                        if group_target_top is not None:
                            try:
                                delta_z = group_target_top - new["ZMax"]
                            except Exception:
                                delta_z = 0.0

                        # Apply translation if needed
                        if abs(delta_x) > 1e-9 or abs(delta_y) > 1e-9 or abs(delta_z) > 1e-9:
                            try:
                                # translate by (delta_x, delta_y, delta_z)
                                base = o.Placement.Base
                                new_base = App.Vector(base.x + delta_x, base.y + delta_y, base.z + delta_z)
                                # keep rotation unchanged
                                rot = o.Placement.Rotation
                                o.Placement = App.Placement(new_base, rot)
                            except Exception:
                                App.Console.PrintError("apply_bulk_rotate: containment translation failed for %s:\n%s\n" % (name, traceback.format_exc()))
                                continue

                    except Exception:
                        App.Console.PrintError("apply_bulk_rotate: per-object containment correction error for %s:\n%s\n" % (name, traceback.format_exc()))
                        continue

                # Recompute after corrections
                try:
                    p_doc.recompute()
                except Exception:
                    App.Console.PrintError("apply_bulk_rotate: recompute failed after containment corrections:\n%s\n" % (traceback.format_exc(),))

            except Exception:
                App.Console.PrintError("apply_bulk_rotate: containment correction phase failed:\n" + traceback.format_exc())

            App.Console.PrintMessage("apply_bulk_rotate: completed for %d object(s).\n" % len(names))

        except Exception:
            App.Console.PrintError("NestingRotator.apply_bulk_rotate failed:\n" + traceback.format_exc())