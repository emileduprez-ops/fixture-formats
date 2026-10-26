"""Auto-detection, the model helpers and the hardened XML parser."""
import json
import os
import tempfile
import unittest
import zipfile

import fixtureformats
from fixtureformats import Channel, FixtureFormatError, FixtureProfile, Mode, WheelSlot, load
from fixtureformats._common import safe_xml

GDTF = """<GDTF DataVersion="1.1"><FixtureType Name="Mini" Manufacturer="Acme"><DMXModes>
<DMXMode Name="1ch"><DMXChannels><DMXChannel DMXBreak="1" Offset="1" Geometry="Body">
<LogicalChannel Attribute="Dimmer"><ChannelFunction Attribute="Dimmer" DMXFrom="0/1"/></LogicalChannel>
</DMXChannel></DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>"""

QXF = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE FixtureDefinition>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Manufacturer>Acme</Manufacturer><Model>Mini</Model><Type>Dimmer</Type>
 <Channel Name="Dimmer"><Group Byte="0">Intensity</Group></Channel>
 <Mode Name="1ch"><Channel Number="0">Dimmer</Channel></Mode>
</FixtureDefinition>"""

OFL = {"name": "Mini", "availableChannels": {"Dimmer": {"capability": {"type": "Intensity"}}},
       "modes": [{"name": "1ch", "channels": ["Dimmer"]}]}


class LoadTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = td.name

    def p(self, name):
        return os.path.join(self.dir, name)

    def test_autodetect(self):
        with zipfile.ZipFile(self.p("mini.GDTF"), "w") as z:
            z.writestr("description.xml", GDTF)
        with open(self.p("mini.qxf"), "w", encoding="utf-8") as fh:
            fh.write(QXF)
        with open(self.p("mini.json"), "w", encoding="utf-8") as fh:
            json.dump(OFL, fh)
        for name, fmt in (("mini.GDTF", "gdtf"), ("mini.qxf", "qxf"), ("mini.json", "ofl")):
            prof = load(self.p(name))
            self.assertIsInstance(prof, FixtureProfile)
            self.assertEqual((prof.format, prof.name, prof.mode_names), (fmt, "Mini", ["1ch"]))
            self.assertEqual(prof.mode().channel("dimmer").offset, 0)

    def test_unknown_extension(self):
        with self.assertRaises(FixtureFormatError):
            load(self.p("x.txt"))
        with self.assertRaises(FixtureFormatError):
            load(42)

    def test_error_is_value_error(self):
        self.assertTrue(issubclass(FixtureFormatError, ValueError))
        self.assertTrue(fixtureformats.__version__)


class ModelTests(unittest.TestCase):
    def test_mode_lookup_and_footprint(self):
        ch = Channel(name="Pan", attribute="pan", offsets=[2, 5], default=1.0)
        self.assertEqual((ch.offset, ch.fine_offset, ch.bits, ch.default_dmx), (2, 5, 16, 65535))
        prof = FixtureProfile(name="X", modes=[Mode("A", [ch]), Mode("b", [])])
        self.assertEqual(prof.mode().footprint, 6)
        self.assertEqual(prof.mode("B").name, "b")
        self.assertEqual(prof.mode(1).footprint, 0)
        for bad in ("zz", 5):
            with self.assertRaises(KeyError):
                prof.mode(bad)
        s = WheelSlot("Open", 0, 127)
        self.assertAlmostEqual(s.end, 127 / 255)


class SafeXmlTests(unittest.TestCase):
    def test_doctype_in_comment_and_cdata_is_fine(self):
        root = safe_xml(b"<a><!-- <!DOCTYPE x> --><b><![CDATA[<!ENTITY y>]]></b></a>")
        self.assertEqual(root.tag, "a")

    def test_rejects(self):
        for data in (b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>', b"\xff\xfe<\x00a\x00/\x00>\x00",
                     b'<?xml version="1.0" encoding="UTF-7"?><a/>', b"<a>" * 150 + b"</a>" * 150, b"", b"<a>"):
            with self.assertRaises(FixtureFormatError, msg=data[:30]):
                safe_xml(data)


if __name__ == "__main__":
    unittest.main()
