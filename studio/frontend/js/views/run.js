// "Run tasks" dialog: choose tasks, files and how to treat existing annotations.
import { api } from '../api.js';
import { h, ico, modalityIcon } from '../util.js';
import { state, projectTasks, trackJob, activeJob } from '../state.js';
import { go, links } from '../nav.js';
import { openModal, toast, errorToast } from '../ui.js';

const GRAN = { file: 'whole file', segment: 'per segment', window: 'windows', event: 'events', word: 'words', phone: 'phones' };

export function openRunDialog({ mediaIds = null, taskIds = null } = {}) {
  const pid = state.project.id;
  if (activeJob()) {
    toast('A run is already in progress for this project', { kind: 'warn' });
    return;
  }
  const ptasks = projectTasks();
  if (!ptasks.length && !taskIds) {
    openModal({
      title: 'No tasks selected', width: 440,
      body: h('p.muted-p', 'Choose which tasks this project should run first. Each task becomes a tier in the editor.'),
      actions: [{ label: 'Cancel', ghost: true, onClick: (a) => a.close() },
        { label: 'Choose tasks', primary: true, onClick: (a) => { a.close(); go(links.tab(pid, 'tasks')); } }],
    });
    return;
  }
  const media = state.media;
  const chosen = new Set(taskIds || ptasks.map(t => t.id));
  const pool = taskIds ? [...new Set([...ptasks.map(t => t.id), ...taskIds])].map(id => state.taskById.get(id)).filter(Boolean) : ptasks;
  const mods = new Set(media.map(m => m.modality));

  const taskList = h('div.task-pick', pool.map(t => {
    const applies = t.modalities.some(m => mods.has(m));
    const cb = h('input', { type: 'checkbox', checked: chosen.has(t.id), onchange: () => { cb.checked ? chosen.add(t.id) : chosen.delete(t.id); refresh(); } });
    return h('label' + (applies ? '' : '.na'), { title: t.title + (applies ? '' : ' (no matching files)') },
      cb, h('i.sw', { style: { '--c': t.color } }), h('span', t.name),
      h('small', t.modalities.map(m => ico(modalityIcon(m), 11)), ' ', GRAN[t.granularity] || ''));
  }));

  const unannotated = media.filter(m => (m.status || 'new') === 'new').map(m => m.id);
  const scopes = [];
  if (mediaIds?.length) scopes.push({ id: 'sel', label: mediaIds.length === 1 ? 'This file' : `Selected files (${mediaIds.length})`, ids: mediaIds });
  scopes.push({ id: 'all', label: `All files (${media.length})`, ids: null });
  if (unannotated.length && unannotated.length !== media.length) scopes.push({ id: 'new', label: `Files without annotations (${unannotated.length})`, ids: unannotated });
  let scope = scopes[0];
  const scopeEls = h('div.radio-list', scopes.map(s => radio('scope', s.id === scope.id, h('b', s.label), null, () => { scope = s; refresh(); })));

  const modes = state.info?.run_modes || { missing: 'Only fill tiers that are still empty' };
  let mode = 'missing';
  const modeLabels = { missing: 'Fill empty tiers', unreviewed: 'Re-run unreviewed tiers', overwrite: 'Replace everything' };
  const modeEls = h('div.radio-list', Object.entries(modes).map(([k, desc]) =>
    radio('mode', k === mode, h('b', modeLabels[k] || k), desc, () => { mode = k; refresh(); })));

  const engine = state.info?.engine;
  const notice = engine && !engine.connected ? h('div.notice', ico('alert', 16), h('div',
    h('b', 'No model connected. '),
    'The mock engine finds speech regions in the audio and fills every tier with placeholder values ' +
    '(marked "mock", with random confidence) so you can try the editing workflow. Real annotations will come from the model once it is connected.')) : null;

  const m = openModal({
    title: 'Run tasks', width: 640, className: 'run-dlg',
    subtitle: 'Each selected task is applied to every matching file and written to its own tier.',
    body: h('div', notice && h('div', { style: { marginBottom: '16px' } }, notice),
      h('h3', 'Tasks'), taskList, h('h3', 'Files'), scopeEls, h('h3', 'Existing annotations'), modeEls),
    actions: [
      { label: 'Cancel', ghost: true, onClick: (a) => a.close() },
      { id: 'run', label: 'Run', icon: 'sparkles', primary: true, onClick: start },
    ],
  });

  function count() {
    const ids = scope.ids ? new Set(scope.ids) : null;
    return media.filter(x => !ids || ids.has(x.id)).length;
  }
  function refresh() {
    for (const el of m.el.querySelectorAll('.radio-list label')) el.classList.toggle('on', el.querySelector('input').checked);
    const b = m.button('run');
    const n = count();
    b.disabled = !chosen.size || !n;
    b.lastChild.textContent = `Run ${chosen.size} task${chosen.size === 1 ? '' : 's'} on ${n} file${n === 1 ? '' : 's'}`;
  }
  async function start(a) {
    try {
      const job = await api.run(pid, { task_ids: [...chosen], media_ids: scope.ids, mode });
      a.close();
      trackJob(job);
      toast(`Started: ${job.task_ids.length} task(s) on ${job.total} file(s)`, { kind: 'ok' });
    } catch (e) { errorToast(e); }
  }
  refresh();
}

function radio(name, checked, title, desc, onChange) {
  return h('label' + (checked ? '.on' : ''), h('input', { type: 'radio', name, checked, onchange: onChange }),
    h('div', title, desc ? h('span', desc) : null));
}
