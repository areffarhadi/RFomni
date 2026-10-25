// The hero orb: a slowly turning sphere of light grains with "AI" written in grains inside it.
// Grains twinkle, the letters breathe, and near the cursor every grain is pushed aside and drifts back.
// Canvas 2D, no libraries; paused off screen; one still frame when the system asks for reduced motion.
(() => {
  const root = document.querySelector('[data-orb]');
  if (!root) return;
  const cv = root.querySelector('canvas'), g = cv.getContext('2d');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TAU = Math.PI * 2;
  let seed = 11;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);

  let W = 0, H = 0, R = 0, dpr = 1, shell = [], text = [], col = '', blend = 'lighter';
  const mouse = { x: -1e4, y: -1e4, on: false };

  function readColour() {
    const cs = getComputedStyle(document.documentElement);
    col = cs.getPropertyValue('--orb').trim() || '#b9f0cf';
    blend = cs.getPropertyValue('color-scheme').trim() === 'light' ? 'source-over' : 'lighter';
  }

  // grains on (and a little inside) a sphere: drawn by rotating them around the vertical axis
  function makeShell(n) {
    shell = [];
    for (let i = 0; i < n; i++) {
      const u = rnd() * 2 - 1, a = rnd() * TAU, s = Math.sqrt(1 - u * u);
      const r = 0.82 + 0.18 * Math.pow(rnd(), 0.35);           // most grains sit near the surface
      shell.push({ x: s * Math.cos(a) * r, y: u * r, z: s * Math.sin(a) * r,
        tw: 0.6 + rnd() * 2.4, ph: rnd() * TAU, big: rnd() < 0.04, dx: 0, dy: 0, vx: 0, vy: 0 });
    }
  }

  // grains on the letters: sampled from the word drawn on a small offscreen canvas
  function makeText() {
    const size = Math.round(R * 0.82), c = document.createElement('canvas');
    c.width = Math.round(R * 1.6); c.height = Math.round(size * 1.05);
    const x = c.getContext('2d');
    x.fillStyle = '#fff'; x.textAlign = 'center'; x.textBaseline = 'middle';
    x.font = `700 ${size}px Satoshi, ui-sans-serif, system-ui, sans-serif`;
    x.fillText('AI', c.width / 2, c.height / 2 + size * 0.04);
    const px = x.getImageData(0, 0, c.width, c.height).data;
    const gap = Math.max(2, Math.round(R / 95));
    text = [];
    for (let y = 0; y < c.height; y += gap) {
      for (let xx = 0; xx < c.width; xx += gap) {
        if (px[(y * c.width + xx) * 4 + 3] < 128) continue;
        text.push({ hx: xx - c.width / 2 + (rnd() - 0.5) * gap, hy: y - c.height / 2 + (rnd() - 0.5) * gap,
          dx: 0, dy: 0, vx: 0, vy: 0, tw: 0.8 + rnd() * 2.8, ph: rnd() * TAU, big: rnd() < 0.05 });
      }
    }
  }

  function size() {
    const r = root.getBoundingClientRect();
    dpr = Math.min(2, devicePixelRatio || 1);
    W = r.width; H = r.height;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    R = Math.min(W, H) * 0.46;
    seed = 11;
    makeShell(Math.round(Math.min(4200, R * 14)));
    makeText();
  }

  // a grain pushed away from the cursor (turned a little, so the grains swirl), then pulled home
  function push(p, x, y) {
    if (mouse.on) {
      const ex = x - mouse.x, ey = y - mouse.y, d2 = ex * ex + ey * ey, rr = R * 0.32;
      if (d2 < rr * rr) {
        const d = Math.sqrt(d2) || 1, f = (1 - d / rr) * 2.2;
        p.vx += (ex / d) * f - (ey / d) * f * 0.35;
        p.vy += (ey / d) * f + (ex / d) * f * 0.35;
      }
    }
    p.vx += -p.dx * 0.035; p.vy += -p.dy * 0.035;
    p.vx *= 0.88; p.vy *= 0.88;
    p.dx += p.vx; p.dy += p.vy;
  }

  function draw(t) {
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    g.globalCompositeOperation = blend;
    g.fillStyle = col;
    const cx = W / 2, cy = H / 2, rot = t * 0.00012, cr = Math.cos(rot), sr = Math.sin(rot);
    const tilt = 0.32, ct = Math.cos(tilt), st = Math.sin(tilt);
    for (const p of shell) {
      const x1 = p.x * cr + p.z * sr, z1 = -p.x * sr + p.z * cr;   // turn around y
      const y2 = p.y * ct - z1 * st, z2 = p.y * st + z1 * ct;       // tilt towards the viewer
      const x = cx + x1 * R, y = cy + y2 * R;
      push(p, x, y);
      const depth = (z2 + 1) / 2;                                   // 0 back, 1 front
      const a = (0.22 + depth * 0.78) * (0.6 + 0.4 * Math.sin(t * 0.001 * p.tw + p.ph));
      const s = (p.big ? 2.3 : 1.35) * (0.7 + depth * 0.6);
      g.globalAlpha = a;
      g.fillRect(x + p.dx - s / 2, y + p.dy - s / 2, s, s);
    }
    const breathe = 1 + Math.sin(t * 0.0011) * 0.012;
    for (const p of text) {
      const x = cx + p.hx * breathe, y = cy + p.hy * breathe;
      push(p, x, y);
      const a = 0.5 + 0.5 * Math.sin(t * 0.0012 * p.tw + p.ph);
      const s = p.big ? 2.3 : 1.45;
      g.globalAlpha = 0.35 + a * 0.65;
      g.fillRect(x + p.dx - s / 2, y + p.dy - s / 2, s, s);
    }
    g.globalAlpha = 1;
    g.globalCompositeOperation = 'source-over';
  }

  let running = false, visible = true;
  function frame(t) {
    if (!visible) { running = false; return; }
    draw(t);
    requestAnimationFrame(frame);
  }
  function start() { if (!running && !reduce) { running = true; requestAnimationFrame(frame); } }

  root.addEventListener('pointermove', (e) => {
    const r = root.getBoundingClientRect();
    mouse.x = e.clientX - r.left; mouse.y = e.clientY - r.top; mouse.on = true;
  });
  root.addEventListener('pointerleave', () => { mouse.on = false; });
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) start(); }).observe(root);
  addEventListener('modechange', () => { readColour(); if (reduce) draw(0); });
  new ResizeObserver(() => { size(); if (reduce) draw(0); }).observe(root);

  readColour();
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => {
    size();
    if (reduce) draw(0); else start();
  });
})();
