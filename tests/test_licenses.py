"""The licensing rule, enforced.

What this package asks for directly must be MIT, Apache-2.0 or BSD. Whatever
those packages bring with them must not be copyleft. See docs/LICENSING.md.
"""

import re
import tomllib
import unittest
from importlib import metadata
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

#: What a direct dependency may declare.
ALLOWED = re.compile(r"\b(MIT|Apache|BSD)\b", re.IGNORECASE)

#: What nothing the engine installs may declare.
COPYLEFT = re.compile(
    r"GPL|GNU|\bMPL\b|Mozilla|\bEPL\b|Eclipse|CDDL|EUPL|\bOSL\b|SSPL|"
    r"CC-BY-(NC|SA)|ShareAlike|Non-?Commercial",
    re.IGNORECASE,
)

NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def name_of(requirement: str) -> str:
    return re.sub(r"[-_.]+", "-", NAME.match(requirement).group(1)).lower()


def declared(distribution: metadata.Distribution) -> str:
    """The license a package declares for itself."""
    found = distribution.metadata
    if expression := found.get("License-Expression"):
        return expression
    classifiers = [line.split("::")[-1].strip() for line in found.get_all("Classifier") or ()
                   if line.startswith("License ::")]
    # Older packages put the whole license text in this field; its first line names it.
    text = (found.get("License") or "").strip().splitlines()[:1]
    return "; ".join([*classifiers, *text])


def direct() -> dict[str, list[str]]:
    """Every requirement in pyproject.toml, by the group that asks for it."""
    project = tomllib.loads(PROJECT.read_text())["project"]
    groups = {"core": project.get("dependencies", []), **project.get("optional-dependencies", {})}
    return {group: [name_of(r) for r in requirements] for group, requirements in groups.items()}


def installed(name: str) -> metadata.Distribution | None:
    try:
        return metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return None


def closure(names: list[str]) -> dict[str, metadata.Distribution]:
    """The installed packages `names` reach, themselves included."""
    reached: dict[str, metadata.Distribution] = {}
    queue = list(names)
    while queue:
        name = queue.pop()
        if name in reached or name == "constructive-media":
            continue
        distribution = installed(name)
        if distribution is None:
            continue
        reached[name] = distribution
        queue += [name_of(r) for r in distribution.requires or () if "extra ==" not in r]
    return reached


class LicenseTest(unittest.TestCase):
    def setUp(self):
        self.direct = sorted({name for names in direct().values() for name in names} - {"constructive-media"})

    def test_the_project_itself_is_mit(self):
        project = tomllib.loads(PROJECT.read_text())["project"]
        self.assertEqual(project["license"], "MIT")
        self.assertIn("MIT License", (PROJECT.parent / "LICENSE").read_text())

    def test_the_core_needs_only_the_database_driver(self):
        self.assertEqual(direct()["core"], ["pg8000"])

    def test_direct_dependencies_are_mit_apache_or_bsd(self):
        for name in self.direct:
            distribution = installed(name)
            if distribution is None:
                continue
            with self.subTest(package=name):
                license = declared(distribution)
                self.assertRegex(license, ALLOWED)
                self.assertNotRegex(license, COPYLEFT)

    def test_nothing_they_bring_with_them_is_copyleft(self):
        for name, distribution in sorted(closure(self.direct).items()):
            with self.subTest(package=name):
                license = declared(distribution)
                self.assertTrue(license, f"{name} declares no license; read it and decide")
                self.assertNotRegex(license, COPYLEFT)

    def test_the_markers_catch_what_they_should(self):
        for license in ("GPL-3.0-only", "LGPL-2.1-or-later", "AGPL-3.0", "MPL-2.0",
                        "GNU General Public License v3 (GPLv3)", "Mozilla Public License 2.0 (MPL 2.0)",
                        "CC-BY-NC-4.0", "EPL-2.0"):
            with self.subTest(license=license):
                self.assertRegex(license, COPYLEFT)
        for license in ("MIT", "Apache-2.0", "BSD-3-Clause", "BSD License", "PSF-2.0", "ISC",
                        "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", "MIT-CMU"):
            with self.subTest(license=license):
                self.assertNotRegex(license, COPYLEFT)


if __name__ == "__main__":
    unittest.main()
