# packaging/windows/cleanstreamts.spec
# -*- mode: python ; coding: utf-8 -*-
"""
CleanStreamTS PyInstaller spec.

Builds TWO bootloaders that share ONE COLLECT, the arrangement ChitraMaya
settled on:

  CleanStreamTS.exe      console=False  the window
  CleanStreamTS-cli.exe  console=True   scan / clean / extract-decoy

Why both: a windowed exe detaches from PowerShell and prints nothing there,
so headless use needs a console bootloader. Sharing one COLLECT means the
Python runtime and every dependency is stored once, not twice.

The spec VERIFIES its own output. Every module and data file it expects is
listed in a ledger below and checked after analysis; a missing entry fails
the build loudly, naming what is absent. A packaging regression that ships a
broken app is far more expensive than a build that refuses to finish.

Build from the repo root, in the release venv:

    pyinstaller packaging/windows/cleanstreamts.spec --noconfirm
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
REPO_ROOT = os.path.abspath(os.path.join(SPEC_DIR, "..", ".."))
PKG_DIR = os.path.join(REPO_ROOT, "cleanstreamts")
VENDOR_DIR = os.path.join(SPEC_DIR, "vendor")
VENDOR_FFMPEG = os.path.join(VENDOR_DIR, "ffmpeg")

sys.path.insert(0, REPO_ROOT)

APP_NAME = "CleanStreamTS"

# --------------------------------------------------------------------------
# Ledger - what MUST be present in the finished bundle
# --------------------------------------------------------------------------

REQUIRED_MODULES = [
    "cleanstreamts",
    "cleanstreamts.cli",
    "cleanstreamts.core",
    "cleanstreamts.repair",
    "cleanstreamts.server",
    "cleanstreamts.paths",
    "cleanstreamts.winproc",
    "cleanstreamts.console_buffer",
    "flask",
    "jinja2",
    "werkzeug",
]

REQUIRED_DATA_SUFFIXES = [
    os.path.join("cleanstreamts", "templates", "ui.html"),
    os.path.join("cleanstreamts", "static", "css", "app.css"),
    os.path.join("cleanstreamts", "static", "js", "app.js"),
]


def _banner(lines):
    print("=" * 72)
    for line in lines:
        print(line)
    print("=" * 72)


# --------------------------------------------------------------------------
# Datas: templates + static are what the Flask app renders from
# --------------------------------------------------------------------------

datas = [
    (os.path.join(PKG_DIR, "templates"), os.path.join("cleanstreamts", "templates")),
    (os.path.join(PKG_DIR, "static"), os.path.join("cleanstreamts", "static")),
]

hiddenimports = list(REQUIRED_MODULES)
hiddenimports += collect_submodules("cleanstreamts")

# pywebview pulls its platform backend in dynamically; on Windows that is the
# WinForms/WebView2 path via pythonnet, which static analysis does not see.
try:
    datas += collect_data_files("webview")
    hiddenimports += collect_submodules("webview")
    hiddenimports += ["clr", "clr_loader", "pythonnet"]
except Exception as exc:                                    # noqa: BLE001
    _banner(["WARNING: could not collect pywebview: %s" % exc,
             "The windowed exe will not start without it."])

# --------------------------------------------------------------------------
# Bundled ffmpeg (LGPL build - see packaging/windows/vendor/README.md)
# --------------------------------------------------------------------------

binaries = []
_ffmpeg_found = []
if os.path.isdir(VENDOR_FFMPEG):
    for name in sorted(os.listdir(VENDOR_FFMPEG)):
        full = os.path.join(VENDOR_FFMPEG, name)
        if os.path.isfile(full):
            binaries.append((full, "ffmpeg"))
            _ffmpeg_found.append(name)

if any(n.lower().startswith("ffmpeg") for n in _ffmpeg_found) and \
   any(n.lower().startswith("ffprobe") for n in _ffmpeg_found):
    print("[spec] ffmpeg BUNDLED (%d files)" % len(_ffmpeg_found))
else:
    _banner([
        "WARNING: no ffmpeg/ffprobe found in",
        "  %s" % VENDOR_FFMPEG,
        "Building WITHOUT a bundled ffmpeg. The packaged app will require the",
        "user to have ffmpeg on PATH. See vendor/README.md for the LGPL build.",
    ])

# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

gui_a = Analysis(
    [os.path.join(REPO_ROOT, "packaging", "entry_gui.py")],
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "numpy", "matplotlib", "PySide6", "PyQt5", "PyQt6"],
    noarchive=False,
)

cli_a = Analysis(
    [os.path.join(REPO_ROOT, "packaging", "entry_cli.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=list(REQUIRED_MODULES) + collect_submodules("cleanstreamts"),
    hookspath=[],
    excludes=["tkinter", "numpy", "matplotlib", "PySide6", "PyQt5", "PyQt6"],
    noarchive=False,
)

# --------------------------------------------------------------------------
# Verify the ledger before building anything
# --------------------------------------------------------------------------

_modules_present = set()
for _entry in gui_a.pure:
    _modules_present.add(_entry[0])

_missing_modules = [m for m in REQUIRED_MODULES if m not in _modules_present]

_data_dests = [d[0].replace("\\", "/") for d in gui_a.datas]
_missing_data = []
for _suffix in REQUIRED_DATA_SUFFIXES:
    _needle = _suffix.replace("\\", "/")
    if not any(dest.endswith(_needle) for dest in _data_dests):
        _missing_data.append(_suffix)

if _missing_modules or _missing_data:
    _banner(
        ["BUILD ABORTED - the bundle is missing required content:"]
        + ["  missing module: %s" % m for m in _missing_modules]
        + ["  missing data:   %s" % d for d in _missing_data]
    )
    raise SystemExit(1)

print("[spec] ledger OK: %d modules, %d data files verified"
      % (len(REQUIRED_MODULES), len(REQUIRED_DATA_SUFFIXES)))

# --------------------------------------------------------------------------
# Executables
# --------------------------------------------------------------------------

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data)

gui_exe = EXE(
    gui_pyz, gui_a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=os.path.join(SPEC_DIR, "cleanstreamts.ico")
        if os.path.isfile(os.path.join(SPEC_DIR, "cleanstreamts.ico")) else None,
)

cli_exe = EXE(
    cli_pyz, cli_a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME + "-cli",
    console=True,
    icon=os.path.join(SPEC_DIR, "cleanstreamts.ico")
        if os.path.isfile(os.path.join(SPEC_DIR, "cleanstreamts.ico")) else None,
)

coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.zipfiles, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.zipfiles, cli_a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
