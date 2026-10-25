// Shared tasks: data that needs human labels, a page designed for the people who label it, and a link
// (backend/shared_tasks.py). The list (#/shared) and the builder (#/shared/<id>): on the left a chat with the
// design agent, the questions by hand, the data and the results; on the right the page itself, live.
import { api } from '../api.js';
import { h, ico, debounce, download } from '../util.js';
import { go } from '../nav.js';
import { confirmDialog, errorToast, openModal, toast } from '../ui.js';
import { ago } from './home.js';
import { state } from '../state.js';

const STATUS = { draft: 'Draft', live: 'Live', paused: 'Paused', closed: 'Closed' };
const KIND_ICON = { image: 'image', video: 'film', audio: 'audio', text: 'file' };
const TYPES = { choice: 'One answer', multi: 'Several answers', rating: 'Rating', text: 'Free text' };
const THEMES = { forest: 'Green', dark: 'Night', light: 'Day' };
const DATA_ACCEPT = '.jpg,.jpeg,.png,.webp,.bmp,.gif,.tif,.tiff,.wav,.mp3,.flac,.ogg,.opus,.m4a,.aac,.mp4,.mov,.mkv,.webm,.avi,.m4v,.txt,.csv,.tsv,.jsonl,.json';
const kinds = (by) => Object.entries(by || {}).map(([k, n]) => `${n} ${k}`).join(' · ') || 'no data yet';

// ----------------------------------------------------------------------------- the list
export class SharedListView {
  constructor(kind = 'labelling') {
    this.kind = kind;
    this.list = h('div.st-list');
    if (kind === 'listening') {  // subjective listening tests: the same tasks, their own list
      this.el = h('div.home', h('div.home-inner',
        h('div.home-head',
          h('div', h('h1', 'Subjective tests'),
            h('p', 'Listening tests: people hear clips (e.g. from several speech systems) and rate them, e.g. naturalness from 1 to 5. ' +
              'Practice clips, listening to the end before rating, the blind system key and MOS per system with 95% intervals.')),
          h('button.btn.primary', { dataset: { guide: 'listen.new' }, onclick: () => this.create() }, ico('plus', 15), 'New listening test')),
        this.list));
      this.load();
      return;
    }
    this.el = h('div.home', h('div.home-inner',
      h('div.home-head',
        h('div', h('h1', 'Shared tasks'),
          h('p', 'Data that needs human labels: upload it, describe the task to the design agent, and share a link. ' +
            'Or share a project whose labels the model already made, so people correct them in the editor. ' +
            'People who open a link need no account; they get a session ID.')),
        h('div.row', { style: { gap: '8px' } },
          h('button.btn', { dataset: { guide: 'shared.correct' }, onclick: () => correctionDialog(() => this.load()) }, ico('edit', 14), 'Share a project for correction'),
          h('button.btn.primary', { dataset: { guide: 'shared.new' }, onclick: () => this.create() }, ico('plus', 15), 'New shared task'))),
      h('h3.st-section', 'Labelling tasks'), this.list,
      h('h3.st-section', 'Correction links', h('span.muted', ' · people fix the model’s labels in the editor')), this.fixes = h('div.st-list')));
    this.load();
  }

  async load() {
    if (this.kind !== 'listening') this.loadFixes();
    let tasks = [];
    try { tasks = await api.sharedList(this.kind); } catch (e) { errorToast(e); }
    if (!tasks.length) {
      this.list.replaceChildren(h('div.st-empty', ico('users', 28),
        h('h3', this.kind === 'listening' ? 'No listening tests yet' : 'No shared tasks yet'),
        h('p', this.kind === 'listening' ? 'Upload the clips, write the scales, and share the link with your listeners.'
          : 'Images, video, audio or text that people should label: start one and the agent will design the page with you.'),
        h('button.btn.primary', { onclick: () => this.create() }, ico('plus', 15), this.kind === 'listening' ? 'New listening test' : 'New shared task')));
      return;
    }
    this.list.replaceChildren(...tasks.map(t => h('a.st-row', { href: `#/shared/${t.id}` },
      h('span.st-status', { class: `s-${t.status}` }, STATUS[t.status]),
      h('div.st-row-main', h('b', t.title), h('span.muted', `${kinds(t.by_kind)}${t.owned ? '' : ` · by ${t.owner_name || 'someone else'}`}`)),
      h('div.st-row-nums', h('span', h('b', String(t.complete)), ` / ${t.items} done`), h('span', h('b', String(t.people)), ' people'),
        h('span.muted', ago(t.updated_at))))));
  }

  async loadFixes() {
    let r;
    try { r = await api.allShares(); } catch (e) { errorToast(e); return; }
    const fixes = r.shares.filter(s => s.access === 'edit');
    if (!fixes.length) {
      this.fixes.replaceChildren(h('p.muted.st-none', 'None yet. Run the model on a project, then share it here so people can check and correct the labels.'));
      return;
    }
    const base = r.server.bases?.[0] || r.server.local_base;
    this.fixes.replaceChildren(...fixes.map(s => {
      const url = `${base}/s/${s.token}`;
      const state_ = s.revoked ? 'closed' : s.expired ? 'paused' : 'live';
      const people = h('div.st-people', { hidden: true }, s.people.length
        ? h('table.st-table', h('thead', h('tr', h('th', 'Session'), h('th', 'Name'), h('th', 'Changes'), h('th', 'Last active'))),
          h('tbody', s.people.map(x => h('tr', h('td', h('code', x.id)), h('td', x.name || '—'), h('td', String(x.edits || 0)), h('td', ago(x.last_at))))))
        : h('p.muted', 'Nobody has opened this link yet.'));
      return h('div.st-fix',
        h('div.st-row.st-row-static',
          h('span.st-status', { class: `s-${state_}` }, s.revoked ? 'Off' : s.expired ? 'Expired' : 'Live'),
          h('div.st-row-main', h('b', `${s.project_name} · ${s.label}`),
            h('span.muted', [s.tasks.length ? `${s.tasks.length} tiers` : 'all tiers', s.allow_download ? 'downloads allowed' : 'no downloads',
              s.expires_at ? `until ${new Date(s.expires_at).toLocaleDateString()}` : null].filter(Boolean).join(' · '))),
          h('div.st-row-nums', h('span', h('b', String(s.people.length)), ' people'), h('span', h('b', String(s.edits || 0)), ' changes')),
          h('div.row', { style: { gap: '4px' } },
            h('button.btn.sm', { title: url, onclick: () => navigator.clipboard.writeText(url).then(() => toast('Link copied')) }, ico('copy', 13), 'Copy link'),
            h('button.btn.sm.ghost', { onclick: () => { people.hidden = !people.hidden; } }, ico('users', 13), 'People'),
            h('a.btn.sm.ghost', { href: `#/p/${s.project_id}`, title: 'Open the project: changed labels show who made them' }, 'Open project'),
            h('button.btn.sm.ghost', { onclick: async () => { await api.updateShare(s.id, { revoked: !s.revoked }).catch(errorToast); this.loadFixes(); } }, s.revoked ? 'Turn on' : 'Turn off'),
            h('button.icon-btn.sm', { title: 'Delete the link', onclick: async () => {
              if (!(await confirmDialog('Delete this link? People can no longer open it; the changes they made stay in the project.', { title: 'Delete link' }))) return;
              await api.deleteShare(s.id).catch(errorToast); this.loadFixes();
            } }, ico('trash', 13)))),
        people);
    }));
  }

  async create() {
    try {
      const t = await api.sharedCreate('', this.kind);
      go(`#/shared/${t.id}`);
    } catch (e) { errorToast(e); }
  }
}

// ----------------------------------------------------------------------------- the builder
export class SharedTaskView {
  constructor(id) {
    this.id = id;
    this.task = null;
    this.tab = 'design';
    this.title = h('input.st-title', { 'aria-label': 'Task title', maxLength: 120, oninput: () => this.saveTitle() });
    this.statusEl = h('span.st-status');
    this.goLive = h('button.btn.primary', { onclick: () => this.toggleLive() });
    this.panel = h('div.st-panel-body');
    this.tabs = h('div.seg-ctl.st-tabs', Object.entries({ design: 'Design with AI', questions: 'Questions', data: 'Data', results: 'Results' })
      .map(([k, v]) => h('button', { type: 'button', dataset: { v: k }, onclick: () => this.show(k) }, v)));
    this.frame = h('iframe.st-frame', { title: 'Preview of the page people will see' });
    this.device = 'desktop';
    const deviceCtl = h('div.seg-ctl.sm', ['desktop', 'phone'].map(d => h('button', {
      type: 'button', dataset: { v: d }, class: d === 'desktop' ? 'on' : '',
      onclick: (e) => { this.device = d; [...deviceCtl.children].forEach(b => b.classList.toggle('on', b === e.currentTarget)); this.frameWrap.classList.toggle('phone', d === 'phone'); },
    }, d === 'desktop' ? 'Desktop' : 'Phone')));
    this.frameWrap = h('div.st-frame-wrap', this.frame);
    this.el = h('div.st-page',
      h('div.st-head',
        this.backLink = h('a.btn.ghost.sm', { href: '#/shared' }, '‹ Shared tasks'),
        this.title, this.statusEl, h('div.grow'),
        h('button.btn', { onclick: () => window.open(this.pageUrl(true), '_blank', 'noopener') }, ico('eye', 14), 'Open preview'),
        h('button.btn', { onclick: () => this.shareDialog() }, ico('link', 14), 'Link'),
        this.goLive),
      h('div.st-body',
        h('aside.st-panel', this.tabs, this.panel),
        h('section.st-preview', h('div.st-preview-bar', h('span.muted', 'What people will see'), h('div.grow'), deviceCtl,
          h('button.icon-btn.sm', { title: 'Reload the preview', onclick: () => this.reloadFrame() }, ico('repeat', 14))),
        this.frameWrap)));
    this.load();
  }

  pageUrl(preview) {
    return `${location.origin}/t/${this.task.token}${preview ? '?preview=1' : ''}`;
  }

  async load() {
    try {
      this.task = await api.shared(this.id);
      if (this.task.kind === 'listening') { this.backLink.href = '#/listen'; this.backLink.textContent = '‹ Subjective tests'; }
    } catch (e) {
      errorToast(e);
      go('#/shared');
      return;
    }
    this.paintHead();
    this.frame.src = this.pageUrl(true);
    this.show(this.task.summary.items ? 'design' : 'data');
  }

  paintHead() {
    const t = this.task;
    if (document.activeElement !== this.title) this.title.value = t.design.title;
    this.statusEl.className = `st-status s-${t.status}`;
    this.statusEl.textContent = STATUS[t.status];
    this.goLive.replaceChildren(ico(t.status === 'live' ? 'pause' : 'share', 14), t.status === 'live' ? 'Pause' : 'Go live');
  }

  show(tab) {
    this.tab = tab;
    [...this.tabs.children].forEach(b => b.classList.toggle('on', b.dataset.v === tab));
    ({ design: () => this.designTab(), questions: () => this.questionsTab(), data: () => this.dataTab(), results: () => this.resultsTab() })[tab]();
  }

  // the preview takes a new design without reloading (the page listens for it)
  pushDesign() {
    this.frame.contentWindow?.postMessage({ type: 'omni-design', design: this.task.design }, location.origin);
  }
  reloadFrame() { this.frame.src = this.pageUrl(true); }

  async save(design) {
    try {
      this.task = await api.sharedPatch(this.id, { design });
      this.pushDesign();
      this.paintHead();
      return true;
    } catch (e) { toast(e.message, { kind: 'warn' }); return false; }
  }
  saveTitle = debounce(() => {
    const v = this.title.value.trim();
    if (v && v !== this.task.design.title) this.save({ ...this.task.design, title: v });
  }, 600);

  async toggleLive() {
    const live = this.task.status === 'live';
    try {
      this.task = await api.sharedPatch(this.id, { status: live ? 'paused' : 'live' });
      this.paintHead();
      if (!live) this.shareDialog(true);
      else toast('Paused: the link shows a "try again later" message until you go live again.');
    } catch (e) { toast(e.message, { kind: 'warn' }); }
  }

  // ---------- design with the agent
  designTab() {
    const log = h('div.st-chat-log');
    const input = h('textarea.textarea.st-chat-input', { rows: 3,
      placeholder: 'Describe the task: what the data is, what people should decide, the classes, how many people per item…' });
    const send = h('button.btn.primary', { onclick: () => this.sendChat(input, log, send) }, ico('send', 14), 'Send');
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send.click(); } });
    const chat = this.task.chat || [];
    if (!chat.length) {
      log.append(h('div.st-msg.agent', 'Tell me about the task and I will design the page people see. For example:'),
        ...['Short customer reviews: people say if each is positive, negative or neutral, 3 people per review.',
          'Photos of street scenes: is there a pedestrian, a cyclist or a car? Several can apply.',
          'Short audio clips: write down exactly what is said, and rate how clear the speech is from 1 to 5.']
          .map(s => h('button.chip.st-suggest', { onclick: () => { input.value = s; input.focus(); } }, ico('sparkles', 12), s)));
    }
    for (const m of chat) log.append(h(`div.st-msg.${m.role === 'owner' ? 'me' : 'agent'}`, m.text));
    this.panel.replaceChildren(
      h('p.st-note', ico('info', 14), h('span', 'The agent writes the instructions, questions, look and settings; the preview on the right changes at once. ' +
        'Name a folder on the server (e.g. /data/reviews) to add its files.')),
      log, h('div.st-chat-box', input, send));
    log.scrollTop = log.scrollHeight;
  }

  async sendChat(input, log, btn) {
    const text = input.value.trim();
    if (!text) return input.focus();
    log.querySelectorAll('.st-suggest').forEach(b => b.remove());
    log.append(h('div.st-msg.me', text));
    const wait = h('div.st-msg.agent.wait', h('i'), h('i'), h('i'));
    log.append(wait);
    log.scrollTop = log.scrollHeight;
    input.value = '';
    btn.disabled = true;
    try {
      const r = await api.sharedChat(this.id, text);
      this.task = r.task;
      wait.replaceWith(h('div.st-msg.agent', r.reply));
      this.paintHead();
      if (r.linked?.some(l => l.added)) this.reloadFrame(); else this.pushDesign();
    } catch (e) {
      wait.replaceWith(h('div.st-msg.agent.bad', e.message));
    } finally {
      btn.disabled = false;
      log.scrollTop = log.scrollHeight;
      input.focus();
    }
  }

  // ---------- the questions by hand
  questionsTab() {
    const d = structuredClone(this.task.design);
    const commit = debounce(async () => { if (await this.save(d)) note.textContent = 'Saved'; }, 500);
    const note = h('span.muted.st-saved');
    const changed = () => { note.textContent = 'Saving…'; commit(); };
    const text = (obj, key, attrs = {}) => h(attrs.rows ? 'textarea.textarea' : 'input.input', {
      value: obj[key] ?? '', ...attrs, oninput: (e) => { obj[key] = attrs.type === 'number' ? +e.target.value : e.target.value; changed(); } });
    const check = (obj, key, label) => h('label.check', h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => { obj[key] = e.target.checked; changed(); } }), label);
    const select = (obj, key, opts, after) => h('select.select', { onchange: (e) => { obj[key] = e.target.value; after?.(); changed(); } },
      Object.entries(opts).map(([k, v]) => h('option', { value: k, selected: obj[key] === k }, v)));

    const list = h('div.st-fields');
    const paint = () => list.replaceChildren(...d.fields.map((f, i) => {
      const body = [];
      if (f.type === 'choice' || f.type === 'multi') {
        f.options = f.options?.length ? f.options : ['yes', 'no'];
        body.push(h('label.field', h('span', 'Options (comma separated)'), h('input.input', {
          value: f.options.join(', '), oninput: (e) => { f.options = e.target.value.split(',').map(s => s.trim()).filter(Boolean); changed(); } })));
      } else if (f.type === 'rating') {
        f.min ??= 1; f.max ??= 5;
        body.push(h('div.st-two', h('label.field', h('span', 'From'), text(f, 'min', { type: 'number', min: 0, max: 10 })),
          h('label.field', h('span', 'To'), text(f, 'max', { type: 'number', min: 1, max: 11 }))),
        h('div.st-two', h('label.field', h('span', 'Low end means'), text(f, 'min_label', { placeholder: 'e.g. very poor' })),
          h('label.field', h('span', 'High end means'), text(f, 'max_label', { placeholder: 'e.g. excellent' }))));
      } else {
        body.push(h('label.field', h('span', 'Placeholder'), text(f, 'placeholder')), check(f, 'multiline', 'Several lines'));
      }
      return h('div.st-field',
        h('div.st-field-head', h('b', `Question ${i + 1}`), h('div.grow'),
          select(f, 'type', TYPES, paint),
          h('button.icon-btn.sm', { title: 'Move up', disabled: i === 0, onclick: () => { [d.fields[i - 1], d.fields[i]] = [d.fields[i], d.fields[i - 1]]; paint(); changed(); } }, '↑'),
          h('button.icon-btn.sm', { title: 'Remove', disabled: d.fields.length === 1, onclick: () => { d.fields.splice(i, 1); paint(); changed(); } }, ico('trash', 13))),
        h('label.field', h('span', 'Question'), text(f, 'label')),
        ...body,
        h('label.field', h('span', 'Hint under the question (optional)'), text(f, 'help')),
        check(f, 'required', 'An answer is required'));
    }));
    paint();
    const st = d.settings, look = d.look;
    d.intro_fields ||= [];
    const introList = h('div.st-fields');
    const paintIntro = () => introList.replaceChildren(...d.intro_fields.map((f, i) => h('div.st-field',
      h('div.st-field-head', h('b', f.label || 'New question'), h('div.grow'),
        select(f, 'type', { choice: 'Choose one', text: 'Free text' }, paintIntro),
        h('button.icon-btn.sm', { title: 'Remove', onclick: () => { d.intro_fields.splice(i, 1); paintIntro(); changed(); } }, ico('trash', 13))),
      h('label.field', h('span', 'Question'), text(f, 'label', { placeholder: 'e.g. Is Persian your first language?' })),
      f.type === 'choice' ? h('label.field', h('span', 'Options (comma separated)'), h('input.input', {
        value: (f.options || []).join(', '), oninput: (e) => { f.options = e.target.value.split(',').map(x => x.trim()).filter(Boolean); changed(); } })) : null,
      check(f, 'required', 'Required'))));
    paintIntro();
    this.panel.replaceChildren(h('div.st-form',
      h('div.st-form-head', h('h4', 'Page'), note),
      h('label.field', h('span', 'Short description (shown on the public Task sharing page)'), text(d, 'summary', { maxLength: 300 })),
      h('label.field', h('span', 'Instructions for the people labelling'), text(d, 'instructions', { rows: 5 }),
        h('span.hint', 'When the items are in groups (e.g. languages), {group} is replaced by the group a person chose.')),
      h('h4', 'Questions'), list,
      h('button.btn', { onclick: () => { d.fields.push({ id: `q${d.fields.length + 1}`, type: 'choice', label: 'New question', options: ['yes', 'no'], required: true }); paint(); changed(); } },
        ico('plus', 14), 'Add a question'),
      h('h4', 'Who labels what'),
      h('div.st-two', h('label.field', h('span', 'People per item (0 = everyone labels every item)'), text(st, 'labels_per_item', { type: 'number', min: 0, max: 50 })),
        h('label.field', h('span', 'Items per person (0 = no limit)'), text(st, 'max_per_session', { type: 'number', min: 0 }))),
      Object.keys(this.task.data.groups || {}).length ? h('label.field', h('span', `Items are in ${Object.keys(this.task.data.groups).length} groups; people choose one first. The groups are called`),
        text(st, 'group_label', { placeholder: 'e.g. Language' })) : null,
      check(st, 'allow_skip', 'People can skip an item'), check(st, 'shuffle', 'Random order'),
      check(st, 'ask_name', 'Ask for a name or ID before starting'),
      h('h4', 'Listener questions (asked once, before starting)'), introList,
      h('button.btn', { onclick: () => { d.intro_fields.push({ id: `info${d.intro_fields.length + 1}`, label: '', type: 'choice', options: ['yes', 'no'], required: true }); paintIntro(); } },
        ico('plus', 14), 'Add a listener question'),
      h('h4', 'Listening test'),
      check(st, 'must_listen', 'Answers open only after the clip has played to the end'),
      check(st, 'no_repeat', 'Never the same sentence twice in a row (items with the same stimulus)'),
      check(st, 'allow_back', 'People can go back and change an earlier answer'),
      check(st, 'final_comment', 'Ask for overall comments at the end'),
      h('h4', 'Look'),
      h('div.st-two', h('label.field', h('span', 'Page language'), select(look, 'ui_lang', { en: 'English', fa: 'Persian (فارسی)' })),
        h('label.field', h('span', 'Direction'), select(look, 'dir', { ltr: 'Left to right', rtl: 'Right to left' }))),
      h('div.st-two', h('label.field', h('span', 'Mode'), select(look, 'theme', THEMES)),
        h('label.field', h('span', 'Layout'), select(look, 'layout', { side: 'Item left, questions right', stacked: 'Questions under the item' }))),
      h('label.field', h('span', 'Accent colour'), h('div.row', { style: { gap: '8px' } },
        h('input', { type: 'color', value: look.accent || '#8eb69b', oninput: (e) => { look.accent = e.target.value; changed(); } }),
        h('button.btn.sm.ghost', { onclick: () => { look.accent = ''; changed(); } }, 'Use the mode’s colour')))));
  }

  // ---------- data
  async dataTab() {
    const status = h('p.muted.st-data-status', kinds(this.task.summary.by_kind) + ` · ${this.task.summary.items} items`);
    const fileIn = h('input', { type: 'file', multiple: true, accept: DATA_ACCEPT, hidden: true, onchange: () => this.upload([...fileIn.files], status) });
    const folderIn = h('input', { type: 'file', webkitdirectory: true, hidden: true, onchange: () => this.upload([...folderIn.files], status) });
    const drop = h('div.st-drop', { onclick: () => fileIn.click() },
      ico('upload', 22), h('b', 'Drop files here, or click to choose'),
      h('span.muted', 'Images, video, audio, or text: .txt (one item per line), .csv / .tsv (a text column), .jsonl'), fileIn, folderIn);
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); this.upload([...e.dataTransfer.files], status); });
    const path = h('input.input', { placeholder: '/path/to/a/folder/on/the/server' });
    const grid = h('div.st-items');
    this.panel.replaceChildren(h('div.st-form',
      drop,
      h('div.row', { style: { gap: '8px', margin: '10px 0 18px' } },
        h('button.btn.sm', { onclick: () => folderIn.click() }, ico('folder', 13), 'Upload a folder'),
        h('div.grow'),
        h('button.btn.sm.ghost', { onclick: async () => {
          if (!(await confirmDialog('Remove all items from this task? Answers already given stay in the results.', { title: 'Remove all data', ok: 'Remove all' }))) return;
          await api.sharedClear(this.id).catch(errorToast); this.refresh('data');
        } }, ico('trash', 13), 'Remove all')),
      h('label.field', h('span', 'Or add a folder on the server (administrators; files are not copied)'),
        h('div.row', { style: { gap: '8px' } }, path, h('button.btn', { onclick: async () => {
          if (!path.value.trim()) return path.focus();
          try { const r = await api.sharedLink(this.id, path.value.trim()); toast(`Added ${r.added} items`); this.refresh('data'); }
          catch (e) { toast(e.message, { kind: 'warn' }); }
        } }, 'Add')))),
      h('div.st-form-head', h('h4', 'Items'), status), grid);
    try {
      const r = await api.sharedItems(this.id, 0, 120);
      grid.replaceChildren(...r.items.map(it => h('div.st-item', { title: it.name },
        it.kind === 'image' || it.kind === 'video'
          ? h('img', { src: `/api/shared/${this.id}/items/${it.id}/thumb`, alt: '', loading: 'lazy' })
          : it.kind === 'text' ? h('p', it.text) : h('div.st-item-ico', ico(KIND_ICON[it.kind], 22)),
        h('span', ico(KIND_ICON[it.kind], 11), it.name),
        h('button.icon-btn.sm.st-item-x', { title: 'Remove', onclick: async () => { await api.sharedRemoveItem(this.id, it.id).catch(errorToast); this.refresh('data'); } }, ico('x', 12)))),
      r.total > r.items.length ? h('p.muted', `and ${r.total - r.items.length} more`) : null);
    } catch (e) { errorToast(e); }
  }

  async upload(files, status) {
    if (!files.length) return;
    let n = 0, bad = 0;
    for (const [i, f] of files.entries()) {
      status.textContent = `Uploading ${i + 1} of ${files.length}: ${f.name}`;
      try { n += (await api.sharedUpload(this.id, f, f.webkitRelativePath || '')).added; }
      catch (e) { bad++; if (bad <= 3) toast(`${f.name}: ${e.message}`, { kind: 'warn' }); }
    }
    toast(`Added ${n} items${bad ? `, ${bad} files could not be read` : ''}`);
    this.refresh('data');
  }

  async refresh(tab) {
    try { this.task = await api.shared(this.id); } catch (e) { errorToast(e); }
    this.paintHead();
    this.reloadFrame();
    if (this.tab === tab) this.show(tab);
  }

  // ---------- results
  async resultsTab(where = this.where || '') {
    this.where = where;
    this.panel.replaceChildren(h('div.st-form', h('p.muted', 'Loading…')));
    let r;
    try { r = await api.sharedResults(this.id, where); } catch (e) { errorToast(e); return; }
    const s = r.summary;
    const ratings = r.fields.filter(f => f.type === 'rating');
    const fmt = (m) => m?.mean == null ? '—' : `${m.mean.toFixed(2)}${m.ci95 != null ? ` ± ${m.ci95.toFixed(2)}` : ''}`;
    // keep only people who answered a listener question a certain way (e.g. native speakers)
    const filters = (r.intro_fields || []).filter(f => f.type === 'choice').map(f => h('label.field', h('span', f.label),
      h('select.select', { onchange: () => {
        const parts = [...this.panel.querySelectorAll('.st-filter select')].map(x => x.value).filter(Boolean);
        this.resultsTab(parts.join(','));
      } }, h('option', { value: '' }, 'Everyone'), f.options.map(o => h('option', { value: `${f.id}:${o}`, selected: (r.where || {})[f.id] === o }, o)))));
    const keyIn = h('textarea.textarea', { rows: 3, placeholder: 'c01,system_a\nc02,native\n…  (clip, system per line)' });
    const keyBox = ratings.length ? h('div',
      h('h4', 'Per system (MOS ± 95% interval)'),
      r.systems.length ? h('table.st-table', h('thead', h('tr', h('th', 'System'), ratings.map(f => h('th', f.label)), h('th', 'Ratings'))),
        h('tbody', r.systems.map(x => h('tr', h('td', h('b', x.system)), ratings.map(f => h('td', fmt(x[f.id]))), h('td', String(x[ratings[0].id]?.n ?? 0))))))
        : h('p.muted', 'Add the blind key (which system made each clip) to see results per system. It stays private: listeners never see it.'),
      h('label.field', { style: { marginTop: '10px' } }, h('span', `Blind key${r.keyed ? ` (${r.keyed} clips matched)` : ''}`), keyIn,
        h('div.row', { style: { gap: '8px', marginTop: '6px' } },
          h('input', { type: 'file', accept: '.csv,.txt,.tsv', onchange: async (e) => { keyIn.value = (await e.target.files[0]?.text() || '').replaceAll('\t', ','); } }),
          h('div.grow'),
          h('button.btn.sm', { onclick: async () => {
            try { const k = await api.sharedKey(this.id, keyIn.value); toast(`Matched ${k.matched} clips to ${k.systems.length} systems`); this.resultsTab(); }
            catch (e) { toast(e.message, { kind: 'warn' }); }
          } }, 'Use this key')))) : null;
    const bar = (label, n, max) => h('div.st-bar', h('span', label), h('i', h('b', { style: { width: `${max ? (n / max) * 100 : 0}%` } })), h('em', String(n)));
    this.panel.replaceChildren(h('div.st-form',
      h('div.st-nums',
        h('div', h('b', `${s.complete} / ${s.items}`), h('span', 'items complete')),
        h('div', h('b', String(s.answers)), h('span', 'answers')),
        h('div', h('b', String(s.people)), h('span', 'people'))),
      h('div.row', { style: { gap: '8px', margin: '4px 0 18px' } },
        h('button.btn.sm', { onclick: () => download(`/api/shared/${this.id}/export.csv`) }, ico('download', 13), 'CSV (one row per answer)'),
        h('button.btn.sm', { onclick: () => download(`/api/shared/${this.id}/export.jsonl`) }, ico('download', 13), 'JSONL (per item)'),
        h('div.grow'), h('button.icon-btn.sm', { title: 'Refresh', onclick: () => this.resultsTab() }, ico('repeat', 13))),
      filters.length ? h('div.st-filter', h('h4', 'Whose answers'), h('div.st-two', filters)) : null,
      ratings.length ? h('table.st-table', { style: { marginBottom: '8px' } }, h('thead', h('tr', h('th', 'Scale'), h('th', 'MOS ± 95%'), h('th', 'Ratings'))),
        h('tbody', ratings.map(f => h('tr', h('td', f.label), h('td', fmt(f.mos)), h('td', String(f.mos?.n ?? 0)))))) : null,
      keyBox,
      ...r.fields.map(f => {
        const max = Math.max(1, ...f.counts.map(c => c[1]));
        return h('div.st-result', h('h4', f.label),
          f.agreement != null ? h('p.muted', `Agreement where several people answered: ${Math.round(f.agreement * 100)}%`) : null,
          f.type === 'text' ? h('p.muted', `${f.texts || 0} written answers (in the exports)`)
            : f.counts.length ? f.counts.map(([v, n]) => bar(v, n, max)) : h('p.muted', 'No answers yet'));
      }),
      h('h4', 'People'),
      r.sessions.length ? h('table.st-table', h('thead', h('tr', h('th', 'Session'), h('th', 'Name'), h('th', 'About'), h('th', 'Answers'), h('th', 'Last active'))),
        h('tbody', r.sessions.map(x => h('tr', { style: { opacity: x.kept ? 1 : 0.45 }, title: x.comment || '' }, h('td', h('code', x.id)), h('td', x.name || '—'),
          h('td', Object.values(x.info || {}).join(', ') || '—'), h('td', String(x.done)), h('td', ago(x.last_at))))))
        : h('p.muted', 'Nobody has started yet. Share the link to begin.')));
  }

  // ---------- the link
  shareDialog(justLive = false) {
    const t = this.task;
    const linkRow = (label, url) => h('div.field', h('span', label), h('div.row', { style: { gap: '8px' } },
      h('input.input', { value: url, readOnly: true, onfocus: (e) => e.target.select() }),
      h('button.btn', { onclick: () => navigator.clipboard.writeText(url).then(() => toast('Link copied')) }, ico('copy', 13), 'Copy')));
    const internet = h('div');
    const paintInternet = () => {
      const l = this.task.links;
      internet.replaceChildren(l.internet ? linkRow('On the internet (anyone with the link)', l.internet)
        : h('div.field', h('span', 'On the internet'),
          h('p.hint', l.tunnel.available ? 'Opens a temporary public address through a Cloudflare tunnel.' : 'Needs cloudflared on the server.'),
          h('button.btn', { disabled: !l.tunnel.available || t.status !== 'live', onclick: async (e) => {
            e.target.disabled = true;
            try { await api.startTunnel(); this.task = await api.shared(this.id); paintInternet(); }
            catch (err) { toast(err.message, { kind: 'warn' }); e.target.disabled = false; }
          } }, ico('globe', 13), 'Open an internet link')));
    };
    paintInternet();
    openModal({
      title: justLive ? 'The task is live' : 'Share the task', width: 560,
      subtitle: t.status === 'live' ? 'Send this link to the people who will label. They need no account.'
        : 'The link works once the task is live. Until then only you can open it, as a preview.',
      body: h('div', linkRow('On this network', t.links.network), internet,
        h('label.check', { style: { margin: '6px 0 4px' } }, h('input', { type: 'checkbox', checked: !!t.listed, onchange: async (e) => {
          try { this.task = await api.sharedPatch(this.id, { listed: e.target.checked }); toast(e.target.checked ? 'Listed on the public Task sharing page' : 'Removed from the public page'); }
          catch (err) { toast(err.message, { kind: 'warn' }); }
        } }), 'List it on the public Task sharing page (anyone can find it while it is live)'),
        h('p.hint', 'Each person gets a session ID when they start and can continue later with it. ' +
          '“New link” stops the old link from working.')),
      actions: [
        { label: 'New link', ghost: true, onClick: async (m) => {
          if (!(await confirmDialog('The current link will stop working. People with a session ID can continue on the new link.', { title: 'Make a new link', ok: 'New link', danger: false }))) return;
          this.task = await api.sharedPatch(this.id, { new_link: true }); m.close(); this.reloadFrame(); this.shareDialog();
        } },
        h('div.grow'),
        t.status === 'live' ? { label: 'Close the task', ghost: true, onClick: async (m) => {
          this.task = await api.sharedPatch(this.id, { status: 'closed' }); this.paintHead(); m.close();
        } } : null,
        { label: 'Done', primary: true, onClick: (m) => m.close() },
      ].filter(Boolean),
    });
  }

  destroy() {}
}

// ----------------------------------------------------------------------------- correction links
// A project the model already labelled, shared so people check and fix the labels in the studio's editor
// (backend/share.py, access "edit"). The clips can be played there but not downloaded unless allowed.
async function correctionDialog(onDone) {
  let projects = [];
  try { projects = await api.projects(); } catch (e) { errorToast(e); return; }
  if (!projects.length) { toast('Create a project and run the model on it first', { kind: 'warn' }); return; }
  const byId = new Map(projects.map(p => [p.id, p]));
  const draft = { pid: projects[0].id, label: 'Correction', tasks: [], allow_download: false, ask_name: true, note: '', expires_days: 0 };
  const tiers = h('div.st-tiers');
  const paintTiers = async () => {
    let proj;
    try { proj = await api.project(draft.pid); } catch (e) { errorToast(e); return; }
    draft.tasks = [];
    tiers.replaceChildren(...(proj.tasks || []).map(tid => {
      const t = state.taskById?.get(tid);
      return h('label.check', h('input', { type: 'checkbox', checked: false,
        onchange: (e) => { draft.tasks = e.target.checked ? [...draft.tasks, tid] : draft.tasks.filter(x => x !== tid); } }), t?.title || t?.name || tid);
    }));
    if (!(proj.tasks || []).length) tiers.append(h('p.muted', 'This project has no tasks yet.'));
  };
  const pick = h('select.select', { onchange: (e) => { draft.pid = e.target.value; paintTiers(); } },
    projects.map(p => h('option', { value: p.id }, `${p.name} · ${p.stats?.files ?? 0} files, ${p.stats?.annotated ?? 0} labelled`)));
  const field = (label, el, hint) => h('label.field', h('span', label), el, hint ? h('span.hint', hint) : null);
  const check = (key, label) => h('label.check', h('input', { type: 'checkbox', checked: draft[key], onchange: (e) => { draft[key] = e.target.checked; } }), label);
  paintTiers();
  openModal({
    title: 'Share a project for correction', width: 600,
    subtitle: 'People open the project in the editor, play each file and fix the labels the model made. They need no account; every change is signed with their session ID.',
    body: h('div',
      field('Project', pick, 'Run the model on it first (Run tasks), so there are labels to correct.'),
      h('div.field', h('span', 'Tiers to correct'), tiers, h('span.hint', 'None ticked = every tier of the project.')),
      field('Name of the link', h('input.input', { value: draft.label, maxLength: 60, oninput: (e) => { draft.label = e.target.value; } }), 'Shown to the people and next to each change they make.'),
      field('Instructions (optional)', h('textarea.textarea', { rows: 3, placeholder: 'e.g. Fix the transcripts word for word; mark each speaker change.', oninput: (e) => { draft.note = e.target.value; } })),
      check('ask_name', 'Ask people for their name or ID'),
      check('allow_download', 'Allow downloading the clips and the labels'),
      h('p.hint', 'With downloads off, people can play and label the data in the page but not save the files: there is no download button, the media has no "save as" menu, and the server refuses clips opened on their own. Anything a browser plays can still be recorded, so share only with people you trust.'),
      field('Link works for', h('select.select', { onchange: (e) => { draft.expires_days = +e.target.value; } },
        [[0, 'Until I turn it off'], [7, '7 days'], [30, '30 days'], [90, '90 days']].map(([v, l]) => h('option', { value: v }, l))))),
    actions: [h('div.grow'), { label: 'Cancel', ghost: true, onClick: (m) => m.close() },
      { label: 'Create the link', primary: true, icon: 'link', onClick: async (m) => {
        try {
          const r = await api.createShare(draft.pid, { label: draft.label.trim() || 'Correction', access: 'edit', tasks: draft.tasks,
            allow_download: draft.allow_download, ask_name: draft.ask_name, note: draft.note.trim(), expires_days: draft.expires_days || null });
          m.close();
          const base = r.server.bases?.[0] || r.server.local_base;
          const url = `${base}/s/${r.share.token}`;
          navigator.clipboard?.writeText(url).catch(() => {});
          openModal({ title: 'The link is ready', width: 520, subtitle: `Anyone with it can correct the labels of “${byId.get(draft.pid)?.name}”.`,
            body: h('div', h('div.row', { style: { gap: '8px' } }, h('input.input', { value: url, readOnly: true, onfocus: (e) => e.target.select() }),
              h('button.btn', { onclick: () => navigator.clipboard.writeText(url).then(() => toast('Link copied')) }, ico('copy', 13), 'Copy')),
              h('p.hint', 'Copied. It works on this network; for people elsewhere, open an internet link from the project’s Share dialog.')),
            actions: [h('div.grow'), { label: 'Done', primary: true, onClick: (x) => x.close() }] });
          onDone?.();
        } catch (e) { toast(e.message, { kind: 'warn' }); }
      } }],
  });
}
