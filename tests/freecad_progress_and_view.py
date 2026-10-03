"""Real Qt popup text plus explicit result-view fitting with a controlled GUI adapter."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import FreeCAD as App
import Part
from PySide import QtGui
import IPNestingLanguages as L
import IPNestingResult as R
from IPNestingWaitDialog import NestingWaitDialog

APPLICATION = QtGui.QApplication.instance() or QtGui.QApplication([])


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.language = patch.object(L, 'current_language', return_value='lv')
        self.language_mock = self.language.start()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.finish()
            dialog.deleteLater()
        APPLICATION.processEvents()
        self.language.stop()
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_timed_duration_and_live_language_switch(self):
        dialog = NestingWaitDialog(lambda: None)
        self.dialogs.append(dialog)
        for seconds, displayed in ((30, '30'), (30.5, '30.5'), (.125, '0.125')):
            dialog.set_nesting_mode('timed', seconds)
            self.assertEqual(dialog.message.text(), str(L.tr('nesting_wait_timed_seconds_s') % displayed))
        self.language_mock.return_value = 'en'
        L._refresh_widget(dialog.message)
        self.assertIn('0.125 seconds', dialog.message.text())
        self.assertNotIn('result.json', dialog.message.text())
        dialog.set_nesting_mode('first')
        self.assertEqual(dialog.message.text(), str(L.tr('waiting_for_result_json')))

    def test_continuous_uses_actual_translated_cancel_caption(self):
        cancelled = []
        dialog = NestingWaitDialog(lambda: cancelled.append(True))
        self.dialogs.append(dialog)
        dialog.set_nesting_mode('continuous', 30)
        self.assertIn('bez laika limita', dialog.message.text())
        self.assertIn(dialog.cancel_button.text(), dialog.message.text())
        self.assertNotIn('30', dialog.message.text())
        self.language_mock.return_value = 'en'
        L._refresh_widget(dialog.message)
        L._refresh_widget(dialog.cancel_button)
        self.assertIn('without a time limit', dialog.message.text())
        self.assertIn('“Cancel”', dialog.message.text())
        dialog.cancel_button.click()
        self.assertEqual(cancelled, [True])

    def test_popup_immediately_uses_exporter_time_budget_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            for value, displayed in (('30', '30'), ('30,5', '30.5'), ('bad', '60'), ('-1', '60'), ('100000', '86400')):
                panel = NS(form=QtGui.QWidget(), run_btn=QtGui.QPushButton(), stop_btn=QtGui.QPushButton(),
                           mode_combo=NS(currentText=lambda: 'timed'), time_limit_edit=NS(text=lambda: value))
                manager = R.NestingProcessManager(panel)
                manager._module_directory = lambda: folder
                self.assertTrue(manager.prepare_and_start(str(Path(folder) / 'input.json')))
                self.assertTrue(manager.wait_dialog.isVisible())
                self.assertEqual(manager.wait_dialog.message.text(), str(L.tr('nesting_wait_timed_seconds_s') % displayed))
                self.assertFalse((Path(folder) / 'input.json').exists())
                manager.stop_nesting()
                APPLICATION.processEvents()
                panel.form.deleteLater()

    def test_fit_all_targets_latest_result_after_activation_and_layout(self):
        preview = App.newDocument('FitPreview')
        result = dict(placed=0, sheets=[dict(points=[[0, 0], [10, 0], [10, 10], [0, 10]], parts=[]),
                                      dict(points=[[0, 0], [20, 0], [20, 10], [0, 10]], parts=[])])
        importer = R.NestingResultImporter(NS(preview_doc_name=preview.Name))
        events = []
        def activate(name):
            self.assertIn(name, App.listDocuments())
            events.append(('activate', name))
        def gui_document(name):
            return NS(activeView=lambda: NS(viewTop=lambda: events.append(('top', name)),
                                             fitAll=lambda: events.append(('fit', name))))
        with patch.object(R, 'Gui', NS(activateDocument=activate, getDocument=gui_document)):
            self.assertTrue(importer.import_result(result, show_summary=False))
            first = importer.result_doc.Name
            self.assertEqual(events[:3], [('activate', first), ('top', first), ('fit', first)])
            self.assertEqual(len(importer.sheet_groups), 2)
            self.assertTrue(importer.import_result(result, show_summary=False))
            latest = importer.result_doc.Name
            self.assertNotIn(first, App.listDocuments())
            APPLICATION.processEvents()
            self.assertEqual(events.count(('fit', first)), 1)
            self.assertEqual(events.count(('fit', latest)), 2)
            App.closeDocument(latest)
            importer._fit_result_view(latest)
            self.assertEqual(events.count(('fit', latest)), 2)

    def test_mode_captions_have_matching_placeholders_in_every_language(self):
        import json
        for path in (ROOT / 'lng').glob('*.json'):
            if path.stem in ('index', 'perimeters'): continue
            catalog = json.loads(path.read_bytes())
            for key in ('nesting_wait_timed_seconds_s', 'nesting_wait_continuous_cancel_s'):
                with self.subTest(language=path.stem, key=key):
                    self.assertEqual(catalog[key].count('%s'), 1)
                    self.assertTrue(catalog[key] % '30')


if __name__ == '__main__':
    unittest.main(verbosity=2)
