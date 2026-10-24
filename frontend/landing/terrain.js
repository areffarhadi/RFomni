// The footer background: rolling hills drawn as thin contour lines in perspective, drifting slowly towards
// the viewer, brighter near the cursor. Canvas 2D, no libraries; paused off screen; still with reduced motion.
(() => {
  const root = document.querySelector('[data-terrain]');
  if (!root) return;
  const cv = root.querySelector('canvas'), g = cv.getContext('2d');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let W = 0, H = 0, dpr = 1, col = '', my = -1;   // my: cursor height, -1 when away

  const readColour = () => { col = getComputedStyle(document.documentElement).getPropertyValue('--terrain').trim() || '#8eb69b'; };
  function size() {
    const r = root.getBoundingClientRect();
    dpr = Math.min(2, devicePixelRatio || 1);
    W = r.width; H = r.height;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  }

  // height of the hills at (x across, z away from the viewer), both 0..1
  const hill = (x, z, t) =>
    Math.sin(x * 6.1 + z * 2.3 + t * 0.21) * 0.45 + Math.sin(x * 13.7 - z * 4.1 + t * 0.13) * 0.22
    + Math.sin(x * 3.1 + z * 7.9 - t * 0.17) * 0.33 + Math.sin(x * 23.0 + z * 11.0) * 0.06;

  function draw(ms) {
    const t = ms / 1000;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    g.strokeStyle = col; g.lineWidth = 1;
    const ROWS = 34, STEP = Math.max(6, W / 160), horizon = H * 0.18, drift = (t * 0.035) % (1 / ROWS);
    for (let i = ROWS; i >= 0; i--) {
      const z = i / ROWS - drift;                    // 0 near, 1 far
      if (z < 0) continue;
      const persp = 1 / (0.35 + z * 1.9);              // rows bunch up towards the horizon
      const base = horizon + (H - horizon) * (persp - 0.32) / 2.54 * 1.25;
      const amp = H * 0.16 * persp;
      const fade = Math.min(1, (1 - z) * 1.6) * Math.min(1, z * 8 + 0.15);
      g.beginPath();
      for (let x = -STEP; x <= W + STEP; x += STEP) {
        const u = x / W, k = (u - 0.5) / (0.6 + z * 1.4) + 0.5;  // spread the near rows wider
        const y = base - hill(k, z * 3 + t * 0.05, t) * amp;
        if (x < 0) g.moveTo(x, y); else g.lineTo(x, y);
      }
      const near = my < 0 ? 0 : Math.max(0, 1 - Math.abs(base - my) / (H * 0.35));
      g.globalAlpha = (0.2 + near * 0.3) * fade;
      g.stroke();
    }
    g.globalAlpha = 1;
  }

  let running = false, visible = false;
  function frame(ms) {
    if (!visible) { running = false; return; }
    draw(ms);
    requestAnimationFrame(frame);
  }
  root.addEventListener('pointermove', (e) => { my = e.clientY - root.getBoundingClientRect().top; });
  root.addEventListener('pointerleave', () => { my = -1; });
  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    if (visible && !running && !reduce) { running = true; requestAnimationFrame(frame); }
  }).observe(root);
  new ResizeObserver(() => { size(); draw(0); }).observe(root);
  addEventListener('modechange', () => { readColour(); draw(0); });
  readColour();
})();
