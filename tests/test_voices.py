"""Speakers in the database: voices found, lines given to them, names kept.

The recordings are written here: made-up voices, each a wavering note, saying
nothing. The embedder is a stand-in that tells them apart by pitch.
"""

import tempfile
import unittest
import wave
from pathlib import Path

from constructive_media.speakers import SpeakerError, cards, names, voices

from .support import HAVE_FFMPEG, HAVE_NUMPY, ByNote, DatabaseTest, make_voices, settings_in

MEETING = [("ada", 9.0), ("ben", 9.0), ("ada", 9.0), ("cy", 7.5), ("ben", 9.0), ("ada", 9.0),
           ("cy", 7.5), ("ben", 9.0), ("ada", 9.0), ("cy", 7.5), ("ben", 0.6), ("cy", 0.2)]
ADA, BEN, CY = "voice1", "voice2", "voice3"


@unittest.skipUnless(HAVE_FFMPEG and HAVE_NUMPY, "ffmpeg and numpy are needed")
class VoicesTest(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        cls.root = Path(folder.name).resolve()
        cls.spans = {
            "meeting": make_voices(cls.root / "meeting.wav", MEETING),
            "afterwards": make_voices(cls.root / "afterwards.wav", [("ben", 20.0), ("ada", 30.0)]),
        }

    def recording(self, name):
        """Register a recording with a transcript of one segment per line."""
        ((asset_id,),) = self.query(
            """INSERT INTO media.assets (medium, path, content_hash, has_audio)
               VALUES ('audio', %s, %s, true) RETURNING id""",
            (str(self.root / f"{name}.wav"), f"made-up-{name}"),
        )
        for index, (start, end) in enumerate(self.spans[name]):
            self.query(
                """INSERT INTO media_video.segments (asset_id, idx, start_s, end_s, text)
                   VALUES (%s, %s, %s, %s, 'made-up words') RETURNING id""",
                (asset_id, index, start, end),
            )
        return asset_id

    def find(self, match="meeting", **options):
        return voices.find(match, embedder=options.pop("embedder", None) or ByNote(), **options)

    def said_by(self, asset_id):
        return [who for (who,) in self.query(
            "SELECT who FROM media_video.v_dialogue WHERE asset_id = %s ORDER BY idx", (asset_id,))]

    def test_the_voices_of_a_recording_are_found_and_stored(self):
        asset = self.recording("meeting")
        (found,) = self.find()
        self.assertEqual((found.asset_id, found.name, found.model), (asset, "meeting.wav", "by-note-1"))
        self.assertEqual([voice.label for voice in found.telling.voices], [ADA, BEN, CY])
        stored = self.query(
            """SELECT label, n_segments, n_windows, speech_s, method, model, cardinality(centroid), persona_id
                 FROM media_video.speaker_clusters WHERE asset_id = %s ORDER BY label""", (asset,))
        self.assertEqual(stored, [
            (ADA, 4, 44, 36.0, "voice-embedding", "by-note-1", 4, None),
            (BEN, 4, 33, 27.6, "voice-embedding", "by-note-1", 4, None),
            (CY, 3, 27, 22.5, "voice-embedding", "by-note-1", 4, None),
        ])
        for (cohesion,) in self.query(
                "SELECT cohesion FROM media_video.speaker_clusters WHERE asset_id = %s", (asset,)):
            self.assertGreater(cohesion, 0.99)

    def test_every_line_is_given_to_its_voice_and_the_unclear_ones_to_nobody(self):
        asset = self.recording("meeting")
        (found,) = self.find()
        self.assertEqual(self.said_by(asset), [ADA, BEN, ADA, CY, BEN, ADA, CY, BEN, ADA, CY, BEN, "unknown"])
        self.assertEqual(found.telling.unknown, 1)

    def test_a_recording_gone_over_is_left_alone_until_forced(self):
        self.recording("meeting")
        listener = ByNote()
        self.assertEqual(len(self.find(embedder=listener)), 1)
        asked = listener.asked
        self.assertEqual(self.find(embedder=listener), [])
        self.assertEqual(listener.asked, asked)
        self.assertEqual(len(self.find(embedder=listener, force=True)), 1)
        self.assertEqual(self.query("SELECT count(*) FROM media_video.speaker_clusters"), [(3,)])

    def test_only_transcribed_recordings_that_match_are_gone_over(self):
        self.recording("meeting")
        self.query("INSERT INTO media.assets (medium, path, content_hash, has_audio) "
                   "VALUES ('audio', '/made-up/silent.wav', 'made-up-silent', true) RETURNING id")
        self.assertEqual(self.find("no-such-recording"), [])
        self.assertEqual([found.name for found in self.find(None)], ["meeting.wav"])

    def test_the_number_of_voices_can_be_given(self):
        asset = self.recording("meeting")
        (found,) = self.find(voices=2)
        self.assertEqual(len(found.telling.voices), 2)
        self.assertEqual(len(set(self.said_by(asset)) - {"unknown"}), 2)

    def test_a_named_voice_speaks_under_its_name(self):
        asset = self.recording("meeting")
        self.find()
        self.assertEqual(names.name("meeting", ADA, "Ada Example", new=True), ("meeting.wav", 4))
        self.assertEqual(self.said_by(asset)[:4], ["Ada Example", BEN, "Ada Example", CY])
        self.assertEqual(self.query("SELECT slug, kind FROM media.personas"), [("ada-example", "person")])
        # By name or by slug, and nobody is added twice.
        names.name("meeting", ADA, "ada-example")
        names.name("meeting", ADA, "ADA EXAMPLE")
        self.assertEqual(self.query("SELECT count(*) FROM media.personas"), [(1,)])
        names.name("meeting", ADA, None)
        self.assertEqual(self.said_by(asset)[0], ADA)

    def test_names_are_kept_when_a_recording_is_gone_over_again(self):
        asset = self.recording("meeting")
        self.find()
        names.name("meeting", BEN, "Ben Example", new=True, kind="character")
        (again,) = self.find(force=True)
        self.assertEqual([voice.persona for voice in again.telling.voices], [None, "Ben Example", None])
        self.assertEqual(self.said_by(asset)[:2], [ADA, "Ben Example"])
        self.assertEqual(self.query("SELECT kind FROM media.personas"), [("character",)])

    def test_a_named_voice_is_suggested_where_it_is_heard_again(self):
        self.recording("meeting")
        self.find()
        names.name("meeting", ADA, "Ada Example", new=True)
        self.recording("afterwards")
        (found,) = self.find("afterwards", least=12)
        self.assertEqual([(voice.label, voice.persona, voice.like) for voice in found.telling.voices],
                         [("voice1", None, "Ada Example"), ("voice2", None, None)])
        (afterwards, meeting) = names.listed()
        self.assertEqual([(voice.label, voice.persona, voice.like) for voice in meeting.voices],
                         [(ADA, "Ada Example", None), (BEN, None, None), (CY, None, None)])
        self.assertEqual([(voice.label, voice.like) for voice in afterwards.voices],
                         [("voice1", "Ada Example"), ("voice2", None)])
        self.assertEqual((meeting.unknown, afterwards.unknown, meeting.model), (1, 0, "by-note-1"))
        # A suggestion is not a name: nothing in the other recording is theirs until someone says so.
        self.assertEqual(self.query(
            "SELECT count(*) FROM media_video.speaker_clusters WHERE persona_id IS NOT NULL"), [(1,)])
        self.assertEqual([recording.name for recording in names.listed("after")], ["afterwards.wav"])

    def test_naming_says_what_is_wrong(self):
        with self.assertRaisesRegex(SpeakerError, "me diarize"):
            names.name("meeting", ADA, "Ada Example", new=True)
        self.recording("meeting")
        self.recording("afterwards")
        self.find(None, least=12)
        with self.assertRaisesRegex(SpeakerError, "2 recordings match"):
            names.name("wav", ADA, "Ada Example", new=True)
        with self.assertRaisesRegex(SpeakerError, "no voice 'voice9'; it has voice1, voice2, voice3"):
            names.name("meeting", "voice9", "Ada Example", new=True)
        with self.assertRaisesRegex(SpeakerError, "--new"):
            names.name("meeting", ADA, "Ada Example")
        with self.assertRaisesRegex(SpeakerError, "no letters"):
            names.name("meeting", ADA, "...", new=True)
        self.assertEqual(self.query("SELECT count(*) FROM media.personas"), [(0,)])

    def test_each_voice_gets_a_card_to_listen_to(self):
        asset = self.recording("meeting")
        (found,) = self.find()
        settings = settings_in(self.root / "home")
        self.assertIsNone(cards.card(asset, ADA, settings))
        made = cards.make(asset, found.source, found.telling.voices, settings)
        self.assertEqual([path.name for path in made], ["voice1.wav", "voice2.wav", "voice3.wav"])
        self.assertEqual(cards.card(asset, ADA, settings), made[0])
        self.assertEqual(made[0].parent, self.root / "home" / "work" / "speakers" / str(asset)[:8])
        with wave.open(str(made[0])) as card:
            seconds = card.getnframes() / card.getframerate()
            self.assertEqual(card.getnchannels(), 1)
        moments = len(found.telling.voices[0].best)
        self.assertEqual(moments, voices.BEST)
        self.assertAlmostEqual(seconds, moments * (voices.WINDOW_S + cards.BREATH_S), delta=0.05)
        # Cards are made again with the voices, and the old ones go.
        (made[2].parent / "voice9.wav").write_bytes(b"left over")
        cards.make(asset, found.source, found.telling.voices[:1], settings)
        self.assertEqual(sorted(path.name for path in made[0].parent.iterdir()), ["voice1.wav"])
