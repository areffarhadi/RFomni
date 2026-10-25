// Review queue: low-confidence segments across the whole project.
import { api } from '../api.js';
import { h, ico, fmtTime, modalityIcon, debounce } from '../util.js';
import { state, projectTasks, updateProject, refreshProject } from '../state.js';
import { go, links } from '../nav.js';
import { errorToast } from '../ui.js';

export class ReviewView {
  constructor(pv) {
    this.pv = pv;
    this.thr = state.project?.settings?.low_confidence ?? 0.6;
    this.task = '';
    this.thrLabel = h('span.thr-val', this.thr.toFixed(2));
    const slider = h('input', {
      type: 'range', min: 0.05, max: 1, step: 0.05, value: this.thr,
      oninput: (e) => { this.thr = +e.target.value; this.thrLabel.textContent = this.thr.toFixed(2); this.loadSoon(); },
      onchange: () => updateProject({ settings: { low_confidence: this.thr } }).then(refreshProject).catch(errorToast),
    });
    const taskSel = h('select.select', { style: { width: '220px' }, 'aria-label': 'Tier to review', onchange: (e) => { this.task = e.target.value; this.load(); } },
      h('option', { value: '' }, 'All tiers'), projectTasks().map(t => h('option', { value: t.id }, t.title)));
    this.count = h('span.badge');
    this.body = h('div');
    this.el = h('div.page', h('div.page-inner',
      h('div.page-head', h('div', h('h1', 'Review queue'),
        h('p', 'Segments the model was least sure about, lowest confidence first. Open one to listen, fix it and mark it reviewed (R); reviewed segments leave the queue.'))),
      h('div.section',
        h('div.section-head',
          h('div.review-tools',
            h('label.thr', h('span', 'Confidence below'), slider, this.thrLabel),
            taskSel),
          this.count),
        this.body)));
    this.loadSoon = debounce(() => this.load(), 200);
    this.load();
  }

  async load() {
    this.body.replaceChildren(h('div.empty', h('div.spinner')));
    let d;
    try { d = await api.review(state.project.id, { threshold: this.thr, task: this.task }); } catch (e) { errorToast(e); return; }
    this.count.textContent = `${d.total} segment${d.total === 1 ? '' : 's'}`;
    if (!d.items.length) {
      this.body.replaceChildren(h('div.empty', ico('check', 30), h('h3', 'Nothing to review'),
        h('div', 'No unreviewed segments below this confidence. Segments without a confidence score (hand-made or imported) never appear here.')));
      return;
    }
    const pid = state.project.id;
    this.body.replaceChildren(h('div', { style: { maxHeight: 'calc(100vh - 260px)', overflow: 'auto' }, tabindex: 0, 'aria-label': 'Segments to review' }, h('table.list',
      h('thead', h('tr', h('th', 'Confidence'), h('th', 'File'), h('th', 'Tier'), h('th.num', 'Time'), h('th', 'Value'), h('th.go', h('span.sr-only', 'Open')))),
      h('tbody', d.items.map(r => h('tr', { onclick: () => go(links.file(pid, r.media_id, { seg: r.segment_id })) },
        h('td', confBar(r.confidence, this.thr)),
        h('td', h('span.file', ico(modalityIcon(r.modality), 13), r.media_name)),
        h('td', h('span.tchip', { style: { '--c': r.color } }, r.tier_name)),
        h('td.num', r.start == null ? 'file' : `${fmtTime(r.start)}`),
        h('td.val-cell', { title: r.value }, r.value || h('span.muted', '(empty)')),
        h('td.go', ico('chev-r', 14))))))),
    d.total > d.items.length ? h('div.review-more', `Showing the ${d.items.length} lowest of ${d.total}.`) : null);
  }

  destroy() {}
}

export function confBar(v, thr = 0.6) {
  const c = v < thr * 0.66 ? 'var(--danger)' : v < thr ? 'var(--warn)' : 'var(--accent)';
  return h('span.confbar', { style: { '--v': v, '--cc': c } }, h('i'), v == null ? '–' : v.toFixed(2));
}
