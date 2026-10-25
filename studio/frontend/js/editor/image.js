// Image editor: the image with editable regions (boxes, pose keypoints) on the left,
// and one card per task on the right (captions, labels, tags, plus the list of regions).
import { urls } from '../api.js';
import { h, ico, isTyping, clamp, ls, segColor, valStr } from '../util.js';
import { state } from '../state.js';
import { modalOpen, toast, popover } from '../ui.js';
import { openRunDialog } from '../views/run.js';
import { confBar } from '../views/review.js';
import { BaseEditor } from './base.js';
import { tierFromTask } from './doc.js';
import { valueEditor, srcBadge, pickValue, tierLabels } from './inspector.js';
import { sortTiers } from './order.js';

export const KP_NAMES = ['nose', 'left eye', 'right eye', 'left ear', 'right ear', 'left shoulder', 'right shoulder',
  'left elbow', 'right elbow', 'left wrist', 'right wrist', 'left hip', 'right hip', 'left knee', 'right knee', 'left ankle', 'right ankle'];
const KP_SHORT = ['Nose', 'L eye', 'R eye', 'L ear', 'R ear', 'L shoulder', 'R shoulder', 'L elbow', 'R elbow',
  'L wrist', 'R wrist', 'L hip', 'R hip', 'L knee', 'R knee', 'L ankle', 'R ankle'];
const SKELETON = [[15, 13], [13, 11], [16, 14], [14, 12], [11, 12], [5, 11], [6, 12], [5, 6], [5, 7], [6, 8], [7, 9], [8, 10],
  [1, 2], [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6]];
const SIDE_COLOR = { l: '#38bdf8', r: '#fb923c', c: '#e2e8f0' };
const side = (i) => (i === 0 ? 'c' : i % 2 ? 'l' : 'r');
// standing person facing the camera, relative to its bounding box (person's left = image right)
const POSE_TEMPLATE = [[.50, .08], [.54, .06], [.46, .06], [.58, .07], [.42, .07], [.66, .20], [.34, .20], [.73, .37], [.27, .37],
  [.75, .53], [.25, .53], [.60, .55], [.40, .55], [.61, .76], [.39, .76], [.61, .96], [.39, .96]];
const SPATIAL = new Set(['boxes', 'keypoints']);
const EXTRA_COLOR = '#c084fc';
const kpColor = (i) => (i < 17 ? SIDE_COLOR[side(i)] : EXTRA_COLOR);
/** Connections of a skeleton: its own list if edited, otherwise the COCO skeleton. */
export function poseEdges(g) {
  const n = g.points.length;
  return (Array.isArray(g.edges) ? g.edges : SKELETON).filter(([a, b]) => a < n && b < n && a !== b);
}
export const kpName = (g, i) => g.names?.[i] || KP_NAMES[i] || `point ${i + 1}`;
const kpShort = (g, i) => g.names?.[i] || KP_SHORT[i] || `point ${i + 1}`;
const hasEdge = (edges, a, b) => edges.some(([x, y]) => (x === a && y === b) || (x === b && y === a));
const SVGNS = 'http://www.w3.org/2000/svg';

function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) el.setAttribute(k, v);
  for (const c of kids.flat()) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}

const measure = (() => {
  const c = document.createElement('canvas').getContext('2d');
  c.font = '600 11px "IBM Plex Sans", system-ui, sans-serif';
  return (t) => c.measureText(t).width;
})();

export class ImageEditor extends BaseEditor {
  constructor(dv, media, params) {
    super(dv, media, params);
    this.el.classList.add('image');
    this.onKey = this.onKey.bind(this);
    this.onKeyUp = (e) => { if (e.key === ' ') { this.space = false; this.stage?.classList.remove('panning'); } };
    this.selId = null;
    this.selKp = null;
    this.selEdge = null;
    this.connect = null;
    this.mode = 'select';
    this.placing = null;
    this.preview = null;
    this.ghost = null;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.userZoomed = false;
    this.overlay = true;
    this.hidden = new Set(ls.get('imgHidden:' + dv.pv.pid, []));
    this.lastLabel = {};
  }

  // ------------------------------------------------------------------ setup
  async init() {
    await this.loadDoc();
    const m = this.media;
    this.W = m.width || 1000;
    this.H = m.height || 1000;
    this.img = h('img', { src: urls.file(this.pid, m.id), alt: m.name, draggable: false });
    this.svg = sv('svg', { class: 'img-svg', viewBox: `0 0 ${this.W} ${this.H}` });
    this.world = h('div.img-world', this.img, this.svg);
    this.hint = h('div.img-hint');
    this.zoomLbl = h('span.img-zoom-lbl');
    this.stage = h('div.img-stage2', this.world, this.hint);
    this.side = h('div.img-fields');
    this.el.replaceChildren(this.buildHeader(), h('div.img-ed', h('div.img-left', this.buildToolbar(), this.stage), this.side));
    this.sizeWorld();
    this.img.addEventListener('load', () => {
      if (this.img.naturalWidth && (this.img.naturalWidth !== this.W || this.img.naturalHeight !== this.H)) {
        this.W = this.img.naturalWidth;
        this.H = this.img.naturalHeight;
        this.svg.setAttribute('viewBox', `0 0 ${this.W} ${this.H}`);
        this.sizeWorld();
      }
      this.fit();
    });
    this.ro = new ResizeObserver(() => { if (!this.userZoomed) this.fit(); });
    this.ro.observe(this.stage);
    this.stage.addEventListener('pointerdown', (e) => this.onDown(e));
    this.stage.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
    this.stage.addEventListener('dblclick', (e) => this.onDbl(e));
    this.stage.addEventListener('contextmenu', (e) => e.preventDefault());
    this.pickDrawTiers();
    this.fit();
    this.renderAll();
    document.addEventListener('keydown', this.onKey);
    document.addEventListener('keyup', this.onKeyUp);
    this.applyParams(this.params);
  }

  sizeWorld() {
    this.world.style.width = this.W + 'px';
    this.world.style.height = this.H + 'px';
    this.svg.setAttribute('width', this.W);
    this.svg.setAttribute('height', this.H);
  }

  applyParams(params) {
    const seg = params?.get?.('seg');
    const s = seg && this.doc.seg(seg);
    if (!s) return;
    if (s.geom) { this.select(seg); return; }
    // whole-image label / caption: scroll to its card and flash it
    const card = this.side.querySelector(`[data-tier="${s.tier_id}"]`);
    if (card) {
      card.scrollIntoView({ block: 'center' });
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1800);
    }
  }

  // ------------------------------------------------------------------ tiers
  get spatialTiers() { return sortTiers(this.doc.tiers).filter(t => SPATIAL.has(t.output)); }

  /** Spatial tasks offered for drawing: tiers already in the file + project tasks that produce boxes / keypoints. */
  spatialTargets(output) {
    const out = [];
    for (const t of this.spatialTiers) if (t.output === output) out.push({ key: t.id, name: t.name, title: t.title, tier: t });
    for (const id of state.project.tasks || []) {
      const t = state.taskById.get(id);
      if (t && t.output === output && t.modalities.includes('image') && !out.some(o => o.key === t.id)) out.push({ key: t.id, name: t.name, title: t.title, task: t });
    }
    if (!out.length) {
      const fallback = state.tasks.find(t => t.output === output && t.modalities.includes('image'));
      if (fallback) out.push({ key: fallback.id, name: fallback.name, title: fallback.title, task: fallback });
    }
    return out;
  }

  pickDrawTiers() {
    const boxes = this.spatialTargets('boxes');
    if (!boxes.some(b => b.key === this.drawKey)) this.drawKey = boxes[0]?.key || null;
    const poses = this.spatialTargets('keypoints');
    if (!poses.some(p => p.key === this.poseKey)) this.poseKey = poses[0]?.key || null;
    if (this.drawSel) {
      this.drawSel.replaceChildren(...boxes.map(b => h('option', { value: b.key, selected: b.key === this.drawKey }, b.title)));
      this.drawSel.disabled = !boxes.length;
    }
  }

  /** Returns the tier id for a key, creating the tier inside the given transaction when needed. */
  ensureTier(tx, key) {
    if (this.doc.tier(key)) return key;
    const task = state.taskById.get(key);
    if (!task) return null;
    tx.setTiers([...this.doc.tiers, tierFromTask(task)]);
    return task.id;
  }

  // ------------------------------------------------------------------ toolbar
  buildToolbar() {
    const tool = (mode, icon, label, key) => h('button', { dataset: { v: mode }, title: `${label} (${key})`, onclick: () => this.setMode(mode) }, ico(icon, 14), h('span', label));
    this.toolCtl = h('div.seg-ctl.sm', tool('select', 'target', 'Select', 'V'), tool('box', 'grid', 'Box', 'B'), tool('pose', 'user', 'Pose', 'K'));
    this.drawSel = h('select.select.sm', { title: 'New boxes go into this task', 'aria-label': 'New boxes go into this task', onchange: (e) => { this.drawKey = e.target.value; this.setMode('box'); } });
    this.overlayBtn = h('button.icon-btn.active', { title: 'Show / hide all regions (H)', 'aria-label': 'Show regions', 'aria-pressed': 'true', onclick: () => this.toggleOverlay() }, ico('eye', 16));
    return h('div.img-tools', this.toolCtl, h('span.sep'), h('span.blab', 'Draw into'), this.drawSel, this.overlayBtn, h('div.grow'),
      h('button.icon-btn', { title: 'Zoom out (−)', 'aria-label': 'Zoom out', onclick: () => this.zoomBy(1 / 1.4) }, ico('zoom-out', 16)),
      this.zoomLbl,
      h('button.icon-btn', { title: 'Zoom in (+)', 'aria-label': 'Zoom in', onclick: () => this.zoomBy(1.4) }, ico('zoom-in', 16)),
      h('button.icon-btn', { title: 'Fit (0)', 'aria-label': 'Fit to view', onclick: () => this.fit() }, ico('fit', 16)));
  }

  setMode(m) {
    this.mode = m;
    this.placing = null;
    this.renderTools();
  }

  toggleOverlay() {
    this.overlay = !this.overlay;
    this.overlayBtn.classList.toggle('active', this.overlay);
    this.overlayBtn.setAttribute('aria-pressed', String(this.overlay));
    this.overlayBtn.replaceChildren(ico(this.overlay ? 'eye' : 'eye-off', 16));
    this.renderShapes();
  }

  renderTools() {
    for (const b of this.toolCtl.children) b.classList.toggle('on', b.dataset.v === this.mode);
    this.stage.classList.toggle('drawing', this.mode !== 'select' || !!this.placing);
    const drawName = this.spatialTargets('boxes').find(b => b.key === this.drawKey)?.title || 'a box task';
    const poseName = this.spatialTargets('keypoints').find(b => b.key === this.poseKey)?.title || 'pose';
    let text;
    if (this.placing) text = this.placing.k === 'new' ? 'Click on the image to add a new point to this person. Esc cancels.'
      : `Click on the image to place the ${KP_NAMES[this.placing.k]}. Esc cancels.`;
    else if (this.mode === 'box') text = `Drag on the image to draw a box into "${drawName}". Esc stops.`;
    else if (this.mode === 'pose') text = `Drag a box around a person to add a skeleton (${poseName}), then drag each point into place`;
    else text = 'Drag a box or point to move it; corners resize, the wheel zooms, and dragging empty space (or holding Space) pans.';
    this.hint.textContent = text;
    this.hint.classList.toggle('active', !!this.placing || this.mode !== 'select');
  }

  // ------------------------------------------------------------------ view
  applyView() {
    this.world.style.transform = `translate(${this.pan.x}px, ${this.pan.y}px) scale(${this.zoom})`;
    this.zoomLbl.textContent = Math.round(this.zoom * 100) + '%';
    this.renderShapes();
  }

  fit() {
    const r = this.stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const z = Math.min(r.width / this.W, r.height / this.H) * 0.96;
    this.zoom = z;
    this.fitZoom = z;
    this.pan = { x: (r.width - this.W * z) / 2, y: (r.height - this.H * z) / 2 };
    this.userZoomed = false;
    this.applyView();
  }

  zoomAt(f, cx, cy) {
    const z = clamp(this.zoom * f, (this.fitZoom || 1) * 0.4, 30);
    const k = z / this.zoom;
    this.pan = { x: cx - (cx - this.pan.x) * k, y: cy - (cy - this.pan.y) * k };
    this.zoom = z;
    this.userZoomed = true;
    this.applyView();
  }

  zoomBy(f) {
    const r = this.stage.getBoundingClientRect();
    this.zoomAt(f, r.width / 2, r.height / 2);
  }

  onWheel(e) {
    e.preventDefault();
    const r = this.stage.getBoundingClientRect();
    if (e.shiftKey || (Math.abs(e.deltaX) > Math.abs(e.deltaY) && !e.ctrlKey)) {
      this.pan.x -= e.deltaX || e.deltaY;
      this.pan.y -= e.shiftKey ? 0 : e.deltaY;
      this.userZoomed = true;
      this.applyView();
      return;
    }
    const k = Math.abs(e.deltaY) < 30 ? 0.012 : 0.0022;
    this.zoomAt(Math.exp(-e.deltaY * k), e.clientX - r.left, e.clientY - r.top);
  }

  toImg(e) {
    const r = this.stage.getBoundingClientRect();
    return { x: (e.clientX - r.left - this.pan.x) / this.zoom, y: (e.clientY - r.top - this.pan.y) / this.zoom };
  }

  // ------------------------------------------------------------------ geometry
  geomOf(seg) { return this.preview && this.preview.id === seg.id ? this.preview.geom : seg.geom; }

  boxPx(g) { return { x: g.x * this.W, y: g.y * this.H, w: g.w * this.W, h: g.h * this.H }; }

  normBox(x0, y0, x1, y1) {
    const ax = clamp(Math.min(x0, x1), 0, this.W), bx = clamp(Math.max(x0, x1), 0, this.W);
    const ay = clamp(Math.min(y0, y1), 0, this.H), by = clamp(Math.max(y0, y1), 0, this.H);
    return { type: 'box', x: ax / this.W, y: ay / this.H, w: (bx - ax) / this.W, h: (by - ay) / this.H };
  }

  poseBounds(g) {
    const vis = g.points.filter(p => p[2]);
    if (!vis.length) return g.box ? { x: g.box[0] * this.W, y: g.box[1] * this.H, w: g.box[2] * this.W, h: g.box[3] * this.H } : null;
    const xs = vis.map(p => p[0] * this.W), ys = vis.map(p => p[1] * this.H);
    const x = Math.min(...xs), y = Math.min(...ys);
    return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
  }

  // ------------------------------------------------------------------ rendering
  renderAll() {
    this.pickDrawTiers();
    this.renderTools();
    this.renderShapes();
    this.renderPanel();
  }

  renderShapes() {
    if (!this.svg) return;
    if (!this.overlay) { this.svg.replaceChildren(); return; }
    const S = 1 / this.zoom;
    const items = [];
    for (const t of this.spatialTiers) {
      if (this.hidden.has(t.id)) continue;
      for (const seg of this.doc.list(t.id)) {
        const g = this.geomOf(seg);
        if (g) items.push({ seg, t, g });
      }
    }
    // big boxes underneath, small boxes and skeletons on top, the selected shape above everything
    const area = (g) => (g.type === 'box' ? g.w * g.h : -1);
    items.sort((a, b) => (a.seg.id === this.selId) - (b.seg.id === this.selId) || area(b.g) - area(a.g));
    const kids = items.map(({ seg, t, g }) => (g.type === 'box' ? this.boxEl(seg, t, g, S) : this.poseEl(seg, t, g, S)));
    if (this.connect) {
      const c = this.connect;
      kids.push(sv('line', { class: 'connect-line', x1: c.x1, y1: c.y1, x2: c.x2, y2: c.y2 }));
    }
    if (this.ghost) {
      const gh = this.ghost;
      kids.push(sv('rect', { class: 'ghost', x: gh.x, y: gh.y, width: gh.w, height: gh.h }));
    }
    this.svg.replaceChildren(...kids);
  }

  tag(seg, t, x, y, S, color) {
    const unlabelled = !valStr(seg.value);
    let label = unlabelled ? t.name : valStr(seg.value);
    if (unlabelled) color = 'rgba(100, 116, 139, 0.9)';
    if (seg.confidence != null && !seg.reviewed && seg.source !== 'human') label += ` ${seg.confidence.toFixed(2)}`;
    if (seg.reviewed) label += ' ✓';
    const w = measure(label) + 10;
    const inside = y * this.zoom + this.pan.y < 22;
    return sv('g', { class: 'tag', transform: `translate(${x} ${inside ? y + 19 * S : y}) scale(${S})`, 'data-seg': seg.id },
      sv('rect', { x: -1, y: -19, width: w, height: 18, rx: 3, fill: color }),
      sv('text', { x: 5, y: -6, class: unlabelled ? 'nolabel' : null }, label));
  }

  boxEl(seg, t, g, S) {
    const { x, y, w, h: hh } = this.boxPx(g);
    const sel = seg.id === this.selId;
    const low = !seg.reviewed && seg.confidence != null && seg.confidence < this.lowThr;
    const color = segColor(t, seg.value);
    const grp = sv('g', { class: `shape box${sel ? ' sel' : ''}${low ? ' low' : ''}${seg.source === 'mock' ? ' mock' : ''}`, 'data-seg': seg.id, style: `--c:${color}` },
      sv('rect', { class: 'body', x, y, width: Math.max(1, w), height: Math.max(1, hh), 'data-seg': seg.id }),
      this.tag(seg, t, x, y, S, color));
    if (sel) {
      const hs = 9 * S;
      const pos = { nw: [x, y], n: [x + w / 2, y], ne: [x + w, y], e: [x + w, y + hh / 2], se: [x + w, y + hh], s: [x + w / 2, y + hh], sw: [x, y + hh], w: [x, y + hh / 2] };
      for (const [k, [px, py]] of Object.entries(pos)) {
        grp.append(sv('rect', { class: 'h', 'data-h': k, 'data-seg': seg.id, x: px - hs / 2, y: py - hs / 2, width: hs, height: hs }));
      }
    }
    return grp;
  }

  poseEl(seg, t, g, S) {
    const sel = seg.id === this.selId;
    const low = !seg.reviewed && seg.confidence != null && seg.confidence < this.lowThr;
    const P = g.points.map(([x, y, v]) => [x * this.W, y * this.H, v]);
    const grp = sv('g', { class: `shape pose${sel ? ' sel' : ''}${low ? ' low' : ''}`, 'data-seg': seg.id, style: `--c:${t.color}` });
    const b = this.poseBounds(g);
    if (sel && b) grp.append(sv('rect', { class: 'pbox', x: b.x - 8 * S, y: b.y - 8 * S, width: b.w + 16 * S, height: b.h + 16 * S }));
    for (const [a, c] of poseEdges(g)) {
      if (!P[a]?.[2] || !P[c]?.[2]) continue;
      const eSel = sel && this.selEdge && hasEdge([this.selEdge], a, c);
      const col = a >= 17 || c >= 17 ? EXTRA_COLOR : SIDE_COLOR[side(a) === 'c' ? side(c) : side(a)];
      const at = { x1: P[a][0], y1: P[a][1], x2: P[c][0], y2: P[c][1] };
      grp.append(sv('line', { class: 'bone' + (P[a][2] === 1 || P[c][2] === 1 ? ' occ' : '') + (eSel ? ' esel' : ''), ...at, stroke: col }));
      grp.append(sv('line', { class: 'bone-hit', ...at, 'data-seg': seg.id, 'data-e': `${a}-${c}` },
        sv('title', {}, `${kpName(g, a)} — ${kpName(g, c)} · click to select, Delete to remove`)));
    }
    P.forEach(([x, y, v], i) => {
      if (!v) return;
      const isSel = sel && this.selKp === i;
      const r = (isSel ? 7 : sel ? 5.5 : 4.5) * S;
      grp.append(sv('circle', { class: 'kp' + (v === 1 ? ' occ' : '') + (isSel ? ' kpsel' : '') + (i >= 17 ? ' extra' : ''), cx: x, cy: y, r,
        fill: v === 1 ? 'rgba(0,0,0,0.35)' : kpColor(i), stroke: v === 1 ? kpColor(i) : null,
        'data-seg': seg.id, 'data-k': i }, sv('title', {}, kpName(g, i) + (v === 1 ? ' (occluded)' : '') + ' · Shift + drag to another point to connect')));
      if (i >= 17 && sel) {
        grp.append(sv('text', { class: 'kp-name', transform: `translate(${x + 9 * S} ${y - 7 * S}) scale(${S})` }, kpName(g, i)));
      }
    });
    if (b && sel) grp.append(this.tag(seg, t, b.x - 8 * S, b.y - 8 * S, S, t.color));
    return grp;
  }

  // ------------------------------------------------------------------ selection & editing
  select(id, kp = null, edge = null) {
    this.selId = id && this.doc.seg(id) ? id : null;
    this.selKp = this.selId ? kp : null;
    this.selEdge = this.selId ? edge : null;
    this.renderShapes();
    this.renderPanel();
    const row = this.side.querySelector(`[data-row="${this.selId}"]`);
    row?.scrollIntoView({ block: 'nearest' });
  }

  /** Human edit: mark reviewed and keep what the model originally said (label + geometry). */
  humanPatch(cur, patch) {
    const p = { reviewed: true, ...patch };
    if (cur.source !== 'human') {
      p.source = 'human';
      p.meta = { ...(cur.meta || {}), model_source: cur.source, model_value: cur.meta?.model_value ?? cur.value,
        ...(cur.geom ? { model_geom: cur.meta?.model_geom ?? cur.geom } : {}) };
    }
    return p;
  }

  update(id, patch, label, merge = false) {
    const cur = this.doc.seg(id);
    if (!cur) return;
    this.localEdit = true;
    this.doc.transact(label, (tx) => tx.update(id, this.humanPatch(cur, patch)), { merge });
    this.localEdit = false;
  }

  setPoint(id, k, pt, label = 'Move keypoint', merge = false) {
    const seg = this.doc.seg(id);
    const pts = seg.geom.points.map(p => p.slice());
    while (pts.length < 17) pts.push([0, 0, 0]);
    pts[k] = pt;
    this.update(id, { geom: { ...seg.geom, points: pts } }, label, merge);
  }

  /** Remove an extra (non-COCO) point: drop it, its name and its connections, and renumber. */
  removePoint(id, i) {
    const g = this.doc.seg(id).geom;
    const pts = g.points.map(p => p.slice());
    pts.splice(i, 1);
    const names = (g.names || []).slice();
    names.splice(i, 1);
    const edges = poseEdges(g).filter(([a, b]) => a !== i && b !== i).map(([a, b]) => [a > i ? a - 1 : a, b > i ? b - 1 : b]);
    this.update(id, { geom: { ...g, points: pts, names, edges } }, 'Remove point');
    this.select(id, null);
  }

  addEdge(id, a, b) {
    const g = this.doc.seg(id).geom;
    const edges = poseEdges(g).map(e => e.slice());
    if (hasEdge(edges, a, b)) { toast('These points are already connected', { timeout: 1600 }); return; }
    edges.push([a, b]);
    this.update(id, { geom: { ...g, edges } }, 'Connect points');
    this.select(id, b);
  }

  removeEdge(id, a, b) {
    const g = this.doc.seg(id).geom;
    const edges = poseEdges(g).filter(([x, y]) => !((x === a && y === b) || (x === b && y === a)));
    this.update(id, { geom: { ...g, edges } }, 'Remove connection');
    this.select(id, this.selKp);
  }

  /** Nearest visible point of a skeleton within 14 px of the pointer. */
  hitKp(id, ev) {
    const g = this.doc.seg(id)?.geom;
    if (!g) return null;
    const p = this.toImg(ev);
    let best = null, bd = 14 / this.zoom;
    g.points.forEach(([x, y, v], i) => {
      if (!v) return;
      const d = Math.hypot(x * this.W - p.x, y * this.H - p.y);
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  }

  startConnect(id, k) {
    const a = this.doc.seg(id).geom.points[k];
    this.select(id, k);
    this.connect = { x1: a[0] * this.W, y1: a[1] * this.H, x2: a[0] * this.W, y2: a[1] * this.H };
    this.drag((ev) => {
      const p = this.toImg(ev);
      this.connect.x2 = p.x;
      this.connect.y2 = p.y;
      this.renderShapes();
    }, (ev) => {
      const b = this.hitKp(id, ev);
      this.connect = null;
      this.renderShapes();
      if (b != null && b !== k) this.addEdge(id, k, b);
      else toast('Release on another point of the same person to connect the two', { timeout: 2500 });
    });
  }

  /** Name a new point: a missing standard keypoint (fills that slot) or a custom name. */
  askPointName(id, i) {
    const g = this.doc.seg(id)?.geom;
    if (!g) return;
    const el = this.svg.querySelector(`[data-seg="${id}"][data-k="${i}"]`);
    const rect = el ? el.getBoundingClientRect() : this.stage.getBoundingClientRect();
    const missing = KP_NAMES.map((n, j) => [n, j]).filter(([, j]) => !g.points[j]?.[2]);
    const input = h('input.input.sm', { value: kpName(g, i), placeholder: 'Point name' });
    const done = () => { pop.close(); };
    const useCustom = () => {
      const name = input.value.trim() || `point ${i + 1}`;
      const g2 = this.doc.seg(id).geom;
      const names = (g2.names || []).slice();
      while (names.length < g2.points.length) names.push('');
      names[i] = name;
      this.update(id, { geom: { ...g2, names } }, 'Name point');
      done();
    };
    const useSlot = (j) => {
      const g2 = this.doc.seg(id).geom;
      const pts = g2.points.map(p => p.slice());
      pts[j] = pts[i];
      pts.splice(i, 1);
      const names = (g2.names || []).slice();
      names.splice(i, 1);
      const edges = poseEdges(g2).filter(([a, b]) => a !== i && b !== i).map(([a, b]) => [a > i ? a - 1 : a, b > i ? b - 1 : b]);
      this.update(id, { geom: { ...g2, points: pts, names, ...(Array.isArray(g2.edges) ? { edges } : {}) } }, `Add ${KP_NAMES[j]}`);
      done();
      this.select(id, j);
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); useCustom(); } });
    const pop = popover(rect, h('div.label-pop',
      h('div.menu-head', 'Name this point'),
      h('div.row', input, h('button.btn.sm', { onclick: useCustom }, 'OK')),
      missing.length ? [h('div.menu-head', 'or use it as a missing body point'),
        h('div.opts', missing.map(([n, j]) => h('button.opt', { onclick: () => useSlot(j) }, h('i', { style: { '--c': kpColor(j) } }), n)))] : null));
    setTimeout(() => { input.focus(); input.select(); }, 20);
  }

  deleteSeg(id) {
    this.doc.transact('Delete region', (tx) => tx.remove(id));
    this.select(null);
  }

  createBox(geom) {
    const key = this.drawKey;
    if (!key) { toast('Add a box task (Faces, People, Objects…) to the project first', { kind: 'warn' }); return; }
    let seg;
    this.doc.transact('Add box', (tx) => {
      const tid = this.ensureTier(tx, key);
      seg = tx.add({ tier_id: tid, start: null, end: null, value: '', geom, reviewed: true, source: 'human' });
    });
    this.select(seg.id);
    const tier = this.doc.tier(seg.tier_id);
    if ((tier.labels || []).length > 1) requestAnimationFrame(() => this.askLabel(seg.id));
  }

  createPose(px) {
    const key = this.poseKey;
    if (!key) { toast('Add the Pose task to the project first', { kind: 'warn' }); return; }
    const points = POSE_TEMPLATE.map(([u, v]) => [(px.x + u * px.w) / this.W, (px.y + v * px.h) / this.H, 2]);
    let seg;
    this.doc.transact('Add person pose', (tx) => {
      const tid = this.ensureTier(tx, key);
      seg = tx.add({ tier_id: tid, start: null, end: null, value: 'person', reviewed: true, source: 'human',
        geom: { type: 'keypoints', points, skeleton: 'coco17', box: [px.x / this.W, px.y / this.H, px.w / this.W, px.h / this.H] } });
    });
    this.select(seg.id);
    toast('Skeleton added: drag each point onto the body. Select a point and press Delete if it is not visible, O if occluded.', { timeout: 6000 });
  }

  askLabel(id) {
    const seg = this.doc.seg(id);
    if (!seg) return;
    const tier = this.doc.tier(seg.tier_id);
    const el = this.svg.querySelector(`.shape[data-seg="${id}"] .body`) || this.svg.querySelector(`.shape[data-seg="${id}"]`);
    const rect = el ? el.getBoundingClientRect() : this.stage.getBoundingClientRect();
    pickValue(rect, { ...tier, output: 'label' }, seg, this.doc, (v) => {
      if (v !== seg.value) this.update(id, { value: v }, 'Edit label');
    }, { allowEmpty: true });
  }

  // ------------------------------------------------------------------ pointer
  drag(onMove, onUp) {
    const move = (ev) => onMove(ev);
    const up = (ev) => { removeEventListener('pointermove', move); removeEventListener('pointerup', up); onUp?.(ev); };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  }

  onDown(e) {
    if (e.button === 2) return;
    e.preventDefault();
    const p = this.toImg(e);
    const t = e.target;
    const segId = t.closest?.('[data-seg]')?.getAttribute('data-seg') || null;
    const handle = t.getAttribute?.('data-h');
    const k = t.getAttribute?.('data-k');
    const edge = t.getAttribute?.('data-e');
    if (this.placing) { this.placeKp(p); return; }
    if (e.button === 1 || this.space) { this.startPan(e); return; }
    if (handle && segId) { this.startResize(e, segId, handle); return; }
    if (this.mode === 'box' || this.mode === 'pose') { this.startDraw(e, p); return; }
    if (k != null && segId) { this.startKp(e, segId, +k); return; }
    if (edge && segId) { this.startMove(e, segId, edge.split('-').map(Number)); return; }
    if (segId) { this.select(segId); this.startMove(e, segId); return; }
    if (this.selId) this.select(null);
    this.startPan(e);
  }

  onDbl(e) {
    const segId = e.target.closest?.('[data-seg]')?.getAttribute('data-seg');
    const seg = segId && this.doc.seg(segId);
    if (seg && seg.geom?.type === 'box') this.askLabel(segId);
  }

  startPan(e) {
    const x0 = e.clientX, y0 = e.clientY, p0 = { ...this.pan };
    this.stage.classList.add('panning');
    this.drag((ev) => {
      this.pan = { x: p0.x + ev.clientX - x0, y: p0.y + ev.clientY - y0 };
      this.userZoomed = true;
      this.applyView();
    }, () => this.stage.classList.remove('panning'));
  }

  startMove(e, id, edge = null) {
    const seg = this.doc.seg(id), g0 = seg.geom;
    if (!g0) return;
    const p0 = this.toImg(e);
    let moved = false, geom = g0;
    this.drag((ev) => {
      const p = this.toImg(ev);
      let dx = (p.x - p0.x) / this.W, dy = (p.y - p0.y) / this.H;
      if (!moved && Math.hypot(dx * this.W, dy * this.H) * this.zoom < 3) return;
      moved = true;
      if (g0.type === 'box') {
        dx = clamp(dx, -g0.x, 1 - g0.x - g0.w);
        dy = clamp(dy, -g0.y, 1 - g0.y - g0.h);
        geom = { ...g0, x: g0.x + dx, y: g0.y + dy };
      } else {
        geom = { ...g0, points: g0.points.map(([x, y, v]) => [x + dx, y + dy, v]), box: g0.box ? [g0.box[0] + dx, g0.box[1] + dy, g0.box[2], g0.box[3]] : undefined };
      }
      this.preview = { id, geom };
      this.renderShapes();
    }, () => {
      this.preview = null;
      if (moved) this.update(id, { geom }, g0.type === 'box' ? 'Move box' : 'Move pose');
      else if (edge) this.select(id, null, edge);
      else this.renderShapes();
    });
  }

  startResize(e, id, hd) {
    const seg = this.doc.seg(id);
    const b = this.boxPx(seg.geom);
    let x0 = b.x, y0 = b.y, x1 = b.x + b.w, y1 = b.y + b.h, moved = false, geom = seg.geom;
    this.drag((ev) => {
      const p = this.toImg(ev);
      moved = true;
      if (hd.includes('w')) x0 = p.x;
      if (hd.includes('e')) x1 = p.x;
      if (hd.includes('n')) y0 = p.y;
      if (hd.includes('s')) y1 = p.y;
      geom = this.normBox(x0, y0, x1, y1);
      this.preview = { id, geom };
      this.renderShapes();
    }, () => {
      this.preview = null;
      if (moved && geom.w * this.W >= 2 && geom.h * this.H >= 2) this.update(id, { geom }, 'Resize box');
      else this.renderShapes();
    });
  }

  startKp(e, id, k) {
    const seg = this.doc.seg(id), g0 = seg.geom;
    if (e.shiftKey) { this.startConnect(id, k); return; }
    this.select(id, k);
    if (e.altKey) { this.startMove(e, id); return; }
    let moved = false, pt = g0.points[k];
    this.drag((ev) => {
      const p = this.toImg(ev);
      moved = true;
      pt = [clamp(p.x, 0, this.W) / this.W, clamp(p.y, 0, this.H) / this.H, g0.points[k][2] || 2];
      const pts = g0.points.map(q => q.slice());
      pts[k] = pt;
      this.preview = { id, geom: { ...g0, points: pts } };
      this.renderShapes();
    }, () => {
      this.preview = null;
      if (moved) this.setPoint(id, k, pt);
      else this.renderShapes();
    });
  }

  startDraw(e, p0) {
    let moved = false;
    this.drag((ev) => {
      const p = this.toImg(ev);
      moved = true;
      const b = this.normBox(p0.x, p0.y, p.x, p.y);
      this.ghost = { x: b.x * this.W, y: b.y * this.H, w: b.w * this.W, h: b.h * this.H };
      this.renderShapes();
    }, () => {
      const g = this.ghost;
      this.ghost = null;
      this.renderShapes();
      if (this.mode === 'pose') {
        const big = g && g.w > 12 && g.h > 24;
        const hh = this.H * 0.45;
        const px = big ? g : { x: clamp(p0.x - hh * 0.25, 0, this.W), y: clamp(p0.y - hh * 0.1, 0, this.H), w: hh * 0.5, h: hh };
        if (moved || !g) this.createPose(px);
        return;
      }
      if (g && g.w >= 4 && g.h >= 4) this.createBox({ type: 'box', x: g.x / this.W, y: g.y / this.H, w: g.w / this.W, h: g.h / this.H });
    });
  }

  placeKp(p) {
    const { id, k } = this.placing;
    this.placing = null;
    const pt = [clamp(p.x, 0, this.W) / this.W, clamp(p.y, 0, this.H) / this.H, 2];
    if (k === 'new') {
      const g = this.doc.seg(id).geom;
      const pts = g.points.map(q => q.slice());
      while (pts.length < 17) pts.push([0, 0, 0]);
      pts.push(pt);
      const i = pts.length - 1;
      const names = (g.names || []).slice();
      while (names.length < i) names.push('');
      names[i] = `point ${i + 1}`;
      this.update(id, { geom: { ...g, points: pts, names } }, 'Add point');
      this.select(id, i);
      this.renderTools();
      requestAnimationFrame(() => this.askPointName(id, i));
      return;
    }
    this.setPoint(id, k, pt, 'Add keypoint');
    this.select(id, k);
    this.renderTools();
  }

  startPlacing(id, k) {
    this.select(id, k);
    this.placing = { id, k };
    this.renderTools();
  }

  // ------------------------------------------------------------------ side panel
  renderPanel() {
    if (!this.side) return;
    const doc = this.doc;
    const cards = [];
    const spatial = this.spatialTiers;
    const shownKeys = new Set(spatial.map(t => t.task_id || t.id));
    // tiers present in the file
    for (const t of spatial) cards.push(this.regionCard(t));
    // project spatial tasks not in this file yet
    for (const id of state.project.tasks || []) {
      const task = state.taskById.get(id);
      if (!task || !SPATIAL.has(task.output) || !task.modalities.includes('image') || shownKeys.has(task.id)) continue;
      cards.push(this.regionCard(null, task));
    }
    const fieldTiers = sortTiers(doc.tiers).filter(t => !SPATIAL.has(t.output));
    for (const t of fieldTiers) cards.push(this.field(t));
    const present = new Set(doc.tiers.map(t => t.task_id || t.id));
    const suggested = (state.project.tasks || []).map(id => state.taskById.get(id))
      .filter(t => t && !SPATIAL.has(t.output) && t.modalities.includes('image') && !present.has(t.id));
    if (!doc.tiers.length) {
      cards.unshift(h('div.empty', h('div','No annotations yet. Run the project tasks, or draw / write them by hand.'),
        h('button.btn.primary.sm', { onclick: () => openRunDialog({ mediaIds: [this.media.id] }) }, ico('play', 13), 'Run tasks')));
    }
    if (suggested.length) {
      cards.push(h('div.menu-head', { style: { marginTop: '4px' } }, 'Not annotated yet'));
      for (const t of suggested) {
        cards.push(h('button.btn', { style: { justifyContent: 'flex-start' }, onclick: () => this.addTier(t) },
          ico('plus', 14), h('span.tchip', { style: { '--c': t.color } }, t.name), h('span.muted', t.title)));
      }
    }
    const scroll = this.side.scrollTop;
    this.side.replaceChildren(...cards);
    this.side.scrollTop = scroll;
  }

  regionCard(t, task = null) {
    const tier = t || tierFromTask(task);
    const segs = t ? this.doc.list(t.id) : [];
    const isPose = tier.output === 'keypoints';
    const hidden = t && this.hidden.has(t.id);
    const addBtn = h('button.btn.sm', {
      title: isPose ? 'Add a person skeleton (K)' : 'Draw a new box (B)',
      onclick: () => {
        if (isPose) { this.poseKey = tier.task_id || tier.id; this.setMode('pose'); }
        else { this.drawKey = tier.task_id || tier.id; this.pickDrawTiers(); this.setMode('box'); }
      },
    }, ico('plus', 12), isPose ? 'Person' : 'Box');
    const eye = t && h('button.icon-btn.sm', {
      title: hidden ? 'Show on image' : 'Hide on image', 'aria-label': hidden ? 'Show on image' : 'Hide on image',
      onclick: () => { hidden ? this.hidden.delete(t.id) : this.hidden.add(t.id); ls.set('imgHidden:' + this.pid, [...this.hidden]); this.renderShapes(); this.renderPanel(); },
    }, ico(hidden ? 'eye-off' : 'eye', 14));
    const rows = segs.map(s => this.regionRow(tier, s));
    return h('div.file-field.region-card',
      h('div.ff-head', h('b', { style: { '--c': tier.color } }, h('i'), tier.title || tier.name), h('span.badge', segs.length), h('div.grow'), eye, addBtn),
      rows.length ? h('div.region-list', rows) : h('div.muted', { style: { fontSize: '12px' } },
        isPose ? 'No people yet. Click "+ Person" and drag a box around someone.' : 'No boxes yet. Click "+ Box" and drag on the image.'));
  }

  regionRow(tier, s) {
    const sel = s.id === this.selId;
    const color = tier.output === 'boxes' ? segColor(tier, s.value) : tier.color;
    const g = s.geom || {};
    const nVis = g.type === 'keypoints' ? g.points.filter(p => p[2]).length : null;
    const row = h('div.region-row' + (sel ? '.sel' : ''), { dataset: { row: s.id }, onclick: (e) => { if (!e.target.closest('button')) this.select(s.id); } },
      h('i.dot', { style: { '--c': color } }),
      h('span.rl', valStr(s.value) || h('span.muted', 'no label'), nVis != null ? h('small', ` ${nVis}/${Math.max(17, g.points.length)} points`) : null),
      s.confidence != null ? confBar(s.confidence, this.lowThr) : null,
      srcBadge(s.source, s.meta?.edited_by),
      h('button.icon-btn.sm' + (s.reviewed ? '.active' : ''), { title: s.reviewed ? 'Reviewed (R)' : 'Mark reviewed (R)', 'aria-label': 'Reviewed', 'aria-pressed': String(!!s.reviewed), onclick: () => this.update(s.id, { reviewed: !s.reviewed }, 'Review') }, ico('check', 13)),
      h('button.icon-btn.sm', { title: 'Delete (Del)', 'aria-label': 'Delete region', onclick: () => this.deleteSeg(s.id) }, ico('trash', 13)));
    if (!sel) return row;
    const detail = [];
    if (tier.output === 'boxes') {
      detail.push(valueEditor({ ...tier, output: 'label' }, s.value, (v) => this.update(s.id, { value: v }, 'Edit label'), { doc: this.doc, allowEmpty: true }));
    } else {
      const pts = g.points || [];
      const n = Math.max(17, pts.length);
      const chips = [];
      for (let i = 0; i < n; i++) {
        const v = pts[i]?.[2] || 0;
        const cls = v === 2 ? 'vis' : v === 1 ? 'occ' : 'miss';
        chips.push(h(`button.kp-chip.${cls}${this.selKp === i ? '.on' : ''}${i >= 17 ? '.extra' : ''}`, {
          title: v ? `${kpName(g, i)} (${v === 1 ? 'occluded' : 'visible'}) · click to select` : `${kpName(g, i)} is missing · click, then click on the image to place it`,
          style: { '--k': kpColor(i) },
          onclick: () => (v ? this.select(s.id, i) : this.startPlacing(s.id, i)),
        }, h('i'), kpShort(g, i), v ? null : ico('plus', 10)));
      }
      chips.push(h('button.kp-chip.add', { title: 'Add a new point (P), then name it and connect it', onclick: () => this.startPlacing(s.id, 'new') }, ico('plus', 11), 'Point'));
      detail.push(h('div.kp-grid', chips));
      const edges = poseEdges(g);
      if (this.selKp != null && pts[this.selKp]?.[2]) {
        const k = this.selKp, v = pts[k][2];
        const nb = edges.filter(([a, b]) => a === k || b === k).map(([a, b]) => (a === k ? b : a));
        const others = pts.map((p, j) => j).filter(j => j !== k && pts[j][2] && !nb.includes(j));
        const nameInput = k >= 17 ? h('input.input.sm', {
          value: kpName(g, k), style: { width: '150px' },
          onchange: (e) => {
            const names = (g.names || []).slice();
            while (names.length < pts.length) names.push('');
            names[k] = e.target.value.trim() || `point ${k + 1}`;
            this.update(s.id, { geom: { ...g, names } }, 'Name point');
          },
        }) : h('b', { style: { fontSize: '13px', fontWeight: 600 } }, kpName(g, k));
        detail.push(h('div.kp-detail',
          h('div.row', { style: { gap: '6px', flexWrap: 'wrap' } }, nameInput,
            h('button.btn.sm', { onclick: () => this.setPoint(s.id, k, [pts[k][0], pts[k][1], v === 2 ? 1 : 2], 'Toggle occluded') }, v === 2 ? 'Occluded (O)' : 'Visible (O)'),
            k >= 17
              ? h('button.btn.sm', { onclick: () => this.removePoint(s.id, k) }, ico('trash', 12), 'Remove point')
              : h('button.btn.sm', { onclick: () => { this.setPoint(s.id, k, [0, 0, 0], 'Remove keypoint'); this.select(s.id, null); } }, ico('x', 12), 'Not visible (Del)')),
          h('div.row', { style: { gap: '5px', flexWrap: 'wrap', alignItems: 'center' } },
            h('span.muted', { style: { fontSize: '12px' } }, 'Connected to'),
            nb.length ? nb.map(j => h('span.chip', { style: { '--c': kpColor(j) } }, h('i.dot'), kpShort(g, j),
              h('span.x', { title: 'Remove connection', onclick: () => this.removeEdge(s.id, k, j) }, ico('x', 11))))
              : h('span.muted', { style: { fontSize: '12px' } }, 'nothing'),
            others.length ? h('select.select.sm', {
              style: { width: 'auto' }, 'aria-label': 'Connect to another point',
              onchange: (e) => { if (e.target.value !== '') this.addEdge(s.id, k, +e.target.value); },
            }, h('option', { value: '' }, '+ connect to…'), others.map(j => h('option', { value: j }, kpName(g, j)))) : null)));
      }
      if (this.selEdge) {
        const [a, b] = this.selEdge;
        detail.push(h('div.row', { style: { gap: '6px' } }, h('span.muted', { style: { fontSize: '12px' } }, `Connection ${kpName(g, a)} — ${kpName(g, b)}`),
          h('button.btn.sm', { onclick: () => this.removeEdge(s.id, a, b) }, ico('trash', 12), 'Remove (Del)')));
      }
      detail.push(h('div.muted', { style: { fontSize: '12px', lineHeight: 1.5 } },
        'Drag points to move them; Alt + drag moves the whole skeleton; Shift + drag from one point to another connects them; click a line and press Delete to remove it. Dashed chips are missing points: click one to place it.'));
    }
    if (s.meta?.model_geom || (s.meta?.model_value != null && s.meta.model_value !== s.value)) {
      detail.push(h('div.muted', { style: { fontSize: '12px' } }, 'Corrected by hand',
        s.meta.model_value != null && s.meta.model_value !== s.value ? `; model said "${valStr(s.meta.model_value)}"` : '',
        h('button.btn.sm.ghost', { style: { marginLeft: '6px' }, onclick: () => this.restoreModel(s.id) }, ico('undo', 12), 'Restore model output')));
    }
    return h('div', row, h('div.region-detail', detail));
  }

  restoreModel(id) {
    const s = this.doc.seg(id);
    if (!s?.meta) return;
    const patch = { source: s.meta.model_source || 'model', reviewed: false };
    if (s.meta.model_value != null) patch.value = s.meta.model_value;
    if (s.meta.model_geom) patch.geom = s.meta.model_geom;
    const { model_source, model_value, model_geom, ...rest } = s.meta;
    patch.meta = rest;
    this.doc.transact('Restore model output', (tx) => tx.update(id, patch));
  }

  field(t) {
    const s = this.doc.list(t.id)[0];
    const set = (v, o) => {
      this.localEdit = true;
      if (s) {
        const p = { value: v, reviewed: true };
        if (s.source !== 'human') { p.source = 'human'; p.meta = { ...(s.meta || {}), model_source: s.source, model_value: s.meta?.model_value ?? s.value }; }
        this.doc.transact('Edit value', (tx) => tx.update(s.id, p), { merge: o?.merge });
      } else {
        this.doc.transact('Edit value', (tx) => tx.add({ tier_id: t.id, start: null, end: null, value: v, reviewed: true }));
      }
      this.localEdit = false;
    };
    const rev = s && h('button.icon-btn.sm' + (s.reviewed ? '.active' : ''), {
      title: s.reviewed ? 'Reviewed' : 'Mark reviewed',
      onclick: () => this.doc.transact('Review', (tx) => tx.update(s.id, { reviewed: !s.reviewed })),
    }, ico('check', 14));
    return h('div.file-field', { dataset: { tier: t.id } },
      h('div.ff-head', h('b', { style: { '--c': t.color } }, h('i'), t.title || t.name), h('div.grow'),
        s?.confidence != null ? confBar(s.confidence, this.lowThr) : null, s ? srcBadge(s.source, s.meta?.edited_by) : null, rev),
      valueEditor(t, s?.value ?? (t.output === 'tags' ? [] : ''), set, { doc: this.doc }),
      s?.meta?.model_value != null ? h('div.muted', { style: { fontSize: '12px' } }, 'Model said: ', valStr(s.meta.model_value)) : null);
  }

  addTier(task) {
    this.doc.transact('Add tier', (tx) => tx.setTiers([...this.doc.tiers, tierFromTask(task)]));
  }

  afterChange() {
    if (this.selId && !this.doc.seg(this.selId)) { this.selId = null; this.selKp = null; }
    this.renderShapes();
    const focused = this.side.contains(document.activeElement) && /INPUT|TEXTAREA/.test(document.activeElement.tagName);
    if (this.localEdit && focused) return;
    this.pickDrawTiers();
    this.renderPanel();
  }

  afterReload() {
    this.selId = null;
    this.renderAll();
  }

  // ------------------------------------------------------------------ keyboard
  allShapes() {
    return this.spatialTiers.filter(t => !this.hidden.has(t.id)).flatMap(t => this.doc.list(t.id)).filter(s => s.geom);
  }

  nudge(dx, dy) {
    const s = this.selId && this.doc.seg(this.selId);
    if (!s?.geom) return;
    const ux = dx / this.W, uy = dy / this.H;
    if (s.geom.type === 'keypoints' && this.selKp != null && s.geom.points[this.selKp]?.[2]) {
      const p = s.geom.points[this.selKp];
      this.setPoint(s.id, this.selKp, [p[0] + ux, p[1] + uy, p[2]], 'Nudge keypoint', true);
    } else if (s.geom.type === 'box') {
      const g = s.geom;
      this.update(s.id, { geom: { ...g, x: clamp(g.x + ux, 0, 1 - g.w), y: clamp(g.y + uy, 0, 1 - g.h) } }, 'Nudge box', true);
    } else {
      this.update(s.id, { geom: { ...s.geom, points: s.geom.points.map(([x, y, v]) => [x + ux, y + uy, v]) } }, 'Nudge pose', true);
    }
  }

  onKey(e) {
    if (e.key === ' ' && !isTyping(e) && this.el.offsetParent) { e.preventDefault(); this.space = true; this.stage?.classList.add('panning'); return; }
    if (modalOpen() || isTyping(e) || !this.el.isConnected || !this.el.offsetParent || document.querySelector('.popover')) return;
    const mod = e.metaKey || e.ctrlKey, k = e.key;
    if (mod) {
      if (k.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? this.redo() : this.undo(); }
      else if (k.toLowerCase() === 'y') { e.preventDefault(); this.redo(); }
      else if (k.toLowerCase() === 's') { e.preventDefault(); this.flush(); }
      return;
    }
    const s = this.selId && this.doc.seg(this.selId);
    const step = e.shiftKey ? 10 : 1;
    switch (k) {
      case 'Escape':
        if (this.placing) { this.placing = null; this.renderTools(); }
        else if (this.mode !== 'select') this.setMode('select');
        else this.select(null);
        break;
      case 'v': case 'V': this.setMode('select'); break;
      case 'b': case 'B': this.setMode('box'); break;
      case 'k': case 'K': this.setMode('pose'); break;
      case 'h': case 'H': this.toggleOverlay(); break;
      case 'p': case 'P': if (s?.geom?.type === 'keypoints') this.startPlacing(s.id, 'new'); break;
      case '0': this.fit(); break;
      case '+': case '=': this.zoomBy(1.4); break;
      case '-': case '_': this.zoomBy(1 / 1.4); break;
      case 'Delete': case 'Backspace':
        if (!s) break;
        e.preventDefault();
        if (s.geom?.type === 'keypoints' && this.selEdge) this.removeEdge(s.id, ...this.selEdge);
        else if (s.geom?.type === 'keypoints' && this.selKp != null && this.selKp >= 17) this.removePoint(s.id, this.selKp);
        else if (s.geom?.type === 'keypoints' && this.selKp != null) { this.setPoint(s.id, this.selKp, [0, 0, 0], 'Remove keypoint'); this.select(s.id, null); }
        else this.deleteSeg(s.id);
        break;
      case 'o': case 'O':
        if (s?.geom?.type === 'keypoints' && this.selKp != null) {
          const p = s.geom.points[this.selKp];
          if (p?.[2]) this.setPoint(s.id, this.selKp, [p[0], p[1], p[2] === 2 ? 1 : 2], 'Toggle occluded');
        }
        break;
      case 'r': case 'R': if (s) this.update(s.id, { reviewed: !s.reviewed }, 'Review'); break;
      case 'a': case 'A': {
        if (s) this.update(s.id, { reviewed: true }, 'Accept');
        const list = this.allShapes();
        const i = list.findIndex(x => x.id === this.selId);
        const nxt = list.slice(i + 1).concat(list.slice(0, i + 1)).find(x => !x.reviewed);
        if (nxt) this.select(nxt.id); else toast('Every region on this image is reviewed', { kind: 'ok' });
        break;
      }
      case 'Tab': {
        e.preventDefault();
        const list = this.allShapes();
        if (!list.length) break;
        const i = list.findIndex(x => x.id === this.selId);
        this.select(list[(i + (e.shiftKey ? -1 : 1) + list.length) % list.length].id);
        break;
      }
      case 'Enter': if (s?.geom?.type === 'box') { e.preventDefault(); this.askLabel(s.id); } break;
      case 'ArrowLeft': if (s) { e.preventDefault(); this.nudge(-step, 0); } break;
      case 'ArrowRight': if (s) { e.preventDefault(); this.nudge(step, 0); } break;
      case 'ArrowUp': if (s) { e.preventDefault(); this.nudge(0, -step); } break;
      case 'ArrowDown': if (s) { e.preventDefault(); this.nudge(0, step); } break;
      case '[': this.dv.step(-1); break;
      case ']': this.dv.step(1); break;
      default:
        if (/^[1-9]$/.test(k) && s && s.geom?.type === 'box') {
          const tier = this.doc.tier(s.tier_id);
          const l = tierLabels({ ...tier, output: 'label' }, this.doc)[+k - 1];
          if (l != null) this.update(s.id, { value: l === s.value ? '' : l }, 'Edit label');  // same key again clears the label
        }
    }
  }

  destroy() {
    super.destroy();
    this.ro?.disconnect();
    document.removeEventListener('keydown', this.onKey);
    document.removeEventListener('keyup', this.onKeyUp);
  }
}
