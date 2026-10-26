"""QLC+ fixture definitions (``.qxf``, XML).

QLC+ itself always writes a bare ``<!DOCTYPE FixtureDefinition>``; only that exact form (no internal
subset, SYSTEM/PUBLIC id or entities) is stripped before parsing. Anything else with a DOCTYPE or
ENTITY is rejected by the hardened parser.

Attribute mapping per channel, in order: ``Preset`` (e.g. PositionPanFine) -> ``Group`` (+ ``Colour``)
-> keywords in the channel name -> normalised channel name. Fine channels (``Group Byte="1"``, a
``...Fine`` preset, ``ActsOn`` or a name ending in "fine") are attached to their coarse channel.
Multi-head modes (pixels) set ``Channel.pixel_key`` to the 1-based head number.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ._common import FixtureFormatError, first, kids, local, norm_name, read_file, safe_xml, source_path, warn
from .model import (AMBER, BLUE, COLOR_WHEEL, CYAN, DIMMER, FOCUS, FROST, GOBO_WHEEL, GREEN, INDIGO, IRIS, LIME,
                    MAGENTA, PAN, PRISM, RED, STROBE, TILT, UV, WHITE, YELLOW, ZOOM, Channel, FixtureProfile,
                    Mode, WheelSlot)

MAX_QXF_BYTES = 8 * 1024 * 1024
MAX_CHANNELS = 512

_COLOUR_ATTR = {"red": RED, "green": GREEN, "blue": BLUE, "white": WHITE, "cyan": CYAN, "magenta": MAGENTA,
                "yellow": YELLOW, "amber": AMBER, "uv": UV, "lime": LIME, "indigo": INDIGO}

_PRESET_ATTR = {
    "intensitymasterdimmer": DIMMER, "intensitydimmer": DIMMER,
    "positionpan": PAN, "positiontilt": TILT,
    "colorwheel": COLOR_WHEEL, "colormacro": COLOR_WHEEL, "gobowheel": GOBO_WHEEL,
    "shutterstrobeslowfast": STROBE, "shutterstrobefastslow": STROBE,
    "shutterirismintomax": IRIS, "shutterirismaxtomin": IRIS, "shutteriris": IRIS,
    "beamfocusnearfar": FOCUS, "beamfocusfarnear": FOCUS, "beamfocus": FOCUS,
    "beamzoomsmallbig": ZOOM, "beamzoombigsmall": ZOOM, "beamzoom": ZOOM,
    "goboindex": "gobo_index", "positionxaxis": "x_axis", "positionyaxis": "y_axis",
}
for _c, _a in _COLOUR_ATTR.items():
    _PRESET_ATTR["intensity" + _c] = _a
# colour mixer / HSV presets have no single normalised attribute: they keep their own name
_MIXER_PRESETS = {"colorrgbmixer", "colorctomixer", "colorctbmixer", "intensityhue", "intensitysaturation",
                  "intensitylightness", "intensityvalue"}

_FINE_NAME = re.compile(r"[\s_\-(]*(fine|lsb|16[\s_\-]?bit)\)?\s*$", re.I)
_MIXER_NAME = re.compile(r"cto|ctb|mix|hue|saturation|temp|cmy", re.I)
_ROTATION_NAME = re.compile(r"rot|index|shake|spin|speed|macro|scroll", re.I)
_PLAIN_DOCTYPE = re.compile(rb"<!DOCTYPE[ \t\r\n]+[A-Za-z_][A-Za-z0-9_.:\-]*[ \t\r\n]*>")
_HEX = re.compile(r"#[0-9a-f]{6}")


@dataclass
class _Def:
    name: str
    preset: str = ""
    group: str = ""
    gbyte: Optional[int] = None
    colour: str = ""
    default: Optional[int] = None
    alias: bool = False
    colors: Optional[List[WheelSlot]] = None
    gobos: Optional[List[WheelSlot]] = None


def _text(el, name: str = "") -> str:
    node = first(el, name) if name else el
    return (node.text or "").strip() if node is not None else ""


def _int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def parse_bytes(data: bytes):
    """Bytes -> root element (``FixtureDefinition``)."""
    if not isinstance(data, (bytes, bytearray)):
        raise FixtureFormatError("QXF data must be bytes")
    root = safe_xml(_PLAIN_DOCTYPE.sub(b"", bytes(data), count=1))
    if local(root.tag) != "FixtureDefinition":
        raise FixtureFormatError("not a QLC+ fixture definition (root element FixtureDefinition missing)")
    return root


def _channel_defs(root, warns: List[str]) -> Dict[str, _Def]:
    out: Dict[str, _Def] = {}
    for el in kids(root, "Channel"):
        name = (el.get("Name") or "").strip()
        if not name:
            continue
        if name in out:
            warn(warns, f"channel {name!r} is defined twice; the first definition is used")
            continue
        grp = first(el, "Group")
        d = _Def(name=name, preset=(el.get("Preset") or "").strip(), group=_text(el, "Group"),
                 gbyte=_int(grp.get("Byte")) if grp is not None else None, colour=_text(el, "Colour"),
                 default=_int(el.get("Default")))
        for cap in kids(el, "Capability"):
            mn, mx = _int(cap.get("Min")), _int(cap.get("Max"))
            if first(cap, "Alias") is not None:
                d.alias = True
            if mn is None or mx is None:
                continue
            lo, hi = max(min(mn, mx), 0), min(max(mn, mx), 255)
            label = (cap.text or "").strip()
            # older files put the colour in Color, newer ones in Res1 (with Preset ColorMacro)
            col = (cap.get("Color") or cap.get("Res1") or "").strip().lower()
            if _HEX.fullmatch(col):
                d.colors = (d.colors or []) + [WheelSlot(name=label, dmx_from=lo, dmx_to=hi, color=col)]
            if d.group.lower() == "gobo":
                res = (cap.get("Res1") or cap.get("Res") or "").strip()
                if label or res:
                    gname = label or os.path.splitext(os.path.basename(res.replace("\\", "/")))[0]
                    img = res[:260] if res and not res.startswith("#") else None
                    d.gobos = (d.gobos or []) + [WheelSlot(name=gname, dmx_from=lo, dmx_to=hi, image=img)]
        out[name] = d
    return out


def _by_name(name: str) -> Optional[str]:
    n = name.lower()
    if "frost" in n:
        return FROST
    if "iris" in n:
        return IRIS
    if "focus" in n:
        return FOCUS
    if "zoom" in n:
        return ZOOM
    if "prism" in n:
        return "prism_rotation" if _ROTATION_NAME.search(n) else PRISM
    if re.search(r"strobe|shutter", n):
        return STROBE
    return _COLOUR_ATTR.get(norm_name(name))


def _classify(ch: _Def, warns: List[str]) -> Tuple[str, int]:
    """-> (attribute, byte 0/1). Byte > 1 is treated as 1 with a warning."""
    lp = ch.preset.lower()
    byte = ch.gbyte if ch.gbyte is not None else 0
    name = ch.name
    if lp.endswith("fine") and len(lp) > 4:
        byte, lp = 1, lp[:-4]
    elif ch.gbyte is None and not lp and _FINE_NAME.search(name) and len(_FINE_NAME.sub("", name).strip()) > 1:
        byte = 1
    if byte > 1:
        warn(warns, f"channel {ch.name!r}: Byte {byte} (24-bit) is not supported; treated as a fine channel")
        byte = 1
    if byte == 1:
        name = _FINE_NAME.sub("", name).strip() or name
    if lp in _MIXER_PRESETS:
        return norm_name(name), byte
    if lp in _PRESET_ATTR:
        return _PRESET_ATTR[lp], byte
    g = ch.group.lower()
    if g == "intensity":
        return _COLOUR_ATTR.get(ch.colour.strip().lower()) or _COLOUR_ATTR.get(norm_name(name)) or DIMMER, byte
    if g == "pan":
        return PAN, byte
    if g == "tilt":
        return TILT, byte
    if g in ("colour", "color"):
        return (norm_name(name) if _MIXER_NAME.search(name) else COLOR_WHEEL), byte
    if g == "gobo":
        return (norm_name(name) if _ROTATION_NAME.search(name) else GOBO_WHEEL), byte
    if g == "shutter":
        return _by_name(name) or STROBE, byte
    if g == "prism":
        return _by_name(name) or PRISM, byte
    if g == "speed":
        return norm_name(name), byte
    return _by_name(name) or norm_name(name), byte


def _physical_of(el) -> Dict[str, Dict[str, str]]:
    ph = first(el, "Physical")
    if ph is None:
        return {}
    return {local(c.tag): {str(k): str(v) for k, v in c.attrib.items()} for c in ph}


def _mode(root, mode_el, mode_name: str, defs: Dict[str, _Def], warns: List[str]) -> Mode:
    rows: List[dict] = []
    taken: Dict[int, str] = {}
    for pos, el in enumerate(kids(mode_el, "Channel")):
        name = (el.text or "").strip()
        num = _int(el.get("Number"))
        if num is None:
            num = pos
            warn(warns, f"mode {mode_name!r}: channel {name!r} has no Number; position {pos} used")
        if not 0 <= num < MAX_CHANNELS:
            warn(warns, f"mode {mode_name!r}: channel number {num} outside 0..{MAX_CHANNELS - 1}, ignored")
            continue
        if num in taken:
            warn(warns, f"mode {mode_name!r}: channel number {num} used twice ({taken[num]!r} and {name!r}); "
                        "second ignored")
            continue
        d = defs.get(name)
        if d is None:
            warn(warns, f"mode {mode_name!r}: channel {name!r} (number {num}) is not defined, ignored")
            continue
        taken[num] = name
        rows.append({"num": num, "d": d, "acts_on": _int(el.get("ActsOn")), "head": None})
    rows.sort(key=lambda r: r["num"])

    heads = [[_int(c.text) for c in kids(h, "Channel")] for h in kids(mode_el, "Head")]
    if len(heads) > 1:
        head_of: Dict[int, int] = {}
        for k, lst in enumerate(heads):
            for n in lst:
                if n is not None:
                    head_of.setdefault(n, k)
        for r in rows:
            r["head"] = head_of.get(r["num"])

    for r in rows:
        r["attr"], r["byte"] = _classify(r["d"], warns)
        r["fine"] = None
    coarse = [r for r in rows if r["byte"] == 0]
    for f in (r for r in rows if r["byte"] == 1):
        cands = [c for c in coarse if c["fine"] is None and c["attr"] == f["attr"] and c["head"] == f["head"]]
        if f["acts_on"] is not None:
            by_num = [c for c in coarse if c["num"] == f["acts_on"] and c["fine"] is None]
            cands = by_num or cands
        if not cands:
            warn(warns, f"mode {mode_name!r}: channel {f['d'].name!r} (number {f['num'] + 1}): fine channel "
                        "without a matching coarse channel; treated as a separate 8-bit channel")
            f["byte"] = 0
            f["attr"] = norm_name(f["d"].name)
            coarse.append(f)
            continue
        c = min(cands, key=lambda c: (abs(c["num"] - f["num"]), c["num"] > f["num"]))
        c["fine"] = f
    coarse.sort(key=lambda r: r["num"])

    channels: List[Channel] = []
    for r in coarse:
        d: _Def = r["d"]
        fine = r["fine"]
        if d.default is not None:
            top = 65535 if (fine is not None and d.default > 255) else 255
            default = min(max(d.default / top, 0.0), 1.0)
        else:
            default = 0.0
        slots: List[WheelSlot] = []
        if r["attr"] == COLOR_WHEEL and d.colors:
            slots = [WheelSlot(**vars(s)) for s in d.colors]
        elif r["attr"] == GOBO_WHEEL and d.gobos:
            slots = [WheelSlot(**vars(s)) for s in d.gobos]
        channels.append(Channel(
            name=d.name, attribute=r["attr"], offsets=[r["num"]] + ([fine["num"]] if fine else []),
            default=default, source_attribute=d.preset or d.group,
            pixel_key=None if r["head"] is None else str(r["head"] + 1), wheel_slots=slots))
        if d.alias or (fine and fine["d"].alias):
            warn(warns, "Alias (mode-dependent capability aliases) is not supported")
    if not channels:
        raise FixtureFormatError(f"mode {mode_name!r} contains no usable channels")
    return Mode(name=mode_name, channels=channels, physical=_physical_of(mode_el) or _physical_of(root))


def parse_root(root, source: Optional[str] = None) -> FixtureProfile:
    """Build a profile from a parsed ``FixtureDefinition`` element."""
    try:
        return _parse(root, source)
    except FixtureFormatError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FixtureFormatError(f"QXF could not be processed: {type(e).__name__}: {e}") from e


def _parse(root, source) -> FixtureProfile:
    if local(root.tag) != "FixtureDefinition":
        raise FixtureFormatError("not a QLC+ fixture definition")
    warns: List[str] = []
    defs = _channel_defs(root, warns)
    mode_els = kids(root, "Mode")
    if not mode_els:
        raise FixtureFormatError("QXF contains no Mode")
    modes: List[Mode] = []
    seen = set()
    errors = []
    for i, el in enumerate(mode_els):
        name = (el.get("Name") or "").strip() or f"Mode {i + 1}"
        if name in seen:
            name = f"{name} ({i + 1})"
        seen.add(name)
        try:
            modes.append(_mode(root, el, name, defs, warns))
        except FixtureFormatError as e:
            errors.append(str(e))
            warn(warns, f"mode {name!r} skipped: {e}")
    if not modes:
        raise FixtureFormatError("no usable mode: " + "; ".join(errors))
    typ = _text(root, "Type")
    return FixtureProfile(name=_text(root, "Model") or "QLC+ fixture", manufacturer=_text(root, "Manufacturer"),
                          format="qxf", modes=modes, categories=[typ] if typ else [],
                          physical=_physical_of(root), warnings=warns, source=source)


def load_qxf(path) -> FixtureProfile:
    """Parse a QLC+ .qxf file (path, or the file contents as bytes) into a FixtureProfile with all modes."""
    if isinstance(path, (bytes, bytearray)):
        if len(path) > MAX_QXF_BYTES:
            raise FixtureFormatError(f"file too large (max {MAX_QXF_BYTES} bytes)")
        return parse_root(parse_bytes(path))
    return parse_root(parse_bytes(read_file(path, MAX_QXF_BYTES)), source_path(path))
