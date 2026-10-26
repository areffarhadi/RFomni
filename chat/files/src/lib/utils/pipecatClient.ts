/**
 * Minimal Pipecat SmallWebRTC + RTVI client for Open WebUI Voice mode.
 * Signaling goes through OWUI `/api/v1/pipecat` (proxied to the RFOMNI agent).
 * Audio (and optional camera) ride the WebRTC peer connection; VAD/barge-in
 * stay on the Pipecat server.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

const RTVI_LABEL = 'rtvi-ai';
const RTVI_VERSION = '1.0.0';
const DC_LABEL = 'chat';

export type PipecatMode = 'assistant' | 'translate_en' | 'translate_de' | 'say' | 'annotator';
export type PipecatSpeaker = 'ethan' | 'chelsie' | 'aiden';
/**
 * Session language policy. `auto` leaves the model's own open-set detection alone;
 * `set` restricts the session to the languages the agent is configured with, narrowing
 * ASR to those candidates and bounding the language the reply is spoken in. A specific
 * code, or a comma-separated list of them, is also accepted by the agent.
 */
export type PipecatForceLang = 'auto' | 'set' | (string & {});

/**
 * `rfomni-gate` server message. Agents without goal-speaker recognition never send
 * it, which is what keeps the gate controls hidden against an older agent.
 */
export type PipecatGateStatus = {
	type: 'rfomni-gate';
	available: boolean;
	enrolled: boolean;
	gate: boolean;
	arming: boolean;
	takes: number;
	consistency: number | null;
	threshold: number;
	minEnrollSeconds: number;
	lastScore: number | null;
	gateError?: string;
	/** Absent from agents that cannot gate barge-in; the control stays hidden. */
	bargeGateSupported?: boolean;
	bargeGate?: boolean;
	bargeGuarding?: boolean;
	bargeWindowSeconds?: number;
	lastBargeScore?: number | null;
	/** Absent from agents without a diarizer; the control stays hidden. */
	diarizeSupported?: boolean;
	diarizeAvailable?: boolean;
	diarize?: boolean;
	lastSpeakers?: number | null;
	/**
	 * V5 only: the agent builds the voiceprint from the conversation itself, so the UI
	 * hides the Register control. Absent from V4, which keeps its button.
	 */
	autoSupported?: boolean;
	autoEnroll?: boolean;
	autoActive?: boolean;
	autoState?: string;
	autoDistinct?: number;
	autoSeconds?: number;
};

export type PipecatHandlers = {
	onStatus?: (status: string) => void;
	onUserTranscript?: (text: string, final: boolean) => void;
	onBotText?: (text: string, done: boolean) => void;
	onBotSpeaking?: (speaking: boolean) => void;
	onError?: (error: string) => void;
	onReady?: () => void;
	onDisconnected?: () => void;
	onServerMessage?: (data: any) => void;
};

function msgId(): string {
	return crypto.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function api(path: string, init: RequestInit = {}, token?: string) {
	const headers: Record<string, string> = {
		'Content-Type': 'application/json',
		...(init.headers as Record<string, string>)
	};
	if (token) headers['Authorization'] = `Bearer ${token}`;

	const res = await fetch(`${WEBUI_API_BASE_URL}/pipecat${path}`, {
		...init,
		headers,
		credentials: 'include'
	});
	if (!res.ok) {
		const text = await res.text().catch(() => '');
		throw new Error(`Pipecat ${path} failed (${res.status}): ${text || res.statusText}`);
	}
	const ct = res.headers.get('content-type') || '';
	if (ct.includes('application/json')) return res.json();
	return res.text();
}

export class PipecatVoiceSession {
	private pc: RTCPeerConnection | null = null;
	private dc: RTCDataChannel | null = null;
	private localStream: MediaStream | null = null;
	private remoteAudio: HTMLAudioElement | null = null;
	private sessionId: string | null = null;
	private pcId: string | null = null;
	private token: string | undefined;
	private handlers: PipecatHandlers;
	private botBuf = '';
	// One reply ends with several events (bot-llm-stopped, bot-tts-stopped,
	// bot-stopped-speaking). Each used to re-send botBuf as final, so the overlay saved
	// the same assistant message twice. Final is now sent once per reply.
	private botDone = true;
	private closed = false;
	private videoSender: RTCRtpSender | null = null;

	constructor(handlers: PipecatHandlers = {}, token?: string) {
		this.handlers = handlers;
		this.token = token;
	}

	async connect(opts: {
		audio?: boolean;
		video?: boolean;
		mode?: PipecatMode;
		speaker?: PipecatSpeaker;
		forceLang?: PipecatForceLang;
	} = {}) {
		if (this.pc) await this.disconnect();
		this.closed = false;
		this.handlers.onStatus?.('Connecting…');

		const start = await api(
			'/start',
			{
				method: 'POST',
				body: JSON.stringify({
					createDailyRoom: false,
					enableDefaultIceServers: true,
					transport: 'webrtc',
					body: {}
				})
			},
			this.token
		);

		this.sessionId = start.sessionId || start.session_id;
		const iceServers =
			start.iceConfig?.iceServers ||
			start.ice_config?.iceServers ||
			[{ urls: ['stun:stun.l.google.com:19302'] }];

		this.pc = new RTCPeerConnection({ iceServers });
		this.remoteAudio = document.createElement('audio');
		this.remoteAudio.autoplay = true;
		(this.remoteAudio as any).playsInline = true;

		this.pc.ontrack = (ev) => {
			const stream = ev.streams[0] || new MediaStream([ev.track]);
			if (ev.track.kind === 'audio' && this.remoteAudio) {
				this.remoteAudio.srcObject = stream;
				void this.remoteAudio.play().catch(() => {});
			}
		};

		this.pc.onconnectionstatechange = () => {
			const s = this.pc?.connectionState;
			if (s === 'failed' || s === 'disconnected' || s === 'closed') {
				this.handlers.onDisconnected?.();
			}
		};

		this.dc = this.pc.createDataChannel(DC_LABEL, { ordered: true });
		this.dc.onopen = () => {
			this.sendRtvi({
				label: RTVI_LABEL,
				type: 'client-ready',
				id: msgId(),
				data: {
					version: RTVI_VERSION,
					about: { library: 'open-webui-pipecat', library_version: '1.0.0' }
				}
			});
			if (opts.mode) this.setMode(opts.mode);
			if (opts.speaker) this.setSpeaker(opts.speaker);
			if (opts.forceLang) this.setForceLang(opts.forceLang);
			this.handlers.onReady?.();
			this.handlers.onStatus?.('Listening…');
		};
		this.dc.onmessage = (ev) => this.onDcMessage(ev.data);
		this.dc.onclose = () => this.handlers.onDisconnected?.();

		this.localStream = await navigator.mediaDevices.getUserMedia({
			audio: opts.audio !== false,
			video: opts.video === true
		});
		for (const track of this.localStream.getTracks()) {
			const sender = this.pc.addTrack(track, this.localStream);
			if (track.kind === 'video') this.videoSender = sender;
		}

		// Ensure recv for bot audio even if no remote track yet
		const hasAudioRecv = this.pc.getTransceivers().some((t) => t.receiver.track?.kind === 'audio');
		if (!hasAudioRecv) {
			this.pc.addTransceiver('audio', { direction: 'sendrecv' });
		}

		const offer = await this.pc.createOffer();
		await this.pc.setLocalDescription(offer);
		await this.waitIceGatheringComplete(this.pc);

		const answer = await api(
			`/sessions/${this.sessionId}/api/offer`,
			{
				method: 'POST',
				body: JSON.stringify({
					sdp: this.pc.localDescription?.sdp,
					type: this.pc.localDescription?.type,
					pc_id: null,
					restart_pc: false,
					requestData: {}
				})
			},
			this.token
		);

		this.pcId = answer.pc_id || answer.pcId;
		await this.pc.setRemoteDescription({
			type: answer.type,
			sdp: answer.sdp
		});

		this.pc.onicecandidate = (ev) => {
			if (!ev.candidate || !this.sessionId || !this.pcId) return;
			void api(
				`/sessions/${this.sessionId}/api/offer`,
				{
					method: 'PATCH',
					body: JSON.stringify({
						pc_id: this.pcId,
						candidates: [
							{
								candidate: ev.candidate.candidate,
								sdp_mid: ev.candidate.sdpMid,
								sdp_mline_index: ev.candidate.sdpMLineIndex
							}
						]
					})
				},
				this.token
			).catch(() => {});
		};
	}

	private waitIceGatheringComplete(pc: RTCPeerConnection, timeoutMs = 2500) {
		if (pc.iceGatheringState === 'complete') return Promise.resolve();
		return new Promise<void>((resolve) => {
			const done = () => {
				pc.removeEventListener('icegatheringstatechange', onChange);
				resolve();
			};
			const onChange = () => {
				if (pc.iceGatheringState === 'complete') done();
			};
			pc.addEventListener('icegatheringstatechange', onChange);
			setTimeout(done, timeoutMs);
		});
	}

	private sendRtvi(payload: Record<string, unknown>) {
		if (this.dc?.readyState === 'open') {
			this.dc.send(JSON.stringify(payload));
		}
	}

	private appendBot(text: string) {
		if (this.botDone) {
			this.botBuf = '';
			this.botDone = false;
		}
		this.botBuf += text;
		this.handlers.onBotText?.(this.botBuf, false);
	}

	private finishBot() {
		if (this.botDone || !this.botBuf) return;
		this.botDone = true;
		this.handlers.onBotText?.(this.botBuf, true);
	}

	private onDcMessage(raw: any) {
		let msg: any;
		try {
			msg = typeof raw === 'string' ? JSON.parse(raw) : raw;
		} catch {
			return;
		}
		if (!msg || msg.label !== RTVI_LABEL) return;

		switch (msg.type) {
			case 'bot-ready':
				this.handlers.onReady?.();
				break;
			case 'user-transcription': {
				const text = msg.data?.text ?? '';
				const final = msg.data?.final !== false;
				if (text) this.handlers.onUserTranscript?.(text, final);
				break;
			}
			case 'bot-llm-started':
				// No reset here: the agent streams text before it opens the response, so
				// clearing now would drop it. The next reply's first text resets instead.
				this.handlers.onStatus?.('Thinking…');
				break;
			case 'bot-llm-text': {
				const chunk = msg.data?.text ?? '';
				if (chunk) this.appendBot(chunk);
				break;
			}
			case 'bot-transcription':
				// One sentence at a time, repeating what bot-llm-text already streamed;
				// taking it as the reply kept only the last sentence.
				break;
			case 'bot-llm-stopped':
				this.finishBot();
				break;
			case 'bot-tts-started':
			case 'bot-started-speaking':
				this.handlers.onBotSpeaking?.(true);
				this.handlers.onStatus?.('Speaking…');
				break;
			case 'bot-tts-stopped':
			case 'bot-stopped-speaking':
				// Finish first: the overlay saves the turn on either call, and finishing after
				// it left the text pending, so the next stop event saved it a second time.
				this.finishBot();
				this.handlers.onBotSpeaking?.(false);
				this.handlers.onStatus?.('Listening…');
				break;
			case 'server-message':
				this.handlers.onServerMessage?.(msg.data);
				break;
			case 'error':
			case 'error-response':
				this.handlers.onError?.(msg.data?.error || 'Pipecat error');
				break;
			default:
				break;
		}
	}

	setMode(mode: PipecatMode) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'set-mode', d: { mode } }
		});
	}

	setSpeaker(speaker: PipecatSpeaker) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'set-speaker', d: { speaker } }
		});
	}

	/**
	 * Goal-speaker recognition. Arming is all the client does — the voiceprint is
	 * taken from the user's next spoken turn, so no audio crosses the data channel.
	 */
	enrollVoice() {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'enroll-voice', d: {} }
		});
	}

	/** Answer only the enrolled speaker. Requires an enrolment first. */
	setGate(on: boolean) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'set-gate', d: { on } }
		});
	}

	/**
	 * Separate the speakers in a turn before deciding, instead of scoring the mixture.
	 * Costs ~110 ms per utterance, so it is off unless asked for.
	 */
	setDiarize(on: boolean) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'set-diarize', d: { on } }
		});
	}

	/**
	 * Hold barge-in for a speaker check, so only the enrolled voice can cut the
	 * agent off mid-sentence.
	 */
	setBargeGate(on: boolean) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'set-barge-gate', d: { on } }
		});
	}

	/** Forget the voiceprint. */
	resetVoice() {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'reset-voice', d: {} }
		});
	}

	setForceLang(lang: PipecatForceLang) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'client-message',
			id: msgId(),
			data: { t: 'set-lang', d: { lang } }
		});
	}

	sendText(content: string, audioResponse = true) {
		this.sendRtvi({
			label: RTVI_LABEL,
			type: 'send-text',
			id: msgId(),
			data: {
				content,
				options: { run_immediately: true, audio_response: audioResponse }
			}
		});
	}

	setMuted(muted: boolean) {
		this.localStream?.getAudioTracks().forEach((t) => {
			t.enabled = !muted;
		});
	}

	async enableCamera(enable: boolean, deviceId?: string) {
		if (!this.pc) return;
		if (!enable) {
			this.localStream?.getVideoTracks().forEach((t) => {
				t.stop();
				this.localStream?.removeTrack(t);
			});
			if (this.videoSender) {
				await this.videoSender.replaceTrack(null);
			}
			return;
		}
		const cam = await navigator.mediaDevices.getUserMedia({
			video: deviceId ? { deviceId: { exact: deviceId } } : true
		});
		const track = cam.getVideoTracks()[0];
		if (!track) return;
		if (this.videoSender) {
			await this.videoSender.replaceTrack(track);
		} else if (this.localStream) {
			this.localStream.addTrack(track);
			this.videoSender = this.pc.addTrack(track, this.localStream);
		}
	}

	async disconnect() {
		if (this.closed) return;
		this.closed = true;
		try {
			this.sendRtvi({
				label: RTVI_LABEL,
				type: 'disconnect-bot',
				id: msgId(),
				data: {}
			});
		} catch {
			/* ignore */
		}
		try {
			this.dc?.close();
		} catch {
			/* ignore */
		}
		try {
			this.pc?.close();
		} catch {
			/* ignore */
		}
		this.localStream?.getTracks().forEach((t) => t.stop());
		if (this.remoteAudio) {
			this.remoteAudio.pause();
			this.remoteAudio.srcObject = null;
			this.remoteAudio = null;
		}
		this.pc = null;
		this.dc = null;
		this.localStream = null;
		this.videoSender = null;
		this.handlers.onDisconnected?.();
	}
}

export async function isPipecatReady(token?: string): Promise<boolean> {
	try {
		const st = await api('/status', { method: 'GET' }, token);
		return !!(st?.enabled && st?.ready);
	} catch {
		return false;
	}
}
