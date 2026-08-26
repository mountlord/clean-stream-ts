# cleanstreamts/winproc.py
"""
Windows subprocess flags.

Every subprocess launched from the windowed exe must pass **NOWINDOW or the
user sees a black console window flash for each ffmpeg/ffprobe child. This is
the ChitraMaya Batch 24 lesson, carried over verbatim: it is invisible in a
dev console run and glaringly obvious in the packaged app.

Usage at every call site, without exception:

    subprocess.run(cmd, **NOWINDOW)
"""

import subprocess
import sys

if sys.platform == "win32":
    NOWINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    NOWINDOW = {}
