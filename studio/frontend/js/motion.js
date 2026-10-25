// Motion: the liquid tab rail, page / panel transitions and sliding segmented controls.
// Plain Web Animations API, no library. With "reduce motion" turned on in the system, every change is instant.
import { h, ico } from './util.js';

const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
export const reducedMotion = () => mq.matches;

const EASE_OUT = 'cubic-bezier(.2, .8, .2, 1)';
const EASE_IN = 'cubic-bezier(.4, 0, 1, 1)';
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// ---- page and panel transitions ----------------------------------------------
// A view that leaves blurs and fades where it is, laid over the one that arrives;
// the new view comes into focus while sliding a little from the direction of travel
// (dir +1: the next tab is further down the rail, so the page arrives from below, like scrolling).
const running = new WeakMap();

function stop(el) {
  const a = running.get(el);
  if (a) { running.delete(el); a.cancel(); }
}
const visible = (el) => el.isConnected && el.getClientRects().length > 0;

export function enter(el, dir = 1) {
  stop(el);
  if (reducedMotion() || !visible(el)) return;
  const a = el.animate([
    { opacity: 0, filter: 'blur(10px)', transform: `translateY(${dir * 22}px)` },
    { opacity: 1, filter: 'blur(0)', transform: 'none' },
  ], { duration: 380, delay: 40, easing: EASE_OUT, fill: 'backwards' });
  running.set(el, a);
  a.onfinish = () => { if (running.get(el) === a) running.delete(el); };
}

/** Animate `el` out, then run `done` (hide or remove it). A leave that is interrupted never runs `done`. */
export function leave(el, dir = 1, done) {
  stop(el);
  if (reducedMotion() || !visible(el)) { done?.(); return; }
  const s = el.style, keep = [s.position, s.inset, s.pointerEvents, s.zIndex];
  const restore = () => { [s.position, s.inset, s.pointerEvents, s.zIndex] = keep; };
  Object.assign(s, { position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '2' });
  const a = el.animate([
    { opacity: 1, filter: 'blur(0)', transform: 'none' },
    { opacity: 0, filter: 'blur(8px)', transform: `translateY(${dir * -14}px)` },
  ], { duration: 200, easing: EASE_IN, fill: 'forwards' });
  running.set(el, a);
  a.onfinish = () => {
    if (running.get(el) !== a) return;
    running.delete(el);
    done?.();
    a.cancel();
    restore();
  };
  a.oncancel = restore;
}

/** Replace the content of `parent` (oldEl → newEl) with the leave / enter pair. */
export function swap(parent, oldEl, newEl, dir = 1, animate = true) {
  if (animate && oldEl && oldEl !== newEl && oldEl.parentNode === parent && !reducedMotion()) {
    for (const c of [...parent.children]) if (c !== oldEl && !running.has(c)) c.remove();
    parent.append(newEl);
    leave(oldEl, dir, () => oldEl.remove());
  } else {
    parent.replaceChildren(newEl);
  }
  if (animate) enter(newEl, dir);
}

// ---- liquid rail ----------------------------------------------------------------
// The rail is a floating dock with a light outline. The active tab is a coloured bead centred
// on the dock's right edge; the outline lifts off the edge, wraps the bead with a small gap and
// comes back, through two round shoulders that meet the edge and the ring tangentially
// (a shoulder of radius f touching the edge meets a ring of radius R at sqrt((R + f)^2 - f^2) along it).
// The ring, its fill and the bead travel together as one piece moved with a transform, so the
// browser's compositor runs the glide: it stays smooth even while the next page is being built.
const EDGE = 61.5;   // centre of the dock's right outline, in rail pixels (see .rail-dock in app.css)
const BEAD_R = 14;   // bead radius
const RING_R = 18;   // radius of the outline around the bead (bead + 4px gap)
const SHOULDER = 10; // shoulder radius
const SVGNS = 'http://www.w3.org/2000/svg';

// spring-like easing (damping .8): starts gently, glides, settles with a barely visible overshoot
const SPRING = (() => {
  const z = 0.8, w = 7.5, wd = w * Math.sqrt(1 - z * z), pts = [];
  for (let i = 0; i <= 50; i++) {
    const t = i / 50;
    pts.push(i === 50 ? 1 : +(1 - Math.exp(-z * w * t) * (Math.cos(wd * t) + (z * w / wd) * Math.sin(wd * t))).toFixed(4));
  }
  const css = `linear(${pts.join(', ')})`;
  return window.CSS?.supports?.('animation-timing-function', css) ? css : 'cubic-bezier(.33, 1, .38, 1)';
})();

function ringPiece() {
  const R = RING_R, f = SHOULDER;
  const dy = Math.sqrt((R + f) ** 2 - f ** 2);        // where the shoulders leave the edge
  const tx = f * R / (R + f), ty = dy * R / (R + f);  // where the shoulders meet the ring
  const n = (v) => +v.toFixed(3);
  const outline = `M0 ${n(-dy)} A${f} ${f} 0 0 0 ${n(tx)} ${n(-ty)} A${R} ${R} 0 0 1 ${n(tx)} ${n(ty)} A${f} ${f} 0 0 0 0 ${n(dy)}`;
  const svg = document.createElementNS(SVGNS, 'svg');
  const top = Math.ceil(dy) + 2, w = R + 6;
  svg.setAttribute('viewBox', `-4 ${-top} ${w} ${top * 2}`);
  svg.setAttribute('width', w);
  svg.setAttribute('height', top * 2);
  svg.classList.add('rail-ring');
  svg.style.left = '-4px';
  svg.style.top = -top + 'px';
  const fill = document.createElementNS(SVGNS, 'path');   // covers the straight outline under the ring
  fill.setAttribute('d', `${outline} L-3 ${n(dy)} L-3 ${n(-dy)} Z`);
  fill.classList.add('fill');
  const line = document.createElementNS(SVGNS, 'path');
  line.setAttribute('d', outline);
  line.classList.add('line');
  svg.append(fill, line);
  return svg;
}

export class LiquidRail {
  constructor(rail) {
    this.rail = rail;
    this.bead = h('span.rail-bead');
    this.drop = h('div.rail-drop', ringPiece(), this.bead);       // stretches while it moves
    this.slider = h('div.rail-slider', { 'aria-hidden': 'true', style: { left: EDGE + 'px' } }, this.drop);
    this.glow = h('i.proj-glow', { 'aria-hidden': 'true' });       // coloured light behind the pages
    rail.prepend(h('div.rail-dock'));
    rail.append(this.slider);
    this.y = null; this.ty = null; this.active = null; this.icon = null; this.anims = [];
    this.ro = new ResizeObserver(() => { if (!this.anims.length && this.measure()) this.place(this.ty); });
    this.ro.observe(rail);
  }

  /** The glow lives behind the rail and the pages, in the project element. */
  attachGlow(host) { host.prepend(this.glow); if (this.ty != null) this.place(this.ty); }

  /** Move the bead to tab link `a` (icon name, colour as a CSS value). */
  set(a, { icon, color }) {
    const first = this.active == null;
    this.active = a;
    this.rail.classList.toggle('has-bead', !!a);
    if (!a) return;
    const from = this.currentY();
    if (!this.measure()) return;
    this.rail.style.setProperty('--bead', color);
    this.glow.style.setProperty('--bead', color);
    if (icon !== this.icon) this.setIcon(icon, !first);
    if (first || reducedMotion() || from == null || Math.abs(from - this.ty) < 0.5) { this.place(this.ty); return; }
    this.glide(from, this.ty);
  }

  measure() {
    const a = this.active;
    if (!a || !a.isConnected || !a.offsetHeight) return false;
    const i = a.querySelector('.ico') || a;
    this.ty = a.offsetTop + i.offsetTop + i.offsetHeight / 2;
    return true;
  }

  currentY() {
    if (this.y == null) return null;
    const m = getComputedStyle(this.slider).transform;   // mid-glide: where the bead is right now
    if (!m || m === 'none') return this.y;
    const v = m.match(/matrix(3d)?\(([^)]+)\)/)?.[2].split(',').map(Number);
    return v ? (v.length === 16 ? v[13] : v[5]) : this.y;
  }

  place(y) {
    this.anims.forEach(x => x.cancel());
    this.anims = [];
    this.y = y;
    this.slider.style.transform = `translateY(${y}px)`;
    this.glow.style.transform = `translateY(${y}px)`;
  }

  glide(from, to) {
    this.place(to);
    const dist = Math.abs(to - from);
    const duration = Math.round(clamp(440 + dist * 0.55, 480, 680));
    const T = (y) => ({ transform: `translateY(${y}px)` });
    const run = (el, frames, opts) => { const x = el.animate(frames, opts); this.anims.push(x); return x; };
    const main = run(this.slider, [T(from), T(to)], { duration, easing: SPRING });
    // the drop stretches along its path in the middle of the trip, then rounds again
    const k = clamp(dist / 160, 0.35, 1);
    run(this.drop, [{ transform: 'scale(1, 1)' },
      { transform: `scale(${1 - 0.08 * k}, ${1 + 0.16 * k})`, offset: 0.35 },
      { transform: 'scale(1, 1)' }], { duration: duration * 0.8, easing: 'ease-in-out' });
    // the light lags a little behind the bead
    run(this.glow, [T(from), T(to)], { duration: duration * 1.25, easing: SPRING });
    main.onfinish = () => { this.anims = this.anims.filter(x => x.playState === 'running'); };
    this.rail.style.setProperty('--glide', duration + 'ms');
  }

  setIcon(name, animate) {
    const old = this.bead.firstElementChild;
    const el = ico(name, 15);
    this.bead.append(el);
    this.icon = name;
    if (!old) return;
    if (!animate || reducedMotion()) { old.remove(); return; }
    old.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(.5)' }],
      { duration: 160, easing: EASE_IN, fill: 'forwards' }).onfinish = () => old.remove();
    el.animate([{ opacity: 0, transform: 'scale(.5)' }, { opacity: 1, transform: 'scale(1)' }],
      { duration: 280, delay: 140, easing: EASE_OUT, fill: 'backwards' });
  }

  destroy() {
    this.anims.forEach(x => x.cancel());
    this.ro.disconnect();
  }
}

/** Resolve after the browser has painted once more (lets a glide start before heavy work). */
export const nextFrame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));

// ---- segmented controls ---------------------------------------------------------
// The highlight of a .seg-ctl slides to the chosen option: its front edge leads, the back edge follows.
function placePill(ctl, pill, animate) {
  const on = [...ctl.children].find(b => b !== pill && b.classList.contains('on'));
  if (!on || !ctl.offsetWidth) { pill.style.opacity = '0'; pill._box = null; return; }
  const box = { x: on.offsetLeft, y: on.offsetTop, w: on.offsetWidth, h: on.offsetHeight };
  const old = pill._box;
  pill._box = box;
  pill.style.opacity = '1';
  pill.style.height = box.h + 'px';
  pill.style.width = box.w + 'px';
  pill.style.transform = `translate(${box.x}px, ${box.y}px)`;
  if (!animate || !old || reducedMotion() || (old.x === box.x && old.w === box.w)) return;
  const right = box.x > old.x;
  const L0 = old.x, R0 = old.x + old.w, L1 = box.x, R1 = box.x + box.w;
  // halfway: the leading edge is nearly there, the trailing edge has only started
  const Lm = right ? L0 + (L1 - L0) * 0.25 : L1 + (L0 - L1) * 0.1;
  const Rm = right ? R1 - (R1 - R0) * 0.1 : R0 - (R0 - R1) * 0.25;
  const f = (L, R) => ({ transform: `translate(${L}px, ${box.y}px)`, width: `${R - L}px` });
  pill.animate([f(L0, R0), { ...f(Lm, Rm), offset: 0.45 }, f(L1, R1)], { duration: 340, easing: EASE_OUT });
}

export function liquidSeg(ctl) {
  if (ctl.classList.contains('liquid')) return;
  ctl.classList.add('liquid');
  const pill = h('i.seg-pill', { 'aria-hidden': 'true' });
  ctl.append(pill);  // last child: code that walks the buttons by index or data-* value never touches it
  new MutationObserver(() => placePill(ctl, pill, true))
    .observe(ctl, { subtree: true, attributes: true, attributeFilter: ['class'], childList: true });
  new ResizeObserver(() => placePill(ctl, pill, false)).observe(ctl);
  placePill(ctl, pill, false);
}

/** Give every segmented control on the page (now and later) the sliding highlight. */
export function watchSegments(root = document.body) {
  let queued = false;
  const scan = () => { queued = false; root.querySelectorAll('.seg-ctl:not(.liquid)').forEach(liquidSeg); };
  new MutationObserver(() => { if (!queued) { queued = true; requestAnimationFrame(scan); } })
    .observe(root, { childList: true, subtree: true });
  scan();
}
