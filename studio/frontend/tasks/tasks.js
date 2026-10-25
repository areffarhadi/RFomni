// The public Task sharing page: live shared tasks their owners listed here (GET /api/public/tasks).
// Anyone can open one (/t/<token>) and help; "My shared tasks" leads to the owner's side in the studio.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const KIND = { audio: 'Audio', video: 'Video', image: 'Images', text: 'Text' };
  // the same page lists listening tests (/listen/) when its script tag says data-kind="listening"
  const listening = document.currentScript?.dataset.kind === 'listening';
  let tasks = [], kind = '', query = '';

  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v; else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }

  const n = (x) => Number(x || 0).toLocaleString('en');
  const people = (x) => `${n(x)} ${x === 1 ? 'person' : 'people'}`;

  function card(t) {
    const kinds = Object.keys(t.by_kind || {});
    const pct = t.items ? Math.round((t.complete / t.items) * 100) : 0;
    return h('a', { class: 'card tcard', href: `/t/${encodeURIComponent(t.token)}` },
      h('div', { class: 'tags' }, kinds.map(k => h('span', { class: 'tag' }, KIND[k] || k)),
        t.groups ? h('span', { class: 'tag' }, `${t.groups} ${t.group_label.toLowerCase()}s`) : null,
        listening && t.ui_lang === 'fa' ? h('span', { class: 'tag' }, 'فارسی') : null),
      h('h2', {}, t.title),
      h('p', {}, t.summary || `${t.items} items to label.`),
      listening && t.scales?.length ? h('div', { class: 'tags' }, t.scales.map(x => h('span', { class: 'tag' }, x.split('·').pop().split(':')[0].replace(/[?]/g, '').trim()))) : null,
      t.everyone ? null : h('div', { class: 'meter', title: `${pct}% complete` }, h('i', { style: `width:${pct}%` })),
      h('div', { class: 'foot' },
        h('span', {}, listening ? `${n(t.items)} clips · ${n(t.people)} ${t.people === 1 ? 'listener' : 'listeners'} so far`
          : t.everyone ? `${n(t.items)} items · ${people(t.people)} so far` : `${n(t.complete)} of ${n(t.items)} done · ${people(t.people)}`),
        h('span', { class: 'go' }, 'Start →')));
  }

  function paint() {
    const box = $('#cards');
    const shown = tasks.filter(t => (!kind || (t.by_kind || {})[kind])
      && (!query || `${t.title} ${t.summary}`.toLowerCase().includes(query)));
    if (!shown.length) {
      box.replaceChildren(h('div', { class: 'card empty' },
        h('h2', {}, tasks.length ? 'No task matches' : 'No public tasks right now'),
        h('p', { class: 'muted' }, tasks.length ? 'Try another search or kind of data.' : 'Come back soon, or share a task of your own.')));
      return;
    }
    box.replaceChildren(...shown.map(card));
  }

  document.querySelectorAll('.kind').forEach(b => b.addEventListener('click', () => {
    kind = b.dataset.kind;
    document.querySelectorAll('.kind').forEach(x => x.classList.toggle('on', x === b));
    paint();
  }));
  $('#q')?.addEventListener('input', (e) => { query = e.target.value.trim().toLowerCase(); paint(); });

  fetch(`/api/public/tasks${listening ? '?kind=listening' : ''}`).then(r => r.json()).then(d => { tasks = d.tasks || []; paint(); })
    .catch(() => { $('#cards').replaceChildren(h('div', { class: 'card empty' }, h('h2', {}, 'Could not load the tasks'), h('p', { class: 'muted' }, 'Please try again in a moment.'))); });
})();
