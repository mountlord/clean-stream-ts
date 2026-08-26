# cleanstreamts/core.py
"""
Detection core.

Some HLS downloaders follow a manifest's initialization-segment reference
(EXT-X-MAP) and prepend whatever that URI returns to the output file, without
checking that it is usable stream-initialization data. When a source serves a
small decoy or tracking image at that URI instead, the resulting file is that
image's bytes followed by an otherwise valid transport stream.

Tolerant players scan forward, find the real stream and play it, so the file
looks fine. Strict parsers - hardware decoders, media SDKs, ffprobe's format
guesser - trust byte 0 and fail. NVDEC reports this as:

    cuvidCreateVideoParser(...) returned error 300

This module is the ONLY place detection is implemented. The CLI and the
server both import from here, so the two can never disagree about what
counts as a candidate.
"""

import hashlib
import os
import struct

PNG_SIGNATURE = bytes.fromhex("89504E470D0A1A0A")
EBML_SIGNATURE = bytes.fromhex("1A45DFA3")

TS_PACKET_SIZE = 188
TS_SYNC_BYTE = 0x47

DEFAULT_HEAD_BYTES = 65536
DEFAULT_ESCALATE_BYTES = 4_000_000
DEFAULT_MIN_TS_PACKETS = 8

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".ts", ".m4v", ".mov", ".webm")

# Suffix this app appends to its own output. Scans exclude these so a second
# run over the same folder never treats a cleaned file as new input - the
# double-processing trap that ChitraMaya's batch mode designs out.
OUTPUT_SUFFIX = "-cleaned"

CONTAINER_MATROSKA = "matroska"
CONTAINER_MP4 = "mp4/mov"
CONTAINER_MPEGTS = "mpeg-ts"
CONTAINER_UNKNOWN = "unknown"
CONTAINER_DECOY_TS = "decoy_prefixed_mpegts"
CONTAINER_DECOY_UNKNOWN = "decoy_prefixed_unknown_payload"
CONTAINER_DECOY_UNRESOLVED = "decoy_prefixed_unresolved"


# ---------------------------------------------------------------------------
# Filename masking
# ---------------------------------------------------------------------------

def mask_filename(path, name_len=10, hash_len=6):
    """
    Reduce a filename to its first name_len characters plus a short hash.

    Media filenames are often long and can be explicit; anything a user might
    reasonably paste into a bug report - console output, CSV exports, the
    on-screen lists - shows the masked form instead. The hash is derived from
    the full basename, so two files sharing a prefix never collapse to the
    same label. Real paths are used for all actual file I/O.
    """
    base = os.path.basename(path)
    name, ext = os.path.splitext(base)
    digest = hashlib.sha1(base.encode("utf-8", errors="surrogateescape")).hexdigest()[:hash_len]
    return name[:name_len] + "_" + digest + ext


def mask_path(path, name_len=10, hash_len=6):
    """Keep the directory (useful for locating a file), mask the filename."""
    return os.path.join(os.path.dirname(path), mask_filename(path, name_len, hash_len))


def relative_label(path, root=None):
    """
    The REAL filename, prefixed with its subfolder when it sits below root.

    This is what the window shows. The user is looking at their own files on
    their own screen - obfuscating the names there helps nobody and makes the
    list unreadable. Masking is for output that LEAVES the machine.
    """
    base = os.path.basename(path)
    if not root:
        return base
    try:
        rel = os.path.relpath(os.path.dirname(path), root)
    except ValueError:
        return base
    if rel and rel not in (".", os.curdir):
        return rel.replace("\\", "/") + "/" + base
    return base


def display_label(path, root=None):
    """
    What the CLI console and CSV reports show: the MASKED filename, prefixed
    with its subfolder when it sits below root.

    Media filenames can be long and explicit, and this output is meant to be
    pasteable into a bug report or a forum thread.

    The mask hashes the basename alone, so two files with the SAME name in
    different subfolders mask to the same string. Without the prefix a
    recursive scan shows two identical-looking rows and the user cannot tell
    which is which.
    """
    masked = mask_filename(path)
    if not root:
        return masked
    try:
        rel = os.path.relpath(os.path.dirname(path), root)
    except ValueError:      # different drive on Windows
        return masked
    if rel and rel not in (".", os.curdir):
        return rel.replace("\\", "/") + "/" + masked
    return masked


# ---------------------------------------------------------------------------
# Transport-stream sync validation
# ---------------------------------------------------------------------------

def read_head(path, count):
    with open(path, "rb") as handle:
        return handle.read(count)


def verify_ts_sync(data, offset, min_packets=DEFAULT_MIN_TS_PACKETS):
    """
    Confirm a genuine MPEG-TS sync point.

    A single 0x47 byte proves nothing - it occurs about once every 256 bytes
    of arbitrary data. Real TS framing repeats it at exactly 188-byte
    intervals, so we require min_packets consecutive packet-aligned hits.
    Returns (confirmed, packets_seen).
    """
    count = 0
    pos = offset
    while pos < len(data) and data[pos] == TS_SYNC_BYTE:
        count += 1
        pos += TS_PACKET_SIZE
        if count >= min_packets:
            break
    return count >= min_packets, count


def find_ts_sync(data, start=0, min_packets=DEFAULT_MIN_TS_PACKETS):
    """First offset at or after start where validated TS framing begins."""
    pos = start
    while True:
        idx = data.find(bytes([TS_SYNC_BYTE]), pos)
        if idx == -1:
            return None, 0
        confirmed, packets = verify_ts_sync(data, idx, min_packets=min_packets)
        if confirmed:
            return idx, packets
        pos = idx + 1


def guess_container(head, min_ts_packets=DEFAULT_MIN_TS_PACKETS):
    """Identify a container from its leading bytes. Extension is ignored."""
    if head[:8] == PNG_SIGNATURE:
        return "decoy_prefixed"
    if head[:4] == EBML_SIGNATURE:
        return CONTAINER_MATROSKA
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return CONTAINER_MP4
    if head[:1] == bytes([TS_SYNC_BYTE]):
        confirmed, _ = verify_ts_sync(head, 0, min_packets=min_ts_packets)
        if confirmed:
            return CONTAINER_MPEGTS
    return CONTAINER_UNKNOWN


# ---------------------------------------------------------------------------
# PNG chunk parsing - used by the extract-decoy subcommand
# ---------------------------------------------------------------------------

def list_png_chunks(data):
    """Walk the PNG chunk stream, returning [(type, length), ...] in order."""
    chunks = []
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8].decode("ascii", errors="replace")
        chunks.append((ctype, length))
        pos += 8 + length + 4  # length + type + payload + crc
        if ctype == "IEND":
            break
        if length > len(data):
            break  # corrupt length field; stop rather than loop
    return chunks


def parse_ihdr(data):
    """Parse IHDR without an imaging library, so a broken PNG still reports."""
    idx = data.find(b"IHDR")
    if idx == -1:
        return None
    payload = data[idx + 4:idx + 4 + 13]
    if len(payload) < 13:
        return None
    width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", payload
    )
    color_names = {
        0: "Grayscale", 2: "RGB", 3: "Palette",
        4: "Grayscale+Alpha", 6: "RGBA",
    }
    return {
        "width": width,
        "height": height,
        "bit_depth": depth,
        "color_type": color_names.get(color_type, "unknown(%d)" % color_type),
        "interlace": interlace,
    }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect(path, head_bytes=DEFAULT_HEAD_BYTES, escalate_bytes=DEFAULT_ESCALATE_BYTES,
           min_ts_packets=DEFAULT_MIN_TS_PACKETS):
    """
    Inspect one file. Returns a dict describing what it actually is.

    is_candidate is True only for a decoy prefix followed by VALIDATED
    transport-stream framing - the one case this tool knows how to repair.
    A decoy prefix over an unrecognized payload is reported but never
    repaired, because the payload offset would be a guess.
    """
    result = {
        "path": path,
        "masked": mask_filename(path),
        "size": None,
        "container": None,
        "decoy_prefixed": False,
        "decoy_end": None,
        "gap_len": None,
        "payload_offset": None,
        "packets_confirmed": None,
        "bytes_at_payload": None,
        "is_candidate": False,
        "already_cleaned": False,
        "error": None,
    }

    result["already_cleaned"] = cleaned_output_exists(path)

    try:
        result["size"] = os.path.getsize(path)
        head = read_head(path, head_bytes)
    except OSError as exc:
        result["error"] = str(exc)
        return result

    if head[:8] != PNG_SIGNATURE:
        result["container"] = guess_container(head, min_ts_packets=min_ts_packets)
        return result

    result["decoy_prefixed"] = True

    iend = head.find(b"IEND")
    if iend == -1:
        try:
            head = read_head(path, escalate_bytes)
        except OSError as exc:
            result["error"] = str(exc)
            return result
        iend = head.find(b"IEND")

    if iend == -1:
        result["container"] = CONTAINER_DECOY_UNRESOLVED
        result["error"] = "IEND not found within %d byte probe" % len(head)
        return result

    decoy_end = iend + 4 + 4  # 'IEND' plus its 4-byte CRC
    result["decoy_end"] = decoy_end

    offset, packets = find_ts_sync(head, start=decoy_end, min_packets=min_ts_packets)
    if offset is None and len(head) < escalate_bytes:
        try:
            head = read_head(path, escalate_bytes)
        except OSError as exc:
            result["error"] = str(exc)
            return result
        offset, packets = find_ts_sync(head, start=decoy_end, min_packets=min_ts_packets)

    if offset is None:
        result["container"] = CONTAINER_DECOY_UNKNOWN
        result["bytes_at_payload"] = head[decoy_end:decoy_end + 16].hex(" ")
        return result

    result["container"] = CONTAINER_DECOY_TS
    result["gap_len"] = offset - decoy_end
    result["payload_offset"] = offset
    result["packets_confirmed"] = packets
    result["bytes_at_payload"] = head[offset:offset + 16].hex(" ")
    result["is_candidate"] = True
    return result


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------

def is_own_output(path):
    """True for files this app produced, so scans never re-ingest them."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem.endswith(OUTPUT_SUFFIX):
        return True
    # Collision-counter variants: name-cleaned-2, name-cleaned-3, ...
    marker = OUTPUT_SUFFIX + "-"
    idx = stem.rfind(marker)
    if idx != -1 and stem[idx + len(marker):].isdigit():
        return True
    return False


def cleaned_output_exists(path, output_dir=None):
    """
    True when this input has already been cleaned.

    The original keeps its decoy prefix forever - it is never modified - so
    without this check a second scan re-offers every file that was already
    done. Skip-existing is what makes an interrupted run resumable instead of
    redoing finished work.
    """
    target_dir = output_dir or os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.isfile(os.path.join(target_dir, stem + OUTPUT_SUFFIX + ".mp4"))


def find_media_files(folder, extensions=VIDEO_EXTENSIONS, recursive=False,
                     exclude_own_output=True):
    """
    Enumerate candidate inputs, sorted.

    Sorted deliberately: the UI lists files in this order, and results that
    reshuffle between two scans of an unchanged folder read as a bug even
    when the membership is identical.
    """
    found = []
    if recursive:
        for root, _dirs, names in os.walk(folder):
            for name in names:
                found.append(os.path.join(root, name))
    else:
        try:
            for name in os.listdir(folder):
                full = os.path.join(folder, name)
                if os.path.isfile(full):
                    found.append(full)
        except OSError:
            return []

    out = []
    for path in found:
        if not path.lower().endswith(tuple(e.lower() for e in extensions)):
            continue
        if exclude_own_output and is_own_output(path):
            continue
        out.append(path)
    return sorted(out)
