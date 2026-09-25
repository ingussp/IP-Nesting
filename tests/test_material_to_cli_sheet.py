"""Exercise sheet polygon export without FreeCAD or PySide."""
import importlib.util
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load_export_module():
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
            QtGui=types.SimpleNamespace(),
            QtCore=types.SimpleNamespace(Qt=types.SimpleNamespace(UserRole=32)),
        ),
        'IPNestingLanguages': types.SimpleNamespace(tr=lambda key: key),
    }
    with patch.dict('sys.modules', modules):
        spec = importlib.util.spec_from_file_location(
            'ipnesting_sheet_export_under_test', ROOT / 'IPNestingExport.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class MaterialToCliSheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = _load_export_module()

    # Rectangular sheets are always exported as polygons, never width/height.
    def test_rectangular_sheet_becomes_polygon(self):
        material = {
            'type': 'rectangular',
            'width': 500.0,
            'height': 1000.0,
            'quantity': 2,
            'grain': 'None',
            'outer': [[0, 0], [500, 0], [500, 1000], [0, 1000]],
        }
        sheet = self.export._material_to_cli_sheet(material)
        self.assertNotIn('width', sheet)
        self.assertNotIn('height', sheet)
        self.assertEqual(
            sheet['points'],
            [[0, 0], [500, 0], [500, 1000], [0, 1000]],
        )
        self.assertEqual(sheet['quantity'], 2)

    # A Y grain rotates the sheet so its texture dimension becomes horizontal.
    def test_y_grain_rotates_rectangular_sheet(self):
        material = {
            'type': 'rectangular',
            'width': 500.0,
            'height': 1000.0,
            'quantity': 1,
            'grain': 'Y',
            'outer': [[0, 0], [500, 0], [500, 1000], [0, 1000]],
        }
        sheet = self.export._material_to_cli_sheet(material)
        points = sheet['points']
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertEqual(max(xs) - min(xs), 1000.0)
        self.assertEqual(max(ys) - min(ys), 500.0)
        self.assertEqual(min(xs), 0.0)
        self.assertEqual(min(ys), 0.0)

    # X and None grains leave the polygon unchanged.
    def test_x_and_none_grains_unchanged(self):
        outer = [[10, 20], [110, 20], [110, 70], [10, 70]]
        for grain in ('X', 'None'):
            material = {'type': 'dxf', 'grain': grain, 'outer': outer, 'quantity': 1}
            sheet = self.export._material_to_cli_sheet(material)
            self.assertEqual(sheet['points'], outer, grain)

    # A Y grain on a non-origin polygon is rotated and normalized.
    def test_y_grain_rotates_and_normalizes_dxf(self):
        material = {
            'type': 'dxf',
            'grain': 'Y',
            'outer': [[10, 20], [110, 20], [110, 70], [10, 70]],
            'quantity': 1,
        }
        sheet = self.export._material_to_cli_sheet(material)
        points = sheet['points']
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertEqual(max(xs) - min(xs), 50.0)
        self.assertEqual(max(ys) - min(ys), 100.0)
        self.assertEqual(min(xs), 0.0)
        self.assertEqual(min(ys), 0.0)


if __name__ == '__main__':
    unittest.main()
