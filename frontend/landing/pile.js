// Task pile: every annotation task of Omni Studio as a pill that drops into a box, bounces and stacks.
// Grab a pill and throw it, click one to make it jump, tap the floor to bump the ones nearby, or use
// the two buttons (throw them all up, drop them again). A small rigid-body simulation written for this
// page: each pill is a capsule (a line segment with a radius); contacts are solved with impulses,
// friction and a little bounce, and pills that come to rest go to sleep so the page stays idle.
// No library. With "reduce motion" the pile is settled at once and stays still.
(() => {
  const root = document.querySelector('[data-pile]');
  if (!root) return;
  const floor = root.querySelector('.pile-floor');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The 44 built-in tasks (config/tasks.json), grouped as in the studio.
  const GROUPS = [
    ['speech', 'Speech & language', ['Speech activity', 'Speech recognition', 'Forced alignment', 'Phonemes', 'Language ID',
      'Speech translation', 'Named entities', 'Sentiment', 'Topic']],
    ['speaker', 'Speaker', ['Diarization', 'Pure speech', 'Overlapped speech', 'Speaker gender', 'Speaker age', 'Accent']],
    ['para', 'Paralinguistics', ['Speech emotion', 'Speaking style', 'Non-verbal sounds', 'Audio quality', 'MOS']],
    ['audio', 'Audio scene', ['Sound events', 'Acoustic scene']],
    ['vision', 'Vision', ['Facial expression', 'AV emotion', 'Active speaker', 'Actions', 'Shot boundaries', 'OCR',
      'Image tags', 'Face boxes', 'Person boxes', 'Object boxes', 'Pose (17 points)']],
    ['spoof', 'Spoof & deepfake', ['Audio spoof', 'Deepfake video', 'AI-generated image', 'Fake regions']],
    ['caption', 'Captioning', ['Audio caption', 'Voice caption', 'Video caption', 'Dense captions', 'Image caption',
      'Detailed description', 'Face description']],
  ];
  const TAGS = GROUPS.flatMap(([cat, , names]) => names.map(label => ({ cat, label })));

  // ---------- page parts: legend, a list for screen readers, the pills
  const key = root.querySelector('.pile-key');
  if (key) key.replaceChildren(...GROUPS.map(([cat, name]) => Object.assign(document.createElement('li'), { className: 'c-' + cat, textContent: name })));
  const list = document.createElement('ul');
  list.className = 'sr-only';
  list.append(...GROUPS.map(([, name, names]) => Object.assign(document.createElement('li'), { textContent: `${name}: ${names.join(', ')}` })));
  root.append(list);
  const els = TAGS.map(t => {
    const el = document.createElement('div');
    el.className = 'ptag c-' + t.cat;
    el.textContent = t.label;
    el.setAttribute('aria-hidden', 'true');   // the list above carries the content; the pills are a toy
    floor.append(el);
    return el;
  });

  // ---------- simulation constants (pixels, seconds)
  const STEP = 1 / 60, SUB = 4, ITERS = 8;
  const GRAVITY = 2600, BOUNCE = 0.18, FRICTION = 0.7, SLOP = 0.5, PUSH_BACK = 0.4;
  const REST_DRIFT = 2, SLEEP_STEPS = 30;   // asleep after half a second within 2 px of one spot
  let W = 1, H = 1, time = 0, zTop = 1;
  const bodies = TAGS.map((t, i) => ({ i, el: els[i], live: false, x: 0, y: 0, a: 0, vx: 0, vy: 0, av: 0,
    r: 10, hl: 0, w: 20, h: 20, im: 1, iI: 1, sleep: false, still: 0, e: [0, 0, 0, 0],
    sq: 0, sqv: 0, s: 1, sv: 0, z: 0, px: 0, py: 0, pa: 0, rx: 0, ry: 0, ra: 0 }));
  let queue = [];

  function fit(b) {
    const w = b.el.offsetWidth, h = b.el.offsetHeight;
    if (!w || !h) return;
    b.w = w; b.h = h; b.r = h / 2; b.hl = Math.max(0, w / 2 - b.r);
    const m = (4 * b.hl * b.r + Math.PI * b.r * b.r) * 0.001;   // mass from the pill's area
    b.im = 1 / m;
    b.iI = 12 / (m * (w * w + h * h));                          // a rectangle's moment of inertia
  }
  function ends(b) {
    const c = Math.cos(b.a) * b.hl, s = Math.sin(b.a) * b.hl;
    b.e[0] = b.x - c; b.e[1] = b.y - s; b.e[2] = b.x + c; b.e[3] = b.y + s;
  }
  function closest(px, py, ax, ay, bx, by, out) {
    const dx = bx - ax, dy = by - ay, l = dx * dx + dy * dy;
    let t = l > 1e-9 ? ((px - ax) * dx + (py - ay) * dy) / l : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    out[0] = ax + dx * t; out[1] = ay + dy * t;
  }
  const tmp = [0, 0], tmp2 = [0, 0];

  // ---------- contacts: n points from A to B (or into the wall); vx/vy: speed of a moving obstacle (the cursor)
  const pool = [];
  let nc = 0;
  function add(A, B, nx, ny, px, py, pen, kvx = 0, kvy = 0) {
    const c = pool[nc] || (pool[nc] = {});
    nc++;
    c.A = A; c.B = B; c.nx = nx; c.ny = ny; c.px = px; c.py = py; c.pen = pen; c.kvx = kvx; c.kvy = kvy;
    c.jn = 0; c.jt = 0; c.wt = 1; c.target = null;
  }
  function capsules(A, B) {
    const rr = A.r + B.r, start = nc;
    for (let side = 0; side < 2; side++) {          // the ends of one pill against the other pill's line
      const P = side ? B : A, Q = side ? A : B;
      for (let k = 0; k < 4; k += 2) {
        if (k === 2 && P.hl < 0.5) break;
        const px = P.e[k], py = P.e[k + 1];
        closest(px, py, Q.e[0], Q.e[1], Q.e[2], Q.e[3], tmp);
        let dx = tmp[0] - px, dy = tmp[1] - py;
        const d = Math.hypot(dx, dy);
        if (d >= rr || d < 1e-4) continue;
        dx /= d; dy /= d;
        const sx = side ? -dx : dx, sy = side ? -dy : dy;   // keep the normal pointing from A to B
        add(A, B, sx, sy, px + dx * P.r, py + dy * P.r, rr - d);
      }
    }
    if (nc === start) {                              // crossing pills: nearest points of the two lines
      const [a1, b1, a2, b2] = A.e, [c1, d1, c2, d2] = B.e;
      let best = Infinity, bx = 0, by = 0, cx = 0, cy = 0;
      for (const [px, py] of [[a1, b1], [a2, b2], [(a1 + a2) / 2, (b1 + b2) / 2]]) {
        closest(px, py, c1, d1, c2, d2, tmp);
        closest(tmp[0], tmp[1], a1, b1, a2, b2, tmp2);
        const d = Math.hypot(tmp[0] - tmp2[0], tmp[1] - tmp2[1]);
        if (d < best) { best = d; bx = tmp2[0]; by = tmp2[1]; cx = tmp[0]; cy = tmp[1]; }
      }
      if (best < rr) {
        let nx = cx - bx, ny = cy - by;
        if (best < 1e-4) { nx = B.x - A.x; ny = B.y - A.y; }
        const l = Math.hypot(nx, ny) || 1;
        nx /= l; ny /= l;
        add(A, B, nx, ny, bx + nx * A.r, by + ny * A.r, rr - best);
      }
    }
    const n = nc - start;
    if (n > 1) for (let k = start; k < nc; k++) pool[k].wt = 1 / n;
  }
  function collect() {
    nc = 0;
    const L = bodies;
    for (const b of L) if (b.live) ends(b);
    for (const A of L) {
      if (!A.live) continue;
      for (let k = 0; k < 4; k += 2) {
        const x = A.e[k], y = A.e[k + 1];
        let pen = y + A.r - H;
        if (pen > 0) add(A, null, 0, 1, x, H, pen);
        pen = A.r - x;
        if (pen > 0) add(A, null, -1, 0, 0, y, pen);
        pen = x + A.r - W;
        if (pen > 0) add(A, null, 1, 0, W, y, pen);
      }
    }
    for (let i = 0; i < L.length; i++) {
      const A = L[i];
      if (!A.live) continue;
      const ra = A.hl + A.r;
      for (let j = i + 1; j < L.length; j++) {
        const B = L[j];
        if (!B.live || (A.sleep && B.sleep)) continue;
        const reach = ra + B.hl + B.r;
        if (Math.abs(A.x - B.x) > reach || Math.abs(A.y - B.y) > reach) continue;
        capsules(A, B);
      }
    }
    if (cursor.on && Math.hypot(cursor.vx, cursor.vy) > 250) {   // a fast-moving cursor shoves pills aside
      for (const A of L) {
        if (!A.live || (grip && grip.b === A)) continue;
        closest(cursor.x, cursor.y, A.e[0], A.e[1], A.e[2], A.e[3], tmp);
        const dx = cursor.x - tmp[0], dy = cursor.y - tmp[1], d = Math.hypot(dx, dy), rr = A.r + 40;
        if (d >= rr || d < 1e-4) continue;
        add(A, null, dx / d, dy / d, tmp[0] + dx / d * A.r, tmp[1] + dy / d * A.r, rr - d, cursor.vx, cursor.vy);
      }
    }
  }

  // ---------- solver
  const wake = (b) => { if (b.sleep) { b.sleep = false; b.still = 0; b.rx = b.x; b.ry = b.y; b.ra = b.a; } };
  function solve(c) {
    const A = c.A, B = c.B;
    const imA = A.sleep ? 0 : A.im, iIA = A.sleep ? 0 : A.iI;
    const imB = B && !B.sleep ? B.im : 0, iIB = B && !B.sleep ? B.iI : 0;
    if (imA + imB === 0 && !c.kvx && !c.kvy) return;
    const rax = c.px - A.x, ray = c.py - A.y;
    const rbx = B ? c.px - B.x : 0, rby = B ? c.py - B.y : 0;
    const vel = () => {
      const vax = A.vx - A.av * ray, vay = A.vy + A.av * rax;
      const vbx = B ? B.vx - B.av * rby : c.kvx, vby = B ? B.vy + B.av * rbx : c.kvy;
      return [vbx - vax, vby - vay];
    };
    let [rvx, rvy] = vel();
    const vn = rvx * c.nx + rvy * c.ny;
    if (c.target == null) {                          // first pass: decide the bounce, wake sleepers on a real hit
      c.target = vn < -220 ? -BOUNCE * vn : 0;
      if (vn < -60) { wake(A); if (B) wake(B); }
      if (vn < -380) { kick(A, Math.min(1, -vn / 2200)); if (B) kick(B, Math.min(1, -vn / 2200)); }
    }
    const apply = (px, py) => {
      A.vx -= px * imA; A.vy -= py * imA; A.av -= (rax * py - ray * px) * iIA;
      if (B) { B.vx += px * imB; B.vy += py * imB; B.av += (rbx * py - rby * px) * iIB; }
    };
    const rnA = rax * c.ny - ray * c.nx, rnB = rbx * c.ny - rby * c.nx;
    const kn = imA + imB + rnA * rnA * iIA + rnB * rnB * iIB;
    if (kn <= 0) return;
    let jn = (c.target - vn) / kn;
    const acc = Math.max(0, c.jn + jn);
    jn = acc - c.jn; c.jn = acc;
    apply(jn * c.nx, jn * c.ny);
    // friction along the surface, limited by how hard the pills press together
    [rvx, rvy] = vel();
    const tx = -c.ny, ty = c.nx, vt = rvx * tx + rvy * ty;
    const rtA = rax * ty - ray * tx, rtB = rbx * ty - rby * tx;
    const kt = imA + imB + rtA * rtA * iIA + rtB * rtB * iIB;
    let jt = -vt / kt;
    const lim = (Math.abs(vt) < 20 ? 1.3 : 1) * FRICTION * c.jn, at = Math.max(-lim, Math.min(lim, c.jt + jt));
    jt = at - c.jt; c.jt = at;
    apply(jt * tx, jt * ty);
  }
  function correct() {                               // push overlapping pills apart (position only)
    for (let k = 0; k < nc; k++) {
      const c = pool[k];
      if (c.pen <= SLOP) continue;
      const A = c.A, B = c.B, ia = A.sleep ? 0 : A.im, ib = B && !B.sleep ? B.im : 0;
      if (ia + ib === 0) continue;
      const m = (c.pen - SLOP) * PUSH_BACK * c.wt / (ia + ib);
      A.x -= c.nx * m * ia; A.y -= c.ny * m * ia;
      if (B) { B.x += c.nx * m * ib; B.y += c.ny * m * ib; }
    }
  }

  // ---------- one fixed time step
  function step() {
    time += STEP;
    while (queue.length && queue[0].at <= time) spawn(queue.shift().b);
    if (cursor.on && performance.now() - cursor.t > 60) { cursor.vx *= 0.5; cursor.vy *= 0.5; }
    for (const b of bodies) { b.px = b.x; b.py = b.y; b.pa = b.a; }
    const h = STEP / SUB, damp = 1 - 0.4 * h, spin = 1 - 1.6 * h;
    for (let s = 0; s < SUB; s++) {
      for (const b of bodies) {
        if (!b.live || b.sleep) continue;
        b.vy += GRAVITY * h; b.vx *= damp; b.vy *= damp; b.av *= spin;
      }
      if (grip) pull(h);
      collect();
      for (let it = 0; it < ITERS; it++) for (let k = 0; k < nc; k++) solve(pool[k]);
      for (const b of bodies) {
        if (!b.live || b.sleep) continue;
        const sp = Math.hypot(b.vx, b.vy);
        if (sp > 4200) { b.vx *= 4200 / sp; b.vy *= 4200 / sp; }
        b.av = Math.max(-28, Math.min(28, b.av));
        b.x += b.vx * h; b.y += b.vy * h; b.a += b.av * h;
        if (b.y > H + 300 || b.x < -300 || b.x > W + 300) spawn(b);
      }
      collect();
      correct();
    }
    // slow pills lose speed faster, so a pile stops spreading instead of creeping like sand
    for (const b of bodies) {
      if (!b.live || b.sleep || (grip && grip.b === b)) continue;
      if (Math.hypot(b.vx, b.vy) < 60) { b.vx *= 0.8; b.av *= 0.8; if (b.vy < 0) b.vy *= 0.8; }
    }
    // rest: a pill that has barely moved for a while sleeps until something touches it
    let awake = 0;
    for (const b of bodies) {
      if (!b.live || b.sleep) continue;
      const v = (Math.hypot(b.x - b.px, b.y - b.py) + Math.abs(b.a - b.pa) * (b.hl + b.r)) / STEP;
      // jitter in place does not count as moving: only drift from where the pill settled
      const drift = Math.max(Math.hypot(b.x - b.rx, b.y - b.ry), Math.abs(b.a - b.ra) * (b.hl + b.r));
      if (drift > REST_DRIFT || (grip && grip.b === b) || b.y < 0) { b.rx = b.x; b.ry = b.y; b.ra = b.a; b.still = 0; } else b.still++;
      if (b.still > SLEEP_STEPS) { b.sleep = true; b.vx = b.vy = b.av = 0; continue; }
      awake++;
      if (v > 80) for (const o of bodies) {          // a moving pill wakes the sleeping ones it passes
        if (!o.sleep) continue;
        const reach = b.hl + b.r + o.hl + o.r + 24;
        if (Math.abs(o.x - b.x) < reach && Math.abs(o.y - b.y) < reach) wake(o);
      }
    }
    return awake;
  }

  function spawn(b) {
    const ext = b.hl + b.r, lo = Math.max(ext, W * 0.1 + ext), hi = Math.min(W - ext, W * 0.9 - ext);
    let x = W / 2, y = -(b.h + 20 + Math.random() * 80);
    for (let t = 0; t < 12; t++) {                   // not on top of another pill that is still falling in
      x = hi > lo ? lo + Math.random() * (hi - lo) : W / 2;
      if (!bodies.some(o => o !== b && o.live && o.y < 40 && Math.abs(o.x - x) < ext + o.hl + o.r && Math.abs(o.y - y) < b.h + 10)) break;
      if (t === 11) y -= b.h * 2;
    }
    Object.assign(b, { x, y, a: (Math.random() - 0.5) * 1.2, vx: (Math.random() - 0.5) * 140, vy: 60 + Math.random() * 120,
      av: (Math.random() - 0.5) * 3.5, live: true, sleep: false, still: 0, z: ++zTop });
    b.el.style.visibility = 'visible';
    b.el.style.zIndex = b.z;
  }
  function drop() {
    const order = bodies.map((b, i) => i).sort(() => Math.random() - 0.5);
    for (const b of bodies) { b.live = false; b.el.style.visibility = 'hidden'; }
    zTop = 1;
    queue = order.map((i, k) => ({ b: bodies[i], at: time + 0.15 + k * 0.06 }));
  }

  // ---------- user actions
  const cursor = { on: false, x: 0, y: 0, vx: 0, vy: 0, t: 0 };
  let grip = null, hover = null;
  function kick(b, k) { b.sqv += k * 4.5; }
  function pull(h) {                                 // drag the grabbed point toward the pointer
    const b = grip.b, ca = Math.cos(b.a), sa = Math.sin(b.a);
    const rx = grip.lx * ca - grip.ly * sa, ry = grip.lx * sa + grip.ly * ca;
    const ex = grip.tx - (b.x + rx), ey = grip.ty - (b.y + ry);
    const dvx = ex * 22 - (b.vx - b.av * ry), dvy = ey * 22 - (b.vy + b.av * rx);
    const k11 = b.im + b.iI * ry * ry, k12 = -b.iI * rx * ry, k22 = b.im + b.iI * rx * rx, det = k11 * k22 - k12 * k12;
    if (Math.abs(det) < 1e-14) return;
    let px = (k22 * dvx - k12 * dvy) / det, py = (k11 * dvy - k12 * dvx) / det;
    const cap = 6e4 * h / b.im, l = Math.hypot(px, py);
    if (l > cap) { px *= cap / l; py *= cap / l; }
    b.vx += px * b.im; b.vy += py * b.im; b.av += (rx * py - ry * px) * b.iI; b.av *= 0.99;
  }
  function pick(x, y) {
    let best = null;
    for (const b of bodies) {
      if (!b.live) continue;
      ends(b);
      closest(x, y, b.e[0], b.e[1], b.e[2], b.e[3], tmp);
      if (Math.hypot(x - tmp[0], y - tmp[1]) <= b.r + 2 && (!best || b.z > best.z)) best = b;
    }
    return best;
  }
  function pop(b) {
    wake(b);
    b.vy = Math.min(b.vy, 0) - (1300 + Math.random() * 500);
    b.vx += (Math.random() - 0.5) * 320;
    b.av += (Math.random() < 0.5 ? -1 : 1) * (7 + Math.random() * 6);
    kick(b, 1);
    ring(b.x, b.y, 0.9);
  }
  function bump(x, y) {                              // tapping the floor knocks nearby pills up
    for (const b of bodies) {
      if (!b.live) continue;
      const dx = b.x - x, dy = b.y - y, d = Math.hypot(dx, dy) || 1;
      if (d > 200) continue;
      const f = 1 - d / 200;
      wake(b);
      b.vx += dx / d * f * 540; b.vy += dy / d * f * 360 - f * 1150; b.av += (Math.random() - 0.5) * 7 * f;
      kick(b, f * 0.7);
    }
    ring(x, y, 1.5);
  }
  function toss() {
    for (const b of bodies) {
      if (!b.live) continue;
      wake(b);
      const depth = 0.55 + 0.45 * Math.min(1, Math.max(0, b.y / H));
      b.vy = Math.min(b.vy, 0) - (1150 + Math.random() * 900) * depth;
      b.vx += (Math.random() - 0.5) * 640; b.av += (Math.random() - 0.5) * 16;
      kick(b, 0.8);
    }
    ring(W / 2, H - 10, 2.6);
  }
  function ring(x, y, k) {
    const el = document.createElement('i');
    el.className = 'pile-ring';
    floor.append(el);
    el.animate([{ transform: `translate(${x - 70}px, ${y - 70}px) scale(.12)`, opacity: 0.8 },
      { transform: `translate(${x - 70}px, ${y - 70}px) scale(${k})`, opacity: 0 }],
    { duration: 700, easing: 'cubic-bezier(.2, .8, .2, 1)' }).onfinish = () => el.remove();
  }
  function local(e) {
    const r = floor.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  floor.addEventListener('pointerdown', (e) => {
    if (reduce || !started || (e.pointerType === 'mouse' && e.button !== 0)) return;
    const p = local(e), b = pick(p.x, p.y);
    if (b) {
      wake(b);
      const dx = p.x - b.x, dy = p.y - b.y, ca = Math.cos(b.a), sa = Math.sin(b.a);
      grip = { b, lx: dx * ca + dy * sa, ly: -dx * sa + dy * ca, tx: p.x, ty: p.y, sx: p.x, sy: p.y, t0: performance.now(), moved: false, id: e.pointerId };
      b.z = ++zTop; b.el.style.zIndex = b.z;
      b.el.classList.add('held');
      floor.setPointerCapture(e.pointerId);
      floor.classList.add('grabbing');
      e.preventDefault();
    } else {
      bump(p.x, p.y);
    }
    run();
  });
  floor.addEventListener('pointermove', (e) => {
    if (reduce || !started) return;
    const p = local(e);
    if (grip && grip.id === e.pointerId) {
      grip.tx = p.x; grip.ty = p.y;
      if (Math.hypot(p.x - grip.sx, p.y - grip.sy) > 6) grip.moved = true;
      run();
      return;
    }
    if (e.pointerType !== 'mouse') return;
    const hv = pick(p.x, p.y);
    if (hv !== hover) { hover = hv; floor.style.cursor = hv ? 'grab' : ''; }
    const now = performance.now(), dt = (now - cursor.t) / 1000;
    if (cursor.on && dt > 0.001 && dt < 0.12) {
      cursor.vx = cursor.vx * 0.5 + Math.max(-2600, Math.min(2600, (p.x - cursor.x) / dt)) * 0.5;
      cursor.vy = cursor.vy * 0.5 + Math.max(-2600, Math.min(2600, (p.y - cursor.y) / dt)) * 0.5;
    } else { cursor.vx = 0; cursor.vy = 0; }
    Object.assign(cursor, { x: p.x, y: p.y, t: now, on: true });
    if (Math.hypot(cursor.vx, cursor.vy) > 250) run();
  });
  const release = (e) => {
    if (!grip || grip.id !== e.pointerId) return;
    const g = grip;
    grip = null;
    g.b.el.classList.remove('held');
    floor.classList.remove('grabbing');
    try { floor.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (!g.moved && performance.now() - g.t0 < 320) pop(g.b);   // a click makes it jump
    run();
  };
  floor.addEventListener('pointerup', release);
  floor.addEventListener('pointercancel', release);
  floor.addEventListener('pointerleave', () => { cursor.on = false; hover = null; floor.style.cursor = ''; });
  floor.addEventListener('dragstart', (e) => e.preventDefault());
  root.querySelector('[data-act="toss"]')?.addEventListener('click', () => { if (started) { toss(); run(); } });
  root.querySelector('[data-act="drop"]')?.addEventListener('click', () => {
    if (!started) return;
    grip = null;
    if (reduce) { settle(); return; }
    floor.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 220, easing: 'ease-in' }).onfinish = () => { drop(); run(); };
  });

  // ---------- drawing
  function paint(dt) {
    for (const b of bodies) {
      if (!b.live) continue;
      // springs for the squash on impact and the lift when held or hovered
      const target = grip && grip.b === b ? 1.08 : b === hover ? 1.04 : 1;
      for (let k = 0, n = Math.max(1, Math.ceil(dt * 90)), h = dt / n; k < n; k++) {
        b.sv += ((target - b.s) * 420 - b.sv * 24) * h; b.s += b.sv * h;
        b.sqv += (-b.sq * 560 - b.sqv * 15) * h; b.sq += b.sqv * h;
      }
      b.sq = Math.max(-0.2, Math.min(0.2, b.sq));
      const sx = b.s * (1 + b.sq), sy = b.s * (1 - b.sq);
      b.el.style.transform = `translate3d(${(b.x - b.w / 2).toFixed(2)}px, ${(b.y - b.h / 2).toFixed(2)}px, 0) rotate(${b.a.toFixed(4)}rad) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
    }
  }
  const springsBusy = () => bodies.some(b => b.live && (Math.abs(b.sq) > 0.002 || Math.abs(b.sqv) > 0.02 ||
    Math.abs((grip && grip.b === b ? 1.08 : b === hover ? 1.04 : 1) - b.s) > 0.002 || Math.abs(b.sv) > 0.02));

  // ---------- loop: runs only while something moves and the box is on screen
  let raf = 0, last = 0, acc = 0, quiet = 0, onScreen = false, started = false;
  function frame(t) {
    raf = 0;
    if (!onScreen) { last = 0; return; }
    const dt = last ? Math.min((t - last) / 1000, 0.1) : STEP;
    last = t;
    acc += dt;
    let awake = 0, n = 0;
    while (acc >= STEP && n < 3) { awake = step(); acc -= STEP; n++; }
    if (n === 3) acc = 0;
    paint(dt);
    quiet = !awake && !queue.length && !grip ? quiet + 1 : 0;
    if (quiet > 30 && !springsBusy() && !(cursor.on && t - cursor.t < 160)) { last = 0; return; }
    raf = requestAnimationFrame(frame);
  }
  function run() { if (!raf && onScreen && !reduce) { quiet = 0; raf = requestAnimationFrame(frame); } }

  function measure() {
    W = floor.clientWidth; H = floor.clientHeight - 4;   // a deep stack presses the bottom row in by a few px
    bodies.forEach(fit);
  }
  function settle() {                                // reduced motion: the same drop, run off screen in one go
    drop();
    for (let n = 0; n < 2400; n++) if (step() === 0 && !queue.length && n > 30) break;
    for (const b of bodies) { b.sq = b.sqv = 0; b.s = 1; b.sv = 0; }
    paint(0);
  }
  function start() {
    if (started) return;
    started = true;
    measure();
    if (reduce) { settle(); return; }
    drop();
    run();
  }

  new ResizeObserver(() => {
    const w = floor.clientWidth, h = floor.clientHeight;
    if (!w || !h || (w === W && h - 4 === H)) return;
    measure();
    for (const b of bodies) if (b.live) { wake(b); b.x = Math.min(Math.max(b.x, b.hl + b.r), W - b.hl - b.r); }
    if (reduce && started) settle(); else run();
  }).observe(floor);
  new IntersectionObserver(([en]) => {
    onScreen = en.isIntersecting;
    if (onScreen && en.intersectionRatio >= 0.3) (document.fonts ? document.fonts.ready : Promise.resolve()).then(start);
    if (onScreen) run();
  }, { threshold: [0, 0.3] }).observe(root);
})();
