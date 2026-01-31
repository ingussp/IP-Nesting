# NestingRelayout.py
# Class wrapper for the relayout macro so it can be imported and run from IPNestingGui.py
# Usage (from IPNestingGui or FreeCAD Console):
#   from IPNestingRelayout import NestingRelayoutManager
#   mgr = NestingRelayoutManager(preview_doc_name="Nesting_Preview", grid_cols=4, padding=50.0)
#   mgr.run(copy_selection=True)   # copy selected objects (if any) then relayout
#
# The module still supports running as a macro/script: `python NestingRelayout.py` inside FreeCAD's macro runner.

import FreeCAD as App
import FreeCADGui as Gui
import traceback
import json

class NestingRelayoutManager:
    """Manager to copy selected objects into a preview document and layout all preview objects.

    Methods:
      - run(copy_selection=True): copy selection (if any) then relayout preview contents
      - ensure_preview_doc(): create or return preview doc
    """
    def __init__(self, preview_doc_name="Nesting_Preview", grid_cols=4, padding=50.0, recompute=True):
        self.preview_doc_name = preview_doc_name
        self.grid_cols = int(grid_cols)
        self.padding = float(padding)
        self.recompute = bool(recompute)

    # -------------------------
    # Utility / helper methods
    # -------------------------
    def ensure_preview_doc(self):
        try:
            if self.preview_doc_name not in App.listDocuments():
                App.newDocument(self.preview_doc_name)
                return App.getDocument(self.preview_doc_name)
            return App.getDocument(self.preview_doc_name)
        except Exception:
            App.Console.PrintError("ensure_preview_doc failed:\n" + traceback.format_exc())
            return None

    def is_body_candidate(self, obj):
        try:
            if hasattr(obj, "Parent") and obj.Parent and getattr(obj.Parent, "isDerivedFrom", None):
                try:
                    if obj.Parent.isDerivedFrom("PartDesign::Body"):
                        return obj.Parent
                except Exception:
                    pass
            if hasattr(obj, "InList"):
                for p in getattr(obj, "InList", []):
                    try:
                        if getattr(p, "isDerivedFrom", None) and p.isDerivedFrom("PartDesign::Body"):
                            return p
                    except Exception:
                        pass
        except Exception:
            pass
        return obj

    def align_to_largest_face(self, obj):
        try:
            faces = sorted(obj.Shape.Faces, key=lambda f: f.Area, reverse=True)
            if not faces:
                return App.Rotation()
            best_face = faces[0]
            if len(faces) >= 2:
                try:
                    if len(faces[1].Wires) > len(faces[0].Wires):
                        best_face = faces[1]
                except Exception:
                    pass
            u_min, u_max, v_min, v_max = best_face.ParameterRange
            u_mid = u_min + (u_max - u_min) / 2.0
            v_mid = v_min + (v_max - v_min) / 2.0
            normal = best_face.normalAt(u_mid, v_mid)
            return App.Rotation(normal, App.Vector(0, 0, 1))
        except Exception:
            App.Console.PrintError("align_to_largest_face failed:\n" + traceback.format_exc())
            return App.Rotation()

    # -------------------------
    # Core behaviour
    # -------------------------
    def copy_selected_to_preview(self, p_doc):
        """Copy currently selected objects from the active document into preview doc.
           Skip copies with duplicate labels. Align each newly copied object and recompute.
           Returns number of copied objects.
        """
        try:
            sel = Gui.Selection.getSelection()
            if not sel:
                App.Console.PrintMessage("No selection found — skipping copy step.\n")
                return 0

            existing_labels = set(o.Label for o in p_doc.Objects)
            copied = 0
            for sel_obj in sel:
                try:
                    target = self.is_body_candidate(sel_obj)
                    if getattr(target, "Label", None) in existing_labels:
                        App.Console.PrintMessage("Skipping copy: label '%s' already in preview.\n" % (target.Label,))
                        continue
                    new_obj = p_doc.copyObject(target, False)
                    # keep original label (or adjust if you prefer unique labels)
                    try:
                        new_obj.Label = target.Label
                    except Exception:
                        pass

                    # immediately align and recompute per-object so bbox is valid
                    try:
                        alignment_rot = self.align_to_largest_face(new_obj)
                        new_obj.Placement = App.Placement(App.Vector(0, 0, 0), alignment_rot)
                    except Exception:
                        pass

                    if self.recompute:
                        try:
                            p_doc.recompute()
                        except Exception:
                            App.Console.PrintError("Recompute after copy failed for '%s':\n%s\n" % (getattr(new_obj, "Label", "<unknown>"), traceback.format_exc()))

                    copied += 1
                    existing_labels.add(getattr(new_obj, "Label", ""))
                except Exception:
                    App.Console.PrintError("Failed to copy '%s':\n%s\n" % (getattr(sel_obj, "Name", "<unknown>"), traceback.format_exc()))
                    continue

            if copied > 0 and self.recompute:
                try:
                    p_doc.recompute()
                except Exception:
                    App.Console.PrintError("Recompute failed after copying:\n" + traceback.format_exc())

            App.Console.PrintMessage("Copied %d objects to preview.\n" % copied)
            return copied
        except Exception:
            App.Console.PrintError("copy_selected_to_preview failed:\n" + traceback.format_exc())
            return 0

    def relayout_preview(self, p_doc):
        """Layout all preview objects that have shapes into a grid and place them on a single Z level."""
        try:
            if self.recompute:
                try:
                    p_doc.recompute()
                except Exception:
                    pass

            objs = [o for o in p_doc.Objects if hasattr(o, "Shape") and getattr(o, "Shape", None) is not None]
            if not objs:
                App.Console.PrintMessage("No shape objects in preview to layout.\n")
                return 0

            current_x = 0.0
            current_y = 0.0
            max_row_height = 0.0
            added_count = 0

            for o in objs:
                try:
                    # Align to largest face (fallback)
                    try:
                        rot = self.align_to_largest_face(o)
                        o.Placement = App.Placement(App.Vector(0, 0, 0), rot)
                    except Exception:
                        pass

                    if self.recompute:
                        try:
                            p_doc.recompute()
                        except Exception:
                            pass

                    # bounding box
                    try:
                        bb = o.Shape.BoundBox
                        # Skip objects with invalid bbox to avoid inf placements
                        try:
                            if not (bb.XMax > bb.XMin and bb.YMax > bb.YMin):
                                App.Console.PrintMessage("relayout_preview: skipping invalid bbox for %s\n" % getattr(o, "Name", "<unknown>"))
                                continue
                        except Exception:
                            continue
                        part_w = bb.XMax - bb.XMin
                        part_h = bb.YMax - bb.YMin
                    except Exception:
                        continue

                    if added_count > 0 and (added_count % self.grid_cols == 0):
                        current_x = 0.0
                        current_y += max_row_height + self.padding
                        max_row_height = 0.0

                    offset_x = -bb.XMin
                    offset_y = -bb.YMin
                    offset_z = -bb.ZMin

                    final_pos = App.Vector(current_x + offset_x, current_y + offset_y, offset_z)

                    try:
                        base_rot = o.Placement.Rotation
                        o.Placement = App.Placement(final_pos, base_rot)
                    except Exception:
                        try:
                            o.Placement.Base = final_pos
                        except Exception:
                            pass

                    current_x += part_w + self.padding
                    if part_h > max_row_height:
                        max_row_height = part_h

                    added_count += 1

                except Exception:
                    App.Console.PrintError("relayout per-object error for %s:\n%s\n" % (getattr(o, "Name", "<unknown>"), traceback.format_exc()))
                    continue

            if self.recompute:
                try:
                    p_doc.recompute()
                except Exception:
                    App.Console.PrintError("Recompute failed after relayout:\n" + traceback.format_exc())

            try:
                Gui.setActiveDocument(p_doc)
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass

            App.Console.PrintMessage("Relayout completed: %d objects positioned.\n" % added_count)
            return added_count

        except Exception:
            App.Console.PrintError("relayout_preview failed:\n" + traceback.format_exc())
            return 0

    # -------------------------
    # Public entrypoint
    # -------------------------
    def run(self, copy_selection=True):
        """Main entry: optionally copy selection to preview, then relayout preview doc."""
        try:
            p_doc = self.ensure_preview_doc()
            if p_doc is None:
                App.Console.PrintError("Failed to ensure preview document.\n")
                return 0

            if copy_selection:
                try:
                    self.copy_selected_to_preview(p_doc)
                except Exception:
                    App.Console.PrintError("Error during copy_selected_to_preview:\n" + traceback.format_exc())

            return self.relayout_preview(p_doc)
        except Exception:
            App.Console.PrintError("NestingRelayoutManager.run failed:\n" + traceback.format_exc())
            return 0


# Convenience function so macro-like usage remains simple
def run_relayout(copy_selection=True, preview_doc_name="Nesting_Preview", grid_cols=4, padding=50.0):
    mgr = NestingRelayoutManager(preview_doc_name=preview_doc_name, grid_cols=grid_cols, padding=padding)
    return mgr.run(copy_selection=copy_selection)


# Allow running as script/macro
if __name__ == "__main__":
    run_relayout(copy_selection=True)