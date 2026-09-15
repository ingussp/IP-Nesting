# Languages

IP-Nesting starts in English independently of FreeCAD's language. Open the gear
button, hover over **Language**, then select a language. The choice takes effect
immediately and is saved in `BaseApp/Preferences/Mod/IPNesting/Language`.

The searchable chooser contains 50 language entries, including all 24 official
European Union languages. The remaining entries are selected among the most
widely spoken languages by country population. The 24 EU languages are confirmed
by the [European Union language list](https://european-union.europa.eu/principles-countries-history/languages_en).
[Google Translate language selector](https://translate.google.com/?hl=en), matching
the supplied September 2026 screenshot. English names are sorted alphabetically
down six columns, or seven on wider screens, followed by native names. Scrolling
keeps the chooser usable on smaller displays. Search accepts either name or code.

All 50 entries have a separate JSON catalog with the same 506 message keys.
The complete set of visible labels, table headers and buttons used by the main
panel and its dialogs is translated locally in every catalog. Longer technical
tooltips and diagnostic messages without a reviewed translation retain English
wording so they remain accurate. No translation service is used. Native names
and remaining diagnostic wording for less common languages still need review by
a speaker of each language.

## Live text updates

`tr("key")` retains its key and percent-format arguments in a `str` subclass.
Use `ui_widget(QtGui.QLabel, tr("key"))` when constructing a text-bearing widget,
or `ui_call(widget, "setText", tr("key"))` for text setters. Helpers retain
explicit bindings on the Qt object; they never guess keys from user text.
Lists passed to `addItems` and `setHorizontalHeaderLabels` are supported too.

Language changes refresh bound controls and toolbar actions in place, with table
and combo signals blocked. Inputs, option indices, part names and placements
survive. Standard dialog buttons use `translate_buttons`. Generated perimeter
captions have stable language-key properties and update without repacking parts.
Previous English/Latvian perimeter objects in `Nesting_Preview` are recognized.

FreeCAD-owned controls and native file dialogs use FreeCAD/OS language settings.
Historical console/debug reports and external executable output are not rewritten.
Right-to-left layout and font coverage need verification before shipping catalogs
in additional scripts; keep numeric fields and the English-sorted chooser usable.

## Storage and offline maintenance

Every `lng/<code>.json` remains readable and is shipped separately. The language
loader reads only the selected JSON, validates its dictionary and keeps only the
English and active catalogs in memory after a switch. Missing or broken entries
fall back to English; an invalid new choice does not replace the current one.

`lng/index.json` is a small name index, and `lng/perimeters.json` contains only
caption aliases for saved-document compatibility. Include the entire `lng`
directory when distributing the workbench.

To update a translation, edit its `CODE.json` and run the catalog validation
tests. They reject missing keys, empty strings, incompatible percent placeholders
and changed newline counts. Never alter internal schema keys, axes, object Names,
user-provided names or nesting engine identifiers.

```console
python -m unittest discover -s tests
```

To exercise actual Qt controls, run
`tests/gui_language_smoke.py` with FreeCAD's bundled Python. The smoke test uses
an isolated language preference/cache and checks repeated changes, preserved
input and angles, blocked editing signals, captions, selection and search.

