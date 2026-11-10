// The movement from page to page (since v0.6.6). People who ask their system for reduced motion get plain page loads.
//
// Clicking one of the landing page's headers (Omni Studio, Task sharing, Subjective test, Data collection), or
// signing in:
//   1. a circle of the mode's colour covers the page from where you clicked; the clicked icon and name fly to the
//      middle and the Omni mark draws itself there, with the name of the page you are going to;
//   2. the next page opens in that same state (the mark pulses while it loads); then the mark jumps on a curve to its
//      place in the top-left corner and the page is uncovered by a circle growing from there;
//   3. as the circle passes over them, the page's parts fly from the corner's direction into their places, the small
//      ones (chips, rows, buttons) one after another; numbers count up and bars fill.
// Omni Studio opens on the Data tab of your last project (or the most recently changed one).
//
// It is loaded as the first thing in <body>, so the next page's very first frame already shows the middle state.
(() => {
  const root = document.documentElement;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const KEY = 'omni:journey';
  // the mark, drawn with pathLength 1 so it can draw itself
  const MARK = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle pathLength="1" cx="12" cy="12" r="9.5"/>'
    + '<path pathLength="1" d="M6.5 12h1.5l1.5-4 2 8 2-10 2 9 1.5-3h1"/></svg>';
  // page colour, mark colour, title colour of each mode (the same on every page, so the hand-over does not flicker)
  const PAL = { forest: ['#0c3f2b', '#f2462e', '#eee5d4'], sand: ['#eee5d4', '#f2462e', '#0c3f2b'], dark: ['#0f1012', '#6ea6ff', '#ececee'], light: ['#f4f3ee', '#0969da', '#1a1c1f'],
    navy: ['#0f1b3d', '#ff3d8b', '#ffffff'], cream: ['#f3eee3', '#d9541e', '#111111'], charcoal: ['#1e1e1c', '#6e97ff', '#d8cbb3'] };
  const palette = () => PAL[root.dataset.theme] || PAL.forest;
  const EASE = 'cubic-bezier(.65, 0, .35, 1)';
  const LAND = 'cubic-bezier(.16, 1, .3, 1)';          // fast, then settling softly into place
  const OPEN = [.3, .2, .2, 1];                        // the circle that opens the page: off quickly, then settling
  let canMask = false;
  try { CSS.registerProperty({ name: '--oj-r', syntax: '<length>', inherits: false, initialValue: '0px' }); canMask = true; }
  catch { canMask = !!window.CSS?.registerProperty; }  // already registered (a second copy of this file) still works

  const css = `
.oj { position: fixed; inset: 0; z-index: 2147483000; overflow: hidden; }
.oj-veil { position: absolute; inset: 0;
  background: radial-gradient(58% 52% at 50% 44%, color-mix(in srgb, var(--oj-a) 24%, transparent), transparent 72%), var(--oj-bg); }
.oj-veil.cover { -webkit-mask: radial-gradient(circle at var(--oj-x) var(--oj-y), #000 var(--oj-r), transparent calc(var(--oj-r) + 1px));
  mask: radial-gradient(circle at var(--oj-x) var(--oj-y), #000 var(--oj-r), transparent calc(var(--oj-r) + 1px)); }
.oj-veil.open { -webkit-mask: radial-gradient(circle at var(--oj-x) var(--oj-y), transparent var(--oj-r), #000 calc(var(--oj-r) + 1px));
  mask: radial-gradient(circle at var(--oj-x) var(--oj-y), transparent var(--oj-r), #000 calc(var(--oj-r) + 1px)); }
.oj-ring { position: absolute; left: 0; top: 0; border-radius: 50%; opacity: 0; pointer-events: none;
  box-shadow: 0 0 0 1.5px var(--oj-a), 0 0 60px 8px color-mix(in srgb, var(--oj-a) 40%, transparent); }
.oj-mx { position: absolute; left: calc(50% - 36px); top: calc(50% - 64px); width: 72px; height: 72px; }
.oj-pulse { position: absolute; inset: 0; border-radius: 50%; border: 1.5px solid var(--oj-a); opacity: 0; pointer-events: none; }
.oj-mark { position: relative; width: 72px; height: 72px; color: var(--oj-a); filter: drop-shadow(0 0 22px color-mix(in srgb, var(--oj-a) 55%, transparent)); }
.oj-mark svg { display: block; width: 100%; height: 100%; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round;
  stroke-linejoin: round; stroke-dasharray: 1; stroke-dashoffset: 0; }
.oj-tx { position: absolute; left: 0; right: 0; top: calc(50% + 22px); text-align: center; pointer-events: none; }
.oj-title { display: inline-block; font: 700 28px/1.15 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif; letter-spacing: -.02em;
  color: var(--oj-t); white-space: nowrap; }
.oj-fly { position: absolute; margin: 0; pointer-events: none; transform-origin: 50% 50%; white-space: nowrap; }
.oj-fly svg { display: block; width: 100%; height: 100%; }`;

  let layer = null, phase = '', held = false, born = 0, busy = false, layerPal = null, idle = [];
  const skip = (vt) => { if (!vt) return; vt.ready.catch(() => {}); vt.skipTransition(); };

  function build(title, pal) {
    if (!document.getElementById('oj-style')) {
      const st = document.createElement('style'); st.id = 'oj-style'; st.textContent = css; document.head.append(st);
    }
    const L = document.createElement('div');
    L.className = 'oj'; L.setAttribute('aria-hidden', 'true');
    L.style.setProperty('--oj-bg', pal[0]); L.style.setProperty('--oj-a', pal[1]); L.style.setProperty('--oj-t', pal[2]);
    L.innerHTML = '<div class="oj-veil"></div><div class="oj-ring"></div>'
      + `<div class="oj-mx"><i class="oj-pulse"></i><i class="oj-pulse"></i><div class="oj-mark">${MARK}</div></div>`
      + '<div class="oj-tx"><span class="oj-title"></span></div>';
    L.querySelector('.oj-title').textContent = title;
    (document.body || root).append(L);
    layerPal = pal;
    return L;
  }
  const $ = (s, r = layer) => r.querySelector(s);
  const center = (r) => [r.left + r.width / 2, r.top + r.height / 2];
  const reach = (x, y) => Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y)) + 60;
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 6 && r.height > 6 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
  // when the opening circle (eased by OPEN) has grown to a fraction f of its size, as a fraction of its time
  function timeAt(f) {
    const [x1, y1, x2, y2] = OPEN;
    const bz = (a, b, s) => 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3;
    for (let s = 0; s <= 1; s += .02) if (bz(y1, y2, s) >= f) return bz(x1, x2, s);
    return 1;
  }

  // while the next page loads, the mark breathes and soft rings leave it, so the wait reads as part of the movement
  function breathe() {
    const mk = $('.oj-mark');
    idle.push(mk.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.07)' }], { duration: 900, iterations: Infinity, direction: 'alternate', easing: 'ease-in-out' }));
    layer.querySelectorAll('.oj-pulse').forEach((p, i) => idle.push(p.animate(
      [{ transform: 'scale(1)', opacity: .55 }, { transform: 'scale(2.6)', opacity: 0 }],
      { duration: 1600, delay: i * 800, iterations: Infinity, easing: 'cubic-bezier(.2, .6, .3, 1)' })));
  }

  // =====================================================================
  // arriving: the page was opened by the movement (or is about to be passed on to the next page)
  // =====================================================================
  let flag = null, want = '';
  try { flag = JSON.parse(sessionStorage.getItem(KEY) || 'null'); sessionStorage.removeItem(KEY); } catch { flag = null; }
  if (flag && Date.now() - flag.t < 12000) {
    layer = build(flag.title || 'Omni Studio', flag.pal || palette());
    phase = 'wait'; born = performance.now();
    setTimeout(() => { if (phase === 'wait') breathe(); }, 260);
    // the browser's own page fade would blur the middle state; this movement replaces it
    addEventListener('pagereveal', (e) => { if (layer) skip(e.viewTransition); });
    // Omni Studio opens on the Data tab: of the project in the address, else the last one, else the latest changed
    if (flag.title === 'Omni Studio' && location.pathname === '/' && new URLSearchParams(location.search).has('app')) {
      want = '.data';
      const pidOf = (h) => (/^#\/p\/([^/?]+)/.exec(h || '') || [])[1];
      let last = '';
      try { last = JSON.parse(localStorage.getItem('omni:last') || '""'); } catch { /* none */ }
      const pid = pidOf(location.hash) || pidOf(last);
      if (pid) history.replaceState(null, '', '#/p/' + pid);  // before the Studio reads the address
      else {
        fetch('/api/projects').then(r => r.json()).then(list => {
          const p = [...list].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0];
          if (p) location.replace('#/p/' + encodeURIComponent(p.id)); else want = '';
        }).catch(() => { want = ''; });
      }
    }
    const auto = () => { if (!held) ready(go); };
    if (document.readyState === 'loading') addEventListener('DOMContentLoaded', auto); else auto();
    setTimeout(() => { if (phase === 'wait') go(); }, 6000);  // never stay covered
  }
  addEventListener('pageswap', (e) => { if (busy) skip(e.viewTransition); });
  addEventListener('pageshow', (e) => {  // back to a page that was left with the movement: uncover it
    if (e.persisted && busy) { layer?.remove(); layer = null; busy = false; phase = ''; }
  });

  // the top-left brand of this page: its mark and its name
  function brand() {
    const b = document.querySelector('header .brand, .topbar .brand');
    const mark = b?.querySelector('svg');
    if (!b || !mark || !shown(b)) return null;
    const words = [...b.children].filter(c => !c.contains(mark) && shown(c));
    return { b, mark, words };
  }
  // ready when the brand is drawn and the page has its content (the Studio builds its view after loading data)
  function ready(cb) {
    const t0 = performance.now();
    let seen = 0;
    const tick = () => {
      const main = document.querySelector('main');
      const full = main && main.getElementsByTagName('*').length > 14;
      const target = !want || document.querySelector(want);
      if (target && !seen) seen = performance.now();
      const settled = !want || (seen && performance.now() - seen > 140);  // a moment for the view to fill in
      if ((brand() && full && target && settled && performance.now() - born > 180) || performance.now() - t0 > 5000) {
        Promise.race([document.fonts?.ready, new Promise(r => setTimeout(r, 300))]).then(() => requestAnimationFrame(cb));
      } else requestAnimationFrame(tick);
    };
    tick();
  }

  // ---------- the parts of the page, as the movement brings them in
  // Big containers are opened up so that their contents come in one by one: grids and lists of alike items
  // (cards, chips, rows) always, other containers when they are large. The frames of opened containers stay where
  // they are, like an empty layout that fills.
  const sig = (el) => el.tagName + '.' + (typeof el.className === 'string' ? el.className.split(/\s+/)[0] : '');
  function parts() {
    const out = [], seq = new Map();
    const keep = (el) => el.matches('nav, .rail, canvas, svg, video, img, .wave, .asst-fab');  // never opened up
    const take = (el, depth, i) => {
      if (out.length >= 110 || !shown(el) || el.matches('[aria-hidden="true"], i, script, style, .brand')) return;
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.visibility === 'hidden') return;
      const kids = [...el.children].filter(k => shown(k) && !k.matches('[aria-hidden="true"], i, script, style'));
      if (!keep(el) && kids.length === 1 && depth < 10) return take(kids[0], depth + 1, i);
      const r = el.getBoundingClientRect();
      const grid = /grid/.test(cs.display) || (/flex/.test(cs.display) && cs.flexWrap === 'wrap');
      const alike = kids.length >= 3 && kids.filter(k => sig(k) === sig(kids[0])).length >= kids.length * .7;
      const big = r.height > 150 || r.width > 520;
      if (!keep(el) && depth < 9 && kids.length >= 2 && kids.length <= 80 && (grid || alike || big)) {
        kids.forEach((k, j) => take(k, depth + 1, grid || alike ? j : 0));
        return;
      }
      out.push(el); seq.set(el, i);
    };
    const head = document.querySelector('header');
    if (head) take(head, 0, 0);
    const main = document.querySelector('main');
    if (main) take(main, 0, 0);
    return { list: out, seq };
  }
  // numbers count up ("16", "1,695 items", "3:21") and thin bars fill, inside a part
  let counted = 0;
  function lively(el, delay) {
    const leaves = el.children.length ? el.querySelectorAll('*') : [el];
    for (const leaf of leaves) {
      if (counted > 50) break;
      if (leaf.children.length || leaf.closest('input, textarea, select, button.btn')) continue;
      const tn = leaf.firstChild;
      if (!tn || tn.nodeType !== 3) continue;
      let m = /^(\s*)(\d{1,3}(?:,\d{3})+|\d{2,5})((?:\s+[A-Za-z][^]*)?\s*)$/.exec(tn.data);
      let fmt, end;
      if (m) {
        end = Number(m[2].replace(/,/g, ''));
        const comma = m[2].includes(',');
        fmt = (v) => m[1] + (comma ? v.toLocaleString('en-US') : String(v)) + m[3];
      } else if ((m = /^(\s*)(\d{1,2}):(\d{2})(\s*)$/.exec(tn.data))) {
        end = Number(m[2]) * 60 + Number(m[3]);
        fmt = (v) => `${m[1]}${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}${m[4]}`;
      } else continue;
      if (!end) continue;
      counted++;
      const t0 = performance.now() + delay, dur = 1000, final = tn.data;
      tn.data = fmt(0);
      const step = (now) => {
        const k = Math.min(1, Math.max(0, (now - t0) / dur));
        tn.data = k >= 1 ? final : fmt(Math.round(end * (1 - Math.pow(1 - k, 3))));
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
    for (const bar of el.querySelectorAll('[style*="width"]')) {
      const r = bar.getBoundingClientRect();
      if (r.height > 12 || r.width < 4 || !/%/.test(bar.style.width)) continue;
      bar.animate([{ transform: 'scaleX(0)', transformOrigin: '0 50%' }, { transform: 'scaleX(1)', transformOrigin: '0 50%' }],
        { duration: 1000, delay: delay + 120, easing: 'cubic-bezier(.2, .8, .2, 1)', fill: 'backwards' });
    }
  }
  // each part flies in from the corner's direction as the circle reaches it; small parts pop in one after another
  function bringIn(ox, oy, R0, RD, R) {
    const { list, seq } = parts();
    for (const el of list) {
      const r = el.getBoundingClientRect();
      const [px, py] = center(r);
      const vx = px - ox, vy = py - oy, d = Math.hypot(vx, vy) || 1;
      const small = r.width * r.height < 12000;
      const pull = small ? Math.min(44, d * .1) : Math.min(150, d * .2);   // how far back toward the corner it starts
      const at = R0 + RD * timeAt(Math.min(1, d / R)) - 60;               // when the circle passes over it
      const delay = Math.max(R0, at) + Math.min(seq.get(el) || 0, 18) * 24;
      const from = `translate(${(-vx / d * pull).toFixed(1)}px, ${(-vy / d * pull).toFixed(1)}px) scale(${small ? .78 : .9})`;
      el.animate(small
        ? [{ opacity: 0, transform: from }, { opacity: 1, transform: 'none' }]
        : [{ opacity: 0, transform: from, filter: 'blur(8px)' }, { opacity: 1, transform: 'none', filter: 'blur(0)' }],
      { duration: small ? 620 : 900, delay, easing: LAND, fill: 'backwards' });
      lively(el, delay);
    }
  }

  function go() {
    if (phase !== 'wait' || !layer) return;
    phase = 'go';
    const to = brand();
    const veil = $('.oj-veil'), ring = $('.oj-ring'), mx = $('.oj-mx'), mk = $('.oj-mark'), title = $('.oj-title');
    const start = getComputedStyle(mk).transform;      // where the breathing left it
    idle.forEach(a => a.cancel()); idle = [];
    const D = 620;                       // the jump to the corner
    const R0 = D - 110, RD = 860;        // the circle that opens the page starts as the mark lands
    const OPEN_CSS = `cubic-bezier(${OPEN.join(', ')})`;
    let ox = 34, oy = 30;
    if (to) {
      const mr = mk.getBoundingClientRect(), tr = to.mark.getBoundingClientRect();
      const [ax, ay] = center(mr), [bx, by] = center(tr);
      [ox, oy] = [bx, by];
      const s = tr.width / 72;
      // x and y move with different timing, so the mark travels on a curve, lifting a little before it jumps
      mx.animate([{ transform: 'translateX(0)' }, { transform: `translateX(${bx - ax}px)` }],
        { duration: D, easing: 'cubic-bezier(.55, 0, .25, 1)', fill: 'forwards' });
      mk.animate([{ transform: start === 'none' ? 'translateY(0) scale(1)' : start }, { transform: 'translateY(-16px) scale(1.08)', offset: .22 },
        { transform: `translateY(${by - ay}px) scale(${s})` }], { duration: D, easing: 'cubic-bezier(.4, 0, .2, 1)', fill: 'forwards' });
      mk.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 240, delay: R0 + 240, fill: 'forwards' });
      // the name flies to the brand's name and hands over to it
      const words = to.words;
      if (words.length) {
        const rs = words.map(w => w.getBoundingClientRect());
        const wr = { left: Math.min(...rs.map(r => r.left)), right: Math.max(...rs.map(r => r.right)),
          top: Math.min(...rs.map(r => r.top)), bottom: Math.max(...rs.map(r => r.bottom)) };
        const tr2 = title.getBoundingClientRect();
        const ts = (wr.bottom - wr.top) / tr2.height;
        const [cx, cy] = center(tr2);
        const dx = (wr.left + wr.right) / 2 - cx, dy = (wr.top + wr.bottom) / 2 - cy;
        title.animate([{ transform: 'translate(0, 0) scale(1)' }, { transform: 'translate(0, -12px) scale(1.03)', offset: .22 },
          { transform: `translate(${dx}px, ${dy}px) scale(${ts})` }], { duration: D, easing: 'cubic-bezier(.45, 0, .2, 1)', fill: 'forwards' });
        title.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 240, delay: R0 + 240, fill: 'forwards' });
      } else title.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, fill: 'forwards' });
      to.b.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, delay: R0 + 160, fill: 'backwards' });
    } else {
      mk.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(.6)' }], { duration: 420, easing: EASE, fill: 'forwards' });
      title.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, fill: 'forwards' });
    }
    // the page opens in a circle from where the mark landed, with a soft ring on its edge
    const R = reach(ox, oy);
    if (canMask) {
      veil.style.setProperty('--oj-x', ox + 'px'); veil.style.setProperty('--oj-y', oy + 'px');
      veil.classList.add('open');
      veil.animate([{ '--oj-r': '0px' }, { '--oj-r': R + 'px' }], { duration: RD, delay: R0, easing: OPEN_CSS, fill: 'both' });
    } else veil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: RD, delay: R0, easing: EASE, fill: 'both' });
    Object.assign(ring.style, { width: R * 2 + 'px', height: R * 2 + 'px', left: ox - R + 'px', top: oy - R + 'px' });
    ring.animate([{ transform: 'scale(0)', opacity: 0 }, { opacity: .9, offset: .12 }, { transform: 'scale(1)', opacity: 0 }],
      { duration: RD, delay: R0, easing: OPEN_CSS, fill: 'both' });
    bringIn(ox, oy, R0, RD, R);
    setTimeout(() => { layer?.remove(); layer = null; phase = ''; }, R0 + RD + 60);
  }

  // =====================================================================
  // leaving: cover the page from where it was clicked and settle the mark and the name in the middle
  // =====================================================================
  function leave({ url, title, from, label, point, replace = false, direct = null }) {
    if (busy || layer) return false;
    busy = true;
    const pal = palette();
    layer = build(title, pal);
    phase = 'leave';
    const veil = $('.oj-veil'), ring = $('.oj-ring'), mk = $('.oj-mark'), ttl = $('.oj-title');
    const fr = from?.getBoundingClientRect();
    const [ox, oy] = point && (point[0] || point[1]) ? point : fr ? center(fr) : [innerWidth / 2, innerHeight / 2];
    const R = reach(ox, oy), C = 560;
    if (canMask) {
      veil.style.setProperty('--oj-x', ox + 'px'); veil.style.setProperty('--oj-y', oy + 'px');
      veil.classList.add('cover');
      veil.animate([{ '--oj-r': '0px' }, { '--oj-r': R + 'px' }], { duration: C, easing: EASE, fill: 'both' });
    } else veil.animate([{ opacity: 0 }, { opacity: 1 }], { duration: C, easing: EASE, fill: 'both' });
    Object.assign(ring.style, { width: R * 2 + 'px', height: R * 2 + 'px', left: ox - R + 'px', top: oy - R + 'px' });
    ring.animate([{ transform: 'scale(0)', opacity: 0 }, { opacity: .8, offset: .15 }, { transform: 'scale(1)', opacity: 0 }],
      { duration: C, easing: EASE, fill: 'both' });
    // the clicked icon flies to the middle and turns into the mark, which draws itself
    const mr = mk.getBoundingClientRect();
    if (from && fr) {
      const fly = document.createElement('div');
      fly.className = 'oj-fly';
      const svg = from.cloneNode(true), fs = getComputedStyle(from);
      // the copy leaves the page's style sheet behind: keep its line drawing
      for (const k of ['fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin']) svg.style.setProperty(k, fs.getPropertyValue(k));
      fly.append(svg);
      Object.assign(fly.style, { left: fr.left + 'px', top: fr.top + 'px', width: fr.width + 'px', height: fr.height + 'px', color: fs.color });
      layer.append(fly);
      const [ax, ay] = center(fr), [bx, by] = center(mr);
      fly.animate([{ transform: 'translate(0, 0) scale(1)', opacity: 1 }, { transform: `translate(${bx - ax}px, ${by - ay}px) scale(${mr.width / fr.width})`, opacity: 1, offset: .7 },
        { transform: `translate(${bx - ax}px, ${by - ay}px) scale(${mr.width / fr.width})`, opacity: 0 }], { duration: 640, easing: 'cubic-bezier(.5, 0, .2, 1)', fill: 'forwards' });
    }
    mk.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 260, delay: 380, fill: 'both' });
    for (const p of mk.querySelectorAll('circle, path')) {
      p.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 520, delay: 360, easing: 'cubic-bezier(.4, 0, .2, 1)', fill: 'both' });
    }
    // the clicked header's words fly to the middle and become the name
    const tr = ttl.getBoundingClientRect();
    const text = label && [...label.childNodes].reverse().find(n => n.nodeType === 3 && n.data.trim());
    if (text) {
      const rg = document.createRange(); rg.selectNode(text);
      const lr = rg.getBoundingClientRect(), cs = getComputedStyle(label);
      const w = document.createElement('div');
      w.className = 'oj-fly'; w.textContent = text.data.trim();
      Object.assign(w.style, { left: lr.left + 'px', top: lr.top + 'px', font: cs.font, letterSpacing: cs.letterSpacing, color: cs.color, lineHeight: lr.height + 'px' });
      layer.append(w);
      const [ax, ay] = center(lr), [bx, by] = center(tr);
      w.animate([{ transform: 'translate(0, 0) scale(1)', opacity: 1 }, { transform: `translate(${bx - ax}px, ${by - ay}px) scale(${tr.height / lr.height})`, opacity: 1, offset: .7 },
        { transform: `translate(${bx - ax}px, ${by - ay}px) scale(${tr.height / lr.height})`, opacity: 0 }], { duration: 660, easing: 'cubic-bezier(.5, 0, .2, 1)', fill: 'forwards' });
    }
    ttl.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 320, delay: text ? 420 : 340, easing: 'cubic-bezier(.2, .8, .2, 1)', fill: 'both' });
    // go on once the middle state is drawn; when the sign-in page would only pass us on, go straight to the app
    const wait = new Promise(r => setTimeout(r, 740));
    Promise.all([wait, direct ? Promise.race([direct, new Promise(r => setTimeout(r, 900, null))]) : null]).then(([, alt]) => {
      try { sessionStorage.setItem(KEY, JSON.stringify({ title, pal, t: Date.now() })); } catch { /* the next page opens plainly */ }
      const to = alt || url;
      if (replace) location.replace(to); else location.href = to;
    });
    return true;
  }

  // ---------- the landing page's headers and buttons that open a page of the site
  const NEXT = { studio: 'Omni Studio', chat: 'Chat', share: 'Task sharing', listen: 'Subjective tests', collect: 'Data collection' };
  function titleFor(u) {
    const p = u.pathname;
    if (/^\/tasks\/?$/.test(p)) return 'Task sharing';
    if (/^\/listen\/?$/.test(p)) return 'Subjective tests';
    if (/^\/collect\/?$/.test(p)) return 'Data collection';
    if (/^\/login\/?$/.test(p)) return NEXT[u.searchParams.get('next')] || 'Omni Studio';
    if (p === '/' && u.searchParams.has('app')) return 'Omni Studio';
    return '';
  }
  if (/^\/landing\/?$/.test(location.pathname)) {
    document.addEventListener('click', (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target.closest?.('a[href]');
      if (!a || a.target === '_blank') return;
      const u = new URL(a.href, location.href);
      const title = u.origin === location.origin && titleFor(u);
      if (!title) return;
      // the Studio through the sign-in page: if that page would only pass us on (signed in, or sign-in is off),
      // ask now, during the movement, and skip it
      let direct = null;
      if (/^\/login\/?$/.test(u.pathname) && (u.searchParams.get('next') || 'studio') === 'studio') {
        direct = fetch('/api/auth/me').then(r => (r.ok ? r.json() : null))
          .then(d => (d && (d.session || d.auth === false) ? '/?app' : null)).catch(() => null);
      }
      const nav = a.closest('#nav-links');
      const from = (nav && a.querySelector('svg')) || document.querySelector('header .brand svg');
      if (leave({ url: u.href, title, from, label: nav ? a : null, point: [e.clientX, e.clientY], direct })) e.preventDefault();
    });
  }

  // ---------- for the sign-in page: wait for its decision, then open it, pass on, or leave after signing in
  window.omniJourney = {
    get active() { return phase === 'wait'; },
    hold() { held = true; },
    arrive() { if (phase === 'wait') ready(go); },
    pass(url, title) {  // signed in already: keep the middle state and go straight on
      try { sessionStorage.setItem(KEY, JSON.stringify({ title: title || $('.oj-title').textContent, pal: layerPal || palette(), t: Date.now() })); } catch { /* plain */ }
      phase = 'pass'; busy = true;
      location.replace(url);
    },
    leave,
  };
})();
