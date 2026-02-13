"""
IPNestingOffcutShowDialog - Popup dialog to visualize all added DXF offcuts
and adjust their grain direction (X/Y) per offcut.

- Shows each offcut with a 200x200 preview.
- Grain direction is relative to DXF local axes, but user can swap X/Y.
"""

import traceback

import FreeCAD as App
from PySide import QtGui, QtCore


class _OffcutPreview(QtGui.QLabel):
    """A 200x200 preview image of a polygon."""
    def __init__(self, poly=None, parent=None, size_px=200):
        super(_OffcutPreview, self).__init__(parent)
        self._size_px = int(size_px)
        self.setFixedSize(self._size_px, self._size_px)
        self.setMinimumSize(self._size_px, self._size_px)
        self.setMaximumSize(self._size_px, self._size_px)
        self.setFrameShape(QtGui.QFrame.Box)
        self.setLineWidth(1)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.set_poly(poly)

    def set_poly(self, poly):
        self._poly = poly or []
        self._render()

    def _render(self):
        try:
            w = self._size_px
            h = self._size_px
            pm = QtGui.QPixmap(w, h)
            pm.fill(QtGui.QColor("white"))

            if not self._poly or len(self._poly) < 2:
                self.setPixmap(pm)
                return

            xs = [float(p[0]) for p in self._poly]
            ys = [float(p[1]) for p in self._poly]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            dx = max(1e-9, max_x - min_x)
            dy = max(1e-9, max_y - min_y)

            pad = 10.0
            avail_w = float(w) - 2.0 * pad
            avail_h = float(h) - 2.0 * pad
            s = min(avail_w / dx, avail_h / dy)

            # Centering offset
            scaled_w = dx * s
            scaled_h = dy * s
            ox = pad + 0.5 * (avail_w - scaled_w)
            oy = pad + 0.5 * (avail_h - scaled_h)

            def map_pt(x, y):
                # Qt Y increases downward; we invert Y so preview looks more CAD-like
                px = ox + (x - min_x) * s
                py = oy + (max_y - y) * s
                return QtCore.QPointF(px, py)

            painter = QtGui.QPainter(pm)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

            # draw axes (light)
            painter.setPen(QtGui.QPen(QtGui.QColor(220, 220, 220), 1))
            painter.drawLine(0, h / 2, w, h / 2)
            painter.drawLine(w / 2, 0, w / 2, h)

            # draw polygon
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 2))
            pts = [map_pt(float(p[0]), float(p[1])) for p in self._poly]
            if pts:
                # close for drawing
                pts2 = pts + [pts[0]]
                painter.drawPolyline(QtGui.QPolygonF(pts2))

            painter.end()
            self.setPixmap(pm)
        except Exception:
            # If render fails, show blank
            try:
                pm = QtGui.QPixmap(self._size_px, self._size_px)
                pm.fill(QtGui.QColor("white"))
                self.setPixmap(pm)
            except Exception:
                pass


class OffcutShowDialog(QtGui.QDialog):
    def __init__(self, offcuts, parent=None):
        super(OffcutShowDialog, self).__init__(parent)
        self.setWindowTitle("Offcuts")
        self.setModal(True)

        self._offcuts = offcuts  # list of dicts, mutated in place

        root = QtGui.QVBoxLayout(self)

        info = QtGui.QLabel("Added offcuts (DXF). Grain direction is relative to DXF local axes.")
        root.addWidget(info)

        scroll = QtGui.QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)

        container = QtGui.QWidget()
        v = QtGui.QVBoxLayout(container)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)

        # Build cards
        for idx, off in enumerate(self._offcuts):
            card = QtGui.QGroupBox()
            card_lay = QtGui.QHBoxLayout(card)
            card_lay.setContentsMargins(8, 8, 8, 8)
            card_lay.setSpacing(12)

            left = QtGui.QVBoxLayout()
            title = QtGui.QLabel("<b>%s</b>" % (off.get("label", "Offcut"),))
            left.addWidget(title)

            prev = _OffcutPreview(poly=(off.get("polygons") or [[]])[0] if off.get("polygons") else [])
            left.addWidget(prev)

            card_lay.addLayout(left)

            right = QtGui.QVBoxLayout()
            row = QtGui.QHBoxLayout()
            row.addWidget(QtGui.QLabel("Grain direction:"))

            combo = QtGui.QComboBox()
            combo.addItems(["X", "Y"])
            g = (off.get("grain") or "X").upper()
            combo.setCurrentIndex(0 if g == "X" else 1)

            def _on_combo_changed(i, _idx=idx):
                try:
                    self._offcuts[_idx]["grain"] = "X" if int(i) == 0 else "Y"
                except Exception:
                    pass

            combo.currentIndexChanged.connect(_on_combo_changed)
            row.addWidget(combo)
            row.addStretch(1)
            right.addLayout(row)

            # Show full path (optional but useful)
            path_lbl = QtGui.QLabel(off.get("path", ""))
            path_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            path_lbl.setWordWrap(True)
            right.addWidget(path_lbl)

            right.addStretch(1)
            card_lay.addLayout(right, 1)

            v.addWidget(card)

        v.addStretch(1)
        scroll.setWidget(container)

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)