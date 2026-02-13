"""
IPNestingImport2D - DXF/SVG import to Nesting_Preview and conversion to stable Part::Feature.

Goal:
- Import 2D geometry (DXF/SVG) into the preview document.
- Collect imported objects' Shapes and convert them into one or more Part::Feature objects
  with a stable OCC Shape (Compound of edges/wires, optionally Faces when possible).
- Return created preview object names so the panel can add them into the table and rotate them like others.

Notes:
- FreeCAD import module names may vary between installs. We try multiple fallbacks.
- We don't require Faces for nesting; BoundBox + Placement must work.
"""

import os
import traceback

import FreeCAD as App

try:
    import Part
except Exception:
    Part = None


def _ensure_preview_doc(panel):
    """Get or create preview doc using existing panel machinery if available."""
    try:
        if hasattr(panel, "ensure_preview_doc"):
            return panel.ensure_preview_doc()
    except Exception:
        pass

    # fallback
    if panel.preview_doc_name not in App.listDocuments():
        App.newDocument(panel.preview_doc_name)
    return App.getDocument(panel.preview_doc_name)


def _import_dxf(path, doc_name):
    """Import DXF into doc_name."""
    # Common importer module for DXF in FreeCAD
    try:
        import importDXF
        importDXF.insert(path, doc_name)
        return True
    except Exception:
        pass

    # Fallback: sometimes ImportGui handles formats, depending on install
    try:
        import ImportGui
        ImportGui.insert(path, doc_name)
        return True
    except Exception:
        pass

    return False


def _import_svg(path, doc_name):
    """Import SVG into doc_name."""
    try:
        import importSVG
        importSVG.insert(path, doc_name)
        return True
    except Exception:
        pass

    try:
        import ImportGui
        ImportGui.insert(path, doc_name)
        return True
    except Exception:
        pass

    return False


def _safe_shape(obj):
    try:
        shp = getattr(obj, "Shape", None)
        if shp is None:
            return None
        # Some objects may have empty shapes
        try:
            _ = shp.BoundBox
        except Exception:
            return None
        return shp
    except Exception:
        return None


def _shape_to_compound(shapes):
    """Build a Part.Compound from a list of shapes."""
    if Part is None:
        return None
    try:
        valid = [s for s in shapes if s is not None]
        if not valid:
            return None
        # Flatten compounds (optional, but helps)
        exploded = []
        for s in valid:
            try:
                if hasattr(s, "Solids") and s.Solids:
                    exploded.append(s)
                elif hasattr(s, "Faces") and s.Faces:
                    exploded.append(s)
                elif hasattr(s, "Wires") and s.Wires:
                    exploded.append(s)
                elif hasattr(s, "Edges") and s.Edges:
                    exploded.append(s)
                else:
                    exploded.append(s)
            except Exception:
                exploded.append(s)

        return Part.makeCompound(exploded)
    except Exception:
        return None


def _try_make_faces_from_wires(shp):
    """
    Optional: if shape contains closed wires, try to convert each to a Face.
    Faces are not required, but can be beneficial for downstream operations.
    Returns either a Compound of faces, or None if not possible.
    """
    if Part is None or shp is None:
        return None
    try:
        faces = []
        wires = []
        try:
            wires = list(getattr(shp, "Wires", [])) or []
        except Exception:
            wires = []

        if not wires:
            return None

        for w in wires:
            try:
                if hasattr(w, "isClosed") and not w.isClosed():
                    continue
                f = Part.Face(w)
                faces.append(f)
            except Exception:
                continue

        if not faces:
            return None
        return Part.makeCompound(faces) if len(faces) > 1 else faces[0]
    except Exception:
        return None


def import_2d_file_to_preview(panel, path, fmt=None, make_faces_if_possible=True,
                             group_into_single_object=True, label_prefix=None):
    """
    Import DXF/SVG into Nesting_Preview and convert to stable Part::Feature.

    Args:
        panel: NestingTaskPanel
        path: file path
        fmt: "dxf" or "svg" (optional; auto from extension)
        make_faces_if_possible: try to create Faces from closed wires (optional)
        group_into_single_object: if True -> make ONE Part::Feature from all imported shapes
                                 if False -> make one Part::Feature per imported object (quick & simple)
        label_prefix: label prefix for created features

    Returns:
        created_names: list[str] of created Part::Feature object Names in preview doc
    """
    created_names = []
    try:
        if not path or not os.path.exists(path):
            App.Console.PrintError("IPNestingImport2D: file not found: %s\n" % str(path))
            return created_names

        ext = os.path.splitext(path)[1].lower().lstrip(".")
        fmt = (fmt or ext).lower()

        p_doc = _ensure_preview_doc(panel)
        if not p_doc:
            App.Console.PrintError("IPNestingImport2D: could not ensure preview document.\n")
            return created_names

        before = set([o.Name for o in p_doc.Objects])

        ok = False
        if fmt == "dxf":
            ok = _import_dxf(path, p_doc.Name)
        elif fmt == "svg":
            ok = _import_svg(path, p_doc.Name)
        else:
            App.Console.PrintError("IPNestingImport2D: unsupported format: %s\n" % fmt)
            return created_names

        if not ok:
            App.Console.PrintError("IPNestingImport2D: import failed for %s\n" % path)
            return created_names

        try:
            p_doc.recompute()
        except Exception:
            pass

        after_objs = [o for o in p_doc.Objects if o.Name not in before]
        if not after_objs:
            App.Console.PrintMessage("IPNestingImport2D: import created no new objects.\n")
            return created_names

        # Collect shapes
        imported_shapes = []
        per_obj_shapes = []
        for o in after_objs:
            shp = _safe_shape(o)
            if shp is None:
                continue
            imported_shapes.append(shp)
            per_obj_shapes.append((o, shp))

        if not imported_shapes:
            App.Console.PrintError("IPNestingImport2D: imported objects had no usable Shape.\n")
            return created_names

        # Create stable Part::Feature(s)
        label_prefix = label_prefix or ("DXF" if fmt == "dxf" else "SVG")

        if group_into_single_object:
            compound = _shape_to_compound(imported_shapes)
            final_shape = compound

            if make_faces_if_possible:
                face_shape = _try_make_faces_from_wires(compound)
                if face_shape is not None:
                    final_shape = face_shape

            if final_shape is None:
                App.Console.PrintError("IPNestingImport2D: failed to build compound shape.\n")
                return created_names

            feat = p_doc.addObject("Part::Feature", "Preview2D")
            feat.Label = "%s: %s" % (label_prefix, os.path.basename(path))
            feat.Shape = final_shape
            created_names.append(feat.Name)
        else:
            for (src_obj, shp) in per_obj_shapes:
                final_shape = shp
                if make_faces_if_possible:
                    face_shape = _try_make_faces_from_wires(shp)
                    if face_shape is not None:
                        final_shape = face_shape

                feat = p_doc.addObject("Part::Feature", "Preview2D")
                feat.Label = "%s: %s" % (label_prefix, getattr(src_obj, "Label", src_obj.Name))
                feat.Shape = final_shape
                created_names.append(feat.Name)

        # Optional: hide raw imported objects to avoid clutter (keep them if you prefer)
        try:
            for o in after_objs:
                try:
                    if hasattr(o, "ViewObject"):
                        o.ViewObject.Visibility = False
                except Exception:
                    pass
        except Exception:
            pass

        try:
            p_doc.recompute()
        except Exception:
            pass

        App.Console.PrintMessage("IPNestingImport2D: created %d Part::Feature(s) from %s\n" %
                                 (len(created_names), os.path.basename(path)))
        return created_names

    except Exception:
        App.Console.PrintError("IPNestingImport2D.import_2d_file_to_preview failed:\n" + traceback.format_exc())
        return created_names


# Convenience wrappers
def import_dxf_to_preview(panel, path, **kwargs):
    return import_2d_file_to_preview(panel, path, fmt="dxf", **kwargs)


def import_svg_to_preview(panel, path, **kwargs):
    # SVG import often contains overlapping fill/stroke wires -> faces can overlap visually.
    # For nesting/rotation we only need stable Shape + BoundBox, so default to wires (no faces).
    if "make_faces_if_possible" not in kwargs:
        kwargs["make_faces_if_possible"] = False
    return import_2d_file_to_preview(panel, path, fmt="svg", **kwargs)