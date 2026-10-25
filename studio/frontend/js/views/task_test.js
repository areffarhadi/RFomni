// "Test on a sample": dry-run a task (saved or an unsaved draft) on a few files of the project.
// Nothing is saved; the results are only shown here.
import { api, urls } from '../api.js';
import { h, ico, fmtTime, fmtDur, valStr, segColor, modalityIcon } from '../util.js';
import { state } from '../state.js';
import { go, links } from '../nav.js';
import { openModal, errorToast } from '../ui.js';
import { openRunDialog } from './run.js';
import { confBar } from './review.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const MAX_ROWS = 12;

export function openTaskTest({ task, saved = true }) {
  const pid = state.project?.id;
  if (!pid) return;
  let n = 3, seed = Math.floor(Math.random() * 1e6);
  const audio = new Audio();
  let stopAt = null;
  audio.addEventListener('timeupdate', () => { if (stopAt != null && audio.currentTime >= stopAt) { audio.pause(); stopAt = null; } });
  const play = (mid, a, b) => {
    const src = urls.file(pid, mid);
    if (!audio.src.endsWith(src)) audio.src = src;
    const go_ = () => { audio.currentTime = a; stopAt = b; audio.play().catch(() => {}); };
    if (audio.readyState >= 1) go_(); else audio.addEventListener('loadedmetadata', go_, { once: true });
  };

  const sizeSel = h('select.select.sm', { style: { width: 'auto' }, onchange: (e) => { n = +e.target.value; run(); } },
    [1, 3, 5, 10].map(k => h('option', { value: k, selected: k === n }, `${k} file${k > 1 ? 's' : ''}`)));
  const body = h('div.tt-results');
  const promptBox = h('div.prompt-box', { style: { maxHeight: '120px' } });
  const info = h('span.tt-info');
  const engine = state.info?.engine;
  const m = openModal({
    title: `Test: ${task.title || task.name}`, width: 920, className: 'tt-dlg',
    subtitle: 'Runs the task on a few random files of this project. Nothing is saved to your annotations.',
    body: h('div',
      engine && !engine.connected ? h('div.notice', { style: { marginBottom: '12px' } }, ico('alert', 16),
        h('div', 'No model connected: the mock engine returns placeholder values. Segment timing (speech regions) is real. With a model connected this preview shows real output.')) : null,
      h('div.tt-bar',
        h('span.tt-label', 'Sample'), sizeSel,
        h('button.btn.sm', { onclick: () => { seed = Math.floor(Math.random() * 1e6); run(); } }, ico('repeat', 13), 'Shuffle'),
        h('div.grow'), info),
      h('details.tt-prompt', h('summary', ico('chev-r', 12), 'Prompt sent to the model'), promptBox),
      body),
    onClose: () => { audio.pause(); audio.src = ''; },
    actions: [
      { label: 'Close', ghost: true, onClick: (a) => a.close() },
      { id: 'all', label: saved ? 'Run on all files…' : 'Save the task to run it on all files', primary: true, icon: 'sparkles', disabled: !saved,
        onClick: (a) => { a.close(); openRunDialog({ taskIds: [task.id] }); } },
    ],
  });

  async function run() {
    body.replaceChildren(h('div.empty', h('div.spinner'), 'Running on a sample…'));
    try {
      const r = await api.testTask(pid, { task, n, seed });
      promptBox.textContent = r.prompt;
      info.textContent = r.pool ? `${r.results.length} of ${r.pool} matching file${r.pool > 1 ? 's' : ''}` : '';
      if (!r.results.length) {
        body.replaceChildren(h('div.empty', ico('search', 26), h('h3', 'No matching files'),
          h('div', `This task applies to ${task.modalities.join(', ')}; this project has no such files yet.`)));
        return;
      }
      body.replaceChildren(...r.results.map(res => fileCard(res)));
    } catch (e) {
      body.replaceChildren(h('div.empty', ico('alert', 24), e.message));
      errorToast(e, 'Test failed');
    }
  }

  function fileCard(res) {
    const md = res.media;
    const head = h('div.tt-head', ico(modalityIcon(md.modality), 15), h('b', md.name),
      h('span.muted', md.duration ? fmtDur(md.duration) : md.width ? `${md.width}×${md.height}` : ''),
      h('span.badge', `${res.count} result${res.count === 1 ? '' : 's'}`), h('div.grow'),
      h('button.btn.sm.ghost', { onclick: () => { m.close(); go(links.file(pid, md.id)); } }, 'Open file', ico('chev-r', 13)));
    if (res.error) return h('div.tt-file', head, h('div.notice', ico('alert', 15), h('div', res.error)));
    const segs = res.segments;
    const spatial = segs.some(s => s.geom);
    let content;
    if (spatial && md.modality === 'image') content = imagePreview(md, segs);
    else if (!segs.length) content = h('div.tt-none', 'No output for this file.');
    else content = h('div.tt-rows', segs.slice(0, MAX_ROWS).map(s => row(md, s)),
      segs.length > MAX_ROWS ? h('div.tt-more', `+ ${segs.length - MAX_ROWS} more`) : null);
    return h('div.tt-file', head, content);
  }

  function row(md, s) {
    const timed = s.start != null && s.end != null && md.modality !== 'image';
    const v = valStr(s.value);
    const isLabel = task.output === 'label' || task.output === 'tags' || task.output === 'score';
    return h('div.tt-row',
      timed ? h('button.icon-btn.sm', { title: 'Play this part', onclick: () => play(md.id, s.start, s.end) }, ico('play', 12)) : h('span.ico-spacer'),
      h('span.mono.tt-time', timed ? `${fmtTime(s.start)} – ${fmtTime(s.end)}` : 'whole file'),
      isLabel ? h('span.chip', { style: { '--c': segColor(task, v) } }, h('i.dot'), v || h('span.muted', 'no label'))
        : h('span.tt-text', v || h('span.muted', '(empty)')),
      h('div.grow'),
      s.confidence != null ? confBar(s.confidence) : null);
  }

  function imagePreview(md, segs) {
    const W = md.width || 1, H = md.height || 1;
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('class', 'tt-svg');
    const add = (tag, attrs) => { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); svg.append(el); return el; };
    for (const s of segs) {
      const g = s.geom;
      if (!g) continue;
      const col = segColor(task, valStr(s.value)) || task.color;
      if (g.type === 'box') {
        add('rect', { x: g.x * W, y: g.y * H, width: g.w * W, height: g.h * H, fill: 'none', stroke: col, 'stroke-width': 3, 'vector-effect': 'non-scaling-stroke' });
      } else {
        for (const [x, y, v] of g.points) if (v) add('circle', { cx: x * W, cy: y * H, r: Math.max(W, H) / 150, fill: col });
      }
    }
    return h('div.tt-img',
      h('div.tt-img-wrap', h('img', { src: urls.file(pid, md.id), alt: md.name }), svg),
      h('div.tt-rows', segs.slice(0, MAX_ROWS).map(s => row(md, s))));
  }

  run();
}
