# IPNestingGrain.py
"""
Grain preparer utilities for IP-Nesting — improved font/margin sizing.
Includes logic for packing grain-specific parts at a designated location .
"""
import FreeCAD as App
import FreeCADGui as Gui
import traceback
import math
import time

try:
    import Part
except Exception:
    Part = None

try:
    import Draft
except Exception:
    Draft = None


class GrainPreparer:
    @staticmethod
    def _get_global_bbox_diag(p_doc):
        """Calculates the diagonal of the bounding box covering ALL valid parts in the doc.
           Used to maintain consistent font sizing regardless of subset size."""
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        found = False
        count = 0
        for o in p_doc.Objects:
            if "GrainPerimeter" in getattr(o, "Name", "") or "GrainArrow" in getattr(o, "Name", ""):
                continue
            if hasattr(o, "Shape") and o.Shape is not None:
                bb = o.Shape.BoundBox
                if bb.XMax <= bb.XMin:
                    continue
                min_x = min(min_x, bb.XMin)
                min_y = min(min_y, bb.YMin)
                max_x = max(max_x, bb.XMax)
                max_y = max(max_y, bb.YMax)
                found = True
                count += 1

        if not found:
            return 1000.0, 1  # Default fallback

        w = max_x - min_x
        h = max_y - min_y
        return math.hypot(w, h), count

    @staticmethod
    def _safe_get_scale_factors():
        """
        "Scale method" for text size and margins.

        We try to read optional values from FreeCAD parameters, and fall back to 1.0.
        This prevents NameError for label_scale/margin_scale and makes scaling consistent.
        """
        label_scale = 1.0
        margin_scale = 1.0

        try:
            # Common place for macros/addons to store parameters
            p = App.ParamGet("User parameter:BaseApp/Preferences/Mod/IPNesting")
            try:
                label_scale = float(p.GetFloat("LabelScale", 1.0))
            except Exception:
                pass
            try:
                margin_scale = float(p.GetFloat("MarginScale", 1.0))
            except Exception:
                pass
        except Exception:
            pass

        # Safety clamp
        try:
            label_scale = max(0.1, min(label_scale, 10.0))
        except Exception:
            label_scale = 1.0
        try:
            margin_scale = max(0.1, min(margin_scale, 10.0))
        except Exception:
            margin_scale = 1.0

        return label_scale, margin_scale

    @staticmethod
    def _safe_text_contains_label(text_val, custom_label):
        """Helper: returns True if object's Text property contains the label text."""
        try:
            if text_val is None:
                return False
            # In FreeCAD this is often list like ["..."]
            if isinstance(text_val, (list, tuple)):
                joined = "\n".join([str(x) for x in text_val])
                return str(custom_label) in joined
            return str(custom_label) in str(text_val)
        except Exception:
            return False

    @staticmethod
    def _safe_is_label_object(obj, custom_label):
        """
        Determine whether obj is the perimeter label text for the given custom_label,
        without relying on obj.Name (because in FreeCAD 1.0.2 it may remain 'Text').
        """
        try:
            lbl = getattr(obj, "Label", "") or ""
            if str(custom_label) in lbl and "Label" in lbl:
                return True
            # The code tries to set Label to custom_label + " Label"
            if lbl == (str(custom_label) + " Label"):
                return True

            # Text content match
            if hasattr(obj, "Text"):
                if GrainPreparer._safe_text_contains_label(getattr(obj, "Text", None), custom_label):
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _safe_is_border_object(obj, custom_label):
        """
        Determine whether obj is the perimeter border for the given custom_label.
        """
        try:
            lbl = getattr(obj, "Label", "") or ""
            if lbl == (str(custom_label) + " Border"):
                return True
            if str(custom_label) in lbl and "Border" in lbl:
                return True
        except Exception:
            pass
        return False

    # -----------------------------
    # Shared helpers (refactor)
    # -----------------------------
    @staticmethod
    def _collect_subset_bbox(p_doc, subset_names=None):
        """
        Collect bounding extents for the subset using the same rules as perimeter drawing.
        Returns:
          (found, min_x, min_y, max_x, max_y, subset_part_count)
        """
        found = False
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        subset_part_count = 0

        objects_to_process = []
        if subset_names is not None:
            for name in subset_names:
                o = p_doc.getObject(name)
                if o:
                    objects_to_process.append(o)
        else:
            objects_to_process = p_doc.Objects

        for o in objects_to_process:
            try:
                n = getattr(o, "Name", "")
                if "GrainPerimeter" in n or "GrainArrow" in n:
                    continue

                shp = getattr(o, "Shape", None)
                if shp is None:
                    continue
                bb = shp.BoundBox
                if bb.XMax <= bb.XMin and bb.YMax <= bb.YMin:
                    continue

                min_x = min(min_x, bb.XMin)
                min_y = min(min_y, bb.YMin)
                max_x = max(max_x, bb.XMax)
                max_y = max(max_y, bb.YMax)
                found = True
                subset_part_count += 1
            except Exception:
                continue

        return found, min_x, min_y, max_x, max_y, subset_part_count

    @staticmethod
    def _compute_font_and_margin(preview_doc_name, p_doc, min_x, min_y, max_x, max_y, subset_part_count):
        """
        Compute (world_font_size, final_margin, scale_multiplier) using the same logic
        perimeter drawing needs. Centralizing this allows UI to use identical margin
        (so expanded perimeters never overlap).
        """
        # Use GLOBAL document metrics so font stays consistent between subsets.
        global_diag, global_count = GrainPreparer._get_global_bbox_diag(p_doc)

        # View / screen metrics
        view = None
        try:
            view = Gui.getDocument(preview_doc_name).ActiveView
        except Exception:
            pass

        screen_diag = None
        try:
            if view:
                size = view.getSize()
                if isinstance(size, (tuple, list)) and len(size) >= 2:
                    screen_diag = math.hypot(size[0], size[1])
        except Exception:
            screen_diag = None

        pixels_per_unit = None
        if screen_diag and global_diag > 1e-9:
            pixels_per_unit = screen_diag / global_diag

        # Calculate based on GLOBAL count so font stays consistent
        desired_text_px = int(max(10, min(120, 40 + 4 * math.log(max(1, global_count)))))
        desired_margin_px = int(max(8, min(200, 25 + 3 * math.sqrt(max(1, global_count)))))

        world_font_size = None
        final_margin = None

        try:
            if pixels_per_unit and pixels_per_unit > 0:
                world_per_pixel = 1.0 / pixels_per_unit
                world_font_size = desired_text_px * world_per_pixel
                final_margin = desired_margin_px * world_per_pixel

                # Clamp based on global diag
                max_font = max(1.0, global_diag * 0.05)
                world_font_size = max(1.0, min(world_font_size, max_font))

                # Margin
                final_margin = max(1.0, min(final_margin, global_diag * 0.05))
            else:
                # Fallback if no view
                world_font_size = max(1.0, min(global_diag * 0.03, 12.0))
                final_margin = max(1.0, min(global_diag * 0.05, 10.0))
        except Exception:
            world_font_size = 12.0
            final_margin = 10.0

        # (1) Apply "scale method" for both labels (with/without grain)
        label_scale, margin_scale = GrainPreparer._safe_get_scale_factors()
        try:
            world_font_size = max(1.0, float(world_font_size) * float(label_scale))
        except Exception:
            pass
        try:
            final_margin = max(1.0, float(final_margin) * float(margin_scale))
        except Exception:
            pass

        # --- NEW: scale font by perimeter square side (sqrt(area)) ---
        # Variant 2: scale by "side length" = sqrt(area)
        # We normalize against global_diag to keep it stable across docs.
        side_scale = 1.0
        try:
            subset_w = max(0.0, float(max_x - min_x))
            subset_h = max(0.0, float(max_y - min_y))
            square_side = math.sqrt(max(1e-9, subset_w * subset_h))  # sqrt(area)
            if global_diag and global_diag > 1e-9:
                side_scale = square_side / float(global_diag)
            else:
                side_scale = 1.0
            # clamp: prevent extreme values
            side_scale = max(0.6, min(side_scale, 3.0))
        except Exception:
            side_scale = 1.0

        try:
            world_font_size = max(1.0, float(world_font_size) * float(side_scale))
        except Exception:
            pass

        # --- NEW: FreeCAD 1.0.2 specific: ScaleMultiplier support ---
        scale_multiplier = None
        try:
            # side_scale in [0.6..3.0] -> multiplier in about [2.0..12.0]
            scale_multiplier = 1.0 - 2 * float(side_scale)
            scale_multiplier = max(1.0, min(scale_multiplier, 10.0))
        except Exception:
            scale_multiplier = None

        return float(world_font_size), float(final_margin), scale_multiplier

    @staticmethod
    def get_subset_bbox_and_margin(preview_doc_name, subset_names=None):
        """
        Public helper for both UI placement and perimeter drawing.

        Returns:
          (found, min_x, min_y, max_x, max_y, subset_part_count, final_margin)
        """
        try:
            if preview_doc_name not in App.listDocuments():
                return (False, 0.0, 0.0, 0.0, 0.0, 0, 0.0)
            p_doc = App.getDocument(preview_doc_name)
            if p_doc is None:
                return (False, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

            found, min_x, min_y, max_x, max_y, subset_part_count = GrainPreparer._collect_subset_bbox(
                p_doc, subset_names=subset_names
            )
            if not found or subset_part_count == 0:
                return (False, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

            _, final_margin, _ = GrainPreparer._compute_font_and_margin(
                preview_doc_name, p_doc, min_x, min_y, max_x, max_y, subset_part_count
            )
            return (True, float(min_x), float(min_y), float(max_x), float(max_y), int(subset_part_count), float(final_margin))
        except Exception:
            return (False, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

    @staticmethod
    def draw_perimeter_and_label(
        preview_doc_name,
        base_label_offset=20.0,
        subset_names=None,
        custom_label="Parts without grain direction",
        line_color=None,
    ):
        """
        Draws a perimeter around objects + label.
        If subset_names is provided (list of strings), only considers those objects.
        Otherwise considers all objects in document.

        line_color:
          - None -> default red for "without grain" and blue for "with grain"
          - tuple (r,g,b) in [0..1]
        """
        try:
            if preview_doc_name not in App.listDocuments():
                return
            p_doc = App.getDocument(preview_doc_name)
            if p_doc is None:
                return

            # Suffix determines unique names for this specific group (Main vs Grain)
            suffix = "Main"
            if "with grain" in custom_label:
                suffix = "Grain"

            feat_name_poly = "GrainPerimeter_" + suffix
            feat_name_label = "GrainPerimeterLabel_" + suffix

            # default colors
            if line_color is None:
                if suffix == "Grain":
                    # (4) Grain perimeter should be blue
                    line_color = (0.0, 0.0, 1.0)
                else:
                    line_color = (1.0, 0.0, 0.0)

            # --- ROBUST CLEANUP ---
            # 1) Remove our previous border/label objects by name pattern where possible
            # 2) Also remove by Label/Text match (important for FreeCAD 1.0.2 where Name may remain 'Text')
            for obj in list(p_doc.Objects):
                try:
                    n = getattr(obj, "Name", "") or ""

                    # Delete exact matches (if we managed to name them)
                    if n == feat_name_poly or n == feat_name_label:
                        p_doc.removeObject(n)
                        continue

                    # Delete logic for duplicates (e.g. GrainPerimeterLabel_Main001)
                    if n.startswith("GrainPerimeterLabel_" + suffix) or n.startswith("GrainPerimeter_" + suffix):
                        p_doc.removeObject(n)
                        continue

                    # Delete by label/text match as fallback
                    if GrainPreparer._safe_is_border_object(obj, custom_label) or GrainPreparer._safe_is_label_object(obj, custom_label):
                        p_doc.removeObject(obj.Name)
                        continue
                except Exception:
                    continue

            # Legacy cleanup (for "Main")
            if suffix == "Main":
                for obj in list(p_doc.Objects):
                    try:
                        n = getattr(obj, "Name", "")
                        if n == "GrainPerimeter" or n == "GrainPerimeterLabel":
                            p_doc.removeObject(n)
                    except Exception:
                        continue

            # --- Collect bbox extents for the SUBSET (shared helper) ---
            found, min_x, min_y, max_x, max_y, subset_part_count = GrainPreparer._collect_subset_bbox(
                p_doc, subset_names=subset_names
            )
            if not found or subset_part_count == 0:
                return

            # --- FONT + MARGIN (shared helper) ---
            world_font_size, final_margin, scale_multiplier = GrainPreparer._compute_font_and_margin(
                preview_doc_name, p_doc, min_x, min_y, max_x, max_y, subset_part_count
            )

            # Draw Box (apply margin expansion after sizing computations)
            min_x -= final_margin
            min_y -= final_margin
            max_x += final_margin
            max_y += final_margin

            p1 = App.Vector(min_x, min_y, 0)
            p2 = App.Vector(max_x, min_y, 0)
            p3 = App.Vector(max_x, max_y, 0)
            p4 = App.Vector(min_x, max_y, 0)
            pts = [p1, p2, p3, p4, p1]

            created_perimeter = False
            try:
                if Part is not None:
                    try:
                        wire = Part.makePolygon(pts)
                        feat = p_doc.addObject("Part::Feature", feat_name_poly)
                        feat.Label = custom_label + " Border"
                        feat.Shape = wire
                        try:
                            vo = feat.ViewObject
                            vo.LineWidth = 2
                            vo.LineColor = line_color
                            vo.DisplayMode = "Wireframe"
                        except Exception:
                            pass
                        created_perimeter = True
                    except Exception:
                        pass
            except Exception:
                pass

            if not created_perimeter and Draft is not None:
                try:
                    w = Draft.make_wire([p1, p2, p3, p4], closed=True)
                    w.Name = feat_name_poly
                    w.Label = custom_label + " Border"
                    try:
                        w.ViewObject.LineColor = line_color
                        w.ViewObject.LineWidth = 2
                    except Exception:
                        pass
                    created_perimeter = True
                except Exception:
                    pass

            # Create Label
            label_pos = App.Vector(min_x, max_y + float(base_label_offset) + final_margin, 0)
            text_obj = None

            if Draft is not None:
                try:
                    try:
                        # Try point argument
                        try:
                            text_obj = Draft.make_text([custom_label], point=label_pos)
                        except TypeError:
                            text_obj = Draft.make_text([custom_label], label_pos)
                    except Exception:
                        try:
                            text_obj = Draft.makeText([custom_label], point=label_pos)
                        except Exception:
                            pass

                    if text_obj is not None:
                        # Note: In FreeCAD 1.0.2 this may remain 'Text' (Name may not be settable).
                        try:
                            text_obj.Name = feat_name_label
                        except Exception:
                            pass

                        # This is more reliable than Name for later cleanup
                        try:
                            text_obj.Label = custom_label + " Label"
                        except Exception:
                            pass

                        try:
                            p_doc.recompute()
                            Gui.updateGui()
                        except Exception:
                            pass

                        # Apply font size + ScaleMultiplier
                        success = False
                        try:
                            vo = text_obj.ViewObject

                            # Primary: FontSize in mm (confirmed by your macro)
                            if hasattr(vo, "FontSize"):
                                vo.FontSize = float(world_font_size)
                                success = True
                            elif hasattr(vo, "TextSize"):
                                vo.TextSize = float(world_font_size)
                                success = True
                            elif hasattr(vo, "Size"):
                                vo.Size = float(world_font_size)
                                success = True

                            # Extra scaling for FreeCAD 1.0.2 if available
                            if scale_multiplier is not None and hasattr(vo, "ScaleMultiplier"):
                                try:
                                    vo.ScaleMultiplier = float(scale_multiplier)
                                except Exception:
                                    pass

                        except Exception:
                            pass

                        if not success:
                            try:
                                if hasattr(text_obj, "Size"):
                                    text_obj.Size = float(world_font_size)
                                elif hasattr(text_obj, "Height"):
                                    text_obj.Height = float(world_font_size)
                            except Exception:
                                pass
                except Exception:
                    pass

            try:
                p_doc.recompute()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError("GrainPreparer.draw_perimeter_and_label failed:\n" + traceback.format_exc())

    @staticmethod
    def pack_grain_parts(preview_doc_name, part_names, target_x=0.0, target_y=-5000.0, extra_pct=30.0, padding=4.0):
        """
        Packs the specified parts into a square arrangement and moves them to
        Start X=target_x, Y=target_y (world units).
        """
        try:
            if preview_doc_name not in App.listDocuments():
                return
            p_doc = App.getDocument(preview_doc_name)
            if not p_doc or not part_names:
                return

            items = []
            total_area = 0.0

            # Collect items
            for name in part_names:
                obj = p_doc.getObject(name)
                if not obj:
                    continue

                # Check shape
                if not hasattr(obj, "Shape") or obj.Shape is None:
                    continue

                # Approx area from bbox for sorting/sizing
                bb = obj.Shape.BoundBox
                w = max(0.0, bb.XMax - bb.XMin)
                h = max(0.0, bb.YMax - bb.YMin)
                area = w * h

                items.append({
                    "obj": obj,
                    "bb": bb,
                    "w": w,
                    "h": h,
                    "base": obj.Placement.Base,
                    "rot": obj.Placement.Rotation,
                    "area": area
                })
                total_area += area

            if not items:
                return

            # Initial square side calculation
            adjusted_area = total_area * (1.0 + float(extra_pct) / 100.0)
            side = math.sqrt(max(1e-6, adjusted_area))

            # Packing logic (Shelf algorithm)
            max_expand_iters = 15
            expand_factor = 1.1
            iters = 0

            planned = []

            while iters < max_expand_iters:
                planned = []
                cur_x = 0.0
                cur_y = 0.0
                row_h = 0.0
                pad = float(padding)
                overflow = False

                # Sort items by height desc (shelf packing heuristic)
                items_sorted = sorted(items, key=lambda x: x["h"], reverse=True)

                for it in items_sorted:
                    w = it["w"]
                    h = it["h"]

                    # If wider than side, wrap
                    if cur_x + w > side:
                        cur_x = 0.0
                        cur_y += row_h + pad
                        row_h = 0.0

                    # Update row height
                    if h > row_h:
                        row_h = h

                    # Store position
                    it_copy = dict(it)
                    it_copy["new_rel_x"] = cur_x
                    it_copy["new_rel_y"] = cur_y
                    planned.append(it_copy)

                    cur_x += w + pad

                    if cur_y > side * 1.5:
                        overflow = True

                if not overflow:
                    break

                side *= expand_factor
                iters += 1

            # Apply positions relative to target
            tx = float(target_x)
            ty = float(target_y)

            for p in planned:
                obj = p["obj"]
                orig_bb = p["bb"]

                # Relative pos in pack
                rel_x = p["new_rel_x"]
                rel_y = p["new_rel_y"]

                # Grid point (top-left of slot)
                Gx = tx + rel_x
                Gy = ty - rel_y

                # Align object's Top-Left (bb.XMin, bb.YMax) to Grid Point
                dx = Gx - orig_bb.XMin
                dy = Gy - orig_bb.YMax

                base = p["base"]
                new_base = App.Vector(base.x + dx, base.y + dy, base.z)  # Keep Z

                try:
                    obj.Placement.Base = new_base
                except Exception:
                    pass

            try:
                p_doc.recompute()
            except Exception:
                pass

        except Exception:
            App.Console.PrintError("pack_grain_parts failed:\n" + traceback.format_exc())

    @staticmethod
    def _find_preview_object(p_doc, obj_name_or_label):
        try:
            if not p_doc:
                return None
            obj = p_doc.getObject(obj_name_or_label)
            if obj:
                return obj
            for o in p_doc.Objects:
                try:
                    if getattr(o, "Label", None) == obj_name_or_label or getattr(o, "Name", None) == obj_name_or_label:
                        return o
                except Exception:
                    continue
        except Exception:
            pass
        return None

    @staticmethod
    def _arrow_object_name_for(obj_name):
        safe = str(obj_name)
        return "GrainArrow_" + safe

    @staticmethod
    def remove_grain_arrow(preview_doc_name, obj_name):
        try:
            if preview_doc_name not in App.listDocuments():
                return False
            p_doc = App.getDocument(preview_doc_name)
            if p_doc is None:
                return False
            arrow_name = GrainPreparer._arrow_object_name_for(obj_name)
            try:
                obj = p_doc.getObject(arrow_name)
                if obj:
                    p_doc.removeObject(obj.Name)
                    try:
                        p_doc.recompute()
                    except Exception:
                        pass
                    return True
            except Exception:
                pass
            removed = False
            for o in list(p_doc.Objects):
                try:
                    if getattr(o, "Label", "").startswith("GrainArrow_") and obj_name in getattr(o, "Label", ""):
                        p_doc.removeObject(o.Name)
                        removed = True
                except Exception:
                    continue
            if removed:
                try:
                    p_doc.recompute()
                except Exception:
                    pass
                return True
            return False
        except Exception:
            App.Console.PrintError("remove_grain_arrow failed:\n" + traceback.format_exc())
            return False

    @staticmethod
    def remove_all_grain_arrows(preview_doc_name):
        try:
            if preview_doc_name not in App.listDocuments():
                return False
            p_doc = App.getDocument(preview_doc_name)
            if p_doc is None:
                return False
            removed_any = False
            for o in list(p_doc.Objects):
                try:
                    name = getattr(o, "Name", "") or ""
                    lbl = getattr(o, "Label", "") or ""
                    if name.startswith("GrainArrow_") or lbl.startswith("GrainArrow_"):
                        try:
                            p_doc.removeObject(o.Name)
                            removed_any = True
                        except Exception:
                            App.Console.PrintError("Failed to remove grain arrow '%s':\n%s\n" % (o.Name, traceback.format_exc()))
                except Exception:
                    continue
            if removed_any:
                try:
                    p_doc.recompute()
                except Exception:
                    pass
            return removed_any
        except Exception:
            App.Console.PrintError("remove_all_grain_arrows failed:\n" + traceback.format_exc())
            return False

    @staticmethod
    def update_grain_arrow(preview_doc_name, obj_name, enable=True, axis='X',
                          length_factor=0.5, width_factor=0.06, z_offset=0.5, color=(1.0, 0.0, 0.0)):
        """
        Create or remove a 2D red arrow (planar face/wire) representing the grain direction for obj_name.
        The arrow head (triangle) is clamped inside the part bbox so it doesn't extend past part edges.
        """
        try:
            if preview_doc_name not in App.listDocuments():
                return False
            p_doc = App.getDocument(preview_doc_name)
            if p_doc is None:
                return False

            # Remove existing arrow for this object first
            try:
                GrainPreparer.remove_grain_arrow(preview_doc_name, obj_name)
            except Exception:
                pass

            if not enable:
                return True

            obj = GrainPreparer._find_preview_object(p_doc, obj_name)
            if obj is None:
                App.Console.PrintMessage("update_grain_arrow: object '%s' not found in preview.\n" % str(obj_name))
                return False

            # compute bbox and center/top z
            try:
                bb = obj.Shape.BoundBox
                bbox_w = max(0.0, bb.XMax - bb.XMin)
                bbox_h = max(0.0, bb.YMax - bb.YMin)
                bbox_z = max(0.0, bb.ZMax - bb.ZMin)
                center_x = (bb.XMin + bb.XMax) / 2.0
                center_y = (bb.YMin + bb.YMax) / 2.0
                top_z = bb.ZMax
            except Exception:
                try:
                    base = obj.Placement.Base
                    center_x, center_y, top_z = base.x, base.y, base.z
                    bbox_w = bbox_h = max(10.0, 10.0)
                except Exception:
                    center_x = center_y = top_z = 0.0
                    bbox_w = bbox_h = 10.0
                    bbox_z = 0.0

            main_dim = max(bbox_w, bbox_h, 1.0)

            # desired sizes
            desired_length = max(1.0, float(length_factor) * main_dim)
            shaft_width = max(0.2, float(width_factor) * main_dim)

            # clamp length so arrow fits inside bbox (half-length limited by distances to edges)
            axis_upper = (axis or 'X').upper()
            margin_factor = 0.95  # leave small margin from edge
            if axis_upper == 'X':
                max_pos = bb.XMax - center_x
                max_neg = center_x - bb.XMin
                max_half = min(max_pos, max_neg)
                if max_half <= 0:
                    half_len = desired_length / 2.0
                else:
                    half_len = min(desired_length / 2.0, max_half * margin_factor)
                length = max(half_len * 2.0, 1.0)
            else:
                max_pos = bb.YMax - center_y
                max_neg = center_y - bb.YMin
                max_half = min(max_pos, max_neg)
                if max_half <= 0:
                    half_len = desired_length / 2.0
                else:
                    half_len = min(desired_length / 2.0, max_half * margin_factor)
                length = max(half_len * 2.0, 1.0)

            # head and geometry
            head_length = max(max(shaft_width * 1.5, length * 0.18), min(length * 0.45, length * 0.35))
            head_half = max(shaft_width * 1.8, shaft_width * 2.5)

            half_length = length / 2.0
            x0 = -half_length
            shaft_end = x0 + (length - head_length)
            tip_x = x0 + length
            w = shaft_width

            # polygon: shaft rectangle + triangular head (keeps head within shaft_end..tip_x)
            poly2d = [
                (x0, -w/2.0),
                (shaft_end, -w/2.0),
                (shaft_end, -head_half),
                (tip_x, 0.0),
                (shaft_end, head_half),
                (shaft_end, w/2.0),
                (x0, w/2.0),
                (x0, -w/2.0)
            ]

            # rotate polygon if axis == 'Y' so arrow points +Y.
            if axis_upper == 'Y':
                poly2d = [(-y, x) for (x, y) in poly2d]

            # translate to center_x, center_y and set z = top_z + small offset
            z_plane = top_z + float(z_offset)
            pts = [App.Vector((center_x + px) if axis_upper == 'X' or True else center_x + px,
                              (center_y + py) if axis_upper == 'Y' or True else center_y + py,
                              z_plane) for (px, py) in poly2d]
            # Note: the conditional is redundant but kept for clarity; we translate both coords by center.

            arrow_name = GrainPreparer._arrow_object_name_for(obj.Name)
            try:
                if Part is not None:
                    wire = Part.makePolygon(pts)
                    try:
                        face = Part.Face(wire)
                        feat = p_doc.addObject("Part::Feature", arrow_name)
                        feat.Label = "GrainArrow_" + (getattr(obj, "Label", obj.Name) or obj.Name)
                        feat.Shape = face
                        try:
                            vo = feat.ViewObject
                            vo.ShapeColor = color
                            vo.LineColor = color
                            vo.LineWidth = 1
                            vo.DisplayMode = "Flat Lines"
                        except Exception:
                            pass
                    except Exception:
                        feat = p_doc.addObject("Part::Feature", arrow_name)
                        feat.Label = "GrainArrow_" + (getattr(obj, "Label", obj.Name) or obj.Name)
                        feat.Shape = wire
                        try:
                            vo = feat.ViewObject
                            vo.LineColor = color
                            vo.LineWidth = 2
                            vo.DisplayMode = "Wireframe"
                        except Exception:
                            pass
                    try:
                        p_doc.recompute()
                    except Exception:
                        pass
                    return True
            except Exception:
                App.Console.PrintError("update_grain_arrow (Part) failed:\n" + traceback.format_exc())

            try:
                if Draft is not None:
                    try:
                        dw = Draft.make_wire(pts, closed=True)
                        dw.Label = "GrainArrow_" + (getattr(obj, "Label", obj.Name) or obj.Name)
                        try:
                            dw.ViewObject.LineColor = color
                            dw.ViewObject.LineWidth = 2
                        except Exception:
                            pass
                    except Exception:
                        try:
                            dw = Draft.makeWire(pts, closed=True)
                            dw.Label = "GrainArrow_" + (getattr(obj, "Label", obj.Name) or obj.Name)
                            try:
                                dw.ViewObject.LineColor = color
                                dw.ViewObject.LineWidth = 2
                            except Exception:
                                pass
                        except Exception:
                            ph = p_doc.addObject("App::FeaturePython", arrow_name)
                            ph.Label = "GrainArrow_" + (getattr(obj, "Label", obj.Name) or obj.Name)
                    try:
                        p_doc.recompute()
                    except Exception:
                        pass
                    return True
            except Exception:
                App.Console.PrintError("update_grain_arrow (Draft fallback) failed:\n" + traceback.format_exc())

            try:
                ph = p_doc.addObject("App::FeaturePython", arrow_name)
                ph.Label = "GrainArrow_" + (getattr(obj, "Label", obj.Name) or obj.Name)
                try:
                    p_doc.recompute()
                except Exception:
                    pass
                return True
            except Exception:
                App.Console.PrintError("update_grain_arrow final fallback failed:\n" + traceback.format_exc())
                return False

        except Exception:
            App.Console.PrintError("update_grain_arrow failed:\n" + traceback.format_exc())
            return False
            
    