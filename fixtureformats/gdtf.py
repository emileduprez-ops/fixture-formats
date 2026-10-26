"""GDTF (``.gdtf``: a zip archive containing ``description.xml``).

Safety: nothing is ever extracted to disk; only ``description.xml`` (and, on request, one wheel image)
is read. Zip bombs (entry count, total/single size, compression ratio), path traversal and XML
entities are rejected. All errors surface as ``FixtureFormatError``.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Dict, List, Optional, Tuple

from ._common import FixtureFormatError, first, kids, local, norm_name, safe_xml, source_path, warn
from .model import (AMBER, BLUE, COLOR_WHEEL, CYAN, DIMMER, FOCUS, FROST, GOBO_WHEEL, GREEN, IRIS, LIME,
                    MAGENTA, PAN, PRISM, RED, STROBE, TILT, UV, WHITE, YELLOW, ZOOM, Channel, FixtureProfile,
                    Mode, WheelSlot)

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_RATIO = 1000
MAX_WHEEL_IMAGE_BYTES = 1024 * 1024
MAX_BYTES_PER_CHANNEL = 4

_ATTR_MAP = {
    "dimmer": DIMMER, "pan": PAN, "tilt": TILT, "zoom": ZOOM, "focus1": FOCUS, "iris": IRIS,
    "prism1": PRISM, "frost1": FROST,
    "coloradd_r": RED, "coloradd_g": GREEN, "coloradd_b": BLUE, "coloradd_w": WHITE,
    "coloradd_ry": AMBER, "coloradd_gy": LIME, "coloradd_uv": UV,
    "coloradd_c": CYAN, "coloradd_m": MAGENTA, "coloradd_y": YELLOW,
    "colorsub_c": CYAN, "colorsub_m": MAGENTA, "colorsub_y": YELLOW,
    "colorrgb_red": RED, "colorrgb_green": GREEN, "colorrgb_blue": BLUE, "colorrgb_white": WHITE,
    "color1": COLOR_WHEEL, "colorwheel1": COLOR_WHEEL,
    "gobo1": GOBO_WHEEL,
}


def map_attribute(gdtf_attr: str) -> str:
    """Map a GDTF attribute name to a normalised attribute (or a normalised version of the name)."""
    key = (gdtf_attr or "").strip().lower()
    if key in _ATTR_MAP:
        return _ATTR_MAP[key]
    if key.startswith(("shutter", "strobe")) and not key.endswith(("speed", "mode")):
        return STROBE
    return norm_name(gdtf_attr)


# ---------------------------------------------------------------- zip access

def _unsafe_name(n: str) -> bool:
    parts = n.replace("\\", "/").split("/")
    return n.startswith(("/", "\\")) or ".." in parts or (len(n) > 1 and n[1] == ":")


def _open_zip(src) -> zipfile.ZipFile:
    if isinstance(src, (bytes, bytearray)):
        if len(src) > MAX_FILE_BYTES:
            raise FixtureFormatError(f"file too large (max {MAX_FILE_BYTES} bytes)")
        src = io.BytesIO(bytes(src))
    else:
        try:
            src = os.fspath(src)
            size = os.path.getsize(src)
        except (OSError, TypeError, ValueError) as e:
            raise FixtureFormatError(f"cannot read file: {e}") from e
        if size > MAX_FILE_BYTES:
            raise FixtureFormatError(f"file too large ({size} bytes, max {MAX_FILE_BYTES})")
    try:
        return zipfile.ZipFile(src)
    except (OSError, TypeError) as e:
        raise FixtureFormatError(f"cannot read file: {e}") from e
    except Exception as e:  # noqa: BLE001 - BadZipFile, EOFError, ...
        raise FixtureFormatError(f"not a valid GDTF/zip file: {type(e).__name__}: {e}") from e


def read_description(src) -> bytes:
    """Return the raw ``description.xml`` of a .gdtf (path or bytes), after all safety checks."""
    with _open_zip(src) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise FixtureFormatError(f"too many entries in archive ({len(infos)})")
        total = 0
        for i in infos:
            if _unsafe_name(i.filename):
                raise FixtureFormatError(f"unsafe path in archive rejected: {i.filename!r}")
            total += i.file_size
            if i.file_size > 1024 * 1024 and i.compress_size > 0 and i.file_size / i.compress_size > MAX_RATIO:
                raise FixtureFormatError(f"suspicious compression ratio (zip bomb?): {i.filename!r}")
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise FixtureFormatError("archive is too large when uncompressed (zip bomb?)")
        match = [i for i in infos if i.filename.replace("\\", "/") == "description.xml" and not i.is_dir()]
        if not match:
            match = [i for i in infos if i.filename.replace("\\", "/").lower() == "description.xml"
                     and not i.is_dir()]
        if not match:
            raise FixtureFormatError("description.xml is missing from the GDTF archive")
        info = match[0]
        if info.file_size > MAX_XML_BYTES:
            raise FixtureFormatError("description.xml is too large")
        try:
            with zf.open(info) as fh:
                xml = fh.read(MAX_XML_BYTES + 1)
        except Exception as e:  # noqa: BLE001
            raise FixtureFormatError(f"cannot read description.xml: {type(e).__name__}: {e}") from e
        if len(xml) > MAX_XML_BYTES:
            raise FixtureFormatError("description.xml is too large")
    return xml


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_wheel_image(src, media: str) -> Optional[bytes]:
    """PNG bytes of a wheel slot image (``WheelSlot.image``) from the .gdtf, or None.

    Looks up ``wheels/<media>`` (with or without ``.png``, case-insensitive). PNG only, max 1 MB.
    Never raises: a missing or broken file returns None."""
    if not media or not isinstance(media, str) or _unsafe_name(media) or "/" in media.replace("\\", "/"):
        return None
    try:
        with _open_zip(src) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                return None
            want = {f"wheels/{media}".lower(), f"wheels/{media}.png".lower()}
            info = next((i for i in infos if i.filename.replace("\\", "/").lower() in want and not i.is_dir()), None)
            if info is None or _unsafe_name(info.filename) or info.file_size > MAX_WHEEL_IMAGE_BYTES:
                return None
            with zf.open(info) as fh:
                data = fh.read(MAX_WHEEL_IMAGE_BYTES + 1)
    except Exception:  # noqa: BLE001
        return None
    if len(data) > MAX_WHEEL_IMAGE_BYTES or not data.startswith(_PNG_MAGIC):
        return None
    return data


# ---------------------------------------------------------------- value helpers

def _parse_value(s: Optional[str]) -> Optional[Tuple[int, Optional[int]]]:
    """'value/bytes' (GDTF 1.1+) or 'value' -> (value, bytes|None)."""
    if s is None or not str(s).strip():
        return None
    t = str(s).strip()
    try:
        if "/" in t:
            v, b = t.split("/", 1)
            return int(float(v)), max(int(b), 1)
        return int(float(t)), None
    except ValueError:
        return None


def _to_channel_res(raw: Optional[str], nbytes: int) -> Optional[int]:
    """A DMX value ('value' or 'value/bytes') rescaled to a channel of ``nbytes`` bytes."""
    pv = _parse_value(raw)
    if pv is None:
        return None
    v, b = pv
    b = b or nbytes
    if b <= nbytes:
        return v * 256 ** (nbytes - b)
    return v // 256 ** (b - nbytes)


def _offsets(s: Optional[str]) -> List[int]:
    if s is None:
        return []
    out = []
    for p in str(s).replace(";", ",").split(","):
        p = p.strip()
        if not p or p.lower() == "none":
            continue
        try:
            v = int(p)
        except ValueError:
            return []
        if v < 1:
            return []
        out.append(v)
    return out


_NUM = re.compile(r"[,\s;]+")


def xyy_to_hex(color: Optional[str]) -> Optional[str]:
    """GDTF colour 'x,y,Y' (CIE xyY, Y in percent) -> '#rrggbb' (sRGB, brightness normalised).

    An empty colour means white (D65), as defined by GDTF."""
    if color is None or not str(color).strip():
        x, y, big_y = 0.3127, 0.3290, 100.0
    else:
        try:
            parts = [float(t) for t in _NUM.split(str(color).strip()) if t]
        except ValueError:
            return None
        if len(parts) == 2:
            parts.append(100.0)
        if len(parts) != 3:
            return None
        x, y, big_y = parts
    if not all(v == v and abs(v) < 1e9 for v in (x, y, big_y)) or y <= 0 or big_y <= 0:
        return None
    yy = big_y / 100.0
    cx, cz = x * yy / y, (1.0 - x - y) * yy / y
    lin = (3.2406 * cx - 1.5372 * yy - 0.4986 * cz,
           -0.9689 * cx + 1.8758 * yy + 0.0415 * cz,
           0.0557 * cx - 0.2040 * yy + 1.0570 * cz)
    lin = tuple(max(v, 0.0) for v in lin)
    top = max(lin)
    if top <= 0:
        return None
    out = []
    for v in lin:
        v /= top
        v = 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055
        out.append(min(max(int(round(v * 255)), 0), 255))
    return "#%02x%02x%02x" % tuple(out)


# ---------------------------------------------------------------- wheels

def _wheels(ft) -> Dict[str, list]:
    out: Dict[str, list] = {}
    ws = first(ft, "Wheels")
    for w in (kids(ws, "Wheel") if ws is not None else []):
        out[w.get("Name", "")] = kids(w, "Slot")
    return out


def _wheel_slots(funcs: list, wheels: Dict[str, list], nbytes: int, is_color: bool) -> List[WheelSlot]:
    """Wheel slots of a channel from its ChannelFunctions/ChannelSets.

    A ChannelSet points to a Slot of its function's Wheel via ``WheelSlotIndex`` (1-based); without an
    index the set order is used (only when there are as many sets as slots). A range runs until just
    before the next ChannelSet/ChannelFunction (or the end of the channel). On colour wheels a Slot
    without Color is white (per GDTF); on other wheels a colour is only given when the Slot has one."""
    if not wheels:
        return []
    mx = 256 ** nbytes - 1
    starts = set()
    entries = []
    for f in funcs:
        v = _to_channel_res(f.get("DMXFrom"), nbytes)
        if v is not None:
            starts.add(v)
        slots = wheels.get(f.get("Wheel") or "")
        sets = kids(f, "ChannelSet")
        for i, cs in enumerate(sets):
            cv = _to_channel_res(cs.get("DMXFrom"), nbytes)
            if cv is None:
                continue
            starts.add(cv)
            if slots:
                entries.append((cv, cs, i, len(sets), slots))
    order = sorted(starts)
    out: List[WheelSlot] = []
    for cv, cs, i, n_sets, slots in sorted(entries, key=lambda e: e[0]):
        idx = cs.get("WheelSlotIndex")
        try:
            k = int(float(idx)) if idx is not None and str(idx).strip() else (i + 1 if n_sets == len(slots) else None)
        except ValueError:
            k = None
        if k is None or not 1 <= k <= len(slots):
            continue
        slot = slots[k - 1]
        nxt = next((x for x in order if x > cv), None)
        lo = min(max(cv, 0), mx)
        hi = min(max(nxt - 1 if nxt is not None else mx, lo), mx)
        col = None
        if is_color or slot.get("Color"):
            col = xyy_to_hex(slot.get("Color"))
            if col is None and is_color:
                continue
        media = (slot.get("MediaFileName") or "").strip() or None
        name = slot.get("Name") or cs.get("Name") or f"Slot {k}"
        out.append(WheelSlot(name=name, dmx_from=lo, dmx_to=hi, bits=8 * nbytes, color=col, image=media))
    return out


# ---------------------------------------------------------------- physical data

def _physical(ft) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    geoms = first(ft, "Geometries")
    top = next(iter(geoms), None) if geoms is not None else None
    models = first(ft, "Models")
    if top is not None and models is not None and top.get("Model"):
        m = next((x for x in kids(models, "Model") if x.get("Name") == top.get("Model")), None)
        if m is not None:
            out["Dimensions"] = {k: m.get(k) for k in ("Length", "Width", "Height") if m.get(k)}
    if geoms is not None:
        beam = next((e for e in geoms.iter() if local(e.tag) == "Beam"), None)
        if beam is not None:
            keep = ("LampType", "PowerConsumption", "LuminousFlux", "ColorTemperature", "BeamAngle",
                    "FieldAngle", "BeamType", "ColorRenderingIndex")
            out["Beam"] = {k: beam.get(k) for k in keep if beam.get(k)}
    pd = first(ft, "PhysicalDescriptions")
    props = first(pd, "Properties") if pd is not None else None
    if props is not None:
        for c in props:
            if c.attrib:
                out[local(c.tag)] = {str(k): str(v) for k, v in c.attrib.items()}
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------- parsing

def _fixture_type(root):
    if local(root.tag) == "FixtureType":
        return root
    ft = first(root, "FixtureType")
    if ft is None:
        raise FixtureFormatError("no FixtureType in description.xml")
    return ft


def _mode(sel, mode_name: str, wheels, warns: List[str]) -> Mode:
    dmxch = first(sel, "DMXChannels")
    if dmxch is None:
        raise FixtureFormatError(f"mode {mode_name!r} has no DMXChannels")
    chans: List[Channel] = []
    occupied: Dict[int, str] = {}
    for ch in kids(dmxch, "DMXChannel"):
        geom = ch.get("Geometry", "") or ""
        brk = ch.get("DMXBreak", "1")
        offs = _offsets(ch.get("Offset"))
        lcs = kids(ch, "LogicalChannel")
        funcs = [f for lc in lcs for f in kids(lc, "ChannelFunction")]
        if not offs:
            if ch.get("Offset") not in (None, "", "None"):
                warn(warns, f"mode {mode_name!r}: channel {ch.get('Name') or geom}: invalid Offset "
                            f"{ch.get('Offset')!r}, skipped")
            continue                                    # virtual channel
        if str(brk).strip() not in ("1", "Overwrite"):
            warn(warns, f"mode {mode_name!r}: channel {geom}: DMXBreak {brk} is not supported, skipped")
            continue
        gattr = ""
        if lcs:
            gattr = lcs[0].get("Attribute") or ""
            if not gattr and funcs:
                gattr = funcs[0].get("Attribute") or ""
            if len(lcs) > 1:
                warn(warns, f"mode {mode_name!r}: channel {geom}/{gattr}: {len(lcs)} LogicalChannels, "
                            "only the first is used")
        if not gattr:
            warn(warns, f"mode {mode_name!r}: channel at offset {offs[0]} ({geom}) has no attribute, skipped")
            continue
        if len(offs) > MAX_BYTES_PER_CHANNEL:
            warn(warns, f"mode {mode_name!r}: channel {gattr}: {len(offs)} bytes, only the first "
                        f"{MAX_BYTES_PER_CHANNEL} are used")
            offs = offs[:MAX_BYTES_PER_CHANNEL]
        offsets = [o - 1 for o in offs]
        if any(o > 511 for o in offsets):
            warn(warns, f"mode {mode_name!r}: channel {gattr}: offset beyond 512, skipped")
            continue
        name = ch.get("Name") or (f"{geom}_{gattr}" if geom else gattr)
        for o in offsets:
            if o in occupied:
                warn(warns, f"mode {mode_name!r}: DMX offset {o + 1} used twice ({occupied[o]} and {name})")
            occupied[o] = name
        default_fn = None
        init = ch.get("InitialFunction")
        if init:
            fname = init.split(".")[-1]
            default_fn = next((f for f in funcs if f.get("Name") == fname), None)
        if default_fn is None and funcs:
            default_fn = funcs[0]
        default = 0.0
        if default_fn is not None:
            pv = _parse_value(default_fn.get("Default"))
            if pv is not None:
                nbytes = max(1, min(pv[1] or len(offsets), 4))
                default = min(max(pv[0] / float(256 ** nbytes - 1), 0.0), 1.0)
        attr = map_attribute(gattr)
        slots: List[WheelSlot] = []
        if wheels and lcs:
            lfuncs = kids(lcs[0], "ChannelFunction")
            if any(f.get("Wheel") for f in lfuncs):
                slots = _wheel_slots(lfuncs, wheels, len(offsets),
                                     is_color=attr == COLOR_WHEEL or gattr.lower().startswith("color"))
        chans.append(Channel(name=name, attribute=attr, offsets=offsets, default=default,
                             source_attribute=gattr, geometry=geom, wheel_slots=slots))
    if not chans:
        raise FixtureFormatError(f"mode {mode_name!r} contains no usable DMX channels")
    chans.sort(key=lambda c: c.offset)
    return Mode(name=mode_name, channels=chans)


def parse_description(root, source: Optional[str] = None) -> FixtureProfile:
    """Build a profile from an already parsed ``description.xml`` root element."""
    try:
        return _parse(root, source)
    except FixtureFormatError:
        raise
    except Exception as e:  # noqa: BLE001 - broken input must never raise anything unexpected
        raise FixtureFormatError(f"GDTF could not be processed: {type(e).__name__}: {e}") from e


def _parse(root, source) -> FixtureProfile:
    ft = _fixture_type(root)
    dm = first(ft, "DMXModes")
    mode_els = kids(dm, "DMXMode") if dm is not None else []
    if not mode_els:
        raise FixtureFormatError("GDTF contains no DMXModes")
    warns: List[str] = []
    wheels = _wheels(ft)
    modes: List[Mode] = []
    seen = set()
    errors = []
    for i, el in enumerate(mode_els):
        name = (el.get("Name") or "").strip() or f"Mode {i + 1}"
        if name in seen:
            name = f"{name} ({i + 1})"
        seen.add(name)
        try:
            modes.append(_mode(el, name, wheels, warns))
        except FixtureFormatError as e:
            errors.append(str(e))
            warn(warns, f"mode {name!r} skipped: {e}")
    if not modes:
        raise FixtureFormatError("no usable mode: " + "; ".join(errors))
    model = (ft.get("Name") or "").strip() or (ft.get("LongName") or "").strip() \
        or (ft.get("ShortName") or "").strip() or "GDTF fixture"
    return FixtureProfile(name=model, manufacturer=(ft.get("Manufacturer") or "").strip(),
                          short_name=(ft.get("ShortName") or "").strip(), format="gdtf", modes=modes,
                          physical=_physical(ft), warnings=warns, source=source)


def load_gdtf(path) -> FixtureProfile:
    """Parse a .gdtf file (path, or the file contents as bytes) into a FixtureProfile with all modes."""
    try:
        root = safe_xml(read_description(path))
    except FixtureFormatError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FixtureFormatError(f"GDTF could not be processed: {type(e).__name__}: {e}") from e
    return parse_description(root, source_path(path))
