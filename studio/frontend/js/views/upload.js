// Adding data: browser upload (files / folders / drag & drop) and linking local folders.
import { api, uploadFile } from '../api.js';
import { h, ico, fmtBytes, ls } from '../util.js';
import { upsertMedia, refreshProject } from '../state.js';
import { openModal, toast, errorToast } from '../ui.js';

export const MEDIA_EXT = new Set(['jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif', 'tif', 'tiff',
  'wav', 'mp3', 'flac', 'ogg', 'opus', 'm4a', 'aac', 'wma', 'aif', 'aiff',
  'mp4', 'mov', 'mkv', 'webm', 'avi', 'm4v', 'mpg', 'mpeg', 'wmv']);

const ext = (name) => (name.split('.').pop() || '').toLowerCase();

export function pickAndUpload(pid, { folder = false } = {}) {
  const input = h('input', { type: 'file', multiple: true, style: { display: 'none' } });
  if (folder) input.webkitdirectory = true;
  input.addEventListener('change', () => {
    const list = [...input.files].map(f => ({ file: f, rel: f.webkitRelativePath || f.name }));
    input.remove();
    uploadMany(pid, list);
  });
  document.body.append(input);
  input.click();
}

/** Collect files (with their folder paths) from a drop event, including whole folders. */
export async function filesFromDrop(dt) {
  const out = [];
  const entries = [...(dt.items || [])].map(i => i.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [...(dt.files || [])].map(f => ({ file: f, rel: f.name }));
  async function walk(entry, prefix) {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, rel: prefix + file.name });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const e of batch) await walk(e, prefix + entry.name + '/');
      } while (batch.length);
    }
  }
  for (const e of entries) await walk(e, '');
  return out;
}

export async function uploadMany(pid, list) {
  const files = list.filter(x => !x.file.name.startsWith('.') && MEDIA_EXT.has(ext(x.file.name)));
  const ignored = list.length - files.length;
  if (!files.length) {
    toast(list.length ? `None of the ${list.length} files are audio, video or images` : 'No files selected', { kind: 'warn' });
    return;
  }
  const total = files.reduce((a, x) => a + x.file.size, 0);
  const loaded = new Map();
  let done = 0, failed = [];
  const bar = h('i', { style: { width: '0%' } });
  const label = h('div', `Uploading 0/${files.length} files`);
  const t = toast(h('div', label, h('div.progress', bar)), { kind: 'info', timeout: 0 });
  const update = () => {
    const bytes = [...loaded.values()].reduce((a, b) => a + b, 0);
    bar.style.width = (total ? (bytes / total) * 100 : 0) + '%';
    label.textContent = `Uploading ${done}/${files.length} files, ${fmtBytes(bytes)} of ${fmtBytes(total)}`;
  };
  const queue = files.slice();
  async function worker() {
    while (queue.length) {
      const x = queue.shift();
      try {
        const item = await uploadFile(pid, x.file, x.rel, (l) => { loaded.set(x, l); update(); });
        upsertMedia(item);
      } catch (e) {
        failed.push(`${x.rel}: ${e.message}`);
      }
      loaded.set(x, x.file.size);
      done++;
      update();
    }
  }
  await Promise.all([worker(), worker(), worker()]);
  t.close();
  await refreshProject().catch(() => {});
  const ok = files.length - failed.length;
  toast(`Added ${ok} file${ok === 1 ? '' : 's'}` + (ignored ? `; ${ignored} unsupported skipped` : '') +
    (failed.length ? `; ${failed.length} failed` : ''), { kind: failed.length ? 'warn' : 'ok', timeout: failed.length ? 8000 : 3500 });
  if (failed.length) console.warn('Upload failures', failed);
}

export function openLinkDialog(pid) {
  const pathInput = h('input.input.mono', { value: ls.get('linkPath', ''), placeholder: '/path/to/your/data' });
  const list = h('div.link-list');
  const info = h('div.link-info');
  const recursive = h('input', { type: 'checkbox', checked: true });
  let cur = null;

  async function browse(p) {
    try {
      const d = await api.fs(p || '~');
      cur = d;
      pathInput.value = d.path;
      const rows = [];
      if (d.parent) rows.push(dirRow('..', d.parent, 'chev-u'));
      d.dirs.forEach(x => rows.push(dirRow(x.name, x.path, 'folder')));
      if (!d.dirs.length) rows.push(h('div.muted', 'No subfolders'));
      list.replaceChildren(...rows);
      info.textContent = d.media_here ? `${d.media_here} media file(s) directly in this folder` : 'No media files directly in this folder (subfolders are included when linking recursively)';
    } catch (e) {
      info.textContent = e.message;
    }
  }
  function dirRow(name, path, icon) {
    return h('div.menu-item', { ondblclick: () => browse(path), onclick: () => browse(path), title: path }, ico(icon, 15), h('span.menu-label', name));
  }
  pathInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') browse(pathInput.value); });

  const doLink = async (m) => {
    const btn = m.button('link');
    btn.disabled = true;
    btn.replaceChildren(h('div.spinner'), 'Scanning…');
    try {
      const r = await api.linkMedia(pid, pathInput.value, recursive.checked);
      ls.set('linkPath', pathInput.value);
      m.close();
      await refreshProject();
      toast(`Linked ${r.added} file${r.added === 1 ? '' : 's'}` + (r.skipped ? `; ${r.skipped} already in project` : '') +
        (r.errors.length ? `; ${r.errors.length} could not be read` : ''), { kind: r.errors.length ? 'warn' : 'ok' });
    } catch (e) {
      errorToast(e);
      btn.disabled = false;
      btn.replaceChildren(ico('link', 15), 'Link folder');
    }
  };
  openModal({
    title: 'Link a local folder', width: 600,
    subtitle: 'Files stay where they are. Omni Studio only stores a reference, so large datasets do not need to be copied.',
    body: h('div',
      h('div.row.link-path', pathInput, h('button.btn', { onclick: () => browse(pathInput.value) }, 'Go')),
      list,
      h('div.row.link-foot', info, h('label.check', recursive, 'Include subfolders'))),
    actions: [
      { label: 'Cancel', ghost: true, onClick: (a) => a.close() },
      { id: 'link', label: 'Link folder', icon: 'link', primary: true, onClick: doLink },
    ],
  });
  browse(pathInput.value || '~');
}

export function addDataMenuItems(pid) {
  return [
    { label: 'Upload files…', icon: 'upload', onClick: () => pickAndUpload(pid) },
    { label: 'Upload a folder…', icon: 'folder', onClick: () => pickAndUpload(pid, { folder: true }) },
    'sep',
    { label: 'Link local folder (no copy)…', icon: 'link', onClick: () => openLinkDialog(pid) },
  ];
}
