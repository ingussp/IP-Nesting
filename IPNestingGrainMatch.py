"""Texture-matching editor, preview-document persistence and exact group checks."""
import json
import math
import FreeCAD as App
import Part
from PySide import QtCore, QtGui
from IPNestingLanguages import current_language
from IPNestingGrainMatchModel import edges, polygon, solve, transform, rotate


# This prototype has English/Latvian captions; other locales use English.
_MESSAGES = {
    'title': ('Match texture', 'Saskaņot tekstūru'),
    'hint': ('Select the numbered edges that should face each other. Edges are centred; all parts form one rigid group.',
             'Izvēlies malu numurus, kurām jāatrodas pretī. Malas tiek centrētas; visas detaļas veido vienu stingru grupu.'),
    'parts': ('Numbered parts', 'Numurētās detaļas'),
    'assembly': ('Group preview', 'Grupas priekšskatījums'),
    'spacing': ('Part spacing: %.3f mm', 'Atstarpe starp detaļām: %.3f mm'),
    'edge': ('Select edge…', 'Izvēlies malu…'),
    'save': ('Save group', 'Saglabāt grupu'),
    'cancel': ('Cancel', 'Atcelt'),
    'remove': ('Remove matching', 'Noņemt sasaisti'),
    'matched': ('Texture matched parts', 'Ar tekstūru saskaņotās detaļas'),
    'ready': ('Group is ready. It will be nested as one unit.', 'Grupa ir gatava. Tā tiks izkārtota kā viena vienība.'),
    'select': ('Select at least two part rows in the table.', 'Tabulā iezīmē vismaz divas detaļu rindas.'),
    'equal': ('All selected parts must have the same quantity.', 'Visām izvēlētajām detaļām jābūt vienādam skaitam.'),
    'overlap': ('The group overlaps or violates part spacing. Choose different edges.',
                'Grupas detaļas pārklājas vai neievēro atstarpi. Izvēlies citas malas.'),
    'incomplete': ('Fill all edge pairs to connect every part once into a single group.',
                   'Aizpildi visus malu pārus, savienojot visas detaļas vienā grupā bez cikliem.'),
    'partial': ('Partial preview: %d of %d parts connected. Complete the remaining edge pairs.',
                'Daļējs priekšskatījums: savienotas %d no %d detaļām. Aizpildi atlikušos malu pārus.'),
    'curve': ('curved boundary (reference only)', 'izliekta mala (tikai apskatei)'),
    'curves_hint': ('Numbers refer to CAD boundaries, not polygon segments. Join straight edges; curved boundaries are reference-only.',
                    'Numuri apzīmē CAD malas, nevis poligona posmus. Savieno taisnas malas; izliektās malas ir tikai apskatei.'),
}


def text(key):
    return _MESSAGES[key][1 if current_language() == 'lv' else 0]


def _invert_zoom():
    """Read FreeCAD's 'invert zoom' preference so the preview matches the main window."""
    try:
        params = App.ParamGet('User parameter:BaseApp/Preferences/View')
        return bool(params.GetBool('InvertZoom', False))
    except Exception:
        return False


class ZoomView(QtGui.QGraphicsView):
    """Preview view that only zooms with the mouse wheel, matching FreeCAD."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTransformationAnchor(QtGui.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtGui.QGraphicsView.AnchorViewCenter)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        forward = (delta > 0) != _invert_zoom()
        factor = 1.15 if forward else 1.0 / 1.15
        self.scale(factor, factor)
        event.accept()


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


def boundaries_from_wire(points, wire, origin):
    """Map native CAD vertices onto the sampled outline, retaining whole curves.

    Only the editor's numbering changes: the high-resolution outline used by
    the exporter and collision checks is untouched. Collinear CAD edges merge.
    """
    contour = polygon(points)
    starts = set()
    for vertex in wire.Vertexes:
        xy = (vertex.Point.x-origin[0], vertex.Point.y-origin[1])
        index = min(range(len(contour)), key=lambda i: math.dist(contour[i], xy))
        if math.dist(contour[index], xy) < 2e-5:
            starts.add(index)
    starts = sorted(starts) or [0]
    result = []
    for i, first in enumerate(starts):
        last = starts[(i+1) % len(starts)]
        count = (last-first) % len(contour) or len(contour)
        path = [contour[(first+j) % len(contour)] for j in range(count+1)]
        a, b = path[0], path[-1]
        dx, dy = b[0]-a[0], b[1]-a[1]; length = math.hypot(dx, dy)
        curved = length < 1e-7 or any(abs(dx*(p[1]-a[1])-dy*(p[0]-a[0])) > 1e-5*length for p in path)
        result.append(dict(a=a, b=b, curved=curved, path=path))
    return result


def migrate_links(parts, links):
    """Translate legacy segment numbers; never silently reuse a curve fragment."""
    old = edges([dict(points=p['points']) for p in parts]); new = edges(parts)
    mapping = {}
    for before in old:
        for after in new:
            if (before['part'] == after['part'] and not after.get('curved') and
                    before['a'] == after['a'] and before['b'] == after['b']):
                mapping[before['number']] = after['number']
                break
    return [[mapping.get(number) for number in pair] for pair in links]


_OBJECT = 'IPNestingGrainMatches'

# Table roles used to mark deactivated members and the summary row.
ROW_KIND_ROLE = QtCore.Qt.UserRole + 2
GROUP_NAMES_ROLE = QtCore.Qt.UserRole + 3
GROUP_ROW_KIND = 'texture_group'
MEMBER_ROW_KIND = 'texture_member'


def row_kind(panel, row):
    """Return the texture-matching marker for a table row, or ''/None."""
    item = panel.table.item(row, 0)
    return item.data(ROW_KIND_ROLE) if item is not None else None


def _set_row_kind(panel, row, kind):
    item = panel.table.item(row, 0)
    if item is not None:
        item.setData(ROW_KIND_ROLE, kind or '')


def read_groups(doc):
    obj = doc.getObject(_OBJECT) if doc else None
    if not obj:
        return []
    groups = json.loads(obj.GroupsJson)
    if not isinstance(groups, list):
        raise ValueError('Invalid texture-matching group data.')
    return groups


def write_groups(doc, groups):
    obj = doc.getObject(_OBJECT)
    if not obj:
        obj = doc.addObject('App::FeaturePython', _OBJECT)
        obj.addProperty('App::PropertyString', 'GroupsJson', 'Texture matching')
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
        hint = QtGui.QLabel(text('curves_hint')); hint.setWordWrap(True); root.addWidget(hint)
        root.addWidget(QtGui.QLabel(text('spacing') % spacing))
        views = QtGui.QHBoxLayout(); root.addLayout(views, 1)
        self.views = []
        for heading in ('parts', 'assembly'):
            column = QtGui.QVBoxLayout(); views.addLayout(column)
            column.addWidget(QtGui.QLabel(text(heading)))
            view = ZoomView() if heading == 'assembly' else QtGui.QGraphicsView()
            view.setScene(QtGui.QGraphicsScene(view))
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
                    caption = '%d — %s' % (edge['number'], parts[edge['part']]['label'])
                    if edge.get('curved'):
                        caption += ' — '+text('curve')
                    combo.addItem(caption, edge['number'])
                    if edge.get('curved'):
                        combo.model().item(combo.count()-1).setEnabled(False)
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
            self._position_labels(view)

    def _position_labels(self, view):
        scale = max(view.transform().m11(), 1e-9)
        for label, x, y, nx, ny in getattr(view, 'labels', []):
            label.setScale(1/scale)
            bounds = label.boundingRect()
            offset_x = offset_y = 0.0
            if nx is not None and ny is not None:
                # Shift edge numbers inward so they sit inside their part.
                shift = (abs(nx)*bounds.width()/2 + abs(ny)*bounds.height()/2 + 3.0)/scale
                offset_x = nx*shift
                offset_y = ny*shift
            label.setPos(x+offset_x-bounds.width()/(2*scale),
                         y+offset_y-bounds.height()/(2*scale))

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
            if pose is None:
                continue
            points = transform(part['points'], pose)
            poly = QtGui.QPolygonF([QtCore.QPointF(x, -y) for x, y in points])
            outline = outline.united(poly.boundingRect())
            scene.addPolygon(poly, QtGui.QPen(QtGui.QColor('#355365')), QtGui.QBrush(QtGui.QColor(colours[i % len(colours)])))
            cx = (min(p[0] for p in points)+max(p[0] for p in points))/2
            cy = (min(p[1] for p in points)+max(p[1] for p in points))/2
            label = scene.addSimpleText(part['label'])
            view.labels.append((label, cx, -cy, None, None))
        view.outline_rect = outline
        for edge in numbered:
            if poses[edge['part']] is None:
                continue
            path = edge.get('path', [edge['a'], edge['b']])
            # Put one label near the middle of each complete CAD boundary.
            lengths = [math.dist(a, b) for a, b in zip(path, path[1:])]
            remaining = sum(lengths)/2
            anchor = edge['a']; tangent = None
            for a, b, length in zip(path, path[1:], lengths):
                if remaining <= length and length > 0:
                    f = remaining/length
                    anchor = [a[k]+(b[k]-a[k])*f for k in (0, 1)]
                    tangent = [b[0]-a[0], b[1]-a[1]]; break
                remaining -= length
            if tangent is None:
                tangent = [path[-1][0]-path[0][0], path[-1][1]-path[0][1]]
            # Interior lies left of a CCW boundary; rotate the inward normal with the pose.
            dx, dy = tangent; length = math.hypot(dx, dy)
            nx = -dy/length if length > 1e-7 else 0.0
            ny = dx/length if length > 1e-7 else 0.0
            pose = poses[edge['part']]
            nx, ny = rotate([nx, ny], pose[2])
            x, y = transform([anchor], pose)[0]
            label = scene.addSimpleText(str(edge['number']))
            label.setBrush(QtGui.QBrush(QtGui.QColor('#666666' if edge.get('curved') else '#ab2424')))
            font = label.font(); font.setBold(True); label.setFont(font)
            view.labels.append((label, x, -y, nx, -ny))

    def update_preview(self, *_):
        self.poses = None
        # Side-by-side source parts give every numbered edge a stable reference.
        poses = []; x = 0
        for part in self.parts:
            width = max(p[0] for p in part['points'])
            poses.append([x, 0, 0]); x += width+max(30, width*.12, self.spacing*2)
        self.draw(self.views[0], poses)
        assembled = [None]*len(self.parts)
        links = self.links()
        # Show each valid component immediately, and keep it visible if a later
        # row introduces a cycle. Never substitute the unassembled source view.
        try:
            assembled = solve(self.parts, links, self.spacing, partial=True)
        except ValueError:
            for size in range(len(links)-1, 0, -1):
                try:
                    assembled = solve(self.parts, links[:size], self.spacing, partial=True)
                    break
                except ValueError:
                    pass
        try:
            complete = solve(self.parts, links, self.spacing)
            assembled = complete
            if len({p.get('quantity', 1) for p in self.parts}) > 1:
                raise ValueError(text('equal'))
            validate_layout(self.parts, assembled, self.spacing)
            self.poses = assembled; self.status.setText(text('ready')); self.save_button.setEnabled(True)
        except ValueError as exc:
            connected = sum(p is not None for p in assembled)
            self.status.setText((text('partial') % (connected, len(self.parts)) if connected else text('incomplete'))
                               if any(None in pair for pair in links) else str(exc))
            self.save_button.setEnabled(False)
        self.draw(self.views[1], assembled); self.fit_views()


def open_editor(panel):
    from IPNestingExport import _extract_part_points, _read_boundary_deflection, _extract_part_candidate_wires
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
        if row_kind(panel, row) == GROUP_ROW_KIND:
            continue
        name = panel._primary_name_for_row(row)
        if name in selected:
            obj = doc.getObject(name)
            if obj:
                native = []
                resolution = _read_boundary_deflection(panel)
                candidates = _extract_part_candidate_wires(obj, resolution, native_wires=native)
                points = _extract_part_points(obj, resolution, candidates=candidates)
                origin = [min(p[k] for p in candidates[0]) for k in (0, 1)]
                entries[name] = dict(name=name, label=panel.table.item(row, 0).text(),
                    quantity=int(panel.table.item(row, 1).text()),
                    points=points, matching_edges=boundaries_from_wire(points, native[0], origin))
    names = [name for name in existing['names'] if name in entries] if existing else list(entries)
    names += [name for name in entries if name not in names]
    parts = [entries[name] for name in names]
    if len(parts) < 2 and not existing:
        raise ValueError(text('select'))
    if len({p['quantity'] for p in parts}) > 1 and not existing:
        raise ValueError(text('equal'))
    spacing = panel.get_dimension_value_mm(panel.spacing, 6.0)
    old_links = existing['links'] if existing and names == existing['names'] else None
    if old_links is not None:
        if 'matching_edges' not in existing:
            old_links = migrate_links(parts, old_links)
        elif existing['matching_edges'] != [p['matching_edges'] for p in parts]:
            old_links = None  # Changed geometry must not silently relabel connections.
    dialog = GrainMatchingDialog(parts, spacing, old_links, bool(existing), panel.form)
    if dialog.exec_() != QtGui.QDialog.Accepted:
        return
    replacement = [g for g in groups if g is not existing]
    if not dialog.removed:
        replacement.append(dict(names=names, outlines=[polygon(p['points']) for p in parts], links=dialog.links(),
                                matching_edges=[p['matching_edges'] for p in parts]))
    write_groups(doc, replacement)
    refresh_labels(panel, replacement)
    _refresh_grain_layout(panel)


def _refresh_grain_layout(panel):
    """Re-run the live grain layout so freshly grouped parts move to their square."""
    try:
        controller = getattr(panel, '_grain', None)
        if controller is not None:
            controller._apply_live_layout()
    except Exception:
        pass


def _matched_part_info(panel, groups):
    """Map each group to the (labels, quantity, names) currently shown in the table."""
    info = {}
    for row in range(panel.table.rowCount()-panel.control_rows):
        item = panel.table.item(row, 0)
        if item is None or row_kind(panel, row) == GROUP_ROW_KIND:
            continue
        name = panel._primary_name_for_row(row)
        if not name:
            continue
        quantity = 1
        qty_item = panel.table.item(row, 1)
        if qty_item is not None:
            try:
                quantity = int(str(qty_item.text()).strip())
            except Exception:
                quantity = 1
        info[name] = (item.text(), quantity)
    result = []
    for group in groups:
        names = [n for n in group.get('names', []) if n in info]
        if not names:
            continue
        result.append(([info[n][0] for n in names], info[names[0]][1], names))
    return result


def _set_member_row_active(panel, row, active):
    """Grey out (or restore) a part row and disable (or enable) its controls."""
    try:
        for col in range(panel.table.columnCount()):
            item = panel.table.item(row, col)
            if item is None:
                continue
            flags = item.flags()
            if active:
                flags |= QtCore.Qt.ItemIsEditable
                item.setBackground(QtGui.QBrush())
            else:
                flags &= ~QtCore.Qt.ItemIsEditable
                item.setBackground(QtGui.QBrush(QtGui.QColor('#e4e4e4')))
            item.setFlags(flags)
        for col in (3, 4, 5):
            widget = panel.table.cellWidget(row, col)
            if widget is None:
                continue
            for checkbox in widget.findChildren(QtGui.QCheckBox):
                checkbox.setEnabled(active)
            for combo in widget.findChildren(QtGui.QComboBox):
                combo.setEnabled(active)
    except Exception:
        pass


def _render_summary_rows(panel, groups):
    """Replace texture-matched summary rows with one full part row per group."""
    table = panel.table
    for row in range(table.rowCount()-panel.control_rows-1, -1, -1):
        if row_kind(panel, row) == GROUP_ROW_KIND:
            table.removeRow(row)
    if not groups:
        return
    pos = max(0, table.rowCount()-panel.control_rows)
    for labels, quantity, names in _matched_part_info(panel, groups):
        table.insertRow(pos)
        _fill_group_row(panel, pos, labels, quantity, names)
        pos += 1


def _fill_group_row(panel, row, labels, quantity, names):
    """Populate a group row like a new part, named `Texture matched parts: …`."""
    table = panel.table
    name_item = QtGui.QTableWidgetItem('%s: %s' % (text('matched'), ', '.join(labels)))
    name_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
    name_item.setBackground(QtGui.QBrush(QtGui.QColor('#e5f1df')))
    name_item.setData(ROW_KIND_ROLE, GROUP_ROW_KIND)
    name_item.setData(GROUP_NAMES_ROLE, json.dumps(names))
    table.setItem(row, 0, name_item)

    qty_item = QtGui.QTableWidgetItem(str(quantity))
    qty_item.setTextAlignment(QtCore.Qt.AlignCenter)
    qty_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
    table.setItem(row, 1, qty_item)

    rotations_item = QtGui.QTableWidgetItem(str(panel.get_default_rotations()))
    rotations_item.setTextAlignment(QtCore.Qt.AlignCenter)
    rotations_item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)
    table.setItem(row, 2, rotations_item)

    table.setCellWidget(row, 3, _centered_checkbox())
    grain_widget = _grain_widget()
    table.setCellWidget(row, 4, grain_widget)
    _connect_group_grain_widgets(panel, names, grain_widget)
    table.setCellWidget(row, 5, _centered_checkbox())

    vertical = QtGui.QTableWidgetItem('')
    vertical.setFlags(QtCore.Qt.NoItemFlags)
    table.setVerticalHeaderItem(row, vertical)


def _connect_group_grain_widgets(panel, names, grain_widget):
    """Wire a group row's grain checkbox/combo to the panel's grain controller."""
    try:
        controller = getattr(panel, '_grain', None)
        if controller is None:
            return
        cb = grain_widget.findChild(QtGui.QCheckBox)
        combo = grain_widget.findChild(QtGui.QComboBox)
        if cb is not None and combo is not None:
            controller._connect_group_grain_widgets(names, cb, combo)
    except Exception:
        pass


def _centered_checkbox():
    """Build the standard centred checkbox container used by part rows."""
    widget = QtGui.QWidget()
    layout = QtGui.QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addStretch()
    layout.addWidget(QtGui.QCheckBox())
    layout.addStretch()
    return widget


def _grain_widget():
    """Build the grain-direction checkbox + axis combo used by part rows."""
    widget = QtGui.QWidget()
    layout = QtGui.QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addStretch()
    layout.addWidget(QtGui.QCheckBox())
    combo = QtGui.QComboBox()
    combo.addItems(['X', 'Y'])
    combo.setCurrentIndex(0)
    combo.setFixedWidth(70)
    layout.addWidget(combo)
    layout.addStretch()
    return widget


def refresh_labels(panel, groups=None):
    """Deactivate matched member rows and keep a bottom summary row up to date."""
    if groups is None:
        groups = read_groups(App.getDocument(panel.preview_doc_name))
    matched = set()
    for group in groups:
        matched.update(group.get('names', []))
    for row in range(panel.table.rowCount()-panel.control_rows):
        if row_kind(panel, row) == GROUP_ROW_KIND:
            continue
        name = panel._primary_name_for_row(row)
        is_member = bool(name and name in matched)
        _set_row_kind(panel, row, MEMBER_ROW_KIND if is_member else None)
        _set_member_row_active(panel, row, not is_member)
        item = panel.table.item(row, 0)
        if item is not None:
            group = next((i for i, g in enumerate(groups, 1) if name in g['names']), None)
            item.setToolTip(('%s %d' % (text('title'), group)) if group else '')
    _render_summary_rows(panel, groups)
