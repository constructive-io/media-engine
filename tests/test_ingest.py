import tempfile
import unittest
from pathlib import Path

from constructive_media import ingest


class QuickHashTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return path

    def test_the_same_bytes_hash_the_same_wherever_they_are(self):
        first = self.write("a.bin", b"footage" * 100)
        second = self.write("b.bin", b"footage" * 100)
        self.assertEqual(ingest.quick_hash(first), ingest.quick_hash(second))
        self.assertEqual(len(ingest.quick_hash(first)), 64)

    def test_size_head_and_tail_all_count(self):
        base = ingest.quick_hash(self.write("base.bin", b"h" * 8 + b"m" * 8 + b"t" * 8), chunk=8)
        for name, data in (
            ("longer.bin", b"h" * 8 + b"m" * 9 + b"t" * 8),
            ("head.bin", b"H" * 8 + b"m" * 8 + b"t" * 8),
            ("tail.bin", b"h" * 8 + b"m" * 8 + b"T" * 8),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(ingest.quick_hash(self.write(name, data), chunk=8), base)

    def test_the_middle_of_a_large_file_is_not_read(self):
        one = self.write("one.bin", b"h" * 8 + b"m" * 8 + b"t" * 8)
        other = self.write("other.bin", b"h" * 8 + b"M" * 8 + b"t" * 8)
        self.assertEqual(ingest.quick_hash(one, chunk=8), ingest.quick_hash(other, chunk=8))


class CandidatesTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        for name in ("b.mov", "a.MP4", "notes.txt", "day-two/c.wav", "day-two/still.png",
                     "exports/cut.mp4", "day-two/Renders/old.mp4", "library/work/proxies/p.mp4"):
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()

    def names(self, **options):
        return [str(f.relative_to(self.root)) for f in ingest.candidates(self.root, **options)]

    def test_media_is_found_in_order_and_everything_else_is_left(self):
        self.assertEqual(self.names(), ["a.MP4", "b.mov", "day-two/c.wav", "day-two/still.png"])

    def test_the_work_folder_is_left_out_wherever_it_is(self):
        (self.root / "cache" / "proxies").mkdir(parents=True)
        (self.root / "cache" / "proxies" / "p.mp4").touch()
        self.assertIn("cache/proxies/p.mp4", self.names(skip_folders=frozenset()))
        self.assertNotIn("cache/proxies/p.mp4",
                         self.names(skip_folders=frozenset(), work_dir=self.root / "cache"))

    def test_a_single_file_is_itself(self):
        self.assertEqual(ingest.candidates(self.root / "b.mov"), [self.root / "b.mov"])


if __name__ == "__main__":
    unittest.main()
