// Small SVG charts for the results of shared tasks (views/shared.js): bars, a histogram, means with their 95%
// intervals, and answers over time. They draw in the studio's colours (css .ch-*) and scale with their box.
import { h } from './util.js';

const NS = 'http://www.w3.org/2000/svg';
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
}
const fmt = (n) => (Math.abs(n) >= 100 || Number.isInteger(n) ? String(Math.round(n)) : n.toFixed(1));

// horizontal bars: [{label, value}] (e.g. how often each class was chosen)
export function bars(rows, { unit = '' } = {}) {
  if (!rows.length) return h('p.muted', 'No answers yet');
  const max = Math.max(1, ...rows.map(r => r.value));
  const total = rows.reduce((a, r) => a + r.value, 0) || 1;
  return h('div.ch-bars', rows.map(r => h('div.ch-bar',
    h('span.ch-label', { title: r.label }, r.label),
    h('i', h('b', { style: { width: `${(r.value / max) * 100}%` } })),
    h('em', `${fmt(r.value)}${unit}`, h('small', ` ${Math.round((r.value / total) * 100)}%`)))));
}

// vertical columns over an ordered scale: [{label, value}] (e.g. how often each score 1..5 was given)
export function columns(rows, { mean = null, caption = '' } = {}) {
  const W = 320, H = 150, pad = 22, max = Math.max(1, ...rows.map(r => r.value));
  const bw = (W - pad) / Math.max(1, rows.length);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ch-svg', role: 'img', 'aria-label': caption || 'chart' });
  rows.forEach((r, i) => {
    const bh = ((H - 2 * pad) * r.value) / max;
    const x = pad / 2 + i * bw + bw * 0.15;
    svg.append(s('rect', { x, y: H - pad - bh, width: bw * 0.7, height: Math.max(bh, r.value ? 1.5 : 0), rx: 3, class: 'ch-fill' }),
      s('text', { x: x + bw * 0.35, y: H - pad + 14, class: 'ch-tick', 'text-anchor': 'middle' }, r.label),
      r.value ? s('text', { x: x + bw * 0.35, y: H - pad - bh - 4, class: 'ch-val', 'text-anchor': 'middle' }, fmt(r.value)) : null);
  });
  if (mean != null && rows.length > 1) {  // the mean, placed on the scale between the first and last column
    const lo = +rows[0].label, hi = +rows[rows.length - 1].label;
    if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) {
      const x = pad / 2 + bw / 2 + ((mean - lo) / (hi - lo)) * (bw * (rows.length - 1));
      svg.append(s('line', { x1: x, x2: x, y1: pad - 6, y2: H - pad, class: 'ch-mean' }),
        s('text', { x, y: pad - 9, class: 'ch-val', 'text-anchor': 'middle' }, `mean ${mean.toFixed(2)}`));
    }
  }
  return h('div.ch-box', svg);
}

// a histogram of numbers (e.g. seconds per answer), in `n` equal bins
export function histogram(values, { n = 12, unit = 's' } = {}) {
  if (!values.length) return h('p.muted', 'No answers yet');
  const sorted = [...values].sort((a, b) => a - b);
  const hi = sorted[Math.floor(sorted.length * 0.97)] || sorted[sorted.length - 1];  // the slowest 3% go in the last bin
  const lo = 0, step = Math.max((hi - lo) / n, 0.1);
  const counts = Array(n).fill(0);
  for (const v of values) counts[Math.min(n - 1, Math.floor((v - lo) / step))]++;
  const med = sorted[Math.floor(sorted.length / 2)];
  return h('div', columns(counts.map((c, i) => ({ label: i % 2 ? '' : `${fmt(lo + i * step)}`, value: c }))),
    h('p.ch-note', `Half the answers took under ${fmt(med)} ${unit}; the last column holds everything over ${fmt(hi - step)} ${unit}.`));
}

// means with their 95% intervals: [{label, mean, ci, n}] on a scale lo..hi (e.g. MOS per system)
export function means(rows, lo, hi) {
  const W = 320, rowH = 26, left = 96, right = 14, H = rows.length * rowH + 26;
  const x = (v) => left + ((v - lo) / (hi - lo || 1)) * (W - left - right);
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ch-svg', role: 'img', 'aria-label': 'mean score per system' });
  for (let v = lo; v <= hi; v++) {
    svg.append(s('line', { x1: x(v), x2: x(v), y1: 4, y2: H - 18, class: 'ch-grid' }),
      s('text', { x: x(v), y: H - 4, class: 'ch-tick', 'text-anchor': 'middle' }, v));
  }
  rows.forEach((r, i) => {
    const y = 4 + i * rowH + rowH / 2;
    svg.append(s('text', { x: left - 8, y: y + 4, class: 'ch-tick', 'text-anchor': 'end' }, String(r.label).slice(0, 14)));
    if (r.mean == null) return;
    if (r.ci != null) svg.append(s('line', { x1: x(Math.max(lo, r.mean - r.ci)), x2: x(Math.min(hi, r.mean + r.ci)), y1: y, y2: y, class: 'ch-ci' }));
    svg.append(s('circle', { cx: x(r.mean), cy: y, r: 4.5, class: 'ch-dot' }, s('title', {}, `${r.label}: ${r.mean.toFixed(2)}${r.ci != null ? ` ± ${r.ci.toFixed(2)}` : ''} (${r.n})`)));
  });
  return h('div.ch-box', svg);
}

// answers per day: [[YYYY-MM-DD, n], ...]
export function timeline(points) {
  if (!points.length) return h('p.muted', 'No answers yet');
  const W = 320, H = 130, pad = 22, max = Math.max(1, ...points.map(p => p[1]));
  const x = (i) => pad + (points.length === 1 ? (W - 2 * pad) / 2 : (i / (points.length - 1)) * (W - 2 * pad));
  const y = (v) => H - pad - (v / max) * (H - 2 * pad);
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'ch-svg', role: 'img', 'aria-label': 'answers per day' },
    s('path', { d: `${line}L${x(points.length - 1)},${H - pad}L${x(0)},${H - pad}Z`, class: 'ch-area' }),
    s('path', { d: line, class: 'ch-line' }),
    points.map((p, i) => s('circle', { cx: x(i), cy: y(p[1]), r: 2.5, class: 'ch-dot' }, s('title', {}, `${p[0]}: ${p[1]}`))),
    s('text', { x: pad, y: H - 4, class: 'ch-tick' }, points[0][0].slice(5)),
    points.length > 1 ? s('text', { x: W - pad, y: H - 4, class: 'ch-tick', 'text-anchor': 'end' }, points[points.length - 1][0].slice(5)) : null,
    s('text', { x: pad, y: pad - 8, class: 'ch-val' }, `most in a day: ${max}`));
  return h('div.ch-box', svg);
}

// a share as a ring: 0..1 (e.g. agreement)
export function ring(frac, label) {
  const r = 30, c = 2 * Math.PI * r;
  return h('div.ch-ring', s('svg', { viewBox: '0 0 80 80', class: 'ch-svg', role: 'img', 'aria-label': `${label}: ${Math.round(frac * 100)}%` },
    s('circle', { cx: 40, cy: 40, r, class: 'ch-track' }),
    s('circle', { cx: 40, cy: 40, r, class: 'ch-arc', 'stroke-dasharray': `${c * frac} ${c}`, transform: 'rotate(-90 40 40)' }),
    s('text', { x: 40, y: 45, 'text-anchor': 'middle', class: 'ch-big' }, `${Math.round(frac * 100)}%`)), h('span', label));
}
