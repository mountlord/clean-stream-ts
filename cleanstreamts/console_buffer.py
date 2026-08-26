# cleanstreamts/console_buffer.py
"""
Console mirroring for the windowed executable.

The windowed exe has no terminal, so anything written to stdout/stderr would
otherwise vanish - including the traceback from a crash that happens before
the UI is up. ConsoleBuffer tees every line to CleanStreamTS-console.log next
to the exe, flushing per line so a hard crash still leaves the evidence on
disk.

The previous session's log is kept as CleanStreamTS-console.prev.log, so a
user who reproduces a bug and then restarts the app has not destroyed the
log that mattered.

stdout and stderr share ONE file handle - opening the same path twice in "w"
mode would have the second open truncate the first one's output.
"""

import sys
import threading
from pathlib import Path


class ConsoleBuffer:
    """A tee: writes to the original stream (if any) and to a shared log file."""

    def __init__(self, stream, fh, lock):
        self._stream = stream
        self._fh = fh
        self._lock = lock

    def write(self, text):
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.write(text)
                    self._stream.flush()
                except Exception:
                    pass
            if self._fh is not None:
                try:
                    self._fh.write(text)
                    self._fh.flush()
                except Exception:
                    pass
        return len(text)

    def flush(self):
        with self._lock:
            for target in (self._stream, self._fh):
                if target is not None:
                    try:
                        target.flush()
                    except Exception:
                        pass

    def isatty(self):
        try:
            return self._stream is not None and self._stream.isatty()
        except Exception:
            return False


def install(base_dir, app_name="CleanStreamTS"):
    """
    Redirect stdout/stderr through a ConsoleBuffer writing next to the exe.
    Returns the log path, or None if it could not be created.
    Safe to call more than once; later calls are no-ops.
    """
    if getattr(sys, "_cst_console_installed", False):
        return getattr(sys, "_cst_console_log", None)

    base = Path(base_dir)
    log_path = base / (app_name + "-console.log")
    prev_path = base / (app_name + "-console.prev.log")

    try:
        if log_path.exists():
            if prev_path.exists():
                prev_path.unlink()
            log_path.replace(prev_path)
    except OSError:
        pass

    try:
        fh = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        # A read-only install dir must not stop the app from starting.
        fh = None
        log_path = None

    lock = threading.Lock()
    sys.stdout = ConsoleBuffer(sys.__stdout__, fh, lock)
    sys.stderr = ConsoleBuffer(sys.__stderr__, fh, lock)
    sys._cst_console_installed = True
    sys._cst_console_log = log_path
    return log_path
