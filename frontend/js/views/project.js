// Project shell: left rail + tabs (data / tasks / statistics / review / export).
import { h, ico } from '../util.js';
import { state, on, loadProject, isShared, canDownload } from '../state.js';
import { links } from '../nav.js';
import { DataView } from './data.js';
import { TasksView } from './tasks.js';
import { ReviewView } from './review.js';
import { ExportView } from './export.js';
import { StatsView } from './stats.js';
import { LiquidRail, enter, leave, nextFrame } from '../motion.js';

const TABS = [
  { id: 'data', label: 'Data', icon: 'database' },
  { id: 'tasks', label: 'Tasks', icon: 'tasks', owner: true },
  { id: 'stats', label: 'Statistics', icon: 'chart' },
  { id: 'review', label: 'Review', icon: 'review' },
  { id: 'export', label: 'Export', icon: 'download', download: true },
];

export class ProjectView {
  constructor(pid) {
    this.pid = pid;
    this.links = {};
    const tabs = TABS.filter(t => !(t.owner && isShared()) && !(t.download && !canDownload()));
    // "Projects" first: back to the list of all projects (not shown on shared links)
    const home = isShared() ? null : [
      h('a.rail-home', { href: links.home(), title: 'All projects' }, ico('folder', 19), h('span', 'Projects')),
      h('div.rail-sep')];
    this.tabInfo = {};
    this.rail = h('nav.rail', { 'aria-label': 'Project' }, home, tabs.map(t => {
      const color = `var(--tab-${t.id})`;
      const a = h('a', { href: t.id === 'data' ? links.project(pid) : links.tab(pid, t.id), title: t.label, style: { '--tc': color } },
        ico(t.icon, 19), h('span', t.label));
      this.links[t.id] = a;
      this.tabInfo[t.id] = { icon: t.icon, color };
      return a;
    }));
    this.liquid = new LiquidRail(this.rail);
    this.body = h('div.proj-body');
    this.el = h('div.project', this.rail, this.body);
    this.liquid.attachGlow(this.el);
    this.other = null;
    this.tab = null;
    this.shown = null;
    this.unsub = [on('project', () => this.updateBadges())];
  }

  async load() {
    await loadProject(this.pid);
    this.data = new DataView(this);
    this.body.append(this.data.el);
    this.updateBadges();
  }

  updateBadges() {
    const low = state.project?.stats?.low || 0;
    const a = this.links.review;
    a.querySelector('.count')?.remove();
    if (low) a.append(h('span.count', low > 99 ? '99+' : String(low)));
  }

  async update({ tab, mid, params }) {
    if (tab !== 'data' && !this.links[tab]) tab = 'stats';  // a tab this link may not open
    const prev = this.tab;
    this.tab = tab;
    for (const [id, a] of Object.entries(this.links)) {
      a.classList.toggle('on', id === tab);
      if (id === tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    }
    this.liquid.set(this.links[tab], this.tabInfo[tab]);
    if (prev != null && prev !== tab) {
      await nextFrame();              // the bead starts gliding before the next page is built
      if (this.tab !== tab) return;   // another tab was chosen meanwhile
    }
    // `shown` is the page on screen (it can differ from `prev` after quick clicks)
    const shown = this.shown;
    this.shown = tab;
    const moved = shown != null && shown !== tab;
    // the new page arrives from the side it sits on in the rail: further down = from below
    const order = Object.keys(this.links);
    const dir = Math.sign(order.indexOf(tab) - order.indexOf(shown)) || 1;
    if (tab === 'data') {
      this.links.data.href = mid ? links.file(this.pid, mid) : links.project(this.pid);
      this.dropOther(dir, moved);
      this.data.el.style.display = '';
      if (moved) enter(this.data.el, dir);
      await this.data.open(mid, params, { animate: !moved });
      return;
    }
    if (shown === 'data') {
      this.data.hide();
      leave(this.data.el, dir, () => { this.data.el.style.display = 'none'; });
    } else if (shown == null) {
      this.data.el.style.display = 'none';  // the project was opened on another tab
    }
    this.dropOther(dir, moved);
    const views = { tasks: TasksView, stats: StatsView, review: ReviewView, export: ExportView };
    this.other = new views[tab](this);
    this.body.append(this.other.el);
    if (moved) enter(this.other.el, dir);
  }

  /** Close the current non-data view; its page fades out while the next one comes in. */
  dropOther(dir, animate) {
    const o = this.other;
    if (!o) return;
    this.other = null;
    o.destroy?.();
    if (animate) leave(o.el, dir, () => o.el.remove()); else o.el.remove();
  }

  isDirty() { return this.data?.isDirty(); }
  flush() { return this.data?.flush(); }
  async beforeLeave() { await this.data?.flush(); }

  destroy() {
    this.liquid.destroy();
    this.unsub.forEach(f => f());
    this.data?.destroy();
    this.other?.destroy?.();
  }
}
