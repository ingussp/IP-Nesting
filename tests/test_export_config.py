"""Exercise the nesting CLI config builder without FreeCAD or PySide."""
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
            'ipnesting_export_config_under_test', ROOT / 'IPNestingExport.py'
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class BuildNestingConfigTests(unittest.TestCase):
    # Load the module once for all config builder tests.
    @classmethod
    def setUpClass(cls):
        cls.export = _load_export_module()

    # Defaults produce a valid bitmap `first` config with every CLI field.
    def test_defaults(self):
        config = self.export.build_nesting_config()
        self.assertEqual(config['algorithm'], 'bitmap')
        self.assertEqual(config['mode'], 'first')
        self.assertEqual(config['timeLimitSeconds'], 0.0)
        self.assertEqual(config['continuousRoundSeconds'], 30.0)
        self.assertEqual(config['trials'], 2)
        self.assertEqual(config['perPartRotationsOnly'], True)
        self.assertEqual(config['rotations'], 4)
        self.assertEqual(config['resolution'], 1.0)
        self.assertEqual(config['bitmapSearchStepPx'], 1)
        self.assertEqual(config['curveTolerance'], 0.3)
        self.assertEqual(config['cacheRejects'], True)
        self.assertEqual(config['spacing'], 0.0)
        self.assertEqual(config['partToSheet'], 0.0)
        self.assertEqual(config['partToHole'], 0.0)
        self.assertEqual(config['gpu'], {
            'enabled': False,
            'device': -1,
            'fallbackToCpu': True,
            'batchSize': 65536,
        })

    # The config object only uses keys the CLI accepts.
    def test_keys_match_cli_schema(self):
        config = self.export.build_nesting_config()
        self.assertEqual(
            set(config),
            {
                'algorithm', 'mode', 'perPartRotationsOnly', 'rotations',
                'resolution', 'threads', 'trials', 'curveTolerance',
                'cacheRejects', 'bitmapSearchStepPx', 'spacing',
                'partToSheet', 'partToHole', 'timeLimitSeconds',
                'continuousRoundSeconds', 'gpu',
            }
        )
        self.assertEqual(
            set(config['gpu']),
            {'enabled', 'device', 'fallbackToCpu', 'batchSize'}
        )

    # First and continuous modes always export a zero time budget.
    def test_time_budget_by_mode(self):
        first = self.export.build_nesting_config(
            mode='first', time_limit_seconds=999.0
        )
        self.assertEqual(first['timeLimitSeconds'], 0.0)

        continuous = self.export.build_nesting_config(
            mode='continuous', time_limit_seconds=999.0
        )
        self.assertEqual(continuous['timeLimitSeconds'], 0.0)

        # Timed mode with no budget falls back to a default positive budget.
        timed = self.export.build_nesting_config(
            mode='timed', time_limit_seconds=0.0
        )
        self.assertGreater(timed['timeLimitSeconds'], 0.0)

    # Numeric settings are clamped to the ranges the CLI validates.
    def test_range_coercion(self):
        config = self.export.build_nesting_config(
            trials=99,
            rotations=99999,
            resolution=0.0,
            step=0,
            curve_tolerance=-5.0,
            threads=9999,
            gpu_device=-9,
            gpu_batch_size=1,
            continuous_round_seconds=0.0,
        )
        self.assertEqual(config['trials'], 4)
        self.assertEqual(config['rotations'], 3600)
        self.assertGreater(config['resolution'], 0.0)
        self.assertEqual(config['bitmapSearchStepPx'], 1)
        self.assertEqual(config['curveTolerance'], 0.0)
        self.assertEqual(config['threads'], 256)
        self.assertEqual(config['gpu']['device'], -1)
        self.assertEqual(config['gpu']['batchSize'], 256)
        self.assertEqual(config['continuousRoundSeconds'], 0.01)

    # Invalid mode strings fall back to first.
    def test_invalid_mode_falls_back(self):
        self.assertEqual(
            self.export.build_nesting_config(mode='bogus')['mode'], 'first'
        )


if __name__ == '__main__':
    unittest.main()
