// Hash routing helpers.
export function go(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent('hashchange'));
  else location.hash = hash;
}

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  return { parts: path.split('/').filter(Boolean).map(decodeURIComponent), params: new URLSearchParams(query || '') };
}

export const links = {
  home: () => '#/',
  project: (pid) => `#/p/${encodeURIComponent(pid)}`,
  file: (pid, mid, params) => `#/p/${encodeURIComponent(pid)}/f/${encodeURIComponent(mid)}${params ? '?' + new URLSearchParams(params) : ''}`,
  tab: (pid, tab) => `#/p/${encodeURIComponent(pid)}/${tab}`,
};
