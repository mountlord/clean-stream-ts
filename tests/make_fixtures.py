# tests/make_fixtures.py
"""
Generate test fixtures.

Two kinds, deliberately:

  structural - a decoy prefix over synthetic TS framing. Exercises the
    DETECTOR. Cheap, committed to the repo, no ffmpeg needed. Cleaning one
    of these correctly FAILS at the remux step, because the payload is
    framing with no encoded video in it - that is ffmpeg refusing to remux
    nothing, which is the behaviour we want.

  playable  - a decoy prefix over a real encoded transport stream, built
    with ffmpeg. Exercises the full extract -> remux -> validate round trip.
    Not committed (needs ffmpeg); generate locally with --with-ffmpeg.

Usage:
    python tests/make_fixtures.py [outdir] [--with-ffmpeg]
"""

import os
import struct
import subprocess
import sys
import zlib


def png_chunk(ctype, payload):
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def decoy_blob(with_idat=True):
    """
    A 1x1 fully transparent PNG - the shape observed in the wild.

    with_idat=False produces a header-only PNG (no pixel data at all), which
    is a useful second case: it is still a decoy prefix, but nothing can
    decode it.
    """
    sig = bytes.fromhex("89504E470D0A1A0A")
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    srgb = png_chunk(b"sRGB", bytes([0]))
    gama = png_chunk(b"gAMA", bytes.fromhex("0000B18F"))
    # pHYs: 4725 px/m both axes, unit=metre. Boilerplate any standard PNG
    # encoder emits - part of why the real-world decoy looks library-made.
    phys = png_chunk(b"pHYs", struct.pack(">IIB", 4725, 4725, 1))
    parts = [sig, ihdr, srgb, gama, phys]
    if with_idat:
        raw = b"\x00" + b"\x00\x00\x00\x00"          # filter byte + RGBA(0,0,0,0)
        parts.append(png_chunk(b"IDAT", zlib.compress(raw)))
    parts.append(png_chunk(b"IEND", b""))
    return b"".join(parts)


def synthetic_ts(packets=40):
    """Valid 188-byte TS framing. Structure only - no encoded video."""
    return (bytes([0x47]) + bytes(187)) * packets


def write(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
    print("  %-34s %d bytes" % (os.path.basename(path), len(data)))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outdir = args[0] if args else os.path.join(os.path.dirname(__file__), "fixtures")
    with_ffmpeg = "--with-ffmpeg" in sys.argv
    os.makedirs(outdir, exist_ok=True)

    print("Structural fixtures ->", outdir)

    gap = b"\xFF" * 85     # the padding length observed in real samples
    decoy = decoy_blob()

    write(os.path.join(outdir, "decoy_over_ts.mkv"),
          decoy + gap + synthetic_ts() + b"\x00" * 1024)

    write(os.path.join(outdir, "decoy_over_garbage.mkv"),
          decoy + gap + b"\x47" + b"\x11" * 50 + b"\x47" + b"\x22" * 600)

    write(os.path.join(outdir, "decoy_no_idat.mkv"),
          decoy_blob(with_idat=False) + gap + synthetic_ts() + b"\x00" * 512)

    write(os.path.join(outdir, "clean_matroska.mkv"),
          bytes.fromhex("1A45DFA3") + b"\x00" * 600)

    write(os.path.join(outdir, "clean_mp4.mp4"),
          bytes.fromhex("000000206674797069736F6D") + b"\x00" * 600)

    write(os.path.join(outdir, "clean_ts.ts"), synthetic_ts(60))

    if not with_ffmpeg:
        print("\n(Pass --with-ffmpeg to also build a playable round-trip fixture.)")
        return 0

    print("\nPlayable fixture (requires ffmpeg)")
    ts_path = os.path.join(outdir, "_payload.ts")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
        "-c:v", "libx264", "-c:a", "aac", "-f", "mpegts", ts_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("  ffmpeg not found - skipping.")
        return 0
    if proc.returncode != 0:
        print("  ffmpeg failed:", proc.stderr.strip())
        return 1

    with open(ts_path, "rb") as handle:
        real_ts = handle.read()
    os.remove(ts_path)

    write(os.path.join(outdir, "decoy_over_real.mkv"), decoy + gap + real_ts)
    print("\n  decoy_over_real.mkv exercises the full extract -> remux -> validate path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
