"""FreeCAD Python: real editor, export, unmodified CLI and separate 3D result parts."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import FreeCAD as App
import Part
from PySide import QtCore, QtGui
# FreeCAD initialization prepends the installed workbenches to sys.path.
sys.path.insert(0, str(ROOT))
import IPNestingExport as E
import IPNestingResult as R
import IPNestingGrainMatch as G
from IPNestingGrainMatchModel import solve, edges, polygon, pack_groups, outer_contour
from test_grain_match_model import cabinet

APPLICATION = QtGui.QApplication.instance() or QtGui.QApplication([])
if os.name == 'nt':
    QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    APPLICATION.setFont(QtGui.QFont('Segoe UI', 10))


class GrainIntegrationTests(unittest.TestCase):
    def tearDown(self):
        for name in list(App.listDocuments()):
            App.closeDocument(name)

    def test_dialog_and_exact_spacing_validation(self):
        parts, definition = cabinet()
        for i, part in enumerate(parts):
            part['label'] = ['Door', 'Drawer 1', 'Drawer 2', 'Drawer 3'][i]
        dialog = G.GrainMatchingDialog(parts, 12)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(dialog.views[1].scene().items(), [])
        for pair, values in zip(dialog.pairs, definition['links']):
            for combo, value in zip(pair, values):
                combo.setCurrentIndex(combo.findData(value))
        self.assertTrue(dialog.save_button.isEnabled(), dialog.status.text())
        dialog.show(); APPLICATION.processEvents(); dialog.fit_views(); APPLICATION.processEvents()
        screenshot = os.environ.get('GRAIN_MATCH_SCREENSHOT')
        if screenshot:
            self.assertTrue(dialog.grab().save(screenshot))
        G.validate_layout(parts, dialog.poses, 12)
        with self.assertRaises(ValueError):
            G.validate_layout(parts, dialog.poses, 13)
        with self.assertRaises(ValueError):
            G.validate_layout(parts, [[0, 0, 0]]*4, 0)
        dialog.close()

    def test_real_panel_button_saves_edits_and_removes_group(self):
        from IPNestingGui import NestingTaskPanel
        panel = NestingTaskPanel()
        doc = App.newDocument('Editor_Preview'); panel.preview_doc_name = doc.Name
        parts, definition = cabinet()
        panel.table.blockSignals(True)
        for i, part in enumerate(parts):
            obj = doc.addObject('Part::Feature', 'Front%d' % i)
            obj.Shape = Part.makeBox(100, [200, 40, 40, 40][i], 18)
            panel.table.insertRow(i)
            item = QtGui.QTableWidgetItem(obj.Name); item.setData(QtCore.Qt.UserRole, obj.Name)
            panel.table.setItem(i, 0, item)
            panel.table.setItem(i, 1, QtGui.QTableWidgetItem('1'))
            panel.table.setItem(i, 2, QtGui.QTableWidgetItem('4'))
        panel.table.blockSignals(False); doc.recompute()
        for row in range(4):
            panel.table.selectionModel().select(panel.table.model().index(row, 0),
                QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        self.assertEqual(panel.control_rows, 4)
        self.assertTrue(panel.table.cellWidget(7, 0).isAncestorOf(panel.match_grain_btn))
        original_exec = G.GrainMatchingDialog.exec_
        def fill_and_save(dialog):
            for pair, values in zip(dialog.pairs, definition['links']):
                for combo, value in zip(pair, values):
                    combo.setCurrentIndex(combo.findData(value))
            QtCore.QTimer.singleShot(0, dialog.save_button.click)
            return original_exec(dialog)
        errors = []
        with patch.object(G.GrainMatchingDialog, 'exec_', fill_and_save), \
             patch.object(QtGui.QMessageBox, 'warning', side_effect=lambda *a: errors.append(a[-1])):
            panel.match_grain_btn.click()
        self.assertFalse(errors)
        saved = G.read_groups(doc)[0]
        self.assertEqual({key: saved[key] for key in definition}, definition)
        self.assertEqual(len(saved['matching_edges']), 4)
        panel.table.clearSelection(); panel.table.selectRow(1)
        # Even if quantities no longer match, the user can remove the group.
        panel.table.blockSignals(True); panel.table.item(0, 1).setText('2'); panel.table.blockSignals(False)
        def remove(dialog):
            self.assertEqual(len(dialog.parts), 4)
            self.assertFalse(dialog.save_button.isEnabled())
            QtCore.QTimer.singleShot(0, dialog.remove_button.click)
            return original_exec(dialog)
        with patch.object(G.GrainMatchingDialog, 'exec_', remove):
            G.open_editor(panel)
        self.assertEqual(G.read_groups(doc), [])
        panel.form.close(); panel.form.deleteLater()

    def test_group_row_is_a_full_part_row(self):
        from IPNestingGui import NestingTaskPanel
        panel = NestingTaskPanel()
        doc = App.newDocument('Editor_Group_Row'); panel.preview_doc_name = doc.Name
        parts, definition = cabinet()
        panel.table.blockSignals(True)
        for i, part in enumerate(parts):
            obj = doc.addObject('Part::Feature', 'Front%d' % i)
            obj.Shape = Part.makeBox(100, [200, 40, 40, 40][i], 18)
            panel.table.insertRow(i)
            item = QtGui.QTableWidgetItem(obj.Name); item.setData(QtCore.Qt.UserRole, obj.Name)
            panel.table.setItem(i, 0, item)
            panel.table.setItem(i, 1, QtGui.QTableWidgetItem('1'))
            panel.table.setItem(i, 2, QtGui.QTableWidgetItem('4'))
        panel.table.blockSignals(False); doc.recompute()
        for row in range(4):
            panel.table.selectionModel().select(panel.table.model().index(row, 0),
                QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows)
        original_exec = G.GrainMatchingDialog.exec_
        def fill_and_save(dialog):
            for pair, values in zip(dialog.pairs, definition['links']):
                for combo, value in zip(pair, values):
                    combo.setCurrentIndex(combo.findData(value))
            QtCore.QTimer.singleShot(0, dialog.save_button.click)
            return original_exec(dialog)
        with patch.object(G.GrainMatchingDialog, 'exec_', fill_and_save):
            panel.match_grain_btn.click()
        group_row = None
        for row in range(panel.table.rowCount() - panel.control_rows):
            if G.row_kind(panel, row) == G.GROUP_ROW_KIND:
                group_row = row
                break
        self.assertIsNotNone(group_row)
        name = panel.table.item(group_row, 0).text()
        self.assertTrue(name.startswith('Texture matched parts:'), name)
        self.assertIn('Front0', name)
        self.assertIn('Front3', name)
        self.assertEqual(panel.table.item(group_row, 1).text(), '1')
        self.assertEqual(panel.table.item(group_row, 2).text(), '4')
        for col in (3, 4, 5):
            widget = panel.table.cellWidget(group_row, col)
            self.assertIsNotNone(widget)
            self.assertTrue(widget.findChildren(QtGui.QCheckBox), col)
        panel.form.close(); panel.form.deleteLater()

    def test_curved_cad_boundaries_partial_preview_and_cycle(self):
        doc = App.newDocument('Curved_Matching')
        parts = []
        for i in range(3):
            obj = doc.addObject('Part::Feature', 'Curved%d' % i)
            obj.Shape = Part.makeBox(434, 344, 18).cut(Part.makeCylinder(130, 18, App.Vector(217, 420, 0)))
            native = []
            candidates = E._extract_part_candidate_wires(obj, .01, native_wires=native)
            points = E._extract_part_points(obj, candidates=candidates)
            boundary = G.boundaries_from_wire(points, native[0], [0, 0])
            self.assertGreater(len(points), 50)
            self.assertEqual(len(boundary), 6)
            self.assertEqual(sum(e['curved'] for e in boundary), 1)
            parts.append(dict(points=points, matching_edges=boundary, label=obj.Name, quantity=1,
                              id='part_%d' % i, rotations=4,
                              _ip_nesting=dict(preview_object_name=obj.Name, source_part_index=i, job_id='curved')))
        dialog = G.GrainMatchingDialog(parts, 12)
        self.assertIn('texture', dialog.windowTitle().lower())
        self.assertFalse(dialog.views[1].scene().items())
        def choose(row, pair):
            for combo, number in zip(dialog.pairs[row], pair):
                combo.setCurrentIndex(combo.findData(number))
        choose(0, [2, 10])
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn('2 of 3', dialog.status.text())
        self.assertEqual(sum(isinstance(x, QtGui.QGraphicsPolygonItem) for x in dialog.views[1].scene().items()), 2)
        choose(1, [8, 4])  # Reconnect the same two parts; third is missing.
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn('Curved2', dialog.status.text())
        self.assertIn('cycle', dialog.status.text())
        self.assertTrue(dialog.views[1].scene().items())
        choose(1, [8, 16])
        self.assertTrue(dialog.save_button.isEnabled(), dialog.status.text())
        definition = dict(names=[p['label'] for p in parts], outlines=[polygon(p['points']) for p in parts],
                          matching_edges=[p['matching_edges'] for p in parts], links=dialog.links())
        # Export receives freshly extracted parts without the editor-only boundaries.
        original = [{k: v for k, v in p.items() if k != 'matching_edges'} for p in parts]
        packed, recipes = pack_groups(original, [definition], 12, G.validate_layout)
        self.assertEqual(len(packed), 1)
        self.assertEqual(len(recipes[0]['members']), 3)
        self.assertNotIn('matching_edges', json.dumps(packed))
        self.assertEqual(recipes[0]['members'][0]['points'], original[0]['points'])
        # Legacy curve-fragment links require reselection instead of silent renumbering.
        legacy = edges([dict(points=p['points']) for p in parts])
        old_second_bottom = next(e['number'] for e in legacy if e['part']==1 and e['a'][1]==0 and e['b'][1]==0)
        self.assertEqual(G.migrate_links(parts, [[2, old_second_bottom]]), [[2, 8]])
        with self.assertRaisesRegex(ValueError, 'straight edges'):
            solve(parts, [[2, 11], [8, 16]], 12)
        dialog.close()

    def test_export_cli_and_import_repeated_rotated_fronts(self):
        parts, definition = cabinet(2)
        doc = App.newDocument('Matching_Preview')
        table = QtGui.QTableWidget(4, 3)
        for i, part in enumerate(parts):
            obj = doc.addObject('Part::Feature', 'Front%d' % i)
            obj.Shape = Part.makeBox(100, [200, 40, 40, 40][i], 18)
            if i == 0:
                obj.Shape = obj.Shape.cut(Part.makeCylinder(5, 18, App.Vector(20, 20, 0)))
            # Verify normalization also handles offset source geometry.
            obj.Placement.Base = App.Vector(500+i*150, 500, 10)
            item = QtGui.QTableWidgetItem(obj.Name); item.setData(QtCore.Qt.UserRole, obj.Name)
            table.setItem(i, 0, item)
            table.setItem(i, 1, QtGui.QTableWidgetItem('2'))
            table.setItem(i, 2, QtGui.QTableWidgetItem('4'))
        doc.recompute()
        G.write_groups(doc, [definition])
        form = QtGui.QWidget()
        panel = NS(form=form, preview_doc_name=doc.Name, table=table, control_rows=0,
                   spacing=12, sheet_margin=0, get_dimension_value_mm=lambda value, default: value,
                   get_boundary_resolution_mm=lambda: .01,
                   offcuts=[dict(type='rectangular', outer=[[0, 0], [740, 0], [740, 120], [0, 120]], quantity=1)],
                   mode_combo=NS(currentText=lambda: 'first'), time_limit_edit=NS(text=lambda: '1'),
                   round_seconds_edit=NS(text=lambda: '1'), cpu_cores_combo=NS(currentText=lambda: '2'))
        with tempfile.TemporaryDirectory(prefix='grain matching ') as tmp:
            folder = Path(tmp)
            doc.saveAs(str(folder / 'preview.FCStd'))
            name = doc.Name; App.closeDocument(name)
            doc = App.openDocument(str(folder / 'preview.FCStd'))
            panel.preview_doc_name = doc.Name
            self.assertEqual(G.read_groups(doc), [definition])
            errors = []
            with patch.object(E, '__file__', str(folder / 'IPNestingExport.py')), \
                 patch.object(QtGui.QMessageBox, 'warning', side_effect=lambda *a: errors.append(a[-1])), \
                 patch.object(QtGui.QMessageBox, 'critical', side_effect=lambda *a: errors.append(a[-1])):
                self.assertTrue(E.execute_nesting(panel), errors)
            raw = json.loads((folder / 'input.json').read_text(encoding='utf-8'))
            session = json.loads((folder / 'nesting_session.json').read_text(encoding='utf-8'))
            self.assertEqual(len(raw['parts']), 1)
            self.assertEqual(len(session['parts']), 4)
            for word in ('grain_matching', 'members', 'Front0', 'pose'):
                self.assertNotIn(word, json.dumps(raw['parts']))
            exe = Path(os.environ.get('CLINESTING_EXE', str(ROOT / 'clinesting/windows-x86_64/clinesting.exe')))
            run = subprocess.run([str(exe), '--input', str(folder / 'input.json')], cwd=tmp,
                                 capture_output=True, text=True, timeout=45)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
            result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['placed'], 2, result)
            self.assertTrue(all(p['rotation'] in (90, 270) for p in result['sheets'][0]['parts']))
            normalized = R._normalize_result(result, session)
            self.assertEqual(normalized['placed'], 8)
            importer = R.NestingResultImporter(panel)
            self.assertTrue(importer.import_result(result, session, show_summary=False))
            objects = [o for o in importer.result_doc.Objects if o.Name.startswith('Nesting_')]
            self.assertEqual(len(objects), 8)
            self.assertAlmostEqual(objects[0].Shape.Volume, (20000-25*3.141592653589793)*18, places=4)
            for obj, placement in zip(objects, normalized['placements']):
                expected = placement['absolute_points']; box = obj.Shape.BoundBox
                self.assertAlmostEqual(box.XMin, min(x for x, y in expected), places=5)
                self.assertAlmostEqual(box.YMin, min(y for x, y in expected), places=5)
                self.assertAlmostEqual(box.XMax, max(x for x, y in expected), places=5)
                self.assertAlmostEqual(box.YMax, max(y for x, y in expected), places=5)
            for i in (0, 4):
                for a, b in zip(objects[i:i+3], objects[i+1:i+4]):
                    self.assertAlmostEqual(a.Shape.distToShape(b.Shape)[0], 12, places=5)
            # A continuous update reuses the document, including earlier crash fixes.
            first_doc = importer.result_doc
            self.assertTrue(importer.import_result(result, session, show_summary=False))
            self.assertIs(importer.result_doc, first_doc)
        table.close(); form.close()

    def test_outer_contour_union_uses_exact_outline_not_bounding_box(self):
        def area(p):
            return abs(sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(p, p[1:]+p[:1])) / 2)

        # Two edge-joined rectangles merge into one 60x20 outline.
        outer, holes = outer_contour([[[0, 0], [30, 0], [30, 20], [0, 20]],
                                      [[30, 0], [60, 0], [60, 20], [30, 20]]])
        self.assertAlmostEqual(area(outer), 1200, places=3)
        self.assertEqual(holes, [])

        # An L-shaped assembly keeps its notch; a bounding box would be 40x40=1600.
        outer, holes = outer_contour([[[0, 0], [20, 0], [20, 20], [0, 20]],
                                      [[0, 20], [20, 20], [20, 40], [0, 40]]])
        self.assertAlmostEqual(area(outer), 800, places=3)
        self.assertEqual(holes, [])

        # A member hole survives the union with a touching neighbour.
        outer, holes = outer_contour(
            [[[0, 0], [50, 0], [50, 50], [0, 50]], [[50, 0], [80, 0], [80, 50], [50, 50]]],
            [[[[20, 20], [30, 20], [30, 30], [20, 30]]], None])
        self.assertAlmostEqual(area(outer), 4000, places=3)
        self.assertEqual(len(holes), 1)
        self.assertAlmostEqual(area(holes[0]), 100, places=3)

        # A rotated member keeps its outline; the contour is never the axis-aligned box.
        outer, _ = outer_contour([[[5, 0], [10, 5], [5, 10], [0, 5]]])
        self.assertAlmostEqual(area(outer), 50, places=3)


if __name__ == '__main__':
    unittest.main()
