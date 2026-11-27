/*
 * stage.js: a page that follows a recording.
 *
 * Part of Constructive Media, MIT licensed. Load it from any page:
 *
 *     <link rel="stylesheet" href="stage.css">
 *     <script src="stage.js" defer></script>
 *
 * and open the page with `?timeline=<address of a timeline.json>`. From then
 * on the page shows what the recording was saying at every moment:
 *
 *     <section data-slide="problem">        shown from `[slide problem]`
 *       <li data-step="1">…</li>            revealed at `[slide problem step:1]`
 *     </section>
 *
 * The current slide has the class `is-current`, the ones before it `is-past`.
 * A step has `is-shown` once it is reached and `is-now` while it is the last
 * one reached. Whoever is speaking is drawn over the page as the script's
 * `[speakers …]` asks, and a `[video …]` clip takes the screen while it plays.
 *
 * `<body data-stage="…">` says how the page is laid out:
 *
 *     deck         one slide at a time, in a frame of a fixed size (the default)
 *     document     everything on one page, which scrolls to where the voice is
 *     walkthrough  a deck with a pointer that goes to the step being spoken of
 *     plain        no slides: the speakers and what they say (the default with no slides)
 *
 * `data-speakers="faces captions"` on the body is what is drawn of the speakers
 * until the script says, and `data-stage-overlay="off"` draws none of it, for a
 * page that draws its own from the events below.
 *
 * With `&capture` the page is being filmed: no sound is played, the clock is
 * the wall clock, and `window.__stage` says when it started and when it is
 * done. With `&still` it is held at one moment at a time, for a picture of
 * each: `window.__stage.at(seconds)` puts it there. Without a timeline the
 * page is a deck to step through with the keys.
 *
 * Other scripts can listen:
 *
 *     document.addEventListener('stage:change', e => e.detail)   // { slide, step, line, clip, show }
 *     window.stage.t, window.stage.level(), window.stage.seek(seconds)
 *     window.stage.since('pricing', 2)   // seconds since `[slide pricing step:2]`, or null before it
 *
 * `data-enter="left"` on anything in a slide is how it arrives: see stage.css.
 */
(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var STILL = params.has('still');
  var CAPTURE = STILL || params.has('capture');
  var WIDGETS = ['faces', 'lower-third', 'waveform', 'captions'];
  var LINGER = 0.12;      // a line holds this long after its last word
  var CAPTION = 84;       // letters in one caption
  var BARS = 7;

  function ready(run) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
    else run();
  }

  function make(tag, name, parent) {
    var element = document.createElement(tag);
    if (name) element.className = name;
    if (parent) parent.appendChild(element);
    return element;
  }

  function words(id) {
    var said = String(id || '').replace(/[-_]+/g, ' ').trim();
    return said.charAt(0).toUpperCase() + said.slice(1);
  }

  function hue(name) {
    var sum = 0;
    for (var i = 0; i < name.length; i++) sum = (sum * 31 + name.charCodeAt(i)) % 360;
    return sum;
  }

  function initials(name) {
    var parts = String(name || '?').trim().split(/\s+/);
    return ((parts[0] || '?').charAt(0) + (parts.length > 1 ? parts[parts.length - 1].charAt(0) : '')).toUpperCase();
  }

  // A line as captions: pieces short enough to read, each with its share of the line's time.
  function captions(line) {
    var pieces = [], held = '';
    String(line.text || '').split(/\s+/).forEach(function (word) {
      if (held && (held + ' ' + word).length > CAPTION) { pieces.push(held); held = word; }
      else held = held ? held + ' ' + word : word;
      if (/[.!?…]["')\]]?$/.test(word) && held.length > CAPTION / 2) { pieces.push(held); held = ''; }
    });
    if (held) pieces.push(held);
    var all = pieces.reduce(function (sum, piece) { return sum + piece.length; }, 0) || 1;
    var at = line.t;
    return pieces.map(function (piece) {
      var one = { t: at, text: piece };
      at += line.seconds * piece.length / all;
      return one;
    });
  }

  ready(function () {
    var root = document.documentElement, body = document.body;
    var slides = Array.prototype.slice.call(document.querySelectorAll('[data-slide]'));
    var layout = body.dataset.stage || (slides.length ? 'deck' : 'plain');
    var framed = layout !== 'document';
    var asked = (body.dataset.size || params.get('size') || '1920x1080').split('x');
    var WIDTH = parseInt(asked[0], 10) || 1920, HEIGHT = parseInt(asked[1], 10) || 1080;
    var address = params.get('timeline') || body.dataset.timeline || null;
    var timeline = null, base = location.href;
    var notes = [];

    // `stage-starting` holds every slide's fade until the first moment has been painted.
    root.classList.add('stage', 'stage-' + layout, 'stage-starting');
    if (CAPTURE) root.classList.add('stage-capture');
    root.style.setProperty('--stage-width', WIDTH + 'px');
    root.style.setProperty('--stage-height', HEIGHT + 'px');

    var steps = {};
    slides.forEach(function (slide) {
      var most = parseInt(slide.dataset.steps || '0', 10) || 0;
      slide.querySelectorAll('[data-step]').forEach(function (step) {
        most = Math.max(most, parseInt(step.dataset.step, 10) || 0);
      });
      steps[slide.dataset.slide] = most;
    });

    // A shape that is drawn in is measured as one long, whatever its length, so that one rule draws any of them.
    var SHAPES = 'path, line, polyline, polygon, circle, ellipse, rect';
    document.querySelectorAll('[data-enter="draw"]').forEach(function (drawn) {
      (drawn.matches(SHAPES) ? [drawn] : Array.prototype.slice.call(drawn.querySelectorAll(SHAPES))).forEach(function (shape) {
        if (!shape.hasAttribute('pathLength')) shape.setAttribute('pathLength', '1');
      });
    });

    // What is drawn over the page.
    var overlay = make('div', 'stage-overlay', body);
    var card = layout === 'plain' ? make('div', 'stage-card', overlay) : null;
    var clipBox = make('div', 'stage-clip', overlay);
    var clipVideo = CAPTURE ? null : make('video', '', clipBox);
    if (clipVideo) { clipVideo.muted = true; clipVideo.playsInline = true; clipVideo.preload = 'auto'; }
    var facesBox = make('div', 'stage-faces', overlay);
    var waveBox = make('div', 'stage-waveform', overlay);
    var waveName = make('span', 'stage-waveform-name', waveBox);
    var bars = [];
    var barsBox = make('span', 'stage-waveform-bars', waveBox);
    for (var b = 0; b < BARS; b++) bars.push(make('i', '', barsBox));
    var thirdBox = make('div', 'stage-lower-third', overlay);
    var thirdName = make('div', 'stage-lower-third-name', thirdBox);
    var thirdTitle = make('div', 'stage-lower-third-title', thirdBox);
    var captionBox = make('div', 'stage-captions', overlay);
    var captionText = make('span', '', captionBox);
    var pointer = layout === 'walkthrough' ? make('div', 'stage-pointer', overlay) : null;

    // What is drawn over the page is drawn 1920 wide, and scaled to the frame with it.
    var DRAWN = 1920;
    overlay.style.width = DRAWN + 'px';
    overlay.style.height = (DRAWN * HEIGHT / WIDTH) + 'px';
    if (body.dataset.stageOverlay === 'off') root.classList.add('stage-no-overlay');

    function fit() {
      var scale = Math.min(window.innerWidth / WIDTH, window.innerHeight / HEIGHT);
      var left = (window.innerWidth - WIDTH * scale) / 2, top = (window.innerHeight - HEIGHT * scale) / 2;
      var where = 'translate(' + left + 'px,' + top + 'px) ';
      if (framed) {
        body.style.transform = where + 'scale(' + scale + ')';
        overlay.style.transform = 'scale(' + (WIDTH / DRAWN) + ')';
      } else {
        overlay.style.transform = where + 'scale(' + (scale * WIDTH / DRAWN) + ')';
      }
    }
    fit();
    window.addEventListener('resize', fit);

    // ---- what the recording was doing at a moment

    function slideAt(t) {
      var found = null;
      if (timeline) {
        for (var i = 0; i < timeline.slides.length; i++) {
          if (timeline.slides[i].t <= t + 1e-3) found = timeline.slides[i]; else break;
        }
      }
      if (!found) return slides.length ? { id: slides[0].dataset.slide, step: 0 } : { id: null, step: 0 };
      var most = steps[found.id];
      return { id: found.id, step: most === undefined ? found.step : Math.min(found.step, most) };
    }

    function lineAt(t) {
      var found = null;
      for (var i = 0; i < timeline.lines.length; i++) {
        var line = timeline.lines[i];
        if (line.t <= t + 1e-3 && t < line.t + line.seconds + LINGER) found = line;
      }
      return found;
    }

    // Whoever is speaking at `t`, or spoke last before it.
    function spokeAt(t) {
      var found = null;
      for (var i = 0; i < timeline.lines.length; i++) if (timeline.lines[i].t <= t + 1e-3) found = timeline.lines[i];
      return found;
    }

    function clipAt(t) {
      for (var i = 0; i < timeline.clips.length; i++) {
        var clip = timeline.clips[i];
        if (clip.t <= t + 1e-3 && t < clip.t + clip.seconds) return clip;
      }
      return null;
    }

    function showAt(t) {
      var found = null;
      for (var i = 0; i < timeline.speakers.length; i++) {
        if (timeline.speakers[i].t <= t + 1e-3) found = timeline.speakers[i].show; else break;
      }
      if (found) return found;
      var said = body.dataset.speakers;
      if (said !== undefined) return said.split(/[\s,]+/).filter(Boolean);
      return layout === 'plain' ? ['faces', 'captions'] : [];
    }

    // How loud the voice is at `t`, from 0 to 1.
    function level(t) {
      if (!timeline) return 0;
      var line = lineAt(t === undefined ? stage.t : t);
      if (!line) return 0;
      var at = (t === undefined ? stage.t : t) - line.t;
      if (at < 0 || at > line.seconds) return 0;
      var heard = line.loudness || [];
      if (!heard.length) return 0.45 + 0.25 * Math.sin(at * 9);
      var value = heard[Math.min(heard.length - 1, Math.floor(at * timeline.loudness.rate))] || 0;
      return value / timeline.loudness.top;
    }

    // ---- the page made to match

    var cast = [], faces = {}, firstLine = {}, chunks = {};
    var now = { slide: undefined, step: undefined, line: undefined, spoke: undefined, clip: undefined, show: '' };

    function person(line) { return line.name || words(line.speaker.toLowerCase()); }

    function meet() {
      timeline.lines.forEach(function (line) {
        if (firstLine[line.persona] === undefined) {
          firstLine[line.persona] = line.line;
          cast.push(line);
        }
        chunks[line.line] = captions(line);
      });
      cast.forEach(function (line) {
        var face = make('div', 'stage-face', facesBox);
        var picture = make('div', 'stage-face-picture', face);
        var source = timeline.faces && timeline.faces[line.persona];
        if (source) {
          var image = make('img', '', picture);
          image.alt = '';
          image.src = new URL(source, base).href;
        } else {
          picture.textContent = initials(person(line));
          picture.style.background = 'hsl(' + hue(line.persona) + ' 52% 40%)';
          make('i', 'stage-face-mouth', picture);
        }
        make('div', 'stage-face-name', face).textContent = person(line);
        faces[line.persona] = face;
      });
    }

    function shows(show, widget) {
      return show.some(function (one) { return one === widget || one.indexOf(widget + ':') === 0; });
    }

    function apply(t) {
      var at = slideAt(t);
      var line = timeline ? lineAt(t) : null;
      var clip = timeline ? clipAt(t) : null;
      var show = timeline ? showAt(t) : [];
      var changed = false;

      if (at.id !== now.slide || at.step !== now.step) {
        changed = true;
        var seen = false, current = null;
        slides.forEach(function (slide) {
          var is = slide.dataset.slide === at.id;
          if (is) { seen = true; current = slide; }
          slide.classList.toggle('is-current', is);
          slide.classList.toggle('is-past', !is && !seen);
          var here = is ? at.step : (seen ? 0 : Infinity);
          slide.querySelectorAll('[data-step]').forEach(function (step) {
            var number = parseInt(step.dataset.step, 10) || 0;
            step.classList.toggle('is-shown', number <= here);
            step.classList.toggle('is-now', is && number === here && number > 0);
          });
        });
        if (at.id && !current && slides.length && notes.indexOf(at.id) < 0) notes.push(at.id);
        // Not `data-slide`: that would make the body a slide of its own.
        body.dataset.stageSlide = at.id || '';
        body.dataset.stageStep = String(at.step);
        if (card) card.textContent = at.id ? words(at.id) : (timeline ? words(timeline.label) : '');
        if (current) follow(current, at.id !== now.slide);
        now.slide = at.id; now.step = at.step;
      }

      var key = show.join(' ');
      var lineKey = line ? line.line : null;
      var spoke = timeline ? spokeAt(t) : null;
      var spokeKey = spoke ? spoke.line : null;
      if (key !== now.show || lineKey !== now.line || spokeKey !== now.spoke) {
        changed = true;
        WIDGETS.forEach(function (widget) { root.classList.toggle('stage-show-' + widget, shows(show, widget)); });
        root.classList.toggle('stage-talking', show.indexOf('faces:talking') >= 0);
        cast.forEach(function (one) {
          faces[one.persona].classList.toggle('is-speaking', !!line && line.persona === one.persona);
        });
        root.classList.toggle('stage-speaking', !!line);
        // Whoever is speaking, and through the quiet after them: whoever spoke last.
        body.dataset.stageSpeaker = spoke ? spoke.persona || '' : '';
        var third = !!line && shows(show, 'lower-third')
          && (show.indexOf('lower-third:always') >= 0 || firstLine[line.persona] === line.line);
        thirdBox.classList.toggle('is-shown', third);
        if (third) { thirdName.textContent = person(line); thirdTitle.textContent = line.title || ''; }
        waveBox.classList.toggle('is-shown', !!line && !third);
        if (line) waveName.textContent = person(line);
        now.show = key; now.line = lineKey; now.spoke = spokeKey;
      }

      var clipKey = clip ? clip.t : null;
      if (clipKey !== now.clip) {
        changed = true;
        clipBox.classList.toggle('is-shown', !!clip);
        if (clipVideo) {
          if (clip) { clipVideo.src = new URL(clip.src, base).href; clipVideo.currentTime = Math.max(0, t - clip.t); }
          else clipVideo.pause();
        }
        now.clip = clipKey;
      }
      if (clip && clipVideo) {
        var want = t - clip.t;
        if (Math.abs(clipVideo.currentTime - want) > 0.3) clipVideo.currentTime = Math.max(0, want);
        if (stage.playing && clipVideo.paused) clipVideo.play().catch(function () {});
        if (!stage.playing && !clipVideo.paused) clipVideo.pause();
      }

      // What moves with every frame.
      var said = '';
      if (line) {
        var pieces = chunks[line.line] || [];
        for (var i = 0; i < pieces.length; i++) if (pieces[i].t <= t + 1e-3) said = pieces[i].text;
      }
      if (captionText.textContent !== said) captionText.textContent = said;
      captionBox.classList.toggle('is-shown', !!said);
      var loud = line ? level(t) : 0;
      root.style.setProperty('--stage-level', loud.toFixed(3));
      if (line && waveBox.classList.contains('is-shown')) {
        for (var k = 0; k < BARS; k++) {
          var lag = Math.abs(k - (BARS - 1) / 2) * 0.06;
          bars[k].style.transform = 'scaleY(' + (0.12 + 0.88 * level(t - lag)).toFixed(3) + ')';
        }
      }

      stage.t = t;
      if (changed) {
        stage.state = { slide: at.id, step: at.step, line: line, clip: clip, show: show };
        document.dispatchEvent(new CustomEvent('stage:change', { detail: stage.state }));
      }
    }

    // In a document the page goes to where the voice is. In a walkthrough the pointer does.
    function follow(slide, arrived) {
      var step = slide.querySelector('[data-step].is-now');
      if (layout === 'document') {
        var to = step || slide;
        if (step || arrived) to.scrollIntoView({ behavior: 'smooth', block: step ? 'center' : 'start' });
      }
      if (pointer) {
        var target = step || slide.querySelector('[data-point]');
        pointer.classList.toggle('is-shown', !!target);
        if (!target) return;
        var box = slide.getBoundingClientRect(), scale = box.width / slide.offsetWidth || 1;
        var x, y, point = (target.dataset.point || '').split(',');
        if (point.length === 2) {
          x = slide.offsetWidth * parseFloat(point[0]) / 100;
          y = slide.offsetHeight * parseFloat(point[1]) / 100;
        } else {
          var mark = target.getBoundingClientRect();
          x = (mark.left + mark.width / 2 - box.left) / scale;
          y = (mark.top + mark.height / 2 - box.top) / scale;
        }
        // The pointer is drawn with the rest of the overlay, 1920 wide whatever the frame is.
        var drawn = DRAWN / WIDTH;
        pointer.style.transform = 'translate(' + (x * drawn).toFixed(1) + 'px,' + (y * drawn).toFixed(1) + 'px)';
      }
    }

    // ---- the clock

    var stage = window.stage = {
      layout: layout, capture: CAPTURE, timeline: null, t: 0, playing: false, state: {},
      level: level, seek: function () {}, play: function () {}, pause: function () {},
      // When the script reaches `[slide ID step:N]`, in seconds, or null when it never does.
      when: function (slide, step) {
        var marks = timeline ? timeline.slides : [];
        for (var i = 0; i < marks.length; i++) if (marks[i].id === slide && marks[i].step >= (step || 0)) return marks[i].t;
        return null;
      },
      // How long ago that was, in seconds, or null when it has not been reached. What a page draws from.
      since: function (slide, step) {
        var at = stage.when(slide, step);
        return at === null || stage.t < at ? null : stage.t - at;
      },
    };

    function settled() {
      var waits = [document.fonts ? document.fonts.ready : null];
      Array.prototype.forEach.call(document.images, function (image) {
        if (image.decode) waits.push(image.decode().catch(function () {}));
      });
      return Promise.all(waits);
    }

    function frames(count) {
      return new Promise(function (done) {
        (function next(left) { if (left <= 0) done(); else requestAnimationFrame(function () { next(left - 1); }); })(count);
      });
    }

    function started() {
      frames(2).then(function () { root.classList.remove('stage-starting'); });
    }

    function filmed() {
      // No sound, and the wall clock. The frame painted before the clock starts is the film's first.
      // A browser hands over a picture only when something on the page changes, and a slow machine can
      // hand over none between the page loading and its clock starting. So once the first moment is up
      // one dot in the corner is painted again, which nobody can see and which makes a picture certain,
      // and `shownEpoch` says when: a picture from before it may be of a page still getting ready.
      apply(0);
      var shown = null;
      settled().then(function () { return frames(2); }).then(function () {
        var dot = document.createElement('div');
        dot.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0.02;background:#000;' +
                            'pointer-events:none;z-index:2147483647';
        body.appendChild(dot);
        shown = Date.now();
        return frames(1).then(function () { dot.style.background = '#fff'; return frames(2); });
      }).then(function () {
        root.classList.remove('stage-starting');
        var began = performance.now();
        window.__stage = { startEpoch: Date.now(), shownEpoch: shown, seconds: timeline.seconds, done: false,
                           notes: [] };
        stage.playing = true;
        (function tick() {
          var t = (performance.now() - began) / 1000;
          apply(Math.min(t, timeline.seconds));
          if (t >= timeline.seconds) {
            window.__stage.notes = notes.map(function (id) { return 'the page has no slide called `' + id + '`'; });
            window.__stage.done = true;
            return;
          }
          requestAnimationFrame(tick);
        })();
      });
    }

    // What is arriving is put as far along as it would be by `t`: counted from the mark that started it.
    // What is going is gone.
    function arrive(t) {
      if (!document.getAnimations) return;
      document.getAnimations().forEach(function (moving) {
        var part = moving.effect && moving.effect.target, begun = null;
        var slide = part && part.closest ? part.closest('[data-slide]') : null;
        if (slide && slide.classList.contains('is-current')) {
          var step = part.closest('[data-step]');
          begun = stage.when(slide.dataset.slide, step ? parseInt(step.dataset.step, 10) || 0 : 0);
        }
        try {
          if (begun === null || t < begun) moving.finish();
          else { moving.pause(); moving.currentTime = (t - begun) * 1000; }
        } catch (error) { /* one that never ends is left as it is */ }
      });
    }

    function cleared() {
      // A moment is reached from nothing, so that what has arrived by it does not depend on what was looked at before.
      root.classList.add('stage-holding');
      slides.forEach(function (slide) {
        slide.classList.remove('is-current', 'is-past');
        slide.querySelectorAll('[data-step]').forEach(function (step) { step.classList.remove('is-shown', 'is-now'); });
      });
      now.slide = undefined; now.step = undefined;
      void body.offsetHeight;
      root.classList.remove('stage-holding');
    }

    function stilled() {
      // No sound and no clock: the page is put at a moment, and waits there to be looked at.
      apply(0);
      settled().then(function () { return frames(3); }).then(function () {
        root.classList.remove('stage-starting');
        window.__stage = { seconds: timeline.seconds, still: true, notes: [] };
        window.__stage.at = function (seconds) {
          var t = Math.max(0, Math.min(Number(seconds) || 0, timeline.seconds));
          cleared();
          apply(t);
          return frames(2).then(function () { arrive(t); return frames(2); }).then(function () {
            window.__stage.notes = notes.map(function (id) { return 'the page has no slide called `' + id + '`'; });
            return t;
          });
        };
      });
    }

    function played() {
      var sound = new Audio(new URL(timeline.sound, base).href);
      sound.preload = 'auto';
      var controls = make('div', 'stage-controls', body);
      var button = make('button', 'stage-play', controls);
      var bar = make('input', 'stage-scrub', controls);
      var clock = make('span', 'stage-time', controls);
      button.type = 'button';
      bar.type = 'range'; bar.min = '0'; bar.max = String(timeline.seconds); bar.step = '0.01'; bar.value = '0';

      function time(seconds) {
        var whole = Math.floor(seconds);
        return Math.floor(whole / 60) + ':' + ('0' + whole % 60).slice(-2);
      }
      function paint() {
        button.textContent = sound.paused ? 'Play' : 'Pause';
        stage.playing = !sound.paused;
      }
      stage.play = function () { sound.play().catch(function () {}); };
      stage.pause = function () { sound.pause(); };
      stage.seek = function (seconds) {
        sound.currentTime = Math.max(0, Math.min(timeline.seconds, seconds));
        apply(sound.currentTime);
      };
      button.addEventListener('click', function () { if (sound.paused) stage.play(); else stage.pause(); });
      bar.addEventListener('input', function () { stage.seek(parseFloat(bar.value)); });
      sound.addEventListener('play', paint);
      sound.addEventListener('pause', paint);
      document.addEventListener('keydown', function (event) {
        if (event.target === bar) return;
        var marks = timeline.slides.map(function (mark) { return mark.t; });
        if (event.key === ' ') { event.preventDefault(); button.click(); }
        else if (event.key === 'ArrowRight') {
          var next = marks.filter(function (t) { return t > sound.currentTime + 0.05; })[0];
          stage.seek(next === undefined ? sound.currentTime + 5 : next);
        } else if (event.key === 'ArrowLeft') {
          var before = marks.filter(function (t) { return t < sound.currentTime - 0.5; }).pop();
          stage.seek(before === undefined ? sound.currentTime - 5 : before);
        }
      });
      paint();
      apply(0);
      (function tick() {
        var t = sound.currentTime;
        apply(t);
        bar.value = String(t);
        clock.textContent = time(t) + ' / ' + time(timeline.seconds);
        requestAnimationFrame(tick);
      })();
      started();
    }

    // With no recording the page is stepped through by hand.
    function byHand() {
      var places = [];
      slides.forEach(function (slide) {
        for (var step = 0; step <= steps[slide.dataset.slide]; step++) places.push({ id: slide.dataset.slide, step: step });
      });
      var at = 0, arrived = {};
      slideAt = function () { return places[at] || { id: null, step: 0 }; };
      function go(to) {
        at = Math.max(0, Math.min(places.length - 1, to));
        // Stepped through by hand there is no recording to measure from: a mark was reached when it was stepped to.
        Object.keys(arrived).forEach(function (place) { if (parseInt(place, 10) > at) delete arrived[place]; });
        if (!(at in arrived)) arrived[at] = performance.now();
        apply(0);
      }
      stage.since = function (slide, step) {
        for (var i = 0; i <= at; i++) {
          if (places[i].id === slide && places[i].step >= (step || 0)) {
            return places[at].id === slide ? (performance.now() - (arrived[i] || 0)) / 1000 : null;
          }
        }
        return null;
      };
      document.addEventListener('keydown', function (event) {
        if (['ArrowRight', 'PageDown', ' '].indexOf(event.key) >= 0) { event.preventDefault(); go(at + 1); }
        else if (['ArrowLeft', 'PageUp'].indexOf(event.key) >= 0) { event.preventDefault(); go(at - 1); }
      });
      stage.seek = go;
      go(0);
      started();
    }

    if (!address) { byHand(); return; }
    base = new URL(address, location.href).href;
    fetch(base).then(function (answer) {
      if (!answer.ok) throw new Error('the timeline could not be read: ' + answer.status);
      return answer.json();
    }).then(function (read) {
      timeline = stage.timeline = read;
      root.classList.add('stage-following');
      meet();
      if (STILL) stilled(); else if (CAPTURE) filmed(); else played();
    }).catch(function (error) {
      window.__stage = { error: String(error && error.message || error) };
      console.error(error);
    });
  });
})();
