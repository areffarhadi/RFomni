// Export tab: download a ZIP with the chosen formats for the whole project.
import { urls } from '../api.js';
import { h, ico, ls, download } from '../util.js';
import { state, projectTasks } from '../state.js';

const FORMATS = [
  { id: 'json', name: 'Omni Studio JSON', desc: 'Native format: every tier and segment with confidence, source and review state. Re-importable.' },
  { id: 'eaf', name: 'ELAN (.eaf)', desc: 'One tier per task, linked to the media file. Opens directly in ELAN.' },
  { id: 'textgrid', name: 'Praat TextGrid', desc: 'Interval tiers for Praat, MFA-style word and phone tiers included.' },
  { id: 'srt', name: 'SRT subtitles', desc: 'Transcript (or caption) tier with speaker labels.' },
  { id: 'vtt', name: 'WebVTT', desc: 'Same as SRT for web players, with <v speaker> tags.' },
  { id: 'rttm', name: 'RTTM', desc: 'Diarization tier in the standard NIST format for DER scoring.' },
  { id: 'csv', name: 'CSV (all segments)', desc: 'One row per segment across all files: tier, times, value, confidence.' },
  { id: 'coco', name: 'COCO JSON (images)', desc: 'All boxes and pose keypoints of the images in COCO format (pixel coordinates).' },
  { id: 'manifest', name: 'Training manifest (JSONL)', desc: 'One line per utterance with all overlapping labels and word timings, ready for fine-tuning.' },
];

export class ExportView {
  constructor(pv) {
    this.pv = pv;
    const pid = state.project.id;
    this.sel = new Set(ls.get('exportFormats', ['json', 'eaf', 'csv', 'manifest']));
    this.scope = 'annotated';
    this.anchor = 'asr';
    const grid = h('div.fmt-grid', FORMATS.map(f => {
      const cb = h('input', { type: 'checkbox', checked: this.sel.has(f.id) });
      const card = h('label.fmt-card' + (this.sel.has(f.id) ? '.on' : ''), cb, h('div', h('b', f.name), h('span', f.desc)));
      cb.addEventListener('change', () => {
        cb.checked ? this.sel.add(f.id) : this.sel.delete(f.id);
        card.classList.toggle('on', cb.checked);
        ls.set('exportFormats', [...this.sel]);
        this.update();
      });
      return card;
    }));
    const s = state.project.stats || {};
    const scopes = [['annotated', `Annotated files (${s.annotated || 0})`], ['reviewed', `Reviewed files only (${s.reviewed || 0})`], ['all', `All files (${s.files || 0})`]];
    const scopeSel = h('select.select', { style: { width: '240px' }, onchange: (e) => { this.scope = e.target.value; } },
      scopes.map(([k, v]) => h('option', { value: k }, v)));
    const anchors = projectTasks().filter(t => t.granularity === 'segment' || t.granularity === 'window');
    if (!anchors.some(t => t.id === 'asr')) this.anchor = anchors[0]?.id || 'asr';
    const anchorSel = h('select.select', { style: { width: '240px' }, onchange: (e) => { this.anchor = e.target.value; } },
      anchors.length ? anchors.map(t => h('option', { value: t.id, selected: t.id === this.anchor }, t.title)) : h('option', { value: 'asr' }, 'Speech recognition'));
    this.btn = h('button.btn.primary.lg', { onclick: () => download(urls.exportZip(pid, { formats: [...this.sel].join(','), scope: this.scope, anchor: this.anchor })) },
      ico('download', 16), 'Download ZIP');
    this.el = h('div.page', h('div.page-inner',
      h('div.page-head', h('div', h('h1', 'Export'),
        h('p', 'Download annotations for the whole project. Single files can also be exported from the editor menu (⋯).'))),
      h('div.section', h('div.section-head', h('h3', 'Formats')), h('div.section-body', grid)),
      h('div.section', h('div.section-head', h('h3', 'Options')), h('div.section-body',
        h('div.row', { style: { gap: '30px', alignItems: 'flex-start', flexWrap: 'wrap' } },
          h('label.field', h('span', 'Files'), scopeSel),
          h('label.field', h('span', 'Manifest: one line per segment of'), anchorSel, h('span.hint', 'Other tiers are attached by time overlap.'))),
        this.btn))));
    this.update();
  }

  update() { this.btn.disabled = !this.sel.size; }
  destroy() {}
}
