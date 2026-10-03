"""Real Qt popup + FreeCAD snapshots + bundled CLI, using disposable job folders."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import FreeCAD as App
import Part
from PySide import QtCore, QtGui
import IPNestingExport as E
import IPNestingResult as R
from IPNestingRuntime import snapshot_part

APPLICATION = QtGui.QApplication.instance() or QtGui.QApplication([])


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench integration ')
        self.root = Path(self.temp.name)
        folder = self.root / 'clinesting/windows-x86_64'
        folder.mkdir(parents=True)
        shutil.copy2(ROOT / 'clinesting/windows-x86_64/clinesting.exe', folder / 'clinesting.exe')
        self.doc = App.newDocument('Job_Preview')
        obj = self.doc.addObject('Part::Feature', 'Original')
        obj.Shape = Part.makeBox(10, 20, 3)
        # The source top face faces down; the result must preserve this flip.
        obj.Placement = App.Placement(App.Vector(100, 50, 8), App.Rotation(App.Vector(1, 0, 0), 180))
        self.doc.recompute()
        self.source = obj
        self.form = QtGui.QWidget()
        self.manager = None
        self.errors = []
        self.dialog_patch = patch.object(QtGui.QMessageBox, 'critical', side_effect=lambda *args: self.errors.append(str(args[-1])))
        self.dialog_patch.start()
        self.warning_patch = patch.object(QtGui.QMessageBox, 'warning', side_effect=lambda *args: self.errors.append(str(args[-1])))
        self.warning_patch.start()

    def tearDown(self):
        if self.manager is not None and self.manager.is_running():
            self.manager.shutdown()
            self.wait_until(lambda: not self.manager.is_running())
            self.manager._release_after_exit()
        if self.manager is not None:
            self.manager._restore_ui()
        self.form.close()
        APPLICATION.processEvents()
        self.dialog_patch.stop()
        self.warning_patch.stop()
        for name in list(App.listDocuments()):
            App.closeDocument(name)
        self.temp.cleanup()

    def export_and_start(self, mode, quantity=20):
        class Item:
            def __init__(self, value): self.value = value
            def text(self): return self.value
            def data(self, role): return self.value if role == QtCore.Qt.UserRole else None
        class Table:
            def rowCount(self): return 1
            def item(self, row, col): return Item(['Original', str(quantity), '4'][col])
            def cellWidget(self, *args): return None
        panel = NS(form=self.form, preview_doc_name=self.doc.Name, table=Table(), control_rows=0,
                   spacing=0, sheet_margin=0, get_dimension_value_mm=lambda value, default: value,
                   get_boundary_resolution_mm=lambda: .01,
                   offcuts=[dict(type='rectangular', outer=[[0, 0], [1000, 0], [1000, 1000], [0, 1000]], quantity=1)],
                   mode_combo=NS(currentText=lambda: mode), time_limit_edit=NS(text=lambda: '.1'),
                   round_seconds_edit=NS(text=lambda: '.1'), cpu_cores_combo=NS(currentText=lambda: '2'),
                   run_btn=QtGui.QPushButton(), stop_btn=QtGui.QPushButton())
        self.manager = R.NestingProcessManager(panel)
        self.manager._module_directory = lambda: str(self.root)
        self.assertTrue(self.manager.prepare_job())
        original_module = E.__file__
        E.__file__ = str(self.root / 'IPNestingExport.py')
        try:
            self.assertTrue(E.execute_nesting(panel))
        finally:
            E.__file__ = original_module
        self.assertTrue(self.manager.start_nesting(str(self.root / 'input.json')), self.errors)
        self.assertTrue(self.manager.wait_dialog.isVisible())
        self.assertFalse(panel.run_btn.isEnabled())
        return panel

    def wait_until(self, predicate, seconds=12):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            APPLICATION.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate(), self.errors)

    def test_first_preserves_snapshot_after_preview_changes(self):
        original_normal = self.source.Shape.Faces[4].normalAt(.5, .5).z
        panel = self.export_and_start('first', 1000)
        # Changes after Run cannot alter the geometry/orientation of this job.
        self.source.Shape = Part.makeBox(2, 2, 1)
        self.source.Placement = App.Placement()
        self.doc.recompute()
        self.wait_until(lambda: self.manager._finished)
        self.assertFalse(self.errors)
        self.assertTrue(panel.run_btn.isEnabled())
        self.assertIsNone(self.manager.wait_dialog)
        objects = [o for o in self.manager.importer.result_doc.Objects if o.Name.startswith('Nesting_')]
        self.assertEqual(len(objects), 1000)
        for obj in objects:
            self.assertAlmostEqual(obj.Shape.Volume, 600)
            self.assertAlmostEqual(obj.Shape.Faces[4].normalAt(.5, .5).z, original_normal)
        session = json.loads((self.root / 'nesting_session.json').read_text(encoding='utf-8'))
        result = json.loads((self.root / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(session['job_id'], result['job_id'])

    def test_timed_finishes(self):
        self.export_and_start('timed')
        self.wait_until(lambda: self.manager._finished)
        self.assertFalse(self.errors)
        self.assertEqual(json.loads((self.root / 'result.json').read_text())['placed'], 20)

    def test_real_run_button_exports_and_launches(self):
        # A cabinet panel with blind drilling, a groove and one through hole.
        # Keep the displayed bottom/top orientation while exporting its footprint.
        placement = self.source.Placement
        shape = Part.makeBox(10, 20, 3)
        shape = shape.cut(Part.makeCylinder(.6, 1, App.Vector(4, 6, 2)))
        shape = shape.cut(Part.makeCylinder(.8, 3, App.Vector(6, 13, 0)))
        shape = shape.cut(Part.makeBox(10, .5, .5, App.Vector(0, 10, 2.5)))
        self.source.Shape = shape
        self.source.Placement = placement
        self.doc.recompute()
        before = self.source.Shape.copy()
        import IPNestingGui as G
        panel = G.NestingTaskPanel()
        panel.preview_doc_name = self.doc.Name
        panel.offcuts = [dict(type='rectangular', outer=[[0, 0], [100, 0], [100, 100], [0, 100]], quantity=1)]
        panel.table.insertRow(0)
        item = QtGui.QTableWidgetItem('Original')
        item.setData(QtCore.Qt.UserRole, 'Original')
        panel.table.setItem(0, 0, item)
        panel.table.setItem(0, 1, QtGui.QTableWidgetItem('2'))
        panel.table.setItem(0, 2, QtGui.QTableWidgetItem('4'))
        self.manager = panel._nesting_manager
        self.manager._module_directory = lambda: str(self.root)
        originals = E.__file__, G.__file__
        E.__file__ = str(self.root / 'IPNestingExport.py')
        G.__file__ = str(self.root / 'IPNestingGui.py')
        try:
            extracted = []
            extraction = E._extract_part_candidate_wires
            def trace_contour(*args, **kwargs):
                self.assertTrue(self.manager.wait_dialog._painted)
                extracted.append(1)
                return extraction(*args, **kwargs)
            contour_patch = patch.object(E, '_extract_part_candidate_wires', side_effect=trace_contour)
            contour_patch.start()
            panel.run_btn.click()
            self.assertTrue(self.manager.wait_dialog.isVisible())
            self.assertIsNone(self.manager.process)
            self.wait_until(lambda: self.manager.process is not None or self.manager._finished)
            self.assertIsNotNone(self.manager.process, self.errors)
            self.wait_until(lambda: self.manager._finished)
        finally:
            contour_patch.stop()
            E.__file__, G.__file__ = originals
        self.assertEqual(len(extracted), 1)
        self.assertFalse(self.errors)
        self.assertEqual(json.loads((self.root / 'result.json').read_text())['placed'], 2)
        self.assertTrue(panel.isAllowedAlterDocument())
        for name in ('Nesting_1', 'Nesting_2'):
            after = self.manager.importer.result_doc.getObject(name).Shape
            self.assertAlmostEqual(before.Volume, after.Volume)
            for a, b in zip(before.Faces, after.Faces):
                u0, u1, v0, v1 = a.ParameterRange
                za = a.normalAt((u0+u1)/2, (v0+v1)/2).z
                u0, u1, v0, v1 = b.ParameterRange
                zb = b.normalAt((u0+u1)/2, (v0+v1)/2).z
                self.assertAlmostEqual(za, zb)
        panel.form.deleteLater()

    def test_continuous_popup_cancel_and_history_cleanup(self):
        history = self.root / 'results'
        history.mkdir()
        (history / 'old.json').write_text('previous job')
        self.export_and_start('continuous')
        self.wait_until(lambda: self.manager._imported_signature is not None)
        self.assertTrue(self.manager.is_running())
        self.assertFalse(self.manager._finished)
        self.assertTrue(self.manager.wait_dialog.isVisible())
        self.assertFalse((history / 'old.json').exists())
        self.manager.wait_dialog.cancel_button.click()
        self.wait_until(lambda: self.manager._finished)
        self.assertEqual(self.manager.process.returncode, 0)
        self.assertFalse(self.errors)
        self.assertTrue(list(history.glob('result*.json')))
        self.assertFalse(Path(self.manager.cancel_path).exists())

    def test_foreign_job_is_not_imported_and_job_lock_is_exclusive(self):
        self.export_and_start('continuous')
        other = R.NestingProcessManager(self.manager.panel)
        other._module_directory = lambda: str(self.root)
        with patch.object(QtGui.QMessageBox, 'warning'):
            self.assertFalse(other.prepare_job())
        # Stop before polling/importing; launch is now asynchronous.
        self.wait_until(lambda: self.manager.process is not None)
        self.manager.result_timer.stop()
        (self.root / 'result.json').write_text(json.dumps(dict(job_id='foreign', sheets=[], placed=0)))
        for _ in range(3): self.manager._check_result()
        self.assertIsNone(self.manager._imported_signature)
        self.assertFalse(self.manager._finished)
        self.manager.stop_nesting()
        self.manager.process.wait(timeout=5)

    def test_gpu_is_explicit_and_painted_before_slow_scan(self):
        import IPNestingGui as G
        import IPNestingGpu as GPU
        ticks, seen = [], []
        heartbeat = QtCore.QTimer(self.form)
        heartbeat.setInterval(5)
        heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
        heartbeat.start()
        main_thread = threading.get_ident()
        def scan(exe, cancelled):
            seen.append((panel._gpu_discovery.dialog._painted, threading.get_ident()))
            time.sleep(.25)
            return [(0, 'Test GPU')]
        with patch.object(GPU, 'query_devices', side_effect=scan) as query:
            panel = G.NestingTaskPanel()
            self.assertTrue(hasattr(panel, 'show_gpus_btn'))
            query.assert_not_called()
            panel.gpu_enabled_combo.setCurrentIndex(1)
            APPLICATION.processEvents()
            query.assert_not_called()
            panel.show_gpus_btn.click()
            self.assertTrue(panel._gpu_discovery.dialog.isVisible())
            query.assert_not_called()
            self.wait_until(lambda: panel._gpu_discovery.dialog is None)
        heartbeat.stop()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0])
        self.assertNotEqual(seen[0][1], main_thread)
        self.assertGreater(len(ticks), 10)
        self.assertEqual(panel.gpu_device_combo.itemText(1), 'Test GPU')
        panel.form.deleteLater()

    def test_cancel_gpu_before_first_paint_does_not_scan(self):
        import IPNestingGui as G
        import IPNestingGpu as GPU
        panel = G.NestingTaskPanel()
        with patch.object(GPU, 'query_devices') as query:
            panel.show_gpus_btn.click()
            panel._gpu_discovery.dialog.cancel_button.click()
            APPLICATION.processEvents()
            query.assert_not_called()
        self.assertTrue(panel.show_gpus_btn.isEnabled())
        panel.form.deleteLater()

    def test_slow_process_creation_keeps_qt_responsive_after_paint(self):
        ticks, seen = [], []
        heartbeat = QtCore.QTimer(self.form)
        heartbeat.setInterval(5)
        heartbeat.timeout.connect(lambda: ticks.append(time.monotonic()))
        heartbeat.start()
        popen = R.subprocess.Popen
        main_thread = threading.get_ident()
        def delayed(*args, **kwargs):
            seen.append((self.manager.wait_dialog._painted, threading.get_ident()))
            time.sleep(.25)
            return popen(*args, **kwargs)
        with patch.object(R.subprocess, 'Popen', side_effect=delayed):
            self.export_and_start('first', 2)
            self.wait_until(lambda: self.manager.process is not None or self.manager._finished)
        heartbeat.stop()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0])
        self.assertNotEqual(seen[0][1], main_thread)
        self.assertGreater(len(ticks), 10)
        self.wait_until(lambda: self.manager._finished)
        self.assertFalse(self.errors)

    def test_cancel_pending_launch_reaps_late_process_and_keeps_lock(self):
        started, allow = threading.Event(), threading.Event()
        created = []
        popen = R.subprocess.Popen
        def delayed(*args, **kwargs):
            if '--input' not in args[0]:
                return popen(*args, **kwargs)
            started.set()
            allow.wait(5)
            process = popen(*args, **kwargs)
            created.append(process)
            return process
        with patch.object(R.subprocess, 'Popen', side_effect=delayed):
            panel = self.export_and_start('continuous')
            try:
                self.wait_until(started.is_set)
                self.manager.wait_dialog.cancel_button.click()
                self.assertTrue(self.manager._finished)
                self.assertTrue(self.manager.is_running())
                other = R.NestingProcessManager(panel)
                other._module_directory = lambda: str(self.root)
                with patch.object(QtGui.QMessageBox, 'warning'):
                    self.assertFalse(other.prepare_job())
            finally:
                allow.set()
            self.wait_until(lambda: not self.manager.is_running())
        self.manager._release_after_exit()
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())
        self.assertIsNone(self.manager._job_lock)
        self.assertIsNone(self.manager._log)
        self.assertFalse(self.errors)

    def test_cancel_before_export_paint_does_not_touch_job_files(self):
        panel = NS(form=self.form, run_btn=QtGui.QPushButton(), stop_btn=QtGui.QPushButton())
        self.manager = R.NestingProcessManager(panel)
        self.manager._module_directory = lambda: str(self.root)
        with patch.object(E, 'export_nesting_steps') as export:
            self.assertTrue(self.manager.prepare_and_start(str(self.root / 'input.json')))
            self.manager.wait_dialog.cancel_button.click()
            APPLICATION.processEvents()
            export.assert_not_called()
        self.assertFalse((self.root / 'input.json').exists())
        self.assertIsNone(self.manager._job_lock)

    def test_snapshot_works_without_preview_document(self):
        record = snapshot_part(self.source, .01)
        session = dict(parts=[dict(source_part_index=0, part_id='part_0', **record)])
        result = dict(placed=1, sheets=[dict(points=[[0, 0], [100, 0], [100, 100], [0, 100]],
                        parts=[dict(id=1, source=0, x=5, y=7, rotation=90,
                                    _ip_nesting=dict(source_part_index=0, source_type='3d'))])])
        name = self.doc.Name
        App.closeDocument(name)
        importer = R.NestingResultImporter(NS(preview_doc_name=name))
        self.assertTrue(importer.import_result(result, session, show_summary=False))
        self.assertAlmostEqual(importer.result_doc.getObject('Nesting_1').Shape.Volume, 600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
