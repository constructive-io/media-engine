import hashlib
import tempfile
import unittest
from pathlib import Path

from constructive_media.models import ModelError, Remote, fetch


class FetchTest(unittest.TestCase):
    """Downloads, from files on this machine standing in for a server."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.published = self.root / "published.bin"
        self.published.write_bytes(b"weights" * 1000)
        self.remote = Remote(self.published.as_uri(), hashlib.sha256(self.published.read_bytes()).hexdigest())
        self.target = self.root / "models" / "detection" / "tiny" / "model.bin"

    def test_a_file_is_downloaded_into_a_folder_made_for_it(self):
        self.assertEqual(fetch(self.remote, self.target), self.target)
        self.assertEqual(self.target.read_bytes(), self.published.read_bytes())
        self.assertEqual([p.name for p in self.target.parent.iterdir()], ["model.bin"])

    def test_a_file_that_is_already_there_is_not_fetched_again(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"mine")
        gone = Remote((self.root / "gone.bin").as_uri(), self.remote.sha256)
        self.assertEqual(fetch(gone, self.target).read_bytes(), b"mine")

    def test_bytes_that_do_not_match_the_hash_are_refused_and_not_kept(self):
        wrong = Remote(self.remote.url, "0" * 64)
        with self.assertRaises(ModelError) as raised:
            fetch(wrong, self.target)
        self.assertIn("not the file that was expected", str(raised.exception))
        self.assertEqual(list(self.target.parent.iterdir()), [])

    def test_a_download_that_fails_says_where_to_put_the_file(self):
        gone = Remote((self.root / "gone.bin").as_uri(), self.remote.sha256)
        with self.assertRaises(ModelError) as raised:
            fetch(gone, self.target)
        self.assertIn(str(self.target), str(raised.exception))
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
