"""Open Fixture Library parser tests. All fixture data below is handwritten and synthetic."""
import json
import os
import tempfile
import unittest

from fixtureformats import FixtureFormatError, load_ofl

PAR = {
    "name": "Test Par",
    "shortName": "TPar",
    "categories": ["Color Changer", "Moving Head"],
    "physical": {"dimensions": [200, 300, 150], "weight": 4.5, "bulb": {"type": "LED"}},
    "wheels": {
        "Color Wheel": {"slots": [{"type": "Open"}, {"type": "Color", "name": "Deep Red", "colors": ["#cc0000"]},
                                  {"type": "Color", "name": "No Hex"}]},
        "Gobo Wheel": {"slots": [{"type": "Open"},
                                 {"type": "Gobo", "name": "Triangles", "resource": "gobos/acme/tri"},
                                 {"type": "Gobo", "resource": {"key": "gobos/acme/lines"}}]},
    },
    "availableChannels": {
        "Dimmer": {"capability": {"type": "Intensity"}, "defaultValue": "100%"},
        "Red": {"capability": {"type": "ColorIntensity", "color": "Red"}},
        "Amber": {"capability": {"type": "ColorIntensity", "color": "Amber"}},
        "Pan": {"capability": {"type": "Pan", "angleStart": "0deg", "angleEnd": "540deg"},
                "fineChannelAliases": ["Pan fine"], "defaultValue": 32768},
        "Strobe": {"capabilities": [{"dmxRange": [0, 9], "type": "ShutterStrobe", "shutterEffect": "Open"}]},
        "Color Wheel": {"capabilities": [
            {"dmxRange": [0, 9], "type": "WheelSlot", "slotNumber": 1},
            {"dmxRange": [10, 19], "type": "WheelSlot", "slotNumber": 2},
            {"dmxRange": [20, 29], "type": "WheelSlot", "slotNumber": 3},
            {"dmxRange": [30, 39], "type": "WheelSlot", "slotNumber": 1.5},
            {"dmxRange": [40, 49], "type": "ColorPreset", "colors": ["#00FF00"], "comment": "Green"}]},
        "Gobo Wheel": {"capabilities": [
            {"dmxRange": [0, 9], "type": "WheelSlot", "slotNumber": 1},
            {"dmxRange": [10, 19], "type": "WheelSlot", "slotNumber": 2},
            {"dmxRange": [20, 29], "type": "WheelSlot", "slotNumber": 2.5},
            {"dmxRange": [30, 39], "type": "WheelSlot", "slotNumber": 3},
            {"dmxRange": [40, 255], "type": "WheelShake", "slotNumber": 2}]},
    },
    "modes": [
        {"name": "3-channel", "shortName": "3ch", "channels": ["Red", "Amber", "Dimmer"]},
        {"name": "Full", "physical": {"power": 120},
         "channels": ["Dimmer", "Pan", None, "Pan fine", "Strobe", "Color Wheel", "Gobo Wheel"]},
        {"name": "Broken", "channels": ["Does not exist"]},
        {"name": "Fine without coarse", "channels": ["Pan fine", "Dimmer"]},
    ],
}

MATRIX = {
    "name": "Pixelbar",
    "matrix": {"pixelKeys": [[["1", "2"]]]},
    "availableChannels": {"Dimmer": {"capability": {"type": "Intensity"}}},
    "templateChannels": {
        "Red $pixelKey": {"capability": {"type": "ColorIntensity", "color": "Red"}},
        "Green $pixelKey": {"capability": {"type": "ColorIntensity", "color": "Green"}},
    },
    "modes": [{"name": "Pixels", "channels": [
        "Dimmer", {"insert": "matrixChannels", "repeatFor": ["1", "2"], "channelOrder": "perPixel",
                   "templateChannels": ["Red $pixelKey", "Green $pixelKey"]}]}],
}


class OflTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.dir = td.name

    def write(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        return p

    def test_metadata_and_modes(self):
        path = self.write("par.json", json.dumps(PAR))
        p = load_ofl(path)
        self.assertEqual((p.name, p.short_name, p.format, p.source), ("Test Par", "TPar", "ofl", path))
        self.assertEqual(p.categories, ["Color Changer", "Moving Head"])
        self.assertEqual(p.mode_names, ["3-channel", "Full", "Fine without coarse"])
        self.assertTrue(any("'Broken' skipped" in w for w in p.warnings), p.warnings)
        self.assertTrue(any("without coarse" in w for w in p.warnings), p.warnings)
        self.assertEqual(p.physical["weight"], 4.5)
        self.assertEqual(p.mode("Full").physical["power"], 120)
        self.assertEqual(p.mode("Full").physical["weight"], 4.5)
        self.assertNotIn("power", p.mode(0).physical)

    def test_channels(self):
        p = load_ofl(PAR)
        self.assertIsNone(p.source)
        self.assertEqual([c.attribute for c in p.mode(0).channels], ["red", "amber", "dimmer"])
        full = p.mode("Full")
        pan = full.channel("pan")
        self.assertEqual((pan.offset, pan.fine_offset, pan.name), (1, 3, "Pan"))
        self.assertAlmostEqual(pan.default, 32768 / 65535, places=6)
        self.assertEqual(full.channel("dimmer").default, 1.0)
        self.assertEqual(full.channel("strobe").source_attribute, "ShutterStrobe")
        self.assertEqual(full.footprint, 7)

    def test_color_wheel(self):
        s = load_ofl(PAR).mode("Full").channel("color_wheel").wheel_slots
        self.assertEqual([(x.name, x.dmx_from, x.dmx_to, x.color) for x in s],
                         [("Open", 0, 9, "#ffffff"), ("Deep Red", 10, 19, "#cc0000"), ("Green", 40, 49, "#00ff00")])

    def test_gobo_wheel(self):
        s = load_ofl(PAR).mode("Full").channel("gobo_wheel").wheel_slots
        self.assertEqual([x.name for x in s], ["Open", "Triangles", "lines"])
        self.assertEqual(s[1].image, "gobos/acme/tri")
        self.assertEqual(s[2].image, "gobos/acme/lines")
        self.assertEqual((s[2].dmx_from, s[2].dmx_to), (30, 39))

    def test_matrix(self):
        ch = load_ofl(MATRIX).mode("Pixels").channels
        self.assertEqual([(c.attribute, c.offset, c.pixel_key) for c in ch],
                         [("dimmer", 0, None), ("red", 1, "1"), ("green", 2, "1"), ("red", 3, "2"),
                          ("green", 4, "2")])
        self.assertEqual(ch[3].name, "Red 2")

    def test_missing_fields_and_garbage(self):
        p = load_ofl({"availableChannels": {"A": {}}, "modes": [{"channels": ["A"]}]})
        self.assertEqual(p.mode_names, ["Mode 1"])
        self.assertTrue(any("name" in w for w in p.warnings))
        for n, d in {"a.json": "{broken", "b.json": "[]", "c.json": "{}", "d.json": b"\xff\xfe\x00",
                     "e.json": json.dumps({"name": "x", "modes": []}),
                     "f.json": json.dumps({"name": "x", "availableChannels": {}, "modes": [3, "x"]}),
                     "g.json": "[" * 100000}.items():
            with self.assertRaises(FixtureFormatError, msg=n):
                load_ofl(self.write(n, d))
        with self.assertRaises(FixtureFormatError):
            load_ofl(os.path.join(self.dir, "nope.json"))


if __name__ == "__main__":
    unittest.main()
