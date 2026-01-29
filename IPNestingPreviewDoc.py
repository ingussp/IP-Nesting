"""
IPNestingPreviewDoc - Preview document manager extracted from IPNestingGui.
Manages preview document operations (create, delete, select objects).
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore
import json
import traceback
from IPNestingRelayout import NestingRelayoutManager

# Import GrainPreparer (if available)
try:
    from IPNestingGrain import GrainPreparer
except Exception:
    GrainPreparer = None


class PreviewDocManager:
    """
    Manager for preview document operations.
    Operates on a panel instance (NestingTaskPanel).
    """
    
    def __init__(self, panel):
        """
        Initialize the preview document manager.
        
        Args:
            panel: NestingTaskPanel instance
        """
        self.panel = panel
    
    def align_to_largest_face(self, obj):
        """
        Compute rotation to align object to its largest face.
        
        Args:
            obj: FreeCAD object with Shape
            
        Returns:
            FreeCAD Rotation object
        """
        try:
            faces = sorted(obj.Shape.Faces, key=lambda f: f.Area, reverse=True)
            if not faces:
                return App.Rotation()
            best_face = faces[0]
            if len(faces) >= 2:
                if len(faces[1].Wires) > len(faces[0].Wires):
                    best_face = faces[1]
            u_min, u_max, v_min, v_max = best_face.ParameterRange
            u_mid = u_min + (u_max - u_min) / 2.0
            v_mid = v_min + (v_max - v_min) / 2.0
            normal = best_face.normalAt(u_mid, v_mid)
            return App.Rotation(normal, App.Vector(0, 0, 1))
        except Exception:
            App.Console.PrintError("align_to_largest_face failed:\n" + traceback.format_exc())
            return App.Rotation()
    
    def ensure_preview_doc(self, reset_counters_if_new=True):
        """
        Ensure preview document exists; create if needed.
        
        Args:
            reset_counters_if_new: If True, reset added_count when creating new doc
            
        Returns:
            Preview document object
        """
        if self.panel.preview_doc_name not in App.listDocuments():
            App.newDocument(self.panel.preview_doc_name)
            if reset_counters_if_new:
                self.panel.added_count = 0
            return App.getDocument(self.panel.preview_doc_name)
        else:
            return App.getDocument(self.panel.preview_doc_name)
    
    def delete_preview_objects(self, names):
        """
        Delete given object names from the preview document (Nesting_Preview).
        Robustly removes by Name when present. Recomputes and relayouts preview.
        
        Args:
            names: List of object names to delete
            
        Returns:
            List of removed names
        """
        removed = []
        try:
            if not names:
                return removed
            p_doc = App.getDocument(self.panel.preview_doc_name) if self.panel.preview_doc_name in App.listDocuments() else None
            if not p_doc:
                return removed

            # ensure we have names as strings
            candidate_names = [str(n) for n in names if n]
            # remove duplicates while preserving order
            seen = set()
            candidate_names = [x for x in candidate_names if not (x in seen or seen.add(x))]

            for name in candidate_names:
                try:
                    obj = p_doc.getObject(name)
                    if obj is not None:
                        # record name before deletion
                        try:
                            nm = obj.Name
                        except Exception:
                            nm = name
                        try:
                            # remove possible grain arrow for this preview object before removing object
                            try:
                                if GrainPreparer is not None:
                                    GrainPreparer.remove_grain_arrow(self.panel.preview_doc_name, nm)
                            except Exception:
                                pass
                            p_doc.removeObject(nm)
                            removed.append(nm)
                        except Exception:
                            App.Console.PrintError("Failed to remove object '%s' from preview:\n%s\n" % (nm, traceback.format_exc()))
                    else:
                        # object not found by exact name: try to find by label or partial match
                        found = None
                        for o in p_doc.Objects:
                            try:
                                if getattr(o, "Label", None) == name or getattr(o, "Name", None) == name:
                                    found = o
                                    break
                            except Exception:
                                continue
                        if not found:
                            # try substring match
                            for o in p_doc.Objects:
                                try:
                                    if name in (getattr(o, "Name", "") or "") or name in (getattr(o, "Label", "") or ""):
                                        found = o
                                        break
                                except Exception:
                                    continue
                        if found:
                            try:
                                nm = found.Name
                                # remove possible grain arrow attached to found object
                                try:
                                    if GrainPreparer is not None:
                                        GrainPreparer.remove_grain_arrow(self.panel.preview_doc_name, nm)
                                except Exception:
                                    pass
                                p_doc.removeObject(nm)
                                removed.append(nm)
                            except Exception:
                                App.Console.PrintError("Failed to remove matched object '%s':\n%s\n" % (name, traceback.format_exc()))
                        else:
                            App.Console.PrintMessage("delete_preview_objects: name '%s' not found in preview doc.\n" % name)
                except Exception:
                    App.Console.PrintError("delete_preview_objects per-name error for '%s':\n%s\n" % (name, traceback.format_exc()))
                    continue

            if removed:
                try:
                    p_doc.recompute()
                except Exception:
                    App.Console.PrintError("Recompute failed in delete_preview_objects:\n" + traceback.format_exc())
                try:
                    mgr = NestingRelayoutManager(preview_doc_name=self.panel.preview_doc_name, grid_cols=self.panel.grid_cols, padding=50.0)
                    mgr.run(copy_selection=True)
                    mgr.run(copy_selection=True)
                except Exception:
                    App.Console.PrintError("Relayout failed in delete_preview_objects:\n" + traceback.format_exc())
                try:
                    self.panel.added_count = max(0, self.panel.added_count - len(removed))
                except Exception:
                    pass

                # TRIGGER LAYOUT UPDATE AFTER DELETE
                self.panel.update_grain_layout_and_perimeters()

                # update Apply Grain blink state after deletions
                try:
                    self.panel._update_apply_blink_state()
                except Exception:
                    pass

        except Exception:
            App.Console.PrintError("delete_preview_objects failed:\n" + traceback.format_exc())
        return removed
    
    def select_preview_objects_for_row(self, row):
        """
        Select all preview objects associated with a table row in the Nesting_Preview document.

        This reads the stored list at UserRole+1 (JSON or python list). If absent, falls back to
        primary UserRole. Then it clears selection and selects each existing preview object so that
        all copies are highlighted in the 3D view / tree.
        
        Args:
            row: Table row index
        """
        try:
            # basic row checks
            if row is None or row < 0 or row >= self.panel.table.rowCount():
                return
            name_item = self.panel.table.item(row, 0)
            if not name_item:
                return

            p_doc = App.getDocument(self.panel.preview_doc_name) if self.panel.preview_doc_name in App.listDocuments() else None
            if not p_doc:
                return

            # collect object names stored at UserRole+1 (full list) or fallback to primary
            obj_names = []
            list_data = name_item.data(QtCore.Qt.UserRole + 1)
            if list_data:
                try:
                    if isinstance(list_data, list):
                        obj_names = list(list_data)
                    else:
                        obj_names = json.loads(list_data)
                        if not isinstance(obj_names, list):
                            obj_names = [obj_names]
                except Exception:
                    # fallback to primary if JSON parse fails
                    obj_names = []
            if not obj_names:
                primary = name_item.data(QtCore.Qt.UserRole)
                if primary:
                    obj_names = [primary]

            # filter to only objects that actually exist in doc (avoid stale names)
            existing = []
            for n in obj_names:
                try:
                    if n and p_doc.getObject(n):
                        existing.append(n)
                except Exception:
                    continue

            if not existing:
                return

            # perform selection: clear current selection in preview doc and add all preview object names
            try:
                self.panel._suppress_selection_update = True
                Gui.Selection.clearSelection(self.panel.preview_doc_name)
                for n in existing:
                    try:
                        Gui.Selection.addSelection(self.panel.preview_doc_name, n)
                    except Exception:
                        # ignore individual add failures
                        pass
                Gui.updateGui()
            finally:
                self.panel._suppress_selection_update = False
        except Exception:
            App.Console.PrintError("select_preview_objects_for_row failed:\n" + traceback.format_exc())
