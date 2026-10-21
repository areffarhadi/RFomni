// Projects overview.
import { api } from '../api.js';
import { h, ico, fmtDur } from '../util.js';
import { state } from '../state.js';
import { go, links } from '../nav.js';
import { openModal, menu, confirmDialog, promptDialog, errorToast, toast } from '../ui.js';

export function ago(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

export function taskChip(t) {
  return h('span.tchip', { style: { '--c': t.color }, title: t.title }, t.name);
}

export class HomeView {
  constructor() {
    this.grid = h('div.proj-grid');
    this.el = h('div.home', h('div.home-inner',
      h('div.home-head',
        h('div', h('h1', 'Projects'),
          h('p', 'A project is a folder of audio, video and images plus the annotation tasks you want to apply to all of it. ' +
            'Run the tasks, then review and correct every file in the tier editor.')),
        h('button.btn.primary', { onclick: () => newProjectDialog() }, ico('plus', 15), 'New project')),
      this.grid));
    this.load();
  }

  async load() {
    let projects = [];
    try { projects = await api.projects(); } catch (e) {
      errorToast(e);
      this.grid.replaceChildren(h('div.empty.home-empty', ico('alert', 24), h('h3', 'Could not load projects'),
        h('p', e.message || 'The server did not respond.'),
        h('button.btn', { onclick: () => this.load() }, 'Try again')));
      return;
    }
    if (!projects.length) {
      this.grid.replaceChildren(h('div.empty.home-empty', ico('folder', 28), h('h3', 'No projects yet'),
        h('p', 'Create a project, add audio, video or images to it, and choose the annotation tasks to run.'),
        h('button.btn.primary', { onclick: () => newProjectDialog() }, ico('plus', 15), 'New project')));
      return;
    }
    const cards = projects.map(p => this.card(p));
    cards.push(h('button.proj-card.new', { type: 'button', onclick: () => newProjectDialog() }, ico('folder-plus', 20), h('span', 'New project')));
    this.grid.replaceChildren(...cards);
  }

  card(p) {
    const s = p.stats || {};
    const tasks = (p.tasks || []).map(id => state.taskById.get(id)).filter(Boolean);
    const ann = s.files ? (s.annotated / s.files) * 100 : 0;
    const rev = s.files ? (s.reviewed / s.files) * 100 : 0;
    const more = h('button.icon-btn.sm.card-menu', {
      title: 'More', 'aria-label': `More actions for ${p.name}`,
      onclick: (e) => {
        e.stopPropagation();
        menu(more, [
          { label: 'Rename', icon: 'edit', onClick: () => this.rename(p) },
          'sep',
          { label: 'Delete project', icon: 'trash', danger: true, onClick: () => this.remove(p) },
        ]);
      },
    }, ico('more', 16));
    const by = s.by_modality || {};
    const count = (icon, n, one, many) => h('span', { title: `${n} ${n === 1 ? one : many}` }, ico(icon, 13), n);
    const open = () => go(links.project(p.id));
    const hidden = tasks.slice(7);
    return h('div.proj-card', { onclick: open },
      more,
      h('div', h('h3', { title: p.name }, h('a', { href: links.project(p.id), onclick: (e) => e.stopPropagation() }, p.name)), p.description ? h('div.desc', p.description) : null),
      h('div.stats', s.files
        ? [count('audio', by.audio ?? 0, 'audio file', 'audio files'), count('film', by.video ?? 0, 'video', 'videos'),
          count('image', by.image ?? 0, 'image', 'images'),
          s.duration ? h('span', { title: 'Total duration' }, ico('clock', 13), fmtDur(s.duration)) : null]
        : h('span.muted', 'No files yet')),
      tasks.length ? h('div.tasks', tasks.slice(0, 7).map(taskChip),
        hidden.length ? h('span.proj-more', { title: hidden.map(t => t.name).join(', ') }, `+${hidden.length}`) : null)
        : h('div.no-tasks', 'No tasks selected yet'),
      h('div.foot',
        h('div.progress.multi', h('i', { style: { width: rev + '%', background: 'var(--ok)' } }),
          h('i', { style: { width: Math.max(0, ann - rev) + '%' } })),
        h('div.row', h('span', `${s.annotated || 0}/${s.files || 0} annotated · ${s.reviewed || 0} reviewed`), h('span', ago(p.updated_at)))));
  }

  async rename(p) {
    const name = await promptDialog('Project name', { value: p.name, title: 'Rename project' });
    if (!name) return;
    await api.patchProject(p.id, { name }).catch(errorToast);
    this.load();
  }

  async remove(p) {
    const ok = await confirmDialog(
      `Delete "${p.name}"? Uploaded copies and all annotations in this project are deleted. Files you linked from other folders are not touched.`,
      { title: 'Delete project', ok: 'Delete project' });
    if (!ok) return;
    await api.deleteProject(p.id).catch(errorToast);
    toast('Project deleted', { kind: 'ok' });
    this.load();
  }

  destroy() {}
}

export function newProjectDialog() {
  let preset = state.presets[0]?.id || null;
  const name = h('input.input', { placeholder: 'e.g. Parliament debates 2026', autofocus: true });
  const desc = h('textarea.textarea', { rows: 2, placeholder: 'Optional notes about the data or the annotation guidelines' });
  const presetEls = [];
  const opts = [...state.presets, { id: null, name: 'Choose later', tasks: [] }];
  const pick = (el, id) => {
    preset = id;
    presetEls.forEach(x => { x.classList.toggle('on', x === el); x.setAttribute('aria-checked', String(x === el)); });
  };
  const grid = h('div.preset-grid', { role: 'radiogroup', 'aria-label': 'Start with tasks' }, opts.map(p => {
    const el = h('div.preset' + (p.id === preset ? '.on' : ''), {
      role: 'radio', tabindex: 0, 'aria-checked': String(p.id === preset),
      onclick: () => pick(el, p.id),
      onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(el, p.id); } },
    }, h('b', p.name), h('div.tasks', p.tasks.length
      ? p.tasks.map(id => state.taskById.get(id)).filter(Boolean).map(taskChip)
      : h('span.muted', 'Pick tasks from the library after creating the project')));
    presetEls.push(el);
    return el;
  }));
  const create = async (m) => {
    const tasks = opts.find(p => p.id === preset)?.tasks || [];
    try {
      const p = await api.createProject({ name: name.value.trim() || 'Untitled project', description: desc.value.trim(), tasks });
      m.close();
      go(links.project(p.id));
    } catch (e) { errorToast(e); }
  };
  const m = openModal({
    title: 'New project', subtitle: 'You can add data and change tasks at any time.', width: 620,
    body: h('div',
      h('label.field', h('span', 'Name'), name),
      h('label.field', h('span', 'Description'), desc),
      h('h4', 'Start with tasks'), grid),
    actions: [{ label: 'Cancel', ghost: true, onClick: (a) => a.close() }, { label: 'Create project', primary: true, onClick: create }],
  });
  name.addEventListener('keydown', (e) => { if (e.key === 'Enter') create(m); });
}
