// Statistics tab: what is in the dataset, how the labels are distributed, how far the review is,
// how often reviewers changed the model's output, and speaker / overlap figures.
import { api, urls } from '../api.js';
import { h, ico, fmtDur, fmtBytes, segColor, modalityIcon, ls, download } from '../util.js';
import { state, on, canDownload } from '../state.js';
import { go, links } from '../nav.js';
import { errorToast } from '../ui.js';

const OUT_NAMES = { text: 'text', label: 'label', tags: 'tags', score: 'score', boxes: 'boxes', keypoints: 'keypoints' };
const SRC_COLORS = { model: 'var(--accent)', mock: 'var(--faint)', human: 'var(--ok)', derived: '#a78bfa', import: '#60a5fa', pseudo: 'var(--accent)' };
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const pct1 = (x) => `${(x * 100).toFixed(x < 0.1 && x > 0 ? 1 : 0)}%`;
const secs = (s) => (s >= 60 ? fmtDur(s) : `${s.toFixed(1)} s`);
const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

function bar(frac, color, label) {
  return h('div.st-track', { title: label || '' }, h('i', { style: { width: `${Math.max(frac > 0 ? 1.5 : 0, frac * 100)}%`, background: color } }));
}

function stacked(parts, total) {
  return h('div.st-stack', parts.filter(p => p.n).map(p =>
    h('i', { style: { width: `${(p.n / total) * 100}%`, background: p.color }, title: `${p.label}: ${p.n} (${pct(p.n, total)}%)` })));
}

function legend(parts) {
  return h('div.st-legend', parts.filter(p => p.n).map(p => h('span', h('i', { style: { background: p.color } }), `${p.label} `, h('b', p.n))));
}

function card(title, icon, ...body) {
  return h('div.st-card', h('div.st-card-head', ico(icon, 15), h('h3', title)), ...body);
}

function histo(vals, color, labels) {
  const max = Math.max(1, ...vals);
  return h('div.st-histo', vals.map((v, i) => h('div.st-hcol', { title: `${labels ? labels[i] : i}: ${v}` },
    h('i', { style: { height: `${(v / max) * 100}%`, background: color } }))));
}

export class StatsView {
  constructor(pv) {
    this.pv = pv;
    this.mode = ls.get('statsMode', 'count');
    this.body = h('div', h('div.empty', h('div.spinner'), 'Counting…'));
    const pid = state.project.id;
    this.el = h('div.page', h('div.page-inner.wide',
      h('div.page-head',
        h('div', h('h1', 'Statistics'),
          h('p', 'What is in this dataset and how far the labelling is: files, label distributions per task, review progress, ' +
            'how often reviewers changed the model output, and speaker overlap.')),
        h('div.row',
          h('button.btn', { onclick: () => this.load() }, ico('repeat', 14), 'Refresh'),
          canDownload() ? h('button.btn', { onclick: () => download(urls.statsCsv(pid)) }, ico('download', 14), 'CSV') : null)),
      this.body));
    this.unsub = [on('job-done', () => this.load())];
    this.load();
  }

  async load() {
    try { this.st = await api.stats(state.project.id); } catch (e) { errorToast(e); this.body.replaceChildren(h('div.empty', ico('alert', 24), e.message)); return; }
    this.render();
  }

  render() {
    const st = this.st, d = st.dataset, rv = st.review;
    const by = d.by_modality || {};
    const human = (rv.sources.human || 0);
    const overview = h('div.stat-row',
      ov('database', 'Files', d.files, null, h('div.sub', ['audio', 'video', 'image'].map(m => h('span', ico(modalityIcon(m), 12), ' ', by[m] || 0)))),
      ov('audio', 'Audio & video', d.duration ? fmtDur(d.duration) : '–', null, h('div.sub', fmtBytes(d.size || 0) + ' on disk')),
      ov('layers', 'Segments', rv.segments.toLocaleString(), null, h('div.sub', `${st.tasks.filter(t => t.segments).length} tasks with output`)),
      ov('check', 'Reviewed', `${pct(rv.reviewed, rv.segments)}%`, null, h('div.progress', h('i', { style: { width: pct(rv.reviewed, rv.segments) + '%', background: 'var(--ok)' } }))),
      ov('user', 'Made or fixed by people', human.toLocaleString(), 'segments', h('div.sub', `${pct(human, rv.segments)}% of all segments`)));

    const parts = [overview];
    if (st.warnings.length) parts.push(this.warnings(st.warnings));
    parts.push(h('div.st-grid2', this.filesCard(d), this.reviewCard(st)));
    if (st.speakers) parts.push(this.speakers(st.speakers));
    parts.push(this.tasks(st.tasks));
    this.body.replaceChildren(...parts);
  }

  warnings(ws) {
    const open = ls.get('statsWarnOpen', true);
    const det = h('details.st-warn', { open },
      h('summary', ico('flag', 14), `Things to look at (${ws.length})`),
      h('ul', ws.map(w => h('li.' + w.level, w.text))));
    det.addEventListener('toggle', () => ls.set('statsWarnOpen', det.open));
    return det;
  }

  filesCard(d) {
    const stLabels = { new: 'Not annotated', annotated: 'Annotated', in_review: 'In review', reviewed: 'Reviewed' };
    const stColors = { new: 'var(--line-2)', annotated: 'var(--accent)', in_review: 'var(--warn)', reviewed: 'var(--ok)' };
    const sp = Object.keys(stLabels).map(k => ({ label: stLabels[k], n: d.status[k] || 0, color: stColors[k] }));
    const lh = d.length_hist.filter((b, i, a) => b.files || a.slice(i).some(x => x.files));
    const maxL = Math.max(1, ...lh.map(b => b.files));
    return card('Files', 'database',
      h('div.st-sub', 'Status'), stacked(sp, d.files || 1), legend(sp),
      lh.some(b => b.files) ? [h('div.st-sub', 'Length of audio and video files'),
        h('div.st-bars', lh.map(b => h('div.st-brow', h('span.st-bl', b.label), bar(b.files / maxL, 'var(--accent)'), h('span.st-bn', b.files))))] : null);
  }

  reviewCard(st) {
    const rv = st.review;
    const src = Object.entries(rv.sources).sort((a, b) => b[1] - a[1])
      .map(([k, n]) => ({ label: { model: 'model', mock: 'mock (placeholder)', human: 'made or fixed by people', derived: 'derived' }[k] || k, n, color: SRC_COLORS[k] || 'var(--faint)' }));
    const rows = st.tasks.filter(t => t.segments);
    const eds = Object.entries(rv.editors || {});
    return card('Review progress', 'review',
      h('div.st-sub', 'Where the segments come from'), stacked(src, rv.segments || 1), legend(src),
      h('div.st-sub', 'Reviewed per task'),
      h('div.st-bars', rows.map(t => h('div.st-brow', { title: `${t.reviewed} of ${t.segments} reviewed · ${t.low} low confidence` },
        h('span.st-bl', h('i.dot', { style: { '--c': t.color } }), t.name), bar(t.reviewed / t.segments, 'var(--ok)'), h('span.st-bn', `${pct(t.reviewed, t.segments)}%`)))),
      eds.length ? [h('div.st-sub', 'Edits through shared links'), h('div.st-legend', eds.map(([k, n]) => h('span', ico('user', 12), ` ${k} `, h('b', n))))] : null);
  }

  speakers(sp) {
    const pid = state.project.id;
    const maxPair = Math.max(1e-6, ...sp.pairs.map(p => p.duration));
    const maxSpk = Math.max(1e-6, ...sp.per_speaker.map(s => s.pure + s.overlap));
    const nsp = sp.n_speakers;
    return h('div.section',
      h('div.section-head', h('h3', ico('users', 15), 'Speakers & overlap'),
        h('span.muted', { style: { fontSize: '12px' } }, `${sp.files} file${sp.files === 1 ? '' : 's'} with speaker turns` + (sp.derived ? ' · overlap computed from the speaker turns' : ''))),
      h('div.section-body',
        h('div.stat-row.compact',
          ov('audio', 'Speech', secs(sp.speech), null, h('div.sub', `${sp.turns} speaker turns`)),
          ov('user', 'Pure speech', secs(sp.pure), null, h('div.sub', `${pct1(sp.speech ? sp.pure / sp.speech : 0)} of speech, one speaker`)),
          ov('users', 'Overlap', secs(sp.overlap), null, h('div.sub', `${pct1(sp.overlap_ratio)} of speech, 2+ speakers`)),
          ov('layers', 'Speakers per file', nsp.length ? nsp.map(x => x.speakers).join(' / ') : '–', null,
            h('div.sub', nsp.map(x => `${x.files}× ${x.speakers}`).join(' · ')))),
        h('div.st-grid2',
          card('Overlap per speaker pair', 'users',
            sp.pairs.length ? h('div.st-bars', sp.pairs.map(p => h('div.st-brow', { title: `${p.count} regions · ${secs(p.duration)}` },
              h('span.st-bl.wide', h('i.dot', { style: { '--c': segColor({ output: 'label', task_id: 'overlap', labels: [] }, p.pair) } }), p.pair),
              bar(p.duration / maxPair, '#f43f5e'), h('span.st-bn', secs(p.duration)))))
              : h('div.muted', 'No overlapping speech found.'),
            h('div.st-note', 'Speaker labels (SPEAKER_0, …) are per file unless your model uses names that are the same across files.')),
          card('Pure speech and overlap per speaker label', 'user',
            h('div.st-bars', sp.per_speaker.map(s => h('div.st-brow', { title: `pure ${secs(s.pure)} · overlap ${secs(s.overlap)} · ${s.files} files` },
              h('span.st-bl.wide', h('i.dot', { style: { '--c': segColor({ output: 'label', task_id: 'diarization', labels: [] }, s.speaker) } }), s.speaker),
              h('div.st-track', h('i', { style: { width: `${(s.pure / maxSpk) * 100}%`, background: 'var(--accent)' } }),
                h('i', { style: { width: `${(s.overlap / maxSpk) * 100}%`, background: '#f43f5e' } })),
              h('span.st-bn', secs(s.pure + s.overlap))))),
            h('div.st-legend', h('span', h('i', { style: { background: 'var(--accent)' } }), 'pure'), h('span', h('i', { style: { background: '#f43f5e' } }), 'overlap')))),
        h('div.st-sub', 'Files with the most overlap'),
        h('div.cov-wrap', h('table.list.st-table',
          h('thead', h('tr', h('th', 'File'), h('th', 'Speakers'), h('th', 'Speech'), h('th', 'Pure'), h('th', 'Overlap'), h('th', 'Overlap share'), h('th', ''))),
          h('tbody', sp.per_file.slice(0, 12).map(f => h('tr', { onclick: () => go(links.file(pid, f.media_id)) },
            h('td', h('span.row', { style: { gap: '6px' } }, ico(modalityIcon(f.modality), 13), f.name)),
            h('td', f.speakers), h('td.mono', secs(f.speech)), h('td.mono', secs(f.pure)), h('td.mono', `${secs(f.overlap)} · ${f.overlaps}×`),
            h('td', h('div.row', { style: { gap: '8px' } }, bar(f.ratio / Math.max(1e-6, sp.per_file[0].ratio), '#f43f5e'), h('span.mono', pct1(f.ratio)))),
            h('td', ico('chev-r', 14)))))))));
  }

  tasks(tasks) {
    const modeCtl = h('div.seg-ctl', [['count', 'Count'], ['duration', 'Duration']].map(([k, v]) =>
      h('button', { class: this.mode === k ? 'on' : '', onclick: () => { this.mode = k; ls.set('statsMode', k); this.render(); } }, v)));
    return h('div.section',
      h('div.section-head', h('h3', ico('tasks', 15), 'Labels per task'), h('div.row', h('span.muted', { style: { fontSize: '12px' } }, 'Show labels by'), modeCtl)),
      h('div.section-body', h('div.st-tasks', tasks.map(t => this.taskCard(t)))));
  }

  taskCard(t) {
    const tier = { output: t.output, task_id: t.id, labels: (t.labels || []).filter(l => l.in_classes).map(l => l.value), granularity: t.granularity, color: t.color };
    const head = h('div.st-task-head',
      h('i.dot', { style: { '--c': t.color } }), h('div.grow', h('b', t.title), h('div.muted', [
        OUT_NAMES[t.output], plural(t.segments, 'segment', 'segments'), `${t.files}/${t.applicable} files`,
      ].join(' · '))),
      t.mean_conf != null ? h('div.st-conf', { title: `Confidence of the model output · mean ${t.mean_conf.toFixed(2)}` },
        histo(t.conf_hist, t.color, t.conf_hist.map((_, i) => `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`)), h('small', `conf ${t.mean_conf.toFixed(2)}`)) : null);
    if (!t.segments) return h('div.st-task.empty-task', head, h('div.muted', 'Not run yet.'));
    const body = [];
    const timed = t.duration > 0;
    if (t.labels) {
      const useDur = this.mode === 'duration' && timed;
      const val = (l) => (useDur ? l.duration : l.count);
      const total = t.labels.reduce((a, l) => a + val(l), 0) || 1;
      const max = Math.max(1e-9, ...t.labels.map(val));
      const labs = t.labels.length > 12 ? t.labels.filter(l => l.count) : t.labels;  // long class lists: only classes in use
      const shown = labs.slice(0, 12);
      body.push(h('div.st-bars', shown.map(l => h('div.st-brow' + (l.count ? '' : '.zero'), { title: `${l.count} × · ${secs(l.duration)} · ${l.files} files` },
        h('span.st-bl', h('i.dot', { style: { '--c': segColor(tier, l.value) } }), l.value),
        bar(val(l) / max, segColor(tier, l.value)),
        h('span.st-bn', l.count ? `${useDur ? secs(l.duration) : l.count.toLocaleString()} · ${pct(val(l), total)}%` : '0')))),
      labs.length > 12 ? h('div.st-note', `+ ${labs.length - 12} more labels`) : null,
      labs.length < t.labels.length ? h('div.st-note', `${plural(t.labels.length - labs.length, 'class has', 'classes have')} no examples yet`) : null);
    }
    if (t.text) {
      body.push(h('div.st-kv',
        kv('Words', t.text.words.toLocaleString()), kv('Vocabulary', t.text.vocab.toLocaleString()),
        kv('Words / segment', t.text.avg_words), t.text.words_per_min ? kv('Words / min', t.text.words_per_min) : null,
        t.text.empty ? kv('Empty', t.text.empty) : null));
    }
    if (t.score) {
      body.push(h('div.st-kv', kv('Mean', t.score.mean), kv('Range', `${t.score.min}–${t.score.max}`)),
        histo(t.score.hist, t.color), h('div.st-axis', h('span', t.score.lo), h('span', t.score.hi)));
    }
    if (t.spatial) {
      body.push(h('div.st-kv', kv('Images', t.spatial.images), kv('Per image', t.spatial.per_image), kv('Most in one image', t.spatial.max_per_image),
        t.spatial.people ? kv('Visible keypoints', pct1(t.spatial.visible_kp)) : null));
    }
    const c = t.checks;
    if (c && (c.accepted || c.changed || c.time_changed || c.geom_changed || c.added)) {
      const bits = [];
      if (c.agreement != null) bits.push(h('span', h('b', pct1(c.agreement)), ' of checked values kept'));
      const b = (n, one, many, extra) => h('span', h('b', n.toLocaleString()), ' ', n === 1 ? one : many, extra || '');
      if (c.changed) bits.push(b(c.changed, 'value changed', 'values changed'));
      if (c.time_changed) bits.push(b(c.time_changed, 'boundary moved', 'boundaries moved', c.mean_shift_ms != null ? ` (avg ${c.mean_shift_ms} ms)` : ''));
      if (c.geom_changed) bits.push(b(c.geom_changed, 'shape fixed', 'shapes fixed'));
      if (c.added) bits.push(b(c.added, 'added by hand', 'added by hand'));
      body.push(h('div.st-checks', ico('user', 13), h('div', h('div.st-check-line', bits),
        c.confusions.length ? h('div.st-conf-list', c.confusions.slice(0, 4).map(x => h('span', `${x.model} → ${x.human}`, h('b', ` ×${x.n}`)))) : null)));
    }
    return h('div.st-task', head, body);
  }

  destroy() { this.unsub.forEach(f => f()); }
}

function ov(icon, label, val, small, extra) {
  return h('div.stat', h('div.lab', ico(icon, 13), label), h('div.val', val ?? '–', small ? h('small', small) : null), extra);
}

function kv(k, v) {
  return h('div', h('span', k), h('b', v));
}
