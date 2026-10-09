"""Grain-matching editor, preview-document persistence and exact group checks."""
import json
import math
import FreeCAD as App
import Part
from PySide import QtCore, QtGui
from IPNestingLanguages import current_language
from IPNestingGrainMatchModel import edges, polygon, solve, transform


# This prototype has English/Latvian captions; other locales use English.
_MESSAGES = {
    'title': ('Match grain', 'Saskaņot tekstūru'),
    'hint': ('Select the numbered edges that should face each other. Edges are centred; all parts form one rigid group.',
             'Izvēlies malu numurus, kurām jāatrodas pretī. Malas tiek centrētas; visas detaļas veido vienu stingru grupu.'),
    'parts': ('Numbered parts', 'Numurētās detaļas'),
    'assembly': ('Group preview', 'Grupas priekšskatījums'),
    'spacing': ('Part spacing: %.3f mm', 'Atstarpe starp detaļām: %.3f mm'),
    'edge': ('Select edge…', 'Izvēlies malu…'),
    'save': ('Save group', 'Saglabāt grupu'),
    'cancel': ('Cancel', 'Atcelt'),
    'remove': ('Remove matching', 'Noņemt sasaisti'),
    'ready': ('Group is ready. It will be nested as one unit.', 'Grupa ir gatava. Tā tiks izkārtota kā viena vienība.'),
    'select': ('Select at least two part rows in the table.', 'Tabulā iezīmē vismaz divas detaļu rindas.'),
    'equal': ('All selected parts must have the same quantity.', 'Visām izvēlētajām detaļām jābūt vienādam skaitam.'),
    'overlap': ('The group overlaps or violates part spacing. Choose different edges.',
                'Grupas detaļas pārklājas vai neievēro atstarpi. Izvēlies citas malas.'),
    'incomplete': ('Fill all edge pairs to connect every part once into a single group.',
                   'Aizpildi visus malu pārus, savienojot visas detaļas vienā grupā bez cikliem.'),
}


def text(key):
    return _MESSAGES[key][1 if current_language() == 'lv' else 0]


def validate_layout(parts, poses, spacing):
    faces = []
    for part, pose in zip(parts, poses):
        points = transform(part['points'], pose)
        wire = Part.makePolygon([App.Vector(x, y, 0) for x, y in points+points[:1]])
        face = Part.Face(wire)
        if not face.isValid():
            raise ValueError(text('overlap'))
        # The outer contour is reserved for the group; do not nest group members
        # into one another's holes in this first version.
        for other in faces:
            if face.common(other).Area > 1e-6 or face.distToShape(other)[0] < spacing-1e-5:
                raise ValueError(text('overlap'))
        faces.append(face)


_OBJECT = 'IPNestingGrainMatches'


def read_groups(doc):
    obj = doc.getObject(_OBJECT) if doc else None
    if not obj:
        return []
    groups = json.loads(obj.GroupsJson)
    if not isinstance(groups, list):
        raise ValueError('Invalid grain-matching group data.')
    return groups


def write_groups(doc, groups):
    obj = doc.getObject(_OBJECT)
    if not obj:
        obj = doc.addObject('App::FeaturePython', _OBJECT)
        obj.addProperty('App::PropertyString', 'GroupsJson', 'Grain matching')
        obj.Label = text('title')
    obj.GroupsJson = json.dumps(groups)
    doc.recompute()


class GrainMatchingDialog(QtGui.QDialog):
    def __init__(self, parts, spacing, links=None, existing=False, parent=None):
        super().__init__(parent)
        self.parts = parts; self.spacing = spacing; self.removed = False; self.poses = None
        self.setWindowTitle(text('title')); self.resize(1100, 740); self.setMinimumSize(760, 540)
        root = QtGui.QVBoxLayout(self)
        hint = QtGui.QLabel(text('hint')); hint.setWordWrap(True); root.addWidget(hint)
        root.addWidget(QtGui.QLabel(text('spacing') % spacing))
        views = QtGui.QHBoxLayout(); root.addLayout(views, 1)
        self.views = []
        for heading in ('parts', 'assembly'):
            column = QtGui.QVBoxLayout(); views.addLayout(column)
            column.addWidget(QtGui.QLabel(text(heading)))
            view = QtGui.QGraphicsView(); view.setScene(QtGui.QGraphicsScene(view))
            view.setRenderHint(QtGui.QPainter.Antialiasing)
            view.setMinimumHeight(260); column.addWidget(view, 1); self.views.append(view)
        form = QtGui.QFormLayout(); root.addLayout(form)
        self.pairs = []
        numbered = edges(parts)
        for row in range(max(0, len(parts)-1)):
            line = QtGui.QHBoxLayout(); combos = []
            for side in range(2):
                combo = QtGui.QComboBox(); combo.addItem(text('edge'), None)
                for edge in numbered:
                    combo.addItem('%d — %s' % (edge['number'], parts[edge['part']]['label']), edge['number'])
                if links and row < len(links):
                    combo.setCurrentIndex(combo.findData(links[row][side]))
                line.addWidget(combo); combos.append(combo)
                combo.currentIndexChanged.connect(self.update_preview)
            form.addRow('%d.' % (row+1), line); self.pairs.append(combos)
        self.status = QtGui.QLabel(); self.status.setWordWrap(True); root.addWidget(self.status)
        buttons = QtGui.QHBoxLayout(); root.addLayout(buttons)
        self.remove_button = QtGui.QPushButton(text('remove')); self.remove_button.setEnabled(existing)
        self.remove_button.clicked.connect(self.remove_group); buttons.addWidget(self.remove_button); buttons.addStretch()
        self.save_button = QtGui.QPushButton(text('save')); self.save_button.clicked.connect(self.accept_group)
        buttons.addWidget(self.save_button)
        cancel = QtGui.QPushButton(text('cancel')); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        self.update_preview()
        QtCore.QTimer.singleShot(0, self.fit_views)

    def links(self):
        return [[box.itemData(box.currentIndex()) for box in row] for row in self.pairs]

    def remove_group(self):
        self.removed = True; self.accept()

    def accept_group(self):
        self.update_preview()
        if self.poses is not None:
            self.accept()

    def fit_views(self):
        for view in self.views:
            rect = getattr(view, 'outline_rect', QtCore.QRectF(0, 0, 1, 1))
            margin = max(rect.width(), rect.height())*.06 + 10
            rect = rect.adjusted(-margin, -margin, margin, margin)
            view.scene().setSceneRect(rect)
            view.fitInView(rect, QtCore.Qt.KeepAspectRatio)
            scale = max(view.transform().m11(), 1e-9)
            for label, x, y in getattr(view, 'labels', []):
                label.setScale(1/scale)
                bounds = label.boundingRect()
                label.setPos(x-bounds.width()/(2*scale), y-bounds.height()/(2*scale))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'views'):
            self.fit_views()

    def draw(self, view, poses):
        scene = view.scene(); scene.clear()
        view.labels = []; outline = QtCore.QRectF()
        numbered = edges(self.parts)
        colours = ['#bedcf0', '#f6d49f', '#bfe0be', '#ddc6e8', '#f0bdbd', '#bde5df']
        for i, (part, pose) in enumerate(zip(self.parts, poses)):
            points = transform(part['points'], pose)
            poly = QtGui.QPolygonF([QtCore.QPointF(x, -y) for x, y in points])
            outline = outline.united(poly.boundingRect())
            scene.addPolygon(poly, QtGui.QPen(QtGui.QColor('#355365')), QtGui.QBrush(QtGui.QColor(colours[i % len(colours)])))
            cx = sum(p[0] for p in points)/len(points); cy = sum(p[1] for p in points)/len(points)
            label = scene.addSimpleText(part['label'])
            view.labels.append((label, cx, -cy))
        view.outline_rect = outline
        for edge in numbered:
            a, b = transform([edge['a'], edge['b']], poses[edge['part']])
            label = scene.addSimpleText(str(edge['number']))
            label.setBrush(QtGui.QBrush(QtGui.QColor('#ab2424')))
            font = label.font(); font.setBold(True); label.setFont(font)
            view.labels.append((label, (a[0]+b[0])/2, -(a[1]+b[1])/2))

    def update_preview(self, *_):
        self.poses = None
        # Side-by-side source parts give every numbered edge a stable reference.
        poses = []; x = 0
        for part in self.parts:
            poses.append([x, 0, 0]); x += max(p[0] for p in part['points'])+max(20, self.spacing*2)
        self.draw(self.views[0], poses)
        try:
            assembled = solve(self.parts, self.links(), self.spacing)
            if len({p.get('quantity', 1) for p in self.parts}) > 1:
                raise ValueError(text('equal'))
            validate_layout(self.parts, assembled, self.spacing)
            self.poses = assembled; self.status.setText(text('ready')); self.save_button.setEnabled(True)
        except ValueError as exc:
            assembled = poses
            self.status.setText(text('incomplete') if any(None in pair for pair in self.links()) else str(exc))
            self.save_button.setEnabled(False)
        self.draw(self.views[1], assembled); self.fit_views()


def open_editor(panel):
    from IPNestingExport import _extract_part_points, _read_boundary_deflection
    rows = sorted({index.row() for index in panel.table.selectionModel().selectedRows()
                   if index.row() < panel.table.rowCount()-panel.control_rows})
    doc = App.getDocument(panel.preview_doc_name)
    selected = {panel._primary_name_for_row(row) for row in rows}
    groups = read_groups(doc)
    matching = [group for group in groups if selected.intersection(group['names'])]
    if len(matching) > 1:
        raise ValueError('Select parts from a single existing group, or ungroup them first.')
    existing = matching[0] if matching else None
    if existing:
        selected.update(existing['names'])
    entries = {}
    for row in range(panel.table.rowCount()-panel.control_rows):
        name = panel._primary_name_for_row(row)
        if name in selected:
            obj = doc.getObject(name)
            if obj:
                entries[name] = dict(name=name, label=panel.table.item(row, 0).text(),
                    quantity=int(panel.table.item(row, 1).text()),
                    points=_extract_part_points(obj, _read_boundary_deflection(panel)))
    names = [name for name in existing['names'] if name in entries] if existing else list(entries)
    names += [name for name in entries if name not in names]
    parts = [entries[name] for name in names]
    if len(parts) < 2 and not existing:
        raise ValueError(text('select'))
    if len({p['quantity'] for p in parts}) > 1 and not existing:
        raise ValueError(text('equal'))
    spacing = panel.get_dimension_value_mm(panel.spacing, 6.0)
    old_links = existing['links'] if existing and names == existing['names'] else None
    dialog = GrainMatchingDialog(parts, spacing, old_links, bool(existing), panel.form)
    if dialog.exec_() != QtGui.QDialog.Accepted:
        return
    replacement = [g for g in groups if g is not existing]
    if not dialog.removed:
        replacement.append(dict(names=names, outlines=[polygon(p['points']) for p in parts], links=dialog.links()))
    write_groups(doc, replacement)
    refresh_labels(panel, replacement)


def refresh_labels(panel, groups=None):
    if groups is None:
        groups = read_groups(App.getDocument(panel.preview_doc_name))
    for row in range(panel.table.rowCount()-panel.control_rows):
        item = panel.table.item(row, 0); name = panel._primary_name_for_row(row)
        group = next((i for i, g in enumerate(groups, 1) if name in g['names']), None)
        item.setToolTip(('%s %d' % (text('title'), group)) if group else '')
        item.setBackground(QtGui.QBrush(QtGui.QColor('#e5f1df')) if group else QtGui.QBrush())
