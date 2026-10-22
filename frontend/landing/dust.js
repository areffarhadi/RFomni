// Action dust: the action recognition demo on the landing page. A figure made of golden dust acts out
// the action found in the current 4-second window of a clip (walking, running, waving, ...), and the
// action's name stands next to it in gold. Near the cursor the figure and the letters break into dust
// that swirls away and drifts back; when the clip moves to the next window, the figure blends into the
// next movement and the letters flow into the next word. Canvas 2D, no library.
// The figure is a side view built from body keypoints, the same joints the Pose task labels.
// Reduced motion: every window is drawn as a still picture, and changes without movement.
(() => {
  const root = document.querySelector('[data-dust]');
  if (!root) return;
  const stage = root.querySelector('.dw-stage');
  const canvas = stage.querySelector('canvas');
  const ctx = canvas.getContext('2d');
  const time = root.querySelector('.dw-time'), conf = root.querySelector('.dw-conf');
  const track = root.querySelector('.dw-track');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // the clip: one action per 4 s window, with the model's confidence
  const CLIP = [['walking', 0.94], ['running', 0.91], ['waving', 0.88], ['jumping', 0.86], ['bowing', 0.83], ['stretching', 0.79]];
  const WIN = 4, HOLD = 3600, BLEND = 0.7;
  const LIGHT = [255, 244, 220], DEEP = [195, 154, 85];   // gold, from pale to deep
  const SHADES = 12;
  const FONT = (px) => `italic 400 ${px}px "Instrument Serif", Georgia, serif`;
  const mm = (s) => `00:${String(s).padStart(2, '0')}`;
  root.querySelector('.dw-now').textContent = 'A golden figure acts out a clip labelled in 4-second windows: ' +
    CLIP.map(([l, c], k) => `${mm(k * WIN)} ${l} (${c.toFixed(2)})`).join(', ') + '.';

  track.replaceChildren(...CLIP.map(([label]) => Object.assign(document.createElement('i'), { title: label })));
  const segs = [...track.children];
  const head = document.createElement('b');
  head.className = 'dw-head';
  track.append(head);

  // =====================================================================================
  // the figure: bones of fixed length (in figure heights), angles in degrees
  // =====================================================================================
  const DEG = Math.PI / 180;
  const BONE = { torso: 0.29, neck: 0.111, upper: 0.16, fore: 0.15, thigh: 0.245, shin: 0.24, foot: 0.075 };
  // [shape, from, to, length, width (radius for dots), far side]
  const PARTS = [
    ['cap', 'shoulder', 'elbowF', BONE.upper, 0.046, 1], ['cap', 'elbowF', 'wristF', BONE.fore, 0.038, 1], ['dot', 'wristF', '', 0, 0.024, 1],
    ['cap', 'hip', 'kneeF', BONE.thigh, 0.064, 1], ['cap', 'kneeF', 'ankleF', BONE.shin, 0.05, 1], ['cap', 'ankleF', 'toeF', BONE.foot, 0.032, 1],
    ['cap', 'hip', 'shoulder', BONE.torso, 0.1, 0], ['cap', 'shoulder', 'head', BONE.neck, 0.042, 0], ['dot', 'head', '', 0, 0.066, 0],
    ['cap', 'hip', 'kneeN', BONE.thigh, 0.064, 0], ['cap', 'kneeN', 'ankleN', BONE.shin, 0.05, 0], ['cap', 'ankleN', 'toeN', BONE.foot, 0.032, 0],
    ['cap', 'shoulder', 'elbowN', BONE.upper, 0.046, 0], ['cap', 'elbowN', 'wristN', BONE.fore, 0.038, 0], ['dot', 'wristN', '', 0, 0.024, 0],
  ];
  const ease = (x) => x * x * (3 - 2 * x);
  const TAU = Math.PI * 2;
  // A pose: torso lean and head tilt (forward +), lift off the ground, and per limb
  // arm [upper-arm angle from straight down (forward +), elbow bend], leg [thigh angle, knee bend, foot tilt (toe down +)].
  // F = the far arm / leg (drawn dimmer), N = the near one.
  const MOVES = {
    walking(t) {
      const a = t / 1.1 * TAU, s = Math.sin(a), c = Math.cos(a);
      const knee = (q) => 6 + 30 * Math.pow(Math.max(0, q), 1.5);   // the swinging leg bends
      return { lean: 4, tilt: 0, lift: 0, legF: [-22 * s, knee(-c), 0], legN: [22 * s, knee(c), 0], armF: [18 * s, 14], armN: [-18 * s, 14] };
    },
    running(t) {
      const a = t / 0.68 * TAU, s = Math.sin(a), c = Math.cos(a);
      const knee = (q, back) => 20 + 80 * Math.pow(Math.max(0, q), 1.2) + 25 * Math.max(0, back);
      return { lean: 14, tilt: -4, lift: 0.035 * Math.max(0, Math.sin(2 * a)), legF: [-40 * s, knee(-c, s), 0], legN: [40 * s, knee(c, -s), 0], armF: [45 * s, 88], armN: [-45 * s, 88] };
    },
    waving(t) {
      const s = Math.sin(t / 0.6 * TAU);
      return { lean: 0, tilt: 4, lift: 0, legF: [-5, 4, 0], legN: [6, 4, 0], armF: [-4, 12], armN: [150, -8 + 32 * s] };
    },
    jumping(t) {
      const p = (t / 1.3) % 1;
      let crouch = 0, air = 0;
      if (p < 0.3) crouch = ease(p / 0.3);
      else if (p < 0.4) crouch = 1 - ease((p - 0.3) / 0.1);
      else if (p < 0.72) air = Math.sin(Math.PI * (p - 0.4) / 0.32);
      else if (p < 0.82) crouch = 0.7 * ease((p - 0.72) / 0.1);
      else crouch = 0.7 * (1 - ease((p - 0.82) / 0.18));
      return { lean: 18 * crouch + 4 * air, tilt: 0, lift: 0.22 * air,
        legF: [48 * crouch + 22 * air, 85 * crouch + 45 * air, 25 * air], legN: [52 * crouch + 26 * air, 90 * crouch + 50 * air, 25 * air],
        armF: [-35 * crouch + 150 * air, 20], armN: [-40 * crouch + 160 * air, 15] };
    },
    bowing(t) {
      const p = (t / 2.8) % 1;
      const b = p < 0.35 ? ease(p / 0.35) : p < 0.6 ? 1 : p < 0.9 ? 1 - ease((p - 0.6) / 0.3) : 0;
      return { lean: 70 * b, tilt: 18 * b, lift: 0, legF: [-10 * b - 3, 4, 0], legN: [-10 * b + 3, 4, 0], armF: [8 + 20 * b, 10], armN: [10 + 22 * b, 10] };
    },
    stretching(t) {
      const r = 0.5 - 0.5 * Math.cos(t / 3.2 * TAU);   // arms up overhead, up on the toes, and back
      return { lean: -7 * r, tilt: -10 * r, lift: 0, legF: [-3, 2, 32 * r], legN: [3, 2, 32 * r], armF: [6 + 166 * r, 12 - 8 * r], armN: [10 + 168 * r, 10 - 8 * r] };
    },
  };
  function mix(a, b, k) {
    const o = {};
    for (const key in a) o[key] = Array.isArray(a[key]) ? a[key].map((v, i) => v + (b[key][i] - v) * k) : a[key] + (b[key] - a[key]) * k;
    return o;
  }
  // keypoints in figure heights, hip at (0, 0); `low` is how far the lowest foot point is below the hip
  function skeleton(p) {
    const up = (a) => [Math.sin(a * DEG), -Math.cos(a * DEG)], down = (a) => [Math.sin(a * DEG), Math.cos(a * DEG)];
    const add = (A, v, l) => [A[0] + v[0] * l, A[1] + v[1] * l];
    const hip = [0, 0], shoulder = add(hip, up(p.lean), BONE.torso), headC = add(shoulder, up(p.lean + p.tilt), BONE.neck);
    const arm = ([sh, el]) => { const e = add(shoulder, down(sh), BONE.upper); return [e, add(e, down(sh + el), BONE.fore)]; };
    const leg = ([th, kn, ft]) => {
      const k = add(hip, down(th), BONE.thigh), a = add(k, down(th - kn), BONE.shin);
      return [k, a, [a[0] + Math.cos(ft * DEG) * BONE.foot, a[1] + Math.sin(ft * DEG) * BONE.foot]];
    };
    const [elbowF, wristF] = arm(p.armF), [elbowN, wristN] = arm(p.armN);
    const [kneeF, ankleF, toeF] = leg(p.legF), [kneeN, ankleN, toeN] = leg(p.legN);
    const low = Math.max(ankleF[1] + 0.026, ankleN[1] + 0.026, toeF[1] + 0.016, toeN[1] + 0.016);
    return { hip, shoulder, head: headC, elbowF, wristF, elbowN, wristN, kneeF, ankleF, toeF, kneeN, ankleN, toeN, low, lift: p.lift };
  }

  // =====================================================================================
  // layout, sprites, text
  // =====================================================================================
  let W = 0, H = 0, dpr = 1, fontPx = 80, gap = 2, narrow = false;
  let FH = 200, figX = 0, groundY = 0, LX = 0, LY = 0, LW = 0;
  let crisp = null, sprites = [], spriteSize = 6;
  let idx = 0, act = CLIP[0][0], prevAct = null, tAct = 0, tPrev = 0, tChange = -10;

  const shade = (t) => LIGHT.map((c, k) => Math.round(c + (DEEP[k] - c) * t));
  function makeSprites() {
    const R = 1.15 * 2.6, px = Math.max(4, Math.ceil(R * 2 * dpr));
    spriteSize = R * 2;
    sprites = Array.from({ length: SHADES }, (_, i) => {
      const [r, g, b] = shade(i / (SHADES - 1));
      const c = document.createElement('canvas');
      c.width = c.height = px;
      const x = c.getContext('2d'), grd = x.createRadialGradient(px / 2, px / 2, 0, px / 2, px / 2, px / 2);
      grd.addColorStop(0, `rgba(${r},${g},${b},1)`);
      grd.addColorStop(0.3, `rgba(${r},${g},${b},.55)`);
      grd.addColorStop(0.62, `rgba(${r},${g},${b},.14)`);
      grd.addColorStop(1, `rgba(${r},${g},${b},0)`);
      x.fillStyle = grd;
      x.fillRect(0, 0, px, px);
      return c;
    });
  }
  function textCanvas(label, scale, fill) {
    const c = document.createElement('canvas');
    c.width = Math.ceil(W * scale); c.height = Math.ceil(H * scale);
    const x = c.getContext('2d');
    x.scale(scale, scale);
    x.font = FONT(fontPx);
    x.textAlign = 'center'; x.textBaseline = 'middle';
    x.fillStyle = fill(x);
    x.fillText(label, LX, LY);
    return { c, x };
  }
  function layout() {
    narrow = W < 520;
    FH = Math.min(H * 0.62, 230);       // room for a jump with the arms up
    groundY = H * 0.88;
    figX = W * (narrow ? 0.22 : 0.26);
    LX = W * (narrow ? 0.66 : 0.67); LY = H * 0.5; LW = W * (narrow ? 0.6 : 0.56);
    const m = document.createElement('canvas').getContext('2d');
    let px = Math.min(96, H * 0.33);
    m.font = FONT(px);
    const widest = Math.max(...CLIP.map(([l]) => m.measureText(l).width));
    if (widest > LW * 0.92) px *= LW * 0.92 / widest;
    fontPx = Math.max(24, px);
    gap = narrow ? 2.6 : 2;
  }
  function sample(label) {          // one dust grain every `gap` px on the letters
    const { c, x } = textCanvas(label, 1, () => '#fff');
    const d = x.getImageData(0, 0, c.width, c.height).data, pts = [];
    const x0 = LX - LW / 2;
    for (let y = 0; y < c.height; y += gap) {
      for (let px = 0; px < c.width; px += gap) {
        const a = d[(y * c.width + px) * 4 + 3];
        if (a < 60) continue;
        const t = Math.min(1, Math.max(0, ((px - x0) / LW + y / H) / 2));   // diagonal gold gradient
        pts.push([px + (Math.random() - 0.5) * gap * 0.6, y + (Math.random() - 0.5) * gap * 0.6, a / 255, Math.min(SHADES - 1, Math.floor(t * SHADES))]);
      }
    }
    return pts;
  }
  function makeCrisp(label) {
    crisp = textCanvas(label, dpr, (x) => {
      const g = x.createLinearGradient(LX - LW / 2, LY - fontPx / 2, LX + LW / 2, LY + fontPx / 2);
      g.addColorStop(0, `rgb(${LIGHT})`); g.addColorStop(1, `rgb(${DEEP})`);
      return g;
    }).c;
  }

  // =====================================================================================
  // particles
  // =====================================================================================
  const alloc = (count, keys) => Object.fromEntries(keys.map(k => [k, new Float32Array(count)]));
  // figure grains: part, position along / across it, brightness, shade, push strength, swirl, twinkle phase
  let F = null, nf = 0;
  function buildFigure() {
    const g = gap * 0.95, grains = [];
    PARTS.forEach(([shape, , , len, w, far], pi) => {
      const area = shape === 'cap' ? len * w + Math.PI * (w / 2) ** 2 : Math.PI * w * w;
      const count = Math.round(area * FH * FH / (g * g));
      for (let k = 0, tries = 0; k < count && tries < count * 8; tries++) {
        let u, v;
        if (shape === 'cap') {
          u = -w / 2 / len + Math.random() * (1 + w / len); v = (Math.random() - 0.5) * w;
          const along = u < 0 ? u * len : u > 1 ? (u - 1) * len : 0;
          if (Math.hypot(along, v) > w / 2) continue;
        } else {
          const r = w * Math.sqrt(Math.random()), th = Math.random() * TAU;
          u = r * Math.cos(th); v = r * Math.sin(th);
        }
        grains.push([pi, u, v, (far ? 0.22 : 0.5) * (0.7 + Math.random() * 0.3)]);   // overlapping glow adds up: keep each grain faint so the body stays gold
        k++;
      }
    });
    nf = grains.length;
    F = alloc(nf, ['p', 'u', 'v', 'a', 's', 'x', 'y', 'vx', 'vy', 'hx', 'hy', 'j', 'r', 'ph']);
    grains.forEach(([pi, u, v, a], i) => {
      F.p[i] = pi; F.u[i] = u; F.v[i] = v; F.a[i] = a;
      F.j[i] = 0.55 + Math.random() * 0.9; F.r[i] = (Math.random() - 0.5) * 0.9; F.ph[i] = Math.random() * TAU;
    });
    figureHomes(MOVES.waving(0));            // shades from head (pale) to feet (deep), on a standing pose
    for (let i = 0; i < nf; i++) F.s[i] = Math.min(SHADES - 1, Math.max(0, Math.floor((F.hy[i] - (groundY - FH * 1.05)) / (FH * 1.05) * SHADES)));
  }
  const PX = new Float32Array(PARTS.length * 6);   // per part: start x, y, vector x, y, normal x, y
  function figureHomes(pose) {
    const K = skeleton(pose);
    const rootY = groundY - (K.low + K.lift) * FH;
    const at = (name) => [figX + K[name][0] * FH, rootY + K[name][1] * FH];
    PARTS.forEach(([shape, a, b], pi) => {
      const A = at(a), o = pi * 6;
      PX[o] = A[0]; PX[o + 1] = A[1];
      if (shape === 'cap') {
        const B = at(b), dx = B[0] - A[0], dy = B[1] - A[1], l = Math.hypot(dx, dy) || 1;
        PX[o + 2] = dx; PX[o + 3] = dy; PX[o + 4] = -dy / l; PX[o + 5] = dx / l;
      }
    });
    for (let i = 0; i < nf; i++) {
      const o = F.p[i] * 6, u = F.u[i], v = F.v[i];
      if (PARTS[F.p[i]][0] === 'cap') {
        F.hx[i] = PX[o] + PX[o + 2] * u + PX[o + 4] * v * FH;
        F.hy[i] = PX[o + 1] + PX[o + 3] * u + PX[o + 5] * v * FH;
      } else {
        F.hx[i] = PX[o] + u * FH; F.hy[i] = PX[o + 1] + v * FH;
      }
    }
    return K;
  }

  // label grains
  let T = null, nt = 0, dying = [];
  const TKEYS = ['hx', 'hy', 'x', 'y', 'vx', 'vy', 'a', 's', 'j', 'r'];
  function place(pts) {
    nt = pts.length; T = alloc(nt, TKEYS);
    pts.forEach(([x, y, a, s], i) => {
      T.hx[i] = T.x[i] = x; T.hy[i] = T.y[i] = y; T.a[i] = a; T.s[i] = s;
      T.j[i] = 0.55 + Math.random() * 0.9; T.r[i] = (Math.random() - 0.5) * 0.9;
    });
  }
  function morph(pts) {             // the letters' dust flows into the next word, roughly left to right
    const old = T, n0 = nt;
    const from = Array.from({ length: n0 }, (_, i) => i).sort((a, b) => (old.x[a] - old.x[b]) + (Math.random() - 0.5) * 30);
    const to = pts.map((p, i) => i).sort((a, b) => (pts[a][0] - pts[b][0]) + (Math.random() - 0.5) * 30);
    const used = new Uint8Array(n0);
    nt = pts.length; T = alloc(nt, TKEYS);
    for (let k = 0; k < nt; k++) {
      const i = to[k], o = from[Math.min(n0 - 1, Math.floor(k * n0 / nt))];
      used[o] = 1;
      const [x, y, a, s] = pts[i], ang = Math.random() * TAU, kick = 1.5 + Math.random() * 4;
      T.hx[k] = x; T.hy[k] = y; T.a[k] = a; T.s[k] = s;
      T.x[k] = old.x[o]; T.y[k] = old.y[o];
      T.vx[k] = old.vx[o] + Math.cos(ang) * kick; T.vy[k] = old.vy[o] + Math.sin(ang) * kick - 1.2;
      T.j[k] = 0.55 + Math.random() * 0.9; T.r[k] = (Math.random() - 0.5) * 0.9;
    }
    for (let o = 0; o < n0; o++) {
      if (used[o]) continue;
      const ang = Math.random() * TAU;
      dying.push({ x: old.x[o], y: old.y[o], vx: Math.cos(ang) * 2, vy: Math.sin(ang) * 2 - 1, a: old.a[o], s: old.s[o], life: 1 });
    }
  }

  // =====================================================================================
  // cursor and loop
  // =====================================================================================
  const cur = { x: -1e4, y: -1e4, on: false, inside: false };
  const REACH = 92, PUSH = 1.1;
  stage.addEventListener('pointermove', (e) => {
    const r = canvas.getBoundingClientRect();
    cur.x = e.clientX - r.left; cur.y = e.clientY - r.top; cur.on = true; cur.inside = true;
  }, { passive: true });
  stage.addEventListener('pointerleave', () => { cur.on = false; cur.inside = false; });

  // one step for a set of grains: pushed away from the cursor (turned a little, so the dust swirls), pulled home
  function stepGrains(G, n, spring, damp) {
    let far = 0;
    for (let i = 0; i < n; i++) {
      if (cur.on) {
        const dx = G.x[i] - cur.x, dy = G.y[i] - cur.y, d2 = dx * dx + dy * dy;
        if (d2 < REACH * REACH) {
          const d = Math.sqrt(d2) || 0.001, f = (1 - d / REACH) * PUSH * G.j[i], ux = dx / d, uy = dy / d;
          G.vx[i] += (ux - uy * G.r[i]) * f; G.vy[i] += (uy + ux * G.r[i]) * f;
        }
      }
      G.vx[i] = (G.vx[i] + (G.hx[i] - G.x[i]) * spring) * damp;
      G.vy[i] = (G.vy[i] + (G.hy[i] - G.y[i]) * spring) * damp;
      G.x[i] += G.vx[i]; G.y[i] += G.vy[i];
      const off = Math.abs(G.x[i] - G.hx[i]) + Math.abs(G.y[i] - G.hy[i]);
      if (off > far) far = off;
    }
    return far;
  }
  function poseAt(now) {
    const pose = MOVES[act]((now - tAct) / 1000);
    const k = (now - tChange) / 1000 / BLEND;
    return prevAct && k < 1 ? mix(MOVES[prevAct]((now - tPrev) / 1000), pose, ease(Math.max(0, k))) : pose;
  }

  let raf = 0, scatter = 0, visible = false, timer = 0, lastK = null;
  function frame(now) {
    raf = 0;
    if (!visible || document.hidden) return;
    lastK = figureHomes(poseAt(now));
    stepGrains(F, nf, 0.2, 0.72);                 // the figure follows its moves closely, trailing a little dust
    const far = stepGrains(T, nt, 0.045, 0.88);   // the letters settle slowly, like the original effect
    for (const d of dying) { d.x += d.vx; d.y += d.vy; d.vx *= 0.96; d.vy = d.vy * 0.96 - 0.02; d.life -= 0.035; }
    dying = dying.filter(d => d.life > 0);
    const want = Math.min(1, far / 6);
    scatter += (want - scatter) * (want > scatter ? 0.25 : 0.08);
    if (scatter < 0.004 && far < 0.4) scatter = 0;
    draw(now);
    raf = requestAnimationFrame(frame);
  }
  function draw(now) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    // a soft golden shadow under the feet, smaller while the figure is in the air
    const lift = lastK ? lastK.lift : 0, sw = FH * 0.34 * (1 - lift * 1.6);
    ctx.save();
    ctx.translate(figX + FH * 0.03, groundY + 2);
    ctx.scale(1, 0.16);
    const sg = ctx.createRadialGradient(0, 0, 0, 0, 0, sw);
    sg.addColorStop(0, `rgba(195,154,85,${0.32 * (1 - lift * 2)})`); sg.addColorStop(1, 'rgba(195,154,85,0)');
    ctx.fillStyle = sg;
    ctx.beginPath(); ctx.arc(0, 0, sw, 0, TAU); ctx.fill();
    ctx.restore();
    const S = spriteSize, h = S / 2, tw = now / 1000 * 4;
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < nf; i++) {               // the figure is always dust, with a gentle twinkle
      ctx.globalAlpha = F.a[i] * (reduce ? 1 : 0.7 + 0.3 * Math.sin(tw + F.ph[i]));
      ctx.drawImage(sprites[F.s[i]], F.x[i] - h, F.y[i] - h, S, S);
    }
    ctx.globalCompositeOperation = 'source-over';
    if (crisp && scatter < 0.995) { ctx.globalAlpha = 1 - scatter; ctx.drawImage(crisp, 0, 0, W, H); }
    if (scatter > 0.004 || dying.length) {
      ctx.globalCompositeOperation = 'lighter';
      for (let i = 0; i < nt; i++) {
        ctx.globalAlpha = T.a[i] * scatter;
        ctx.drawImage(sprites[T.s[i]], T.x[i] - h, T.y[i] - h, S, S);
      }
      for (const d of dying) { ctx.globalAlpha = d.a * d.life; ctx.drawImage(sprites[d.s], d.x - h, d.y - h, S, S); }
    }
    if (cur.inside && !reduce) {   // a soft shine follows the cursor over the gold
      ctx.globalCompositeOperation = 'source-atop';
      ctx.globalAlpha = 1;
      const g = ctx.createRadialGradient(cur.x, cur.y, 0, cur.x, cur.y, 230);
      g.addColorStop(0, 'rgba(255,248,225,.3)'); g.addColorStop(0.5, 'rgba(255,240,205,.1)'); g.addColorStop(1, 'rgba(255,240,205,0)');
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }
  function run() { if (!raf && visible && !reduce) raf = requestAnimationFrame(frame); }
  function still() {               // reduced motion: the window as one still picture
    lastK = figureHomes(MOVES[act](0.9));
    for (let i = 0; i < nf; i++) { F.x[i] = F.hx[i]; F.y[i] = F.hy[i]; }
    scatter = 0; dying = [];
    draw(0);
  }

  // =====================================================================================
  // the clip: show window k, then move on
  // =====================================================================================
  function show(k, animate) {
    const now = performance.now();
    idx = k;
    const [label, c] = CLIP[k];
    if (animate && !reduce) {
      prevAct = act; tPrev = tAct; tChange = now;
      for (let i = 0; i < nf; i++) { const a = Math.random() * TAU; F.vx[i] += Math.cos(a) * 2.5; F.vy[i] += Math.sin(a) * 2.5; }
    } else prevAct = null;
    act = label; tAct = now;
    makeCrisp(label);
    const pts = sample(label);
    if (animate && T && !reduce) { morph(pts); scatter = 1; } else place(pts);
    time.textContent = `${mm(k * WIN)} – ${mm(k * WIN + WIN)}`;
    conf.textContent = `${label} · ${c.toFixed(2)}`;
    segs.forEach((s, i) => s.classList.toggle('on', i === k));
    head.style.setProperty('--k', k);
    head.getAnimations().forEach(a => a.cancel());
    if (!reduce) head.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(100%)' }], { duration: HOLD + 400, easing: 'linear', fill: 'forwards' });
    if (reduce) still();
  }
  function schedule() {
    clearTimeout(timer);
    if (!visible || document.hidden) return;
    timer = setTimeout(next, reduce ? HOLD + 1500 : HOLD);
  }
  function next() {
    if (cur.inside) { timer = setTimeout(next, 600); return; }   // waits while you play with the dust
    show((idx + 1) % CLIP.length, true);
    schedule();
  }

  function build() {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    W = w; H = h; dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.ceil(W * dpr); canvas.height = Math.ceil(H * dpr);
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    layout();
    makeSprites();
    buildFigure();
    // the figure gathers from loose dust the first time it is seen
    lastK = figureHomes(MOVES[act](0));
    for (let i = 0; i < nf; i++) {
      const a = Math.random() * TAU, r = 30 + Math.random() * 90;
      F.x[i] = F.hx[i] + Math.cos(a) * r; F.y[i] = F.hy[i] + Math.sin(a) * r;
    }
    dying = [];
    show(idx, false);
    if (!reduce) { scatter = 0; draw(performance.now()); }
  }

  let ready = false;
  const fontReady = document.fonts ? document.fonts.load(FONT(80), 'walking').catch(() => {}) : Promise.resolve();
  fontReady.then(() => { ready = true; build(); schedule(); run(); });
  new ResizeObserver(() => { if (ready && (stage.clientWidth !== W || stage.clientHeight !== H)) build(); }).observe(stage);
  new IntersectionObserver(([en]) => {
    visible = en.isIntersecting;
    if (visible) { schedule(); run(); } else { clearTimeout(timer); cur.on = cur.inside = false; }
  }).observe(root);
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(timer); else { schedule(); run(); } });
})();
