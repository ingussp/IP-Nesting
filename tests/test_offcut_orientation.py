"""Exercise offcut preview grain orientation without FreeCAD or PySide."""
import importlib.util
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class _DummyClass:
    pass


class _AutoNamespace:
    """Return a dummy class for any attribute so Qt base classes resolve."""

    def __getattr__(self, name):
        return _DummyClass


def _load_module(path, name):
    modules = {
        'FreeCAD': types.SimpleNamespace(
            Console=types.SimpleNamespace(
                PrintError=lambda *a, **k: None,
                PrintMessage=lambda *a, **k: None,
                PrintWarning=lambda *a, **k: None,
            )
        ),
        'FreeCADGui': types.SimpleNamespace(),
        'PySide': types.SimpleNamespace(
            QtGui=_AutoNamespace(),
            QtCore=_AutoNamespace(),
        ),
        'IPNestingLanguages': types.SimpleNamespace(
            tr=lambda key: key,
            translate_buttons=lambda *a, **k: None,
            ui_call=lambda *a, **k: None,
            ui_widget=lambda *a, **k: None,
            register_window=lambda *a, **k: None,
        ),
        'IPNestingAddSheet': types.SimpleNamespace(
            AddSheetOrOffcutDialog=_DummyClass,
        ),
        'IPNestingOffcuts': types.SimpleNamespace(
            extract_offcut_from_dxf=lambda *a, **k: None,
            add_or_increment_material=lambda *a, **k: None,
            polygon_area=lambda *a, **k: 0.0,
        ),
    }
    with patch.dict('sys.modules', modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class OrientPolygonForGrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.offcut = _load_module(
            ROOT / 'IPNestingOffcutShowDialog.py',
            'ipnesting_offcut_under_test',
        )
        cls.export = _load_module(
            ROOT / 'IPNestingExport.py',
            'ipnesting_export_under_test',
        )

    def test_y_grain_rotates_rectangular_sheet(self):
        outer = [[0, 0], [500, 0], [500, 1000], [0, 1000]]
        result = self.offcut._orient_polygon_for_grain(outer, "Y")

        xs = [point[0] for point in result]
        ys = [point[1] for point in result]

        self.assertAlmostEqual(min(xs), 0.0)
        self.assertAlmostEqual(min(ys), 0.0)
        self.assertAlmostEqual(max(xs) - min(xs), 1000.0)
        self.assertAlmostEqual(max(ys) - min(ys), 500.0)

    def test_x_and_none_grains_unchanged(self):
        outer = [[0, 0], [500, 0], [500, 1000], [0, 1000]]

        self.assertEqual(
            self.offcut._orient_polygon_for_grain(outer, "X"),
            outer,
        )
        self.assertEqual(
            self.offcut._orient_polygon_for_grain(outer, "None"),
            outer,
        )

    def test_preview_matches_export_orientation(self):
        outer = [[0, 0], [500, 0], [500, 1000], [0, 1000]]

        for grain in ("X", "Y", "None"):
            self.assertEqual(
                self.offcut._orient_polygon_for_grain(outer, grain),
                self.export._orient_sheet_polygon(outer, grain),
            )


if __name__ == '__main__':
    unittest.main()
