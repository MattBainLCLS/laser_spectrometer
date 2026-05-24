# Pending tasks for Claude

## Fix spectrometer averaging bug in FROG scan

Investigate whether the scan loop waits for stage motion to complete before
collecting a fresh run of averaged spectra.  The bug shows up clearly during
FROG scans — spectra appear to be collected before the stage has fully settled.

Likely place to look: the scan loop in `frog_gui.py` and the averaging logic
in `spectrometer_widget.py`.  Check that `move_to()` blocking behaviour and
the spectrometer averaging run are properly sequenced.

Requires FROG hardware to reproduce and verify the fix.
