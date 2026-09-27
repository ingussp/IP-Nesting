"""
Dialog for marking inner contours (holes) on a single nesting part.

Reuses the material preview widget so the user clicks a contour to toggle it
as a hole. Selected holes are stored on the task panel and exported in the
part's input.json record so the nesting CLI keeps them open.
"""
from IPNestingLanguages import tr, ui_call, ui_widget, register_window, translate_buttons

import traceback

from IPNestingOffcutShowDialog import _OffcutPreview
from IPNestingExport import _polygons_same_2d

import FreeCAD as App
from PySide import QtGui, QtCore


class PartHoleDialog(QtGui.QDialog):
    """
    Let the user mark the selected part's inner contours as nesting holes.

    The outer contour is never selectable; only non-outer contours can be
    toggled. Accepted selections are returned as normalized hole polygons.
    """

    def __init__(self, obj_name, outer, contours, preselected=None, parent=None):
        super(PartHoleDialog, self).__init__(parent)

        self._obj_name = obj_name
        self._contours = list(contours or [])

        # Pre-mark contours that match previously selected holes so re-opening
        # the dialog keeps the current selection.
        for contour in self._contours:
            if contour.get("is_outer"):
                continue

            polygon = contour.get("polygon") or []

            if any(
                _polygons_same_2d(polygon, previous)
                for previous in (preselected or [])
            ):
                contour["selected"] = True

        ui_call(self, 'setWindowTitle', tr('mark_holes_dialog_title'))
        self.setModal(True)
        self.resize(760, 640)
        self.setMinimumSize(600, 480)

        root = QtGui.QVBoxLayout(self)

        hint = ui_widget(
            QtGui.QLabel,
            tr('mark_holes_dialog_hint')
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._preview = _OffcutPreview(
            outer=outer,
            contours=self._contours,
            on_contour_clicked=None,
            grain="None",
            display_units="mm",
            show_dimensions=False
        )
        root.addWidget(self._preview, 1)

        buttons = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok
            | QtGui.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        translate_buttons(buttons)
        root.addWidget(buttons)

        register_window(self)

    # Return the selected (non-outer) contour polygons as hole records.
    def selected_holes(self):
        result = []

        try:
            for contour in self._preview._contours:
                if contour.get("is_outer"):
                    continue

                if not contour.get("selected"):
                    continue

                polygon = list(
                    contour.get("polygon") or []
                )

                if len(polygon) >= 3:
                    result.append(polygon)

        except Exception:
            App.Console.PrintError(
                tr('mark_holes_read_selection_failed')
                + traceback.format_exc()
            )

        return result
