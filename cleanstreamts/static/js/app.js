// cleanstreamts/static/js/app.js
(function () {
  "use strict";

  var el = function (id) { return document.getElementById(id); };

  // path -> record, for each side. Selection is kept as a Set of paths so it
  // survives a re-render.
  var remaining = [];
  var queue = [];
  var allCandidates = [];   // every candidate path from the last scan
  var selRemain = {};
  var selQueue = {};
  var busy = false;
  var toolsOk = true;
  var scanSeq = 0;
  var firstLoad = true;
  var CLI = (document.querySelector(".app") || {}).dataset
            ? document.querySelector(".app").dataset.cli : "CleanStreamTS";
  var lastScanKey = null;

  // ---- helpers -------------------------------------------------------

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json().catch(function () { return {}; }); });
  }

  function bytes(n) {
    if (!n && n !== 0) return "";
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0, v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
    return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
  }

  function quote(s) {
    return /[\s"]/.test(s) ? '"' + s + '"' : s;
  }

  // ---- rendering -----------------------------------------------------

  function renderList(node, records, sel, dim) {
    node.innerHTML = "";
    if (!records.length) {
      var e = document.createElement("div");
      e.className = "empty";
      e.textContent = dim ? "Nothing else in this folder." : "No candidates queued.";
      node.appendChild(e);
      return;
    }
    records.forEach(function (rec) {
      var row = document.createElement("div");
      row.className = "item" + (sel[rec.path] ? " sel" : "") + (dim ? " dim" : "");
      row.title = rec.path;

      var name = document.createElement("span");
      name.textContent = rec.label;
      row.appendChild(name);

      var meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = dim
        ? rec.container + (rec.size ? "  " + bytes(rec.size) : "")
        : "payload @ " + rec.payload_offset + "  " + bytes(rec.size);
      row.appendChild(meta);

      row.addEventListener("click", function (ev) {
        if (!ev.ctrlKey && !ev.metaKey && !ev.shiftKey) {
          Object.keys(sel).forEach(function (k) { delete sel[k]; });
        }
        if (sel[rec.path]) { delete sel[rec.path]; } else { sel[rec.path] = true; }
        render();
      });

      node.appendChild(row);
    });
  }

  function render() {
    renderList(el("remainList"), remaining, selRemain, true);
    renderList(el("queueList"), queue, selQueue, false);
    el("remainCnt").textContent = remaining.length;
    el("queueCnt").textContent = queue.length;

    var haveSelRemain = Object.keys(selRemain).length > 0;
    var haveSelQueue = Object.keys(selQueue).length > 0;

    el("addBtn").disabled = busy || !haveSelRemain;
    el("addAllBtn").disabled = busy || remaining.length === 0;
    el("remBtn").disabled = busy || !haveSelQueue;
    el("browseBtn").disabled = busy;
    el("rescanBtn").disabled = busy;
    el("recursive").disabled = busy;
    el("cleanBtn").disabled = busy || queue.length === 0 || !toolsOk;
    el("stopBtn").disabled = !busy;

    renderCli();
  }

  function renderCli() {
    var folder = el("folder").value.trim() || "<folder>";
    var parts = [CLI, "clean", quote(folder)];
    if (el("recursive").checked) parts.push("-r");

    // --files is only needed when a SUBSET of the detected candidates is
    // queued. If every candidate is queued, the bare command already does
    // exactly this, and naming them all would just be noise.
    var queued = {};
    queue.forEach(function (rec) { queued[rec.path] = true; });
    var isSubset = allCandidates.some(function (p) { return !queued[p]; });

    if (queue.length && isSubset) {
      parts.push("--files");
      queue.forEach(function (rec) { parts.push(quote(relTo(folder, rec.path))); });
    }
    parts.push("--apply");
    el("cliBox").value = parts.join(" ");
  }

  function relTo(root, full) {
    var r = root.replace(/[\\/]+$/, "");
    if (full.indexOf(r) === 0) {
      return full.slice(r.length).replace(/^[\\/]+/, "").replace(/\\/g, "/");
    }
    return full;
  }

  // ---- scanning ------------------------------------------------------

  function scan(force) {
    var folder = el("folder").value.trim();
    if (!folder) return;

    // Pressing Enter in the folder field fires keydown AND change, and a
    // blur right after fires change again - three events, one intent. Skip a
    // scan whose inputs are identical to the last one unless Rescan asked
    // for it explicitly.
    var key = folder + "\u0000" + (el("recursive").checked ? "1" : "0");
    if (!force && key === lastScanKey) return;
    lastScanKey = key;

    var mySeq = ++scanSeq;

    // A new scan means a new log. The server clears its copy; clear ours NOW
    // so the box responds to the click instead of waiting for the next poll.
    el("logBox").value = "";
    lastLogText = null;

    remaining = [];
    queue = [];
    allCandidates = [];
    selRemain = {};
    selQueue = {};
    render();

    post("/api/scan", { folder: folder, recursive: el("recursive").checked })
      .then(function (res) {
        if (mySeq !== scanSeq) return;      // a newer scan started; discard
        if (res && res.ok === false) {
          appendLog(res.error || "Scan failed.");
          return;
        }
        return refresh(true);
      });
  }

  function autoScanIfValid() {
    if (el("folder").value.trim()) scan();
  }

  // ---- state polling -------------------------------------------------

  // The last SERVER log text we rendered. Compared by CONTENT, not length:
  // two successive scans routinely produce the same number of lines
  // ("Scanning X" + "Scan complete"), and a length check cannot see that the
  // text changed - which left the previous folder's log on screen after a
  // Rescan. Tracking the server text (rather than the box's value) also
  // keeps client-only appendLog() lines from being wiped by every poll.
  var lastLogText = null;

  function refresh(adopt) {
    return fetch("/api/state").then(function (r) { return r.json(); }).then(function (st) {
      toolsOk = !!st.tools_ok;
      el("toolWarn").className = "warnbar" + (toolsOk ? "" : " on");

      // Adopt the server's folder ONCE, on first load. The server may already
      // hold a scan (reopened window, or a scan driven from elsewhere), and
      // without this the field stays blank and the command preview shows a
      // <folder> placeholder for a folder that is in fact known. Only on
      // first load - re-adopting later would clobber what the user is typing.
      if (firstLoad) {
        firstLoad = false;
        if (st.folder) el("folder").value = st.folder;
        el("recursive").checked = !!st.recursive;
      }

      if (adopt) {
        // Candidates go straight to the queue; everything else is Remaining.
        queue = st.candidates.slice();
        remaining = st.others.slice();
        allCandidates = st.candidates.map(function (r) { return r.path; });
      }

      var wasBusy = busy;
      busy = !!st.busy;

      var p = st.progress || {};
      var prog = el("prog");
      if (p.running) {
        prog.className = "prog on";
        el("progLabel").textContent = p.current || "Working";
        el("progFill").style.width = (p.percent || 0) + "%";
        el("progCount").textContent =
          "file " + p.file_index + " of " + p.file_total +
          "   |   " + p.done + " cleaned, " + p.failed + " failed";
      } else {
        prog.className = "prog";
      }

      var logText = (st.log || []).join("\n");
      if (logText !== lastLogText) {
        lastLogText = logText;
        var box = el("logBox");
        box.value = logText;
        box.scrollTop = box.scrollHeight;
      }

      // A finished run re-scans server-side; adopt the fresh lists so the
      // files just cleaned drop out of the queue.
      if (wasBusy && !busy) {
        queue = st.candidates.slice();
        remaining = st.others.slice();
        allCandidates = st.candidates.map(function (r) { return r.path; });
        selRemain = {};
        selQueue = {};
      }

      render();
    }).catch(function () { /* window closing */ });
  }

  function appendLog(line) {
    var box = el("logBox");
    box.value += (box.value ? "\n" : "") + line;
    box.scrollTop = box.scrollHeight;
  }

  // ---- moves ---------------------------------------------------------

  function move(fromArr, toArr, sel) {
    var keep = [];
    fromArr.forEach(function (rec) {
      if (sel[rec.path]) { toArr.push(rec); } else { keep.push(rec); }
    });
    Object.keys(sel).forEach(function (k) { delete sel[k]; });
    return keep;
  }

  // ---- copy ----------------------------------------------------------

  function wireCopy(btn) {
    btn.addEventListener("click", function () {
      var src = el(btn.getAttribute("data-copy"));
      var text = src.value || "";
      var done = function () {
        var old = btn.textContent;
        btn.textContent = "Copied";
        btn.classList.add("copied");
        setTimeout(function () {
          btn.textContent = old;
          btn.classList.remove("copied");
        }, 1200);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallback(src, done); });
      } else {
        fallback(src, done);
      }
    });
  }

  function fallback(src, done) {
    // WebView2 can refuse the async clipboard API without a user-gesture
    // chain it recognises; select + execCommand still works there.
    src.removeAttribute("readonly");
    src.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
    src.setAttribute("readonly", "readonly");
    window.getSelection().removeAllRanges();
  }

  // ---- wiring --------------------------------------------------------

  el("browseBtn").addEventListener("click", function () {
    post("/api/browse").then(function (res) {
      if (res && res.folder) {
        el("folder").value = res.folder;
        scan(true);                   // selecting a folder scans immediately
      }
    });
  });

  el("rescanBtn").addEventListener("click", function () { scan(true); });
  el("recursive").addEventListener("change", autoScanIfValid);
  el("folder").addEventListener("change", autoScanIfValid);
  el("folder").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") autoScanIfValid();
  });

  el("addBtn").addEventListener("click", function () {
    remaining = move(remaining, queue, selRemain);
    render();
  });

  el("addAllBtn").addEventListener("click", function () {
    remaining.forEach(function (rec) { queue.push(rec); });
    remaining = [];
    selRemain = {};
    render();
  });

  el("remBtn").addEventListener("click", function () {
    queue = move(queue, remaining, selQueue);
    render();
  });

  el("cleanBtn").addEventListener("click", function () {
    if (!queue.length) return;
    post("/api/clean", { files: queue.map(function (r) { return r.path; }) })
      .then(function (res) {
        if (res && res.ok === false) appendLog(res.error || "Could not start.");
        pollNow();
      });
  });

  el("stopBtn").addEventListener("click", function () {
    post("/api/cancel").then(pollNow);
  });

  // Save The Children. The server opens the system default browser
  // (webbrowser.open) - window.open inside a WebView2 window would try to
  // spawn another app window instead of the user's browser.
  el("donateBtn").addEventListener("click", function () {
    post("/api/open-url",
         { url: "https://www.savethechildren.org/us/ways-to-help/ways-to-give" });
  });

  wireCopy(el("copyCli"));
  wireCopy(el("copyLog"));

  // Poll fast only while something is happening. An idle window has nothing
  // to learn from the server, and a busy one needs a responsive progress bar.
  var POLL_BUSY = 500;
  var POLL_IDLE = 4000;
  var pollTimer = null;

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(function () {
      refresh(false).then(schedulePoll, schedulePoll);
    }, busy ? POLL_BUSY : POLL_IDLE);
  }

  // A click that starts work should not wait out the idle interval.
  function pollNow() {
    if (pollTimer) clearTimeout(pollTimer);
    refresh(false).then(schedulePoll, schedulePoll);
  }

  refresh(true).then(schedulePoll, schedulePoll);
  render();
})();
