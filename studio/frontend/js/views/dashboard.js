// Project overview: stats, tasks, file x task coverage matrix.
import { api } from '../api.js';
import { h, ico, fmtDur, modalityIcon, rafThrottle } from '../util.js';
import { state, on, projectTasks, refreshProject, isShared, shareInfo } from '../state.js';
import { go, links } from '../nav.js';
import { menu, promptDialog, confirmDialog, errorToast, toast } from '../ui.js';
import { addDataMenuItems, pickAndUpload, openLinkDialog } from './upload.js';
import { openRunDialog } from './run.js';
import { taskChip } from './home.js';
import { STATUS } from './files.js';

export class Dashboard {
  constructor(dv) {
    this.dv = dv;
    this.inner = h('div.dash-inner');
    this.el = h('div.dash', this.inner);
    this.renderSoon = rafThrottle(() => this.render());
    this.unsub = [on('media', this.renderSoon), on('project', this.renderSoon)];
    this.render();
  }

  render() {
    const p = state.project;
    if (!p) return;
    const media = state.media;
    const s = p.stats || {};
    const tasks = projectTasks();
    const moreBtn = h('button.icon-btn.owner-only', { title: 'Project settings', 'aria-label': 'Project settings', onclick: () => this.projectMenu(moreBtn) }, ico('more', 18));
    const addBtn = h('button.btn.owner-only', { onclick: () => menu(addBtn, addDataMenuItems(p.id)) }, ico('plus', 15), 'Add data');
    const head = h('div.dash-head',
      h('div', h('h1', p.name), h('p', p.description || 'No description')),
      h('div.row', addBtn,
        h('button.btn.primary.owner-only', { onclick: () => openRunDialog(), disabled: !media.length }, ico('sparkles', 15), 'Run tasks…'),
        h('button.btn', { onclick: () => go(links.tab(p.id, 'stats')) }, ico('chart', 15), 'Statistics'),
        moreBtn));
    const sh = shareInfo();
    const banner = sh ? h('div.notice.share-note', ico(sh.access === 'edit' ? 'edit' : 'eye', 16), h('div',
      h('b', sh.access === 'edit' ? 'You can review and correct the labels in this project.' : 'This is a view-only copy of the project.'),
      ' ', sh.access === 'edit' ? 'Open a file, fix what is wrong and mark it reviewed; your changes are saved for the owner with your link name.'
        : 'Open any file to listen, look at the labels and the statistics. Changes are not saved.',
      sh.note ? h('div.muted', { style: { marginTop: '4px' } }, sh.note) : null)) : null;

    if (!media.length && isShared()) {
      this.inner.replaceChildren(head, banner, h('div.empty', 'This project has no files yet.'));
      return;
    }
    if (!media.length) {
      this.inner.replaceChildren(head, h('div.big-drop',
        ico('upload', 34),
        h('h3', 'Add audio, video and images'),
        h('p', 'Drop files or whole folders anywhere here. Mixed folders are fine: each file is detected as audio, video or image automatically.'),
        h('div.row', { style: { marginTop: '8px' } },
          h('button.btn.primary', { onclick: () => pickAndUpload(p.id) }, ico('upload', 15), 'Upload files'),
          h('button.btn', { onclick: () => pickAndUpload(p.id, { folder: true }) }, ico('folder', 15), 'Upload a folder'),
          h('button.btn', { onclick: () => openLinkDialog(p.id) }, ico('link', 15), 'Link local folder'))),
      this.tasksSection(tasks));
      return;
    }

    const by = s.by_modality || {};
    const pctA = s.files ? Math.round((s.annotated / s.files) * 100) : 0;
    const pctR = s.files ? Math.round((s.reviewed / s.files) * 100) : 0;
    const stats = h('div.stat-row',
      stat('database', 'Files', s.files, null, h('div.sub',
        h('span', ico('audio', 12), ' ', by.audio || 0), h('span', ico('film', 12), ' ', by.video || 0), h('span', ico('image', 12), ' ', by.image || 0))),
      stat('audio', 'Total duration', fmtDur(s.duration) || '–', null, h('div.sub', `${s.segments || 0} segments`)),
      stat('sparkles', 'Annotated', s.annotated, `/ ${s.files}`, h('div.progress', h('i', { style: { width: pctA + '%' } }))),
      stat('check', 'Reviewed', s.reviewed, `/ ${s.files}`, h('div.progress', h('i', { style: { width: pctR + '%', background: 'var(--ok)' } }))),
      Object.assign(stat('flag', 'Low confidence', s.low || 0, 'segments', h('div.sub', `below ${p.settings?.low_confidence ?? 0.6}`)),
        { className: 'stat link', onclick: () => go(links.tab(p.id, 'review')) }));

    this.inner.replaceChildren(head, banner, stats, this.tasksSection(tasks), this.coverage(tasks, media));
  }

  tasksSection(tasks) {
    const p = state.project;
    return h('div.section',
      h('div.section-head', h('h2', 'Tasks in this project'),
        h('button.btn.sm.owner-only', { onclick: () => go(links.tab(p.id, 'tasks')) }, ico('edit', 13), tasks.length ? 'Edit tasks' : 'Choose tasks')),
      h('div.section-body', tasks.length
        ? h('div.dash-tasks', tasks.map(taskChip))
        : h('div.muted', 'No tasks yet. Pick tasks from the library or write your own prompt task.')));
  }

  coverage(tasks, media) {
    const p = state.project;
    const cols = tasks.filter(t => media.some(m => t.modalities.includes(m.modality)));
    const LIMIT = 400;
    const rows = media.slice().sort((a, b) => (a.rel_path || '').localeCompare(b.rel_path || '', undefined, { numeric: true })).slice(0, LIMIT);
    // review state in a fixed slot after the number, so the numbers stay right-aligned
    const mark = (d) => d.reviewed >= d.n ? h('span.cv-m.rev', ico('check', 12))
      : d.reviewed ? h('span.cv-m.part', { style: { '--p': Math.min(85, Math.max(15, Math.round((d.reviewed / d.n) * 100))) + '%' } })
        : h('span.cv-m');
    const table = h('table.cov',
      h('thead', h('tr', h('th', 'File'), h('th', 'Status'),
        cols.map(t => h('th.n', { title: t.title }, h('span.cov-th', h('i.dot', { style: { '--c': t.color } }), t.name))))),
      h('tbody', rows.map(m => {
        const tiers = m.summary?.tiers || {};
        const st = m.status || 'new';
        return h('tr', { onclick: () => go(links.file(p.id, m.id)) },
          h('td', { title: m.rel_path }, h('span.cov-file', ico(modalityIcon(m.modality), 13), h('span', m.name))),
          h('td', h('span.cov-status', h('i.st.' + st), STATUS[st])),
          cols.map(t => {
            if (!t.modalities.includes(m.modality)) return h('td.n', { title: `${t.name} does not apply to ${m.modality} files` });
            const d = tiers[t.id];
            if (!d || !d.n) return h('td.n', { title: `${t.name}: not run` }, h('span.cv', h('span.cv-none', '–'), h('span.cv-m')));
            return h('td.n', { title: `${d.n} segments · ${d.reviewed} reviewed · ${d.low} low confidence · source: ${d.source || '?'}` },
              h('span.cv', d.low ? h('i.cv-low') : null, h('span.cv-n', d.n), mark(d)));
          }));
      })));
    return h('div.section',
      h('div.section-head', h('h2', 'Coverage'),
        h('div.legend',
          h('span', h('span.lg-n', 'n'), 'segments'),
          h('span', h('span.cv-m.rev', ico('check', 12)), 'reviewed'),
          h('span', h('span.cv-m.part', { style: { '--p': '50%' } }), 'partly reviewed'),
          h('span', h('i.cv-low'), 'has low confidence'),
          h('span', h('span.cv-none', '–'), 'not run'))),
      cols.length ? h('div.cov-wrap', { tabindex: 0, 'aria-label': 'Coverage per file' }, table) : h('div.section-body.muted', 'Select tasks to see per-file coverage.'),
      media.length > LIMIT ? h('div.section-body.muted', `Showing the first ${LIMIT} of ${media.length} files.`) : null);
  }

  projectMenu(anchor) {
    const p = state.project;
    menu(anchor, [
      { label: 'Rename project', icon: 'edit', onClick: async () => {
        const name = await promptDialog('Project name', { value: p.name, title: 'Rename project' });
        if (name) { await api.patchProject(p.id, { name }).catch(errorToast); refreshProject(); }
      } },
      { label: 'Edit description', icon: 'edit', onClick: async () => {
        const d = await promptDialog('Description', { value: p.description || '', title: 'Project description' });
        if (d != null) { await api.patchProject(p.id, { description: d }).catch(errorToast); refreshProject(); }
      } },
      'sep',
      { label: 'Delete project', icon: 'trash', danger: true, onClick: async () => {
        const ok = await confirmDialog(`Delete "${p.name}" with all its annotations? Linked files are not touched.`, { title: 'Delete project', ok: 'Delete project' });
        if (!ok) return;
        await api.deleteProject(p.id).catch(errorToast);
        toast('Project deleted', { kind: 'ok' });
        go('#/');
      } },
    ], { placement: 'bottom-end' });
  }

  destroy() { this.unsub.forEach(f => f()); }
}

function stat(icon, label, val, small, extra) {
  return h('div.stat', h('div.lab', ico(icon, 13), label), h('div.val', val ?? '–', small ? h('small', small) : null), extra);
}
