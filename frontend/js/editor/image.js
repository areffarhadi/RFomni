// Image editor: the image on the left, one field per task (caption, tags, labels…) on the right.
import { urls } from '../api.js';
import { h, ico, isTyping } from '../util.js';
import { state } from '../state.js';
import { modalOpen } from '../ui.js';
import { openRunDialog } from '../views/run.js';
import { BaseEditor } from './base.js';
import { tierFromTask } from './doc.js';
import { valueEditor, srcBadge } from './inspector.js';
import { confBar } from '../views/review.js';
import { sortTiers } from './order.js';

export class ImageEditor extends BaseEditor {
  constructor(dv, media, params) {
    super(dv, media, params);
    this.el.classList.add('image');
    this.onKey = this.onKey.bind(this);
  }

  async init() {
    await this.loadDoc();
    const m = this.media;
    const img = h('img', { src: urls.file(this.pid, m.id), alt: m.name, draggable: false });
    this.stage = h('div.img-stage', { onclick: () => this.stage.classList.toggle('zoom') }, img,
      h('div.img-info', `${m.width}×${m.height} · ${m.format || ''}`));
    this.fields = h('div.img-fields');
    this.el.replaceChildren(this.buildHeader(), h('div.img-ed', this.stage, this.fields));
    this.renderFields();
    document.addEventListener('keydown', this.onKey);
  }

  renderFields() {
    const doc = this.doc;
    const present = new Set(doc.tiers.map(t => t.task_id || t.id));
    const suggested = (state.project.tasks || []).map(id => state.taskById.get(id))
      .filter(t => t && t.modalities.includes('image') && !present.has(t.id));
    const cards = sortTiers(doc.tiers).map(t => this.field(t));
    if (!cards.length) {
      cards.push(h('div.empty', { style: { padding: '24px 10px' } }, ico('image', 28), h('h3', 'No annotations yet'),
        h('div', 'Run the project tasks on this image, or fill in a field below by hand.'),
        h('button.btn.primary', { onclick: () => openRunDialog({ mediaIds: [this.media.id] }) }, ico('sparkles', 14), 'Run tasks')));
    }
    if (suggested.length) {
      cards.push(h('div.menu-head', { style: { marginTop: '6px' } }, 'Not annotated yet'));
      for (const t of suggested) {
        cards.push(h('button.btn', { style: { justifyContent: 'flex-start' }, onclick: () => this.addTier(t) },
          ico('plus', 14), h('span.tchip', { style: { '--c': t.color } }, t.name), h('span.muted', t.title)));
      }
    }
    this.fields.replaceChildren(...cards);
  }

  field(t) {
    const s = this.doc.list(t.id)[0];
    const set = (v, o) => {
      this.localEdit = true;
      if (s) {
        const p = { value: v, reviewed: true };
        if (s.source !== 'human') { p.source = 'human'; p.meta = { ...(s.meta || {}), model_source: s.source, model_value: s.meta?.model_value ?? s.value }; }
        this.doc.transact('Edit value', (tx) => tx.update(s.id, p), { merge: o?.merge });
      } else {
        this.doc.transact('Edit value', (tx) => tx.add({ tier_id: t.id, start: null, end: null, value: v, reviewed: true }));
      }
      this.localEdit = false;
    };
    const rev = s && h('button.icon-btn.sm' + (s.reviewed ? '.active' : ''), {
      title: s.reviewed ? 'Reviewed' : 'Mark reviewed',
      onclick: () => this.doc.transact('Review', (tx) => tx.update(s.id, { reviewed: !s.reviewed })),
    }, ico('check', 14));
    return h('div.file-field',
      h('div.ff-head', h('b', { style: { '--c': t.color } }, h('i'), t.title || t.name), h('div.grow'),
        s?.confidence != null ? confBar(s.confidence, this.lowThr) : null, s ? srcBadge(s.source) : null, rev),
      valueEditor(t, s?.value ?? (t.output === 'tags' ? [] : ''), set, { doc: this.doc }),
      s?.meta?.model_value != null ? h('div.muted', { style: { fontSize: '12px' } }, 'Model said: ', String(s.meta.model_value)) : null);
  }

  addTier(task) {
    this.doc.transact('Add tier', (tx) => tx.setTiers([...this.doc.tiers, tierFromTask(task)]));
  }

  afterChange() {
    const focused = this.fields.contains(document.activeElement) && /INPUT|TEXTAREA/.test(document.activeElement.tagName);
    if (this.localEdit && focused) return;
    this.renderFields();
  }

  afterReload() { this.renderFields(); }

  onKey(e) {
    if (modalOpen() || isTyping(e) || !this.el.isConnected || !this.el.offsetParent) return;
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? this.redo() : this.undo(); }
    else if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); this.flush(); }
    else if (!mod && e.key === '[') this.dv.step(-1);
    else if (!mod && e.key === ']') this.dv.step(1);
  }

  destroy() {
    super.destroy();
    document.removeEventListener('keydown', this.onKey);
  }
}
