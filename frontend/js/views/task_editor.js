// Create / edit a prompt task, and view a task's details.
import { api } from '../api.js';
import { h, ico, debounce, modalityIcon, segColor } from '../util.js';
import { state, loadTasks, updateProject } from '../state.js';
import { openModal, confirmDialog, toast, errorToast } from '../ui.js';

const COLORS = ['#38bdf8', '#22d3ee', '#2dd4bf', '#34d399', '#a3e635', '#facc15', '#fb923c', '#f87171',
  '#f472b6', '#e879f9', '#a78bfa', '#818cf8', '#94a3b8'];

export const OUTPUTS = { text: 'Free text', label: 'One label', tags: 'Tags', score: 'Score' };
export const GRANS = {
  file: 'Whole file', segment: 'Each speech segment', window: 'Fixed windows', event: 'Timed events',
  word: 'Each word', phone: 'Each phone',
};

const EXAMPLES = [
  { name: 'Sarcasm', prompt: 'Is the speaker being sarcastic in this segment?', output: 'label', labels: ['sarcastic', 'not sarcastic'], granularity: 'segment', modalities: ['audio', 'video'] },
  { name: 'Speaker count', prompt: 'How many different people speak in this recording?', output: 'score', granularity: 'file', modalities: ['audio', 'video'], score_min: 0, score_max: 10 },
  { name: 'Background', prompt: 'Describe the background sounds in this segment, ignoring the speech itself.', output: 'text', granularity: 'segment', modalities: ['audio', 'video'], kind: 'captioning' },
  { name: 'Gaze', prompt: 'Where is the main person looking?', output: 'label', labels: ['camera', 'left', 'right', 'down', 'up', 'other person'], granularity: 'window', window_sec: 2, modalities: ['video', 'image'] },
  { name: 'Hesitation', prompt: 'Find every hesitation, false start or filled pause and give its start and end time.', output: 'label', labels: ['filled pause', 'false start', 'repetition', 'long pause'], granularity: 'event', modalities: ['audio', 'video'] },
  { name: 'Scene tags', prompt: 'Which of these describe the scene?', output: 'tags', labels: ['indoor', 'outdoor', 'crowd', 'stage', 'interview', 'vehicle'], granularity: 'file', modalities: ['video', 'image'] },
  { name: 'Politeness', prompt: 'How polite is the speaker in this segment, from 1 (rude) to 5 (very polite)?', output: 'score', granularity: 'segment', modalities: ['audio', 'video'] },
];

/** Open the editor. `base` = existing custom task (edit) or any task to duplicate. */
export function openTaskEditor({ base = null, edit = false, onSaved } = {}) {
  const t = {
    name: '', title: '', kind: 'annotation', output: 'text', granularity: 'segment', modalities: ['audio', 'video'],
    labels: [], color: COLORS[Math.floor(Math.random() * COLORS.length)], prompt: '', description: '', window_sec: 5,
    score_min: 1, score_max: 5,
    ...(base ? structuredClone(base) : {}),
  };
  if (base && !edit) { t.name = `${base.name} (copy)`; t.title = base.title ? `${base.title} (custom)` : ''; }
  let addToProject = !edit && !!state.project;

  const prompt = h('textarea.textarea.big', {
    rows: 6, placeholder: 'Describe what the model should annotate.\n\nExample: "Is the speaker reading from a script or speaking spontaneously?"',
    value: t.prompt, oninput: (e) => { t.prompt = e.target.value; preview(); }, autofocus: true,
  });
  const name = h('input.input', { value: t.name, placeholder: 'Short name, shown as the tier name', maxLength: 40, oninput: (e) => { t.name = e.target.value; previewTier(); } });
  const title = h('input.input', { value: t.title, placeholder: 'Longer title (optional)', oninput: (e) => { t.title = e.target.value; } });

  const kindCtl = segCtl({ annotation: 'Annotation', captioning: 'Captioning' }, () => t.kind, (v) => { t.kind = v; });
  const outCtl = segCtl(OUTPUTS, () => t.output, (v) => { t.output = v; sync(); });
  const granSel = h('select.select', { onchange: (e) => { t.granularity = e.target.value; sync(); } },
    Object.entries(GRANS).map(([k, v]) => h('option', { value: k, selected: t.granularity === k }, v)));
  const winInput = h('input.input', { type: 'number', min: 0.5, step: 0.5, value: t.window_sec, style: { width: '90px' }, oninput: (e) => { t.window_sec = +e.target.value; } });
  const winRow = h('div.row', h('span.muted', 'every'), winInput, h('span.muted', 'seconds'));
  const scoreRow = h('div.row', h('span.muted', 'from'),
    h('input.input', { type: 'number', value: t.score_min, style: { width: '80px' }, oninput: (e) => { t.score_min = +e.target.value; preview(); } }),
    h('span.muted', 'to'),
    h('input.input', { type: 'number', value: t.score_max, style: { width: '80px' }, oninput: (e) => { t.score_max = +e.target.value; preview(); } }));

  const labelsBox = h('div.labels-input');
  const labelInput = h('input', {
    placeholder: 'Type a label and press Enter',
    onkeydown: (e) => {
      if ((e.key === 'Enter' || e.key === ',') && labelInput.value.trim()) {
        e.preventDefault();
        for (const l of labelInput.value.split(',').map(s => s.trim()).filter(Boolean)) if (!t.labels.includes(l)) t.labels.push(l);
        labelInput.value = '';
        renderLabels();
      } else if (e.key === 'Backspace' && !labelInput.value && t.labels.length) {
        t.labels.pop();
        renderLabels();
      }
    },
    onblur: () => { if (labelInput.value.trim()) { labelInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' })); } },
  });
  function renderLabels() {
    labelsBox.replaceChildren(...t.labels.map((l, i) => h('span.chip', { style: { '--c': segColor({ output: 'label', labels: t.labels, color: t.color }, l) } },
      h('i.dot'), l, h('span.x', { onclick: () => { t.labels.splice(i, 1); renderLabels(); } }, ico('x', 11)))), labelInput);
    preview();
    previewTier();
  }
  const labelsField = h('label.field', h('span', 'Labels ', h('span.hint', '(the model must choose from these)')), labelsBox);

  const modBox = h('div.row', { style: { gap: '14px' } }, ['audio', 'video', 'image'].map(m =>
    h('label.check', h('input', {
      type: 'checkbox', checked: t.modalities.includes(m),
      onchange: (e) => { e.target.checked ? t.modalities.push(m) : (t.modalities = t.modalities.filter(x => x !== m)); },
    }), ico(modalityIcon(m), 14), m)));

  const swatches = h('div.swatches', COLORS.map(c => h('button' + (c === t.color ? '.on' : ''), {
    style: { '--c': c }, type: 'button',
    onclick: (e) => { t.color = c; [...swatches.children].forEach(b => b.classList.toggle('on', b === e.currentTarget)); previewTier(); },
  })));

  const promptOut = h('div.prompt-box', '…');
  const tierOut = h('div.tier-preview');
  const examples = h('div.examples', EXAMPLES.map(ex => h('span.chip', {
    onclick: () => {
      Object.assign(t, { kind: 'annotation', labels: [], window_sec: 5, ...structuredClone(ex) });
      prompt.value = t.prompt; name.value = t.name; granSel.value = t.granularity; winInput.value = t.window_sec;
      modBox.querySelectorAll('input').forEach((cb, i) => { cb.checked = t.modalities.includes(['audio', 'video', 'image'][i]); });
      kindCtl.update(); renderLabels(); sync();
    },
  }, ico('wand', 12), ex.name)));

  const fetchPreview = debounce(async () => {
    try {
      const r = await api.previewPrompt(t);
      promptOut.textContent = r.prompt;
    } catch { /* ignore */ }
  }, 250);
  function preview() { fetchPreview(); }
  function previewTier() {
    const fake = { output: t.output, labels: t.labels, color: t.color, granularity: t.granularity };
    const vals = t.output === 'label' ? (t.labels.length ? t.labels : ['label']) : t.output === 'tags' ? [(t.labels.slice(0, 2).join('; ') || 'tag')] :
      t.output === 'score' ? ['4.0', '3.5', '4.5'] : ['model output…', 'another segment'];
    const segs = t.granularity === 'file' ? [[0, 100]] : t.granularity === 'word' || t.granularity === 'phone' ? [[2, 12], [13, 24], [25, 33], [34, 47], [48, 60], [62, 75], [76, 90]]
      : [[3, 30], [36, 62], [70, 95]];
    tierOut.replaceChildren(h('div.tp-row',
      h('div.tp-head', h('span', { style: { width: '4px', height: '16px', borderRadius: '2px', background: t.color } }), t.name || 'New task'),
      h('div.tp-lane', segs.map(([a, b], i) => {
        const v = vals[i % vals.length];
        return h('div.seg.mock' + (t.output === 'label' ? '.label' : ''), { style: { left: a + '%', width: (b - a) + '%', '--c': segColor(fake, v) } }, h('span.t', v));
      }))));
  }
  function sync() {
    outCtl.update();
    labelsField.style.display = t.output === 'label' || t.output === 'tags' ? '' : 'none';
    scoreField.style.display = t.output === 'score' ? '' : 'none';
    winRow.style.display = t.granularity === 'window' ? '' : 'none';
    preview();
    previewTier();
  }
  const scoreField = h('label.field', h('span', 'Score range'), scoreRow);

  const body = h('div.te-grid',
    h('div',
      h('label.field', h('span', 'Prompt'), prompt, h('span.hint', 'Write it as an instruction to the model. The output format is added automatically. Use {labels} to insert the label list.')),
      h('div.field', h('span', 'Start from an example'), examples),
      h('div.row', { style: { gap: '10px', alignItems: 'flex-start' } },
        h('label.field.grow', h('span', 'Name'), name),
        h('label.field.grow', h('span', 'Title'), title)),
      h('div.field', h('span', 'Type'), kindCtl.el),
      h('div.field', h('span', 'Output'), outCtl.el),
      labelsField, scoreField,
      h('label.field', h('span', 'One answer per'), h('div.row', granSel, winRow)),
      h('div.field', h('span', 'Applies to'), modBox),
      h('div.field', h('span', 'Tier color'), swatches)),
    h('div',
      h('div.field', h('span', 'What the model will receive'), promptOut,
        h('span.hint', 'Sent together with each clip: the whole file, a segment, a window or a word, depending on "One answer per".')),
      h('div.field', h('span', 'Tier preview'), tierOut),
      !edit && state.project ? h('label.check', { style: { marginTop: '6px' } },
        h('input', { type: 'checkbox', checked: addToProject, onchange: (e) => { addToProject = e.target.checked; } }),
        `Add to "${state.project.name}"`) : null,
      h('div.notice.info', { style: { marginTop: '14px' } }, ico('info', 16),
        h('div', 'Prompt tasks are saved in your task library and can be reused in every project. ' +
          'Until a model is connected, runs fill them with placeholder output.'))));

  const save = async (m) => {
    try {
      const saved = edit ? await api.updateTask(base.id, t) : await api.createTask(t);
      await loadTasks();
      if (addToProject && state.project && !state.project.tasks.includes(saved.id)) {
        await updateProject({ tasks: [...state.project.tasks, saved.id] });
      }
      m.close();
      toast(edit ? 'Task updated' : `Task "${saved.name}" created`, { kind: 'ok' });
      onSaved?.(saved);
    } catch (e) { errorToast(e); }
  };
  openModal({
    title: edit ? `Edit task: ${base.name}` : 'New prompt task', width: 980,
    subtitle: 'Describe an annotation or captioning task in plain language. It becomes a tier that can be run on every file.',
    body,
    actions: [{ label: 'Cancel', ghost: true, onClick: (a) => a.close() },
      { label: edit ? 'Save changes' : 'Create task', primary: true, icon: 'check', onClick: save }],
  });
  renderLabels();
  sync();
}

function segCtl(options, get, set) {
  const el = h('div.seg-ctl');
  const update = () => [...el.children].forEach(b => b.classList.toggle('on', b.dataset.v === get()));
  for (const [k, v] of Object.entries(options)) {
    el.append(h('button', { type: 'button', dataset: { v: k }, onclick: () => { set(k); update(); } }, v));
  }
  update();
  return { el, update };
}

export function openTaskDetail(task, { onChange } = {}) {
  const promptOut = h('div.prompt-box', '…');
  api.previewPrompt(task).then(r => { promptOut.textContent = r.prompt; }).catch(() => {});
  const inProject = state.project?.tasks.includes(task.id);
  const actions = [];
  if (!task.builtin) {
    actions.push({ label: 'Delete', ghost: true, icon: 'trash', onClick: async (a) => {
      const ok = await confirmDialog(`Delete the prompt task "${task.name}" from your library? Existing tiers in files are kept.`, { title: 'Delete task' });
      if (!ok) return;
      try {
        await api.deleteTask(task.id);
        if (inProject) await updateProject({ tasks: state.project.tasks.filter(x => x !== task.id) });
        await loadTasks();
        a.close();
        onChange?.();
      } catch (e) { errorToast(e); }
    } });
    actions.push({ label: 'Edit', icon: 'edit', onClick: (a) => { a.close(); openTaskEditor({ base: task, edit: true, onSaved: onChange }); } });
  } else {
    actions.push({ label: 'Duplicate & edit prompt', icon: 'copy', onClick: (a) => { a.close(); openTaskEditor({ base: task, onSaved: onChange }); } });
  }
  if (state.project) {
    actions.push({ label: inProject ? 'Remove from project' : 'Add to project', primary: !inProject, onClick: async (a) => {
      const tasks = inProject ? state.project.tasks.filter(x => x !== task.id) : [...state.project.tasks, task.id];
      await updateProject({ tasks }).catch(errorToast);
      a.close();
      onChange?.();
    } });
  }
  openModal({
    title: task.title || task.name, subtitle: task.description, width: 640,
    body: h('div',
      h('div.row', { style: { flexWrap: 'wrap', gap: '6px', marginBottom: '14px' } },
        h('span.tchip', { style: { '--c': task.color } }, task.name),
        h('span.badge', task.kind === 'captioning' ? 'Captioning' : 'Annotation'),
        h('span.badge', OUTPUTS[task.output]),
        h('span.badge', GRANS[task.granularity] + (task.granularity === 'window' ? ` (${task.window_sec} s)` : '')),
        task.modalities.map(m => h('span.badge', ico(modalityIcon(m), 11), m)),
        task.parent ? h('span.badge', `after ${state.taskById.get(task.parent)?.name || task.parent}`) : null,
        task.builtin ? h('span.badge.accent', 'built-in') : h('span.badge.warn', 'my task')),
      task.labels?.length ? [h('h4', 'Labels'), h('div.label-chips', task.labels.map(l => h('span.chip', { style: { '--c': segColor(task, l) } }, h('i.dot'), l)))] : null,
      h('h4', 'Prompt sent to the model'), promptOut),
    actions,
  });
}
