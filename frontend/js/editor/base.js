// Shared editor plumbing: load/save with revision checks, header, file menu, import/export.
import { api, urls } from '../api.js';
import { h, ico, debounce, download, fmtDur, modalityIcon } from '../util.js';
import { state, on, emit, upsertMedia, removeMediaLocal, refreshProject } from '../state.js';
import { go, links } from '../nav.js';
import { openModal, menu, toast, errorToast, confirmDialog } from '../ui.js';
import { openRunDialog } from '../views/run.js';
import { STATUS } from '../views/files.js';
import { AnnoDoc } from './doc.js';

const SAVE_TEXT = { saved: 'All changes saved', saving: 'Saving…', dirty: 'Unsaved changes', error: 'Save failed' };

export class BaseEditor {
  constructor(dv, media, params) {
    this.dv = dv;
    this.pid = dv.pv.pid;
    this.media = media;
    this.params = params;
    this.doc = null;
    this.saving = false;
    this.savingP = null;
    this.savedVersion = 0;
    this.saveSoon = debounce(() => this.save(), 800);
    this.unsub = [];
    this.saveEl = h('span.save-state');
    this.el = h('div.editor', h('div.empty', { style: { gridRow: '1 / -1' } }, h('div.spinner'), 'Loading…'));
  }

  get lowThr() { return state.project?.settings?.low_confidence ?? 0.6; }
  get duration() { return this.media.duration || 0; }

  async loadDoc() {
    const raw = await api.annotations(this.pid, this.media.id);
    this.doc = new AnnoDoc(raw);
    this.savedVersion = this.doc.version;
    this.doc.addEventListener('change', (e) => {
      this.setSaveState('dirty');
      this.saveSoon();
      this.afterChange(e.detail);
    });
    this.unsub.push(on('media-annotated', (ids) => this.onExternalUpdate(ids)));
    this.unsub.push(on('media', () => this.onMediaUpdate()));
    this.setSaveState('saved');
  }

  isDirty() { return !!this.doc && this.doc.version !== this.savedVersion; }

  setSaveState(st, msg) {
    const text = msg || SAVE_TEXT[st] || '';
    this.saveEl.className = 'save-state ' + st;
    this.saveEl.replaceChildren(st === 'saving' ? h('div.spinner', { style: { width: '10px', height: '10px' } }) : ico(st === 'saved' ? 'check' : st === 'error' ? 'alert' : 'edit', 12), text);
    emit('save-state', { state: st, text });
  }

  save() {
    if (!this.doc || !this.isDirty()) return this.savingP || Promise.resolve();
    if (this.saving) { this.saveAgain = true; return this.savingP; }
    this.saving = true;
    const v = this.doc.version;
    this.setSaveState('saving');
    this.savingP = (async () => {
      try {
        const r = await api.saveAnnotations(this.pid, this.media.id, this.doc.toJSON());
        this.doc.rev = r.rev;
        this.savedVersion = v;
        if (r.media) { this.media = r.media; upsertMedia(r.media); }
        this.setSaveState(this.isDirty() ? 'dirty' : 'saved');
      } catch (e) {
        if (e.status === 409) this.conflict();
        else { this.setSaveState('error'); errorToast(e, 'Could not save'); }
      } finally {
        this.saving = false;
        if (this.saveAgain) { this.saveAgain = false; if (this.isDirty()) this.save(); }
      }
    })();
    return this.savingP;
  }

  async flush() {
    this.saveSoon.cancel();
    if (this.savingP) await this.savingP;
    if (this.isDirty()) await this.save();
  }

  conflict() {
    this.setSaveState('error', 'Changed elsewhere');
    openModal({
      title: 'This file was changed elsewhere', width: 480,
      body: h('p.muted-p', 'The annotations were updated outside this editor (for example by a task run or an import) while you had unsaved edits. ' +
        'Reload to get the new version, or keep your version and overwrite it.'),
      actions: [
        { label: 'Reload (lose my edits)', ghost: true, onClick: async (a) => { a.close(); await this.reload(); } },
        { label: 'Keep mine & overwrite', primary: true, onClick: async (a) => {
          a.close();
          try {
            const r = await api.saveAnnotations(this.pid, this.media.id, this.doc.toJSON(), true);
            this.doc.rev = r.rev;
            this.savedVersion = this.doc.version;
            this.setSaveState('saved');
          } catch (e) { errorToast(e); }
        } },
      ],
    });
  }

  async reload() {
    const raw = await api.annotations(this.pid, this.media.id);
    this.doc.load(raw);
    this.savedVersion = this.doc.version;
    this.setSaveState('saved');
    this.afterReload();
  }

  onExternalUpdate(ids) {
    if (!ids.includes(this.media.id) || !this.doc) return;
    if (!this.isDirty() && !this.saving) {
      this.reload().then(() => toast('This file was just annotated by the run', { kind: 'ok' }));
    }
  }

  onMediaUpdate() {
    const m = state.mediaById.get(this.media.id);
    if (m && m !== this.media) { this.media = m; this.renderStatus?.(); }
  }

  undo() {
    const l = this.doc.undo();
    if (!l) toast('Nothing to undo', { timeout: 1200 });
  }

  redo() {
    const l = this.doc.redo();
    if (!l) toast('Nothing to redo', { timeout: 1200 });
  }

  // ------------------------------------------------------------ header
  buildHeader() {
    const m = this.media;
    this.statusBtn = h('button.btn.sm', { onclick: () => this.toggleFileReviewed() });
    const more = h('button.icon-btn', { title: 'More', onclick: () => this.fileMenu(more) }, ico('more', 17));
    this.renderStatus();
    return h('div.ed-head',
      h('div.ed-title', ico(modalityIcon(m.modality), 18),
        h('div', h('div.nm', m.name), h('div.pth', [m.rel_path, m.source === 'linked' ? 'linked' : null,
          m.duration ? fmtDur(m.duration) : null, m.width ? `${m.width}×${m.height}` : null].filter(Boolean).join(' · ')))),
      this.saveEl,
      h('button.icon-btn', { title: 'Undo (⌘Z)', onclick: () => this.undo() }, ico('undo', 16)),
      h('button.icon-btn', { title: 'Redo (⌘⇧Z)', onclick: () => this.redo() }, ico('redo', 16)),
      h('span', { style: { width: '1px', height: '20px', background: 'var(--line-2)' } }),
      h('button.icon-btn', { title: 'Previous file ([)', onclick: () => this.dv.step(-1) }, ico('chev-l', 17)),
      h('button.icon-btn', { title: 'Next file (])', onclick: () => this.dv.step(1) }, ico('chev-r', 17)),
      this.statusBtn,
      h('button.btn.sm', { onclick: () => this.flush().then(() => openRunDialog({ mediaIds: [m.id] })) }, ico('sparkles', 13), 'Run'),
      more);
  }

  renderStatus() {
    if (!this.statusBtn) return;
    const rev = !!this.media.reviewed;
    this.statusBtn.className = 'btn sm' + (rev ? ' active' : '');
    this.statusBtn.title = `Status: ${STATUS[this.media.status || 'new']}. Click to ${rev ? 'reopen' : 'mark the whole file as reviewed'}.`;
    this.statusBtn.replaceChildren(ico(rev ? 'check' : 'review', 13), rev ? 'File reviewed' : 'Mark file reviewed');
  }

  async toggleFileReviewed() {
    await this.flush();
    try {
      const m = await api.patchMedia(this.pid, this.media.id, { reviewed: !this.media.reviewed });
      this.media = m;
      upsertMedia(m);
      this.renderStatus();
      refreshProject().catch(() => {});
      if (m.reviewed) toast('File marked as reviewed', { kind: 'ok', timeout: 1800 });
    } catch (e) { errorToast(e); }
  }

  fileMenu(anchor) {
    const m = this.media;
    const ex = (fmt, label) => ({ label, icon: 'download', onClick: async () => { await this.flush(); download(urls.exportFile(this.pid, m.id, fmt)); } });
    const av = m.modality !== 'image';
    menu(anchor, [
      { label: 'Import annotations (ELAN, Praat, JSON)…', icon: 'upload', onClick: () => this.importFile() },
      'sep', { header: 'Export this file' },
      av && ex('eaf', 'ELAN .eaf'), av && ex('textgrid', 'Praat TextGrid'), ex('json', 'Omni Studio JSON'),
      av && ex('srt', 'SRT subtitles'), av && ex('vtt', 'WebVTT'), av && ex('rttm', 'RTTM (diarization)'), ex('csv', 'CSV'),
      !av && ex('coco', 'COCO JSON (boxes & keypoints)'),
      'sep',
      { label: 'Open original file', icon: 'link', onClick: () => window.open(urls.file(this.pid, m.id), '_blank') },
      { label: m.source === 'linked' ? 'Remove from project (file stays on disk)' : 'Delete from project', icon: 'trash', danger: true, onClick: () => this.removeFile() },
    ], { placement: 'bottom-end' });
  }

  importFile() {
    const input = h('input', { type: 'file', accept: '.eaf,.TextGrid,.textgrid,.json', style: { display: 'none' } });
    input.addEventListener('change', async () => {
      const f = input.files[0];
      input.remove();
      if (!f) return;
      await this.flush();
      try {
        const r = await api.importAnnotations(this.pid, this.media.id, f);
        await this.reload();
        refreshProject().catch(() => {});
        toast(`Imported ${r.segments} segments into ${r.tiers.length} tier(s): ${r.tiers.join(', ')}`, { kind: 'ok', timeout: 5000 });
      } catch (e) { errorToast(e, 'Import failed'); }
    });
    document.body.append(input);
    input.click();
  }

  async removeFile() {
    const m = this.media;
    const ok = await confirmDialog(m.source === 'linked'
      ? `Remove "${m.name}" and its annotations from this project? The file itself stays where it is.`
      : `Delete "${m.name}" and its annotations from this project?`, { title: 'Remove file', ok: 'Remove' });
    if (!ok) return;
    try {
      this.saveSoon.cancel();
      this.savedVersion = this.doc?.version;
      await api.deleteMedia(this.pid, m.id);
      removeMediaLocal(m.id);
      refreshProject().catch(() => {});
      go(links.project(this.pid));
    } catch (e) { errorToast(e); }
  }

  afterChange() {}
  afterReload() {}
  pause() {}

  destroy() {
    this.unsub.forEach(f => f());
    this.saveSoon.cancel();
  }
}
