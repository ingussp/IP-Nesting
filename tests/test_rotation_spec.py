"""Exercise rotation cell parsing without a FreeCAD or PySide installation."""
import importlib.util
import sys
import types
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


# Load IPNestingExport.py with the FreeCAD/PySide dependencies stubbed out.
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
            'ipnesting_export_under_test', ROOT / 'IPNestingExport.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class RotationSpecTests(unittest.TestCase):
    # Load the module once for all rotation parsing tests.
    @classmethod
    def setUpClass(cls):
        cls.export = _load_export_module()

    # Plain integers map to a uniform orientation grid, clamped to 1..3600.
    def test_plain_count(self):
        parse = self.export.parse_rotation_spec
        self.assertEqual(parse('1'), {'rotations': 1})
        self.assertEqual(parse('32'), {'rotations': 32})
        self.assertEqual(parse('3600'), {'rotations': 3600})
        self.assertEqual(parse('5000'), {'rotations': 3600})
        self.assertEqual(parse('0'), {'rotations': 1})
        self.assertEqual(parse('  4  '), {'rotations': 4})

    # A parenthesized value is a degree step expanded into permitted angles.
    def test_degree_step(self):
        parse = self.export.parse_rotation_spec
        self.assertEqual(parse('(90)'), {'allowedAngles': [0.0, 90.0, 180.0, 270.0]})
        self.assertEqual(
            parse('(45)'),
            {'allowedAngles': [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]}
        )
        self.assertEqual(parse('(120)'), {'allowedAngles': [0.0, 120.0, 240.0]})

    # A bracketed list becomes permitted angles with 0 always included.
    def test_angle_list(self):
        parse = self.export.parse_rotation_spec
        self.assertEqual(parse('[45, 90]'), {'allowedAngles': [0.0, 45.0, 90.0]})
        self.assertEqual(parse('[180]'), {'allowedAngles': [0.0, 180.0]})
        self.assertEqual(
            parse('[90, 45, 90]'),
            {'allowedAngles': [0.0, 90.0, 45.0]}
        )
        self.assertEqual(
            parse('[-45, 370]'),
            {'allowedAngles': [0.0, 315.0, 10.0]}
        )

    # Empty, incomplete and invalid input yields no rule.
    def test_invalid(self):
        parse = self.export.parse_rotation_spec
        for bad in ('', '   ', None, 'abc', '12.5', '[45', '(90', '[]', '(0)', '(360)'):
            self.assertIsNone(parse(bad), bad)

    # Display normalization keeps valid bracket input, rejects garbage and
    # leaves an in-progress edit untouched so the user can keep typing.
    def test_normalize(self):
        norm = self.export.normalize_rotation_text
        self.assertEqual(norm('32'), '32')
        self.assertEqual(norm('5000'), '3600')
        self.assertEqual(norm('(90)'), '(90)')
        self.assertEqual(norm('[45, 90]'), '[45, 90]')
        self.assertEqual(norm('abc'), '1')
        self.assertEqual(norm(''), '1')
        self.assertEqual(norm(None), '1')
        self.assertEqual(norm('[45'), '[45')
        self.assertEqual(norm('(90'), '(90')
        self.assertEqual(norm('[45, '), '[45, ')


if __name__ == '__main__':
    unittest.main()
