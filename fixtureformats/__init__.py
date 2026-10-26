"""fixtureformats: parse GDTF, QLC+ (.qxf) and Open Fixture Library (.json) fixture definitions.

Every loader returns a :class:`FixtureProfile` with all modes, channels, DMX offsets (coarse and fine),
default values, colour/gobo wheel slots and physical data where present. Invalid or unsafe input
raises :class:`FixtureFormatError` (a ``ValueError``) and nothing else.
"""
from __future__ import annotations

import os

from ._common import FixtureFormatError
from .gdtf import load_gdtf, read_wheel_image
from .model import ATTRIBUTES, Channel, FixtureProfile, Mode, WheelSlot
from .ofl import load_ofl
from .qxf import load_qxf

__version__ = "0.1.0"

__all__ = ["load", "load_gdtf", "load_qxf", "load_ofl", "read_wheel_image", "FixtureProfile", "Mode",
           "Channel", "WheelSlot", "FixtureFormatError", "ATTRIBUTES", "__version__"]

_LOADERS = {".gdtf": load_gdtf, ".qxf": load_qxf, ".json": load_ofl}


def load(path) -> FixtureProfile:
    """Load a fixture file, choosing the parser by extension (.gdtf, .qxf, .json)."""
    try:
        ext = os.path.splitext(os.fspath(path))[1].lower()
    except TypeError as e:
        raise FixtureFormatError(f"expected a file path, got {type(path).__name__}") from e
    loader = _LOADERS.get(ext)
    if loader is None:
        raise FixtureFormatError(f"unsupported file extension {ext!r} (expected .gdtf, .qxf or .json)")
    return loader(path)
