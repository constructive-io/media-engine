# Starting a project

`media init` starts a project from a template: a folder of files with blanks in
them, kept in a repository of boilerplates.

```sh
media init workspace harbour-talks    # a pnpm workspace, with a first deck in it
cd harbour-talks
pnpm install                          # the script checker
pnpm lint                             # every script is a script. Needs no voice
pnpm build                            # packages/first-talk/out/first-talk.mp4
```

```sh
media init deck quarterly-review --title "The quarter in ten minutes"
media init walkthrough first-day      # a tour of an application's screens, with a pointer
media init animation how-it-works     # pictures the page draws, moving with the voice
media init document annual-report     # a document read aloud, and the same page as a PDF
```

Each of these, run at the top of a pnpm workspace, puts a package in
`packages/`: the first makes `packages/quarterly-review`. Run anywhere else, it
makes `./quarterly-review` and says that there is no workspace around it.

A library of voices is the one thing that is not a package. It is a repository
of its own, made wherever you are ([LIBRARIES.md](./LIBRARIES.md)):

```sh
media init library my-voices          # voices to clone, banks of sounds, faces; nothing in it yet
```

The templates come from
[media-engine-boilerplates](https://github.com/constructive-io/media-engine-boilerplates)
unless you say otherwise.

## What a template asks

```sh
media init --list
```

```
media/animation: into a pnpm workspace
  --title  The title on the first screen [A short film]
  --fullName  Who is speaking
  --role  What is written under their name [Narrator]
media/document: into a pnpm workspace
  --title  The title of the document [A short report]
  --fullName  Who wrote it, and reads it aloud
  --role  What is written under their name [Author]
media/module (also: deck): into a pnpm workspace
  --title  The title on the first screen [A short talk]
  --fullName  Who is speaking
  --role  What is written under their name [Presenter]
media/walkthrough: into a pnpm workspace
  --title  The title on the first screen [A short tour]
  --fullName  Who is speaking
  --role  What is written under their name [Guide]
media/workspace: a workspace of its own
  --fullName  Enter author full name
  --email  Enter author email
  --repoName  Enter the repository name
  --license  Choose a license [MIT]
```

Every question is asked at the keyboard, with its default offered, or answered
with a flag of the same name: `--title "On tides"`. With nobody to ask
(`--no-tty`, `CI=true`, or no terminal) each default is taken, and a question
with no default stops it with the flags that are missing. Nothing is written
until every question has an answer.

| Flag | Does |
| --- | --- |
| `--repo WHERE` | reads other boilerplates: a folder (`./there`), `owner/name` on GitHub, or a git address |
| `--template NAME`, `-t` | the template, as a flag in place of the first word |
| `--dir FOLDER` | the folder of the boilerplates to look in |
| `--from-branch BRANCH` | the branch to read |
| `--refresh` | fetches the boilerplates again. They are otherwise kept for a day under the engine's home |
| `--cwd FOLDER` | where to start it |
| `--bare` | a workspace alone, without what it would start with |
| `--no-skills` | leaves out the agent skills the template names |
| `--json` | what was made, or listed, as JSON |

## The skills that come with it

A template can name agent skills that belong with what it makes: how to write
the script, how to film it, how the engine is driven. `media init` copies each
into `.agents/skills/` at the top of the workspace, where an agent working in
the project finds it, and says which:

```
harbour-talks: 14 files, from media/workspace
harbour-talks/packages/first-talk: 9 files, from media/module
harbour-talks/.agents/skills: constructive-media, conv, media-workspace, narrated-video
```

A skill is a folder of text in the repository it is kept in, `.agents/skills/NAME`.
That repository is fetched as the boilerplates are and kept for a day; the
folder's files are copied and nothing in them is run. Started again later, a
skill that is there already is brought up to what its repository has. One that
cannot be fetched is said, with the line that adds it afterwards, and the
project is made all the same. `--no-skills` leaves them out. `pgpm init` reads
the same list and does the same.

## What it will not do

- **Write into a folder that has something in it.** It stops and says so. A
  workspace is made in a folder named for it, or in the folder you are in when
  that is empty and has the name already.
- **Run anything from a template.** A template is files. Its blanks are filled
  and it is copied; no script in it runs and no package is installed. The
  skills it names are copied as files, from `owner/name` on GitHub or a git
  address and never from a path. A link, in a template or in a skill, is left
  out, since it could point at anything on your machine.
- **Follow an answer out of the project.** A blank in a file's name filled with
  `..` or a path is refused.
- **Fetch what is not an address.** `--repo` takes a folder that starts `.`,
  `/` or `~`, `owner/name`, or an address starting `https://`, `ssh://`,
  `git@` or `file://`.

## The same boilerplates, with pgpm

The format is the one [pgpm](https://www.npmjs.com/package/pgpm) reads for
`pgpm init`, so a machine without the engine starts the same project:

```sh
pgpm init workspace --repo constructive-io/media-engine-boilerplates --name harbour-talks
cd harbour-talks
pgpm init --repo constructive-io/media-engine-boilerplates --moduleName first-talk
```

Both write the same files in the same places. `media init` reads the format with
Python's standard library and needs no Node.

## Boilerplates of your own

A repository of boilerplates is folders:

```
.boilerplates.json            {"dir": "media", "aliases": {"deck": "module"},
                               "first": {"template": "module", "name": "first-talk"}}
media/
  workspace/
    .boilerplate.json         {"type": "workspace", "questions": [...]}
    package.json              "name": "____repoName____"
  module/
    .boilerplate.json         {"type": "module", "requiresWorkspace": "pnpm", "questions": [...]}
    talk.conv
    .motd                     printed when it is made, not written
```

- A folder with a `.boilerplate.json` is a template. `dir` is where templates
  are looked for by a bare name.
- A word between four underscores, `____title____`, is a blank, in a file or
  in a file's name. A file that is not text is copied as it is.
- A question has a `name` (`____title____`), a `message`, and any of `default`,
  `defaultFrom` and `setFrom`, `type` (`text`, `confirm`, `number`, `list`
  with `options`) and `required`. A blank no question asks about is asked
  about anyway.
- `defaultFrom` offers an answer from the machine: `git.user.name`,
  `git.user.email`, `workspace.dirname`, `workspace.name`,
  `workspace.license`, `workspace.author.name`, `workspace.author.email`,
  `date.year`. `setFrom` takes it without asking.
- A workspace template is told its `name` and `workspaceName`; any other is
  told its `moduleName`, the name as a folder. A `license` answer writes a
  `LICENSE` file for MIT, ISC, BSD-3-Clause, the Unlicense and "all rights
  reserved".
- `requiresWorkspace` (`pnpm`, `pgpm`, `lerna`, `npm` or `false`) says which
  kind of workspace a module goes into the `packages/` of.
- `skills` names the agent skills that go with a template, as
  `[{"source": "your-org/your-tools", "skills": ["tide-tables"]}]`: the
  repository, and the folders of its `.agents/skills/` to copy.
- `aliases` and `first` are read by `media init` alone: other names for a
  template, and what a new workspace starts with.

```sh
media init poster launch-night --repo your-org/your-boilerplates
export CONSTRUCTIVE_MEDIA_BOILERPLATES=your-org/your-boilerplates
```
