"""Bundled executable selection and immutable geometry for a nesting job."""
import os
import platform


def executable_candidates(workbench, system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if system == "Windows" and arch == "x86_64":
        folders, filename = ["windows-x86_64"], "clinesting.exe"
    elif system == "Linux" and arch in ("x86_64", "arm64"):
        folders, filename = ["linux-x86_64" if arch == "x86_64" else "linux-aarch64"], "clinesting"
    elif system == "Darwin" and arch in ("x86_64", "arm64"):
        folders, filename = ["macos-universal2", "macos-" + arch], "clinesting"
    else:
        raise RuntimeError("Unsupported nesting platform: %s / %s" % (system, machine))
    return [os.path.join(os.path.abspath(workbench), "clinesting", folder, filename) for folder in folders]


def find_executable(workbench, system=None, machine=None):
    for path in executable_candidates(workbench, system, machine):
        if os.path.isfile(path):
            if (system or platform.system()) != "Windows" and not os.access(path, os.X_OK):
                raise RuntimeError("The bundled CLI is not executable: " + path)
            return path
    return None


def snapshot_part(obj, deflection):
    """Capture the visible shape with its existing Placement and face orientation.

    BREP includes curved geometry and the displayed top/bottom orientation.
    Never derive the result from a later, mutable preview object.
    """
    from IPNestingExport import _extract_part_candidate_wires, _points_min_xy
    candidates = _extract_part_candidate_wires(obj, deflection)
    ox, oy = _points_min_xy(candidates[0])
    record = {
        "shape_brep": obj.Shape.exportBrepToString(),
        "normalization_offset": [ox, oy, obj.Shape.BoundBox.ZMin],
        "display": {},
    }
    try:
        view = obj.ViewObject
        record["display"] = {
            "shape_color": list(view.ShapeColor),
            "line_color": list(view.LineColor),
            "transparency": view.Transparency,
            "face_colors": [list(color) for color in view.DiffuseColor],
        }
    except (AttributeError, TypeError):
        pass
    return record
