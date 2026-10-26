# fixtureformats

A small, dependency-free Python library that reads lighting fixture definitions in three common
formats and returns them in one neutral data model:

- **GDTF** (`.gdtf`, the zip archive with `description.xml`)
- **QLC+** fixture definitions (`.qxf`)
- **Open Fixture Library** (`fixture.json`)

For every fixture you get all modes, their channels with DMX offsets (coarse and fine bytes),
default values, colour and gobo wheel slots with names and DMX ranges, and physical data where the
file provides it.

Only the Python standard library is used (`zipfile`, `xml.etree` with hardened parsing, `json`).
Python 3.9 or newer.

## Installation

```
pip install git+https://github.com/<user>/fixture-formats.git
```

Or from a local checkout:

```
pip install .
```

## Usage

```python
import fixtureformats as ff

profile = ff.load("my_spot.gdtf")          # chooses the parser by extension: .gdtf, .qxf, .json
print(profile.manufacturer, profile.name, profile.format)
print(profile.mode_names)                   # ['Standard', 'Extended', ...]

mode = profile.mode("Standard")             # by name (case-insensitive) or index; no argument = first mode
print(mode.footprint)                       # number of DMX addresses

for ch in mode.channels:
    print(ch.offset + 1, ch.name, ch.attribute, ch.bits, ch.default)

pan = mode.channel("pan")
print(pan.offset, pan.fine_offset)          # 0-based offsets of the coarse and fine byte

for slot in mode.channel("color_wheel").wheel_slots:
    print(slot.name, slot.dmx_from, slot.dmx_to, slot.color)

for w in profile.warnings:                  # anything that was skipped or approximated
    print("warning:", w)
```

The format-specific loaders can be called directly:

```python
ff.load_gdtf("spot.gdtf")                   # path or the file contents as bytes
ff.load_qxf("spot.qxf")                     # path or bytes
ff.load_ofl("fixture.json")                 # path or an already decoded dict
```

GDTF files may contain gobo images. `WheelSlot.image` holds the referenced media name, and
`ff.read_wheel_image(path, slot.image)` returns the PNG bytes from the archive (or `None`).

## Data model

All classes are plain dataclasses.

| Class | Fields |
|---|---|
| `FixtureProfile` | `name`, `manufacturer`, `short_name`, `format` (`"gdtf"`, `"qxf"`, `"ofl"`), `modes`, `categories`, `physical`, `warnings`, `source`; `mode(name_or_index)`, `mode_names` |
| `Mode` | `name`, `channels`, `physical`; `footprint`, `channel(attribute)` |
| `Channel` | `name`, `attribute`, `offsets` (coarse first, then fine bytes), `default` (0..1), `source_attribute`, `geometry` (GDTF), `pixel_key` (multi-cell fixtures), `wheel_slots`; `offset`, `fine_offset`, `bits`, `default_dmx` |
| `WheelSlot` | `name`, `dmx_from`, `dmx_to` (inclusive, raw DMX in `bits` resolution), `bits`, `color` (`"#rrggbb"`), `image`; `start`, `end` (0..1) |

`Channel.attribute` is a normalised name. Common functions map to a fixed vocabulary
(`fixtureformats.ATTRIBUTES`): `dimmer`, `red`, `green`, `blue`, `white`, `amber`, `uv`, `lime`,
`indigo`, `cyan`, `magenta`, `yellow`, `pan`, `tilt`, `zoom`, `focus`, `iris`, `prism`, `frost`,
`strobe`, `color_wheel`, `gobo_wheel`. Everything else gets a normalised version of its own name
(for example `gobo_rotation`). The original name from the file is kept in `Channel.source_attribute`.
Attributes are not made unique: a fixture with several cells has several `red` channels, told apart
by `pixel_key`.

`physical` is format-specific and passed through as found in the file: QLC+ `<Physical>` elements as
`{element: {attribute: value}}`, the OFL `physical` object as-is, and for GDTF the main model's
dimensions, the first beam and the physical properties (for example weight).

## Errors and safety

Every loader raises `fixtureformats.FixtureFormatError` (a subclass of `ValueError`) for missing,
broken or unsafe input, and nothing else. Problems that do not prevent parsing (an unknown channel
reference, a mode that cannot be used) are collected in `FixtureProfile.warnings`.

Input is treated as untrusted:

- XML is parsed without DTDs or entities (no XXE, no "billion laughs"), with limits on element count
  and nesting depth. QLC+'s own bare `<!DOCTYPE FixtureDefinition>` line is the only accepted doctype.
- GDTF archives are never extracted to disk. Zip bombs (entry count, total size, compression ratio)
  and path traversal entries are rejected.
- File sizes are capped (GDTF 256 MB, `description.xml` 16 MB, QXF and OFL 8 MB).

## Limits

- GDTF: channels with a `DMXBreak` other than 1 are skipped; only the first `LogicalChannel` of a
  channel is used; at most 4 bytes per channel. Geometry references and mode relations are not
  resolved.
- QLC+: 24-bit channels are treated as 16-bit; capability aliases are not supported; the pixel
  layout matrix is not interpreted (heads are exposed as `pixel_key`).
- OFL: only the first capability's type decides the attribute; matrix channel orders other than an
  explicit `repeatFor` list follow the order of `pixelKeys` in the file.
- Only colour and gobo wheel positions are exposed as slots; other capability ranges (rotation,
  shake, macros) are not modelled.

## Fixture files are not included

This package contains parsers only. No fixture files from GDTF Share, the QLC+ fixture library or
the Open Fixture Library are included; bring your own files and respect their respective licences.
The test suite uses small handwritten examples.

## Running the tests

```
python -m unittest discover -s tests
```

## License

MIT, see [LICENSE](LICENSE).
