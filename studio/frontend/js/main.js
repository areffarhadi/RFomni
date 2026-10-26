// App shell: top bar, router, status bar.
import { api, setShareSession } from './api.js';
import { $, h, ico, isTyping, ls } from './util.js';
import { state, on, loadInfo, loadTasks, shareInfo } from './state.js';
import { parseHash, links } from './nav.js';
import { errorToast, menu, modalOpen, openModal, popover, toast } from './ui.js';
import { HomeView } from './views/home.js';
import { ProjectView } from './views/project.js';
import { showShortcuts } from './views/shortcuts.js';
import { openShareDialog } from './views/share.js';
import { swap, watchSegments } from './motion.js';
import { mountAssistant } from './assistant.js';
import { SharedListView, SharedTaskView } from './views/shared.js';
import { CollectListView, CollectView } from './views/collect.js';

const app = $('#app');
const crumbs = h('div.crumbs');
const enginePill = h('div.engine-pill', h('i'), h('span', 'Engine…'));
const themeBtn = h('button.icon-btn', { onclick: () => openThemeMenu(), 'aria-haspopup': 'menu' });
const shareBtn = h('button.btn.sm.share-btn', { title: 'Share this project with a link', onclick: () => openShareDialog(), style: { display: 'none' } }, ico('share', 14), 'Share');
const sharedPill = h('div.shared-pill', { style: { display: 'none' } });
// to the landing page (what Omni Studio is); its "Open the studio" signs you in and brings you back here
const homeLink = h('a.btn.sm.ghost.owner-only', { href: '/landing/', title: 'Home page: the platform, with Chat, Task sharing, Subjective tests and Data collection' }, ico('home', 14), 'Home');
// who is signed in (accounts are the chat page's, see backend/auth.py), and signing out
const userBtn = h('button.icon-btn.owner-only', { title: 'Account', 'aria-label': 'Account', 'aria-haspopup': 'menu', onclick: () => openUserMenu() }, ico('user', 17));
let me = null;
async function loadMe() {
  try { me = (await api.me()).user; userBtn.title = `Signed in as ${me.name || me.email}`; } catch { /* the gate sends a signed-out page to the sign-in page */ }
}
function openUserMenu() {
  menu(userBtn, [
    me && { header: me.name ? `${me.name} (${me.email})` : me.email },
    { label: 'Open the chat', icon: 'chat', onClick: () => window.open((location.port ? `https://${location.hostname}:8080` : `https://chat.${location.hostname}`) + '/', '_blank', 'noopener') },
    'sep',
    { label: 'Sign out', icon: 'logout', onClick: async () => { await api.signOut().catch(() => {}); location.href = '/login/?next=studio'; } },
  ], { placement: 'bottom-end' });
}
// shared tasks: data that needs human labels, shared as a link (views/shared.js)
const sharedTasksLink = h('a.btn.sm.ghost.owner-only', { href: '#/shared', title: 'Shared labelling tasks: design a page, share a link' }, ico('users', 14), 'Shared tasks');
const listenLink = h('a.btn.sm.ghost.owner-only', { href: '#/listen', title: 'Subjective tests: listening tests that people rate' }, ico('audio', 14), 'Subjective tests');
const collectLink = h('a.btn.sm.ghost.owner-only', { href: '#/collect', title: 'Data collection: projects that ask people to record' }, ico('mic', 14), 'Data collection');
const brand = h('a.brand', { href: '#/' }, ico('logo', 26, ''), h('b', 'Omni'), h('span.light', 'Studio'));
const topbar = h('header.topbar',
  brand,
  crumbs, h('div.grow'), sharedPill, sharedTasksLink, listenLink, collectLink, homeLink, shareBtn, enginePill,
  h('button.icon-btn', { title: 'Keyboard shortcuts (?)', onclick: showShortcuts }, ico('keyboard', 17)),
  themeBtn, userBtn);
const main = h('main.main');
const jobEl = h('div.job');
const saveEl = h('span.save-state');
const wsEl = h('span');
const verEl = h('span');
const statusbar = h('footer.statusbar', jobEl, h('div.sb-right', saveEl, wsEl, verEl));
app.append(topbar, main, statusbar);

// ---- themes ----------------------------------------------------------------
// The colours of each theme live in app.css; `sw` only previews them in the menu (page, surface, accent).
const THEMES = [  // same names as the mode switch on the landing page
  { id: 'light', label: 'Day', hint: 'Bright, for daylight', icon: 'sun', sw: ['#f5f6f8', '#ffffff', '#0969da'] },
  { id: 'dark', label: 'Night', hint: 'Neutral greys, blue accent', icon: 'moon', sw: ['#0f1012', '#24262a', '#5d9bff'] },
  { id: 'forest', label: 'Green', hint: 'Deep greens, sage accent', icon: 'leaf', sw: ['#051f20', '#235347', '#8eb69b'] },
];
const curTheme = () => THEMES.find(t => t.id === document.documentElement.dataset.theme) || THEMES[1];

function setThemeIcon() {
  const t = curTheme();
  themeBtn.replaceChildren(ico(t.icon, 17));
  themeBtn.title = `Theme: ${t.label}`;
  themeBtn.setAttribute('aria-label', `Theme: ${t.label}. Change theme`);
}
function setTheme(id) {
  document.documentElement.dataset.theme = id;
  ls.set('theme', id);
  setThemeIcon();
  window.dispatchEvent(new Event('themechange'));
}
function openThemeMenu() {
  const cur = curTheme().id;
  const items = THEMES.map(t => h('button.menu-item.theme-item' + (t.id === cur ? '.active' : ''), {
    type: 'button', role: 'menuitemradio', 'aria-checked': String(t.id === cur),
    onclick: () => { pop.close(); setTheme(t.id); themeBtn.focus(); },
  }, h('span.theme-sw', { 'aria-hidden': 'true' }, t.sw.map(c => h('i', { style: { background: c } }))),
  h('span.menu-label', h('b', t.label), h('small', t.hint)),
  t.id === cur ? ico('check', 15) : h('span.ico-spacer')));
  const list = h('div.menu.theme-menu', { role: 'menu', 'aria-label': 'Theme' }, h('div.menu-head', 'Theme'), items);
  list.addEventListener('keydown', (e) => {  // arrow keys move between the options
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
    }
  });
  const pop = popover(themeBtn, list, { placement: 'bottom-end', onClose: () => themeBtn.setAttribute('aria-expanded', 'false') });
  themeBtn.setAttribute('aria-expanded', 'true');
  (items.find(b => b.classList.contains('active')) || items[0]).focus();
}
setThemeIcon();
watchSegments();  // every segmented control gets the sliding highlight

// ---- breadcrumbs ----------------------------------------------------------
let crumbFile = null;
function renderCrumbs() {
  const shared = shareInfo();
  const { parts } = parseHash();
  const items = shared ? [] : parts[0] === 'shared' ? [h('span', '/'), h('a', { href: '#/shared' }, 'Shared tasks')]
    : parts[0] === 'collect' ? [h('span', '/'), h('a', { href: '#/collect' }, 'Data collection')]
    : parts[0] === 'listen' ? [h('span', '/'), h('a', { href: '#/listen' }, 'Subjective tests')]
    : [h('span', '/'), h('a', { href: '#/' }, 'Projects')];
  shareBtn.style.display = !shared && parts[0] === 'p' && state.project ? '' : 'none';
  if (parts[0] === 'p' && state.project) {
    items.push(h('span', '/'));
    const last = !crumbFile;
    items.push(last ? h('span.cur', state.project.name) : h('a', { href: links.project(state.project.id) }, state.project.name));
    if (crumbFile) items.push(h('span', '/'), h('span.cur', crumbFile));
  }
  crumbs.replaceChildren(...items);
}
on('project', renderCrumbs);
on('editor-file', (name) => { crumbFile = name; renderCrumbs(); });

// ---- status bar -----------------------------------------------------------
let jobHideTimer;
on('job', (j) => {
  if (j.project_id !== state.project?.id) return;
  clearTimeout(jobHideTimer);
  const pct = j.total ? (j.done / j.total) * 100 : 0;
  if (j.status === 'running' || j.status === 'queued') {
    jobEl.replaceChildren(h('div.spinner'),
      h('span', `Running ${j.task_ids.length} task${j.task_ids.length > 1 ? 's' : ''}: ${j.done}/${j.total} files`),
      h('div.progress', h('i', { style: { width: pct + '%' } })),
      j.current ? h('span.muted', j.current) : null,
      h('button', { onclick: () => api.cancelJob(j.id) }, 'Cancel'));
  } else {
    const msg = j.status === 'done' ? `Run finished: ${j.written_tiers} tiers written` + (j.skipped_tiers ? `, ${j.skipped_tiers} skipped` : '')
      : j.status === 'cancelled' ? 'Run cancelled' : 'Run failed';
    jobEl.replaceChildren(ico(j.status === 'done' ? 'check' : 'alert', 14), h('span', msg),
      j.errors?.length ? h('span', { style: { color: 'var(--danger)' } }, `${j.errors.length} error(s)`) : null);
    jobHideTimer = setTimeout(() => jobEl.replaceChildren(), 9000);
  }
});
on('save-state', (s) => {
  saveEl.className = 'save-state ' + (s.state || '');
  saveEl.textContent = s.text || '';
});

// ---- router ---------------------------------------------------------------
let view = null;
async function route() {
  const { parts, params } = parseHash();
  const shared = shareInfo();
  if (shared && (parts[0] !== 'p' || parts[1] !== shared.project_id)) {  // a shared link only opens its project
    location.replace(links.project(shared.project_id));
    return;
  }
  try {
    if (parts[0] === 'p' && parts[1]) {
      const pid = parts[1];
      if (!(view instanceof ProjectView) || view.pid !== pid) {
        const old = view;
        if (old) { await old.beforeLeave?.(); old.destroy?.(); }
        crumbFile = null;
        view = new ProjectView(pid);
        swap(main, old?.el, view.el, 1, !!old);  // into a project: forward
        await view.load();
      }
      let tab = parts[2] || 'data', mid = null;
      if (tab === 'f') { tab = 'data'; mid = parts[3] || null; }
      if (!mid) crumbFile = null;
      await view.update({ tab, mid, params });
    } else if (parts[0] === 'listen') {
      const old = view;
      if (old) { await old.beforeLeave?.(); old.destroy?.(); }
      state.project = null;
      crumbFile = null;
      view = new SharedListView('listening');
      swap(main, old?.el, view.el, -1, !!old);
      renderCrumbs();
    } else if (parts[0] === 'collect') {
      const old = view;
      if (old) { await old.beforeLeave?.(); old.destroy?.(); }
      state.project = null;
      crumbFile = null;
      view = parts[1] ? new CollectView(parts[1]) : new CollectListView();
      swap(main, old?.el, view.el, parts[1] ? 1 : -1, !!old);
      renderCrumbs();
    } else if (parts[0] === 'shared') {
      const old = view;
      if (old) { await old.beforeLeave?.(); old.destroy?.(); }
      state.project = null;
      crumbFile = null;
      view = parts[1] ? new SharedTaskView(parts[1]) : new SharedListView();
      swap(main, old?.el, view.el, parts[1] ? 1 : -1, !!old);
      renderCrumbs();
    } else {
      const old = view;
      if (old) { await old.beforeLeave?.(); old.destroy?.(); }
      state.project = null;
      crumbFile = null;
      view = new HomeView();
      swap(main, old?.el, view.el, -1, !!old);  // back to the list
    }
  } catch (e) {
    errorToast(e);
    if (e.status === 404) location.hash = '#/';
  }
  if (!shared) ls.set('last', location.hash || '#/');  // the landing page's "Open the studio" returns here
  renderCrumbs();
}

window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', (e) => {
  if (view?.isDirty?.()) { view.flush?.(); e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('keydown', (e) => {
  if (e.key === '?' && !isTyping(e) && !modalOpen()) { e.preventDefault(); showShortcuts(); }
});

/** Opened through a share link: no project list, no runs or uploads; view-only links cannot edit. */
function setupShared(sh) {
  document.body.classList.add('shared', sh.access === 'edit' ? 'can-edit' : 'readonly');
  brand.href = links.project(sh.project_id);
  enginePill.style.display = 'none';
  sharedPill.style.display = '';
  sharedPill.title = [sh.note, sh.expires_at ? `Link expires ${new Date(sh.expires_at).toLocaleDateString()}` : ''].filter(Boolean).join('\n');
  sharedPill.replaceChildren(ico(sh.access === 'edit' ? 'edit' : 'eye', 13),
    h('span', sh.access === 'edit' ? 'Shared: you can review and edit labels' : 'Shared: view only'),
    sh.label ? h('b', sh.label) : null);
  document.title = `${sh.project_name} · Omni Studio`;
  if (!sh.allow_download) {
    // the data can be played and labelled here, not saved: no "save as" menu on the media (the server also
    // refuses clips opened as a page). Anything a browser plays can still be recorded; this keeps it from being one click.
    document.body.classList.add('no-download');
    document.addEventListener('contextmenu', (e) => { if (e.target.closest?.('video, audio, img, canvas')) e.preventDefault(); }, true);
    document.addEventListener('dragstart', (e) => { if (e.target.closest?.('video, img')) e.preventDefault(); }, true);
  }
  if (sh.access === 'edit') shareSessionFor(sh);
}

// People on an edit link get a session ID (no account): their changes are signed with it, and they can continue later
// with it on any device. It is remembered in this browser.
async function shareSessionFor(sh) {
  const key = `omni:share-session:${sh.id}`;
  let saved = null;
  try { saved = localStorage.getItem(key); } catch { /* not remembered */ }
  const use = (s) => {
    setShareSession(s.id);
    try { localStorage.setItem(key, s.id); } catch { /* not remembered */ }
    sharedPill.append(h('button.sid-chip', { title: 'Your session ID: your changes are saved under it. Click to copy.',
      onclick: () => navigator.clipboard?.writeText(s.id).then(() => toast('Session ID copied')) }, s.id));
  };
  if (saved) {
    try { return use((await api.shareSession({ resume: saved })).session); } catch { /* gone: start again */ }
  }
  const name = h('input.input', { placeholder: sh.ask_name ? 'Your name or ID' : 'Your name (optional)', maxLength: 60 });
  const resume = h('input.input', { placeholder: 'e.g. K7Q2-M9XD', spellcheck: false });
  const msg = h('p.hint');
  openModal({
    title: `Welcome to ${sh.project_name}`, width: 480,
    subtitle: 'You can check and correct the labels here. No account is needed: your changes are saved under a session ID, which you can use to continue later.',
    body: h('div', sh.note ? h('div.notice.info', { style: { marginBottom: '14px' } }, ico('info', 16), h('div', sh.note)) : null,
      h('label.field', h('span', sh.ask_name ? 'Your name or ID' : 'Your name (optional)'), name),
      h('label.field', h('span', 'Or continue an earlier session'), resume), msg),
    actions: [h('div.grow'), { label: 'Start', primary: true, onClick: async (m) => {
      if (sh.ask_name && !name.value.trim() && !resume.value.trim()) { msg.textContent = 'Please enter your name or ID.'; name.focus(); return; }
      try {
        const r = await api.shareSession(resume.value.trim() ? { resume: resume.value.trim() } : { name: name.value.trim() });
        use(r.session);
        m.close();
        toast(resume.value.trim() ? `Welcome back. Session ${r.session.id}.` : `Your session ID is ${r.session.id}. Keep it to continue later.`, { timeout: 7000 });
      } catch (e) { msg.textContent = e.message; }
    } }],
  });
}

(async function boot() {
  try {
    await Promise.all([loadInfo(), loadTasks()]);
    const shared = shareInfo();
    if (shared) setupShared(shared);
    else loadMe();
    const eng = state.info.engine;
    enginePill.classList.toggle('on', !!eng.connected);
    enginePill.lastChild.textContent = eng.connected ? eng.title : 'Mock engine (no model)';
    enginePill.title = eng.description || '';
    wsEl.textContent = state.info.workspace || '';
    wsEl.title = 'Workspace folder';
    verEl.textContent = `v${state.info.version} “${state.info.codename}”`;
    mountAssistant();
  } catch (e) {
    errorToast(e, 'Could not reach the Omni Studio server');
  }
  route();
})();
