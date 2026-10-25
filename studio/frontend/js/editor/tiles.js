// Small async tile cache with a LIFO request queue (the latest view loads first).
export class TileStore {
  constructor(onLoad, { max = 6, limit = 500 } = {}) {
    this.cache = new Map();
    this.queue = [];
    this.inflight = 0;
    this.max = max;
    this.limit = limit;
    this.onLoad = onLoad;
  }

  /** Cached data or null; starts loading when missing. */
  get(key, loader) {
    const e = this.cache.get(key);
    if (e) {
      if (e.data !== undefined) {
        this.cache.delete(key);
        this.cache.set(key, e);
        return e.data;
      }
      return null;
    }
    this.cache.set(key, { loading: true });
    this.queue.push({ key, loader });
    if (this.queue.length > 80) {
      for (const old of this.queue.splice(0, this.queue.length - 80)) this.cache.delete(old.key);
    }
    this.pump();
    return null;
  }

  peek(key) {
    const e = this.cache.get(key);
    return e && e.data !== undefined ? e.data : null;
  }

  pump() {
    while (this.inflight < this.max && this.queue.length) {
      const { key, loader } = this.queue.pop();
      const entry = this.cache.get(key);
      if (!entry) continue;
      this.inflight++;
      loader().then((data) => { entry.data = data; }, () => { entry.data = null; })
        .finally(() => {
          this.inflight--;
          this.prune();
          this.onLoad();
          this.pump();
        });
    }
  }

  prune() {
    if (this.cache.size <= this.limit) return;
    for (const [k, e] of this.cache) {
      if (this.cache.size <= this.limit * 0.8) break;
      if (e.data !== undefined) this.cache.delete(k);
    }
  }

  clear(prefix = '') {
    for (const k of [...this.cache.keys()]) if (k.startsWith(prefix)) this.cache.delete(k);
    this.queue = this.queue.filter(q => !q.key.startsWith(prefix));
  }
}

export function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}
