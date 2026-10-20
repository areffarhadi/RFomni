// Project shell: left rail + tabs (data / tasks / statistics / review / export).
import { h, ico } from '../util.js';
import { state, on, loadProject, isShared, canDownload } from '../state.js';
import { links } from '../nav.js';
import { DataView } from './data.js';
import { TasksView } from './tasks.js';
import { ReviewView } from './review.js';
import { ExportView } from './export.js';
import { StatsView } from './stats.js';

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
    this.rail = h('nav.rail', home, tabs.map(t => {
      const a = h('a', { href: t.id === 'data' ? links.project(pid) : links.tab(pid, t.id), title: t.label },
        ico(t.icon, 19), h('span', t.label));
      this.links[t.id] = a;
      return a;
    }));
    this.body = h('div.proj-body');
    this.el = h('div.project', this.rail, this.body);
    this.other = null;
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
    if (low) a.append(h('span.count', low > 999 ? '999+' : String(low)));
  }

  async update({ tab, mid, params }) {
    for (const [id, a] of Object.entries(this.links)) a.classList.toggle('on', id === tab);
    if (tab === 'data') {
      this.links.data.href = mid ? links.file(this.pid, mid) : links.project(this.pid);
      if (this.other) { this.other.destroy?.(); this.other.el.remove(); this.other = null; }
      this.data.el.style.display = '';
      await this.data.open(mid, params);
      return;
    }
    this.data.hide();
    this.data.el.style.display = 'none';
    if (this.other) { this.other.destroy?.(); this.other.el.remove(); }
    const views = { tasks: TasksView, stats: StatsView, review: ReviewView, export: ExportView };
    const View = this.links[tab] ? views[tab] : StatsView;
    this.other = new View(this);
    this.body.append(this.other.el);
  }

  isDirty() { return this.data?.isDirty(); }
  flush() { return this.data?.flush(); }
  async beforeLeave() { await this.data?.flush(); }

  destroy() {
    this.unsub.forEach(f => f());
    this.data?.destroy();
    this.other?.destroy?.();
  }
}
