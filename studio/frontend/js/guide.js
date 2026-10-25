// The assistant's pointer: highlight one part of the page with a ring and a note.
// Parts are found by their data-guide name (e.g. "tasks.library", "task:asr", "data.add"),
// so the assistant can show where something is instead of only describing it.
import { h, ico } from './util.js';

let current = null;

/** Wait for the part to be on the page (views render after navigation), up to `ms`. */
async function find(target, ms = 4000) {
  const sel = `[data-guide="${CSS.escape(target)}"]`;
  const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    const el = document.querySelector(sel);
    if (el && el.getClientRects().length) return el;
    await new Promise(r => setTimeout(r, 120));
  }
  return null;
}

/** Set the task library's filters (the Tasks page listens for this on its filter bar). */
export async function applyFilter(f) {
  const el = await find('tasks.filters');
  if (!el) return false;
  el.dispatchEvent(new CustomEvent('guide-filter', { detail: f }));
  return true;
}

export function clearGuide() {
  current?.close();
}

export async function pointAt(target, note = '') {
  clearGuide();
  const el = await find(target);
  if (!el) return false;
  // a part taller than the screen (the task library) is shown from its top, not its middle
  const tall = el.getBoundingClientRect().height > innerHeight * 0.6;
  el.scrollIntoView({ block: tall ? 'start' : 'center', inline: 'nearest', behavior: 'smooth' });

  const ring = h('div.guide-ring', { 'aria-hidden': 'true' });
  const tip = h('div.guide-tip', { role: 'status' },
    h('span.guide-tip-icon', ico('sparkles', 14)),
    h('div.guide-tip-text', note),
    h('button.guide-tip-close', { title: 'Got it', 'aria-label': 'Close the hint', onclick: () => close() }, ico('x', 13)));
  document.body.append(ring, tip);

  const panel = document.querySelector('.asst-panel');
  let raf = 0;
  const place = () => {
    const b = el.getBoundingClientRect();
    const pad = 6;
    // clip the ring to the screen, so a tall part still reads as one outlined area
    const top = Math.max(b.top - pad, 54), bottom = Math.min(b.bottom + pad, innerHeight - 30);
    const r = { left: b.left, right: b.right, width: b.width, top: top + pad, bottom: bottom - pad };
    Object.assign(ring.style, { left: `${b.left - pad}px`, top: `${top}px`, width: `${b.width + pad * 2}px`, height: `${Math.max(0, bottom - top)}px` });
    // the note sits below the part, or above it when there is no room below
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const below = r.bottom + 14 + th < innerHeight - 30;
    const above = r.top - th - 14 > 54;
    const inside = !below && !above;  // the part fills the screen: the note goes in its bottom corner
    let x = Math.min(Math.max(12, r.left + (inside ? 12 : 0)), innerWidth - tw - 12);
    const y = below ? r.bottom + 14 : above ? r.top - th - 14 : r.bottom - th - 12;
    // keep the note and the part clear of the assistant panel
    const pr = panel && !panel.hidden ? panel.getBoundingClientRect() : null;
    const hits = (a) => pr && a.left < pr.right && a.right > pr.left && a.top < pr.bottom && a.bottom > pr.top;
    if (hits({ left: x, right: x + tw, top: y, bottom: y + th })) x = Math.max(12, Math.min(x, pr.left - tw - 12));
    panel?.classList.toggle('guide-under', !!hits(b));
    Object.assign(tip.style, { left: `${x}px`, top: `${y}px` });
    tip.classList.toggle('above', !below);
    raf = requestAnimationFrame(place);
  };
  place();

  // the hint stays until the user acts: clicks elsewhere, presses Escape, or the page changes
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const onDown = (e) => { if (!tip.contains(e.target) && !e.target.closest?.('.asst-panel')) close(); };
  const timer = setTimeout(() => close(), 20000);
  setTimeout(() => {
    document.addEventListener('keydown', onKey);
    document.addEventListener('pointerdown', onDown, true);
    window.addEventListener('hashchange', close);
  }, 300);

  function close() {
    if (current !== handle) return;
    current = null;
    cancelAnimationFrame(raf);
    clearTimeout(timer);
    panel?.classList.remove('guide-under');
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('pointerdown', onDown, true);
    window.removeEventListener('hashchange', close);
    ring.classList.add('out');
    tip.classList.add('out');
    setTimeout(() => { ring.remove(); tip.remove(); }, 220);
  }
  const handle = { close };
  current = handle;
  return true;
}
