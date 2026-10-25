"""Read the shutter time from a JPEG's EXIF block. Standard library only."""
import struct
from datetime import datetime, timedelta, timezone

DATETIME_ORIGINAL = 0x9003
OFFSET_TIME_ORIGINAL = 0x9011
SUBSEC_TIME_ORIGINAL = 0x9291
EXIF_IFD = 0x8769


def _app1(data):
    """The TIFF bytes inside the JPEG's Exif APP1 segment, or None."""
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\0\0":
            return data[i + 10:i + 2 + length]
        if marker == 0xDA:  # image data starts; no more headers
            return None
        i += 2 + length
    return None


def _ifd(tiff, offset, end):
    count = struct.unpack(end + "H", tiff[offset:offset + 2])[0]
    tags = {}
    for n in range(count):
        e = offset + 2 + n * 12
        tag, typ, cnt = struct.unpack(end + "HHI", tiff[e:e + 8])
        val = tiff[e + 8:e + 12]
        if typ == 2:  # ASCII
            raw = val[:cnt] if cnt <= 4 else tiff[struct.unpack(end + "I", val)[0]:][:cnt]
            tags[tag] = raw.split(b"\0")[0].decode("ascii", "replace").strip()
        elif typ == 4:  # LONG
            tags[tag] = struct.unpack(end + "I", val)[0]
    return tags


def shot_time(path):
    """Shutter time as a timezone-aware datetime, or None.

    Without OffsetTimeOriginal the camera's local time is taken as this PC's
    local time.
    """
    with open(path, "rb") as f:
        head = f.read(256 * 1024)  # Z8 EXIF sits in the first 64 KB
    try:
        tiff = _app1(head)
        if not tiff:
            return None
        end = {b"II": "<", b"MM": ">"}[tiff[:2]]
        ifd0 = _ifd(tiff, struct.unpack(end + "I", tiff[4:8])[0], end)
        exif = _ifd(tiff, ifd0[EXIF_IFD], end)
        dt = datetime.strptime(exif[DATETIME_ORIGINAL], "%Y:%m:%d %H:%M:%S")
    except (KeyError, ValueError, struct.error):
        return None
    sub = exif.get(SUBSEC_TIME_ORIGINAL, "")
    if sub.isdigit():
        dt += timedelta(seconds=int(sub) / 10 ** len(sub))
    off = exif.get(OFFSET_TIME_ORIGINAL, "")
    if len(off) == 6 and off[0] in "+-":
        sign = 1 if off[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[4:6])))
        return dt.replace(tzinfo=tz)
    return dt.astimezone()


def jpeg_complete(path):
    """True if the JPEG ends with its end-of-image marker.

    FTP cannot tell a dropped link from the end of a file, so this is how a
    cut-off upload is caught. Entropy data never holds FF D9 (0xFF is
    byte-stuffed), so a truncated file fails this check.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 32))
            return b"\xff\xd9" in f.read()
    except OSError:
        return False
