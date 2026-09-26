"""Exercise part coordinate export helpers without a FreeCAD/PySide install."""
import importlib.util
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class _Vec:
    def __init__(self, x, y, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class _Rotation90:
    """Rotate a vector 90 degrees counter-clockwise about Z."""

    @staticmethod
    def multVec(vector):
        return _Vec(-vector.y, vector.x, vector.z)


def _load_export_module():
    modules = {
        'FreeCAD': types.SimpleNamespace(
            Vector=_Vec,
            Console=types.SimpleNamespace(
                PrintError=lambda *a, **k: None,
                PrintMessage=lambda *a, **k: None,
                PrintWarning=lambda *a, **k: None,
            ),
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
            'ipnesting_export_coordinates_under_test',
            ROOT / 'IPNestingExport.py',
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class PartCoordinateExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = _load_export_module()

    # Part polygons must be exported as [x, y] arrays to match the sheet
    # format, not as {"x", "y"} dictionaries.
    def test_normalize_polygon_returns_xy_arrays(self):
        normalize = self.export._normalize_polygon
        result = normalize(
            [
                [5.0, 7.0],
                [9.0, 7.0],
                [9.0, 3.0],
            ]
        )
        self.assertEqual(
            result,
            [[0.0, 4.0], [4.0, 4.0], [4.0, 0.0]],
        )

    # Point rotation must reflect the object's Placement so the exported
    # polygon matches the on-screen (grain-rotated) orientation.
    def test_transform_point_applies_placement_rotation(self):
        transform = self.export._transform_point_without_translation
        obj = types.SimpleNamespace(
            Placement=types.SimpleNamespace(
                Rotation=_Rotation90()
            )
        )
        self.assertEqual(transform(obj, [1.0, 0.0]), [0.0, 1.0])
        self.assertEqual(transform(obj, [0.0, 1.0]), [-1.0, 0.0])

    # The grain rotation restriction produces the permitted [0, 180] rule.
    def test_grain_rotation_restriction(self):
        parse = self.export.parse_rotation_spec
        self.assertEqual(
            parse('[0, 180]'),
            {'allowedAngles': [0.0, 180.0]},
        )


if __name__ == '__main__':
    unittest.main()
