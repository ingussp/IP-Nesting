"""
IPNestingResult - run deepnest.exe, wait for result.json,
and import nesting results into a new FreeCAD document.

This module is intentionally separate from IPNestingGui so the
process-waiting logic and result-import logic stay isolated
from the UI layer.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui, QtCore
import json
import os
import traceback
import subprocess
import tempfile
import math
from datetime import datetime


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def _load_json_file(path):
    """
    Load a JSON file and return parsed data.
    Returns None if loading fails.
    """
    try:
        if not path or not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    except Exception:
        return None


def _file_size(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return None


def _normalize_point_2d(x, y):
    try:
        return {
            "x": round(float(x), 6),
            "y": round(float(y), 6),
        }
    except Exception:
        return {
            "x": 0.0,
            "y": 0.0,
        }


def _rotate_point_2d(x, y, angle_deg):
    """
    Rotate a 2D point around the origin by angle_deg.
    """
    try:
        angle_rad = math.radians(float(angle_deg))
        c = math.cos(angle_rad)
        s = math.sin(angle_rad)
        return (x * c - y * s, x * s + y * c)
    except Exception:
        return (x, y)


def _polygon_to_vectors(points, z=0.0):
    """
    Convert a polygon point list to FreeCAD vectors.
    """
    vectors = []
    for p in points or []:
        try:
            if isinstance(p, dict):
                x = float(p.get("x", 0.0))
                y = float(p.get("y", 0.0))
            else:
                x = float(p[0])
                y = float(p[1])
            vectors.append(App.Vector(x, y, z))
        except Exception:
            continue
    return vectors


class NestingProcessManager(object):
    """
    Launches deepnest.exe, waits for result.json asynchronously,
    and hands the result to NestingResultImporter.
    """

    def __init__(self, panel):
        self.panel = panel
        self.process = None
        self.result_timer = None
        self.input_path = None
        self.result_path = None
        self.session_path = None
        self.job_id = None
        self._last_result_size = None
        self._stable_result_checks = 0
        self._started_at = None
        self._deepnest_exe = None

    def _script_dir(self):
        return os.path.abspath(os.path.dirname(__file__))

    def _default_deepnest_exe(self):
        """
        Adjust this path if your deepnest.exe lives elsewhere.
        """
        return os.path.join(self._script_dir(), "deepnest.exe")

    def start_nesting(self, input_path):
        """
        Start deepnest.exe and begin polling for result.json.
        """
        try:
            self.input_path = input_path
            script_dir = self._script_dir()
            self.result_path = os.path.join(script_dir, "result.json")
            self.session_path = os.path.join(script_dir, "nesting_session.json")
            self._deepnest_exe = self._default_deepnest_exe()

            if not os.path.exists(self._deepnest_exe):
                QtGui.QMessageBox.critical(
                    self.panel.form,
                    "Nesting error",
                    "deepnest.exe was not found:\n%s" % self._deepnest_exe
                )
                return False

            # Load job/session data if available
            session = _load_json_file(self.session_path)
            if session:
                self.job_id = session.get("job_id")

            # Remove old result file
            try:
                if os.path.exists(self.result_path):
                    os.remove(self.result_path)
            except Exception:
                pass

            # Disable run button while waiting
            try:
                self.panel.run_btn.setEnabled(False)
            except Exception:
                pass

            # Start deepnest.exe
            self.process = subprocess.Popen(
                [self._deepnest_exe, self.input_path],
                cwd=script_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            self._started_at = datetime.utcnow()
            self._last_result_size = None
            self._stable_result_checks = 0

            # Start timer
            self.result_timer = QtCore.QTimer()
            self.result_timer.setInterval(500)
            self.result_timer.timeout.connect(self._check_result_file)
            self.result_timer.start()

            App.Console.PrintMessage(
                "deepnest.exe started. Waiting for result.json...\n"
            )

            return True

        except Exception:
            App.Console.PrintError(
                "start_nesting failed:\n" + traceback.format_exc()
            )
            self._restore_ui()
            return False

    def _restore_ui(self):
        try:
            if self.result_timer is not None:
                self.result_timer.stop()
        except Exception:
            pass

        try:
            self.panel.run_btn.setEnabled(True)
        except Exception:
            pass

    def _check_result_file(self):
        """
        Poll deepnest.exe and result.json until the result is stable.
        """
        try:
            if self.process is None:
                self._finish_error("No running deepnest process.")
                return

            # Process state
            proc_return = self.process.poll()

            # Result file existence
            if not self.result_path or not os.path.exists(self.result_path):
                # If process ended but no file, fail early
                if proc_return is not None:
                    self._finish_error("deepnest.exe finished but result.json was not created.")
                return

            current_size = _file_size(self.result_path)
            if current_size is None:
                return

            if self._last_result_size is None:
                self._last_result_size = current_size
                self._stable_result_checks = 0
                return

            if current_size == self._last_result_size:
                self._stable_result_checks += 1
            else:
                self._stable_result_checks = 0
                self._last_result_size = current_size
                return

            # Need a couple of stable checks before importing
            if self._stable_result_checks < 2:
                return

            # Try loading JSON
            result_data = _load_json_file(self.result_path)
            if not result_data:
                return

            # Optional job_id check
            if self.job_id is not None:
                result_job_id = result_data.get("job_id")
                if result_job_id is not None and str(result_job_id) != str(self.job_id):
                    self._finish_error(
                        "result.json job_id does not match the current nesting session."
                    )
                    return

            importer = NestingResultImporter(self.panel)
            ok = importer.import_result(
                result_data=result_data,
                session_data=_load_json_file(self.session_path),
            )

            if ok:
                self._finish_success()
            else:
                self._finish_error("Result import failed.")

        except Exception:
            self._finish_error("Result polling failed:\n%s" % traceback.format_exc())

    def _finish_success(self):
        try:
            if self.result_timer is not None:
                self.result_timer.stop()
        except Exception:
            pass

        self._restore_ui()

        try:
            App.Console.PrintMessage("Nesting result imported successfully.\n")
        except Exception:
            pass

    def _finish_error(self, message):
        try:
            if self.result_timer is not None:
                self.result_timer.stop()
        except Exception:
            pass

        self._restore_ui()

        App.Console.PrintError("Nesting failed: %s\n" % message)

        try:
            QtGui.QMessageBox.critical(
                self.panel.form,
                "Nesting failed",
                str(message)
            )
        except Exception:
            pass


class NestingResultImporter(object):
    """
    Reads result.json and creates a Nesting_Result document.
    Supports both 3D FreeCAD parts and 2D DXF/SVG geometry.
    """

    def __init__(self, panel):
        self.panel = panel

    def _get_preview_doc(self):
        try:
            if self.panel.preview_doc_name in App.listDocuments():
                return App.getDocument(self.panel.preview_doc_name)
        except Exception:
            pass
        return None

    def _get_session_part_map(self, session_data):
        """
        Build a map source_part_index -> session part record.
        """
        part_map = {}
        try:
            for part in session_data.get("parts", []) if session_data else []:
                idx = part.get("source_part_index")
                if idx is None:
                    continue
                part_map[int(idx)] = part
        except Exception:
            pass
        return part_map

    def _ensure_result_document(self):
        """
        Create a clean Nesting_Result document.
        """
        try:
            if "Nesting_Result" in App.listDocuments():
                try:
                    App.closeDocument("Nesting_Result")
                except Exception:
                    pass
            doc = App.newDocument("Nesting_Result")
            return doc
        except Exception:
            App.Console.PrintError(
                "Failed to create Nesting_Result document:\n"
                + traceback.format_exc()
            )
            return None

    def import_result(self, result_data, session_data=None):
        """
        Import result.json into a new document.
        """
        try:
            if not result_data:
                return False

            placements = result_data.get("placements", [])
            if not placements:
                App.Console.PrintWarning("No placements found in result.json.\n")
                return False

            preview_doc = self._get_preview_doc()
            if not preview_doc:
                App.Console.PrintWarning("Preview document not found.\n")
                return False

            session_part_map = self._get_session_part_map(session_data or {})
            result_doc = self._ensure_result_document()
            if result_doc is None:
                return False

            # Add a very simple sheet marker
            try:
                sheet_obj = result_doc.addObject("Part::Feature", "Sheet_0")
                sheet_obj.Label = "Nesting Sheet"
            except Exception:
                pass

            for placement in placements:
                try:
                    if not placement.get("placed", True):
                        continue

                    source_index = placement.get("source_part_index")
                    if source_index is None:
                        continue

                    session_part = session_part_map.get(int(source_index), {})
                    preview_obj_name = session_part.get("preview_object_name")
                    source_type = str(session_part.get("source_type", "3d")).lower()

                    instance_index = _safe_int(placement.get("instance_index", 0), 0)
                    part_id = session_part.get("part_id", "part_%s" % source_index)

                    obj_name = "%s_%s" % (part_id, instance_index)

                    if source_type == "3d":
                        self._import_3d_instance(
                            result_doc,
                            preview_doc,
                            preview_obj_name,
                            obj_name,
                            placement
                        )
                    else:
                        self._import_2d_instance(
                            result_doc,
                            preview_doc,
                            preview_obj_name,
                            obj_name,
                            placement
                        )

                except Exception:
                    App.Console.PrintError(
                        "Failed to import one placement:\n"
                        + traceback.format_exc()
                    )

            try:
                result_doc.recompute()
            except Exception:
                pass

            try:
                Gui.activateDocument(result_doc.Name)
                Gui.ActiveDocument.ActiveView.viewTop()
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass

            return True

        except Exception:
            App.Console.PrintError(
                "import_result failed:\n"
                + traceback.format_exc()
            )
            return False

    def _import_3d_instance(self, result_doc, preview_doc, preview_obj_name, obj_name, placement):
        """
        Copy the preview Shape and place it on the result sheet.
        """
        try:
            if not preview_obj_name:
                return False

            src = preview_doc.getObject(preview_obj_name)
            if not src or not hasattr(src, "Shape") or src.Shape is None:
                return False

            new_obj = result_doc.addObject("Part::Feature", obj_name)
            new_obj.Label = obj_name
            new_obj.Shape = src.Shape.copy()

            x = _safe_float(placement.get("x", 0.0))
            y = _safe_float(placement.get("y", 0.0))
            rot_deg = _safe_float(placement.get("rotation_deg", 0.0))

            # NOTE: This is a simple placement. If your result.json
            # defines placement relative to a different anchor point,
            # adjust this here.
            new_obj.Placement = App.Placement(
                App.Vector(x, y, 0.0),
                App.Rotation(App.Vector(0, 0, 1), rot_deg)
            )

            return True

        except Exception:
            App.Console.PrintError(
                "_import_3d_instance failed:\n"
                + traceback.format_exc()
            )
            return False

    def _import_2d_instance(self, result_doc, preview_doc, preview_obj_name, obj_name, placement):
        """
        Build a simple 2D polygon/shape in the result document.
        """
        try:
            if not preview_obj_name:
                return False

            src = preview_doc.getObject(preview_obj_name)
            if not src or not hasattr(src, "Shape") or src.Shape is None:
                return False

            # Use source shape wire if available
            shape = src.Shape
            wires = list(getattr(shape, "Wires", []) or [])
            if not wires:
                return False

            wire = wires[0]
            pts = []
            for v in wire.Vertexes:
                try:
                    pts.append((float(v.Point.x), float(v.Point.y)))
                except Exception:
                    continue

            if not pts:
                return False

            x = _safe_float(placement.get("x", 0.0))
            y = _safe_float(placement.get("y", 0.0))
            rot_deg = _safe_float(placement.get("rotation_deg", 0.0))

            transformed_pts = []
            for px, py in pts:
                rx, ry = _rotate_point_2d(px, py, rot_deg)
                transformed_pts.append(App.Vector(rx + x, ry + y, 0.0))

            if transformed_pts and transformed_pts[0] != transformed_pts[-1]:
                transformed_pts.append(transformed_pts[0])

            import Part
            poly = Part.makePolygon(transformed_pts)
            feat = result_doc.addObject("Part::Feature", obj_name)
            feat.Label = obj_name
            feat.Shape = poly
            return True

        except Exception:
            App.Console.PrintError(
                "_import_2d_instance failed:\n"
                + traceback.format_exc()
            )
            return False