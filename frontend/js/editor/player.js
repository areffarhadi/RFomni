// Wraps an <audio>/<video> element: seek, play ranges, and a smooth time signal.
import { clamp } from '../util.js';

export class Player extends EventTarget {
  constructor(el, duration) {
    super();
    this.el = el;
    this.duration = duration || 0;
    this.stopAt = null;
    this.rangeStart = null;
    this.raf = 0;
    const emit = () => this.emitTime();
    this.handlers = {
      play: () => { this.loop(); this.dispatchEvent(new Event('state')); },
      pause: () => { this.stopAt = null; cancelAnimationFrame(this.raf); this.emitTime(); this.dispatchEvent(new Event('state')); },
      seeked: emit, timeupdate: () => { if (this.el.paused) emit(); },
      loadedmetadata: () => { if (isFinite(this.el.duration) && this.el.duration > 0) this.duration = this.el.duration; emit(); },
      ratechange: () => this.dispatchEvent(new Event('state')),
    };
    for (const [k, f] of Object.entries(this.handlers)) el.addEventListener(k, f);
  }

  get t() { return this.el.currentTime || 0; }
  get playing() { return !this.el.paused && !this.el.ended; }

  loop() {
    cancelAnimationFrame(this.raf);
    const step = () => {
      if (this.stopAt != null && this.t >= this.stopAt) {
        const back = this.rangeStart;
        this.el.pause();
        if (back != null) this.el.currentTime = back;
        this.stopAt = null;
        return;
      }
      this.emitTime();
      if (!this.el.paused) this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }

  emitTime() { this.dispatchEvent(new CustomEvent('time', { detail: this.t })); }

  seek(t) {
    this.el.currentTime = clamp(t, 0, this.duration || Infinity);
    this.emitTime();
  }

  play() {
    this.stopAt = null;
    return this.el.play().catch(() => {});
  }

  pause() { this.el.pause(); }

  toggle() { this.playing ? this.pause() : this.play(); }

  /** Play [a, b] once, then jump back to a (handy for re-listening while transcribing). */
  playRange(a, b) {
    this.el.currentTime = a;
    this.el.play().then(() => { this.rangeStart = a; this.stopAt = b; }).catch(() => {});
  }

  set rate(r) { this.el.playbackRate = r; }
  get rate() { return this.el.playbackRate; }

  destroy() {
    cancelAnimationFrame(this.raf);
    for (const [k, f] of Object.entries(this.handlers)) this.el.removeEventListener(k, f);
    this.el.pause();
    this.el.removeAttribute('src');
    this.el.load();
  }
}
