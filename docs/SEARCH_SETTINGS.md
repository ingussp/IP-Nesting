# Search settings

The main panel contains the job's search mode, time budgets, placement strategies,
CPU and GPU settings. Mode captions are translated in all 50 languages while
`input.json` and the saved `SearchMode` preference use the fixed CLI identifiers:

| Mode in English | JSON identifier | Time limit | Round duration | Placement strategies |
| --- | --- | --- | --- | --- |
| Fast | `first` | Disabled | Disabled | Disabled |
| Timed | `timed` | Enabled | Enabled | Enabled |
| Continuous | `continuous` | Disabled | Enabled | Enabled |

## Placement strategy sets

The strategy selector displays translated words, while item data and JSON
`trials` remain integers. The existing `Trials` preference remains a numeric
string, so selections saved before this change restore correctly.

| Caption in English | Exported `trials` | Compared strategies |
| --- | --- | --- |
| Compact | 1 | `compact` |
| Compact + holes first | 2 | `compact`, `holes_first_rows` |
| Compact + holes first + largest first | 3 | Those two plus `large_first` |
| All strategies | 4 | Those three plus `small_first` |

The CLI compares the selected set and retains its best validated layout; the
number is a strategy count, not an individual strategy identifier. Fast mode
ignores `trials` and always runs one largest-first, bottom-left strategy, so the
selector and its label are disabled. Its selection is retained for switching
back to Timed or Continuous. Changing language also preserves the numeric value.

Long captions do not determine the closed selector's minimum width, keeping the
settings grid's original 2:1 column proportions. The opened dropdown expands to
show the full translated strategy names, recalculating its width after language
changes.

## Advanced settings

The gear/Settings menu contains the advanced search settings directly below the
contour approximation tolerance:

1. Bitmap resolution (mm/px).
2. Search step (px).
3. Cache rejected positions.
4. Contact simplification (mm).

These retain the existing preference keys (`Resolution`, `SearchStepPx`,
`CacheRejects`, `CurveTolerance`), so existing values survive moving the controls.
The exporter reads them at the start of each job, including edits made while the
main panel is open. The menu is also available when no task panel is open.

## The two geometric tolerances

Contour approximation tolerance (`BoundaryResolution`, default 0.01 mm) belongs
to FreeCAD. It controls the maximum deviation when curved edges are discretized
into line segments, and affects the polygons exported to `input.json`.

Contact simplification (`curveTolerance` in the CLI JSON, default 0.3 mm) belongs
to the CLI. In the current bitmap engine it simplifies the contour used to
propose contact positions. The raster occupancy, vector validation and exported
result geometry retain the original contour. A value of zero disables this
simplification. It is a separate operation, so it remains available in Settings
under a clearer name instead of being removed as a duplicate.

Bitmap resolution is a third parameter: the size of one raster pixel. All three
use canonical millimetres even if the main panel displays sheet dimensions in
inches.
