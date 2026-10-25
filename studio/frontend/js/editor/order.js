// Project-wide tier order. One list of keys (task id, or tier id for imported / blank tiers)
// stored on the project, so reordering channels in one file reorders them in every file.
import { state, updateProject } from '../state.js';

export const tierKey = (t) => t.task_id || t.id;

export function projectOrder() {
  const p = state.project || {};
  return p.tier_order?.length ? p.tier_order : (p.tasks || []);
}

export function sortTiers(tiers) {
  const order = projectOrder();
  const rank = (t) => {
    const i = order.indexOf(tierKey(t));
    return i < 0 ? 1e6 + tiers.indexOf(t) : i;
  };
  return tiers.slice().sort((a, b) => rank(a) - rank(b));
}

/** Keep task keys in the order given, then any other keys (imported tiers) from the old order. */
export function mergeOrder(taskIds, old = []) {
  return [...taskIds, ...old.filter(k => !taskIds.includes(k))];
}

/** Move `key` before / after `targetKey` in the project order and save it. */
export async function moveTierKey(key, targetKey, after, fileKeys = []) {
  let order = projectOrder().slice();
  for (const k of fileKeys) if (!order.includes(k)) order.push(k);
  order = order.filter(k => k !== key);
  let i = order.indexOf(targetKey);
  if (i < 0) i = order.length - 1;
  order.splice(after ? i + 1 : i, 0, key);
  const idx = (k) => { const j = order.indexOf(k); return j < 0 ? 1e6 : j; };
  const tasks = (state.project.tasks || []).slice().sort((a, b) => idx(a) - idx(b));
  await updateProject({ tier_order: order, tasks });
}
