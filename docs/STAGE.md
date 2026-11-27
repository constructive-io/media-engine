# The stage: a script to a video

`media say` turns a [`.conv`](https://github.com/constructive-io/conversation-format)
script into sound, and writes down when everything in it fell. The stage turns
that into pictures: a page follows the recording, a browser with no window is
watched while it does, and the pictures are laid under the sound.

```sh
media say  talk.conv out/talk.flac           # the sound, and out/talk.manifest.json
media film out/talk.flac out/talk.mp4        # a video of the built-in page
media film out/talk.flac out/talk.mp4 --page deck  # a video of your own page
media still out/talk.flac out/stills --page deck   # pictures of the page at moments of the talk, in seconds
media stage out/talk.flac --page deck        # the same page in your browser, with the sound, to scrub through
media print deck out/talk.pdf                # the page as a PDF
```

The film is as long as the sound, to the frame. Filming is in real time: a
recording of a minute takes a minute to film. [A still](#looking-before-filming)
does not, so a page is looked at with those while it is being made, and filmed
when it is right.

A film at 1920 by 1080 is written at up to 8 Mbit a second, which keeps small
text sharp: a minute of slides comes to thirty megabytes or so. `--bitrate 1M`
wrote the same minute in ten, its text still sharp. Dark gradients are the
first thing to show a low rate, so look at a frame before sending one on.
`media film` says how big the file came out.

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
  on where the recording is, and `data-stage-speaker`, the persona who spoke
  last, for styles that depend on who is talking:
  `body[data-stage-speaker="ada"] { --stage-accent: #e9b44c; }`.
- `--stage-level` on the root is how loud the voice is at this moment, from 0
  to 1, for anything that should move with it.

Colours and type are variables to set in your own stylesheet: `--stage-bg`,
`--stage-ink`, `--stage-dim`, `--stage-accent`, `--stage-font`, and for the
panels captions and names are written on, `--stage-panel`, `--stage-panel-ink`
and `--stage-panel-dim`.

### How a thing arrives

`data-enter` on anything in a slide says how it comes on. On a step it
happens when the voice reaches the step; on anything else, when its slide
does.

| `data-enter` | It |
| --- | --- |
| `rise` | fades in from a little below. What any other word gives too |
| `fade` | fades in where it is |
| `left` | slides in from the left |
| `right` | slides in from the right |
| `grow` | grows from four fifths of its size |
| `pop` | springs up from small, a little past its size and back |
| `wipe` | is uncovered from left to right |
| `draw` | is drawn, for an SVG shape or a group of them with a stroke |

```html
<li data-step="1" data-enter="left">One.</li>
<li data-step="1" data-enter="left" style="--stage-delay: 0.15s">Two, a moment later.</li>
<path data-step="2" data-enter="draw" style="--stage-time: 2s" d="M0 0 L400 120" stroke="currentColor" fill="none"/>
```

`--stage-delay` holds one back, so things on one step come in a row, and
`--stage-time` is how long it takes. A step with no `data-enter` appears as it
always has. The engine names whoever speaks first at the bottom left of the
frame: keep that corner of a slide clear where somebody new begins.

### A page that draws its own

What moves with the script is drawn from the script's clock, never from the
browser's, so a film and a preview show the same thing:

```js
function draw() {
  const since = window.stage.since('pricing', 2)   // seconds since `[slide pricing step:2]`, or null before it
  if (since !== null) bar.style.width = Math.min(1, since / 1.5) * 100 + '%'
  requestAnimationFrame(draw)
}
window.stage.when('close')   // when the script reaches a mark, in seconds, or null if it never does
```

```js
document.addEventListener('stage:change', event => {
  const { slide, step, line, clip, show } = event.detail   // show: what `[speakers …]` asked for
})
window.stage.t            // seconds into the recording
window.stage.level()      // how loud it is now, 0 to 1
window.stage.seek(12.5)   // go there
window.stage.timeline     // everything below
```

### Looking before filming

```sh
media still out/talk.flac out/stills --page deck                 # every beat of the script, as it is left
media still out/talk.flac out/stills --page deck --at pricing:2+0.4 close 48.5 --sheet
```

`media still` puts the page at a moment of the recording and takes a picture
of it there. Twenty pictures take about ten seconds, where a film takes as
long as the recording, so this is how a page that moves is fitted to its
script: change the page, take the stills, look at them.

A moment is seconds into the recording (`48.5`), a slide of the script
(`pricing`), one of its steps (`pricing:2`), or so long after one
(`pricing:2+0.4`). With no `--at` there is one picture for every beat, taken
just before the script leaves it, and one at the end. A picture is named for
its place, its moment and its beat: `003_034.31s_pricing-2.png`. `--sheet`
also lays them four to a picture, `sheet_01.jpg` and on, which is the quick
way to look at a whole talk. `--json` prints what was made. The folder holds
the pictures of the last asking: the ones an earlier asking wrote are removed,
and nothing else in it is touched.

What a still shows:

- What the page [draws from the script's clock](#a-page-that-draws-its-own) is
  exactly what the film has at that moment. This is the reason to draw from
  that clock and no other.
- What arrives by [`data-enter`](#how-a-thing-arrives) is as far along as it
  would be: half way across, half a second into a wipe that takes one.
- A slide that is going is gone: a still is of one beat, not of two crossing.
- Anything else the page leaves to the browser to move, a CSS animation or a
  timer, is shown where it ends up. A film is the only picture of those.

A still is the same picture whenever it is taken and whatever was taken before
it. It has no sound, so `window.stage.level()` is the recording's loudness at
that moment, as it is in a film.

### Faces

A picture named for a persona, `faces/ada.png`, in the page's folder or the
one given with `--faces`, is that persona's face. Whoever has none there gets
the one a [library](./LIBRARIES.md) has for them, and failing that their
initials.

## The timeline

`media stage out/talk.flac --write out/talk.stage` writes a folder that holds
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

`media film out/talk.flac out/talk.mp4 --url http://localhost:3000/deck` films a
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

`media still … --url` asks a little more of such a page. It is opened with
`still=1` in place of `capture=1`, starts no clock, and sets
`window.__stage = { seconds, still: true, notes: [], at }`, where `at(seconds)`
puts the page at that moment and returns a promise that settles once it is
painted there. A picture is taken each time it settles.
