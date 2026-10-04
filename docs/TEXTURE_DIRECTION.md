# Immediate texture direction controls

Checking **Texture Direction** immediately aligns the selected row's preview
parts with the X texture direction and packs them inside the blue perimeter.
The separate **Apply Texture** button and its blink timer have been removed.
X/Y changes, including the bulk selector, also apply immediately. The bulk
operation rebuilds the preview once after updating all checked rows.

Before a part enters the texture group, its complete FreeCAD Placement and
its row's rotation specification are saved. Each preview copy has its own
saved placement. Unchecking Texture Direction restores that position and
rotation, removes the arrows, resets the Custom angle checkbox and restores
the original rotation specification. The standard group is not repacked during
these checkbox/axis/custom-angle operations, so restored positions are preserved.

Accepting a Custom angle aligns and repacks the texture group immediately.
Each preview copy retains its accepted custom angle independently of the
transient grain alignment angle. Reopening the dialog starts at that saved
value. Changing 37 to 52 degrees applies only the 15-degree difference; accepting
the same value does not rotate the part again. Arrow previews use the same
per-part difference and Cancel restores their original placements without
changing the saved angle.

For multiple selected rows, the dialog starts at the first selected copy's
saved angle and OK applies the chosen target angle to each selected copy,
using each copy's own previous value. An explicit X/Y choice starts a new
custom-angle baseline at zero. Unchecking Texture Direction clears the saved
custom angle along with restoring the original placement.
Unchecking Texture Direction also undoes that rotation; it does not merely set
the arrow to X. Cancelling the angle dialog retains the current texture layout.
The saved placement is removed on return, so a later check uses the part's
current standard placement rather than an older snapshot. Rotations about Z
retain the displayed top/bottom orientation.

The script-facing `apply_change_grain()` method remains available for callers
that explicitly request a refresh. It is no longer required by the UI.

## Verification

Run `tests/freecad_grain_controls.py` with FreeCAD's bundled Python. It exercises
real checkbox signals, part geometry, perimeters, individual and bulk axis
changes, the accepted/cancelled angle dialog, legacy row name storage, distinct
copy rotations, return ordering, and a second check after manual repositioning.

Regression coverage includes reopening, unchanged acceptance, angle differences,
359/1-degree wrapping, cancellation after a visible arrow preview, distinct
row angles, axis resets and texture off/on resets.
