"""QLC+ .qxf parser tests. All definitions below are handwritten and synthetic."""
import os
import tempfile
import unittest

from fixtureformats import FixtureFormatError, load_qxf

NS = 'xmlns="http://www.qlcplus.org/FixtureDefinition"'
HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE FixtureDefinition>\n'


def fx(body: str, man="Testco", model="Spot X", typ="Moving Head") -> str:
    return (HEAD + f'<FixtureDefinition {NS}>\n <Creator><Name>test</Name><Version>1</Version></Creator>\n'
            f' <Manufacturer>{man}</Manufacturer>\n <Model>{model}</Model>\n <Type>{typ}</Type>\n{body}'
            '</FixtureDefinition>\n')


SPOT = fx('''
 <Channel Name="Pan"><Group Byte="0">Pan</Group></Channel>
 <Channel Name="Pan Fine"><Group Byte="1">Pan</Group></Channel>
 <Channel Name="Tilt" Preset="PositionTilt"/>
 <Channel Name="Tilt Fine" Preset="PositionTiltFine"/>
 <Channel Name="Dimmer" Default="255"><Group Byte="0">Intensity</Group></Channel>
 <Channel Name="Red"><Group Byte="0">Intensity</Group><Colour>Red</Colour></Channel>
 <Channel Name="Cyan" Preset="IntensityCyan"/>
 <Channel Name="Colors"><Group Byte="0">Colour</Group>
   <Capability Min="0" Max="9" Color="#ffffff">Open</Capability>
   <Capability Min="10" Max="19" Res1="#ff0000" Preset="ColorMacro">Red</Capability>
   <Capability Min="20" Max="255">Rotate</Capability></Channel>
 <Channel Name="Gobos"><Group Byte="0">Gobo</Group>
   <Capability Min="0" Max="9">Open</Capability>
   <Capability Min="10" Max="19" Res1="Others/gobo00034.svg">Dots</Capability>
   <Capability Min="20" Max="29" Res="Others/gobo00050.svg"></Capability></Channel>
 <Channel Name="Gobo Rotation"><Group Byte="0">Gobo</Group></Channel>
 <Channel Name="Shutter"><Group Byte="0">Shutter</Group></Channel>
 <Channel Name="Zoom" Preset="BeamZoomBigSmall"/>
 <Channel Name="Iris"><Group Byte="0">Shutter</Group></Channel>
 <Channel Name="Mix" Preset="ColorRGBMixer"><Group Byte="0">Colour</Group></Channel>
 <Channel Name="Reset"><Group Byte="0">Maintenance</Group></Channel>
 <Mode Name="Full">
  <Physical><Bulb Type="LED" Lumens="9000"/><Dimensions Weight="12" Width="300"/>
   <Focus Type="Head" PanMax="540" TiltMax="270"/></Physical>
  <Channel Number="0">Pan</Channel><Channel Number="1">Pan Fine</Channel>
  <Channel Number="2">Tilt</Channel><Channel Number="3">Tilt Fine</Channel>
  <Channel Number="4">Dimmer</Channel><Channel Number="5">Red</Channel><Channel Number="6">Cyan</Channel>
  <Channel Number="7">Colors</Channel><Channel Number="8">Gobos</Channel><Channel Number="9">Gobo Rotation</Channel>
  <Channel Number="10">Shutter</Channel><Channel Number="11">Zoom</Channel><Channel Number="12">Iris</Channel>
  <Channel Number="13">Mix</Channel><Channel Number="14">Reset</Channel>
 </Mode>
 <Mode Name="Basic">
  <Channel Number="0">Pan</Channel><Channel Number="1">Tilt</Channel><Channel Number="2">Dimmer</Channel>
 </Mode>''')

BAR = fx('''
 <Channel Name="Master" Preset="IntensityMasterDimmer"/>
 <Channel Name="Red 1"><Group Byte="0">Intensity</Group><Colour>Red</Colour></Channel>
 <Channel Name="Red 2"><Group Byte="0">Intensity</Group><Colour>Red</Colour></Channel>
 <Mode Name="Pixels">
  <Channel Number="0">Master</Channel><Channel Number="1">Red 1</Channel><Channel Number="2">Red 2</Channel>
  <Head><Channel>1</Channel></Head><Head><Channel>2</Channel></Head>
 </Mode>''', model="Bar", typ="LED Bar (Pixels)")


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = td.name

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        return p


class QxfTests(Base):
    def test_metadata_modes_physical(self):
        p = load_qxf(self.write("spot.qxf", SPOT))
        self.assertEqual((p.name, p.manufacturer, p.format, p.categories), ("Spot X", "Testco", "qxf", ["Moving Head"]))
        self.assertEqual(p.mode_names, ["Full", "Basic"])
        self.assertEqual(p.mode("Full").physical["Focus"]["PanMax"], "540")
        self.assertEqual(p.mode("basic").footprint, 3)

    def test_attributes_and_16bit(self):
        m = load_qxf(self.write("spot.qxf", SPOT)).mode(0)
        by = {c.name: c for c in m.channels}
        self.assertEqual((by["Pan"].attribute, by["Pan"].offsets), ("pan", [0, 1]))
        self.assertEqual((by["Tilt"].attribute, by["Tilt"].offsets), ("tilt", [2, 3]))
        self.assertNotIn("Pan Fine", by)
        want = {"Dimmer": "dimmer", "Red": "red", "Cyan": "cyan", "Colors": "color_wheel", "Gobos": "gobo_wheel",
                "Gobo Rotation": "gobo_rotation", "Shutter": "strobe", "Zoom": "zoom", "Iris": "iris",
                "Mix": "mix", "Reset": "reset"}
        self.assertEqual({k: by[k].attribute for k in want}, want)
        self.assertEqual(by["Dimmer"].default, 1.0)
        self.assertEqual(by["Pan"].default, 0.0)
        self.assertEqual(by["Zoom"].source_attribute, "BeamZoomBigSmall")
        self.assertEqual(m.footprint, 15)

    def test_wheel_slots(self):
        m = load_qxf(self.write("spot.qxf", SPOT)).mode(0)
        cols = m.channel("color_wheel").wheel_slots
        self.assertEqual([(s.name, s.dmx_from, s.dmx_to, s.color) for s in cols],
                         [("Open", 0, 9, "#ffffff"), ("Red", 10, 19, "#ff0000")])
        gobos = m.channel("gobo_wheel").wheel_slots
        self.assertEqual([s.name for s in gobos], ["Open", "Dots", "gobo00050"])
        self.assertEqual(gobos[1].image, "Others/gobo00034.svg")
        self.assertIsNone(gobos[0].image)
        self.assertAlmostEqual(gobos[2].end, 29 / 255)

    def test_fine_via_actson_and_orphan(self):
        x = fx('''
 <Channel Name="Position A"><Group Byte="0">Pan</Group></Channel>
 <Channel Name="Position B"><Group Byte="0">Pan</Group></Channel>
 <Channel Name="Fine B"><Group Byte="1">Pan</Group></Channel>
 <Channel Name="Lone Fine"><Group Byte="1">Gobo</Group></Channel>
 <Mode Name="M"><Channel Number="0">Position A</Channel><Channel Number="1">Position B</Channel>
  <Channel Number="2" ActsOn="1">Fine B</Channel><Channel Number="3">Lone Fine</Channel></Mode>''')
        p = load_qxf(self.write("a.qxf", x))
        by = {c.offset: c for c in p.mode(0).channels}
        self.assertIsNone(by[0].fine_offset)
        self.assertEqual(by[1].fine_offset, 2)
        self.assertEqual(by[3].attribute, "lone_fine")
        self.assertTrue(any("without a matching coarse" in w for w in p.warnings), p.warnings)

    def test_16bit_default(self):
        x = fx('''
 <Channel Name="Pan" Default="32768"><Group Byte="0">Pan</Group></Channel>
 <Channel Name="Pan Fine"><Group Byte="1">Pan</Group></Channel>
 <Mode Name="M"><Channel Number="0">Pan</Channel><Channel Number="1">Pan Fine</Channel></Mode>''')
        pan = load_qxf(self.write("d.qxf", x)).mode(0).channel("pan")
        self.assertAlmostEqual(pan.default, 32768 / 65535)

    def test_heads_become_pixel_keys(self):
        m = load_qxf(self.write("bar.qxf", BAR)).mode(0)
        self.assertEqual([(c.attribute, c.offset, c.pixel_key) for c in m.channels],
                         [("dimmer", 0, None), ("red", 1, "1"), ("red", 2, "2")])

    def test_bad_references_warn(self):
        x = fx('''
 <Channel Name="Dim"><Group Byte="0">Intensity</Group>
   <Capability Min="0" Max="255"><Alias Mode="A" Channel="Dim" With="X"/>x</Capability></Channel>
 <Mode Name="M"><Channel Number="0">Dim</Channel><Channel Number="0">Dim</Channel>
  <Channel Number="1">Ghost</Channel><Channel Number="900">Dim</Channel></Mode>''')
        p = load_qxf(self.write("w.qxf", x))
        txt = "\n".join(p.warnings)
        for needle in ("Alias", "used twice", "Ghost", "900"):
            self.assertIn(needle, txt)
        self.assertEqual(p.mode(0).footprint, 1)

    def test_bytes_input(self):
        self.assertEqual(load_qxf(SPOT.encode("utf-8")).name, "Spot X")

    def test_broken_inputs(self):
        cases = {"empty.qxf": "", "junk.qxf": "<FixtureDefinition><Manufacturer>x", "text.qxf": "hello",
                 "root.qxf": "<Other/>", "nomode.qxf": fx(""), "emptymode.qxf": fx('<Mode Name="M"/>'),
                 "bin.qxf": b"\xff\xfe\x00\x01garbage"}
        for name, text in cases.items():
            with self.assertRaises(FixtureFormatError, msg=name):
                load_qxf(self.write(name, text))
        with self.assertRaises(FixtureFormatError):
            load_qxf(os.path.join(self.dir, "missing.qxf"))

    def test_xml_hardening(self):
        secret = self.write("secret.txt", "SECRET")
        xxe = (f'<?xml version="1.0"?>\n<!DOCTYPE FixtureDefinition [<!ENTITY e SYSTEM "file://{secret}">]>\n'
               f'<FixtureDefinition {NS}><Manufacturer>&e;</Manufacturer><Model>m</Model></FixtureDefinition>')
        sysdt = '<!DOCTYPE FixtureDefinition SYSTEM "http://example.invalid/x.dtd"><FixtureDefinition/>'
        deep = "<FixtureDefinition>" + "<a>" * 300 + "</a>" * 300 + "</FixtureDefinition>"
        for name, text in (("xxe", xxe), ("sysdt", sysdt), ("deep", deep)):
            with self.assertRaises(FixtureFormatError, msg=name) as cm:
                load_qxf(self.write(name + ".qxf", text))
            self.assertNotIn("SECRET", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
