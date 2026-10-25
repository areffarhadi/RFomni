// Data collection: what people see when they open a project link (/c/<token>).
// They agree to the consent (and may say a little about themselves), get a session ID, choose a language and
// record each sentence in each condition (e.g. emotion). The microphone is read raw (no noise suppression, echo
// cancellation or gain control), resampled to the project's rate and sent as 16-bit mono WAV, after they have
// listened to it and, if they like, trimmed the start and end. With ?preview=1 the owner sees the page; nothing
// is saved. The microphone needs https (or localhost).
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const token = location.pathname.split('/').filter(Boolean)[1] || '';
  const params = new URLSearchParams(location.search);
  const preview = params.get('preview') === '1';
  const key = `omni:collect:${token}`;
  const app = $('#app');
  const COLORS = { neutral: '#8a96a3', happy: '#f2c14e', sad: '#5b8def', angry: '#e5534b', surprised: '#b77cf2',
    fearful: '#7ac7a8', disgusted: '#9bb04a', calm: '#7ab8d6' };

  let project = null, sid = null, lang = null, pair = null, prog = { done: 0, total: 0 }, step = 0;

  // ---------- helpers
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.className = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const svg = (d) => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.innerHTML = d; return s; };
  const MIC = '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>';
  const STOP = '<rect x="6" y="6" width="12" height="12" rx="2"/>';
  let toastTimer = 0;
  function toast(text) {
    const t = $('.toast'); t.textContent = text; t.classList.add('on');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), Math.max(2200, text.length * 60));
  }
  async function api(method, path, body) {
    const sep = path.includes('?') ? '&' : '?';
    const isForm = body instanceof FormData;
    const r = await fetch(`/api/c/${encodeURIComponent(token)}${path}${preview ? sep + 'preview=1' : ''}`, {
      method, headers: body && !isForm ? { 'Content-Type': 'application/json' } : {},
      body: body ? (isForm ? body : JSON.stringify(body)) : undefined, credentials: 'same-origin' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.detail || 'Something went wrong. Please try again.'), { status: r.status });
    return data;
  }
  const store = { get: (k) => { try { return localStorage.getItem(`${key}:${k}`); } catch { return null; } },
    set: (k, v) => { try { v ? localStorage.setItem(`${key}:${k}`, v) : localStorage.removeItem(`${key}:${k}`); } catch { /* not remembered */ } } };
  function screen(...nodes) { stopMeter(); app.replaceChildren(...nodes); app.firstElementChild?.classList.add('fade'); }
  function fail(msg) { screen(h('div', { class: 'card center' }, h('h1', {}, 'Not available'), h('p', { class: 'lead' }, msg))); }
  function setProgress() {
    const box = $('.progress'); box.hidden = !lang;
    if (!lang) return;
    const name = project.design.languages.find(l => l.code === lang)?.name || lang;
    $('.progress-text').textContent = `${name}: ${prog.done} of ${prog.total} recorded`;
    $('.progress-bar i').style.width = `${prog.total ? (prog.done / prog.total) * 100 : 0}%`;
  }
  function showSid() { const b = $('.sid'); b.hidden = !sid || preview; b.textContent = sid || ''; }
  $('.sid').addEventListener('click', () => navigator.clipboard?.writeText(sid).then(() => toast('Session ID copied. Use it to continue later.'), () => toast(`Your session ID: ${sid}`)));
  $('.change-group').addEventListener('click', () => pickLanguage());

  function applyDesign(d) {
    document.documentElement.dataset.theme = d.look?.theme || 'forest';
    const root = document.documentElement.style;
    if (d.look?.accent) { root.setProperty('--accent', d.look.accent); root.setProperty('--grad-a', `color-mix(in srgb, ${d.look.accent} 70%, white)`); root.setProperty('--grad-b', d.look.accent); }
    document.title = d.title; $('.brand-title').textContent = d.title;
  }

  // ---------- intro: consent and a little about the speaker
  function intro(info) {
    const d = project.design, st = d.settings;
    const saved = store.get('session');
    const fields = {};
    const field = (k, label) => {
      const req = st.info_required.includes(k);
      const choices = info.choices[k];
      const el = choices ? h('select', { class: 'input' }, h('option', { value: '' }, req ? 'Choose…' : 'Prefer not to say'), choices.map(c => h('option', { value: c }, c)))
        : h('input', { class: 'input', autocomplete: 'off', maxlength: 80 });
      fields[k] = el;
      return h('label', { class: 'f' }, `${label}${req ? '' : ' (optional)'}`, el);
    };
    const keys = Object.keys(info.info_fields);
    const consent = h('input', { type: 'checkbox' });
    const err = h('p', { class: 'err', role: 'alert' });
    const resume = h('input', { class: 'input', placeholder: 'e.g. K7Q2-M9XD', autocomplete: 'off', spellcheck: 'false' });
    const begin = async () => {
      if (!consent.checked) { err.textContent = 'Please agree to the consent to take part.'; return; }
      const vals = Object.fromEntries(Object.entries(fields).map(([k, el]) => [k, el.value.trim()]));
      start({ consent: true, info: vals }, err);
    };
    screen(h('div', { class: 'card center wide' },
      h('h1', {}, d.title),
      d.instructions ? h('p', { class: 'lead' }, d.instructions) : null,
      keys.length ? h('div', { class: 'two' }, keys.map(k => field(k, info.info_fields[k]))) : null,
      d.consent ? h('label', { class: 'consent' }, consent, h('span', {}, d.consent)) : (consent.checked = true, null),
      saved && !preview ? h('button', { class: 'btn primary block', type: 'button', style: 'margin-bottom:10px', onclick: () => start({ resume: saved }, err) }, `Continue session ${saved}`) : null,
      h('button', { class: `btn ${saved && !preview ? '' : 'primary '}block`, type: 'button', onclick: begin }, saved && !preview ? 'Start a new session' : 'Start recording'),
      err,
      preview ? null : [h('div', { class: 'or' }, 'or continue a session'),
        h('div', { class: 'row' }, resume, h('button', { class: 'btn', type: 'button', onclick: () => resume.value.trim() ? start({ resume: resume.value }, err) : resume.focus() }, 'Continue')),
        h('p', { class: 'hint' }, 'No account needed. You get a session ID; keep it to continue later on any device.')]));
  }

  async function start(body, err) {
    try {
      const r = await api('POST', '/session', body);
      sid = r.session.id;
      if (!preview) store.set('session', sid);
      showSid();
      if (!body.resume && !preview) toast(`Your session ID is ${sid}. Keep it to continue later.`);
      const langs = project.design.languages.filter(l => l.prompts.length);
      const savedLang = store.get('lang');
      if (langs.length === 1) return chooseLang(langs[0].code);
      if (body.resume && savedLang && langs.some(l => l.code === savedLang)) return chooseLang(savedLang);
      pickLanguage();
    } catch (e) {
      if (body.resume && e.status === 404) store.set('session', null);
      if (err) err.textContent = e.message; else toast(e.message);
    }
  }

  function pickLanguage() {
    lang = null; setProgress(); $('.change-group').hidden = true;
    const d = project.design;
    screen(h('div', { class: 'card center wide' },
      h('h1', {}, 'Choose a language'),
      h('p', { class: 'lead' }, 'Pick a language you speak fluently. You can record in more than one.'),
      h('div', { class: 'groups' }, d.languages.filter(l => l.prompts.length).map(l => h('button', { class: 'group', type: 'button', onclick: () => chooseLang(l.code) },
        h('b', {}, l.name), h('span', {}, `${l.prompts.length} sentences × ${d.conditions.length} ${d.conditions.length === 1 ? 'way' : 'ways'}`))))));
  }
  function chooseLang(code) { lang = code; step = 0; store.set('lang', code); next(); }

  async function next() {
    screen(h('div', { class: 'card center' }, h('div', { class: 'spin', 'aria-label': 'Loading' })));
    $('.change-group').hidden = project.design.languages.filter(l => l.prompts.length).length < 2;
    try {
      const r = await api('GET', `/next?lang=${encodeURIComponent(lang)}&session=${encodeURIComponent(sid || '')}${preview ? `&step=${step}` : ''}`);
      prog = { done: r.done, total: r.total }; setProgress();
      if (!r.pair) return done();
      pair = r.pair;
      recordScreen();
    } catch (e) {
      if (e.status === 404) { store.set('session', null); sid = null; showSid(); boot(); toast(e.message); } else fail(e.message);
    }
  }

  function done() {
    const name = project.design.languages.find(l => l.code === lang)?.name || lang;
    const more = project.design.languages.filter(l => l.prompts.length).length > 1;
    screen(h('div', { class: 'card center' },
      h('div', { class: 'done-icon' }, svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>')),
      h('h1', {}, `${name}: all recorded`),
      h('p', { class: 'lead' }, `Thank you! You recorded everything there is in ${name}.`),
      more ? h('button', { class: 'btn primary block', type: 'button', onclick: () => pickLanguage() }, 'Record another language') : null,
      sid && !preview ? [h('p', { class: 'muted', style: 'margin-top:16px' }, 'Your session ID'), h('div', { class: 'big-sid' }, sid)] : null));
  }

  // ---------- microphone
  let stream = null, actx = null, proc = null, src = null, chunks = [], recording = false, startedAt = 0, meterRaf = 0, level = 0;
  let take = null;   // { buf: AudioBuffer at the project's rate, start, end }
  async function openMic() {
    if (stream) return;
    if (!window.isSecureContext) throw new Error('The microphone only works on a secure (https) address. Ask the owner for the https link.');
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
    actx = new (window.AudioContext || window.webkitAudioContext)();
    src = actx.createMediaStreamSource(stream);
    proc = actx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (e) => {
      const x = e.inputBuffer.getChannelData(0);
      let s = 0;
      for (let i = 0; i < x.length; i += 4) s += x[i] * x[i];
      level = Math.sqrt(s / (x.length / 4));
      if (recording) chunks.push(new Float32Array(x));
    };
    src.connect(proc); proc.connect(actx.destination);  // the processor only runs while connected; it outputs silence
  }
  function stopMeter() { cancelAnimationFrame(meterRaf); }

  async function startRec(ui) {
    try { await openMic(); } catch (e) { ui.err.replaceChildren(h('div', { class: 'notice-box' }, e.name === 'NotAllowedError' ? 'The microphone was blocked. Allow it in the browser’s address bar, then try again.' : e.message)); return; }
    await actx.resume();
    chunks = []; recording = true; startedAt = performance.now(); take = null;
    ui.paint('live');
    const maxS = project.design.settings.max_seconds;
    const tick = () => {
      const t = (performance.now() - startedAt) / 1000;
      ui.timer.textContent = `${t.toFixed(1)} s / ${maxS} s`;
      ui.lvl.firstChild.style.width = `${Math.min(100, level * 260)}%`;
      ui.lvl.classList.toggle('hot', level > 0.35);
      if (t >= maxS) return stopRec(ui);
      meterRaf = requestAnimationFrame(tick);
    };
    tick();
  }

  async function stopRec(ui) {
    if (!recording) return;
    recording = false; stopMeter();
    const n = chunks.reduce((a, c) => a + c.length, 0);
    const raw = new Float32Array(n);
    let o = 0;
    for (const c of chunks) { raw.set(c, o); o += c.length; }
    const rate = project.design.settings.sample_rate;
    const inBuf = actx.createBuffer(1, Math.max(1, n), actx.sampleRate);
    inBuf.copyToChannel(raw, 0);
    // resample to the project's rate
    const off = new OfflineAudioContext(1, Math.max(1, Math.round(n * rate / actx.sampleRate)), rate);
    const s = off.createBufferSource(); s.buffer = inBuf; s.connect(off.destination); s.start();
    const buf = await off.startRendering();
    take = { buf, start: 0, end: buf.duration };
    ui.paint('take');
  }

  function autoTrim() {
    const x = take.buf.getChannelData(0), sr = take.buf.sampleRate, win = Math.round(sr * 0.02);
    const rms = [];
    for (let i = 0; i + win <= x.length; i += win) { let s = 0; for (let j = i; j < i + win; j++) s += x[j] * x[j]; rms.push(Math.sqrt(s / win)); }
    if (!rms.length) return;
    const sorted = [...rms].sort((a, b) => a - b);
    const floor = sorted[Math.floor(sorted.length * 0.1)], peak = sorted[sorted.length - 1];
    const thr = Math.max(floor * 3, peak * 0.06);
    const first = rms.findIndex(v => v > thr), last = rms.length - 1 - [...rms].reverse().findIndex(v => v > thr);
    if (first < 0) return;
    take.start = Math.max(0, first * 0.02 - 0.15);
    take.end = Math.min(take.buf.duration, (last + 1) * 0.02 + 0.2);
  }

  function encodeWav(buf, a, b) {
    const sr = buf.sampleRate, x = buf.getChannelData(0).subarray(Math.floor(a * sr), Math.ceil(b * sr));
    const out = new DataView(new ArrayBuffer(44 + x.length * 2));
    const str = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
    str(0, 'RIFF'); out.setUint32(4, 36 + x.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
    out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true); out.setUint32(24, sr, true);
    out.setUint32(28, sr * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true); str(36, 'data'); out.setUint32(40, x.length * 2, true);
    for (let i = 0; i < x.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, x[i])) * 0x7fff, true);
    return new Blob([out], { type: 'audio/wav' });
  }

  // ---------- the recording screen
  function recordScreen() {
    const d = project.design, c = pair.condition, pr = pair.prompt;
    const lg = d.languages.find(l => l.code === lang);
    const col = COLORS[c.id] || null;
    const err = h('div', { class: 'err', role: 'alert' });
    const mic = h('button', { class: 'mic', type: 'button', 'aria-label': 'Record' }, svg(MIC));
    const timer = h('div', { class: 'timer' }, 'Press to record');
    const lvl = h('div', { class: 'level', hidden: true }, h('i'));
    const canvas = h('canvas');
    const hA = h('div', { class: 'handle', title: 'Drag to trim the start' }), hB = h('div', { class: 'handle', title: 'Drag to trim the end' });
    const head = h('div', { class: 'playhead' });
    const wave = h('div', { class: 'wave-wrap', hidden: true }, canvas, hA, hB, head);
    const dur = h('span', { class: 'dur' });
    const playBtn = h('button', { class: 'btn', type: 'button' }, 'Play');
    const tools = h('div', { class: 'rec-tools', hidden: true },
      playBtn,
      h('button', { class: 'btn', type: 'button', onclick: () => { autoTrim(); draw(); }, title: 'Cut the silence at the start and the end' }, 'Trim silence'),
      h('button', { class: 'btn', type: 'button', onclick: () => startRec(ui) }, 'Record again'),
      h('span', { class: 'grow' }), dur,
      h('button', { class: 'btn primary', type: 'button', onclick: () => submit(ui) }, 'Submit', h('kbd', {}, 'Enter')));
    const hint = h('p', { class: 'hint center' }, 'Press the button (or Space), read the sentence, then press it again.');
    const ui = { err, timer, lvl, paint: null };
    ui.paint = (stateName) => {
      const live = stateName === 'live', has = stateName === 'take';
      mic.classList.toggle('live', live);
      mic.replaceChildren(svg(live ? STOP : MIC));
      mic.setAttribute('aria-label', live ? 'Stop' : 'Record');
      mic.hidden = has;
      lvl.hidden = !live; timer.hidden = has;
      wave.hidden = !has; tools.hidden = !has;
      hint.textContent = has ? 'Listen to it. Drag the handles to cut the start or the end, then submit.' : live ? 'Recording… press again (or Space) to stop.' : 'Press the button (or Space), read the sentence, then press it again.';
      if (has) { autoTrim(); draw(); }
      err.replaceChildren();
    };
    mic.addEventListener('click', () => (recording ? stopRec(ui) : startRec(ui)));

    // waveform and trim handles
    let peaks = null;
    function draw() {
      const w = wave.clientWidth || 600, hgt = wave.clientHeight || 120, dpr = Math.min(2, devicePixelRatio || 1);
      canvas.width = w * dpr; canvas.height = hgt * dpr;
      const g = canvas.getContext('2d'); g.scale(dpr, dpr);
      const x = take.buf.getChannelData(0), per = Math.max(1, Math.floor(x.length / w));
      if (!peaks || peaks.length !== w) { peaks = new Float32Array(w); for (let i = 0; i < w; i++) { let m = 0; for (let j = i * per; j < (i + 1) * per && j < x.length; j++) m = Math.max(m, Math.abs(x[j])); peaks[i] = m; } }
      const cs = getComputedStyle(document.documentElement);
      const T = take.buf.duration, a = (take.start / T) * w, b = (take.end / T) * w;
      g.fillStyle = cs.getPropertyValue('--accent').trim();
      for (let i = 0; i < w; i++) { const v = Math.max(1, peaks[i] * hgt * 0.92); g.globalAlpha = i >= a && i <= b ? 1 : 0.25; g.fillRect(i, (hgt - v) / 2, 1, v); }
      g.globalAlpha = 0.35; g.fillStyle = cs.getPropertyValue('--bg').trim(); g.fillRect(0, 0, a, hgt); g.fillRect(b, 0, w - b, hgt);
      hA.style.left = `${a}px`; hB.style.left = `${b}px`;
      dur.textContent = `${(take.end - take.start).toFixed(1)} s`;
    }
    const drag = (handle, which) => handle.addEventListener('pointerdown', (e) => {
      e.preventDefault(); handle.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const r = wave.getBoundingClientRect(), t = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * take.buf.duration;
        if (which === 'a') take.start = Math.min(t, take.end - 0.2); else take.end = Math.max(t, take.start + 0.2);
        draw();
      };
      const up = () => { handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up); };
      handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', up);
    });
    drag(hA, 'a'); drag(hB, 'b');
    let playing = null;
    playBtn.addEventListener('click', async () => {
      if (playing) { playing.stop(); return; }
      await actx.resume();
      const s = actx.createBufferSource(); s.buffer = take.buf; s.connect(actx.destination);
      const t0 = actx.currentTime, len = take.end - take.start;
      s.start(0, take.start, len); playing = s; playBtn.textContent = 'Stop';
      head.style.display = 'block';
      const anim = () => {
        if (playing !== s) return;
        const t = take.start + (actx.currentTime - t0);
        head.style.left = `${(t / take.buf.duration) * wave.clientWidth}px`;
        requestAnimationFrame(anim);
      };
      anim();
      s.onended = () => { if (playing === s) { playing = null; playBtn.textContent = 'Play'; head.style.display = 'none'; } };
    });
    ui.play = () => playBtn.click();

    screen(h('div', { class: 'rec' },
      h('section', { class: 'card prompt', 'aria-label': 'What to record' },
        h('div', { class: 'cond', style: col ? `--c:${col}` : '' }, h('i'), c.label),
        c.hint ? h('p', { class: 'cond-hint' }, c.hint) : null,
        h('p', { class: 'sentence', dir: 'auto', lang }, pr.text),
        h('div', { class: 'sent-meta' }, `${lg?.name || lang} · sentence ${pair.index + 1} of ${lg?.prompts.length || '?'}`)),
      h('section', { class: 'card recorder', 'aria-label': 'Recorder' }, mic, timer, lvl, wave, tools, hint, err,
        project.design.settings.allow_skip ? h('button', { class: 'skip', type: 'button', onclick: () => skip() }, 'Skip this one') : null)));
    current = ui;
  }
  let current = null;

  async function submit(ui) {
    if (!take) return;
    const st = project.design.settings, len = take.end - take.start;
    if (len < st.min_seconds) { ui.err.textContent = `The recording is shorter than ${st.min_seconds} s.`; return; }
    if (len > st.max_seconds + 0.4) { ui.err.textContent = `The recording is longer than ${st.max_seconds} s. Trim it.`; return; }
    const fd = new FormData();
    fd.append('file', encodeWav(take.buf, take.start, take.end), 'recording.wav');
    fd.append('meta', JSON.stringify({ session: sid, lang, prompt: pair.prompt.id, condition: pair.condition.id,
      trim: [+take.start.toFixed(3), +take.end.toFixed(3)], client: navigator.userAgent.slice(0, 120) }));
    try {
      const r = await api('POST', '/record', fd);
      if (r.preview) toast('Preview: the recording was made, not saved.');
      step++;
      next();
    } catch (e) { ui.err.textContent = e.message; }
  }
  async function skip() {
    try { await api('POST', '/skip', { session: sid, lang, prompt: pair.prompt.id, condition: pair.condition.id }); step++; next(); }
    catch (e) { toast(e.message); }
  }

  document.addEventListener('keydown', (e) => {
    if (!current || !$('.recorder') || e.target.closest?.('input, textarea, select') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === ' ' || e.key === 'r' || e.key === 'R') {
      e.preventDefault();
      if (take && !recording && e.key === ' ') current.play(); else (recording ? stopRec(current) : startRec(current));
    }
    if (e.key === 'Enter' && take && !recording) { e.preventDefault(); submit(current); }
  });

  // ---------- start
  async function boot() {
    if (!token) return fail('This link is incomplete. Ask the person who shared it for the full link.');
    try {
      const r = await api('GET', '');
      project = { design: r.design };
      applyDesign(r.design);
      if (preview) $('.preview-bar').hidden = false;
      intro(r);
    } catch (e) { fail(e.message); }
  }
  boot();
})();
