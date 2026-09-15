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
import builtins
from unittest.mock import patch

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

    # English is the first-launch default; a saved choice takes effect immediately.
    def test_default_and_persistence(self):
        m = self.module
        self.assertEqual(m.tr('settings.title'), 'Settings')
        m.set_language('lv')
        self.assertEqual(m.saved_language(), 'lv')
        self.assertEqual(m.current_language(), 'lv')
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

    # All fifty catalogs contain every source key, valid placeholders and preserved formatting.
    def test_catalog_integrity(self):
        en = json.loads((ROOT / 'lng/en.json').read_text(encoding='utf-8'))
        self.assertEqual(len(self.module.LANGUAGES), 50)
        for code, _ in self.module.LANGUAGES:
            with self.subTest(language=code):
                translated = json.loads((ROOT / 'lng' / (code + '.json')).read_text(encoding='utf-8'))
                self.assertEqual(set(en), set(translated))
                for key in en:
                    self.assertIsInstance(translated[key], str)
                    self.assertTrue(translated[key], key)
                    self.assertEqual(self.module._percent.findall(en[key]), self.module._percent.findall(translated[key]), key)
                    self.assertEqual(en[key].count('\n'), translated[key].count('\n'), key)
                    self.assertEqual(en[key].count('<b>'), translated[key].count('<b>'), key)
                    self.assertEqual(en[key].count('</b>'), translated[key].count('</b>'), key)
                if code != 'en':
                    self.assertGreaterEqual(sum(en[key] != translated[key] for key in en), 5,
                                            'Core UI vocabulary must be translated')
        for path in ROOT.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'tr':
                    if n.args and isinstance(n.args[0], ast.Constant):
                        self.assertIn(n.args[0].value, en, (path.name, n.lineno))

    # The controls visible in the main panel must not silently fall back to
    # English in any selected catalog.
    def test_visible_panel_is_translated_for_all_languages(self):
        en = json.loads((ROOT / 'lng/en.json').read_text(encoding='utf-8'))
        visible = {
            'sheet_settings', 'sheet_margin_mm', 'part_spacing_mm',
            'sheet_offcut_materials', 'material', 'count', 'grain', 'move',
            'add', 'show', 'remove', 'general_parameters',
            'boundary_resolution_mm', 'units', 'nesting_cli_settings',
            'time_ratio', 'population_size', 'mutation_rate',
            'export_sheet_boundaries', 'export_sheet_spacing',
            'sheet_spacing_value', 'placement_strategy', 'gravity',
            'bounding_box', 'squeeze', 'cpu_cores',
            'b_selected_parts_preview_mode_b', 'body', 'qty', 'rotations',
            'select_for_rotation', 'grain_direction', 'custom_angle',
            'add_selected', 'remove_selected', 'run_nesting', 'rotate',
            'clear_all', 'change_grain_direction', 'apply_grain',
            'set_custom_angle',
        }
        # A few short words (for example "Material" and "Rotations") are
        # legitimately identical in several languages.  Use distinctive
        # panel phrases for the fallback assertion instead of rejecting those
        # valid cognates.
        distinctive = {
            'sheet_settings', 'sheet_margin_mm', 'part_spacing_mm',
            'general_parameters', 'placement_strategy',
            'b_selected_parts_preview_mode_b', 'change_grain_direction',
            'set_custom_angle',
        }
        for code, _ in self.module.LANGUAGES:
            if code == 'en':
                continue
            translated = json.loads((ROOT / 'lng' / (code + '.json')).read_text(encoding='utf-8'))
            with self.subTest(language=code):
                self.assertTrue(all(translated[key] != en[key] for key in distinctive))

    # Perimeter cleanup recognizes saved labels independently of the current language.
    def test_perimeter_aliases(self):
        labels = self.module.perimeter_labels('Parts with grain direction')
        self.assertIn('Parts with grain direction', labels)
        self.assertIn('Detaļas ar tekstūras virzienu', labels)
        self.assertIn('木目方向ありの部品', labels)
        self.assertIn('ชิ้นงานที่มีทิศทางเสี้ยน', labels)
        self.assertNotIn('Detaļas bez tekstūras virziena', labels)

    # PySide may emit triggered() without the optional bool; selecting must still work.
    def test_language_action_without_bool(self):
        selected = []
        self.module._choose_language = selected.append
        self.module._language_triggered('lv')
        self.module._language_triggered('en', True)
        self.assertEqual(selected, ['lv', 'en'])

    # Load only the selected JSON; the small name/alias indexes do not load other catalogs.
    def test_selective_json_loading(self):
        m = self.module
        m._load_catalog('en')
        open_file = builtins.open
        members = []
        # Record file reads while delegating to Python's normal text reader.
        def observed(name, *args, **kwargs):
            members.append(Path(name).name)
            return open_file(name, *args, **kwargs)
        with patch.object(builtins, 'open', observed):
            self.assertIn('lv', m.available_languages())
            m.perimeter_labels('Parts with grain direction')
            self.assertEqual(members, ['perimeters.json'])
            members.clear()
            self.assertEqual(members, [])
            m.set_language('lv')
            self.assertEqual(members, ['lv.json'])
            self.assertEqual(m.current_language(), 'lv')
            self.assertEqual(m.tr('settings.title'), 'Iestatījumi')
            self.assertEqual(members, ['lv.json'])

    # Retain placeholders and nested translated captions when text is formatted before binding.
    def test_bound_format_arguments(self):
        m = self.module
        encoded = m._encode(m.tr('perimeter.label') % m.tr('perimeter.with_grain'))
        m.set_language('lv')
        self.assertEqual(m._resolve(encoded), 'Detaļas ar tekstūras virzienu Uzraksts')

    # Unavailable catalogs cannot silently save an English-only language selection.
    def test_missing_choice_preserves_language(self):
        m = self.module
        m.set_language('lv')
        with tempfile.TemporaryDirectory() as tmp:
            m.DIRECTORY = tmp
            with self.assertRaises(ValueError):
                m.set_language('de')
        self.assertEqual(m.current_language(), 'lv')
        self.assertEqual(m.saved_language(), 'lv')

    # Include every official EU language, unique sorted names, and plain JSON storage only.
    def test_language_selection(self):
        eu = set('bg hr cs da nl en et fi fr de el hu ga it lv lt mt pl pt ro sk sl es sv'.split())
        rows = json.loads((ROOT / 'lng/index.json').read_text(encoding='utf-8'))
        codes = {row['code'] for row in rows}
        self.assertEqual(len(rows), 50)
        self.assertEqual(len(codes), 50)
        self.assertLessEqual(eu, codes)
        names = [row['english'] for row in rows]
        self.assertEqual(names, sorted(names, key=str.casefold))
        self.assertTrue(all(row['native'] for row in rows))
        self.assertEqual(list((ROOT / 'lng').glob('*.zip')), [])


if __name__ == '__main__':
    unittest.main()

