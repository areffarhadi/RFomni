// Omni Studio sign-in page: the card (sign in / create an account), the password toggle, the colour modes and
// the drifting lights behind the glass. Plain JavaScript, no libraries.
//
// Accounts are the chat page's (Open WebUI): the studio posts the fields to it and keeps its session in the `token`
// cookie, which the chat page reads too, so one sign-in opens both apps when they share an address.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // where to go after signing in: the page you last had open in the studio (the studio remembers it)
  let lastPlace = '';
  try { lastPlace = JSON.parse(localStorage.getItem('omni:last') || '""'); } catch { /* blocked storage */ }
  if (!/^#\/[\w\-/?=&.%]*$/.test(lastPlace)) lastPlace = '';
  const studio = (location.protocol === 'file:' ? 'https://127.0.0.1:8710/' : '../') + lastPlace;
  // the chat app (Open WebUI with the agent) on the same address; its /auth page picks up the shared cookie
  const CHAT_URL = `https://${location.hostname || '192.168.8.174'}:8080/auth`;
  const APPS = { studio: { url: studio, name: 'Omni Studio' }, chat: { url: CHAT_URL, name: 'the chat' } };

  // ---------- the card
  const card = $('#auth'), toast = $('.toast');
  let toastTimer = 0;
  const say = (text) => {
    toast.textContent = text; toast.classList.add('on');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove('on'), Math.max(2400, text.length * 70));
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

  const note = $('#auth-note');
  function tell(text, bad = true) {
    note.textContent = text; note.hidden = !text; note.classList.toggle('bad', bad);
  }
  card.addEventListener('input', () => tell(''));

  async function enter() {
    const reg = card.dataset.mode === 'register';
    const email = $('#email').value.trim(), password = pw.value, name = $('#name').value.trim();
    if (!email || !password || (reg && !name)) return tell(reg ? 'Fill in your name, email and password.' : 'Fill in your email and password.');
    card.classList.add('busy');
    let res = null, data = null;
    try {
      res = await fetch(`/api/auth/${reg ? 'signup' : 'signin'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reg ? { name, email, password } : { email, password }),
      });
      data = await res.json().catch(() => null);
    } catch { /* offline: handled below */ }
    if (!res?.ok) {
      card.classList.remove('busy');
      return tell(data?.detail || 'Could not reach Omni Studio. Try again in a moment.');
    }
    if (data.user?.role === 'pending') {
      card.classList.remove('busy');
      return tell('Your account was created. An administrator has to approve it before you can continue.', false);
    }
    setTimeout(() => { location.href = APPS[app].url; }, reduce ? 0 : 550);
  }
  card.addEventListener('submit', (e) => { e.preventDefault(); if (!card.classList.contains('busy')) enter(); });
  if (new URLSearchParams(location.search).get('pending')) tell('Your account is waiting for an administrator\'s approval.', false);

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
