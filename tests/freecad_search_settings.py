"""Exercise translated modes, the Settings menu and real input.json export."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import json
import tempfile
import types
import unittest
from unittest.mock import patch
import FreeCAD as App
import Part
from PySide import QtGui, QtCore
import FreeCADGui as Gui

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import IPNestingLanguages as L
import IPNestingGui as G
import IPNestingExport as E
from IPNestingWaitDialog import NestingWaitDialog

APPLICATION = QtGui.QApplication.instance() or QtGui.QApplication([])
QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
APPLICATION.setFont(QtGui.QFont('Segoe UI', 9))


class Preferences:
    def __init__(self):
        self.values = {'Language': 'en', 'SearchMode': 'timed', 'TimeLimitSeconds': '30',
                       'Resolution': '0.25', 'SearchStepPx': '4', 'CacheRejects': False,
                       'CurveTolerance': '0.05', 'BoundaryResolution': '0.01'}
    def GetString(self, key, default=''): return self.values.get(key, default)
    def SetString(self, key, value): self.values[key] = value
    def GetBool(self, key, default=False): return self.values.get(key, default)
    def SetBool(self, key, value): self.values[key] = value
    def GetInt(self, key, default=0): return self.values.get(key, default)
    def SetInt(self, key, value): self.values[key] = value


class SearchSettingsIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='search-settings-')
        self.folder = Path(self.temp.name)
        self.preferences = Preferences()
        self.parent = QtGui.QWidget()
        self.patches = [
            patch.object(G.NestingTaskPanel, '_prefs', return_value=self.preferences),
            patch.object(L, 'App', types.SimpleNamespace(
                ParamGet=lambda _: self.preferences, listDocuments=App.listDocuments,
                getUserCachePath=lambda: str(self.folder))),
            patch.object(Gui, 'getMainWindow', return_value=self.parent, create=True),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        L._active_language = 'en'
        self.panels = []
        self.panel = self.make_panel()
        self.document = App.newDocument('SearchSettingsTest')

    def make_panel(self):
        panel = G.NestingTaskPanel()
        self.panels.append(panel)
        return panel

    def tearDown(self):
        if L._menu is not None:
            L._menu.close()
            L._menu.deleteLater()
            L._menu = None
        for panel in self.panels:
            panel._nesting_manager.shutdown()
            panel.form.close()
            panel.form.deleteLater()
        App.closeDocument(self.document.Name)
        self.parent.close()
        self.parent.deleteLater()
        # Finish Qt destruction before the next test enumerates allWidgets().
        APPLICATION.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        APPLICATION.processEvents()
        for item in reversed(self.patches): item.stop()
        L._active_language = None
        self.temp.cleanup()

    def test_all_languages_preserve_mode_codes_and_enable_correct_budgets(self):
        panel = self.panel
        expected = [('first', 'mode.fast_first', False, False),
                    ('timed', 'mode.timed', True, True),
                    ('continuous', 'mode.continuous', False, True)]
        for language, _ in L.LANGUAGES:
            L.set_language(language)
            for index, (code, key, timed, rounds) in enumerate(expected):
                with self.subTest(language=language, mode=code):
                    panel.mode_combo.setCurrentIndex(index)
                    self.assertEqual(panel.mode_combo.currentText(), str(L.tr(key)))
                    self.assertNotIn(':', panel.mode_combo.currentText())
                    self.assertEqual(panel.mode_combo.currentData(), code)
                    self.assertEqual(self.preferences.values['SearchMode'], code)
                    self.assertEqual(E._read_search_mode(panel), code)
                    self.assertEqual(panel.time_limit_edit.isEnabled(), timed)
                    self.assertEqual(panel.time_limit_label.isEnabled(), timed)
                    self.assertEqual(panel.round_seconds_edit.isEnabled(), rounds)
                    self.assertEqual(panel.round_seconds_label.isEnabled(), rounds)
                    self.assertEqual(panel.trials_combo.isEnabled(), rounds)
                    self.assertEqual(panel.trials_label.isEnabled(), rounds)
        # Restoring a saved identifier works with a translated display caption.
        L.set_language('lv')
        self.preferences.values['SearchMode'] = 'timed'
        restored = self.make_panel()
        self.assertEqual(restored.mode_combo.currentData(), 'timed')
        self.assertEqual(restored.mode_combo.currentText(), 'Ar laika limitu')

    def test_strategy_captions_preserve_counts_preferences_and_fast_mode_selection(self):
        panel = self.panel
        keys = ('trials.compact', 'trials.compact_holes', 'trials.compact_holes_large', 'trials.all')
        self.assertEqual(panel.trials_combo.currentData(), 2)
        for language, _ in L.LANGUAGES:
            L.set_language(language)
            for index, key in enumerate(keys):
                with self.subTest(language=language, trials=index + 1):
                    panel.trials_combo.setCurrentIndex(index)
                    self.assertEqual(panel.trials_combo.currentText(), str(L.tr(key)))
                    self.assertEqual(panel.trials_combo.currentData(), index + 1)
                    self.assertEqual(E._read_combo_int(panel, 'trials_combo', 2), index + 1)
                    self.assertEqual(self.preferences.values['Trials'], str(index + 1))
                    panel.mode_combo.setCurrentIndex(0)
                    self.assertFalse(panel.trials_combo.isEnabled())
                    self.assertEqual(panel.trials_combo.currentData(), index + 1)
                    panel.mode_combo.setCurrentIndex(2)
                    self.assertTrue(panel.trials_combo.isEnabled())
                    self.assertEqual(panel.trials_combo.currentData(), index + 1)
        L.set_language('lv')
        self.preferences.values['Trials'] = '3'
        restored = self.make_panel()
        self.assertEqual(restored.trials_combo.currentData(), 3)
        self.assertEqual(restored.trials_combo.currentText(), str(L.tr(keys[2])))
        restored.mode_combo.setCurrentIndex(0)
        restored.form.resize(1100, 850)
        APPLICATION.processEvents()
        output = ROOT.parent / 'ip-search-settings-tests'
        output.mkdir(exist_ok=True)
        restored.form.grab().save(str(output / 'main-panel-fast-lv.png'))
        restored.mode_combo.setCurrentIndex(1)
        APPLICATION.processEvents()
        restored.form.grab().save(str(output / 'main-panel-strategies-lv.png'))

    def test_strategy_caption_length_does_not_widen_settings_column(self):
        panel = self.panel
        panel.form.resize(1100, 850)
        panel.form.show()
        L.set_language('lv')
        panel.trials_combo.setCurrentIndex(2)
        APPLICATION.processEvents()
        combo = panel.trials_combo
        right = combo.parentWidget()
        left = panel.offcuts_table.parentWidget()
        self.assertLess(right.width(), left.width() * 0.6)
        original = combo.itemText(2)
        original_width = right.width()
        combo.setItemText(2, original * 4)
        panel.form.layout().activate()
        APPLICATION.processEvents()
        self.assertEqual(right.width(), original_width)
        combo.setItemText(2, original)
        combo.showPopup()
        APPLICATION.processEvents()
        longest = max(combo.fontMetrics().boundingRect(combo.itemText(i)).width()
                      for i in range(combo.count()))
        self.assertGreaterEqual(combo.view().viewport().width(), longest + 8)
        output = ROOT.parent / 'ip-search-settings-tests'
        combo.view().parentWidget().grab().save(str(output / 'strategy-dropdown-lv.png'))
        combo.hidePopup()
        APPLICATION.processEvents()
        panel.form.grab().save(str(output / 'main-panel-width-lv.png'))
        print('Settings columns (left/right): %d/%d px' % (left.width(), right.width()))

    def test_settings_menu_order_live_values_and_real_json_export(self):
        panel = self.panel
        for attr in ('resolution_edit', 'step_edit', 'curve_edit', 'cache_combo'):
            self.assertFalse(hasattr(panel, attr), attr)
        L.set_language('lv')
        L.show_settings()
        menu = L._menu
        APPLICATION.processEvents()
        self.assertEqual([action._label.text() for action in menu.actions() if hasattr(action, '_label')],
                         [str(L.tr(key)) for key in ('default_rotations', 'boundary_resolution_mm',
                          'resolution_mm_per_px', 'bitmap_search_step_px', 'cache_rejects',
                          'contact_simplification_mm')])
        self.assertEqual(menu._bitmap_resolution_spin.value(), .25)
        self.assertEqual(menu._search_step_spin.value(), 4)
        self.assertEqual(menu._cache_combo.currentIndex(), 0)
        self.assertEqual(menu._curve_spin.value(), .05)
        menu._bitmap_resolution_spin.setValue(.125)
        menu._search_step_spin.setValue(7)
        menu._cache_combo.setCurrentIndex(1)
        menu._curve_spin.setValue(.08)
        self.assertEqual(menu._resolution_spin.value(), .01)
        panel._save_settings_to_prefs()  # Other panel edits must not overwrite menu values.
        self.assertEqual(panel.get_search_settings(), {
            'resolution': .125, 'bitmapSearchStepPx': 7, 'cacheRejects': True, 'curveTolerance': .08})
        output = ROOT.parent / 'ip-search-settings-tests'
        output.mkdir(exist_ok=True)
        menu.grab().save(str(output / 'settings-menu-lv.png'))
        panel.form.resize(1100, 850)
        APPLICATION.processEvents()
        panel.form.grab().save(str(output / 'main-panel-lv.png'))
        menu.close()

        part = self.document.addObject('Part::Feature', 'Original')
        part.Shape = Part.makeBox(10, 20, 3)
        panel.preview_doc_name = self.document.Name
        panel.offcuts = [dict(type='rectangular', outer=[[0, 0], [100, 0], [100, 100], [0, 100]], quantity=1)]
        panel.table.insertRow(0)
        item = QtGui.QTableWidgetItem('Original')
        item.setData(QtCore.Qt.UserRole, 'Original')
        panel.table.setItem(0, 0, item)
        panel.table.setItem(0, 1, QtGui.QTableWidgetItem('1'))
        panel.table.setItem(0, 2, QtGui.QTableWidgetItem('4'))
        errors = []
        with patch.object(E, '__file__', str(self.folder / 'IPNestingExport.py')), \
             patch.object(QtGui.QMessageBox, 'warning', side_effect=lambda *args: errors.append(args)):
            for index, code in enumerate(('first', 'timed', 'continuous')):
                panel.mode_combo.setCurrentIndex(index)
                self.assertTrue(E.execute_nesting(panel), errors)
                payload = json.loads((self.folder / 'input.json').read_text(encoding='utf-8'))
                config = payload['config']
                self.assertEqual(config['mode'], code)
                self.assertEqual(config['timeLimitSeconds'], 30 if code == 'timed' else 0)
                self.assertEqual(config['resolution'], .125)
                self.assertEqual(config['bitmapSearchStepPx'], 7)
                self.assertEqual(config['cacheRejects'], True)
                self.assertEqual(config['curveTolerance'], .08)
                self.assertEqual(self.preferences.values['BoundaryResolution'], '0.01')
                for trials_index in range(4):
                    panel.trials_combo.setCurrentIndex(trials_index)
                    self.assertTrue(E.execute_nesting(panel), errors)
                    config = json.loads((self.folder / 'input.json').read_text(encoding='utf-8'))['config']
                    self.assertEqual(config['mode'], code)
                    self.assertEqual(config['trials'], trials_index + 1)
                    self.assertIsInstance(config['trials'], int)
        L.show_settings()
        self.assertEqual(L._menu._bitmap_resolution_spin.value(), .125)
        self.assertEqual(L._menu._search_step_spin.value(), 7)
        self.assertEqual(L._menu._curve_spin.value(), .08)
        # The initial popup message reads the same stable code as input export.
        panel.mode_combo.setCurrentIndex(1)
        dialog = NestingWaitDialog(lambda: None, parent=panel.form)
        panel._nesting_manager.wait_dialog = dialog
        panel._nesting_manager._update_wait_message()
        self.assertEqual(dialog.message.text(), str(L.tr('nesting_wait_timed_seconds_s') % '30'))
        dialog.close()


if __name__ == '__main__':
    unittest.main()
