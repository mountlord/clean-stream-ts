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
    Directory containing bundled templates/static.

    PyInstaller unpacks datas into sys._MEIPASS, which is NOT the exe
    directory - so this is a different question from app_base_dir() and needs
    its own answer.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent
