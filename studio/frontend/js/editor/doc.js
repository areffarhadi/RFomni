// Annotation document for one file: tiers + segments, indexes, undo/redo transactions.
import { uid } from '../util.js';

export class AnnoDoc extends EventTarget {
  constructor(raw) {
    super();
    this.version = 0;
    this.load(raw);
  }

  load(raw) {
    this.rev = raw.rev;
    this.tiers = raw.tiers.map(t => ({ ...t }));
    this.segs = new Map(raw.segments.map(s => [s.id, { ...s }]));
    this.undoStack = [];
    this.redoStack = [];
    this.reindex();
    this.version++;
  }

  reindex() {
    this.byTier = new Map(this.tiers.map(t => [t.id, []]));
    for (const s of this.segs.values()) this.byTier.get(s.tier_id)?.push(s);
    this.maxDur = new Map();
    for (const [tid, arr] of this.byTier) {
      arr.sort((a, b) => (a.start ?? -1) - (b.start ?? -1) || (a.end ?? 0) - (b.end ?? 0));
      let m = 0;
      for (const s of arr) if (s.start != null) m = Math.max(m, s.end - s.start);
      this.maxDur.set(tid, m);
    }
  }

  tier(id) { return this.tiers.find(t => t.id === id); }
  seg(id) { return this.segs.get(id); }
  list(tid) { return this.byTier.get(tid) || []; }
  index(s) { return this.list(s.tier_id).indexOf(s); }

  /** Segments of a tier overlapping [t0, t1], in time order. */
  visible(tid, t0, t1) {
    const arr = this.list(tid);
    const x = t0 - (this.maxDur.get(tid) || 0);
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if ((arr[mid].start ?? -Infinity) < x) lo = mid + 1; else hi = mid;
    }
    const out = [];
    for (let i = lo; i < arr.length; i++) {
      const s = arr[i];
      if (s.start == null) continue;
      if (s.start > t1) break;
      if (s.end >= t0) out.push(s);
    }
    return out;
  }

  /** The segment of a tier under time t (shortest one if several overlap). */
  at(tid, t) {
    let best = null;
    for (const s of this.visible(tid, t, t)) {
      if (s.start <= t && t < s.end && (!best || s.end - s.start < best.end - best.start)) best = s;
    }
    return best;
  }

  neighbors(s) {
    const arr = this.list(s.tier_id);
    const i = arr.indexOf(s);
    return { prev: arr[i - 1] || null, next: arr[i + 1] || null, i, n: arr.length };
  }

  /**
   * Run `fn(tx)` as one undoable step. tx.add(seg) / tx.update(id, patch) / tx.remove(id) / tx.setTiers(list).
   * With merge=true, consecutive steps with the same label are merged (typing).
   */
  transact(label, fn, { merge = false } = {}) {
    if (this.readOnly) { this.dispatchEvent(new CustomEvent('readonly', { detail: { label } })); return; }
    const changes = new Map();
    let tiersBefore = null;
    const tx = {
      add: (s) => {
        const seg = { confidence: null, source: 'human', reviewed: false, value: '', ...s, id: s.id || uid('s') };
        changes.set(seg.id, { before: changes.get(seg.id)?.before ?? null, after: seg });
        this.segs.set(seg.id, seg);
        return seg;
      },
      update: (id, patch) => {
        const cur = this.segs.get(id);
        if (!cur) return null;
        const after = { ...cur, ...patch };
        const ch = changes.get(id);
        if (ch) ch.after = after; else changes.set(id, { before: cur, after });
        this.segs.set(id, after);
        return after;
      },
      remove: (id) => {
        const cur = this.segs.get(id);
        if (!cur) return;
        const ch = changes.get(id);
        if (ch) ch.after = null; else changes.set(id, { before: cur, after: null });
        this.segs.delete(id);
      },
      setTiers: (tiers) => {
        if (!tiersBefore) tiersBefore = this.tiers;
        this.tiers = tiers;
      },
    };
    fn(tx);
    if (!changes.size && !tiersBefore) return;
    const last = this.undoStack[this.undoStack.length - 1];
    if (merge && last && last.label === label && Date.now() - last.time < 2000 && !tiersBefore && !last.tiersBefore) {
      const prev = new Map(last.changes);
      for (const [id, ch] of changes) {
        const p = prev.get(id);
        prev.set(id, p ? { before: p.before, after: ch.after } : ch);
      }
      last.changes = [...prev.entries()];
      last.time = Date.now();
    } else {
      this.undoStack.push({ label, time: Date.now(), changes: [...changes.entries()], tiersBefore, tiersAfter: tiersBefore ? this.tiers : null });
      if (this.undoStack.length > 400) this.undoStack.shift();
    }
    this.redoStack = [];
    this.changed(label);
  }

  undo() {
    if (this.readOnly) return null;
    const e = this.undoStack.pop();
    if (!e) return null;
    for (const [id, { before }] of e.changes) before ? this.segs.set(id, before) : this.segs.delete(id);
    if (e.tiersBefore) this.tiers = e.tiersBefore;
    this.redoStack.push(e);
    this.changed('undo');
    return e.label;
  }

  redo() {
    const e = this.redoStack.pop();
    if (!e) return null;
    for (const [id, { after }] of e.changes) after ? this.segs.set(id, after) : this.segs.delete(id);
    if (e.tiersAfter) this.tiers = e.tiersAfter;
    this.undoStack.push(e);
    this.changed('redo');
    return e.label;
  }

  changed(label) {
    this.reindex();
    this.version++;
    this.dispatchEvent(new CustomEvent('change', { detail: { label } }));
  }

  toJSON() {
    return { rev: this.rev, tiers: this.tiers, segments: [...this.segs.values()] };
  }
}

export function tierFromTask(task, source = 'human') {
  return {
    id: task.id, task_id: task.id, name: task.name, title: task.title || task.name, color: task.color,
    output: task.output, granularity: task.granularity, labels: task.labels || [], parent: task.parent || null, source,
  };
}
