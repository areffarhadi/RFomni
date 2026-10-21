// "At the playhead" panel: transcript context (previous / current / next) with word highlighting,
// plus chips with every label tier's value at the playhead.
import { h, fmtTime, valStr, segColor } from '../util.js';

export class NowPanel {
  constructor(ed, { compact = false } = {}) {
    this.ed = ed;
    this.compact = compact;
    this.timeEl = h('div.now-time');
    this.prevEl = h('div.now-ctx');
    this.textEl = h('div.now-text');
    this.nextEl = h('div.now-ctx');
    this.chips = h('div.now-chips');
    this.el = compact ? this.chips : h('div.now', this.timeEl, this.prevEl, this.textEl, this.nextEl, this.chips);
    this.key = null;
    this.words = [];
    this.lastT = -1;
  }

  pickTiers() {
    const tiers = this.ed.doc.tiers;
    this.asr = tiers.find(t => t.task_id === 'asr') || tiers.find(t => t.output === 'text' && t.granularity === 'segment');
    this.wordTier = tiers.find(t => t.task_id === 'forced_alignment') || tiers.find(t => t.granularity === 'word');
    this.chipTiers = tiers.filter(t => t.output !== 'text' && !['word', 'phone'].includes(t.granularity) && !this.ed.hidden.has(t.id));
  }

  refresh() {
    this.pickTiers();
    this.key = null;
    this.update(this.ed.player.t, true);
  }

  ctxLine(el, seg, label) {
    if (!seg) { el.replaceChildren(); return; }
    el.replaceChildren(h('span.lab', label), h('span', { onclick: () => this.ed.select(seg.id, { seek: true }) }, valStr(seg.value) || '(empty)'));
  }

  update(t, force = false) {
    if (!force && t === this.lastT) return;
    this.lastT = t;
    const doc = this.ed.doc;
    if (!this.compact) {
      this.timeEl.replaceChildren(fmtTime(t), h('small', this.ed.media.name));
      let cur = null, prev = null, next = null;
      if (this.asr) {
        const list = doc.list(this.asr.id).filter(s => s.start != null);
        cur = doc.at(this.asr.id, t);
        const i = cur ? list.indexOf(cur) : list.findIndex(s => s.start > t);
        if (cur) { prev = list[i - 1]; next = list[i + 1]; } else if (i >= 0) { prev = list[i - 1]; next = list[i]; } else prev = list[list.length - 1];
      }
      const key = `${cur?.id}|${cur?.value}|${prev?.id}|${next?.id}|${doc.version}`;
      if (key !== this.key) {
        this.key = key;
        this.words = [];
        this.ctxLine(this.prevEl, prev, 'Before');
        this.ctxLine(this.nextEl, next, cur ? 'Next' : 'Up next');
        if (!this.asr) {
          this.textEl.className = 'now-text is-empty';
          this.textEl.textContent = 'No transcript tier yet. Run ASR (or import ELAN / Praat / JSON) and the text will follow playback here.';
        } else if (!cur) {
          this.textEl.className = 'now-text is-empty';
          this.textEl.textContent = '(no speech at the playhead)';
        } else {
          this.textEl.className = 'now-text';
          const ws = this.wordTier ? doc.visible(this.wordTier.id, cur.start, cur.end)
            .filter(w => w.start >= cur.start - 0.02 && w.end <= cur.end + 0.02) : [];
          if (ws.length) {
            this.words = ws.map(w => ({ w, el: h('span.w', { onclick: () => this.ed.player.seek(w.start) }, valStr(w.value)) }));
            this.textEl.replaceChildren(...this.words.flatMap(x => [x.el, ' ']));
          } else {
            this.textEl.textContent = valStr(cur.value) || '(empty)';
          }
        }
      }
      for (const { w, el } of this.words) {
        el.classList.toggle('cur', w.start <= t && t < w.end);
        el.classList.toggle('past', w.end <= t);
      }
    }
    const chips = [];
    for (const tier of this.chipTiers || []) {
      const s = doc.at(tier.id, t);
      if (!s) continue;
      const v = valStr(s.value);
      if (!v) continue;
      chips.push(h('span.now-chip', { style: { '--c': segColor(tier, s.value) } }, h('b', tier.name), h('i'), v));
    }
    this.chips.replaceChildren(...chips);
    if (this.compact) this.chips.style.display = chips.length ? '' : 'none';
  }
}
