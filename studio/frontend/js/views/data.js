// Data tab: file list on the left, dashboard or editor on the right.
import { h, ls } from '../util.js';
import { state, emit, isShared } from '../state.js';
import { go, links } from '../nav.js';
import { toast } from '../ui.js';
import { FileList } from './files.js';
import { Dashboard } from './dashboard.js';
import { filesFromDrop, uploadMany } from './upload.js';
import { Editor } from '../editor/editor.js';
import { ImageEditor } from '../editor/image.js';
import { swap } from '../motion.js';

export class DataView {
  constructor(pv) {
    this.pv = pv;
    this.files = new FileList(this);
    this.detail = h('section.detail');
    this.el = h('div.data', { style: { '--files-w': ls.get('filesW', 300) + 'px' } }, this.files.el, this.detail);
    this.editor = null;
    this.dash = null;
    this.mid = undefined;
    this.setupDrop();
  }

  setupDrop() {
    if (isShared()) return;
    let depth = 0;
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
    this.el.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; this.el.classList.add('dropzone-over'); });
    this.el.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
    this.el.addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) this.el.classList.remove('dropzone-over'); });
    this.el.addEventListener('drop', async (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      this.el.classList.remove('dropzone-over');
      const list = await filesFromDrop(e.dataTransfer);
      uploadMany(this.pv.pid, list);
    });
  }

  async open(mid, params, { animate = true } = {}) {
    if (mid && mid === this.mid && this.editor) {
      this.editor.applyParams?.(params);
      return;
    }
    await this.flush();
    // overview -> file goes forward (from below), file -> overview back; file -> file follows the list order
    const prev = this.mid;
    const order = this.files.visibleOrder();
    const dir = !mid ? -1 : !prev ? 1 : Math.sign(order.indexOf(mid) - order.indexOf(prev)) || 1;
    const oldEl = (this.editor || this.dash)?.el;
    animate = animate && prev !== undefined && mid !== prev;
    this.editor?.destroy();
    this.editor = null;
    this.dash?.destroy();
    this.dash = null;
    this.mid = mid;
    this.el.classList.toggle('has-file', !!mid);  // phones show the list or the open file, not both
    this.files.setSelected(mid);
    if (!mid) {
      emit('editor-file', null);
      emit('save-state', { state: '', text: '' });
      this.dash = new Dashboard(this);
      swap(this.detail, oldEl, this.dash.el, dir, animate);
      return;
    }
    const item = state.mediaById.get(mid);
    if (!item) {
      toast('That file is not in this project anymore', { kind: 'warn' });
      go(links.project(this.pv.pid));
      return;
    }
    emit('editor-file', item.name);
    const E = item.modality === 'image' ? ImageEditor : Editor;
    this.editor = new E(this, item, params);
    swap(this.detail, oldEl, this.editor.el, dir, animate);
    await this.editor.init();
  }

  /** Go to the previous / next file in the current list order. */
  step(dir) {
    const order = this.files.visibleOrder();
    const i = order.indexOf(this.mid);
    const j = i < 0 ? 0 : i + dir;
    if (j >= 0 && j < order.length) go(links.file(this.pv.pid, order[j]));
  }

  hide() { this.editor?.pause?.(); }
  isDirty() { return this.editor?.isDirty?.(); }
  async flush() { await this.editor?.flush?.(); }

  destroy() {
    this.editor?.destroy();
    this.dash?.destroy();
    this.files.destroy();
  }
}
