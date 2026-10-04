"""Check preference migration and canonical values without FreeCAD or Qt."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('search_settings_under_test', ROOT / 'IPNestingSettings.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


class Preferences:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def GetString(self, key, default=''):
        return self.values.get(key, default)

    def SetString(self, key, value):
        self.values[key] = value

    def GetBool(self, key, default=False):
        return self.values.get(key, default)

    def SetBool(self, key, value):
        self.values[key] = value


class SearchSettingsTests(unittest.TestCase):
    def test_existing_preferences_survive_moving_controls(self):
        preferences = Preferences({'Resolution': '0,25', 'SearchStepPx': '7',
                                   'CurveTolerance': '0.08', 'CacheRejects': False})
        self.assertEqual(settings.read_search_settings(preferences), {
            'resolution': .25, 'bitmapSearchStepPx': 7,
            'curveTolerance': .08, 'cacheRejects': False})
        self.assertNotIn('BoundaryResolution', preferences.values)

    def test_menu_writes_are_visible_to_export_without_reopening_panel(self):
        preferences = Preferences({'BoundaryResolution': '0.01'})
        settings.write_search_setting('resolution', .125, preferences)
        settings.write_search_setting('bitmapSearchStepPx', 5, preferences)
        settings.write_search_setting('curveTolerance', 0, preferences)
        settings.write_search_setting('cacheRejects', False, preferences)
        self.assertEqual(settings.read_search_settings(preferences), {
            'resolution': .125, 'bitmapSearchStepPx': 5,
            'curveTolerance': 0, 'cacheRejects': False})
        self.assertEqual(preferences.values['BoundaryResolution'], '0.01')

    def test_invalid_and_out_of_range_stored_values_are_safe(self):
        preferences = Preferences({'Resolution': 'nan', 'SearchStepPx': '-3', 'CurveTolerance': 'inf'})
        self.assertEqual(settings.read_search_settings(preferences), {
            'resolution': 1, 'bitmapSearchStepPx': 1, 'curveTolerance': .3, 'cacheRejects': True})
        settings.write_search_setting('resolution', 0, preferences)
        settings.write_search_setting('bitmapSearchStepPx', 999999, preferences)
        values = settings.read_search_settings(preferences)
        self.assertEqual(values['resolution'], .000001)
        self.assertEqual(values['bitmapSearchStepPx'], 100000)


if __name__ == '__main__':
    unittest.main()
