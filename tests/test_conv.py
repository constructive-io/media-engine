"""Reading `.conv` scripts, held to the format's own conformance files."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from constructive_media import conv
from constructive_media.conv import ConvError, Memory, Plugin, parse
from constructive_media.conv.schema import named, number, text
from constructive_media.conv.values import closest, seconds
from constructive_media.conv.values import number as read_number

from .test_cli import run

FIXTURES = Path(__file__).parent / "fixtures" / "conv-spec"

HEAD = ("@characters\nADA: { persona: ada }\n\n@sounds\nBED: { file: music/bed.mp3 }\n"
        "GLASS: { prompt: \"glass\", duration: 2 }\n---\n")
BANK = "@sounds\nBED: { file: a.mp3 }\nSTING: { file: s.mp3 }\n@tracks\nroom: { reverb: room }\n"


def banks():
    return Memory({path.stem: conv.read(path) for path in (FIXTURES / "banks").glob("*.sounds")})


def said(script, **options):
    """What a strict reading of a faulty script says."""
    try:
        parse(script, source="t.conv", **options)
    except ConvError as error:
        return error
    raise AssertionError("the script was read without a problem")


class Conformance(unittest.TestCase):
    def test_every_scene_parses_to_the_scene_the_format_gives(self):
        scripts = sorted((FIXTURES / "scenes").glob("*.conv"))
        self.assertGreaterEqual(len(scripts), 7)
        for script in scripts:
            with self.subTest(script.name):
                scene = parse(conv.read(script), source=script.name, banks=banks())
                wanted = json.loads(script.with_suffix(".json").read_text(encoding="utf-8"))
                self.assertEqual(json.loads(json.dumps(scene)), wanted)

    def test_every_faulty_script_reports_the_problems_the_format_gives(self):
        scripts = sorted((FIXTURES / "errors").glob("*.conv"))
        self.assertGreaterEqual(len(scripts), 4)
        for script in scripts:
            with self.subTest(script.name):
                scene = parse(conv.read(script), source=script.name, banks=banks(), strict=False)
                wanted = json.loads(script.with_name(script.stem + ".errors.json").read_text(encoding="utf-8"))
                self.assertTrue(scene["problems"])
                self.assertEqual(scene["problems"], wanted)

    def test_a_scene_is_plain_data_with_whole_numbers_whole(self):
        scene = parse(HEAD + "[pause 2]\n[pause 1.5]\n[GLASS vol:1.0 pan:-0.5]\n")
        self.assertEqual(json.dumps(scene["items"][0]), '{"type": "pause", "line": 8, "seconds": 2}')
        self.assertEqual(json.dumps(scene["items"][2]["overrides"]), '{"volume": 1, "pan": -0.5}')
        self.assertEqual(list(scene), ["characters", "sounds", "tracks", "items", "problems", "index"])


class Values(unittest.TestCase):
    def scene(self, body, head=""):
        return parse("@characters\nADA: { persona: ada }\n@sounds\nBED: { file: b.mp3 }\n" + head + "---\n" + body)

    def test_values_are_read_by_what_their_key_takes(self):
        scene = self.scene("[music BED start:1:02.5, for:20 hold]\n",
                           "@tracks\nroom: { duck-under: dialogue+music, loop: yes }\n")
        item = scene["items"][0]
        self.assertEqual((item["type"], item["action"]), ("track", "play"))
        self.assertEqual(item["overrides"], {"start": 62.5, "for": 20, "hold": True})
        self.assertEqual(scene["tracks"]["room"], {"duck_under": ["dialogue", "music"], "loop": True})

    def test_at_is_here_in_the_flow_or_an_offset_or_from_the_start(self):
        scene = self.scene("[BED]\n[BED at:+2]\n[BED at:18.5]\n[BED at:-1]\n")
        self.assertEqual([item["overrides"].get("at") for item in scene["items"]], [
            None, {"mode": "offset", "seconds": 2}, {"mode": "absolute", "seconds": 18.5},
            {"mode": "offset", "seconds": -1},
        ])

    def test_commas_inside_quotes_stay_in_the_value(self):
        scene = parse('@sounds\nCITY: { prompt: "street, cars, horns", duration: 4 }\n---\n')
        self.assertEqual(scene["sounds"]["CITY"], {"prompt": "street, cars, horns", "duration": 4})

    def test_voice_and_speaker_are_other_words_for_the_persona(self):
        scene = parse("@characters\nBOB: { voice: Bob }\nCY: { speaker: Cy, persona: other }\n---\nBOB: hi\n")
        self.assertEqual(scene["characters"]["BOB"], {"voice": "Bob", "persona": "bob"})
        self.assertEqual(scene["characters"]["CY"]["persona"], "other")

    def test_a_bare_cue_plays_on_the_track_its_sound_names(self):
        scene = parse("@sounds\nRAIN: { file: r.wav, track: ambience }\nDOOR: { file: d.wav }\n---\n[RAIN]\n[DOOR]\n")
        self.assertEqual([item["track"] for item in scene["items"]], ["ambience", "foreground"])

    def test_other_spellings_of_a_key_are_one_key(self):
        scene = self.scene("[BED vol:0.5 fadein:1 fade-out:2 dur:3 from:0:02 length:4 gain:2]\n")
        self.assertEqual(scene["items"][0]["overrides"],
                         {"volume": 2, "fade_in": 1, "fade_out": 2, "duration": 3, "start": 2, "for": 4})

    def test_numbers_are_read_as_the_format_writes_them(self):
        for written, read in (("5", 5), ("5.", 5), (".5", 0.5), ("+2", 2), ("-0.25", -0.25), ("1e3", 1000),
                              (" 7 ", 7), ("0x10", 16)):
            self.assertEqual(read_number(written), read, written)
        for written in ("", ".", "1.2.3", "1_000", "inf", "Infinity", "nan", "-0x10", "five", "1e999", "٥"):
            self.assertIsNone(read_number(written), written)
        self.assertEqual(seconds("1:02.5"), 62.5)
        self.assertEqual(seconds("1:00:00"), 3600)
        self.assertIsNone(seconds("1::5"))
        self.assertIsNone(seconds("soon"))

    def test_the_closest_word_is_the_one_suggested(self):
        self.assertEqual(closest("fade_ot", ["fade_in", "fade_out", "for"]), "fade_out")
        self.assertEqual(closest("glas", ["GLASS", "glass"]), "glass")
        self.assertIsNone(closest("zebra", ["fade_in", "pan"]))
        self.assertEqual(conv.similarity("", ""), 1)
        self.assertEqual(conv.similarity("abcd", "bcde"), 0.75)


class Lines(unittest.TestCase):
    def segments(self, words):
        return parse(HEAD + f"ADA: {words}\n")["items"][0]["segments"]

    def test_a_line_with_no_markers_is_one_piece(self):
        self.assertEqual(self.segments("Just words, nothing else."),
                         [{"type": "text", "text": "Just words, nothing else.", "overrides": {}}])

    def test_punctuation_left_alone_joins_the_span_before_it(self):
        self.assertEqual(self.segments("{firm: we need to talk}."),
                         [{"type": "text", "text": "we need to talk.", "overrides": {"emotion": "firm"}}])

    def test_breaks_spans_and_sounds_come_out_in_order(self):
        self.assertEqual(self.segments("{whisper, reverb:cathedral: come here} <sfx GLASS vol:0.5> so <0.4> {seed:7: now}"), [
            {"type": "text", "text": "come here", "overrides": {"reverb": "cathedral", "emotion": "whisper"}},
            {"type": "sfx", "sound": "GLASS", "overrides": {"volume": 0.5}},
            {"type": "text", "text": "so", "overrides": {}},
            {"type": "break", "seconds": 0.4},
            {"type": "text", "text": "now", "overrides": {"seed": 7}},
        ])

    def test_braces_that_are_not_a_span_are_words(self):
        self.assertEqual(self.segments("plain {not a span} text"),
                         [{"type": "text", "text": "plain {not a span} text", "overrides": {}}])

    def test_seconds_that_cannot_be_read_are_a_problem(self):
        self.assertIn("`<break 1.2.3>` needs seconds", str(said(HEAD + "ADA: one <break 1.2.3> two\n")))
        self.assertIn("`[pause N]` needs seconds", str(said("[pause 1.2.3]\n")))
        self.assertIn("`[pause N]` needs seconds", str(said("[pause]\n")))


class Problems(unittest.TestCase):
    """Each test pins the words, because the words are the point."""

    def test_every_problem_is_reported_at_once_with_its_line(self):
        error = said(HEAD + "ADA: hi\n[music BED fade-ot:3]\n[GLASS vol:loud]\n")
        self.assertEqual([problem["line"] for problem in error.problems], [9, 10])
        self.assertIn("t.conv: 2 problems", str(error))
        self.assertIn("line 9: [music BED fade-ot:3]", str(error))
        self.assertEqual(error.source, "t.conv")

    def test_a_report_is_laid_out_by_line(self):
        self.assertEqual(conv.report([
            {"line": 3, "text": "[x]", "message": "second\nmore"},
            {"line": None, "text": None, "message": "last"},
            {"line": 1, "text": "@y", "message": "first"},
        ]), ".conv script: 3 problems\n\n  line 1: @y\n    first\n\n  line 3: [x]\n    second\n      more\n\n"
            "  (script)\n    last")
        self.assertTrue(conv.report([{"line": 1, "text": "x", "message": "m"}], "a.conv").startswith("a.conv: 1 problem\n"))

    def test_a_mistyped_key_is_met_with_the_one_meant(self):
        message = str(said(HEAD + "[music BED fade-ot:3]\n"))
        self.assertIn("unknown key `fade-ot`", message)
        self.assertIn("Did you mean `fade-out`?", message)
        self.assertIn("Valid keys: at, duration, fade-in, fade-out", message)

    def test_values_that_are_not_numbers_seeds_or_times(self):
        self.assertIn("`vol:loud` — vol must be a number", str(said(HEAD + "[GLASS vol:loud]\n")))
        self.assertIn("`seed:abc` — a seed must be a whole number", str(said(HEAD + "ADA[seed:abc]: hi\n")))
        self.assertIn("seconds or m:ss", str(said(HEAD + "[music BED start:soon]\n")))
        self.assertIn("use true or false", str(said(HEAD + "[GLASS hold:maybe]\n")))
        self.assertIn("at must be seconds", str(said(HEAD + "[GLASS at:later]\n")))

    def test_values_out_of_range(self):
        self.assertIn("out of range; use -1 to 1", str(said(HEAD + "[GLASS pan:3]\n")))
        self.assertIn("out of range; use 0.05 to 3600", str(said(HEAD + "[GLASS dur:0]\n")))

    def test_a_sound_nobody_declared_is_named_with_a_suggestion(self):
        message = str(said(HEAD + "[GLAS]\nADA: and <sfx GLAS> again\n"))
        self.assertIn("sound `GLAS` isn't declared under @sounds", message)
        self.assertIn("Did you mean `GLASS`?", message)
        self.assertIn("t.conv: 2 problems", message)

    def test_an_unknown_reverb_is_not_quietly_made_a_default(self):
        message = str(said(HEAD + "ADA[reverb:haul]: hi\n"))
        self.assertIn("not one of: room, hall, cathedral, plate", message)
        self.assertIn("Did you mean `hall`?", message)

    def test_a_line_has_one_emotion(self):
        self.assertIn("names 2 emotions (happy, sad)", str(said(HEAD + "ADA[happy, sad]: hi\n")))

    def test_the_old_forms_are_explained(self):
        message = str(said("@characters\nHOST: ivo\n\nHOST: hello\npause 0.5\n"))
        self.assertIn("`HOST: { persona: ivo }`", message)
        self.assertIn("`[pause 0.5]`", message)

    def test_case_and_hyphens_in_names_are_hinted_at(self):
        self.assertIn("UPPERCASE: `ADA: hi", str(said(HEAD + "ada: hi\n")))
        self.assertIn("not -: `MARY_JANE", str(said(HEAD + "MARY-JANE: hi\n")))
        self.assertIn("sound names are UPPERCASE: `[GLASS vol:1]`", str(said(HEAD + "[glass vol:1]\n")))

    def test_a_sound_has_exactly_one_source(self):
        self.assertIn("needs a source", str(said("@sounds\nX: { duration: 2 }\n---\n")))
        self.assertIn("both `file:` and `prompt:`", str(said('@sounds\nX: { file: a.wav, prompt: "x" }\n---\n')))

    def test_the_persona_stays_on_the_character(self):
        self.assertIn("can't be changed per line", str(said(HEAD + "ADA[persona:bob]: hi\n")))

    def test_only_the_volume_moves_mid_scene(self):
        message = str(said(HEAD + "ADA: hi\n[music reverb:hall]\n"))
        self.assertIn("can't change mid-scene", message)
        self.assertIn("`music: { reverb: hall }`", message)

    def test_any_track_setting_is_allowed_before_the_first_line(self):
        scene = parse(HEAD + "[music reverb:hall]\nADA: hi\n")
        self.assertEqual(scene["items"][0]["overrides"], {"reverb": "hall"})

    def test_unknown_tracks_and_headers(self):
        self.assertIn("unknown track `musik`. Did you mean `music`?", str(said(HEAD + "[musik BED]\n")))
        self.assertIn("Did you mean `@characters`?", str(said("@charactrs\n")))
        self.assertIn("`[music]` does nothing", str(said(HEAD + "[music]\n")))

    def test_every_key_of_a_declaration_needs_a_value(self):
        self.assertIn("`compress` needs a value", str(said("@characters\nADA: { persona: ada, compress }\n")))

    def test_without_strict_the_problems_come_back_in_the_scene(self):
        scene = parse(HEAD + "[GLAS]\n", strict=False)
        self.assertEqual(len(scene["problems"]), 1)
        self.assertEqual(scene["problems"][0]["line"], 8)
        self.assertEqual(parse(HEAD + "ADA: hi\n")["problems"], [])


class Plugins(unittest.TestCase):
    def test_a_directive_makes_a_mark(self):
        def chapter(title, wrong):
            if not title:
                return wrong("`[chapter]` needs a title")
            return {"title": title}

        chapters = Plugin(name="chapters", directives={"chapter": chapter})
        scene = parse("[chapter The Attic]\n", plugins=[*conv.defaults(), chapters])
        self.assertEqual(scene["items"], [{"type": "mark", "line": 1, "kind": "chapter", "data": {"title": "The Attic"}}])
        self.assertIn("needs a title", str(said("[chapter]\n", plugins=[chapters])))

    def test_a_directive_name_cannot_name_a_track(self):
        plugin = Plugin(name="p", directives={"cue": lambda said, wrong: {}})
        self.assertIn("reserved word", str(said("@tracks\ncue: { level: -20 }\n", plugins=[plugin])))

    def test_keys_added_to_a_context_are_checked_like_any_other(self):
        rendering = Plugin(name="rendering", keys={"line": {"engine": text(), "rate": number(0.5, 2)}})
        script = "@characters\nADA: { persona: ada }\n---\nADA[rate:0.9, engine:fast]: hi\n"
        scene = parse(script, plugins=[*conv.defaults(), rendering])
        self.assertEqual(scene["items"][0]["overrides"], {"rate": 0.9, "engine": "fast"})
        self.assertIn("out of range; use 0.5 to 2", str(said(script.replace("0.9", "9"), plugins=[rendering])))

    def test_plugins_add_to_a_named_list(self):
        spring = Plugin(name="spring", enums={"reverb": ["spring"]})
        script = "@characters\nADA: { persona: ada, reverb: spring }\n"
        self.assertIn("not one of", str(said(script)))
        self.assertEqual(parse(script, plugins=[*conv.defaults(), spring])["characters"]["ADA"]["reverb"], "spring")

    def test_the_rooms_a_renderer_offers_can_be_set(self):
        scene = parse("@tracks\nhall2: { reverb: vault }\n", plugins=[conv.effects(reverbs=["vault"])])
        self.assertEqual(scene["tracks"]["hall2"]["reverb"], "vault")

    def test_a_track_can_come_with_its_own_rules(self):
        def ringing(item, wrong):
            if item["action"] == "play" and not item["overrides"].get("ring"):
                wrong("a phone play needs `ring:`")

        phone = Plugin(name="phone", tracks=["phone"], keys={"play": {"ring": named("rings")}},
                       enums={"rings": ["short", "long"]}, track_rules={"phone": ringing})
        script = "@sounds\nRING: { file: r.wav }\n---\n"
        item = parse(script + "[phone RING ring:long]\n", plugins=[phone])["items"][0]
        self.assertEqual((item["track"], item["overrides"]), ("phone", {"ring": "long"}))
        self.assertIn("needs `ring:`", str(said(script + "[phone RING]\n", plugins=[phone])))

    def test_a_pass_over_the_finished_scene_can_report(self):
        def short_pauses(scene, problem):
            for item in scene["items"]:
                if item["type"] == "pause" and item["seconds"] > 5:
                    problem(item, "pauses over 5 s lose the listener")

        message = str(said("[pause 9]\n", plugins=[Plugin(name="short-pauses", finalize=short_pauses)]))
        self.assertIn("line 1: [pause 9]", message)
        self.assertIn("lose the listener", message)

    def test_two_plugins_cannot_claim_one_directive(self):
        first = Plugin(name="a", directives={"x": lambda said, wrong: {}})
        second = Plugin(name="b", directives={"x": lambda said, wrong: {}})
        with self.assertRaisesRegex(ValueError, '"a" and "b" both define'):
            parse("", plugins=[first, second])
        with self.assertRaisesRegex(ValueError, "already a track"):
            parse("", plugins=[Plugin(name="c", directives={"music": lambda said, wrong: {}})])

    def test_the_grammar_stands_with_no_plugins(self):
        self.assertIn("unknown track `slide`", str(said("[slide intro]\n", plugins=[])))
        scene = parse("@characters\nADA: { persona: ada }\n---\nADA: hi\n", plugins=[])
        self.assertEqual(len(scene["items"]), 1)

    def test_slides(self):
        scene = parse("[slide intro]\n[slide market-size step:2]\n")
        self.assertEqual([item["data"] for item in scene["items"]],
                         [{"id": "intro", "step": 0}, {"id": "market-size", "step": 2}])
        self.assertIn("needs the slide's id", str(said("[slide]\n")))
        self.assertIn("a slide id is lowercase", str(said("[slide Intro]\n")))
        self.assertIn("one slide per marker", str(said("[slide a b]\n")))
        self.assertIn("isn't a slide option", str(said("[slide a stp:1]\n")))
        self.assertIn("step is a whole number", str(said("[slide a step:x]\n")))

    def test_speakers(self):
        def show(words):
            return parse(f"[speakers {words}]\n")["items"][0]["data"]["show"]

        self.assertEqual(show("captions faces"), ["faces", "captions"])
        self.assertEqual(show("all"), ["faces", "lower-third", "waveform", "captions"])
        self.assertEqual(show("off"), [])
        self.assertEqual(show("faces:talking lower-third:always faces lower-third"),
                         ["lower-third:always", "faces:talking"])
        self.assertEqual(show("FACES:still+lower-third:first"), ["faces", "lower-third"])
        self.assertIn("needs what to show", str(said("[speakers]\n")))
        self.assertIn("`face` isn't a speaker widget. Did you mean `faces`?", str(said("[speakers face]\n")))
        self.assertIn("stand alone", str(said("[speakers off faces]\n")))
        named_ = parse('@characters\nADA: { persona: ada, name: "Ada, of the Hill", title: Keeper }\n')
        self.assertEqual(named_["characters"]["ADA"]["name"], "Ada, of the Hill")

    def test_video(self):
        head = "@sounds\nCLIP: { file: quay.mp4 }\nMADE: { prompt: waves }\n---\n"
        item = parse(head + "[video CLIP start:2, for:4]\n")["items"][0]
        self.assertEqual(item["overrides"], {"start": 2, "for": 4, "hold": True})
        self.assertIn("plays one clip", str(said(head + "[video off]\n")))
        self.assertIn("drop `at:`", str(said(head + "[video CLIP at:2]\n")))
        self.assertIn("`pan` doesn't apply to a video clip", str(said(head + "[video CLIP pan:1]\n")))
        self.assertIn("needs a video file", str(said(head + "[video MADE]\n")))
        self.assertIn("reserved word", str(said("@tracks\nslide: { level: -3 }\n")))


class Includes(unittest.TestCase):
    def test_a_bank_brings_sounds_and_tracks_and_the_script_wins(self):
        scene = parse("@include bank\n@sounds\nBED: { file: mine.mp3 }\n---\n[music BED]\n[STING]\n",
                      banks=Memory({"bank": BANK}))
        self.assertEqual(scene["sounds"]["BED"]["file"], "mine.mp3")
        self.assertEqual(scene["sounds"]["STING"]["file"], "s.mp3")
        self.assertEqual(scene["tracks"]["room"], {"reverb": "room"})
        self.assertEqual(scene["index"]["sound"]["STING"], {"line": 1, "include": "bank"})
        self.assertEqual(scene["index"]["sound"]["BED"], {"line": 3})

    def test_two_banks_with_one_name_clash_unless_the_script_settles_it(self):
        finder = Memory({"a": "@sounds\nX: { file: a }\n", "b": "@sounds\nX: { file: b }\n"})
        self.assertIn("comes from both `a` and `b`", str(said("@include a\n@include b\n---\n", banks=finder)))
        scene = parse("@include a\n@include b\n@sounds\nX: { file: c }\n---\n", banks=finder)
        self.assertEqual(scene["sounds"]["X"], {"file": "c"})

    def test_a_missing_bank_is_reported_with_a_suggestion(self):
        message = str(said("@include podcats\n---\n", banks=Memory({"podcast": BANK})))
        self.assertIn("can't find `podcats` — looked for podcats. Did you mean `podcast`?", message)
        self.assertIn("Banks: podcast", message)
        self.assertIn("needs a bank", str(said("@include\n---\n", banks=Memory({"podcast": BANK}))))

    def test_a_circle_of_includes_is_reported(self):
        finder = Memory({"a": "@include b\n", "b": "@include a\n"})
        self.assertIn("goes round in a circle", str(said("@include a\n---\n", banks=finder)))

    def test_a_bank_within_a_bank_is_read(self):
        finder = Memory({"outer": "@include inner\n@sounds\nA: { file: a }\n", "inner": "@sounds\nB: { file: b }\n"})
        self.assertEqual(list(parse("@include outer\n---\n[A]\n[B]\n", banks=finder)["sounds"]), ["B", "A"])

    def test_characters_and_dialogue_stay_out_of_banks(self):
        finder = Memory({"bad": "@characters\nADA: { persona: ada }\n"})
        self.assertIn("has problems — fix them there", str(said("@include bad\n---\n", banks=finder)))

    def test_an_include_needs_somewhere_to_look(self):
        self.assertIn("no include resolver", str(said("@include bank\n---\n")))

    def test_an_include_belongs_in_the_header(self):
        self.assertIn("belongs in the header", str(said("---\n@include bank\n", banks=Memory({"bank": BANK}))))


class OnDisk(unittest.TestCase):
    def setUp(self):
        self.finder = conv.Files(banks=[FIXTURES / "banks"], roots=[FIXTURES])

    def test_a_bank_is_read_by_name(self):
        self.assertIn("@sounds", self.finder.find("studio").text)

    def test_a_path_is_looked_for_beside_the_file_that_includes_it(self):
        found = self.finder.find("../banks/other.sounds", str(FIXTURES / "scenes" / "include.conv"))
        self.assertEqual(found.id, os.path.realpath(FIXTURES / "banks" / "other.sounds"))
        self.assertEqual(self.finder.find("banks/other.sounds").id, found.id)

    def test_it_says_where_it_looked(self):
        missing = self.finder.find("missing")
        self.assertEqual(missing.tried, (os.path.abspath(FIXTURES / "banks" / "missing.sounds"),))

    def test_banks_are_listed(self):
        self.assertEqual(self.finder.names(), ["other", "studio"])
        self.assertEqual(conv.Files(banks=[FIXTURES / "nowhere"]).names(), [])

    def test_a_script_is_loaded_with_the_banks_it_names(self):
        scene = conv.load(FIXTURES / "scenes" / "include.conv", banks=[FIXTURES / "banks"])
        wanted = json.loads((FIXTURES / "scenes" / "include.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(scene)), wanted)

    def test_a_banks_folder_beside_the_script_is_found_without_being_named(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "banks").mkdir()
            (Path(folder) / "banks" / "harbour.sounds").write_text("@sounds\nGULL: { file: gull.wav }\n", encoding="utf-8")
            script = Path(folder) / "quay.conv"
            script.write_bytes(b"@include harbour\r\n@characters\r\nADA: { persona: ada }\r\n---\r\n[GULL]\r\nADA: Listen.\r\n")
            scene = conv.load(script)
            self.assertEqual(scene["index"]["sound"]["GULL"], {"line": 1, "include": "harbour"})
            self.assertEqual(scene["items"][1]["text"], "Listen.")


class Commands(unittest.TestCase):
    def test_clean_scripts_pass(self):
        status, out, err = run("conv", str(FIXTURES / "scenes" / "basic.conv"), str(FIXTURES / "scenes" / "tracks.conv"))
        self.assertEqual((status, err), (0, ""))
        self.assertIn("2 ok, 0 with problems", out)

    def test_banks_are_found_where_they_are_said_to_be(self):
        script = str(FIXTURES / "scenes" / "include.conv")
        self.assertEqual(run("conv", script, "--banks", str(FIXTURES / "banks"))[0], 0)
        status, _, err = run("conv", script, "--banks", str(FIXTURES / "scenes"))
        self.assertEqual(status, 1)
        self.assertIn("can't find `studio`", err)

    def test_every_problem_is_reported_and_the_status_says_so(self):
        script = str(FIXTURES / "errors" / "typos.conv")
        status, out, err = run("conv", script)
        self.assertEqual((status, out), (1, ""))
        self.assertIn(f"{script}: 5 problems", err)
        self.assertIn("Did you mean `fade-out`?", err)

    def test_a_missing_file_is_said_to_be_missing(self):
        status, out, err = run("conv", "nowhere.conv", str(FIXTURES / "scenes" / "basic.conv"))
        self.assertEqual(status, 1)
        self.assertIn("nowhere.conv: no such file", err)
        self.assertIn("1 ok, 1 with problems", out)

    def test_a_scene_is_printed_as_json(self):
        script = str(FIXTURES / "scenes" / "basic.conv")
        status, out, _ = run("conv", script, "--json")
        self.assertEqual(status, 0)
        wanted = json.loads((FIXTURES / "scenes" / "basic.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(out), wanted)
        self.assertGreater(out.count("\n"), 10)
        compact = run("conv", script, "--json", "--compact")[1]
        self.assertEqual(compact.count("\n"), 1)
        self.assertEqual(json.loads(compact), wanted)
        self.assertEqual(run("conv", script, script, "--json")[0], 2)


if __name__ == "__main__":
    unittest.main()
