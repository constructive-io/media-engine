import importlib
import sys
import tempfile
import textwrap
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace

from constructive_media import adapters
from constructive_media.adapters import Adapter, AdapterError, LicenseError, Registry
from constructive_media.settings import Settings


class Counter:
    """A factory that counts how many times it was asked to build."""

    def __init__(self):
        self.built = 0

    def __call__(self):
        self.built += 1
        return object()


def adapter(name="plain", slot="effects", license="MIT", factory=None, origin="builtin"):
    return Adapter(slot=slot, name=name, factory=factory or object, license=license, origin=origin)


def entry_point(name, loads, dist=None):
    def load():
        if isinstance(loads, Exception):
            raise loads
        return loads

    return SimpleNamespace(name=name, load=load, dist=SimpleNamespace(name=dist) if dist else None)


class RegisterTest(unittest.TestCase):
    def test_unknown_slot_is_refused(self):
        with self.assertRaises(AdapterError):
            Registry().register(adapter(slot="teleportation"))

    def test_builtin_must_be_mit_or_apache(self):
        registry = Registry()
        registry.register(adapter("a", license="MIT"))
        registry.register(adapter("b", license="Apache-2.0"))
        for license in ("GPL-3.0-only", "AGPL-3.0-only", "LGPL-3.0-only", "BSD-3-Clause", ""):
            with self.subTest(license=license), self.assertRaises(LicenseError):
                registry.register(adapter("c", license=license))

    def test_a_plugin_may_carry_any_license(self):
        registry = Registry()
        registry.register(adapter("copyleft", license="GPL-3.0-only", origin="example-plugin"))
        self.assertEqual(registry.describe("effects", "copyleft").license, "GPL-3.0-only")

    def test_a_name_is_registered_once_per_slot(self):
        registry = Registry()
        registry.register(adapter("plain"))
        with self.assertRaises(AdapterError):
            registry.register(adapter("plain"))
        registry.register(adapter("plain", slot="speech"))


class ChoiceTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.registry.register(adapter("plain"), default=True)
        self.registry.register(adapter("studio", license="GPL-3.0-only", origin="example-plugin"))

    def test_default_is_used_when_nothing_is_chosen(self):
        self.assertEqual(self.registry.describe("effects").name, "plain")

    def test_first_default_stays(self):
        self.registry.register(adapter("later"), default=True)
        self.assertEqual(self.registry.describe("effects").name, "plain")

    def test_settings_choose_over_the_default(self):
        settings = Settings.from_env({"CONSTRUCTIVE_MEDIA_ADAPTER_EFFECTS": "studio"})
        self.assertEqual(self.registry.describe("effects", settings=settings).name, "studio")

    def test_an_explicit_name_chooses_over_settings(self):
        settings = Settings.from_env({"CONSTRUCTIVE_MEDIA_ADAPTER_EFFECTS": "studio"})
        self.assertEqual(self.registry.describe("effects", "plain", settings=settings).name, "plain")

    def test_unknown_adapter_names_what_is_available(self):
        with self.assertRaises(AdapterError) as raised:
            self.registry.describe("effects", "missing")
        self.assertIn("plain", str(raised.exception))
        self.assertIn("studio", str(raised.exception))

    def test_empty_slot_says_so(self):
        with self.assertRaises(AdapterError) as raised:
            self.registry.describe("speech")
        self.assertIn("text to speech", str(raised.exception))

    def test_unknown_slot_is_an_error(self):
        with self.assertRaises(AdapterError):
            self.registry.get("teleportation")
        with self.assertRaises(AdapterError):
            self.registry.available("teleportation")

    def test_available_lists_one_slot_or_all(self):
        self.registry.register(adapter("voice", slot="speech"))
        self.assertEqual([a.name for a in self.registry.available("effects")], ["plain", "studio"])
        self.assertEqual(len(self.registry.available()), 3)


class BuildTest(unittest.TestCase):
    def test_nothing_is_built_until_it_is_used_and_then_only_once(self):
        counter = Counter()
        registry = Registry()
        registry.register(adapter("plain", factory=counter), default=True)
        registry.describe("effects")
        self.assertEqual(counter.built, 0)
        first = registry.get("effects")
        self.assertIs(registry.get("effects"), first)
        self.assertEqual(counter.built, 1)

    def test_lazy_imports_on_first_call(self):
        build = adapters.lazy("collections:OrderedDict")
        self.assertEqual(type(build()).__name__, "OrderedDict")
        with self.assertRaises(ValueError):
            adapters.lazy("no-attribute")


class DiscoverTest(unittest.TestCase):
    def test_a_plugin_may_offer_one_adapter_a_list_or_a_function(self):
        registry = Registry()
        found = registry.discover([
            entry_point("one", adapter("one", origin="x"), dist="plugin-one"),
            entry_point("many", [adapter("two"), adapter("three", slot="speech")], dist="plugin-many"),
            entry_point("made", lambda: [adapter("four")], dist="plugin-made"),
        ])
        self.assertEqual([a.name for a in found], ["one", "two", "three", "four"])
        self.assertEqual(registry.describe("effects", "one").origin, "plugin-one")
        self.assertEqual(registry.describe("speech", "three").origin, "plugin-many")

    def test_a_plugin_cannot_pass_itself_off_as_builtin(self):
        registry = Registry()
        registry.discover([entry_point("builtin", adapter("sneaky", license="GPL-3.0-only"))])
        self.assertEqual(registry.describe("effects", "sneaky").origin, "plugin")

    def test_a_broken_plugin_is_skipped_with_a_warning(self):
        registry = Registry()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            found = registry.discover([
                entry_point("broken", ImportError("no module named example"), dist="plugin-broken"),
                entry_point("wrong", ["not an adapter"], dist="plugin-wrong"),
                entry_point("good", adapter("good"), dist="plugin-good"),
            ])
        self.assertEqual([a.name for a in found], ["good"])
        self.assertEqual(len(caught), 2)
        self.assertIn("plugin-broken", str(caught[0].message))

    def test_a_plugin_cannot_replace_a_builtin(self):
        registry = Registry()
        registry.register(adapter("plain"), default=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            registry.discover([entry_point("clash", adapter("plain"), dist="plugin-clash")])
        self.assertEqual(registry.describe("effects").origin, "builtin")
        self.assertEqual(len(caught), 1)

    def test_an_installed_package_is_found_through_its_entry_point(self):
        with tempfile.TemporaryDirectory() as site:
            Path(site, "example_media_plugin.py").write_text(textwrap.dedent("""
                from constructive_media.adapters import Adapter

                class Loud:
                    name = "loud"

                def adapters():
                    return [Adapter(slot="effects", name="loud", factory=Loud, license="GPL-3.0-only")]
            """))
            info = Path(site, "example_media_plugin-1.0.dist-info")
            info.mkdir()
            info.joinpath("METADATA").write_text(
                "Metadata-Version: 2.1\nName: example-media-plugin\nVersion: 1.0\n"
            )
            info.joinpath("entry_points.txt").write_text(
                "[constructive_media.adapters]\nexample = example_media_plugin:adapters\n"
            )
            sys.path.insert(0, site)
            importlib.invalidate_caches()
            try:
                registry = Registry()
                registry.discover()
                found = registry.describe("effects", "loud")
                self.assertEqual(found.origin, "example-media-plugin")
                self.assertEqual(registry.get("effects", "loud").name, "loud")
            finally:
                sys.path.remove(site)
                sys.modules.pop("example_media_plugin", None)
                importlib.invalidate_caches()


class BuiltinsTest(unittest.TestCase):
    def test_every_builtin_is_permissive_and_fills_a_known_slot(self):
        shipped = list(adapters.builtins())
        self.assertTrue(shipped)
        for shipped_adapter in shipped:
            with self.subTest(adapter=shipped_adapter.name):
                self.assertIn(shipped_adapter.slot, adapters.SLOTS)
                self.assertIn(shipped_adapter.license, adapters.PERMISSIVE)
                self.assertEqual(shipped_adapter.origin, "builtin")

    def test_ffmpeg_is_the_default_for_effects(self):
        self.assertEqual(adapters.registry().describe("effects").name, "ffmpeg")
        self.assertEqual(adapters.get("effects", settings=Settings.from_env({})).name, "ffmpeg")


if __name__ == "__main__":
    unittest.main()
