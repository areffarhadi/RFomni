// Small DOM + formatting helpers (no framework).
import { icon } from './icons.js';

// Let el.append / el.replaceChildren skip null/false and flatten arrays, like h() does,
// so conditional children (`cond ? h(...) : null`) never render the text "null".
for (const name of ['append', 'replaceChildren']) {
  const orig = Element.prototype[name];
  Element.prototype[name] = function (...kids) {
    return orig.apply(this, kids.flat(Infinity).filter(k => k != null && k !== false));
  };
}

/** h('div.a.b', {props}, ...children) */
export function h(tag, props, ...children) {
  let el;
  if (typeof tag === 'string') {
    const [name, ...classes] = tag.split('.');
    el = document.createElement(name || 'div');
    if (classes.length) el.className = classes.join(' ');
  } else el = tag;
  if (props != null && (typeof props !== 'object' || props instanceof Node || Array.isArray(props))) {
    children.unshift(props);
    props = null;
  }
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = [el.className, v].filter(Boolean).join(' ');
      else if (k === 'style' && typeof v === 'object') {
        for (const [sk, sv] of Object.entries(v)) {
          if (sv == null) continue;
          if (sk.startsWith('--')) el.style.setProperty(sk, sv); else el.style[sk] = sv;
        }
      }
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'html') el.innerHTML = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k in el && typeof v !== 'string') el[k] = v;
      else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'selected') el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export function ico(name, size = 16, cls = '') {
  const span = document.createElement('span');
  span.className = 'ico ' + cls;
  span.innerHTML = icon(name, size);
  return span;
}

export function fmtTime(t, ms = true) {
  if (t == null || !isFinite(t)) return '–';
  const neg = t < 0;
  t = Math.abs(t);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = ms ? s.toFixed(3).padStart(6, '0') : String(Math.floor(s)).padStart(2, '0');
  return (neg ? '-' : '') + (h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`);
}

export function fmtDur(t) {
  if (t == null || !isFinite(t)) return '';
  if (t < 60) return `${t < 10 ? t.toFixed(1) : Math.round(t)} s`;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.round(t % 60);
  return h ? `${h} h ${m} min` : `${m}:${String(s).padStart(2, '0')}`;
}

export function fmtBytes(n) {
  if (!n && n !== 0) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

export function debounce(fn, ms) {
  let t;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function rafThrottle(fn) {
  let queued = false;
  return () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; fn(); });
  };
}

export function uid(prefix = 's') {
  const b = crypto.getRandomValues(new Uint8Array(5));
  return prefix + [...b].map(x => x.toString(16).padStart(2, '0')).join('');
}

export function valStr(v) {
  return Array.isArray(v) ? v.join('; ') : (v ?? '');
}

export const ls = {
  get(key, def) {
    try { const v = localStorage.getItem('omni:' + key); return v == null ? def : JSON.parse(v); } catch { return def; }
  },
  set(key, val) { try { localStorage.setItem('omni:' + key, JSON.stringify(val)); } catch { /* private mode */ } },
};

// ---- colors -------------------------------------------------------------
const CAT = ['#38bdf8', '#f472b6', '#fb923c', '#a78bfa', '#34d399', '#facc15', '#22d3ee', '#fb7185',
  '#818cf8', '#a3e635', '#f59e0b', '#2dd4bf', '#e879f9', '#60a5fa'];
const EMO = {
  neutral: '#94a3b8', happy: '#facc15', sad: '#60a5fa', angry: '#f87171', fearful: '#a78bfa',
  disgusted: '#4ade80', surprised: '#fb923c', frustrated: '#f97316', determined: '#2dd4bf', other: '#cbd5e1',
  surprise: '#fb923c', fear: '#a78bfa', disgust: '#4ade80', anger: '#f87171', happiness: '#facc15', sadness: '#60a5fa',
  good: '#4ade80', fair: '#facc15', poor: '#f87171', positive: '#4ade80', negative: '#f87171',
  'no face': '#64748b', speech: '#94a3b8',
};

export function hashColor(str) {
  let x = 2166136261;
  for (let i = 0; i < str.length; i++) { x ^= str.charCodeAt(i); x = Math.imul(x, 16777619); }
  return CAT[Math.abs(x) % CAT.length];
}

/** Color for a segment: categorical tiers get a color per label, others use the tier color. */
export function segColor(tier, value) {
  if ((tier.output === 'label' || tier.output === 'boxes') && typeof value === 'string' && value) {
    const k = value.toLowerCase();
    if (EMO[k] && (tier.granularity !== 'file' || tier.output === 'boxes')) return EMO[k];
    if (tier.task_id === 'diarization' || tier.task_id === 'lid' || (tier.labels || []).length !== 1) return hashColor(k);
  }
  return tier.color || '#94a3b8';
}

export function modalityIcon(mod) {
  return { audio: 'audio', video: 'film', image: 'image' }[mod] || 'file';
}

export function download(url) {
  const a = h('a', { href: url, download: '' });
  document.body.append(a);
  a.click();
  a.remove();
}

export function isTyping(e) {
  const t = e.target;
  return t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
}
