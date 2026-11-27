# The stage: a script to a video

`me say` turns a [`.conv`](https://github.com/constructive-io/conversation-format)
script into sound, and writes down when everything in it fell. The stage turns
that into pictures: a page follows the recording, a browser with no window is
watched while it does, and the pictures are laid under the sound.

```sh
me say  talk.conv out/talk.flac              # the sound, and out/talk.manifest.json
me film out/talk.flac out/talk.mp4           # a video of the built-in page
me film out/talk.flac out/talk.mp4 --page deck    # a video of your own page
me stage out/talk.flac --page deck           # the same page in your browser, with the sound, to scrub through
me print deck out/talk.pdf                   # the page as a PDF
```

The film is as long as the sound, to the frame. Filming is in real time: a
recording of a minute takes a minute to film.

It needs `ffmpeg` and a Chrome or Chromium that is already installed. Nothing
else is installed, and nothing of the browser is part of this package: see the
[`browser` slot](./ADAPTERS.md#browser).

## What a script says

Nothing here is new to the format. A script already says what is on the screen
with marks, which a renderer of sound alone passes over:

| In the script | On the stage |
| --- | --- |
| `[slide pricing]` | the slide called `pricing` is shown |
| `[slide pricing step:2]` | its steps up to 2 are revealed |
| `[speakers faces lower-third waveform captions]` | what is drawn of whoever speaks, until the next such mark |
| `[speakers all]`, `[speakers off]` | all four, or none |
| `[video DEMO]` | the clip called `DEMO` takes the screen and is heard |
| `ADA: { persona: ada, name: Ada Marsh, title: Keeper }` | the name and title on her lower third |

A clip is a sound with a picture: `DEMO: { file: demo.mp4 }` under `@sounds`.
It is not filmed. Its own frames are put into the video, so it comes out as it
went in.

## A page

A page is any HTML that loads the player:

```html
<!doctype html>
<html>
<head>
  <link rel="stylesheet" href="stage.css">
  <script src="stage.js" defer></script>
</head>
<body data-stage="deck">
  <section data-slide="title"><h1>The lighthouse log</h1></section>
  <section data-slide="log">
    <h2>Every night</h2>
    <ul>
      <li data-step="1">When the lamp was lit</li>
      <li data-step="2">Which boats came in</li>
      <li data-step="3">What the weather did</li>
    </ul>
  </section>
</body>
</html>
```

A page that asks for `stage.js` and `stage.css` and has none of its own is
given the engine's, so a page never carries a copy. There is one in
[`examples/talk`](../examples/talk). Opened as a file, with no recording, a
page is a deck to step through with the arrow keys.

### What the player reads

| On | Attribute | Means |
| --- | --- | --- |
| any element | `data-slide="ID"` | shown from `[slide ID]` |
| inside a slide | `data-step="N"` | revealed at `[slide ID step:N]` |
| a slide | `data-steps="N"` | it has N steps, when they are not elements (a page that draws its own) |
| a slide or a step | `data-point="x%,y%"` | where the pointer goes, in a walkthrough |
| `<body>` | `data-stage="deck"` | how the page is laid out, below |
| `<body>` | `data-size="1920x1080"` | the size the slides were drawn at |
| `<body>` | `data-speakers="faces captions"` | what is drawn of the speakers until the script says |
| `<body>` | `data-stage-overlay="off"` | draw nothing of the speakers: the page draws its own |
| `<body>` | `data-timeline="…/timeline.json"` | the recording to follow, when the address does not say |

### Layouts

| `data-stage` | Is |
| --- | --- |
| `deck` | one slide at a time, in a frame of a fixed size, scaled to fit. The default when there are slides |
| `document` | everything on one page, which scrolls to where the voice is. Prints as a document |
| `walkthrough` | a deck with a pointer that goes to the step being spoken of |
| `plain` | no slides: the speakers and what they say. The default when there are none |

### What the player sets

- The current slide has the class `is-current`; the ones before it `is-past`.
- A step has `is-shown` once it is reached, and `is-now` while it is the last
  one reached.
- `<body>` has `data-stage-slide` and `data-stage-step`, for styles that depend
  on where the recording is.
- `--stage-level` on the root is how loud the voice is at this moment, from 0
  to 1, for anything that should move with it.

Colours and type are variables to set in your own stylesheet: `--stage-bg`,
`--stage-ink`, `--stage-dim`, `--stage-accent`, `--stage-font`, and for the
panels captions and names are written on, `--stage-panel`, `--stage-panel-ink`
and `--stage-panel-dim`.

### A page that draws its own

```js
document.addEventListener('stage:change', event => {
  const { slide, step, line, clip, show } = event.detail   // show: what `[speakers …]` asked for
})
window.stage.t            // seconds into the recording
window.stage.level()      // how loud it is now, 0 to 1
window.stage.seek(12.5)   // go there
window.stage.timeline     // everything below
```

### Faces

A picture named for a persona, `faces/ada.png`, in the page's folder or the
one given with `--faces`, is that persona's face. Whoever has none there gets
the one a [library](./LIBRARIES.md) has for them, and failing that their
initials.

## The timeline

`me stage out/talk.flac --write out/talk.stage` writes a folder that holds
everything a page needs, with every path relative, to serve from anywhere or
copy into a site:

```
timeline.json       what fell when
sound.flac          the mix
clips/clip_01.mp4   each `[video …]` clip, cut to what is shown, without its sound
faces/ada.png       a picture for a persona, when there is one
```

```json
{
  "version": 1,
  "label": "talk",
  "sound": "sound.flac",
  "seconds": 42.5,
  "slides":   [{"t": 0.0, "id": "title", "step": 0}],
  "speakers": [{"t": 0.0, "show": ["faces", "captions"]}],
  "clips":    [{"t": 12.0, "seconds": 4.0, "src": "clips/clip_01.mp4", "sound": "DEMO"}],
  "lines":    [{"t": 0.4, "seconds": 2.1, "line": 1, "speaker": "ADA", "persona": "ada",
                "track": "dialogue", "text": "…", "name": "Ada Marsh", "title": "Keeper",
                "loudness": [0, 41, 77]}],
  "faces":    {"ada": "faces/ada.png"},
  "loudness": {"rate": 25, "top": 99}
}
```

A line's `loudness` is how loud it was, `rate` times a second from where the
line starts, from 0 to `top`.

## A page that is not built on the player

`me film out/talk.flac out/talk.mp4 --url http://localhost:3000/deck` films a
page served by something else: a site, an application, a deck made with any
tool. It keeps this contract and nothing more:

1. It is opened with three things added to its address: `timeline=`, the
   address of the `timeline.json`, which answers from any origin; `capture=1`;
   and `size=WxH`.
2. It reads the timeline, gets ready, and then sets
   `window.__stage = { startEpoch: Date.now(), seconds, done: false, notes: [] }`.
   `startEpoch` is the moment its clock begins.
3. From then on it shows, at every moment, what the timeline says is on at
   `(Date.now() - startEpoch) / 1000` seconds. It plays no sound.
4. When that passes `seconds` it sets `window.__stage.done = true`.
5. If it cannot start it sets `window.__stage = { error: "why" }`.

`notes` are things worth telling whoever is filming, such as a slide the
script names and the page lacks. They are printed, and the film is still made.
