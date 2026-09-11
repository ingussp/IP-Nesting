# Initial code review

Reviewed on 2026-09-11 against main commit `3195a2d`.

This review covers all 17 Python modules, README, ignore rules and the icon.
The workbench prepares FreeCAD preview geometry, exports a Deepnest job and
imports the external engine's result. The executable and its source are not
included in this repository. The older label/bin import helpers remain present
alongside the current Deepnest result importer.

The owner clarified that Deepnest is still being developed. The current
deliverable is correct `input.json` generation. Result polling and import
observations below are deferred integration notes, not blockers for that
export-only milestone; their final behavior depends on the future engine contract.

This change documents the existing implementation. The findings below remain
open; executable code has not been changed. P1 means a geometry/data-correctness
problem to prioritize; P2 means an incorrect setting, interaction or failure path.

## Findings

### 1. P1: inch dimensions are exported as millimetres without conversion

Location: [IPNestingExport.py / execute_nesting](../IPNestingExport.py#L825),
[IPNestingGui.py / get_dimension_value_mm](../IPNestingGui.py#L3816).

The exporter reads spacing, sheet margin and boundary resolution using
`_read_float_widget`, but declares `units: "mm"`. With inch display selected,
entering `1` exports `1.0` instead of `25.4`. Material geometry already uses mm,
so these settings become inconsistent with the geometry. An isolated execution
of the actual readers reproduced the 1.0 versus 25.4 mismatch. Export canonical
millimetre values through the panel's unit-aware interface.

### 2. P1: hole-to-part clearance is ignored

Location: [IPNestingExport.py / execute_nesting](../IPNestingExport.py#L825),
[IPNestingOffcutShowDialog.py / _store_shared_clearance_state](../IPNestingOffcutShowDialog.py#L1493).

The dialog stores `offcut_clearance_mode` and `offcut_custom_clearance`, but the
exporter unconditionally writes `partToHole: 0.0`. Neither a custom clearance nor
the "same as part spacing" choice reaches the engine. Resolve the selected mode
to a millimetre distance when writing the job.

### 3. P1: curved offcut edges can become straight chords

Location: [IPNestingOffcuts.py / _wire_to_polyline_2d](../IPNestingOffcuts.py#L111).

If at least three vertices exist, the function returns those vertices without
discretizing any edges. A contour containing both arcs and straight edges can
therefore lose the arcs, changing available material and hole geometry. A
four-vertex wire stub confirmed that edge discretization is never called.
Sample curved edges independently of the total vertex count and stitch them in
wire order. Validate against a real mixed-line/arc DXF in FreeCAD.

### 4. P1: different contours can be discarded as duplicates

Location: [IPNestingOffcuts.py / _polygons_are_same](../IPNestingOffcuts.py#L761),
[IPNestingOffcuts.py / _append_unique_contour](../IPNestingOffcuts.py#L799).

Equality uses only area and bounding-box extrema. The distinct triangles
`[(0,0),(2,0),(0,2)]` and `[(0,0),(2,0),(2,2)]` compare equal in an isolated
execution of the actual helper. One can be removed before the user gets to
select it as an exclusion. Compare contour geometry with tolerance, accounting
for starting vertex and winding, after the area/bounds prefilter.

### 5. P1: deleting a part realigns the surviving preview geometry

Location: [IPNestingPreviewDoc.py / delete_preview_objects](../IPNestingPreviewDoc.py#L83),
[IPNestingRelayout.py / relayout_preview](../IPNestingRelayout.py#L153).

Deletion invokes `mgr.run(copy_selection=True)` twice. Relayout resets Placement
and aligns every shape again, including remaining parts that the user manually
rotated. Depending on the remaining FreeCAD selection, it can also copy selected
source objects back into the preview without adding corresponding table rows.
Deletion should only remove the requested objects and reposition survivors while
preserving their rotations; it should not import selection.

### 6. P2: Apply Grain skips the normal selection-checkbox layout

Location: [IPNestingGui.py / apply_change_grain](../IPNestingGui.py#L2861),
[IPNestingGui.py / add_selected_objects](../IPNestingGui.py#L2274).

The handler checks `layout.itemAt(0).widget()`, but part-row layouts start with a
stretch spacer. That call returns None without raising, so the `findChild`
fallback inside `except` is not reached. A checked row is skipped. An isolated
execution of the handler with this layout reported zero changed rows. Always
look for the checkbox after an unsuccessful first-item lookup, or use the
existing robust rotation-helper lookup.

### 7. P2: the selected placement strategy is ignored

Location: [IPNestingExport.py / execute_nesting](../IPNestingExport.py#L825),
[IPNestingGui.py / _save_settings_to_prefs](../IPNestingGui.py#L4132).

The UI exposes and persists Gravity, Bounding box and Squeeze, but export always
writes `placementType: "gravity"`. Changing the combo has no effect on the job.
Map the selected strategy to the engine's supported value and verify it in the
generated JSON.

### 8. Deferred integration: malformed or stale results can leave Run Nesting disabled

Location: [IPNestingResult.py / _check_result](../IPNestingResult.py#L1543).

Process exit is handled only while the result file is absent. Once a file exists,
invalid JSON, a non-object root or an old modification time can cause repeated
early returns even after the process exits. An isolated check with a stable
`{broken` file and an exited process never reached a completion callback after
eight polls. After process exit, distinguish a temporarily incomplete write from
a terminal invalid result and restore the UI with an error.

### 9. Deferred integration: multiple sheet boundaries are drawn on top of one another

Location: [IPNestingResult.py / _create_sheet_object](../IPNestingResult.py#L615),
[IPNestingResult.py / _import_placements](../IPNestingResult.py#L769).

All rectangular sheets are created at (0,0); polygon sheets use their original
coordinates. There is no per-sheet display offset or grouping, and placement
import does not associate a displayed offset with a sheet instance. Two identical
sheet records therefore produce coincident boundaries. Introduce per-instance
groups/documents or consistent display offsets for both sheet outlines and their
parts. The exact placement-coordinate contract needs verification with real
Deepnest multi-sheet output before changing the transform logic.

### 10. Deferred integration: result import can report success after importing no parts

Location: [IPNestingResult.py / import_result](../IPNestingResult.py#L289),
[IPNestingResult.py / _import_placements](../IPNestingResult.py#L769).

Individual failed/missing source objects are skipped; the actual imported count
is only logged. `import_result` then returns True and shows engine-reported
counts, even if every placement failed to import. Deleting a source part while
the external process runs can trigger this mismatch. Return import counts and
failures and distinguish engine success from successful FreeCAD reconstruction.

### 11. P2: an invalid quantity edit deletes the part

Location: [IPNestingGui.py / on_item_changed](../IPNestingGui.py#L2611).

An empty or non-integer Qty becomes zero after conversion fails, entering the
same deletion path as an intentional zero. For example, committing `abc` removes
the row and preview object. Restore the previous valid quantity for invalid
input and reserve deletion for an explicitly entered zero.

### 12. P2: legacy sheet import ignores rotation and current contour format

Location: [IPNestingImportSheets.py / import_nesting_sheets](../IPNestingImportSheets.py#L79).

This helper reads `parts[].polygons`, normalizes the first contour and adds only
placement x/y. It never applies rotation and cannot consume current
`parts[].points`. It is imported by the GUI but is not called by the current Run
Nesting flow, so this is a legacy-path defect. Retire it explicitly or adapt its
schema and rotation handling before reuse.

## Additional observations and integration checks

- `InitGui.py` uses `traceback.format_exc()` without importing traceback. An
  auto-add failure can lose the intended error report because an outer handler
  swallows the resulting error. FreeCAD-provided `App`/`Workbench` globals are
  separate from this missing standard-library import.
- The grain-arrow fallback still accesses `bb` after bounding-box acquisition
  fails. Its final error handler returns False instead of using the intended
  placement-based fallback. Also, minimum arrow dimensions can exceed small
  parts; documentation no longer promises full containment.
- The label `scale_multiplier` calculation always clamps to 1.0 for the allowed
  `side_scale` interval 0.6..3.0. Confirm the intended scaling formula before
  changing it.
- The exporter supplies normalized polygons and shape bounds, while the 3D
  importer looks for `geometry_transform.nesting_to_source_shape_offset` in
  returned metadata. Verify this contract and FreeCAD Shape/Placement semantics
  with a translated, rotated asymmetric 3D part. The external executable is not
  available here, so this review does not assert that the full transform is
  correct or prescribe an unverified transform fix.
- Once Deepnest is ready, verify result-document replacement with unsaved edits and closing/reopening
  the task panel during a running job in a real FreeCAD session.

## Validation of this documentation change

- All 17 Python files compile under Python 3.14.5.
- All 303 function/method definitions and 17 class definitions have a descriptive
  preceding comment; decorated definitions are documented before the decorator.
  The eight lambda expressions also have comments at their containing operation.
- ASTs, excluding module/class/function docstrings and source locations, are
  identical to the reviewed main commit. No executable behavior was changed.
- `git diff --check` passes.
- Five isolated checks reproduced findings 1, 3, 4, 6 and 8 using actual function
  bodies and small stubs. Polygon-area and 90-degree rotation smoke checks pass.
  These checks are not a substitute for FreeCAD/OCC/Qt integration testing.
- No existing test suite or CI configuration is included in the repository.
  FreeCAD and PySide are not installed in this environment, and the ignored
  `deepnest/` directory is absent; full GUI/engine testing was not run.
