// Speaker turns (diarization, may overlap) -> single-speaker ("pure") speech and overlaps per speaker set.
// Same logic as backend/speakers.py.
export const DIAR = 'diarization', PURE = 'pure_speech', OVERLAP = 'overlap';

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
export const sortSpeakers = (arr) => [...new Set(arr)].sort(collator.compare);
export const pairLabel = (speakers) => sortSpeakers(speakers).join(' + ');
export const tierKind = (tier) => tier && (tier.task_id || tier.id);

const minN = (a, b) => (a == null ? b : b == null ? a : Math.min(a, b));

export function speakerRegions(turns, minDur = 0.02) {
  const tl = turns.filter(s => s.start != null && s.end > s.start && typeof s.value === 'string' && s.value);
  const ev = [];
  tl.forEach((s, i) => { ev.push([s.start, 1, i]); ev.push([s.end, 0, i]); });
  ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const active = new Map(), runs = [];
  let prev = null;
  for (const [t, kind, i] of ev) {
    if (prev != null && t > prev && active.size) {
      const set = sortSpeakers([...active.values()].map(s => s.value));
      let conf = null;
      for (const s of active.values()) conf = minN(conf, s.confidence);
      const key = set.join('\u0000');
      const last = runs[runs.length - 1];
      if (last && last.key === key && Math.abs(last.end - prev) < 1e-9) { last.end = t; last.confidence = minN(last.confidence, conf); }
      else runs.push({ start: prev, end: t, key, speakers: set, confidence: conf });
    }
    if (kind === 1) active.set(i, tl[i]); else active.delete(i);
    prev = t;
  }
  const r3 = (x) => Math.round(x * 1000) / 1000;
  const pure = [], overlap = [];
  for (const r of runs) {
    if (r.end - r.start < minDur) continue;
    const rec = { start: r3(r.start), end: r3(r.end), speakers: r.speakers, confidence: r.confidence };
    if (r.speakers.length === 1) pure.push({ ...rec, value: r.speakers[0] });
    else overlap.push({ ...rec, value: r.speakers.join(' + ') });
  }
  return { pure, overlap };
}

/** Do any two turns of different (or the same) speakers overlap by more than 20 ms? */
export function hasOverlap(segs) {
  let end = -Infinity;
  for (const s of segs) {
    if (s.start == null) continue;
    if (s.start < end - 0.02) return true;
    end = Math.max(end, s.end);
  }
  return false;
}
