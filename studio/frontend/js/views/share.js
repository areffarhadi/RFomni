// Share dialog (owner): create links that open this project for other people, view-only or with editing.
import { api } from '../api.js';
import { h, ico } from '../util.js';
import { state, projectTasks } from '../state.js';
import { openModal, toast, errorToast, confirmDialog } from '../ui.js';

const EXPIRY = [[0, 'Never expires'], [1, 'Expires in 1 day'], [7, 'Expires in 7 days'], [30, 'Expires in 30 days']];

function ago(iso) {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('Link copied', { kind: 'ok', timeout: 1600 }); }
  catch { window.prompt('Copy this link', text); }
}

export async function openShareDialog() {
  const p = state.project;
  if (!p) return;
  let data;
  try { data = await api.shares(p.id); } catch (e) { errorToast(e); return; }

  // ---- new link form
  const draft = { label: '', access: 'view', tasks: [], allow_download: false, expires_days: 0, note: '' };
  const name = h('input.input', { placeholder: 'Who is it for? e.g. Reviewer Sara', maxLength: 60, oninput: (e) => { draft.label = e.target.value; } });
  const accessCtl = h('div.seg-ctl', [['view', 'eye', 'View only'], ['edit', 'edit', 'Can review & edit labels']].map(([k, i, v]) =>
    h('button', { dataset: { v: k }, type: 'button', onclick: () => { draft.access = k; paintForm(); } }, ico(i, 13), v)));
  const tasks = projectTasks();
  const tierChips = h('div.share-tiers');
  const dl = h('label.switch', h('input', { type: 'checkbox', onchange: (e) => { draft.allow_download = e.target.checked; } }), h('span'));
  const exp = h('select.select.sm', { style: { width: 'auto' }, onchange: (e) => { draft.expires_days = +e.target.value; } },
    EXPIRY.map(([d, t]) => h('option', { value: d }, t)));
  const note = h('input.input', { placeholder: 'Message shown to them (optional)', maxLength: 300, oninput: (e) => { draft.note = e.target.value; } });
  const createBtn = h('button.btn.primary', { type: 'button', onclick: create }, ico('link', 14), 'Create link');

  function paintForm() {
    for (const b of accessCtl.children) b.classList.toggle('on', b.dataset.v === draft.access);
    const all = !draft.tasks.length;
    tierChips.replaceChildren(
      h('span.chip' + (all ? '.on' : ''), { onclick: () => { draft.tasks = []; paintForm(); } }, all ? ico('check', 11) : null, 'All tiers'),
      tasks.map(t => {
        const on = draft.tasks.includes(t.id);
        return h('span.chip' + (on ? '.on' : ''), { style: { '--c': t.color }, title: t.title,
          onclick: () => { draft.tasks = on ? draft.tasks.filter(x => x !== t.id) : [...draft.tasks, t.id]; paintForm(); } },
        on ? ico('check', 11) : h('i.dot'), t.name);
      }));
  }

  const form = h('div.share-form',
    h('div.share-grid',
      h('label.field', h('span', 'Name'), name),
      h('div.field', h('span', 'Access'), accessCtl),
      h('div.field.wide', h('span', 'What they see'), tierChips),
      h('label.field.wide', h('span', 'Note'), note),
      h('div.row.wide', { style: { gap: '18px', flexWrap: 'wrap' } },
        h('label.share-dl', dl, 'Allow downloading the annotations'),
        exp, h('div.grow'), createBtn)));

  // ---- reach + links
  const reach = h('div.share-reach');
  const list = h('div.share-list');

  function base() { return data.server.bases?.[0] || data.server.local_base; }

  function paintReach() {
    const sv = data.server, tn = sv.tunnel || {};
    const lan = sv.bases?.length ? sv.bases.join(' or ') : null;
    reach.replaceChildren(
      h('div.reach-row', ico('wifi', 16), h('div.grow',
        h('b', 'Same network'), h('div.muted', lan ? `Anyone on your Wi-Fi or office network: ${lan}` : 'No network address found; links work on this computer only.')),
        h('span.badge' + (sv.running ? '.ok' : ''), sv.running ? 'on' : 'starts with the first link')),
      h('div.reach-row', ico('globe', 16), h('div.grow',
        h('b', 'Internet'),
        tn.running && tn.url ? h('div.muted', h('span.mono', tn.url), ' · works while Omni Studio runs; the address changes each time you turn it on.')
          : tn.available ? h('div.muted', 'A free Cloudflare tunnel gives your links an https address that works from anywhere.')
            : h('div.muted', 'Install cloudflared to share over the internet (macOS: brew install cloudflared), then reopen this window.')),
      tn.running ? h('button.btn.sm', { type: 'button', onclick: async () => { data.server = await api.stopTunnel().catch(errorToast) || data.server; paintAll(); } }, 'Turn off')
        : tn.available ? h('button.btn.sm', { type: 'button', disabled: !sv.running, title: sv.running ? '' : 'Create a link first', onclick: (e) => startTunnel(e.currentTarget) }, ico('globe', 13), 'Turn on') : null),
      sv.error ? h('div.notice', ico('alert', 15), h('div', sv.error)) : null);
  }

  async function startTunnel(btn) {
    btn.disabled = true;
    btn.replaceChildren(h('div.spinner', { style: { width: '12px', height: '12px' } }), 'Connecting…');
    try { data.server = await api.startTunnel(); toast('Internet link is on', { kind: 'ok' }); }
    catch (e) { errorToast(e); }
    paintAll();
  }

  function linkRow(s) {
    const url = `${base()}/s/${s.token}`;
    const net = data.server.tunnel?.running && data.server.tunnel.url ? `${data.server.tunnel.url}/s/${s.token}` : null;
    const tiers = s.tasks.length ? s.tasks.map(id => state.taskById.get(id)?.name || id).join(', ') : 'all tiers';
    const status = !s.active ? h('span.badge' + (s.revoked ? '' : '.warn'), s.revoked ? 'turned off' : 'expired') : null;
    return h('div.share-item' + (s.active ? '' : '.off'),
      h('div.share-item-top',
        h('span.share-ico', ico(s.access === 'edit' ? 'edit' : 'eye', 15)),
        h('div.grow',
          h('div.share-name', s.label, ' ', status),
          h('div.muted.share-meta', [
            s.access === 'edit' ? 'can edit' : 'view only', tiers,
            s.allow_download ? 'downloads on' : null,
            s.expires_at ? `until ${new Date(s.expires_at).toLocaleDateString()}` : null,
            `opened ${s.views || 0}× · last ${ago(s.last_seen)}`,
            s.access === 'edit' ? `${s.edits || 0} edits` : null,
          ].filter(Boolean).join(' · '))),
        h('button.icon-btn.sm', { type: 'button', title: s.active ? 'Turn this link off' : 'Turn this link on again',
          'aria-label': s.active ? 'Turn this link off' : 'Turn this link on again',
          onclick: () => update(s, { revoked: s.active }) }, ico(s.active ? 'lock' : 'repeat', 14)),
        h('button.icon-btn.sm', { type: 'button', title: 'Delete link', 'aria-label': 'Delete link', onclick: () => remove(s) }, ico('trash', 14))),
      s.active ? h('div.share-urls',
        h('div.share-url', ico('wifi', 13), h('input.input.sm.mono', { readOnly: true, value: url, 'aria-label': 'Link on the same network', onfocus: (e) => e.target.select() }),
          h('button.btn.sm', { type: 'button', onclick: () => copy(url) }, ico('copy', 13), 'Copy'),
          h('button.btn.sm.ghost', { type: 'button', title: 'Open the link yourself to see what they see', onclick: () => window.open(`${data.server.local_base}/s/${s.token}`, '_blank') }, 'Preview')),
        net ? h('div.share-url', ico('globe', 13), h('input.input.sm.mono', { readOnly: true, value: net, 'aria-label': 'Internet link', onfocus: (e) => e.target.select() }),
          h('button.btn.sm', { type: 'button', onclick: () => copy(net) }, ico('copy', 13), 'Copy')) : null) : null);
  }

  function paintList() {
    const items = data.shares.slice().sort((a, b) => (b.active - a.active) || b.created_at.localeCompare(a.created_at));
    list.replaceChildren(items.length ? items.map(linkRow)
      : h('div.share-empty', 'No links yet. Anyone with a link can open this project in a browser, nothing to install.'));
  }

  function paintAll() { paintReach(); paintList(); }

  async function create() {
    createBtn.disabled = true;
    try {
      const r = await api.createShare(p.id, { ...draft, label: draft.label.trim() || (draft.access === 'edit' ? 'Reviewer' : 'Viewer') });
      data.shares.push(r.share);
      data.server = r.server;
      name.value = ''; note.value = ''; draft.label = ''; draft.note = '';
      paintAll();
      copy(`${base()}/s/${r.share.token}`);
    } catch (e) { errorToast(e, 'Could not create the link'); }
    createBtn.disabled = false;
  }

  async function update(s, patch) {
    try {
      const r = await api.updateShare(s.id, patch);
      Object.assign(s, r.share);
      data.server = r.server;
      paintAll();
    } catch (e) { errorToast(e); }
  }

  async function remove(s) {
    if (!await confirmDialog(`Delete the link "${s.label}"? Anyone using it loses access right away.`, { title: 'Delete link', ok: 'Delete' })) return;
    try {
      const r = await api.deleteShare(s.id);
      data.shares = data.shares.filter(x => x.id !== s.id);
      data.server = r.server;
      paintAll();
    } catch (e) { errorToast(e); }
  }

  paintForm();
  paintAll();
  openModal({
    title: `Share “${p.name}”`, width: 720, className: 'share-modal',
    subtitle: 'People open the project in their browser, nothing to install. They only see this project; view-only links cannot change anything.',
    body: h('div',
      h('div.share-section', h('h3', 'New link'), form),
      h('div.share-section', h('h3', 'Links'), list),
      h('div.share-section', h('h3', 'Where links work'), reach)),
    actions: [{ label: 'Done', primary: true, onClick: (a) => a.close() }],
  });
}
