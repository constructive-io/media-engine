# Libraries: voices, sounds and faces in repositories of their own

A script names a persona and a bank and nothing else:

```conv
@include weather
@characters
ADA: { persona: ada }
---
[ambience RAIN]
ADA: The rain came in at four.
```

Whose voice `ada` is, and which file `RAIN` plays, are answered by the machine
that renders it. That keeps a script free of paths, models and recordings, and
it means two machines render the same script only if they were given the same
answers.

A library is how the answers are passed around. It is a folder, most often a
git repository, that holds voices to clone, banks of sounds and faces, with
one file that says what is in it. The engine holds none of this; the
[`.conv` format](https://github.com/constructive-io/conversation-format) does
not change for it.

```sh
me library add owner/lighthouse-voices     # a repository on GitHub, or any git address, or a folder
me library voices lighthouse               # give its voices to the personas here
me say scene.conv out/scene.flac           # `@include weather` is found in it
me film out/scene.flac out/scene.mp4       # and so is Ada's face
```

## What a library holds

```
media-library.json
voices/ada/neutral.flac      a clip to clone from: 15 to 28 seconds, one voice, one mood
voices/ada/neutral.txt       the words said in it
banks/weather.sounds         a bank a script can @include
sounds/rain.flac             the files banks play
faces/ada.png                a picture for a persona
```

```json
{
  "library": "lighthouse",
  "version": "0.1.0",
  "license": "CC-BY-4.0",
  "summary": "The keepers of the light, and the weather they keep it in.",
  "voices": [
    {
      "persona": "ada",
      "name": "Ada Marsh",
      "emotion": "neutral",
      "reference": "voices/ada/neutral.flac",
      "consent": { "by": "Ada Marsh", "kind": "own", "on": "2026-11-26" }
    },
    {
      "persona": "ada",
      "emotion": "whisper",
      "reference": "voices/ada/whisper.flac",
      "seed": 31,
      "consent": { "by": "Ada Marsh", "kind": "own", "on": "2026-11-26" }
    }
  ],
  "banks": ["banks"],
  "sounds": ["sounds"],
  "faces": { "ada": "faces/ada.png" }
}
```

| Of a voice | Is |
| --- | --- |
| `persona` | who speaks with it, as a script writes them: lower case, with hyphens |
| `emotion` | which of their voices it is. `neutral` unless said |
| `reference` | the clip to clone from. [How to make one](./VOICES.md#make-a-clip-to-clone-from) |
| `said` | the words said in the clip, when they are not in a `.txt` beside it |
| `profile` | the name a speech server knows the voice by. Instead of a clip, for one of a server's own voices; with a clip, the name to send with it |
| `engine` | the speech adapter that speaks it, when it is not the configured one |
| `seed` | always say this voice's lines with this seed |
| `name`, `tags` | for people reading the library |
| `consent` | whose voice it is, and by what right it is here. Asked of every voice |

Every path is inside the library. Nothing in a library is run: its manifest is
read, and that is all.

## Consent

A voice is somebody's. Every voice in a library says whose, and by what right
the library holds it, and **a library with a voice that does not is refused**:

```json
"consent": { "by": "Ada Marsh", "kind": "own", "on": "2026-11-26", "note": "…" }
```

| `kind` | Means |
| --- | --- |
| `own` | the voice of whoever recorded it, who is named in `by` |
| `permission` | the person whose voice it is, named in `by`, gave leave for it to be used this way |
| `licensed` | the recording came under a license that allows this. `note` says which, and where it is from |
| `synthetic` | nobody's voice: it was designed or generated. `note` says how |

The engine can check that this is written down. It cannot check that it is
true. That is yours to answer for, and the law on a person's voice differs
from place to place. Do not put a voice in a library on the strength of it
being easy to record.

## Check one

```sh
me library check              # this folder
me library check ../lighthouse-voices --json
```

Needs no database and no model. It reads the manifest and everything it
names: every clip is there, has its words and its consent, and is fit to
clone from; every bank reads; every sound a bank plays is there; every face
is a picture. Every problem is said at once, and it exits 1 when there are
any, which makes it a check to run before every commit and in CI.

## Use one

```sh
me library add owner/name                  # cloned under ~/.constructive-media/libraries
me library add https://example.com/voices.git --ref v1.2.0
me library add ../lighthouse-voices        # a folder, used where it is, to try a library as you write it
me library list
me library update lighthouse               # to where its main branch is now, or --ref
me library remove lighthouse
```

A library is kept at the commit it was added at until you update it, so a
render does not change because somebody else pushed. One that has problems is
not added, and an update that would bring problems is undone.

Once added:

- **Banks.** `@include NAME` looks beside the script first, then in the
  libraries. A project's own bank wins over a library's of the same name.
- **Sounds.** A file a bank plays is looked for beside the script, in
  `--sounds`, then in the libraries.
- **Faces.** A film gives whoever has no picture of their own the one a
  library has for them.
- **Voices** are given when you say so, since they are written to the
  database:

  ```sh
  me library voices lighthouse
  me library voices lighthouse --only ada,grace
  ```

  A persona who is not here yet is added. One who already has a voice from
  somewhere else keeps it, unless `--replace`.

## Make the same render on another machine

```sh
me library lock        # writes media-libraries.json: each library, where it is from, its commit
me library install     # on the other machine: adds each at that commit
```

Commit `media-libraries.json` with the project. Voices are then given with
`me library voices`, as above.

## Write one

Start from the template in
[voice-boilerplates](https://github.com/constructive-io/voice-boilerplates),
which has the layout above, a check that runs in CI, and a `.gitignore` that
keeps recordings out. Then:

1. Record, and cut each clip with `me reference`.
2. Write each voice down in `media-library.json`, with its consent.
3. `me library check`, then `me library add .` and `me audition ada`.
4. Commit the clips and their words. Keep the recordings they were cut from
   somewhere else: a library of clips is small, and stays quick to clone.

A library is private until you make it otherwise. Before one is made public,
read every `consent` in it again, as a promise to strangers.
