# Languages

IP-Nesting starts in English independently of FreeCAD's own language. Open the
gear button next to Run Nesting, hover over **Language**, and select **Latviešu**
or **English**. The choice is saved in FreeCAD preferences under
`BaseApp/Preferences/Mod/IPNesting/Language`. Restart FreeCAD to apply the selection
consistently to all commands, dialogs and newly generated preview labels.

`IPNestingLanguages.py` loads UTF-8 dictionaries from `lng/en.json` and
`lng/lv.json`. Call `tr("message_key")` for text owned by the workbench. English
is the fallback for missing, empty or incompatible translated messages. Preserve
percent placeholders (`%s`, `%d`, `%.2f`, `%%`), newlines and HTML markup when
editing translations. Existing user-provided object names and external engine
output are not translated. FreeCAD-owned controls and operating-system file
dialogs retain the host application's language.

Display text must never become a JSON schema key, object Name, preference key,
file path, enum code or group identifier. Perimeters retain canonical group names
and recognize captions saved in either language during cleanup. Numeric entry
continues to use the existing unit and decimal parsing rules.

To add a language, copy `lng/en.json`, translate all values, and register its code
and native display name in `LANGUAGES`. Keep keys stable. Include `lng/` and
`icons/` when distributing the workbench; no external translation service or
network connection is needed at runtime.

Run `python -m unittest discover -s tests` to check first launch, persistence,
fallback and catalog formatting. A visual check of translated dialog sizes is
also recommended after changing long labels.
