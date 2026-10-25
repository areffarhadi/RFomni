// Tasks tab: the project's tiers as a numbered list in editor order, the default task library as a table
// (left) and, behind a vertical line, the tasks you created or changed (right) with Edit / Test / Reset / Delete.
import { h, ico, modalityIcon } from '../util.js';
import { state, on, updateProject } from '../state.js';
import { errorToast } from '../ui.js';
import { openTaskEditor, openTaskDetail, resetTask, deleteTask, OUTPUTS, GRANS, FIELD_NAMES } from './task_editor.js';
import { openTaskTest } from './task_test.js';
import { openRunDialog } from './run.js';
import { mergeOrder } from '../editor/order.js';

const changed = (t) => [...new Set((t.modified_fields || []).map(k => FIELD_NAMES[k] || k))].join(', ');
const classCount = (n) => `${n} class${n === 1 ? '' : 'es'}`;
const MODS = ['audio', 'video', 'image', 'text'];
/** The three media types as small icons; the ones the task does not apply to are dimmed. */
const mods = (t) => h('span.tc-mods', { title: `Applies to ${t.modalities.join(', ')}` },
  MODS.map(m => ico(modalityIcon(m), 13, t.modalities.includes(m) ? '' : 'dim')));

export class TasksView {
  constructor(pv) {
    this.pv = pv;
    this.q = '';
    this.kind = 'all';
    this.mod = 'all';
    this.pipe = h('div.pipeline');
    this.lib = h('div');
    this.mine = h('aside.tv-mine', { dataset: { guide: 'tasks.mine' } });
    const search = h('input.input', { placeholder: 'Search tasks', 'aria-label': 'Search tasks', oninput: (e) => { this.q = e.target.value.toLowerCase(); this.renderLib(); } });
    this.kindCtl = h('div.seg-ctl', [['all', 'All'], ['annotation', 'Annotation'], ['captioning', 'Captioning']].map(([k, v]) =>
      h('button', { dataset: { v: k }, onclick: () => { this.kind = k; this.renderLib(); } }, v)));
    this.modCtl = h('div.seg-ctl', [['all', 'Any media'], ['audio', 'Audio'], ['video', 'Video'], ['image', 'Image'], ['text', 'Text']].map(([k, v]) =>
      h('button', { dataset: { v: k }, onclick: () => { this.mod = k; this.renderLib(); } }, k !== 'all' ? ico(modalityIcon(k), 13) : null, v)));
    this.filters = h('div.tv-filters', { dataset: { guide: 'tasks.filters' } }, h('div.fp-search', ico('search', 14), search), this.kindCtl, this.modCtl);
    // the assistant can set the filters for the user (js/guide.js)
    this.filters.addEventListener('guide-filter', ({ detail: f }) => {
      this.mod = f.media || 'all';
      this.kind = f.kind || 'all';
      this.q = (f.search || '').toLowerCase();
      search.value = f.search || '';
      this.renderLib();
    });
    this.el = h('div.page', h('div.page-inner.wide',
      h('div.page-head',
        h('div', h('h1', 'Tasks'),
          h('p', 'Choose what should be annotated in this project. Every task becomes one tier (channel) in the editor, ' +
            'in the order below. Change the classes or the prompt of any task, write your own, and test it on a few files first.')),
        h('div.row',
          h('button.btn', { dataset: { guide: 'tasks.run' }, onclick: () => openRunDialog() }, ico('play', 15), 'Run tasks…'),
          h('button.btn.primary', { dataset: { guide: 'tasks.new' }, onclick: () => openTaskEditor({ onSaved: () => this.render() }) }, ico('prompt', 15), 'New prompt task'))),
      h('div.section', { dataset: { guide: 'tasks.selected' } },
        h('div.section-head', h('div.section-title', h('h2', 'Tiers in this project'), h('span.badge', { dataset: { n: '' } })),
          h('span.muted', { style: { fontSize: '12px' } }, 'In editor order. Drag a tier to move it.')),
        h('div.section-body', this.pipe)),
      h('div.tv-split',
        h('div.tv-left', { dataset: { guide: 'tasks.library' } },
          h('div.tv-col-head', h('h3', 'Task library'), h('span.muted', 'defaults')),
          this.filters,
          this.lib),
        h('div.tv-divider'),
        this.mine)));
    this.unsub = [on('tasks', () => this.render()), on('project', () => this.render())];
    this.render();
  }

  get selected() { return state.project?.tasks || []; }

  async setTasks(ids) {
    try { await updateProject({ tasks: ids, tier_order: mergeOrder(ids, state.project.tier_order || []) }); } catch (e) { errorToast(e); }
  }

  toggle(id) {
    const sel = this.selected;
    if (sel.includes(id)) return this.setTasks(sel.filter(x => x !== id));
    const t = state.taskById.get(id);
    const ids = [...sel];
    const chain = [];
    let p = t?.parent;
    while (p && !ids.includes(p) && !chain.includes(p)) { chain.unshift(p); p = state.taskById.get(p)?.parent; }
    return this.setTasks([...ids, ...chain, id]);
  }

  render() {
    this.renderPipe();
    this.renderLib();
    this.renderMine();
  }

  renderPipe() {
    const ids = this.selected.filter(id => state.taskById.has(id));
    this.el.querySelector('[data-n]').textContent = ids.length;
    if (!ids.length) {
      this.pipe.replaceChildren(h('span.muted', 'No tasks yet. Switch tasks on in the library below, or start from a preset: '),
        ...state.presets.map(p => h('span.chip', { onclick: () => this.setTasks(p.tasks) }, ico('plus', 12), p.name)));
      return;
    }
    let dragId = null;
    this.pipe.replaceChildren(h('ol.tier-order', ids.map((id, i) => {
      const t = state.taskById.get(id);
      const parent = t.parent ? state.taskById.get(t.parent)?.name || '' : '';
      const chip = h('li.tier-item', {
        draggable: true, style: { '--c': t.color }, title: t.title,
        ondragstart: (e) => { dragId = id; chip.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; },
        ondragend: () => chip.classList.remove('dragging'),
        ondragover: (e) => { e.preventDefault(); chip.classList.add('over'); },
        ondragleave: () => chip.classList.remove('over'),
        ondrop: (e) => {
          e.preventDefault();
          chip.classList.remove('over');
          if (!dragId || dragId === id) return;
          const arr = ids.filter(x => x !== dragId);
          arr.splice(arr.indexOf(id), 0, dragId);
          this.setTasks(arr);
        },
      }, h('span.grip', ico('grip', 13)), h('span.ti-n', String(i + 1)), h('span.sw'), h('span.ti-name', t.name),
      parent ? h('span.ti-parent', { title: `Runs on the output of ${parent}` }, `on ${parent}`) : null,
      t.modified || !t.builtin ? h('span.pipe-mark', { title: t.builtin ? `Changed: ${changed(t)}` : 'Your task' }, ico(t.builtin ? 'edit' : 'user', 12)) : null,
      h('button.icon-btn.sm', { title: 'Remove from this project', 'aria-label': `Remove ${t.name}`, onclick: () => this.toggle(id) }, ico('x', 12)));
      return chip;
    })));
  }

  /** Left: the default library (built-in tasks you have not changed). */
  renderLib() {
    for (const b of this.kindCtl.children) b.classList.toggle('on', b.dataset.v === this.kind);
    for (const b of this.modCtl.children) b.classList.toggle('on', b.dataset.v === this.mod);
    const sel = new Set(this.selected);
    const match = (t) => (this.kind === 'all' || t.kind === this.kind) && (this.mod === 'all' || t.modalities.includes(this.mod)) &&
      (!this.q || `${t.name} ${t.title} ${t.description} ${t.id}`.toLowerCase().includes(this.q));
    const groups = [];
    for (const cat of state.categories) {
      if (cat.id === 'custom') continue;
      const tasks = state.tasks.filter(t => t.builtin && !t.modified && t.category === cat.id && match(t));
      if (!tasks.length) continue;
      groups.push(h('tbody',
        h('tr.cat-row', h('th', { colspan: 6, scope: 'colgroup' },
          h('span.cat-name', cat.name), h('span.cat-n', `${tasks.filter(t => sel.has(t.id)).length} of ${tasks.length} in this project`))),
        tasks.map(t => this.row(t, sel.has(t.id)))));
    }
    if (!groups.length) { this.lib.replaceChildren(h('p.tv-none', 'No default task matches these filters.')); return; }
    this.lib.replaceChildren(h('table.task-table',
      h('thead', h('tr', h('th.c-on', h('span.sr-only', 'In this project')), h('th', 'Task'), h('th', 'What it writes'),
        h('th', 'Output'), h('th', 'Media'), h('th.c-act', h('span.sr-only', 'Actions')))),
      groups));
  }

  /** One library task as a table row; the name opens its details, the switch puts it in the project. */
  row(t, on) {
    const open = () => openTaskDetail(t, { onChange: () => this.render() });
    const sw = h('label.switch', { onclick: (e) => e.stopPropagation(), title: on ? 'In this project' : 'Add to this project' },
      h('input', { type: 'checkbox', checked: on, 'aria-label': `${t.name} in this project`, onchange: () => this.toggle(t.id) }), h('span'));
    const per = [GRANS[t.granularity], t.labels?.length && t.output !== 'keypoints' ? classCount(t.labels.length) : null].filter(Boolean).join(', ');
    return h('tr.task-row' + (on ? '.on' : ''), { dataset: { guide: `task:${t.id}` }, style: { '--c': t.color }, onclick: open },
      h('td.c-on', sw),
      h('td.c-task', h('button.tt-name', { type: 'button', onclick: (e) => { e.stopPropagation(); open(); } }, h('span.sw'), t.name),
        h('div.tt-title', t.title)),
      h('td.c-desc', h('div.tt-desc', t.description || t.prompt)),
      h('td.c-out', OUTPUTS[t.output], h('div.tt-per', per)),
      h('td.c-media', mods(t)),
      h('td.c-act', h('div.tc-actions',
        h('button.btn.sm.ghost', { title: 'Change classes or prompt', onclick: (e) => { e.stopPropagation(); openTaskEditor({ base: t, edit: true, onSaved: () => this.render() }); } }, ico('edit', 12), 'Edit'),
        state.project ? h('button.btn.sm.ghost', { title: 'Try it on a few files', onclick: (e) => { e.stopPropagation(); openTaskTest({ task: t }); } }, ico('play', 12), 'Test') : null)));
  }

  /** Right: tasks you created and built-in tasks you changed. */
  renderMine() {
    const sel = new Set(this.selected);
    const created = state.tasks.filter(t => !t.builtin);
    const modified = state.tasks.filter(t => t.builtin && t.modified);
    const total = created.length + modified.length;
    const group = (title, hint, tasks) => tasks.length ? [h('div.mine-group', h('b', title), h('span.muted', hint)), tasks.map(t => this.mineCard(t, sel.has(t.id)))] : null;
    this.mine.replaceChildren(
      h('div.tv-col-head', h('h3', 'Changed & new tasks'), h('span.badge', total)),
      total ? null : h('div.mine-empty',
        h('p', 'Tasks you create or change are listed here, so you can see what differs from the defaults. ' +
          'Edit a task in the library to change its classes or prompt; you can reset it any time.'),
        h('button.btn.sm', { onclick: () => openTaskEditor({ onSaved: () => this.render() }) }, ico('prompt', 13), 'New prompt task')),
      group('New', 'created by you', created),
      group('Changed defaults', 'reset brings back the original', modified));
  }

  mineCard(t, on) {
    const sw = h('label.switch', { onclick: (e) => e.stopPropagation(), title: on ? 'In this project' : 'Add to this project' },
      h('input', { type: 'checkbox', checked: on, 'aria-label': `${t.name} in this project`, onchange: () => this.toggle(t.id) }), h('span'));
    const d = t.default || {};
    const diff = [];
    if (t.modified && t.modified_fields?.includes('labels')) diff.push(`classes ${(d.labels || []).length} → ${(t.labels || []).length}`);
    if (t.modified && t.modified_fields?.includes('prompt')) diff.push('prompt edited');
    const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
    return h('div.mine-card' + (on ? '.on' : ''), { style: { '--c': t.color }, onclick: () => openTaskDetail(t, { onChange: () => this.render() }) },
      h('div.tc-top', h('div.grow', h('div.tc-name', h('span.sw'), t.name, t.builtin ? h('span.badge.warn', 'changed') : h('span.badge.accent', 'new')),
        h('div.tc-title', t.title)), sw),
      h('div.mine-meta', [OUTPUTS[t.output], GRANS[t.granularity], t.labels?.length && t.output !== 'keypoints' ? classCount(t.labels.length) : null].filter(Boolean).join(', '),
        mods(t)),
      diff.length ? h('div.mine-diff', diff.join(', ')) : (t.builtin ? h('div.mine-diff', `Changed: ${changed(t)}`) : null),
      h('div.tc-actions',
        h('button.btn.sm', { onclick: stop(() => openTaskEditor({ base: t, edit: true, onSaved: () => this.render() })) }, ico('edit', 12), 'Edit'),
        state.project ? h('button.btn.sm', { onclick: stop(() => openTaskTest({ task: t })) }, ico('play', 12), 'Test') : null,
        h('div.grow'),
        t.builtin
          ? h('button.btn.sm.ghost', { title: 'Back to the default classes and prompt', onclick: stop(() => resetTask(t, () => this.render())) }, ico('undo', 12), 'Reset')
          : h('button.btn.sm.ghost', { title: 'Delete this task', onclick: stop(() => deleteTask(t, () => this.render())) }, ico('trash', 12), 'Delete')));
  }

  destroy() { this.unsub.forEach(f => f()); }
}
