# cleanstreamts/cli.py
"""
Command-line interface.

    CleanStreamTS-cli scan <folder>            list what is in a folder
    CleanStreamTS-cli clean <folder>           dry run, then --apply to write
    CleanStreamTS-cli extract-decoy <in> <out> pull out the decoy image
    CleanStreamTS-cli gui                      launch the window

All console output is ASCII. Filenames are masked in every line this prints,
so the output of a scan can be pasted into a bug report as-is.
"""

import argparse
import csv
import json
import os
import sys

from . import __version__, APP_NAME
from . import core
from . import repair as repair_mod
from .paths import app_base_dir

CSV_FIELDS = [
    "file", "masked_path", "size", "container", "decoy_prefixed",
    "decoy_end", "gap_len", "payload_offset", "packets_confirmed",
    "bytes_at_payload", "is_candidate", "error",
]


def _csv_row(res):
    return {
        "file": res["masked"],
        "masked_path": core.mask_path(res["path"]),
        "size": res["size"],
        "container": res["container"],
        "decoy_prefixed": res["decoy_prefixed"],
        "decoy_end": res["decoy_end"],
        "gap_len": res["gap_len"],
        "payload_offset": res["payload_offset"],
        "packets_confirmed": res["packets_confirmed"],
        "bytes_at_payload": res["bytes_at_payload"],
        "is_candidate": res["is_candidate"],
        "error": res["error"],
    }


def _scan_folder(folder, recursive, extensions, min_ts_packets):
    files = core.find_media_files(folder, extensions=extensions, recursive=recursive)
    return [core.detect(p, min_ts_packets=min_ts_packets) for p in files]


def _parse_extensions(raw):
    return tuple(e.strip().lower() for e in raw.split(",") if e.strip())


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def cmd_scan(args):
    if not os.path.isdir(args.folder):
        sys.stderr.write("error: not a folder: %s\n" % args.folder)
        return 2

    results = _scan_folder(args.folder, args.recursive,
                           _parse_extensions(args.ext), args.min_ts_packets)

    if args.json:
        payload = [
            {
                "path": r["path"],
                "masked": r["masked"],
                "size": r["size"],
                "container": r["container"],
                "payload_offset": r["payload_offset"],
                "packets_confirmed": r["packets_confirmed"],
                "is_candidate": r["is_candidate"],
                "error": r["error"],
            }
            for r in results
        ]
        sys.stdout.write(json.dumps({"results": payload}) + "\n")
        return 0

    if not results:
        print("No media files found in %s" % args.folder)
        return 0

    candidates = 0
    for idx, res in enumerate(results, 1):
        if res["error"]:
            flag = "ERROR"
        elif res["is_candidate"] and res["already_cleaned"]:
            flag = "DONE"
        elif res["is_candidate"]:
            flag = "DECOY"
            candidates += 1
        elif res["decoy_prefixed"]:
            flag = "SUSPECT"
        else:
            flag = "ok"
        label = (core.relative_label(res["path"], args.folder) if args.no_mask
                 else core.display_label(res["path"], args.folder))
        print("[%d/%d] %-8s %-30s %s"
              % (idx, len(results), flag, label, res["container"]))
        if args.verbose and res["is_candidate"]:
            print("         payload_offset=%d gap=%d packets=%d"
                  % (res["payload_offset"], res["gap_len"], res["packets_confirmed"]))

    print("")
    print("Scanned %d file(s); %d repairable candidate(s)." % (len(results), candidates))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8", errors="replace") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for res in results:
                writer.writerow(_csv_row(res))
        print("CSV written to %s (filenames masked)." % args.csv)

    return 0


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

def cmd_clean(args):
    if not os.path.isdir(args.folder):
        sys.stderr.write("error: not a folder: %s\n" % args.folder)
        return 2

    base = app_base_dir()
    ffmpeg_bin, ffprobe_bin = repair_mod.resolve_tools(
        base, args.ffmpeg, args.ffprobe)
    ok, message = repair_mod.check_tools(ffmpeg_bin, ffprobe_bin)
    if not ok:
        sys.stderr.write("error: %s\n" % message)
        sys.stderr.write("Install ffmpeg, or pass --ffmpeg/--ffprobe.\n")
        return 3

    results = _scan_folder(args.folder, args.recursive,
                           _parse_extensions(args.ext), args.min_ts_packets)
    candidates = [r for r in results if r["is_candidate"]]

    if not args.redo:
        skipped = [r for r in candidates if r["already_cleaned"]]
        candidates = [r for r in candidates if not r["already_cleaned"]]
        if skipped:
            print("Skipping %d file(s) already cleaned earlier "
                  "(pass --redo to clean them again)." % len(skipped))
            print("")

    # --files restricts to a chosen subset. Matching is on the path RELATIVE
    # to the scan root, not the bare basename: a recursive scan can easily
    # contain two files with the same name in different subfolders, and
    # matching on basename would repair both when the user picked one.
    if args.files:
        wanted = set(f.replace("\\", "/") for f in args.files)
        picked = []
        for res in candidates:
            rel = os.path.relpath(res["path"], args.folder).replace("\\", "/")
            if rel in wanted:
                picked.append(res)
        missing = wanted - set(
            os.path.relpath(r["path"], args.folder).replace("\\", "/")
            for r in picked
        )
        for name in sorted(missing):
            sys.stderr.write("warning: not a candidate in this folder: %s\n" % name)
        candidates = picked

    print("Mode: %s" % ("CLEAN" if args.apply else "DRY RUN"))
    if not args.apply:
        print("(nothing will be written - add --apply to actually repair)")
    print("")

    if not candidates:
        print("No repairable candidates found.")
        return 0

    print("Found %d candidate(s):" % len(candidates))
    print("")

    repaired = 0
    failed = 0
    for res in candidates:
        print("- %s" % ((core.relative_label(res["path"], args.folder))
                        if args.no_mask
                        else core.display_label(res["path"], args.folder)))
        print("    payload_offset=%d  gap=%d  packets=%d"
              % (res["payload_offset"], res["gap_len"], res["packets_confirmed"]))
        if not args.apply:
            preview = repair_mod.unique_output_path(res["path"], args.output)
            print("    would write: %s" % (os.path.basename(preview) if args.no_mask
                                           else core.mask_filename(preview)))
            continue

        outcome = repair_mod.repair_one(
            res["path"], res["payload_offset"],
            output_dir=args.output,
            ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
            keep_intermediate=args.keep_intermediate,
        )
        if outcome["status"] == repair_mod.STATUS_REPAIRED:
            repaired += 1
            print("    OK  -> %s  (duration %s)"
                  % (os.path.basename(outcome["output"]) if args.no_mask
                     else core.mask_filename(outcome["output"]),
                     outcome["duration"]))
        else:
            failed += 1
            print("    %s: %s" % (outcome["status"].upper(), outcome["error"]))

    print("")
    if not args.apply:
        print("Dry run complete. %d candidate(s) would be cleaned." % len(candidates))
        print("Re-run with --apply to write the cleaned files.")
    else:
        print("Done: %d cleaned, %d failed, of %d candidate(s)."
              % (repaired, failed, len(candidates)))
        print("Originals were not modified. Check playback before deleting anything.")
    return 0


# ---------------------------------------------------------------------------
# extract-decoy
# ---------------------------------------------------------------------------

def cmd_extract_decoy(args):
    try:
        with open(args.input, "rb") as handle:
            head = handle.read(args.probe_bytes)
    except OSError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2

    if head[:8] != core.PNG_SIGNATURE:
        sys.stderr.write("error: file does not start with a PNG signature; nothing to extract\n")
        return 1

    iend = head.find(b"IEND")
    if iend == -1:
        sys.stderr.write("error: no IEND chunk within the first %d bytes\n" % args.probe_bytes)
        return 1

    blob = head[:iend + 8]
    try:
        with open(args.output, "wb") as handle:
            handle.write(blob)
    except OSError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 2

    print("Extracted %d bytes -> %s" % (len(blob), args.output))
    print("")

    chunks = core.list_png_chunks(blob)
    print("Chunks:")
    for ctype, length in chunks:
        print("  %-6s length=%d" % (ctype, length))
    print("")

    ihdr = core.parse_ihdr(blob)
    if ihdr:
        print("IHDR:")
        for key, value in ihdr.items():
            print("  %-12s %s" % (key, value))
        print("")

    has_idat = any(ctype == "IDAT" for ctype, _ in chunks)
    print("Pixel data (IDAT) present: %s" % ("yes" if has_idat else "no"))

    try:
        from PIL import Image
        with Image.open(args.output) as img:
            img.load()
            print("Decodes as: mode=%s size=%s" % (img.mode, img.size))
            if img.size == (1, 1):
                pixel = img.convert("RGBA").getpixel((0, 0))
                print("Single pixel value: %s" % (pixel,))
                if pixel[3] == 0:
                    print("")
                    print("A 1x1 fully transparent pixel renders as nothing at all.")
                    print("That is the shape of a tracking beacon, not a thumbnail:")
                    print("its purpose is to be fetched, not to be seen.")
    except ImportError:
        print("(Pillow not installed - skipping the decode test.)")
    except Exception as exc:
        print("Does not decode as an image: %s: %s" % (type(exc).__name__, exc))

    return 0


def cmd_self_check(_args):
    """
    Verify a packaged build can actually find everything it needs.

    This exists because of a real failure: the spec bundled templates and
    static correctly, a manifest check confirmed they were present, and the
    app still died with TemplateNotFound on first launch - because the code
    resolved the resource root one directory too high. Checking that files
    are IN the bundle is not the same as checking the app can FIND them.

    So this resolves the same paths the running app resolves, opens the
    files, imports every module, and runs the bundled ffmpeg/ffprobe. The
    packager runs it against the built exe, so a broken bundle fails the
    build instead of the user's first launch.
    """
    failures = []
    warnings = []
    checks = 0

    def check(label, ok, detail="", fatal=True):
        """
        fatal=False marks an advisory check. ffmpeg absent from PATH means
        the user still has a step to do, not that the install is broken -
        so it must not fail a build or an install that is otherwise sound.
        A missing template genuinely is broken, and does.
        """
        nonlocal checks
        checks += 1
        if ok:
            print("  PASS  %s" % label)
        elif fatal:
            failures.append(label)
            print("  FAIL  %s%s" % (label, ("  [%s]" % detail) if detail else ""))
        else:
            warnings.append(label)
            print("  WARN  %s%s" % (label, ("  [%s]" % detail) if detail else ""))

    print("%s %s self-check" % (APP_NAME, __version__))
    print("  frozen: %s" % bool(getattr(sys, "frozen", False)))
    print("")

    print("Modules")
    for name in ("cleanstreamts.core", "cleanstreamts.repair",
                 "cleanstreamts.cli", "cleanstreamts.paths",
                 "cleanstreamts.winproc", "cleanstreamts.console_buffer"):
        try:
            __import__(name)
            check(name, True)
        except Exception as exc:
            check(name, False, str(exc))

    print("")
    print("Bundled resources")
    from .paths import resource_dir
    res = resource_dir()
    print("  resource_dir: %s" % res)
    for rel in (os.path.join("templates", "ui.html"),
                os.path.join("static", "css", "app.css"),
                os.path.join("static", "js", "app.js")):
        path = os.path.join(str(res), rel)
        ok = os.path.isfile(path)
        check(rel, ok, "not found at %s" % path)

    print("")
    print("Flask template loader")
    # Resolve the way the server does, not the way we hope it does.
    try:
        from flask import Flask
        from pathlib import Path as _P
        app = Flask("selfcheck",
                    template_folder=str(_P(res) / "templates"),
                    static_folder=str(_P(res) / "static"))
        with app.app_context():
            app.jinja_env.get_template("ui.html")
        check("ui.html loads through Jinja", True)
    except Exception as exc:
        check("ui.html loads through Jinja", False, "%s: %s" % (type(exc).__name__, exc))

    print("")
    print("External tools")
    from .paths import app_base_dir
    ffmpeg_bin, ffprobe_bin = repair_mod.resolve_tools(app_base_dir())
    ok, message = repair_mod.check_tools(ffmpeg_bin, ffprobe_bin)
    check("ffmpeg/ffprobe present", ok, message or "", fatal=False)
    if ok:
        print("        ffmpeg:  %s" % ffmpeg_bin)
        print("        ffprobe: %s" % ffprobe_bin)

    print("")
    print("=" * 52)
    if failures:
        print("  %d of %d checks FAILED" % (len(failures), checks))
        for f in failures:
            print("    - %s" % f)
        print("=" * 52)
        return 1
    if warnings:
        print("  %d checks passed, %d warning(s)" % (checks - len(warnings), len(warnings)))
        for w in warnings:
            print("    - %s" % w)
        print("")
        print("  Install ffmpeg and ffprobe, or put them in an 'ffmpeg' folder")
        print("  beside the application. Scanning works without them; cleaning")
        print("  does not.")
        print("=" * 52)
        return 0
    print("  all %d checks passed" % checks)
    print("=" * 52)
    return 0


# ---------------------------------------------------------------------------
# gui
# ---------------------------------------------------------------------------

def cmd_gui(_args):
    try:
        from .server import run_app
    except ImportError as exc:
        sys.stderr.write("error: cannot start the window: %s\n" % exc)
        sys.stderr.write("Install the UI dependencies:  pip install cleanstreamts[gui]\n")
        return 3
    run_app()
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="CleanStreamTS",
        description=("Detect and remove tracker/decoy payloads prepended to "
                     "downloaded transport streams. With no subcommand, opens "
                     "the window."),
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (APP_NAME, __version__))
    sub = parser.add_subparsers(dest="command")

    common_scan = argparse.ArgumentParser(add_help=False)
    common_scan.add_argument("-r", "--recursive", action="store_true",
                             help="include subfolders")
    common_scan.add_argument("-e", "--ext", default=",".join(core.VIDEO_EXTENSIONS),
                             help="comma-separated extensions to consider")
    common_scan.add_argument("--no-mask", action="store_true",
                             help=("print real filenames. Default output is "
                                   "masked so it can be pasted into a bug "
                                   "report; use this when the output stays "
                                   "on your own machine."))
    common_scan.add_argument("--min-ts-packets", type=int,
                             default=core.DEFAULT_MIN_TS_PACKETS,
                             help=("consecutive 188-byte-aligned sync bytes "
                                   "required to confirm a payload (default: 8)"))

    p_gui = sub.add_parser("gui", help="open the window (default)")
    p_gui.set_defaults(func=cmd_gui)

    p_self = sub.add_parser("self-check",
                            help="verify a packaged build can find its resources and tools")
    p_self.set_defaults(func=cmd_self_check)

    p_scan = sub.add_parser("scan", parents=[common_scan],
                            help="report what is in a folder")
    p_scan.add_argument("folder")
    p_scan.add_argument("--csv", metavar="FILE", help="write a masked CSV report")
    p_scan.add_argument("--json", action="store_true",
                        help="machine-readable output (used by the window)")
    p_scan.add_argument("-v", "--verbose", action="store_true")
    p_scan.set_defaults(func=cmd_scan)

    p_clean = sub.add_parser("clean", parents=[common_scan],
                             help="repair candidates (dry run unless --apply)")
    p_clean.add_argument("folder")
    p_clean.add_argument("--apply", action="store_true",
                         help="actually write cleaned files")
    p_clean.add_argument("-o", "--output", metavar="DIR",
                         help="write cleaned files here (default: beside the input)")
    p_clean.add_argument("--files", nargs="+", metavar="REL",
                         help=("restrict to these files, given relative to the "
                               "scan folder"))
    p_clean.add_argument("--redo", action="store_true",
                         help=("clean files even if a -cleaned.mp4 already "
                               "exists (a new numbered output is written)"))
    p_clean.add_argument("--keep-intermediate", action="store_true",
                         help="keep the extracted .part.ts for inspection")
    p_clean.add_argument("--ffmpeg", help="path to ffmpeg")
    p_clean.add_argument("--ffprobe", help="path to ffprobe")
    p_clean.set_defaults(func=cmd_clean)

    p_extract = sub.add_parser("extract-decoy",
                               help="save the embedded decoy image for inspection")
    p_extract.add_argument("input")
    p_extract.add_argument("output")
    p_extract.add_argument("--probe-bytes", type=int, default=1_000_000)
    p_extract.set_defaults(func=cmd_extract_decoy)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        return cmd_gui(args) or 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
