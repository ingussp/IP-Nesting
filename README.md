Šeit atrodas tikai pēdējā strādājošā versija

**Import DXF** supports selecting multiple files with Ctrl/Shift or Ctrl+A.
Each file becomes a separate part row with quantity 1 and the configured default
rotation count. The preview layout is refreshed once after the batch. Files that
fail to import are listed in one warning; remaining files are still processed.

The batch import regression uses real Qt tables and FreeCAD geometry, with
temporary DXF fixtures and a mocked file chooser. Run it with FreeCAD's bundled
Python from the repository root, for example on Windows:

```powershell
& 'C:\Program Files\FreeCAD 1.0\bin\python.exe' tests/freecad_dxf_batch_import.py
```


GPU discovery is explicit: use **Show GPU's** next to the GPU device setting.
The **Looking for graphic cards** progress window is painted before a background
worker starts `clinesting --list-gpus`. Opening the workbench or changing GPU
on/off does not enumerate devices. Auto selection and a saved GPU index remain
available without a scan.

**Run nesting** paints its progress window before exporting the job. Export
processes sheets and parts between Qt event-loop turns, reuses each part's contour
for its outline, selected holes and immutable shape snapshot, and starts the CLI
process in a background worker. FreeCAD geometry/recompute operations stay on the
UI thread; a single expensive geometry operation can still delay repainting.
Cancel also works before export or while the operating system is creating the
process. A cancelled late process is stopped before the workbench job lock is
released. The CLI executable and nesting algorithms are unchanged.


Machined panels with blind drilling and grooves use their complete XY footprint.
The exporter proves that the footprint encloses all material instead of requiring
an identical cross-section throughout the thickness. Only empty columns through
the full thickness are exposed as holes. Original BREP geometry and face
orientation are preserved for result placement; disconnected material and solids
extending beyond the proven footprint remain rejected.
