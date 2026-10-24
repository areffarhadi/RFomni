// Omni Studio sign-in page: the card (sign in / create an account), the password toggle, the colour modes and
// the drifting lights behind the glass. Plain JavaScript, no libraries.
//
// Accounts are not finished yet: for now every submit opens the chosen app (Studio or Chat), filled in or not. When the backend has
// accounts, `enter()` is the one place to change: post the fields to it and only go on when it says yes.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // where to go after signing in: the page you last had open in the studio (the studio remembers it)
  let lastPlace = '';
  try { lastPlace = JSON.parse(localStorage.getItem('omni:last') || '""'); } catch { /* blocked storage */ }
  if (!/^#\/[\w\-/?=&.%]*$/.test(lastPlace)) lastPlace = '';
  const studio = (location.protocol === 'file:' ? 'http://127.0.0.1:8710/' : '../') + lastPlace;
  // the chat app (Open WebUI with the agent); change this when it moves
  const CHAT_URL = 'https://192.168.8.174:8080/';
  const APPS = { studio: { url: studio, name: 'Omni Studio' }, chat: { url: CHAT_URL, name: 'the chat' } };

  // ---------- the card
  const card = $('#auth'), toast = $('.toast');
  let toastTimer = 0;
  const say = (text) => {
    toast.textContent = text; toast.classList.add('on');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove('on'), 2400);
  };

  // ---------- where to go: Studio or Chat (?next= from the landing page, else the last choice)
  let app = new URLSearchParams(location.search).get('next');
  if (!APPS[app]) { try { app = JSON.parse(localStorage.getItem('omni:app') || '""'); } catch { app = ''; } }
  if (!APPS[app]) app = 'studio';
  const dests = $$('.dest [role=radio]');
  function pickApp(a) {
    app = a;
    try { localStorage.setItem('omni:app', JSON.stringify(a)); } catch { /* not remembered */ }
    dests.forEach(b => { const on = b.dataset.app === a; b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1; });
    setLead();
  }
  function setLead() {
    const reg = card.dataset.mode === 'register';
    $('#auth-lead').textContent = reg ? `Create an account, then continue to ${APPS[app].name}.` : `Sign in to continue to ${APPS[app].name}.`;
  }
  dests.forEach((b, i) => {
    b.addEventListener('click', () => pickApp(b.dataset.app));
    b.addEventListener('keydown', (e) => {
      const k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!k) return;
      e.preventDefault();
      const n = dests[(i + k + dests.length) % dests.length];
      n.focus(); pickApp(n.dataset.app);
    });
  });

  function setMode(mode) {
    card.dataset.mode = mode;
    const reg = mode === 'register';
    $('#auth-title').textContent = reg ? 'Create your account' : 'Welcome back';
    setLead();
    $('#submit-label').textContent = reg ? 'Create account' : 'Sign in';
    $('#password').autocomplete = reg ? 'new-password' : 'current-password';
    if (!reduce) { card.classList.remove('swap'); void card.offsetWidth; card.classList.add('swap'); }
  }
  pickApp(app);
  $('#switch').addEventListener('click', () => setMode(card.dataset.mode === 'signin' ? 'register' : 'signin'));

  const show = $('.show'), pw = $('#password');
  show.addEventListener('click', () => {
    const on = pw.type === 'password';
    pw.type = on ? 'text' : 'password';
    show.setAttribute('aria-pressed', String(on));
    show.querySelector('span').textContent = on ? 'Hide' : 'Show';
  });
  $$('[data-soon]').forEach(a => a.addEventListener('click', (e) => { e.preventDefault(); say('Coming soon'); }));

  function enter() {
    card.classList.add('busy');
    setTimeout(() => { location.href = APPS[app].url; }, reduce ? 0 : 550);
  }
  card.addEventListener('submit', (e) => { e.preventDefault(); if (!card.classList.contains('busy')) enter(); });

  // ---------- colour mode: Day / Night / Green, saved for the app and the landing page as well
  const modes = $$('.modes [role=radio]');
  const mode = () => document.documentElement.dataset.theme || 'forest';
  function paintModes() {
    modes.forEach(b => { const on = b.dataset.mode === mode(); b.setAttribute('aria-checked', String(on)); b.tabIndex = on ? 0 : -1; });
    $('meta[name=theme-color]').content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  }
  function pickMode(m) {
    document.documentElement.dataset.theme = m;
    try { localStorage.setItem('omni:theme', JSON.stringify(m)); } catch { /* private mode: not remembered */ }
    paintModes();
    readLights();
    window.dispatchEvent(new Event('modechange'));  // the orb picks up its new colour
  }
  modes.forEach((b, i) => {
    b.addEventListener('click', () => pickMode(b.dataset.mode));
    b.addEventListener('keydown', (e) => {
      const k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!k) return;
      e.preventDefault();
      const n = modes[(i + k + modes.length) % modes.length];
      n.focus(); pickMode(n.dataset.mode);
    });
  });
  paintModes();

  // ---------- out-of-focus lights: large soft discs that drift and slowly breathe, warmest near the bottom
  const cv = $('.bokeh'), g = cv.getContext('2d');
  let W = 0, H = 0, dpr = 1, lights = [], cols = [], light = false;
  let seed = 3;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  function readLights() {
    const cs = getComputedStyle(document.documentElement);
    cols = ['--light-1', '--light-2', '--light-3'].map(v => cs.getPropertyValue(v).trim());
    light = cs.getPropertyValue('color-scheme').trim() === 'light';
  }
  function size() {
    dpr = Math.min(2, devicePixelRatio || 1);
    W = innerWidth; H = innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    seed = 3;
    const n = Math.round(Math.min(22, 8 + W / 90));
    lights = Array.from({ length: n }, () => {
      const low = rnd() < 0.6;                         // most lights gather in the lower half, like the template
      return { x: rnd(), y: low ? 0.62 + rnd() * 0.42 : rnd() * 0.6, r: 40 + rnd() * 110,
        c: low ? (rnd() < 0.75 ? 0 : 2) : (rnd() < 0.5 ? 1 : 2), a: 0.25 + rnd() * 0.4,
        sx: (rnd() - 0.5) * 0.012, sy: (rnd() - 0.5) * 0.008, ph: rnd() * 6.28, sp: 0.2 + rnd() * 0.5 };
    });
  }
  function draw(ms) {
    const t = ms / 1000;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    g.globalCompositeOperation = light ? 'multiply' : 'lighter';
    for (const L of lights) {
      const x = ((L.x + L.sx * t) % 1.2 + 1.2) % 1.2 * W - 0.1 * W;
      const y = (L.y + Math.sin(t * L.sp * 0.3 + L.ph) * 0.02 + L.sy * Math.sin(t * 0.1 + L.ph)) * H;
      const a = L.a * (0.75 + 0.25 * Math.sin(t * L.sp + L.ph)) * (light ? 0.35 : 1);
      const grd = g.createRadialGradient(x, y, 0, x, y, L.r);
      grd.addColorStop(0, `rgba(${cols[L.c]}, ${a})`);
      grd.addColorStop(0.55, `rgba(${cols[L.c]}, ${a * 0.75})`);
      grd.addColorStop(1, `rgba(${cols[L.c]}, 0)`);
      g.fillStyle = grd;
      g.beginPath(); g.arc(x, y, L.r, 0, 6.2832); g.fill();
    }
    g.globalCompositeOperation = 'source-over';
  }
  function frame(ms) { draw(ms); requestAnimationFrame(frame); }
  addEventListener('resize', () => { size(); if (reduce) draw(0); });
  readLights(); size();
  if (reduce) draw(0); else requestAnimationFrame(frame);
})();
