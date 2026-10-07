"""Real Qt table and FreeCAD geometry regression tests for multi-file DXF import."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import json
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
import FreeCAD as App
import Import
from PySide import QtGui, QtCore
sys.path.insert(0, str(ROOT))
import IPNestingGui as G
import IPNestingImport2D as I
assert Path(G.__file__).resolve() == ROOT / 'IPNestingGui.py'

APPLICATION = QtGui.QApplication.instance() or QtGui.QApplication([])
# Disposable fixtures keep this regression independent of external sample files.
FIXTURES = tempfile.TemporaryDirectory(prefix='ipnesting batch dxf ')
FILES = []
for number in range(154):
    path = Path(FIXTURES.name) / f'part_{number:03d}.dxf'
    pairs = [(0, 'SECTION'), (2, 'HEADER'), (9, '$ACADVER'), (1, 'AC1015'),
             (9, '$INSUNITS'), (70, 4), (0, 'ENDSEC'), (0, 'SECTION'),
             (2, 'ENTITIES'), (0, 'LWPOLYLINE'), (100, 'AcDbEntity'),
             (8, '0'), (100, 'AcDbPolyline'), (90, 4), (70, 1)]
    for x, y in [(0, 0), (10 + number, 0), (10 + number, 20), (0, 20)]:
        pairs.extend([(10, x), (20, y)])
    pairs.extend([(0, 'ENDSEC'), (0, 'EOF')])
    path.write_text(''.join(f'{code}\n{value}\n' for code, value in pairs), encoding='ascii')
    FILES.append(path)


class MultiDxfTests(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument('BatchImportTest')
        self.form = QtGui.QWidget()
        table = QtGui.QTableWidget(1, 6, self.form)
        table.setItem(0, 0, QtGui.QTableWidgetItem('CONTROL'))
        self.panel = SimpleNamespace(
            form=self.form, table=table, control_rows=1, added_count=0,
            preview_doc_name=self.doc.Name, ensure_preview_doc=Mock(return_value=self.doc),
            get_default_rotations=lambda: 360, _suppress_qty_update=False,
            update_grain_layout_and_perimeters=Mock(), _fit_all_views=Mock(),
            _update_apply_blink_state=Mock())
        self.panel._add_preview_object_to_table = lambda doc, name: G.NestingTaskPanel._add_preview_object_to_table(self.panel, doc, name)
        self.changed = Mock()
        table.itemChanged.connect(self.changed)
        self.patches = [
            patch.object(G.Gui, 'setActiveDocument', create=True),
            patch.object(G.Gui, 'SendMsgToActiveView', create=True),
            patch.object(I, '_import_dxf', side_effect=self.native_import),
            patch.object(QtGui.QMessageBox, 'exec_', return_value=QtGui.QMessageBox.Ok),
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.form.close()
        App.closeDocument(self.doc.Name)

    @staticmethod
    def native_import(path, document_name):
        # Exercise actual DXF parsing without a running FreeCAD GUI/import preferences.
        Import.readDXF(path, document_name, False)
        return True

    def run_import(self, paths):
        with patch.object(QtGui.QFileDialog, 'getOpenFileNames', return_value=([str(p) for p in paths], 'DXF')) as dialog:
            G.NestingTaskPanel.import_dxf_2d(self.panel)
            dialog.assert_called_once()

    def test_154_files_create_154_independent_rows(self):
        self.assertEqual(len(FILES), 154)
        self.run_import(FILES)
        table = self.panel.table
        self.assertEqual(table.rowCount(), 155)
        self.assertEqual(table.item(154, 0).text(), 'CONTROL')
        self.assertEqual(self.panel.added_count, 154)
        names = set()
        for row, path in enumerate(FILES):
            item = table.item(row, 0)
            self.assertIn(path.name, item.text())
            name = item.data(QtCore.Qt.UserRole)
            names.add(name)
            self.assertEqual(json.loads(item.data(QtCore.Qt.UserRole + 1)), [name])
            shape = self.doc.getObject(name).Shape
            self.assertTrue(shape.isValid())
            self.assertGreater(len(shape.Edges), 2)
            self.assertEqual(table.item(row, 1).text(), '1')
            self.assertEqual(table.item(row, 2).text(), '360')
        self.assertEqual(len(names), 154)
        self.panel.update_grain_layout_and_perimeters.assert_called_once()
        self.panel._fit_all_views.assert_called_once()
        self.changed.assert_not_called()
        self.mocks[-1].assert_not_called()
        self.assertFalse(table.signalsBlocked())
        self.assertTrue(table.updatesEnabled())
        self.assertFalse(self.panel._suppress_qty_update)

    def test_second_batch_appends_and_retains_existing_parts(self):
        self.run_import(FILES[:1])
        first_name = self.panel.table.item(0, 0).data(QtCore.Qt.UserRole)
        self.run_import(FILES[1:3])
        self.assertEqual(self.panel.table.rowCount(), 4)
        self.assertEqual(self.panel.table.item(0, 0).data(QtCore.Qt.UserRole), first_name)
        self.assertIsNotNone(self.doc.getObject(first_name))
        self.assertEqual(self.panel.table.item(3, 0).text(), 'CONTROL')

    def test_cancel_does_not_change_table_or_create_preview(self):
        self.run_import([])
        self.panel.ensure_preview_doc.assert_not_called()
        self.assertEqual(self.panel.table.rowCount(), 1)
        self.panel.update_grain_layout_and_perimeters.assert_not_called()

    def test_bad_files_do_not_prevent_later_files_and_report_once(self):
        real = G.import_dxf_to_preview
        def importer(panel, path, **kwargs):
            if path.endswith('raises.dxf'):
                raise ValueError('Deliberate invalid-file regression')
            return real(panel, path, **kwargs)
        with patch.object(G, 'import_dxf_to_preview', side_effect=importer):
            self.run_import([FILES[0], ROOT / 'missing.dxf', ROOT / 'raises.dxf', FILES[1]])
        self.assertEqual(self.panel.table.rowCount(), 3)
        self.assertEqual(self.panel.added_count, 2)
        self.mocks[-1].assert_called_once()
        self.panel.update_grain_layout_and_perimeters.assert_called_once()

    def test_all_failed_restores_preexisting_ui_state(self):
        self.panel.table.blockSignals(True)
        self.panel.table.setUpdatesEnabled(False)
        self.panel._suppress_qty_update = True
        self.run_import([ROOT / 'missing.dxf'])
        self.assertTrue(self.panel.table.signalsBlocked())
        self.assertFalse(self.panel.table.updatesEnabled())
        self.assertTrue(self.panel._suppress_qty_update)
        self.assertEqual(self.panel.table.rowCount(), 1)
        self.panel.update_grain_layout_and_perimeters.assert_not_called()
        self.mocks[-1].assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
