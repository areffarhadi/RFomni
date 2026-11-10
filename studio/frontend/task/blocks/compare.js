// Comparisons: several items on one screen (backend/blocks.py). The server sends the screen with the item: its items
// (A, B, C…), X for ABX and the reference for MUSHRA, by addresses that do not give them away. The answer is to the
// whole screen; the server splits it into one answer per item.
//   ab_choice: A or B is better (or no difference)       value: the better item's id, or "same"
//   ab_scale:  how much better B is than A, -3 to +3       value: a number
//   abx:       is X A or B                                value: the id of A or B
//   mushra:    every version scored 0-100 (reference)     value: {item id: score}
//   best_worst: the best and the worst                    value: {best: id, worst: id}
//   pick_items: the one (or ones) that fit                value: [ids]
(() => {
  const B = (window.OmniBlocks = window.OmniBlocks || {});
  const STR = {
    en: { better: (x) => `${x} is better`, same: 'No difference', xis: (x) => `X is ${x}`, ref: 'Reference', best: 'Best', worst: 'Worst',
      play: (x) => `Play ${x}`, pause: (x) => `Pause ${x}`, drag: 'Drag', pickOne: 'Pick one', pickMany: 'Pick all that fit',
      cmos: ['A is much better', 'A is better', 'A is slightly better', 'About the same', 'B is slightly better', 'B is better', 'B is much better'],
      bands: ['Bad', 'Poor', 'Fair', 'Good', 'Excellent'], scoreOf: (x) => `Score for ${x}` },
    fa: { better: (x) => `${x} بهتر است`, same: 'تفاوتی ندارند', xis: (x) => `X همان ${x} است`, ref: 'مرجع', best: 'بهترین', worst: 'بدترین',
      play: (x) => `پخش ${x}`, pause: (x) => `توقف ${x}`, drag: 'بکشید', pickOne: 'یکی را انتخاب کنید', pickMany: 'همه‌ی موارد درست را انتخاب کنید',
      cmos: ['A خیلی بهتر است', 'A بهتر است', 'A کمی بهتر است', 'تقریباً یکسان', 'B کمی بهتر است', 'B بهتر است', 'B خیلی بهتر است'],
      bands: ['بد', 'ضعیف', 'متوسط', 'خوب', 'عالی'], scoreOf: (x) => `امتیاز ${x}` },
  };
  const LETTERS = 'ABCDEFGHIJKL';
  const PLAY = '<path d="M8 5.5v13l11-6.5z"/>', PAUSE = '<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>';

  // one item of the screen: a play button for a clip, the picture, the video or the text, under its letter
  function card(ctx, it, letter, key, players) {
    const { h, svg } = ctx;
    const s = STR[ctx.lang] || STR.en;
    const head = h('span', { class: 'cmp-letter', 'aria-hidden': 'true' }, letter);
    if (it.kind === 'audio') {
      const audio = h('audio', { src: it.src, preload: 'auto' });
      const btn = h('button', { class: 'cmp-play', type: 'button', 'data-key': key, 'aria-label': s.play(letter) }, svg(PLAY), head);
      const bar = h('i', { class: 'cmp-bar' }, h('b'));
      btn.addEventListener('click', () => {
        if (!audio.paused) return audio.pause();
        const now = players.find(p => p !== audio && !p.paused);   // switching while one plays: go on at the same moment
        if (now) { audio.currentTime = Math.min(now.currentTime, (audio.duration || 1e9) - 0.05); now.pause(); }
        audio.play().catch(() => {});
      });
      audio.addEventListener('play', () => { btn.replaceChildren(svg(PAUSE), head); btn.setAttribute('aria-label', s.pause(letter)); btn.classList.add('on'); });
      audio.addEventListener('pause', () => { btn.replaceChildren(svg(PLAY), head); btn.setAttribute('aria-label', s.play(letter)); btn.classList.remove('on'); });
      audio.addEventListener('timeupdate', () => { bar.firstChild.style.width = `${audio.duration ? (audio.currentTime / audio.duration) * 100 : 0}%`; });
      players.push(audio);
      ctx.track(audio);
      return h('div', { class: 'cmp-card audio' }, btn, bar, audio);
    }
    if (it.kind === 'video') {
      const video = h('video', { src: it.src, controls: true, playsinline: true, preload: 'metadata' });
      video.addEventListener('play', () => players.forEach(p => p !== video && p.pause()));
      players.push(video);
      ctx.track(video);
      return h('div', { class: 'cmp-card video' }, h('div', { class: 'cmp-head' }, head), video);
    }
    if (it.kind === 'image') {
      const img = h('img', { src: it.src, alt: letter, loading: 'eager', onclick: () => img.classList.toggle('zoom') });
      return h('div', { class: 'cmp-card image' }, h('div', { class: 'cmp-head' }, head), img);
    }
    return h('div', { class: 'cmp-card text' }, h('div', { class: 'cmp-head' }, head), h('blockquote', { dir: 'auto' }, it.text || ''));
  }

  // the cards of a screen: A, B… (and X or the reference), one clip playing at a time
  function cards(ctx, opts = {}) {
    const sc = ctx.screen, players = [];
    const s = STR[ctx.lang] || STR.en;
    const out = [];
    if (sc.ref && opts.ref) out.push(card(ctx, sc.ref, s.ref, 'r', players));
    if (opts.list !== false) sc.items.forEach((it, i) => out.push(card(ctx, it, LETTERS[i], LETTERS[i].toLowerCase(), players)));
    if (sc.x) out.push(card(ctx, sc.x, 'X', 'x', players));
    players.forEach(p => p.addEventListener('play', () => players.forEach(o => o !== p && !o.paused && o.pause())));
    const kind = sc.items[0]?.kind || 'audio';
    return { el: ctx.h('div', { class: `cmp cmp-${kind} n${out.length}` }, out), players };
  }

  // a row of answer buttons, like the page's own (.opts): the digit keys pick them
  function opts(ctx, f, list, multi, onPick) {
    const { h } = ctx;
    const box = h('div', { class: 'opts', role: multi ? 'group' : 'radiogroup' });
    const paint = () => [...box.children].forEach(b => {
      const v = ctx.value;
      const on = multi ? (v || []).includes(b.dataset.v) : v === b.dataset.v;
      b.setAttribute(multi ? 'aria-pressed' : 'aria-checked', String(on));
    });
    list.forEach(([v, label], i) => box.append(h('button', { class: 'opt', type: 'button', role: multi ? null : 'radio', 'data-v': v, 'data-lock': true,
      onclick: () => {
        if (multi) { const cur = new Set(ctx.value || []); cur.has(v) ? cur.delete(v) : cur.add(v); ctx.value = [...cur]; }
        else ctx.value = v;
        ctx.set(multi && !ctx.value.length ? null : ctx.value);
        paint(); onPick?.();
      } }, label, ctx.keys && i < 9 ? h('kbd', {}, ctx.num(i + 1)) : null)));
    paint();
    return box;
  }

  const ids = (ctx) => ctx.screen.items.map(i => i.id);

  B.ab_choice = {
    mount(f, ctx) {
      const s = STR[ctx.lang] || STR.en, [a, b] = ids(ctx);
      const list = [[a, s.better('A')], [b, s.better('B')], ...(f.same ? [['same', s.same]] : [])];
      return { media: cards(ctx).el, answer: opts(ctx, f, list, false) };
    },
  };

  B.abx = {
    mount(f, ctx) {
      const s = STR[ctx.lang] || STR.en, [a, b] = ids(ctx);
      return { media: cards(ctx).el, answer: opts(ctx, f, [[a, s.xis('A')], [b, s.xis('B')]], false) };
    },
  };

  B.ab_scale = {
    mount(f, ctx) {
      const { h } = ctx, s = STR[ctx.lang] || STR.en;
      const box = h('div', { class: 'scale labelled cmp-cmos', role: 'radiogroup' });
      const paint = () => [...box.children].forEach(x => x.setAttribute('aria-checked', String(+x.dataset.v === ctx.value)));
      for (let v = -3; v <= 3; v++) {
        box.append(h('button', { class: 'opt', type: 'button', role: 'radio', 'data-v': v, 'data-lock': true,
          onclick: () => { ctx.value = v; ctx.set(v); paint(); } }, (v > 0 ? '+' : '') + ctx.num(v).replace('-', '−'), h('small', {}, s.cmos[v + 3])));
      }
      paint();
      return { media: cards(ctx).el, answer: box };
    },
    answered: (f, v) => typeof v === 'number',
  };

  // MUSHRA: the reference on top; a row per version with its play button and a slider from 0 to 100
  B.mushra = {
    mount(f, ctx) {
      const { h } = ctx, s = STR[ctx.lang] || STR.en;
      const val = { ...(ctx.value || {}) };
      const players = [];
      const ref = ctx.screen.ref ? card(ctx, ctx.screen.ref, s.ref, 'r', players) : null;
      const rows = ctx.screen.items.map((it, i) => {
        const letter = LETTERS[i];
        const c = card(ctx, it, letter, letter.toLowerCase(), players);
        const has = val[it.id] != null;
        const out = h('output', { class: `cmp-score${has ? '' : ' idle'}` }, has ? ctx.num(val[it.id]) : '—');
        const input = h('input', { type: 'range', class: `slider${has ? '' : ' idle'}`, min: 0, max: 100, step: 1, value: has ? val[it.id] : 50,
          'data-lock': true, 'aria-label': s.scoreOf(letter) });
        const set = () => {
          val[it.id] = +input.value;
          out.textContent = ctx.num(input.value); out.classList.remove('idle'); input.classList.remove('idle');
          input.setAttribute('aria-valuetext', input.value);
          ctx.set(Object.keys(val).length ? { ...val } : null);
        };
        input.addEventListener('input', set);
        input.addEventListener('pointerup', set);
        return h('div', { class: 'cmp-row' }, c, input, out);
      });
      players.forEach(p => p.addEventListener('play', () => players.forEach(o => o !== p && !o.paused && o.pause())));
      const bands = h('div', { class: 'cmp-bands', 'aria-hidden': 'true' }, s.bands.map(b => h('span', {}, b)));
      return { media: ref ? h('div', { class: 'cmp cmp-ref' }, ref) : null, answer: h('div', { class: 'cmp-mushra' }, bands, rows) };
    },
    answered: (f, v) => !!v && Object.keys(v).length > 0,
  };

  B.best_worst = {
    mount(f, ctx) {
      const { h } = ctx, s = STR[ctx.lang] || STR.en;
      const val = { ...(ctx.value || {}) };
      const row = (which, label) => {
        const box = h('div', { class: 'opts cmp-letters', role: 'radiogroup', 'aria-label': label });
        const paint = () => [...box.children].forEach(b => b.setAttribute('aria-checked', String(val[which] === b.dataset.v)));
        ctx.screen.items.forEach((it, i) => box.append(h('button', { class: 'opt', type: 'button', role: 'radio', 'data-v': it.id, 'data-lock': true,
          onclick: () => {
            val[which] = it.id;
            const other = which === 'best' ? 'worst' : 'best';
            if (val[other] === it.id) delete val[other];   // one item cannot be both
            ctx.set(val.best && val.worst ? { ...val } : null);
            rows.forEach(r => r.paint());
          } }, LETTERS[i])));
        return Object.assign(h('div', { class: 'cmp-bw' }, h('span', { class: 'cmp-bw-l' }, label), box), { paint });
      };
      const rows = [row('best', s.best), row('worst', s.worst)];
      rows.forEach(r => r.paint());
      return { media: cards(ctx).el, answer: h('div', { class: 'cmp-bws' }, rows) };
    },
    answered: (f, v) => !!(v && v.best && v.worst && v.best !== v.worst),
  };

  B.pick_items = {
    mount(f, ctx) {
      const s = STR[ctx.lang] || STR.en;
      const c = cards(ctx);
      const mark = () => [...c.el.children].forEach((el, i) => {
        const id = ctx.screen.items[i]?.id;
        el.classList.toggle('picked', !!id && (ctx.value || []).includes(id));
      });
      const box = opts(ctx, f, ctx.screen.items.map((it, i) => [it.id, LETTERS[i]]), true, () => {
        if (!f.several && (ctx.value || []).length > 1) { ctx.value = ctx.value.slice(-1); ctx.set(ctx.value); [...box.children].forEach(b => b.setAttribute('aria-pressed', String(ctx.value.includes(b.dataset.v)))); }
        mark();
      });
      box.classList.add('cmp-letters');
      mark();
      return { media: c.el, answer: ctx.h('div', {}, ctx.h('p', { class: 'q-help' }, f.several ? s.pickMany : s.pickOne), box) };
    },
    answered: (f, v) => Array.isArray(v) && v.length > 0,
  };
})();
