"""GDTF parser tests. All fixture data below is handwritten and synthetic."""
import os
import struct
import tempfile
import unittest
import zipfile
import zlib

from fixtureformats import FixtureFormatError, load_gdtf, read_wheel_image
from fixtureformats.gdtf import xyy_to_hex

GDTF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<GDTF DataVersion="1.1">
 <FixtureType Name="Testspot" ShortName="TS" Manufacturer="Acme">
  <PhysicalDescriptions><Properties><Weight Value="12.5"/></Properties></PhysicalDescriptions>
  <Models><Model Name="Base" Length="0.3" Width="0.25" Height="0.5"/></Models>
  <Geometries><Geometry Name="Body" Model="Base">
   <Beam Name="Beam" BeamAngle="25" LuminousFlux="9000" ColorTemperature="6500"/></Geometry></Geometries>
  <Wheels>
   <Wheel Name="Colors">
    <Slot Name="Open"/>
    <Slot Name="Red" Color="0.64,0.33,21.26"/>
    <Slot Name="Blue" Color="0.15,0.06,7.22"/>
   </Wheel>
   <Wheel Name="Gobos">
    <Slot Name="Open"/>
    <Slot Name="Star" MediaFileName="gobo_star"/>
   </Wheel>
  </Wheels>
  <DMXModes>
   <DMXMode Name="Standard" Geometry="Body">
    <DMXChannels>
     <DMXChannel DMXBreak="1" Offset="1,2" InitialFunction="Body_Pan.Pan.Pan 1" Geometry="Body">
      <LogicalChannel Attribute="Pan"><ChannelFunction Name="Pan 1" Attribute="Pan" DMXFrom="0/2" Default="32768/2"/></LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="1" Offset="3" Geometry="Body">
      <LogicalChannel Attribute="Tilt"><ChannelFunction Name="Tilt 1" Attribute="Tilt" DMXFrom="0/1" Default="0/1"/></LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="1" Offset="4" Geometry="Body">
      <LogicalChannel Attribute="Dimmer"><ChannelFunction Name="Dimmer" Attribute="Dimmer" DMXFrom="0/1" Default="255/1"/></LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="1" Offset="5" Geometry="Body">
      <LogicalChannel Attribute="Shutter1"><ChannelFunction Name="Shutter" Attribute="Shutter1" DMXFrom="0/1"/></LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="1" Offset="6" Geometry="Body">
      <LogicalChannel Attribute="Color1">
       <ChannelFunction Name="Color" Attribute="Color1" DMXFrom="0/1" Wheel="Colors">
        <ChannelSet Name="Open" DMXFrom="0/1" WheelSlotIndex="1"/>
        <ChannelSet Name="Red" DMXFrom="10/1" WheelSlotIndex="2"/>
        <ChannelSet Name="Blue" DMXFrom="20/1" WheelSlotIndex="3"/>
       </ChannelFunction>
       <ChannelFunction Name="Spin" Attribute="Color1WheelSpin" DMXFrom="128/1"/>
      </LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="1" Offset="7,8" Geometry="Body">
      <LogicalChannel Attribute="Gobo1">
       <ChannelFunction Name="Gobo" Attribute="Gobo1" DMXFrom="0/1" Wheel="Gobos">
        <ChannelSet Name="Open" DMXFrom="0/1"/>
        <ChannelSet Name="Star" DMXFrom="64/1"/>
       </ChannelFunction>
      </LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="1" Offset="None" Geometry="Body">
      <LogicalChannel Attribute="Zoom"><ChannelFunction Name="Z" Attribute="Zoom" DMXFrom="0/1"/></LogicalChannel>
     </DMXChannel>
     <DMXChannel DMXBreak="2" Offset="1" Geometry="Body">
      <LogicalChannel Attribute="Focus1"><ChannelFunction Name="F" Attribute="Focus1" DMXFrom="0/1"/></LogicalChannel>
     </DMXChannel>
    </DMXChannels>
   </DMXMode>
   <DMXMode Name="Basic" Geometry="Body">
    <DMXChannels>
     <DMXChannel DMXBreak="1" Offset="1" Geometry="Body">
      <LogicalChannel Attribute="Dimmer"><ChannelFunction Name="D" Attribute="Dimmer" DMXFrom="0/1"/></LogicalChannel>
     </DMXChannel>
    </DMXChannels>
   </DMXMode>
   <DMXMode Name="Empty"><DMXChannels/></DMXMode>
  </DMXModes>
 </FixtureType>
</GDTF>
"""


def png(w=2, h=2):
    raw = b"".join(b"\x00" + bytes(w) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = td.name

    def p(self, name):
        return os.path.join(self.dir, name)


class GdtfTests(Base):
    def setUp(self):
        super().setUp()
        self.path = make_zip(self.p("spot.gdtf"), {"description.xml": GDTF_XML,
                                                    "wheels/gobo_star.png": png()})
        self.prof = load_gdtf(self.path)

    def test_metadata_and_modes(self):
        p = self.prof
        self.assertEqual((p.name, p.manufacturer, p.short_name, p.format), ("Testspot", "Acme", "TS", "gdtf"))
        self.assertEqual(p.mode_names, ["Standard", "Basic"])
        self.assertTrue(any("'Empty' skipped" in w for w in p.warnings), p.warnings)
        self.assertTrue(any("DMXBreak" in w for w in p.warnings), p.warnings)
        self.assertEqual(p.source, self.path)
        self.assertEqual(p.mode("basic").footprint, 1)

    def test_channels_offsets_defaults(self):
        m = self.prof.mode("Standard")
        self.assertEqual(m.footprint, 8)
        pan = m.channel("pan")
        self.assertEqual((pan.offset, pan.fine_offset, pan.bits), (0, 1, 16))
        self.assertAlmostEqual(pan.default, 32768 / 65535, places=6)
        self.assertEqual(pan.default_dmx, 32768)
        self.assertEqual(pan.source_attribute, "Pan")
        self.assertEqual(pan.geometry, "Body")
        self.assertIsNone(m.channel("tilt").fine_offset)
        self.assertEqual(m.channel("dimmer").default, 1.0)
        self.assertEqual(m.channel("strobe").offset, 4)
        self.assertIsNone(m.channel("zoom"))          # virtual channel
        self.assertIsNone(m.channel("focus"))         # DMXBreak 2 is skipped
        self.assertEqual([c.offset for c in m.channels], sorted(c.offset for c in m.channels))

    def test_physical(self):
        ph = self.prof.physical
        self.assertEqual(ph["Weight"]["Value"], "12.5")
        self.assertEqual(ph["Dimensions"]["Height"], "0.5")
        self.assertEqual(ph["Beam"]["BeamAngle"], "25")

    def test_color_wheel_slots(self):
        s = self.prof.mode(0).channel("color_wheel").wheel_slots
        self.assertEqual([x.name for x in s], ["Open", "Red", "Blue"])
        self.assertEqual([(x.dmx_from, x.dmx_to) for x in s], [(0, 9), (10, 19), (20, 127)])
        self.assertEqual(s[0].color, "#ffffff")
        r, g, b = (int(s[1].color[i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(r, 200)
        self.assertLess(max(g, b), 60)
        self.assertAlmostEqual(s[1].start, 10 / 255)

    def test_gobo_slots_16bit_by_order(self):
        ch = self.prof.mode(0).channel("gobo_wheel")
        self.assertEqual(ch.offsets, [6, 7])
        s = ch.wheel_slots
        self.assertEqual([x.name for x in s], ["Open", "Star"])
        self.assertEqual((s[1].dmx_from, s[1].dmx_to, s[1].bits), (64 * 256, 65535, 16))
        self.assertEqual(s[1].image, "gobo_star")
        self.assertIsNone(s[1].color)

    def test_wheel_image(self):
        self.assertTrue(read_wheel_image(self.path, "gobo_star").startswith(b"\x89PNG"))
        self.assertIsNone(read_wheel_image(self.path, "missing"))
        self.assertIsNone(read_wheel_image(self.path, "../description"))
        self.assertIsNone(read_wheel_image(self.p("nope.gdtf"), "gobo_star"))

    def test_bytes_input(self):
        with open(self.path, "rb") as fh:
            prof = load_gdtf(fh.read())
        self.assertEqual(prof.mode_names, ["Standard", "Basic"])
        self.assertIsNone(prof.source)

    def test_xyy_to_hex(self):
        self.assertEqual(xyy_to_hex(None), "#ffffff")
        self.assertIsNone(xyy_to_hex("rubbish"))
        self.assertIsNone(xyy_to_hex("0.3,0,10"))


class GdtfBrokenInput(Base):
    def test_broken_inputs(self):
        with open(self.p("notzip.gdtf"), "wb") as fh:
            fh.write(b"not a zip")
        cases = [self.p("notzip.gdtf"), self.p("missing.gdtf"),
                 make_zip(self.p("noxml.gdtf"), {"other.txt": "x"}),
                 make_zip(self.p("badxml.gdtf"), {"description.xml": "<GDTF><FixtureType"}),
                 make_zip(self.p("nomodes.gdtf"), {"description.xml": "<GDTF><FixtureType Name='x'/></GDTF>"}),
                 make_zip(self.p("noft.gdtf"), {"description.xml": "<GDTF/>"})]
        for c in cases:
            with self.assertRaises(FixtureFormatError, msg=c):
                load_gdtf(c)
        good = make_zip(self.p("good.gdtf"), {"description.xml": GDTF_XML})
        with open(good, "rb") as fh:
            raw = fh.read()
        with self.assertRaises(FixtureFormatError):
            load_gdtf(raw[: len(raw) // 2])

    def test_path_traversal_and_entities(self):
        for name in ("../evil.txt", "/etc/passwd"):
            p = make_zip(self.p("evil.gdtf"), {"description.xml": GDTF_XML, name: "x"})
            with self.assertRaises(FixtureFormatError):
                load_gdtf(p)
        xxe = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
               "<GDTF><FixtureType Name='&b;'/></GDTF>")
        with self.assertRaises(FixtureFormatError):
            load_gdtf(make_zip(self.p("xxe.gdtf"), {"description.xml": xxe}))

    def test_zip_bomb(self):
        p = make_zip(self.p("bomb.gdtf"), {"description.xml": GDTF_XML, "big.bin": b"\0" * (30 * 1024 * 1024)})
        with self.assertRaises(FixtureFormatError):
            load_gdtf(p)


if __name__ == "__main__":
    unittest.main()
