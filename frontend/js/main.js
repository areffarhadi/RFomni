// App shell: top bar, router, status bar.
import { api } from './api.js';
import { $, h, ico, isTyping, ls } from './util.js';
import { state, on, loadInfo, loadTasks, shareInfo } from './state.js';
import { parseHash, links } from './nav.js';
import { errorToast, modalOpen, popover } from './ui.js';
import { HomeView } from './views/home.js';
import { ProjectView } from './views/project.js';
import { showShortcuts } from './views/shortcuts.js';
import { openShareDialog } from './views/share.js';
import { swap, watchSegments } from './motion.js';

const app = $('#app');
const crumbs = h('div.crumbs');
const enginePill = h('div.engine-pill', h('i'), h('span', 'Engine…'));
const themeBtn = h('button.icon-btn', { onclick: () => openThemeMenu(), 'aria-haspopup': 'menu' });
const shareBtn = h('button.btn.sm.share-btn', { title: 'Share this project with a link', onclick: () => openShareDialog(), style: { display: 'none' } }, ico('share', 14), 'Share');
const sharedPill = h('div.shared-pill', { style: { display: 'none' } });
// to the landing page (what Omni Studio is); its "Open the studio" signs you in and brings you back here
const aboutLink = h('a.btn.sm.ghost.owner-only', { href: '/landing/', title: 'About Omni Studio: what it does' }, ico('info', 14), 'About');
const brand = h('a.brand', { href: '#/' }, ico('logo', 26, ''), h('b', 'Omni'), h('span.light', 'Studio'));
const topbar = h('header.topbar',
  brand,
  crumbs, h('div.grow'), sharedPill, aboutLink, shareBtn, enginePill,
  h('button.icon-btn', { title: 'Keyboard shortcuts (?)', onclick: showShortcuts }, ico('keyboard', 17)),
  themeBtn);
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
  const items = shared ? [] : [h('span', '/'), h('a', { href: '#/' }, 'Projects')];
  const { parts } = parseHash();
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
    h('span', sh.access === 'edit' ? 'Shared · you can review and edit labels' : 'Shared · view only'),
    sh.label ? h('b', sh.label) : null);
  document.title = `${sh.project_name} · Omni Studio`;
}

(async function boot() {
  try {
    await Promise.all([loadInfo(), loadTasks()]);
    const shared = shareInfo();
    if (shared) setupShared(shared);
    const eng = state.info.engine;
    enginePill.classList.toggle('on', !!eng.connected);
    enginePill.lastChild.textContent = eng.connected ? eng.title : 'Mock engine · no model';
    enginePill.title = eng.description || '';
    wsEl.textContent = state.info.workspace || '';
    wsEl.title = 'Workspace folder';
    verEl.textContent = `v${state.info.version} · ${state.info.codename}`;
  } catch (e) {
    errorToast(e, 'Could not reach the Omni Studio server');
  }
  route();
})();
