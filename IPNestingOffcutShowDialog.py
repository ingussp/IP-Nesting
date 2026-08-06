"""
IPNestingOffcutShowDialog - Popup dialog to visualize all added DXF offcuts
and adjust their grain direction (X/Y/None) per offcut.

- Shows each offcut with a 200x200 preview.
- Grain direction is relative to DXF local axes, but user can swap X/Y or set None.
- Draws a small red arrow indicating grain direction on the preview:
    - X: horizontal arrow near the top
    - Y: vertical arrow near the left
    - None: no arrow
"""

import traceback
import math
import time
import os

from IPNestingAddSheet import AddSheetOrOffcutDialog
from IPNestingOffcuts import (extract_offcut_from_dxf,add_or_increment_material,polygon_area)

import FreeCAD as App
from PySide import QtGui, QtCore
MM_PER_INCH = 25.4

def _sync_compatibility_holes(offcut):
    """
    Synchronize compatibility fields from user-selected contours.

    The new source of truth is:
        offcut["contours"][...]["selected"]

    The old fields holes and selected_holes are kept synchronized
    for compatibility with code that still reads them.
    """
    try:
        contours = offcut.get("contours") or []

        selected_holes = []

        for contour in contours:
            if contour.get("is_outer"):
                continue

            if not contour.get("selected", False):
                continue

            polygon = contour.get("polygon") or []

            if len(polygon) >= 3:
                selected_holes.append(
                    list(polygon)
                )

        offcut["holes"] = selected_holes
        offcut["selected_holes"] = [
            True for _ in selected_holes
        ]

    except Exception:
        App.Console.PrintError(
            "_sync_compatibility_holes failed:\n"
            + traceback.format_exc()
        )


class _ContourGraphicsItem(QtGui.QGraphicsPolygonItem):
    """
    Clickable non-outer contour.

    Selected contour is exported as a forbidden nesting area.
    The outer contour is never represented by this class.
    """

    def __init__(
        self,
        contour_index,
        polygon,
        selected,
        callback,
        parent=None
    ):
        super(_ContourGraphicsItem, self).__init__(
            QtGui.QPolygonF(polygon),
            parent
        )

        self.contour_index = int(contour_index)
        self.selected = bool(selected)
        self.callback = callback

        self.setZValue(10)
        self.setAcceptedMouseButtons(
            QtCore.Qt.LeftButton
        )

        self.update_style()

    def update_style(self):
        if self.selected:
            self.setBrush(
                QtGui.QBrush(
                    QtGui.QColor(255, 120, 120)
                )
            )
            self.setPen(
                QtGui.QPen(
                    QtGui.QColor(180, 0, 0),
                    2
                )
            )
        else:
            self.setBrush(
                QtGui.QBrush(
                    QtGui.QColor(220, 220, 220)
                )
            )
            self.setPen(
                QtGui.QPen(
                    QtGui.QColor(100, 100, 100),
                    2
                )
            )

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.selected = not self.selected
            self.update_style()

            if self.callback:
                self.callback(
                    self.contour_index,
                    self.selected
                )

            event.accept()
            return

        event.ignore()


class _OffcutPreview(QtGui.QGraphicsView):
    """
    Proportional sheet/offcut preview with:

    - full outer contour;
    - all holes;
    - mouse-wheel zoom;
    - horizontal and vertical scrolling;
    - clickable holes.
    """

    def __init__(
        self,
        outer=None,
        contours=None,
        on_contour_clicked=None,
        grain="None",
        parent=None,
        size_px=700
    ):
        super(_OffcutPreview, self).__init__(parent)

        self._outer = list(outer or [])

        self._contours = []

        for contour in contours or []:
            self._contours.append(
                dict(contour)
            )

        self._on_contour_clicked = (
            on_contour_clicked
        )

        self._grain = "None"
        self._zoom = 1.0
        self._has_user_zoom = False

        self._scene = QtGui.QGraphicsScene(self)
        self.setScene(self._scene)

        self.setMinimumSize(500, 400)
        self.setSizePolicy(
            QtGui.QSizePolicy.Expanding,
            QtGui.QSizePolicy.Expanding
        )

        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded
        )
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded
        )

        self.setTransformationAnchor(
            QtGui.QGraphicsView.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QtGui.QGraphicsView.AnchorViewCenter
        )

        self.setRenderHint(
            QtGui.QPainter.Antialiasing,
            True
        )

        self.setBackgroundBrush(
            QtGui.QBrush(QtGui.QColor("white"))
        )

        self.set_grain(grain)

    def _valid_points(self, polygon):
        result = []

        for point in polygon or []:
            try:
                if point is None or len(point) < 2:
                    continue

                x = float(point[0])
                y = float(point[1])

                if (
                    math.isfinite(x)
                    and math.isfinite(y)
                ):
                    # Invert Y for QGraphicsScene coordinates.
                    result.append(
                        QtCore.QPointF(x, -y)
                    )

            except Exception:
                continue

        return result
    
    def _add_grain_arrow_to_scene(self):
        """
        Add a red grain-direction arrow over the preview.

        The arrow is drawn in scene coordinates. The model uses
        inverted Y coordinates, therefore scene_y is negative
        model_y.
        """
        try:
            if self._grain == "None":
                return

            outer_points = self._valid_points(
                self._outer
            )

            if len(outer_points) < 3:
                return

            xs = [
                point.x()
                for point in outer_points
            ]

            ys = [
                point.y()
                for point in outer_points
            ]

            min_x = min(xs)
            max_x = max(xs)
            min_y = min(ys)
            max_y = max(ys)

            width = max_x - min_x
            height = max_y - min_y

            if width <= 1e-9 or height <= 1e-9:
                return

            # Keep the arrow inside the outer contour bbox.
            margin_x = width * 0.12
            margin_y = height * 0.12

            arrow_pen = QtGui.QPen(
                QtGui.QColor(210, 0, 0),
                max(1.0, min(width, height) * 0.008)
            )

            arrow_brush = QtGui.QBrush(
                QtGui.QColor(210, 0, 0)
            )

            if self._grain == "X":
                # Horizontal arrow: left -> right.
                y = min_y + margin_y
                x1 = min_x + margin_x
                x2 = max_x - margin_x

                line = self._scene.addLine(
                    x1,
                    y,
                    x2,
                    y,
                    arrow_pen
                )
                line.setZValue(5)

                head_size = max(
                    min(width, height) * 0.04,
                    1.0
                )

                head = QtGui.QPolygonF([
                    QtCore.QPointF(
                        x2,
                        y
                    ),
                    QtCore.QPointF(
                        x2 - head_size,
                        y - head_size * 0.55
                    ),
                    QtCore.QPointF(
                        x2 - head_size,
                        y + head_size * 0.55
                    ),
                ])

            else:
                # Vertical arrow: top -> bottom.
                x = min_x + margin_x
                y1 = min_y + margin_y
                y2 = max_y - margin_y

                line = self._scene.addLine(
                    x,
                    y1,
                    x,
                    y2,
                    arrow_pen
                )
                line.setZValue(5)

                head_size = max(
                    min(width, height) * 0.04,
                    1.0
                )

                head = QtGui.QPolygonF([
                    QtCore.QPointF(
                        x,
                        y2
                    ),
                    QtCore.QPointF(
                        x - head_size * 0.55,
                        y2 - head_size
                    ),
                    QtCore.QPointF(
                        x + head_size * 0.55,
                        y2 - head_size
                    ),
                ])

            head_item = self._scene.addPolygon(
                head,
                arrow_pen,
                arrow_brush
            )
            head_item.setZValue(5)

        except Exception:
            App.Console.PrintError(
                "_add_grain_arrow_to_scene failed:\n"
                + traceback.format_exc()
            )
    
    def set_grain(self, grain):
        value = str(
            grain or "None"
        ).strip().upper()

        if value not in ("X", "Y"):
            value = "None"

        self._grain = value
        
        self._rebuild_scene(
            preserve_view=True
        )
    
    def _contour_clicked(self, contour_index, selected):
        for contour in self._contours:
            if int(
                contour.get("index", -1)
            ) == int(contour_index):

                if contour.get("is_outer"):
                    return

                contour["selected"] = bool(selected)
                break

        if self._on_contour_clicked:
            self._on_contour_clicked(
                contour_index,
                bool(selected)
            )
    
    def _rebuild_scene(self, preserve_view=False):
        old_center = None

        try:
            if preserve_view:
                old_center = self.mapToScene(
                    self.viewport().rect().center()
                )
        except Exception:
            old_center = None

        self._scene.clear()

        outer_points = self._valid_points(
            self._outer
        )

        if len(outer_points) >= 3:
            outer_item = self._scene.addPolygon(
                QtGui.QPolygonF(outer_points),
                QtGui.QPen(
                    QtGui.QColor(0, 0, 0),
                    2
                ),
                QtGui.QBrush(
                    QtGui.QColor(245, 245, 245)
                )
            )

            # Outer contour is always above background
            # but below selectable contours.
            outer_item.setZValue(0)

        for contour in self._contours:
            if contour.get("is_outer"):
                continue

            contour_index = int(
                contour.get("index", -1)
            )

            polygon = self._valid_points(
                contour.get("polygon") or []
            )

            if len(polygon) < 3:
                continue

            selected = bool(
                contour.get("selected", False)
            )

            item = _ContourGraphicsItem(
                contour_index=contour_index,
                polygon=polygon,
                selected=selected,
                callback=self._contour_clicked
            )

            self._scene.addItem(item)

        # Draw grain arrow after outer and selectable contours.
        self._add_grain_arrow_to_scene()
        
        bounds = self._scene.itemsBoundingRect()

        if not bounds.isNull():
            margin = max(
                10.0,
                max(
                    bounds.width(),
                    bounds.height()
                ) * 0.03
            )

            self._scene.setSceneRect(
                bounds.adjusted(
                    -margin,
                    -margin,
                    margin,
                    margin
                )
            )

        if not self._has_user_zoom:
            self.fitInView(
                self._scene.sceneRect(),
                QtCore.Qt.KeepAspectRatio
            )

        if old_center is not None:
            self.centerOn(old_center)

    def wheelEvent(self, event):
        """
        Mouse wheel zoom.

        Scroll up:
            zoom in

        Scroll down:
            zoom out
        """
        try:
            delta = event.angleDelta().y()

            if delta == 0:
                event.ignore()
                return

            if delta > 0:
                factor = 1.20
            else:
                factor = 1.0 / 1.20

            new_zoom = self._zoom * factor
            new_zoom = max(
                0.15,
                min(12.0, new_zoom)
            )

            factor = new_zoom / self._zoom
            self._zoom = new_zoom
            self._has_user_zoom = True

            self.scale(
                factor,
                factor
            )

            event.accept()

        except Exception:
            App.Console.PrintError(
                "OffcutPreview.wheelEvent failed:\n"
                + traceback.format_exc()
            )
            event.ignore()

    def mouseDoubleClickEvent(self, event):
        """
        Double-click resets the preview to fit the whole sheet.
        """
        try:
            if event.button() == QtCore.Qt.LeftButton:
                self._zoom = 1.0
                self._has_user_zoom = False
                self.resetTransform()

                self.fitInView(
                    self._scene.sceneRect(),
                    QtCore.Qt.KeepAspectRatio
                )

                event.accept()
                return

        except Exception:
            pass

        super(_OffcutPreview, self).mouseDoubleClickEvent(
            event
        )

    def resizeEvent(self, event):
        super(_OffcutPreview, self).resizeEvent(event)

        if not self._has_user_zoom:
            try:
                self.fitInView(
                    self._scene.sceneRect(),
                    QtCore.Qt.KeepAspectRatio
                )
            except Exception:
                pass


def _parse_clearance_value(text, default=0.0):
    """
    Parse a decimal value using either comma or dot.
    """
    try:
        value = str(text or "").strip()
        value = value.replace(",", ".")
        return max(0.0, float(value))
    except Exception:
        return float(default)


def _format_clearance_value(value):
    """
    Format clearance value for display.
    """
    try:
        value = max(0.0, float(value))

        text = "%.6f" % value
        text = text.rstrip("0").rstrip(".")

        return text or "0"
    except Exception:
        return "0"

def _parse_display_dimension(text, units):
    try:
        value = str(text or "").strip()

        if not value or "/" in value:
            return None

        value = float(
            value.replace(",", ".")
        )

        if value < 0.0:
            return None

        if str(units).lower() == "inch":
            return value * MM_PER_INCH

        return value

    except Exception:
        return None


def _format_display_dimension(value_mm, units):
    try:
        value_mm = float(value_mm)

        if str(units).lower() == "inch":
            value = value_mm / MM_PER_INCH
        else:
            value = value_mm

        return (
            "%.6f" % value
        ).rstrip("0").rstrip(".") or "0"

    except Exception:
        return "0"

class OffcutShowDialog(QtGui.QDialog):
    def __init__(self, offcuts, parent=None, panel=None):
        super(OffcutShowDialog, self).__init__(parent)
        self.setWindowTitle("Offcuts")
        self.setModal(True)
        
        self.setSizeGripEnabled(True)

        # 1) Minimum dialog size 900x700
        self.resize(1100, 850)
        self.setMinimumSize(900, 700)

        self._offcuts = offcuts  # list of dicts, mutated in place
        self._panel = panel
        
        self._display_units = getattr(
            panel,
            "display_units",
            "mm"
        ) if panel is not None else "mm"

        self._clearance_combos = []
        self._clearance_edits = []
        self._preview_widgets = []
        
        if self._panel is not None:
            self._clearance_mode = str(
                getattr(
                    self._panel,
                    "offcut_clearance_mode",
                    "same"
                )
                or "same"
            ).strip().lower()

            self._custom_clearance = max(
                0.0,
                float(
                    getattr(
                        self._panel,
                        "offcut_custom_clearance",
                        0.0
                    )
                    or 0.0
                )
            )
        else:
            self._clearance_mode = "same"
            self._custom_clearance = 0.0

        if self._clearance_mode not in (
            "same",
            "custom"
        ):
            self._clearance_mode = "same"
        
        self._cards = []
        self._scroll_area = None

        root = QtGui.QVBoxLayout(self)

        info = QtGui.QLabel(
            "Added sheets and offcuts. "
            "Grain direction is relative to the material local axes."
        )
        root.addWidget(info)

        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded
        )

        self._scroll_area = scroll

        root.addWidget(scroll, 1)

        container = QtGui.QWidget()
        v = QtGui.QVBoxLayout(container)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)

        # Build cards
        for idx, off in enumerate(self._offcuts):
            card = QtGui.QGroupBox()
            self._cards.append(card)
            card_lay = QtGui.QVBoxLayout(card)
            card_lay.setContentsMargins(10, 10, 10, 10)
            card_lay.setSpacing(8)

            label = str(off.get("label", "Offcut"))

            try:
                count = max(
                    1,
                    int(
                        off.get(
                            "count",
                            off.get("quantity", 1)
                        )
                    )
                )
            except Exception:
                count = 1

            # -------------------------
            # Controls above the model
            # -------------------------
            header_lay = QtGui.QHBoxLayout()
            header_lay.setSpacing(12)

            title = QtGui.QLabel(
                "<b>%s</b>  |  Count: %d"
                % (label, count)
            )
            header_lay.addWidget(title)
            
            clearance_lay = QtGui.QHBoxLayout()
            clearance_lay.setSpacing(8)

            clearance_lay.addWidget(
                QtGui.QLabel(
                    "Hole-to-part clearance:"
                )
            )

            clearance_combo = QtGui.QComboBox()
            clearance_combo.addItems([
                "same as part spacing",
                "custom",
            ])
            clearance_combo.setMinimumWidth(180)

            clearance_edit = QtGui.QLineEdit()
            clearance_edit.setMinimumWidth(90)
            clearance_edit.setMaximumWidth(120)
            clearance_edit.setToolTip(
                "Custom distance from hole edge to part edge."
            )

            self._clearance_combos.append(
                clearance_combo
            )
            self._clearance_edits.append(
                clearance_edit
            )

            clearance_combo.currentIndexChanged.connect(
                self._on_shared_clearance_mode_changed
            )

            clearance_edit.editingFinished.connect(
                lambda _checked=False,
                _edit=clearance_edit:
                    self._on_shared_clearance_value_changed(
                        _edit
                    )
            )

            clearance_lay.addWidget(
                clearance_combo
            )
            clearance_lay.addWidget(
                clearance_edit
            )
            clearance_lay.addStretch(1)

            header_lay.addStretch(1)

            header_lay.addWidget(
                QtGui.QLabel("Grain direction:")
            )

            combo = QtGui.QComboBox()
            combo.addItems(["None", "X", "Y"])
            combo.setMinimumWidth(75)

            grain = str(
                off.get("grain", "None")
                or "None"
            ).strip().upper()

            if grain == "X":
                combo.setCurrentIndex(1)
            elif grain == "Y":
                combo.setCurrentIndex(2)
            else:
                combo.setCurrentIndex(0)

            header_lay.addWidget(combo)
            card_lay.addLayout(header_lay)
            card_lay.addLayout(
                clearance_lay
            )            

            # DXF path below the header, still above the model
            path = str(off.get("path", "") or "")

            if path:
                path_label = QtGui.QLabel(path)
                path_label.setWordWrap(True)
                path_label.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                )
                card_lay.addWidget(path_label)

            # -------------------------
            # Outer contour and holes
            # -------------------------
            outer = off.get("outer") or []

            # Compatibility with older records.
            if not outer:
                polygons = off.get("polygons") or []
                outer = polygons[0] if polygons else []

            contours = off.get("contours")

            if contours is None:
                # Compatibility fallback for old records that only
                # contain outer/holes.
                contours = []

                contours.append({
                    "index": 0,
                    "polygon": outer,
                    "area": abs(polygon_area(outer)),
                    "is_outer": True,
                    "selected": False,
                })

                for hole_index, hole in enumerate(
                    off.get("holes") or [],
                    start=1
                ):
                    contours.append({
                        "index": hole_index,
                        "polygon": hole,
                        "area": abs(polygon_area(hole)),
                        "is_outer": False,
                        "selected": True,
                    })

            off["contours"] = contours
            
            # Ensure every contour has normalized fields.
            normalized_contours = []

            for contour in contours or []:
                normalized_contours.append({
                    "index": int(
                        contour.get("index", 0)
                    ),
                    "polygon": list(
                        contour.get("polygon") or []
                    ),
                    "area": float(
                        contour.get("area", 0.0)
                    ),
                    "bbox": dict(
                        contour.get("bbox") or {}
                    ),
                    "is_outer": bool(
                        contour.get("is_outer", False)
                    ),
                    "selected": bool(
                        contour.get("selected", False)
                    ),
                })

            contours = normalized_contours
            off["contours"] = contours

            # -------------------------
            # Large preview
            # -------------------------

            def _on_contour_clicked(
                contour_index,
                selected,
                _off=off
            ):
                try:
                    contours = _off.get(
                        "contours"
                    ) or []

                    for contour in contours:
                        if int(
                            contour.get("index", -1)
                        ) != int(contour_index):
                            continue

                        if contour.get("is_outer"):
                            return

                        contour["selected"] = bool(
                            selected
                        )
                        break

                    _sync_compatibility_holes(
                        _off
                    )

                except Exception:
                    App.Console.PrintError(
                        "Failed to update contour state:\n"
                        + traceback.format_exc()
                    )
            
            preview = _OffcutPreview(
                outer=outer,
                contours=contours,
                on_contour_clicked=_on_contour_clicked,
                grain=grain,
                parent=card,
                size_px=700,
            )
            self._preview_widgets.append(
                preview
            )            

            # Store preview so checkbox callbacks can refresh it.
            off["_preview_widget"] = preview

            card_lay.addWidget(
                preview,
                1
            )

            def _on_combo_changed(
                index,
                _off=off,
                _preview=preview
            ):
                try:
                    if int(index) == 1:
                        _off["grain"] = "X"
                        _preview.set_grain("X")
                    elif int(index) == 2:
                        _off["grain"] = "Y"
                        _preview.set_grain("Y")
                    else:
                        _off["grain"] = "None"
                        _preview.set_grain("None")
                except Exception:
                    pass

            combo.currentIndexChanged.connect(
                _on_combo_changed
            )

            v.addWidget(card, 1)

        v.addStretch(1)
        scroll.setWidget(container)

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)
        self._apply_shared_clearance_to_widgets()
        QtCore.QTimer.singleShot(
            0,
            self._resize_cards_to_viewport
        )
    
    def set_display_units(self, units):
        """
        Update all visible Offcut card dimension fields.
        """
        try:
            units = str(units or "mm").lower()

            if units not in ("mm", "inch"):
                units = "mm"

            self._display_units = units

            # Rebuild or refresh dimension labels/widgets here.
            # Clearance widgets should be refreshed by the
            # shared-clearance update method.
            self._apply_shared_clearance_to_widgets()

        except Exception:
            App.Console.PrintError(
                "OffcutShowDialog.set_display_units failed:\n"
                + traceback.format_exc()
            )
    
    def _resize_cards_to_viewport(self):
        """
        Make every card fill the available scroll viewport height
        while preserving the outer margins.
        """
        try:
            if self._scroll_area is None:
                return

            viewport_height = int(
                self._scroll_area.viewport().height()
            )

            if viewport_height <= 0:
                return

            # Preserve the container margins and a small safety gap.
            card_height = max(
                500,
                viewport_height - 20
            )

            for card in self._cards:
                try:
                    card.setMinimumHeight(card_height)
                    card.setSizePolicy(
                        QtGui.QSizePolicy.Expanding,
                        QtGui.QSizePolicy.Fixed
                    )
                except Exception:
                    pass

        except Exception:
            App.Console.PrintError(
                "_resize_cards_to_viewport failed:\n"
                + traceback.format_exc()
            )
            
    def _store_shared_clearance_state(self):
        """
        Store the shared clearance state on the main panel.

        This is session/workbench state, not per-offcut state.
        """
        try:
            if self._panel is None:
                return

            self._panel.offcut_clearance_mode = (
                self._clearance_mode
            )

            self._panel.offcut_custom_clearance = (
                self._custom_clearance
            )

        except Exception:
            App.Console.PrintError(
                "_store_shared_clearance_state failed:\n"
                + traceback.format_exc()
            )

    def _apply_shared_clearance_to_widgets(self):
        """
        Update all cards so every card shows the same shared setting.
        """
        try:
            is_custom = (
                self._clearance_mode == "custom"
            )

            for combo in self._clearance_combos:
                combo.blockSignals(True)

                try:
                    combo.setCurrentIndex(
                        1 if is_custom else 0
                    )
                finally:
                    combo.blockSignals(False)

            for edit in self._clearance_edits:
                edit.blockSignals(True)

                try:
                    edit.setText(
                        _format_display_dimension(
                            self._custom_clearance,
                            self._display_units
                        )
                    )
                    edit.setEnabled(is_custom)
                finally:
                    edit.blockSignals(False)

        except Exception:
            App.Console.PrintError(
                "_apply_shared_clearance_to_widgets failed:\n"
                + traceback.format_exc()
            )

    def _on_shared_clearance_mode_changed(self, index):
        """
        Change the shared mode for all cards.
        """
        try:
            if int(index) == 1:
                self._clearance_mode = "custom"
            else:
                self._clearance_mode = "same"

            self._store_shared_clearance_state()
            self._apply_shared_clearance_to_widgets()

        except Exception:
            App.Console.PrintError(
                "_on_shared_clearance_mode_changed failed:\n"
                + traceback.format_exc()
            )

    def _on_shared_clearance_value_changed(self, edit):
        """
        Update the shared custom clearance value.

        Only the edited field is normalized first; then all cards
        receive the same value.
        """
        try:
            text = str(edit.text()).strip()

            if not text:
                value = 0.0
            else:
                value = _parse_display_dimension(
                    text,
                    self._display_units
                )

                if value is None:
                    value = self._custom_clearance

            self._custom_clearance = value
            self._clearance_mode = "custom"

            self._store_shared_clearance_state()
            self._apply_shared_clearance_to_widgets()

        except Exception:
            App.Console.PrintError(
                "_on_shared_clearance_value_changed failed:\n"
                + traceback.format_exc()
            )
    
    def resizeEvent(self, event):
        try:
            super(OffcutShowDialog, self).resizeEvent(
                event
            )
        except Exception:
            pass

        QtCore.QTimer.singleShot(
            0,
            self._resize_cards_to_viewport
        )
        
class OffcutMaterialsController(object):
    """
    Controls the Sheet & Offcut Materials table and related dialogs.
    """
    
    def __init__(self, panel):
        self.panel = panel
        self._count_update_guard = False
        self._table_rebuild_guard = False

    @property
    def offcuts(self):
        return self.panel.offcuts

    @property
    def offcuts_table(self):
        return self.panel.offcuts_table
    
    def add_offcut_dxf(self):
        """
        Open the add sheet/offcut dialog and process its result.
        """
        try:
            dialog = AddSheetOrOffcutDialog(
                parent=QtGui.QApplication.activeWindow()
            )

            result = dialog.exec_()

            if result != QtGui.QDialog.Accepted:
                return

            if dialog.result_type == "rectangular":
                App.Console.PrintMessage(
                    "[Offcuts][DIALOG] processing rectangular sheet\n"
                )

                self._process_rectangular_sheet_result(
                    dialog.result_data
                )

            elif dialog.result_type == "dxf":
                App.Console.PrintMessage(
                    "[Offcuts][DIALOG] processing DXF offcut\n"
                )

                self._process_dxf_offcut_result(
                    dialog.result_data
                )

            App.Console.PrintMessage(
                "[Offcuts][DIALOG] result processing finished\n"
            )

        except Exception:
            App.Console.PrintError(
                "add_offcut_dxf failed:\n"
                + traceback.format_exc()
            )
            
    
    def _process_dxf_offcut_result(self, data):
        """
        Import the selected DXF and add it as one grouped material row.
        Repeated imports of the same DXF increase Count.
        """
        try:
            data = data or {}

            path = os.path.abspath(
                str(data.get("path", "") or "")
            )

            try:
                quantity = int(data.get("quantity", 1))
            except Exception:
                quantity = 1

            quantity = max(1, quantity)

            if not path or not os.path.exists(path):
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "DXF offcut",
                    "The selected DXF file does not exist."
                )
                return

            try:
                boundary_resolution = (
                    self.panel.get_boundary_resolution_mm()
                )
            except Exception:
                boundary_resolution = 0.1

            boundary_resolution = max(
                0.001,
                min(100.0, boundary_resolution)
            )

            App.Console.PrintMessage(
                "[Offcuts] DXF deflection: %.4f\n"
                % boundary_resolution
            )

            outer, holes, bbox, contour_info = (
                extract_offcut_from_dxf(
                    path,
                    debug=False,
                    deflection=boundary_resolution
                )
            )
            
            App.Console.PrintMessage(
                "[Offcuts] Outer points: %d, contours: %d\n"
                % (
                    len(outer or []),
                    len(contour_info or [])
                )
            )
            
            if not outer:
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "DXF offcut",
                    "No closed outer contour was found in the DXF file."
                )
                return

            contours = []

            for contour in contour_info or []:
                contours.append({
                    "index": int(
                        contour.get("index", 0)
                    ),
                    "polygon": list(
                        contour.get("polygon") or []
                    ),
                    "area": float(
                        contour.get("area", 0.0)
                    ),
                    "bbox": dict(
                        contour.get("bbox") or {}
                    ),
                    "is_outer": bool(
                        contour.get("is_outer", False)
                    ),
                    "selected": False,
                })

            offcut = {
                "id": int(
                    self.panel._offcut_next_id
                ),
                "type": "dxf",
                "path": path,
                "label": os.path.basename(path),
                "grain": "None",

                "outer": outer,
                "contours": contours,

                # Compatibility fields.
                "holes": [],
                "selected_holes": [],
                "polygons": [outer],

                "bbox": bbox,
                "contour_info": contour_info,

                "count": quantity,
                "quantity": quantity,
                "duplicate": False,
            }

            self.panel._offcut_next_id += 1

            material, row, was_existing = add_or_increment_material(
                self.offcuts,
                offcut,
                count=quantity
            )

            self._rebuild_offcuts_table(
                selected_row=row
            )

        except Exception:
            App.Console.PrintError(
                "_process_dxf_offcut_result failed:\n"
                + traceback.format_exc()
            )
            
    def _process_rectangular_sheet_result(self, data):
        """
        Add a rectangular sheet as one grouped material row.
        Repeated sheets with the same dimensions increase Count.
        """
        try:
            data = data or {}

            try:
                width = float(data.get("width", 0.0))
                height = float(data.get("height", 0.0))
            except Exception:
                width = 0.0
                height = 0.0

            try:
                quantity = int(data.get("quantity", 1))
            except Exception:
                quantity = 1

            quantity = max(1, quantity)

            if width <= 0.0 or height <= 0.0:
                QtGui.QMessageBox.warning(
                    self.panel.form,
                    "Sheet",
                    "Sheet width and height must be greater than zero."
                )
                return

            outer = [
                [0.0, 0.0],
                [width, 0.0],
                [width, height],
                [0.0, height],
            ]
            
            sheet = {
                "id": int(
                    self.panel._offcut_next_id
                ),
                "type": "rectangular",
                "path": "",
                "label": "Sheet %.0f x %.0f mm"
                % (width, height),
                "grain": "None",

                "outer": outer,

                "contours": [
                    {
                        "index": 0,
                        "polygon": outer,
                        "area": float(
                            width * height
                        ),
                        "bbox": {
                            "min_x": 0.0,
                            "min_y": 0.0,
                            "max_x": width,
                            "max_y": height,
                        },
                        "is_outer": True,
                        "selected": False,
                    }
                ],

                # Compatibility fields.
                "holes": [],
                "selected_holes": [],
                "polygons": [outer],

                "bbox": {
                    "min_x": 0.0,
                    "min_y": 0.0,
                    "max_x": width,
                    "max_y": height,
                },

                "width": width,
                "height": height,
                "quantity": quantity,
                "count": quantity,
                "duplicate": False,
            }

            self.panel._offcut_next_id += 1

            material, row, was_existing = add_or_increment_material(
                self.offcuts,
                sheet,
                count=quantity
            )

            self._rebuild_offcuts_table(
                selected_row=row
            )

        except Exception:
            App.Console.PrintError(
                "_process_rectangular_sheet_result failed:\n"
                + traceback.format_exc()
            )
            
    def _set_count_cell_text(self, item, value):
        """
        Safely restore or update a Count cell without triggering
        recursive Count processing.
        """
        try:
            self._count_update_guard = True
            item.setText(str(max(1, int(value))))
        except Exception:
            pass
        finally:
            self._count_update_guard = False
    
    def on_offcut_count_changed(self, item):
        """
        Update the material count when the Count cell is edited.
        """
        if self._count_update_guard:
            return

        if item is None:
            return

        # Count is column 1.
        if item.column() != 1:
            return

        row = item.row()

        if row < 0 or row >= len(self.offcuts):
            return

        material = self.offcuts[row]

        try:
            text = str(item.text()).strip()

            # Empty or invalid input restores the previous value.
            new_count = int(text)

            if new_count < 1:
                new_count = 1

            if new_count > 100000:
                new_count = 100000

        except Exception:
            try:
                old_count = int(
                    material.get(
                        "count",
                        material.get("quantity", 1)
                    )
                )
            except Exception:
                old_count = 1

            self._set_count_cell_text(item, old_count)
            return

        try:
            self._count_update_guard = True

            material["count"] = new_count
            material["quantity"] = new_count

            if item.text() != str(new_count):
                item.setText(str(new_count))

        except Exception:
            App.Console.PrintError(
                "on_offcut_count_changed failed:\n"
                + traceback.format_exc()
            )

        finally:
            self._count_update_guard = False
    
    def remove_offcuts(self):
        """
        Remove the currently selected material row.
        """
        try:
            selection = self.offcuts_table.selectionModel().selectedRows()

            if not selection:
                return

            rows = sorted(
                [index.row() for index in selection],
                reverse=True
            )

            for row in rows:
                if 0 <= row < len(self.offcuts):
                    del self.offcuts[row]

            self._rebuild_offcuts_table()

        except Exception:
            App.Console.PrintError(
                "remove_offcuts failed:\n"
                + traceback.format_exc()
            )
            
    def _move_offcut_row(self, row, direction):
        """
        Move one material in self.offcuts and rebuild the table.

        direction:
            -1 = up
            +1 = down
        """
        try:
            if row < 0 or row >= len(self.offcuts):
                return

            target_row = row + int(direction)

            if target_row < 0:
                return

            if target_row >= len(self.offcuts):
                return

            # Preserve the material order in the model.
            self.offcuts[row], self.offcuts[target_row] = (
                self.offcuts[target_row],
                self.offcuts[row],
            )

            self._rebuild_offcuts_table(selected_row=target_row)

        except Exception:
            App.Console.PrintError(
                "_move_offcut_row failed:\n"
                + traceback.format_exc()
            )
            
    def _rebuild_offcuts_table(self, selected_row=None):
        """
        Rebuild the materials table from self.offcuts.
        """
        if self._table_rebuild_guard:
            return

        try:
            self._table_rebuild_guard = True
            self.offcuts_table.setUpdatesEnabled(False)

            self.offcuts_table.clearContents()
            self.offcuts_table.setRowCount(0)

            for row, material in enumerate(self.offcuts):
                self._append_offcut_table_row(
                    material,
                    row=row
                )

            if selected_row is not None:
                if 0 <= selected_row < self.offcuts_table.rowCount():
                    self.offcuts_table.selectRow(selected_row)
                    self.offcuts_table.setCurrentCell(
                        selected_row,
                        0
                    )

        except Exception:
            App.Console.PrintError(
                "_rebuild_offcuts_table failed:\n"
                + traceback.format_exc()
            )

        finally:
            self.offcuts_table.setUpdatesEnabled(True)
            self.offcuts_table.viewport().update()
            self._table_rebuild_guard = False
            
    def _append_offcut_table_row(self, material, row=None):
        """
        Add one material row to the table.

        Table order follows self.offcuts order.
        Repeated identical materials are represented by Count.
        """
        try:
            if row is None:
                row = self.offcuts_table.rowCount()

            self.offcuts_table.insertRow(row)

            # Column 0: Material
            label = str(
                material.get("label", "Sheet or Offcut")
            )

            material_item = QtGui.QTableWidgetItem(label)
            material_item.setFlags(
                QtCore.Qt.ItemIsEnabled |
                QtCore.Qt.ItemIsSelectable
            )

            if "id" in material:
                try:
                    material_item.setData(
                        QtCore.Qt.UserRole,
                        int(material.get("id"))
                    )
                except Exception:
                    pass

            if bool(material.get("duplicate", False)):
                material_item.setForeground(
                    QtGui.QBrush(QtGui.QColor("red"))
                )

            self.offcuts_table.setItem(row, 0, material_item)

            # Column 1: Count
            try:
                count = max(1, int(material.get("count", 1)))
            except Exception:
                count = 1

            count_item = QtGui.QTableWidgetItem(str(count))
            count_item.setFlags(
                QtCore.Qt.ItemIsEnabled
                | QtCore.Qt.ItemIsSelectable
                | QtCore.Qt.ItemIsEditable
            )
            count_item.setTextAlignment(QtCore.Qt.AlignCenter)
            count_item.setToolTip("Enter the number of sheets or offcuts.")
            self.offcuts_table.setItem(row, 1, count_item)

            # Column 2: Grain
            grain = str(
                material.get("grain", "None") or "None"
            ).strip().upper()

            if grain not in ("X", "Y"):
                grain = "None"

            grain_item = QtGui.QTableWidgetItem(grain)
            grain_item.setFlags(
                QtCore.Qt.ItemIsEnabled |
                QtCore.Qt.ItemIsSelectable
            )
            grain_item.setTextAlignment(QtCore.Qt.AlignCenter)
            self.offcuts_table.setItem(row, 2, grain_item)

            # Column 3: Move buttons
            move_widget = QtGui.QWidget()
            move_layout = QtGui.QHBoxLayout(move_widget)
            move_layout.setContentsMargins(2, 0, 2, 0)
            move_layout.setSpacing(2)

            up_button = QtGui.QToolButton()
            up_button.setText("↑")
            up_button.setToolTip("Move material up")
            up_button.setAutoRaise(True)
            up_button.setFixedWidth(28)

            down_button = QtGui.QToolButton()
            down_button.setText("↓")
            down_button.setToolTip("Move material down")
            down_button.setAutoRaise(True)
            down_button.setFixedWidth(28)

            up_button.clicked.connect(
                lambda checked=False, r=row:
                    self._move_offcut_row(r, -1)
            )

            down_button.clicked.connect(
                lambda checked=False, r=row:
                    self._move_offcut_row(r, 1)
            )

            move_layout.addWidget(up_button)
            move_layout.addWidget(down_button)

            self.offcuts_table.setCellWidget(row, 3, move_widget)

        except Exception:
            App.Console.PrintError(
                "_append_offcut_table_row failed:\n"
                + traceback.format_exc()
            )
            
    def _refresh_offcut_grain_column(self):
        try:
            for row in range(self.offcuts_table.rowCount()):
                item = self.offcuts_table.item(row, 0)
                if not item:
                    continue

                material_id = item.data(QtCore.Qt.UserRole)

                material = None
                for entry in self.offcuts:
                    if int(entry.get("id", -1)) == int(material_id):
                        material = entry
                        break

                if material is None:
                    continue

                grain_value = str(
                    material.get("grain", "None") or "None"
                ).strip().upper()

                if grain_value not in ("X", "Y"):
                    grain_value = "None"
                    
                grain_item = self.offcuts_table.item(row, 2)

                if grain_item is None:
                    grain_item = QtGui.QTableWidgetItem()
                    grain_item.setFlags(
                        QtCore.Qt.ItemIsEnabled
                        | QtCore.Qt.ItemIsSelectable
                    )
                    grain_item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.offcuts_table.setItem(row, 2, grain_item)

                grain_item.setText(grain_value)

        except Exception:
            App.Console.PrintError(
                "_refresh_offcut_grain_column failed:\n"
                + traceback.format_exc()
            )
            
    def _get_selected_offcuts(self):
        """
        Return material records corresponding to selected table rows.
        The order follows the table/model order.
        """
        selected = []

        try:
            indexes = self.offcuts_table.selectionModel().selectedRows()

            rows = sorted(
                set(index.row() for index in indexes)
            )

            for row in rows:
                if 0 <= row < len(self.offcuts):
                    selected.append(self.offcuts[row])

        except Exception:
            App.Console.PrintError(
                "_get_selected_offcuts failed:\n"
                + traceback.format_exc()
            )

        return selected
    
    def show_offcuts_popup(self):
        """
        Show previews only for the selected sheet/offcut rows.
        """
        try:
            selected_offcuts = self._get_selected_offcuts()

            if not selected_offcuts:
                QtGui.QMessageBox.information(
                    self.panel.form,
                    "Sheets and Offcuts",
                    "Select at least one sheet or offcut first."
                )
                return

            parent = QtGui.QApplication.activeWindow()

            dlg = OffcutShowDialog(
                selected_offcuts,
                parent=parent,
                panel=self.panel
            )

            self.panel._active_offcut_dialog = dlg

            try:
                dlg.exec_()
            finally:
                if getattr(
                    self.panel,
                    "_active_offcut_dialog",
                    None
                ) is dlg:
                    self.panel._active_offcut_dialog = None

            # Grain values are updated in-place by OffcutShowDialog.
            self._refresh_offcut_grain_column()

        except Exception:
            App.Console.PrintError(
                "show_offcuts_popup failed:\n"
                + traceback.format_exc()
            )