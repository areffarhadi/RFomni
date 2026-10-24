// Omni Studio landing page: scroll reveals, word focus, the hero grid, the live feed, words lit by scrolling,
// tilted screenshots, the animated editor demo.
// Plain JavaScript, no libraries. With "reduce motion" switched on, everything shows its final state.
(() => {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const el = (tag, cls, props = {}) => Object.assign(document.createElement(tag), cls ? { className: cls } : {}, props);

  // ---------- links to the studio go through the sign-in page (which then returns you to the page you last had open);
  // relative when served by Omni Studio, absolute when this file is opened from disk
  const signIn = location.protocol === 'file:' ? 'http://127.0.0.1:8710/login/' : '../login/';
  $$('.open-studio').forEach(a => { a.href = signIn; });
  $$('[data-app]').forEach(a => { a.href = signIn + '?next=' + a.dataset.app; });  // Studio / Chat: sign in, then that app
  // phones: the platform links fold into a drop-down behind the menu button
  const menuBtn = $('.menu-btn'), navEl = $('.nav');
  const setMenu = (on) => { navEl.classList.toggle('open', on); menuBtn.setAttribute('aria-expanded', String(on)); };
  menuBtn.addEventListener('click', () => setMenu(!navEl.classList.contains('open')));
  document.addEventListener('click', (e) => { if (!navEl.contains(e.target)) setMenu(false); });
  addEventListener('keydown', (e) => { if (e.key === 'Escape') setMenu(false); });

  // ---------- nav background after scrolling
  const nav = $('.nav');
  const onNav = () => nav.classList.toggle('scrolled', scrollY > 12);
  addEventListener('scroll', onNav, { passive: true });
  onNav();

  // ---------- headlines: split into words (keeps <em> accents), revealed one by one
  for (const h of $$('[data-words]')) {
    let i = 0;
    const split = (node) => {
      for (const child of [...node.childNodes]) {
        if (child.nodeType === 3) {
          const frag = document.createDocumentFragment();
          child.textContent.split(/(\s+)/).forEach(part => {
            if (!part) return;
            if (/^\s+$/.test(part)) frag.append(' ');
            else frag.append(el('span', 'w', { textContent: part, style: `--i:${i++}` }));
          });
          child.replaceWith(frag);
        } else if (child.nodeType === 1) split(child);
      }
    };
    split(h);
  }

  // ---------- reveal on entering the viewport
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add('in');
      if (e.target.classList.contains('facts')) countUp(e.target);
      io.unobserve(e.target);
    }
  }, { rootMargin: '0px 0px -12% 0px', threshold: 0.12 });
  $$('.reveal, [data-words], .spk-viz').forEach(n => (reduce ? n.classList.add('in') : io.observe(n)));

  function countUp(box) {
    if (reduce) return;
    for (const b of $$('[data-count]', box)) {
      const n = +b.dataset.count, t0 = performance.now();
      const step = (t) => {
        const k = clamp((t - t0) / 900, 0, 1);
        b.textContent = Math.round(n * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
  }

  // ---------- hero grid: a few cells light up and fade, each on its own slow beat
  const cells = $('.hero-cells');
  if (cells) {
    let s = 5;
    const r = () => ((s = (s * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 16; i++) {
      const c = el('i');
      c.style.cssText = `left:${Math.floor(r() * 36) * 40}px;top:${Math.floor(r() * 22) * 40}px;--d:${(r() * 9).toFixed(2)}s;--t:${(6 + r() * 6).toFixed(2)}s`;
      cells.append(c);
    }
  }

  // ---------- live feed: two rows of recent labels from the example project, drifting in opposite directions
  const FEED = [
    ['#38bdf8', 'ASR', 'signals_and_systems_ep12.wav', '“so the transfer function tells us…”', 0.94],
    ['#fb923c', 'Diarization', 'studio_debate.wav', '3 speakers · 11 turns', 0.88],
    ['#f472b6', 'Emotion', 'support_call_0423_noisy.wav', 'frustrated → neutral', 0.71],
    ['#22d3ee', 'Forced alignment', 'lecture03_forced_alignment.mp4', '412 words aligned', 0.97],
    ['#f43f5e', 'Overlap', 'studio_debate.wav', 'SPEAKER_0 + SPEAKER_2 · 1.4 s', 0.83],
    ['#a3e635', 'Faces', 'team_photo.png', '6 boxes · happy ×4', 0.91],
    ['#c4b5fd', 'Language ID', 'multilingual_panel.wav', 'en · de · fr · es · it', 0.96],
    ['#fcd34d', 'Pose', 'pose_park.png', '3 people · 17 keypoints', 0.86],
    ['#7dd3fc', 'Translation', 'weather_tokyo_ja.wav', '“You will not need an umbrella.”', 0.9],
    ['#f0907f', 'Spoof', 'support_call_0423_noisy.wav', 'bonafide', 0.93],
    ['#8fd3c6', 'Sound events', 'street_traffic_clip.mp4', 'car horn · engine · footsteps', 0.79],
    ['#e879f9', 'OCR', 'meeting_notes_scan.png', '9 lines read', 0.95],
    ['#facc15', 'Reviewed', 'signals_and_systems_ep12.wav', 'Sara accepted 12 segments', 1],
    ['#60a5fa', 'Caption', 'street_scene.png', '“a cyclist waits at a crossing”', 0.87],
  ];
  const rowsEl = $$('.feed-row');
  if (rowsEl.length) {
    const item = ([c, task, file, val, conf], k) => {
      const li = el('li', 'fi');
      li.style.setProperty('--c', c);
      li.append(el('i'), el('b', '', { textContent: task }), el('span', 'ff', { textContent: file }),
        el('span', 'fv', { textContent: val }),
        el('span', 'fc', { textContent: conf === 1 ? '✓' : conf.toFixed(2) }),
        el('span', 'ft', { textContent: `${2 + ((k * 7) % 19)}s ago` }));
      return li;
    };
    rowsEl.forEach((row, r) => {
      const half = FEED.filter((_, i) => i % 2 === r);
      // twice the items, so the row can loop seamlessly by sliding exactly one half
      [...half, ...half].forEach((f, k) => row.append(item(f, k + r * 3)));
    });
  }

  // ---------- "What is": words light up one after another as the paragraph scrolls through the screen
  const what = $('[data-scrollwords]');
  const whatWords = [];
  if (what) {
    const split = (node) => {
      for (const child of [...node.childNodes]) {
        if (child.nodeType === 3) {
          const frag = document.createDocumentFragment();
          child.textContent.split(/(\s+)/).forEach(part => {
            if (!part) return;
            if (/^\s+$/.test(part)) frag.append(' ');
            else { const w = el('span', 'sw', { textContent: part }); whatWords.push(w); frag.append(w); }
          });
          child.replaceWith(frag);
        } else if (child.nodeType === 1 && !child.classList.contains('chip')) split(child);
        else if (child.nodeType === 1) whatWords.push(child);
      }
    };
    split(what);
  }

  // ---------- scroll-linked: tilted screenshot straightens, workflow line fills
  const tilts = $$('.tilt');
  const steps = $('#steps'), stepEls = $$('.step');
  let ticking = false;
  function onScroll() {
    ticking = false;
    const vh = innerHeight;
    if (what) {
      const r = what.getBoundingClientRect();
      const f = reduce ? 1 : clamp((vh * 0.85 - r.top) / (r.height + vh * 0.35), 0, 1);
      const lit = Math.round(f * whatWords.length);
      whatWords.forEach((w, i) => w.classList.toggle('lit', i < lit));
    }
    for (const t of tilts) {
      const r = t.getBoundingClientRect();
      t.style.setProperty('--p', reduce ? 1 : clamp((vh - r.top) / (vh * 0.72), 0, 1).toFixed(3));
    }
    if (steps) {
      const r = steps.getBoundingClientRect();
      const f = reduce ? 1 : clamp((vh * 0.62 - r.top) / Math.max(1, r.height * 0.8), 0, 1);
      steps.style.setProperty('--f', f.toFixed(3));
      stepEls.forEach((s, i) => s.classList.toggle('on', f >= i / (stepEls.length - 1) - 0.02));
    }
  }
  addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
  addEventListener('resize', onScroll);
  onScroll();

  // ---------- screenshot tabs
  const tabs = $$('.tabs [role=tab]'), shotImg = $('#shot img');
  const mode = () => document.documentElement.dataset.theme || 'forest';
  const shotSrc = (name) => `img/${mode()}/${name}.webp`;  // screenshots of the app in the same mode
  function selectTab(btn, force = false) {
    if (!force && btn.getAttribute('aria-selected') === 'true') return;
    tabs.forEach(t => t.setAttribute('aria-selected', String(t === btn)));
    tabs.forEach(t => t.tabIndex = t === btn ? 0 : -1);
    const swap = () => { shotImg.onload = () => shotImg.classList.remove('fade'); shotImg.src = shotSrc(btn.dataset.shot); shotImg.alt = btn.dataset.alt; };
    if (reduce) { swap(); return; }
    shotImg.classList.add('fade');
    setTimeout(swap, 220);
  }
  tabs.forEach((t, i) => {
    t.tabIndex = i === 0 ? 0 : -1;
    t.addEventListener('click', () => selectTab(t));
    t.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const n = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      n.focus(); selectTab(n);
    });
  });
  if (mode() !== 'forest') shotImg.src = shotSrc('editor');
  const preload = () => tabs.forEach(t => { new Image().src = shotSrc(t.dataset.shot); });
  setTimeout(preload, 2500);  // the other screenshots, once the page is idle

  // ---------- colour mode: Day / Night / Green, saved for the app as well
  const modes = $$('.modes [role=radio]');
  const themeMeta = $('meta[name=theme-color]');
  function paintModes() {
    modes.forEach(b => { const on = b.dataset.mode === mode(); b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1; });
    themeMeta.content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  }
  function setMode(m) {
    document.documentElement.dataset.theme = m;
    try { localStorage.setItem('omni:theme', JSON.stringify(m)); } catch { /* private mode: not remembered */ }
    paintModes();
    const cur = tabs.find(t => t.getAttribute('aria-selected') === 'true') || tabs[0];
    selectTab(cur, true);
    preload();
    window.dispatchEvent(new Event('modechange'));
  }
  modes.forEach((b, i) => {
    b.addEventListener('click', () => setMode(b.dataset.mode));
    b.addEventListener('keydown', (e) => {  // radio group: arrows move the choice
      const k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!k) return;
      e.preventDefault();
      const n = modes[(i + k + modes.length) % modes.length];
      n.focus(); setMode(n.dataset.mode);
    });
  });
  paintModes();

  // ---------- copy the install commands
  for (const b of $$('[data-copy]')) {
    b.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = 'Copied'; }
      catch { b.textContent = 'Select and copy'; }
      setTimeout(() => { b.textContent = 'Copy'; }, 1800);
    });
  }

  // =====================================================================
  // speaker data (seconds): shared by the demo and the speakers diagram
  // =====================================================================
  const TURNS = [
    [0.4, 4.8, 0], [4.0, 8.1, 1], [8.4, 10.7, 0], [10.0, 11.6, 2], [11.3, 13.0, 1], [13.4, 18.0, 2],
    [16.4, 17.6, 0], [18.3, 20.3, 1], [20.6, 23.2, 2], [22.2, 23.8, 0],
  ];
  const SPK_C = ['#7dd3fc', '#c4b5fd', '#fcd34d'];
  function regions(turns) {  // same idea as backend/speakers.py: split into one-speaker and overlap parts
    const ev = [];
    turns.forEach(([a, b, s], i) => { ev.push([a, 1, i, s]); ev.push([b, 0, i, s]); });
    ev.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
    const act = new Map(), out = [];
    let prev = null;
    for (const [t, k, i, s] of ev) {
      if (prev != null && t > prev && act.size) {
        const set = [...new Set(act.values())].sort();
        const last = out[out.length - 1];
        if (last && last.end === prev && last.set.join() === set.join()) last.end = t;
        else out.push({ start: prev, end: t, set });
      }
      if (k) act.set(i, s); else act.delete(i);
      prev = t;
    }
    return out;
  }

  // ---------- speakers diagram
  const viz = $('#spk-viz');
  if (viz) {
    const T = 24, pct = (t) => (t / T) * 100;
    let n = 0;
    const seg = (lane, a, b, c, text, ov) => {
      const s = el('div', 'sv-seg' + (ov ? ' ov' : ''), { textContent: text || '' });
      s.style.cssText = `left:${pct(a)}%;width:${pct(b - a)}%;--c:${c};--i:${n++}`;
      $(`[data-lane="${lane}"]`, viz).append(s);
    };
    TURNS.forEach(([a, b, s]) => seg(String(s), a, b, SPK_C[s], ''));
    n += 2;   // a short pause: then the rows worked out from the turns
    for (const r of regions(TURNS)) {
      if (r.set.length === 1) seg('pure', r.start, r.end, SPK_C[r.set[0]], r.end - r.start > 1.6 ? `S${r.set[0]}` : '');
      else seg('ovl', r.start, r.end, '#f43f5e', r.end - r.start > 2 ? r.set.map(x => `S${x}`).join('+') : '', true);
    }
    // the bars draw in one after another, in time order, each time the diagram comes into view
    if (!reduce) {
      new IntersectionObserver(([e]) => {
        if (e.intersectionRatio >= 0.35) viz.classList.add('play');
        else if (!e.isIntersecting) viz.classList.remove('play');
      }, { threshold: [0, 0.35] }).observe(viz);
    }
  }

  // =====================================================================
  // animated editor demo
  // =====================================================================
  const demo = $('#demo');
  if (!demo) return;
  const W = 990, T0 = 0, T1 = 16.5, X0 = 130;           // lane width, visible time range, header width
  const px = (t) => ((t - T0) / (T1 - T0)) * W;
  const ROW_Y = { asr: 164, words: 238, spk: 282, ovl: 386, emo: 426 };  // top of each row inside the demo
  const EMO = { neutral: '#94a3b8', happy: '#facc15', frustrated: '#f97316', surprised: '#fb923c' };

  // scale the 1120 px demo to its container
  const scaleBox = $('.demo-scale');
  const fit = () => demo.style.setProperty('--s', (scaleBox.clientWidth / 1120).toFixed(4));
  new ResizeObserver(fit).observe(scaleBox);
  fit();

  const rows = Object.fromEntries($$('.d-row', demo).map(r => [r.dataset.row, r]));
  const mk = (row, a, b, c, text, top, cls = '') => {
    b = Math.min(b, T1);
    const s = el('div', 'seg ' + cls, { textContent: text });
    s.style.cssText = `left:${px(a)}px;width:${Math.max(4, px(b) - px(a) - 2)}px;top:${top}px;--c:${c}`;
    rows[row].append(s);
    return s;
  };
  const ASR = [
    [0.4, 4.8, 'Let me be clear, the data shows that labelling by hand…', 0], [4.0, 8.1, 'But automatic labels are only as good as the model…', 1],
    [8.4, 10.7, 'Which is exactly why people should…', 0], [10.0, 11.6, 'Can I add something?', 1],
    [11.3, 13.0, 'Yes, go ahead.', 0], [13.4, 18.0, 'In our lab the model does the first pass…', 1],
  ];
  ASR.forEach(([a, b, t, r]) => mk('asr', a, Math.min(b, T1), '#38bdf8', t, r ? 40 : 9));
  // words of the row-0 utterances (proportional to their length)
  let target = null;
  for (const [a, b, text, r] of ASR) {
    if (r) continue;
    const words = text.replace('…', '').split(' ');
    const w = words.map(x => x.length + 2), sum = w.reduce((p, q) => p + q, 0);
    let t = a;
    words.forEach((word, i) => {
      const d = ((b - a) * w[i]) / sum;
      const s = mk('words', t, t + d, '#22d3ee', word, 11, 'word');
      if (word === 'ahead.') target = { s, a: t, b: t + d };
      t += d;
    });
  }
  TURNS.filter(([a]) => a < T1).forEach(([a, b, s]) => mk('spk', a, Math.min(b, T1), SPK_C[s], `SPEAKER_${s}`, 8 + s * 32));
  const ovSegs = regions(TURNS).filter(r => r.set.length > 1 && r.start < T1)
    .map(r => ({ r, s: mk('ovl', r.start, r.end, '#f43f5e', r.set.map(x => `S${x}`).join('+'), 7) }));
  const EMOS = [[0.4, 4.0, 'neutral'], [4.0, 8.1, 'neutral'], [8.4, 10.0, 'frustrated'], [10.0, 11.3, 'neutral'], [11.3, 13.0, 'neutral'], [13.4, 16.4, 'neutral']];
  const emoSegs = EMOS.map(([a, b, v]) => mk('emo', a, b, EMO[v], v, 13));
  const fixEmo = emoSegs[emoSegs.length - 1];
  const bnd = el('div', 'bnd');
  rows.words.append(bnd);

  // waveform: bars from a fixed pseudo-random signal, louder where people speak
  const cv = $('#wave'), g = cv.getContext('2d');
  const dpr = 2;
  cv.width = W * dpr; cv.height = 112 * dpr;
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const BAR = 3, bars = [];
  for (let x = 0; x < W; x += BAR) {
    const t = T0 + (x / W) * (T1 - T0);
    const n = TURNS.filter(([a, b]) => t >= a && t <= b).length;
    const syll = 0.55 + 0.45 * Math.abs(Math.sin(t * 7.3 + Math.sin(t * 2.1) * 2));
    bars.push(n ? clamp((0.25 + rnd() * 0.6) * syll * (n > 1 ? 1.25 : 1), 0.06, 1) : 0.03 + rnd() * 0.04);
  }
  let waveCol = [], tpLast = 0;
  const readWave = () => { const cs = getComputedStyle(document.documentElement); waveCol = [cs.getPropertyValue('--wave').trim(), cs.getPropertyValue('--wave-dim').trim()]; };
  readWave();
  addEventListener('modechange', () => { readWave(); drawWave(tpLast); });
  function drawWave(tp) {
    tpLast = tp;
    g.clearRect(0, 0, cv.width, cv.height);
    const mid = cv.height / 2, xp = px(tp) * dpr;
    bars.forEach((v, i) => {
      const x = i * BAR * dpr, h = v * (cv.height * 0.44);
      g.fillStyle = x < xp ? waveCol[0] : waveCol[1];
      g.fillRect(x, mid - h, BAR * dpr - 2, h * 2);
    });
  }

  // playhead
  const ph = $('#playhead');
  let tPlay = 9.2, lastFrame = null, paused = false, visible = true;
  const setPlay = (t) => { tPlay = t; ph.style.setProperty('--x', px(t)); drawWave(t); };
  function frame(now) {
    if (lastFrame != null && !paused && visible) {
      let t = tPlay + (now - lastFrame) / 1000;
      if (t > T1) t = T0;
      setPlay(t);
    }
    lastFrame = now;
    requestAnimationFrame(frame);
  }

  // cursors, menu, note
  const curA = $('#cur-a'), curB = $('#cur-b'), menu = $('#emo-menu'), note = $('#note');
  const go = (c, x, y) => { c.style.setProperty('--cx', `${x}px`); c.style.setProperty('--cy', `${y}px`); };
  const showNote = (text, x, y) => { note.textContent = text; note.style.left = `${x}px`; note.style.top = `${y}px`; note.classList.add('on'); };

  // a sleep that only counts time while the demo is playing and on screen
  const sleep = (ms) => new Promise(res => {
    let left = ms, prev = performance.now();
    const tick = (now) => {
      if (!paused && visible) left -= now - prev;
      prev = now;
      if (left <= 0) res(); else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });

  const wordX = (t) => X0 + px(t);
  function setWordStart(t) {
    const { s, b } = target;
    s.style.left = `${px(t)}px`;
    s.style.width = `${Math.max(4, px(b) - px(t) - 2)}px`;
    bnd.style.left = `${px(t) - 1}px`;
  }
  function setEmo(v, done) {
    fixEmo.style.setProperty('--c', EMO[v]);
    fixEmo.textContent = v;
    fixEmo.classList.toggle('low', !done);
    if (done) fixEmo.prepend(el('span', 'ok', { textContent: '✓' }));
  }
  const ovTarget = ovSegs.find(o => o.r.set.join() === '0,2') || ovSegs[0];
  const turnsOf = (o) => $$('.d-row.spk .seg', demo).filter(s => {
    const a = parseFloat(s.style.left), b = a + parseFloat(s.style.width);
    return b > px(o.r.start) && a < px(o.r.end) && o.r.set.some(k => s.textContent === `SPEAKER_${k}`);
  });
  function glowOverlap(on) {
    ovTarget.s.classList.toggle('glow', on);
    turnsOf(ovTarget).forEach(s => s.classList.toggle('glow', on));
  }
  function resetState() {
    setWordStart(target.a);
    bnd.classList.remove('on');
    setEmo('neutral', false);
    glowOverlap(false);
    note.classList.remove('on');
    menu.classList.remove('on');
    $$('span', menu).forEach(s => s.classList.remove('hl'));
  }

  const yWords = ROW_Y.words + 22, yEmo = ROW_Y.emo + 26, yOvl = ROW_Y.ovl + 20;
  const emoX = X0 + px(13.4) + (px(16.4) - px(13.4)) / 2;
  async function loop() {
    for (;;) {
      resetState();
      go(curA, 1180, 620); go(curB, -80, 620);
      await sleep(700);
      // 1. move the start of "ahead." a little later: the aligner started it too early
      go(curA, wordX(target.a) - 4, yWords - 6);
      await sleep(1000);
      bnd.classList.add('on'); curA.classList.add('press');
      await sleep(300);
      const shift = 0.32;
      setWordStart(target.a + shift); go(curA, wordX(target.a + shift) - 4, yWords - 6);
      await sleep(600);
      curA.classList.remove('press');
      showNote(`word border +${shift.toFixed(2)} s`, wordX(target.a + shift) - 40, ROW_Y.words - 32);
      await sleep(1400);
      note.classList.remove('on'); bnd.classList.remove('on');
      // 2. relabel a doubtful emotion
      go(curA, emoX, yEmo - 6);
      await sleep(1000);
      curA.classList.add('press'); await sleep(160); curA.classList.remove('press');
      menu.style.left = `${emoX - 200}px`; menu.style.top = `${ROW_Y.emo - 132}px`;  // opens to the left: the segment is near the edge
      menu.classList.add('on');
      await sleep(600);
      const happy = $('[data-v=happy]', menu);
      go(curA, emoX - 150, ROW_Y.emo - 132 + 66);
      await sleep(700);
      happy.classList.add('hl'); curA.classList.add('press');
      await sleep(250);
      curA.classList.remove('press'); menu.classList.remove('on');
      setEmo('happy', true);
      await sleep(500);
      go(curA, 1180, 560);
      // 3. a second reviewer checks an overlap between two speakers
      const ox = X0 + px(ovTarget.r.start) + (px(ovTarget.r.end) - px(ovTarget.r.start)) / 2;
      go(curB, ox, yOvl - 4);
      await sleep(1100);
      curB.classList.add('press'); await sleep(160); curB.classList.remove('press');
      glowOverlap(true);
      showNote(`${ovTarget.r.set.map(k => `SPEAKER_${k}`).join(' + ')} · ${(ovTarget.r.end - ovTarget.r.start).toFixed(1)} s overlap`, ox - 120, ROW_Y.ovl + 46);
      await sleep(2600);
      note.classList.remove('on'); glowOverlap(false);
      go(curB, -80, 620);
      await sleep(1600);
    }
  }

  // play / pause, and stop when the demo is off screen
  const toggle = $('#demo-toggle');
  toggle.addEventListener('click', () => {
    paused = !paused;
    toggle.setAttribute('aria-pressed', String(paused));
    toggle.setAttribute('aria-label', paused ? 'Play the animation' : 'Pause the animation');
  });
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; }, { threshold: 0.05 }).observe(demo);

  if (reduce) {  // still image of the end result
    toggle.hidden = true;
    setPlay(11.6);
    setWordStart(target.a + 0.32);
    setEmo('happy', true);
    glowOverlap(true);
    [curA, curB].forEach(c => { c.hidden = true; });
    return;
  }
  setPlay(tPlay);
  requestAnimationFrame(frame);
  loop();
})();
