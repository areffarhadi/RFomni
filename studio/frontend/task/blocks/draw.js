// Drawing on the item: boxes, points and outlined shapes on a picture or on a paused video frame (styles: draw.css).
// Places are fractions of the picture (0–1, 4 decimals), so they do not depend on the screen. On a video each mark also
// keeps the time of its frame (t, seconds) and shows only on that frame.
(() => {
  const B = (window.OmniBlocks = window.OmniBlocks || {});
  const STR = {
    en: { box: 'box', point: 'point', shape: 'shape', label: 'Label', remove: 'Remove', finish: 'Finish', cancel: 'Cancel',
      area: 'The picture: draw on it with a mouse or a finger', alt: 'The picture to mark', marks: 'What you marked',
      none: 'Nothing marked yet.', sec: 's', del: (s) => `Remove ${s}`,
      hbox: 'Drag on the picture to draw a box. Click a box to select it, then drag it or its corners.',
      hpoint: 'Click the picture to add a point. Drag a point to move it.',
      hshape: 'Click to place the corners. To finish, click the first corner, double-click or press Enter.',
      video: 'Each mark shows only on the frame it was drawn on.',
      corners: (n) => `Corners placed: ${n}. A shape needs at least 3.`,
      close: 'Click the first corner, double-click or press Enter to finish. Escape cancels.',
      noImg: 'The picture could not be loaded.', noVideo: 'This video cannot be played in a browser. Please skip it.',
      play: 'Play', pause: 'Pause', prev: 'Back one frame', next: 'Forward one frame', seek: 'Place in the video' },
    fa: { box: 'کادر', point: 'نقطه', shape: 'ناحیه', label: 'برچسب', remove: 'حذف', finish: 'پایان', cancel: 'لغو',
      area: 'تصویر: با ماوس یا انگشت روی آن بکشید', alt: 'تصویری که باید علامت بزنید', marks: 'علامت‌های شما',
      none: 'هنوز چیزی را علامت نزده‌اید.', sec: 'ثانیه', del: (s) => `حذف ${s}`,
      hbox: 'برای رسم کادر روی تصویر بکشید. با کلیک یک کادر را انتخاب کنید و سپس خودش یا گوشه‌هایش را بکشید.',
      hpoint: 'برای افزودن نقطه روی تصویر کلیک کنید. برای جابه‌جایی، نقطه را بکشید.',
      hshape: 'با کلیک گوشه‌ها را بگذارید. برای پایان روی گوشه‌ی اول کلیک کنید، دوبار کلیک کنید یا Enter را بزنید.',
      video: 'هر علامت فقط روی فریمی دیده می‌شود که روی آن کشیده شده است.',
      corners: (n) => `گوشه‌های گذاشته‌شده: ${n}. هر ناحیه دست‌کم ۳ گوشه دارد.`,
      close: 'برای پایان روی گوشه‌ی اول کلیک کنید، دوبار کلیک کنید یا Enter را بزنید. Escape لغو می‌کند.',
      noImg: 'تصویر بارگیری نشد.', noVideo: 'این ویدیو در مرورگر پخش نمی‌شود. لطفاً از آن رد شوید.',
      play: 'پخش', pause: 'توقف', prev: 'یک فریم عقب', next: 'یک فریم جلو', seek: 'جای پخش در ویدیو' },
  };
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, a) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); return e; };
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const r4 = (v) => Math.round(clamp(v, 0, 1) * 1e4) / 1e4;
  const fin = (v) => typeof v === 'number' && Number.isFinite(v);
  const TRASH = '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/>', CROSS = '<path d="M7 7l10 10M17 7L7 17"/>';
  const PLAY = '<path d="M8 5.5v13l11-6.5z"/>', PAUSE = '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/>';
  const PREV = '<path d="M6 5v14M18 6v12l-8.5-6z"/>', NEXT = '<path d="M18 5v14M6 6v12l8.5-6z"/>';
  const FRAME = 0.045;   // a mark shows on frames within about 1/25 s of its own time
  // point in a polygon, and a polygon's area (to pick the smallest shape around a click)
  const inside = ([x, y], pts) => pts.reduce((c, [xi, yi], i) => {
    const [xj, yj] = pts[(i || pts.length) - 1];
    return (yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi ? !c : c;
  }, false);
  const size = (pts) => Math.abs(pts.reduce((s, [x, y], i) => { const [x2, y2] = pts[(i + 1) % pts.length]; return s + x * y2 - x2 * y; }, 0)) / 2;

  for (const [type, kind] of [['boxes', 'box'], ['points', 'point'], ['shapes', 'shape']]) {
    B[type] = { mount: (f, ctx) => mount(kind, f, ctx), answered: (f, v) => Array.isArray(v) && v.length > 0 };
  }

  function mount(kind, f, ctx) {
    const { h } = ctx, it = ctx.item || {}, video = it.kind === 'video';
    const T = (k, ...a) => { const v = (STR[ctx.lang] || STR.en)[k] ?? STR.en[k]; return typeof v === 'function' ? v(...a) : v; };
    const n = (x) => (ctx.num ? ctx.num(x) : String(x));
    if (!it.src || it.pending || !['image', 'video'].includes(it.kind) || (video && it.playable === false)) {
      return { media: h('div', { class: 'cant' }, T(video ? 'noVideo' : 'noImg')), answer: null };
    }
    const labels = (f.labels || []).map(String);
    const colour = (label) => `var(--dr-c${Math.max(0, labels.indexOf(label)) % 8})`;
    const coarse = matchMedia('(pointer: coarse)').matches, HIT = coarse ? 20 : 12;

    // a mark in the answer's shape ({x, y, w, h, label, t?} and so on); stored answers are read back through it too
    const norm = (m) => {
      const o = kind === 'box' ? { x: r4(m.x), y: r4(m.y), w: r4(m.w), h: r4(m.h) }
        : kind === 'point' ? { x: r4(m.x), y: r4(m.y) } : { points: m.points.map(p => [r4(p[0]), r4(p[1])]) };
      o.label = String(m.label ?? '');
      if (video) o.t = fin(m.t) ? Math.round(clamp(m.t, 0, 1e6) * 100) / 100 : 0;
      return o;
    };
    const valid = (m) => m && (kind === 'shape' ? Array.isArray(m.points) && m.points.length >= 3 && m.points.every(p => fin(p?.[0]) && fin(p?.[1]))
      : fin(m.x) && fin(m.y) && (kind === 'point' || (fin(m.w) && fin(m.h))));
    let marks = Array.isArray(ctx.value) ? ctx.value.filter(valid).map(norm) : [];
    let sel = -1, cur = labels[0] ?? '', draft = null, drag = null, hover = null, nat = null, live = false;
    let area = { x: 0, y: 0, w: 0, h: 0 };   // where the picture shows inside the stage, in pixels
    const now = () => Math.round(pic.currentTime * 100) / 100;
    const make = (m) => norm({ ...m, label: cur, t: video ? now() : undefined });
    const shown = (m) => !video || (pic.paused && Math.abs(m.t - pic.currentTime) < FRAME);
    const far = (a, b, px) => Math.hypot((a[0] - b[0]) * area.w, (a[1] - b[1]) * area.h) > px;
    const name = (m, i) => `${m.label ? `${m.label} · ` : ''}${T(kind)} ${n(i + 1)}`;

    // ---------- the parts: label chips and tools, the stage (picture + drawing), the list under the question
    const chips = labels.map(l => h('button', { class: 'opt dr-chip', type: 'button', role: 'radio', style: `--c:${colour(l)}`, onclick: () => pick(l) },
      h('i', { class: 'dr-sw', 'aria-hidden': 'true' }), h('bdi', {}, l)));
    const tool = (text, icon, onclick) => h('button', { class: 'btn dr-tool', type: 'button', onclick }, icon ? ctx.svg(icon, 'dr-ic') : null, text);
    const del = tool(T('remove'), TRASH, () => remove(sel)), done = tool(T('finish'), null, () => close()), stop = tool(T('cancel'), null, () => cancel());
    const svg = el('svg', { class: 'dr-svg' });
    const pic = video ? h('video', { src: it.src, playsinline: true, preload: 'metadata', disablepictureinpicture: true })
      : h('img', { src: it.src, alt: T('alt'), draggable: 'false' });
    const stage = h('div', { class: `dr-stage${video ? ' is-video' : ''}`, tabindex: '-1', role: 'group', 'aria-label': T('area') },
      pic, svg, h('div', { class: 'spin dr-wait' }));
    const media = h('div', { class: `dr dr-${kind}` },
      h('div', { class: 'dr-bar' }, chips.length ? h('div', { class: 'dr-chips', role: 'radiogroup', 'aria-label': T('label') }, chips) : null,
        h('div', { class: 'dr-tools' }, done, stop, del)), stage, video ? player() : null);
    const status = h('p', { class: 'hint dr-status', 'aria-live': 'polite' });
    const listEl = h('ul', { class: 'dr-list', 'aria-label': T('marks') });
    const answer = h('div', { class: 'dr-answer' }, status, listEl);

    // ---------- fitting: the picture as large as the stage allows (as object-fit: contain), the drawing exactly on it
    const ready = (w, hh) => { if (w && hh) { nat = [w, hh]; stage.classList.add('ready'); fit(); } };
    const broken = () => { stage.classList.add('ready'); stage.append(h('p', { class: 'dr-err' }, T(video ? 'noVideo' : 'noImg'))); };
    if (video) {
      ctx.track?.(pic);
      pic.addEventListener('loadedmetadata', () => ready(pic.videoWidth, pic.videoHeight));
      for (const ev of ['play', 'pause', 'seeking', 'seeked', 'timeupdate']) pic.addEventListener(ev, onTime);
    } else pic.addEventListener('load', () => ready(pic.naturalWidth, pic.naturalHeight));
    pic.addEventListener('error', broken);
    const ro = new ResizeObserver(() => fit());
    ro.observe(stage);
    function fit() {
      if (!stage.isConnected) { if (live) ro.disconnect(); return; }
      live = true;
      const W = stage.clientWidth, H = stage.clientHeight, [nw, nh] = nat || [16, 9], k = Math.max(0, Math.min(W / nw, H / nh));
      area = { w: nw * k, h: nh * k, x: (W - nw * k) / 2, y: (H - nh * k) / 2 };
      const px = (o) => Object.fromEntries(Object.entries(o).map(([key, v]) => [key, `${v}px`]));
      Object.assign(svg.style, px({ left: area.x, top: area.y, width: area.w, height: area.h }));
      Object.assign(pic.style, px({ left: area.x, top: area.y, width: area.w, height: area.h }));
      paint();
    }

    // ---------- drawing: everything is drawn again from the marks on each change (there are few)
    function paint() {
      svg.replaceChildren();
      svg.classList.toggle('drafting', !!draft);
      if (!nat || !area.w) return;
      const X = (v) => v * area.w, Y = (v) => v * area.h;
      const g = (cls, label, kids) => { const e = el('g', { class: cls, style: `--c:${colour(label)}` }); e.append(...kids); return e; };
      const knob = (x, y, i, k, cls = '') => [el('circle', { cx: X(x), cy: Y(y), r: coarse ? 7 : 5, class: `knob ${cls}` }),
        el('circle', { cx: X(x), cy: Y(y), r: HIT, class: `grip ${cls}`, 'data-i': i, 'data-k': k })];
      const body = (m, i, on) => {
        const hit = (tag, a) => (i >= 0 ? [el(tag, { ...a, class: 'hit', 'data-i': i })] : []);
        if (kind === 'point') {
          const c = { cx: X(m.x), cy: Y(m.y) };
          return [...(on ? [el('circle', { ...c, r: 12, class: 'halo' }), el('circle', { ...c, r: 12, class: 'ring' })] : []),
            el('circle', { ...c, r: on ? 7 : 6, class: 'dot' }), ...hit('circle', { ...c, r: HIT + 2 })];
        }
        const [tag, a] = kind === 'box' ? ['rect', { x: X(m.x), y: Y(m.y), width: X(m.w), height: Y(m.h) }]
          : ['polygon', { points: m.points.map(([x, y]) => `${X(x)},${Y(y)}`).join(' ') }];
        const corners = !on ? [] : kind === 'box' ? [['nw', m.x, m.y], ['ne', m.x + m.w, m.y], ['sw', m.x, m.y + m.h], ['se', m.x + m.w, m.y + m.h]]
          .flatMap(([k, x, y]) => knob(x, y, i, k, k)) : m.points.flatMap(([x, y], k) => knob(x, y, i, k));
        return [el(tag, { ...a, class: 'halo' }), el(tag, { ...a, class: 'line' }), ...hit(tag, a), ...corners];
      };
      const order = marks.map((_, i) => i).filter(i => i !== sel && shown(marks[i]));
      if (sel >= 0 && shown(marks[sel])) order.push(sel);   // the selected one on top
      for (const i of order) svg.append(g(i === sel ? 'm on' : 'm', marks[i].label, body(marks[i], i, i === sel)));
      if (drag?.box) svg.append(g('m draft', cur, body(drag.box, -1, false)));
      if (draft) {   // the outline being placed, with a line to the pointer; the first corner grows once it can close
        const pts = [...draft, ...(hover && !drag ? [hover] : [])].map(([x, y]) => `${X(x)},${Y(y)}`).join(' ');
        const closes = draft.length >= 3, hot = closes && hover && !far(hover, draft[0], HIT);
        svg.append(g('m draft', cur, [el('polyline', { points: pts, class: 'halo' }), el('polyline', { points: pts, class: 'line' }),
          ...draft.map(([x, y], k) => el('circle', { cx: X(x), cy: Y(y), r: k || !closes ? 4 : hot ? 9 : 7, class: k || !closes ? 'knob' : 'knob first' }))]));
      }
    }

    // everything else that shows the state: chips, tools, the hint line and the list
    function update() {
      paint();
      chips.forEach((b, i) => b.setAttribute('aria-checked', String(labels[i] === cur)));
      done.hidden = stop.hidden = !draft; done.disabled = !draft || draft.length < 3;
      del.hidden = !!draft; del.disabled = sel < 0;
      const text = draft ? (draft.length < 3 ? T('corners', n(draft.length)) : T('close')) : `${T(`h${kind}`)}${video ? ` ${T('video')}` : ''}`;
      if (status.textContent !== text) status.textContent = text;
      const a = document.activeElement, keep = listEl.contains(a) ? [a.classList[0], +a.dataset.i] : null;
      listEl.replaceChildren(...marks.map((m, i) => h('li', { class: 'dr-row', style: `--c:${colour(m.label)}` },
        h('button', { class: 'dr-pick', type: 'button', 'aria-pressed': String(i === sel), 'data-i': i, onclick: () => choose(i) },
          h('i', { class: 'dr-sw', 'aria-hidden': 'true' }), h('span', { class: 'dr-name' }, m.label ? [h('bdi', {}, m.label), ' · '] : null, `${T(kind)} ${n(i + 1)}`),
          video ? h('span', { class: 'dr-time' }, `${n(m.t.toFixed(2))} ${T('sec')}`) : null),
        h('button', { class: 'dr-x', type: 'button', 'data-i': i, 'aria-label': T('del', name(m, i)), title: T('del', name(m, i)), onclick: () => remove(i) },
          ctx.svg(CROSS, 'dr-ic')))));
      if (!marks.length) listEl.append(h('li', { class: 'dr-empty' }, T('none')));
      if (keep) (listEl.querySelector(`.${keep[0]}[data-i="${Math.min(keep[1], marks.length - 1)}"]`) || stage).focus({ preventScroll: true });
    }
    const save = () => { ctx.set(marks.length ? marks.map(norm) : null); update(); };
    const select = (i) => { sel = i; if (i >= 0) cur = marks[i].label; };

    function pick(l) {   // a chip: the label for new marks, and the selected mark's new label
      cur = l;
      if (sel >= 0 && marks[sel].label !== l) { marks[sel] = { ...marks[sel], label: l }; save(); } else update();
    }
    function choose(i) {   // a row in the list: select its mark (on a video, go to its frame first)
      if (video) { pic.pause(); if (Math.abs(pic.currentTime - marks[i].t) > 0.005) pic.currentTime = marks[i].t; }
      draft = null; select(i); update();
    }
    function remove(i) {
      if (!marks[i]) return;
      marks.splice(i, 1);
      sel = sel === i ? -1 : sel > i ? sel - 1 : sel;
      save();
    }
    function begin(p) { draft = [p]; hover = null; select(-1); }
    function close() {
      if (!draft || draft.length < 3) return update();
      marks.push(make({ points: draft }));
      draft = hover = null; select(marks.length - 1); save();
    }
    function cancel() { draft = hover = null; update(); }
    // a mark moved by (dx, dy), kept inside the picture
    function shift(o, dx, dy) {
      const xs = kind === 'shape' ? o.points.map(p => p[0]) : kind === 'box' ? [o.x, o.x + o.w] : [o.x];
      const ys = kind === 'shape' ? o.points.map(p => p[1]) : kind === 'box' ? [o.y, o.y + o.h] : [o.y];
      dx = clamp(dx, -Math.min(...xs), 1 - Math.max(...xs)); dy = clamp(dy, -Math.min(...ys), 1 - Math.max(...ys));
      return kind === 'shape' ? { ...o, points: o.points.map(([x, y]) => [r4(x + dx), r4(y + dy)]) }
        : { ...o, x: r4(o.x + dx), y: r4(o.y + dy) };
    }
    // the smallest box or shape around a spot: a click inside one selects it (a drag inside an unselected box draws)
    function around(p) {
      let best = -1, least = Infinity;
      marks.forEach((m, i) => {
        if (!shown(m)) return;
        const s = kind === 'box' ? (p[0] >= m.x && p[0] <= m.x + m.w && p[1] >= m.y && p[1] <= m.y + m.h ? m.w * m.h : Infinity)
          : kind === 'shape' && inside(p, m.points) ? size(m.points) : Infinity;
        if (s < least) { least = s; best = i; }
      });
      return best;
    }

    // ---------- pointer: mouse, pen and touch alike; one pointer at a time
    const P = (e) => { const r = svg.getBoundingClientRect(); return [r4((e.clientX - r.left) / r.width), r4((e.clientY - r.top) / r.height)]; };
    const span = (a, b) => ({ x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: r4(Math.abs(a[0] - b[0])), h: r4(Math.abs(a[1] - b[1])) });
    const tiny = (m) => m.w * area.w < 4 || m.h * area.h < 4;
    svg.addEventListener('pointerdown', (e) => {
      if (drag || e.button > 0) return;
      e.preventDefault();
      stage.focus({ preventScroll: true, focusVisible: false });   // keys now act here; no focus ring for a click
      if (video && !pic.paused) pic.pause();   // drawing happens on a still frame
      try { svg.setPointerCapture(e.pointerId); } catch { /* a pointer that is already gone */ }
      const p = P(e), hit = e.target.closest('[data-i]'), i = hit ? +hit.dataset.i : -1, k = hit?.dataset.k;
      const d = drag = { id: e.pointerId, p0: p, x0: e.clientX, y0: e.clientY, moved: false, i, was: sel, orig: marks[i] };
      if (draft) {   // placing an outline: each press adds a corner; a press on the first one closes it
        if (draft.length >= 3 && !far(p, draft[0], HIT)) { drag = null; return close(); }
        if (far(p, draft[draft.length - 1], 8)) draft.push(p);   // (not again for the second press of a double-click)
        d.mode = 'corner';
      } else if (k != null) Object.assign(d, { mode: 'knob', k });
      else if (i >= 0) { select(i); d.mode = 'move'; }
      else if (kind === 'point') { marks.push(make({ x: p[0], y: p[1] })); select(marks.length - 1); Object.assign(d, { mode: 'move', i: sel, orig: marks[sel], added: true }); }
      else d.mode = kind === 'box' ? 'new' : 'tap';
      update();
    });
    svg.addEventListener('pointermove', (e) => {
      const d = drag;
      if (!d) { if (draft && e.pointerType === 'mouse') { hover = P(e); paint(); } return; }
      if (e.pointerId !== d.id || (!d.moved && Math.hypot(e.clientX - d.x0, e.clientY - d.y0) < (e.pointerType === 'mouse' ? 4 : 8))) return;
      d.moved = true;
      const p = P(e), o = d.orig;
      if (d.mode === 'new') d.box = span(d.p0, p);
      else if (d.mode === 'corner') draft[draft.length - 1] = p;
      else if (d.mode === 'move') marks[d.i] = shift(o, p[0] - d.p0[0], p[1] - d.p0[1]);
      else if (d.mode === 'knob' && kind === 'box') marks[d.i] = { ...o, ...span([d.k.includes('w') ? o.x + o.w : o.x, d.k.includes('n') ? o.y + o.h : o.y], p) };
      else if (d.mode === 'knob') marks[d.i] = { ...o, points: o.points.map((q, j) => (j === +d.k ? p : q)) };
      paint();
    });
    const end = (e) => {
      const d = drag;
      if (!d || e.pointerId !== d.id) return;
      drag = null;
      if (d.mode === 'new') {
        if (d.moved && !tiny(d.box)) { marks.push(make(d.box)); select(marks.length - 1); save(); }
        else select(around(d.p0));   // a click: select the box around it, or nothing
      } else if (d.mode === 'tap' && !d.moved) { const i = around(d.p0); i >= 0 ? select(i) : begin(d.p0); }
      else if (d.mode === 'knob' && kind === 'box' && tiny(marks[d.i])) marks[d.i] = d.orig;   // squashed flat: put it back
      else if (kind === 'shape' && !d.moved && d.was === d.i && d.i >= 0) begin(d.p0);   // a second click on a shape starts a new one there
      else if ((d.moved || d.added) && (d.mode === 'move' || d.mode === 'knob')) return save();
      update();
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('pointerleave', () => { if (hover) { hover = null; paint(); } });
    svg.addEventListener('dblclick', (e) => { e.preventDefault(); if (draft) close(); });

    // ---------- video: a small player under the frame. The browser's own controls would sit under the drawing (Firefox's
    // big play button, Chrome's on phones), out of reach; this row also steps one frame at a time.
    function player() {
      const btn = (label, icon, onclick) => h('button', { class: 'dr-pbtn', type: 'button', 'aria-label': T(label), title: T(label), onclick }, ctx.svg(icon, 'dr-ic'));
      const play = btn('play', PLAY, () => (pic.paused ? pic.play().catch(() => {}) : pic.pause()));
      const step = (d) => { pic.pause(); pic.currentTime = clamp(Math.round((pic.currentTime + d) * 100) / 100, 0, pic.duration || 0); };
      const seek = h('input', { class: 'slider dr-seek', type: 'range', min: 0, max: 0, step: 0.04, value: 0, 'aria-label': T('seek'),
        oninput: () => { pic.currentTime = +seek.value; } });
      const time = h('span', { class: 'dr-clock' });
      const show = () => {
        const d = Number.isFinite(pic.duration) ? pic.duration : 0;
        seek.max = d; seek.value = pic.currentTime;
        seek.setAttribute('aria-valuetext', `${n(pic.currentTime.toFixed(2))} ${T('sec')}`);
        time.textContent = `${n(pic.currentTime.toFixed(2))} / ${n(d.toFixed(2))}`;
        play.replaceChildren(ctx.svg(pic.paused ? PLAY : PAUSE, 'dr-ic'));
        play.setAttribute('aria-label', T(pic.paused ? 'play' : 'pause')); play.title = T(pic.paused ? 'play' : 'pause');
      };
      for (const ev of ['loadedmetadata', 'durationchange', 'timeupdate', 'seeked', 'play', 'pause']) pic.addEventListener(ev, show);
      show();
      return h('div', { class: 'dr-player', dir: 'ltr' }, play, btn('prev', PREV, () => step(-0.04)), btn('next', NEXT, () => step(0.04)), seek, time);
    }

    // ---------- video: marks belong to their frame; playing or seeking drops an outline that is not finished
    function onTime(e) {
      const before = [sel, !!draft];
      if (draft && (e.type === 'play' || e.type === 'seeking')) draft = hover = null;
      if (sel >= 0 && !shown(marks[sel])) sel = -1;
      before[0] !== sel || before[1] !== !!draft ? update() : paint();
    }

    // ---------- keys, while the focus is on the picture or in this question's list:
    // Delete/Backspace remove, arrows nudge (Shift: further), Escape lets go; while outlining Enter closes (not submit)
    function onKey(e) {
      if (!stage.isConnected) { if (live) removeEventListener('keydown', onKey, true); return; }
      const a = document.activeElement;
      if (!(media.contains(a) || answer.contains(a)) || a === pic || e.ctrlKey || e.metaKey || e.altKey) return;
      const row = a.closest('.dr-row') ? +a.dataset.i : null, i = row ?? sel, k = e.key || '';
      if (draft && (k === 'Enter' || k === 'Escape')) k === 'Enter' ? close() : cancel();
      else if (draft && (k === 'Backspace' || k === 'Delete')) { draft.pop(); if (!draft.length) draft = null; update(); }
      else if (k === 'Escape' && sel >= 0) { sel = -1; update(); }
      else if ((k === 'Delete' || k === 'Backspace') && i >= 0) remove(i);
      else if (k.startsWith('Arrow') && i >= 0 && (row != null || a === stage)) {
        if (i !== sel) choose(i);
        const step = (e.shiftKey ? 10 : 1) / ((k === 'ArrowLeft' || k === 'ArrowRight' ? area.w : area.h) || 500);
        const [dx, dy] = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[k] || [0, 0];
        marks[i] = shift(marks[i], dx, dy); save();
      } else return;
      e.preventDefault(); e.stopPropagation();
    }
    addEventListener('keydown', onKey, true);

    update();
    return { media, answer };
  }
})();
