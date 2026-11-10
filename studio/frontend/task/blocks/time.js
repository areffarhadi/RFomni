// Time questions on audio and video items: mark parts of the clip (ranges), mark moments, or trace a rating while it
// plays (trace). The clip shows as a waveform (audio) or a light timeline (video: never decoded, so long videos stay
// light), drawn by wavesurfer.js 7.8.6 and its regions plugin (BSD-3-Clause, ../vendor/), loaded the first time one of
// these questions shows. Until it has loaded, and if it cannot load, playing, the buttons and the lists still work.
(() => {
  const B = (window.OmniBlocks = window.OmniBlocks || {});
  const VENDOR = new URL('../vendor/', document.currentScript?.src || location.href);

  const STR = {
    en: { wave: 'waveform', line: 'timeline', loading: (w) => `Loading the ${w}…`,
      noWave: (w) => `The ${w} could not be shown. Playing and the buttons still work.`,
      play: 'Play', pause: 'Pause', s: 's', part: 'Part', mark: 'Moment', parts: 'Marked parts', marks: 'Marked moments',
      next: (p) => (p ? 'Label for the next moment' : 'Label for the next part'),
      chosen: (p) => (p ? 'Label of the selected moment' : 'Label of the selected part'),
      hintRanges: (w) => `Drag across the ${w} to mark a part; drag its edges to adjust it. Click the ${w} to play from there.`,
      hintMoments: (w) => `Press “Mark now” the moment it happens. Click the ${w} to play from there; drag a mark to move it.`,
      hintTrace: 'Play the clip and keep moving the slider while it plays. Playing a part again records over it.',
      markStart: 'Mark start', markEnd: 'Mark end', markNow: 'Mark now', noParts: 'No parts marked yet.',
      noMarks: 'No moments marked yet.', from: 'From', to: 'to', at: 'At', remove: 'Remove',
      added: (l, t) => `Marked ${l} at ${t}`, traced: (a, b) => `Traced ${a} of ${b}`, startOver: 'Start over',
      noMedia: 'This item has no recording to mark.' },
    fa: { wave: 'شکل موج', line: 'نوار زمان', loading: (w) => `در حال بارگذاری ${w}…`,
      noWave: (w) => `${w} نمایش داده نشد. پخش و دکمه‌ها همچنان کار می‌کنند.`,
      play: 'پخش', pause: 'توقف', s: 'ثانیه', part: 'بخش', mark: 'لحظه', parts: 'بخش‌های علامت‌خورده', marks: 'لحظه‌های علامت‌خورده',
      next: (p) => (p ? 'برچسب لحظه‌ی بعدی' : 'برچسب بخش بعدی'),
      chosen: (p) => (p ? 'برچسب لحظه‌ی انتخاب‌شده' : 'برچسب بخش انتخاب‌شده'),
      hintRanges: (w) => `برای علامت زدن یک بخش، روی ${w} بکشید و برای تنظیم آن، لبه‌هایش را جابه‌جا کنید. با کلیک روی ${w}، پخش از همان نقطه شروع می‌شود.`,
      hintMoments: (w) => `همان لحظه‌ای که رخ می‌دهد «علامت بزن» را بزنید. با کلیک روی ${w}، پخش از همان نقطه شروع می‌شود؛ برای جابه‌جا کردن هر علامت، آن را بکشید.`,
      hintTrace: 'نمونه را پخش کنید و در همان حال نوار لغزنده را جابه‌جا کنید. اگر بخشی را دوباره پخش کنید، پاسخ تازه جای پاسخ قبلی آن بخش را می‌گیرد.',
      markStart: 'علامت شروع', markEnd: 'علامت پایان', markNow: 'علامت بزن', noParts: 'هنوز بخشی علامت نخورده است.',
      noMarks: 'هنوز لحظه‌ای علامت نخورده است.', from: 'از', to: 'تا', at: 'زمان', remove: 'حذف',
      added: (l, t) => `${l} در ${t} علامت خورد`, traced: (a, b) => `${a} از ${b} ثبت شد`, startOver: 'از نو',
      noMedia: 'این نمونه صدا یا ویدیویی برای علامت زدن ندارد.' },
  };
  const words = (ctx) => STR[ctx.lang === 'fa' ? 'fa' : 'en'];
  // one colour per label, by its place in the list; each reads on the dark and the light themes
  const COLORS = ['#2f8fd8', '#d9661f', '#1f9d55', '#c84fd8', '#a88a0a', '#0f9d9a', '#e5484d', '#8e6cf0'];
  const PLAY = '<path d="M8 5.5v13l11-6.5z"/>', PAUSE = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>', CROSS = '<path d="M7 7l10 10M17 7L7 17"/>';
  const r2 = (x) => Math.round(x * 100) / 100;
  const uid = () => `m${Math.random().toString(36).slice(2, 10)}`;
  // a typed time: Persian and Arabic digits and decimal marks count too
  const parse = (s) => parseFloat(String(s).replace(/[۰-۹]/g, (d) => d.charCodeAt(0) - 1776)
    .replace(/[٠-٩]/g, (d) => d.charCodeAt(0) - 1632).replace(/[٫,]/g, '.'));

  // the waveform is a canvas, which cannot read CSS variables: read the theme's colours as plain values
  const pen = document.createElement('canvas').getContext('2d');
  const rgb = (c) => { pen.fillStyle = '#000'; pen.fillStyle = c; const s = pen.fillStyle;
    return s[0] === '#' ? [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16)) : s.match(/[\d.]+/g).slice(0, 3).map(Number); };
  const fade = (c, a) => `rgba(${rgb(c).join(',')},${a})`;
  const tok = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';

  // wavesurfer and its regions plugin, loaded once, on first use
  let lib = null;
  const script = (name, has) => new Promise((ok, no) => (has() ? ok() : document.head.append(
    Object.assign(document.createElement('script'), { src: new URL(name, VENDOR).href, onload: ok, onerror: no }))));
  const loadLib = () => (lib ||= script('wavesurfer-7.8.6.min.js', () => window.WaveSurfer?.create)
    .then(() => script('wavesurfer-regions-7.8.6.min.js', () => window.WaveSurfer.Regions))
    .then(() => window.WaveSurfer, (e) => { lib = null; throw e; }));

  // run fn once el has been on the page and is gone again (the page moved on): stop listening, free the waveform
  function onGone(el, fn) {
    let seen = false;
    const mo = new MutationObserver(() => {
      if (el.isConnected) seen = true;
      else if (seen) { mo.disconnect(); fn(); }
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  // Enter and Space on our own buttons press them: the page's own keys (Enter submits, Space plays) stay outside
  const ownKeys = (el) => { el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('button')) e.stopPropagation(); }); return el; };
  const usable = (it) => !!it?.src && !it.pending && (it.kind === 'audio' || (it.kind === 'video' && it.playable !== false));
  const none = (ctx) => ({ media: null, answer: ctx.h('p', { class: 'tb-empty' }, words(ctx).noMedia) });

  // the clip with its waveform or timeline, a play button and the time; blocks add their own controls to `bar`
  function player(ctx, { height, soft = false }) {
    const { h, item: it } = ctx, T = words(ctx), video = it.kind === 'video';
    // audio is decoded for its shape unless it is long; video, and long audio, get a flat timeline with the duration only
    const flat = video || (it.duration || 0) > 600, w = flat ? T.line : T.wave;
    const media = h(video ? 'video' : 'audio', { class: video ? 'tb-video' : null, src: new URL(it.src, location.href).href,
      preload: video ? 'metadata' : 'auto', controls: video, playsinline: true });
    ctx.track(media);
    const wait = h('span', { class: 'tb-wait' }, T.loading(w));
    const wave = h('div', { class: 'tb-wave', dir: 'ltr', style: `height:${height}px` }, wait);
    const P = { media, wave, video, w, ws: null, regions: null, ticks: [], gone: [], stopAt: null, from: 0 };
    P.dur = () => (Number.isFinite(media.duration) && media.duration) || it.duration || 0;
    const long = () => P.dur() >= 60;
    P.time = (t) => ctx.num(long() ? `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, '0')}` : t.toFixed(2));
    P.unit = () => (long() ? '' : ` ${T.s}`);
    P.toggle = () => (media.paused ? media.play().catch(() => {}) : media.pause());
    P.playPart = (a, b) => { P.from = a; P.stopAt = b; media.currentTime = a; media.play().catch(() => {}); };

    const btn = h('button', { class: 'play tb-play', type: 'button', 'aria-label': T.play, onclick: P.toggle }, ctx.svg(PLAY));
    const clock = h('bdi', { dir: 'ltr' }, ''), unit = h('span', {}, '');
    P.bar = h('div', { class: 'tb-bar' }, btn, h('span', { class: 'tb-clock' }, clock, unit));
    // each frame while playing (and on every media event): the clock, the end of a part being played, trace samples
    let raf = 0;
    const tick = () => {
      const t = media.currentTime;
      if (P.stopAt != null && t >= P.stopAt) { media.pause(); P.stopAt = null; }
      clock.firstChild.data = `${P.time(t)} / ${P.time(P.dur())}`;   // text data, not new nodes, each frame
      unit.firstChild.data = P.unit();
      P.ticks.forEach((fn) => fn(t));
    };
    const loop = () => { tick(); raf = media.paused ? 0 : requestAnimationFrame(loop); };
    media.addEventListener('play', () => {
      btn.replaceChildren(ctx.svg(PAUSE)); btn.setAttribute('aria-label', T.pause);
      cancelAnimationFrame(raf); raf = requestAnimationFrame(loop);
    });
    media.addEventListener('pause', () => { btn.replaceChildren(ctx.svg(PLAY)); btn.setAttribute('aria-label', T.play); P.stopAt = null; tick(); });
    ['timeupdate', 'seeked', 'loadedmetadata', 'durationchange'].forEach((e) => media.addEventListener(e, tick));
    // a jump outside the part being played ends "play this part"
    media.addEventListener('seeking', () => {
      if (P.stopAt != null && (media.currentTime > P.stopAt || media.currentTime < P.from - 0.05)) P.stopAt = null;
    });
    if (video) media.addEventListener('loadedmetadata', () => { if (media.videoWidth) media.style.aspectRatio = `${media.videoWidth} / ${media.videoHeight}`; });
    tick();

    P.ready = loadLib().then((WS) => new Promise((ok, no) => {
      const make = (flat) => {
        let done = false;
        const accent = tok('--accent'), muted = tok('--muted');
        const ws = WS.create({ container: wave, media, height: height - 2, cursorColor: tok('--text'), cursorWidth: 2, hideScrollbar: true,
          waveColor: fade(muted, soft ? 0.35 : 0.6), progressColor: soft ? fade(accent, 0.5) : accent,
          ...(flat ? { peaks: [new Float32Array(1000).fill((soft ? 4 : 8) / (height - 2))], duration: it.duration || undefined, normalize: false }
            : { url: media.src, normalize: true, barWidth: 2, barGap: 1, barRadius: 2 }) });
        const regions = ws.registerPlugin(WS.Regions.create());
        ws.once('ready', () => { done = true; wait.remove(); Object.assign(P, { ws, regions }); ok(P); });
        ws.on('error', (e) => { if (done) return; done = true; ws.destroy(); flat ? no(e) : make(true); });
      };
      make(flat);
    }));
    P.ready.catch(() => { wait.textContent = T.noWave(w); wave.classList.add('tb-off'); });

    P.mount = (...kids) => {
      const view = h('div', { class: 'tb' }, !video && it.text ? h('p', { class: 'stim', dir: 'auto' }, it.text) : null,
        media, wave, P.bar, ...kids);
      onGone(view, () => { cancelAnimationFrame(raf); P.ws?.destroy(); P.gone.forEach((fn) => fn()); });
      return (P.view = ownKeys(view));
    };
    return P;
  }

  // ranges (point = false): parts of the clip, each with a label; moments (point = true): points in time with a label
  function marks(f, ctx, point) {
    if (!usable(ctx.item)) return none(ctx);
    const { h } = ctx, T = words(ctx), labels = (f.labels || []).map(String);
    const P = player(ctx, { height: ctx.item.kind === 'video' ? 56 : 96 });
    const accent = tok('--accent');
    const color = (l) => (labels.length ? COLORS[Math.max(0, labels.indexOf(l)) % COLORS.length] : accent);
    let list = (Array.isArray(ctx.value) ? ctx.value : []).filter((v) => v && typeof v === 'object').map((v) => {
      const a = Math.max(0, +(point ? v.t : v.start) || 0);
      return { id: uid(), label: String(v.label ?? ''), start: a, end: point ? a : +v.end || 0 };
    }).filter((m) => point || m.end > m.start);
    let cur = labels[0] ?? '', sel = null, from = null, pend = null, adding = false;   // from: a start marked with the button
    const byId = (id) => list.find((m) => m.id === id);
    const regs = new Map();   // mark id -> its region on the waveform
    const name = (m) => m.label || (point ? T.mark : T.part);
    const span = (m) => (point ? P.time(m.start) : `${P.time(m.start)}–${P.time(m.end)}`);

    const emit = () => {
      list.sort((a, b) => a.start - b.start || a.end - b.end);
      ctx.set(!list.length ? null : list.map((m) => (point ? { t: r2(m.start), label: m.label }
        : { start: r2(m.start), end: Math.max(r2(m.end), r2(m.start) + 0.01), label: m.label })));
    };

    // the label written on a mark (the region lives in wavesurfer's shadow DOM, so it is styled inline)
    const tag = (m, c) => (m.label ? h('span', { dir: 'auto', style: `display:inline-block;box-sizing:border-box;max-width:${point ? '12em' : 'calc(100% - 6px)'};`
      + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin:3px;padding:1px 6px;border-radius:6px;'
      + `font:600 11px/1.4 var(--font);color:var(--text);background:color-mix(in srgb,var(--surface) 82%,transparent);border:1px solid ${c}` }, m.label) : undefined);
    // a mark on the waveform, drawn or brought up to date
    const draw = (m) => {
      if (!P.regions) return;
      const c = color(m.label), on = m.id === sel;
      const opts = { start: m.start, end: m.end, color: point ? c : fade(c, on ? 0.45 : 0.22), drag: true, resize: !point };
      let r = regs.get(m.id);
      if (r) r.setOptions(r.tbLabel === m.label ? opts : { ...opts, content: tag(m, c) });   // a new label only when it changed
      else { adding = true; r = P.regions.addRegion({ ...opts, content: tag(m, c), id: m.id }); adding = false; regs.set(m.id, r); }
      r.tbLabel = m.label;
      const el = r.element;
      el.style.transition = 'none';
      if (point) Object.assign(el.style, { background: 'none', borderLeft: `${on ? 4 : 2}px solid ${c}` });
      else el.style.boxShadow = on ? `inset 0 0 0 2px ${c}` : 'none';
      el.querySelectorAll('[part~="region-handle"]').forEach((x) => { x.style.borderColor = c; });
    };

    // label chips: the label for the next mark, or of the selected one
    const capId = `tb-cap-${f.id}`;
    const cap = h('p', { class: 'tb-cap', id: capId });
    const chips = labels.map((l, i) => h('button', { class: 'tb-chip', type: 'button', role: 'radio', 'data-v': l, onclick: () => pick(l) },
      h('i', { class: 'tb-dot', style: `background:${COLORS[i % COLORS.length]}` }), l));
    const paintChips = () => {
      const m = sel && byId(sel);
      chips.forEach((b) => b.setAttribute('aria-checked', String(b.dataset.v === (m ? m.label : cur))));
      cap.textContent = m ? T.chosen(point) : T.next(point);
    };
    let undrag = () => {};
    const dragColor = () => { if (point || !P.regions) return; undrag(); undrag = P.regions.enableDragSelection({ color: fade(color(cur), 0.3) }); };
    const pick = (l) => {
      cur = l; dragColor();
      const m = sel && byId(sel);
      if (m) { m.label = l; draw(m); emit(); fixRow(m); }
      paintChips();
    };

    // the list under the question: a row selects and plays its mark; the selected row can be typed into
    const ul = h('ul', { class: 'tb-list', 'aria-label': point ? T.marks : T.parts });
    const empty = h('p', { class: 'tb-empty' }, point ? T.noMarks : T.noParts);
    const status = h('p', { class: 'sr-only', role: 'status' });
    const rows = new Map();
    const box = (m, key, label) => {
      const input = h('input', { class: 'tb-num', inputmode: 'decimal', dir: 'ltr', autocomplete: 'off', 'aria-label': label,
        'data-f': `${m.id}:${key}`, value: ctx.num(m[key].toFixed(2)), onchange: () => commit(input, m, key),
        onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); commit(input, m, key); } } });
      return input;
    };
    const commit = (input, m, key) => {
      const v = parse(input.value);
      let ok = Number.isFinite(v) && v >= 0 && v <= (P.dur() || Infinity);
      if (ok && !point) ok = key === 'start' ? v < m.end : v > m.start;
      if (ok && v !== m[key]) { m[key] = v; if (point) m.end = v; draw(m); emit(); fixRow(m); }
      input.value = ctx.num(m[key].toFixed(2));
    };
    const row = (m) => {
      const on = m.id === sel;
      const dot = h('i', { class: 'tb-dot', style: `background:${color(m.label)}` }), lab = h('span', { class: 'tb-lab' }, name(m));
      const time = h('bdi', { dir: 'ltr' }, span(m)), unit = h('span', {}, P.unit());
      const x = h('button', { class: 'tb-x', type: 'button', 'data-f': `${m.id}:x`, 'aria-label': `${T.remove}: ${name(m)} ${span(m)}`, onclick: () => remove(m.id) }, ctx.svg(CROSS));
      const item = h('button', { class: 'tb-item', type: 'button', 'aria-pressed': String(on), 'data-f': `${m.id}:item`,
        onclick: () => { select(m.id); point ? P.playPart(Math.max(0, m.start - 1), m.start + 1) : P.playPart(m.start, m.end); } },
      dot, lab, h('span', { class: 'tb-time' }, time, unit));
      const edit = on ? h('div', { class: 'tb-edit' }, point ? [T.at, box(m, 'start', T.at)]
        : [T.from, box(m, 'start', T.from), T.to, box(m, 'end', T.to)], ` ${T.s}`) : null;
      rows.set(m.id, { dot, lab, time, unit, x, edit });
      return h('li', { class: `tb-row${on ? ' on' : ''}` }, item, x, edit);
    };
    // times and labels change in place, so typing and focus are not disturbed
    const fixRow = (m) => {
      const r = rows.get(m.id);
      if (!r) return;
      r.dot.style.background = color(m.label); r.lab.textContent = name(m); r.time.textContent = span(m); r.unit.textContent = P.unit();
      r.x.setAttribute('aria-label', `${T.remove}: ${name(m)} ${span(m)}`);
      r.edit?.querySelectorAll('input').forEach((i) => { if (i !== document.activeElement) i.value = ctx.num(m[i.dataset.f.split(':')[1]].toFixed(2)); });
    };
    const paintList = () => {
      const focus = ul.contains(document.activeElement) ? document.activeElement.dataset.f : null;
      rows.clear();
      ul.replaceChildren(...list.map(row));
      empty.hidden = list.length > 0;
      if (focus) ul.querySelector(`[data-f="${focus}"]`)?.focus();
    };

    const select = (id) => {
      const old = sel;
      sel = id;
      [old, id].forEach((x) => { const m = x && byId(x); if (m) draw(m); });
      paintChips(); paintList();
      const li = id && rows.get(id)?.x.closest('li');   // keep the selected row in view, scrolling the list only
      if (li && li.offsetTop < ul.scrollTop) ul.scrollTop = li.offsetTop;
      else if (li && li.offsetTop + li.offsetHeight > ul.scrollTop + ul.clientHeight) ul.scrollTop = li.offsetTop + li.offsetHeight - ul.clientHeight;
    };
    const add = (m, choose) => {
      list.push(m); draw(m); emit();
      choose ? select(m.id) : paintList();
      status.textContent = T.added(name(m), span(m));
    };
    const remove = (id) => {
      const i = list.findIndex((m) => m.id === id), wasIn = ul.contains(document.activeElement);
      list = list.filter((m) => m.id !== id);
      regs.get(id)?.remove(); regs.delete(id);
      if (sel === id) sel = null;
      paintChips(); paintList(); emit();
      if (wasIn) ((ul.children[i] || ul.children[i - 1])?.querySelector('.tb-item') || markBtn).focus();
    };

    // the button next to play: "Mark now" for moments; "Mark start", then "Mark end", for parts
    const markBtn = h('button', { class: `btn tb-mark${point ? ' primary' : ''}`, type: 'button', onclick: () => {
      const t = P.media.currentTime;
      if (point) return add({ id: uid(), label: cur, start: t, end: t });
      if (from == null) {
        from = t; markBtn.textContent = T.markEnd;
        if (P.regions) { adding = true; pend = P.regions.addRegion({ start: t, color: tok('--text'), drag: false }); adding = false; }
        return;
      }
      const [a, b] = from <= t ? [from, t] : [t, from];
      cancelFrom();
      if (b - a >= 0.05) add({ id: uid(), label: cur, start: a, end: b }, true);
    } }, point ? T.markNow : T.markStart);
    const cancelFrom = () => { from = null; pend?.remove(); pend = null; markBtn.textContent = point ? T.markNow : T.markStart; };
    P.bar.append(h('span', { class: 'tb-grow' }), markBtn);

    // Delete removes the selected mark, Escape lets go of it (not while typing)
    const onKey = (e) => {
      if (!P.view?.isConnected || e.target.closest?.('input, textarea, select') || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.key === 'Delete' || e.key === 'Backspace') && sel) { e.preventDefault(); remove(sel); }
      else if (e.key === 'Escape' && (sel || from != null)) { cancelFrom(); select(null); }
    };
    document.addEventListener('keydown', onKey);
    P.gone.push(() => document.removeEventListener('keydown', onKey));

    P.ready.then(({ regions }) => {
      list.forEach(draw); dragColor();
      regions.on('region-created', (r) => {   // a part dragged out on the waveform
        if (adding || point) return;
        if (r.end - r.start < 0.02) return r.remove();
        const m = { id: r.id, label: cur, start: r.start, end: r.end };
        regs.set(m.id, r); add(m, true);
      });
      const follow = (r, end) => {
        const m = byId(r.id);
        if (!m) return;
        m.start = r.start; m.end = point ? r.start : r.end; fixRow(m);
        if (end) { emit(); paintList(); }   // the rows follow the new order
      };
      regions.on('region-update', (r) => follow(r, false));
      regions.on('region-updated', (r) => follow(r, true));
      regions.on('region-clicked', (r) => { if (byId(r.id) && sel !== r.id) select(r.id); });
    }, () => {});

    paintChips(); paintList();
    const media = P.mount(h('p', { class: 'hint tb-hint' }, point ? T.hintMoments(P.w) : T.hintRanges(P.w)));
    return { media, answer: ownKeys(h('div', { class: 'tb-ans' }, labels.length ? [cap, h('div', { class: 'tb-chips', role: 'radiogroup', 'aria-labelledby': capId }, chips)] : null, ul, empty, status)) };
  }

  B.ranges = { mount: (f, ctx) => marks(f, ctx, false), answered: (f, v) => Array.isArray(v) && v.length > 0 };
  B.moments = { mount: (f, ctx) => marks(f, ctx, true), answered: (f, v) => Array.isArray(v) && v.length > 0 };

  // trace: a slider people keep moving while the clip plays; its value is kept about 4 times a second of media time
  B.trace = {
    mount(f, ctx) {
      if (!usable(ctx.item)) return none(ctx);
      const { h } = ctx, T = words(ctx);
      const P = player(ctx, { height: ctx.item.kind === 'video' ? 72 : 96, soft: true });
      const min = Number.isFinite(+f.min) && f.min !== null ? +f.min : 0;
      const max = Number.isFinite(+f.max) && f.max !== null && +f.max > min ? +f.max : min + 100;
      const pts = new Map();   // quarter second -> value
      (Array.isArray(ctx.value) ? ctx.value : []).forEach((p) => {
        if (Array.isArray(p) && Number.isFinite(+p[0]) && Number.isFinite(+p[1])) pts.set(Math.round(p[0] * 4) / 4, +p[1]);
      });
      const keys = () => [...pts.keys()].sort((a, b) => a - b);
      const start = pts.size ? pts.get(keys()[0]) : (min + max) / 2;
      const input = h('input', { type: 'range', class: 'tb-slider', min, max, step: +((max - min) / 100).toPrecision(2), value: start,
        'aria-labelledby': `q-${f.id}` });
      const say = () => input.setAttribute('aria-valuetext', ctx.num(r2(+input.value)));
      say();
      input.addEventListener('input', () => { say(); now(); });
      input.addEventListener('keydown', (e) => { if (e.key === ' ') { e.preventDefault(); P.toggle(); } });   // Space plays here too

      // the traced line over the waveform, and a dot where the slider is now
      const NS = 'http://www.w3.org/2000/svg';
      const line = document.createElementNS(NS, 'svg'), path = document.createElementNS(NS, 'path');
      line.setAttribute('viewBox', '0 0 1000 100'); line.setAttribute('preserveAspectRatio', 'none'); line.setAttribute('class', 'tb-line');
      line.setAttribute('aria-hidden', 'true'); line.append(path);
      const dot = h('i', { class: 'tb-now', 'aria-hidden': 'true' });
      P.wave.append(line, dot);
      const y = (v) => 94 - ((v - min) / (max - min)) * 88;
      const now = () => {
        const d = P.dur();
        dot.style.left = `${d ? (P.media.currentTime / d) * 100 : 0}%`; dot.style.top = `${y(+input.value)}%`;
      };
      const drawLine = () => {
        const d = P.dur();
        let s = '', prev = -1;
        if (d) for (const k of keys()) { s += `${k - prev > 0.3 ? 'M' : 'L'}${((k / d) * 1000).toFixed(1)} ${y(pts.get(k)).toFixed(1)}`; prev = k; }
        path.setAttribute('d', s);
        done.textContent = T.traced(P.time(Math.min(pts.size * 0.25, d)), P.time(d) + P.unit());
        clear.hidden = !pts.size;
      };
      const save = () => { const ks = keys(); ctx.set(ks.length ? ks.map((k) => [k, pts.get(k)]) : null); drawLine(); };
      P.ticks.push((t) => {
        now();
        if (P.media.paused || P.media.seeking) return;
        const k = Math.round(t * 4) / 4, v = r2(+input.value);
        if (pts.get(k) !== v) { pts.set(k, v); save(); }
      });
      P.media.addEventListener('loadedmetadata', drawLine);

      const done = h('p', { class: 'tb-cap', 'aria-live': 'off' });
      const clear = h('button', { class: 'btn tb-small', type: 'button', onclick: () => { pts.clear(); save(); } }, T.startOver);
      const end = (n, w) => h('span', {}, h('b', {}, ctx.num(n)), w ? ` ${w}` : '');
      const media = P.mount(h('div', { class: 'tb-trace' }, input,
        h('div', { class: 'scale-ends' }, end(min, f.min_label), end(max, f.max_label))), h('p', { class: 'hint tb-hint' }, T.hintTrace));
      drawLine(); now();
      return { media, answer: ownKeys(h('div', { class: 'tb-ans tb-traced' }, done, clear)) };
    },
    answered: (f, v) => Array.isArray(v) && v.length > 0,
  };
})();
