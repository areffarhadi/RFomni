// Tasks tab: the project's task pipeline + the task library.
import { h, ico, modalityIcon } from '../util.js';
import { state, on, updateProject } from '../state.js';
import { errorToast } from '../ui.js';
import { openTaskEditor, openTaskDetail, OUTPUTS, GRANS } from './task_editor.js';
import { openRunDialog } from './run.js';
import { mergeOrder } from '../editor/order.js';

export class TasksView {
  constructor(pv) {
    this.pv = pv;
    this.q = '';
    this.kind = 'all';
    this.mod = 'all';
    this.pipe = h('div.pipeline');
    this.lib = h('div');
    const search = h('input.input', { placeholder: 'Search tasks', oninput: (e) => { this.q = e.target.value.toLowerCase(); this.renderLib(); } });
    this.kindCtl = h('div.seg-ctl', [['all', 'All'], ['annotation', 'Annotation'], ['captioning', 'Captioning']].map(([k, v]) =>
      h('button', { dataset: { v: k }, onclick: () => { this.kind = k; this.renderLib(); } }, v)));
    this.modCtl = h('div.seg-ctl', [['all', 'Any media'], ['audio', 'Audio'], ['video', 'Video'], ['image', 'Image']].map(([k, v]) =>
      h('button', { dataset: { v: k }, onclick: () => { this.mod = k; this.renderLib(); } }, k !== 'all' ? ico(modalityIcon(k), 13) : null, v)));
    this.el = h('div.page', h('div.page-inner',
      h('div.page-head',
        h('div', h('h1', 'Tasks'),
          h('p', 'Choose what should be annotated in this project. Every task becomes one tier (channel) in the editor, ' +
            'in the order below. Write your own task as a prompt when the library does not have it.')),
        h('div.row',
          h('button.btn', { onclick: () => openRunDialog() }, ico('sparkles', 15), 'Run tasks…'),
          h('button.btn.primary', { onclick: () => openTaskEditor({ onSaved: () => this.render() }) }, ico('prompt', 15), 'New prompt task'))),
      h('div.section',
        h('div.section-head', h('h3', ico('layers', 15), 'Tiers in this project', h('span.badge', { dataset: { n: '' } })),
          h('span.muted', { style: { fontSize: '12px' } }, 'Drag to reorder · click × to remove')),
        h('div.section-body', this.pipe)),
      h('div.tv-filters', h('div.fp-search', ico('search', 14), search), this.kindCtl, this.modCtl),
      this.lib));
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
    // add parents automatically (alignment needs ASR, phones need alignment)
    const chain = [];
    let p = t?.parent;
    while (p && !ids.includes(p) && !chain.includes(p)) { chain.unshift(p); p = state.taskById.get(p)?.parent; }
    return this.setTasks([...ids, ...chain, id]);
  }

  render() {
    this.renderPipe();
    this.renderLib();
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
      t.parent ? h('span.child', `↳ ${state.taskById.get(t.parent)?.name || ''}`) : null,
      h('button.icon-btn.sm', { title: 'Remove', onclick: () => this.toggle(id) }, ico('x', 12)));
      return chip;
    }));
  }

  renderLib() {
    for (const b of this.kindCtl.children) b.classList.toggle('on', b.dataset.v === this.kind);
    for (const b of this.modCtl.children) b.classList.toggle('on', b.dataset.v === this.mod);
    const sel = new Set(this.selected);
    const match = (t) => (this.kind === 'all' || t.kind === this.kind) && (this.mod === 'all' || t.modalities.includes(this.mod)) &&
      (!this.q || `${t.name} ${t.title} ${t.description} ${t.id}`.toLowerCase().includes(this.q));
    const out = [];
    for (const cat of state.categories) {
      const tasks = state.tasks.filter(t => (t.builtin ? t.category : 'custom') === cat.id && match(t));
      if (cat.id === 'custom' && !tasks.length && !this.q) {
        out.push(h('div.cat-title', h('h3', cat.name)),
          h('div.task-grid', h('div.task-card', { style: { '--c': 'var(--accent)', borderStyle: 'dashed' }, onclick: () => openTaskEditor({ onSaved: () => this.render() }) },
            h('div.tc-top', ico('prompt', 18), h('div', h('div.tc-name', 'Write a prompt task'), h('div.tc-title', 'Any annotation or captioning task, described in plain language'))))));
        continue;
      }
      if (!tasks.length) continue;
      out.push(h('div.cat-title', h('h3', cat.name), h('span', `${tasks.filter(t => sel.has(t.id)).length} of ${tasks.length} selected`)),
        h('div.task-grid', tasks.map(t => this.card(t, sel.has(t.id)))));
    }
    if (!out.length) out.push(h('div.empty', 'No tasks match.'));
    this.lib.replaceChildren(...out);
  }

  card(t, on) {
    const sw = h('label.switch', { onclick: (e) => e.stopPropagation() },
      h('input', { type: 'checkbox', checked: on, onchange: () => this.toggle(t.id) }), h('span'));
    return h('div.task-card' + (on ? '.on' : ''), { style: { '--c': t.color }, onclick: () => openTaskDetail(t, { onChange: () => this.render() }) },
      h('div.tc-top', h('div.grow', h('div.tc-name', t.name), h('div.tc-title', t.title)), sw),
      h('div.tc-desc', t.description || t.prompt),
      h('div.tc-badges',
        h('span.badge', OUTPUTS[t.output]),
        h('span.badge', GRANS[t.granularity]),
        t.kind === 'captioning' ? h('span.badge.accent', 'caption') : null,
        !t.builtin ? h('span.badge.warn', 'my task') : null,
        h('span.tc-mods', ['audio', 'video', 'image'].map(m => ico(modalityIcon(m), 13, t.modalities.includes(m) ? '' : 'dim')))));
  }

  destroy() { this.unsub.forEach(f => f()); }
}
