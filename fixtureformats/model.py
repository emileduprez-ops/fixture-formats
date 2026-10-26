"""Format-neutral data model returned by all parsers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# Normalised attribute names. Channels that do not match one of these get a normalised
# version of their own name (lowercase, non-alphanumerics replaced by "_").
DIMMER = "dimmer"
RED = "red"
GREEN = "green"
BLUE = "blue"
WHITE = "white"
AMBER = "amber"
UV = "uv"
LIME = "lime"
INDIGO = "indigo"
CYAN = "cyan"
MAGENTA = "magenta"
YELLOW = "yellow"
PAN = "pan"
TILT = "tilt"
ZOOM = "zoom"
FOCUS = "focus"
IRIS = "iris"
PRISM = "prism"
FROST = "frost"
STROBE = "strobe"
COLOR_WHEEL = "color_wheel"
GOBO_WHEEL = "gobo_wheel"

ATTRIBUTES = (DIMMER, RED, GREEN, BLUE, WHITE, AMBER, UV, LIME, INDIGO, CYAN, MAGENTA, YELLOW, PAN, TILT,
              ZOOM, FOCUS, IRIS, PRISM, FROST, STROBE, COLOR_WHEEL, GOBO_WHEEL)


@dataclass
class WheelSlot:
    """One position on a colour or gobo wheel.

    ``dmx_from``/``dmx_to`` are inclusive raw DMX values in the resolution given by ``bits``
    (8 = 0..255, 16 = 0..65535). ``color`` is ``"#rrggbb"`` for colour slots, ``image`` is the
    media/resource name referenced by the source file (the image itself is not loaded).
    """
    name: str
    dmx_from: int
    dmx_to: int
    bits: int = 8
    color: Optional[str] = None
    image: Optional[str] = None

    @property
    def start(self) -> float:
        """Start of the range as a fraction 0..1."""
        return self.dmx_from / float(2 ** self.bits - 1)

    @property
    def end(self) -> float:
        """End of the range as a fraction 0..1."""
        return self.dmx_to / float(2 ** self.bits - 1)


@dataclass
class Channel:
    """One logical channel of a mode (a coarse byte plus optional fine bytes)."""
    name: str
    attribute: str
    offsets: List[int]                     # 0-based DMX offsets, coarse first, then fine bytes
    default: float = 0.0                   # default value as a fraction 0..1
    source_attribute: str = ""             # attribute/preset/type exactly as named in the source file
    geometry: str = ""                     # GDTF geometry name
    pixel_key: Optional[str] = None        # pixel/head key for multi-cell fixtures
    wheel_slots: List[WheelSlot] = field(default_factory=list)

    @property
    def offset(self) -> int:
        """0-based offset of the coarse byte."""
        return self.offsets[0]

    @property
    def fine_offset(self) -> Optional[int]:
        """0-based offset of the first fine byte, or None for an 8-bit channel."""
        return self.offsets[1] if len(self.offsets) > 1 else None

    @property
    def bits(self) -> int:
        return 8 * len(self.offsets)

    @property
    def default_dmx(self) -> int:
        """Default value as a raw DMX value in the channel's resolution."""
        return int(round(self.default * (2 ** self.bits - 1)))


@dataclass
class Mode:
    name: str
    channels: List[Channel] = field(default_factory=list)
    physical: Dict[str, Any] = field(default_factory=dict)

    @property
    def footprint(self) -> int:
        """Number of DMX addresses this mode occupies."""
        return max((o + 1 for c in self.channels for o in c.offsets), default=0)

    def channel(self, attribute: str) -> Optional[Channel]:
        """First channel with the given normalised attribute (or None)."""
        return next((c for c in self.channels if c.attribute == attribute), None)


@dataclass
class FixtureProfile:
    name: str
    manufacturer: str = ""
    short_name: str = ""
    format: str = ""                       # "gdtf", "qxf" or "ofl"
    modes: List[Mode] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    physical: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    source: Optional[str] = None           # path of the parsed file, if loaded from disk

    @property
    def mode_names(self) -> List[str]:
        return [m.name for m in self.modes]

    def mode(self, which: Union[str, int, None] = None) -> Mode:
        """Mode by name (exact, then case-insensitive) or index; None = first mode. Raises KeyError."""
        if which is None:
            if not self.modes:
                raise KeyError("profile has no modes")
            return self.modes[0]
        if isinstance(which, int) and not isinstance(which, bool):
            if 0 <= which < len(self.modes):
                return self.modes[which]
            raise KeyError(f"mode index {which} out of range (0..{len(self.modes) - 1})")
        for m in self.modes:
            if m.name == which:
                return m
        low = str(which).strip().casefold()
        for m in self.modes:
            if m.name.casefold() == low:
                return m
        raise KeyError(f"mode {which!r} not found; available: {self.mode_names}")
