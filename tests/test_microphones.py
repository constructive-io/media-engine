"""Who wore which microphone, and who spoke each line, on a conversation made on the spot."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import ffmpeg, takes
from constructive_media.edit import said
from constructive_media.speakers import SpeakerError
from constructive_media.takes import TakeError

from .support import (
    HAVE_FFMPEG,
    HAVE_NUMPY,
    TEST_DATABASE_VARIABLE,
    DatabaseTest,
    needs_numpy,
    settings_in,
    spoken,
    write_lines,
)
from .test_cli import run

if HAVE_NUMPY:
    import numpy as np

    from constructive_media.audio import pcm
    from constructive_media.takes import microphones
    from constructive_media.takes.microphones import Choice

RATE = 16000
#: A made-up conversation at a table, in the take's time: when each line starts, who says it, and what.
#: Ada and Ben each wear a microphone. Cy sits between them with none.
TALK = (
    (2.0, "ada", "The tide tables came by post, a month at once."),
    (7.0, "ben", "And you copied them out by hand for the boats."),
    (12.0, "ada", "Every one, in pencil first and then in ink, Sundays."),
    (17.0, "ben", "My father kept his folded in his hat all season."),
    (22.0, "ada", "He was the only one who brought a copy back."),
    (27.0, "ben", "He would have liked to hear that said out loud."),
    (32.0, "ada", "Then say it to the harbour next time you pass."),
    (37.0, "cy", "Could one of you pass the bread before it cools?"),
)
#: How long each line is spoken for, in seconds.
SPOKEN_S = 3.0
#: How loud a voice is in its wearer's microphone, in the other one, and when it has none.
OWN, OTHER, NEITHER = 0.5, 0.05, 0.02


def level(seconds, loud, quiet=0.0):
    """A microphone's level, a value a hundredth of a second: `loud` in the stretches given, else `quiet`."""
    heard = np.full(round(seconds * microphones.RATE), float(quiet))
    for start, end, how in loud:
        heard[round(start * microphones.RATE):round(end * microphones.RATE)] = how
    return heard


def record(path, seconds, began_s, wearer, gain=1.0):
    """What one microphone at the table recorded, having started `began_s` into the take."""
    sound = np.random.default_rng(len(path.name)).normal(0, 0.0005, round(seconds * RATE))
    for start, who, _ in TALK:
        loud = NEITHER if who == "cy" else OWN if who == wearer else OTHER if wearer else 0.3
        first = round((start - began_s) * RATE)
        voice = spoken(who, SPOKEN_S, RATE)[:max(0, len(sound) - first)] * (loud / 0.5)
        sound[first:first + len(voice)] += voice
    pcm.write(path, (sound * gain).astype(np.float32), RATE, bits=16)


@needs_numpy
class LoudestTest(unittest.TestCase):
    def setUp(self):
        # Ada's recorder is turned well up and Ben's well down, and each hears the other faintly.
        self.ada = level(30, [(2, 5, 0.3), (12, 15, 0.3), (7, 10, 0.03)], quiet=0.0003)
        self.ben = level(30, [(7, 10, 0.02), (17, 20, 0.02), (2, 5, 0.002), (12, 15, 0.002)], quiet=0.00002)

    def test_each_microphone_is_measured_against_its_own_loud_level(self):
        hers, his = microphones.loudest([(self.ada, 0.0), (self.ben, 0.0)], [(2, 5), (7, 10)])
        self.assertEqual((hers.winner, his.winner), (0, 1))
        # Ben's line is louder in Ada's recorder than in his own, and is still his.
        self.assertGreater(self.ada[800], self.ben[800])
        self.assertAlmostEqual(hers.lead_db, 20.0, places=3)
        self.assertAlmostEqual(his.lead_db, 20.0, places=3)
        self.assertAlmostEqual(his.level_db, 0.0, places=3)

    def test_a_recording_is_read_in_its_own_time(self):
        # Ben's recorder was started four seconds into the take.
        late = [(self.ada, 0.0), (self.ben[400:], 4.0)]
        self.assertEqual([found.winner for found in microphones.loudest(late, [(2, 3.5), (7, 10), (17, 20)])],
                         [None, 1, 1])
        # Read as if it had started with the take, his line is nobody's.
        self.assertIsNone(microphones.loudest([(self.ada, 0.0), (self.ben[400:], 0.0)], [(7, 10)])[0].winner)

    def test_a_line_no_microphone_leads_clearly_is_left_without_a_name(self):
        both = level(30, [(2, 5, 0.3), (7, 10, 0.3)], quiet=0.0003)
        (found,) = microphones.loudest([(both, 0.0), (both * 0.1, 0.0)], [(2, 5)])
        self.assertEqual((found.winner, round(found.lead_db, 3)), (None, 0.0))
        # Three decibels is a lead, and not one to act on unless asked.
        other = both.copy()
        other[200:500] *= 0.7
        nearly = [(other, 0.0), (both, 0.0)]
        (found,) = microphones.loudest(nearly, [(2, 5)])
        self.assertEqual((found.winner, round(found.lead_db, 1)), (None, 3.1))
        self.assertEqual(microphones.loudest(nearly, [(2, 5)], margin_db=3.0)[0].winner, 1)

    def test_a_line_far_under_every_microphones_loud_level_is_left(self):
        # Somebody across the room: Ada's microphone hears them best, and barely.
        self.ada[2200:2500], self.ben[2200:2500] = 0.3 / 20, 0.02 / 200
        (found,) = microphones.loudest([(self.ada, 0.0), (self.ben, 0.0)], [(22, 25)])
        self.assertEqual((found.winner, round(found.lead_db), round(found.level_db)), (None, 20, -26))
        self.assertEqual(microphones.loudest([(self.ada, 0.0), (self.ben, 0.0)], [(22, 25)], floor_db=-30)[0].winner, 0)

    def test_a_microphone_that_recorded_nothing_never_wins(self):
        dead = np.zeros(3000)
        found = microphones.loudest([(dead, 0.0), (self.ada, 0.0), (self.ben, 0.0)], [(2, 5), (7, 10), (25, 28)])
        self.assertEqual([one.winner for one in found], [1, 2, None])
        # With one left to hear it there is nothing to compare.
        self.assertEqual(microphones.loudest([(dead, 0.0), (self.ada, 0.0)], [(2, 5)]), [Choice()])

    def test_a_line_too_short_or_outside_the_sound_is_not_judged(self):
        pair = [(self.ada, 0.0), (self.ben, 0.0)]
        self.assertEqual(microphones.loudest(pair, [(2.0, 2.1), (40, 43), (-5, -2)]), [Choice()] * 3)
        self.assertEqual(microphones.loudest(pair, []), [])
        self.assertEqual(microphones.loudest([(self.ada, 0.0), (self.ben[:600], 0.0)], [(7, 10)]), [Choice()])


@unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
class TableTest(DatabaseTest):
    """The made-up conversation as a take: a recorder on the table, and a microphone each on Ada and Ben."""

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        record(cls.root / "table.wav", 45.0, 0.0, None)
        record(cls.root / "ada-collar.wav", 45.0, 0.0, "ada")
        # Ben's was started a second and a half late, stopped early, and is turned well down.
        record(cls.root / "ben-collar.wav", 40.0, 1.5, "ben", gain=0.2)
        cls.settings = settings_in(cls.root / "home")

    def setUp(self):
        super().setUp()
        environment = mock.patch.dict(os.environ, {
            "CONSTRUCTIVE_MEDIA_HOME": str(self.root / "home"),
            "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
        })
        environment.start()
        self.addCleanup(environment.stop)
        ((self.take_id,),) = self.query(
            "INSERT INTO media.takes (name, project) VALUES ('made-up-table', 'made-up') RETURNING id")
        self.table = self.join("table.wav", "ambient", primary=True)
        self.ada = self.join("ada-collar.wav", "lav")
        self.ben = self.join("ben-collar.wav", "lav", offset=1.5)
        self.lines = write_lines(self.table, [(start, text) for start, _, text in TALK])
        write_lines(self.ada, [(start, text) for start, who, text in TALK if who == "ada"])
        write_lines(self.ben, [(start - 1.5, text) for start, who, text in TALK if who == "ben"])
        self.take = takes.find("made-up-table")

    def join(self, name, role, *, primary=False, offset=0.0, placed=True):
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio, project, duration_s)
               VALUES ('audio', %s, %s, true, 'made-up', 45) RETURNING id""",
            (str(self.root / name), f"made-up-{name}"))
        self.query(
            """INSERT INTO media.take_assets (take_id, asset_id, role, is_primary, offset_s, offset_method)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING asset_id""",
            (self.take_id, asset_id, role, primary, offset, "words" if placed and not primary else None))
        return asset_id

    def worn(self):
        microphones.wear("made-up-table", "ada-collar", "Ada", new=True)
        microphones.wear("made-up-table", "ben-collar", "Ben", new=True)

    def voices(self, **named):
        """The table recorder's voices as `diarize` would leave them: Ada is voice1, Ben voice2, Cy unknown."""
        for label, who in (("voice1", "ada"), ("voice2", "ben")):
            ((cluster,),) = self.query(
                """INSERT INTO media_video.speaker_clusters (asset_id, label, method, persona_id)
                   VALUES (%s, %s, 'voice-embedding', (SELECT id FROM media.personas WHERE name = %s)) RETURNING id""",
                (self.table, label, named.get(label)))
            for line, (_, speaker, _) in zip(self.lines, TALK):
                if speaker == who:
                    self.query("UPDATE media_video.segments SET cluster_id = %s WHERE id = %s RETURNING id",
                               (cluster, line))

    def said(self):
        """Who each of the table recorder's lines is given to."""
        return [who for (who,) in self.query(
            """SELECT p.name FROM media_video.segments s LEFT JOIN media.personas p ON p.id = s.persona_id
                WHERE s.asset_id = %s ORDER BY s.idx""", (self.table,))]

    def test_a_microphone_is_tied_to_whoever_wore_it(self):
        self.assertEqual(microphones.wear("made-up-table", "ada-collar", "Ada", new=True), ("ada-collar.wav", "Ada"))
        self.assertEqual(microphones.wear("made-up-table", "BEN", "Ben", new=True, kind="character"),
                         ("ben-collar.wav", "Ben"))
        self.assertEqual(self.query("SELECT label, persona FROM media.v_take_assets WHERE take = 'made-up-table' "
                                    "AND persona IS NOT NULL ORDER BY persona"),
                         [(None, "Ada"), (None, "Ben")])
        self.assertEqual(self.query("SELECT kind FROM media.personas WHERE slug IN ('ada', 'ben') ORDER BY slug"),
                         [("person",), ("character",)])
        self.assertEqual([(found.name, found.wearer, found.offset_s) for found in microphones.worn(self.take)],
                         [("ada-collar.wav", "Ada", 0.0), ("ben-collar.wav", "Ben", 1.5)])
        self.assertEqual(microphones.wear("made-up-table", "ada-collar", None), ("ada-collar.wav", None))
        self.assertEqual([found.wearer for found in microphones.worn(self.take)], [None, "Ben"])

    def test_wearing_needs_one_recording_of_the_take_and_someone_known(self):
        with self.assertRaisesRegex(TakeError, "no take is called 'made-up-bench'"):
            microphones.wear("made-up-bench", "collar", "Ada", new=True)
        with self.assertRaisesRegex(TakeError, "no recording of made-up-table matches 'boom'"):
            microphones.wear("made-up-table", "boom", "Ada", new=True)
        with self.assertRaisesRegex(TakeError, r"2 recordings of made-up-table match 'collar' "
                                               r"\(ada-collar.wav, ben-collar.wav\); say which one"):
            microphones.wear("made-up-table", "collar", "Ada", new=True)
        with self.assertRaisesRegex(SpeakerError, "nobody is called 'Ada' yet"):
            microphones.wear("made-up-table", "ada-collar", "Ada")
        self.assertEqual(self.query("SELECT count(*) FROM media.personas WHERE slug = 'ada'"), [(0,)])
        self.query("UPDATE media.assets SET has_audio = false WHERE id = %s RETURNING id", (self.ben,))
        with self.assertRaisesRegex(TakeError, "ben-collar.wav has no sound"):
            microphones.wear("made-up-table", "ben-collar", "Ben", new=True)

    def test_a_recording_somebody_wore_is_a_microphone_whatever_it_was_called(self):
        self.assertEqual(len(microphones.worn(self.take)), 2)
        microphones.wear("made-up-table", "table", "Cy", new=True)
        self.assertEqual([found.name for found in microphones.worn(self.take)],
                         ["table.wav", "ada-collar.wav", "ben-collar.wav"])
        # One nothing has placed has no time to be read in.
        self.join("loose.wav", "lav", placed=False)
        self.assertEqual(len(microphones.worn(self.take)), 3)

    def test_each_line_goes_to_the_microphone_that_heard_it_loudest(self):
        reading = microphones.read(self.take)
        self.assertEqual(len(reading.lines), 15)
        on_table = [line for line in reading.lines if line.asset_id == self.table]
        self.assertEqual([line.microphone and line.microphone.name.split("-")[0] for line in on_table],
                         [who if who != "cy" else None for _, who, _ in TALK])
        self.assertEqual([(line.start_s, round(line.end_s, 2)) for line in on_table][:2], [(2.0, 4.95), (7.0, 9.95)])
        for line in on_table[:-1]:
            self.assertAlmostEqual(line.lead_db, 20.0, delta=1.5)
        # Nobody leads on Cy's line, and how nearly is still said.
        self.assertLess(on_table[-1].lead_db, 2.0)
        self.assertEqual((reading.count(microphones.UNWORN), reading.count(microphones.UNCLEAR)), (14, 1))
        self.assertEqual(reading.silent, [])

    def test_nothing_is_written_until_someone_is_said_to_wear_a_microphone(self):
        self.assertEqual(microphones.write(microphones.read(self.take)), 0)
        self.assertEqual(self.said(), [None] * 8)
        microphones.wear("made-up-table", "ada-collar", "Ada", new=True)
        reading = microphones.read(self.take)
        self.assertEqual((reading.count(microphones.GIVEN), reading.count(microphones.UNWORN)), (8, 6))
        self.assertEqual(microphones.write(reading), 8)
        self.assertEqual(self.said(), ["Ada", None, "Ada", None, "Ada", None, "Ada", None])

    def test_a_line_is_shown_under_its_speakers_name(self):
        self.worn()
        self.assertEqual(microphones.write(microphones.read(self.take)), 14)
        self.assertEqual(self.said(), ["Ada", "Ben", "Ada", "Ben", "Ada", "Ben", "Ada", None])
        heard = [line for line in said.lines(self.take_id) if line.asset_id == self.table]
        self.assertEqual([line.who for line in heard][:3], ["Ada", "Ben", "Ada"])
        # Every line of each microphone's own transcript is its wearer's too.
        self.assertEqual(self.query(
            """SELECT p.name, count(*) FROM media_video.segments s JOIN media.personas p ON p.id = s.persona_id
                WHERE s.asset_id IN (%s, %s) GROUP BY 1 ORDER BY 1""", (self.ada, self.ben)), [("Ada", 4), ("Ben", 3)])

    def test_going_over_a_take_again_takes_back_what_it_gave_and_nothing_else(self):
        self.worn()
        ((cy,),) = self.query("INSERT INTO media.personas (slug, name, kind) VALUES ('cy', 'Cy', 'person') RETURNING id")
        # A person says the second line was Cy's, whatever the microphones heard.
        self.query("UPDATE media_video.segments SET persona_id = %s WHERE id = %s RETURNING id", (cy, self.lines[1]))
        reading = microphones.read(self.take)
        self.assertEqual((reading.count(microphones.GIVEN), reading.count(microphones.BY_HAND)), (13, 1))
        self.assertEqual(microphones.write(reading), 13)
        self.assertEqual(self.said(), ["Ada", "Cy", "Ada", "Ben", "Ada", "Ben", "Ada", None])
        # The same again changes nothing: what it gave last time is not taken for a person's.
        again = microphones.read(self.take)
        self.assertEqual((again.count(microphones.GIVEN), again.count(microphones.BY_HAND)), (13, 1))
        self.assertEqual(microphones.write(again), 13)
        self.assertEqual(self.said(), ["Ada", "Cy", "Ada", "Ben", "Ada", "Ben", "Ada", None])
        # The microphones were the other way round all along.
        microphones.wear("made-up-table", "ada-collar", "Ben")
        microphones.wear("made-up-table", "ben-collar", "Ada")
        self.assertEqual(microphones.write(microphones.read(self.take)), 13)
        self.assertEqual(self.said(), ["Ben", "Cy", "Ben", "Ada", "Ben", "Ada", "Ben", None])
        # And with nobody wearing them, only the person's line is left.
        microphones.wear("made-up-table", "ada-collar", None)
        microphones.wear("made-up-table", "ben-collar", None)
        self.assertEqual(microphones.write(microphones.read(self.take)), 0)
        self.assertEqual(self.said(), [None, "Cy", None, None, None, None, None, None])
        self.assertEqual(self.query("SELECT count(*) FROM media.assets WHERE jsonb_exists(meta, 'microphones')"), [(0,)])

    def test_a_voice_a_person_named_as_someone_else_is_not_overruled(self):
        self.worn()
        self.query("INSERT INTO media.personas (slug, name, kind) VALUES ('cy', 'Cy', 'person') RETURNING id")
        # Somebody listened to the table recorder and said its second voice is Cy.
        self.voices(voice1="Ada", voice2="Cy")
        reading = microphones.read(self.take)
        self.assertEqual((reading.count(microphones.GIVEN), reading.count(microphones.DISAGREES)), (11, 3))
        self.assertEqual(microphones.write(reading), 11)
        self.assertEqual(self.said(), ["Ada", None, "Ada", None, "Ada", None, "Ada", None])
        heard = [line for line in said.lines(self.take_id) if line.asset_id == self.table]
        self.assertEqual([line.who for line in heard][:2], ["Ada", "Cy"])
        ada, ben = microphones.won(reading)
        self.assertEqual((ada.voice.owner, ben.voice.owner, ben.voice.alone), ("Ada", "Cy", True))

    def test_each_microphone_should_carry_one_voice(self):
        # With no voices found there is nothing to check against.
        self.assertEqual([found.voice for found in microphones.won(microphones.read(self.take))], [None, None])
        self.voices()
        ada, ben = microphones.won(microphones.read(self.take))
        self.assertEqual((ada.microphone.name, ada.lines, round(ada.seconds, 1)), ("ada-collar.wav", 8, 23.6))
        self.assertEqual((ada.voice.label, ada.voice.recording, ada.voice.share, ada.voice.alone),
                         ("voice1", "table.wav", 1.0, True))
        self.assertEqual((ben.lines, ben.voice.label, ben.voice.share), (6, "voice2", 1.0))
        self.assertAlmostEqual(ada.lead_db, 20.0, delta=1.5)
        # Ben's microphone is on the table between the two of them after all, and takes Ada's lines as well.
        with mock.patch.object(microphones, "loudest", lambda heard, spans, **how: [Choice(1, 6.0, -3.0) for _ in spans]):
            ada, ben = microphones.won(microphones.read(self.take))
        self.assertEqual((ada.lines, ada.voice, ada.lead_db), (0, None, None))
        self.assertEqual((ben.lines, ben.voice.label, ben.voice.alone), (15, "voice1", False))
        self.assertAlmostEqual(ben.voice.share, 4 / 7)

    def test_a_line_only_one_microphone_was_recording_is_not_judged(self):
        self.worn()
        # Ben's microphone stopped at 41.5 seconds into the take.
        write_lines(self.table, [(42.0, "And that was the last of the bread, I am sorry to say.")])
        reading = microphones.read(self.take)
        self.assertEqual((reading.count(microphones.UNHEARD), reading.lines[-1].lead_db), (1, None))

    def test_two_microphones_are_needed_and_each_must_be_where_it_was(self):
        self.query("UPDATE media.take_assets SET offset_method = NULL WHERE asset_id = %s RETURNING asset_id", (self.ben,))
        with self.assertRaisesRegex(TakeError, "made-up-table has 1 microphones placed"):
            microphones.read(self.take)
        self.query("UPDATE media.take_assets SET offset_method = 'manual' WHERE asset_id = %s RETURNING asset_id",
                   (self.ben,))
        self.query("UPDATE media.assets SET path = '/made-up/gone.wav' WHERE id = %s RETURNING id", (self.ben,))
        with self.assertRaisesRegex(TakeError, "gone.wav is not where it was"):
            microphones.read(self.take)

    def test_a_microphone_that_recorded_nothing_is_named_and_left_out(self):
        pcm.write(self.root / "off.wav", np.zeros(20 * RATE, dtype=np.float32), RATE, bits=16)
        self.addCleanup((self.root / "off.wav").unlink)
        self.join("off.wav", "lav")
        reading = microphones.read(self.take)
        self.assertEqual((reading.silent, len(reading.microphones)), (["off.wav"], 3))
        self.assertEqual((reading.count(microphones.UNWORN), reading.count(microphones.UNCLEAR)), (14, 1))

    def test_a_clip_of_each_microphone_is_cut_from_what_it_won(self):
        made = microphones.clips(microphones.read(self.take), self.settings)
        self.assertEqual(sorted(made), sorted([self.ada, self.ben]))
        self.assertEqual(made[self.ada].parent, self.root / "home" / "work" / "microphones" / "made-up-table")
        self.assertRegex(made[self.ada].name, r"^ada-collar-[0-9a-f]{8}\.wav$")
        # Four of Ada's lines and three of Ben's, a moment of each and a breath after it, each line once.
        self.assertAlmostEqual(ffmpeg.probe(made[self.ada]).duration_s, 4 * 1.85, delta=0.05)
        self.assertAlmostEqual(ffmpeg.probe(made[self.ben]).duration_s, 3 * 1.85, delta=0.05)
        # Ada's clip is Ada: as loud all through as she is in her own microphone, not as Ben is.
        heard = pcm.read(made[self.ada], rate=RATE, stereo=False)
        self.assertGreater(float(np.sqrt((heard[:int(1.4 * RATE)] ** 2).mean())), 0.3)
        # A stricter reading that nothing passes leaves no clip, and clears the old ones.
        with mock.patch.object(microphones, "CLIP_LEAD_DB", 40.0):
            self.assertEqual(microphones.clips(microphones.read(self.take), self.settings), {})
        self.assertFalse(made[self.ada].exists())

    def test_the_commands_say_who_wore_what_and_who_spoke(self):
        self.assertEqual(run("wear", "made-up-table", "ada-collar", "Ada")[::2],
                         (1, "media: nobody is called 'Ada' yet; add --new to add them\n"))
        self.assertEqual(run("wear", "made-up-table", "ada-collar", "Ada", "--new")[:2],
                         (0, "ada-collar.wav in made-up-table was worn by Ada\n"))
        self.assertEqual(run("wear", "made-up-table", "ada-collar")[::2],
                         (2, "media: give a name, or --clear to say nobody wore it\n"))
        status, out, _ = run("microphones", "made-up-table", "--dry-run")
        self.assertEqual((status, out), (0, """made-up-table: 2 microphones, 15 lines
  ada-collar.wav  Ada             8 lines    0:24  leads by 20 dB
  ben-collar.wav  nobody yet      6 lines    0:18  leads by 20 dB
  1 lines unclear: no microphone led the next by 4 dB
  6 lines won by a microphone nobody is said to wear; `media wear` says who
  no voices have been found in these recordings; after `media diarize` each microphone is checked against them
8 of 15 lines given a speaker; nothing was written
"""))
        self.assertEqual(self.said(), [None] * 8)
        self.assertEqual(run("wear", "made-up-table", "ben-collar", "Ben", "--new", "--character")[:2],
                         (0, "ben-collar.wav in made-up-table was worn by Ben\n"))
        self.voices(voice1="Ada")
        status, out, _ = run("microphones", "made-up-table", "--clips")
        lines = out.splitlines()
        self.assertEqual((status, lines[0], lines[-2:]), (0, "made-up-table: 2 microphones, 15 lines", [
            "  1 lines unclear: no microphone led the next by 4 dB", "14 of 15 lines given a speaker"]))
        self.assertEqual(lines[1::2][:2], [
            "  ada-collar.wav  Ada      8 lines    0:24  leads by 20 dB  one voice: voice1 of table.wav",
            "  ben-collar.wav  Ben      6 lines    0:18  leads by 20 dB  one voice: voice2 of table.wav"])
        self.assertRegex(lines[2], r"^                  listen: .*/work/microphones/made-up-table/ada-collar-[0-9a-f]{8}\.wav$")
        self.assertTrue(Path(lines[4].split("listen: ")[1]).is_file())
        self.assertEqual(self.said(), ["Ada", "Ben", "Ada", "Ben", "Ada", "Ben", "Ada", None])
        # Asked for a lead nobody has, it gives nobody a line, and takes back the ones it gave.
        self.assertTrue(run("microphones", "made-up-table", "--margin", "30")[1].endswith(
            "  15 lines unclear: no microphone led the next by 30 dB\n0 of 15 lines given a speaker\n"))
        self.assertEqual(self.said(), [None] * 8)
        self.assertEqual(run("wear", "made-up-table", "ben-collar", "--clear")[:2],
                         (0, "ben-collar.wav in made-up-table was worn by nobody\n"))
        self.assertEqual(run("microphones", "made-up-bench")[::2],
                         (1, "media: no take is called 'made-up-bench'; `media takes` lists them\n"))
