// ELAN-style timeline: ruler, waveform / spectrogram, video frames, one lane per tier.
import { urls } from '../api.js';
import { h, ico, clamp, fmtTime, segColor, valStr, rafThrottle, ls } from '../util.js';
import { menu, popover, toast } from '../ui.js';
import { TileStore, loadImage } from './tiles.js';
import { tierKind, hasOverlap, sortSpeakers, DIAR } from './speakers.js';

const TILE = 512;          // tile width in px at its native zoom level
const MAX_DOM = 600;       // above this many visible segments a tier is drawn on canvas
const STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const FRAME_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];
const NON_OVERLAP = new Set(['segment', 'word', 'phone', 'window']);
// speaker turns may overlap (two people talking at once), whatever granularity an older file stored
const noOverlap = (tier) => NON_OVERLAP.has(tier.granularity) && tierKind(tier) !== DIAR;

const niceStep = (min, steps = STEPS) => steps.find(s => s >= min) || steps[steps.length - 1];

function fmtTick(t, step) {
  const m = Math.floor(t / 60), s = t - m * 60;
  const dec = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
  const ss = dec ? s.toFixed(dec).padStart(3 + dec, '0') : String(Math.round(s)).padStart(2, '0');
  if (m >= 60) return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${ss}`;
  return `${m}:${ss}`;
}

export class Timeline {
  constructor(ed) {
    this.ed = ed;
    this.pid = ed.pid;
    this.mid = ed.media.id;
    this.media = ed.media;
    this.dur = Math.max(0.05, ed.media.duration || 1);
    this.hasAudio = ed.media.modality === 'audio' || ed.media.has_audio !== false;
    this.isVideo = ed.media.modality === 'video';
    this.view = { start: 0, pps: 100 };
    this.laneW = 0;
    this.mode = ls.get('sigMode', 'wave');
    this.sigH = ls.get('sigH', 110);
    this.spec = { scale: 'linear', cmap: 'magma', fmax: 8000, drange: 70, ...ls.get('specOpts', {}) };
    this.gain = 1;
    this.peak = null;
    this.follow = ls.get('follow', true);
    this.autoPlay = ls.get('autoPlayBoundary', true);
    this.linked = ls.get('linkedBorders', false); // false: every word has its own start / end
    this.bPref = ls.get('btier:' + ed.pid, null); // tier whose boundaries are drawn & draggable on the signal
    this.bTierId = null;
    this.hoverB = null;
    this.dragB = null;
    this.preview = null;
    this.sel = null;
    this.rows = new Map();
    this.dpr = window.devicePixelRatio || 1;
    this.tiles = new TileStore(() => this.schedule());
    this.schedule = rafThrottle(() => this.render());
    this.readColors();
    this.build();
    this.bind();
  }

  // ------------------------------------------------------------------ DOM
  build() {
    this.playBtn = h('button.icon-btn.play', { title: 'Play / pause (Space)', 'aria-label': 'Play / pause', onclick: () => this.ed.player.toggle() }, ico('play', 15));
    this.clock = h('div.clock');
    this.selInfo = h('span.selinfo');
    const rate = h('select.select', { title: 'Playback speed', onchange: (e) => { this.ed.player.rate = +e.target.value; } },
      [0.5, 0.75, 1, 1.25, 1.5, 2].map(r => h('option', { value: r, selected: r === 1 }, `${r}×`)));
    this.modeCtl = h('div.seg-ctl.sm', [['wave', 'wave', 'Waveform'], ['spec', 'spectrum', 'Spectrogram'], ['both', 'split', 'Both']].map(([k, i, t]) =>
      h('button', { dataset: { v: k }, title: t, onclick: () => this.setMode(k) }, ico(i, 13), h('span', t === 'Both' ? 'Both' : t === 'Waveform' ? 'Wave' : 'Spec'))));
    const specBtn = h('button.icon-btn', { title: 'Signal display settings', 'aria-label': 'Signal display settings', onclick: () => this.specPopover(specBtn) }, ico('settings', 16));
    this.followBtn = h('button.icon-btn' + (this.follow ? '.active' : ''), { title: 'Follow playhead (F)', 'aria-label': 'Follow playhead', 'aria-pressed': String(this.follow), onclick: () => this.toggleFollow() }, ico('target', 16));
    const tiersBtn = h('button.btn.sm.tiers-btn', { title: 'Show / hide tiers', 'aria-label': 'Tiers', onclick: () => this.ed.tiersPopover(tiersBtn) }, ico('layers', 14), h('span', 'Tiers'));
    this.bSel = h('select.select', {
      title: 'Draw the boundaries of this tier on the waveform / spectrogram, where you can drag them',
      onchange: (e) => { this.bTierId = e.target.value || null; this.bPref = e.target.value || 'none'; ls.set('btier:' + this.pid, this.bPref); this.schedule(); },
    });
    const toggle = (icon, label, title, get, set) => {
      const b = h('button.tl-toggle', { title, onclick: () => { set(!get()); b.sync(); } }, ico(icon, 13), h('span', label));
      b.sync = () => { b.classList.toggle('on', get()); b.setAttribute('aria-pressed', get()); };
      b.sync();
      return b;
    };
    this.autoBtn = toggle('play', 'Auto-play', 'Play the word again after you move its border (L)',
      () => this.autoPlay, (v) => { this.autoPlay = v; ls.set('autoPlayBoundary', v); });
    this.linkBtn = toggle('link', 'Linked', 'Linked borders: move the shared border of two touching words together. Off: every word has its own border. Alt while dragging does the opposite.',
      () => this.linked, (v) => { this.linked = v; ls.set('linkedBorders', v); });
    // groups: transport | view mode | boundaries | zoom ... selection, tiers
    const grp = (...kids) => h('div.tl-grp', ...kids);
    this.bar = h('div.tl-bar',
      grp(this.playBtn, this.clock, rate), h('span.sep'),
      grp(this.modeCtl, specBtn), h('span.sep'),
      grp(h('span.blab', { title: 'Boundaries drawn on the signal' }, ico('split', 13), h('span', 'Boundaries')), this.bSel, this.linkBtn, this.autoBtn), h('span.sep'),
      grp(h('button.icon-btn', { title: 'Zoom out (−)', 'aria-label': 'Zoom out', onclick: () => this.zoomCenter(1 / 1.6) }, ico('zoom-out', 16)),
        h('button.icon-btn', { title: 'Zoom in (+)', 'aria-label': 'Zoom in', onclick: () => this.zoomCenter(1.6) }, ico('zoom-in', 16)),
        h('button.icon-btn', { title: 'Fit whole file (0)', 'aria-label': 'Fit whole file', onclick: () => this.fit() }, ico('fit', 16)),
        this.followBtn),
      h('div.grow'), this.selInfo, tiersBtn);

    this.ovCanvas = h('canvas');
    this.ovLane = h('div.tl-lane', { style: { cursor: 'pointer' } }, this.ovCanvas);
    this.ov = h('div.tl-ov', h('div.tl-head', 'Overview'), this.ovLane);

    const ovl = () => h('div.tl-ovl', h('div.selr'), h('div.hov'), h('div.snap'), h('div.ph'));
    this.rulerCanvas = h('canvas');
    this.hovTime = h('div.hov-time');
    this.rulerLane = h('div.tl-lane', this.rulerCanvas, this.hovTime);
    this.waveCanvas = h('canvas');
    this.specCanvas = h('canvas');
    this.frameCanvas = h('canvas');
    this.waveRow = h('div.tl-row.sig', h('div.tl-head', h('div.hl', ico('wave', 13), 'Waveform'), h('div.hl.sub', 'Alt + scroll: gain')), h('div.tl-lane', this.waveCanvas));
    this.specRow = h('div.tl-row.sig', h('div.tl-head', h('div.hl', ico('spectrum', 13), 'Spectrogram'), this.specLabel = h('div.hl.sub')), h('div.tl-lane', this.specCanvas));
    this.frameRow = h('div.tl-row.frames', { style: { height: '46px' } }, h('div.tl-head', h('div.hl', ico('film', 13), 'Frames')), h('div.tl-lane', this.frameCanvas));
    this.topOvl = ovl();
    this.topOvl.querySelector('.ph').classList.add('top');
    this.top = h('div.tl-top',
      h('div.tl-row.ruler', h('div.tl-head', h('span.hl', 'Time')), this.rulerLane),
      this.waveRow, this.specRow, this.isVideo ? this.frameRow : null, this.topOvl);

    this.tierOvl = ovl();
    this.dropLine = h('div.drop-line');
    this.tiersInner = h('div.tl-tiers-inner', this.tierOvl);
    this.tiersEl = h('div.tl-tiers', this.tiersInner);
    this.thumb = h('div.thumb');
    this.hs = h('div.tl-hs', this.thumb);
    this.el = h('div.tl', this.bar, this.ov, this.top, this.tiersEl, this.hs);
    this.applyMode();
  }

  readColors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    this.colors = {
      wave: v('--wave'), wave2: v('--wave-2'), ruler: v('--ruler'), lane: v('--lane'), text: v('--text-2'),
      muted: v('--muted'), line: v('--line-2'), accent: v('--accent'), danger: v('--danger'), bg1: v('--bg-1'), faint: v('--faint'),
      bnd: v('--bnd'), bndBg: v('--bnd-bg'), bndText: v('--text'), warn: v('--warn'),
    };
  }

  // ------------------------------------------------------------------ geometry
  get end() { return this.view.start + this.laneW / this.view.pps; }
  get span() { return this.laneW / this.view.pps; }
  t2x(t) { return (t - this.view.start) * this.view.pps; }
  x2t(x) { return this.view.start + x / this.view.pps; }
  minPps() { return this.laneW / this.dur; }

  clampView() {
    const v = this.view;
    v.pps = clamp(v.pps, this.minPps(), 8000);
    v.start = clamp(v.start, 0, Math.max(0, this.dur - this.laneW / v.pps));
  }

  setView(start, pps = this.view.pps) {
    this.view.start = start;
    this.view.pps = pps;
    this.clampView();
    this.schedule();
  }

  fit() { this.setView(0, this.minPps()); }

  zoomAt(f, x) {
    const t = this.x2t(x);
    const pps = clamp(this.view.pps * f, this.minPps(), 8000);
    this.setView(t - x / pps, pps);
  }

  zoomCenter(f) {
    const t = this.ed.player.t;
    const x = t >= this.view.start && t <= this.end ? this.t2x(t) : this.laneW / 2;
    this.zoomAt(f, x);
  }

  zoomTo(a, b, pad = 0.15) {
    const d = Math.max(0.05, b - a);
    const pps = this.laneW / (d * (1 + 2 * pad));
    this.setView(a - d * pad, pps);
  }

  ensureVisible(a, b) {
    if (a >= this.view.start && b <= this.end) return;
    const span = this.span;
    if (b - a > span * 0.9) { this.zoomTo(a, b); return; }
    this.setView(a - span * 0.25);
  }

  // ------------------------------------------------------------------ setup
  mount() {
    this.ro = new ResizeObserver(() => this.measure());
    this.ro.observe(this.el);
    this.measure();
  }

  measure() {
    const w = this.rulerLane.clientWidth;
    if (!w) return;
    const first = !this.laneW;
    const keepT = this.laneW ? this.view.start : 0;
    this.laneW = w;
    this.dpr = window.devicePixelRatio || 1;
    if (first) {
      if (this.dur <= 90) this.fit();
      else this.setView(0, w / 45);
    } else {
      this.view.start = keepT;
      this.clampView();
    }
    this.sizeCanvases();
    this.schedule();
  }

  sizeCanvases() {
    const W = this.laneW;
    const set = (c, w, hh) => {
      c.width = Math.round(w * this.dpr);
      c.height = Math.round(hh * this.dpr);
      c.style.width = w + 'px';
      c.style.height = hh + 'px';
    };
    set(this.rulerCanvas, W, 24);
    set(this.ovCanvas, W, 29);
    const waveH = this.mode === 'both' ? 64 : this.sigH;
    this.waveRow.style.height = waveH + 'px';
    this.specRow.style.height = this.sigH + 'px';
    set(this.waveCanvas, W, waveH);
    set(this.specCanvas, W, this.sigH);
    if (this.isVideo) set(this.frameCanvas, W, 46);
    for (const r of this.rows.values()) if (r.dense) this.sizeDense(r);
  }

  setMode(m) {
    this.mode = m;
    ls.set('sigMode', m);
    this.applyMode();
    this.sizeCanvases();
    this.schedule();
  }

  applyMode() {
    for (const b of this.modeCtl.children) b.classList.toggle('on', b.dataset.v === this.mode);
    this.waveRow.style.display = this.mode === 'spec' ? 'none' : '';
    this.specRow.style.display = this.mode === 'wave' ? 'none' : '';
    this.specLabel.textContent = `${this.spec.scale === 'mel' ? 'mel' : 'linear'} · ${this.spec.fmax / 1000} kHz`;
  }

  toggleAutoPlay() {
    this.autoPlay = !this.autoPlay;
    ls.set('autoPlayBoundary', this.autoPlay);
    this.autoBtn.sync();
    toast(`Auto-play after moving a border: ${this.autoPlay ? 'on' : 'off'}`, { timeout: 1500 });
  }

  toggleFollow() {
    this.follow = !this.follow;
    ls.set('follow', this.follow);
    this.followBtn.classList.toggle('active', this.follow);
    this.followBtn.setAttribute('aria-pressed', String(this.follow));
  }

  specPopover(anchor) {
    const sel = (label, key, opts, fmt = (x) => x) => h('label.field', h('span', label),
      h('select.select.sm', { onchange: (e) => { this.spec[key] = isNaN(+e.target.value) ? e.target.value : +e.target.value; this.specChanged(); } },
        opts.map(o => h('option', { value: o, selected: this.spec[key] === o }, fmt(o)))));
    popover(anchor, h('div.spec-pop',
      sel('Frequency scale', 'scale', ['linear', 'mel']),
      sel('Max frequency', 'fmax', [4000, 5000, 8000], (x) => `${x / 1000} kHz`),
      sel('Colors', 'cmap', ['magma', 'viridis', 'inferno', 'ice', 'gray']),
      sel('Dynamic range', 'drange', [50, 60, 70, 80, 90], (x) => `${x} dB`),
      h('label.field', h('span', 'Signal height'),
        h('input', { type: 'range', min: 60, max: 260, value: this.sigH, style: { accentColor: 'var(--accent)' },
          oninput: (e) => { this.sigH = +e.target.value; ls.set('sigH', this.sigH); this.tiles.clear('s:'); this.sizeCanvases(); this.schedule(); } }))),
    { placement: 'bottom-start' });
  }

  specChanged() {
    ls.set('specOpts', this.spec);
    this.tiles.clear('s:');
    this.applyMode();
    this.schedule();
  }

  // ------------------------------------------------------------------ tiers
  setTiers(list, activeId) {
    this.fillBoundarySelect();
    for (const r of this.rows.values()) r.row.remove();
    this.rows.clear();
    const frag = [];
    for (const { tier, depth } of list) {
      const r = this.makeRow(tier, depth);
      this.rows.set(tier.id, r);
      frag.push(r.row);
    }
    this.tiersInner.replaceChildren(...frag, this.dropLine, this.tierOvl);
    if (!list.length) {
      this.tiersInner.prepend(h('div.empty', { style: { padding: '28px' } },
        h('div', 'No tiers yet. Run tasks on this file, import an ELAN / Praat / JSON file, or add an empty tier from "Tiers".')));
    }
    this.setActive(activeId);
    this.schedule();
  }

  fillBoundarySelect() {
    const tiers = this.ed.doc.tiers.filter(t => t.granularity !== 'file');
    let id = this.bPref === 'none' ? null : (tiers.find(t => t.id === this.bPref)?.id
      || (tiers.find(t => t.task_id === 'forced_alignment') || tiers.find(t => t.granularity === 'word'))?.id || null);
    this.bTierId = id;
    this.bSel.replaceChildren(h('option', { value: '' }, 'Off'),
      tiers.map(t => h('option', { value: t.id, selected: t.id === id }, t.name)));
    this.bSel.value = id || '';
  }

  makeRow(tier, depth) {
    const small = tier.granularity === 'word' || tier.granularity === 'phone';
    const height = small ? 28 : tier.output === 'text' ? 38 : 30;
    const count = h('small');
    const more = h('button.icon-btn.sm', { title: 'Tier options', 'aria-label': `${tier.name} options`, onclick: (e) => { e.stopPropagation(); this.ed.tierMenu(tier.id, more); } }, ico('more', 14));
    const head = h('div.tl-head', { title: `${tier.title || tier.name}\nDrag to reorder (applies to every file in the project)`, onclick: () => this.ed.setActiveTier(tier.id) },
      h('span.grip', ico('grip', 12)), h('span.sw'), h('span.tn', tier.name, count), more);
    const layer = h('div', { style: { position: 'absolute', inset: '0' } });
    const lane = h('div.tl-lane', layer);
    const row = h('div.tl-row.tier' + (small ? '.w' : '') + (depth ? '.child' : ''),
      { style: { height: height + 'px', '--c': tier.color }, dataset: { tier: tier.id } }, head, lane);
    const r = { tier, row, head, lane, layer, count, els: new Map(), dense: false, canvas: null, hint: null, height };
    head.addEventListener('pointerdown', (e) => this.onHeadDown(e, r));
    return r;
  }

  /** Drag a tier header up / down to change the channel order (saved for the whole project). */
  onHeadDown(e, r) {
    if (e.button !== 0 || e.target.closest('button')) return;
    const y0 = e.clientY;
    let dragging = false, target = null, scrollTimer = null;
    const pick = (ev) => {
      const others = [...this.rows.values()].filter(x => x !== r);
      if (!others.length) return null;
      for (const x of others) {
        const rc = x.row.getBoundingClientRect();
        if (ev.clientY >= rc.top && ev.clientY < rc.bottom) return { x, after: ev.clientY > rc.top + rc.height / 2 };
      }
      const first = others[0].row.getBoundingClientRect();
      return ev.clientY < first.top ? { x: others[0], after: false } : { x: others[others.length - 1], after: true };
    };
    this.drag((ev) => {
      if (!dragging && Math.abs(ev.clientY - y0) < 5) return;
      if (!dragging) { dragging = true; r.row.classList.add('dragging'); document.body.style.cursor = 'grabbing'; }
      target = pick(ev);
      if (target) {
        const top = target.after ? target.x.row.offsetTop + target.x.row.offsetHeight : target.x.row.offsetTop;
        this.dropLine.style.display = 'block';
        this.dropLine.style.top = (top - 1) + 'px';
      }
      const box = this.tiersEl.getBoundingClientRect();
      clearInterval(scrollTimer);
      const d = ev.clientY < box.top + 24 ? -12 : ev.clientY > box.bottom - 24 ? 12 : 0;
      if (d) scrollTimer = setInterval(() => { this.tiersEl.scrollTop += d; }, 30);
    }, () => {
      clearInterval(scrollTimer);
      this.dropLine.style.display = 'none';
      r.row.classList.remove('dragging');
      document.body.style.cursor = '';
      if (dragging && target) this.ed.reorderTier(r.tier.id, target.x.tier.id, target.after);
    });
  }

  setActive(id) {
    this.activeId = id;
    for (const [tid, r] of this.rows) r.row.classList.toggle('active', tid === id);
  }

  scrollTierIntoView(id) {
    const r = this.rows.get(id);
    if (!r) return;
    const top = r.row.offsetTop, bottom = top + r.row.offsetHeight, el = this.tiersEl;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (bottom > el.scrollTop + el.clientHeight) el.scrollTop = bottom - el.clientHeight;
  }

  // ------------------------------------------------------------------ render
  render() {
    if (!this.laneW) return;
    this.clampView();
    this.drawRuler();
    if (this.mode !== 'spec') this.drawWave();
    if (this.mode !== 'wave') this.drawSpec();
    if (this.isVideo) this.drawFrames();
    this.drawOverview();
    this.renderTiers();
    this.renderOverlays();
    this.renderScrollbar();
    this.renderClock();
  }

  ctx(c) {
    const g = c.getContext('2d');
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    return g;
  }

  drawRuler() {
    const g = this.ctx(this.rulerCanvas), W = this.laneW, H = 24;
    g.clearRect(0, 0, W, H);
    const step = niceStep(90 / this.view.pps);
    const minor = step / (String(step)[0] === '2' ? 4 : 5);
    const t0 = Math.floor(this.view.start / minor) * minor;
    g.font = '11px "IBM Plex Mono", ui-monospace, monospace';
    g.textBaseline = 'top';
    for (let t = t0; t <= this.end + minor; t += minor) {
      const x = Math.round(this.t2x(t)) + 0.5;
      const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
      g.fillStyle = major ? this.colors.muted : this.colors.line;
      g.fillRect(x, major ? H - 9 : H - 5, 1, major ? 9 : 5);
      if (major && t >= -1e-9) {
        g.fillStyle = this.colors.text;
        g.fillText(fmtTick(Math.max(0, t), step), x + 4, 4);
      }
    }
    // file end marker
    const xe = this.t2x(this.dur);
    if (xe < W) { g.fillStyle = this.colors.bg1; g.fillRect(xe, 0, W - xe, H); }
  }

  level() {
    const L = Math.ceil(2 * Math.log2(this.view.pps));
    return { L, ts: TILE / 2 ** (L / 2) };
  }

  tileRange(ts) {
    const i0 = Math.max(0, Math.floor(this.view.start / ts));
    const i1 = Math.min(Math.floor(Math.min(this.end, this.dur) / ts), Math.floor(this.dur / ts));
    return [i0, i1];
  }

  peaksLoader(a, b, n) {
    return () => fetch(urls.peaks(this.pid, this.mid, a, b, n)).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(d => { if (this.peak == null) this.peak = d.peak; return d; });
  }

  drawWave() {
    const c = this.waveCanvas, g = this.ctx(c), W = this.laneW, H = parseFloat(c.style.height);
    g.clearRect(0, 0, W, H);
    g.fillStyle = this.colors.line;
    g.fillRect(0, Math.round(H / 2), W, 1);
    if (!this.hasAudio) {
      g.fillStyle = this.colors.muted;
      g.font = '12px "IBM Plex Sans", sans-serif';
      g.fillText('No audio track', 12, H / 2 - 8);
      return;
    }
    const { L, ts } = this.level();
    const [i0, i1] = this.tileRange(ts);
    for (let i = i0; i <= i1; i++) {
      const a = i * ts;
      const d = this.tiles.get(`w:${L}:${i}`, this.peaksLoader(a, a + ts, TILE));
      if (d) { this.drawPeaks(g, d, a, ts, a, a + ts, H); continue; }
      for (let k = 1; k <= 8; k++) {
        const L2 = L - 2 * k, ts2 = ts * 2 ** k, i2 = Math.floor(a / ts2);
        const d2 = this.tiles.peek(`w:${L2}:${i2}`);
        if (d2) { this.drawPeaks(g, d2, i2 * ts2, ts2, a, a + ts, H); break; }
      }
    }
    this.drawBounds(g, W, H, 'wave');
    const x = this.t2x(this.dur);
    if (x < W) { g.fillStyle = this.colors.bg1; g.fillRect(x, 0, W - x, H); }
  }

  drawPeaks(g, d, t0, tSec, clipA, clipB, H) {
    const n = d.min.length, colT = tSec / n, pps = this.view.pps;
    const scale = (H / 2 - 2) * this.gain / (this.peak || d.peak || 1);
    const mid = H / 2;
    const j0 = Math.max(0, Math.floor((Math.max(clipA, this.view.start) - t0) / colT));
    const j1 = Math.min(n, Math.ceil((Math.min(clipB, this.end) - t0) / colT));
    g.fillStyle = this.colors.wave;
    g.strokeStyle = this.colors.wave;
    if (colT * 16000 < 2.5) { // sample level: draw a line
      g.beginPath();
      for (let j = j0; j < j1; j++) {
        const x = this.t2x(t0 + (j + 0.5) * colT), y = mid - d.max[j] * scale;
        j === j0 ? g.moveTo(x, y) : g.lineTo(x, y);
      }
      g.lineWidth = 1.2;
      g.stroke();
      return;
    }
    const cw = colT * pps;
    for (let j = j0; j < j1; j++) {
      const x = this.t2x(t0 + j * colT);
      const y1 = mid - d.max[j] * scale, y2 = mid - d.min[j] * scale;
      g.fillRect(x, Math.min(y1, mid - 0.5), Math.max(1, cw + 0.3), Math.max(1, y2 - y1));
    }
  }

  drawSpec() {
    const c = this.specCanvas, g = this.ctx(c), W = this.laneW, H = this.sigH;
    g.fillStyle = '#000';
    g.clearRect(0, 0, W, H);
    if (!this.hasAudio) return;
    const { L, ts } = this.level();
    const [i0, i1] = this.tileRange(ts);
    const hp = Math.round(H * this.dpr);
    const o = this.spec;
    const okey = `${hp}:${o.scale}:${o.cmap}:${o.fmax}:${o.drange}`;
    for (let i = i0; i <= i1; i++) {
      const a = i * ts;
      const key = `s:${L}:${i}:${okey}`;
      const img = this.tiles.get(key, () => loadImage(urls.spec(this.pid, this.mid,
        { start: a.toFixed(4), end: (a + ts).toFixed(4), w: TILE, h: hp, scale: o.scale, cmap: o.cmap, fmax: o.fmax, drange: o.drange })));
      if (img) { this.blit(g, img, a, ts, a, a + ts, H); continue; }
      for (let k = 1; k <= 8; k++) {
        const ts2 = ts * 2 ** k, i2 = Math.floor(a / ts2);
        const img2 = this.tiles.peek(`s:${L - 2 * k}:${i2}:${okey}`);
        if (img2) { this.blit(g, img2, i2 * ts2, ts2, a, a + ts, H); break; }
      }
    }
    this.drawBounds(g, W, H, 'spec');
    const x = this.t2x(this.dur);
    if (x < W) { g.fillStyle = this.colors.bg1; g.fillRect(x, 0, W - x, H); }
  }

  /** Effective times of a segment while a boundary is being dragged. */
  eff(s) {
    const p = this.preview?.get(s.id);
    return p ? { ...s, ...p } : s;
  }

  /** Boundaries (and labels) of the chosen tier drawn on top of the waveform / spectrogram, Praat-style. */
  drawBounds(g, W, H, kind) {
    const doc = this.ed.doc;
    const tier = this.bTierId && doc.tier(this.bTierId);
    if (!tier) return;
    const segs = doc.visible(tier.id, this.view.start - 1, this.end + 1).map(s => this.eff(s));
    if (!segs.length || segs.length > 2500) return;
    const sel = segs.find(s => s.id === this.ed.selId);
    if (sel) {
      g.fillStyle = this.colors.accent + '2e';
      g.fillRect(this.t2x(sel.start), 0, this.t2x(sel.end) - this.t2x(sel.start), H);
    }
    const xs = new Set();
    for (const s of segs) for (const t of [s.start, s.end]) {
      const x = Math.round(this.t2x(t));
      if (x >= -1 && x <= W + 1) xs.add(x);
    }
    const dense = xs.size > W / 3;
    g.fillStyle = kind === 'spec' ? 'rgba(255,255,255,0.55)' : this.colors.bnd;
    for (const x of xs) g.fillRect(x, dense ? H - 8 : 0, 1, dense ? 8 : H);
    const hv = this.dragB || this.hoverB;
    if (hv?.id) {  // the word that will move (independent borders)
      const ts = segs.find(s => s.id === hv.id);
      if (ts) { g.fillStyle = this.colors.warn + '2a'; g.fillRect(this.t2x(ts.start), 0, this.t2x(ts.end) - this.t2x(ts.start), H); }
    }
    const hb = hv ? hv.t : null;
    if (hb != null) {
      const x = this.t2x(hb);
      g.fillStyle = this.colors.warn;
      g.fillRect(x - 1, 0, 3, H);
      g.beginPath(); g.moveTo(x - 6, 0); g.lineTo(x + 6, 0); g.lineTo(x, 7); g.fill();
      g.beginPath(); g.moveTo(x - 6, H); g.lineTo(x + 6, H); g.lineTo(x, H - 7); g.fill();
    }
    if (dense || (kind === 'spec' && this.mode === 'both')) return;
    g.font = `${tier.granularity === 'phone' ? 10 : 11}px "IBM Plex Sans", system-ui, sans-serif`;
    g.textBaseline = 'middle';
    for (const s of segs) {
      const x0 = this.t2x(s.start), x1 = this.t2x(s.end), txt = valStr(s.value);
      if (!txt || x1 < 0 || x0 > W) continue;
      const tw = g.measureText(txt).width;
      if (x1 - x0 < tw + 8) continue;
      const cx = clamp((x0 + x1) / 2, Math.max(x0, 0) + tw / 2 + 4, Math.min(x1, W) - tw / 2 - 4);
      g.fillStyle = s.id === this.ed.selId ? this.colors.accent : this.colors.bndBg;
      g.fillRect(cx - tw / 2 - 4, 3, tw + 8, 15);
      g.fillStyle = s.id === this.ed.selId ? this.colors.bg1 : this.colors.bndText;
      g.fillText(txt, cx - tw / 2, 11);
    }
  }

  redrawSignal() {
    if (this.mode !== 'spec') this.drawWave();
    if (this.mode !== 'wave') this.drawSpec();
  }

  /** Border under the pointer: {t, id, edge}. On a border shared by two words, the selected word wins,
   *  otherwise the word on the side of the pointer (left of the line = end of the left word). */
  hitBoundary(x) {
    if (!this.bTierId) return null;
    const cands = [];
    for (const s0 of this.ed.doc.visible(this.bTierId, this.x2t(x - 8), this.x2t(x + 8))) {
      for (const edge of ['start', 'end']) {
        const ex = this.t2x(s0[edge]), d = Math.abs(ex - x);
        if (d <= 6) cands.push({ s: s0, edge, d, ex });
      }
    }
    if (!cands.length) return null;
    const minD = Math.min(...cands.map(c => c.d));
    const near = cands.filter(c => c.d <= minD + 1.5);
    const pick = near.find(c => c.s.id === this.ed.selId)
      || near.find(c => (x < c.ex ? c.edge === 'end' : c.edge === 'start')) || near[0];
    return { t: pick.s[pick.edge], id: pick.s.id, edge: pick.edge };
  }

  /** Independent border: only this segment's edge moves (its phones are rescaled); it stops at the neighbour. */
  independentPlan(tid, hint) {
    const doc = this.ed.doc, s = doc.seg(hint.id), tol = 0.012;
    const tier = doc.tier(tid);
    const { down } = this.family(tid);
    const { prev, next } = doc.neighbors(s);
    let lo = 0, hi = this.dur, block = null;
    if (hint.edge === 'start') {
      hi = s.end - 0.01;
      if (prev && noOverlap(tier) && prev.end <= s.start + tol) { lo = Math.max(lo, prev.end); block = 'lo'; }
    } else {
      lo = s.start + 0.01;
      if (next && noOverlap(tier) && next.start >= s.end - tol) { hi = Math.min(hi, next.start); block = 'hi'; }
    }
    const kids = [];
    for (const T of down) {
      for (const k of doc.visible(T.id, s.start, s.end)) {
        if (k.start >= s.start - tol && k.end <= s.end + tol) kids.push({ k, parent: s });
      }
    }
    return { t0: s[hint.edge], lo, hi, block, items: [{ s, edge: hint.edge, own: true }], kids };
  }

  /** The tier, its ancestors (e.g. ASR above words) and descendants (e.g. phones below words). */
  family(tid) {
    const doc = this.ed.doc, tier = doc.tier(tid);
    const byKey = (k) => doc.tiers.find(t => t.id === k || t.task_id === k);
    const up = [];
    for (let p = tier.parent && byKey(tier.parent); p && !up.includes(p) && up.length < 6; p = p.parent && byKey(p.parent)) up.push(p);
    const down = [], queue = [tier];
    while (queue.length) {
      const cur = queue.shift();
      for (const t of doc.tiers) {
        if (t !== tier && !down.includes(t) && t.parent && (t.parent === cur.id || t.parent === cur.task_id)) { down.push(t); queue.push(t); }
      }
    }
    return { tier, up, down };
  }

  /** Which segment edges move together when the boundary at t0 of tier tid is dragged. */
  boundaryPlan(tid, t0, hint) {
    const doc = this.ed.doc, tol = Math.max(0.012, 2 / this.view.pps);
    const { tier, up, down } = this.family(tid);
    let items = [];
    const near = (T) => {
      for (const s of doc.visible(T.id, t0 - tol, t0 + tol)) {
        if (Math.abs(s.start - t0) <= tol) items.push({ s, edge: 'start', own: T === tier });
        if (Math.abs(s.end - t0) <= tol) items.push({ s, edge: 'end', own: T === tier });
      }
    };
    near(tier);
    up.forEach(near);
    if (hint) {
      const hs = doc.seg(hint.id);
      items = items.filter(i => i.s.id !== hint.id);
      if (hs) items.push({ s: hs, edge: hint.edge, own: true });
    }
    const seen = new Map();
    for (const it of items) {  // a very short segment may have both edges near t0: keep the closer one
      const prev = seen.get(it.s.id);
      if (!prev || Math.abs(it.s[it.edge] - t0) < Math.abs(prev.s[prev.edge] - t0)) seen.set(it.s.id, it);
    }
    items = [...seen.values()];
    let lo = 0, hi = this.dur;
    for (const it of items) {
      if (it.edge === 'start') hi = Math.min(hi, it.s.end - 0.01); else lo = Math.max(lo, it.s.start + 0.01);
      if (it.own && noOverlap(tier)) {
        const nb = doc.neighbors(it.s);
        if (it.edge === 'end' && nb.next && !items.some(j => j.s === nb.next)) hi = Math.min(hi, nb.next.start);
        if (it.edge === 'start' && nb.prev && !items.some(j => j.s === nb.prev)) lo = Math.max(lo, nb.prev.end);
      }
    }
    const kids = [];
    for (const it of items) {
      if (!it.own) continue;
      for (const T of down) {
        for (const k of doc.visible(T.id, it.s.start, it.s.end)) {
          if (k.start >= it.s.start - tol && k.end <= it.s.end + tol) kids.push({ k, parent: it.s });
        }
      }
    }
    return { t0, lo, hi, items, kids };
  }

  applyPlan(plan, t) {
    const m = new Map();
    for (const it of plan.items) {
      const cur = m.get(it.s.id) || { start: it.s.start, end: it.s.end };
      cur[it.edge] = t;
      m.set(it.s.id, cur);
    }
    for (const { k, parent } of plan.kids) {
      const np = m.get(parent.id);
      if (!np) continue;
      const f = (np.end - np.start) / Math.max(1e-6, parent.end - parent.start);
      m.set(k.id, { start: np.start + (k.start - parent.start) * f, end: np.start + (k.end - parent.start) * f });
    }
    return m;
  }

  /** Drag a boundary: linked edges move together, child segments are rescaled, optional auto-play on release. */
  startBoundaryDrag(tid, t0, xAt, hint, linked = this.linked) {
    const plan = linked || !hint ? this.boundaryPlan(tid, t0, hint) : this.independentPlan(tid, hint);
    if (!plan.items.length) return false;
    this.dragB = { t: plan.t0 ?? t0, id: linked ? null : hint?.id };
    let t = this.dragB.t, moved = false;
    this.drag((ev) => {
      moved = true;
      const raw = this.x2t(xAt(ev));
      t = clamp(raw, plan.lo, plan.hi);
      if (!linked && !Timeline.blockHintShown && ((plan.block === 'lo' && raw < plan.lo - 4 / this.view.pps) || (plan.block === 'hi' && raw > plan.hi + 4 / this.view.pps))) {
        Timeline.blockHintShown = true;
        toast('This word touches its neighbour. Shrink the neighbour first, or hold Alt (or switch on linked borders) to move both.', { timeout: 6000 });
      }
      this.dragB.t = t;
      this.preview = this.applyPlan(plan, t);
      this.schedule();
    }, () => {
      const map = moved && Math.abs(t - (plan.t0 ?? t0)) > 5e-4 ? this.applyPlan(plan, t) : null;
      this.preview = null;
      this.dragB = null;
      if (map) {
        this.ed.commitTimes(map, new Set(plan.items.map(i => i.s.id)), linked ? 'Move shared border' : 'Move word border');
        if (this.autoPlay) {
          const own = plan.items.filter(i => i.own).map(i => map.get(i.s.id));
          if (own.length) this.ed.player.playRange(Math.min(...own.map(r => r.start)), Math.max(...own.map(r => r.end)));
        }
      }
      this.schedule();
    });
    return true;
  }

  blit(g, img, t0, tSec, a, b, H) {
    const sx = ((a - t0) / tSec) * img.width, sw = ((b - a) / tSec) * img.width;
    const x0 = Math.floor(this.t2x(a)), x1 = Math.ceil(this.t2x(b));
    g.imageSmoothingEnabled = true;
    g.drawImage(img, sx, 0, sw, img.height, x0, 0, x1 - x0 + 0.5, H);
  }

  drawFrames() {
    const c = this.frameCanvas, g = this.ctx(c), W = this.laneW, H = 46;
    g.clearRect(0, 0, W, H);
    const aspect = (this.media.width && this.media.height) ? this.media.width / this.media.height : 16 / 9;
    const tw = Math.round(H * aspect);
    const dt = niceStep(tw / this.view.pps, FRAME_STEPS);
    const k0 = Math.floor(this.view.start / dt), k1 = Math.ceil(Math.min(this.end, this.dur) / dt);
    const hp = Math.round(H * this.dpr);
    for (let k = k0; k < k1; k++) {
      const t = k * dt;
      const tq = Math.round(Math.min(t + Math.min(dt / 2, 0.5), this.dur - 0.05) * 10) / 10;
      const img = this.tiles.get(`f:${tq}:${hp}`, () => loadImage(urls.frame(this.pid, this.mid, Math.max(0, tq), hp)));
      const x = Math.round(this.t2x(t));
      if (img) g.drawImage(img, x, 0, tw, H);
      else { g.fillStyle = this.colors.lane; g.fillRect(x, 0, tw, H); }
      g.fillStyle = this.colors.bg1;
      g.fillRect(x + tw, 0, 1, H);
    }
  }

  drawOverview() {
    const c = this.ovCanvas, g = this.ctx(c), W = this.laneW, H = 29;
    g.clearRect(0, 0, W, H);
    if (this.hasAudio) {
      const n = Math.max(64, Math.round(W / 2));
      const d = this.tiles.get(`ov:${n}`, this.peaksLoader(0, this.dur, n));
      if (d) {
        const pk = this.peak || d.peak || 1, bw = W / n, mid = H / 2;
        g.fillStyle = this.colors.wave2;
        for (let j = 0; j < n; j++) {
          const a = Math.min(1, Math.max(Math.abs(d.max[j]), Math.abs(d.min[j])) / pk);
          g.fillRect(j * bw, mid - a * (mid - 2), Math.max(1, bw - 0.5), Math.max(1, 2 * a * (mid - 2)));
        }
      }
    }
    // tier density markers: first visible tier with segments
    const k = W / this.dur;
    const x = this.view.start * k, w = Math.max(3, this.span * k);
    g.fillStyle = this.colors.accent + '22';
    g.fillRect(x, 0, w, H);
    g.strokeStyle = this.colors.accent;
    g.lineWidth = 1;
    g.strokeRect(x + 0.5, 0.5, w - 1, H - 1);
    const px = this.ed.player.t * k;
    g.fillStyle = this.colors.danger;
    g.fillRect(px, 0, 1, H);
  }

  renderTiers() {
    const pad = this.span * 0.02, t0 = this.view.start - pad, t1 = this.end + pad;
    const doc = this.ed.doc;
    for (const r of this.rows.values()) {
      const segs = doc.visible(r.tier.id, t0, t1);
      r.count.textContent = doc.list(r.tier.id).length || '';
      if (segs.length > MAX_DOM) { this.drawDense(r, segs); continue; }
      if (r.dense) this.undense(r);
      if (r.layoutVer !== doc.version) this.layoutTier(r);
      const { rowOf, nRows } = r;
      const rh = (r.curH - 6) / nRows;
      const seen = new Set();
      for (const s of segs) {
        let el = r.els.get(s.id);
        if (!el) {
          el = h('div.seg' + (r.tier.output === 'label' ? '.label' : ''), h('span.t'), h('i.cf'));
          r.els.set(s.id, el);
          r.layer.append(el);
        }
        this.updateSeg(el, this.eff(s), r.tier);
        const top = nRows > 1 ? 3 + Math.min(rowOf.get(s.id) ?? 0, nRows - 1) * rh : null;
        el.style.top = top == null ? '' : top + 'px';
        el.style.bottom = top == null ? '' : 'auto';
        el.style.height = top == null ? '' : Math.max(6, rh - 1) + 'px';
        el.style.fontSize = nRows > 2 ? '11px' : '';
        seen.add(s.id);
      }
      for (const [id, el] of r.els) if (!seen.has(id)) { el.remove(); r.els.delete(id); }
    }
  }

  /** Overlapping segments (e.g. sound events) get stacked sub-rows instead of being drawn on top of each other.
   *  Overlaps under 20 ms (rounding at word boundaries) are ignored. */
  layoutTier(r) {
    const rowOf = new Map(), ends = [];
    const list = this.ed.doc.list(r.tier.id);
    if (tierKind(r.tier) === DIAR && hasOverlap(list)) {
      // overlapping speaker turns: one lane per speaker, ELAN style
      const spk = sortSpeakers(list.filter(s => s.start != null).map(s => valStr(s.value)));
      if (spk.length > 1 && spk.length <= 6) {
        for (const s of list) rowOf.set(s.id, spk.indexOf(valStr(s.value)));
        r.rowOf = rowOf;
        r.nRows = spk.length;
        r.curH = Math.max(r.height, r.nRows * 21 + 6);
        r.row.style.height = r.curH + 'px';
        r.row.classList.add('lanes');
        r.layoutVer = this.ed.doc.version;
        return;
      }
    }
    r.row.classList.remove('lanes');
    for (const s of list) {
      if (s.start == null) continue;
      let k = ends.findIndex(e => e <= s.start + 0.02);
      if (k < 0) { k = ends.length; ends.push(s.end); } else ends[k] = Math.max(ends[k], s.end);
      rowOf.set(s.id, k);
    }
    r.rowOf = rowOf;
    r.nRows = Math.max(1, Math.min(ends.length, 6));
    r.curH = r.nRows > 1 ? Math.max(r.height, r.nRows * 21 + 6) : r.height;
    r.row.style.height = r.curH + 'px';
    r.layoutVer = this.ed.doc.version;
  }

  updateSeg(el, s, tier) {
    const W = this.laneW;
    const x0 = this.t2x(s.start), x1 = this.t2x(s.end);
    const cl = x0 < -3, cr = x1 > W + 3;
    const L = cl ? -3 : x0, R = cr ? W + 3 : x1;
    const w = Math.max(2, R - L);
    el.style.left = L + 'px';
    el.style.width = w + 'px';
    const v = valStr(s.value);
    const sel = this.ed.selId === s.id;
    const low = !s.reviewed && s.confidence != null && s.confidence < this.ed.lowThr;
    const key = `${v}|${s.confidence}|${s.reviewed}|${s.source}|${sel}|${low}`;
    if (el._k !== key) {
      el._k = key;
      el.dataset.id = s.id;
      el.firstChild.textContent = v || (tier.output === 'label' ? '?' : '');
      el.style.setProperty('--c', segColor(tier, s.value));
      el.style.setProperty('--cf', s.confidence == null ? 0 : s.confidence);
      el.classList.toggle('sel', sel);
      el.classList.toggle('low', low);
      el.classList.toggle('rev', !!s.reviewed);
      el.classList.toggle('mock', s.source === 'mock');
      el.classList.toggle('blank', !v);
      el.title = `${v || '(empty)'}\n${fmtTime(s.start)} – ${fmtTime(s.end)}` +
        (s.confidence != null ? `\nconfidence ${s.confidence.toFixed(2)}` : '') + `\n${s.source}${s.reviewed ? ' · reviewed' : ''}`;
    }
    el.classList.toggle('tiny', w < 14);
    el.classList.toggle('cl', cl);
    el.classList.toggle('cr', cr);
  }

  sizeDense(r) {
    const hh = r.lane.clientHeight || r.height;
    r.canvas.width = Math.round(this.laneW * this.dpr);
    r.canvas.height = Math.round(hh * this.dpr);
    r.canvas.style.width = this.laneW + 'px';
    r.canvas.style.height = hh + 'px';
  }

  drawDense(r, segs) {
    if (!r.dense) {
      r.dense = true;
      for (const el of r.els.values()) el.remove();
      r.els.clear();
      r.canvas = h('canvas');
      r.hint = h('span.dense-hint', 'zoom in to edit');
      r.lane.append(r.canvas, r.hint);
      r.row.classList.add('dense');
      this.sizeDense(r);
    }
    const g = this.ctx(r.canvas), H = parseFloat(r.canvas.style.height);
    g.clearRect(0, 0, this.laneW, H);
    let lastX = -1;
    for (const s of segs) {
      const x0 = this.t2x(s.start), x1 = this.t2x(s.end);
      g.fillStyle = segColor(r.tier, s.value) + '55';
      g.fillRect(x0, 4, Math.max(1, x1 - x0), H - 8);
      const xb = Math.round(x0);
      if (xb !== lastX) { g.fillStyle = segColor(r.tier, s.value); g.fillRect(xb, 4, 1, H - 8); lastX = xb; }
    }
  }

  undense(r) {
    r.dense = false;
    r.canvas?.remove();
    r.hint?.remove();
    r.canvas = r.hint = null;
    r.row.classList.remove('dense');
  }

  renderOverlays() {
    const t = this.ed.player.t;
    const x = this.t2x(t);
    for (const o of [this.topOvl, this.tierOvl]) {
      const ph = o.querySelector('.ph');
      ph.style.display = x < -1 || x > this.laneW + 1 ? 'none' : '';
      ph.style.transform = `translateX(${x}px)`;
      const sr = o.querySelector('.selr');
      if (this.sel) {
        const a = this.t2x(this.sel.a), b = this.t2x(this.sel.b);
        sr.style.display = 'block';
        sr.style.left = a + 'px';
        sr.style.width = Math.max(1, b - a) + 'px';
      } else sr.style.display = 'none';
    }
    this.selInfo.textContent = this.sel ? `sel ${fmtTime(this.sel.a)} – ${fmtTime(this.sel.b)} (${(this.sel.b - this.sel.a).toFixed(3)} s)` : '';
  }

  renderScrollbar() {
    const tw = this.hs.clientWidth;
    const w = Math.max(20, (this.span / this.dur) * tw);
    const x = (this.view.start / this.dur) * tw;
    this.thumb.style.width = w + 'px';
    this.thumb.style.left = Math.min(x, tw - w) + 'px';
    this.hs.style.visibility = this.span >= this.dur - 1e-6 ? 'hidden' : '';
  }

  renderClock() {
    this.clock.replaceChildren(fmtTime(this.ed.player.t), h('span', ' / ' + fmtTime(this.dur)));
  }

  /** Called on every playhead update. */
  onTime(t) {
    if (this.follow && this.ed.player.playing && this.laneW) {
      const span = this.span;
      if (t > this.end - span * 0.03 || t < this.view.start) {
        this.view.start = t - span * 0.03;
        this.clampView();
        this.render();
        return;
      }
    }
    this.renderOverlays();
    this.renderClock();
    this.drawOverview();
  }

  setPlaying(p) {
    this.playBtn.replaceChildren(ico(p ? 'pause' : 'play', 15));
  }

  // ------------------------------------------------------------------ interaction
  bind() {
    this.el.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
    this.top.addEventListener('pointerdown', (e) => this.onTopDown(e));
    this.tiersInner.addEventListener('pointerdown', (e) => this.onTierDown(e));
    this.tiersInner.addEventListener('dblclick', (e) => {
      const el = e.target.closest('.seg');
      if (el) this.ed.editInline(el.dataset.id);
    });
    this.top.addEventListener('dblclick', (e) => {
      const onSignal = this.waveRow.contains(e.target) || this.specRow.contains(e.target);
      const s = onSignal && this.bTierId && this.ed.doc.at(this.bTierId, this.x2t(this.laneX(e)));
      if (s) { this.ed.select(s.id, { scroll: false }); this.ed.player.playRange(s.start, s.end); }
    });
    this.ovLane.addEventListener('pointerdown', (e) => this.onOverviewDown(e));
    this.thumb.addEventListener('pointerdown', (e) => this.onThumbDown(e));
    this.el.addEventListener('pointermove', (e) => this.onHover(e));
    this.el.addEventListener('pointerleave', () => this.hideHover());
    this.onTheme = () => { this.readColors(); this.schedule(); };
    window.addEventListener('themechange', this.onTheme);
  }

  laneX(e) {
    return e.clientX - this.rulerLane.getBoundingClientRect().left;
  }

  onWheel(e) {
    if (!this.laneW) return;
    const x = clamp(this.laneX(e), 0, this.laneW);
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const k = Math.abs(e.deltaY) < 30 ? 0.012 : 0.0025;
      this.zoomAt(Math.exp(-e.deltaY * k), x);
      return;
    }
    const overSignal = this.top.contains(e.target) || this.ov.contains(e.target);
    if (e.altKey && this.waveRow.contains(e.target)) {
      e.preventDefault();
      this.gain = clamp(this.gain * Math.exp(-e.deltaY * 0.004), 0.25, 30);
      this.schedule();
      return;
    }
    const horiz = Math.abs(e.deltaX) > Math.abs(e.deltaY);
    if (e.shiftKey || horiz || overSignal) {
      e.preventDefault();
      const d = horiz ? e.deltaX : e.deltaY;
      this.setView(this.view.start + d / this.view.pps);
    }
  }

  onHover(e) {
    if (!this.laneW || e.buttons) return;
    const x = this.laneX(e);
    if (x < 0 || x > this.laneW) { this.hideHover(); return; }
    for (const o of [this.topOvl, this.tierOvl]) {
      const hv = o.querySelector('.hov');
      hv.style.display = 'block';
      hv.style.left = x + 'px';
    }
    this.hovTime.style.display = 'block';
    this.hovTime.style.left = x + 'px';
    this.hovTime.textContent = fmtTime(this.x2t(x));
    const onSignal = this.waveRow.contains(e.target) || this.specRow.contains(e.target);
    const hb = onSignal ? this.hitBoundary(x) : null;
    const key = (b) => (b ? `${b.id}:${b.edge}` : '');
    if (key(hb) !== key(this.hoverB)) { this.hoverB = hb; this.redrawSignal(); }
    if (onSignal) e.target.closest('.tl-lane').style.cursor = hb ? 'ew-resize' : '';
    // resize cursor near segment edges
    const el = e.target.closest?.('.seg');
    if (el) {
      const s = this.ed.doc.seg(el.dataset.id);
      if (s) {
        const lx = e.clientX - el.parentElement.getBoundingClientRect().left;
        const near = Math.abs(lx - this.t2x(s.start)) <= 5 || Math.abs(lx - this.t2x(s.end)) <= 5;
        el.style.cursor = near ? 'ew-resize' : (e.altKey ? 'grab' : 'pointer');
      }
    }
  }

  hideHover() {
    if (this.hoverB != null && !this.dragB) { this.hoverB = null; this.redrawSignal(); }
    for (const o of [this.topOvl, this.tierOvl]) o.querySelector('.hov').style.display = 'none';
    this.hovTime.style.display = 'none';
  }

  drag(onMove, onUp) {
    const move = (ev) => onMove(ev);
    const up = (ev) => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      onUp?.(ev);
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  }

  onTopDown(e) {
    if (e.button !== 0) return;
    const x0 = this.laneX(e);
    if (x0 < 0) return;
    e.preventDefault();
    const onSignal = this.waveRow.contains(e.target) || this.specRow.contains(e.target);
    if (onSignal) {
      const hb = this.hitBoundary(x0);
      if (hb && this.startBoundaryDrag(this.bTierId, hb.t, (ev) => this.laneX(ev), { id: hb.id, edge: hb.edge }, this.linked !== e.altKey)) return;
    }
    const tA = clamp(this.x2t(x0), 0, this.dur);
    this.ed.player.seek(tA);
    let moved = false;
    this.drag((ev) => {
      const x = this.laneX(ev);
      if (!moved && Math.abs(x - x0) < 3) return;
      moved = true;
      const tB = clamp(this.x2t(clamp(x, 0, this.laneW)), 0, this.dur);
      this.sel = { a: Math.min(tA, tB), b: Math.max(tA, tB) };
      this.renderOverlays();
    }, () => {
      if (!moved) {
        this.sel = null;
        const s = onSignal && this.bTierId && this.ed.doc.at(this.bTierId, tA);
        if (s) this.ed.select(s.id, { scroll: false });
      }
      this.renderOverlays();
    });
  }

  onOverviewDown(e) {
    e.preventDefault();
    const go = (ev) => {
      const x = clamp(ev.clientX - this.ovLane.getBoundingClientRect().left, 0, this.laneW);
      this.setView((x / this.laneW) * this.dur - this.span / 2);
    };
    go(e);
    this.drag(go);
  }

  onThumbDown(e) {
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX, s0 = this.view.start, tw = this.hs.clientWidth;
    this.drag((ev) => this.setView(s0 + ((ev.clientX - x0) / tw) * this.dur));
  }

  snapTargets(excludeId) {
    const out = [this.ed.player.t];
    if (this.sel) out.push(this.sel.a, this.sel.b);
    for (const r of this.rows.values()) {
      if (r.dense) continue;
      for (const s of this.ed.doc.visible(r.tier.id, this.view.start, this.end)) {
        if (s.id !== excludeId) out.push(s.start, s.end);
      }
    }
    return out;
  }

  snap(t, targets, ev) {
    const snapEls = [this.topOvl, this.tierOvl].map(o => o.querySelector('.snap'));
    let best = null, bd = 6 / this.view.pps;
    if (!ev.shiftKey) for (const c of targets) { const d = Math.abs(c - t); if (d < bd) { bd = d; best = c; } }
    for (const el of snapEls) {
      el.style.display = best == null ? 'none' : 'block';
      if (best != null) el.style.left = this.t2x(best) + 'px';
    }
    return best ?? t;
  }

  hideSnap() {
    for (const o of [this.topOvl, this.tierOvl]) o.querySelector('.snap').style.display = 'none';
  }

  onTierDown(e) {
    if (e.button !== 0) return;
    const rowEl = e.target.closest('.tl-row.tier');
    if (!rowEl || e.target.closest('.tl-head') || e.target.closest('.inline-edit')) return;
    const r = this.rows.get(rowEl.dataset.tier);
    if (!r) return;
    const xAt = (ev) => ev.clientX - r.lane.getBoundingClientRect().left;
    const x0 = xAt(e);
    this.ed.setActiveTier(r.tier.id);
    if (r.dense) { this.zoomAt(6, x0); return; }
    e.preventDefault();
    const doc = this.ed.doc;
    const segEl = e.target.closest('.seg');
    const fixed = r.tier.granularity === 'file';
    if (segEl) {
      const s = doc.seg(segEl.dataset.id);
      if (!s) return;
      let mode = null;
      if (!fixed && Math.abs(x0 - this.t2x(s.start)) <= 5 && !segEl.classList.contains('cl')) mode = 'start';
      else if (!fixed && Math.abs(x0 - this.t2x(s.end)) <= 5 && !segEl.classList.contains('cr')) mode = 'end';
      else if (!fixed && e.altKey) mode = 'move';
      this.ed.select(s.id, { seek: !mode && !this.ed.player.playing, scroll: false });
      if (!mode) return;
      if ((mode === 'start' || mode === 'end') && (['word', 'phone'].includes(r.tier.granularity) || r.tier.id === this.bTierId)) {
        this.startBoundaryDrag(r.tier.id, s[mode], xAt, { id: s.id, edge: mode }, this.linked !== e.altKey);
        return;
      }
      const { prev, next } = doc.neighbors(s);
      const strict = noOverlap(r.tier);
      const lo = strict && prev ? prev.end : 0;
      const hi = strict && next ? next.start : this.dur;
      const targets = this.snapTargets(s.id);
      const dur = s.end - s.start;
      const tGrab = this.x2t(x0);
      let a = s.start, b = s.end, moved = false;
      this.drag((ev) => {
        moved = true;
        const t = this.x2t(xAt(ev));
        if (mode === 'start') a = clamp(this.snap(t, targets, ev), lo, b - 0.01);
        else if (mode === 'end') b = clamp(this.snap(t, targets, ev), a + 0.01, hi);
        else {
          let na = clamp(s.start + (t - tGrab), lo, hi - dur);
          const sa = this.snap(na, targets, ev);
          if (sa !== na) na = clamp(sa, lo, hi - dur);
          else { const sb = this.snap(na + dur, targets, ev); na = clamp(sb - dur, lo, hi - dur); }
          a = na; b = na + dur;
        }
        const el = r.els.get(s.id);
        if (el) { el.style.left = this.t2x(a) + 'px'; el.style.width = Math.max(2, this.t2x(b) - this.t2x(a)) + 'px'; }
        this.ed.previewTimes?.(a, b);
      }, () => {
        this.hideSnap();
        if (moved && (a !== s.start || b !== s.end)) this.ed.updateSeg(s.id, { start: a, end: b }, mode === 'move' ? 'Move segment' : 'Adjust boundary');
        else this.schedule();
      });
      return;
    }
    // empty space: click = seek, drag = create a segment
    const targets = this.snapTargets(null);
    const tA0 = clamp(this.x2t(x0), 0, this.dur);
    let ghost = null, a = tA0, b = tA0;
    this.drag((ev) => {
      const x = xAt(ev);
      if (!ghost && Math.abs(x - x0) < 4) return;
      if (!ghost) { ghost = h('div.ghost-seg'); r.lane.append(ghost); }
      const tA = this.snap(tA0, targets, ev);
      const tB = clamp(this.snap(this.x2t(x), targets, ev), 0, this.dur);
      a = Math.min(tA, tB); b = Math.max(tA, tB);
      ghost.style.left = this.t2x(a) + 'px';
      ghost.style.width = (this.t2x(b) - this.t2x(a)) + 'px';
    }, () => {
      this.hideSnap();
      if (ghost) {
        ghost.remove();
        if (b - a > 0.01) this.ed.createSegment(r.tier.id, a, b);
      } else {
        this.ed.select(null);
        this.ed.player.seek(tA0);
      }
    });
  }

  /** Inline text editor over a segment (text tiers). Resolves with the new text or null. */
  inlineText(s, onCommit, onTab) {
    const r = this.rows.get(s.tier_id);
    if (!r || r.dense) return false;
    this.ensureVisible(s.start, s.end);
    this.render();
    const x0 = clamp(this.t2x(s.start), 0, this.laneW), x1 = clamp(this.t2x(s.end), 0, this.laneW);
    const w = Math.min(this.laneW, Math.max(280, x1 - x0));
    const left = clamp(x0, 0, this.laneW - w);
    const input = h('input', { value: valStr(s.value), spellcheck: true });
    const wrap = h('div.inline-edit', { style: { left: left + 'px', width: w + 'px' } }, input);
    r.lane.append(wrap);
    input.focus();
    input.select();
    let done = false;
    const finish = (save, tab) => {
      if (done) return;
      done = true;
      wrap.remove();
      if (save && input.value !== valStr(s.value)) onCommit(input.value);
      if (tab) onTab?.(tab);
      this.ed.focus();
    };
    input.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
      else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
      else if (ev.key === 'Tab') { ev.preventDefault(); finish(true, ev.shiftKey ? -1 : 1); }
    });
    input.addEventListener('blur', () => finish(true));
    return true;
  }

  segRect(id) {
    for (const r of this.rows.values()) {
      const el = r.els.get(id);
      if (el) return el.getBoundingClientRect();
    }
    return null;
  }

  destroy() {
    this.ro?.disconnect();
    window.removeEventListener('themechange', this.onTheme);
  }
}
