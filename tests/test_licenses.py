"""The licensing rule, enforced.

What this package asks for directly must be MIT, Apache-2.0 or BSD. Whatever
those packages bring with them must not be copyleft. See docs/LICENSING.md.
"""

import ast
import re
import sys
import tomllib
import unittest
from email.message import Message
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
CODE = PROJECT.parent / "python" / "constructive_media"

#: Every package outside Python that the engine's code names in an import: the name it is imported by, the name
#: it is installed by, and what it is under. Asked for by the engine or installed by whoever wants the adapter
#: that uses it, each has been read and found to be MIT, Apache-2.0 or BSD. A package that is not here fails the
#: test; one that does not meet the rule does not go here, it goes in a plugin.
IMPORTED = {
    "PIL": ("pillow", "MIT-CMU"),
    "diffusers": ("diffusers", "Apache-2.0"),
    "huggingface_hub": ("huggingface-hub", "Apache-2.0"),
    "misaki": ("misaki", "Apache-2.0"),
    "mlx": ("mlx", "MIT"),
    "mlx_audio": ("mlx-audio", "MIT"),
    "mlx_speech": ("mlx-speech", "MIT"),
    "numpy": ("numpy", "BSD-3-Clause"),
    "onnxruntime": ("onnxruntime", "MIT"),
    "pg8000": ("pg8000", "BSD-3-Clause"),
    "spacy": ("spacy", "MIT"),
    "torch": ("torch", "BSD-3-Clause"),
    "torchvision": ("torchvision", "BSD-3-Clause"),
    "transformers": ("transformers", "Apache-2.0"),
}

#: Ways of importing a module by a name written in the code.
BY_NAME = ("import_module", "find_spec", "__import__")

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
    if classifiers or text:
        return "; ".join([*classifiers, *text])
    # Some declare nothing and only ship the license itself; its opening names it.
    return "; ".join(opening(distribution, name) for name in found.get_all("License-File") or ())


def opening(distribution: metadata.Distribution, name: str) -> str:
    """The first lines of a license file a package ships."""
    text = distribution.read_text(f"licenses/{name}") or distribution.read_text(name) or ""
    return " ".join([line.strip() for line in text.splitlines() if line.strip()][:3])


def direct() -> dict[str, list[str]]:
    """Every requirement in pyproject.toml, by the group that asks for it."""
    project = tomllib.loads(PROJECT.read_text())["project"]
    groups = {"core": project.get("dependencies", []), **project.get("optional-dependencies", {})}
    return {group: [name_of(r) for r in requirements] for group, requirements in groups.items()}


def imported() -> dict[str, set[str]]:
    """Every module outside Python and the engine that the engine's code imports, and the files that do."""
    found: dict[str, set[str]] = {}
    for path in sorted(CODE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module and not node.level else []
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) in BY_NAME:
                names = [first.value for first in node.args[:1]
                         if isinstance(first, ast.Constant) and isinstance(first.value, str)]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top and top not in sys.stdlib_module_names and top != CODE.name:
                    found.setdefault(top, set()).add(str(path.relative_to(CODE)))
    return found


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

    def test_every_package_the_code_imports_has_been_read_and_meets_the_rule(self):
        found = imported()
        for module, files in sorted(found.items()):
            with self.subTest(module=module):
                self.assertIn(module, IMPORTED, f"{sorted(files)} import {module}: read its license. MIT, Apache-2.0 "
                              "or BSD goes in IMPORTED and docs/LICENSING.md; anything else goes in a plugin")
        self.assertEqual(sorted(IMPORTED), sorted(found), "IMPORTED lists a package nothing imports any more")
        for module, (package, license) in sorted(IMPORTED.items()):
            with self.subTest(package=package):
                self.assertRegex(license, ALLOWED)
                self.assertNotRegex(license, COPYLEFT)
                distribution = installed(package)
                if distribution is not None:
                    self.assertRegex(declared(distribution), ALLOWED)
                    self.assertNotRegex(declared(distribution), COPYLEFT)

    def test_a_package_imported_by_a_name_in_a_string_is_seen(self):
        self.assertIn("speech/kokoro.py", imported()["misaki"])
        self.assertIn("speech/mlx.py", imported()["mlx_audio"])
        self.assertNotIn("num2words", imported())

    def test_a_license_shipped_only_as_a_file_is_read(self):
        def package(text):
            found = Message()
            found["License-File"] = "LICENSE"
            return SimpleNamespace(metadata=found, read_text={"licenses/LICENSE": text}.get)

        permissive = declared(package("\nPermission is hereby granted, free of charge, to any person\n"))
        self.assertIn("Permission is hereby granted", permissive)
        self.assertNotRegex(permissive, COPYLEFT)
        self.assertRegex(declared(package("GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n")), COPYLEFT)
        self.assertEqual(declared(SimpleNamespace(metadata=Message(), read_text={}.get)), "")

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
