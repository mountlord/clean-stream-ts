# tests/selfcheck.py
"""
Self-check.

Asserts the properties that actually matter, against generated fixtures:

  * a decoy prefix over validated TS framing IS a candidate
  * a decoy prefix over anything else is NOT a candidate, so the tool never
    guesses at a payload offset
  * a lone 0x47 that does not repeat at 188-byte intervals is REJECTED -
    the false-positive guard, and the reason detection requires eight
    consecutive packet-aligned hits rather than one byte match
  * clean containers are identified and never flagged
  * enumeration is deterministic, and excludes this app's own output
  * repair writes a playable file, leaves the original untouched, and never
    overwrites an existing output

Run:  python tests/selfcheck.py
Exit code is 0 only if every check passes.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cleanstreamts import core, repair as repair_mod   # noqa: E402

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  PASS  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (label, ("  [%s]" % detail) if detail else ""))


def have_ffmpeg():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def main():
    workdir = tempfile.mkdtemp(prefix="cst-selfcheck-")
    try:
        gen = os.path.join(os.path.dirname(__file__), "make_fixtures.py")
        cmd = [sys.executable, gen, workdir]
        if have_ffmpeg():
            cmd.append("--with-ffmpeg")
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        print("\nDetection")
        res = {name: core.detect(os.path.join(workdir, name))
               for name in sorted(os.listdir(workdir))}

        r = res["decoy_over_ts.mkv"]
        check("decoy over TS framing is a candidate", r["is_candidate"])
        check("payload offset lands past the decoy",
              r["payload_offset"] is not None and r["payload_offset"] > r["decoy_end"],
              "offset=%s decoy_end=%s" % (r["payload_offset"], r["decoy_end"]))
        check("gap length recorded", r["gap_len"] == 85, "gap=%s" % r["gap_len"])

        r = res["decoy_over_garbage.mkv"]
        check("stray 0x47 is NOT mistaken for a payload", not r["is_candidate"])
        check("garbage payload reported as decoy-prefixed", r["decoy_prefixed"])
        check("garbage payload has no offset", r["payload_offset"] is None)

        r = res["decoy_no_idat.mkv"]
        check("header-only decoy still detected", r["is_candidate"])

        check("matroska identified",
              res["clean_matroska.mkv"]["container"] == core.CONTAINER_MATROSKA,
              res["clean_matroska.mkv"]["container"])
        check("mp4 identified",
              res["clean_mp4.mp4"]["container"] == core.CONTAINER_MP4,
              res["clean_mp4.mp4"]["container"])
        check("bare ts identified",
              res["clean_ts.ts"]["container"] == core.CONTAINER_MPEGTS,
              res["clean_ts.ts"]["container"])
        check("clean files are never candidates",
              not any(res[n]["is_candidate"]
                      for n in ("clean_matroska.mkv", "clean_mp4.mp4", "clean_ts.ts")))

        print("\nSync validator")
        aligned = (bytes([0x47]) + bytes(187)) * 10
        ok, seen = core.verify_ts_sync(aligned, 0)
        check("aligned framing confirms", ok and seen >= 8, "seen=%d" % seen)

        unaligned = bytes([0x47]) + b"\x00" * 50 + bytes([0x47]) + b"\x00" * 300
        ok, seen = core.verify_ts_sync(unaligned, 0)
        check("unaligned 0x47 rejected", not ok, "seen=%d" % seen)

        print("\nEnumeration")
        listing = core.find_media_files(workdir)
        check("enumeration is sorted", listing == sorted(listing))
        check("enumeration is stable across calls",
              listing == core.find_media_files(workdir))

        own = os.path.join(workdir, "something-cleaned.mp4")
        open(own, "wb").write(b"\x00" * 16)
        check("own output excluded from scans",
              own not in core.find_media_files(workdir))
        check("is_own_output matches the counter variants",
              core.is_own_output("a-cleaned.mp4")
              and core.is_own_output("a-cleaned-2.mp4")
              and not core.is_own_output("a-cleanedish.mp4"))

        print("\nOutput naming")
        target = repair_mod.unique_output_path(os.path.join(workdir, "movie.mkv"))
        check("output is <stem>-cleaned.mp4",
              os.path.basename(target) == "movie-cleaned.mp4",
              os.path.basename(target))
        open(target, "wb").write(b"")
        second = repair_mod.unique_output_path(os.path.join(workdir, "movie.mkv"))
        check("existing output is never overwritten",
              os.path.basename(second) == "movie-cleaned-2.mp4",
              os.path.basename(second))
        os.remove(target)

        print("\nRepair round trip")
        playable = os.path.join(workdir, "decoy_over_real.mkv")
        if not os.path.isfile(playable):
            print("  SKIP  ffmpeg not available - round trip not exercised")
        else:
            before = os.path.getsize(playable)
            det = core.detect(playable)
            check("real-payload fixture is a candidate", det["is_candidate"])

            outcome = repair_mod.repair_one(playable, det["payload_offset"])
            check("repair reports success",
                  outcome["status"] == repair_mod.STATUS_REPAIRED,
                  "%s / %s" % (outcome["status"], outcome["error"]))
            check("original untouched", os.path.getsize(playable) == before)

            out = outcome["output"]
            if os.path.isfile(out):
                probe = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", out, "-f", "null", "-"],
                    capture_output=True, text=True)
                check("output decodes end to end", probe.returncode == 0,
                      probe.stderr.strip()[:120])
                check("no intermediate left behind",
                      not os.path.isfile(out + ".part.ts"))
            else:
                check("output file exists", False)

        print("")
        print("=" * 52)
        print("  %d passed, %d failed" % (PASS, FAIL))
        print("=" * 52)
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
