"""Open Fixture Library ``fixture.json``.

Supported: modes, availableChannels, capability/capabilities, fineChannelAliases (16 bit and up),
null channels (unused addresses), matrixChannels/templateChannels (pixel fixtures), colour and gobo
wheels. Missing fields are handled with a warning where possible.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Dict, List, Optional, Tuple

from ._common import FixtureFormatError, decode_text, norm_name, read_file, source_path, warn
from .model import (BLUE, COLOR_WHEEL, DIMMER, GOBO_WHEEL, GREEN, PAN, RED, STROBE, TILT, WHITE, ZOOM, Channel,
                    FixtureProfile, Mode, WheelSlot)

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_CHANNELS = 512

_COLOR_MAP = {"red": RED, "green": GREEN, "blue": BLUE, "white": WHITE}
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _caps(chan: dict) -> list:
    c = chan.get("capabilities")
    if isinstance(c, list):
        return [x for x in c if isinstance(x, dict)]
    c = chan.get("capability")
    return [c] if isinstance(c, dict) else []


def _type(chan: dict) -> str:
    caps = _caps(chan)
    return str(caps[0].get("type", chan.get("type", ""))) if caps else str(chan.get("type", ""))


def _attr_for(name: str, chan: dict) -> str:
    caps = _caps(chan)
    typ = _type(chan)
    if typ == "Intensity":
        return DIMMER
    if typ in ("Pan", "Tilt", "Zoom"):
        return {"Pan": PAN, "Tilt": TILT, "Zoom": ZOOM}[typ]
    if typ in ("ShutterStrobe", "StrobeSpeed", "StrobeDuration"):
        return STROBE
    if typ in ("ColorIntensity", "SingleColor"):
        col = str(caps[0].get("color", chan.get("color", ""))).strip().lower() if caps else ""
        if col in _COLOR_MAP:
            return _COLOR_MAP[col]
        return norm_name(col) if col else norm_name(name)
    low = name.lower()
    if typ in ("ColorPreset", "ColorWheel") or (typ == "WheelSlot" and "color" in
                                                str(caps[0].get("wheel", low)).lower()):
        return COLOR_WHEEL
    if typ in ("WheelSlot", "WheelShake", "WheelSlotRotation", "WheelRotation") and \
            "gobo" in (str(caps[0].get("wheel", "")).lower() + low):
        return GOBO_WHEEL if typ == "WheelSlot" else norm_name(name)
    return norm_name(name)


def _range(cap: dict, bits: int) -> Optional[Tuple[int, int]]:
    rng = cap.get("dmxRange")
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        return None
    try:
        a, b = int(float(rng[0])), int(float(rng[1]))
    except (TypeError, ValueError, OverflowError):
        return None
    mx = 2 ** bits - 1
    return min(max(min(a, b), 0), mx), min(max(max(a, b), 0), mx)


def _color_slots(chan: dict, data: dict, bits: int) -> List[WheelSlot]:
    """Colour wheel positions. A capability either gives its colour directly (``colors``/``color``) or
    references ``wheels`` via ``wheel`` + ``slotNumber``. Positions without a usable colour ('Open' counts
    as white) and split positions (fractional slotNumber) are skipped."""
    wheels = data.get("wheels") if isinstance(data.get("wheels"), dict) else {}
    out: List[WheelSlot] = []
    for cap in _caps(chan):
        rng = _range(cap, bits)
        if rng is None:
            continue
        col = None
        cols = cap.get("colors")
        if isinstance(cols, list) and cols and isinstance(cols[0], str):
            col = cols[0]
        elif isinstance(cap.get("color"), str):
            col = cap["color"]
        name = str(cap.get("comment") or "")
        wname = cap.get("wheel") or (chan.get("name") if cap.get("type") == "WheelSlot" else None)
        if col is None and wname is not None and cap.get("slotNumber") is not None:
            try:
                n = float(cap["slotNumber"])
            except (TypeError, ValueError):
                continue
            slots = (wheels.get(str(wname)) or {}).get("slots")
            if n != int(n) or not isinstance(slots, list) or not 1 <= int(n) <= len(slots):
                continue
            slot = slots[int(n) - 1]
            if isinstance(slot, dict):
                if slot.get("type") == "Open":
                    col = "#ffffff"
                    name = name or "Open"
                elif isinstance(slot.get("colors"), list) and slot["colors"] and isinstance(slot["colors"][0], str):
                    col = slot["colors"][0]
                name = name or str(slot.get("name") or "")
        if col is not None and _HEX.fullmatch(col.strip()):
            out.append(WheelSlot(name=name, dmx_from=rng[0], dmx_to=rng[1], bits=bits, color=col.strip().lower()))
    return out


def _gobo_slots(chan: dict, data: dict, bits: int) -> List[WheelSlot]:
    """Gobo wheel positions from ``WheelSlot`` capabilities that reference ``wheels.<name>.slots``.

    A ``Gobo`` slot provides ``name`` and ``resource`` (string, or object with ``name``/``key``); an
    ``Open`` slot is named 'Open'. Split positions and other capability types (shake, rotation) are skipped."""
    wheels = data.get("wheels") if isinstance(data.get("wheels"), dict) else {}
    out: List[WheelSlot] = []
    for cap in _caps(chan):
        if cap.get("type") != "WheelSlot":
            continue
        rng = _range(cap, bits)
        if rng is None:
            continue
        try:
            n = float(cap.get("slotNumber"))
        except (TypeError, ValueError):
            continue
        wname = cap.get("wheel") or chan.get("name") or ""
        slots = (wheels.get(str(wname)) or {}).get("slots") if wname else None
        if n != int(n) or not isinstance(slots, list) or not 1 <= int(n) <= len(slots):
            continue
        slot = slots[int(n) - 1]
        if not isinstance(slot, dict):
            continue
        res = slot.get("resource")
        if isinstance(res, dict):
            res = res.get("name") or res.get("key")
        res = str(res).strip() if isinstance(res, str) else ""
        if slot.get("type") == "Open":
            name = "Open"
        else:
            name = str(slot.get("name") or cap.get("comment") or (res.rsplit("/", 1)[-1] if res else "")
                       or f"Gobo {int(n)}")
        out.append(WheelSlot(name=name, dmx_from=rng[0], dmx_to=rng[1], bits=bits, image=res[:260] or None))
    return out


def _default(chan: dict, bits: int) -> Tuple[float, bool]:
    dv = chan.get("defaultValue")
    if dv is None:
        return 0.0, True
    mx = float(2 ** bits - 1)
    try:
        if isinstance(dv, str):
            t = dv.strip()
            if t.endswith("%"):
                return min(max(float(t[:-1]) / 100.0, 0.0), 1.0), True
            return min(max(float(t) / mx, 0.0), 1.0), True
        if isinstance(dv, (int, float)) and not isinstance(dv, bool):
            return min(max(float(dv) / mx, 0.0), 1.0), True
    except ValueError:
        pass
    return 0.0, False


def _bits(chan: dict) -> int:
    m = re.match(r"(\d+)bit", str(chan.get("dmxValueResolution", "")).lower())
    if m and int(m.group(1)) in (8, 16, 24, 32):
        return int(m.group(1))
    fa = chan.get("fineChannelAliases")
    n = len(fa) if isinstance(fa, list) else 0
    return 8 * (1 + min(n, 3))


def _pixel_keys(data: dict, repeat) -> Optional[List[str]]:
    if isinstance(repeat, list):
        return [str(k) for k in repeat]
    m = data.get("matrix")
    if not isinstance(m, dict):
        return None
    out: List[str] = []

    def flat(x):
        if isinstance(x, list):
            for y in x:
                flat(y)
        elif x is not None:
            out.append(str(x))
    flat(m.get("pixelKeys"))
    return out or None


def _expand_mode(data: dict, mode: dict, avail: dict, templ: dict, warns: List[str]):
    """Mode channel list -> [(channel name, pixel key, definition)], name None for unused addresses."""
    out = []
    mname = mode.get("name", "?")
    for item in mode.get("channels") or []:
        if item is None:
            out.append((None, "", {}))
        elif isinstance(item, str):
            out.append((item, "", None))
        elif isinstance(item, dict) and item.get("insert") == "matrixChannels":
            keys = _pixel_keys(data, item.get("repeatFor"))
            if keys is None:
                raise FixtureFormatError(f"mode {mname!r}: matrixChannels without usable pixelKeys")
            if not isinstance(item.get("repeatFor"), list):
                warn(warns, f"mode {mname!r}: matrix order ({item.get('repeatFor')!r}) is approximated "
                            "(pixelKeys in file order)")
            tpl = list(item.get("templateChannels") or [])
            if item.get("channelOrder") == "perChannel":
                seq = [(k, t) for t in tpl for k in keys]
            else:
                seq = [(k, t) for k in keys for t in tpl]
            for k, t in seq:
                if t is None:
                    out.append((None, k, {}))
                    continue
                t = str(t)
                actual = t.replace("$pixelKey", k)
                d = templ.get(t)
                if d is None:
                    out.append((actual, k, None))
                else:
                    d = copy.deepcopy(d)
                    if isinstance(d.get("fineChannelAliases"), list):
                        d["fineChannelAliases"] = [str(a).replace("$pixelKey", k) for a in d["fineChannelAliases"]]
                    avail.setdefault(actual, d)
                    out.append((actual, k, d))
        else:
            raise FixtureFormatError(f"mode {mname!r}: unknown channel item (offsets would shift)")
    return out


def _build(data: dict, mode: dict, mname: str, warns: List[str]) -> List[Channel]:
    avail = dict(data.get("availableChannels") or {})
    templ = data.get("templateChannels") or {}
    if not isinstance(avail, dict) or not isinstance(templ, dict):
        raise FixtureFormatError("availableChannels/templateChannels must be objects")
    items = _expand_mode(data, mode, avail, templ, warns)
    if len(items) > MAX_CHANNELS:
        raise FixtureFormatError(f"mode {mname!r} has {len(items)} channels (max {MAX_CHANNELS})")
    alias: Dict[str, str] = {}
    for cname, cdef in avail.items():
        if isinstance(cdef, dict) and isinstance(cdef.get("fineChannelAliases"), list):
            for a in cdef["fineChannelAliases"]:
                alias[str(a)] = cname
    pos: Dict[str, int] = {}
    for i, (n, _k, _d) in enumerate(items):
        if n is not None:
            pos[n] = i
    chans: List[Channel] = []
    for i, (n, key, d) in enumerate(items):
        if n is None:
            continue
        if n in alias:
            if alias[n] not in pos:
                warn(warns, f"mode {mname!r}: fine channel {n!r} without coarse channel {alias[n]!r}, ignored")
            continue
        cdef = d if isinstance(d, dict) and d else avail.get(n)
        if not isinstance(cdef, dict):
            warn(warns, f"mode {mname!r}: channel {n!r} is not defined in availableChannels, ignored")
            continue
        offsets = [i]
        fa = cdef.get("fineChannelAliases")
        for a in (fa if isinstance(fa, list) else [])[:3]:
            if str(a) not in pos:
                break
            offsets.append(pos[str(a)])
        bits = _bits(cdef)
        dflt, ok = _default(cdef, bits)
        if not ok:
            warn(warns, f"channel {n!r}: defaultValue {cdef.get('defaultValue')!r} not understood, 0 used")
        attr = _attr_for(n, cdef)
        slots: List[WheelSlot] = []
        if attr == COLOR_WHEEL:
            slots = _color_slots(dict(cdef, name=cdef.get("name") or n), data, bits)
        elif attr == GOBO_WHEEL:
            slots = _gobo_slots(dict(cdef, name=cdef.get("name") or n), data, bits)
        chans.append(Channel(name=n, attribute=attr, offsets=offsets, default=dflt, source_attribute=_type(cdef),
                             pixel_key=key or None, wheel_slots=slots))
    if not chans:
        raise FixtureFormatError(f"mode {mname!r} contains no usable channels")
    return chans


def parse_ofl(data, source: Optional[str] = None) -> FixtureProfile:
    """Build a profile from an already decoded OFL fixture dict."""
    try:
        return _parse(data, source)
    except FixtureFormatError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FixtureFormatError(f"OFL could not be processed: {type(e).__name__}: {e}") from e


def _parse(data, source) -> FixtureProfile:
    warns: List[str] = []
    if not isinstance(data, dict):
        raise FixtureFormatError("OFL data must be a JSON object")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        warns.append("fixture has no 'name'; 'OFL fixture' used")
        name = "OFL fixture"
    modes = data.get("modes")
    if not isinstance(modes, list) or not modes:
        raise FixtureFormatError("OFL data contains no 'modes'")
    if not isinstance(data.get("availableChannels", {}), dict):
        raise FixtureFormatError("'availableChannels' is invalid")
    if "availableChannels" not in data and "templateChannels" not in data:
        raise FixtureFormatError("'availableChannels' is missing")
    glob_phys = data.get("physical") if isinstance(data.get("physical"), dict) else {}
    out: List[Mode] = []
    seen = set()
    errors = []
    for idx, m in enumerate(modes):
        if not isinstance(m, dict):
            warns.append(f"mode #{idx + 1} is not an object, skipped")
            continue
        mname = str(m.get("name") or m.get("shortName") or f"Mode {idx + 1}")
        if mname in seen:
            mname = f"{mname} ({idx + 1})"
        seen.add(mname)
        try:
            sub: List[str] = []
            chans = _build(data, m, mname, sub)
            warns.extend(w for w in sub if w not in warns)
            phys = copy.deepcopy(glob_phys)
            if isinstance(m.get("physical"), dict):
                phys.update(copy.deepcopy(m["physical"]))
            out.append(Mode(name=mname, channels=chans, physical=phys))
        except FixtureFormatError as e:
            errors.append(str(e))
            warns.append(f"mode {mname!r} skipped: {e}")
        except (TypeError, AttributeError, ValueError, KeyError, RecursionError) as e:
            errors.append(f"{type(e).__name__}: {e}")
            warns.append(f"mode {mname!r} skipped: invalid structure ({type(e).__name__}: {e})")
    if not out:
        raise FixtureFormatError("no usable mode: " + "; ".join(errors))
    cats = data.get("categories")
    man = data.get("manufacturer")
    short = data.get("shortName")
    return FixtureProfile(name=name.strip(), manufacturer=man.strip() if isinstance(man, str) else "",
                          short_name=short.strip() if isinstance(short, str) else "", format="ofl", modes=out,
                          categories=[str(c) for c in cats] if isinstance(cats, list) else [],
                          physical=copy.deepcopy(glob_phys), warnings=warns, source=source)


def load_ofl(path_or_dict) -> FixtureProfile:
    """Parse an OFL fixture: a path to a ``.json`` file, or an already decoded dict."""
    if isinstance(path_or_dict, dict):
        return parse_ofl(path_or_dict)
    try:
        text = decode_text(read_file(path_or_dict, MAX_JSON_BYTES))
        try:
            data = json.loads(text)
        except (ValueError, RecursionError) as e:
            raise FixtureFormatError(f"invalid JSON: {e}") from e
    except FixtureFormatError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FixtureFormatError(f"OFL could not be processed: {type(e).__name__}: {e}") from e
    return parse_ofl(data, source_path(path_or_dict))
