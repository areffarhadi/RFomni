// App shell: top bar, router, status bar.
import { api } from './api.js';
import { $, h, ico, isTyping, ls } from './util.js';
import { state, on, loadInfo, loadTasks } from './state.js';
import { parseHash, links } from './nav.js';
import { errorToast, modalOpen } from './ui.js';
import { HomeView } from './views/home.js';
import { ProjectView } from './views/project.js';
import { showShortcuts } from './views/shortcuts.js';

const app = $('#app');
const crumbs = h('div.crumbs');
const enginePill = h('div.engine-pill', h('i'), h('span', 'Engine…'));
const themeBtn = h('button.icon-btn', { title: 'Toggle light / dark', onclick: toggleTheme });
const topbar = h('header.topbar',
  h('a.brand', { href: '#/' }, ico('logo', 20, ''), h('b', 'Omni'), h('span.light', 'Studio')),
  crumbs, h('div.grow'), enginePill,
  h('button.icon-btn', { title: 'Keyboard shortcuts (?)', onclick: showShortcuts }, ico('keyboard', 17)),
  themeBtn);
const main = h('main.main');
const jobEl = h('div.job');
const saveEl = h('span.save-state');
const wsEl = h('span');
const verEl = h('span');
const statusbar = h('footer.statusbar', jobEl, h('div.sb-right', saveEl, wsEl, verEl));
app.append(topbar, main, statusbar);

function setThemeIcon() {
  themeBtn.replaceChildren(ico(document.documentElement.dataset.theme === 'light' ? 'moon' : 'sun', 17));
}
function toggleTheme() {
  const t = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
  ls.set('theme', t);
  setThemeIcon();
  window.dispatchEvent(new Event('themechange'));
}
setThemeIcon();

// ---- breadcrumbs ----------------------------------------------------------
let crumbFile = null;
function renderCrumbs() {
  const items = [h('span', '/'), h('a', { href: '#/' }, 'Projects')];
  const { parts } = parseHash();
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
      h('span', `Running ${j.task_ids.length} task${j.task_ids.length > 1 ? 's' : ''} · ${j.done}/${j.total} files`),
      h('div.progress', h('i', { style: { width: pct + '%' } })),
      j.current ? h('span.muted', j.current) : null,
      h('button', { onclick: () => api.cancelJob(j.id) }, 'Cancel'));
  } else {
    const msg = j.status === 'done' ? `Run finished · ${j.written_tiers} tiers written` + (j.skipped_tiers ? `, ${j.skipped_tiers} skipped` : '')
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
  try {
    if (parts[0] === 'p' && parts[1]) {
      const pid = parts[1];
      if (!(view instanceof ProjectView) || view.pid !== pid) {
        if (view) { await view.beforeLeave?.(); view.destroy?.(); }
        crumbFile = null;
        view = new ProjectView(pid);
        main.replaceChildren(view.el);
        await view.load();
      }
      let tab = parts[2] || 'data', mid = null;
      if (tab === 'f') { tab = 'data'; mid = parts[3] || null; }
      if (!mid) crumbFile = null;
      await view.update({ tab, mid, params });
    } else {
      if (view) { await view.beforeLeave?.(); view.destroy?.(); }
      state.project = null;
      crumbFile = null;
      view = new HomeView();
      main.replaceChildren(view.el);
    }
  } catch (e) {
    errorToast(e);
    if (e.status === 404) location.hash = '#/';
  }
  renderCrumbs();
}

window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', (e) => {
  if (view?.isDirty?.()) { view.flush?.(); e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('keydown', (e) => {
  if (e.key === '?' && !isTyping(e) && !modalOpen()) { e.preventDefault(); showShortcuts(); }
});

(async function boot() {
  try {
    await Promise.all([loadInfo(), loadTasks()]);
    const eng = state.info.engine;
    enginePill.classList.toggle('on', !!eng.connected);
    enginePill.lastChild.textContent = eng.connected ? eng.title : 'Mock engine · no model';
    enginePill.title = eng.description || '';
    wsEl.textContent = state.info.workspace;
    wsEl.title = 'Workspace folder';
    verEl.textContent = `v${state.info.version} · ${state.info.codename}`;
  } catch (e) {
    errorToast(e, 'Could not reach the Omni Studio server');
  }
  route();
})();
