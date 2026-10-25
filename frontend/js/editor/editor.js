// Audio / video editor: media + inspector on top, multi-tier timeline below.
import { urls } from '../api.js';
import { canDownload } from '../state.js';
import { h, ico, clamp, ls, isTyping, valStr } from '../util.js';
import { state } from '../state.js';
import { menu, popover, modalOpen, toast, confirmDialog, promptDialog } from '../ui.js';
import { openRunDialog } from '../views/run.js';
import { BaseEditor } from './base.js';
import { tierFromTask } from './doc.js';
import { Player } from './player.js';
import { Timeline } from './timeline.js';
import { Inspector, pickValue, tierLabels } from './inspector.js';
import { NowPanel } from './now.js';
import { sortTiers, tierKey, moveTierKey } from './order.js';
import { on, isShared, canEdit } from '../state.js';
import { speakerRegions, tierKind, DIAR, PURE, OVERLAP } from './speakers.js';

export class Editor extends BaseEditor {
  constructor(dv, media, params) {
    super(dv, media, params);
    this.selId = null;
    this.activeTier = null;
    this.hidden = new Set(ls.get('hidden:' + this.pid, []));
    this.tierSig = '';
    this.onKey = this.onKey.bind(this);
  }

  async init() {
    await this.loadDoc();
    const m = this.media;
    const isVideo = m.modality === 'video';
    this.mediaEl = isVideo
      ? h('video', { src: urls.file(this.pid, m.id), preload: 'auto', playsInline: true, controls: true,
        // a shared link without downloads: no download button or picture-in-picture in the player
        ...(canDownload() ? {} : { controlsList: 'nodownload', disablePictureInPicture: true }) })
      : h('audio', { src: urls.file(this.pid, m.id), preload: 'auto' });
    this.mediaEl.addEventListener('error', () => this.onMediaError(), { once: true });
    this.player = new Player(this.mediaEl, m.duration);
    this.timeline = new Timeline(this);
    this.inspector = new Inspector(this);
    this.now = new NowPanel(this, { compact: isVideo });

    const mediaPane = h('div.ed-media', isVideo ? [this.mediaEl, this.now.el] : [this.mediaEl, this.now.el]);
    this.mediaPane = mediaPane;
    const split = h('div.ed-split', { onpointerdown: (e) => this.startSplit(e, split) });
    this.el.replaceChildren(this.buildHeader(), h('div.ed-top', mediaPane, this.inspector.el), split, this.timeline.el);
    this.el.style.setProperty('--top-h', ls.get(isVideo ? 'topHv' : 'topH', isVideo ? 300 : 200) + 'px');

    this.player.addEventListener('time', (e) => { this.timeline.onTime(e.detail); this.now.update(e.detail); });
    this.player.addEventListener('state', () => this.timeline.setPlaying(this.player.playing));

    this.refreshTiers();
    this.inspector.render();
    this.now.refresh();
    this.timeline.mount();
    document.addEventListener('keydown', this.onKey);
    this.orderSig = JSON.stringify(state.project.tier_order || state.project.tasks);
    this.unsub.push(on('project', () => {
      const sig = JSON.stringify(state.project.tier_order || state.project.tasks);
      if (sig !== this.orderSig) { this.orderSig = sig; this.refreshTiers(); }
    }));
    this.applyParams(this.params);
    this.turnSig = this.speakerSig();
    if (!this.doc.tiers.length) this.suggestRun();
  }

  onMediaError() {
    const m = this.media;
    if (m.modality === 'audio' || m.has_audio) {
      const t = this.player.t;
      this.mediaEl.src = urls.audioProxy(this.pid, m.id);
      this.mediaEl.currentTime = t;
      toast(m.modality === 'video' ? 'This video cannot be played in the browser. Playing its audio track instead.'
        : 'The browser cannot play this format directly; using a converted 16 kHz copy.', { kind: 'warn', timeout: 6000 });
    }
  }

  suggestRun() {
    if (!state.project.tasks.length || isShared()) return;
    this.timeline.tiersInner.querySelector('.empty')?.append(
      h('div', { style: { marginTop: '10px' } }, h('button.btn.primary', { onclick: () => openRunDialog({ mediaIds: [this.media.id] }) }, ico('sparkles', 14), 'Run tasks on this file')));
  }

  applyParams(params) {
    if (!params) return;
    const seg = params.get('seg');
    if (seg && this.doc.seg(seg)) {
      const s = this.doc.seg(seg);
      this.timeline.zoomTo(s.start - 1, s.end + 1, 0.05);
      this.select(seg, { seek: true });
    }
    const t = params.get('t');
    if (t) this.player.seek(+t);
  }

  focus() { if (document.activeElement && this.el.contains(document.activeElement)) document.activeElement.blur(); }
  pause() { this.player?.pause(); }

  // ------------------------------------------------------------ tiers
  orderTiers() {
    const tiers = sortTiers(this.doc.tiers);
    const parentOf = (t) => t.parent && tiers.find(x => x !== t && (x.id === t.parent || x.task_id === t.parent));
    const out = [], placed = new Set();
    const visit = (t, depth) => {
      if (placed.has(t.id)) return;
      placed.add(t.id);
      out.push({ tier: t, depth });
      for (const c of tiers) if (parentOf(c) === t) visit(c, depth + 1);
    };
    for (const t of tiers) if (!parentOf(t)) visit(t, 0);
    for (const t of tiers) visit(t, 0);
    return out;
  }

  visibleTiers() {
    return this.orderTiers().filter(x => !this.hidden.has(x.tier.id));
  }

  refreshTiers() {
    this.tierSig = this.doc.tiers.map(t => t.id + ':' + t.name + ':' + t.color).join('|');
    const list = this.visibleTiers();
    if (!this.activeTier || !list.some(x => x.tier.id === this.activeTier)) this.activeTier = list[0]?.tier.id || null;
    this.timeline.setTiers(list, this.activeTier);
    this.now.refresh();
  }

  setActiveTier(id) {
    this.activeTier = id;
    this.timeline.setActive(id);
  }

  setHidden(id, hide) {
    hide ? this.hidden.add(id) : this.hidden.delete(id);
    ls.set('hidden:' + this.pid, [...this.hidden]);
    this.refreshTiers();
  }

  tiersPopover(anchor) {
    const ordered = this.orderTiers();
    const present = new Set(this.doc.tiers.map(t => t.task_id || t.id));
    const addable = (state.project.tasks || []).map(id => state.taskById.get(id))
      .filter(t => t && !present.has(t.id) && t.modalities.includes(this.media.modality));
    const list = h('div.tiers-pop',
      h('div.menu-head', 'Visible tiers'),
      ordered.length ? ordered.map(({ tier, depth }) => h('label', { style: { paddingLeft: 10 + depth * 16 + 'px' } },
        h('input', { type: 'checkbox', checked: !this.hidden.has(tier.id), onchange: (e) => this.setHidden(tier.id, !e.target.checked), style: { accentColor: 'var(--accent)' } }),
        h('i', { style: { '--c': tier.color } }), tier.name, h('small', this.doc.list(tier.id).length))) : h('div.muted', { style: { padding: '6px 8px' } }, 'No tiers yet'),
      h('div.menu-sep'),
      h('div.menu-head', 'Add an empty tier'),
      addable.map(t => h('button.menu-item', { onclick: () => { pop.close(); this.addTier(tierFromTask(t)); } }, ico('plus', 14), h('span.menu-label', t.title))),
      h('button.menu-item', { onclick: async () => {
        pop.close();
        const name = await promptDialog('Tier name', { title: 'New blank tier', placeholder: 'e.g. Notes' });
        if (name) this.addTier({ id: 't_' + Math.random().toString(36).slice(2, 8), task_id: null, name, title: name, color: '#94a3b8', output: 'text', granularity: 'segment', labels: [], parent: null, source: 'human' });
      } }, ico('plus', 14), h('span.menu-label', 'Blank text tier…')));
    const pop = popover(anchor, list, { placement: 'bottom-end' });
  }

  addTier(tier) {
    if (this.doc.tier(tier.id)) { toast('That tier already exists'); return; }
    this.doc.transact('Add tier', (tx) => tx.setTiers([...this.doc.tiers, tier]));
    this.setHidden(tier.id, false);
    this.setActiveTier(tier.id);
  }

  /** Reorder channels for the whole project (all files share one tier order). */
  async reorderTier(srcId, dstId, after) {
    const src = this.doc.tier(srcId), dst = this.doc.tier(dstId);
    if (!src || !dst || src === dst) return;
    if (isShared()) { toast('Only the owner of the project can change the tier order', { timeout: 2400 }); return; }
    try {
      await moveTierKey(tierKey(src), tierKey(dst), after, this.doc.tiers.map(tierKey));
      this.orderSig = JSON.stringify(state.project.tier_order || state.project.tasks);
      this.refreshTiers();
      toast('Tier order saved for every file in this project', { kind: 'ok', timeout: 2200 });
    } catch (e) { toast(e.message, { kind: 'error' }); }
  }

  moveTierBy(tid, dir) {
    const vis = this.visibleTiers();
    const i = vis.findIndex(x => x.tier.id === tid);
    if (i < 0) return;
    const me = vis[i];
    // move among siblings (same depth), so children stay under their parent
    let j = i + dir;
    while (j >= 0 && j < vis.length && vis[j].depth > me.depth) j += dir;
    if (j < 0 || j >= vis.length || vis[j].depth !== me.depth) return;
    this.reorderTier(tid, vis[j].tier.id, dir > 0);
  }

  tierMenu(tid, anchor) {
    const tier = this.doc.tier(tid);
    const segs = this.doc.list(tid);
    const kind = tierKind(tier);
    const speakerTier = [DIAR, PURE, OVERLAP].includes(kind);
    menu(anchor, [
      { header: tier.title || tier.name },
      !isShared() && { label: 'Move up', icon: 'chev-u', onClick: () => this.moveTierBy(tid, -1) },
      !isShared() && { label: 'Move down', icon: 'chev-d', onClick: () => this.moveTierBy(tid, 1) },
      !isShared() && 'sep',
      speakerTier && canEdit() ? { label: kind === DIAR ? 'Make pure speech & overlap tiers from these turns' : 'Rebuild from the speaker turns',
        icon: 'users', disabled: !this.doc.tiers.some(t => tierKind(t) === DIAR), onClick: () => this.rebuildSpeakerTiers() } : null,
      speakerTier && canEdit() ? 'sep' : null,
      { label: 'Mark all reviewed', icon: 'check', disabled: !segs.length, onClick: () => this.doc.transact('Review tier', (tx) => segs.forEach(s => tx.update(s.id, { reviewed: true }))) },
      { label: 'Mark all unreviewed', icon: 'review', disabled: !segs.length, onClick: () => this.doc.transact('Unreview tier', (tx) => segs.forEach(s => tx.update(s.id, { reviewed: false }))) },
      !isShared() && tier.task_id && state.taskById.get(tier.task_id) ? { label: 'Run this task again…', icon: 'sparkles', onClick: () => this.flush().then(() => openRunDialog({ mediaIds: [this.media.id], taskIds: [tier.task_id] })) } : null,
      { label: 'Hide tier', icon: 'eye-off', onClick: () => this.setHidden(tid, true) },
      'sep',
      { label: 'Clear all segments', icon: 'x', danger: true, disabled: !segs.length, onClick: async () => {
        if (await confirmDialog(`Remove all ${segs.length} segments of "${tier.name}"? You can undo this.`, { ok: 'Clear' }))
          this.doc.transact('Clear tier', (tx) => segs.forEach(s => tx.remove(s.id)));
      } },
      { label: 'Delete tier', icon: 'trash', danger: true, onClick: async () => {
        if (await confirmDialog(`Delete the tier "${tier.name}" and its ${segs.length} segments? You can undo this.`, { ok: 'Delete tier' }))
          this.doc.transact('Delete tier', (tx) => { segs.forEach(s => tx.remove(s.id)); tx.setTiers(this.doc.tiers.filter(t => t.id !== tid)); });
      } },
    ]);
  }

  // ------------------------------------------------------------ speakers
  speakerSig() {
    const d = this.doc.tiers.find(t => tierKind(t) === DIAR);
    return d ? this.doc.list(d.id).map(s => `${s.start}|${s.end}|${s.value}`).join(',') : '';
  }

  /** Pure speech (one speaker) and overlap (speaker pairs) tiers, recomputed from the current speaker turns. One undo step. */
  rebuildSpeakerTiers({ quiet = false } = {}) {
    const dia = this.doc.tiers.find(t => tierKind(t) === DIAR);
    if (!dia) { toast('This file has no speaker diarization tier'); return; }
    const { pure, overlap } = speakerRegions(this.doc.list(dia.id));
    let tiers = this.doc.tiers;
    this.doc.transact('Rebuild speaker regions', (tx) => {
      for (const [kind, regs] of [[PURE, pure], [OVERLAP, overlap]]) {
        let tier = tiers.find(t => tierKind(t) === kind);
        if (!tier) {
          const task = state.taskById.get(kind);
          if (!task) continue;
          tier = { ...tierFromTask(task, 'derived'), parent: dia.task_id || dia.id };
          tiers = [...tiers, tier];
        }
        for (const s of this.doc.list(tier.id)) tx.remove(s.id);
        for (const r of regs) tx.add({ tier_id: tier.id, start: r.start, end: r.end, value: r.value, confidence: r.confidence, source: 'derived', reviewed: false });
      }
      if (tiers !== this.doc.tiers) tx.setTiers(tiers);
    });
    this.turnSig = this.speakerSig();
    for (const t of this.doc.tiers) if ([PURE, OVERLAP].includes(tierKind(t))) this.hidden.delete(t.id);
    this.refreshTiers();
    if (!quiet) toast(`${pure.length} pure-speech regions and ${overlap.length} overlaps from ${this.doc.list(dia.id).length} speaker turns`, { kind: 'ok', timeout: 2600 });
  }

  /** After the speaker turns change, offer to bring the pure speech / overlap tiers up to date. */
  checkSpeakerTurns() {
    const sig = this.speakerSig();
    if (sig === this.turnSig) return;
    this.turnSig = sig;
    if (!this.doc.tiers.some(t => [PURE, OVERLAP].includes(tierKind(t))) || this.doc.readOnly) return;
    if (Date.now() - (this.turnToast || 0) < 15000) return;
    this.turnToast = Date.now();
    toast('Speaker turns changed. The pure speech and overlap tiers may be out of date.', {
      timeout: 6000, action: { label: 'Rebuild', onClick: () => this.rebuildSpeakerTiers() },
    });
  }

  // ------------------------------------------------------------ selection & editing
  select(id, { seek = false, scroll = true } = {}) {
    const s = id ? this.doc.seg(id) : null;
    this.selId = s ? s.id : null;
    if (s) {
      if (this.hidden.has(s.tier_id)) this.setHidden(s.tier_id, false);
      this.setActiveTier(s.tier_id);
      this.timeline.scrollTierIntoView(s.tier_id);
      if (scroll) this.timeline.ensureVisible(s.start, s.end);
      if (seek) this.player.seek(s.start);
    }
    this.inspector.render();
    this.timeline.schedule();
  }

  previewTimes(a, b) { this.inspector.previewTimes(a, b); }

  /** A human edit marks the segment reviewed and keeps the original model output (value + times) in meta. */
  humanPatch(cur, patch) {
    const p = { ...patch };
    const content = 'value' in p || 'start' in p || 'end' in p;
    if (content && !('reviewed' in p)) p.reviewed = true;
    if (content && cur.source !== 'human') {
      p.meta = { ...(cur.meta || {}), model_source: cur.source, model_value: cur.meta?.model_value ?? cur.value,
        model_start: cur.meta?.model_start ?? cur.start, model_end: cur.meta?.model_end ?? cur.end };
      p.source = 'human';
    }
    return p;
  }

  updateSeg(id, patch, label = 'Edit', merge = false) {
    const cur = this.doc.seg(id);
    if (!cur) return;
    this.doc.transact(label, (tx) => tx.update(id, this.humanPatch(cur, patch)), { merge });
  }

  /** Apply new times from a boundary drag as one undo step. `direct` = segments whose edge the user moved;
   *  the others (e.g. phones inside a word) were only rescaled and keep their review state. */
  commitTimes(map, direct, label = 'Move boundary') {
    const r3 = (x) => Math.round(x * 1000) / 1000;
    this.doc.transact(label, (tx) => {
      for (const [id, t] of map) {
        const cur = this.doc.seg(id);
        if (!cur) continue;
        const patch = { start: r3(t.start), end: r3(t.end) };
        tx.update(id, direct.has(id) ? this.humanPatch(cur, patch) : patch);
      }
    });
  }

  createSegment(tid, a, b, value, { edit = true } = {}) {
    const tier = this.doc.tier(tid);
    if (!tier) return;
    const v = value ?? (tier.output === 'tags' ? [] : '');
    let seg;
    this.doc.transact('Create segment', (tx) => { seg = tx.add({ tier_id: tid, start: a, end: b, value: v, reviewed: value != null }); });
    this.select(seg.id, { scroll: false });
    if (edit) requestAnimationFrame(() => this.editInline(seg.id));
  }

  editInline(id) {
    const s = this.doc.seg(id);
    if (!s) return;
    const tier = this.doc.tier(s.tier_id);
    if (this.selId !== id) this.select(id);
    if (tier.output === 'text') {
      const ok = this.timeline.inlineText(s, (v) => this.updateSeg(id, { value: v }, 'Edit value'), (dir) => {
        const cur = this.doc.seg(id);
        const nb = cur && this.doc.neighbors(cur);
        const target = nb && (dir > 0 ? nb.next : nb.prev);
        if (target) { this.select(target.id, { seek: true }); this.editInline(target.id); }
      });
      if (!ok) this.inspector.el.querySelector('textarea')?.focus();
      return;
    }
    this.timeline.ensureVisible(s.start, s.end);
    this.timeline.render();
    const rect = this.timeline.segRect(id) || this.inspector.el.getBoundingClientRect();
    pickValue(rect, tier, s, this.doc, (v) => this.updateSeg(id, { value: v }, 'Edit value'));
  }

  deleteSeg(id) {
    const s = this.doc.seg(id);
    if (!s) return;
    const { prev, next } = this.doc.neighbors(s);
    this.doc.transact('Delete segment', (tx) => tx.remove(id));
    this.select((next || prev)?.id || null, { scroll: false });
  }

  selectedOrAtPlayhead() {
    if (this.selId) return this.doc.seg(this.selId);
    return this.activeTier ? this.doc.at(this.activeTier, this.player.t) : null;
  }

  split() {
    const s = this.selectedOrAtPlayhead();
    const t = this.player.t;
    if (!s || t <= s.start + 0.01 || t >= s.end - 0.01) { toast('Put the playhead inside the segment to split it', { timeout: 2200 }); return; }
    const tier = this.doc.tier(s.tier_id);
    let v1 = s.value, v2 = s.value;
    if (tier.output === 'text') {
      const text = valStr(s.value);
      const words = this.wordsIn(s);
      let idx;
      if (words.length) {
        const n = words.filter(w => (w.start + w.end) / 2 < t).length;
        const toks = text.split(/\s+/);
        idx = toks.slice(0, n).join(' ').length;
      } else {
        const target = Math.round(text.length * (t - s.start) / (s.end - s.start));
        const sp = [...text.matchAll(/\s/g)].map(m => m.index);
        idx = sp.length ? sp.reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a) : text.length;
      }
      v1 = text.slice(0, idx).trim();
      v2 = text.slice(idx).trim();
    }
    let added;
    this.doc.transact('Split segment', (tx) => {
      tx.update(s.id, { end: t, value: v1, source: 'human' });
      added = tx.add({ tier_id: s.tier_id, start: t, end: s.end, value: v2, confidence: s.confidence, source: 'human' });
    });
    this.select(added.id, { scroll: false });
  }

  wordsIn(s) {
    const wt = this.doc.tiers.find(t => t.granularity === 'word' && (t.parent === s.tier_id || t.parent === this.doc.tier(s.tier_id)?.task_id));
    return wt ? this.doc.visible(wt.id, s.start, s.end).filter(w => w.start >= s.start - 0.02 && w.end <= s.end + 0.02) : [];
  }

  mergeNext() {
    const s = this.selectedOrAtPlayhead();
    if (!s) return;
    const { next } = this.doc.neighbors(s);
    if (!next) { toast('No next segment to merge with', { timeout: 1800 }); return; }
    const tier = this.doc.tier(s.tier_id);
    let v = s.value;
    if (tier.output === 'text') v = [valStr(s.value), valStr(next.value)].filter(Boolean).join(' ');
    else if (tier.output === 'tags') v = [...new Set([...(s.value || []), ...(next.value || [])])];
    const conf = s.confidence == null ? next.confidence : next.confidence == null ? s.confidence : Math.min(s.confidence, next.confidence);
    this.doc.transact('Merge segments', (tx) => {
      tx.update(s.id, { end: Math.max(s.end, next.end), value: v, confidence: conf, source: 'human' });
      tx.remove(next.id);
    });
    this.select(s.id, { scroll: false });
  }

  toggleReviewed(id = this.selId) {
    const s = id && this.doc.seg(id);
    if (!s) return;
    this.doc.transact('Review', (tx) => tx.update(id, { reviewed: !s.reviewed }));
  }

  accept() {
    const s = this.selId && this.doc.seg(this.selId);
    if (!s) { this.nextLow(); return; }
    this.doc.transact('Accept', (tx) => tx.update(s.id, { reviewed: true }));
    const list = this.doc.list(s.tier_id);
    const nxt = list.slice(list.findIndex(x => x.id === s.id) + 1).find(x => !x.reviewed);
    if (nxt) this.select(nxt.id, { seek: true }); else this.nextLow();
  }

  nextLow() {
    const ids = new Set(this.visibleTiers().map(x => x.tier.id));
    const cands = [...this.doc.segs.values()].filter(s => ids.has(s.tier_id) && !s.reviewed && s.confidence != null && s.confidence < this.lowThr && s.start != null)
      .sort((a, b) => a.start - b.start || a.end - b.end);
    if (!cands.length) { toast('No unreviewed low-confidence segments left in this file', { kind: 'ok' }); return; }
    const cur = this.selId ? this.doc.seg(this.selId) : null;
    const t = cur ? cur.start : this.player.t - 1e-3;
    const nxt = cands.find(s => s.start > t || (cur && s.start === t && s.id > cur.id)) || cands[0];
    this.select(nxt.id, { seek: true });
  }

  stepSeg(dir) {
    const s = this.selId && this.doc.seg(this.selId);
    let target;
    if (s) {
      const nb = this.doc.neighbors(s);
      target = dir > 0 ? nb.next : nb.prev;
    } else if (this.activeTier) {
      const list = this.doc.list(this.activeTier).filter(x => x.start != null);
      const t = this.player.t;
      target = dir > 0 ? list.find(x => x.start >= t - 1e-3) : [...list].reverse().find(x => x.start < t - 1e-3);
    }
    if (target) this.select(target.id, { seek: true });
  }

  moveTier(dir) {
    const vis = this.visibleTiers().map(x => x.tier.id);
    if (!vis.length) return;
    const i = vis.indexOf(this.activeTier);
    const j = clamp(i < 0 ? 0 : i + dir, 0, vis.length - 1);
    const tid = vis[j];
    this.setActiveTier(tid);
    this.timeline.scrollTierIntoView(tid);
    const s = this.selId && this.doc.seg(this.selId);
    const t = s ? (s.start + s.end) / 2 : this.player.t;
    const hit = this.doc.at(tid, t) || this.doc.visible(tid, t - 2, t + 2)[0];
    if (hit) this.select(hit.id, { scroll: true }); else if (s) this.select(null);
  }

  setLabelIndex(i) {
    const s = this.selId && this.doc.seg(this.selId);
    if (!s) return;
    const tier = this.doc.tier(s.tier_id);
    if (tier.output !== 'label') return;
    const l = tierLabels(tier, this.doc)[i];
    if (l != null) this.updateSeg(s.id, { value: l }, 'Edit value');
  }

  playSeg() {
    const tl = this.timeline;
    if (tl.sel) { this.player.playRange(tl.sel.a, tl.sel.b); return; }
    const s = this.selId && this.doc.seg(this.selId);
    if (s && s.start != null) this.player.playRange(s.start, s.end);
    else this.player.toggle();
  }

  // ------------------------------------------------------------ document events
  afterChange(d) {
    if (this.selId && !this.doc.seg(this.selId)) this.selId = null;
    if (d?.label !== 'Rebuild speaker regions') this.checkSpeakerTurns();
    const sig = this.doc.tiers.map(t => t.id + ':' + t.name + ':' + t.color).join('|');
    if (sig !== this.tierSig) this.refreshTiers();
    else this.now.refresh();
    this.timeline.schedule();
    this.inspector.refresh();
  }

  afterReload() {
    this.selId = null;
    this.refreshTiers();
    this.inspector.render();
  }

  // ------------------------------------------------------------ keyboard & layout
  onKey(e) {
    if (modalOpen() || isTyping(e) || !this.el.isConnected || !this.el.offsetParent) return;
    if (document.querySelector('.popover')) return;
    const k = e.key, mod = e.metaKey || e.ctrlKey;
    if (mod) {
      const lk = k.toLowerCase();
      if (lk === 'z') { e.preventDefault(); e.shiftKey ? this.redo() : this.undo(); }
      else if (lk === 'y') { e.preventDefault(); this.redo(); }
      else if (lk === 's') { e.preventDefault(); this.flush().then(() => toast('Saved', { kind: 'ok', timeout: 1000 })); }
      return;
    }
    const tl = this.timeline;
    const handled = () => e.preventDefault();
    switch (k) {
      case ' ': handled(); e.shiftKey ? this.playSeg() : this.player.toggle(); break;
      case 'ArrowLeft': case 'ArrowRight': {
        handled();
        const d = (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 5 : e.altKey ? 0.1 : 1);
        this.player.seek(this.player.t + d);
        tl.ensureVisible(this.player.t, this.player.t);
        break;
      }
      case 'ArrowUp': handled(); this.moveTier(-1); break;
      case 'ArrowDown': handled(); this.moveTier(1); break;
      case 'Tab': handled(); this.stepSeg(e.shiftKey ? -1 : 1); break;
      case 'Enter':
        handled();
        if (this.selId) this.editInline(this.selId);
        else if (tl.sel && this.activeTier) { const { a, b } = tl.sel; tl.sel = null; this.createSegment(this.activeTier, a, b); }
        break;
      case 'Escape': this.select(null); tl.sel = null; tl.renderOverlays(); break;
      case 'p': case 'P': this.playSeg(); break;
      case 'Delete': case 'Backspace': if (this.selId) { handled(); this.deleteSeg(this.selId); } break;
      case 'r': case 'R': this.toggleReviewed(); break;
      case 'a': case 'A': this.accept(); break;
      case 'n': case 'N': this.nextLow(); break;
      case 's': case 'S': this.split(); break;
      case 'm': case 'M': this.mergeNext(); break;
      case '+': case '=': tl.zoomCenter(1.6); break;
      case '-': case '_': tl.zoomCenter(1 / 1.6); break;
      case '0': tl.fit(); break;
      case 'z': case 'Z': { const s = this.selId && this.doc.seg(this.selId); if (s) tl.zoomTo(s.start, s.end); break; }
      case 'f': case 'F': tl.toggleFollow(); break;
      case 'l': case 'L': tl.toggleAutoPlay(); break;
      case '[': this.dv.step(-1); break;
      case ']': this.dv.step(1); break;
      default:
        if (/^[1-9]$/.test(k)) this.setLabelIndex(+k - 1);
    }
  }

  startSplit(e, el) {
    e.preventDefault();
    el.classList.add('drag');
    const y0 = e.clientY;
    const h0 = parseFloat(getComputedStyle(this.el).getPropertyValue('--top-h')) || 220;
    const key = this.media.modality === 'video' ? 'topHv' : 'topH';
    const move = (ev) => this.el.style.setProperty('--top-h', clamp(h0 + ev.clientY - y0, 90, window.innerHeight - 260) + 'px');
    const up = () => {
      el.classList.remove('drag');
      ls.set(key, parseFloat(getComputedStyle(this.el).getPropertyValue('--top-h')));
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  }

  destroy() {
    super.destroy();
    document.removeEventListener('keydown', this.onKey);
    this.timeline?.destroy();
    this.player?.destroy();
  }
}
