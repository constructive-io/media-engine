import os
import unittest
from contextlib import closing
from unittest import mock

from constructive_media import adapters, db
from constructive_media.settings import Database

from .support import TEST_DATABASE_VARIABLE, DatabaseTest


class ConnectionChoiceTest(unittest.TestCase):
    def test_use_scopes_a_connection_and_restores_the_one_before(self):
        outer, inner = object(), object()
        with db.use(outer):
            self.assertIs(db.connection(), outer)
            with db.use(inner):
                self.assertIs(db.connection(), inner)
            self.assertIs(db.connection(), outer)

    def test_the_driver_is_an_adapter(self):
        described = adapters.registry().describe("database")
        self.assertEqual((described.name, described.license), ("pg8000", "BSD-3-Clause"))

    def test_an_unreachable_database_is_a_database_error(self):
        try:
            driver = db.driver()
            nowhere = Database(name="nowhere", host="127.0.0.1", port=1)
            with self.assertRaises(db.DatabaseError) as raised:
                driver.connect(nowhere)
        except ModuleNotFoundError:
            self.skipTest("pg8000 is not installed")
        self.assertIn("nowhere", str(raised.exception))

    def test_a_password_that_was_asked_for_and_not_given_is_said_plainly(self):
        try:
            from pg8000 import dbapi
        except ModuleNotFoundError:
            self.skipTest("pg8000 is not installed")
        tripped = AttributeError("'NoneType' object has no attribute 'decode'")
        with mock.patch.object(dbapi, "connect", side_effect=tripped):
            with self.assertRaises(db.DatabaseError) as raised:
                db.driver().connect(Database(name="made-up"))
        self.assertIn("asked for a password", str(raised.exception))
        self.assertIn("PGPASSWORD", str(raised.exception))
        self.assertNotIn("NoneType", str(raised.exception))


class DriverTest(DatabaseTest):
    def test_rows_are_tuples_and_parameters_are_percent_s(self):
        cur = db.cursor()
        cur.execute("SELECT %s::int + %s::int, %s::text", (2, 3, "50% done"))
        self.assertEqual(cur.fetchone(), (5, "50% done"))
        self.assertIsNone(cur.fetchone())
        self.assertEqual(cur.execute("SELECT 1 WHERE false").fetchall(), [])

    def test_a_cursor_can_be_looped_over(self):
        rows = list(db.cursor().execute("SELECT n FROM generate_series(1, 3) AS n ORDER BY n"))
        self.assertEqual(rows, [(1,), (2,), (3,)])

    def test_lists_are_arrays_and_jsonb_is_one_document(self):
        cur = db.cursor()
        cur.execute("SELECT %s::float8[], %s::jsonb, %s::jsonb",
                    ([0.5, 1.5], db.jsonb({"a": [1, 2]}), db.jsonb([1, "two"])))
        self.assertEqual(cur.fetchone(), ([0.5, 1.5], {"a": [1, 2]}, [1, "two"]))

    def test_a_failed_statement_raises_with_its_sqlstate(self):
        with self.assertRaises(db.DatabaseError) as raised:
            db.cursor().execute("SELECT * FROM media.no_such_table")
        self.assertEqual(raised.exception.sqlstate, "42P01")
        self.assertIn("no_such_table", str(raised.exception))

    def test_a_constraint_names_itself(self):
        with self.assertRaises(db.DatabaseError) as raised:
            db.cursor().execute(
                "INSERT INTO media.assets (medium, path, content_hash) VALUES (%s,%s,%s)",
                ("hologram", "/made-up/file", "made-up-hash"),
            )
        self.assertEqual(raised.exception.sqlstate, "23514")

    def test_executemany_writes_every_row(self):
        cur = db.cursor()
        cur.executemany(
            "INSERT INTO media.assets (medium, path, content_hash) VALUES (%s,%s,%s)",
            [("audio", f"/made-up/{n}.wav", f"made-up-{n}") for n in range(3)],
        )
        count = self.query("SELECT count(*) FROM media.assets WHERE path LIKE '/made-up/%%'")
        self.assertEqual(count, [(3,)])

    def test_numpy_numbers_are_sent_as_numbers(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is not installed")
        # The adapters are registered when a connection opens, so open one
        # now that numpy is certainly imported.
        with closing(db.driver().connect(self.database)) as connection:
            cur = connection.cursor()
            cur.execute("SELECT %s::float8, %s::int, %s::boolean, %s::float8",
                        (np.float32(0.5), np.int64(7), np.bool_(True), np.float64(1e-9)))
            self.assertEqual(cur.fetchone(), (0.5, 7, True, 1e-9))

    def test_nothing_a_test_writes_is_seen_from_outside(self):
        db.cursor().execute(
            "INSERT INTO media.assets (medium, path, content_hash) VALUES (%s,%s,%s)",
            ("audio", "/made-up/unseen.wav", "made-up-unseen"),
        )
        with closing(db.driver().connect(self.database)) as other:
            seen = other.cursor().execute(
                "SELECT count(*) FROM media.assets WHERE content_hash = %s", ("made-up-unseen",)
            ).fetchone()
        self.assertEqual(seen, (0,))


class UnitInsideATestTest(DatabaseTest):
    def held(self):
        return self.query("SELECT name FROM media.takes WHERE name LIKE 'made-up-unit-%%' ORDER BY name")

    def test_a_unit_is_kept_whole_or_not_at_all(self):
        cur = db.cursor()
        cur.execute("INSERT INTO media.takes (name) VALUES ('made-up-unit-before')")
        with db.transaction():
            cur.execute("INSERT INTO media.takes (name) VALUES ('made-up-unit-kept')")
        with db.transaction(keep=False) as connection:
            cur.execute("INSERT INTO media.takes (name) VALUES ('made-up-unit-shown')")
            self.assertIs(connection, self.connection)
            self.assertEqual(len(self.held()), 3)
        with self.assertRaises(db.DatabaseError):
            with db.transaction():
                cur.execute("INSERT INTO media.takes (name) VALUES ('made-up-unit-lost')")
                cur.execute("INSERT INTO media.takes (name) VALUES ('made-up-unit-kept')")
        # What came before the unit is untouched, and the connection still works.
        self.assertEqual(self.held(), [("made-up-unit-before",), ("made-up-unit-kept",)])


class UnitOfItsOwnTest(unittest.TestCase):
    """Outside `use`, a unit opens its own connection and really commits."""

    NAME = "made-up-unit-of-its-own"

    def setUp(self):
        url = os.environ.get(TEST_DATABASE_VARIABLE)
        if not url:
            self.skipTest(f"{TEST_DATABASE_VARIABLE} is not set")
        self.database = Database.from_url(url)
        environment = mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_DATABASE_URL": url})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.outside, "DELETE FROM media.takes WHERE name = %s RETURNING 1")

    def outside(self, sql):
        with closing(db.driver().connect(self.database)) as other:
            return other.cursor().execute(sql, (self.NAME,)).fetchall()

    def test_it_is_stored_when_kept_and_not_when_shown_or_failed(self):
        insert, count = "INSERT INTO media.takes (name) VALUES (%s)", "SELECT count(*) FROM media.takes WHERE name = %s"
        with db.transaction(keep=False):
            db.cursor().execute(insert, (self.NAME,))
            self.assertEqual(db.cursor().execute(count, (self.NAME,)).fetchall(), [(1,)])
            self.assertEqual(self.outside(count), [(0,)])
        self.assertEqual(self.outside(count), [(0,)])
        with self.assertRaises(ZeroDivisionError):
            with db.transaction():
                db.cursor().execute(insert, (self.NAME,))
                1 / 0
        self.assertEqual(self.outside(count), [(0,)])
        with db.transaction():
            db.cursor().execute(insert, (self.NAME,))
        self.assertEqual(self.outside(count), [(1,)])


if __name__ == "__main__":
    unittest.main()
