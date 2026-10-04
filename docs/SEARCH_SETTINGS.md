# Search settings

The main panel contains the job's search mode, time budgets, placement strategies,
CPU and GPU settings. Mode captions are translated in all 50 languages while
`input.json` and the saved `SearchMode` preference use the fixed CLI identifiers:

| Mode in English | JSON identifier | Time limit | Round duration |
| --- | --- | --- | --- |
| Fast:first | `first` | Disabled | Disabled |
| Timed:timed | `timed` | Enabled | Enabled |
| Continuous:continuous | `continuous` | Disabled | Enabled |

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
