// Shared labelling task: what people see when they open a shared task link (/t/<token>).
// No account: starting gives a session ID (e.g. K7Q2-M9XD), remembered in this browser and shown in the corner,
// which can be typed in later, on any device, to continue. Items come one at a time from the server; each answer is
// checked there. With ?preview=1 the task owner sees the page as it will look; nothing is saved, and the studio's
// designer sends new designs to it while the owner works on them.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const token = location.pathname.split('/').filter(Boolean)[1] || '';
  const params = new URLSearchParams(location.search);
  const preview = params.get('preview') === '1';
  const q = preview ? '?preview=1' : '';
  const key = `omni:task:${token}`;
  const app = $('#app');

  let design = null, sid = null, item = null, answers = {}, shownAt = 0, busy = false, kind = 'labelling';
  // listening tests: rate only after hearing the clip to the end (heard), count the plays, and go back (history)
  let heard = false, plays = 0, history = [], pos = null;

  // page language: the owner picks English or Persian (right to left, Persian digits)
  const STR = {
    en: { start: 'Start', newSession: 'Start a new session', cont: (id) => `Continue session ${id}`, or: 'or continue a session',
      noAccount: 'No account needed. You get a session ID; keep it to continue later on any device.', go: 'Continue',
      submit: 'Submit', next: 'Next', back: 'Back', skip: 'Skip', practice: 'Practice clip: not scored',
      listenFirst: 'Listen to the whole clip first.', clipHint: 'Listen to the whole clip before answering. Space plays and pauses.',
      answer: (q) => `Please answer “${q}”.`, thanks: 'Thank you!', nothingLeft: 'There is nothing left for you to do.',
      sid: 'Your session ID', overall: 'Overall comments (optional)', send: 'Send', sent: 'Thank you, your comment was saved.',
      done: (a, b) => `${a} of ${b} done`, welcome: (n) => `Welcome back. ${n} done so far.`, yourSid: (id) => `Your session ID is ${id}. Keep it to continue later.`,
      required: 'Please fill in the required answers.' },
    fa: { start: 'شروع', newSession: 'شروع جلسه‌ی تازه', cont: (id) => `ادامه‌ی جلسه‌ی ${id}`, or: 'یا ادامه‌ی جلسه‌ی قبلی',
      noAccount: 'نیازی به حساب کاربری نیست. یک شناسه‌ی جلسه می‌گیرید و با آن می‌توانید بعداً، روی هر دستگاهی، ادامه دهید.', go: 'ادامه',
      submit: 'ثبت', next: 'بعدی', back: 'قبلی', skip: 'رد کردن', practice: 'نمونه‌ی تمرینی (امتیاز آن حساب نمی‌شود)',
      listenFirst: 'ابتدا نمونه را تا آخر گوش دهید.', clipHint: 'پیش از پاسخ، نمونه را تا آخر گوش دهید. کلید فاصله پخش و توقف می‌کند.',
      answer: (q) => `لطفاً به «${q}» پاسخ دهید.`, thanks: 'سپاس از همکاری شما', nothingLeft: 'نمونه‌ی دیگری برای شما باقی نمانده است.',
      sid: 'شناسه‌ی جلسه‌ی شما', overall: 'نظر کلی (اختیاری)', send: 'ارسال', sent: 'سپاس، نظر شما ثبت شد.',
      done: (a, b) => `${FA(a)} از ${FA(b)}`, welcome: (n) => `خوش آمدید. تاکنون ${FA(n)} نمونه.`, yourSid: (id) => `شناسه‌ی جلسه‌ی شما ${id} است. برای ادامه نگهش دارید.`,
      required: 'لطفاً پرسش‌های الزامی را پاسخ دهید.' },
  };
  const FA = (d) => String(d).replace(/[0-9]/g, (c) => '۰۱۲۳۴۵۶۷۸۹'[c]);
  const ui = () => design?.look?.ui_lang === 'fa' ? 'fa' : 'en';
  const t = (k, ...a) => { const v = (STR[ui()][k] ?? STR.en[k]); return typeof v === 'function' ? v(...a) : v; };
  const num = (n) => (ui() === 'fa' ? FA(n) : String(n));
  // tasks whose items are sorted into groups (e.g. languages): people choose one, then label within it
  let groups = [], group = null;
  const groupLabel = () => design?.settings?.group_label || 'Group';
  // "{group}" in the wording becomes the chosen group: "Is this speaker a native {group} speaker?"
  const fill = (t) => String(t || '').replaceAll('{group}', group || `the chosen ${groupLabel().toLowerCase()}`);

  // ---------- small helpers
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const svg = (d, cls = '') => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 24 24'); if (cls) s.setAttribute('class', cls); s.innerHTML = d; return s; };
  let toastTimer = 0;
  function toast(text) {
    const t = $('.toast'); t.textContent = text; t.classList.add('on');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), Math.max(2200, text.length * 60));
  }
  async function api(method, path, body) {
    const sep = path.includes('?') ? '&' : '?';
    const r = await fetch(`/api/t/${encodeURIComponent(token)}${path}${preview ? sep + 'preview=1' : ''}`, {
      method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(data.detail || 'Something went wrong. Please try again.'), { status: r.status });
    return data;
  }
  const store = { get: () => { try { return localStorage.getItem(key); } catch { return null; } },
    set: (v) => { try { v ? localStorage.setItem(key, v) : localStorage.removeItem(key); } catch { /* not remembered */ } } };

  // ---------- design: mode, accent, title
  function applyDesign(d) {
    design = d;
    document.documentElement.dataset.theme = d.look?.theme || 'forest';
    const root = document.documentElement.style;
    if (d.look?.accent) {
      root.setProperty('--accent', d.look.accent);
      root.setProperty('--grad-a', `color-mix(in srgb, ${d.look.accent} 70%, white)`);
      root.setProperty('--grad-b', d.look.accent);
    } else ['--accent', '--grad-a', '--grad-b'].forEach(p => root.removeProperty(p));
    document.title = d.title || 'Labelling task';
    document.documentElement.dir = d.look?.dir === 'rtl' ? 'rtl' : 'ltr';
    document.documentElement.lang = ui();
    $('.change-group').textContent = `Change ${groupLabel().toLowerCase()}`;
    $('.brand-title').textContent = d.title || 'Labelling task';
  }
  function setProgress(p) {
    if (!p) return;
    const box = $('.progress');
    box.hidden = false;
    const limit = p.limit || 0;
    const mine = p.mine ?? 0;
    const everyone = design?.settings?.labels_per_item === 0;
    $('.progress-text').textContent = limit ? t('done', mine, limit)
      : everyone ? `${group ? `${group}: ` : ''}${t('done', p.mine_here ?? p.complete, p.items)}`
        : `${mine} done · ${p.complete} of ${p.items} items complete`;
    const frac = limit ? mine / limit : p.items ? (everyone ? (p.mine_here ?? p.complete) : p.complete) / p.items : 0;
    $('.progress-bar i').style.width = `${Math.min(100, frac * 100)}%`;
  }
  function showSid() {
    const b = $('.sid');
    b.hidden = !sid || preview;
    b.textContent = sid || '';
  }
  $('.sid').addEventListener('click', () => {
    navigator.clipboard?.writeText(sid).then(() => toast('Session ID copied. Use it to continue later.'), () => toast(`Your session ID: ${sid}`));
  });

  // ---------- screens
  function screen(...nodes) {
    app.replaceChildren(...nodes);
    app.firstElementChild?.classList.add('fade');
  }
  function fail(msg) {
    screen(h('div', { class: 'card center' }, h('h1', {}, 'Not available'), h('p', { class: 'lead' }, msg)));
  }

  function intro() {
    const saved = store.get();
    const name = design.settings.ask_name ? h('input', { class: 'input', id: 'name', autocomplete: 'nickname', placeholder: 'Your name or ID' }) : null;
    const resume = h('input', { class: 'input', id: 'resume', placeholder: 'e.g. K7Q2-M9XD', autocomplete: 'off', spellcheck: 'false' });
    // questions people answer once before they start (e.g. native speaker? listening device?)
    const info = {};
    const introFields = (design.intro_fields || []).map(f => {
      const el = f.type === 'choice'
        ? h('select', { class: 'input' }, h('option', { value: '' }, '—'), f.options.map(o => h('option', { value: o }, o)))
        : h('input', { class: 'input', autocomplete: 'off', maxlength: 120 });
      info[f.id] = el;
      return h('label', { class: 'f' }, f.label + (f.required ? ' *' : ''), el);
    });
    const err = h('p', { class: 'err', role: 'alert' });
    const begin = () => {
      const vals = Object.fromEntries(Object.entries(info).map(([k, el]) => [k, el.value.trim()]));
      if ((design.intro_fields || []).some(f => f.required && !vals[f.id])) { err.textContent = t('required'); return; }
      start({ name: name?.value || '', info: vals });
    };
    const startBtn = h('button', { class: 'btn primary block', type: 'button', onclick: begin }, t('start'));
    screen(h('div', { class: `card center${introFields.length ? ' wide' : ''}` },
      h('h1', {}, design.title),
      design.instructions ? h('p', { class: 'lead' }, fill(design.instructions)) : null,
      name ? h('label', { class: 'f' }, 'Your name or ID (shown to the task owner)', name) : null,
      introFields.length ? h('div', { class: 'two' }, introFields) : null,
      saved && !preview ? h('button', { class: 'btn primary block', type: 'button', style: 'margin-bottom:10px', onclick: () => start({ resume: saved }) },
        t('cont', saved)) : null,
      saved && !preview ? h('button', { class: 'btn block', type: 'button', onclick: begin }, t('newSession')) : startBtn,
      err,
      preview ? null : [h('div', { class: 'or' }, t('or')),
        h('div', { class: 'row' }, resume, h('button', { class: 'btn', type: 'button', onclick: () => resume.value.trim() ? start({ resume: resume.value }) : resume.focus() }, t('go'))),
        h('p', { class: 'hint' }, t('noAccount'))]));
    resume?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); start({ resume: resume.value }); } });
  }

  async function start(body) {
    try {
      const r = await api('POST', '/session', body);
      sid = r.session.id;
      history = r.history || []; pos = null;
      if (!preview) store.set(sid);
      showSid();
      setProgress(r.progress);
      if (body.resume) toast(t('welcome', r.session.done));
      else if (!preview) toast(t('yourSid', sid));
      groups = r.groups || [];
      if (groups.length) {
        let saved = null;
        try { saved = localStorage.getItem(`${key}:group`); } catch { /* not remembered */ }
        if (saved && groups.some(g => g.name === saved) && body.resume) { group = saved; return next(); }
        return pickGroup();
      }
      next();
    } catch (e) {
      if (body.resume && e.status === 404) store.set(null);
      toast(e.message);
    }
  }

  // ---------- groups: choose one (e.g. a language) and label within it
  async function pickGroup() {
    try { groups = (await api('GET', `/groups?session=${encodeURIComponent(sid)}`)).groups; } catch { /* keep the last list */ }
    const everyone = design.settings.labels_per_item === 0;
    $('.change-group').hidden = true;
    screen(h('div', { class: 'card center wide' },
      h('h1', {}, `Choose a ${groupLabel().toLowerCase()}`),
      h('p', { class: 'lead' }, `Pick the ${groupLabel().toLowerCase()} you know well. You can switch at any time.`),
      h('input', { class: 'input group-search', placeholder: `Search ${groupLabel().toLowerCase()}s`, oninput: (e) => {
        const q = e.target.value.trim().toLowerCase();
        document.querySelectorAll('.group').forEach(b => { b.hidden = q && !b.dataset.name.toLowerCase().includes(q); });
      } }),
      h('div', { class: 'groups' }, groups.map(g => {
        const done = everyone ? g.mine : g.complete;
        return h('button', { class: 'group', type: 'button', 'data-name': g.name, onclick: () => choose(g.name) },
          h('b', {}, g.name), h('span', {}, everyone ? `${g.mine} of ${g.items} done by you` : `${g.complete} of ${g.items} complete`),
          h('i', { class: 'bar' }, h('i', { style: `width:${g.items ? (done / g.items) * 100 : 0}%` })));
      }))));
  }
  function choose(name) {
    group = name;
    try { localStorage.setItem(`${key}:group`, name); } catch { /* not remembered */ }
    next();
  }
  $('.change-group').addEventListener('click', () => pickGroup());

  async function next() {
    screen(h('div', { class: 'card center' }, h('div', { class: 'spin', 'aria-label': 'Loading' })));
    $('.change-group').hidden = !groups.length;
    try {
      const r = await api('GET', `/next?session=${encodeURIComponent(sid)}${group ? `&group=${encodeURIComponent(group)}` : ''}`);
      setProgress(r.progress);
      if (!r.item) return done(r.progress);
      item = r.item;
      answers = {}; heard = false; plays = 0; pos = null;
      work();
    } catch (e) {
      if (e.status === 404) { store.set(null); sid = null; showSid(); intro(); toast(e.message); }
      else fail(e.message);
    }
  }

  function done(p) {
    const limitHit = p?.limit && p.mine >= p.limit;
    if (group && !limitHit) {
      return screen(h('div', { class: 'card center' },
        h('div', { class: 'done-icon' }, svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>')),
        h('h1', {}, `${group}: all done`),
        h('p', { class: 'lead' }, `There is nothing left for you to label in ${group}. Thank you!`),
        h('button', { class: 'btn primary block', type: 'button', onclick: () => pickGroup() }, `Choose another ${groupLabel().toLowerCase()}`),
        sid && !preview ? [h('p', { class: 'muted', style: 'margin-top:16px' }, 'Your session ID'), h('div', { class: 'big-sid' }, sid)] : null));
    }
    const comment = design.settings.final_comment ? h('textarea', { class: 'input', rows: 3 }) : null;
    screen(h('div', { class: 'card center' },
      h('div', { class: 'done-icon' }, svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>')),
      h('h1', {}, t('thanks')),
      h('p', { class: 'lead' }, ui() === 'fa' ? t('nothingLeft') : limitHit ? `You labelled ${p.mine} items, the number this task asks of each person.`
        : `There is nothing left for you to label${p?.mine ? `: you labelled ${p.mine} items` : ''}.`),
      comment ? [h('label', { class: 'f' }, t('overall'), comment),
        h('button', { class: 'btn block', type: 'button', style: 'margin-bottom:14px', onclick: async (e) => {
          try { await api('POST', '/comment', { session: sid, text: comment.value }); toast(t('sent')); e.target.disabled = true; } catch (err) { toast(err.message); }
        } }, t('send'))] : null,
      design.settings.allow_back && history.length ? h('button', { class: 'btn block', type: 'button', style: 'margin-bottom:14px', onclick: back }, t('back')) : null,
      sid && !preview ? [h('p', { class: 'muted' }, t('sid')), h('div', { class: 'big-sid' }, sid)] : null));
  }

  // ---------- the item and the questions
  function mediaView(it) {
    if (it.pending) {
      return h('div', { class: 'pending' },
        svg('<path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/><path d="M14 8v7"/><path d="M18 5v13"/><path d="M22 10v3"/>', 'pending-ico'),
        h('b', {}, `The ${it.kind === 'audio' ? 'recording' : it.kind} for this item is not uploaded yet`),
        h('span', {}, 'It will be added soon. This page already shows how the task works.'));
    }
    if (it.kind === 'text') {
      const info = Object.entries(it.info || {});
      return h('div', { class: 'text' }, h('blockquote', {}, it.text),
        info.length ? h('div', { class: 'chips' }, info.map(([k, v]) => h('span', { class: 'chip' }, `${k}: ${v}`))) : null);
    }
    if (it.kind === 'image') {
      const img = h('img', { src: it.src, alt: 'The item to label', onclick: () => img.classList.toggle('zoom') });
      return img;
    }
    if (it.kind === 'video') {
      if (it.playable === false) return h('div', { class: 'cant' }, 'This video format cannot be played in a browser. Please skip it.');
      return h('video', { src: it.src, controls: true, playsinline: true, preload: 'metadata' });
    }
    const audio = h('audio', { src: it.src, controls: true, preload: 'auto' });
    audio.addEventListener('play', () => { plays++; });
    audio.addEventListener('ended', () => { heard = true; lockRatings(); });
    const playIcon = '<path d="M8 5.5v13l11-6.5z"/>', pauseIcon = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';
    const btn = h('button', { class: 'play', type: 'button', 'aria-label': 'Play', onclick: () => (audio.paused ? audio.play() : audio.pause()) }, svg(playIcon));
    audio.addEventListener('play', () => { btn.replaceChildren(svg(pauseIcon)); btn.setAttribute('aria-label', 'Pause'); });
    audio.addEventListener('pause', () => { btn.replaceChildren(svg(playIcon)); btn.setAttribute('aria-label', 'Play'); });
    return h('div', { class: 'audio' }, it.text ? h('p', { class: 'stim', dir: 'auto' }, it.text) : null, btn, audio, h('p', { class: 'hint' }, t('clipHint')));
  }

  function question(f, idx) {
    const head = [h('p', { class: 'q-label', id: `q-${f.id}` }, fill(f.label), f.required ? h('span', { class: 'req', 'aria-hidden': 'true' }, '*') : null),
      f.help ? h('p', { class: 'q-help' }, fill(f.help)) : null];
    if (f.type === 'choice' || f.type === 'multi') {
      const multi = f.type === 'multi';
      const box = h('div', { class: 'opts', role: multi ? 'group' : 'radiogroup', 'aria-labelledby': `q-${f.id}` });
      const paint = () => [...box.children].forEach(b => {
        const on = multi ? (answers[f.id] || []).includes(b.dataset.v) : answers[f.id] === b.dataset.v;
        b.setAttribute(multi ? 'aria-pressed' : 'aria-checked', String(on));
      });
      f.options.forEach((o, i) => box.append(h('button', {
        class: 'opt', type: 'button', role: multi ? null : 'radio', 'data-v': o,
        onclick: () => {
          if (multi) { const cur = new Set(answers[f.id] || []); cur.has(o) ? cur.delete(o) : cur.add(o); answers[f.id] = [...cur]; }
          else answers[f.id] = o;
          paint(); $('.err').textContent = '';
        },
      }, o, idx === 0 && i < 9 ? h('kbd', {}, String(i + 1)) : null)));
      paint();
      return h('div', { class: 'q' }, head, box);
    }
    if (f.type === 'rating') {
      const box = h('div', { class: 'scale', role: 'radiogroup', 'aria-labelledby': `q-${f.id}` });
      for (let v = f.min; v <= f.max; v++) {
        box.append(h('button', { class: 'opt', type: 'button', role: 'radio', 'aria-checked': String(+answers[f.id] === v), 'data-v': v,
          onclick: () => { answers[f.id] = v; [...box.children].forEach(b => b.setAttribute('aria-checked', String(+b.dataset.v === v))); $('.err').textContent = ''; } }, num(v)));
      }
      return h('div', { class: 'q' }, head, box,
        f.min_label || f.max_label ? h('div', { class: 'scale-ends' }, h('span', {}, f.min_label || ''), h('span', {}, f.max_label || '')) : null);
    }
    const input = h(f.multiline ? 'textarea' : 'input', { class: 'input', placeholder: f.placeholder || '', 'aria-labelledby': `q-${f.id}`,
      dir: 'auto', oninput: (e) => { answers[f.id] = e.target.value; } });
    input.value = answers[f.id] || '';
    return h('div', { class: 'q' }, head, input);
  }

  // must listen to the end: the answers open once the clip has played through (or when going back to it)
  function lockRatings() {
    const locked = design.settings.must_listen && item?.kind === 'audio' && !item.pending && !heard;
    document.querySelectorAll('.questions .opt').forEach(b => { b.disabled = locked; });
    const hint = $('.listen-first');
    if (hint) hint.textContent = locked ? t('listenFirst') : '';
  }

  function work() {
    const stacked = design.look?.layout === 'stacked';
    const listening = kind === 'listening';
    const submit = h('button', { class: 'btn primary', type: 'button', onclick: send }, listening ? t('next') : t('submit'), h('kbd', {}, 'Enter'));
    const skipBtn = design.settings.allow_skip ? h('button', { class: 'btn', type: 'button', onclick: skip }, t('skip')) : null;
    const backBtn = design.settings.allow_back && !preview && history.length && (pos === null || pos > 0)
      ? h('button', { class: 'btn', type: 'button', onclick: back }, t('back')) : null;
    screen(h('div', { class: `work${stacked ? ' stacked' : ''}` },
      h('section', { class: 'card media', 'aria-label': 'Item' },
        item.practice ? h('div', { class: 'practice-tag' }, t('practice')) : null,
        mediaView(item), item.kind !== 'text' && !listening ? h('span', { class: 'name' }, item.name) : null),
      h('section', { class: 'card questions', 'aria-label': 'Questions' },
        h('p', { class: 'listen-first', role: 'status' }),
        design.fields.map(question),
        h('p', { class: 'err', role: 'alert' }),
        h('div', { class: 'actions' }, backBtn, skipBtn, h('span', { class: 'grow' }), submit))));
    shownAt = performance.now();
    lockRatings();
  }

  // back: the previous clip with this person's answer, to change it; then on to where they were
  async function back() {
    const at = pos === null ? history.length : pos;
    if (at <= 0) return;
    try {
      const r = await api('GET', `/answered/${encodeURIComponent(history[at - 1])}?session=${encodeURIComponent(sid)}`);
      pos = at - 1; item = r.item; answers = { ...r.values }; heard = true; plays = 0;
      work();
    } catch (e) { toast(e.message); }
  }

  async function send() {
    if (busy) return;
    const missing = design.fields.find(f => f.required && (answers[f.id] == null || answers[f.id] === '' || (Array.isArray(answers[f.id]) && !answers[f.id].length)));
    if (design.settings.must_listen && item.kind === 'audio' && !item.pending && !heard) { $('.err').textContent = t('listenFirst'); return; }
    if (missing) { $('.err').textContent = t('answer', fill(missing.label)); return; }
    busy = true;
    try {
      const r = await api('POST', '/answer', { session: sid, item: item.id, values: answers, ms: Math.round(performance.now() - shownAt), plays });
      if (r.preview) toast('Preview: the answer was checked, not saved.');
      if (r.history) history = r.history;
      if (pos !== null && pos < history.length - 1) {  // went back: walk forward through what they already did
        const nxt = history[pos + 1];
        const a = await api('GET', `/answered/${encodeURIComponent(nxt)}?session=${encodeURIComponent(sid)}`);
        pos += 1; item = a.item; answers = { ...a.values }; heard = true; plays = 0;
        return work();
      }
      next();
    } catch (e) {
      $('.err').textContent = e.message;
    } finally { busy = false; }
  }

  async function skip() {
    if (busy) return;
    busy = true;
    try { await api('POST', '/skip', { session: sid, item: item.id }); next(); }
    catch (e) { $('.err').textContent = e.message; }
    finally { busy = false; }
  }

  // keys: 1-9 answer the first list question, Enter submits, Space plays audio (not while typing)
  document.addEventListener('keydown', (e) => {
    if (!item || !$('.questions')) return;
    const typing = e.target.closest?.('input, textarea');
    if (e.key === 'Enter' && !e.shiftKey && !(typing && e.target.tagName === 'TEXTAREA')) { e.preventDefault(); send(); return; }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^[1-9]$/.test(e.key)) $('.questions .opts')?.children[+e.key - 1]?.click();
    if (e.key === ' ' && item.kind === 'audio') { e.preventDefault(); $('.play')?.click(); }
  });

  // the studio's designer sends each new design to its preview
  addEventListener('message', (e) => {
    if (!preview || e.origin !== location.origin || e.data?.type !== 'omni-design') return;
    applyDesign(e.data.design);
    if (item && $('.questions')) { answers = {}; work(); } else if (!sid) intro(); else if ($('.groups')) pickGroup();
  });

  // ---------- start
  (async () => {
    if (!token) return fail('This link is incomplete. Ask the person who shared it for the full link.');
    try {
      const r = await api('GET', '');
      kind = r.kind || 'labelling';
      applyDesign(r.design);
      groups = r.groups || [];
      if (preview) $('.preview-bar').hidden = false;
      const fromUrl = params.get('s');
      if (fromUrl && !preview) return start({ resume: fromUrl });
      intro();
    } catch (e) { fail(e.message); }
  })();
})();
