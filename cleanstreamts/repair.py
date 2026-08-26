# cleanstreamts/repair.py
"""
Repair engine.

Three stages per file, each of which must succeed before the next runs:

  1. extract - stream the validated transport-stream payload out from
     payload_offset to EOF, chunked, never loading a multi-gigabyte file
     into memory.
  2. remux   - wrap that payload in MP4 with ffmpeg -c copy. No re-encode,
     so this is fast and lossless; only the container changes.
  3. validate - ffprobe the result and require a real video stream and a
     non-zero duration. A file is reported repaired only after this passes,
     never because ffmpeg happened to exit zero.

The input file is never modified, moved or deleted. Output goes beside it as
<stem>-cleaned.mp4, and an existing output is never overwritten - the
collision counter writes -cleaned-2, -cleaned-3 and so on, and the console
says which name it used.
"""

import os
import shutil
import subprocess
from pathlib import Path

from .core import OUTPUT_SUFFIX
from .winproc import NOWINDOW

CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB streaming copy

STATUS_REPAIRED = "repaired"
STATUS_EXTRACT_FAILED = "extract_failed"
STATUS_REMUX_FAILED = "remux_failed"
STATUS_VALIDATION_FAILED = "validation_failed"
STATUS_SKIPPED = "skipped"


def resolve_tools(base_dir=None, ffmpeg_bin=None, ffprobe_bin=None):
    """
    Locate ffmpeg/ffprobe: explicit override, then a bundled ffmpeg\\ folder
    beside the exe, then PATH.
    """
    names = {"ffmpeg": ffmpeg_bin, "ffprobe": ffprobe_bin}
    resolved = {}
    for tool, override in names.items():
        if override:
            resolved[tool] = override
            continue
        if base_dir:
            for candidate in (
                Path(base_dir) / "ffmpeg" / (tool + ".exe"),
                Path(base_dir) / "ffmpeg" / tool,
            ):
                if candidate.is_file():
                    resolved[tool] = str(candidate)
                    break
        resolved.setdefault(tool, tool)
    return resolved["ffmpeg"], resolved["ffprobe"]


def check_tools(ffmpeg_bin, ffprobe_bin):
    """Returns (ok, message). Checked once up front, not per file."""
    for label, binary in (("ffmpeg", ffmpeg_bin), ("ffprobe", ffprobe_bin)):
        if shutil.which(binary) is None and not os.path.isfile(binary):
            return False, "%s not found (looked for '%s')" % (label, binary)
    return True, None


def unique_output_path(src_path, output_dir=None):
    """
    <stem>-cleaned.mp4 beside the input, or in output_dir.

    Never overwrites: a taken name becomes -cleaned-2, -cleaned-3, ...
    Re-running the tool must not destroy the result of an earlier run.
    """
    src = Path(src_path)
    target_dir = Path(output_dir) if output_dir else src.parent
    base = src.stem + OUTPUT_SUFFIX
    candidate = target_dir / (base + ".mp4")
    counter = 2
    while candidate.exists():
        candidate = target_dir / ("%s-%d.mp4" % (base, counter))
        counter += 1
    return str(candidate)


def extract_payload(src_path, payload_offset, dest_path, progress_cb=None,
                    cancel_cb=None):
    """
    Copy bytes [payload_offset, EOF) into dest_path in 64 MB chunks.
    Returns bytes written, or None if cancelled.
    """
    total = os.path.getsize(src_path) - payload_offset
    written = 0
    with open(src_path, "rb") as src, open(dest_path, "wb") as dst:
        src.seek(payload_offset)
        while True:
            if cancel_cb is not None and cancel_cb():
                return None
            chunk = src.read(CHUNK_SIZE)
            if not chunk:
                break
            dst.write(chunk)
            written += len(chunk)
            if progress_cb is not None and total > 0:
                progress_cb(written, total)
    return written


def remux_to_mp4(ts_path, mp4_path, ffmpeg_bin="ffmpeg", faststart=True):
    """Stream-copy the payload into MP4. No re-encode."""
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", ts_path,
        "-map", "0", "-c", "copy",
    ]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd.append(mp4_path)

    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", **NOWINDOW
    )
    return proc.returncode == 0, (proc.stderr or "").strip()


def probe_output(mp4_path, ffprobe_bin="ffprobe"):
    """
    Require a real video stream and a sane duration.

    Without this, a remux that produced a structurally valid but empty file
    would be reported as success.
    """
    cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name",
        "-of", "default=noprint_wrappers=1",
        mp4_path,
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", **NOWINDOW
    )
    if proc.returncode != 0:
        return False, {}, (proc.stderr or "").strip()

    info = {}
    has_video = False
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "duration":
            info["duration"] = value
        elif key == "codec_type" and value == "video":
            has_video = True
        elif key == "codec_name" and "codec" not in info:
            info["codec"] = value

    duration = info.get("duration")
    ok = has_video and duration not in (None, "N/A", "0.000000")
    return ok, info, None


def repair_one(path, payload_offset, output_dir=None, ffmpeg_bin="ffmpeg",
               ffprobe_bin="ffprobe", keep_intermediate=False,
               progress_cb=None, cancel_cb=None, log=None):
    """
    Repair a single file. Never raises for an expected failure - it returns a
    status, so one bad file cannot abort a batch.
    """
    def emit(message):
        if log is not None:
            log(message)

    out_path = unique_output_path(path, output_dir)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp_path = out_path + ".part.ts"

    result = {
        "path": path,
        "output": out_path,
        "status": None,
        "duration": None,
        "error": None,
    }

    try:
        written = extract_payload(
            path, payload_offset, tmp_path,
            progress_cb=progress_cb, cancel_cb=cancel_cb,
        )
    except OSError as exc:
        result["status"] = STATUS_EXTRACT_FAILED
        result["error"] = str(exc)
        _safe_remove(tmp_path)
        return result

    if written is None:
        result["status"] = STATUS_SKIPPED
        result["error"] = "cancelled"
        _safe_remove(tmp_path)
        return result

    ok, err = remux_to_mp4(tmp_path, out_path, ffmpeg_bin=ffmpeg_bin)
    if not ok:
        result["status"] = STATUS_REMUX_FAILED
        result["error"] = err or "ffmpeg failed"
        if not keep_intermediate:
            _safe_remove(tmp_path)
        _safe_remove(out_path)
        return result

    valid, info, probe_err = probe_output(out_path, ffprobe_bin=ffprobe_bin)
    if not keep_intermediate:
        _safe_remove(tmp_path)

    if valid:
        result["status"] = STATUS_REPAIRED
        result["duration"] = info.get("duration")
        emit("wrote %s (duration %s)" % (os.path.basename(out_path),
                                         info.get("duration", "?")))
    else:
        result["status"] = STATUS_VALIDATION_FAILED
        result["error"] = probe_err or "no video stream or zero duration"
        _safe_remove(out_path)

    return result


def _safe_remove(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
