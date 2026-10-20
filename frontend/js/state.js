// Shared app state + a tiny event bus.
import { api } from './api.js';

export const state = {
  info: null,
  tasks: [],
  taskById: new Map(),
  categories: [],
  presets: [],
  project: null,
  media: [],
  mediaById: new Map(),
  jobs: new Map(), // jid -> job
};

const bus = new EventTarget();
export function on(name, fn) {
  const w = (e) => fn(e.detail);
  bus.addEventListener(name, w);
  return () => bus.removeEventListener(name, w);
}
export function emit(name, detail) {
  bus.dispatchEvent(new CustomEvent(name, { detail }));
}

export async function loadInfo() {
  state.info = await api.info();
  return state.info;
}

export async function loadTasks() {
  const d = await api.tasks();
  state.tasks = d.tasks;
  state.categories = d.categories;
  state.presets = d.presets;
  state.taskById = new Map(d.tasks.map(t => [t.id, t]));
  emit('tasks');
  return d;
}

export async function loadProject(pid) {
  const p = await api.project(pid);
  const { media, jobs, ...proj } = p;
  state.project = proj;
  setMedia(media);
  for (const j of jobs || []) if (j.status === 'running' || j.status === 'queued') trackJob(j);
  emit('project', proj);
  return proj;
}

export async function refreshProject() {
  if (!state.project) return;
  const p = await api.project(state.project.id);
  const { media, jobs, ...proj } = p;
  state.project = proj;
  setMedia(media);
  emit('project', proj);
}

export function setMedia(items) {
  state.media = items;
  state.mediaById = new Map(items.map(m => [m.id, m]));
  emit('media');
}

export function upsertMedia(item) {
  const i = state.media.findIndex(m => m.id === item.id);
  if (i >= 0) state.media[i] = item; else state.media.push(item);
  state.mediaById.set(item.id, item);
  emit('media', item);
}

export function removeMediaLocal(mid) {
  state.media = state.media.filter(m => m.id !== mid);
  state.mediaById.delete(mid);
  emit('media');
}

export function projectTasks() {
  const p = state.project;
  if (!p) return [];
  return p.tasks.map(id => state.taskById.get(id)).filter(Boolean);
}

export async function updateProject(patch) {
  const p = await api.patchProject(state.project.id, patch);
  state.project = { ...state.project, ...p };
  emit('project', state.project);
  return p;
}

// ---- background runs ------------------------------------------------------
const polling = new Set();
export function trackJob(job) {
  state.jobs.set(job.id, job);
  emit('job', job);
  if (polling.has(job.id)) return;
  polling.add(job.id);
  let lastDone = -1, lastRefresh = 0, seen = new Set();
  const tick = async () => {
    let j;
    try { j = await api.job(job.id); } catch { polling.delete(job.id); return; }
    state.jobs.set(j.id, j);
    emit('job', j);
    const fresh = (j.updated_media || []).filter(m => !seen.has(m));
    fresh.forEach(m => seen.add(m));
    const now = Date.now();
    if (state.project?.id === j.project_id && (fresh.length && now - lastRefresh > 1500 || j.done !== lastDone && j.status !== 'running')) {
      lastRefresh = now;
      await refreshProject().catch(() => {});
    }
    if (fresh.length) emit('media-annotated', fresh);
    lastDone = j.done;
    if (j.status === 'running' || j.status === 'queued') setTimeout(tick, 600);
    else {
      polling.delete(j.id);
      if (state.project?.id === j.project_id) await refreshProject().catch(() => {});
      emit('job-done', j);
    }
  };
  setTimeout(tick, 300);
}

export function activeJob() {
  for (const j of state.jobs.values()) {
    if ((j.status === 'running' || j.status === 'queued') && j.project_id === state.project?.id) return j;
  }
  return null;
}
