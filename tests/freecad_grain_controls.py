"""Real FreeCAD placements and Qt signals for immediate texture controls."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import freecad_search_settings as fixture

App, Part = fixture.App, fixture.Part
QtGui, QtCore = fixture.QtGui, fixture.QtCore
G, L = fixture.G, fixture.L
APPLICATION = fixture.APPLICATION


class TextureControlsTests(unittest.TestCase):
    make_panel = fixture.SearchSettingsIntegration.make_panel
    tearDown = fixture.SearchSettingsIntegration.tearDown

    def setUp(self):
        fixture.SearchSettingsIntegration.setUp(self)
        self.panel.preview_doc_name = self.document.Name
        fit = patch.object(self.panel, '_fit_all_views')
        self.fit = fit.start()
        self.addCleanup(fit.stop)

    def add_row(self, name, copies=1, primary_only=False):
        objects = []
        for index in range(copies):
            obj = self.document.addObject('Part::Feature', name)
            obj.Shape = Part.makeBox(40 + index * 5, 25, 4)
            obj.Placement = App.Placement(
                App.Vector(100 + index * 140, 400 + self.panel.table.rowCount() * 60, 9),
                App.Rotation(App.Vector(0, 0, 1), 17 + index * 31).multiply(
                    App.Rotation(App.Vector(1, 0, 0), 180)))
            objects.append(obj)
        row = self.panel.table.rowCount() - self.panel.control_rows
        blocked = self.panel.table.blockSignals(True)
        self.panel.table.insertRow(row)
        item = QtGui.QTableWidgetItem(name)
        item.setData(QtCore.Qt.UserRole, objects[0].Name)
        if not primary_only:
            item.setData(QtCore.Qt.UserRole + 1, json.dumps([o.Name for o in objects]))
        self.panel.table.setItem(row, 0, item)
        self.panel.table.setItem(row, 1, QtGui.QTableWidgetItem('3'))
        self.panel.table.setItem(row, 2, QtGui.QTableWidgetItem('[0, 45, 90]'))
        widget = QtGui.QWidget()
        layout = QtGui.QHBoxLayout(widget)
        cb = QtGui.QCheckBox()
        combo = QtGui.QComboBox()
        combo.addItems(['X', 'Y'])
        layout.addWidget(cb)
        layout.addWidget(combo)
        self.panel.table.setCellWidget(row, 4, widget)
        custom_widget = QtGui.QWidget()
        custom_layout = QtGui.QHBoxLayout(custom_widget)
        custom = QtGui.QCheckBox()
        custom_layout.addWidget(custom)
        self.panel.table.setCellWidget(row, 5, custom_widget)
        self.panel.table.blockSignals(blocked)
        self.panel._connect_grain_widgets(cb, combo, objects[0].Name)
        self.document.recompute()
        return row, objects, cb, combo, custom

    def placement(self, obj):
        return list(obj.Placement.toMatrix().A)

    def assert_placement(self, obj, expected):
        for a, b in zip(self.placement(obj), expected):
            self.assertAlmostEqual(a, b, places=8)

    def assert_blue_contains(self, objects):
        perimeter = self.document.getObject('GrainPerimeter_Grain')
        self.assertIsNotNone(perimeter)
        bounds = perimeter.Shape.BoundBox
        for obj in objects:
            part = obj.Shape.BoundBox
            self.assertLessEqual(bounds.XMin, part.XMin)
            self.assertGreaterEqual(bounds.XMax, part.XMax)
            self.assertLessEqual(bounds.YMin, part.YMin)
            self.assertGreaterEqual(bounds.YMax, part.YMax)

    def set_custom_angle(self, angle, accept=True):
        original = G.GrainAngleDialog
        def dialog(**kwargs):
            result = original(**kwargs)
            def finish():
                result.spin.setValue(angle)
                if accept:
                    result.accept()
                else:
                    result.reject()
            QtCore.QTimer.singleShot(0, finish)
            return result
        with patch.object(G, 'GrainAngleDialog', side_effect=dialog):
            self.panel.set_angle_btn.click()

    def test_checkbox_moves_all_copies_immediately_and_restores_every_placement(self):
        row, parts, cb, _, custom = self.add_row('Panel', copies=2)
        _, stationary, _, _, _ = self.add_row('Stationary')
        before = [self.placement(o) for o in parts]
        fixed = self.placement(stationary[0])
        self.assertFalse(hasattr(self.panel, 'bulk_grain_apply_btn'))
        self.assertFalse(any(b.text() == str(L.tr('apply_grain'))
                             for b in self.panel.form.findChildren(QtGui.QPushButton)))
        cb.setChecked(True)
        for obj, placement in zip(parts, before):
            self.assertNotEqual(self.placement(obj), placement)
            self.assertIsNotNone(self.document.getObject('GrainArrow_' + obj.Name))
        self.assert_blue_contains(parts)
        self.assert_placement(stationary[0], fixed)
        self.assertEqual(self.panel.table.item(row, 2).text(), '[0, 180]')
        custom.setChecked(True)
        cb.setChecked(False)
        for obj, placement in zip(parts, before):
            self.assert_placement(obj, placement)
            self.assertIsNone(self.document.getObject('GrainArrow_' + obj.Name))
            self.assertFalse(hasattr(obj, 'IPNestingStandardPlacement'))
        self.assert_placement(stationary[0], fixed)
        self.assertFalse(custom.isChecked())
        self.assertEqual(self.panel.table.item(row, 2).text(), '[0, 45, 90]')
        self.assertIsNone(self.document.getObject('GrainPerimeter_Grain'))
        self.assertEqual(self.fit.call_count, 2)

    def test_custom_angle_is_undone_and_later_recheck_uses_new_standard_placement(self):
        _, parts, cb, _, custom = self.add_row('Custom', copies=2)
        _, stationary, _, _, _ = self.add_row('Stationary')
        original = [self.placement(obj) for obj in parts]
        stationary_before = self.placement(stationary[0])
        cb.setChecked(True)
        checked_rotations = [obj.Placement.Rotation.Q for obj in parts]
        custom.setChecked(True)
        self.set_custom_angle(37)
        for obj, rotation in zip(parts, checked_rotations):
            self.assertNotEqual(obj.Placement.Rotation.Q, rotation)
            self.assertAlmostEqual(obj.Placement.Rotation.multVec(App.Vector(0, 0, 1)).z, -1)
        self.assert_blue_contains(parts)
        self.assert_placement(stationary[0], stationary_before)
        cb.setChecked(False)
        for obj, expected in zip(parts, original):
            self.assert_placement(obj, expected)
        self.assertFalse(custom.isChecked())
        parts[0].Placement = App.Placement(App.Vector(12, 345, 9), App.Rotation(App.Vector(0, 0, 1), 71))
        moved = self.placement(parts[0])
        cb.setChecked(True)
        cb.setChecked(False)
        self.assert_placement(parts[0], moved)

    def test_all_checked_then_unchecked_in_different_order_preserves_originals(self):
        _, a, ca, _, _ = self.add_row('A', primary_only=True)
        _, b, cb, _, _ = self.add_row('B')
        before_a, before_b = self.placement(a[0]), self.placement(b[0])
        ca.setChecked(True)
        cb.setChecked(True)
        self.assert_blue_contains(a + b)
        ca.setChecked(False)
        self.assert_placement(a[0], before_a)
        self.assert_blue_contains(b)
        self.assertLess(b[0].Shape.BoundBox.YMax, a[0].Shape.BoundBox.YMin)
        cb.setChecked(False)
        self.assert_placement(a[0], before_a)
        self.assert_placement(b[0], before_b)

    def test_y_axis_and_bulk_changes_apply_without_button_and_restore_on_uncheck(self):
        _, a, ca, axis, _ = self.add_row('A')
        _, b, cb, _, _ = self.add_row('B')
        original_a, original_b = self.placement(a[0]), self.placement(b[0])
        axis.setCurrentIndex(1)
        self.assert_placement(a[0], original_a)
        ca.setChecked(True)
        self.assertEqual(axis.currentText(), 'X')
        cb.setChecked(True)
        rotations = [obj.Placement.Rotation.Q for obj in a + b]
        with patch.object(self.panel._grain, '_apply_live_layout', wraps=self.panel._grain._apply_live_layout) as apply:
            self.panel.bulk_grain_combo.setCurrentIndex(1)
            self.assertEqual(apply.call_count, 1)
        for obj, old in zip(a + b, rotations):
            self.assertNotEqual(obj.Placement.Rotation.Q, old)
            self.assertEqual(obj.GrainAngleDeg, 0)
        ca.setChecked(False)
        cb.setChecked(False)
        self.assert_placement(a[0], original_a)
        self.assert_placement(b[0], original_b)

    def test_custom_angle_cancel_keeps_current_texture_placement(self):
        _, parts, cb, _, custom = self.add_row('Cancel')
        original = self.placement(parts[0])
        cb.setChecked(True)
        checked = self.placement(parts[0])
        custom.setChecked(True)
        self.set_custom_angle(53, accept=False)
        self.assert_placement(parts[0], checked)
        self.assertEqual(parts[0].GrainAngleDeg, 0)
        cb.setChecked(False)
        self.assert_placement(parts[0], original)


if __name__ == '__main__':
    unittest.main()
