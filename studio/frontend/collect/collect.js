// The public Data collection page: live projects their owners listed here (GET /api/public/collect).
// Anyone can open one (/c/<token>) and record; "My data collections" leads to the owner's side in the studio.
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  let projects = [], query = '';

  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v; else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }

  function card(p) {
    return h('a', { class: 'card tcard', href: `/c/${encodeURIComponent(p.token)}` },
      h('div', { class: 'tags' }, h('span', { class: 'tag' }, 'Recording'), p.languages.slice(0, 6).map(l => h('span', { class: 'tag' }, l)),
        p.languages.length > 6 ? h('span', { class: 'tag' }, `+${p.languages.length - 6}`) : null),
      h('h2', {}, p.title),
      h('p', {}, p.summary || `${p.prompts} sentences to read.`),
      p.conditions.length > 1 ? h('div', { class: 'tags' }, p.conditions.map(c => h('span', { class: 'tag' }, c))) : null,
      h('div', { class: 'foot' },
        h('span', {}, `${p.people} ${p.people === 1 ? 'person' : 'people'}, ${p.minutes} min recorded`),
        h('span', { class: 'go' }, 'Record')));
  }

  function paint() {
    const box = $('#cards');
    const shown = projects.filter(p => !query || `${p.title} ${p.summary} ${p.languages.join(' ')}`.toLowerCase().includes(query));
    if (!shown.length) {
      box.replaceChildren(h('div', { class: 'card empty' },
        h('h2', {}, projects.length ? 'No project matches' : 'No open projects right now'),
        h('p', { class: 'muted' }, projects.length ? 'Try another search.' : 'Come back soon, or start a project of your own.')));
      return;
    }
    box.replaceChildren(...shown.map(card));
  }

  $('#q').addEventListener('input', (e) => { query = e.target.value.trim().toLowerCase(); paint(); });
  fetch('/api/public/collect').then(r => r.json()).then(d => { projects = d.projects || []; paint(); })
    .catch(() => { $('#cards').replaceChildren(h('div', { class: 'card empty' }, h('h2', {}, 'Could not load the projects'), h('p', { class: 'muted' }, 'Please try again in a moment.'))); });
})();
