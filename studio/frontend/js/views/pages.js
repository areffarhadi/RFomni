// Default pages (backend/pages/): Claude designed one ready-made page per likely task; a person picks the kind of
// data, describes the task, and the small page assistant picks the closest default and adapts its settings in a
// chat. Here:
//   PageWizardView  #/new/<area>: three steps (kind of data, the task, a page) -> a new task or project
//   pageDesign      the "Design" tab of a page made from a default: the chat with the page assistant, versions, undo
//   pageSettings    the "Settings" tab: every setting by hand (also when the assistant is offline)
//   RequestsView    #/requests: requests to the administrators ("no default fits"), answered within 12 hours
//   uploadFiles     files, folders and archives into a shared task; big ones go in pieces
// Approving a version is the builder's main button ("Approve and go live", views/shared.js and views/collect.js).
import { api } from '../api.js';
import { h, ico, debounce } from '../util.js';
import { go } from '../nav.js';
import { errorToast, menu, openModal, toast } from '../ui.js';
import { ago } from './home.js';

export const AREAS = {
  tasks: { title: 'New shared task', back: '#/shared', name: 'Shared tasks', one: 'Shared task',
    lead: 'People label your data: they listen, read or look at each item and answer a question.' },
  listen: { title: 'New subjective test', back: '#/listen', name: 'Subjective tests', one: 'Subjective test',
    lead: 'People rate what they hear, read or see, so you can compare systems.' },
  collect: { title: 'New data collection', back: '#/collect', name: 'Data collection', one: 'Data collection',
    lead: 'People open a link and record or send you new data.' },
};
const MODS = {
  voice: { icon: 'audio', label: 'Voice', hint: 'Speech, recordings and sounds' },
  text: { icon: 'text', label: 'Text', hint: 'Sentences, reviews and documents' },
  image: { icon: 'image', label: 'Image', hint: 'Photos, scans and drawings' },
  video: { icon: 'film', label: 'Video', hint: 'Clips and recordings' },
};
// a few descriptions per kind of data; a click puts one in the box
const EXAMPLES = {
  tasks: {
    voice: ['Call-centre clips: is the caller angry, happy, sad or neutral? 3 people per clip.', 'Write down exactly what is said in each recording.', 'Is the speaker a man, a woman or a child?'],
    text: ['Product reviews: positive, negative or neutral.', 'News headlines: sport, politics, science or other.', 'Translate each sentence into German.'],
    image: ['Street photos: is there a car, a bus, a bicycle or none of these?', 'Write one sentence that describes each photo.', 'Chest X-rays: normal or abnormal?'],
    video: ['Short clips: is the person walking, running, sitting or cycling?', 'Describe what happens in each clip in one sentence.', 'Is each clip filmed indoors or outdoors?'],
  },
  listen: {
    voice: ['Rate how natural my TTS samples sound, 1 to 5, native English listeners with headphones.', 'Rate the audio quality of enhanced speech, 1 to 5.', 'Rate how easy each recording is to understand.'],
    text: ['Rate the fluency of machine translations, 1 to 5.', 'Rate how helpful each chatbot answer is, 1 to 7.', 'Rate summaries for accuracy and for fluency.'],
    image: ['Rate how realistic generated faces look, 1 to 5.', 'Rate the quality of upscaled photos.', 'Rate how well each image matches its prompt.'],
    video: ['Rate the visual quality of compressed clips, 1 to 5.', 'Rate how smooth the motion looks.', 'Rate generated videos for realism.'],
  },
  collect: {
    voice: ['People read 200 Persian sentences in a happy and an angry voice.', 'People answer questions about their day out loud.', 'People read English sentences at 48 kHz for a TTS voice.'],
    text: ['People write how they would ask a shop assistant for help.'],
    image: ['People take photos of their breakfast.'],
    video: ['People record themselves waving hello.'],
  },
};
const builder = (kind, id) => (kind === 'collect' ? `#/collect/${id}` : `#/shared/${id}`);

// ----------------------------------------------------------------------------- a small picture of a page
// What people see on a default page, drawn small: the item (a waveform, lines of text, a picture, a video frame)
// and the answer (class buttons, a 1 to 5 scale, a text box, a record button). Decorative: the name says it all.
const WAVE = [34, 58, 42, 76, 52, 88, 64, 40, 70, 96, 60, 44, 72, 54, 80, 46, 62, 36, 50, 30];
export function pagePicture(t) {
  const item = t.family === 'record'
    ? h('div.pp-prompt', h('i'), h('i.short'))
    : {
      voice: () => h('div.pp-wave', h('span.pp-play', ico('play', 10)), h('div', WAVE.map(v => h('i', { style: { height: `${v}%` } })))),
      text: () => h('div.pp-text', h('i'), h('i'), h('i'), h('i.short')),
      image: () => h('div.pp-media', ico('image', 22)),
      video: () => h('div.pp-media.video', h('span.pp-play', ico('play', 12)), h('i.pp-scrub')),
    }[t.modality]();
  const answer = {
    classify: () => h('div.pp-chips', h('i.on'), h('i'), h('i'), h('i')),
    rate: () => h('div.pp-scale', [1, 2, 3, 4, 5].map(n => h('i', { class: n === 4 ? 'on' : '' }, String(n)))),
    write: () => h('div.pp-box', h('i'), h('i.short')),
    record: () => h('div.pp-rec', h('span', ico('mic', 14)), h('i')),
  }[t.family]?.() || null;
  return h(`div.pp.${t.layout === 'side' ? 'side' : 'stacked'}`, { 'aria-hidden': 'true' },
    h('div.pp-top', h('i'), h('b')), h('div.pp-body', item, h('div.pp-answer', h('i.pp-q'), answer)));
}

// ----------------------------------------------------------------------------- asking the administrators
export function askAdminDialog({ text = '', area = '', modality = '', ref = null } = {}) {
  const box = h('textarea.textarea', { id: 'ask-what', rows: 6, value: text });
  const contact = h('input.input', { id: 'ask-contact', type: 'email', autocomplete: 'email' });
  openModal({
    title: 'Ask for a new page', width: 540,
    subtitle: 'An administrator reads every request and answers within 12 hours, then prepares a ready-made page for it.',
    body: h('div',
      h('div.field', h('label', { for: 'ask-what' }, h('span', 'What should the page do?')), box,
        h('span.hint', 'The data, what people do with it, and what they answer.')),
      h('div.field', h('label', { for: 'ask-contact' }, h('span', 'Email (optional)')), contact,
        h('span.hint', 'The answer also appears under Requests and in the page’s chat.'))),
    actions: [
      { label: 'Cancel', ghost: true, onClick: (m) => m.close() },
      { label: 'Send request', primary: true, icon: 'send', onClick: async (m) => {
        if (box.value.trim().length < 8) { box.focus(); toast('Say in a sentence what the page should do.', { kind: 'warn' }); return; }
        try {
          await api.pageRequest({ text: box.value, area, modality, ref, contact: contact.value });
          m.close();
          toast('Request sent. An administrator answers within 12 hours.', { kind: 'ok', action: { label: 'See requests', onClick: () => go('#/requests') } });
        } catch (e) { toast(e.message, { kind: 'warn' }); }
      } },
    ],
  });
  setTimeout(() => box.focus(), 50);
}

// ----------------------------------------------------------------------------- the wizard
const STEPS = ['Kind of data', 'Your task', 'Choose a page'];

export class PageWizardView {
  constructor(area) {
    this.area = AREAS[area] ? area : 'tasks';
    this.a = AREAS[this.area];
    this.cat = null;
    this.mod = null;        // the kind of data
    this.text = '';         // the description
    this.pick = null;       // the assistant's choice: { template, reply, ask } or { template: null, reply }
    this.browsing = false;  // step 3 without a description
    this.step = 1;
    this.steps = h('ol.pg-steps');
    this.body = h('div.pg-wiz-body');
    this.el = h('div.home', h('div.home-inner.pg-wiz',
      h('div.home-head',
        h('div', h('h1', this.a.title), h('p', this.a.lead)),
        h('a.btn', { href: this.a.back }, 'Cancel')),
      h('nav', { 'aria-label': 'Steps' }, this.steps), this.body));
    this.body.append(h('div.pg-loading', h('span.spinner'), 'Loading the ready-made pages…'));
    this.load();
  }

  async load() {
    try { this.cat = await api.pagesCatalogue(this.area); } catch (e) {
      this.body.replaceChildren(h('div.st-empty', ico('alert', 26), h('h2', 'The pages could not be loaded'), h('p', e.message),
        h('button.btn', { onclick: () => this.load() }, 'Try again')));
      return;
    }
    this.go(1);
  }

  pages(mod) { return this.cat.templates.filter(t => t.modality === mod); }
  reachable(n) { return n === 1 || (n === 2 && this.mod) || (n === 3 && this.mod && (this.pick || this.browsing)); }

  paintSteps() {
    const sub = [this.mod && MODS[this.mod].label, this.text && (this.text.length > 34 ? this.text.slice(0, 32) + '…' : this.text),
      this.pick?.template?.name];
    this.steps.replaceChildren(...STEPS.map((name, i) => {
      const n = i + 1, state = n < this.step ? 'done' : n === this.step ? 'on' : 'todo';
      const inner = [h('span.pg-step-n', state === 'done' ? ico('check', 13) : String(n)),
        h('span.pg-step-t', h('b', name), sub[i] && state !== 'todo' ? h('small', sub[i]) : null)];
      return h(`li.${state}`, this.reachable(n) && n !== this.step
        ? h('button', { type: 'button', onclick: () => this.go(n) }, inner)
        : h('div', { 'aria-current': state === 'on' ? 'step' : null }, inner));
    }));
  }

  go(n) {
    this.step = n;
    this.paintSteps();
    this.body.replaceChildren(...[null, () => this.kinds(), () => this.describe(), () => this.choose()][n]());
    const head = this.body.querySelector('h2');
    head?.setAttribute('tabindex', '-1');
    if (n > 1) head?.focus({ preventScroll: true });
    this.el.scrollTo?.({ top: 0, behavior: 'smooth' });
  }

  // step 1: voice, text, image or video
  kinds() {
    return [h('h2.pg-h', 'What kind of data will people work with?'),
      h('div.pg-mods', { role: 'radiogroup', 'aria-label': 'Kind of data' }, Object.entries(MODS).map(([k, m]) => {
        const ps = this.pages(k), on = this.mod === k;
        return h(`button.pg-mod${on ? '.on' : ''}`, { type: 'button', role: 'radio', 'aria-checked': on ? 'true' : 'false',
          onclick: () => { if (this.mod !== k) { this.pick = null; this.browsing = false; } this.mod = k; this.go(2); } },
        h('span.pg-mod-top', h('span.pg-mod-ico', ico(m.icon, 22)), h('span.pg-mod-check', ico('check', 12))),
        h('b', m.label), h('span.pg-mod-hint', m.hint),
        h('span.pg-mod-foot', ps.length ? `${ps.length} ready-made page${ps.length > 1 ? 's' : ''}` : 'No ready-made page yet'),
        ps.length ? h('span.pg-mod-pages', ps.map(p => p.name).join(' · ')) : h('span.pg-mod-pages', 'Ask us and we make one'));
      }))];
  }

  // step 2: what the task is
  describe() {
    const input = h('textarea.textarea.pg-describe', { id: 'pg-task', rows: 5, value: this.text,
      placeholder: `For example: ${EXAMPLES[this.area][this.mod][0]}`, oninput: () => { this.text = input.value; } });
    const find = h('button.btn.primary.lg', { type: 'button', onclick: () => this.find(find) }, ico('sparkles', 15), 'Find the best page');
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) find.click(); });
    const ps = this.pages(this.mod), none = !ps.length, label = MODS[this.mod].label.toLowerCase();
    setTimeout(() => input.focus(), 60);
    const aside = h('aside.pg-aside',
      h('h3', none ? `No ready-made ${label} page yet` : `Ready-made ${label} pages`),
      h('p', none ? 'Tell us what you need and we prepare one.' : 'The assistant picks the closest one and adapts it to your description.'),
      ps.map(t => h('div.pg-mini', h('div.pg-mini-pic', pagePicture(t)), h('div', h('b', t.name), h('span', t.summary)))));
    return [
      h('h2.pg-h', 'Describe your task'),
      h('div.pg-two', h('div.pg-panel',
        h('div.field',
          h('label', { for: 'pg-task' }, h('span', 'What is the data, what should people do, and what do they answer?')),
          input,
          h('span.hint', 'A sentence or two is enough. You can change everything later in the chat.')),
        h('div.pg-examples', h('span', 'Examples'), EXAMPLES[this.area][this.mod].map(s => h('button.chip', { type: 'button',
          onclick: () => { input.value = s; this.text = s; input.focus(); } }, s))),
        none ? h('p.pg-notice', ico('info', 15), h('span', `There is no ready-made ${MODS[this.mod].label.toLowerCase()} page here yet. ` +
          'Describe what you need and send it to us: we answer within 12 hours.')) : null,
        !none && !this.cat.online ? h('p.pg-notice.warn', ico('alert', 15), h('span', 'The page assistant is offline right now. Browse the pages and pick one yourself.')) : null,
        h('div.pg-panel-foot',
          h('button.btn.ghost', { type: 'button', onclick: () => this.go(1) }, 'Back'),
          h('div.grow'),
          none ? h('button.btn.primary.lg', { type: 'button', onclick: () => askAdminDialog({ text: input.value, area: this.area, modality: this.mod }) }, ico('send', 15), 'Ask for this page')
            : [h('button.btn', { type: 'button', onclick: () => { this.browsing = true; this.pick = null; this.go(3); } }, 'Browse the pages'),
              this.cat.online ? find : null])), aside)];
  }

  async find(btn) {
    if (this.text.trim().length < 8) { toast('Describe the task in a sentence or two first.', { kind: 'warn' }); return; }
    btn.disabled = true;
    btn.replaceChildren(h('span.spinner'), 'Finding the best page…');
    try {
      const r = await api.pagesPick(this.area, this.mod, this.text);
      this.pick = { template: r.template || null, reply: r.reply, ask: r.ask };
      this.browsing = false;
      this.go(3);
    } catch (e) {
      toast(e.message, { kind: 'warn' });
      btn.disabled = false;
      btn.replaceChildren(ico('sparkles', 15), 'Find the best page');
    }
  }

  // step 3: the suggested page, and every page for this kind of data
  choose() {
    const all = this.pages(this.mod), best = this.pick?.template;
    const others = all.filter(t => t.id !== best?.id);
    const out = [h('h2.pg-h', best ? 'We suggest this page' : this.pick ? 'No ready-made page fits yet' : `Pick a ${MODS[this.mod].label.toLowerCase()} page to start from`)];
    if (best) {
      out.push(h('div.pg-best',
        h('div.pg-best-pic', pagePicture(best)),
        h('div.pg-best-main',
          h('span.badge.accent', ico('sparkles', 12), 'Best match'),
          h('h3', best.name), h('p', best.summary),
          this.pick.reply ? h('p.pg-why', h('b', 'Why: '), this.pick.reply) : null,
          this.pick.ask ? h('p.pg-notice', ico('info', 15), h('span', this.pick.ask)) : null,
          h('div.pg-best-foot',
            h('button.btn.primary.lg', { type: 'button', onclick: () => this.create(best) }, 'Start with this page'),
            h('button.btn.ghost', { type: 'button', onclick: () => this.go(2) }, 'Change the description')))));
    } else if (this.pick) {   // no close match: the assistant still adapts the nearest page; the admins are the second choice
      const near = all[0];
      out.push(h('div.pg-nofit', h('span.pg-nofit-ico', ico('info', 18)),
        h('div', h('p', this.pick.reply || 'No ready-made page matches this closely.'),
          h('div.row', near ? h('button.btn.primary', { type: 'button', onclick: () => this.create(near) }, `Start from ${near.name} and adapt it`) : null,
            h('button.btn.ghost', { type: 'button', onclick: () => askAdminDialog({ text: this.text, area: this.area, modality: this.mod }) },
              ico('send', 13), 'Ask the admins instead')))));
    }
    const list = best ? others : all;
    const sub = best ? (others.length ? `Other ${MODS[this.mod].label.toLowerCase()} pages` : 'Not what you need?') : this.pick ? 'Start from the closest page' : '';
    if (sub) out.push(h('h3.pg-sub', sub));
    out.push(h('div.pg-tpls',
        list.map(t => h('div.pg-tpl',
          h('div.pg-tpl-pic', pagePicture(t)),
          h('div.pg-tpl-main', h('b', t.name), h('p', t.summary),
            h('button.btn.sm', { type: 'button', onclick: () => this.create(t) }, 'Use this page')))),
        h('button.pg-tpl.ask', { type: 'button', onclick: () => askAdminDialog({ text: this.text, area: this.area, modality: this.mod }) },
          ico('send', 18), h('b', 'Need a different page?'), h('span', 'Tell us what it should do. We answer within 12 hours.'))));
    out.push(h('div.pg-wiz-foot', h('button.btn.ghost', { type: 'button', onclick: () => this.go(2) }, 'Back')));
    return out;
  }

  async create(t) {
    const text = this.browsing ? '' : this.text;
    this.body.replaceChildren(h('div.pg-making', h('div.pg-making-pic', pagePicture(t)), h('span.spinner'),
      h('h2', `Setting up your ${t.name} page`),
      h('p', text.trim() ? 'The page assistant is adapting it to your description. This takes a few seconds.' : 'One moment…')));
    try {
      const r = await api.pagesCreate(this.area, this.mod, t.id, text);
      go(builder(r.kind, r.id));
    } catch (e) { errorToast(e); this.go(3); }
  }
}

// ----------------------------------------------------------------------------- the design chat of a page
// host: { kind, id, panel, setObj(obj), preview(), obj() } from the builder (views/shared.js, views/collect.js)
export async function pageDesign(host) {
  const log = h('div.st-chat-log.pg-log', { role: 'log', 'aria-live': 'polite' });
  const input = h('textarea.pg-input', { rows: 1, 'aria-label': 'Message to the page assistant', placeholder: 'Tell the assistant what to change…' });
  const send = h('button.pg-send', { type: 'button', title: 'Send (Enter)', 'aria-label': 'Send', onclick: () => say() }, ico('send', 15));
  const undo = h('button.icon-btn.sm', { type: 'button', title: 'Undo: back to the version before', 'aria-label': 'Undo', onclick: () => doUndo() }, ico('undo', 15));
  const saveBtn = h('button.btn.sm', { type: 'button', title: 'Save this version to find it and continue later', onclick: () => doSave() }, ico('check', 13), 'Save');
  const verBtn = h('button.btn.sm.ghost.pg-verbtn', { type: 'button', title: 'All versions', onclick: () => versions() });
  const askBtn = h('button.btn.sm.ghost.pg-askadmin', { type: 'button', title: 'Ask an administrator for something this page cannot do',
    onclick: () => askAdminDialog({ area: st?.page.area, modality: st?.page.modality, ref: { kind: host.kind, id: host.id },
      text: st ? `On my ${st.page.template.name} page: ` : '' }) }, ico('send', 12), 'Ask the admins');
  const head = h('div.pg-bar');
  const state = h('div.pg-state');
  const suggest = h('div.pg-suggest');
  const grow = () => { input.style.height = 'auto'; input.style.height = `${Math.min(160, input.scrollHeight)}px`; };
  input.addEventListener('input', grow);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); say(); } });
  host.panel.replaceChildren(h('div.pg-design', head, state, log, suggest,
    h('div.pg-composer', input, send), h('div.pg-foot', h('span.pg-kbd', 'Enter sends · Shift+Enter starts a new line'), askBtn)));
  let st;

  function paint(r) {
    st = r;
    const p = r.page, ap = p.approved, obj = host.obj?.() || r.obj, live = obj.status === 'live';
    const m = MODS[p.modality] || { icon: 'file', label: '' };
    const saved = p.versions?.[p.version - 1]?.saved;
    verBtn.replaceChildren(`Version ${p.version}`, saved ? ico('check', 12) : null, ico('chevron-down', 12));
    head.replaceChildren(
      h('span.pg-bar-ico', ico(m.icon, 16)),
      h('div.pg-bar-t', h('b', p.template.name), h('span', `${m.label} · ${AREAS[p.area]?.one || ''}`)),
      verBtn, undo, saveBtn);
    undo.disabled = p.version < 2;
    const where = host.kind === 'collect' ? 'Prompts' : 'Data';
    const line = !ap ? ['info', '', h('span', 'Chat until the preview looks right, then press ', h('b', 'Approve and go live'), ' at the top.')]
      : ap.version === p.version ? (live ? ['check', 'ok', `Version ${ap.version} is approved and live.`]
        : ['check', 'ok', `Version ${ap.version} is approved. Add your ${where.toLowerCase()} in the ${where} tab to go live.`])
        : live ? ['info', '', `You approved version ${ap.version}. People already see version ${p.version}.`]
          : ['info', '', `You approved version ${ap.version}. Approve again to keep version ${p.version}.`];
    state.className = `pg-state ${line[1]}`;
    state.replaceChildren(ico(line[0], 14), typeof line[2] === 'string' ? h('span', line[2]) : line[2]);
    log.replaceChildren(...(r.online === false ? [h('p.pg-notice.warn', ico('alert', 15), h('span', 'The page assistant is offline. You can change everything by hand in the Settings tab.'))] : []),
      ...(r.chat.length ? [] : [h('div.st-msg.agent', `This is the ${p.template.name} page, ready to use. Tell me what to change: the question, ` +
        'the answers, the scale, how many people do each item, a comment box, charts or colours. The preview on the right changes at once.')]),
      ...r.chat.map(msg), ...r.requests.map(reqCard));
    suggest.replaceChildren(...(p.template.suggest || []).map(s => h('button.chip', { type: 'button', onclick: () => { input.value = s; grow(); input.focus(); } }, s)));
    log.scrollTop = log.scrollHeight;
  }

  function msg(m) {
    if (m.role === 'owner') return h('div.st-msg.me', m.text);
    if (m.undo) return h('div.pg-divider', h('span', ico('undo', 12), m.text));
    return h('div.st-msg.agent.pg-reply',
      h('div', m.text),
      m.ask ? h('p.pg-callout', ico('info', 14), h('span', m.ask)) : null,

      m.problems?.length ? h('p.pg-small', `Not changed: ${m.problems.join('; ')}`) : null,
      m.changes?.length ? h('span.pg-ver', `Version ${m.version}`) : null);
  }

  function reqCard(r) {
    const left = Math.round((new Date(r.due) - new Date()) / 3600e3);
    return h('div.pg-reqcard',
      h('div.pg-reqcard-head', ico('send', 13), h('b', 'Your request to the admins'),
        r.answer ? h('span.badge.ok', 'Answered') : h('span.badge.warn', left > 0 ? `Answer within ${left} h` : 'Answer due now')),
      h('p', r.text), r.answer ? h('p.pg-answer', r.answer) : null);
  }

  async function run(fn) {
    send.disabled = undo.disabled = saveBtn.disabled = true;
    try {
      const r = await fn();
      host.setObj(r.obj);
      host.preview();
      paint({ ...st, ...r, online: r.online ?? st.online });
      return r;
    } catch (e) {
      log.append(h('div.st-msg.agent.bad', e.message));
      log.scrollTop = log.scrollHeight;
    } finally {
      send.disabled = saveBtn.disabled = false;
      undo.disabled = (st?.page?.version || 1) < 2;
    }
  }

  async function say() {
    const text = input.value.trim();
    if (!text || send.disabled) return input.focus();
    input.value = ''; grow();
    log.append(h('div.st-msg.me', text), h('div.st-msg.agent.wait', { 'aria-label': 'The assistant is writing' }, h('i'), h('i'), h('i')));
    log.scrollTop = log.scrollHeight;
    await run(() => api.pageChat(host.kind, host.id, text));
    log.querySelector('.wait')?.remove();
    input.focus();
  }
  const doUndo = () => run(() => api.pageUndo(host.kind, host.id));
  async function doSave() {
    const r = await run(() => api.pageSave(host.kind, host.id));
    if (r) toast(`Saved as version ${r.page.version}. It stays in your list: open it any time to continue.`, { kind: 'ok', timeout: 6000 });
  }
  // every version, newest first: what changed, which are saved; a click goes back to one (as a new version)
  function versions() {
    const vs = (st?.page.versions || []).map((v, i) => ({ ...v, n: i + 1 })).reverse();
    const what = (v) => v.by === 'default' ? 'the ready-made page' : v.by === 'restore' ? 'back to an earlier version'
      : `${v.by === 'hand' ? 'by hand' : 'in the chat'}${v.changes?.length ? ': ' + v.changes.map(c => c.split('.').pop().replace(/_/g, ' ')).slice(0, 3).join(', ') : ''}`;
    menu(verBtn, [{ header: 'Versions: pick one to go back to it' },
      ...vs.slice(0, 30).map(v => ({ icon: v.saved ? 'check' : null, active: v.n === st.page.version,
        label: `Version ${v.n}${v.saved ? ' · saved' : ''} · ${what(v)}`, hint: new Date(v.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        onClick: () => v.n !== st.page.version && run(() => api.pageRestore(host.kind, host.id, v.n)) }))], { placement: 'bottom-end', className: 'pg-versions' });
  }

  try { paint(await api.page(host.kind, host.id)); } catch (e) { errorToast(e); }
}

// the builder's main button on a page made from a default: approve this version, and go live when the data is there
export async function approvePage(kind, id) {
  try { return await api.pageApprove(kind, id); } catch (e) { toast(e.message, { kind: 'warn' }); return null; }
}
export function approveLabel(obj) {
  if (obj.status === 'live') return ['pause', 'Pause'];
  const ap = obj.page?.approved, n = obj.page?.versions?.length;
  return ap && ap.version === n ? ['share', 'Go live'] : ['check', 'Approve and go live'];
}

// ----------------------------------------------------------------------------- every setting by hand
// [label, help]
const L = {
  title: ['Title'], instructions: ['Instructions', 'What people read before they start.'],
  consent: ['Consent', 'People must agree to this before they record.'],
  question: ['Question'], classes: ['Answers', 'The classes people choose from.'],
  multiple: ['Allow several answers'], notSure: ['Add “Not sure”'], other: ['Add “Other”', 'With a box to say what.'],
  classHelp: ['Answer definitions', 'Shown under the question.'], confidence: ['Ask how sure they are', 'An extra 1 to 5 rating.'],
  mustPlay: ['Play to the end first', 'The answers unlock once the recording or video has played to the end.'],
  pointLabels: ['Words on the scale', 'Low to high, one per line: one for each point, or two for the ends.'],
  secondQuestion: ['Second question', 'On the same scale. Leave empty for none.'],
  noRepeat: ['No repeats in a row', 'Never the same sentence twice in a row.'],
  placeholder: ['Hint in the empty box'], multiline: ['Big box for long answers'],
  extraRating: ['Extra 1 to 5 rating', 'A question under the box. Leave empty for none.'],
  randomOrder: ['Random order', 'Each person gets the items in a different order.'], allowSkip: ['Allow skipping'],
  allowBack: ['Allow going back', 'People can return and change an answer.'], askName: ['Ask for a name or ID'],
  askAbout: ['Questions before starting', 'Asked once, before the first item.'],
  peoplePerItem: ['People per item', '0 means everyone does every item.'], itemsPerPerson: ['Items per person', '0 means no limit.'],
  comment: ['Comment box', 'Under each item.'], finalComment: ['Final comment box', 'For overall comments at the end.'],
  practiceItems: ['Practice items', 'The first items are for practice and are not scored.'],
  progress: ['Progress shown as'], finishCharts: ['On the last screen', 'Charts people see when they finish.'],
  charts: ['On your Results tab'], theme: ['Colour mode'], accent: ['Accent colour', 'For buttons and highlights.'],
  language: ['Page language', 'The language of the buttons.'], mode: ['People'],
  prompts: ['Prompts', 'One per line. The Prompts tab also loads them from a file.'], languageName: ['Language of the prompts'],
  ways: ['Ways to say each prompt', 'For example happy, sad, angry. Leave empty for one plain way.'], wayOrder: ['Order of the ways'],
  sampleRate: ['Recording quality'], peoplePerPrompt: ['People per prompt', '0 means everyone records everything.'],
};
const OPTION = {
  forest: 'Green', dark: 'Night', light: 'Day', navy: 'Navy', cream: 'Cream', charcoal: 'Charcoal', en: 'English', fa: 'Persian',
  bar: 'Bar', ring: 'Ring', off: 'Off', optional: 'Optional', required: 'Required', read: 'Read each prompt aloud',
  answer: 'Answer each prompt out loud', together: 'One prompt in every way', shuffled: 'Mixed',
  16000: '16 kHz (speech)', 48000: '48 kHz (full quality)', 'my-answers': 'Their answers', everyone: 'Everyone’s answers',
  'my-time': 'Their time per item', answers: 'Answer counts', scores: 'Scores', systems: 'Score per system',
  agreement: 'Agreement', timeline: 'Answers per day', 'time-per-item': 'Time per item', people: 'Items per person',
};
const optName = (o) => OPTION[o] || o[0].toUpperCase() + o.slice(1);
const GROUPS = [
  ['The page', 'Title, instructions and consent', ['title', 'instructions', 'consent']],
  ['The question', 'What people answer, and how', ['question', 'classes', 'multiple', 'notSure', 'other', 'classHelp', 'confidence', 'scaleMin', 'scaleMax',
    'pointLabels', 'secondQuestion', 'placeholder', 'multiline', 'extraRating', 'mode', 'prompts', 'languageName', 'ways', 'wayOrder']],
  ['Playing and recording', 'Must play, length and quality', ['mustPlay', 'noRepeat', 'minSeconds', 'maxSeconds', 'sampleRate']],
  ['People', 'Who does what, and what they are asked', ['peoplePerItem', 'itemsPerPerson', 'peoplePerPrompt', 'randomOrder', 'allowSkip', 'allowBack',
    'practiceItems', 'askName', 'askAbout', 'comment', 'finalComment']],
  ['Charts', 'Progress, last screen and results', ['progress', 'finishCharts', 'charts']],
  ['Look', 'Colours and language', ['theme', 'accent', 'language']],
];
const SWATCHES = ['#8eb69b', '#5d9bff', '#ff3d8b', '#f5b942', '#e8743b', '#9b7bff', '#2fb8ac', '#e05260'];
const openSections = new Set(['The page', 'The question']);   // which sections stay open while the studio is open
const TAGS = new Set(['classes', 'ways']);                     // short lists: chips; long ones stay a box

export async function pageSettings(host) {
  let r;
  try { r = await api.page(host.kind, host.id); } catch (e) { errorToast(e); return; }
  if (r.page.view) return pageEditor(host, r);   // Task sharing and Subjective tests: the page itself
  const cur = structuredClone(r.page.settings);
  const saved = h('span.pg-saved', ico('check', 13), `Saved · version ${r.page.version}`);
  const pending = {};
  const save = debounce(async () => {
    const patch = { ...pending };
    for (const k of Object.keys(pending)) delete pending[k];
    try {
      const out = await api.pageSettings(host.kind, host.id, patch);
      host.setObj(out.obj); host.preview();
      saved.className = 'pg-saved'; saved.replaceChildren(ico('check', 13), `Saved · version ${out.page.version}`);
    } catch (e) {
      saved.className = 'pg-saved bad'; saved.replaceChildren(ico('alert', 13), 'Not saved');
      toast(e.message, { kind: 'warn' });
    }
  }, 700);
  const set = (k, v) => { cur[k] = v; pending[k] = v; saved.className = 'pg-saved busy'; saved.replaceChildren(h('span.spinner'), 'Saving…'); save(); };
  const lines = (t) => t.split('\n').map(x => x.trim()).filter(Boolean);
  let seq = 0;
  const fid = (k) => `ps-${k}-${++seq}`;
  const head = (k, id) => {
    const [label, help] = L[k] || [k];
    return h('span.pg-f-t', h('label', { for: id }, label), help ? h('small', help) : null);
  };

  // a row: label on the left, a small control on the right
  const row = (k, id, control) => h('div.pg-f.row', head(k, id), control);
  // a block: label above, a wide control under it
  const block = (k, id, control) => h('div.pg-f', head(k, id), control);

  const segmented = (k, f, id) => h('div.seg-ctl.pg-seg', { role: 'radiogroup', id, 'aria-label': L[k]?.[0] || k }, f.options.map(o => {
    const b = h('button', { type: 'button', role: 'radio', class: cur[k] === o ? 'on' : '', 'aria-checked': cur[k] === o ? 'true' : 'false',
      onclick: () => { [...b.parentNode.children].forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', x === b ? 'true' : 'false'); }); set(k, o); } },
    optName(o));
    return b;
  }));

  const tags = (k, id) => {
    const wrap = h('div.pg-tags');
    const add = h('input.pg-tag-in', { id, placeholder: 'Add, then press Enter', 'aria-label': `Add to ${L[k]?.[0] || k}` });
    const paint = () => wrap.replaceChildren(...(cur[k] || []).map((v, i) => h('span.chip.pg-tag', h('span', v),
      h('button', { type: 'button', 'aria-label': `Remove ${v}`, onclick: () => { set(k, cur[k].filter((_, j) => j !== i)); paint(); add.focus(); } }, ico('x', 12)))), add);
    const push = (vals) => { const seen = new Set(cur[k] || []); set(k, [...(cur[k] || []), ...vals.filter(v => !seen.has(v))]); paint(); add.focus(); };
    add.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ',') && add.value.trim()) { e.preventDefault(); const v = add.value.trim(); add.value = ''; push([v]); }
      else if (e.key === 'Backspace' && !add.value && (cur[k] || []).length) { set(k, cur[k].slice(0, -1)); paint(); add.focus(); }
    });
    add.addEventListener('paste', (e) => {
      const t = e.clipboardData.getData('text');
      if (/[\n,]/.test(t)) { e.preventDefault(); push(t.split(/[\n,]/).map(x => x.trim()).filter(Boolean)); }
    });
    wrap.addEventListener('click', (e) => { if (e.target === wrap) add.focus(); });
    paint();
    return wrap;
  };

  const control = (f, byName) => {
    const k = f.name, v = cur[k], id = fid(k);
    if (k === 'scaleMax' || k === 'maxSeconds') return null;   // shown with scaleMin / minSeconds
    if (k === 'scaleMin' || k === 'minSeconds') {
      const hi = k === 'scaleMin' ? 'scaleMax' : 'maxSeconds', fh = byName[hi];
      const num = (name, ff, nid) => h('input.input.pg-num', { id: nid, type: 'number', min: ff.lo, max: ff.hi, value: cur[name] ?? 0,
        'aria-label': name, oninput: (e) => e.target.value !== '' && set(name, +e.target.value) });
      return h('div.pg-f.row', h('span.pg-f-t', h('label', { for: id }, k === 'scaleMin' ? 'Scale' : 'Recording length'),
        h('small', k === 'scaleMin' ? 'The lowest and the highest score.' : 'The shortest and the longest recording, in seconds.')),
      h('span.pg-range', num(k, f, id), h('span', 'to'), fh ? num(hi, fh, fid(hi)) : null));
    }
    if (f.kind === 'bool') {
      return h('div.pg-f.row', head(k, id), h('label.switch', h('input', { id, type: 'checkbox', checked: !!v, onchange: (e) => set(k, e.target.checked) }), h('span')));
    }
    if (f.kind === 'int') return row(k, id, h('input.input.pg-num', { id, type: 'number', min: f.lo, max: f.hi, value: v ?? 0, oninput: (e) => e.target.value !== '' && set(k, +e.target.value) }));
    if (f.kind === 'text') return block(k, id, h('input.input', { id, value: v ?? '', maxLength: f.max, oninput: (e) => set(k, e.target.value) }));
    if (f.kind === 'long') return block(k, id, h('textarea.textarea', { id, rows: 3, value: v ?? '', maxLength: f.max, dir: 'auto', oninput: (e) => set(k, e.target.value) }));
    if (f.kind === 'one') {
      const short = f.options.length <= 3 && f.options.every(o => optName(o).length <= 14);
      if (short) return block(k, id, segmented(k, f, id));
      return block(k, id, h('select.select', { id, onchange: (e) => set(k, e.target.value) }, f.options.map(o => h('option', { value: o, selected: v === o }, optName(o)))));
    }
    if (f.kind === 'list') {
      if (TAGS.has(k)) return block(k, id, tags(k, id));
      return block(k, id, h('textarea.textarea', { id, rows: Math.min(10, Math.max(3, (v || []).length + 1)), dir: 'auto', value: (v || []).join('\n'),
        oninput: (e) => set(k, lines(e.target.value)) }));
    }
    if (f.kind === 'some') {
      return h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', { id }, L[k]?.[0] || k), L[k]?.[1] ? h('small', L[k][1]) : null),
        h('div.pg-checks', { role: 'group', 'aria-labelledby': id }, f.options.map(o => {
          const b = h('button.chip', { type: 'button', class: (cur[k] || []).includes(o) ? 'on' : '', 'aria-pressed': (cur[k] || []).includes(o) ? 'true' : 'false',
            onclick: () => {
              const on = !(cur[k] || []).includes(o);
              set(k, on ? [...(cur[k] || []), o] : (cur[k] || []).filter(x => x !== o));
              b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
            } }, ico('check', 12), optName(o));
          return b;
        })));
    }
    if (f.kind === 'color') {
      const sw = h('div.pg-swatches', { role: 'radiogroup', 'aria-label': 'Accent colour' });
      const paint = () => sw.replaceChildren(
        h('button.pg-sw.none', { type: 'button', role: 'radio', 'aria-checked': !cur[k] ? 'true' : 'false', class: !cur[k] ? 'on' : '', title: 'The colour mode’s own colour',
          onclick: () => { set(k, ''); paint(); } }, 'Auto'),
        ...SWATCHES.map(c => h('button.pg-sw', { type: 'button', role: 'radio', 'aria-label': c, 'aria-checked': cur[k] === c ? 'true' : 'false',
          class: cur[k] === c ? 'on' : '', style: { '--c': c }, onclick: () => { set(k, c); paint(); } })),
        h('label.pg-sw.pick', { title: 'Any colour', class: cur[k] && !SWATCHES.includes(cur[k]) ? 'on' : '', style: cur[k] && !SWATCHES.includes(cur[k]) ? { '--c': cur[k] } : null },
          ico('palette', 13), h('input', { type: 'color', id, value: cur[k] || '#8eb69b', 'aria-label': 'Any colour', onchange: (e) => { set(k, e.target.value); paint(); } })));
      paint();
      return h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', L[k][0]), h('small', L[k][1])), sw);
    }
    return null;
  };

  const byName = Object.fromEntries(r.page.form.map(f => [f.name, f]));
  const placed = new Set();
  const section = (title, desc, names) => {
    const fs = names.filter(n => byName[n]).map(n => { placed.add(n); return control(byName[n], byName); }).filter(Boolean);
    if (!fs.length) return null;
    const d = h('details.pg-sec', { open: openSections.has(title) },
      h('summary', h('span.pg-sec-t', h('b', title), h('small', desc)), h('span.pg-chev')), h('div.pg-sec-body', fs));
    d.addEventListener('toggle', () => (d.open ? openSections.add(title) : openSections.delete(title)));
    return d;
  };
  const sections = GROUPS.map(([t, d, names]) => section(t, d, names));
  const rest = r.page.form.filter(f => !placed.has(f.name)).map(f => f.name);
  host.panel.replaceChildren(h('div.pg-settings',
    h('div.pg-settings-head', h('div', h('h2', 'Settings'), h('p', 'Every change is saved as a new version. Undo in the Design tab takes it back.')), saved),
    sections, rest.length ? section('More', '', rest) : null));
}

// ----------------------------------------------------------------------------- the page itself, by hand
// Task sharing and Subjective test pages: the same page the assistant edits (backend/pages/design.py view), every part
// of it. A change is saved as a new version once the page is complete (each question has its words and answers).
// the kinds of question come from the server (backend/blocks.py menu): only those that suit the page's data
const BASIC_KINDS = [{ kind: 'buttons', name: 'Buttons', group: 'Questions', does: 'pick one answer', keys: ['answers'] },
  { kind: 'text', name: 'Text box', group: 'Questions', does: 'a box to write in', keys: ['big_box', 'hint'] }];
const LIST_NAME = { grid: ['Aspects', 'The things people rate, one per row.'], dropdown: ['Answers', 'People pick one from the list.'],
  ranking: ['Answers', 'People put them in order, best first.'], checkboxes: ['Answers', 'People can pick several.'], buttons: ['Answers', 'People pick one.'] };
const RANGE = { scale: [0, 10, 1, 11], grid: [0, 10, 1, 11], slider: [0, 99, 1, 100], rating_over_time: [0, 99, 1, 100], number: [-1e9, 1e9, -1e9, 1e9] };
const RULES = {
  people_per_item: ['People per item', '0 means everyone does every item.'], items_per_person: ['Items per person', '0 means no limit.'],
  practice_items: ['Practice items', 'The first items are for practice and are not scored.'],
  random_order: ['Random order', 'Each person gets the items in a different order.'], can_skip: ['Allow skipping'],
  can_go_back: ['Allow going back', 'People can return and change an answer.'], ask_name: ['Ask for a name or ID'],
  play_to_end: ['Play to the end first', 'The answers unlock once the item has played to the end.'],
  no_repeat: ['No repeats in a row', 'Never the same sentence twice in a row.'], final_comment: ['Final comment box', 'For overall comments at the end.'],
  max_plays: ['Plays per clip', '0 means any number of plays.'], seconds_per_item: ['Seconds per item', '0 means no time limit.'],
  keyboard_keys: ['Keyboard keys', 'Show 1, 2, 3… and A, B on the answers; people can answer with the keys.'],
};
const editorOpen = new Set(['The page', 'Questions']);

function tagEditor(values, onChange, label) {
  const wrap = h('div.pg-tags');
  const add = h('input.pg-tag-in', { placeholder: 'Add an answer, then press Enter', 'aria-label': `Add to ${label}` });
  const paint = () => wrap.replaceChildren(...values.map((v, i) => h('span.chip.pg-tag', h('span', v),
    h('button', { type: 'button', 'aria-label': `Remove ${v}`, onclick: () => { values.splice(i, 1); onChange(); paint(); add.focus(); } }, ico('x', 12)))), add);
  const push = (vals) => { for (const v of vals) if (!values.includes(v)) values.push(v); onChange(); paint(); add.focus(); };
  add.addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ',') && add.value.trim()) { e.preventDefault(); const v = add.value.trim(); add.value = ''; push([v]); }
    else if (e.key === 'Backspace' && !add.value && values.length) { values.pop(); onChange(); paint(); add.focus(); }
  });
  add.addEventListener('paste', (e) => {
    const t = e.clipboardData.getData('text');
    if (/[\n,]/.test(t)) { e.preventDefault(); push(t.split(/[\n,]/).map(x => x.trim()).filter(Boolean)); }
  });
  wrap.addEventListener('click', (e) => { if (e.target === wrap) add.focus(); });
  paint();
  return wrap;
}

async function pageEditor(host, r) {
  const v = structuredClone(r.page.view);
  const saved = h('span.pg-saved', ico('check', 13), `Saved · version ${r.page.version}`);
  const status = (kind, icon, text) => { saved.className = `pg-saved ${kind}`; saved.replaceChildren(icon === 'spin' ? h('span.spinner') : ico(icon, 13), text); };
  const pending = new Set();
  const problem = () => {
    const qi = v.questions.findIndex(q => !q.question.trim() || (['buttons', 'checkboxes', 'dropdown', 'ranking', 'grid'].includes(q.type) && (q.answers || []).length < 2));
    if (qi >= 0) return `Finish question ${qi + 1} to save`;
    const bi = v.before_start.findIndex(q => !q.question.trim() || (q.type === 'buttons' && q.answers.length < 2));
    return bi >= 0 ? `Finish the question before starting ${bi + 1} to save` : '';
  };
  const save = debounce(async () => {
    const why = problem();
    if (why) { status('wait', 'info', why); return; }
    const patch = Object.fromEntries([...pending].map(k => [k, v[k]]));
    pending.clear();
    try {
      const out = await api.pageSettings(host.kind, host.id, patch);
      host.setObj(out.obj); host.preview();
      status('', 'check', `Saved · version ${out.page.version}`);
    } catch (e) { status('bad', 'alert', 'Not saved'); toast(e.message, { kind: 'warn' }); }
  }, 700);
  const touch = (part) => { pending.add(part); status('busy', 'spin', 'Saving…'); save(); };
  let seq = 0;
  const uid = () => `pe-${++seq}`;
  const label = (id, text, help) => h('span.pg-f-t', h('label', { for: id }, text), help ? h('small', help) : null);
  const textIn = (obj, key, part, max, ph = '') => h('input.input', { id: uid(), value: obj[key] ?? '', maxLength: max, placeholder: ph, oninput: (e) => { obj[key] = e.target.value; touch(part); } });
  const sw = (obj, key, part) => h('label.switch', h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => { obj[key] = e.target.checked; touch(part); } }), h('span'));
  const seg = (opts, val, onPick, aria) => {
    const box = h('div.seg-ctl.pg-seg', { role: 'radiogroup', 'aria-label': aria });
    box.append(...opts.map(([k, t]) => h('button', { type: 'button', role: 'radio', class: val === k ? 'on' : '', 'aria-checked': val === k ? 'true' : 'false',
      onclick: (e) => { [...box.children].forEach(b => { b.classList.toggle('on', b === e.currentTarget); b.setAttribute('aria-checked', b === e.currentTarget ? 'true' : 'false'); }); onPick(k); } }, t)));
    return box;
  };
  const num = (obj, key, part, lo, hi, aria) => h('input.input.pg-num', { type: 'number', min: lo, max: hi, value: obj[key] ?? 0, 'aria-label': aria,
    oninput: (e) => { if (e.target.value !== '') { obj[key] = Math.max(lo, Math.min(hi, +e.target.value)); touch(part); } } });

  // one question: its words, its kind and what goes with the kind
  const kindsFor = (before) => (before ? BASIC_KINDS : r.page.kinds || BASIC_KINDS);
  const kindPicker = (q, before, onPick) => {
    const kinds = kindsFor(before);
    if (before) return seg(kinds.map(k => [k.kind, k.name]), q.type, onPick, 'Kind of answer');
    const groups = [...new Set(kinds.map(k => k.group))];
    const sel = h('select.select.pg-kind', { 'aria-label': 'Kind of answer', onchange: (e) => onPick(e.target.value) },
      groups.map(g => h('optgroup', { label: g }, kinds.filter(k => k.group === g).map(k => h('option', { value: k.kind, selected: k.kind === q.type }, k.name)))));
    return sel;
  };
  const qCard = (list, i, part, before, repaint) => {
    const q = list[i];
    const body = h('div.pg-q-body');
    const lines = (key, n) => (e) => { q[key] = e.target.value.split('\n').map(x => x.trim()).filter(Boolean).slice(0, n); touch(part); };
    const paintBody = () => {
      const k = kindsFor(before).find(x => x.kind === q.type) || BASIC_KINDS[0];
      const has = (x) => k.keys.includes(x);
      const parts = [h('p.pg-kind-does', k.does[0].toUpperCase() + k.does.slice(1) + '.')];
      if (has('answers')) {
        q.answers = q.answers || [];
        const [name, help] = LIST_NAME[q.type] || ['Labels', 'People give each mark one of these. Leave empty for no labels.'];
        parts.push(h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', name), h('small', help)), tagEditor(q.answers, () => touch(part), name.toLowerCase())));
      }
      if (has('from')) {
        const [lo1, hi1, lo2, hi2] = RANGE[q.type] || [0, 10, 1, 11];
        q.from ??= q.type === 'slider' || q.type === 'rating_over_time' ? 0 : 1; q.to ??= q.type === 'number' ? 100 : q.type === 'slider' || q.type === 'rating_over_time' ? 10 : 5;
        parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Range'), h('small', 'The lowest and the highest number.')),
          h('span.pg-range', num(q, 'from', part, lo1, hi1, 'From'), h('span', 'to'), num(q, 'to', part, lo2, hi2, 'To'))));
      }
      if (has('decimals')) {
        q.decimals ??= q.type === 'slider' ? 2 : 0;
        parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Decimals'), h('small', 'Digits after the point: 7, 7.3 or 7.25.')),
          seg([[0, 'None'], [1, 'One'], [2, 'Two']].concat(q.type === 'number' ? [[3, 'Three']] : []), q.decimals, (d) => { q.decimals = d; touch(part); }, 'Decimals')));
      }
      if (has('words')) {
        q.words ??= [];
        const two = q.type === 'slider' || q.type === 'rating_over_time';
        const wid = uid();
        parts.push(h('div.pg-f', label(wid, two ? 'Words at the two ends' : 'Words on the scale',
          two ? 'Two lines: the low end, then the high end. Leave empty for numbers only.' : 'Low to high, one per line: one for each number, or two for the ends.'),
        h('textarea.textarea', { id: wid, rows: two ? 2 : 3, value: q.words.join('\n'), oninput: lines('words', two ? 2 : 11) })));
      }
      if (has('count')) {
        q.count ??= 4;
        const [lo, hi] = q.type === 'best_worst' ? [3, 6] : [2, 9];
        parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Items on a screen'), h('small', `From ${lo} to ${hi}.`)), num(q, 'count', part, lo, hi, 'Items on a screen')));
      }
      if (has('same')) {
        q.same ??= true;
        parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', '“No difference” answer'), h('small', 'People may say A and B are the same.')), sw(q, 'same', part)));
      }
      if (has('several')) parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Pick several'), h('small', 'People may pick more than one.')), sw(q, 'several', part)));
      if (has('big_box') && !before) {
        q.big_box ??= true;
        parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Big box'), h('small', 'For long answers.')), sw(q, 'big_box', part)));
      }
      if (has('hint') && !before) {
        const hid = uid();
        parts.push(h('div.pg-f', label(hid, q.type === 'number' ? 'Unit' : 'Hint in the empty box', q.type === 'number' ? 'Shown after the box, like years or kg.' : null),
          Object.assign(textIn(q, 'hint', part, q.type === 'number' ? 20 : 120), { id: hid })));
      }
      if (!before) {
        const hid = uid();
        parts.push(h('div.pg-f', label(hid, 'Explanation', 'Shown under the question; leave empty for none.'), Object.assign(textIn(q, 'help', part, 300), { id: hid })));
      }
      parts.push(h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Required')), sw(q, 'required', part)));
      body.replaceChildren(...parts);
    };
    const qid = uid();
    const move = (d) => { const j = i + d; if (j < 0 || j >= list.length) return; [list[i], list[j]] = [list[j], list[i]]; touch(part); repaint(); };
    paintBody();
    return h('div.pg-q',
      h('div.pg-q-head', h('b', before ? `Before starting ${i + 1}` : `Question ${i + 1}`), h('div.grow'),
        h('button.icon-btn.sm', { type: 'button', title: 'Move up', 'aria-label': 'Move up', disabled: i === 0, onclick: () => move(-1) }, ico('chevron-up', 14)),
        h('button.icon-btn.sm', { type: 'button', title: 'Move down', 'aria-label': 'Move down', disabled: i === list.length - 1, onclick: () => move(1) }, ico('chevron-down', 14)),
        h('button.icon-btn.sm', { type: 'button', title: 'Remove', 'aria-label': 'Remove this question', disabled: !before && list.length < 2,
          onclick: () => { list.splice(i, 1); touch(part); repaint(); } }, ico('trash', 14))),
      h('div.pg-f', h('label.sr-only', { for: qid }, 'Question'), Object.assign(textIn(q, 'question', part, 300, 'The question people read'), { id: qid })),
      kindPicker(q, before, (k) => { q.type = k; touch(part); paintBody(); }),
      body);
  };
  const qList = (part, before) => {
    const box = h('div.pg-qs');
    const repaint = () => box.replaceChildren(...v[part].map((_, i) => qCard(v[part], i, part, before, repaint)),
      h('button.btn.sm.pg-q-add', { type: 'button', onclick: () => { v[part].push({ question: '', type: 'buttons', answers: [], required: !before, help: '' }); repaint();
        box.querySelectorAll('.pg-q input.input')[v[part].length - 1]?.focus(); } }, ico('plus', 13), before ? 'Add a question before starting' : 'Add a question'));
    repaint();
    return box;
  };

  const section = (title, desc, ...kids) => {
    const d = h('details.pg-sec', { open: editorOpen.has(title) },
      h('summary', h('span.pg-sec-t', h('b', title), h('small', desc)), h('span.pg-chev')), h('div.pg-sec-body', kids));
    d.addEventListener('toggle', () => (d.open ? editorOpen.add(title) : editorOpen.delete(title)));
    return d;
  };
  const tid = uid(), iid = uid();
  const ruleRows = Object.keys(v.rules).filter(k => RULES[k]).map(k => typeof v.rules[k] === 'boolean'
    ? h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', RULES[k][0]), RULES[k][1] ? h('small', RULES[k][1]) : null), sw(v.rules, k, 'rules'))
    : h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', RULES[k][0]), RULES[k][1] ? h('small', RULES[k][1]) : null),
      num(v.rules, k, 'rules', 0, k === 'items_per_person' ? 100000 : k === 'practice_items' ? 10 : 50, RULES[k][0])));
  const chartChips = (key, opts) => h('div.pg-checks', opts.map(o => {
    const b = h('button.chip', { type: 'button', class: v.charts[key].includes(o) ? 'on' : '', 'aria-pressed': v.charts[key].includes(o) ? 'true' : 'false',
      onclick: () => {
        const on = !v.charts[key].includes(o);
        v.charts[key] = on ? [...v.charts[key], o] : v.charts[key].filter(x => x !== o);
        b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); touch('charts');
      } }, ico('check', 12), optName(o));
    return b;
  }));
  const accent = h('div.pg-swatches', { role: 'radiogroup', 'aria-label': 'Accent colour' });
  const paintAccent = () => accent.replaceChildren(
    h('button.pg-sw.none', { type: 'button', role: 'radio', class: !v.look.accent ? 'on' : '', 'aria-checked': !v.look.accent ? 'true' : 'false', onclick: () => { v.look.accent = ''; touch('look'); paintAccent(); } }, 'Auto'),
    ...SWATCHES.map(c => h('button.pg-sw', { type: 'button', role: 'radio', 'aria-label': c, class: v.look.accent === c ? 'on' : '', 'aria-checked': v.look.accent === c ? 'true' : 'false',
      style: { '--c': c }, onclick: () => { v.look.accent = c; touch('look'); paintAccent(); } })),
    h('label.pg-sw.pick', { title: 'Any colour', class: v.look.accent && !SWATCHES.includes(v.look.accent) ? 'on' : '', style: v.look.accent && !SWATCHES.includes(v.look.accent) ? { '--c': v.look.accent } : null },
      ico('palette', 13), h('input', { type: 'color', value: v.look.accent || '#8eb69b', 'aria-label': 'Any colour', onchange: (e) => { v.look.accent = e.target.value; touch('look'); paintAccent(); } })));
  paintAccent();

  // the results file: its format, one row per what, extra details, column names; "See it" shows a sample
  function fileSection() {
    const o = v.output;
    const extras = [['name', 'Names'], ['before_start', 'Answers before starting'], ['time', 'Time answered'], ['seconds', 'Seconds per item'],
      ['plays', 'Plays'], ['majority', 'Majority answer'], ['path', 'File path'], ['group', 'Group'], ['system', 'System']];
    const rowsHelp = h('small');
    const paintRows = () => { rowsHelp.textContent = { answer: 'One row for each answer: an item and one person.',
      item: 'One row for each item, with each person’s answer in their own column.', person: 'One row for each person, with all their answers.' }[o.rows]; };
    paintRows();
    const names = h('div.pg-colnames');
    const paintNames = () => names.replaceChildren(...v.questions.map(q => {
      const have = o.column_names.find(c => c.question === q.question);
      const id = uid();
      return h('div.pg-f.row', h('label.pg-f-t', { for: id }, h('span.pg-f-l', q.question)),
        h('input.input.pg-colin', { id, value: have?.column || '', placeholder: q.question.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '_').replace(/^_|_$/g, '').slice(0, 28),
          oninput: (e) => { o.column_names = [...o.column_names.filter(c => c.question !== q.question), ...(e.target.value.trim() ? [{ question: q.question, column: e.target.value.trim() }] : [])]; touch('output'); } }));
    }));
    paintNames();
    return section('Results file', 'What you download with the answers',
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Format')),
        seg([['csv', 'CSV (Excel)'], ['json', 'JSON'], ['jsonl', 'JSON lines']], o.format, (k) => { o.format = k; touch('output'); }, 'File format')),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'One row per'), rowsHelp),
        seg([['answer', 'Answer'], ['item', 'Item'], ['person', 'Person']], o.rows, (k) => { o.rows = k; paintRows(); touch('output'); }, 'One row per')),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Also in the file')), h('div.pg-checks', extras.map(([k, t]) => {
        const b = h('button.chip', { type: 'button', class: o.include.includes(k) ? 'on' : '', 'aria-pressed': o.include.includes(k) ? 'true' : 'false',
          onclick: () => { const on = !o.include.includes(k); o.include = on ? [...o.include, k] : o.include.filter(x => x !== k);
            b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); touch('output'); } }, ico('check', 12), t);
        return b;
      }))),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Column names'), h('small', 'Leave empty for the short form shown.')), names),
      host.showFile ? h('button.btn.sm.pg-q-add', { type: 'button', onclick: () => host.showFile() }, ico('eye', 13), 'See a sample of the file') : null);
  }

  host.panel.replaceChildren(h('div.pg-settings',
    h('div.pg-settings-head', h('div', h('h2', 'Edit the page'), h('p', 'The same page the assistant changes. Every change is a new version; Undo in the Design tab takes it back.')), saved),
    section('The page', 'Title and instructions',
      h('div.pg-f', label(tid, 'Title'), Object.assign(textIn(v, 'title', 'title', 100), { id: tid })),
      h('div.pg-f', label(iid, 'Instructions', 'What people read before they start.'),
        h('textarea.textarea', { id: iid, rows: 4, dir: 'auto', value: v.instructions, maxLength: 1500, oninput: (e) => { v.instructions = e.target.value; touch('instructions'); } }))),
    section('Questions', 'Asked with each item, in this order', qList('questions', false),
      h('div.pg-f.row', h('span.pg-f-t', h('span.pg-f-l', 'Item position'), h('small', 'Where the recording, text, picture or video is shown.')),
        seg([['top', 'On top'], ['side', 'Beside']], v.item_position, (k) => { v.item_position = k; touch('item_position'); }, 'Item position'))),
    section('Before starting', 'Asked once, about the people themselves', qList('before_start', true)),
    fileSection(),
    section('People', 'Who does what', ruleRows),
    section('Charts', 'On the last screen and on your Results tab',
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'On the last screen'), h('small', 'What people see when they finish.')), chartChips('last_screen', ['my-answers', 'everyone', 'my-time'])),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'On your Results tab')), chartChips('results', ['answers', 'scores', 'systems', 'agreement', 'timeline', 'time-per-item', 'people']))),
    section('Look', 'Colours, language and progress',
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Colour mode')), h('select.select', { 'aria-label': 'Colour mode', onchange: (e) => { v.look.colours = e.target.value; touch('look'); } },
        ['forest', 'dark', 'light', 'navy', 'cream', 'charcoal'].map(o => h('option', { value: o, selected: v.look.colours === o }, optName(o))))),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Accent colour'), h('small', 'For buttons and highlights.')), accent),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Page language'), h('small', 'The language of the buttons.')),
        seg([['en', 'English'], ['fa', 'Persian']], v.look.language, (k) => { v.look.language = k; touch('look'); }, 'Page language')),
      h('div.pg-f', h('span.pg-f-t', h('span.pg-f-l', 'Progress shown as')),
        seg([['bar', 'Bar'], ['ring', 'Ring']], v.look.progress, (k) => { v.look.progress = k; touch('look'); }, 'Progress')))));
}

// ----------------------------------------------------------------------------- requests to the administrators
export class RequestsView {
  constructor() {
    this.filter = 'open';
    this.list = h('div.st-list');
    this.filterCtl = h('div.seg-ctl', { role: 'tablist', 'aria-label': 'Which requests' }, [['open', 'Open'], ['all', 'All']].map(([k, v]) =>
      h('button', { type: 'button', role: 'tab', class: k === this.filter ? 'on' : '', 'aria-selected': k === this.filter ? 'true' : 'false',
        onclick: (e) => { this.filter = k; [...this.filterCtl.children].forEach(b => { b.classList.toggle('on', b === e.currentTarget); b.setAttribute('aria-selected', b === e.currentTarget ? 'true' : 'false'); }); this.paint(); } }, v)));
    this.el = h('div.home', h('div.home-inner',
      h('div.home-head', h('div', h('h1', 'Requests'),
        this.lead = h('p', 'Pages you asked for when no ready-made page could do the task. An administrator answers each within 12 hours.')),
      this.filterCtl),
      this.list));
    this.load();
  }

  async load() {
    try { this.r = await api.pageRequests(); } catch (e) { errorToast(e); return; }
    if (this.r.admin) this.lead.textContent = `Requests from everyone: ${this.r.open} open. Answer each within 12 hours, then add the ready-made page it needs (backend/pages/templates/).`;
    this.paint();
  }

  paint() {
    const r = this.r;
    if (!r) return;
    const qs = r.requests.filter(q => this.filter === 'all' || q.status === 'open');
    if (!qs.length) {
      this.list.replaceChildren(h('div.st-empty', ico('send', 26), h('h2', this.filter === 'open' && r.requests.length ? 'No open requests' : 'No requests yet'),
        h('p', 'When no ready-made page fits a task, ask for one from the new-page steps or from the page chat.')));
      return;
    }
    this.list.replaceChildren(...qs.map(q => {
      const left = (new Date(q.due) - new Date()) / 3600e3;
      const due = q.status !== 'open' ? null : left < 0 ? h('span.badge.danger', `${Math.round(-left)} h late`)
        : h(`span.badge.${left < 3 ? 'warn' : 'accent'}`, `${Math.max(1, Math.round(left))} h left`);
      const answer = h('textarea.textarea', { rows: 3, value: q.answer || '', 'aria-label': 'Your answer', placeholder: 'Your answer' });
      const reply = async (status) => {
        try { await api.pageAnswer(q.id, answer.value, status); toast(status === 'done' ? 'Marked as done' : 'Answer sent', { kind: 'ok' }); this.load(); }
        catch (e) { toast(e.message, { kind: 'warn' }); }
      };
      return h(`div.pg-req.s-${q.status}`,
        h('div.pg-req-head',
          h('span.st-status', { class: q.status === 'open' ? 's-paused' : 's-live' }, q.status === 'open' ? 'Open' : q.status === 'done' ? 'Done' : 'Answered'),
          h('b', q.user_name || q.user_email || q.user),
          h('span.muted', [AREAS[q.area]?.name, MODS[q.modality]?.label].filter(Boolean).join(' · ')),
          h('span.muted', ago(q.at)), due, h('div.grow'),
          q.ref ? h('a.btn.sm', { href: builder(q.ref.kind, q.ref.id) }, 'Open the page') : null),
        h('p.pg-req-text', q.text),
        q.contact ? h('p.pg-small', `Email: ${q.contact}`) : null,
        r.admin ? h('div.pg-req-answer', answer, h('div.row',
          h('button.btn.sm.primary', { type: 'button', onclick: () => reply('answered') }, ico('send', 12), 'Send answer'),
          h('button.btn.sm', { type: 'button', onclick: () => reply('done') }, ico('check', 12), 'Done: the page exists')))
          : q.answer ? h('p.pg-answer', q.answer) : null);
    }));
  }
}

// ----------------------------------------------------------------------------- uploads into a shared task
const ARCHIVE = /\.(zip|tar|tgz|tar\.gz|tbz2|tar\.bz2|txz|tar\.xz)$/i;
const BIG = 64 << 20;   // above this a file goes in pieces (Cloudflare refuses a body over 100 MB)

async function inPieces(file, status) {
  const s = await api.uploadStart(file.name, file.size);
  let off = 0;
  while (off < file.size) {
    const r = await api.uploadPiece(s.id, off, file.slice(off, off + s.piece));
    off = r.received;
    status(`${file.name}: ${Math.floor((off / file.size) * 100)}% sent`);
  }
  return s.id;
}

// files (from a picker, a folder or a drop) into shared task `tid`; archives are unpacked on the server
export async function uploadFiles(tid, files, status) {
  let added = 0, bad = 0;
  for (const [i, f] of files.entries()) {
    const where = files.length > 1 ? ` (${i + 1} of ${files.length})` : '';
    try {
      if (ARCHIVE.test(f.name) || f.size > BIG) {
        const uid = await inPieces(f, (t) => status(t + where));
        let job = await api.uploadImport(uid, tid);
        while (job.state === 'running') {
          status(`${f.name}: ${ARCHIVE.test(f.name) ? `unpacking, ${job.added} files added` : 'adding'}${where}`);
          await new Promise(r => setTimeout(r, 1000));
          job = await api.uploadJob(uid);
        }
        if (job.state === 'error') throw new Error(job.error);
        added += job.added;
        if (job.skipped) toast(`${f.name}: ${job.skipped} files were not data files and were left out`);
      } else {
        status(`Uploading ${f.name}${where}`);
        added += (await api.sharedUpload(tid, f, f.webkitRelativePath || '')).added;
      }
    } catch (e) { bad++; if (bad <= 3) toast(`${f.name}: ${e.message}`, { kind: 'warn' }); }
  }
  return { added, bad };
}
