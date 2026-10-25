// Projects overview.
import { api, urls } from '../api.js';
import { h, ico, fmtDur } from '../util.js';
import { state } from '../state.js';
import { go, links } from '../nav.js';
import { openModal, menu, confirmDialog, promptDialog, errorToast, toast } from '../ui.js';

export function ago(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function taskChip(t) {
  return h('span.tchip', { style: { '--c': t.color }, title: t.title }, t.name);
}

export class HomeView {
  constructor() {
    this.grid = h('div.proj-grid');
    // every folder's outline follows its size (the shapes are paths, see folderPaths)
    this.ro = new ResizeObserver((entries) => entries.forEach(e => shapeFolder(e.target)));
    this.el = h('div.home', h('div.home-inner',
      h('div.home-head',
        h('div', h('h1', 'Projects'),
          h('p', 'A project is a folder of audio, video and images plus the annotation tasks you want to apply to all of it. ' +
            'Run the tasks, then review and correct every file in the tier editor.')),
        h('button.btn.primary', { onclick: () => newProjectDialog() }, ico('plus', 15), 'New project')),
      this.grid,
      h('p.proj-hint', 'Rest on a folder to see its files; click it to open the project.')));
    this.load();
  }

  async load() {
    let projects = [];
    try { projects = await api.projects(); } catch (e) {
      errorToast(e);
      this.grid.replaceChildren(h('div.empty.home-empty', ico('alert', 24), h('h2', 'Could not load projects'),
        h('p', e.message || 'The server did not respond.'),
        h('button.btn', { onclick: () => this.load() }, 'Try again')));
      return;
    }
    if (!projects.length) {
      this.grid.replaceChildren(h('div.empty.home-empty', ico('folder', 28), h('h2', 'No projects yet'),
        h('p', 'Create a project, add audio, video or images to it, and choose the annotation tasks to run.'),
        h('button.btn.primary', { onclick: () => newProjectDialog() }, ico('plus', 15), 'New project')));
      return;
    }
    const cards = projects.map(p => this.card(p));
    const outline = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    outline.classList.add('pf-outline');
    outline.setAttribute('aria-hidden', 'true');
    outline.append(document.createElementNS('http://www.w3.org/2000/svg', 'path'));
    cards.push(h('button.pfold.new', { type: 'button', dataset: { guide: 'home.new' }, onclick: () => newProjectDialog() }, outline,
      h('span.pf-new', ico('folder-plus', 22), h('b', 'New project'), h('small', 'Name it, pick tasks, add data'))));
    this.ro.disconnect();
    this.grid.replaceChildren(...cards);
    cards.forEach(c => this.ro.observe(c));
  }

  // A project is drawn as a folder: artwork on the back (its first picture, or the colours of its tasks),
  // a frosted front with the name and progress, and a few of its files inside. Resting on the folder
  // (or focusing it) leans the front forward and lifts the files out, fanned like cards.
  card(p) {
    const s = p.stats || {};
    const tasks = (p.tasks || []).map(id => state.taskById.get(id)).filter(Boolean);
    const ann = s.files ? (s.annotated / s.files) * 100 : 0;
    const rev = s.files ? (s.reviewed / s.files) * 100 : 0;
    const open = () => go(links.project(p.id));
    const more = h('button.pf-menu', {
      type: 'button', title: 'More', 'aria-label': `More actions for ${p.name}`,
      onclick: (e) => {
        e.stopPropagation();
        menu(more, [
          { label: 'Rename', icon: 'edit', onClick: () => this.rename(p) },
          'sep',
          { label: 'Delete project', icon: 'trash', danger: true, onClick: () => this.remove(p) },
        ]);
      },
    }, ico('more', 16));
    const palette = [...tasks.map(t => t.color), 'var(--accent)'].slice(0, 1);  // a folder without a picture: its first tier's colour, flat
    const back = h('div.pf-back', { style: { '--c1': palette[0] } },
      p.cover ? h('img.pf-cover', { src: urls.thumb(p.id, p.cover), alt: '', loading: 'lazy', draggable: false }) : null);
    const preview = p.preview || [];
    const extra = (s.files || 0) - preview.length;
    const shown = [...preview.map(m => fileCard(p, m)), extra > 0 ? h('div.pff.more', h('b', `+${extra}`), h('span', extra === 1 ? 'more file' : 'more files')) : null].filter(Boolean);
    fan(shown);
    const files = h('div.pf-files', { 'aria-hidden': 'true' }, shown);
    const by = s.by_modality || {};
    const parts = [by.audio ? `${by.audio} audio` : '', by.video ? `${by.video} video` : '', by.image ? `${by.image} image${by.image > 1 ? 's' : ''}` : ''].filter(Boolean);
    const front = h('div.pf-front',
      h('h2', { title: p.name }, h('a', { href: links.project(p.id), onclick: (e) => e.stopPropagation() }, p.name)),
      h('div.pf-meta', s.files ? [`${s.files} file${s.files > 1 ? 's' : ''}`, s.duration ? fmtDur(s.duration) : '', parts.join(', ')].filter(Boolean).join(', ') : 'No files yet'),
      h('div.pf-tasks', tasks.length ? `${tasks.length} task${tasks.length > 1 ? 's' : ''}: ${tasks.slice(0, 3).map(t => t.name).join(', ')}${tasks.length > 3 ? '…' : ''}` : 'No tasks selected yet'),
      h('div.pf-foot',
        h('div.pf-prog',
          h('div.progress.multi', h('i', { style: { width: rev + '%', background: 'var(--ok)' } }), h('i', { style: { width: Math.max(0, ann - rev) + '%' } })),
          h('span', `${s.annotated || 0}/${s.files || 0} annotated, ${ago(p.updated_at)}`)),
        more));
    const el = h('div.pfold', { onclick: open, title: p.description || '' }, back, files, front);
    el.addEventListener('pointerenter', () => loadWaves(el), { once: true });
    el.addEventListener('focusin', () => loadWaves(el), { once: true });
    return el;
  }

  async rename(p) {
    const name = await promptDialog('Project name', { value: p.name, title: 'Rename project' });
    if (!name) return;
    await api.patchProject(p.id, { name }).catch(errorToast);
    this.load();
  }

  async remove(p) {
    const ok = await confirmDialog(
      `Delete "${p.name}"? Uploaded copies and all annotations in this project are deleted. Files you linked from other folders are not touched.`,
      { title: 'Delete project', ok: 'Delete project' });
    if (!ok) return;
    await api.deleteProject(p.id).catch(errorToast);
    toast('Project deleted', { kind: 'ok' });
    this.load();
  }

  destroy() { this.ro.disconnect(); }
}

// ---- project folders --------------------------------------------------------
// One file of the project, as a little card that rises out of the folder: a picture for images and
// videos, a waveform for audio (its real peaks, loaded the first time the folder opens).
function fileCard(p, m) {
  const ext = (m.name.split('.').pop() || '').toUpperCase();
  const meta = m.modality === 'image' ? `${ext}, ${m.width}×${m.height}` : `${ext}, ${fmtDur(m.duration || 0)}`;
  const body = m.modality === 'audio'
    ? h('canvas.pff-wave', { dataset: { pid: p.id, mid: m.id, dur: m.duration || 0 } })
    : h('img.pff-img', { src: urls.thumb(p.id, m.id), alt: '', loading: 'lazy', draggable: false });
  return h('div.pff.' + m.modality, h('div.pff-head', h('b', m.name), h('span', meta)), body);
}

// where each card goes when the folder opens: spread left to right, a little turned, the middle ones highest
function fan(cards) {
  const n = cards.length;
  cards.forEach((c, i) => {
    const k = n > 1 ? i / (n - 1) - 0.5 : 0;      // -0.5 .. 0.5
    c.style.setProperty('--i', i);
    c.style.setProperty('--x', `${(k * (n > 2 ? 64 : 40)).toFixed(1)}%`);
    c.style.setProperty('--y', `${(-50 - 10 * (1 - Math.abs(k) * 2)).toFixed(1)}%`);
    c.style.setProperty('--r', `${(k * (n > 2 ? 16 : 10)).toFixed(1)}deg`);
  });
}

async function loadWaves(folder) {
  for (const cv of folder.querySelectorAll('canvas.pff-wave')) {
    const { pid, mid, dur } = cv.dataset;
    try {
      const r = await fetch(urls.peaks(pid, mid, 0, +dur || 0, 64)).then(x => x.json());
      const dpr = Math.min(2, window.devicePixelRatio || 1), w = cv.clientWidth || 160, ht = cv.clientHeight || 60;
      cv.width = w * dpr; cv.height = ht * dpr;
      const ctx = cv.getContext('2d');
      ctx.scale(dpr, dpr);
      ctx.fillStyle = getComputedStyle(cv).color;
      const n = r.max.length, bw = w / n, top = Math.max(1e-3, r.peak || 1);
      for (let i = 0; i < n; i++) {
        const a = Math.min(1, Math.max(Math.abs(r.max[i]), Math.abs(r.min[i])) / top);
        const bh = Math.max(2, a * ht * 0.9);
        ctx.fillRect(i * bw + bw * 0.2, (ht - bh) / 2, bw * 0.6, bh);
      }
    } catch { /* the card keeps its plain look */ }
  }
}

// The folder outlines: a back with a raised tab on the right, a front pocket whose left part stands
// higher, joined by smooth S-curves. Drawn as clip paths so the corners stay round at any width.
const R = 16, TAB = 22, CURVE = 30, STEP = 24;
function folderPaths(w, h, fh) {
  const tx = Math.round(w * 0.46), fx = Math.round(w * 0.34);
  const back = `M0 ${TAB + R} Q0 ${TAB} ${R} ${TAB} L${tx - CURVE} ${TAB} C${tx - CURVE / 2} ${TAB} ${tx - CURVE / 2} 0 ${tx} 0 ` +
    `L${w - R} 0 Q${w} 0 ${w} ${R} L${w} ${h - R} Q${w} ${h} ${w - R} ${h} L${R} ${h} Q0 ${h} 0 ${h - R} Z`;
  const front = `M0 ${R} Q0 0 ${R} 0 L${fx} 0 C${fx + CURVE / 2} 0 ${fx + CURVE / 2} ${STEP} ${fx + CURVE} ${STEP} ` +
    `L${w - R} ${STEP} Q${w} ${STEP} ${w} ${STEP + R} L${w} ${fh - R} Q${w} ${fh} ${w - R} ${fh} L${R} ${fh} Q0 ${fh} 0 ${fh - R} Z`;
  return { back, front };
}
function shapeFolder(el) {
  const w = el.clientWidth, h = el.clientHeight;
  if (!w || !h) return;
  const front = el.querySelector('.pf-front');
  const { back, front: fp } = folderPaths(w, h, front ? front.offsetHeight : h * 0.72);
  const b = el.querySelector('.pf-back');
  if (b) b.style.clipPath = `path('${back}')`;
  if (front) front.style.clipPath = `path('${fp}')`;
  const outline = el.querySelector('.pf-outline');
  if (outline) {   // the "new project" folder: the same outline, dashed
    outline.setAttribute('viewBox', `-1 -1 ${w + 2} ${h + 2}`);
    outline.querySelector('path').setAttribute('d', back);
  }
}

export function newProjectDialog() {
  let preset = state.presets[0]?.id || null;
  const name = h('input.input', { placeholder: 'e.g. Parliament debates 2026', autofocus: true });
  const desc = h('textarea.textarea', { rows: 2, placeholder: 'Optional notes about the data or the annotation guidelines' });
  const presetEls = [];
  const opts = [...state.presets, { id: null, name: 'Choose later', tasks: [] }];
  const pick = (el, id) => {
    preset = id;
    presetEls.forEach(x => { x.classList.toggle('on', x === el); x.setAttribute('aria-checked', String(x === el)); });
  };
  const grid = h('div.preset-grid', { role: 'radiogroup', 'aria-label': 'Start with tasks' }, opts.map(p => {
    const el = h('div.preset' + (p.id === preset ? '.on' : ''), {
      role: 'radio', tabindex: 0, 'aria-checked': String(p.id === preset),
      onclick: () => pick(el, p.id),
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(el, p.id); } },
    }, h('b', p.name), h('div.tasks', p.tasks.length
      ? p.tasks.map(id => state.taskById.get(id)).filter(Boolean).map(taskChip)
      : h('span.muted', 'Pick tasks from the library after creating the project')));
    presetEls.push(el);
    return el;
  }));
  const create = async (m) => {
    const tasks = opts.find(p => p.id === preset)?.tasks || [];
    try {
      const p = await api.createProject({ name: name.value.trim() || 'Untitled project', description: desc.value.trim(), tasks });
      m.close();
      go(links.project(p.id));
    } catch (e) { errorToast(e); }
  };
  const m = openModal({
    title: 'New project', subtitle: 'You can add data and change tasks at any time.', width: 620,
    body: h('div',
      h('label.field', h('span', 'Name'), name),
      h('label.field', h('span', 'Description'), desc),
      h('h3', 'Start with tasks'), grid),
    actions: [{ label: 'Cancel', ghost: true, onClick: (a) => a.close() }, { label: 'Create project', primary: true, onClick: create }],
  });
  name.addEventListener('keydown', (e) => { if (e.key === 'Enter') create(m); });
}
