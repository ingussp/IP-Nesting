# Bundled CLI-nesting

The workbench selects the bundled executable using the operating system and CPU
architecture. GPU discovery and Run Nesting use the same selection function;
an unrelated executable on PATH is never selected.

| System | Relative executable path | Included now |
| --- | --- | --- |
| Windows x64 | `windows-x86_64/clinesting.exe` | Yes |
| Linux Intel/AMD 64-bit | `linux-x86_64/clinesting` | No |
| Linux ARM 64-bit | `linux-aarch64/clinesting` | No |
| macOS Intel and Apple Silicon | `macos-universal2/clinesting` | No |
| macOS Intel fallback | `macos-x86_64/clinesting` | No |
| macOS Apple Silicon fallback | `macos-arm64/clinesting` | No |

For macOS, prefer one Universal 2 binary containing both `x86_64` and `arm64`
slices. Two separately built binaries can also be supplied in the fallback
folders. See [Apple's universal binary documentation](https://developer.apple.com/documentation/apple-silicon/building-a-universal-macos-binary).
Linux and macOS binaries must have their executable permission set. Platform
selection is tested using mocked platforms; only the Windows executable has
been built and exercised in this change.

The Windows executable uses a static MSVC runtime and dynamically discovers the
system's OpenCL implementation. The adjacent `manifest.json` records its source
commit and SHA-256. Future platform builds must implement the same JSON/file
protocol before being added.

## Job protocol

1. Run Nesting acquires the workbench job lock, exports `input.json` and
   `nesting_session.json`, and starts the selected CLI with `--input`.
2. Both job files have the same UUID. Input includes every search/hardware
   setting, part/stock polygons, `output.json = "result.json"`, and a unique
   `output.cancelFile` marker name.
3. The session captures the original displayed BREP and colors once per part
   type. Result reconstruction applies normalization, the returned Z rotation
   and XY translation to this frozen geometry. It never re-aligns, mirrors or
   re-reads a changed preview part. This preserves the displayed top/bottom.
4. CLI publishes `result.json` atomically beside input. Workbench checks the
   exact UUID before importing a snapshot into a new FreeCAD result document.
5. Continuous mode clears `results/` under an exclusive CLI lock and saves
   improving layouts as `results/result1.json`, `result2.json`, etc. The latest
   complete snapshot is also published as root `result.json`. Polling and the
   small Cancel window stay active until the process stops.
6. Cancel creates the job-specific marker; CLI checks it cooperatively and
   exits normally. A delayed forced stop is only a fallback. Valid snapshots
   already saved remain available. Failed launch/import closes the wait window
   and restores the controls; diagnostic output is in `clinesting.log`.

`input.json`, `result.json`, the session, log, locks and `results/` belong in
the **workbench directory**, not the platform executable folder. The workbench
must be writable. A second job cannot overwrite the active job's files.

## Validation

Run ordinary tests with `python -m unittest discover -s tests -p "test_*.py"`.
Run `tests/freecad_geometry_regression.py` and
`tests/freecad_workbench_integration.py` with FreeCAD's bundled Python. The latter
uses real Qt offscreen widgets and the bundled executable in disposable folders;
it tests first/timed/continuous, cancellation, job identity, exclusive ownership,
1000 instances, immutable shape/orientation, and a closed preview document.
