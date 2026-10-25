// Your own task: the landing page picture of writing a new task as a prompt. The prompt box writes
// example prompts, sends them and erases them, one after another. Visitors can type into the box; the
// examples pause while they do, and sending takes them to the studio, where tasks are written for real.
// (The status pills below the box are drawn only if the page has an .ask-agents list; it no longer does.)
// With "reduce motion" everything stays still.
(() => {
  const root = document.querySelector('[data-ask]');
  if (!root) return;
  const form = root.querySelector('.ask-box'), input = root.querySelector('#ask-input');
  const grid = root.querySelector('.ask-agents'), send = root.querySelector('.ask-send');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TAU = Math.PI * 2;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  // the prompts the box writes, in order (the first three were asked for)
  const PROMPTS = [
    'What is the Persian translation of the text spoken in each file?',
    'What is the accent of the speakers? Please select one of these classes: {en-US, en-GB, en-CA}',
    'Describe the environment in each video clip.',
    'Find every moment someone laughs while another person is still talking.',
    'Is the speaker reading from a script or speaking freely?',
    'How many people are on screen in each shot?',
  ];
  // the nine agents, with the names of the thinking states
  const AGENTS = [
    ['listening', 'Listening'], ['reasoning', 'Reasoning'], ['searching', 'Searching'],
    ['fetching', 'Fetching data'], ['linking', 'Connecting'], ['drafting', 'Drafting a response'],
    ['shaping', 'Generating output'], ['synthesizing', 'Synthesizing sources'], ['idle', 'Idle'],
  ];

  // =====================================================================================
  // orbs: a small moving sign for each state, drawn in the pill's text colour
  // =====================================================================================
  const fib = (n) => {
    const pts = [], inc = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i++) { const y = 1 - (i + 0.5) * 2 / n, r = Math.sqrt(1 - y * y), a = i * inc; pts.push([Math.cos(a) * r, y, Math.sin(a) * r]); }
    return pts;
  };
  const DENSE = fib(64), SPARSE = fib(30);
  const ease = (x) => x * x * (3 - 2 * x);
  const polyR = (th, sides, rot) => {   // distance to the edge of a regular polygon (a circle when sides < 3)
    if (sides < 3) return 1;
    const sec = TAU / sides;
    let ph = (th - rot) % sec;
    if (ph < 0) ph += sec;
    return Math.cos(sec / 2) / Math.cos(ph - sec / 2);
  };
  function drawOrb(ctx, S, state, t, ink) {
    ctx.clearRect(0, 0, S, S);
    const cx = S / 2, cy = S / 2, R = S * 0.4;
    const col = (a) => `rgba(${ink},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
    const dot = (x, y, r, a) => { ctx.fillStyle = col(a); ctx.beginPath(); ctx.arc(x, y, Math.max(0.3, r), 0, TAU); ctx.fill(); };
    const ring = (r, a, w) => { ctx.strokeStyle = col(a); ctx.lineWidth = w; ctx.beginPath(); ctx.arc(cx, cy, Math.max(0.5, r), 0, TAU); ctx.stroke(); };
    switch (state) {
      case 'reasoning': case 'searching': {                         // a turning sphere of dots
        const pts = state === 'reasoning' ? DENSE : SPARSE, rot = t * (state === 'reasoning' ? 0.9 : 2.1);
        const c = Math.cos(rot), s = Math.sin(rot), ct = Math.cos(0.35), st = Math.sin(0.35);
        const pulse = (t * 0.45) % 1, scan = (t * 3) % TAU - Math.PI;
        for (const [x, y, z] of pts) {
          const X = x * c + z * s, Z = -x * s + z * c, Y = y * ct - Z * st, depth = (y * st + Z * ct + 1) / 2;
          let a = 0.16 + 0.7 * depth;
          if (state === 'reasoning') a += Math.max(0, 1 - Math.abs(Math.hypot(X, Y) - pulse) * 6) * 0.5;         // a pulse travelling outward
          else a += Math.max(0, 1 - Math.abs(((Math.atan2(Y, X) - scan + 3 * Math.PI) % TAU) - Math.PI) * 2.5) * 0.7 * depth;  // a sweeping beam
          dot(cx + X * R, cy + Y * R, S * (0.016 + 0.03 * depth), a);
        }
        break;
      }
      case 'listening': {                                            // sound coming in: rings closing on a centre that answers
        dot(cx, cy, S * (0.07 + 0.025 * Math.sin(t * 5)), 0.95);
        for (let i = 0; i < 3; i++) { const p = 1 - ((t * 0.6 + i / 3) % 1); ring(R * (0.25 + 0.75 * p), 0.15 + 0.6 * (1 - p), S * 0.045); }
        break;
      }
      case 'fetching': {                                             // grains pulled in to the centre
        dot(cx, cy, S * 0.08 * (1 + 0.25 * Math.sin(t * 6)), 0.95);
        for (let i = 0; i < 7; i++) { const p = (t * 0.7 + i / 7) % 1, a = i / 7 * TAU + t * 0.8, r = R * (1 - p); dot(cx + Math.cos(a) * r, cy + Math.sin(a) * r, S * 0.045, 0.2 + 0.75 * p); }
        break;
      }
      case 'linking': {                                              // nodes on a ring, joined one link after another
        const n = 6, pts = Array.from({ length: n }, (_, i) => { const a = i / n * TAU + t * 0.4; return [cx + Math.cos(a) * R, cy + Math.sin(a) * R]; });
        ctx.lineWidth = S * 0.04;
        for (let i = 0; i < n; i++) {
          const j = (i + 2) % n, a = Math.max(0, Math.sin(t * 2.2 - i * 1.05));
          ctx.strokeStyle = col(0.12 + 0.6 * a); ctx.beginPath(); ctx.moveTo(pts[i][0], pts[i][1]); ctx.lineTo(pts[j][0], pts[j][1]); ctx.stroke();
        }
        pts.forEach(([x, y], i) => dot(x, y, S * 0.06, 0.5 + 0.5 * Math.max(0, Math.sin(t * 2.2 - i * 1.05 + 0.6))));
        break;
      }
      case 'drafting': {                                             // three dots, typing
        for (let i = 0; i < 3; i++) { const b = Math.max(0, Math.sin(t * 7 - i * 0.9)); dot(cx + (i - 1) * S * 0.24, cy - b * S * 0.16, S * 0.08, 0.4 + 0.6 * b); }
        break;
      }
      case 'shaping': {                                              // an outline trying shapes: triangle, square, ..., circle
        const q = (t * 0.6) % 5, i = Math.floor(q), f = ease(q - i), A = [3, 4, 5, 6, 0][i], B = [4, 5, 6, 0, 3][i], rot = t * 0.5;
        const r = (th) => R * 0.92 * (polyR(th, A, rot) * (1 - f) + polyR(th, B, rot) * f);
        ctx.strokeStyle = col(0.9); ctx.lineWidth = S * 0.065; ctx.lineJoin = 'round'; ctx.beginPath();
        for (let j = 0; j <= 72; j++) { const th = j / 72 * TAU, x = cx + Math.cos(th) * r(th), y = cy + Math.sin(th) * r(th); if (j) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
        ctx.stroke();
        const th = t * 2.4;
        dot(cx + Math.cos(th) * r(th), cy + Math.sin(th) * r(th), S * 0.08, 1);
        break;
      }
      case 'synthesizing': {                                         // three arcs from different sources, turning into one
        ctx.lineCap = 'round'; ctx.lineWidth = S * 0.06;
        const m = 0.5 + 0.5 * Math.sin(t * 1.4);
        for (let i = 0; i < 3; i++) {
          const r = R * (0.45 + 0.5 * (1 - m) * (i / 2) + 0.5 * m * 0.9), a = t * (1.2 + i * 0.5) + i * 2.1;
          ctx.strokeStyle = col(0.35 + 0.2 * i + 0.3 * m); ctx.beginPath(); ctx.arc(cx, cy, r, a, a + TAU * (0.22 + 0.12 * m)); ctx.stroke();
        }
        break;
      }
      default: {                                                     // idle: a quiet ring, breathing
        const b = 0.5 + 0.5 * Math.sin(t * 1.6);
        ring(R * (0.78 + 0.1 * b), 0.45 + 0.4 * b, S * 0.07);
      }
    }
  }

  // the nine pills, all working all the time
  const dpr = Math.min(2, window.devicePixelRatio || 1), ORB = 24;
  const pills = !grid ? [] : AGENTS.map(([state, label]) => {
    const li = document.createElement('li');
    li.className = 'ag';
    const cv = document.createElement('canvas');
    cv.className = 'ag-orb';
    cv.width = cv.height = ORB * dpr;
    cv.setAttribute('aria-hidden', 'true');
    const b = document.createElement('b');
    b.className = 'ag-label';
    b.textContent = label + (state === 'idle' ? '' : '…');
    li.append(cv, b);
    grid.append(li);
    return { state, ctx: cv.getContext('2d'), phase: Math.random() * 10 };
  });
  let ink = '255,255,255';
  const readInk = () => { if (!grid?.firstElementChild) return; const m = getComputedStyle(grid.firstElementChild).color.match(/\d+(\.\d+)?/g); if (m) ink = m.slice(0, 3).join(','); };
  let raf = 0, visible = false, last = 0;
  function frame(now) {
    raf = 0;
    if (!visible || document.hidden) return;
    const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
    last = now;
    for (const p of pills) {
      p.phase += dt;
      p.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawOrb(p.ctx, ORB, p.state, p.phase, ink);
    }
    raf = requestAnimationFrame(frame);
  }
  const startLoop = () => { if (!raf && visible && !reduce) { last = 0; raf = requestAnimationFrame(frame); } };
  function drawStill() { for (const p of pills) { p.ctx.setTransform(dpr, 0, 0, dpr, 0, 0); drawOrb(p.ctx, ORB, p.state, 1.3, ink); } }

  // =====================================================================================
  // the prompt box: writes a prompt, sends it, erases it, writes the next
  // =====================================================================================
  let paused = false, typing = false, mine = false;   // `mine`: the box holds the visitor's own text
  let epoch = 0;                                      // bumped when the visitor steps in: the example in progress starts over
  const syncSend = () => { send.disabled = !input.value.trim(); };
  const onScreen = async () => { while (!visible || document.hidden || paused) await sleep(250); };
  async function write(text) {
    const e = epoch;
    typing = true;
    for (const ch of text) {
      await onScreen();
      if (mine || e !== epoch) break;
      input.value += ch;
      syncSend();
      await sleep(24 + Math.random() * 38 + (/[,.?}]/.test(ch) ? 140 : 0));
    }
    typing = false;
  }
  async function erase() {
    const e = epoch;
    typing = true;
    while (input.value && !mine) {
      await onScreen();
      if (mine || e !== epoch) break;
      input.value = input.value.slice(0, -Math.max(1, Math.ceil(input.value.length / 28)));
      syncSend();
      await sleep(16);
    }
    typing = false;
  }
  function pulse() {                 // "sent": the border glows up for a moment
    form.classList.remove('sent');
    void form.offsetWidth;
    form.classList.add('sent');
  }
  async function loop() {
    for (let k = 0; ; k = (k + 1) % PROMPTS.length) {
      await onScreen();
      if (mine) { await sleep(400); k--; continue; }
      const e = epoch;
      await write(PROMPTS[k]);
      if (mine || e !== epoch) { k--; continue; }        // interrupted: write this one again from the start
      await sleep(1700);
      if (mine || e !== epoch) { k--; continue; }
      send.classList.add('press');
      pulse();
      await sleep(180);
      send.classList.remove('press');
      await sleep(900);
      await erase();
      await sleep(350);
    }
  }

  // visitors can write too: the examples wait while the box is theirs
  input.addEventListener('focus', () => {
    paused = true;
    epoch++;
    if (!mine) { input.value = ''; syncSend(); }
  });
  input.addEventListener('input', () => { if (!typing) mine = !!input.value.trim(); syncSend(); });
  input.addEventListener('blur', () => { paused = false; if (!input.value.trim()) mine = false; });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!input.value.trim()) { input.focus(); return; }
    pulse();
    // nothing is sent from this page: the studio is where a task is written and tested
    location.href = '../login/?next=studio';
  });
  syncSend();

  let started = false;
  new IntersectionObserver(([en]) => {
    visible = en.isIntersecting;
    if (visible) { readInk(); startLoop(); }
    if (visible && !started) {
      started = true;
      if (reduce) { input.value = PROMPTS[0]; syncSend(); } else loop();
    }
  }).observe(root);
  document.addEventListener('visibilitychange', startLoop);
  window.addEventListener('modechange', () => { readInk(); if (reduce) drawStill(); });
  readInk();
  if (reduce) drawStill();
})();
