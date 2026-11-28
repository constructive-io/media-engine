"""Video made through diffusers, with a stand-in for the model.

No model is loaded and neither PyTorch nor diffusers is needed: the adapter
is handed stand-ins for both, which keep what they were asked and answer with
frames of flat colour.
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import ffmpeg
from constructive_media.generate import GenerateError, Request
from constructive_media.generate import diffusers as filmed

from .support import HAVE_NUMPY, needs_ffmpeg, needs_numpy, settings_in

if HAVE_NUMPY:
    import numpy as np

WORDS = "fog lifting off a made-up harbour"


class Numbers:
    """A stand-in for what a model made: it knows where it is and how exact, and nothing else."""

    def __init__(self, device, dtype, name="numbers"):
        self.device, self.dtype, self.name = device, dtype, name

    def to(self, device, dtype):
        return Numbers(device, dtype, self.name)

    def __eq__(self, other):
        return (self.device, self.dtype, self.name) == (other.device, other.dtype, other.name)

    def __repr__(self):
        return f"{self.name} on {self.device} as {self.dtype}"


class Scheduler:
    """A stand-in for what goes between a model's passes: it keeps what each step was handed."""

    def __init__(self):
        self.stepped = []

    def step(self, model_output, timestep, sample, return_dict=True):
        self.stepped.append((model_output, timestep, sample))
        after = Numbers(sample.device, sample.dtype, "the next")
        return (after, "kept") if not return_dict else types.SimpleNamespace(prev_sample=after)


class Pipeline:
    """A stand-in for a video model: it answers with frames of one colour, and keeps what it was asked."""

    def __init__(self, loaded):
        self.loaded, self.asked, self.device, self.fails, self.empty = loaded, [], None, None, False
        self.scheduler = Scheduler()

    def to(self, device):
        self.device = device
        return self

    def set_progress_bar_config(self, **how):
        self.quiet = how

    def __call__(self, **asked):
        self.asked.append(asked)
        if self.fails:
            raise RuntimeError(self.fails)
        count = 0 if self.empty else asked["num_frames"]
        frames = np.full((count, asked["height"], asked["width"], 3), 0.5, dtype=np.float32)
        frames[..., 0] = 1.5       # past what a picture can hold: the adapter keeps it in range
        return types.SimpleNamespace(frames=[frames])


def stand_ins(have=("mps",), broken=None):
    """What the adapter uses of `torch` and `diffusers`."""
    loads = []

    class Generator:
        def __init__(self, device=None):
            self.device, self.seed = device, None

        def manual_seed(self, seed):
            self.seed = seed
            return self

    torch = types.ModuleType("torch")
    torch.float32, torch.bfloat16, torch.Generator = "float32", "bfloat16", Generator
    torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: "mps" in have))
    torch.cuda = types.SimpleNamespace(is_available=lambda: "cuda" in have)

    def decoder(model, **how):
        if broken:
            raise OSError(broken)
        loads.append(("decoder", model, how))
        return "the decoder"

    def pipeline(model, **how):
        loads.append(("pipeline", model, how))
        return Pipeline(loads)

    diffusers = types.ModuleType("diffusers")
    diffusers.AutoencoderKLWan = types.SimpleNamespace(from_pretrained=decoder)
    diffusers.WanPipeline = types.SimpleNamespace(from_pretrained=pipeline)
    return {"torch": torch, "diffusers": diffusers}, loads


@needs_numpy
@needs_ffmpeg
class DiffusersVideoTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve()
        self.target = self.folder / "work" / "harbour.mp4"
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.loads = self.install()

    def install(self, **how):
        modules, loads = stand_ins(**how)
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        return loads

    def maker(self, extra=None, **changes):
        if extra is not None:
            changes["extras"] = {"video": extra}
        return filmed.DiffusersVideo(settings=settings_in(self.folder / "home", **changes))

    def test_a_piece_is_made_with_the_defaults_and_plays(self):
        maker = self.maker()
        self.assertEqual((maker.name, maker.model), ("diffusers", "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"))
        made = maker.make(Request("video", WORDS), self.target)
        self.assertEqual(made, self.target)
        _, pipeline = maker.loaded()
        (asked,) = pipeline.asked
        self.assertEqual(asked, {
            "prompt": WORDS, "negative_prompt": "blurry, low quality, distorted", "height": 480, "width": 832,
            "num_frames": 33, "num_inference_steps": 15, "guidance_scale": 5.0, "output_type": "np",
        })
        described = ffmpeg.probe(made)
        self.assertEqual((described.medium, described.width, described.height, round(described.fps)),
                         ("video", 832, 480, 16))
        self.assertAlmostEqual(described.duration_s, 33 / 16, delta=0.1)
        self.assertFalse(described.has_audio)
        self.assertTrue((self.folder / "home" / "locks" / "mlx.lock").is_file())
        self.assertEqual(os.environ["PYTORCH_ENABLE_MPS_FALLBACK"], "1")

    def test_what_is_asked_for_is_what_the_model_is_given(self):
        maker = self.maker()
        maker.make(Request("video", WORDS, width=320, height=176, seconds=1.0, steps=4, seed=7), self.target)
        (asked,) = maker.loaded()[1].asked
        self.assertEqual((asked["width"], asked["height"], asked["num_frames"], asked["num_inference_steps"]),
                         (320, 176, 17, 4))
        self.assertEqual((asked["generator"].seed, asked["generator"].device), (7, "cpu"))
        described = ffmpeg.probe(self.target)
        self.assertEqual((described.width, described.height), (320, 176))

    def test_a_piece_is_as_many_frames_as_the_model_can_write(self):
        self.assertEqual([filmed.frames_for(*one) for one in ((2, 16), (1, 16), (0.1, 16), (5, 16), (3, 24), (2.1, 16))],
                         [33, 17, 9, 81, 73, 33])

    def test_the_model_and_the_options_come_from_settings(self):
        maker = self.maker('{"steps": 6, "fps": 8, "guidance": 3, "negative": ""}', models={"video": "made-up/film"})
        maker.make(Request("video", WORDS, width=160, height=96, seconds=1.0), self.target)
        (asked,) = maker.loaded()[1].asked
        self.assertEqual((asked["num_inference_steps"], asked["num_frames"], asked["guidance_scale"], asked["negative_prompt"]),
                         (6, 9, 3.0, None))
        self.assertEqual([one[1] for one in self.loads], ["made-up/film", "made-up/film"])
        self.assertEqual(round(ffmpeg.probe(self.target).fps), 8)

    def test_they_are_its_own_when_it_is_chosen_by_name_as_well(self):
        maker = self.maker('{"steps": 6}', models={"video": "made-up/film"}, adapters={"video": "diffusers"})
        self.assertEqual((maker.model, maker.options["steps"]), ("made-up/film", 6))

    def test_with_another_adapter_chosen_the_settings_are_that_one_s(self):
        # A plugin chosen for video has a model and options of its own: asked for by name, this runs on its defaults.
        maker = self.maker('{"steps": 25, "rough": 12}', models={"video": "made-up/another-kind"},
                           adapters={"video": "another"})
        self.assertEqual((maker.model, maker.options), ("Wan-AI/Wan2.1-T2V-1.3B-Diffusers", filmed.DEFAULTS))

    def test_the_model_is_loaded_once_on_the_best_there_is(self):
        maker = self.maker()
        for name in ("one.mp4", "two.mp4"):
            maker.make(Request("video", WORDS, width=160, height=96, seconds=0.5), self.folder / name)
        (decoder, _, how), (pipeline, _, held) = self.loads
        self.assertEqual((decoder, how), ("decoder", {"subfolder": "vae", "torch_dtype": "float32"}))
        self.assertEqual((pipeline, held), ("pipeline", {"vae": "the decoder", "torch_dtype": "bfloat16"}))
        self.assertEqual((maker.loaded()[1].device, maker.loaded()[1].quiet), ("mps", {"disable": True}))

    def test_what_it_runs_on_is_what_was_asked_for_or_what_there_is(self):
        for have, wanted, device, kind in ((("mps", "cuda"), "auto", "mps", "bfloat16"), (("cuda",), "auto", "cuda", "bfloat16"),
                                           ((), "auto", "cpu", "float32"), (("mps",), "cpu", "cpu", "float32")):
            with self.subTest(have=have, wanted=wanted):
                loads = self.install(have=have)
                maker = self.maker(device=wanted)
                self.assertEqual(maker.loaded()[1].device, device)
                self.assertEqual(loads[-1][2]["torch_dtype"], kind)
        self.install(have=())
        with self.assertRaisesRegex(GenerateError, "cannot use 'cuda' on this machine"):
            self.maker(device="cuda").loaded()
        with self.assertRaisesRegex(GenerateError, "runs on one of mps, cuda, cpu, not on 'abacus'"):
            self.maker(device="abacus").loaded()

    def test_between_passes_the_arithmetic_is_done_on_the_processor_where_the_graphics_processor_gets_it_wrong(self):
        made, at = Numbers("mps", "bfloat16", "made"), Numbers("mps", "bfloat16", "at")
        for device, handed in (("mps", ("cpu", "float32")), ("cuda", ("mps", "bfloat16"))):
            with self.subTest(device):
                self.install(have=("mps", "cuda"))
                _, pipeline = self.maker(device=device).loaded()
                for return_dict in (False, True):
                    out = pipeline.scheduler.step(made, 857, at, return_dict=return_dict)
                    after = out[0] if not return_dict else out.prev_sample
                    self.assertEqual(after, Numbers("mps", "bfloat16", "the next"))
                self.assertEqual(pipeline.scheduler.stepped[0],
                                 (Numbers(*handed, "made"), 857, Numbers(*handed, "at")))
                self.assertEqual(pipeline.scheduler.step(made, 857, at, return_dict=False)[1], "kept")

    def test_what_it_cannot_make_is_refused_before_a_model_is_loaded(self):
        maker = self.maker()
        start = self.folder / "quay.png"
        for request, said in (
            (Request("image", WORDS), "makes video, not pictures"),
            (Request("video", WORDS, image=start), "starts from words, not from a picture; the ltx adapter"),
            (Request("video", WORDS, width=830), "830x480 cannot be made.*such as 832x480"),
        ):
            with self.subTest(said), self.assertRaisesRegex(GenerateError, said):
                maker.make(request, self.target)
        self.assertEqual(self.loads, [])

    def test_without_the_packages_it_says_what_to_install(self):
        with mock.patch.dict(sys.modules, {"torch": None, "diffusers": None}), \
                self.assertRaisesRegex(GenerateError, "pip install torch diffusers transformers ftfy"):
            self.maker().make(Request("video", WORDS), self.target)

    def test_a_model_that_fails_is_an_error_of_making(self):
        self.install(broken="no made-up model in that folder")
        with self.assertRaisesRegex(GenerateError, "could not be loaded: no made-up model in that folder"):
            self.maker().make(Request("video", WORDS), self.target)
        self.install()
        maker = self.maker()
        maker.loaded()[1].fails = "the made-up model ran out of memory"
        with self.assertRaisesRegex(GenerateError, "could not make the piece: the made-up model ran out of memory"):
            maker.make(Request("video", WORDS), self.target)
        maker.loaded()[1].fails, maker.loaded()[1].empty = None, True
        with self.assertRaisesRegex(GenerateError, "nothing to show"):
            maker.make(Request("video", WORDS), self.target)
        self.assertFalse(self.target.exists())

    def test_options_it_does_not_know_are_refused(self):
        for extra, said in (("{", "is not JSON"), ("[4]", "should be a JSON object"),
                            ('{"paint": "oil"}', "has paint, which the diffusers adapter does not know")):
            with self.subTest(extra), self.assertRaisesRegex(GenerateError, said):
                self.maker(extra)


if __name__ == "__main__":
    unittest.main()
