# cleanstreamts/server.py
"""
Flask application and PyWebView host.

The window is a PyWebView (WebView2) shell over a local Flask server, the
same arrangement ChitraMaya uses. Long jobs run on a worker thread and report
through a progress dict that the UI polls, so the window never blocks and
Stop can take effect between files.

Per-file error isolation is deliberate: one file failing - a locked handle, a
malformed payload, an ffmpeg error - is recorded and the batch continues.
"""

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import __version__, APP_NAME
from . import core
from . import repair as repair_mod
from .paths import app_base_dir, resource_dir

HOST = "127.0.0.1"
PORT = 5177


class CleanStreamServer:
    """Owns app state: the last scan, the running job, and its progress."""

    def __init__(self):
        self.base_dir = app_base_dir()
        res = resource_dir()
        self.app = Flask(
            __name__,
            template_folder=str(Path(res) / "templates"),
            static_folder=str(Path(res) / "static"),
        )
        self.window = None

        self._lock = threading.Lock()
        self._scan = []          # list of detect() dicts, sorted
        self._folder = ""
        self._recursive = False
        self._cancel = threading.Event()
        self._busy = False
        self._log = []
        self._progress = {
            "running": False,
            "phase": "idle",
            "file_index": 0,
            "file_total": 0,
            "current": "",
            "percent": 0,
            "done": 0,
            "failed": 0,
        }

        self.ffmpeg_bin, self.ffprobe_bin = repair_mod.resolve_tools(self.base_dir)
        self._register_routes()

    # -- logging ---------------------------------------------------------

    def log(self, message):
        with self._lock:
            self._log.append(str(message))
            if len(self._log) > 2000:
                del self._log[:1000]
        print("[%s] %s" % (APP_NAME, message))

    # -- routes ----------------------------------------------------------

    def _register_routes(self):
        app = self.app

        @app.route("/")
        def index():
            return render_template(
                "ui.html",
                app_name=APP_NAME,
                version=__version__,
                cachebust=_CACHEBUST,
                cli_name=_cli_command_name(),
            )

        @app.route("/favicon.ico")
        def favicon():
            # WebView2 requests this on every page load. Answer it rather
            # than logging a 404 the user has to wonder about.
            return ("", 204)

        @app.route("/api/state")
        def api_state():
            with self._lock:
                return jsonify({
                    "folder": self._folder,
                    "recursive": self._recursive,
                    "busy": self._busy,
                    "progress": dict(self._progress),
                    "log": list(self._log),
                    "candidates": [self._pack(r) for r in self._scan
                                   if r["is_candidate"] and not r["already_cleaned"]],
                    "others": [self._pack(r) for r in self._scan
                               if not (r["is_candidate"] and not r["already_cleaned"])],
                    "tools_ok": self._tools_ok,
                })

        @app.route("/api/browse", methods=["POST"])
        def api_browse():
            folder = self._browse_folder()
            return jsonify({"folder": folder or ""})

        @app.route("/api/scan", methods=["POST"])
        def api_scan():
            data = request.get_json(silent=True) or {}
            folder = (data.get("folder") or "").strip()
            recursive = bool(data.get("recursive"))
            if not folder or not os.path.isdir(folder):
                return jsonify({"ok": False, "error": "Not a folder: %s" % folder}), 400
            if self._busy:
                return jsonify({"ok": False, "error": "A job is already running."}), 409
            self._do_scan(folder, recursive)
            return jsonify({"ok": True})

        @app.route("/api/clean", methods=["POST"])
        def api_clean():
            data = request.get_json(silent=True) or {}
            selected = data.get("files") or []
            if self._busy:
                return jsonify({"ok": False, "error": "A job is already running."}), 409
            if not selected:
                return jsonify({"ok": False, "error": "Nothing queued."}), 400

            ok, message = repair_mod.check_tools(self.ffmpeg_bin, self.ffprobe_bin)
            if not ok:
                return jsonify({"ok": False, "error": message}), 400

            with self._lock:
                by_path = {r["path"]: r for r in self._scan}
            jobs = [by_path[p] for p in selected if p in by_path and by_path[p]["is_candidate"]]
            if not jobs:
                return jsonify({"ok": False, "error": "No valid candidates in the queue."}), 400

            self._cancel.clear()
            thread = threading.Thread(target=self._run_clean, args=(jobs,), daemon=True)
            thread.start()
            return jsonify({"ok": True})

        @app.route("/api/cancel", methods=["POST"])
        def api_cancel():
            self._cancel.set()
            self.log("Stop requested - finishing the current file, then stopping.")
            return jsonify({"ok": True})

        @app.route("/api/open-url", methods=["POST"])
        def api_open_url():
            data = request.get_json(silent=True) or {}
            url = (data.get("url") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                self._open_url(url)
            return jsonify({"ok": True})

    # -- helpers ---------------------------------------------------------

    @property
    def _tools_ok(self):
        ok, _ = repair_mod.check_tools(self.ffmpeg_bin, self.ffprobe_bin)
        return ok

    def _pack(self, res):
        """What the UI needs. 'label' is shown; 'path' is the handle."""
        return {
            "path": res["path"],
            "label": core.relative_label(res["path"], self._folder),
            "container": res["container"],
            "size": res["size"],
            "payload_offset": res["payload_offset"],
            "packets": res["packets_confirmed"],
            "already_cleaned": res["already_cleaned"],
            "error": res["error"],
        }

    def _browse_folder(self):
        if self.window is None:
            return ""
        try:
            import webview
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as exc:
            self.log("Folder dialog failed: %s" % exc)
            return ""
        if not result:
            return ""
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    def _open_url(self, url):
        try:
            webbrowser.open(url)
        except Exception as exc:
            self.log("Could not open browser: %s" % exc)

    def _do_scan(self, folder, recursive, clear_log=True):
        with self._lock:
            self._folder = folder
            self._recursive = recursive
            if clear_log:
                # Only a user-initiated scan starts a fresh log. The refresh
                # that runs after a job must NOT erase the record of what
                # just happened - that is the part worth reading.
                self._log = []
        self.log("Scanning %s%s" % (folder, " (including subfolders)" if recursive else ""))

        files = core.find_media_files(folder, recursive=recursive)
        results = [core.detect(p) for p in files]

        with self._lock:
            self._scan = results

        candidates = sum(1 for r in results
                         if r["is_candidate"] and not r["already_cleaned"])
        already = sum(1 for r in results if r["is_candidate"] and r["already_cleaned"])
        suspects = sum(1 for r in results if r["decoy_prefixed"] and not r["is_candidate"])
        self.log("Scan complete: %d file(s), %d repairable candidate(s)."
                 % (len(results), candidates))
        if already:
            self.log("%d file(s) were already cleaned earlier; skipped." % already)
        if suspects:
            self.log("%d file(s) carry a decoy prefix but no recognizable payload; "
                     "left alone." % suspects)
        if not self._tools_ok:
            self.log("WARNING: ffmpeg/ffprobe not found. Cleaning is disabled until "
                     "they are on PATH or bundled beside the app.")

    def _run_clean(self, jobs):
        with self._lock:
            self._busy = True
            self._progress.update({
                "running": True, "phase": "cleaning",
                "file_index": 0, "file_total": len(jobs),
                "current": "", "percent": 0, "done": 0, "failed": 0,
            })

        done = 0
        failed = 0
        try:
            for index, res in enumerate(jobs, 1):
                if self._cancel.is_set():
                    self.log("Stopped before %s." % core.relative_label(res["path"], self._folder))
                    break

                with self._lock:
                    self._progress.update({
                        "file_index": index,
                        "current": core.relative_label(res["path"], self._folder),
                        "percent": 0,
                    })
                self.log("[%d/%d] %s  (payload at byte %d)"
                         % (index, len(jobs),
                            core.relative_label(res["path"], self._folder),
                            res["payload_offset"]))

                def on_progress(written, total, _idx=index):
                    with self._lock:
                        self._progress["percent"] = int(written * 100 / total) if total else 0

                outcome = repair_mod.repair_one(
                    res["path"], res["payload_offset"],
                    ffmpeg_bin=self.ffmpeg_bin, ffprobe_bin=self.ffprobe_bin,
                    progress_cb=on_progress,
                    cancel_cb=self._cancel.is_set,
                    log=self.log,
                )

                if outcome["status"] == repair_mod.STATUS_REPAIRED:
                    done += 1
                elif outcome["status"] == repair_mod.STATUS_SKIPPED:
                    self.log("    cancelled")
                else:
                    failed += 1
                    self.log("    %s: %s" % (outcome["status"], outcome["error"]))

                with self._lock:
                    self._progress.update({"done": done, "failed": failed})
        finally:
            with self._lock:
                self._busy = False
                self._progress.update({"running": False, "phase": "idle", "percent": 0})
            self.log("Finished: %d cleaned, %d failed." % (done, failed))
            self.log("Originals were not modified. Check playback before deleting anything.")
            # Refresh so cleaned files drop out of the candidate list.
            if self._folder:
                self._do_scan(self._folder, self._recursive, clear_log=False)


def _cli_command_name():
    """
    The command the user can actually type on THIS machine.

    Packaged, the console bootloader sits beside the windowed exe as
    CleanStreamTS-cli.exe. From source there is no such executable - pip
    installs one console script, CleanStreamTS, which takes the same
    subcommands. Printing the packaged name to someone running from source
    hands them a command that does not resolve.
    """
    if getattr(sys, "frozen", False):
        return APP_NAME + "-cli"
    return APP_NAME


# Per-process cache-busting token. WebView2's HTTP cache survives app
# restarts, so a changed CSS/JS file can otherwise keep serving stale until
# the cache is cleared by hand. Baked into the serving layer, not left to
# the browser.
_CACHEBUST = os.urandom(8).hex()


def run_app():
    """Start Flask on a worker thread and open the window."""
    from .console_buffer import install as install_console

    server = CleanStreamServer()
    install_console(server.base_dir, APP_NAME)
    print("%s %s starting" % (APP_NAME, __version__))

    def serve():
        # Werkzeug logs every request. The UI polls for progress, so an idle
        # app would emit a line or two per second forever - straight into
        # CleanStreamTS-console.log via ConsoleBuffer, burying any real
        # message in request noise and growing the file without bound. The
        # same logger prints the "development server" banner, which is also
        # meaningless here: this server is bound to 127.0.0.1 and serves one
        # local window.
        logging.getLogger("werkzeug").disabled = True
        server.app.run(host=HOST, port=PORT, threaded=True,
                       debug=False, use_reloader=False)

    threading.Thread(target=serve, daemon=True).start()

    try:
        import webview
    except ImportError:
        print("pywebview is not installed; open http://%s:%d/ in a browser." % (HOST, PORT))
        threading.Event().wait()
        return

    window = webview.create_window(
        "%s %s" % (APP_NAME, __version__),
        "http://%s:%d/" % (HOST, PORT),
        width=1180, height=820, min_size=(940, 680),
        background_color="#151517",
    )
    server.window = window
    webview.start()
