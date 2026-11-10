// Form blocks for the volunteer page: a long list to pick from (dropdown), a number box, putting answers in order
// (rank), several aspects on one scale (grid), a box that starts with the item's own text (prefill) and highlighting
// words in a text item (spans). A plain script loaded before task.js; each block registers itself on window.OmniBlocks.
(() => {
  const B = (window.OmniBlocks = window.OmniBlocks || {});
  const FA = (d) => String(d).replace(/[0-9]/g, (c) => '۰۱۲۳۴۵۶۷۸۹'[c]);
  const STR = {
    en: { choose: 'Choose…', search: 'Type to search', noMatch: 'Nothing matches.', found: (n) => (n === 1 ? '1 answer' : `${n} answers`),
      showAll: 'Show all', notNum: 'Please type a number.', whole: 'Please type a whole number.',
      decimals: (d) => (d === 1 ? 'Use at most one decimal.' : `Use at most ${d} decimals.`), range: (a, b) => `Please type a number from ${a} to ${b}.`,
      rankHint: 'Best at the top. Drag the rows or use the arrows.', up: (o) => `Move up: ${o}`, down: (o) => `Move down: ${o}`,
      orderOk: 'This order is right', place: (o, i, n) => `${o}: ${i} of ${n}`, again: 'Start again',
      pick: (tap) => `Pick a label, then select words in the text, or ${tap ? 'tap' : 'click'} the first and the last word.`,
      pickNoLabels: (tap) => `Select words in the text, or ${tap ? 'tap' : 'click'} the first and the last word.`, last: 'Now pick the last word.',
      overlap: 'That overlaps another highlight. Remove that one first.', cancel: 'Cancel', remove: 'Remove',
      removeX: (t) => `Remove “${t}”`, label: 'Label', marks: 'Your highlights', noMarks: 'No highlights yet.',
      noText: 'This item has no text to highlight.', textHelp: 'Text to highlight. Arrow keys move between words; Enter starts and ends a highlight.' },
    fa: { choose: 'انتخاب کنید…', search: 'برای جست‌وجو بنویسید', noMatch: 'چیزی پیدا نشد.', found: (n) => `${FA(n)} گزینه`,
      showAll: 'نمایش همه', notNum: 'لطفاً یک عدد بنویسید.', whole: 'لطفاً یک عدد صحیح بنویسید.',
      decimals: (d) => `حداکثر ${FA(d)} رقم اعشار مجاز است.`, range: (a, b) => `لطفاً عددی از ${FA(a)} تا ${FA(b)} بنویسید.`,
      rankHint: 'بهترین در بالا. ردیف‌ها را بکشید یا از پیکان‌ها استفاده کنید.', up: (o) => `بالاتر: ${o}`, down: (o) => `پایین‌تر: ${o}`,
      orderOk: 'این ترتیب درست است', place: (o, i, n) => `${o}: ${i} از ${n}`, again: 'بازگشت به متن اولیه',
      pick: 'یک برچسب انتخاب کنید، سپس واژه‌ها را در متن انتخاب کنید یا روی واژه‌ی اول و آخر بزنید.',
      pickNoLabels: 'واژه‌ها را در متن انتخاب کنید یا روی واژه‌ی اول و آخر بزنید.', last: 'حالا واژه‌ی آخر را انتخاب کنید.',
      overlap: 'این بخش با بخش برچسب‌خورده‌ی دیگری هم‌پوشانی دارد. اول آن را حذف کنید.', cancel: 'لغو', remove: 'حذف',
      removeX: (t) => `حذف «${t}»`, label: 'برچسب', marks: 'بخش‌های برچسب‌خورده', noMarks: 'هنوز بخشی برچسب نخورده است.',
      noText: 'این نمونه متنی برای برچسب‌گذاری ندارد.', textHelp: 'متن برای برچسب‌گذاری. با کلیدهای پیکان بین واژه‌ها بروید؛ Enter آغاز و پایان بخش را تعیین می‌کند.' },
  };
  const say = (ctx, k, ...a) => { const v = STR[ctx.lang]?.[k] ?? STR.en[k]; return typeof v === 'function' ? v(...a) : v; };
  let uid = 0;
  const newId = (part) => `ob-${part}-${++uid}`;
  const ICON = { down: '<path d="M6 9l6 6 6-6"/>', up: '<path d="M6 15l6-6 6 6"/>', check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>', again: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/>',
    grip: '<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>' };
  const icon = (ctx, name) => ctx.svg(ICON[name], `ob-ico${name === 'grip' ? ' fill' : ''}`);
  // Enter and Space press the focused button here; the page would take them otherwise (Enter submits, Space plays)
  const ownKeys = (el) => {
    el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.closest?.('button')) e.stopPropagation(); });
    return el;
  };
  // the page sets `disabled` on [data-lock] until the clip has been heard; a fieldset passes it to everything inside
  const lockBox = (h, cls, ...kids) => h('fieldset', { class: `ob-lock ${cls}`, 'data-lock': true }, ...kids);

  // ---------- dropdown: one answer from a long list (choice with dropdown: true)
  B.dropdown = {
    mount(f, ctx) {
      const { h } = ctx, opts = f.options || [], lab = `q-${f.id}`;
      let chosen = opts.includes(ctx.value) ? ctx.value : null;
      if (opts.length <= 12) {   // a short list: the browser's own menu
        const sel = h('select', { class: 'input', 'aria-labelledby': lab, onchange: () => ctx.set(sel.value || null) },
          h('option', { value: '' }, say(ctx, 'choose')), opts.map((o) => h('option', { value: o }, o)));
        sel.value = chosen ?? '';
        return { media: null, answer: lockBox(h, 'ob-drop', sel) };
      }
      // a long list: typing narrows it down (best matches first); arrows and Enter pick, Escape closes
      const listId = newId('list');
      const fold = (s) => s.normalize('NFKD').replace(/\p{M}/gu, '').replace(/[يى]/g, 'ی').replace(/ك/g, 'ک').toLowerCase().trim();
      const keys = opts.map(fold);
      const items = opts.map((o, i) => h('li', { role: 'option', id: `${listId}-${i}`, class: 'ob-option', dir: 'auto' }, o));
      const list = h('ul', { class: 'ob-listbox', role: 'listbox', id: listId, 'aria-labelledby': lab }, items);
      const none = h('p', { class: 'ob-none' }, say(ctx, 'noMatch'));
      const pop = h('div', { class: 'ob-pop', hidden: true }, list, none);
      const status = h('span', { class: 'sr-only', role: 'status' });
      const input = h('input', { class: 'input ob-combo', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
        'aria-controls': listId, 'aria-labelledby': lab, autocomplete: 'off', spellcheck: 'false', placeholder: say(ctx, 'search') });
      input.value = chosen ?? '';
      let shown = [];
      const isOpen = () => !pop.hidden;
      function filter() {
        const q = fold(input.value), all = !q || input.value === chosen;
        const rank = (k) => (k.startsWith(q) ? 0 : ` ${k}`.includes(` ${q}`) ? 1 : 2);
        shown = opts.map((_, i) => i).filter((i) => all || keys[i].includes(q));
        if (!all) shown.sort((a, b) => rank(keys[a]) - rank(keys[b]) || a - b);
        const on = new Set(shown);
        items.forEach((li, i) => { li.hidden = !on.has(i); });
        list.append(...shown.map((i) => items[i]));
        none.hidden = shown.length > 0;
        status.textContent = say(ctx, 'found', shown.length);
      }
      function setActive(k) {   // k: a place in the shown list, -1 for none
        list.querySelector('.active')?.classList.remove('active');
        const li = items[shown[k]];
        if (!li) return input.removeAttribute('aria-activedescendant');
        li.classList.add('active');
        input.setAttribute('aria-activedescendant', li.id);
        li.scrollIntoView({ block: 'nearest' });
      }
      const activeAt = () => shown.findIndex((i) => items[i].classList.contains('active'));
      function openList(start) {   // start: 'none', 'first' or 'chosen'
        filter();
        pop.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        const at = shown.indexOf(opts.indexOf(chosen));
        setActive(start === 'first' ? 0 : start === 'chosen' ? at : -1);
      }
      function close() { pop.hidden = true; input.setAttribute('aria-expanded', 'false'); setActive(-1); }
      const paint = () => items.forEach((li, i) => li.setAttribute('aria-selected', String(opts[i] === chosen)));
      function pick(i) { chosen = opts[i]; input.value = chosen; paint(); close(); ctx.set(chosen); }
      // leaving the box: an exact name counts, an empty box clears the answer, anything else goes back to the answer
      function settle() {
        const q = fold(input.value), hit = keys.indexOf(q);
        if (q && hit >= 0 && opts[hit] !== chosen) return pick(hit);
        if (!q && chosen !== null) { chosen = null; paint(); ctx.set(null); }
        input.value = chosen ?? '';
        close();
      }
      input.addEventListener('input', () => openList(input.value.trim() ? 'first' : 'none'));
      input.addEventListener('focus', () => input.select());
      input.addEventListener('click', () => { if (input.value === chosen) input.select(); if (!isOpen()) openList('chosen'); });
      input.addEventListener('blur', settle);
      input.addEventListener('keydown', (e) => {
        const open = isOpen();
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          if (!open) return openList(chosen ? 'chosen' : 'first');
          const at = activeAt();
          setActive(Math.max(0, Math.min(shown.length - 1, at + (e.key === 'ArrowDown' ? 1 : at < 0 ? shown.length : -1))));
        } else if (e.key === 'Enter') {
          if (!open) return settle();   // and the page submits
          e.preventDefault(); e.stopPropagation();
          const at = activeAt();
          if (at >= 0) pick(shown[at]); else if (shown.length === 1) pick(shown[0]); else settle();
        } else if (e.key === 'Escape' && open) { e.preventDefault(); input.value = chosen ?? ''; close(); }
      });
      list.addEventListener('mousedown', (e) => e.preventDefault());   // keep the focus in the box
      list.addEventListener('click', (e) => { const li = e.target.closest('[role=option]'); if (li) pick(items.indexOf(li)); });
      const chev = h('button', { type: 'button', class: 'ob-chev', tabindex: '-1', 'aria-label': say(ctx, 'showAll'), 'aria-controls': listId,
        onmousedown: (e) => e.preventDefault(), onclick: () => { if (isOpen()) close(); else { input.focus(); openList('chosen'); } } }, icon(ctx, 'down'));
      paint();
      return { media: null, answer: lockBox(h, 'ob-drop', h('div', { class: 'ob-combo-box' }, input, chev, pop), status) };
    },
    answered: (f, v) => (f.options || []).includes(v),
  };

  // ---------- number: Persian or Latin digits, checked against min, max and decimals; only a valid number is an answer
  const latin = (s) => s.replace(/[٠-٩۰-۹]/g, (c) => String(c.charCodeAt(0) & 15)).replace(/٫/g, '.').replace(/[٬\s]/g, '').replace(/[−–]/g, '-');
  B.number = {
    mount(f, ctx) {
      const { h } = ctx, dec = f.decimals || 0, lo = f.min ?? 0, hi = f.max ?? 100;
      const msg = h('p', { class: 'ob-msg', id: newId('msg'), 'aria-live': 'polite' });
      const unit = f.unit ? h('span', { class: 'ob-unit', id: newId('unit'), dir: 'auto' }, f.unit) : null;
      const input = h('input', { class: 'input ob-num', type: 'text', inputmode: lo < 0 ? 'text' : dec ? 'decimal' : 'numeric', dir: 'ltr',
        autocomplete: 'off', 'aria-labelledby': `q-${f.id}`, 'aria-describedby': [unit?.id, msg.id].filter(Boolean).join(' '),
        placeholder: `${ctx.num(lo)} – ${ctx.num(hi)}` });
      if (typeof ctx.value === 'number') input.value = ctx.num(ctx.value);
      // {v} a valid number, {} an empty box, {err} what is wrong (now: say it while typing, more typing cannot fix it)
      function check() {
        let s = latin(input.value);
        if (/^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, '');   // 1,500 is fifteen hundred
        s = s.replace(',', '.');                                              // 2,5 is two and a half
        if (!s) return {};
        if (!/^-?(\d+\.?\d*|\.\d+)$/.test(s)) return { err: say(ctx, 'notNum'), now: !/^-?\.?$/.test(s) };
        if ((s.split('.')[1] || '').replace(/0+$/, '').length > dec) return { err: say(ctx, dec ? 'decimals' : 'whole', dec), now: true };
        const v = +s;
        return v < lo || v > hi ? { err: say(ctx, 'range', lo, hi) } : { v: +v.toFixed(dec) };
      }
      const run = (final) => {
        const r = check();
        ctx.set(r.v ?? null);
        const show = r.err && (final || r.now || msg.textContent) ? r.err : '';
        msg.textContent = show;
        input.setAttribute('aria-invalid', String(!!show));
      };
      input.addEventListener('input', () => run(false));
      input.addEventListener('blur', () => run(true));
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(true); });
      return { media: null, answer: lockBox(h, 'ob-number', h('div', { class: 'ob-num-row' }, input, unit), msg) };
    },
    answered: (f, v) => typeof v === 'number' && Number.isFinite(v),
  };

  // ---------- rank: every answer in order, best first; no answer until something moved or the order is confirmed
  B.rank = {
    mount(f, ctx) {
      const { h } = ctx, opts = f.options || [];
      const given = Array.isArray(ctx.value) && ctx.value.length === opts.length && opts.every((o) => ctx.value.includes(o));
      let done = given, drag = null, raf = 0;
      const live = h('p', { class: 'sr-only', 'aria-live': 'polite' });
      const list = h('ol', { class: 'ob-rank', 'aria-labelledby': `q-${f.id}` });
      const ok = h('button', { type: 'button', class: 'opt ob-ok', 'aria-pressed': 'false', onclick: () => { done = true; save(); } },
        icon(ctx, 'check'), say(ctx, 'orderOk'));
      const row = (o) => h('li', { class: 'ob-rank-row', 'data-v': o },
        h('span', { class: 'ob-grip', 'aria-hidden': 'true' }, icon(ctx, 'grip')),
        h('b', { class: 'ob-pos', 'aria-hidden': 'true' }),
        h('span', { class: 'ob-rank-name', dir: 'auto' }, o),
        h('button', { type: 'button', class: 'ob-move', 'data-d': '-1', 'aria-label': say(ctx, 'up', o) }, icon(ctx, 'up')),
        h('button', { type: 'button', class: 'ob-move', 'data-d': '1', 'aria-label': say(ctx, 'down', o) }, icon(ctx, 'down')));
      list.append(...(given ? ctx.value : opts).map(row));
      const rows = () => [...list.children];
      const order = () => rows().map((li) => li.dataset.v);
      function paint() {   // the place numbers, and no arrow past either end
        rows().forEach((li, i, all) => {
          li.querySelector('.ob-pos').textContent = ctx.num(i + 1);
          const [up, down] = li.querySelectorAll('.ob-move');
          up.disabled = i === 0;
          down.disabled = i === all.length - 1;
        });
        ok.setAttribute('aria-pressed', String(done));
      }
      const save = () => { paint(); if (done) ctx.set(order()); };
      function moved(li) {
        done = true;
        save();
        live.textContent = say(ctx, 'place', li.dataset.v, ctx.num(rows().indexOf(li) + 1), ctx.num(opts.length));
      }
      // the arrows move the neighbour, so the row and its focused button stay where they are in the page
      list.addEventListener('click', (e) => {
        const b = e.target.closest('.ob-move');
        if (!b) return;
        const li = b.closest('li'), up = b.dataset.d === '-1', sib = up ? li.previousElementSibling : li.nextElementSibling;
        if (!sib) return;
        if (up) li.after(sib); else li.before(sib);
        moved(li);
        if (b.disabled) li.querySelectorAll('.ob-move')[up ? 1 : 0].focus();
      });
      // dragging: with a mouse anywhere on the row, by touch on the grip (the rest of the row scrolls the page)
      list.addEventListener('pointerdown', (e) => {
        const li = e.target.closest('.ob-rank-row');
        if (!li || e.button > 0 || e.target.closest('button') || box.disabled) return;
        if (e.pointerType !== 'mouse' && !e.target.closest('.ob-grip')) return;
        e.preventDefault();
        li.setPointerCapture(e.pointerId);
        drag = { li, y: e.clientY + scrollY, at: e.clientY, was: order().join('\n') };
        li.classList.add('dragging');
      });
      function follow() {   // once the row's middle passes a neighbour's middle, they swap
        const { li } = drag, y = drag.at + scrollY;
        for (let n = 0; n < opts.length; n++) {
          const mid = li.offsetTop + li.offsetHeight / 2 + y - drag.y, top = li.offsetTop;
          const next = li.nextElementSibling, prev = li.previousElementSibling;
          if (next && mid > next.offsetTop + next.offsetHeight / 2) li.before(next);
          else if (prev && mid < prev.offsetTop + prev.offsetHeight / 2) li.after(prev);
          else break;
          drag.y += li.offsetTop - top;
          paint();
        }
        li.style.transform = `translateY(${y - drag.y}px)`;
      }
      function edge() {   // held near the top or bottom of the window: scroll, so a long list fits a phone
        raf = 0;
        if (!drag) return;
        const v = drag.at < 48 ? -6 : drag.at > innerHeight - 48 ? 6 : 0;
        if (v) { scrollBy(0, v); follow(); raf = requestAnimationFrame(edge); }
      }
      list.addEventListener('pointermove', (e) => {
        if (!drag) return;
        drag.at = e.clientY;
        follow();
        if (!raf) raf = requestAnimationFrame(edge);
      });
      const drop = () => {
        if (!drag) return;
        const { li, was } = drag;
        drag = null;
        li.style.transform = '';
        li.classList.remove('dragging');
        if (order().join('\n') !== was) moved(li);
      };
      ['pointerup', 'pointercancel', 'lostpointercapture'].forEach((t) => list.addEventListener(t, drop));
      const box = lockBox(h, 'ob-rank-box', list, h('div', { class: 'ob-rank-foot' }, ok, h('span', { class: 'hint' }, say(ctx, 'rankHint'))), live);
      save();
      return { media: null, answer: ownKeys(box) };
    },
    answered: (f, v) => Array.isArray(v) && v.length === (f.options || []).length,
  };

  // ---------- grid: each aspect (row) on one scale; a table when there is room, else each row's points under its name
  B.grid = {
    mount(f, ctx) {
      const { h } = ctx, rows = f.rows || [], pts = [];
      for (let v = f.min; v <= f.max; v++) pts.push(v);
      const words = f.labels?.length === pts.length ? f.labels : null;
      const val = {};
      for (const r of rows) if (pts.includes(ctx.value?.[r])) val[r] = ctx.value[r];
      const word = (i) => (words ? words[i] : i === 0 ? f.min_label : i === pts.length - 1 ? f.max_label : '') || '';
      const face = (v, i) => [h('b', {}, ctx.num(v)), words ? h('small', {}, words[i]) : null];
      const store = () => ctx.set(Object.fromEntries(rows.filter((r) => r in val).map((r) => [r, val[r]])));
      const grid = h('div', { class: `ob-grid${pts.length > 7 ? ' many' : ''}`, style: `--pts:${pts.length}` },
        h('div', { class: 'ob-g-head', 'aria-hidden': 'true' }, h('span'), h('div', { class: 'ob-g-pts' }, pts.map((v, i) => h('span', { class: 'ob-g-pt' }, face(v, i))))),
        !words && (f.min_label || f.max_label) ? h('div', { class: 'ob-g-ends', 'aria-hidden': 'true' }, h('span'),
          h('div', { class: 'scale-ends' }, h('span', {}, f.min_label || ''), h('span', {}, f.max_label || ''))) : null,
        rows.map((r) => {
          const nameId = newId('row'), group = newId('pts');
          return h('div', { class: 'ob-g-row', role: 'radiogroup', 'aria-labelledby': nameId },
            h('span', { class: 'ob-g-name', id: nameId, dir: 'auto' }, r),
            h('div', { class: 'ob-g-pts' }, pts.map((v, i) => h('label', { class: 'ob-g-cell', title: word(i) || null },
              h('input', { type: 'radio', name: group, value: v, checked: val[r] === v, 'aria-label': [ctx.num(v), word(i)].filter(Boolean).join(' '),
                onchange: () => { val[r] = v; store(); } }),
              h('span', { class: 'ob-g-face', 'aria-hidden': 'true' }, face(v, i))))));
        }));
      const wrap = lockBox(h, 'ob-grid-wrap', grid);
      const need = 150 + pts.length * (words ? 56 : 40);   // the width a table needs: the names, then a column per point
      new ResizeObserver(([e]) => grid.classList.toggle('table', e.contentRect.width >= need)).observe(wrap);
      return { media: null, answer: wrap };
    },
    answered: (f, v) => !!v && typeof v === 'object' && (f.rows || []).every((r) => typeof v[r] === 'number'),
  };

  // ---------- prefill: a text box that starts with the item's text (an automatic transcript to correct); the text as
  // it stands is the answer, so accepting it unchanged counts too
  B.prefill = {
    mount(f, ctx) {
      const { h } = ctx, orig = String(ctx.item?.text ?? '');
      const box = h(f.multiline !== false ? 'textarea' : 'input', { class: 'input ob-prefill-box', dir: 'auto', 'aria-labelledby': `q-${f.id}`,
        placeholder: f.placeholder || null });
      box.value = typeof ctx.value === 'string' ? ctx.value : orig;
      if (box.tagName === 'TEXTAREA') box.rows = Math.min(12, Math.max(3, Math.ceil(box.value.length / 46) + 1));
      const again = h('button', { type: 'button', class: 'btn ob-small', onclick: () => { box.value = orig; sync(); box.focus(); } },
        icon(ctx, 'again'), say(ctx, 'again'));
      const sync = () => { again.disabled = box.value === orig; ctx.set(box.value.trim() ? box.value : null); };
      box.addEventListener('input', sync);
      if (typeof ctx.value === 'string') again.disabled = box.value === orig; else sync();
      return { media: null, answer: ownKeys(h('div', { class: 'ob-prefill' }, box, orig ? h('div', { class: 'ob-prefill-foot' }, again) : null)) };
    },
    answered: (f, v) => typeof v === 'string' && v.trim() !== '',
  };

  // ---------- spans: highlight words in a text item and give each a label. Marks are kept in UTF-16 positions (the
  // page's) and sent as code points (the server's), so the server's text[start:end] is the highlight even after an emoji.
  const segmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, { granularity: 'word' }) : null;
  const wordsOf = (text) => (segmenter
    ? [...segmenter.segment(text)].filter((s) => s.isWordLike).map((s) => ({ s: s.index, e: s.index + s.segment.length }))
    : [...text.matchAll(/[\p{L}\p{M}\p{N}\u200c\u200d'’_-]+/gu)].map((m) => ({ s: m.index, e: m.index + m[0].length })));
  // does the range hold at least one character of this text node?
  const covers = (r, t) => r.comparePoint(t, t.length) >= 0 && r.comparePoint(t, 0) <= 0
    && !(r.startContainer === t && r.startOffset === t.length) && !(r.endContainer === t && r.endOffset === 0);

  B.spans = {
    mount(f, ctx) {
      const { h } = ctx, text = String(ctx.item?.text ?? ''), labels = f.labels || [];
      if (!text.trim()) return { media: null, answer: h('p', { class: 'hint' }, say(ctx, 'noText')) };
      const words = wordsOf(text);
      const cps = [], toCp = [];   // code point k starts at UTF-16 position cps[k]; toCp goes the other way
      for (let i = 0; i <= text.length; i += text.codePointAt(i) > 0xffff ? 2 : 1) { toCp[i] = cps.length; cps.push(i); }
      const span = (a, b) => {     // the first and last word touching [a, b), or null
        const hit = words.flatMap((w, i) => (w.s < b && w.e > a ? [i] : []));
        return hit.length ? [hit[0], hit[hit.length - 1]] : null;
      };
      const hue = (label) => `var(--hl-${Math.max(0, labels.indexOf(label)) % 8})`;
      let marks = [], cur = 0, sel = -1, anchor = -1, focusW = 0, note = '', dragged = false;
      for (const m of Array.isArray(ctx.value) ? ctx.value : []) {   // going back: draw the earlier answer
        const a = cps[m?.start], b = cps[m?.end], w = a != null && b > a ? span(a, b) : null;
        const label = labels.length ? (labels.includes(m?.label) ? m.label : null) : '';
        if (!w || label === null) continue;
        const s = words[w[0]].s, e = words[w[1]].e;
        if (!marks.some((x) => x.s < e && s < x.e)) marks.push({ s, e, label });
      }
      marks.sort((x, y) => x.s - y.s);

      const msg = h('p', { class: 'ob-msg', 'aria-live': 'polite' });
      const action = h('button', { type: 'button', class: 'btn ob-small', hidden: true, onclick: () => (sel >= 0 ? remove(sel) : reset()) });
      const chips = labels.length ? h('div', { class: 'ob-chips', role: 'group', 'aria-label': say(ctx, 'label') }, labels.map((l, i) =>
        h('button', { type: 'button', class: 'ob-chip', style: `--hl:${hue(l)}`, 'aria-pressed': 'false', onclick: () => chip(i) },
          h('i', { class: 'ob-sw' }), h('bdi', {}, l)))) : null;
      // the text is one stop in the tab order; arrows move a cursor from word to word (words are not focusable:
      // Chromium starts no mouse selection in a focusable element), and a live line reads the word out
      const bq = h('blockquote', { class: 'ob-text', dir: 'auto', tabindex: '0', role: 'group', 'aria-label': say(ctx, 'textHelp'), 'data-own-keys': true });
      const spoken = h('span', { class: 'sr-only', 'aria-live': 'polite' });
      const list = h('ul', { class: 'ob-marks', 'aria-label': say(ctx, 'marks') });
      const empty = h('p', { class: 'hint' }, say(ctx, 'noMarks'));
      const info = Object.entries(ctx.item.info || {});
      const media = ownKeys(h('div', { class: 'text ob-spans' },
        h('div', { class: 'ob-tools' }, chips, h('div', { class: 'ob-status' }, msg, action)), bq, spoken,
        info.length ? h('div', { class: 'chips' }, info.map(([k, v]) => h('span', { class: 'chip' }, `${k}: ${v}`))) : null));

      const save = () => ctx.set(marks.map((m) => ({ start: toCp[m.s], end: toCp[m.e], text: text.slice(m.s, m.e), label: m.label })));
      function draw() {
        const kids = [];
        let w = 0;
        const run = (a, b, into) => {   // words as spans, the text between them as it is
          for (; w < words.length && words[w].s < b; w++) {
            const W = words[w], el = document.createElement('span');
            if (W.s > a) into.push(text.slice(a, W.s));
            el.className = `ob-w${w === anchor ? ' ob-start' : ''}${w === focusW ? ' ob-cur' : ''}`;
            el.dataset.i = w;
            el.textContent = text.slice(W.s, W.e);
            into.push(el);
            a = W.e;
          }
          if (a < b) into.push(text.slice(a, b));
        };
        let p = 0;
        marks.forEach((m, k) => {
          run(p, m.s, kids);
          const inner = [];
          run(m.s, m.e, inner);
          kids.push(h('mark', { class: k === sel ? 'ob-hl on' : 'ob-hl', 'data-k': k, 'data-label': m.label || null, style: `--hl:${hue(m.label)}` }, inner));
          p = m.e;
        });
        run(p, text.length, kids);
        bq.replaceChildren(...kids);
        bq.style.setProperty('--hl', hue(labels[cur]));
        const m = marks[sel];
        msg.textContent = note || (m ? `“${text.slice(m.s, m.e)}”${m.label ? ` · ${m.label}` : ''}`
          : anchor >= 0 ? say(ctx, 'last') : say(ctx, labels.length ? 'pick' : 'pickNoLabels', matchMedia('(hover: none)').matches));
        msg.classList.toggle('bad', !!note);
        action.hidden = !m && anchor < 0;
        action.textContent = say(ctx, m ? 'remove' : 'cancel');
        chips?.querySelectorAll('.ob-chip').forEach((c, i) => c.setAttribute('aria-pressed', String(labels[i] === (m ? m.label : labels[cur]))));
        list.replaceChildren(...marks.map((x, k) => h('li', { class: 'ob-mark', style: `--hl:${hue(x.label)}` },
          h('button', { type: 'button', class: 'ob-m', 'aria-pressed': String(k === sel), onclick: () => choose(k, true) },
            h('i', { class: 'ob-sw' }), h('span', {}, h('bdi', {}, text.slice(x.s, x.e)), x.label ? h('span', { class: 'ob-lab' }, ' · ', h('bdi', {}, x.label)) : null)),
          h('button', { type: 'button', class: 'ob-x', 'aria-label': say(ctx, 'removeX', text.slice(x.s, x.e)), onclick: () => remove(k, true) }, icon(ctx, 'x')))));
        empty.hidden = marks.length > 0;
      }
      function chip(i) {   // the label for new highlights; with a highlight selected, its new label
        cur = i; note = '';
        if (sel >= 0) { marks[sel].label = labels[i]; save(); }
        draw();
      }
      function choose(k, fromList) {
        sel = sel === k ? -1 : k; anchor = -1; note = '';
        draw();
        if (fromList) {
          list.children[k]?.querySelector('.ob-m').focus();
          if (sel >= 0) bq.querySelector(`mark[data-k="${k}"]`)?.scrollIntoView({ block: 'nearest' });
        }
      }
      function remove(k, fromList) {
        marks.splice(k, 1); sel = -1; note = '';
        save(); draw();
        if (fromList) (list.children[Math.min(k, marks.length - 1)]?.querySelector('.ob-x') || bq).focus();
      }
      const reset = () => { sel = -1; anchor = -1; note = ''; draw(); };
      function add(a, b) {   // words a..b; inside a highlight selects it, across one is refused
        const s = words[a].s, e = words[b].e, within = marks.findIndex((m) => m.s <= s && e <= m.e);
        anchor = -1; sel = within; note = '';
        if (within < 0 && marks.some((m) => m.s < e && s < m.e)) note = say(ctx, 'overlap');
        else if (within < 0) { marks = [...marks, { s, e, label: labels[cur] ?? '' }].sort((x, y) => x.s - y.s); save(); }
        draw();
      }
      // a click or tap: on a highlight, select it; on a word, start a highlight, or end the one started
      function tap(el) {
        const m = el.closest('.ob-hl'), w = el.closest('.ob-w');
        note = '';
        if (w) focusW = +w.dataset.i;
        if (m && anchor < 0) return choose(+m.dataset.k);
        if (!w) return reset();
        if (anchor < 0) { anchor = focusW; sel = -1; return draw(); }
        add(Math.min(anchor, focusW), Math.max(anchor, focusW));
      }
      // a mouse selection becomes a highlight of the whole words it touches
      function fromSelection() {
        const s = getSelection();
        if (!s.rangeCount || s.isCollapsed) return;
        const r = s.getRangeAt(0);
        if (!r.intersectsNode(bq)) return;
        const hit = [...bq.querySelectorAll('.ob-w')].filter((el) => covers(r, el.firstChild)).map((el) => +el.dataset.i);
        s.removeAllRanges();
        dragged = true;
        if (hit.length) add(hit[0], hit[hit.length - 1]);
      }
      bq.addEventListener('pointerdown', () => { dragged = false; document.addEventListener('pointerup', fromSelection, { once: true }); });
      bq.addEventListener('click', (e) => { if (dragged) dragged = false; else tap(e.target); });
      const wordAt = (i) => bq.querySelector(`[data-i="${i}"]`);
      function say1(el) { const m = el?.closest('.ob-hl'); spoken.textContent = el ? el.textContent + (m?.dataset.label ? `, ${m.dataset.label}` : '') : ''; }
      bq.addEventListener('focus', () => say1(wordAt(focusW)));
      bq.addEventListener('keydown', (e) => {
        if (e.target !== bq || !words.length) return;
        const rtl = getComputedStyle(bq).direction === 'rtl';
        const go = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1, Home: -1e9, End: 1e9 }[e.key];
        const w = wordAt(focusW), m = w?.closest('.ob-hl');
        if (go) {
          e.preventDefault();
          w?.classList.remove('ob-cur');
          focusW = Math.max(0, Math.min(words.length - 1, focusW + go));
          const n = wordAt(focusW);
          n.classList.add('ob-cur');
          n.scrollIntoView({ block: 'nearest' });
          say1(n);
        } else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); tap(w); }
        else if ((e.key === 'Delete' || e.key === 'Backspace') && m) { e.preventDefault(); remove(+m.dataset.k); }
      });
      media.addEventListener('keydown', (e) => { if (e.key === 'Escape' && (sel >= 0 || anchor >= 0)) reset(); });
      list.addEventListener('keydown', (e) => {
        const b = e.target.closest('.ob-m');
        if (b && (e.key === 'Delete' || e.key === 'Backspace')) { e.preventDefault(); remove([...list.children].indexOf(b.closest('li')), true); }
      });
      draw();
      return { media, answer: ownKeys(h('div', { class: 'ob-marks-box' }, list, empty)) };
    },
    answered: (f, v) => Array.isArray(v) && v.length > 0,
  };
})();
