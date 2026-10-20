// Tasks tab: the project's tiers, the default task library (left) and, behind a vertical line,
// the tasks you created or changed (right) with Edit / Test / Reset / Delete.
import { h, ico, modalityIcon } from '../util.js';
import { state, on, updateProject } from '../state.js';
import { errorToast } from '../ui.js';
import { openTaskEditor, openTaskDetail, resetTask, deleteTask, OUTPUTS, GRANS, FIELD_NAMES } from './task_editor.js';
import { openTaskTest } from './task_test.js';
import { openRunDialog } from './run.js';
import { mergeOrder } from '../editor/order.js';

const changed = (t) => [...new Set((t.modified_fields || []).map(k => FIELD_NAMES[k] || k))].join(' · ');

export class TasksView {
  constructor(pv) {
    this.pv = pv;
    this.q = '';
    this.kind = 'all';
    this.mod = 'all';
    this.pipe = h('div.pipeline');
    this.lib = h('div');
    this.mine = h('aside.tv-mine');
    const search = h('input.input', { placeholder: 'Search tasks', oninput: (e) => { this.q = e.target.value.toLowerCase(); this.renderLib(); } });
    this.kindCtl = h('div.seg-ctl', [['all', 'All'], ['annotation', 'Annotation'], ['captioning', 'Captioning']].map(([k, v]) =>
      h('button', { dataset: { v: k }, onclick: () => { this.kind = k; this.renderLib(); } }, v)));
    this.modCtl = h('div.seg-ctl', [['all', 'Any media'], ['audio', 'Audio'], ['video', 'Video'], ['image', 'Image']].map(([k, v]) =>
      h('button', { dataset: { v: k }, onclick: () => { this.mod = k; this.renderLib(); } }, k !== 'all' ? ico(modalityIcon(k), 13) : null, v)));
    this.el = h('div.page', h('div.page-inner.wide',
      h('div.page-head',
        h('div', h('h1', 'Tasks'),
          h('p', 'Choose what should be annotated in this project. Every task becomes one tier (channel) in the editor, ' +
            'in the order below. Change the classes or the prompt of any task, write your own, and test it on a few files first.')),
        h('div.row',
          h('button.btn', { onclick: () => openRunDialog() }, ico('sparkles', 15), 'Run tasks…'),
          h('button.btn.primary', { onclick: () => openTaskEditor({ onSaved: () => this.render() }) }, ico('prompt', 15), 'New prompt task'))),
      h('div.section',
        h('div.section-head', h('h3', ico('layers', 15), 'Tiers in this project', h('span.badge', { dataset: { n: '' } })),
          h('span.muted', { style: { fontSize: '12px' } }, 'Drag to reorder · click × to remove')),
        h('div.section-body', this.pipe)),
      h('div.tv-split',
        h('div.tv-left',
          h('div.tv-filters', h('div.fp-search', ico('search', 14), search), this.kindCtl, this.modCtl),
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
    this.pipe.replaceChildren(...ids.map(id => {
      const t = state.taskById.get(id);
      const chip = h('div.pipe-chip', {
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
      }, h('span.grip', ico('grip', 13)), h('span.sw'), t.name,
      t.modified || !t.builtin ? h('span.pipe-mark', { title: t.builtin ? `Changed: ${changed(t)}` : 'Your task' }, t.builtin ? '✎' : '★') : null,
      t.parent ? h('span.child', `↳ ${state.taskById.get(t.parent)?.name || ''}`) : null,
      h('button.icon-btn.sm', { title: 'Remove', onclick: () => this.toggle(id) }, ico('x', 12)));
      return chip;
    }));
  }

  /** Left: the default library (built-in tasks you have not changed). */
  renderLib() {
    for (const b of this.kindCtl.children) b.classList.toggle('on', b.dataset.v === this.kind);
    for (const b of this.modCtl.children) b.classList.toggle('on', b.dataset.v === this.mod);
    const sel = new Set(this.selected);
    const match = (t) => (this.kind === 'all' || t.kind === this.kind) && (this.mod === 'all' || t.modalities.includes(this.mod)) &&
      (!this.q || `${t.name} ${t.title} ${t.description} ${t.id}`.toLowerCase().includes(this.q));
    const out = [];
    for (const cat of state.categories) {
      if (cat.id === 'custom') continue;
      const tasks = state.tasks.filter(t => t.builtin && !t.modified && t.category === cat.id && match(t));
      if (!tasks.length) continue;
      out.push(h('div.cat-title', h('h3', cat.name), h('span', `${tasks.filter(t => sel.has(t.id)).length} of ${tasks.length} selected`)),
        h('div.task-grid', tasks.map(t => this.card(t, sel.has(t.id)))));
    }
    if (!out.length) out.push(h('div.empty', 'No default tasks match.'));
    this.lib.replaceChildren(h('div.tv-col-head', h('h3', 'Task library'), h('span.muted', 'defaults')), ...out);
  }

  card(t, on) {
    const sw = h('label.switch', { onclick: (e) => e.stopPropagation(), title: on ? 'In this project' : 'Add to this project' },
      h('input', { type: 'checkbox', checked: on, onchange: () => this.toggle(t.id) }), h('span'));
    return h('div.task-card' + (on ? '.on' : ''), { style: { '--c': t.color }, onclick: () => openTaskDetail(t, { onChange: () => this.render() }) },
      h('div.tc-top', h('div.grow', h('div.tc-name', t.name), h('div.tc-title', t.title)), sw),
      h('div.tc-desc', t.description || t.prompt),
      h('div.tc-badges',
        h('span.badge', OUTPUTS[t.output]),
        h('span.badge', GRANS[t.granularity]),
        t.labels?.length && t.output !== 'keypoints' ? h('span.badge', `${t.labels.length} classes`) : null,
        t.kind === 'captioning' ? h('span.badge.accent', 'caption') : null,
        h('span.tc-mods', ['audio', 'video', 'image'].map(m => ico(modalityIcon(m), 13, t.modalities.includes(m) ? '' : 'dim')))),
      h('div.tc-actions',
        h('button.btn.sm.ghost', { title: 'Change classes or prompt', onclick: (e) => { e.stopPropagation(); openTaskEditor({ base: t, edit: true, onSaved: () => this.render() }); } }, ico('edit', 12), 'Edit'),
        state.project ? h('button.btn.sm.ghost', { title: 'Try it on a few files', onclick: (e) => { e.stopPropagation(); openTaskTest({ task: t }); } }, ico('play', 12), 'Test') : null));
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
        ico('edit', 22),
        h('div', 'Tasks you create or change show up here, so you always know what differs from the defaults.'),
        h('div.muted', 'Edit a task in the library to change its classes or prompt; you can reset it any time.'),
        h('button.btn.primary.sm', { onclick: () => openTaskEditor({ onSaved: () => this.render() }) }, ico('prompt', 13), 'New prompt task')),
      group('New', 'created by you', created),
      group('Changed defaults', 'reset brings back the original', modified));
  }

  mineCard(t, on) {
    const sw = h('label.switch', { onclick: (e) => e.stopPropagation(), title: on ? 'In this project' : 'Add to this project' },
      h('input', { type: 'checkbox', checked: on, onchange: () => this.toggle(t.id) }), h('span'));
    const d = t.default || {};
    const diff = [];
    if (t.modified && t.modified_fields?.includes('labels')) diff.push(`classes ${(d.labels || []).length} → ${(t.labels || []).length}`);
    if (t.modified && t.modified_fields?.includes('prompt')) diff.push('prompt edited');
    const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
    return h('div.mine-card' + (on ? '.on' : ''), { style: { '--c': t.color }, onclick: () => openTaskDetail(t, { onChange: () => this.render() }) },
      h('div.tc-top', h('div.grow', h('div.tc-name', t.name, ' ', t.builtin ? h('span.badge.warn', 'changed') : h('span.badge.accent', 'new')),
        h('div.tc-title', t.title)), sw),
      h('div.tc-badges', h('span.badge', OUTPUTS[t.output]), h('span.badge', GRANS[t.granularity]),
        t.labels?.length && t.output !== 'keypoints' ? h('span.badge', `${t.labels.length} classes`) : null,
        h('span.tc-mods', ['audio', 'video', 'image'].map(m => ico(modalityIcon(m), 13, t.modalities.includes(m) ? '' : 'dim')))),
      diff.length ? h('div.mine-diff', diff.join(' · ')) : (t.builtin ? h('div.mine-diff', `changed: ${changed(t)}`) : null),
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
