"""Run with FreeCAD's bundled Python to verify live Qt updates without a GUI session."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import types
import FreeCAD as App
import Part
from PySide import QtGui, QtCore

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import IPNestingLanguages as languages
from IPNestingGui import NestingTaskPanel
from IPNestingAddSheet import AddSheetOrOffcutDialog
from IPNestingGrainAngleDialog import GrainAngleDialog
from IPNestingOffcutShowDialog import OffcutShowDialog
import FreeCADGui as Gui


# Keep language-switch tests away from the user's actual preferences and cache.
class Preferences:
    # Initialize an English session.
    def __init__(self):
        self.language = 'en'

    # Return the in-memory language.
    def GetString(self, key, default=''):
        return self.language

    # Record the new selection only inside this test.
    def SetString(self, key, value):
        self.language = value


# Exercise repeated changes on real Qt controls and real FreeCAD document properties.
def main():
    application = QtGui.QApplication.instance() or QtGui.QApplication([])
    QtGui.QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    application.setFont(QtGui.QFont('Segoe UI', 9))
    preferences = Preferences()
    with tempfile.TemporaryDirectory() as cache:
        languages.App = types.SimpleNamespace(ParamGet=lambda _: preferences,
                                             getUserCachePath=lambda: cache,
                                             listDocuments=App.listDocuments)
        languages._active_language = 'en'
        panel = NestingTaskPanel()
        dialog = AddSheetOrOffcutDialog(panel=panel)
        angle = GrainAngleDialog(part_labels=['Settings', 'Lietotāja detaļa'], initial_angle=37)
        material = dict(id=731, type='rectangular', width=300, height=200, count=3,
                        grain='None', outer=[(0, 0), (300, 0), (300, 200), (0, 200)])
        panel.offcut_controller._append_offcut_table_row(material)
        offcuts = OffcutShowDialog([material], panel=panel)
        # Existing input, options and user-defined names must survive unchanged.
        panel.placement_strategy.blockSignals(True)
        panel.placement_strategy.setCurrentIndex(2)
        panel.placement_strategy.blockSignals(False)
        panel.table.blockSignals(True)
        panel.table.insertRow(0)
        name_item = QtGui.QTableWidgetItem('Settings')
        name_item.setData(QtCore.Qt.UserRole, 'TestPart')
        panel.table.setItem(0, 0, name_item)
        panel.table.setItem(0, 1, QtGui.QTableWidgetItem('7'))
        grain_check = QtGui.QCheckBox()
        grain_check.setChecked(True)
        panel.table.setCellWidget(0, 4, grain_check)
        panel.table.blockSignals(False)
        values = [(widget, widget.text()) for widget in panel.form.findChildren(QtGui.QLineEdit)]
        signals = []
        panel.placement_strategy.currentIndexChanged.connect(signals.append)
        panel.table.itemChanged.connect(signals.append)
        document = App.newDocument('LanguageSmoke')
        part = document.addObject('Part::Feature', 'SourcePart')
        part.Shape = Part.makeBox(10, 20, 5)
        part.Placement.Base = App.Vector(100, 200, 0)
        placement = part.Placement.toMatrix().A
        caption = document.addObject('App::FeaturePython', 'GrainCaption')
        caption.addProperty('App::PropertyStringList', 'Text')
        languages.tag_perimeter(caption, 'Parts with grain direction', 'label')
        parent = QtGui.QWidget()
        Gui.getMainWindow = lambda: parent
        action = QtGui.QAction(parent)
        action.setObjectName('IP_RunNesting')
        available = languages.available_languages()
        sequence = ['lv', 'en'] + [code for code, _ in languages.LANGUAGES if code in available] + ['en']
        for code in sequence:
            languages.set_language(code)
            application.processEvents()
            assert panel.table.horizontalHeaderItem(0).text() == languages.tr('body')
            direction = QtCore.Qt.RightToLeft if code in {'ar', 'fa', 'he', 'ur', 'ps'} else QtCore.Qt.LeftToRight
            assert panel.form.layoutDirection() == direction
            assert dialog.layoutDirection() == direction
            assert offcuts.layoutDirection() == direction
            assert parent.layoutDirection() == QtCore.Qt.LeftToRight
            assert panel.placement_strategy.currentIndex() == 2
            assert panel.table.item(0, 0).text() == 'Settings'
            assert panel.table.item(0, 0).data(QtCore.Qt.UserRole) == 'TestPart'
            assert panel.table.item(0, 1).text() == '7'
            assert grain_check.isChecked()
            assert dialog.windowTitle() == languages.tr('add_sheet_or_offcut')
            assert angle.windowTitle() == languages.tr('grain_angle')
            assert offcuts.windowTitle() == languages.tr('offcuts')
            assert action.text() == languages.tr('nesting_tool')
            assert angle.spin.value() == 37
            assert angle.listw.item(0).text() == 'Settings'
            assert angle.listw.item(1).text() == 'Lietotāja detaļa'
            assert caption.Text == [str(languages.tr('perimeter.with_grain'))]
            assert part.Placement.toMatrix().A == placement
            assert panel.offcuts_table.item(0, 0).text() == languages.tr('sheet_3f_x_3f_s') % (300, 200, 'mm')
            assert panel.offcuts_table.item(0, 1).text() == '3'
            assert any(label.text() == languages.tr('b_s_b_count_d') % (
                languages.tr('sheet_3f_x_3f_s') % (300, 200, 'mm'), 3)
                for label in offcuts.findChildren(QtGui.QLabel))
            assert all(widget.text() == value for widget, value in values)
            assert all(widget.layoutDirection() == QtCore.Qt.LeftToRight for widget, _ in values)
            assert not signals, signals
        assert set(languages._catalogs) == {'en'}
        assert len(list(Path(cache).rglob('*.json'))) == 0
        languages.show_settings()
        assert len(languages._menu._language_buttons) == 50
        languages._menu._language_search.setText('latviešu')
        application.processEvents()
        matching = [(code, button) for code, button in languages._menu._language_buttons
                    if not button.isHidden()]
        assert [code for code, _ in matching] == ['lv']
        matching[0][1].click()
        assert languages.current_language() == 'lv'
        assert panel.table.horizontalHeaderItem(0).text() == 'Detaļa'
        assert not languages._menu.isVisible()
        # Simulate a wide display to inspect the seven-column layout offscreen.
        original_screen_at = application.screenAt
        application.screenAt = lambda _: types.SimpleNamespace(
            availableGeometry=lambda: QtCore.QRect(0, 0, 1920, 1080))
        languages.show_settings()
        application.screenAt = original_screen_at
        languages._menu._language_search.setText('')
        languages._menu._language_widget.resize(1500, 800)
        languages._menu._language_widget.grab().save(str(ROOT.parent / 'language_chooser.png'))
        panel.form.resize(1100, 1000)
        panel.form.grab().save(str(ROOT.parent / 'language_live_panel.png'))
        languages._menu.close()
        dialog.close()
        angle.close()
        offcuts.close()
        panel.form.close()
        App.closeDocument(document.Name)
        print('PASS: %d live changes; preserved input, option indices, user names and angles; ' % len(sequence) +
              'no editing signals; preview captions; direct JSON loading; searchable chooser click')


if __name__ == '__main__':
    main()

