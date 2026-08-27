# cleanstreamts/paths.py
"""
Path anchoring.

The ChitraMaya Fix Batch 2 lesson: never resolve app-relative paths against
the current working directory. A packaged app launched by double-clicking the
exe, or from a shortcut, or from PATH, has a cwd somewhere else entirely
(C:\\Users\\<you>, C:\\Windows\\System32), and every relative path misses.

Frozen  -> the directory containing the exe, whatever the cwd.
Source  -> the current working directory, preserving the pip install -e flow.
"""

import sys
from pathlib import Path


def app_base_dir():
    """Directory to anchor bundled resources and log files against."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


def resource_dir():
    """
    Directory containing this package's bundled templates/ and static/.

    PyInstaller unpacks datas into sys._MEIPASS, and the spec places this
    package's data under a "cleanstreamts" subfolder there - mirroring the
    source layout - so the resource root is _MEIPASS/cleanstreamts, NOT
    _MEIPASS itself.

    Getting this wrong does not fail the build. The files are present and a
    manifest check confirms it; the app simply looks one directory too high
    and Flask raises TemplateNotFound the first time a window opens. Only
    running the packaged exe catches it, which is why cli.py has a
    --self-check that resolves these paths for real.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "cleanstreamts"
    return Path(__file__).resolve().parent
