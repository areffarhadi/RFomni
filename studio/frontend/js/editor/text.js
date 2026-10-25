// Text editor: the document on the left with its marked spans (entities, personal data, aspects; character
// offsets), and on the right one card per span task (the list of its marks) and one field per whole-document task
// (label, tags, score, text). Select words in the document to mark them with the task chosen above it.
import { urls } from '../api.js';
import { h, ico, isTyping, ls, segColor, valStr } from '../util.js';
import { state } from '../state.js';
import { modalOpen, toast, popover } from '../ui.js';
import { openRunDialog } from '../views/run.js';
import { confBar } from '../views/review.js';
import { BaseEditor } from './base.js';
import { tierFromTask } from './doc.js';
import { valueEditor, srcBadge, pickValue, tierLabels } from './inspector.js';
import { sortTiers } from './order.js';

const isSpan = (t) => t.granularity === 'span';

export class TextEditor extends BaseEditor {
  constructor(dv, media, params) {
    super(dv, media, params);
    this.el.classList.add('text');
    this.onKey = this.onKey.bind(this);
    this.selId = null;
    this.hidden = new Set(ls.get('txtHidden:' + dv.pv.pid, []));
    this.markKey = ls.get('txtMark:' + dv.pv.pid, null);
    this.text = '';
  }

  // ------------------------------------------------------------------ setup
  async init() {
    await this.loadDoc();
    const r = await fetch(urls.file(this.pid, this.media.id), { credentials: 'same-origin' });
    this.text = r.ok ? (await r.text()).replace(/\r\n?/g, '\n').replace(/^﻿/, '') : '';
    this.page = h('div.txt-doc', { dir: 'auto', lang: '' });
    this.page.addEventListener('mouseup', () => setTimeout(() => this.onSelect(), 0));
    this.page.addEventListener('click', (e) => {
      const m = e.target.closest('[data-ids]');
      if (m && window.getSelection().isCollapsed) this.select(m.dataset.ids.split(' ')[0]);
    });
    this.markSel = h('select.select.sm', { 'aria-label': 'Mark selected words as', onchange: (e) => { this.markKey = e.target.value; ls.set('txtMark:' + this.pid, this.markKey); } });
    this.legend = h('div.txt-legend');
    this.hint = h('span.muted.txt-hint', 'Select words to mark them.');
    this.side = h('div.img-fields.txt-fields');
    const info = this.media.info && Object.keys(this.media.info).length
      ? h('div.txt-info', Object.entries(this.media.info).map(([k, v]) => h('span', h('b', k), String(v)))) : null;
    this.el.replaceChildren(this.buildHeader(), h('div.img-ed.txt-ed',
      h('div.img-left.txt-left',
        h('div.img-tools.txt-tools', h('span.muted', 'Mark as'), this.markSel, this.legend, h('div.grow'), this.hint),
        h('div.txt-scroll', { tabindex: 0, 'aria-label': 'The text' }, info, this.page)),
      this.side));
    document.addEventListener('keydown', this.onKey);
    this.renderAll();
    this.applyParams(this.params);
  }

  applyParams(params) {
    const seg = params?.get?.('seg');
    const s = seg && this.doc.seg(seg);
    if (!s) return;
    if (s.start != null) { this.select(seg, true); return; }
    const card = this.side.querySelector(`[data-tier="${s.tier_id}"]`);
    if (card) {
      card.scrollIntoView({ block: 'center' });
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1800);
    }
  }

  // ------------------------------------------------------------------ tiers
  get spanTiers() { return sortTiers(this.doc.tiers).filter(isSpan); }

  /** Span tasks one can mark with: span tiers in the file, then the project's span tasks not in it yet. */
  markTargets() {
    const out = this.spanTiers.map(t => ({ key: t.task_id || t.id, tier: t }));
    const have = new Set(out.map(x => x.key));
    for (const id of state.project?.tasks || []) {
      const task = state.taskById.get(id);
      if (task && isSpan(task) && task.modalities.includes('text') && !have.has(task.id)) out.push({ key: task.id, task });
    }
    return out;
  }

  ensureTier(key) {
    const t = this.doc.tiers.find(x => (x.task_id || x.id) === key);
    if (t) return t;
    const task = state.taskById.get(key);
    if (!task) return null;
    const tier = tierFromTask(task);
    this.doc.transact('Add tier', (tx) => tx.setTiers([...this.doc.tiers, tier]));
    return this.doc.tier(tier.id);
  }

  renderTools() {
    const targets = this.markTargets();
    if (!targets.some(x => x.key === this.markKey)) this.markKey = targets[0]?.key || null;
    this.markSel.replaceChildren(...(targets.length ? targets.map(x => h('option', { value: x.key, selected: x.key === this.markKey },
      (x.tier || x.task).name)) : [h('option', { value: '' }, 'no span task in this project')]));
    this.markSel.disabled = !targets.length;
    this.hint.textContent = targets.length ? 'Select words to mark them.' : 'Add a span task (Entities, Personal data, Aspects) to mark words.';
    this.legend.replaceChildren(...this.spanTiers.map(t => {
      const off = this.hidden.has(t.id);
      return h('button.txt-key' + (off ? '.off' : ''), {
        type: 'button', title: off ? 'Show these marks' : 'Hide these marks', 'aria-pressed': String(!off), style: { '--c': t.color },
        onclick: () => { off ? this.hidden.delete(t.id) : this.hidden.add(t.id); ls.set('txtHidden:' + this.pid, [...this.hidden]); this.renderAll(); },
      }, h('i'), t.name, h('span.badge', this.doc.list(t.id).length));
    }));
  }

  // ------------------------------------------------------------------ the document
  visibleSpans() {
    return this.spanTiers.filter(t => !this.hidden.has(t.id))
      .flatMap(t => this.doc.list(t.id).filter(s => s.start != null && s.end > s.start).map(s => ({ s, t })));
  }

  renderDoc() {
    const text = this.text;
    const spans = this.visibleSpans();
    const cuts = new Set([0, text.length]);
    for (const { s } of spans) { cuts.add(Math.max(0, Math.round(s.start))); cuts.add(Math.min(text.length, Math.round(s.end))); }
    const pts = [...cuts].sort((a, b) => a - b);
    const ends = new Map();  // offset -> spans ending there (their label is shown after the words)
    for (const x of spans) {
      const e = Math.min(text.length, Math.round(x.s.end));
      if (!ends.has(e)) ends.set(e, []);
      ends.get(e).push(x);
    }
    const out = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      const cover = spans.filter(({ s }) => Math.round(s.start) <= a && Math.round(s.end) >= b);
      const piece = text.slice(a, b);
      if (!cover.length) out.push(h('span', { dataset: { o: a } }, piece));
      else {
        cover.sort((p, q) => (p.s.end - p.s.start) - (q.s.end - q.s.start));  // the smallest mark on top
        const top = cover[0];
        const sel = cover.some(({ s }) => s.id === this.selId);
        out.push(h('mark.txt-mark' + (sel ? '.sel' : '') + (cover.some(({ s }) => !s.reviewed) ? '' : '.rev'), {
          dataset: { o: a, ids: cover.map(({ s }) => s.id).join(' ') },
          style: { '--c': segColor(top.t, top.s.value), '--n': cover.length },
          title: cover.map(({ s, t }) => `${t.name}: ${valStr(s.value) || 'no label'}`).join('\n'),
        }, piece));
      }
      for (const { s, t } of ends.get(b) || []) {
        out.push(h('span.txt-tag', { dataset: { skip: '1', ids: s.id }, style: { '--c': segColor(t, s.value) } }, valStr(s.value) || '?'));
      }
    }
    this.page.replaceChildren(...out);
  }

  /** Character offset of a DOM position inside the document (labels after marks are not text). */
  offsetOf(node, off) {
    let el = node.nodeType === 3 ? node.parentElement : node;
    if (node.nodeType !== 3) {  // a position between children: use the child's start
      const child = node.childNodes[off];
      if (child?.dataset?.o != null) return +child.dataset.o;
      el = child?.previousElementSibling || node;
      off = el.textContent.length;
    }
    while (el && el !== this.page && el.dataset?.o == null) el = el.parentElement;
    if (!el || el === this.page) return null;
    if (el.dataset.skip) return null;
    return +el.dataset.o + Math.min(off, el.textContent.length);
  }

  onSelect() {
    const sel = window.getSelection();
    if (!sel.rangeCount || sel.isCollapsed || !this.page.contains(sel.anchorNode)) return;
    const r = sel.getRangeAt(0);
    let a = this.offsetOf(r.startContainer, r.startOffset), b = this.offsetOf(r.endContainer, r.endOffset);
    if (a == null || b == null) return;
    if (a > b) [a, b] = [b, a];
    while (a < b && /\s/.test(this.text[a])) a++;   // no leading or trailing spaces in a mark
    while (b > a && /\s/.test(this.text[b - 1])) b--;
    if (b <= a) return;
    if (!this.markKey) { toast('Add a span task to the project (e.g. Entities) to mark words.', { kind: 'warn' }); return; }
    const tier = this.ensureTier(this.markKey);
    if (!tier) return;
    const words = this.text.slice(a, b);
    pickValue(r.getBoundingClientRect(), { ...tier, output: tier.output === 'tags' ? 'tags' : 'label' }, { value: '' }, this.doc, (v) => {
      sel.removeAllRanges();
      let id;
      this.doc.transact('Mark words', (tx) => { id = tx.add({ tier_id: tier.id, start: a, end: b, value: v, reviewed: true, meta: { text: words } })?.id; });
      this.lastLabel = v;
      if (id) this.select(id);
    });
  }

  select(id, scroll = false) {
    this.selId = id;
    this.renderDoc();
    this.renderPanel();
    if (scroll) this.page.querySelector('.txt-mark.sel')?.scrollIntoView({ block: 'center' });
    this.side.querySelector(`[data-row="${id}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  update(id, patch, label) {
    const s = this.doc.seg(id);
    if (!s) return;
    const p = { ...patch };
    if ('value' in patch && s.source !== 'human') {
      p.source = 'human';
      p.meta = { ...(s.meta || {}), model_source: s.source, model_value: s.meta?.model_value ?? s.value };
    }
    this.doc.transact(label, (tx) => tx.update(id, p));
  }

  deleteSeg(id) {
    if (this.selId === id) this.selId = null;
    this.doc.transact('Delete mark', (tx) => tx.remove(id));
  }

  // ------------------------------------------------------------------ the panel
  renderPanel() {
    if (!this.side) return;
    const cards = [];
    for (const t of this.spanTiers) cards.push(this.spanCard(t));
    for (const t of sortTiers(this.doc.tiers).filter(t => !isSpan(t))) cards.push(this.field(t));
    const present = new Set(this.doc.tiers.map(t => t.task_id || t.id));
    const suggested = (state.project?.tasks || []).map(id => state.taskById.get(id))
      .filter(t => t && t.modalities.includes('text') && !present.has(t.id));
    if (!this.doc.tiers.length) {
      cards.unshift(h('div.empty', h('div', 'No annotations yet. Run the project tasks, or mark and write them by hand.'),
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

  spanCard(t) {
    const segs = this.doc.list(t.id);
    return h('div.file-field.region-card', { dataset: { tier: t.id } },
      h('div.ff-head', h('b', { style: { '--c': t.color } }, h('i'), t.title || t.name), h('span.badge', segs.length), h('div.grow'),
        h('button.btn.sm', { title: 'Mark words with this task: select them in the text', onclick: () => {
          this.markKey = t.task_id || t.id; ls.set('txtMark:' + this.pid, this.markKey); this.renderTools(); toast(`Select words in the text to mark them as ${t.name}.`);
        } }, ico('plus', 12), 'Mark')),
      segs.length ? h('div.region-list', segs.map(s => this.spanRow(t, s)))
        : h('div.muted', { style: { fontSize: '12px' } }, 'Nothing marked yet. Select words in the text.'));
  }

  spanRow(t, s) {
    const sel = s.id === this.selId;
    const words = s.meta?.text ?? this.text.slice(Math.round(s.start), Math.round(s.end));
    const row = h('div.region-row' + (sel ? '.sel' : ''), { dataset: { row: s.id }, onclick: (e) => { if (!e.target.closest('button')) this.select(s.id, true); } },
      h('i.dot', { style: { '--c': segColor(t, s.value) } }),
      h('span.rl', h('q', { dir: 'auto' }, words), ' ', h('small', valStr(s.value) || 'no label')),
      s.confidence != null ? confBar(s.confidence, this.lowThr) : null,
      srcBadge(s.source, s.meta?.edited_by),
      h('button.icon-btn.sm' + (s.reviewed ? '.active' : ''), { title: s.reviewed ? 'Reviewed (R)' : 'Mark reviewed (R)', 'aria-label': 'Reviewed', 'aria-pressed': String(!!s.reviewed), onclick: () => this.update(s.id, { reviewed: !s.reviewed }, 'Review') }, ico('check', 13)),
      h('button.icon-btn.sm', { title: 'Delete (Del)', 'aria-label': 'Delete mark', onclick: () => this.deleteSeg(s.id) }, ico('trash', 13)));
    if (!sel) return row;
    const detail = [valueEditor({ ...t, output: t.output === 'tags' ? 'tags' : 'label' }, s.value,
      (v) => this.update(s.id, { value: v }, 'Edit label'), { doc: this.doc, allowEmpty: true })];
    if (s.meta?.model_value != null && s.meta.model_value !== s.value) {
      detail.push(h('div.muted', { style: { fontSize: '12px' } }, `Corrected by hand; model said "${valStr(s.meta.model_value)}"`));
    }
    return h('div', row, h('div.region-detail', detail));
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
      title: s.reviewed ? 'Reviewed' : 'Mark reviewed', 'aria-label': 'Reviewed', 'aria-pressed': String(!!s.reviewed),
      onclick: () => this.doc.transact('Review', (tx) => tx.update(s.id, { reviewed: !s.reviewed })),
    }, ico('check', 14));
    return h('div.file-field', { dataset: { tier: t.id } },
      h('div.ff-head', h('b', { style: { '--c': t.color } }, h('i'), t.title || t.name), h('div.grow'),
        s?.confidence != null ? confBar(s.confidence, this.lowThr) : null, s ? srcBadge(s.source, s.meta?.edited_by) : null, rev),
      valueEditor(t, s?.value ?? (t.output === 'tags' ? [] : ''), set, { doc: this.doc }),
      s?.meta?.read_chars ? h('div.muted', { style: { fontSize: '12px' } }, `The model read the first ${s.meta.read_chars.toLocaleString('en')} characters.`) : null,
      s?.meta?.model_value != null ? h('div.muted', { style: { fontSize: '12px' } }, 'Model said: ', valStr(s.meta.model_value)) : null);
  }

  addTier(task) {
    this.doc.transact('Add tier', (tx) => tx.setTiers([...this.doc.tiers, tierFromTask(task)]));
  }

  renderAll() {
    this.renderTools();
    this.renderDoc();
    this.renderPanel();
  }

  afterChange() {
    if (this.selId && !this.doc.seg(this.selId)) this.selId = null;
    this.renderTools();
    this.renderDoc();
    const focused = this.side.contains(document.activeElement) && /INPUT|TEXTAREA/.test(document.activeElement.tagName);
    if (this.localEdit && focused) return;
    this.renderPanel();
  }

  afterReload() {
    this.selId = null;
    this.renderAll();
  }

  // ------------------------------------------------------------------ keyboard
  allMarks() {
    return this.visibleSpans().map(x => x.s).sort((a, b) => a.start - b.start);
  }

  onKey(e) {
    if (modalOpen() || isTyping(e) || !this.el.isConnected || !this.el.offsetParent || document.querySelector('.popover')) return;
    const mod = e.metaKey || e.ctrlKey, k = e.key;
    if (mod) {
      if (k.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? this.redo() : this.undo(); }
      else if (k.toLowerCase() === 'y') { e.preventDefault(); this.redo(); }
      else if (k.toLowerCase() === 's') { e.preventDefault(); this.flush(); }
      return;
    }
    const s = this.selId && this.doc.seg(this.selId);
    switch (k) {
      case 'Escape': this.select(null); break;
      case 'Delete': case 'Backspace': if (s) { e.preventDefault(); this.deleteSeg(s.id); } break;
      case 'r': case 'R': if (s) this.update(s.id, { reviewed: !s.reviewed }, 'Review'); break;
      case 'a': case 'A': {
        if (s) this.update(s.id, { reviewed: true }, 'Accept');
        const list = this.allMarks();
        const i = list.findIndex(x => x.id === this.selId);
        const nxt = list.slice(i + 1).concat(list.slice(0, i + 1)).find(x => !x.reviewed);
        if (nxt) this.select(nxt.id, true); else toast('Every mark in this text is reviewed', { kind: 'ok' });
        break;
      }
      case 'Tab': {
        const list = this.allMarks();
        if (!list.length) break;
        e.preventDefault();
        const i = list.findIndex(x => x.id === this.selId);
        this.select(list[(i + (e.shiftKey ? -1 : 1) + list.length) % list.length].id, true);
        break;
      }
      case 'Enter': if (s) { e.preventDefault(); const el = this.page.querySelector('.txt-mark.sel'); if (el) pickValue(el, this.doc.tier(s.tier_id), s, this.doc, (v) => this.update(s.id, { value: v }, 'Edit label'), { allowEmpty: true }); } break;
      case '[': this.dv.step(-1); break;
      case ']': this.dv.step(1); break;
      default:
        if (/^[1-9]$/.test(k) && s) {
          const l = tierLabels({ ...this.doc.tier(s.tier_id), output: 'label' }, this.doc)[+k - 1];
          if (l != null) this.update(s.id, { value: l }, 'Edit label');
        }
    }
  }

  destroy() {
    super.destroy();
    document.removeEventListener('keydown', this.onKey);
  }
}
