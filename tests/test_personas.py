"""Personas as a command: written down on purpose, amended, and listed with their voices."""

import os
from unittest import mock

from constructive_media import personas
from constructive_media.speakers import SpeakerError

from . import test_cli
from .support import TEST_DATABASE_VARIABLE, DatabaseTest


class PersonasTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE]})
        environment.start()
        self.addCleanup(environment.stop)

    def stored(self, slug):
        return self.query("SELECT name, kind, bio FROM media.personas WHERE slug = %s", (slug,))

    def test_somebody_new_is_added_only_when_asked(self):
        with self.assertRaisesRegex(SpeakerError, "nobody is called 'Ada Quay' yet; add --new to add them"):
            personas.keep("Ada Quay", kind="character")
        self.assertEqual(self.stored("ada-quay"), [])
        ada, done = personas.keep("  Ada   Quay ", new=True)
        self.assertEqual((ada, done), (personas.Persona("ada-quay", "Ada Quay", "person"), "added"))
        self.assertEqual(self.stored("ada-quay"), [("Ada Quay", "person", None)])
        with self.assertRaisesRegex(SpeakerError, "'!!' has no letters or numbers to make a slug from"):
            personas.keep("!!", new=True)
        with self.assertRaisesRegex(SpeakerError, "a persona is a person or a character, not 'robot'"):
            personas.keep("Ada Quay", kind="robot")

    def test_what_is_not_given_is_left_as_it_was(self):
        personas.keep("Ada Quay", kind="character", bio="Keeps  the light.\n", new=True)
        self.assertEqual(self.stored("ada-quay"), [("Ada Quay", "character", "Keeps the light.")])
        # By name in any case, or by slug; asking for somebody new who is known already makes no second one.
        self.assertEqual(personas.keep("ada quay")[1], "unchanged")
        self.assertEqual(personas.keep("ada-quay", kind="character", new=True)[1], "unchanged")
        ada, done = personas.keep("ada-quay", kind="person")
        self.assertEqual((ada.kind, ada.bio, done), ("person", "Keeps the light.", "amended"))
        ada, done = personas.keep("Ada Quay", bio="Kept the light.")
        self.assertEqual((ada.kind, ada.bio, done), ("person", "Kept the light.", "amended"))
        ada, done = personas.keep("Ada Quay", bio=" ")
        self.assertEqual((ada.bio, done), (None, "amended"))
        self.assertEqual(self.query("SELECT count(*) FROM media.personas WHERE lower(name) = 'ada quay'"), [(1,)])

    def test_two_people_of_one_name_are_told_apart_by_slug(self):
        self.query("INSERT INTO media.personas (slug, name) VALUES ('ben-pier', 'Ben'), ('ben-mole', 'Ben') RETURNING 1")
        with self.assertRaisesRegex(SpeakerError, "2 people are called 'Ben'; use the slug of the one you mean"):
            personas.keep("Ben", kind="character")
        self.assertEqual(personas.keep("ben-mole", kind="character")[1], "amended")
        self.assertEqual(self.stored("ben-pier"), [("Ben", "person", None)])

    def voices(self):
        """Ada can speak two ways, Ben was found in two recordings, and Cy is only a name."""
        ((ada,),) = self.query("INSERT INTO media.personas (slug, name) VALUES ('ada-quay', 'Ada Quay') RETURNING id")
        ((ben,),) = self.query(
            "INSERT INTO media.personas (slug, name, kind) VALUES ('ben', 'ben', 'character') RETURNING id")
        self.query("INSERT INTO media.personas (slug, name) VALUES ('cy', 'Cy') RETURNING id")
        self.query("""INSERT INTO media.voices (persona_id, direction, engine, emotion, profile_ref)
                      VALUES (%s, 'synthesis', 'made-up', 'neutral', 'ada'), (%s, 'synthesis', 'made-up', 'warm', 'ada')
                      RETURNING id""", (ada, ada))
        for number, who in enumerate((ben, ben, ada, None)):
            ((asset,),) = self.query(
                """INSERT INTO media.assets (medium, path, content_hash, has_audio)
                   VALUES ('audio', %s, %s, true) RETURNING id""",
                (f"/made-up/meeting {number}.wav", f"made-up-meeting-{number}"))
            self.query("""INSERT INTO media_video.speaker_clusters (asset_id, label, persona_id, method)
                          VALUES (%s, 'voice1', %s, 'made-up') RETURNING id""", (asset, who))

    def test_everybody_is_listed_with_the_voices_they_have(self):
        self.voices()
        ours = [one for one in personas.listing() if one.slug in ("ada-quay", "ben", "cy")]
        self.assertEqual(ours, [
            personas.Persona("ada-quay", "Ada Quay", "person", None, 1, 2),
            personas.Persona("ben", "ben", "character", None, 2, 0),
            personas.Persona("cy", "Cy", "person", None, 0, 0),
        ])

    def test_the_commands_say_who_there_is(self):
        status, out, err = test_cli.run("persona", "Ada Quay")
        self.assertEqual((status, out, err), (1, "", "media: nobody is called 'Ada Quay' yet; add --new to add them\n"))
        self.assertEqual(
            test_cli.run("persona", "Ada Quay", "--new", "--kind", "character", "--bio", "Keeps the light."),
            (0, "added Ada Quay (ada-quay), a character: 0 found voices, 0 speaking voices\n  Keeps the light.\n", ""))
        self.assertEqual(
            test_cli.run("persona", "ada-quay", "--kind", "person"),
            (0, "amended Ada Quay (ada-quay), a person: 0 found voices, 0 speaking voices\n  Keeps the light.\n", ""))
        self.assertEqual(
            test_cli.run("persona", "ada-quay", "--bio", ""),
            (0, "amended Ada Quay (ada-quay), a person: 0 found voices, 0 speaking voices\n", ""))
        self.query("DELETE FROM media.personas WHERE slug = 'ada-quay' RETURNING 1")
        self.voices()
        self.assertEqual(test_cli.run("persona", "ben"),
                         (0, "ben (ben), a character: 2 found voices, 0 speaking voices\n", ""))
        self.assertEqual(test_cli.run("persona", "Ada Quay")[1],
                         "Ada Quay (ada-quay), a person: 1 found voice, 2 speaking voices\n")
        status, out, err = test_cli.run("personas")
        self.assertEqual((status, err), (0, ""))
        lines = [" ".join(line.split()) for line in out.splitlines()]
        self.assertEqual([line for line in lines if line.endswith((" ada-quay", " ben", " cy"))], [
            "Ada Quay person 1 found 2 speaking ada-quay",
            "ben character 2 found 0 speaking ben",
            "Cy person 0 found 0 speaking cy",
        ])
        ((everybody, silent),) = self.query(
            """SELECT count(*), count(*) FILTER (WHERE slug NOT IN ('ada-quay', 'ben')) FROM media.personas""")
        self.assertEqual(lines[-1], f"{everybody} personas, {silent} with no voice yet")
        # The commands that were there still mean what they meant.
        self.assertIn("2 voices for 1 personas", test_cli.run("voices")[1])
