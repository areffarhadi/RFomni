// Right-hand inspector: edit the selected segment, or file-level values when nothing is selected.
import { h, ico, fmtTime, valStr, segColor, debounce, clamp } from '../util.js';
import { state } from '../state.js';
import { popover } from '../ui.js';
import { confBar } from '../views/review.js';
import { sortSpeakers, pairLabel, DIAR, PURE, OVERLAP } from './speakers.js';

export function parseTime(str) {
  const s = String(str).trim();
  if (!s) return null;
  const parts = s.split(':').map(Number);
  if (parts.some(isNaN)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

/** Labels a tier can take: the task's current classes (edited on the Tasks page) plus any value
 *  already used in this tier, or (open vocabulary) the values already used. */
export function tierLabels(tier, doc) {
  const kind = tier.task_id || tier.id;
  if (kind === PURE || kind === OVERLAP) {  // speakers come from the diarization tier of the file
    const dia = doc.tiers.find(t => (t.task_id || t.id) === DIAR);
    const spk = sortSpeakers((dia ? doc.list(dia.id) : []).map(s => valStr(s.value)).filter(Boolean));
    const used = doc.list(tier.id).map(s => valStr(s.value)).filter(Boolean);
    if (kind === PURE) return sortSpeakers([...spk, ...used]);
    const pairs = spk.length <= 6 ? spk.flatMap((a, i) => spk.slice(i + 1).map(b => pairLabel([a, b]))) : [];
    return sortSpeakers([...used, ...pairs]);
  }
  const task = tier.task_id && state.taskById.get(tier.task_id);
  const fixed = task?.labels?.length ? task.labels : (tier.labels || []);
  if (fixed.length && tier.task_id !== 'diarization') {
    const extra = new Set();
    for (const s of doc.list(tier.id)) {
      for (const v of Array.isArray(s.value) ? s.value : [s.value]) if (v && !fixed.includes(v)) extra.add(v);
    }
    return [...fixed, ...extra];
  }
  const used = new Set(fixed);
  for (const s of doc.list(tier.id)) {
    if (Array.isArray(s.value)) s.value.forEach(v => used.add(v));
    else if (s.value) used.add(s.value);
  }
  return [...used].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

export function srcBadge(src, by) {
  const k = (src || '').split('+')[0];
  return h('span.src.' + k, { title: by ? `Edited through the shared link "${by}"` : '' }, src || '?', by ? h('small', ` · ${by}`) : null);
}

/** Editor widget for a value of the given tier. onChange(value, {merge}) */
export function valueEditor(tier, value, onChange, { doc, autofocus = false, allowEmpty = false } = {}) {
  if (tier.output === 'label') {
    const labels = tierLabels(tier, doc);
    const none = allowEmpty ? h('span.chip' + (!value ? '.on' : ''), { style: { '--c': 'var(--faint)' }, onclick: () => onChange(''), title: 'Keep the region without a label' },
      ico('x', 11), 'No label') : null;
    const custom = h('input.input.sm', {
      placeholder: labels.length ? 'Other label…' : 'Type a label and press Enter', style: { width: '160px' },
      onkeydown: (e) => { if (e.key === 'Enter' && custom.value.trim()) { onChange(custom.value.trim()); custom.value = ''; } },
    });
    const next = tier.task_id === 'diarization' ? `SPEAKER_${labels.filter(l => /^SPEAKER_\d+$/.test(l)).length}` : null;
    return h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } },
      h('div.label-chips', none, labels.map((l, i) => h('span.chip' + (l === value ? '.on' : ''), {
        style: { '--c': segColor(tier, l) }, onclick: () => onChange(l), title: i < 9 ? `Key ${i + 1}` : '',
      }, h('i.dot'), l, i < 9 ? h('kbd', i + 1) : null))),
      h('div.row', custom, next && !labels.includes(next) ? h('button.btn.sm', { onclick: () => onChange(next) }, ico('plus', 12), next) : null));
  }
  if (tier.output === 'tags') {
    const cur = Array.isArray(value) ? value : (value ? String(value).split(';').map(s => s.trim()).filter(Boolean) : []);
    const all = [...new Set([...(tier.labels || []), ...cur])];
    const add = h('input.input.sm', {
      placeholder: 'Add tag…', style: { width: '160px' },
      onkeydown: (e) => { if (e.key === 'Enter' && add.value.trim()) { onChange([...cur, add.value.trim()]); add.value = ''; } },
    });
    return h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } },
      h('div.label-chips', all.map(l => h('span.chip' + (cur.includes(l) ? '.on' : ''), {
        style: { '--c': tier.color },
        onclick: () => onChange(cur.includes(l) ? cur.filter(x => x !== l) : [...cur, l]),
      }, cur.includes(l) ? ico('check', 11) : h('i.dot'), l))),
      add);
  }
  if (tier.output === 'score') {
    const task = state.taskById.get(tier.task_id) || {};
    const lo = task.score_min ?? 1, hi = task.score_max ?? 5;
    const num = h('input.input', { type: 'number', step: 0.5, min: lo, max: hi, value: value, style: { width: '90px' } });
    const rng = h('input', { type: 'range', min: lo, max: hi, step: 0.5, value: value || lo, style: { flex: 1, accentColor: tier.color } });
    num.addEventListener('change', () => { rng.value = num.value; onChange(num.value); });
    rng.addEventListener('input', () => { num.value = rng.value; });
    rng.addEventListener('change', () => onChange(rng.value));
    return h('div.row', num, rng, h('span.muted', `${lo}–${hi}`));
  }
  const ta = h('textarea.textarea', { rows: 3, value: valStr(value), spellcheck: true, autofocus });
  const commit = debounce(() => onChange(ta.value, { merge: true }), 350);
  ta.addEventListener('input', commit);
  ta.addEventListener('blur', () => { commit.cancel(); if (ta.value !== valStr(value)) onChange(ta.value, { merge: true }); });
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ta.blur(); } if (e.key === 'Escape') ta.blur(); });
  const auto = () => { ta.style.height = 'auto'; ta.style.height = Math.min(220, ta.scrollHeight + 2) + 'px'; };
  ta.addEventListener('input', auto);
  requestAnimationFrame(auto);
  return ta;
}

/** Popover to pick a label / tags / score for a segment. */
export function pickValue(anchor, tier, seg, doc, onPick, { allowEmpty = false } = {}) {
  if (tier.output === 'tags' || tier.output === 'score') {
    const pop = popover(anchor, h('div', { style: { padding: '8px', width: '300px' } },
      valueEditor(tier, seg.value, (v) => { onPick(v); if (tier.output === 'score') pop.close(); }, { doc })));
    return;
  }
  const labels = tierLabels(tier, doc);
  const filter = h('input.input.sm', { placeholder: labels.length ? 'Filter or type a new label' : 'Type a label', autofocus: true });
  const opts = h('div.opts');
  let hl = 0, shown = [];
  const render = () => {
    const q = filter.value.trim().toLowerCase();
    shown = labels.filter(l => !q || l.toLowerCase().includes(q));
    if (q && !labels.some(l => l.toLowerCase() === q)) shown.push(filter.value.trim());
    if (allowEmpty && !q) shown.unshift('');
    hl = clamp(hl, 0, Math.max(0, shown.length - 1));
    opts.replaceChildren(...shown.map((l, i) => {
      if (l === '') return h('button.opt' + (i === hl ? '.hl' : '') + (!seg.value ? '.on' : ''), { onclick: () => { pop.close(); onPick(''); } },
        h('i', { style: { '--c': 'var(--faint)' } }), h('span.muted', 'No label'), h('kbd', 'Esc'));
      const n = labels.indexOf(l);
      return h('button.opt' + (i === hl ? '.hl' : '') + (l === seg.value ? '.on' : ''), {
        onclick: () => { pop.close(); onPick(l); },
      }, h('i', { style: { '--c': segColor(tier, l) } }), labels.includes(l) ? l : `Use "${l}"`, n >= 0 && n < 9 && !q ? h('kbd', n + 1) : null);
    }));
  };
  filter.addEventListener('input', () => { hl = 0; render(); });
  filter.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); hl++; render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); hl--; render(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (shown[hl] != null) { pop.close(); onPick(shown[hl]); } }
    else if (/^[1-9]$/.test(e.key) && !filter.value) { e.preventDefault(); const l = labels[+e.key - 1]; if (l) { pop.close(); onPick(l); } }
  });
  const pop = popover(anchor, h('div.label-pop', filter, opts));
  render();
  setTimeout(() => filter.focus(), 20);
}

export class Inspector {
  constructor(ed) {
    this.ed = ed;
    this.el = h('div.insp');
    this.curId = undefined;
  }

  get doc() { return this.ed.doc; }

  render() {
    const s = this.ed.selId && this.doc.seg(this.ed.selId);
    this.curId = s ? s.id : null;
    if (!s) return this.renderFile();
    const doc = this.doc, tier = doc.tier(s.tier_id), ed = this.ed;
    const { i, n, prev, next } = doc.neighbors(s);
    const task = state.taskById.get(tier.task_id);
    const fileLevel = tier.granularity === 'file';
    this.startIn = h('input.input', { value: fmtTime(s.start) });
    this.endIn = h('input.input', { value: fmtTime(s.end) });
    this.durEl = h('div.dur', `${(s.end - s.start).toFixed(3)} s`);
    const bindTime = (input, key) => {
      const apply = () => {
        const t = parseTime(input.value);
        const cur = doc.seg(s.id);
        if (t == null || !cur) { input.value = fmtTime(cur?.[key]); return; }
        const patch = key === 'start' ? { start: clamp(t, 0, cur.end - 0.01) } : { end: clamp(t, cur.start + 0.01, ed.duration) };
        if (patch[key] !== cur[key]) ed.updateSeg(s.id, patch, 'Edit time');
      };
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); apply(); input.blur(); }
        if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
          e.preventDefault();
          const cur = doc.seg(s.id);
          const d = (e.key === 'ArrowUp' ? 1 : -1) * (e.shiftKey ? 0.1 : 0.01);
          const v = key === 'start' ? clamp(cur.start + d, 0, cur.end - 0.01) : clamp(cur.end + d, cur.start + 0.01, ed.duration);
          ed.updateSeg(s.id, { [key]: v }, 'Nudge ' + key, true);
        }
      });
      input.addEventListener('blur', apply);
    };
    bindTime(this.startIn, 'start');
    bindTime(this.endIn, 'end');
    this.metaEl = h('div.meta');
    this.updateMeta(s);
    const value = valueEditor(tier, s.value, (v, o) => ed.updateSeg(s.id, { value: v }, 'Edit value', o?.merge), { doc });
    this.el.replaceChildren(
      h('div.insp-head',
        h('div.tname', { style: { '--c': tier.color } }, h('i'), tier.title || tier.name),
        h('span.pos', `${i + 1} / ${n}`),
        h('div.grow'),
        h('button.icon-btn.sm', { title: 'Previous (Shift+Tab)', 'aria-label': 'Previous segment', disabled: !prev, onclick: () => ed.select(prev.id, { seek: true }) }, ico('chev-l', 15)),
        h('button.icon-btn.sm', { title: 'Next (Tab)', 'aria-label': 'Next segment', disabled: !next, onclick: () => ed.select(next.id, { seek: true }) }, ico('chev-r', 15)),
        h('button.icon-btn.sm', { title: 'Deselect (Esc)', 'aria-label': 'Deselect', onclick: () => ed.select(null) }, ico('x', 15))),
      h('div.insp-body',
        value,
        fileLevel ? null : h('div.times',
          h('label', 'Start', this.startIn), h('label', 'End', this.endIn), this.durEl),
        this.metaEl,
        h('div.actions',
          h('button.btn.sm', { onclick: () => ed.playSeg(), title: 'Shift+Space' }, ico('play', 12), 'Play'),
          fileLevel ? null : h('button.btn.sm', { onclick: () => ed.split(), title: 'S' }, ico('scissors', 13), 'Split at playhead'),
          fileLevel || !next ? null : h('button.btn.sm', { onclick: () => ed.mergeNext(), title: 'M' }, ico('merge', 13), 'Merge next'),
          h('div.grow'),
          h('button.btn.sm.ghost', { onclick: () => ed.deleteSeg(s.id), title: 'Delete' }, ico('trash', 13), 'Delete')),
        s.meta?.model_value != null ? h('div.muted', { style: { fontSize: '12px' } }, 'Model said: ', h('span', { style: { color: 'var(--text-2)' } }, valStr(s.meta.model_value) || '(empty)')) : null,
        task ? h('details', h('summary.muted', { style: { fontSize: '12px', cursor: 'pointer' } }, 'Task prompt'),
          h('div.prompt-box', { style: { marginTop: '6px', maxHeight: '140px' } }, task.prompt)) : null));
  }

  updateMeta(s) {
    const ed = this.ed;
    const sw = h('label.switch', h('input', { type: 'checkbox', checked: !!s.reviewed, onchange: () => ed.toggleReviewed(s.id) }), h('span'));
    this.metaEl.replaceChildren(
      s.confidence != null ? confBar(s.confidence, ed.lowThr) : h('span', 'no confidence'),
      srcBadge(s.source, s.meta?.edited_by),
      h('div.grow'),
      h('label.row', { style: { gap: '7px', cursor: 'pointer' }, title: 'R' }, sw, s.reviewed ? 'Reviewed' : 'Not reviewed'));
  }

  /** Refresh after a document change without stealing focus from an input being typed in. */
  refresh() {
    const s = this.ed.selId && this.doc.seg(this.ed.selId);
    const focused = this.el.contains(document.activeElement) && /INPUT|TEXTAREA/.test(document.activeElement.tagName);
    if (s && this.curId === s.id && focused) {
      this.updateMeta(s);
      if (document.activeElement !== this.startIn) this.startIn.value = fmtTime(s.start);
      if (document.activeElement !== this.endIn) this.endIn.value = fmtTime(s.end);
      this.durEl.textContent = `${(s.end - s.start).toFixed(3)} s`;
      return;
    }
    if (!s && this.curId === null && focused) return;
    this.render();
  }

  previewTimes(a, b) {
    if (!this.startIn) return;
    this.startIn.value = fmtTime(a);
    this.endIn.value = fmtTime(b);
    this.durEl.textContent = `${(b - a).toFixed(3)} s`;
  }

  renderFile() {
    const doc = this.doc, ed = this.ed;
    const fileTiers = doc.tiers.filter(t => t.granularity === 'file');
    const fields = fileTiers.map(t => {
      const s = doc.list(t.id)[0];
      return h('div.file-field',
        h('div.ff-head', h('b', { style: { '--c': t.color } }, h('i'), t.title || t.name), h('div.grow'),
          s?.confidence != null ? confBar(s.confidence, ed.lowThr) : null, s ? srcBadge(s.source, s.meta?.edited_by) : null,
          s ? h('button.icon-btn.sm' + (s.reviewed ? '.active' : ''), { title: 'Reviewed', onclick: () => ed.toggleReviewed(s.id) }, ico('check', 14)) : null),
        valueEditor(t, s?.value ?? (t.output === 'tags' ? [] : ''), (v, o) => {
          if (s) ed.updateSeg(s.id, { value: v }, 'Edit value', o?.merge);
          else ed.createSegment(t.id, 0, ed.duration, v, { edit: false });
        }, { doc }));
    });
    const rows = doc.tiers.filter(t => t.granularity !== 'file').map(t => {
      const list = doc.list(t.id);
      const rev = list.filter(s => s.reviewed).length;
      const low = list.filter(s => !s.reviewed && s.confidence != null && s.confidence < ed.lowThr).length;
      return h('tr', { style: { cursor: 'pointer' }, onclick: () => { ed.setActiveTier(t.id); if (list[0]) ed.select((list.find(s => !s.reviewed) || list[0]).id, { seek: true }); } },
        h('td', h('span.ov-tier', { style: { '--c': t.color } }, h('i'), t.name)), h('td', list.length), h('td', rev),
        h('td', low ? h('span', { style: { color: 'var(--danger)' } }, low) : '0'));
    });
    this.el.replaceChildren(
      h('div.insp-head', h('div.tname', 'File overview'), h('div.grow'),
        h('span.pos', `${doc.segs.size} segments`)),
      h('div.insp-empty',
        fields.length ? fields : null,
        rows.length ? h('table.list', h('thead', h('tr', h('th', 'Tier'), h('th', 'Segments'), h('th', 'Reviewed'), h('th', 'Low conf.'))), h('tbody', rows)) : null,
        h('div.tips',
          h('kbd', 'Click'), h('span', 'a segment to edit it here'),
          h('kbd', 'Drag'), h('span', 'on an empty part of a tier to create a segment'),
          h('kbd', 'Enter'), h('span', 'edit the selected segment in place'),
          h('kbd', 'N'), h('span', 'jump to the next low-confidence segment'),
          h('kbd', '?'), h('span', 'all shortcuts'))));
  }
}

