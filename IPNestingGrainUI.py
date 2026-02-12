"""
IPNestingGrainUI - Grain UI controller extracted from IPNestingGui.
Manages grain checkbox/combobox state, blinking Apply Grain button, and grain arrows.
"""

import FreeCAD as App
from PySide import QtGui, QtCore
import json
import traceback
from functools import partial

# Import GrainPreparer (if available)
try:
    from IPNestingGrain import GrainPreparer
except Exception:
    GrainPreparer = None


class GrainUIController:
    """
    Controller for grain-related UI operations.
    Operates on a panel instance (NestingTaskPanel).
    """

    def __init__(self, panel):
        """
        Initialize the grain UI controller.

        Args:
            panel: NestingTaskPanel instance
        """
        self.panel = panel
        
        # Snapshot of checkbox states when "Apply Grain" was last pressed
        self._last_applied_grain_state = None

        # Setup blinking Apply Grain timer
        try:
            self._apply_blink_timer = QtCore.QTimer()
            self._apply_blink_timer.setInterval(500)
            self._apply_blink_timer.timeout.connect(self._on_apply_blink_tick)
            self._apply_blink_state = False
            try:
                self._apply_original_style = panel.bulk_grain_apply_btn.styleSheet()
            except Exception:
                self._apply_original_style = ""
            # ensure initial state correct
            self._update_apply_blink_state()
        except Exception:
            # ignore if timer setup fails in some environment
            self._apply_blink_timer = None
            self._apply_original_style = ""

    # --- Apply Grain blinking helpers ---
    def _on_apply_blink_tick(self):
        """Timer callback that toggles orange border on Apply Grain button."""
        try:
            if not hasattr(self, "_apply_blink_timer") or self._apply_blink_timer is None:
                return
            # toggle state
            try:
                if self._apply_blink_state:
                    # restore original
                    self.panel.bulk_grain_apply_btn.setStyleSheet(self._apply_original_style or "")
                    self._apply_blink_state = False
                else:
                    # set orange 1px border while preserving existing style if any
                    base = (self._apply_original_style + "; ") if self._apply_original_style else ""
                    self.panel.bulk_grain_apply_btn.setStyleSheet(base + "border:1px solid orange;")
                    self._apply_blink_state = True
            except Exception:
                pass
        except Exception:
            App.Console.PrintError("Apply blink tick error:\n" + traceback.format_exc())

    def _start_apply_blink(self):
        """Start blinking timer when grain checkbox is checked."""
        try:
            if hasattr(self, "_apply_blink_timer") and self._apply_blink_timer is not None:
                if not self._apply_blink_timer.isActive():
                    # ensure we start with the highlighted state immediately
                    try:
                        base = (self._apply_original_style + "; ") if self._apply_original_style else ""
                        self.panel.bulk_grain_apply_btn.setStyleSheet(base + "border:1px solid orange;")
                        self._apply_blink_state = True
                    except Exception:
                        pass
                    self._apply_blink_timer.start()
        except Exception:
            App.Console.PrintError("Failed to start apply blink:\n" + traceback.format_exc())

    def _stop_apply_blink(self):
        """Stop blinking timer and restore button style."""
        try:
            if hasattr(self, "_apply_blink_timer") and self._apply_blink_timer is not None:
                if self._apply_blink_timer.isActive():
                    self._apply_blink_timer.stop()
            try:
                self.panel.bulk_grain_apply_btn.setStyleSheet(self._apply_original_style or "")
                self._apply_blink_state = False
            except Exception:
                pass
        except Exception:
            App.Console.PrintError("Failed to stop apply blink:\n" + traceback.format_exc())

    def _get_current_grain_state(self):
        """
        Return a stable snapshot of grain checkbox states for all data rows.
        Use tuple of (row_index, is_checked) so we can compare later.
        """
        try:
            data_rows = self.panel.table.rowCount() - self.panel.control_rows
            state = []
            for r in range(data_rows):
                checked = False
                try:
                    grain_widget = self.panel.table.cellWidget(r, 4)
                    if grain_widget:
                        cb = grain_widget.findChild(QtGui.QCheckBox)
                        checked = bool(cb and cb.isChecked())
                except Exception:
                    checked = False
                state.append((r, checked))
            return tuple(state)
        except Exception:
            return tuple()

    def _update_apply_blink_state(self):
        """
        Blink Apply Grain button only when there are UNSAVED changes in grain checkboxes
        compared to the last-applied snapshot.
        """
        try:
            current = self._get_current_grain_state()

            # If we have never applied yet:
            # - blink if any checkbox is checked (user is making a selection)
            if self._last_applied_grain_state is None:
                any_checked = any(chk for (_, chk) in current)
                if any_checked:
                    self._start_apply_blink()
                else:
                    self._stop_apply_blink()
                return

            # After we have applied:
            # - blink only if state differs from the last applied snapshot
            is_dirty = (current != self._last_applied_grain_state)
            if is_dirty:
                self._start_apply_blink()
            else:
                self._stop_apply_blink()

        except Exception:
            App.Console.PrintError("Failed to update apply blink state:\n" + traceback.format_exc())

    # --- Grain arrow helpers (connect widgets + callbacks) ---
    def _on_grain_checkbox_state_changed(self, preview_obj_name, grain_cb, grain_combo, state):
        """Callback for per-row grain checkbox state change."""
        try:
            if GrainPreparer is None:
                return
            checked = (state == QtCore.Qt.Checked)
            # find row matching preview_obj_name and apply to all preview copies stored for that row
            row_count = self.panel.table.rowCount() - self.panel.control_rows
            for r in range(row_count):
                try:
                    itm = self.panel.table.item(r, 0)
                    if not itm:
                        continue
                    try:
                        primary = itm.data(QtCore.Qt.UserRole)
                    except Exception:
                        primary = None
                    # match by primary or by contained list
                    matches = False
                    if primary == preview_obj_name:
                        matches = True
                    else:
                        try:
                            list_data = itm.data(QtCore.Qt.UserRole + 1)
                            if list_data:
                                if isinstance(list_data, list):
                                    names_list = list_data
                                else:
                                    names_list = json.loads(list_data)
                                if preview_obj_name in names_list:
                                    matches = True
                        except Exception:
                            pass
                    if not matches:
                        continue

                    # collect all preview object names for this row
                    names = []
                    try:
                        ld = itm.data(QtCore.Qt.UserRole + 1)
                        if ld:
                            if isinstance(ld, list):
                                names = list(ld)
                            else:
                                names = json.loads(ld)
                    except Exception:
                        try:
                            p = itm.data(QtCore.Qt.UserRole)
                            if p:
                                names = [p]
                        except Exception:
                            names = []

                    axis = "X"
                    try:
                        axis = grain_combo.currentText() if hasattr(grain_combo, "currentText") else "X"
                    except Exception:
                        axis = "X"

                    # inside _on_grain_checkbox_state_changed, after you compute `names`:

                    p_doc = App.getDocument(self.panel.preview_doc_name) if self.panel.preview_doc_name in App.listDocuments() else None

                    if checked:
                        # Save once: use the first (pattern) object as reference
                        try:
                            if p_doc and names:
                                pat = p_doc.getObject(names[0])
                                self._ensure_saved_std_rotation(pat)
                        except Exception:
                            pass
                    else:
                        # Restore standard rotation for all copies (pattern applies to all)
                        try:
                            if p_doc and names:
                                # Prefer reading from pattern object; if it has saved rotation, apply to all
                                pat = p_doc.getObject(names[0])
                                if pat and hasattr(pat, "IPNestingStdRotAngleDeg"):
                                    # build rotation from pattern and apply to every copy
                                    ax = float(getattr(pat, "IPNestingStdRotAxisX", 0.0))
                                    ay = float(getattr(pat, "IPNestingStdRotAxisY", 0.0))
                                    az = float(getattr(pat, "IPNestingStdRotAxisZ", 1.0))
                                    ang = float(getattr(pat, "IPNestingStdRotAngleDeg", 0.0))
                                    restored_rot = App.Rotation(App.Vector(ax, ay, az), float(ang))

                                    for n in names:
                                        o = p_doc.getObject(n)
                                        if not o:
                                            continue
                                        base = o.Placement.Base
                                        o.Placement = App.Placement(base, restored_rot)
                                else:
                                    # fallback: try restore per-object (if you later decide to store per-object)
                                    for n in names:
                                        o = p_doc.getObject(n)
                                        if o:
                                            self._restore_saved_std_rotation(o)
                        except Exception:
                            pass

                        # After restoring orientation, you likely want immediate visual + perimeter correctness
                        try:
                            self.panel.update_grain_layout_and_perimeters()
                        except Exception:
                            pass
                    
                    for n in names:
                        try:
                            if checked:
                                GrainPreparer.update_grain_arrow(self.panel.preview_doc_name, n, enable=True, axis=axis)
                            else:
                                GrainPreparer.remove_grain_arrow(self.panel.preview_doc_name, n)
                        except Exception:
                            App.Console.PrintError("grain checkbox per-object update failed for '%s':\n" % (str(n),) + traceback.format_exc())
                            
                        # keep part object's GrainAngleDeg in sync with checkbox/axis (absolute vs +X)
                        try:
                            part_obj = App.getDocument(self.panel.preview_doc_name).getObject(n)
                            if part_obj:
                                if not hasattr(part_obj, "GrainAngleDeg"):
                                    try:
                                        part_obj.addProperty("App::PropertyInteger", "GrainAngleDeg", "IPNesting", "Absolute grain angle in degrees vs +X")
                                    except Exception:
                                        pass
                                part_obj.GrainAngleDeg = 0 if axis.upper() == "X" else 90
                        except Exception:
                            pass
                    break
                except Exception:
                    continue

            # NOTE: We do NOT trigger layout update here.
            # Layout updates happen only when "Apply Grain" is clicked.
            # self.panel.update_grain_layout_and_perimeters()

            # update blinking button state
            try:
                self._update_apply_blink_state()
            except Exception:
                pass
        except Exception:
            App.Console.PrintError("grain checkbox callback failed:\n" + traceback.format_exc())

    def _on_grain_axis_changed(self, preview_obj_name, grain_cb, grain_combo, index):
        """Callback for per-row grain axis combobox change (redraw only if checked)."""
        try:
            if GrainPreparer is None:
                return
            row_count = self.panel.table.rowCount() - self.panel.control_rows
            for r in range(row_count):
                try:
                    itm = self.panel.table.item(r, 0)
                    if not itm:
                        continue
                    try:
                        primary = itm.data(QtCore.Qt.UserRole)
                    except Exception:
                        primary = None
                    matches = False
                    if primary == preview_obj_name:
                        matches = True
                    else:
                        try:
                            list_data = itm.data(QtCore.Qt.UserRole + 1)
                            if list_data:
                                if isinstance(list_data, list):
                                    names_list = list_data
                                else:
                                    names_list = json.loads(list_data)
                                if preview_obj_name in names_list:
                                    matches = True
                        except Exception:
                            pass
                    if not matches:
                        continue

                    names = []
                    try:
                        ld = itm.data(QtCore.Qt.UserRole + 1)
                        if ld:
                            if isinstance(ld, list):
                                names = list(ld)
                            else:
                                names = json.loads(ld)
                    except Exception:
                        try:
                            p = itm.data(QtCore.Qt.UserRole)
                            if p:
                                names = [p]
                        except Exception:
                            names = []

                    try:
                        checked = grain_cb.isChecked()
                    except Exception:
                        checked = False

                    axis = grain_combo.currentText() if hasattr(grain_combo, "currentText") else "X"
                    for n in names:
                        try:
                            if checked:
                                GrainPreparer.update_grain_arrow(self.panel.preview_doc_name, n, enable=True, axis=axis)
                            else:
                                GrainPreparer.remove_grain_arrow(self.panel.preview_doc_name, n)
                        except Exception:
                            App.Console.PrintError("grain axis per-object update failed for '%s':\n" % (str(n),) + traceback.format_exc())
                        # keep part object's GrainAngleDeg in sync with checkbox/axis (absolute vs +X)
                        try:
                            part_obj = App.getDocument(self.panel.preview_doc_name).getObject(n)
                            if part_obj:
                                if not hasattr(part_obj, "GrainAngleDeg"):
                                    try:
                                        part_obj.addProperty("App::PropertyInteger", "GrainAngleDeg", "IPNesting", "Absolute grain angle in degrees vs +X")
                                    except Exception:
                                        pass
                                part_obj.GrainAngleDeg = 0 if axis.upper() == "X" else 90
                        except Exception:
                            pass
                    break
                except Exception:
                    continue
        except Exception:
            App.Console.PrintError("grain axis callback failed:\n" + traceback.format_exc())

    def _connect_grain_widgets(self, grain_cb, grain_combo, preview_obj_name):
        """Wire per-row grain checkbox and combobox to callbacks (safe using partial)."""
        try:
            # Avoid duplicate connections: attempt disconnects (may fail harmlessly)
            try:
                grain_cb.stateChanged.disconnect()
            except Exception:
                pass
            try:
                grain_combo.currentIndexChanged.disconnect()
            except Exception:
                pass

            grain_cb.stateChanged.connect(partial(self._on_grain_checkbox_state_changed,
                                                 preview_obj_name, grain_cb, grain_combo))
            grain_combo.currentIndexChanged.connect(partial(self._on_grain_axis_changed,
                                                           preview_obj_name, grain_cb, grain_combo))
        except Exception:
            App.Console.PrintError("Failed to connect grain widgets:\n" + traceback.format_exc())

    def _on_bulk_grain_changed(self, index):
        """When bottom bulk combobox is changed, set per-row combobox only for checked rows and update arrows."""
        try:
            axis = self.panel.bulk_grain_combo.currentText() if hasattr(self.panel, "bulk_grain_combo") else "X"
            data_rows = self.panel.table.rowCount() - self.panel.control_rows
            for r in range(data_rows):
                try:
                    grain_widget = self.panel.table.cellWidget(r, 4)
                    if not grain_widget:
                        continue
                    cb = grain_widget.findChild(QtGui.QCheckBox)
                    combo = grain_widget.findChild(QtGui.QComboBox)
                    # only change per-row combo for rows where checkbox is checked
                    if cb and cb.isChecked() and combo:
                        try:
                            combo.blockSignals(True)
                            idx = 0 if axis.upper() == "X" else 1
                            combo.setCurrentIndex(idx)
                        except Exception:
                            pass
                        finally:
                            try:
                                combo.blockSignals(False)
                            except Exception:
                                pass
                        # update arrows for all preview objects in that row
                        name_item = self.panel.table.item(r, 0)
                        if not name_item:
                            continue
                        primary = name_item.data(QtCore.Qt.UserRole)
                        names = []
                        try:
                            ld = name_item.data(QtCore.Qt.UserRole + 1)
                            if ld:
                                if isinstance(ld, list):
                                    names = list(ld)
                                else:
                                    names = json.loads(ld)
                        except Exception:
                            if primary:
                                names = [primary]
                        for n in names:
                            try:
                                if GrainPreparer is not None:
                                    GrainPreparer.update_grain_arrow(self.panel.preview_doc_name, n, enable=True, axis=axis)
                            except Exception:
                                App.Console.PrintError("bulk change: failed to update arrow for '%s':\n" % (str(n),) + traceback.format_exc())
                except Exception:
                    continue
        except Exception:
            App.Console.PrintError("bulk grain changed callback failed:\n" + traceback.format_exc())

    def _delta_to_align_grain_to_pos_x(self, angle_deg):
        """
        Compute delta degrees (range [-180..180]) so that after rotation
        the grain angle becomes 0 (arrow parallel to +X).
        """
        try:
            a = int(angle_deg) % 360
        except Exception:
            a = 0
        delta = (-a) % 360
        if delta > 180:
            delta -= 360
        return int(delta)
    
    def update_grain_layout_and_perimeters(self):
        """
        Splits parts into Standard and Grain groups.

        RULE (UPDATED):
          - Ensure expanded (margin-inflated) blue perimeter never overlaps expanded red perimeter.
          - gap is taken as a fraction of BLUE bbox height.

        (Legacy wording kept in UI docstring may mention "10% lower".)
        """
        if GrainPreparer is None:
            return

        try:
            p_doc = App.getDocument(self.panel.preview_doc_name) if self.panel.preview_doc_name in App.listDocuments() else None
            if not p_doc:
                return

            data_rows = self.panel.table.rowCount() - self.panel.control_rows

            standard_parts = []
            grain_parts = []
            grain_axis_by_name = {}

            for r in range(data_rows):
                name_item = self.panel.table.item(r, 0)
                if not name_item:
                    continue

                # Get names
                names = []
                try:
                    list_data = name_item.data(QtCore.Qt.UserRole + 1)
                    if list_data:
                        if isinstance(list_data, list):
                            names = list(list_data)
                        else:
                            names = json.loads(list_data)
                    else:
                        primary = name_item.data(QtCore.Qt.UserRole)
                        if primary:
                            names = [primary]
                except Exception:
                    pass

                if not names:
                    continue

                # Check grain checkbox
                is_grain = False
                try:
                    grain_widget = self.panel.table.cellWidget(r, 4)
                    if grain_widget:
                        cb = grain_widget.findChild(QtGui.QCheckBox)
                        if cb and cb.isChecked():
                            is_grain = True
                except Exception:
                    pass

                if is_grain:
                    grain_parts.extend(names)

                    # remember per-object axis so we can redraw arrows correctly after moving parts
                    axis = "X"
                    try:
                        grain_widget = self.panel.table.cellWidget(r, 4)
                        if grain_widget:
                            combo = grain_widget.findChild(QtGui.QComboBox)
                            if combo:
                                axis = combo.currentText() or "X"
                    except Exception:
                        axis = "X"

                    for nm in names:
                        grain_axis_by_name[nm] = axis
                else:
                    standard_parts.extend(names)

            def _bbox_for_names(names_list):
                found = False
                min_x = min_y = float("inf")
                max_x = max_y = float("-inf")
                for nm in names_list or []:
                    try:
                        obj = p_doc.getObject(nm)
                        if not obj or not hasattr(obj, "Shape") or obj.Shape is None:
                            continue
                        bb = obj.Shape.BoundBox
                        if bb.XMax <= bb.XMin and bb.YMax <= bb.YMin:
                            continue
                        min_x = min(min_x, bb.XMin)
                        min_y = min(min_y, bb.YMin)
                        max_x = max(max_x, bb.XMax)
                        max_y = max(max_y, bb.YMax)
                        found = True
                    except Exception:
                        continue
                return found, min_x, min_y, max_x, max_y

            def _shift_names_y(names_list, dy):
                if not names_list:
                    return
                for nm in names_list:
                    try:
                        obj = p_doc.getObject(nm)
                        if not obj:
                            continue
                        base = obj.Placement.Base
                        obj.Placement.Base = App.Vector(base.x, base.y + float(dy), base.z)
                    except Exception:
                        continue

            # --- NEW: Re-pack STANDARD (red) group too, so red parts compact after moving items back ---
            # Keep the red group's current TOP as an anchor so it doesn't jump around.
            red_anchor_y = None
            try:
                red_found0, _, _, _, red_max_y0 = _bbox_for_names(standard_parts)
                if red_found0:
                    red_anchor_y = float(red_max_y0)
            except Exception:
                red_anchor_y = None

            if standard_parts:
                try:
                    target_y_red = red_anchor_y if red_anchor_y is not None else 0.0
                    GrainPreparer.pack_grain_parts(
                        self.panel.preview_doc_name,
                        standard_parts,
                        target_x=0.0,
                        target_y=target_y_red
                    )
                except TypeError:
                    # Backward compat if pack_grain_parts signature differs
                    try:
                        GrainPreparer.pack_grain_parts(self.panel.preview_doc_name, standard_parts)
                    except Exception:
                        pass
                except Exception:
                    pass
            
            # NEW STEP: rotate all grain parts so their grain direction becomes parallel to +X
            try:
                for nm in grain_parts:
                    try:
                        obj = p_doc.getObject(nm)
                        if not obj:
                            continue

                        # Read absolute grain angle (degrees vs +X)
                        angle = 0
                        if hasattr(obj, "GrainAngleDeg"):
                            try:
                                angle = int(obj.GrainAngleDeg) % 360
                            except Exception:
                                angle = 0
                        else:
                            # If no custom angle exists yet, derive from UI axis:
                            # X -> 0°, Y -> 90°
                            axis = grain_axis_by_name.get(nm, "X")
                            angle = 0 if str(axis).upper() == "X" else 90

                            # Persist it so next Apply Grain is stable
                            try:
                                obj.addProperty(
                                    "App::PropertyInteger",
                                    "GrainAngleDeg",
                                    "IPNesting",
                                    "Absolute grain angle in degrees vs +X"
                                )
                            except Exception:
                                pass
                            try:
                                obj.GrainAngleDeg = int(angle) % 360
                            except Exception:
                                pass

                        delta = self._delta_to_align_grain_to_pos_x(angle)
                        if delta != 0:
                            self.panel._rotate_preview_parts_about_z(p_doc, [nm], delta)

                        # After normalization, grain is aligned to +X
                        try:
                            obj.GrainAngleDeg = 0
                        except Exception:
                            pass

                    except Exception:
                        continue

                try:
                    p_doc.recompute()
                except Exception:
                    pass
            except Exception:
                pass
            
            # 1) Pack grain parts to temporary location y=0 (stable bbox)
            if grain_parts:
                try:
                    GrainPreparer.pack_grain_parts(self.panel.preview_doc_name, grain_parts, target_x=0.0, target_y=0.0)
                except TypeError:
                    GrainPreparer.pack_grain_parts(self.panel.preview_doc_name, grain_parts)

            # 2) Compute red bottom (standard min_y)
            red_found, _, red_min_y, _, _ = _bbox_for_names(standard_parts)

            # 3) Compute blue bbox after packing
            blue_found, _, blue_min_y, _, blue_max_y = _bbox_for_names(grain_parts)

            # 4) Apply non-overlapping rule using actual perimeter margins
            if grain_parts and red_found and blue_found:
                blue_h = max(1e-6, float(blue_max_y - blue_min_y))

                # Keep original factor (0.10) unless you intentionally want larger spacing.
                gap = max(300.0, 0.30 * blue_h)

                red_info = GrainPreparer.get_subset_bbox_and_margin(self.panel.preview_doc_name, subset_names=standard_parts)
                blue_info = GrainPreparer.get_subset_bbox_and_margin(self.panel.preview_doc_name, subset_names=grain_parts)

                red_margin = float(red_info[6]) if red_info and red_info[0] else 0.0
                blue_margin = float(blue_info[6]) if blue_info and blue_info[0] else 0.0

                # Optional: make bbox values consistent with perimeter bbox collector
                try:
                    if red_info and red_info[0]:
                        red_min_y = float(red_info[2])
                except Exception:
                    pass
                try:
                    if blue_info and blue_info[0]:
                        blue_min_y = float(blue_info[2])
                        blue_max_y = float(blue_info[4])
                        blue_h = max(1e-6, float(blue_max_y - blue_min_y))
                except Exception:
                    pass

                # Want: (blue_max_y + blue_margin) <= (red_min_y - red_margin) - gap
                desired_blue_max_y = float(red_min_y) - float(red_margin) - float(gap) - float(blue_margin)
                dy = desired_blue_max_y - float(blue_max_y)

                _shift_names_y(grain_parts, dy)
                
            # --- NEW: auto-rotate grain parts to X if GrainAngleDeg not 0/180 ---
            try:
                # Only rotate checked grain parts; apply once after moving
                for nm in grain_parts:
                    try:
                        obj = p_doc.getObject(nm)
                        if not obj:
                            continue

                        # If no GrainAngleDeg -> treat as 0 (already parallel to X)
                        angle = 0
                        if hasattr(obj, "GrainAngleDeg"):
                            try:
                                angle = int(obj.GrainAngleDeg) % 360
                            except Exception:
                                angle = 0

                        # If already parallel to X (0 or 180) -> skip
                        if angle in (0, 180):
                            continue

                        # Rotate to nearest of 0 or 180
                        target = 0 if angle < 90 or angle > 270 else 180
                        delta = (target - angle) % 360
                        if delta > 180:
                            delta -= 360  # keep minimal rotation (-180..180)

                        # Use existing helper from IPNestingGui
                        self.panel._rotate_preview_parts_about_z(p_doc, [nm], delta)

                        # Persist corrected angle
                        try:
                            obj.GrainAngleDeg = int(target) % 360
                        except Exception:
                            pass

                    except Exception:
                        continue
            except Exception:
                pass    

            try:
                p_doc.recompute()
            except Exception:
                pass

            # 5) Draw Standard Perimeter (red)
            GrainPreparer.draw_perimeter_and_label(
                self.panel.preview_doc_name,
                subset_names=standard_parts,
                custom_label="Parts without grain direction"
            )

            # 6) Draw Grain Perimeter (blue handled in IPNestingGrain.py)
            if grain_parts:
                GrainPreparer.draw_perimeter_and_label(
                    self.panel.preview_doc_name,
                    subset_names=grain_parts,
                    custom_label="Parts with grain direction"
                )
            else:
                GrainPreparer.draw_perimeter_and_label(
                    self.panel.preview_doc_name,
                    subset_names=[],
                    custom_label="Parts with grain direction"
                )
                
            # 7) Redraw grain arrows AFTER parts have been moved (otherwise arrows stay at old coords)
            try:
                if grain_parts:
                    for nm in grain_parts:
                        try:
                            axis = "X"
                            GrainPreparer.update_grain_arrow(self.panel.preview_doc_name, nm, enable=True, axis=axis)
                        except Exception:
                            pass
                else:
                    # optional: if no grain parts, remove all arrows (keeps scene clean)
                    try:
                        GrainPreparer.remove_all_grain_arrows(self.panel.preview_doc_name)
                    except Exception:
                        pass
            except Exception:
                pass
            
            # After successful apply/layout update, record snapshot and stop blinking
            try:
                self._last_applied_grain_state = self._get_current_grain_state()
                self._stop_apply_blink()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError("update_grain_layout_and_perimeters failed:\n" + traceback.format_exc())
            
    # inside class GrainUIController:

    def _ensure_saved_std_rotation(self, obj):
        """Save object's current rotation as 'standard' rotation (only once)."""
        try:
            if obj is None:
                return

            # If already saved, do not overwrite (first-time only)
            if hasattr(obj, "IPNestingStdRotAngleDeg"):
                return

            rot = obj.Placement.Rotation
            axis = rot.Axis
            # FreeCAD's Rotation.Angle is radians in many versions; convert to degrees safely
            try:
                angle_deg = float(rot.Angle) * 180.0 / 3.141592653589793
            except Exception:
                angle_deg = 0.0

            try:
                obj.addProperty("App::PropertyFloat", "IPNestingStdRotAxisX", "IPNesting", "Saved standard rotation axis X")
                obj.addProperty("App::PropertyFloat", "IPNestingStdRotAxisY", "IPNesting", "Saved standard rotation axis Y")
                obj.addProperty("App::PropertyFloat", "IPNestingStdRotAxisZ", "IPNesting", "Saved standard rotation axis Z")
                obj.addProperty("App::PropertyFloat", "IPNestingStdRotAngleDeg", "IPNesting", "Saved standard rotation angle (deg)")
            except Exception:
                # properties might already exist or addProperty can fail in some contexts
                pass

            try:
                obj.IPNestingStdRotAxisX = float(axis.x)
                obj.IPNestingStdRotAxisY = float(axis.y)
                obj.IPNestingStdRotAxisZ = float(axis.z)
                obj.IPNestingStdRotAngleDeg = float(angle_deg)
            except Exception:
                pass

        except Exception:
            pass


    def _restore_saved_std_rotation(self, obj):
        """Restore object's saved 'standard' rotation if available; keeps Base position intact."""
        try:
            if obj is None:
                return False
            if not hasattr(obj, "IPNestingStdRotAngleDeg"):
                return False

            try:
                ax = float(getattr(obj, "IPNestingStdRotAxisX", 0.0))
                ay = float(getattr(obj, "IPNestingStdRotAxisY", 0.0))
                az = float(getattr(obj, "IPNestingStdRotAxisZ", 1.0))
                ang = float(getattr(obj, "IPNestingStdRotAngleDeg", 0.0))
            except Exception:
                return False

            # Rebuild rotation and apply while preserving translation
            base = obj.Placement.Base
            rot = App.Rotation(App.Vector(ax, ay, az), float(ang))
            obj.Placement = App.Placement(base, rot)
            return True

        except Exception:
            return False