// Left-hand file list: search, filters, folder groups, thumbnails, multi-select.
import { urls } from '../api.js';
import { h, ico, fmtDur, ls, modalityIcon, rafThrottle, clamp } from '../util.js';
import { state, on } from '../state.js';
import { go, links } from '../nav.js';
import { menu } from '../ui.js';
import { addDataMenuItems } from './upload.js';
import { openRunDialog } from './run.js';

export const STATUS = { new: 'Not annotated', annotated: 'Annotated', in_review: 'In review', reviewed: 'Reviewed' };
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export class FileList {
  constructor(dv) {
    this.dv = dv;
    this.pid = dv.pv.pid;
    this.q = '';
    this.mod = 'all';
    this.status = 'all';
    this.selected = null;
    this.checked = new Set();
    this.closed = new Set(ls.get('closed:' + this.pid, []));

    this.count = h('span.badge');
    const addBtn = h('button.icon-btn', { title: 'Add data', onclick: () => menu(addBtn, addDataMenuItems(this.pid)) }, ico('plus', 17));
    const head = h('div.fp-head',
      h('h2', 'Files', this.count),
      h('button.icon-btn', { title: 'Project overview', onclick: () => go(links.project(this.pid)) }, ico('grid', 16)),
      h('button.icon-btn', { title: 'Run tasks on files', onclick: () => openRunDialog({ mediaIds: this.checked.size ? [...this.checked] : null }) }, ico('sparkles', 16)),
      addBtn);
    const search = h('input.input', { placeholder: 'Search files', oninput: (e) => { this.q = e.target.value.toLowerCase(); this.render(); } });
    this.modChips = h('div.fp-chips', ['all', 'audio', 'video', 'image'].map(m =>
      h('span.chip' + (m === 'all' ? '.on' : ''), { dataset: { m }, onclick: () => { this.mod = m; this.render(); } },
        m === 'all' ? 'All' : [ico(modalityIcon(m), 12), m[0].toUpperCase() + m.slice(1)])));
    const statusSel = h('select.select.sm', { onchange: (e) => { this.status = e.target.value; this.render(); } },
      h('option', { value: 'all' }, 'Any status'),
      Object.entries(STATUS).map(([k, v]) => h('option', { value: k }, v)),
      h('option', { value: 'low' }, 'Has low-confidence segments'));
    this.list = h('div.fp-list', { tabindex: 0, onkeydown: (e) => this.onKey(e) });
    this.selbar = h('div.fp-selbar', { style: { display: 'none' } });
    const resize = h('div.files-resize', { onpointerdown: (e) => this.startResize(e, resize) });
    this.el = h('aside.files', head,
      h('div.fp-filters', h('div.fp-search', ico('search', 14), search), this.modChips, statusSel),
      this.list, this.selbar, resize);

    this.io = new IntersectionObserver((ents) => {
      for (const e of ents) {
        if (e.isIntersecting && e.target.dataset.thumb) {
          e.target.style.backgroundImage = `url("${e.target.dataset.thumb}")`;
          delete e.target.dataset.thumb;
          this.io.unobserve(e.target);
        }
      }
    }, { root: this.list, rootMargin: '200px' });
    this.renderSoon = rafThrottle(() => this.render());
    this.unsub = [on('media', this.renderSoon)];
    this.render();
  }

  filtered() {
    let items = state.media.slice();
    if (this.mod !== 'all') items = items.filter(m => m.modality === this.mod);
    if (this.status === 'low') items = items.filter(m => (m.summary?.n_low || 0) > 0);
    else if (this.status !== 'all') items = items.filter(m => (m.status || 'new') === this.status);
    if (this.q) items = items.filter(m => (m.rel_path || m.name).toLowerCase().includes(this.q));
    items.sort((a, b) => collator.compare(a.rel_path || a.name, b.rel_path || b.name));
    return items;
  }

  visibleOrder() {
    return this.filtered().map(m => m.id);
  }

  render() {
    for (const c of this.modChips.children) c.classList.toggle('on', c.dataset.m === this.mod);
    const items = this.filtered();
    this.count.textContent = items.length === state.media.length ? String(items.length) : `${items.length}/${state.media.length}`;
    const groups = new Map();
    for (const m of items) {
      const rel = m.rel_path || m.name;
      const dir = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '';
      if (!groups.has(dir)) groups.set(dir, []);
      groups.get(dir).push(m);
    }
    const nodes = [];
    const showFolders = groups.size > 1 || (groups.size === 1 && !groups.has(''));
    for (const [dir, ms] of groups) {
      const closed = this.closed.has(dir);
      if (showFolders) {
        nodes.push(h('div.fp-folder' + (closed ? '.closed' : ''), {
          title: dir || '(top level)',
          onclick: () => {
            closed ? this.closed.delete(dir) : this.closed.add(dir);
            ls.set('closed:' + this.pid, [...this.closed]);
            this.render();
          },
        }, ico('chev-d', 13), ico('folder', 13), h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis' } }, dir || '(top level)'), h('span.n', ms.length)));
      }
      if (!closed) {
        const rows = ms.slice(0, 3000).map(m => this.row(m));
        nodes.push(h('div.fp-group', rows));
      }
    }
    if (!items.length) {
      nodes.push(h('div.empty', { style: { padding: '30px 10px' } },
        state.media.length ? 'No files match the filters' : h('div', 'No files yet.', h('br'), 'Use + or drop files here.')));
    }
    this.list.classList.toggle('selecting', this.checked.size > 0);
    this.list.replaceChildren(...nodes);
    this.renderSelbar();
  }

  row(m) {
    const thumb = h('div.fp-thumb.' + m.modality, m.modality === 'audio' ? ico('audio', 16) : null);
    if (m.modality !== 'audio') {
      thumb.dataset.thumb = urls.thumb(this.pid, m.id);
      this.io.observe(thumb);
    }
    const st = m.status || 'new';
    const low = m.summary?.n_low || 0;
    const cb = h('input.fp-check', {
      type: 'checkbox', checked: this.checked.has(m.id),
      onclick: (e) => {
        e.stopPropagation();
        e.target.checked ? this.checked.add(m.id) : this.checked.delete(m.id);
        this.list.classList.toggle('selecting', this.checked.size > 0);
        this.renderSelbar();
      },
    });
    return h('div.fp-row' + (m.id === this.selected ? '.sel' : ''), {
      dataset: { id: m.id }, title: m.rel_path,
      onclick: (e) => {
        if (e.shiftKey || e.metaKey || e.ctrlKey) { cb.click(); return; }
        go(links.file(this.pid, m.id));
      },
    }, cb, thumb,
    h('div.fp-meta',
      h('div.fp-name', m.name),
      h('div.fp-sub', h('i.st.' + st), STATUS[st],
        m.duration ? h('span', '· ' + fmtDur(m.duration)) : (m.width ? h('span', `· ${m.width}×${m.height}`) : null),
        low ? h('span.fp-low', { title: 'Low-confidence segments' }, '· ', ico('flag', 10), low) : null)));
  }

  renderSelbar() {
    const n = this.checked.size;
    this.selbar.style.display = n ? '' : 'none';
    if (!n) return;
    this.selbar.replaceChildren(h('span.grow', `${n} selected`),
      h('button.btn.sm', { onclick: () => openRunDialog({ mediaIds: [...this.checked] }) }, ico('sparkles', 13), 'Run tasks'),
      h('button.btn.sm.ghost', { onclick: () => { this.checked.clear(); this.render(); } }, 'Clear'));
  }

  setSelected(mid) {
    this.selected = mid;
    for (const r of this.list.querySelectorAll('.fp-row')) r.classList.toggle('sel', r.dataset.id === mid);
    const el = mid && this.list.querySelector(`.fp-row[data-id="${mid}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  onKey(e) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const order = this.visibleOrder();
    const i = order.indexOf(this.selected);
    const j = clamp(i + (e.key === 'ArrowDown' ? 1 : -1), 0, order.length - 1);
    if (order[j] && order[j] !== this.selected) go(links.file(this.pid, order[j]));
  }

  startResize(e, el) {
    e.preventDefault();
    el.classList.add('drag');
    const data = this.dv.el;
    const x0 = e.clientX, w0 = this.el.offsetWidth;
    const move = (ev) => {
      const w = clamp(w0 + ev.clientX - x0, 200, 560);
      data.style.setProperty('--files-w', w + 'px');
      window.dispatchEvent(new Event('resize'));
    };
    const up = () => {
      el.classList.remove('drag');
      ls.set('filesW', this.el.offsetWidth);
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  }

  destroy() {
    this.unsub.forEach(f => f());
    this.io.disconnect();
  }
}
