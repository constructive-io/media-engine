"""Pictures and video a model makes, with a stand-in for the model.

Nothing here loads a model or asks a service. The makers are stand-ins that
write a file made on the spot and keep what they were asked.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import adapters, derive, generate, ingest
from constructive_media.adapters import PERMISSIVE
from constructive_media.edit import timelines
from constructive_media.edit.timelines import Clip
from constructive_media.generate import GenerateError, Job, Request

from . import test_cli
from .support import TEST_DATABASE_VARIABLE, DatabaseTest, make_still, make_video, needs_ffmpeg, settings_in

WORDS = "a made-up harbour at dawn, fog over still water"


class Painter:
    """A stand-in for a model: it writes a file made on the spot, and keeps what it was asked."""

    name = "made-up"
    model = "made-up-painter-1"

    def __init__(self, fails=None):
        self.asked, self.fails = [], fails

    def make(self, request, target):
        self.asked.append(request)
        if self.fails:
            raise RuntimeError(self.fails)
        if request.medium == "video":
            return make_video(Path(target), colours=("red",), seconds=request.seconds or 1.0)
        Path(target).write_bytes(b"made-up picture %d" % len(self.asked))
        return target


class Priced(Painter):
    name = "made-up-priced"

    def price(self, request):
        return 0.25 * (request.seconds or 1)


class Forgetful(Painter):
    """It says it made a file, and did not."""

    def make(self, request, target):
        self.asked.append(request)
        return target


class Service:
    """A stand-in for a service that answers later."""

    name = "made-up-service"
    model = "made-up-service-2"

    def __init__(self):
        self.asked, self.states, self.fetched = [], {}, []

    def submit(self, request):
        self.asked.append(request)
        job = f"job-{len(self.asked)}"
        self.states[job] = Job("running")
        return job

    def poll(self, job):
        return self.states[job]

    def fetch(self, job, target):
        self.fetched.append(job)
        Path(target).write_bytes(b"made-up picture from afar")
        return target


class GenerateTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.settings = settings_in(self.root / "home")
        self.painter = Painter()

    def make(self, words=WORDS, medium="image", maker=None, **asked):
        how = {name: asked.pop(name) for name in ("out", "project", "again", "max_usd", "dry_run") if name in asked}
        return generate.make(Request(medium, words, **asked), maker=maker or self.painter, settings=self.settings, **how)

    def rows(self):
        return self.query(
            "SELECT status, model, backend, prompt, params, error, cost_usd FROM media_video.generations ORDER BY created_at, id"
        )

    # Making.

    def test_what_is_made_is_a_file_a_row_and_a_record(self):
        done = self.make(project="made-up-quay")
        self.assertEqual(done.state, generate.MADE)
        self.assertEqual(done.file.parent, self.settings.work_dir.resolve() / "generated")
        self.assertRegex(done.file.name, r"^a-made-up-harbour-at-dawn-[0-9a-f]{8}\.png$")
        self.assertTrue(done.file.is_file())
        (status, model, backend, prompt, params, error, cost), = self.rows()
        self.assertEqual((status, model, backend, prompt, error, cost),
                         ("succeeded", "made-up-painter-1", "made-up", WORDS, None, None))
        self.assertEqual(params["file"], str(done.file))
        self.assertEqual(params["project"], "made-up-quay")
        self.assertIn("took_s", params)
        self.assertNotIn("seed", params)
        (medium, project), = self.query("SELECT medium, project FROM media.assets WHERE path = %s", (str(done.file),))
        self.assertEqual((medium, project), ("image", "made-up-quay"))
        found = derive.lineage(done.file)
        self.assertEqual((found.kind, found.tool), ("generated", "media generate"))
        self.assertEqual(found.params["prompt"], WORDS)
        self.assertEqual(found.params["model"], "made-up-painter-1")
        self.assertEqual(found.params["generation"], str(done.generation.id))
        self.assertEqual(done.generation.file, done.file)
        self.assertEqual(done.generation.status, "succeeded")

    def test_the_same_request_is_made_once(self):
        first = self.make()
        again = self.make("  a made-up harbour at dawn,\n fog over   still water ")
        self.assertEqual((again.state, again.file), (generate.HAD, first.file))
        self.assertEqual(len(self.painter.asked), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_other_words_or_other_settings_are_another_request(self):
        first = self.make()
        self.assertNotEqual(self.make("a made-up lighthouse").file, first.file)
        self.assertNotEqual(self.make(steps=8).file, first.file)
        self.assertNotEqual(self.make(width=640, height=480).file, first.file)
        self.assertNotEqual(self.make(seed=3).file, first.file)
        self.assertEqual(len(self.rows()), 5)
        self.assertEqual(self.painter.asked[2].steps, 8)
        self.assertEqual((self.painter.asked[3].width, self.painter.asked[3].height), (640, 480))

    def test_another_model_is_another_request(self):
        other = Painter()
        other.model = "made-up-painter-2"
        self.assertNotEqual(self.make().file, self.make(maker=other).file)

    def test_again_makes_another_with_a_seed_of_its_own(self):
        first = self.make()
        second = self.make(again=True)
        self.assertEqual(second.state, generate.MADE)
        self.assertNotEqual(second.file, first.file)
        self.assertTrue(first.file.is_file())
        seed = self.painter.asked[1].seed
        self.assertIsInstance(seed, int)
        self.assertEqual(self.rows()[1][4]["seed"], seed)
        # And that one can be had again by its seed.
        self.assertEqual(self.make(seed=seed).state, generate.HAD)

    def test_again_with_a_seed_makes_it_in_place_of_the_first(self):
        first = self.make(seed=11)
        second = self.make(seed=11, again=True)
        self.assertEqual((second.state, second.file), (generate.MADE, first.file))
        self.assertEqual(second.file.read_bytes(), b"made-up picture 2")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.query("SELECT count(*) FROM media.derivations WHERE kind = 'generated'"), [(1,)])

    def test_a_file_that_is_gone_is_made_again(self):
        first = self.make()
        first.file.unlink()
        second = self.make()
        self.assertEqual((second.state, second.file), (generate.MADE, first.file))
        self.assertEqual(len(self.rows()), 1)

    # Where it goes.

    def test_out_says_where_it_goes(self):
        out = self.root / "pictures" / "harbour.png"
        done = self.make(out=out)
        self.assertEqual(done.file, out)
        self.assertTrue(out.is_file())
        self.assertEqual(self.make(out=out).state, generate.HAD)

    def test_out_must_be_the_kind_of_file_it_is(self):
        with self.assertRaisesRegex(GenerateError, r"written as a \.png file"):
            self.make(out=self.root / "harbour.jpg")
        self.assertEqual(self.rows(), [])

    def test_nothing_is_written_over_a_file_that_is_not_its_own(self):
        out = self.root / "harbour.png"
        out.write_bytes(b"somebody's own picture")
        with self.assertRaisesRegex(GenerateError, "there already"):
            self.make(out=out)
        self.assertEqual(out.read_bytes(), b"somebody's own picture")
        mine = self.root / "mine.png"
        self.make(out=mine)
        with self.assertRaisesRegex(GenerateError, "another request made"):
            self.make("a made-up lighthouse", out=mine)
        self.assertEqual(len(self.painter.asked), 1)

    # A picture to start from.

    def test_a_picture_to_start_from_is_what_it_was_made_from(self):
        still = make_still(self.root / "footage" / "quay.png")
        ingest.ingest(still, "made-up-quay", settings=self.settings)
        (asset,), = self.query("SELECT id FROM media.assets WHERE path = %s", (str(still),))
        done = self.make(image=still)
        self.assertEqual(self.painter.asked[0].image, still)
        self.assertEqual(done.generation.input_asset_id, asset)
        self.assertEqual(done.generation.params["from"], str(still))
        self.assertEqual(derive.lineage(done.file).sources, (still.name,))
        self.assertEqual(self.query("SELECT role FROM media.derivation_sources WHERE source_id = %s", (asset,)),
                         [("reference",)])
        self.assertNotEqual(self.make().file, done.file)

    def test_a_picture_that_is_not_on_record_is_kept_by_where_it_was(self):
        still = make_still(self.root / "loose.png")
        done = self.make(image=still)
        self.assertIsNone(done.generation.input_asset_id)
        self.assertEqual(done.generation.params["from"], str(still))
        other = make_still(self.root / "other.png", size="320x180")
        self.assertNotEqual(self.make(image=other).file, done.file)

    def test_a_picture_that_is_not_there_is_refused(self):
        with self.assertRaisesRegex(GenerateError, "not there"):
            self.make(image=self.root / "nowhere.png")

    # What goes wrong.

    def test_a_request_must_make_sense(self):
        for words, medium, asked, said in (
            ("  ", "image", {}, "no words"),
            (WORDS, "image", {"width": 0}, "width is 0"),
            (WORDS, "image", {"steps": -2}, "steps is -2"),
            (WORDS, "image", {"seconds": 2.0}, "only video"),
            (WORDS, "song", {}, "unknown medium 'song'"),
        ):
            with self.subTest(said), self.assertRaisesRegex(GenerateError, said):
                self.make(words, medium, **asked)
        self.assertEqual(self.rows(), [])

    def test_a_failure_is_on_record_and_can_be_tried_again(self):
        broken = Painter(fails="the made-up model ran out of paint")
        with self.assertRaisesRegex(RuntimeError, "out of paint"):
            self.make(maker=broken)
        (status, *_, error, _), = self.rows()
        self.assertEqual((status, error), ("failed", "the made-up model ran out of paint"))
        self.assertEqual(self.make().state, generate.MADE)
        (status, *_, error, _), = self.rows()
        self.assertEqual((status, error), ("succeeded", None))

    def test_a_maker_that_wrote_nothing_has_failed(self):
        with self.assertRaisesRegex(GenerateError, "no such file"):
            self.make(maker=Forgetful())
        self.assertEqual(self.rows()[0][0], "failed")

    def test_stopping_it_is_on_record(self):
        class Stopped(Painter):
            def make(self, request, target):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.make(maker=Stopped())
        self.assertEqual((self.rows()[0][0], self.rows()[0][5]), ("failed", "stopped before it had finished"))

    # What it costs.

    def test_a_price_is_kept_and_can_be_refused(self):
        priced = Priced()
        with self.assertRaisesRegex(GenerateError, r"\$0\.25, over the \$0\.10 allowed"):
            self.make(maker=priced, max_usd=0.10)
        self.assertEqual((self.rows(), priced.asked), ([], []))
        done = self.make(maker=priced, max_usd=0.50)
        self.assertEqual((done.price, done.generation.cost_usd), (0.25, 0.25))
        (spent,) = generate.spend()
        self.assertEqual((spent.model, spent.backend, spent.status, spent.runs, spent.usd),
                         ("made-up-painter-1", "made-up-priced", "succeeded", 1, 0.25))

    def test_a_dry_run_makes_nothing(self):
        done = self.make(maker=Priced(), dry_run=True)
        self.assertEqual((done.state, done.price, done.model, done.backend),
                         (generate.WOULD, 0.25, "made-up-painter-1", "made-up-priced"))
        self.assertFalse(done.file.exists())
        self.assertEqual(self.rows(), [])

    def test_what_a_maker_says_it_cannot_make_is_refused_before_anything_is_written(self):
        class Square(Painter):
            def check(self, request):
                if request.width != request.height:
                    raise GenerateError("the made-up painter paints squares")

        painter = Square()
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), self.assertRaisesRegex(GenerateError, "paints squares"):
                self.make(maker=painter, width=320, height=160, dry_run=dry_run)
        self.assertEqual((painter.asked, self.rows()), ([], []))
        self.assertEqual(self.make(maker=painter, width=160, height=160).state, generate.MADE)

    # Video.

    @needs_ffmpeg
    def test_video_is_on_record_with_how_long_it_runs(self):
        done = self.make(medium="video", seconds=1.0)
        self.assertEqual(done.file.suffix, ".mp4")
        self.assertAlmostEqual(done.generation.params["duration_s"], 1.0, delta=0.15)
        (medium, duration), = self.query("SELECT medium, duration_s FROM media.assets WHERE path = %s", (str(done.file),))
        self.assertEqual(medium, "video")
        self.assertAlmostEqual(duration, 1.0, delta=0.15)
        self.assertAlmostEqual(generate.spend()[0].seconds, 1.0, delta=0.15)

    @needs_ffmpeg
    def test_video_that_does_not_play_has_failed(self):
        class Scribbler(Painter):
            def make(self, request, target):
                Path(target).write_bytes(b"not video")
                return target

        with self.assertRaises(Exception):
            self.make(medium="video", maker=Scribbler())
        self.assertEqual(self.rows()[0][0], "failed")

    # A maker that answers later.

    def test_a_service_is_handed_the_request_and_asked_again_later(self):
        service = Service()
        asked = self.make(maker=service)
        self.assertEqual(asked.state, generate.WAITING)
        self.assertFalse(asked.file.exists())
        self.assertEqual((asked.generation.status, asked.generation.job), ("queued", "job-1"))
        # Asked for again before it has come, it is not asked of the service twice.
        self.assertEqual(self.make(maker=service).state, generate.WAITING)
        self.assertEqual(len(service.asked), 1)

        found = generate.collect(maker=service, settings=self.settings)
        self.assertEqual([one.job for one in found.waiting], ["job-1"])
        self.assertEqual((found.made, found.failed, found.lost), ([], [], []))
        self.assertEqual(self.rows()[0][0], "running")

        service.states["job-1"] = Job("succeeded")
        found = generate.collect(maker=service, settings=self.settings)
        (made,) = found.made
        self.assertEqual((made.status, made.file), ("succeeded", asked.file))
        self.assertEqual(asked.file.read_bytes(), b"made-up picture from afar")
        self.assertEqual(derive.lineage(asked.file).kind, "generated")
        self.assertEqual(self.make(maker=service).state, generate.HAD)
        self.assertEqual(generate.collect(maker=service, settings=self.settings), generate.Collected())

    def test_what_a_service_could_not_make_is_marked_failed(self):
        service = Service()
        self.make(maker=service)
        service.states["job-1"] = Job("failed", "the made-up service was out of ink")
        (failed,) = generate.collect(maker=service, settings=self.settings).failed
        self.assertEqual((failed.status, failed.error), ("failed", "the made-up service was out of ink"))
        # And it can be asked for again.
        self.assertEqual(self.make(maker=service).generation.job, "job-2")

    def test_what_a_service_cannot_send_is_marked_failed_and_the_rest_come_in(self):
        class Flaky(Service):
            def fetch(self, job, target):
                if job == "job-1":
                    raise OSError("the made-up line dropped")
                return super().fetch(job, target)

        service = Flaky()
        self.make(maker=service)
        second = self.make("a made-up lighthouse", maker=service)
        service.states = {"job-1": Job("succeeded"), "job-2": Job("succeeded")}
        found = generate.collect(maker=service, settings=self.settings)
        self.assertEqual([one.error for one in found.failed], ["the made-up line dropped"])
        self.assertEqual([one.file for one in found.made], [second.file])

    def test_a_request_whose_maker_is_gone_is_lost_not_forgotten(self):
        self.make(maker=Service())
        (lost,) = generate.collect(settings=self.settings).lost
        self.assertEqual((lost.backend, lost.status), ("made-up-service", "queued"))

    def test_a_service_that_refuses_the_request_has_failed(self):
        class Shut(Service):
            def submit(self, request):
                raise RuntimeError("the made-up service is shut")

        with self.assertRaisesRegex(RuntimeError, "is shut"):
            self.make(maker=Shut())
        self.assertEqual((self.rows()[0][0], self.rows()[0][5]), ("failed", "the made-up service is shut"))

    # What has been asked for.

    def test_listing_is_the_latest_first_and_can_be_narrowed(self):
        self.make()
        with self.assertRaises(RuntimeError):
            self.make("a made-up lighthouse", maker=Painter(fails="no paint"))
        self.make("a made-up gull", maker=Service())
        self.assertEqual([one.prompt for one in generate.listed()],
                         ["a made-up gull", "a made-up lighthouse", WORDS])
        self.assertEqual([one.prompt for one in generate.listed(status="failed")], ["a made-up lighthouse"])
        self.assertEqual([one.prompt for one in generate.listed(limit=1)], ["a made-up gull"])
        self.assertEqual(generate.listed("video"), [])
        with self.assertRaisesRegex(GenerateError, "unknown medium"):
            generate.listed("song")
        with self.assertRaisesRegex(GenerateError, "unknown status"):
            generate.listed(status="lost")

    def test_the_key_is_the_model_the_words_and_every_setting(self):
        one = Request("image", WORDS, steps=4)
        self.assertEqual(generate.request_key("m", one), generate.request_key("m", Request("image", f" {WORDS}\n", steps=4)))
        self.assertEqual(len(generate.request_key("m", one)), 32)
        others = {generate.request_key(*other) for other in (
            ("m", one), ("n", one), ("m", Request("image", WORDS)), ("m", Request("video", WORDS, steps=4)),
            ("m", Request("image", WORDS, steps=4, seed=1)), ("m", one, "a-made-up-hash"),
        )}
        self.assertEqual(len(others), 6)

    # What nobody filmed.

    @needs_ffmpeg
    def test_a_cut_can_be_asked_which_of_its_clips_nobody_filmed(self):
        filmed = make_video(self.root / "footage" / "quay cam.mp4")
        ingest.ingest(filmed, "made-up-quay", settings=self.settings)
        (camera,), = self.query("SELECT id FROM media.assets WHERE path = %s", (str(filmed),))
        made = self.make(medium="video", seconds=1.0)
        timelines.save("made-up-tide", [
            Clip(camera, 0.2, 1.0), Clip(made.generation.asset_id, 0.0, 0.5),
            Clip(camera, 1.2, 2.0, sound=made.generation.asset_id, sound_start_s=0.0, sound_end_s=0.8),
        ], project="made-up-quay")
        timelines.save("made-up-ebb", [Clip(camera, 0.2, 1.0)], project="made-up-quay")
        found = generate.synthetic("made-up-tide")
        self.assertEqual([(one.number, one.track, one.file, one.model) for one in found],
                         [(2, "picture", made.file.name, "made-up-painter-1"),
                          (3, "sound", made.file.name, "made-up-painter-1")])
        self.assertEqual(generate.synthetic("made-up-ebb"), [])
        self.assertEqual({one.timeline for one in generate.synthetic()}, {"made-up-tide"})


class GenerateCommandsTest(DatabaseTest):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.painter, self.service = Painter(), Service()
        self.installed = adapters.get
        for patch in (
            mock.patch.dict(os.environ, {
                "CONSTRUCTIVE_MEDIA_HOME": str(self.root / "home"),
                "CONSTRUCTIVE_MEDIA_DATABASE_URL": os.environ[TEST_DATABASE_VARIABLE],
            }),
            mock.patch("constructive_media.generate.maker_for", self.maker_for),
            mock.patch("constructive_media.adapters.get", self.maker_for),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def maker_for(self, medium, name=None, *, settings=None):
        if medium not in generate.MEDIUMS:
            return self.installed(medium, name, settings=settings)
        if name in (None, "made-up"):
            return self.painter
        if name == "made-up-service":
            return self.service
        raise adapters.AdapterError(f"no {medium!r} adapter named {name!r}.")

    def test_generate_makes_it_once_and_says_where(self):
        status, out, err = test_cli.run("generate", "image", WORDS, "--steps", "6", "--size", "640x480", "--seed", "5")
        self.assertEqual((status, err), (0, ""))
        self.assertRegex(out, r"^made  .*/work/generated/a-made-up-harbour-at-dawn-[0-9a-f]{8}\.png\n"
                              r"  with made-up-painter-1 through made-up, in \d+ s\n$")
        asked = self.painter.asked[0]
        self.assertEqual((asked.steps, asked.width, asked.height, asked.seed), (6, 640, 480, 5))
        status, out, _ = test_cli.run("generate", "image", WORDS, "--steps", "6", "--size", "640x480", "--seed", "5")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"^had   .*\.png\n  made before with made-up-painter-1 through made-up; --again makes another\n$")
        self.assertEqual(len(self.painter.asked), 1)

    def test_a_dry_run_says_what_would_be_made(self):
        status, out, _ = test_cli.run("generate", "image", WORDS, "--dry-run")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"^would make .*\.png\n  with made-up-painter-1 through made-up\n$")
        self.assertEqual(self.painter.asked, [])

    def test_what_cannot_be_asked_for_is_one_line(self):
        self.assertEqual(test_cli.run("generate", "song", WORDS)[0], 2)
        self.assertEqual(test_cli.run("generate", "image", WORDS, "--size", "wide")[0], 2)
        status, out, err = test_cli.run("generate", "image", WORDS, "--seconds", "2")
        self.assertEqual((status, out, err), (1, "", "media: only video runs for a length of time\n"))
        status, _, err = test_cli.run("generate", "image", WORDS, "--adapter", "nobody")
        self.assertEqual((status, err), (1, "media: no 'image' adapter named 'nobody'.\n"))

    def test_generations_lists_them_and_adds_them_up(self):
        self.assertEqual(test_cli.run("generations")[1], "nothing has been asked for yet\n")
        self.assertEqual(test_cli.run("generations", "--spend")[1], "nothing has been asked for yet\n")
        test_cli.run("generate", "image", WORDS)
        self.painter.fails = "no paint"
        with self.assertRaises(RuntimeError):
            test_cli.run("generate", "image", "a made-up lighthouse")
        status, out, _ = test_cli.run("generations")
        self.assertEqual(status, 0)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"failed +image  made-up-painter-1 through made-up$")
        self.assertEqual(lines[1:3], ["      a made-up lighthouse", "      failed: no paint"])
        self.assertRegex(lines[3], r"succeeded  image  made-up-painter-1 through made-up$")
        self.assertRegex(lines[5], r"^      /.*\.png$")
        self.assertEqual(lines[-1], "2 requests")
        self.assertEqual(test_cli.run("generations", "--status", "failed")[1].splitlines()[-1], "1 request")
        self.assertEqual(test_cli.run("generations", "--medium", "video")[1], "nothing asked for matches\n")
        status, out, _ = test_cli.run("generations", "--spend")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"made-up-painter-1  made-up  failed +1 request +\$ +0\.00")
        self.assertRegex(out, r"made-up-painter-1  made-up  succeeded +1 request +\$ +0\.00")
        self.assertEqual(out.splitlines()[-1], "$0.00 in all")

    def test_collect_brings_in_what_a_service_made(self):
        self.assertEqual(test_cli.run("collect"), (0, "no service has been handed anything that is still to come\n", ""))
        status, out, _ = test_cli.run("generate", "image", WORDS, "--adapter", "made-up-service")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"^asked made-up-service-2 through made-up-service for a-made-up-harbour-at-dawn-\w+\.png\n"
                              r"  `media collect` brings it in when it is made\n$")
        status, out, _ = test_cli.run("collect")
        self.assertEqual((status, out), (0, f"  waiting  {WORDS}  (made-up-service)\n"))
        self.service.states["job-1"] = Job("succeeded")
        status, out, _ = test_cli.run("collect")
        self.assertEqual(status, 0)
        self.assertRegex(out, r"^  made     /.*\.png\n$")
        test_cli.run("generate", "image", "a made-up gull", "--adapter", "made-up-service")
        self.service.states["job-2"] = Job("failed", "out of ink")
        status, out, err = test_cli.run("collect")
        self.assertEqual((status, out, err), (1, "", "  failed   a made-up gull: out of ink\n"))

    def test_collect_can_wait_for_what_is_still_to_come(self):
        test_cli.run("generate", "image", WORDS, "--adapter", "made-up-service")
        test_cli.run("generate", "image", "a made-up gull", "--adapter", "made-up-service")
        asked = []

        def later(seconds):
            asked.append(seconds)
            # The first is made while the command waits; the second never is.
            self.service.states["job-1"] = Job("succeeded")

        with mock.patch("constructive_media.cli.generate.time.sleep", side_effect=later), \
                mock.patch("constructive_media.cli.generate.EVERY_S", 0.004):
            status, out, _ = test_cli.run("collect", "--wait", "0.02")
        self.assertEqual(status, 0)
        self.assertGreaterEqual(len(asked), 1)
        self.assertLessEqual(max(asked), 0.004)
        self.assertRegex(out, r"^  made     /.*\.png\n  waiting  a made-up gull  \(made-up-service\)\n$")
        # With nothing still to come there is nothing to wait for.
        self.service.states["job-2"] = Job("succeeded")
        with mock.patch("constructive_media.cli.generate.time.sleep", side_effect=AssertionError("it waited")):
            status, out, _ = test_cli.run("collect", "--wait", "60")
        self.assertEqual((status, len(out.splitlines())), (0, 1))
        self.assertRegex(out, r"^  made     /.*\.png\n$")

    @needs_ffmpeg
    def test_synthetic_returns_1_when_a_cut_holds_what_nobody_filmed(self):
        filmed = make_video(self.root / "footage" / "quay cam.mp4")
        ingest.ingest(filmed, "made-up-quay")
        (camera,), = self.query("SELECT id FROM media.assets WHERE path = %s", (str(filmed),))
        timelines.save("made-up-ebb", [Clip(camera, 0.2, 1.0)], project="made-up-quay")
        self.assertEqual(test_cli.run("synthetic"), (0, "no cut holds a clip a model made\n", ""))
        self.assertEqual(test_cli.run("synthetic", "made-up-ebb"), (0, "every clip of made-up-ebb was filmed\n", ""))
        test_cli.run("generate", "video", WORDS, "--seconds", "1")
        (made,), = self.query("SELECT output_asset_id FROM media_video.generations")
        timelines.save("made-up-tide", [Clip(camera, 0.2, 1.0), Clip(made, 0.0, 0.5)], project="made-up-quay")
        status, out, _ = test_cli.run("synthetic", "made-up-tide")
        self.assertEqual(status, 1)
        self.assertRegex(out, r"^  made-up-tide  clip   2  picture  a-made-up-harbour-at-dawn-\w+\.mp4  "
                              r"\(made-up-painter-1: a made-up harbour at dawn, fog over still water\)\n"
                              r"1 clip that nobody filmed\n$")
        self.assertEqual(test_cli.run("synthetic")[0], 1)
        status, _, err = test_cli.run("synthetic", "made-up-nowhere")
        self.assertEqual(status, 1)
        self.assertIn("no timeline is called 'made-up-nowhere'", err)

    def test_what_was_generated_is_a_kind_of_output(self):
        test_cli.run("generate", "image", WORDS)
        status, out, _ = test_cli.run("outputs", "--kind", "generated")
        self.assertEqual(status, 0)
        self.assertIn("a-made-up-harbour-at-dawn", out)
        self.assertNotIn("no record", out.split("a-made-up-harbour-at-dawn")[0])


class BuiltInMakersTest(unittest.TestCase):
    def test_pictures_and_video_each_have_a_maker_under_an_allowed_licence(self):
        registry = adapters.Registry()
        for adapter in adapters.builtins():
            registry.register(adapter, default=True)
        for slot, name in (("image", "mflux"), ("video", "diffusers")):
            found = registry.describe(slot)
            self.assertEqual(found.name, name)
            self.assertIn(found.license, PERMISSIVE)

    def test_an_unknown_medium_has_no_maker(self):
        with self.assertRaisesRegex(GenerateError, "unknown medium 'song'"):
            generate.maker_for("song")
