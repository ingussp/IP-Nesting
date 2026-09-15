"""JSON-backed IP-Nesting translations and the workbench Settings menu."""

import json
import os
import re

import FreeCAD as App

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
PREFERENCES = "User parameter:BaseApp/Preferences/Mod/IPNesting"


# Load the small name index independently of the translated message catalogs.
def _language_names():
    try:
        with open(os.path.join(DIRECTORY, "lng", "index.json"), encoding="utf-8") as handle:
            rows = json.load(handle)
        names = [(row["code"], row["english"] + " (" + row["native"] + ")") for row in rows
                 if re.fullmatch(r"[A-Za-z0-9-]+", row["code"])]
        return tuple(sorted(names, key=lambda item: item[1].casefold()))
    except (OSError, ValueError, KeyError, TypeError):
        return (("en", "English (English)"), ("lv", "Latvian (Latviešu)"))


LANGUAGES = _language_names()
_catalogs = {}
_active_language = None
_menu = None
_perimeters = None
_percent = re.compile(r"%(?:\([^)]+\))?[#0 +\-]*\d*(?:\.\d+)?[hlL]?[diouxXeEfFgGcrsa%]")


# Read the saved language, choosing English for a fresh or invalid preference.
def saved_language():
    try:
        code = App.ParamGet(PREFERENCES).GetString("Language", "en")
    except Exception:
        code = "en"
    return code if code in dict(LANGUAGES) else "en"


# Return the currently active language, restoring the preference on first use.
def current_language():
    global _active_language
    if _active_language is None:
        _active_language = saved_language()
    return _active_language


# List installed JSON catalogs without loading any unselected translation.
def available_languages():
    return {code for code, _ in LANGUAGES
            if os.path.isfile(os.path.join(DIRECTORY, "lng", code + ".json"))}


# Load UTF-8 catalogs safely, accepting only dictionaries of string values.
def _load_catalog(code):
    if code not in dict(LANGUAGES):
        code = "en"
    if code not in _catalogs:
        try:
            with open(os.path.join(DIRECTORY, "lng", code + ".json"), encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                data = {}
            _catalogs[code] = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError):
            _catalogs[code] = {}
    return _catalogs[code]


# Resolve a stable message key with English fallback and compatible percent placeholders.
def tr(key):
    english = _load_catalog("en").get(key, key)
    text = _load_catalog(current_language()).get(key, english)
    if not text or _percent.findall(text) != _percent.findall(english):
        text = english
    return TranslatedText(text, key)


# Keep the message key and format arguments until a translated value is bound to Qt.
class TranslatedText(str):
    # Build a string accepted by Qt while retaining its translation source.
    def __new__(cls, value, key, arguments=None, formatted=False):
        instance = super().__new__(cls, value)
        instance.key = key
        instance.arguments = arguments
        instance.formatted = formatted
        return instance

    # Preserve formatting arguments so dimensions and counts can be translated in place.
    def __mod__(self, arguments):
        return TranslatedText(str(self) % arguments, self.key, arguments, True)


# Encode only explicitly translated values; plain user text is never inferred as a key.
def _encode(value):
    if isinstance(value, TranslatedText):
        return {"key": value.key, "arguments": _encode(value.arguments), "formatted": value.formatted}
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    return value


# Recreate a bound value using the new language and the original numeric or user arguments.
def _resolve(value):
    if isinstance(value, dict) and "key" in value:
        text = tr(value["key"])
        if value["formatted"]:
            arguments = _resolve(value["arguments"])
            text = text % (tuple(arguments) if isinstance(arguments, list) else arguments)
        return str(text)
    if isinstance(value, list):
        return [_resolve(item) for item in value]
    return value


# Determine whether arguments contain a translation, including a list of table headers.
def _has_translation(value):
    if isinstance(value, TranslatedText):
        return True
    return isinstance(value, (list, tuple)) and any(_has_translation(item) for item in value)


# Store bindings on Qt objects, or in a dedicated item role for table items.
def _bindings(obj, value=None):
    from PySide import QtCore
    if hasattr(obj, "setProperty"):
        if value is not None:
            obj.setProperty("IPNestingTranslations", json.dumps(value, ensure_ascii=False))
        raw = obj.property("IPNestingTranslations")
    else:
        role = int(QtCore.Qt.UserRole) + 751
        if value is not None:
            obj.setData(role, json.dumps(value, ensure_ascii=False))
        raw = obj.data(role)
    return json.loads(raw) if isinstance(raw, str) and raw else {}


# Apply a Qt text operation and remember its keys without altering input values or signals.
def ui_call(obj, method, *arguments):
    result = getattr(obj, method)(*arguments)
    if method == "addItems":
        start = obj.count() - len(arguments[0])
        for offset, value in enumerate(arguments[0]):
            if _has_translation(value):
                ui_call(obj, "setItemText", start + offset, value)
        return result
    bindings = _bindings(obj)
    if not bindings and not _has_translation(arguments):
        return result
    identity = method
    if method in ("setItemText", "setItemData"):
        identity += ":" + str(arguments[0])
        if method == "setItemData" and len(arguments) > 2:
            identity += ":" + str(int(arguments[2]))
    if _has_translation(arguments):
        bindings[identity] = [method, _encode(arguments)]
    else:
        bindings.pop(identity, None)
    _bindings(obj, bindings)
    return result


# Construct a text-bearing Qt control and retain only text explicitly produced by tr().
def ui_widget(factory, *arguments, **kwargs):
    widget = factory(*arguments, **kwargs)
    if arguments and _has_translation(arguments[0]):
        setter = "setTitle" if hasattr(widget, "setTitle") else "setText"
        ui_call(widget, setter, arguments[0])
    return widget


# Update bound Qt text without emitting combo/table signals or recreating controls.
def _refresh_widget(obj):
    bindings = _bindings(obj)
    if not bindings:
        return
    previous = obj.blockSignals(True) if hasattr(obj, "blockSignals") else None
    try:
        for method, arguments in bindings.values():
            getattr(obj, method)(*_resolve(arguments))
    finally:
        if previous is not None:
            obj.blockSignals(previous)


# Refresh only IP-Nesting-bound controls and its two commands in the current GUI.
def refresh_ui():
    try:
        from PySide import QtGui
        import FreeCADGui as Gui
    except ImportError:
        return
    application = QtGui.QApplication.instance()
    if application is None:
        return
    for widget in application.allWidgets():
        try:
            if widget.property("IPNestingWindow"):
                _set_window_direction(widget)
            _refresh_widget(widget)
            if isinstance(widget, QtGui.QTableWidget):
                previous = widget.blockSignals(True)
                try:
                    for column in range(widget.columnCount()):
                        item = widget.horizontalHeaderItem(column)
                        if item is not None:
                            _refresh_widget(item)
                    for row in range(widget.rowCount()):
                        for column in range(widget.columnCount()):
                            item = widget.item(row, column)
                            if item is not None:
                                _refresh_widget(item)
                finally:
                    widget.blockSignals(previous)
        except RuntimeError:
            continue  # A deleted dialog can disappear during a queued GUI update.
    main = Gui.getMainWindow()
    if main is not None:
        commands = {"IP_RunNesting": ("nesting_tool", "open_the_nesting_configuration_panel"),
                    "IP_NestingSettings": ("settings.title", "settings.tooltip")}
        for action in main.findChildren(QtGui.QAction):
            keys = commands.get(action.objectName())
            if keys:
                action.setText(tr(keys[0]))
                action.setToolTip(tr(keys[1]))
    if hasattr(Gui, "listWorkbenches"):
        workbench = Gui.listWorkbenches().get("IPNestingWorkbench")
        if workbench is not None:
            workbench.ToolTip = tr("optimal_parts_nesting_on_a_sheet")


# Apply the selected writing direction only to our windows, keeping numbers and paths left-to-right.
def _set_window_direction(widget):
    from PySide import QtCore, QtGui
    direction = QtCore.Qt.RightToLeft if current_language() in {"ar", "fa", "he", "ur", "ps"} else QtCore.Qt.LeftToRight
    widget.setLayoutDirection(direction)
    for field in widget.findChildren(QtGui.QLineEdit):
        field.setLayoutDirection(QtCore.Qt.LeftToRight)


# Register an owned panel or dialog after construction so host FreeCAD windows are not changed.
def register_window(widget):
    widget.setProperty("IPNestingWindow", True)
    _set_window_direction(widget)


# Read compact perimeter aliases without opening any additional translated catalog.
def _perimeter_catalogs():
    global _perimeters
    if _perimeters is None:
        try:
            with open(os.path.join(DIRECTORY, "lng", "perimeters.json"), encoding="utf-8") as handle:
                _perimeters = json.load(handle)
        except (OSError, ValueError):
            _perimeters = {"en": _load_catalog("en")}
    return _perimeters.values()


# Recognize saved perimeter captions in any supported language without translating group IDs.
def perimeter_labels(canonical):
    key = "perimeter.with_grain" if "with grain" in canonical else "perimeter.without_grain"
    return {canonical} | {catalog.get(key, canonical) for catalog in _perimeter_catalogs()}


# Match generated object labels using each catalog's caption and suffix template.
def perimeter_object_labels(canonical, kind):
    key = "perimeter.with_grain" if "with grain" in canonical else "perimeter.without_grain"
    legacy = canonical + (" Label" if kind == "label" else " Border")
    result = {legacy}
    for catalog in _perimeter_catalogs():
        template = catalog.get("perimeter." + kind, "%s")
        if _percent.findall(template) == ["%s"]:
            result.add(template % catalog.get(key, canonical))
    return result


# Mark generated geometry with stable language keys, independent of its visible label.
def tag_perimeter(obj, canonical, kind):
    for name in ("IPNestingCaptionKey", "IPNestingCaptionKind"):
        if name not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", name, "IPNesting")
        obj.setEditorMode(name, 2)
    obj.IPNestingCaptionKey = "perimeter.with_grain" if "with grain" in canonical else "perimeter.without_grain"
    obj.IPNestingCaptionKind = kind


# Translate generated preview captions in place, preserving all placements and dimensions.
def refresh_perimeters():
    if not hasattr(App, "listDocuments"):
        return
    for document in App.listDocuments().values():
        changed = False
        for obj in document.Objects:
            key = getattr(obj, "IPNestingCaptionKey", "")
            kind = getattr(obj, "IPNestingCaptionKind", "")
            if not key and document.Name == "Nesting_Preview":
                for canonical in ("Parts with grain direction", "Parts without grain direction"):
                    if obj.Name.startswith("GrainPerimeter") and obj.Label in perimeter_object_labels(canonical, "border"):
                        tag_perimeter(obj, canonical, "border")
                    elif (obj.Label in perimeter_object_labels(canonical, "label")
                          and hasattr(obj, "Text")
                          and set(obj.Text).intersection(perimeter_labels(canonical))):
                        tag_perimeter(obj, canonical, "label")
                key = getattr(obj, "IPNestingCaptionKey", "")
                kind = getattr(obj, "IPNestingCaptionKind", "")
            if key not in ("perimeter.with_grain", "perimeter.without_grain") or kind not in ("label", "border"):
                continue
            caption = tr(key)
            obj.Label = tr("perimeter." + kind) % caption
            if kind == "label" and hasattr(obj, "Text"):
                obj.Text = [str(caption)]
            changed = True
        if changed:
            document.recompute()
            try:
                import FreeCADGui as Gui
                gui_document = Gui.getDocument(document.Name)
                if gui_document is not None:
                    gui_document.activeView().fitAll()
            except (AttributeError, RuntimeError):
                pass


# Activate a validated catalog immediately, persist the choice and refresh open controls.
def set_language(code):
    global _active_language
    if code not in dict(LANGUAGES):
        raise ValueError("Unsupported language code: " + str(code))
    catalog = _load_catalog(code)
    if not catalog:
        raise ValueError("Language catalog is missing or invalid: " + code)
    App.ParamGet(PREFERENCES).SetString("Language", code)
    if hasattr(App, "saveParameter"):
        App.saveParameter()
    _active_language = code
    refresh_ui()
    refresh_perimeters()
    for cached in list(_catalogs):
        if cached not in ("en", code):
            del _catalogs[cached]


# Give standard buttons in our own dialogs the application's selected language.
def translate_buttons(button_box):
    from PySide import QtGui
    for name, key in (("Ok", "common.ok"), ("Cancel", "common.cancel"),
                      ("Close", "close"), ("Save", "common.save"),
                      ("Apply", "common.apply"), ("Yes", "common.yes"), ("No", "common.no")):
        button = button_box.button(getattr(QtGui.QDialogButtonBox, name))
        if button is not None:
            ui_call(button, "setText", tr(key))


# Apply a language as soon as its choice is clicked and close the chooser.
def _choose_language(code):
    from PySide import QtGui
    import FreeCADGui as Gui
    try:
        set_language(code)
    except (OSError, ValueError) as error:
        QtGui.QMessageBox.warning(Gui.getMainWindow(), tr("settings.title"),
                                 tr("settings.load_failed") % str(error))
        return
    if _menu is not None:
        _menu.close()


# Filter by English name, native name or code, keeping alphabetical columns.
def _filter_languages(grid, buttons, query, columns):
    query = query.casefold().strip()
    matching = []
    for code, button in buttons:
        grid.removeWidget(button)
        visible = not query or query in (code + " " + button.text()).casefold()
        button.setVisible(visible)
        if visible:
            matching.append(button)
    rows = max(1, (len(matching) + columns - 1) // columns)
    for index, button in enumerate(matching):
        grid.addWidget(button, index % rows, index // rows)


# Show a large, searchable language submenu with six or seven alphabetical columns.
def show_settings():
    global _menu
    from functools import partial
    from PySide import QtGui, QtCore
    import FreeCADGui as Gui
    if _menu is not None:
        _menu.close()
        _menu.deleteLater()
    _menu = QtGui.QMenu(Gui.getMainWindow())
    _menu.setTitle(tr("settings.title"))
    _menu.setMinimumWidth(320)
    _menu.setStyleSheet("QMenu { font-size: 15px; } QMenu::item { padding: 14px 28px; }")
    # Keep Python references as well as Qt parents for FreeCAD's PySide compatibility layer.
    languages = QtGui.QMenu(tr("settings.language"), _menu)
    _menu._language_menu = languages
    _menu.addMenu(languages)
    container = QtGui.QWidget(languages)
    container.setLayoutDirection(QtCore.Qt.LeftToRight)
    layout = QtGui.QVBoxLayout(container)
    search = QtGui.QLineEdit(container)
    search.setPlaceholderText(tr("settings.search"))
    search.setMinimumHeight(38)
    search.setAccessibleName(tr("settings.search"))
    layout.addWidget(search)
    scroll = QtGui.QScrollArea(container)
    scroll.setWidgetResizable(True)
    contents = QtGui.QWidget(scroll)
    grid = QtGui.QGridLayout(contents)
    grid.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
    scroll.setWidget(contents)
    layout.addWidget(scroll)
    application = QtGui.QApplication.instance()
    if hasattr(application, "screenAt"):
        screen_object = application.screenAt(QtGui.QCursor.pos()) or application.primaryScreen()
        screen = screen_object.availableGeometry()
    else:
        screen = application.desktop().availableGeometry(QtGui.QCursor.pos())
    columns = 7 if screen.width() >= 1600 else 6
    row_count = (len(LANGUAGES) + columns - 1) // columns
    container.setFixedSize(min(1720, screen.width() - 48),
                           min(row_count * 64 + 100, 900, screen.height() - 100))
    group = QtGui.QButtonGroup(container)
    group.setExclusive(True)
    buttons = []
    available = available_languages()
    for code, label in sorted(LANGUAGES, key=lambda item: item[1].casefold()):
        english, native = label.rsplit(" (", 1)
        button = QtGui.QPushButton(english + "\n(" + native, contents)
        button.setAccessibleName(label)
        button.setMinimumSize(210, 58)
        button.setCheckable(True)
        button.setChecked(code == current_language())
        button.setEnabled(code in available)
        if code not in available:
            button.setToolTip(tr("settings.unavailable"))
        button.setStyleSheet("QPushButton { text-align: left; padding: 6px; font-size: 14px; } "
                             "QPushButton:checked { background: #d8eaff; color: #102848; }")
        group.addButton(button)
        button.clicked.connect(partial(_language_triggered, code))
        buttons.append((code, button))
    search.textChanged.connect(partial(_filter_languages, grid, buttons, columns=columns))
    _filter_languages(grid, buttons, "", columns)
    widget_action = QtGui.QWidgetAction(languages)
    widget_action.setDefaultWidget(container)
    languages.addAction(widget_action)
    _menu._language_buttons = buttons
    _menu._language_search = search
    _menu._language_widget = container
    _menu._language_group = group
    _menu._widget_action = widget_action
    _menu.popup(QtGui.QCursor.pos())


# Adapt QAction's checked argument while preserving the selected language code.
def _language_triggered(code, checked=False):
    _choose_language(code)


# Expose a document-independent Settings command next to Run Nesting.
class SettingsCommand:
    # Provide translated command text and the repository-local scalable settings icon.
    def GetResources(self):
        return {"MenuText": tr("settings.title"), "ToolTip": tr("settings.tooltip"),
                "Pixmap": os.path.join(DIRECTORY, "icons", "settings.svg")}

    # Open the popup menu at the cursor position.
    def Activated(self):
        show_settings()

    # Allow changing the application language even when no document is open.
    def IsActive(self):
        return True

