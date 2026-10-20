// Thin client for the Omni Studio API.
export class ApiError extends Error {
  constructor(status, detail, body) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function req(method, url, body) {
  const opt = { method, headers: {} };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new ApiError(r.status, (data && data.detail) || text || r.statusText, data);
  return data;
}

const enc = encodeURIComponent;
const P = (pid) => `/api/projects/${enc(pid)}`;
const M = (pid, mid) => `${P(pid)}/media/${enc(mid)}`;
const qs = (o) => Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k}=${enc(v)}`).join('&');

export const api = {
  info: () => req('GET', '/api/info'),
  fs: (path) => req('GET', `/api/fs?${qs({ path })}`),

  tasks: () => req('GET', '/api/tasks'),
  createTask: (t) => req('POST', '/api/tasks', t),
  updateTask: (id, t) => req('PUT', `/api/tasks/${enc(id)}`, t),
  deleteTask: (id) => req('DELETE', `/api/tasks/${enc(id)}`),
  resetTask: (id) => req('POST', `/api/tasks/${enc(id)}/reset`),
  testTask: (pid, body) => req('POST', `${P(pid)}/test-task`, body),
  previewPrompt: (t) => req('POST', '/api/tasks/preview', t),

  projects: () => req('GET', '/api/projects'),
  createProject: (p) => req('POST', '/api/projects', p),
  project: (pid) => req('GET', P(pid)),
  patchProject: (pid, patch) => req('PATCH', P(pid), patch),
  deleteProject: (pid) => req('DELETE', P(pid)),

  linkMedia: (pid, path, recursive = true) => req('POST', `${P(pid)}/media/link`, { path, recursive }),
  patchMedia: (pid, mid, patch) => req('PATCH', M(pid, mid), patch),
  deleteMedia: (pid, mid) => req('DELETE', M(pid, mid)),

  annotations: (pid, mid) => req('GET', `${M(pid, mid)}/annotations`),
  saveAnnotations: (pid, mid, doc, force = false) =>
    req('PUT', `${M(pid, mid)}/annotations${force ? '?force=true' : ''}`, doc),
  importAnnotations: (pid, mid, file) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    return req('POST', `${M(pid, mid)}/annotations/import`, fd);
  },

  run: (pid, body) => req('POST', `${P(pid)}/run`, body),
  jobs: (pid) => req('GET', `/api/jobs?${qs({ project: pid })}`),
  job: (jid) => req('GET', `/api/jobs/${enc(jid)}`),
  cancelJob: (jid) => req('POST', `/api/jobs/${enc(jid)}/cancel`),
  review: (pid, params) => req('GET', `${P(pid)}/review?${qs(params)}`),
  stats: (pid) => req('GET', `${P(pid)}/stats`),

  shares: (pid) => req('GET', `${P(pid)}/shares`),
  createShare: (pid, body) => req('POST', `${P(pid)}/shares`, body),
  updateShare: (sid, patch) => req('PATCH', `/api/shares/${enc(sid)}`, patch),
  deleteShare: (sid) => req('DELETE', `/api/shares/${enc(sid)}`),
  startTunnel: () => req('POST', '/api/share-server/tunnel'),
  stopTunnel: () => req('DELETE', '/api/share-server/tunnel'),
};

export const urls = {
  file: (pid, mid) => `${M(pid, mid)}/file`,
  audioProxy: (pid, mid) => `${M(pid, mid)}/audio.wav`,
  peaks: (pid, mid, start, end, n) => `${M(pid, mid)}/peaks?${qs({ start: start.toFixed(4), end: end.toFixed(4), n })}`,
  spec: (pid, mid, o) => `${M(pid, mid)}/spectrogram?${qs(o)}`,
  frame: (pid, mid, t, h) => `${M(pid, mid)}/frame?${qs({ t: t.toFixed(2), h })}`,
  thumb: (pid, mid) => `${M(pid, mid)}/thumb`,
  exportFile: (pid, mid, format) => `${M(pid, mid)}/export?${qs({ format })}`,
  exportZip: (pid, o) => `${P(pid)}/export.zip?${qs(o)}`,
  statsCsv: (pid) => `${P(pid)}/stats.csv`,
};

/** Upload one file with progress; resolves to the new media item. */
export function uploadFile(pid, file, relPath, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('rel_path', relPath || file.name);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${P(pid)}/media/upload`);
    xhr.upload.onprogress = (e) => onProgress && e.lengthComputable && onProgress(e.loaded, e.total);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, (data && data.detail) || xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, 'Network error'));
    xhr.send(fd);
  });
}
