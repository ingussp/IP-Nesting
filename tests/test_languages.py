"""Run with Python; GUI tests additionally use FreeCAD's bundled Python."""
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


# Emulate preferences without changing the user's actual FreeCAD configuration.
class Preferences:
    # Start with an empty preference store, as on first launch.
    def __init__(self):
        self.values = {}

    # Return saved strings or the supplied default.
    def GetString(self, key, default=""):
        return self.values.get(key, default)

    # Record a selected language in the test store.
    def SetString(self, key, value):
        self.values[key] = value


# Check catalog coverage, fallback, placeholders and persistent language selection.
class LanguageTests(unittest.TestCase):
    # Load a fresh isolated language module against in-memory preferences.
    def setUp(self):
        self.preferences = Preferences()
        previous = sys.modules.get('FreeCAD')
        sys.modules['FreeCAD'] = types.SimpleNamespace(ParamGet=lambda _: self.preferences)
        try:
            spec = importlib.util.spec_from_file_location('languages_under_test', ROOT / 'IPNestingLanguages.py')
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        finally:
            if previous is None:
                sys.modules.pop('FreeCAD', None)
            else:
                sys.modules['FreeCAD'] = previous

    # English is the first-launch default; saving Latvian takes effect next session.
    def test_default_and_persistence(self):
        m = self.module
        self.assertEqual(m.tr('settings.title'), 'Settings')
        m.set_language('lv')
        self.assertEqual(m.saved_language(), 'lv')
        self.assertEqual(m.current_language(), 'en')
        m._active_language = None
        self.assertEqual(m.tr('settings.title'), 'Iestatījumi')
        m.set_language('en')
        m._active_language = None
        self.assertEqual(m.tr('settings.title'), 'Settings')

    # Invalid preferences and missing or invalid translations safely fall back to English.
    def test_fallbacks(self):
        m = self.module
        self.preferences.values['Language'] = '../bad'
        self.assertEqual(m.current_language(), 'en')
        m._active_language = 'lv'
        m._catalogs['lv'] = {'sheet_d': 'Loksne %s', 'settings.title': ''}
        self.assertEqual(m.tr('sheet_d'), 'Sheet %d')
        self.assertEqual(m.tr('settings.title'), 'Settings')
        self.assertEqual(m.tr('missing.key'), 'missing.key')
        with self.assertRaises(ValueError):
            m.set_language('../bad')

    # Corrupt or missing optional catalogs never prevent opening the application.
    def test_corrupt_catalog(self):
        m = self.module
        english = dict(m._load_catalog('en'))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'lng'
            folder.mkdir()
            (folder / 'lv.json').write_text('{broken', encoding='utf-8')
            m.DIRECTORY = tmp
            m._catalogs = {'en': english}
            m._active_language = 'lv'
            self.assertEqual(m.tr('settings.title'), 'Settings')

    # Every translated source key exists, both languages are complete and formats match.
    def test_catalog_integrity(self):
        en = json.loads((ROOT / 'lng/en.json').read_text(encoding='utf-8'))
        lv = json.loads((ROOT / 'lng/lv.json').read_text(encoding='utf-8'))
        self.assertEqual(set(en), set(lv))
        for key in en:
            self.assertTrue(lv[key], key)
            self.assertEqual(self.module._percent.findall(en[key]), self.module._percent.findall(lv[key]), key)
            self.assertEqual(en[key].count('\n'), lv[key].count('\n'), key)
        for path in ROOT.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'tr':
                    if n.args and isinstance(n.args[0], ast.Constant):
                        self.assertIn(n.args[0].value, en, (path.name, n.lineno))

    # Perimeter cleanup recognizes saved labels independently of the current language.
    def test_perimeter_aliases(self):
        labels = self.module.perimeter_labels('Parts with grain direction')
        self.assertIn('Parts with grain direction', labels)
        self.assertIn('Detaļas ar tekstūras virzienu', labels)
        self.assertNotIn('Detaļas bez tekstūras virziena', labels)

    # PySide may emit triggered() without the optional bool; selecting must still work.
    def test_language_action_without_bool(self):
        selected = []
        self.module._choose_language = selected.append
        self.module._language_triggered('lv')
        self.module._language_triggered('en', True)
        self.assertEqual(selected, ['lv', 'en'])


if __name__ == '__main__':
    unittest.main()
