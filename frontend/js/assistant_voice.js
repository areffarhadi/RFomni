// Voice for the assistant panel. The same voice agent as the chat page (Pipecat over WebRTC),
// opened in its "studio" mode: it checks the speaker, transcribes, hands the text to the
// studio assistant and speaks the reply. Signalling goes through /api/voice on this server.
import { h, ico } from './util.js';
import { errorToast } from './ui.js';

const RTVI = 'rtvi-ai';
const id = () => crypto.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

async function signal(path, method, body) {
  const r = await fetch(`/api/voice${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`Voice ${path} failed (${r.status}): ${await r.text().catch(() => r.statusText)}`);
  return r.json();
}

function iceDone(pc, ms = 2500) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener('icegatheringstatechange', check); resolve(); };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    pc.addEventListener('icegatheringstatechange', check);
    setTimeout(done, ms);
  });
}

export class AssistantVoice {
  constructor(panel) {
    this.panel = panel;
    this.pc = null;
    this.dc = null;
    this.stream = null;
    this.audio = null;
    this.gate = null;  // last agigo-gate status
    this.statusEl = h('span.asst-voice-status', 'Off');
    this.level = h('i');
    this.speakerEl = h('span.asst-voice-speaker');
    this.gateBtn = h('button.asst-btn.sm', { hidden: true, onclick: () => this.toggleGate() });
    panel.voiceEl.replaceChildren(ico('mic', 13), this.statusEl, h('span.lvl', this.level), this.speakerEl, this.gateBtn);
  }

  get live() { return !!this.pc; }

  toggle() { return this.live ? this.stop() : this.start(); }

  async start() {
    const { panel } = this;
    panel.micBtn.disabled = true;
    this.setStatus('Connecting…');
    panel.voiceEl.hidden = false;
    try {
      // the mic first: if the browser refuses it, nothing is opened on the agent
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const start = await signal('/start', 'POST', { createDailyRoom: false, enableDefaultIceServers: true, transport: 'webrtc', body: { mode: 'studio' } });
      const sid = start.sessionId || start.session_id;
      const pc = this.pc = new RTCPeerConnection({ iceServers: start.iceConfig?.iceServers || [{ urls: ['stun:stun.l.google.com:19302'] }] });
      this.audio = Object.assign(document.createElement('audio'), { autoplay: true, playsInline: true });
      pc.ontrack = (ev) => {
        if (ev.track.kind !== 'audio') return;
        this.audio.srcObject = ev.streams[0] || new MediaStream([ev.track]);
        this.audio.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (['failed', 'closed'].includes(pc.connectionState)) this.stop('Disconnected');
      };
      this.dc = pc.createDataChannel('chat', { ordered: true });
      this.dc.onopen = () => {
        this.send({ type: 'client-ready', data: { version: '1.0.0', about: { library: 'omni-studio', library_version: '1.0.0' } } });
        this.send({ type: 'client-message', data: { t: 'set-mode', d: { mode: 'studio' } } });
        this.setStatus('Listening…');
      };
      this.dc.onmessage = (ev) => this.onMessage(ev.data);
      for (const t of this.stream.getTracks()) pc.addTrack(t, this.stream);
      if (!pc.getTransceivers().some(t => t.receiver.track?.kind === 'audio')) pc.addTransceiver('audio', { direction: 'sendrecv' });
      await pc.setLocalDescription(await pc.createOffer());
      await iceDone(pc);
      const answer = await signal(`/sessions/${sid}/api/offer`, 'POST', {
        sdp: pc.localDescription.sdp, type: pc.localDescription.type, pc_id: null, restart_pc: false, requestData: { mode: 'studio' },
      });
      const pcId = answer.pc_id || answer.pcId;
      await pc.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
      pc.onicecandidate = (ev) => {
        if (!ev.candidate) return;
        signal(`/sessions/${sid}/api/offer`, 'PATCH', {
          pc_id: pcId, candidates: [{ candidate: ev.candidate.candidate, sdp_mid: ev.candidate.sdpMid, sdp_mline_index: ev.candidate.sdpMLineIndex }],
        }).catch(() => {});
      };
      this.meter();
      panel.micBtn.classList.add('live');
      panel.micBtn.title = 'Stop talking to the assistant';
    } catch (e) {
      this.stop('Off');
      errorToast(e, window.isSecureContext ? 'Voice could not start' : 'The microphone needs https or 127.0.0.1');
    } finally {
      panel.micBtn.disabled = false;
    }
  }

  stop(status = 'Off') {
    try { this.send({ type: 'disconnect-bot', data: {} }); } catch { /* closing anyway */ }
    try { this.dc?.close(); } catch { /* closing anyway */ }
    try { this.pc?.close(); } catch { /* closing anyway */ }
    this.stream?.getTracks().forEach(t => t.stop());
    this.meterCtx?.close().catch(() => {});
    if (this.audio) { this.audio.pause(); this.audio.srcObject = null; }
    this.pc = this.dc = this.stream = this.audio = this.meterCtx = null;
    this.panel.micBtn.classList.remove('live');
    this.panel.micBtn.title = 'Talk to the assistant';
    this.setStatus(status);
    this.panel.voiceEl.hidden = true;
  }

  /** Stop button: cut the spoken reply short; the agent keeps listening. */
  interrupt() {
    if (this.audio) this.audio.muted = true;
  }

  send(msg) {
    if (this.dc?.readyState === 'open') this.dc.send(JSON.stringify({ label: RTVI, id: id(), ...msg }));
  }

  setStatus(text) {
    this.statusEl.textContent = text;
    const live = !['Off', 'Disconnected'].includes(text);
    this.panel.setOnline(live ? text.replace('…', '') : 'Online', live);
  }

  onMessage(raw) {
    let m;
    try { m = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { return; }
    if (!m || m.label !== RTVI) return;
    const d = m.data || {};
    switch (m.type) {
      case 'user-transcription':
        if (d.text && d.final !== false) { this.panel.add('user.voice', d.text); this.panel.thinking(true); }
        break;
      case 'bot-started-speaking':
      case 'bot-tts-started':
        if (this.audio) this.audio.muted = false;
        this.setStatus('Speaking…');
        break;
      case 'bot-stopped-speaking':
      case 'bot-tts-stopped':
        this.panel.thinking(false);
        this.setStatus('Listening…');
        break;
      case 'server-message':
        this.onServer(d);
        break;
      case 'error':
      case 'error-response':
        this.panel.thinking(false);
        this.panel.add('error', d.error || 'Voice error');
        break;
    }
  }

  onServer(d) {
    if (d.type === 'agigo-studio') {
      this.panel.thinking(false);
      this.panel.apply(d.reply);
    } else if (d.event === 'lang-skipped') {
      this.panel.thinking(false);
    } else if (d.type === 'agigo-gate' || 'enrolled' in d) {
      this.gate = d;
      this.showGate(d);
    } else if (d.type === 'agigo-error') {
      this.panel.add('error', d.error);
    }
  }

  /** Spoken approvals count only when the speaker check is on and passed for that turn. */
  showGate(g) {
    if (!g.available) {
      this.speakerEl.textContent = 'Speaker check unavailable · approve with the button';
      this.gateBtn.hidden = true;
      return;
    }
    if (g.event === 'gate-rejected') this.panel.add('assistant', 'I heard a voice that is not the registered one, so I ignored it.');
    if (!g.enrolled) {
      this.speakerEl.textContent = g.autoActive ? 'Learning your voice…' : 'Voice not registered';
      this.gateBtn.hidden = !!g.autoSupported;
      this.gateBtn.textContent = 'Register my voice';
      this.gateBtn.onclick = () => this.send({ type: 'client-message', data: { t: 'enroll-voice', d: {} } });
      return;
    }
    this.speakerEl.textContent = g.gate ? 'Only your voice · say “yes” to approve' : 'Voice registered';
    this.gateBtn.hidden = false;
    this.gateBtn.textContent = g.gate ? 'Anyone' : 'Only my voice';
    this.gateBtn.onclick = () => this.toggleGate();
  }

  toggleGate() {
    this.send({ type: 'client-message', data: { t: 'set-gate', d: { on: !this.gate?.gate } } });
  }

  meter() {
    try {
      const ctx = this.meterCtx = new AudioContext();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(this.stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const tick = () => {
        if (this.meterCtx !== ctx) return;
        an.getByteTimeDomainData(buf);
        let peak = 0;
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
        this.level.style.width = Math.min(100, peak / 0.9) + '%';
        requestAnimationFrame(tick);
      };
      tick();
    } catch { /* the meter is a nicety */ }
  }
}

/** Show the mic button only when the voice agent answers. */
export async function attachVoice(panel) {
  try {
    const st = await (await fetch('/api/voice/status')).json();
    if (!st.ready) return null;
  } catch {
    return null;
  }
  panel.voice = new AssistantVoice(panel);
  panel.micBtn.hidden = false;
  return panel.voice;
}
