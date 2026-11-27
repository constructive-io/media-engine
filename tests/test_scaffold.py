import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from constructive_media import cli, scaffold
from constructive_media.cli import init
from constructive_media.scaffold import Question, ScaffoldError
from constructive_media.settings import Settings

from .test_libraries import HAVE_GIT, git


def run(*arguments):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            status = cli.main(list(arguments))
        except SystemExit as stopped:
            status = stopped.code
    return status, out.getvalue(), err.getvalue()


def write(folder: Path, files: dict) -> Path:
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text if isinstance(text, str) else json.dumps(text), encoding="utf-8")
    return folder


#: Not text: a picture's first bytes. Read as text and written back, it would not be the same file.
PICTURE = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x00"

WORKSPACE = {
    ".boilerplate.json": {"type": "workspace", "questions": [
        {"name": "____fullName____", "message": "Enter author full name", "defaultFrom": "git.user.name", "required": True},
        {"name": "____email____", "message": "Enter author email", "defaultFrom": "git.user.email", "required": True},
        {"name": "____repoName____", "message": "Enter the repository name", "defaultFrom": "workspace.dirname",
         "required": True},
        {"name": "____license____", "message": "Choose a license", "type": "list", "optionsFrom": "licenses",
         "default": "MIT", "required": True},
    ]},
    "package.json": '{"name": "____repoName____", "author": "____fullName____ <____email____>", '
                    '"license": "____license____", "private": true}\n',
    "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
    ".motd": "\n  A workspace called ____name____.\n",
}
MODULE = {
    ".boilerplate.json": {"type": "module", "requiresWorkspace": "pnpm", "questions": [
        {"name": "____title____", "message": "The title on the first screen", "default": "A short talk",
         "required": True},
        {"name": "____fullName____", "message": "Who is speaking", "setFrom": "workspace.author.name",
         "defaultFrom": "git.user.name", "required": True},
    ]},
    "package.json": '{"name": "____moduleName____"}\n',
    "talk.conv": "# ____title____\n@characters\nNARRATOR: { persona: narrator, name: ____fullName____ }\n---\n"
                 "NARRATOR: Hello.\n",
    "pictures/____moduleName____.png": PICTURE,
    ".motd": "  pnpm build makes out/____moduleName____.mp4\n",
}

#: Git and the day, as a machine that was never set up would not have them.
SOURCES = {"git.user.name": lambda: "Ada Marsh", "git.user.email": lambda: "ada@example.com"}


def boilerplates(folder: Path, root=None) -> Path:
    write(folder / "media/workspace", WORKSPACE)
    write(folder / "media/module", MODULE)
    write(folder, {".boilerplates.json": {"dir": "media", "aliases": {"deck": "module"},
                                          "first": {"template": "module", "name": "first-talk"}} if root is None else root})
    return folder


class InFolder(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.repo = boilerplates(self.folder / "boilerplates")
        self.where = self.folder / "work"
        self.where.mkdir()
        self.settings = Settings.from_env({"CONSTRUCTIVE_MEDIA_HOME": str(self.folder / "home")})

    def sources(self, where=None, **more):
        return {**scaffold.resolvers(where or self.where), **SOURCES, **more}

    def start(self, name, called=None, where=None, **given):
        where = where or self.where
        return scaffold.start(str(self.repo), name, called, where=where, given=given, settings=self.settings,
                              sources={**self.sources(where), "workspace.dirname": lambda: scaffold.slug(called or "")})


class TemplatesTest(InFolder):
    def test_every_template_of_a_repository_is_found(self):
        found = scaffold.templates(self.repo)
        self.assertEqual([(one.name, one.kind, one.requires, one.aliases) for one in found],
                         [("media/module", "module", "pnpm", ("deck",)), ("media/workspace", "workspace", "pgpm", ())])
        self.assertEqual([question.name for question in found[0].questions], ["title", "fullName"])
        self.assertEqual(found[1].questions[3].options, scaffold.LICENSES)

    def test_a_template_is_found_by_its_folder_by_the_folder_templates_are_kept_in_or_by_another_name(self):
        for name in ("media/module", "module", "deck"):
            self.assertEqual(scaffold.find(self.repo, name).name, "media/module")
        self.assertEqual(scaffold.find(self.repo, "module", variant="media").name, "media/module")
        # With no folder said to hold the templates, the one folder called that is it.
        write(self.repo, {".boilerplates.json": {}})
        self.assertEqual(scaffold.find(self.repo, "workspace").name, "media/workspace")
        write(self.repo / "other/workspace", WORKSPACE)
        with self.assertRaises(ScaffoldError):
            scaffold.find(self.repo, "workspace")               # two are called that: say which
        self.assertEqual(scaffold.find(self.repo, "other/workspace").name, "other/workspace")

    def test_a_template_that_is_not_there_is_answered_with_what_is(self):
        with self.assertRaises(ScaffoldError) as raised:
            scaffold.find(self.repo, "poster")
        self.assertEqual(str(raised.exception),
                         "there is no template called `poster` here. There is: deck, module, workspace")
        # A template beside the repository is not one of its templates.
        write(self.folder / "beside", MODULE)
        with self.assertRaises(ScaffoldError):
            scaffold.find(self.repo, "../beside")

    def test_what_is_wrong_with_what_describes_a_template_is_said(self):
        (self.repo / "media/module/.boilerplate.json").write_text("{not json")
        with self.assertRaises(ScaffoldError) as raised:
            scaffold.find(self.repo, "module")
        self.assertIn("cannot be read as JSON", str(raised.exception))
        write(self.repo, {".boilerplates.json": {"first": {"template": "module"}}})
        with self.assertRaises(ScaffoldError) as raised:
            scaffold.first(self.repo)
        self.assertIn('"first" names a template and gives a name', str(raised.exception))

    def test_blanks_are_read_from_files_and_from_their_names(self):
        self.assertEqual(scaffold.blanks(scaffold.find(self.repo, "module")), ["moduleName", "title", "fullName"])


class AnswerTest(InFolder):
    def answer(self, given=None, ask=None, name="module", **sources):
        template = scaffold.find(self.repo, name)
        return scaffold.answer(template, {"moduleName": "tides", **(given or {})}, where=self.where, ask=ask,
                               sources=self.sources(**sources))

    def test_with_nobody_to_ask_every_default_is_taken(self):
        self.assertEqual(self.answer(), {"moduleName": "tides", "title": "A short talk", "fullName": "Ada Marsh"})

    def test_what_is_given_stands_and_what_the_workspace_says_is_not_asked(self):
        asked = []

        def ask(question, default):
            asked.append((question.name, default))
            return "On tides" if question.name == "title" else ""

        self.assertEqual(self.answer(ask=ask)["title"], "On tides")
        self.assertEqual(asked, [("title", "A short talk"), ("fullName", "Ada Marsh")])
        del asked[:]
        answers = self.answer({"title": "Given"}, ask=ask, **{"workspace.author.name": lambda: "Grace Lowe"})
        self.assertEqual((answers["title"], answers["fullName"]), ("Given", "Grace Lowe"))
        self.assertEqual(asked, [])
        self.assertEqual(self.answer({"fullName": "Said"}, **{"workspace.author.name": lambda: "Grace Lowe"})["fullName"],
                         "Said")

    def test_a_question_nobody_answered_is_named_with_its_flag(self):
        with self.assertRaises(ScaffoldError) as raised:
            self.answer(**{"git.user.name": lambda: None})
        self.assertEqual(str(raised.exception), "this template asks for --fullName (Who is speaking)")

    def test_a_blank_nothing_asks_about_is_asked_about(self):
        write(self.repo / "media/module", {"README.md": "For ____audience____.\n"})
        with self.assertRaises(ScaffoldError) as raised:
            self.answer()
        self.assertIn("--audience", str(raised.exception))
        self.assertEqual(self.answer({"audience": "keepers"})["audience"], "keepers")

    def test_an_answer_is_the_kind_of_thing_that_was_asked(self):
        write(self.repo / "media/module", {".boilerplate.json": {"type": "module", "requiresWorkspace": False, "questions": [
            {"name": "____title____", "default": "A short talk"}, {"name": "____fullName____", "default": "Ada Marsh"},
            {"name": "____captions____", "type": "confirm", "default": True},
            {"name": "____slides____", "type": "number", "default": 5},
            {"name": "____access____", "type": "list", "options": ["public", "restricted"], "default": "public"},
        ]}})
        self.assertIsNone(scaffold.find(self.repo, "module").requires)
        answers = self.answer({"captions": "no", "slides": "12", "access": "Restricted"})
        self.assertEqual((answers["captions"], answers["slides"], answers["access"]), (False, 12, "restricted"))
        for given, said in (({"access": "secret"}, "--access is one of public, restricted, not `secret`"),
                            ({"captions": "perhaps"}, "--captions is yes or no, not `perhaps`"),
                            ({"slides": "many"}, "--slides is a number, not `many`")):
            with self.assertRaises(ScaffoldError) as raised:
                self.answer(given)
            self.assertEqual(str(raised.exception), said)

    def test_a_license_is_one_of_those_known_however_it_is_typed(self):
        answers = self.answer({"name": "tides", "repoName": "tides", "license": "mit"}, name="workspace")
        self.assertEqual(answers["license"], "MIT")
        with self.assertRaises(ScaffoldError) as raised:
            self.answer({"name": "tides", "repoName": "tides", "license": "mine"}, name="workspace")
        self.assertIn("--license is one of MIT, ISC", str(raised.exception))

    def test_a_name_is_made_a_folder_as_pgpm_makes_one(self):
        for name, folder in (("Harbour Talks", "harbour-talks"), ("  tides & weather ", "tides-and-weather"),
                             ("first_talk", "first_talk"), ("a/../b", "ab"), ("über talk!", "ber-talk"), ("---", "")):
            self.assertEqual(scaffold.slug(name), folder)


class StartTest(InFolder):
    def test_a_workspace_is_a_folder_of_its_own_with_its_blanks_filled(self):
        made = self.start("workspace", "Harbour Talks")
        self.assertEqual(made.folder, self.where / "harbour-talks")
        self.assertEqual(sorted(path.name for path in made.files), ["LICENSE", "package.json", "pnpm-workspace.yaml"])
        self.assertEqual(json.loads((made.folder / "package.json").read_text()), {
            "name": "harbour-talks", "author": "Ada Marsh <ada@example.com>", "license": "MIT", "private": True})
        self.assertEqual(made.said, "  A workspace called Harbour Talks.")
        self.assertFalse((made.folder / ".motd").exists())
        self.assertFalse((made.folder / ".boilerplate.json").exists())
        license = (made.folder / "LICENSE").read_text()
        self.assertIn("The MIT License (MIT)\n\nCopyright (c) ", license)
        self.assertIn(" Ada Marsh <ada@example.com>\n", license)

    def test_an_empty_folder_of_the_same_name_is_used_as_it_is(self):
        here = self.where / "harbour-talks"
        (here / ".git").mkdir(parents=True)
        self.assertEqual(self.start("workspace", "harbour-talks", where=here).folder, here)
        with self.assertRaises(ScaffoldError) as raised:
            self.start("workspace", "harbour-talks", where=self.where)
        self.assertIn("is there already and is not empty. Nothing was written", str(raised.exception))

    def test_a_license_whose_text_is_long_is_left_to_you(self):
        made = self.start("workspace", "tides", license="apache-2.0", fullName="Grace Lowe")
        self.assertFalse((made.folder / "LICENSE").exists())
        self.assertEqual(made.notes, ("the text of APACHE-2.0 is long and is not written for you: put it in a "
                                      "LICENSE file",))
        self.assertIn('"license": "APACHE-2.0"', (made.folder / "package.json").read_text())
        closed = self.start("workspace", "closed", license="closed", email="").folder / "LICENSE"
        self.assertTrue(closed.read_text().startswith("Copyright (c) "))
        self.assertIn("All Rights Reserved.", closed.read_text())

    def test_a_deck_goes_into_the_packages_of_the_workspace_it_is_started_in(self):
        top = self.start("workspace", "harbour-talks").folder
        made = self.start("deck", "On Tides", where=top, title="On tides")
        self.assertEqual(made.folder, top / "packages/on-tides")
        self.assertEqual(made.notes, ())
        # Who speaks is whoever the workspace says wrote it, not asked again.
        self.assertEqual((made.folder / "talk.conv").read_text().splitlines()[:3],
                         ["# On tides", "@characters", "NARRATOR: { persona: narrator, name: Ada Marsh }"])
        self.assertEqual(json.loads((made.folder / "package.json").read_text()), {"name": "on-tides"})
        self.assertEqual(made.said, "  pnpm build makes out/on-tides.mp4")
        # Started further in, it is made where you are.
        self.assertEqual(self.start("deck", "second", where=top / "packages").folder, top / "packages/second")
        with self.assertRaises(ScaffoldError):
            self.start("deck", "on-tides", where=top)

    def test_a_file_that_is_not_text_is_copied_as_it_is_and_its_name_is_filled(self):
        made = self.start("deck", "tides")
        self.assertEqual((made.folder / "pictures/tides.png").read_bytes(), PICTURE)

    def test_a_link_in_a_template_is_not_followed(self):
        # Boilerplates are somebody else's files: a link in them could point at a key on this machine.
        secret = write(self.folder / "elsewhere", {"key": "a secret\n", "more/key": "another\n"})
        os.symlink(secret / "key", self.repo / "media/module/notes.txt")
        os.symlink(secret / "more", self.repo / "media/module/more", target_is_directory=True)
        made = self.start("deck", "tides")
        self.assertEqual(sorted(path.name for path in made.files), ["package.json", "talk.conv", "tides.png"])
        self.assertFalse((made.folder / "notes.txt").exists())
        self.assertFalse((made.folder / "more").exists())

    def test_outside_a_workspace_it_is_made_where_you_are_and_says_so(self):
        made = self.start("deck", "tides")
        self.assertEqual(made.folder, self.where / "tides")
        self.assertIn("this is not inside a pnpm workspace", made.notes[0])
        # A workspace of another kind is not the kind it asks for.
        (self.where / "lerna.json").write_text("{}")
        self.assertEqual(self.start("deck", "weather").folder, self.where / "weather")

    def test_it_has_to_be_called_something_and_started_somewhere(self):
        for called in (None, "", "!!!"):
            with self.assertRaises(ScaffoldError) as raised:
                self.start("deck", called)
            self.assertEqual(str(raised.exception), "give it a name: me init deck NAME")
        with self.assertRaises(ScaffoldError):
            self.start("deck", "tides", where=self.where / "nowhere")

    def test_an_answer_cannot_put_a_file_outside_the_project(self):
        write(self.repo / "media/module", {"____section____/notes.md": "Notes.\n"})
        for section in ("..", "a/b", "."):
            with self.assertRaises(ScaffoldError) as raised:
                self.start("deck", "tides", section=section)
            self.assertIn("outside the project", str(raised.exception))
        self.assertFalse((self.where / "notes.md").exists())
        self.assertTrue((self.start("deck", "weather", section="back").folder / "back/notes.md").is_file())


class FetchTest(InFolder):
    def test_a_folder_is_used_where_it_is_and_must_be_there(self):
        self.assertEqual(scaffold.fetch(str(self.repo), self.settings), self.repo)
        with self.assertRaises(ScaffoldError) as raised:
            scaffold.fetch(str(self.folder / "nowhere"), self.settings)
        self.assertIn("no such folder of boilerplates", str(raised.exception))

    def test_what_is_neither_a_folder_nor_an_address_is_refused_before_git_hears_of_it(self):
        mark = self.folder / "ran"
        for repo in (f"ext::sh -c 'touch {mark}'", "--upload-pack=touch", "owner", "owner/..", "a b/c", "owner/name",
                     "https://example.com/x.git"):
            with mock.patch("constructive_media.libraries._git", side_effect=AssertionError("git was run")) as ran:
                if repo in ("owner/name", "https://example.com/x.git"):
                    with self.assertRaises(AssertionError):          # these two are addresses, and are fetched
                        scaffold.fetch(repo, self.settings)
                    self.assertIn(repo if "://" in repo else "https://github.com/owner/name.git", ran.call_args.args)
                    with self.assertRaises(ScaffoldError):
                        scaffold.fetch(repo, self.settings, branch="--upload-pack=touch")
                else:
                    with self.assertRaises(ScaffoldError):
                        scaffold.fetch(repo, self.settings)
        self.assertFalse(mark.exists())

    @unittest.skipUnless(HAVE_GIT, "git is needed")
    def test_boilerplates_at_an_address_are_kept_and_looked_at_again_when_asked(self):
        git(self.repo, "init", "--quiet")
        git(self.repo, "add", "--all")
        git(self.repo, "commit", "--quiet", "--message", "boilerplates")
        address = f"file://{self.repo}"
        kept = scaffold.fetch(address, self.settings)
        self.assertEqual(kept.parent, self.folder / "home/boilerplates")
        self.assertEqual(len(scaffold.templates(kept)), 2)
        write(self.repo / "media/poster", MODULE)
        git(self.repo, "add", "--all")
        git(self.repo, "commit", "--quiet", "--message", "a poster")
        self.assertEqual(len(scaffold.templates(scaffold.fetch(address, self.settings))), 2)          # within the day
        self.assertEqual(len(scaffold.templates(scaffold.fetch(address, self.settings, refresh=True))), 3)
        # A branch is kept apart from the main one.
        git(self.repo, "checkout", "--quiet", "-b", "next")
        shutil.rmtree(self.repo / "media/poster")
        git(self.repo, "commit", "--quiet", "--all", "--message", "no poster after all")
        other = scaffold.fetch(address, self.settings, branch="next")
        self.assertNotEqual(other, kept)
        self.assertEqual(len(scaffold.templates(other)), 2)
        # The address gone, what was fetched before still stands; and what was never fetched cannot be.
        shutil.rmtree(self.repo)
        self.assertEqual(scaffold.fetch(address, self.settings, refresh=True), kept)
        elsewhere = Settings.from_env({"CONSTRUCTIVE_MEDIA_HOME": str(self.folder / "elsewhere")})
        with self.assertRaises(ScaffoldError) as raised:
            scaffold.fetch(address, elsewhere)
        self.assertIn("could not be reached", str(raised.exception))


class CommandTest(InFolder):
    def init(self, *arguments, where=None):
        git_says = {"user.name": "Ada Marsh", "user.email": "ada@example.com"}
        with mock.patch.dict(os.environ, {"CONSTRUCTIVE_MEDIA_HOME": str(self.folder / "home"),
                                          "CONSTRUCTIVE_MEDIA_BOILERPLATES": str(self.repo)}), \
                mock.patch.object(scaffold, "_git_says", side_effect=git_says.get):
            return run("init", *arguments, "--cwd", str(where or self.where), "--no-tty")

    def test_answers_are_taken_out_of_what_was_typed(self):
        self.assertEqual(init.split(["init", "deck", "--title", "On tides", "--repo", "./there", "tides", "--no-tty",
                                     "--full-name=Ada Marsh", "--captions", "--cwd", "/tmp", "-t", "deck"]),
                         (["init", "deck", "--repo", "./there", "tides", "--no-tty", "--cwd", "/tmp", "-t", "deck"],
                          {"title": "On tides", "fullName": "Ada Marsh", "captions": True}))
        self.assertEqual(init.split(["init", "--title", "--role", "Keeper"]), (["init"], {"title": True, "role": "Keeper"}))

    def test_a_workspace_starts_with_what_its_boilerplates_say(self):
        status, out, err = self.init("workspace", "Harbour Talks", "--title", "The harbour at night")
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            "harbour-talks: 3 files, from media/workspace",
            "harbour-talks/packages/first-talk: 3 files, from media/module",
            "", "  A workspace called Harbour Talks."])
        deck = self.where / "harbour-talks/packages/first-talk"
        self.assertEqual((deck / "talk.conv").read_text().splitlines()[0], "# The harbour at night")
        self.assertEqual(json.loads((deck / "package.json").read_text()), {"name": "first-talk"})
        # And another deck beside it, from inside the workspace.
        status, out, err = self.init("deck", "second-talk", where=self.where / "harbour-talks")
        self.assertEqual((status, out.splitlines()[0]), (0, "packages/second-talk: 3 files, from media/module"))
        self.assertEqual((deck.parent / "second-talk/talk.conv").read_text().splitlines()[0], "# A short talk")

    def test_a_workspace_alone(self):
        status, out, _ = self.init("workspace", "tides", "--bare", "--license", "isc", "--json")
        self.assertEqual(status, 0)
        (made,) = json.loads(out)
        self.assertEqual((made["template"], made["kind"]), ("media/workspace", "workspace"))
        self.assertFalse((self.where / "tides/packages").exists())
        self.assertTrue((self.where / "tides/LICENSE").read_text().startswith("ISC License"))

    def test_with_no_template_named_it_is_the_module(self):
        status, out, err = self.init("--moduleName", "tides")
        self.assertEqual((status, out.splitlines()[0]), (0, "tides: 3 files, from media/module"))
        self.assertIn("note: this is not inside a pnpm workspace", err)

    def test_what_went_wrong_is_said_in_a_line(self):
        for arguments, said in (
            (("poster", "tides"), "me: there is no template called `poster` here. There is: deck, module, workspace\n"),
            (("deck",), "me: give it a name: me init deck NAME\n"),
            (("deck", "tides", "-t", "deck"), "me: the template is given once: as the first word, or with --template\n"),
            (("deck", "tides", "--repo", "owner"), None),
        ):
            status, out, err = self.init(*arguments)
            self.assertEqual((status, out), (1, ""))
            if said is not None:
                self.assertEqual(err, said)
            self.assertEqual(len(err.splitlines()), 1)
        self.assertEqual(list(self.where.iterdir()), [])

    def test_it_lists_what_the_boilerplates_hold(self):
        status, out, _ = self.init("--list")
        self.assertEqual(status, 0)
        self.assertEqual(out.splitlines()[:3], ["media/module (also: deck): into a pnpm workspace",
                                                "  --title  The title on the first screen [A short talk]",
                                                "  --fullName  Who is speaking"])
        listed = json.loads(self.init("--list", "--json")[1])
        self.assertEqual([one["template"] for one in listed], ["media/module", "media/workspace"])
        self.assertEqual(listed[0]["goes_into"], "pnpm")

    def test_where_boilerplates_are_is_a_setting(self):
        self.assertEqual(Settings.from_env({}).boilerplates, "constructive-io/media-boilerplates")
        self.assertEqual(Settings.from_env({"CONSTRUCTIVE_MEDIA_BOILERPLATES": "owner/ours"}).boilerplates, "owner/ours")
        status, out, _ = run("--help")
        self.assertIn("init", out)

    def test_a_question_is_asked_at_the_keyboard_with_its_default_offered(self):
        asked = []

        def typed(prompt):
            asked.append(prompt)
            return {"The title on the first screen [A short talk]: ": "On tides"}.get(prompt, "2" if "number" in prompt else "")

        with mock.patch("builtins.input", side_effect=typed), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(init._ask(Question("title", "The title on the first screen"), "A short talk"), "On tides")
            self.assertEqual(init._ask(Question("license", "Choose a license", kind="list", options=("MIT", "ISC")), "MIT"), "ISC")
            self.assertEqual(init._ask(Question("captions", "Captions?", kind="confirm"), None), "")
        self.assertEqual(asked[1:], ["a number or a name [MIT]: ", "Captions? (y/n): "])
        self.assertEqual(out.getvalue(), "Choose a license:\n  1. MIT\n  2. ISC\n")


if __name__ == "__main__":
    unittest.main()
