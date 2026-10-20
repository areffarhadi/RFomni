// Create / edit tasks (new prompt tasks, your own tasks, and changes to built-in tasks), and view a task.
import { api } from '../api.js';
import { h, ico, debounce, modalityIcon, segColor } from '../util.js';
import { state, loadTasks, updateProject } from '../state.js';
import { openModal, confirmDialog, toast, errorToast } from '../ui.js';
import { openTaskTest } from './task_test.js';

const COLORS = ['#38bdf8', '#22d3ee', '#2dd4bf', '#34d399', '#a3e635', '#facc15', '#fb923c', '#f87171',
  '#f472b6', '#e879f9', '#a78bfa', '#818cf8', '#94a3b8'];

export const OUTPUTS = { text: 'Free text', label: 'One label', tags: 'Tags', score: 'Score', boxes: 'Boxes', keypoints: 'Keypoints' };
export const GRANS = {
  file: 'Whole file', segment: 'Each speech segment', window: 'Fixed windows', event: 'Timed events',
  word: 'Each word', phone: 'Each phone',
};
export const FIELD_NAMES = { labels: 'classes', prompt: 'prompt', name: 'name', title: 'title', description: 'description',
  color: 'color', window_sec: 'window', score_min: 'score range', score_max: 'score range' };

const EXAMPLES = [
  { name: 'Sarcasm', prompt: 'Is the speaker being sarcastic in this segment?', output: 'label', labels: ['sarcastic', 'not sarcastic'], granularity: 'segment', modalities: ['audio', 'video'] },
  { name: 'Speaker count', prompt: 'How many different people speak in this recording?', output: 'score', granularity: 'file', modalities: ['audio', 'video'], score_min: 0, score_max: 10 },
  { name: 'Background', prompt: 'Describe the background sounds in this segment, ignoring the speech itself.', output: 'text', granularity: 'segment', modalities: ['audio', 'video'], kind: 'captioning' },
  { name: 'Gaze', prompt: 'Where is the main person looking?', output: 'label', labels: ['camera', 'left', 'right', 'down', 'up', 'other person'], granularity: 'window', window_sec: 2, modalities: ['video', 'image'] },
  { name: 'Hesitation', prompt: 'Find every hesitation, false start or filled pause and give its start and end time.', output: 'label', labels: ['filled pause', 'false start', 'repetition', 'long pause'], granularity: 'event', modalities: ['audio', 'video'] },
  { name: 'Scene tags', prompt: 'Which of these describe the scene?', output: 'tags', labels: ['indoor', 'outdoor', 'crowd', 'stage', 'interview', 'vehicle'], granularity: 'file', modalities: ['video', 'image'] },
  { name: 'Politeness', prompt: 'How polite is the speaker in this segment, from 1 (rude) to 5 (very polite)?', output: 'score', granularity: 'segment', modalities: ['audio', 'video'] },
  { name: 'Hands', prompt: 'Find every visible hand.', output: 'boxes', labels: ['left hand', 'right hand'], granularity: 'file', modalities: ['image'] },
];

const changedSummary = (t) => [...new Set((t.modified_fields || []).map(k => FIELD_NAMES[k] || k))].join(' · ');

export async function resetTask(task, onChange) {
  const ok = await confirmDialog(`Reset "${task.title || task.name}" to its default ${changedSummary(task) || 'settings'}? ` +
    'Labels already used in your files are kept.', { title: 'Reset to default', ok: 'Reset', danger: false });
  if (!ok) return false;
  try {
    await api.resetTask(task.id);
    await loadTasks();
    toast(`"${task.name}" is back to its default`, { kind: 'ok' });
    onChange?.();
    return true;
  } catch (e) { errorToast(e); return false; }
}

/** Open the editor.
 *  - no base: new prompt task
 *  - base + edit (custom task): edit it
 *  - base + edit (built-in): change classes / prompt / names; output type stays fixed
 *  - base without edit: duplicate as a new prompt task */
export function openTaskEditor({ base = null, edit = false, onSaved } = {}) {
  const builtinEdit = !!(edit && base?.builtin);
  const dflt = builtinEdit ? { ...base, ...(base.default || {}) } : null;
  const t = {
    name: '', title: '', kind: 'annotation', output: 'text', granularity: 'segment', modalities: ['audio', 'video'],
    labels: [], color: COLORS[Math.floor(Math.random() * COLORS.length)], prompt: '', description: '', window_sec: 5,
    score_min: 1, score_max: 5,
    ...(base ? structuredClone(base) : {}),
  };
  if (base && !edit) { delete t.id; t.name = `${base.name} (copy)`; t.title = base.title ? `${base.title} (custom)` : ''; delete t.default; delete t.modified; delete t.modified_fields; }
  let addToProject = !edit && !!state.project;
  const openVocab = builtinEdit && !(dflt.labels || []).length;

  const prompt = h('textarea.textarea.big', {
    rows: 6, placeholder: 'Describe what the model should annotate.\n\nExample: "Is the speaker reading from a script or speaking spontaneously?"',
    value: t.prompt, oninput: (e) => { t.prompt = e.target.value; preview(); defaults(); }, autofocus: true,
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

  // ---- classes: chips, or the whole list as text
  const labelsBox = h('div.labels-input');
  const labelsText = h('textarea.textarea', { rows: 6, style: { display: 'none' }, placeholder: 'One class per line',
    oninput: () => { t.labels = [...new Set(labelsText.value.split(/\n|,/).map(s => s.trim()).filter(Boolean))]; labelCount(); preview(); previewTier(); defaults(); } });
  let asText = false;
  const textToggle = h('button.btn.sm.ghost', { type: 'button', onclick: () => {
    asText = !asText;
    if (asText) labelsText.value = t.labels.join('\n'); else renderLabels();
    labelsBox.style.display = asText ? 'none' : '';
    labelsText.style.display = asText ? '' : 'none';
    textToggle.lastChild.textContent = asText ? 'Edit as chips' : 'Edit as list';
    if (asText) labelsText.focus();
  } }, ico('edit', 12), 'Edit as list');
  const countEl = h('span.badge');
  const labelCount = () => { countEl.textContent = `${t.labels.length} class${t.labels.length === 1 ? '' : 'es'}`; };
  const labelInput = h('input', {
    placeholder: 'Type a class and press Enter',
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
    labelsBox.replaceChildren(...t.labels.map((l, i) => h('span.chip', {
      style: { '--c': segColor({ output: 'label', labels: t.labels, color: t.color }, l) },
      title: 'Double-click to rename',
      ondblclick: (e) => {
        const chip = e.currentTarget;
        const inp = h('input.chip-edit', { value: l, size: Math.max(6, l.length + 2) });
        let done = false;
        const commit = (save) => {
          if (done) return;
          done = true;
          const v = inp.value.trim();
          if (save && v && v !== l && !t.labels.includes(v)) t.labels[i] = v;
          renderLabels();
        };
        inp.addEventListener('keydown', (ev) => { ev.stopPropagation(); if (ev.key === 'Enter') { ev.preventDefault(); commit(true); } if (ev.key === 'Escape') commit(false); });
        inp.addEventListener('blur', () => commit(true));
        chip.replaceWith(inp);
        inp.focus();
        inp.select();
      },
    }, h('i.dot'), l, h('span.x', { onclick: () => { t.labels.splice(i, 1); renderLabels(); } }, ico('x', 11)))), labelInput);
    labelCount();
    preview();
    previewTier();
    defaults();
  }
  const labelsDefault = h('div.default-hint');
  const labelsField = h('div.field',
    h('div.row', h('span', { style: { fontSize: '12px', color: 'var(--text-2)', fontWeight: 500 } }, openVocab ? 'Suggested labels ' : 'Classes '),
      h('span.hint', openVocab ? '(open set: other labels are allowed)' : '(the model must choose from these)'), countEl, h('div.grow'), textToggle),
    labelsBox, labelsText, labelsDefault);

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
  const promptDefault = h('div.default-hint');
  const examples = h('div.examples', EXAMPLES.map(ex => h('span.chip', {
    onclick: () => {
      Object.assign(t, { kind: 'annotation', labels: [], window_sec: 5, ...structuredClone(ex) });
      prompt.value = t.prompt; name.value = t.name; granSel.value = t.granularity; winInput.value = t.window_sec;
      modBox.querySelectorAll('input').forEach((cb, i) => { cb.checked = t.modalities.includes(['audio', 'video', 'image'][i]); });
      kindCtl.update(); renderLabels(); sync();
    },
  }, ico('wand', 12), ex.name)));

  /** For built-in tasks: show the default next to each changed field, with a one-click "use default". */
  function defaults() {
    if (!builtinEdit) return;
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    labelsDefault.replaceChildren();
    promptDefault.replaceChildren();
    if (!same(t.labels, dflt.labels || [])) {
      labelsDefault.append(h('span', 'Default: ', (dflt.labels || []).join(', ') || '(open set)'),
        h('button.btn.sm.ghost', { type: 'button', onclick: () => { t.labels = [...(dflt.labels || [])]; if (asText) labelsText.value = t.labels.join('\n'); renderLabels(); } }, ico('undo', 12), 'Use default'));
    }
    if (t.prompt.trim() !== (dflt.prompt || '').trim()) {
      promptDefault.append(h('span', 'Default: ', dflt.prompt),
        h('button.btn.sm.ghost', { type: 'button', onclick: () => { t.prompt = dflt.prompt; prompt.value = t.prompt; preview(); defaults(); } }, ico('undo', 12), 'Use default'));
    }
  }

  const fetchPreview = debounce(async () => {
    try {
      const r = await api.previewPrompt(t);
      promptOut.textContent = r.prompt;
    } catch { /* ignore */ }
  }, 250);
  function preview() { fetchPreview(); }
  function previewTier() {
    if (t.output === 'boxes' || t.output === 'keypoints') {
      tierOut.replaceChildren(h('div', { style: { padding: '10px 12px', fontSize: '12.5px', color: 'var(--text-2)' } },
        t.output === 'boxes' ? 'Shown as labelled rectangles on the image; you can move, resize, relabel and add boxes.'
          : 'Shown as a skeleton of 17 keypoints per person; you can drag, add and hide points.'));
      return;
    }
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
    const spatial = t.output === 'boxes' || t.output === 'keypoints';
    if (spatial && !builtinEdit) {
      t.granularity = 'file';
      t.modalities = ['image'];
      granSel.value = 'file';
      modBox.querySelectorAll('input').forEach((cb, i) => { cb.checked = i === 2; });
    }
    granField.style.display = spatial || builtinEdit ? 'none' : '';
    modField.style.display = spatial || builtinEdit ? 'none' : '';
    labelsField.style.display = t.output === 'label' || t.output === 'tags' || t.output === 'boxes' ? '' : 'none';
    scoreField.style.display = t.output === 'score' ? '' : 'none';
    winRow.style.display = t.granularity === 'window' ? '' : 'none';
    preview();
    previewTier();
  }
  const scoreField = h('label.field', h('span', 'Score range'), scoreRow);
  const granField = h('label.field', h('span', 'One answer per'), h('div.row', granSel, winRow));
  const modField = h('div.field', h('span', 'Applies to'), modBox);
  const fixedInfo = builtinEdit ? h('div.field', h('span', 'Output'),
    h('div.row', { style: { gap: '6px', flexWrap: 'wrap' } },
      h('span.badge', OUTPUTS[t.output]), h('span.badge', GRANS[t.granularity] + (t.granularity === 'window' ? ` (${t.window_sec} s)` : '')),
      t.modalities.map(m => h('span.badge', ico(modalityIcon(m), 11), m))),
    h('span.hint', 'Built-in tasks keep their output type. To change it, duplicate the task as a new one.')) : null;

  const body = h('div.te-grid',
    h('div',
      h('label.field', h('span', 'Prompt'), prompt, promptDefault,
        h('span.hint', 'Write it as an instruction to the model. The output format is added automatically. Use {labels} to insert the class list.')),
      builtinEdit ? null : h('div.field', h('span', 'Start from an example'), examples),
      h('div.row', { style: { gap: '10px', alignItems: 'flex-start' } },
        h('label.field.grow', h('span', 'Name'), name),
        h('label.field.grow', h('span', 'Title'), title)),
      builtinEdit ? fixedInfo : [h('div.field', h('span', 'Type'), kindCtl.el), h('div.field', h('span', 'Output'), outCtl.el)],
      labelsField, scoreField,
      granField,
      modField,
      h('div.field', h('span', 'Tier color'), swatches)),
    h('div',
      h('div.field', h('span', 'What the model will receive'), promptOut,
        h('span.hint', 'Sent together with each clip: the whole file, a segment, a window or a word, depending on "One answer per".')),
      h('div.field', h('span', 'Tier preview'), tierOut),
      !edit && state.project ? h('label.check', { style: { marginTop: '6px' } },
        h('input', { type: 'checkbox', checked: addToProject, onchange: (e) => { addToProject = e.target.checked; } }),
        `Add to "${state.project.name}"`) : null,
      h('div.notice.info', { style: { marginTop: '14px' } }, ico('info', 16),
        h('div', builtinEdit
          ? 'Your changes apply to every project and appear under "Changed & new tasks". You can reset this task to its default at any time.'
          : 'Prompt tasks are saved in your task library and can be reused in every project. Until a model is connected, runs fill them with placeholder output.'))));

  const save = async (m) => {
    try {
      const saved = edit ? await api.updateTask(base.id, t) : await api.createTask(t);
      await loadTasks();
      if (addToProject && state.project && !state.project.tasks.includes(saved.id)) {
        await updateProject({ tasks: [...state.project.tasks, saved.id] });
      }
      m.close();
      toast(builtinEdit ? (saved.modified ? `"${saved.name}" changed` : `"${saved.name}" matches its default again`)
        : edit ? 'Task updated' : `Task "${saved.name}" created`, { kind: 'ok' });
      onSaved?.(saved);
    } catch (e) { errorToast(e); }
  };
  const testDraft = () => {
    if (!state.project) { toast('Open a project to test on its files', { kind: 'warn' }); return; }
    openTaskTest({ task: { ...t, id: edit ? base.id : undefined }, saved: false });
  };
  const actions = [];
  if (builtinEdit && base.modified) {
    actions.push({ label: 'Reset to default', ghost: true, icon: 'undo', onClick: async (a) => { if (await resetTask(base, onSaved)) a.close(); } });
  }
  actions.push(h('div.grow'));
  actions.push({ label: 'Test on a sample', icon: 'play', onClick: testDraft });
  actions.push({ label: 'Cancel', ghost: true, onClick: (a) => a.close() });
  actions.push({ label: edit ? 'Save changes' : 'Create task', primary: true, icon: 'check', onClick: save });
  openModal({
    title: builtinEdit ? `Edit built-in task: ${base.title || base.name}` : edit ? `Edit task: ${base.name}` : 'New prompt task', width: 980,
    subtitle: builtinEdit ? 'Change the classes, the prompt or the names. Changed tasks are listed on the right of the Tasks page.'
      : 'Describe an annotation or captioning task in plain language. It becomes a tier that can be run on every file.',
    body, actions,
  });
  renderLabels();
  sync();
  defaults();
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
      if (await deleteTask(task, onChange)) a.close();
    } });
  } else if (task.modified) {
    actions.push({ label: 'Reset to default', ghost: true, icon: 'undo', onClick: async (a) => { if (await resetTask(task, onChange)) a.close(); } });
  }
  actions.push(h('div.grow'));
  if (state.project) actions.push({ label: 'Test', icon: 'play', onClick: () => openTaskTest({ task }) });
  actions.push({ label: 'Duplicate', icon: 'copy', ghost: true, onClick: (a) => { a.close(); openTaskEditor({ base: task, onSaved: onChange }); } });
  actions.push({ label: task.builtin ? 'Edit classes & prompt' : 'Edit', icon: 'edit', onClick: (a) => { a.close(); openTaskEditor({ base: task, edit: true, onSaved: onChange }); } });
  if (state.project) {
    actions.push({ label: inProject ? 'Remove from project' : 'Add to project', primary: !inProject, onClick: async (a) => {
      const tasks = inProject ? state.project.tasks.filter(x => x !== task.id) : [...state.project.tasks, task.id];
      await updateProject({ tasks }).catch(errorToast);
      a.close();
      onChange?.();
    } });
  }
  const d = task.default || {};
  openModal({
    title: task.title || task.name, subtitle: task.description, width: 720,
    body: h('div',
      h('div.row', { style: { flexWrap: 'wrap', gap: '6px', marginBottom: '14px' } },
        h('span.tchip', { style: { '--c': task.color } }, task.name),
        h('span.badge', task.kind === 'captioning' ? 'Captioning' : 'Annotation'),
        h('span.badge', OUTPUTS[task.output]),
        h('span.badge', GRANS[task.granularity] + (task.granularity === 'window' ? ` (${task.window_sec} s)` : '')),
        task.modalities.map(m => h('span.badge', ico(modalityIcon(m), 11), m)),
        task.parent ? h('span.badge', `after ${state.taskById.get(task.parent)?.name || task.parent}`) : null,
        !task.builtin ? h('span.badge.warn', 'new task') : task.modified ? h('span.badge.warn', `changed: ${changedSummary(task)}`) : h('span.badge.accent', 'default')),
      task.labels?.length ? [h('h4', `Classes (${task.labels.length})`), h('div.label-chips', task.labels.map(l => h('span.chip', { style: { '--c': segColor(task, l) } }, h('i.dot'), l)))] : null,
      task.modified && d.labels && JSON.stringify(d.labels) !== JSON.stringify(task.labels)
        ? h('div.default-hint', h('span', 'Default classes: ', d.labels.join(', ') || '(open set)')) : null,
      h('h4', 'Prompt sent to the model'), promptOut,
      task.modified && d.prompt && d.prompt !== task.prompt ? h('div.default-hint', h('span', 'Default prompt: ', d.prompt)) : null),
    actions,
  });
}

export async function deleteTask(task, onChange) {
  const ok = await confirmDialog(`Delete the task "${task.name}" from your library? Existing tiers in files are kept.`, { title: 'Delete task' });
  if (!ok) return false;
  try {
    await api.deleteTask(task.id);
    if (state.project?.tasks.includes(task.id)) await updateProject({ tasks: state.project.tasks.filter(x => x !== task.id) });
    await loadTasks();
    onChange?.();
    return true;
  } catch (e) { errorToast(e); return false; }
}
