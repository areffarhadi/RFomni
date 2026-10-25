// Data collection, the owner's side (backend/collect.py): the list (#/collect) and the builder (#/collect/<id>).
// On the left the sentences per language, the ways to read them (e.g. emotions), the page texts and settings,
// and the recordings; on the right the recording page people see, live.
import { api } from '../api.js';
import { h, ico, debounce, download } from '../util.js';
import { go } from '../nav.js';
import { confirmDialog, errorToast, openModal, toast } from '../ui.js';
import { ago } from './home.js';

const STATUS = { draft: 'Draft', live: 'Live', paused: 'Paused', closed: 'Closed' };
const THEMES = { forest: 'Green', dark: 'Night', light: 'Day' };
const INFO = { name: 'Name or ID', gender: 'Gender', age: 'Age range', first_language: 'First language', accent: 'Accent or region' };

export class CollectListView {
  constructor() {
    this.list = h('div.st-list');
    this.el = h('div.home', h('div.home-inner',
      h('div.home-head',
        h('div', h('h1', 'Data collection'),
          h('p', 'Projects that ask people to record: sentences to read, in the ways you choose (e.g. emotions). ' +
            'Share the link or list it on the public Data collection page; people need no account.')),
        h('button.btn.primary', { dataset: { guide: 'collect.new' }, onclick: () => this.create() }, ico('plus', 15), 'New data collection')),
      this.list));
    this.load();
  }

  async load() {
    let ps = [];
    try { ps = await api.collectList(); } catch (e) { errorToast(e); }
    if (!ps.length) {
      this.list.replaceChildren(h('div.st-empty', ico('mic', 28), h('h2', 'No data collections yet'),
        h('p', 'Write the sentences, choose how people should read them, and share a link.'),
        h('button.btn.primary', { onclick: () => this.create() }, ico('plus', 15), 'New data collection')));
      return;
    }
    this.list.replaceChildren(...ps.map(p => h('a.st-row', { href: `#/collect/${p.id}` },
      h('span.st-status', { class: `s-${p.status}` }, STATUS[p.status]),
      h('div.st-row-main', h('b', p.title), h('span.muted', `${p.languages} languages · ${p.prompts} sentences · ${p.conditions} ways${p.listed ? ' · listed' : ''}`)),
      h('div.st-row-nums', h('span', h('b', String(p.recordings)), ' recordings'), h('span', h('b', String(p.people)), ' people'),
        h('span', h('b', String(p.minutes)), ' min'), h('span.muted', ago(p.updated_at))))));
  }

  async create() {
    try { go(`#/collect/${(await api.collectCreate('')).id}`); } catch (e) { errorToast(e); }
  }
}

export class CollectView {
  constructor(id) {
    this.id = id;
    this.p = null;
    this.tab = 'sentences';
    this.title = h('input.st-title', { 'aria-label': 'Project title', maxLength: 120, oninput: () => { this.d.title = this.title.value; this.changed(); } });
    this.statusEl = h('span.st-status');
    this.goLive = h('button.btn.primary', { onclick: () => this.toggleLive() });
    this.panel = h('div.st-panel-body');
    this.saved = h('span.muted.st-saved');
    this.tabs = h('div.seg-ctl.st-tabs', Object.entries({ sentences: 'Sentences', ways: 'Ways to read', page: 'Page', settings: 'Settings', recordings: 'Recordings' })
      .map(([k, v]) => h('button', { type: 'button', dataset: { v: k }, onclick: () => this.show(k) }, v)));
    this.frame = h('iframe.st-frame', { title: 'Preview of the recording page', allow: 'microphone' });
    const deviceCtl = h('div.seg-ctl.sm', ['desktop', 'phone'].map(d => h('button', { type: 'button', class: d === 'desktop' ? 'on' : '',
      onclick: (e) => { [...deviceCtl.children].forEach(b => b.classList.toggle('on', b === e.currentTarget)); this.frameWrap.classList.toggle('phone', d === 'phone'); } },
    d === 'desktop' ? 'Desktop' : 'Phone')));
    this.frameWrap = h('div.st-frame-wrap', this.frame);
    this.el = h('div.st-page', h('h1.sr-only', 'Data collection project'),
      h('div.st-head', h('a.btn.ghost.sm', { href: '#/collect' }, '‹ Data collection'), this.title, this.statusEl, this.saved, h('div.grow'),
        h('button.btn', { onclick: () => window.open(`${this.p.link}?preview=1`, '_blank', 'noopener') }, ico('eye', 14), 'Open preview'),
        h('button.btn', { onclick: () => this.linkDialog() }, ico('link', 14), 'Link'),
        this.goLive),
      h('div.st-body', h('aside.st-panel', this.tabs, this.panel),
        h('section.st-preview', h('div.st-preview-bar', h('span.muted', 'What people will see (you can record here; nothing is saved)'), h('div.grow'), deviceCtl,
          h('button.icon-btn.sm', { title: 'Reload the preview', onclick: () => this.reload() }, ico('repeat', 14))), this.frameWrap)));
    this.load();
  }

  async load() {
    try { this.p = await api.collect(this.id); } catch (e) { errorToast(e); go('#/collect'); return; }
    this.d = structuredClone(this.p.design);
    this.paintHead();
    this.reload();
    this.show('sentences');
  }
  reload() { this.frame.src = `${this.p.link}?preview=1`; }
  paintHead() {
    if (document.activeElement !== this.title) this.title.value = this.p.design.title;
    this.statusEl.className = `st-status s-${this.p.status}`;
    this.statusEl.textContent = STATUS[this.p.status];
    this.goLive.replaceChildren(ico(this.p.status === 'live' ? 'pause' : 'share', 14), this.p.status === 'live' ? 'Pause' : 'Go live');
  }
  show(tab) {
    this.tab = tab;
    [...this.tabs.children].forEach(b => b.classList.toggle('on', b.dataset.v === tab));
    this[`${tab}Tab`]();
  }

  // every edit saves after a moment and reloads the preview
  changed = (() => {
    const save = debounce(async () => {
      try {
        this.p = await api.collectPatch(this.id, { design: this.d });
        this.saved.textContent = 'Saved';
        this.paintHead();
        this.reloadSoon();
      } catch (e) { this.saved.textContent = ''; toast(e.message, { kind: 'warn' }); }
    }, 600);
    return () => { this.saved.textContent = 'Saving…'; save(); };
  })();
  reloadSoon = debounce(() => this.reload(), 400);

  text(obj, key, attrs = {}) {
    return h(attrs.rows ? 'textarea.textarea' : 'input.input', { value: obj[key] ?? '', ...attrs,
      oninput: (e) => { obj[key] = attrs.type === 'number' ? +e.target.value : e.target.value; this.changed(); } });
  }
  check(obj, key, label) {
    return h('label.check', h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => { obj[key] = e.target.checked; this.changed(); } }), label);
  }

  // ---------- sentences per language
  sentencesTab() {
    const list = h('div.st-fields');
    const paint = () => list.replaceChildren(...this.d.languages.map((lg, i) => h('div.st-field',
      h('div.st-field-head', h('b', lg.name || 'New language'), h('span.muted', ` · ${lg.prompts.length} sentences`), h('div.grow'),
        h('button.icon-btn.sm', { title: 'Remove the language', disabled: this.d.languages.length === 1,
          onclick: () => { this.d.languages.splice(i, 1); paint(); this.changed(); } }, ico('trash', 13))),
      h('div.st-two', h('label.field', h('span', 'Language'), this.text(lg, 'name', { placeholder: 'e.g. German' })),
        h('label.field', h('span', 'Code'), this.text(lg, 'code', { placeholder: 'e.g. de', maxLength: 12 }))),
      h('label.field', h('span', 'Sentences, one per line'), h('textarea.textarea', { rows: Math.min(10, Math.max(4, lg.prompts.length + 1)), dir: 'auto',
        value: lg.prompts.map(p => p.text).join('\n'),
        oninput: (e) => {
          const lines = e.target.value.split('\n').map(s => s.trim()).filter(Boolean);
          lg.prompts = lines.map((t, j) => ({ id: lg.prompts[j]?.text === t ? lg.prompts[j].id : (lg.prompts.find(p => p.text === t)?.id || `p${j + 1}`), text: t }));
          this.changed();
        } })))));
    paint();
    this.panel.replaceChildren(h('div.st-form',
      h('p.st-note', ico('info', 14), h('span', 'Short sentences with no emotion of their own work best: the emotion should come from the voice, not from the words.')),
      list,
      h('button.btn', { onclick: () => { this.d.languages.push({ code: `l${this.d.languages.length + 1}`, name: '', prompts: [] }); paint(); } }, ico('plus', 14), 'Add a language')));
  }

  // ---------- ways to read (conditions, e.g. emotions)
  waysTab() {
    const list = h('div.st-fields');
    const paint = () => list.replaceChildren(...this.d.conditions.map((c, i) => h('div.st-field',
      h('div.st-field-head', h('b', c.label || 'New'), h('div.grow'),
        h('button.icon-btn.sm', { title: 'Move up', disabled: i === 0, onclick: () => { [this.d.conditions[i - 1], this.d.conditions[i]] = [this.d.conditions[i], this.d.conditions[i - 1]]; paint(); this.changed(); } }, '↑'),
        h('button.icon-btn.sm', { title: 'Remove', disabled: this.d.conditions.length === 1, onclick: () => { this.d.conditions.splice(i, 1); paint(); this.changed(); } }, ico('trash', 13))),
      h('label.field', h('span', 'Name'), this.text(c, 'label', { placeholder: 'e.g. Happy' })),
      h('label.field', h('span', 'Acting hint'), this.text(c, 'hint', { placeholder: 'e.g. As if you just heard great news.' })))));
    paint();
    const st = this.d.settings;
    this.panel.replaceChildren(h('div.st-form',
      h('p.st-note', ico('info', 14), h('span', 'Each sentence is recorded once in every way listed here, e.g. each emotion.')),
      list,
      h('button.btn', { onclick: () => { this.d.conditions.push({ id: `c${this.d.conditions.length + 1}`, label: '', hint: '' }); paint(); } }, ico('plus', 14), 'Add a way'),
      h('label.field', { style: { marginTop: '14px' } }, h('span', 'Order'), h('select.select', { onchange: (e) => { st.condition_order = e.target.value; this.changed(); } },
        [['together', 'In the order above, for each sentence'], ['shuffled', 'Shuffled for each person and sentence']].map(([v, l]) => h('option', { value: v, selected: st.condition_order === v }, l))))));
  }

  // ---------- page texts and look
  pageTab() {
    const d = this.d, look = d.look;
    const sel = (obj, key, opts) => h('select.select', { onchange: (e) => { obj[key] = e.target.value; this.changed(); } },
      Object.entries(opts).map(([k, v]) => h('option', { value: k, selected: obj[key] === k }, v)));
    this.panel.replaceChildren(h('div.st-form',
      h('label.field', h('span', 'Short description (shown on the public Data collection page)'), this.text(d, 'summary', { maxLength: 300 })),
      h('label.field', h('span', 'Instructions'), this.text(d, 'instructions', { rows: 4 })),
      h('label.field', h('span', 'Consent people agree to'), this.text(d, 'consent', { rows: 4 }),
        h('span.hint', 'People must tick this before they can record. Say what the recordings are for and who may use them.')),
      h('div.st-two', h('label.field', h('span', 'Mode'), sel(look, 'theme', THEMES)),
        h('label.field', h('span', 'Accent colour'), h('div.row', { style: { gap: '8px' } },
          h('input', { type: 'color', value: look.accent || '#8eb69b', oninput: (e) => { look.accent = e.target.value; this.changed(); } }),
          h('button.btn.sm.ghost', { onclick: () => { look.accent = ''; this.changed(); } }, 'Mode colour'))))));
  }

  // ---------- recording settings and what people tell about themselves
  settingsTab() {
    const st = this.d.settings;
    const info = h('div.st-tiers', Object.entries(INFO).map(([k, label]) => h('div', { style: { display: 'grid', gap: '2px' } },
      h('label.check', h('input', { type: 'checkbox', checked: st.info.includes(k), onchange: (e) => {
        st.info = e.target.checked ? [...st.info, k] : st.info.filter(x => x !== k);
        st.info_required = st.info_required.filter(x => st.info.includes(x));
        this.changed(); this.settingsTab();
      } }), label),
      st.info.includes(k) ? h('label.check', { style: { marginLeft: '22px', fontSize: '12px' } }, h('input', { type: 'checkbox', checked: st.info_required.includes(k),
        onchange: (e) => { st.info_required = e.target.checked ? [...st.info_required, k] : st.info_required.filter(x => x !== k); this.changed(); } }), 'required') : null)));
    this.panel.replaceChildren(h('div.st-form',
      h('h2', 'Recording'),
      h('label.field', h('span', 'Sample rate'), h('select.select', { onchange: (e) => { st.sample_rate = +e.target.value; this.changed(); } },
        [[16000, '16 kHz (speech models, smaller files)'], [48000, '48 kHz (full quality)']].map(([v, l]) => h('option', { value: v, selected: st.sample_rate === v }, l)))),
      h('div.st-two', h('label.field', h('span', 'Shortest recording (s)'), this.text(st, 'min_seconds', { type: 'number', min: 0.2, step: 0.5 })),
        h('label.field', h('span', 'Longest recording (s)'), this.text(st, 'max_seconds', { type: 'number', min: 2, step: 1 }))),
      h('label.field', h('span', 'Recordings wanted per sentence and way (0 = everyone records everything)'), this.text(st, 'per_pair', { type: 'number', min: 0 })),
      this.check(st, 'allow_skip', 'People can skip a sentence'),
      h('h2', 'About the speaker (asked before they start)'), info));
  }

  // ---------- recordings
  async recordingsTab() {
    this.panel.replaceChildren(h('p.muted', 'Loading…'));
    let ov, recs;
    try { [ov, recs] = await Promise.all([api.collectOverview(this.id), api.collectRecordings(this.id, { limit: 300 })]); } catch (e) { errorToast(e); return; }
    const s = ov.summary, d = this.p.design;
    const grid = h('table.st-table', h('thead', h('tr', h('th', ''), d.conditions.map(c => h('th', c.label)))),
      h('tbody', d.languages.map(lg => h('tr', h('td', lg.name), d.conditions.map(c => h('td', String(ov.grid[lg.code]?.[c.id] ?? 0)))))));
    let audio = null;
    const row = (r) => {
      const status = h('span.badge', { class: r.status === 'accepted' ? 'accent' : r.status === 'rejected' ? 'warn' : '' }, r.status || 'new');
      const set = async (st) => { try { r = await api.collectReview(this.id, r.id, st); status.textContent = st; status.className = `badge ${st === 'accepted' ? 'accent' : st === 'rejected' ? 'warn' : ''}`; } catch (e) { errorToast(e); } };
      return h('div.cl-rec',
        h('button.icon-btn.sm', { title: 'Play', onclick: () => { audio?.pause(); audio = new Audio(`/api/collect/${this.id}/recordings/${r.id}/audio`); audio.play(); } }, ico('play', 13)),
        h('div.cl-rec-main', h('span', { dir: 'auto' }, r.text), h('span.muted', `${d.languages.find(l => l.code === r.lang)?.name || r.lang} · ${d.conditions.find(c => c.id === r.condition)?.label || r.condition} · ${r.seconds} s · ${r.session}`)),
        status,
        h('button.icon-btn.sm', { title: 'Accept', onclick: () => set('accepted') }, ico('check', 13)),
        h('button.icon-btn.sm', { title: 'Reject', onclick: () => set('rejected') }, ico('x', 13)));
    };
    this.panel.replaceChildren(h('div.st-form',
      h('div.st-nums', h('div', h('b', String(s.recordings)), h('span', 'recordings')), h('div', h('b', String(s.people)), h('span', 'people')),
        h('div', h('b', `${s.minutes}`), h('span', 'minutes'))),
      h('div.row', { style: { gap: '8px', margin: '4px 0 14px', flexWrap: 'wrap' } },
        h('button.btn.sm', { onclick: () => download(`/api/collect/${this.id}/export.zip`) }, ico('download', 13), 'Export all (ZIP)'),
        h('button.btn.sm', { onclick: () => download(`/api/collect/${this.id}/export.zip?accepted=true`) }, ico('download', 13), 'Accepted only'),
        h('div.grow'), h('button.icon-btn.sm', { title: 'Refresh', onclick: () => this.recordingsTab() }, ico('repeat', 13))),
      h('h2', 'Recordings per language and way'), grid,
      h('h2', `Latest recordings${recs.total > recs.recordings.length ? ` (${recs.recordings.length} of ${recs.total})` : ''}`),
      recs.recordings.length ? h('div.cl-recs', recs.recordings.map(row)) : h('p.muted', 'No recordings yet. Share the link to begin.'),
      h('h2', 'People'),
      ov.people.length ? h('table.st-table', h('thead', h('tr', h('th', 'Session'), h('th', 'About'), h('th', 'Recordings'), h('th', 'Last active'))),
        h('tbody', ov.people.map(x => h('tr', h('td', h('code', x.id)), h('td', Object.values(x.info).join(', ') || '—'), h('td', `${x.recordings} (${x.minutes} min)`), h('td', ago(x.last_at))))))
        : h('p.muted', 'Nobody has started yet.')));
  }

  // ---------- going live and the link
  async toggleLive() {
    const live = this.p.status === 'live';
    try {
      this.p = await api.collectPatch(this.id, { status: live ? 'paused' : 'live' });
      this.paintHead();
      if (!live) this.linkDialog(true); else toast('Paused: the link shows a "try again later" message until you go live again.');
    } catch (e) { toast(e.message, { kind: 'warn' }); }
  }

  linkDialog(justLive = false) {
    const url = this.p.link;
    openModal({
      title: justLive ? 'The project is live' : 'Share the project', width: 560,
      subtitle: this.p.status === 'live' ? 'Send this link to the people who will record. They need no account.'
        : 'The link works once the project is live. Until then only you can open it, as a preview.',
      body: h('div',
        h('div.field', h('span', 'Link'), h('div.row', { style: { gap: '8px' } }, h('input.input', { value: url, readOnly: true, onfocus: (e) => e.target.select() }),
          h('button.btn', { onclick: () => navigator.clipboard.writeText(url).then(() => toast('Link copied')) }, ico('copy', 13), 'Copy'))),
        h('label.check', { style: { margin: '6px 0' } }, h('input', { type: 'checkbox', checked: !!this.p.listed, onchange: async (e) => {
          try { this.p = await api.collectPatch(this.id, { listed: e.target.checked }); toast(e.target.checked ? 'Listed on the public Data collection page' : 'Removed from the public page'); }
          catch (err) { toast(err.message, { kind: 'warn' }); }
        } }), 'List it on the public Data collection page'),
        h('p.hint', 'The recording page needs a secure (https) address for the microphone; this link is one.')),
      actions: [
        { label: 'Delete project', ghost: true, onClick: async (m) => {
          if (!(await confirmDialog('Delete this data collection and all its recordings?', { title: 'Delete', ok: 'Delete' }))) return;
          await api.collectDelete(this.id).catch(errorToast); m.close(); go('#/collect');
        } },
        h('div.grow'),
        { label: 'Done', primary: true, onClick: (m) => m.close() }],
    });
  }

  destroy() {}
}
