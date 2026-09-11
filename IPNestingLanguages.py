"""JSON-backed IP-Nesting translations and the workbench Settings menu."""

import json
import os
import re

import FreeCAD as App

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
PREFERENCES = "User parameter:BaseApp/Preferences/Mod/IPNesting"
LANGUAGES = (("en", "English"), ("lv", "Latviešu"))
_catalogs = {}
_active_language = None
_menu = None
_percent = re.compile(r"%(?:\([^)]+\))?[#0 +\-]*\d*(?:\.\d+)?[hlL]?[diouxXeEfFgGcrsa%]")


# Read the saved language, choosing English for a fresh or invalid preference.
def saved_language():
    try:
        code = App.ParamGet(PREFERENCES).GetString("Language", "en")
    except Exception:
        code = "en"
    return code if code in dict(LANGUAGES) else "en"


# Keep the current session in one language; saved changes take effect after restart.
def current_language():
    global _active_language
    if _active_language is None:
        _active_language = saved_language()
    return _active_language


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
        return english
    return text


# Recognize saved perimeter captions in any supported language without translating group IDs.
def perimeter_labels(canonical):
    key = "perimeter.with_grain" if "with grain" in canonical else "perimeter.without_grain"
    return {canonical} | {_load_catalog(code).get(key, canonical) for code, _ in LANGUAGES}


# Match generated object labels using each catalog's caption and suffix template.
def perimeter_object_labels(canonical, kind):
    key = "perimeter.with_grain" if "with grain" in canonical else "perimeter.without_grain"
    legacy = canonical + (" Label" if kind == "label" else " Border")
    result = {legacy}
    for code, _ in LANGUAGES:
        catalog = _load_catalog(code)
        template = catalog.get("perimeter." + kind, "%s")
        if _percent.findall(template) == ["%s"]:
            result.add(template % catalog.get(key, canonical))
    return result


# Save a supported choice without changing the language of an already-open task panel.
def set_language(code):
    if code not in dict(LANGUAGES):
        raise ValueError("Unsupported language code: " + str(code))
    current_language()
    App.ParamGet(PREFERENCES).SetString("Language", code)
    if hasattr(App, "saveParameter"):
        App.saveParameter()


# Give standard buttons in our own dialogs the application's selected language.
def translate_buttons(button_box):
    from PySide import QtGui
    for name, key in (("Ok", "common.ok"), ("Cancel", "common.cancel"),
                      ("Close", "close"), ("Save", "common.save"),
                      ("Apply", "common.apply"), ("Yes", "common.yes"), ("No", "common.no")):
        button = button_box.button(getattr(QtGui.QDialogButtonBox, name))
        if button is not None:
            button.setText(tr(key))


# Persist the selection and explain when all dialogs and labels will use it.
def _choose_language(code):
    from PySide import QtGui
    import FreeCADGui as Gui
    set_language(code)
    message = QtGui.QMessageBox(Gui.getMainWindow())
    message.setWindowTitle(tr("settings.title"))
    message.setText(tr("settings.restart") % dict(LANGUAGES)[code])
    message.setIcon(QtGui.QMessageBox.Information)
    message.addButton(tr("common.ok"), QtGui.QMessageBox.AcceptRole)
    message.exec_()


# Show the Settings popup; hovering over Language opens the language submenu.
def show_settings():
    global _menu
    from functools import partial
    from PySide import QtGui
    import FreeCADGui as Gui
    if _menu is not None:
        _menu.close()
        _menu.deleteLater()
    _menu = QtGui.QMenu(Gui.getMainWindow())
    _menu.setTitle(tr("settings.title"))
    # Keep Python references as well as Qt parents for FreeCAD's PySide compatibility layer.
    languages = QtGui.QMenu(tr("settings.language"), _menu)
    _menu._language_menu = languages
    _menu.addMenu(languages)
    group = QtGui.QActionGroup(languages)
    _menu._language_group = group
    _menu._language_actions = []
    group.setExclusive(True)
    for code, label in LANGUAGES:
        action = languages.addAction(label)
        _menu._language_actions.append(action)
        action.setData(code)
        action.setCheckable(True)
        action.setChecked(code == saved_language())
        group.addAction(action)
        action.triggered.connect(partial(_language_triggered, code))
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
