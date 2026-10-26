"""Shared helpers: the single public exception, bounded file reading and hardened XML parsing."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_XML_ELEMENTS = 200_000
MAX_XML_DEPTH = 100


class FixtureFormatError(ValueError):
    """Raised for any unreadable, invalid or unsafe input. Parsers never raise anything else."""


def read_file(path, limit: int = MAX_TEXT_BYTES) -> bytes:
    """Read a file with a size limit; OS errors become FixtureFormatError."""
    try:
        p = os.fspath(path)
        size = os.path.getsize(p)
        if size > limit:
            raise FixtureFormatError(f"file too large ({size} bytes, max {limit})")
        with open(p, "rb") as fh:
            data = fh.read(limit + 1)
    except FixtureFormatError:
        raise
    except (OSError, TypeError, ValueError) as e:
        raise FixtureFormatError(f"cannot read file: {e}") from e
    if len(data) > limit:
        raise FixtureFormatError(f"file too large (max {limit} bytes)")
    return data


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with BOM, otherwise Latin-1 as a last resort."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        return data.decode("latin-1")


_XML_SCAN = re.compile(rb"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<!\s*(DOCTYPE|ENTITY)", re.I | re.S)
_ALLOWED_ENCODINGS = ("utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin1", "latin-1")


def _parse_limited(data: bytes) -> ET.Element:
    """Incremental parse with limits on element count and nesting depth."""
    parser = ET.XMLPullParser(events=("start", "end"))
    root = None
    count = depth = 0

    def drain():
        nonlocal root, count, depth
        for ev, el in parser.read_events():
            if ev == "start":
                if root is None:
                    root = el
                count += 1
                depth += 1
                if count > MAX_XML_ELEMENTS:
                    raise FixtureFormatError(f"XML too large (more than {MAX_XML_ELEMENTS} elements)")
                if depth > MAX_XML_DEPTH:
                    raise FixtureFormatError(f"XML nested too deeply (max {MAX_XML_DEPTH})")
            else:
                depth -= 1

    for i in range(0, len(data), 65536):
        parser.feed(data[i:i + 65536])
        drain()
    parser.close()
    drain()
    if root is None:
        raise FixtureFormatError("invalid XML: no root element")
    return root


def safe_xml(data: bytes) -> ET.Element:
    """Parse XML without DTDs or entities (no XXE, no billion laughs).

    Only UTF-8/ASCII/Latin-1 is accepted (no BOM/NUL/exotic encodings that could hide a DOCTYPE),
    and any ``<!DOCTYPE``/``<!ENTITY`` outside comments and CDATA is rejected.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:1024]:
        raise FixtureFormatError("XML must be UTF-8")
    m = re.match(rb"\s*(?:\xef\xbb\xbf)?<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)[\"']", data[:512], re.I)
    if m and m.group(1).decode("ascii", "replace").lower() not in _ALLOWED_ENCODINGS:
        raise FixtureFormatError(f"XML encoding {m.group(1).decode('ascii', 'replace')!r} is not supported")
    for m in _XML_SCAN.finditer(data):
        if m.group(1):
            raise FixtureFormatError("XML with DOCTYPE/ENTITY is rejected for security reasons")
    try:
        return _parse_limited(data)
    except FixtureFormatError:
        raise
    except (ET.ParseError, ValueError, RecursionError, MemoryError) as e:
        raise FixtureFormatError(f"invalid XML: {e}") from e
    except Exception as e:  # noqa: BLE001 - never leak an unexpected exception
        raise FixtureFormatError(f"invalid XML: {type(e).__name__}: {e}") from e


def norm_name(s) -> str:
    """Normalised identifier: lowercase, runs of non-alphanumerics -> '_'."""
    s = re.sub(r"[^0-9a-z]+", "_", str(s).strip().lower()).strip("_")
    return s or "channel"


def local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def kids(el, name):
    return [c for c in el if local(c.tag) == name]


def first(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def warn(warns, msg: str) -> None:
    if msg not in warns:
        warns.append(msg)


def source_path(path):
    try:
        return os.fspath(path) if isinstance(path, (str, os.PathLike)) else None
    except TypeError:
        return None
