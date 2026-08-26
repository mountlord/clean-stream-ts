# CleanStreamTS

**Some downloaded videos play perfectly in VLC or PotPlayer and fail everywhere else.** This tool finds out why, and fixes it.

If you have ever hit this while trying to process a file that plays fine:

```
NvDecoder::NvDecoder : Error code : 300
Error Type : m_api.cuvidCreateVideoParser(&m_hParser, &videoParserParameters) returned error 300
```

…or watched `ffprobe` describe a 3 GB movie as a one-pixel image:

```
Input #0, png_pipe, from 'movie.mkv':
  Stream #0:0: Video: png, rgba, 1x1
```

…this is almost certainly your problem, and it takes about a minute to fix.

- **[For Users](#for-users)** — download the installer, point it at a folder, done. No Python, no build tools.
- **[What is actually wrong with the file](#what-is-actually-wrong-with-the-file)** — the full technical story.
- **[For Developers](#for-developers)** — run from source, use the CLI, build the installer.

---

## What it looks like

Point it at a folder. Repairable files queue themselves; everything else stays on the left.

<p align="center">
  <img src="docs/screen-1-scan.png" alt="CleanStreamTS — scanning a folder" width="900"/>
</p>

Two clean files sit in **Remaining** — an ordinary MP4, and a `.ts` that was downloaded with the prefix already stripped, so it needs nothing. The three on the right each show where their real video starts: `payload @ 205` for the older decoy variant, and the file sizes make clear these are real 2–5 GB videos, not toys.

Tick **Include subfolders** and the scan re-runs by itself:

<p align="center">
  <img src="docs/screen-2-subfolders.png" alt="Including subfolders — candidates keep their subfolder in the label" width="900"/>
</p>

Files below the scan folder keep their subfolder in the label, so two files with the same name in different folders never look identical. Note `payload @ 70` on the newest one — the decoy's size varies, which is why nothing here uses a hardcoded offset.

Cleaning writes a copy beside each original and says what it did:

<p align="center">
  <img src="docs/screen-3-cleaning.png" alt="Cleaning in progress" width="900"/>
</p>

Then rescan:

<p align="center">
  <img src="docs/screen-4-complete.png" alt="After cleaning — already-cleaned files are skipped on rescan" width="900"/>
</p>

The queue is empty and the log reads *"2 file(s) were already cleaned earlier; skipped."* Run it again over the same folder and it does nothing — which is what makes it safe to point at a whole collection, and lets an interrupted run resume instead of redoing finished work.

The **Copy** buttons put the command line or the log on your clipboard. The command line box always shows what the window is about to run, so you can script the same job.

## For Users

### 1. Requirements

Windows 10 or 11. Nothing else — ffmpeg is bundled, and the window uses the WebView2 runtime that ships with Windows.

### 2. Download and install

Grab `CleanStreamTS-install.exe` from the **[Releases](https://github.com/mountlord/clean-stream-ts/releases)** page and run it.

> [!NOTE]
> It is **one file**. Run it, choose where to put the application folder, and that's it. Nothing goes into the registry and nothing is installed system-wide — to uninstall, delete the folder.

You get a folder containing `CleanStreamTS.exe` (the window) and `CleanStreamTS-cli.exe` (the same tool for the command line).

### 3. Clean some files

1. Launch **CleanStreamTS.exe**
2. **Browse…** to a folder — the scan starts by itself
3. Anything repairable lands in **Clean Queue** automatically; everything else sits in **Remaining**
4. Click **Clean Files**

Each repaired file is written **next to its original** as `<name>-cleaned.mp4`.

> [!IMPORTANT]
> **Your original files are never modified, moved, or deleted.** Play the `-cleaned.mp4` and satisfy yourself it is correct before you delete anything. If a cleaned file already exists, a second run writes `-cleaned-2.mp4` rather than overwriting it.

### 4. Reading the two lists

**Clean Queue** holds files where a decoy payload was found *and* a genuine transport stream was confirmed behind it. These are the ones the tool knows how to repair.

**Remaining** holds everything else:

| What you see | What it means |
|---|---|
| `matroska`, `mp4/mov`, `mpeg-ts` | A normal file. Nothing wrong with it. |
| `decoy_prefixed_unknown_payload` | A decoy was found, but what follows it is not a stream this tool recognises. **Deliberately left alone** — repairing it would mean guessing where the video starts. |
| already cleaned | You cleaned this one before. Add it back to the queue to do it again. |

If a file you expected is missing from both lists, it is either not a video extension, or it is a `-cleaned.mp4` this app produced earlier — those are skipped so a second pass never re-ingests its own output.

### 5. If cleaning fails on a file

The batch keeps going; one bad file never stops the rest. The **Log** box tells you which failed and why, and the **Copy** button next to it puts the whole log on your clipboard.

> [!NOTE]
> The window shows your **real filenames** — it is your screen and your files.
> The command line masks them by default (ten characters plus a short hash),
> because that output is what tends to end up in a bug report or a forum post.
> If you want a report you can paste publicly, use
> `CleanStreamTS-cli scan <folder> -r` rather than copying the window's log.
> Add `--no-mask` when you want real names on the command line too.

---

## What is actually wrong with the file

HLS manifests can carry an `EXT-X-MAP` tag pointing at an *initialization segment* — a small piece of setup data that belongs at the front of the stream. It is meaningful for fragmented-MP4 streams and meaningless for plain transport streams.

Some downloaders fetch whatever that URI returns and prepend it to the output file without checking that it is usable. When the source serves something else at that address — in every sample examined so far, a **1×1 fully transparent PNG** — you end up with a file laid out like this:

```
[ 120-byte PNG ][ ~85 bytes of 0xFF padding ][ ...the real MPEG-TS stream... ]
 ^ byte 0                                     ^ the actual video starts here
```

A fully transparent single pixel displays as nothing at all. It is not a broken thumbnail; it has no visual purpose. That is the shape of a **tracking beacon** — a resource that exists to be *requested*, not seen.

**Why it plays in some things and not others.** VLC, PotPlayer and Windows Media Player scan forward looking for something they recognise, find the transport stream, and play it without ever mentioning the junk at the front. Stricter parsers trust byte 0: `ffprobe` matches the PNG signature and reports a 1×1 image, and NVIDIA's `cuvidCreateVideoParser` is handed bytes that are not a video stream and returns error 300.

**The repair** is therefore not a re-encode. The video was never damaged. The tool finds where the real stream begins, copies from there to the end of the file, and rewraps it — no quality loss, and it runs at disk speed.

**Finding the payload safely.** MPEG-TS packets start with `0x47` every 188 bytes. A single `0x47` proves nothing — it turns up roughly once every 256 bytes of arbitrary data. So a payload is only accepted when that byte recurs at **eight consecutive 188-byte-aligned positions**. Anything less and the file is reported but left alone. Guessing wrong here would mean writing a corrupt file over a perfectly good source, which is exactly the outcome the whole design avoids.

---

## For Developers

### Run from source

```powershell
git clone https://github.com/mountlord/clean-stream-ts
cd clean-stream-ts
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e .

CleanStreamTS              # the window
CleanStreamTS --help       # the subcommands
```

Needs `ffmpeg` and `ffprobe` on `PATH` when running from source (the packaged build bundles them).

> [!NOTE]
> **The command name differs between a source install and the packaged app.**
> From source, pip installs one console script, `CleanStreamTS`, which takes
> every subcommand. The packaged build additionally ships a separate console
> executable, `CleanStreamTS-cli.exe`, because a windowed exe detaches from
> PowerShell and prints nothing there. Substitute whichever you have — the
> subcommands are identical, and the window's command-preview box always
> shows the name that works on the machine it is running on.

### Command line

Examples use the packaged name; from source, drop the `-cli`.

```powershell
# what is in this folder?
CleanStreamTS-cli scan "D:\ToClean\Full" -r

# what WOULD be cleaned (writes nothing)
CleanStreamTS-cli clean "D:\ToClean\Full" -r

# do it
CleanStreamTS-cli clean "D:\ToClean\Full" -r --apply

# just these two, named relative to the scan folder
CleanStreamTS-cli clean "D:\ToClean\Full" -r --files "Sub/a.mkv" "b.mkv" --apply

# pull out the decoy image and inspect it
CleanStreamTS-cli extract-decoy "D:\ToClean\movie.mkv" decoy.png
```

| Flag | Effect |
|---|---|
| `-r`, `--recursive` | include subfolders |
| `-e`, `--ext` | extensions to consider (default `.mkv,.mp4,.ts,.m4v,.mov,.webm`) |
| `--apply` | actually write files; without it, `clean` is a dry run |
| `-o`, `--output` | write cleaned files to this folder instead of beside the input |
| `--files` | restrict to specific files, **relative to the scan folder** |
| `--redo` | clean a file again even though a `-cleaned.mp4` exists |
| `--keep-intermediate` | keep the extracted `.part.ts` |
| `--min-ts-packets` | aligned sync bytes required to confirm a payload (default 8) |
| `--csv` | (`scan`) write a report; filenames are masked |
| `--no-mask` | print real filenames instead of masked ones |

`--files` matches on the path **relative to the scan folder**, not the bare filename. A recursive scan can easily hold two files with the same name in different subfolders; matching on the name alone would repair both when you picked one.

### The window is a thin shell over the CLI

The UI does not re-implement scanning or repair. It calls the same code, and the **Equivalent command line** box shows the command that reproduces what it is about to do — copy it and you can script the same job. The two cannot drift apart because there is only one implementation.

### Self-check

```powershell
python tests\selfcheck.py
```

Generates fixtures and asserts the properties that matter: the false-positive guard rejects an unaligned `0x47`, clean containers are never flagged, enumeration is deterministic and excludes the app's own output, an existing output is never overwritten, and — when ffmpeg is present — a full extract → remux → validate round trip produces a file that decodes end to end.

Two fixture kinds, deliberately. `decoy_over_ts.mkv` is a decoy over *synthetic* TS framing: it exercises the **detector**, and cleaning it correctly **fails** at the remux step because there is no encoded video inside — that is ffmpeg refusing to remux nothing, and it is the behaviour we want. `decoy_over_real.mkv` (built with `--with-ffmpeg`) carries a real encoded stream and exercises the full round trip.

### Build the installer

```powershell
powershell -ExecutionPolicy Bypass .\packaging\windows\cleanstreamts-packager.ps1
```

Produces a single `dist\CleanStreamTS-install.exe`. The spec builds two bootloaders sharing one `COLLECT` — a windowed exe and a console exe — so the runtime is stored once rather than twice, and it verifies its own output against a ledger, failing the build and naming anything missing.

Vendor files are needed first; see [`packaging/windows/vendor/README.md`](packaging/windows/vendor/README.md). Use an **LGPL** ffmpeg build: this tool never encodes video, so the GPL encoders are not needed, and an LGPL build keeps the release free of GPL redistribution obligations.

### Layout

```
cleanstreamts/
  core.py            detection - the only implementation of it
  repair.py          extract -> remux -> validate
  cli.py             scan / clean / extract-decoy / gui
  server.py          Flask app + PyWebView host
  paths.py           frozen-vs-source path anchoring
  winproc.py         CREATE_NO_WINDOW for every child process
  console_buffer.py  stdout/stderr -> CleanStreamTS-console.log
  templates/ static/ the UI
packaging/windows/   spec, packager, installer config, vendor drop
tests/               fixture generator + self-check
docs/                screenshots
```

## Reporting a problem

Please include the output of `CleanStreamTS-cli scan <folder> -r`. The command line masks filenames to ten characters plus a hash by default, so that output is safe to paste. (The window itself shows real names — copy from the CLI, not from the window's log.) **Do not attach media.**

If you have a file this tool reports as `decoy_prefixed_unknown_payload`, a capture of the `.m3u8` manifest it came from would be genuinely useful — it would show what the `EXT-X-MAP` tag pointed at.

## License

MIT — see [LICENSE](LICENSE). Bundled ffmpeg is LGPL and is not modified.
