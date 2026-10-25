// Modals, menus, popovers, toasts.
import { h, ico, $ } from './util.js';

// ---- toasts ---------------------------------------------------------------
export function toast(message, { kind = 'info', timeout = 3800, action } = {}) {
  const host = $('#toasts');
  const el = h('div.toast.' + kind,
    ico({ ok: 'check', error: 'alert', warn: 'alert', info: 'info' }[kind] || 'info', 16),
    h('div.toast-msg', message),
    action && h('button.btn.sm.ghost', { onclick: () => { action.onClick(); close(); } }, action.label),
    h('button.icon-btn.sm', { onclick: () => close(), title: 'Dismiss' }, ico('x', 14)));
  host.append(el);
  requestAnimationFrame(() => el.classList.add('in'));
  let t = timeout ? setTimeout(close, timeout) : null;
  el.addEventListener('mouseenter', () => clearTimeout(t));
  el.addEventListener('mouseleave', () => { if (timeout) t = setTimeout(close, 1500); });
  function close() {
    el.classList.remove('in');
    setTimeout(() => el.remove(), 200);
  }
  return { close, el };
}

export function errorToast(e, prefix = '') {
  console.error(e);
  toast((prefix ? prefix + ': ' : '') + (e?.message || e), { kind: 'error', timeout: 6000 });
}

// ---- modal ----------------------------------------------------------------
let modalDepth = 0;
export function openModal({ title, subtitle, body, actions = [], width = 520, onClose, className = '' }) {
  const prevFocus = document.activeElement;
  const footer = actions.length ? h('div.modal-foot', actions.map(a => a instanceof Node ? a :
    h('button.btn' + (a.primary ? '.primary' : '') + (a.danger ? '.danger' : '') + (a.ghost ? '.ghost' : ''),
      { onclick: () => a.onClick?.(api), disabled: a.disabled, type: 'button', dataset: { act: a.id || '' } },
      a.icon && ico(a.icon, 15), a.label))) : null;
  const box = h('div.modal' + (className ? '.' + className : ''), { style: { width: `min(${width}px, calc(100vw - 32px))` }, role: 'dialog', 'aria-modal': 'true' },
    h('div.modal-head',
      h('div', h('h2', title), subtitle && h('div.modal-sub', subtitle)),
      h('button.icon-btn', { onclick: () => api.close(), title: 'Close (Esc)' }, ico('x', 18))),
    h('div.modal-body', body),
    footer);
  const back = h('div.modal-back', { onmousedown: (e) => { if (e.target === back) api.close(); } }, box);
  document.body.append(back);
  modalDepth++;
  requestAnimationFrame(() => back.classList.add('in'));
  const onKey = (e) => {
    if (e.key === 'Escape' && back === [...document.querySelectorAll('.modal-back')].pop()) {
      e.stopPropagation();
      api.close();
    }
  };
  document.addEventListener('keydown', onKey, true);
  const first = box.querySelector('[autofocus], input, textarea, select');
  if (first) setTimeout(() => first.focus(), 30);
  const api = {
    el: box,
    footer,
    closed: false,
    close() {
      if (api.closed) return;
      api.closed = true;
      modalDepth--;
      document.removeEventListener('keydown', onKey, true);
      back.classList.remove('in');
      setTimeout(() => back.remove(), 160);
      onClose?.();
      prevFocus?.focus?.();
    },
    button(id) { return footer?.querySelector(`[data-act="${id}"]`); },
  };
  return api;
}

export const modalOpen = () => modalDepth > 0;

export function confirmDialog(message, { title = 'Are you sure?', ok = 'Delete', danger = true } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal({
      title, width: 420, body: h('p.muted-p', message),
      onClose: () => { if (!done) resolve(false); },
      actions: [
        { label: 'Cancel', ghost: true, onClick: (a) => a.close() },
        { label: ok, primary: !danger, danger, onClick: (a) => { done = true; resolve(true); a.close(); } },
      ],
    });
    setTimeout(() => m.footer?.lastChild?.focus(), 40);
  });
}

export function promptDialog(label, { title = '', value = '', ok = 'Save', placeholder = '' } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const input = h('input.input', { value, placeholder, autofocus: true });
    const submit = (a) => { done = true; resolve(input.value.trim()); a.close(); };
    const m = openModal({
      title: title || label, width: 420,
      body: h('label.field', h('span', label), input),
      onClose: () => { if (!done) resolve(null); },
      actions: [{ label: 'Cancel', ghost: true, onClick: (a) => a.close() }, { label: ok, primary: true, onClick: submit }],
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(m); });
  });
}

// ---- popovers & menus -----------------------------------------------------
let openPop = null;

export function closePopover() {
  if (openPop) openPop.close();
}

/** Show `content` floating next to an element or a DOMRect. */
export function popover(anchor, content, { placement = 'bottom-start', onClose, className = '', offset = 6 } = {}) {
  closePopover();
  const el = h('div.popover' + (className ? '.' + className : ''), content);
  document.body.append(el);
  const r = anchor instanceof Element ? anchor.getBoundingClientRect() : anchor;
  const place = () => {
    const pw = el.offsetWidth, ph = el.offsetHeight;
    let x = placement.endsWith('end') ? r.right - pw : r.left;
    let y = placement.startsWith('top') ? r.top - ph - offset : r.bottom + offset;
    if (y + ph > innerHeight - 8) y = Math.max(8, r.top - ph - offset);
    if (y < 8) y = Math.max(8, Math.min(innerHeight - ph - 8, r.bottom + offset));
    x = Math.max(8, Math.min(x, innerWidth - pw - 8));
    el.style.left = x + 'px';
    el.style.top = y + 'px';
  };
  place();
  requestAnimationFrame(place);  // callers often fill the content right after this returns
  const onDown = (e) => {
    if (!el.contains(e.target) && !(anchor instanceof Element && anchor.contains(e.target))) api.close();
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); api.close(); } };
  setTimeout(() => {
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey, true);
  });
  const api = {
    el,
    close() {
      if (openPop !== api) return;
      openPop = null;
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
      el.remove();
      onClose?.();
    },
  };
  openPop = api;
  return api;
}

/** items: [{label, icon, onClick, danger, disabled, hint}] or 'sep' or {header} */
export function menu(anchor, items, opts = {}) {
  const list = h('div.menu', items.filter(Boolean).map(it => {
    if (it === 'sep') return h('div.menu-sep');
    if (it.header) return h('div.menu-head', it.header);
    return h('button.menu-item' + (it.danger ? '.danger' : '') + (it.active ? '.active' : ''), {
      disabled: it.disabled, type: 'button',
      onclick: () => { pop.close(); it.onClick?.(); },
    }, it.icon ? ico(it.icon, 15) : h('span.ico-spacer'), h('span.menu-label', it.label), it.hint && h('kbd', it.hint));
  }));
  const pop = popover(anchor, list, { placement: opts.placement || 'bottom-start', ...opts });
  return pop;
}
