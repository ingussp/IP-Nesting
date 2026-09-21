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
The catalogs include translated labels, dialog text, tooltips and diagnostics.
Technical identifiers (such as `Shape.Area`, function names and file names),
format placeholders and genuine cognates can remain identical to English.
Catalogs are prepared ahead of time; the workbench does not call a translation
service. The completed catalogs include offline machine-assisted translations.
Automated validation checks coverage and formatting, not linguistic accuracy;
native-speaker review of the technical terminology is still recommended.

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
caption aliases for saved-document compatibility. Each language code stores its
current captions; additional legacy entries preserve earlier saved captions.
Include the entire `lng` directory when distributing the workbench.

To update a translation, edit its `CODE.json` and run the catalog validation
tests. They reject missing keys, empty strings, incompatible percent placeholders,
changed newline counts and untranslated English messages (with explicit exceptions
for technical strings and genuine cognates). Never alter internal schema keys,
axes, object Names, user-provided names or nesting engine identifiers.

`tools/build_remaining_catalogs.py` is a bootstrap helper, not a translator.
It preserves completed translations and existing perimeter aliases. Any newly
scaffolded English fallback messages must be translated before the catalog
coverage tests will pass.

```console
python -m unittest discover -s tests
```

To exercise actual Qt controls, run
`tests/gui_language_smoke.py` with FreeCAD's bundled Python. The smoke test uses
an isolated language preference/cache and checks repeated changes, preserved
input and angles, blocked editing signals, captions, selection and search.

