// The assistant panel: a floating chat in the bottom-right corner.
// Text goes to /api/agent/*; voice (assistant_voice.js) reaches the same plan through the voice agent.
// Actions the assistant wants to take arrive as a proposal card and only run after Approve.
import { api, signedOut } from './api.js';
import { h, ico, ls } from './util.js';
import { isShared, loadTasks, refreshProject, trackJob } from './state.js';
import { go } from './nav.js';
import { errorToast } from './ui.js';
import { pickAndUpload } from './views/upload.js';
import { attachVoice } from './assistant_voice.js';
import { applyFilter, pointAt } from './guide.js';
import { toast } from './ui.js';

const SUGGESTIONS = [
  'Start a new labeling project',
  'What can Omni Studio label?',
  'Help me choose tasks for my audio',
];
const STEP_LABEL = { project: 'Project', data: 'Data', tasks: 'Tasks', test: 'Test', run: 'Run', review: 'Review' };

async function call(method, path, body) {
  const r = await fetch(`/api/agent/${path}`, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) signedOut();
  const data = await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error((data && data.detail) || r.statusText), { status: r.status });
  checkVersion(data?.ui_version);
  return data;
}

// The studio's files changed since this page was loaded: the open page still runs the old code.
let loadedVersion = null, warned = false;
function checkVersion(v) {
  if (!v) return;
  if (!loadedVersion) loadedVersion = v;
  else if (v !== loadedVersion && !warned) {
    warned = true;
    toast('Omni Studio was updated. Reload the page to use the new version.', { timeout: 0, action: { label: 'Reload', onClick: () => location.reload() } });
  }
}

class AssistantPanel {
  constructor() {
    this.busy = false;
    this.loaded = false;
    this.voice = null;  // set by assistant_voice.js when the voice agent is reachable
    this.log = h('div.asst-log', { role: 'log', 'aria-live': 'polite', tabindex: 0, 'aria-label': 'Conversation' });
    this.stepEl = h('span.asst-step');
    this.stateEl = h('span.asst-state', h('i'), h('span', 'Online'));
    this.input = h('textarea.asst-input', {
      rows: 1, placeholder: 'Ask anything…', 'aria-label': 'Message to the assistant',
      onkeydown: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
        if (e.key === 'Escape') this.toggle(false);
      },
      oninput: () => this.fit(),
    });
    this.sendBtn = h('button.asst-send', { title: 'Send (Enter)', 'aria-label': 'Send', onclick: () => this.send() }, ico('send', 16));
    this.micBtn = h('button.asst-icon.asst-mic', { title: 'Talk to the assistant', 'aria-label': 'Voice', hidden: true, onclick: () => this.voice?.toggle() }, ico('mic', 16));
    this.voiceEl = h('div.asst-voice', { hidden: true });
    this.panel = h('section.asst-panel', { role: 'dialog', 'aria-label': 'Omni assistant', hidden: true },
      h('div.asst-head',
        h('span.asst-badge', ico('logo', 20, '')),
        h('div.asst-title', h('b', 'Omni Assistant'), h('div.asst-sub', this.stateEl, this.stepEl)),
        h('div.grow'),
        h('button.asst-stop', { title: 'Stop whatever the assistant is doing, including a running job', onclick: () => this.stop() }, ico('stop', 12), 'Stop'),
        h('button.asst-icon', { title: 'Start over (forget this plan)', 'aria-label': 'Start over', onclick: () => this.reset() }, ico('undo', 15)),
        h('button.asst-icon', { title: 'Close (Esc)', 'aria-label': 'Close', onclick: () => this.toggle(false) }, ico('x', 16))),
      this.log,
      this.voiceEl,
      h('div.asst-compose', h('div.asst-field', this.input, this.micBtn, this.sendBtn)),
      h('div.asst-foot', 'Omni Studio assistant · asks before it changes anything'));
    this.fab = h('button.asst-fab', { title: 'Assistant', 'aria-label': 'Open the assistant', 'aria-expanded': 'false', onclick: () => this.toggle() }, ico('chat', 22));
    document.body.append(this.panel, this.fab);
    if (ls.get('assistant-open', false)) this.toggle(true);
  }

  fit() {
    this.input.style.height = 'auto';
    this.input.style.height = Math.min(this.input.scrollHeight, 120) + 'px';
  }

  async toggle(open = this.panel.hidden) {
    this.panel.hidden = !open;
    this.fab.classList.toggle('open', open);
    this.fab.setAttribute('aria-expanded', String(open));
    ls.set('assistant-open', open);
    if (open) {
      if (!this.loaded) await this.loadPlan();
      this.input.focus();
    }
  }

  async loadPlan() {
    this.loaded = true;
    try {
      const p = await call('GET', 'plan');
      this.log.replaceChildren();
      if (!p.history.length) this.log.append(this.welcome());
      for (const m of p.history) this.add(m.role, m.text);
      this.setStep(p.step);
      if (p.pending) this.card(p.pending);
    } catch (e) {
      this.loaded = false;
      this.setOnline('Offline');
      this.add('error', `The assistant is not reachable: ${e.message}`);
    }
  }

  /** Empty chat: a greeting and a few ways to start, like a fresh chat card. */
  welcome() {
    return h('div.asst-welcome',
      h('span.asst-badge.lg', ico('logo', 28, '')),
      h('h3', 'How can I help today?'),
      h('p', 'Set up a project, add data, pick or write tasks, test them on a few files and run them.'),
      h('div.asst-chips', SUGGESTIONS.map(t => h('button.asst-chip', { onclick: () => this.send(t) }, t))));
  }

  setOnline(text, live = false) {
    this.stateEl.lastChild.textContent = text;
    this.stateEl.classList.toggle('live', live);
  }

  setStep(step) {
    this.stepEl.textContent = step ? `Step · ${STEP_LABEL[step] || step}` : '';
  }

  add(role, text, extra) {
    this.log.querySelector('.asst-welcome')?.remove();
    const el = h(`div.asst-msg.${role}`, h('div.asst-bubble', text, extra));
    this.log.append(el);
    this.log.scrollTop = this.log.scrollHeight;
    return el;
  }

  thinking(on) {
    this.busy = on;
    this.sendBtn.disabled = on;
    this.typing?.remove();
    this.typing = on ? this.add('assistant.typing', h('span.asst-dots', h('i'), h('i'), h('i'))) : null;
  }

  /** The approval card. Only one proposal is open at a time; a new reply retires older cards. */
  card(pr) {
    for (const c of this.log.querySelectorAll('.asst-card:not(.done)')) this.retire(c, 'Replaced');
    const yes = h('button.asst-btn.primary', { onclick: () => this.decide(pr, true, el) }, ico('check', 13), 'Approve');
    const no = h('button.asst-btn', { onclick: () => this.decide(pr, false, el) }, 'Reject');
    const el = h('div.asst-card', { dataset: { id: pr.id } },
      h('div.asst-card-head', ico('shield', 14), 'Needs your approval'),
      h('p', pr.summary),
      h('div.asst-card-actions', yes, no));
    this.log.append(el);
    this.log.scrollTop = this.log.scrollHeight;
  }

  retire(cardEl, label) {
    cardEl.classList.add('done');
    cardEl.querySelector('.asst-card-actions')?.replaceChildren(h('span.asst-card-state', label));
  }

  async decide(pr, ok, cardEl) {
    if (this.busy) return;
    this.retire(cardEl, ok ? 'Approved' : 'Rejected');
    this.thinking(true);
    try {
      this.apply(await call('POST', ok ? 'approve' : 'reject', { proposal_id: pr.id, via: 'click' }));
    } catch (e) {
      this.add('error', e.message);
    } finally {
      this.thinking(false);
    }
  }

  async send(text = this.input.value.trim()) {
    if (!text || this.busy) return;
    this.input.value = '';
    this.fit();
    this.add('user', text);
    this.thinking(true);
    try {
      this.apply(await call('POST', 'turn', { text, via: 'text', page: pageContext() }));
    } catch (e) {
      this.add('error', e.message);
    } finally {
      this.thinking(false);
    }
  }

  async stop() {
    this.voice?.interrupt();
    try {
      const r = await call('POST', 'stop', {});
      for (const c of this.log.querySelectorAll('.asst-card:not(.done)')) this.retire(c, 'Stopped');
      this.apply(r);
    } catch (e) {
      errorToast(e);
    }
  }

  async reset() {
    try {
      await call('DELETE', 'plan');
      this.loaded = false;
      await this.loadPlan();
    } catch (e) {
      errorToast(e);
    }
  }

  /** A reply from the brain: the text, what to do in the page, and maybe a proposal. */
  apply(r, { echo = true } = {}) {
    if (!r) return;
    if (echo && r.say) {
      const upload = (r.ui || []).some(u => u.type === 'open_upload');
      const pid = r.plan?.project_id;
      this.add('assistant', r.say, upload && pid
        ? h('div.asst-inline', h('button.asst-btn', { onclick: () => pickAndUpload(pid) }, ico('upload', 13), 'Choose files'))
        : null);
    }
    this.setStep(r.step);
    if (r.proposal) this.card(r.proposal);
    else if (r.plan && !r.plan.pending) {
      for (const c of this.log.querySelectorAll('.asst-card:not(.done)')) this.retire(c, 'Closed');
    }
    this.run(r.ui || []);
  }

  async run(actions) {
    for (const a of actions) {
      try {
        if (a.type === 'navigate') go(a.hash);
        else if (a.type === 'refresh') { await loadTasks(); await refreshProject(); }
        else if (a.type === 'job') trackJob(await api.job(a.job_id));
        else if (a.type === 'filter_tasks') await applyFilter(a);
        else if (a.type === 'point') pointAt(a.target, a.note);
      } catch (e) {
        errorToast(e);
      }
    }
  }

  /** The panel stays reachable from the keyboard: Ctrl+/ opens it from anywhere in the studio. */
  bindKeys() {
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === '/') { e.preventDefault(); this.toggle(); }
    });
    // the chat page talks to the same plan: catch up when the user comes back to this tab
    window.addEventListener('focus', () => {
      if (!this.panel.hidden && !this.busy && !this.voice?.live) this.loadPlan();
    });
  }
}

export let assistant = null;

// What the user has open: the route and the parts of the page the assistant knows by name (its data-guide
// names, without the many task cards). The assistant answers about this project and page.
function pageContext() {
  const parts = [...document.querySelectorAll('[data-guide]')]
    .filter(el => el.getClientRects().length && !el.dataset.guide.startsWith('task:'))
    .map(el => el.dataset.guide);
  return { hash: location.hash || '#/', parts: [...new Set(parts)] };
}

// spoken questions go through the voice agent, which cannot see the page: tell the studio when it changes
let pageTimer = 0;
function reportPage() {
  clearTimeout(pageTimer);
  pageTimer = setTimeout(() => call('POST', 'page', pageContext()).catch(() => {}), 700);  // after the view renders
}

export function mountAssistant() {
  if (isShared() || assistant) return assistant;  // share links are for labelling, not for the assistant
  assistant = new AssistantPanel();
  assistant.bindKeys();
  call('GET', 'plan').catch(() => {});  // remembers which version of the studio this page runs
  attachVoice(assistant);
  addEventListener('hashchange', reportPage);
  reportPage();
  return assistant;
}
