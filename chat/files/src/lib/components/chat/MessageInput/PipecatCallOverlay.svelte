<script lang="ts">
	import { config, models, showCallOverlay } from '$lib/stores';
	import { onMount, onDestroy, getContext, createEventDispatcher, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import {
		PipecatVoiceSession,
		type PipecatMode,
		type PipecatSpeaker,
		type PipecatForceLang
	} from '$lib/utils/pipecatClient';
	import { get } from 'svelte/store';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	export let addVoiceTurn: Function;
	export let chatId;
	export let modelId;

	let session: PipecatVoiceSession | null = null;
	let status = 'Connecting…';
	let muted = false;
	let loading = true;
	let assistantSpeaking = false;
	let camera = false;
	let cameraStream: MediaStream | null = null;
	let mode: PipecatMode = 'assistant';
	let speaker: PipecatSpeaker = 'ethan';
	let forceLang: PipecatForceLang = 'auto';
	let liveUser = '';
	let liveBot = '';
	let rmsLevel = 0;
	let connected = false;

	// Goal-speaker recognition. `gate.available` stays false against an agent that
	// does not send `rfomni-gate`, and the whole row is then hidden.
	let gate: {
		available: boolean;
		enrolled: boolean;
		gate: boolean;
		arming: boolean;
		takes: number;
		consistency: number | null;
		lastScore: number | null;
		threshold: number;
		bargeGateSupported: boolean;
		bargeGate: boolean;
		lastBargeScore: number | null;
		diarizeSupported: boolean;
		diarizeAvailable: boolean;
		diarize: boolean;
		lastSpeakers: number | null;
		rejections: number;
		// V5 only: the agent takes the voiceprint from the conversation itself. A V4
		// agent never sends these, so everything below falls back to V4's behaviour.
		autoSupported?: boolean;
		autoEnroll?: boolean;
	} = {
		available: false,
		enrolled: false,
		gate: false,
		arming: false,
		takes: 0,
		consistency: null,
		lastScore: null,
		threshold: 0.45,
		bargeGateSupported: false,
		bargeGate: false,
		lastBargeScore: null,
		diarizeSupported: false,
		diarizeAvailable: false,
		diarize: false,
		lastSpeakers: null,
		rejections: 0
	};

	// A refused speaker is shown, never answered — the lamp lights and fades on its own.
	let rejectFlash = 0;
	let rejectHot = false;

	const MODES: { id: PipecatMode; label: string }[] = [
		{ id: 'assistant', label: 'Assistant' },
		{ id: 'translate_en', label: 'Translate → EN' },
		{ id: 'annotator', label: 'Annotator' }
	];

	const SPEAKERS: { id: PipecatSpeaker; label: string }[] = [
		{ id: 'ethan', label: 'Ethan' },
		{ id: 'chelsie', label: 'Chelsie' },
		{ id: 'aiden', label: 'Aiden' }
	];

	// Two situations: unrestricted, or the fixed set of languages the room is known to
	// speak. The set narrows ASR to those candidates and bounds the spoken reply to them;
	// anything outside it is answered with a spoken request to switch.
	const LANGS: { id: PipecatForceLang; label: string; hint: string }[] = [
		{
			id: 'auto',
			label: 'Any language',
			hint: 'No restriction — the model detects the language itself'
		},
		{
			id: 'set',
			label: 'EN · DE · FR · ES · PT',
			hint: 'Everyone speaks one of these five; anything else is refused'
		}
	];

	$: model = $models.find((m) => m.id === modelId) ?? null;

	let pendingUser = '';
	let pendingBot = '';
	let flushing = false;

	const flushTurn = async () => {
		if (flushing) return;
		const userText = pendingUser.trim();
		const botText = pendingBot.trim();
		if (!userText && !botText) return;
		flushing = true;
		try {
			const msgs = [];
			if (userText) msgs.push({ role: 'user', content: userText });
			if (botText) msgs.push({ role: 'assistant', content: botText });
			pendingUser = '';
			pendingBot = '';
			liveUser = '';
			liveBot = '';
			if (msgs.length && addVoiceTurn) {
				await addVoiceTurn(msgs);
			}
		} catch (e) {
			console.error(e);
			const detail = e?.message || e?.detail || (typeof e === 'string' ? e : '');
			toast.error(detail ? `Failed to save voice turn: ${detail}` : 'Failed to save voice turn to chat');
		} finally {
			flushing = false;
		}
	};

	const start = async () => {
		loading = true;
		status = 'Connecting to Omni voice…';
		const token = localStorage.token;

		session = new PipecatVoiceSession(
			{
				onStatus: (s) => {
					status = s;
				},
				onReady: () => {
					loading = false;
					connected = true;
					status = 'Listening…';
				},
				onUserTranscript: (text, final) => {
					liveUser = text;
					if (final && text.trim()) {
						pendingUser = text.trim();
					}
				},
				onBotText: (text, done) => {
					liveBot = text;
					pendingBot = text;
					if (done) {
						void flushTurn();
					}
				},
				onBotSpeaking: (speaking) => {
					assistantSpeaking = speaking;
					if (!speaking && pendingBot) {
						void flushTurn();
					}
				},
				onError: (err) => {
					toast.error(err);
					status = err;
				},
				onDisconnected: () => {
					connected = false;
					if ($showCallOverlay) status = 'Disconnected';
				},
				onServerMessage: (data) => {
					if (data?.type === 'rfomni-mode' && data.mode) {
						mode = data.mode;
					}
					if (data?.type === 'rfomni-speaker' && data.speaker) {
						speaker = data.speaker;
					}
					if (data?.type === 'rfomni-gate') {
						if ((data.rejections ?? 0) > (gate.rejections ?? 0)) {
							rejectFlash = Date.now();
							rejectHot = true;
							setTimeout(() => {
								if (Date.now() - rejectFlash >= 2400) rejectHot = false;
							}, 2500);
						}
						gate = { ...gate, ...data };
					}
					if (data?.type === 'rfomni-error' && data.error) {
						// Most often "register a voice first" — clear the armed state so the
						// button does not sit on "Speak now…" forever.
						toast.error(data.error);
						gate = { ...gate, arming: false };
					}
					if (data?.type === 'rfomni-lang' && data.lang) {
						forceLang = data.lang;
					}
				}
			},
			token
		);

		try {
			// Enrolments are per session and live only in the agent's memory.
			gate = {
				...gate,
				enrolled: false,
				gate: false,
				arming: false,
				takes: 0,
				lastScore: null,
				lastBargeScore: null,
				lastSpeakers: null,
				rejections: 0
			};
			await session.connect({ audio: true, video: false, mode, speaker, forceLang });
		} catch (e: any) {
			console.error(e);
			toast.error(e?.message || 'Failed to connect Omni voice');
			status = 'Connection failed';
			loading = false;
			showCallOverlay.set(false);
			dispatch('close');
		}
	};

	const stopCamera = () => {
		cameraStream?.getTracks().forEach((t) => t.stop());
		cameraStream = null;
		camera = false;
		void session?.enableCamera(false);
	};

	const startCamera = async () => {
		try {
			cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
			camera = true;
			await tick();
			const video = document.getElementById('pipecat-camera-feed') as HTMLVideoElement | null;
			if (video) {
				video.srcObject = cameraStream;
				await video.play();
			}
			await session?.enableCamera(true);
		} catch (e) {
			console.error(e);
			toast.error('Camera permission denied');
			camera = false;
		}
	};

	const toggleMute = () => {
		muted = !muted;
		session?.setMuted(muted);
		status = muted ? 'Muted' : 'Listening…';
	};

	const endCall = async () => {
		await flushTurn();
		stopCamera();
		await session?.disconnect();
		session = null;
		showCallOverlay.set(false);
		dispatch('close');
	};

	const onKey = (e: KeyboardEvent) => {
		if (e.key === 'm' || e.key === 'M') {
			if ((e.target as HTMLElement)?.tagName === 'INPUT') return;
			toggleMute();
		}
	};

	onMount(() => {
		window.addEventListener('keydown', onKey);
		try {
			const saved = localStorage.getItem('omni_speaker');
			if (saved === 'ethan' || saved === 'chelsie' || saved === 'aiden') {
				speaker = saved;
			}
			const savedLang = localStorage.getItem('omni_force_lang');
			if (savedLang === 'auto' || savedLang === 'set') {
				forceLang = savedLang;
			}
		} catch {
			/* ignore */
		}
		void start();
	});

	onDestroy(() => {
		window.removeEventListener('keydown', onKey);
		stopCamera();
		void session?.disconnect();
	});
</script>

{#if $showCallOverlay}
	<div class="max-w-lg w-full h-full max-h-[100dvh] flex flex-col justify-between p-3 md:p-6">
		<div class="flex flex-wrap gap-2 justify-center pt-2">
			{#each MODES as m}
				<button
					type="button"
					class="px-3 py-1 text-xs rounded-full border transition {mode === m.id
						? 'bg-black text-white dark:bg-white dark:text-black border-transparent'
						: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
					on:click={() => {
						mode = m.id;
						session?.setMode(m.id);
					}}
				>
					{m.label}
				</button>
			{/each}
		</div>
		<div class="flex flex-wrap gap-2 justify-center pt-2">
			<span class="text-xs text-gray-500 self-center mr-1">Voice</span>
			{#each SPEAKERS as s}
				<button
					type="button"
					class="px-3 py-1 text-xs rounded-full border transition {speaker === s.id
						? 'bg-black text-white dark:bg-white dark:text-black border-transparent'
						: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
					on:click={() => {
						speaker = s.id;
						session?.setSpeaker(s.id);
						try {
							localStorage.setItem('omni_speaker', s.id);
						} catch {
							/* ignore */
						}
					}}
				>
					{s.label}
				</button>
			{/each}
		</div>
		<div class="flex flex-wrap gap-2 justify-center pt-2">
			<span
				class="text-xs text-gray-500 self-center mr-1"
				title="Which languages this session accepts, for speech in and speech out"
			>
				Languages
			</span>
			{#each LANGS as l}
				<button
					type="button"
					class="px-3 py-1 text-xs rounded-full border transition {forceLang === l.id
						? 'bg-black text-white dark:bg-white dark:text-black border-transparent'
						: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
					title={l.hint}
					on:click={() => {
						forceLang = l.id;
						session?.setForceLang(l.id);
						try {
							localStorage.setItem('omni_force_lang', l.id);
						} catch {
							/* ignore */
						}
					}}
				>
					{l.label}
				</button>
			{/each}
		</div>

		{#if gate.available}
			<div class="flex flex-wrap gap-2 justify-center pt-2 items-center">
				<span
					class="text-xs text-gray-500 self-center mr-1"
					title="Answer only the registered speaker"
				>
					Speaker
				</span>
				{#if !(gate.autoSupported && gate.autoEnroll)}
					<!-- Hidden against a V5 agent: it is already building the voiceprint from the
					     conversation, and pressing this would replace a multi-turn print with one
					     short take. -->
					<button
						type="button"
						class="px-3 py-1 text-xs rounded-full border transition {gate.arming
							? 'bg-red-500 text-white border-transparent'
							: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
						title="Your next spoken turn becomes the voiceprint — speak for about 5-8 seconds"
						on:click={() => {
							gate = { ...gate, arming: true };
							session?.enrollVoice();
						}}
					>
						{gate.arming ? 'Speak now…' : gate.enrolled ? 'Add take' : 'Register my voice'}
					</button>
				{/if}
				<button
					type="button"
					disabled={!gate.enrolled}
					class="px-3 py-1 text-xs rounded-full border transition disabled:opacity-40 {gate.gate
						? 'bg-amber-500 text-black border-transparent'
						: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
					title={gate.enrolled
						? 'Refuse anyone else, out loud'
						: gate.autoSupported && gate.autoEnroll
							? 'Available once the agent has heard enough of one voice'
							: 'Register a voice first'}
					on:click={() => session?.setGate(!gate.gate)}
				>
					Only my voice
				</button>
				{#if gate.bargeGateSupported}
					<button
						type="button"
						disabled={!gate.enrolled}
						class="px-3 py-1 text-xs rounded-full border transition disabled:opacity-40 {gate.bargeGate
							? 'bg-violet-500 text-white border-transparent'
							: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
						title={gate.enrolled
							? 'Only your voice can cut the agent off mid-sentence'
							: gate.autoSupported && gate.autoEnroll
								? 'Available once the agent has heard enough of one voice'
								: 'Register a voice first'}
						on:click={() => session?.setBargeGate(!gate.bargeGate)}
					>
						Only my barge-in
					</button>
				{/if}
				{#if gate.diarizeSupported && gate.diarizeAvailable}
					<button
						type="button"
						disabled={!gate.enrolled}
						class="px-3 py-1 text-xs rounded-full border transition disabled:opacity-40 {gate.diarize
							? 'bg-emerald-600 text-white border-transparent'
							: 'border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300'}"
						title={gate.enrolled
							? 'Separate overlapping speakers before deciding, instead of scoring the mixture'
							: gate.autoSupported && gate.autoEnroll
								? 'Available once the agent has heard enough of one voice'
								: 'Register a voice first'}
						on:click={() => session?.setDiarize(!gate.diarize)}
					>
						Separate speakers
					</button>
				{/if}
				{#if gate.enrolled}
					<button
						type="button"
						class="px-3 py-1 text-xs rounded-full border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 transition"
						title="Forget the voiceprint"
						on:click={() => session?.resetVoice()}
					>
						Clear
					</button>
				{/if}
				{#if gate.rejections > 0}
					<span
						class="px-2 py-1 text-xs rounded-full self-center tabular-nums transition-colors duration-500 {rejectHot
							? 'bg-red-600 text-white'
							: 'bg-red-500/15 text-red-400'}"
						title="Someone other than the registered speaker was refused"
					>
						●{gate.rejections > 1 ? ` ${gate.rejections}` : ''}
					</span>
				{/if}
				<span class="text-xs text-gray-500 self-center tabular-nums">
					{#if !gate.enrolled}
						{gate.autoSupported && gate.autoEnroll ? 'listening' : 'not registered'}
					{:else}
						{gate.takes} take{gate.takes === 1 ? '' : 's'}{gate.consistency !== null
							? ` · agree ${gate.consistency}`
							: ''}{gate.lastScore !== null ? ` · last ${gate.lastScore}` : ''}{gate.lastBargeScore !==
						null && gate.lastBargeScore !== undefined
							? ` · barge ${gate.lastBargeScore}`
							: ''}{gate.lastSpeakers
							? ` · ${gate.lastSpeakers} spk`
							: ''}
					{/if}
				</span>
			</div>
		{/if}

		<div class="flex justify-center items-center flex-1 h-full w-full max-h-full">
			{#if camera}
				<div class="relative flex video-container w-full max-h-full pt-2 pb-4 md:py-6 px-2 h-full">
					<!-- svelte-ignore a11y-media-has-caption -->
					<video
						id="pipecat-camera-feed"
						autoplay
						playsinline
						class="rounded-2xl h-full min-w-full object-cover object-center"
					/>
					<div class="absolute top-4 md:top-8 left-4">
						<button
							type="button"
							aria-label="Stop camera"
							class="p-1.5 text-white cursor-pointer backdrop-blur-xl bg-black/10 rounded-full"
							on:click={stopCamera}
						>
							<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-6">
								<path
									d="M5.28 4.22a.75.75 0 0 0-1.06 1.06L6.94 8l-2.72 2.72a.75.75 0 1 0 1.06 1.06L8 9.06l2.72 2.72a.75.75 0 1 0 1.06-1.06L9.06 8l2.72-2.72a.75.75 0 0 0-1.06-1.06L8 6.94 5.28 4.22Z"
								/>
							</svg>
						</button>
					</div>
				</div>
			{:else}
				<button type="button" class="outline-none">
					{#if loading || assistantSpeaking}
						<svg
							class="size-44 text-gray-900 dark:text-gray-400"
							viewBox="0 0 24 24"
							fill="currentColor"
							xmlns="http://www.w3.org/2000/svg"
						>
							<style>
								.spinner_qM83 {
									animation: spinner_8HQG 1.05s infinite;
								}
								.spinner_oXPr {
									animation-delay: 0.1s;
								}
								.spinner_ZTLf {
									animation-delay: 0.2s;
								}
								@keyframes spinner_8HQG {
									0%,
									57.14% {
										animation-timing-function: cubic-bezier(0.33, 0.66, 0.66, 1);
										transform: translate(0);
									}
									28.57% {
										animation-timing-function: cubic-bezier(0.33, 0, 0.66, 0.33);
										transform: translateY(-6px);
									}
									100% {
										transform: translate(0);
									}
								}
							</style>
							<circle class="spinner_qM83" cx="4" cy="12" r="3" />
							<circle class="spinner_qM83 spinner_oXPr" cx="12" cy="12" r="3" />
							<circle class="spinner_qM83 spinner_ZTLf" cx="20" cy="12" r="3" />
						</svg>
					{:else}
						<div
							class="size-40 transition-all rounded-full bg-cover bg-center bg-no-repeat"
							style={`background-image: url('${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model?.id}&lang=en&voice=true');`}
						/>
					{/if}
				</button>
			{/if}
		</div>

		<div class="flex flex-col items-center gap-3 pb-4 w-full">
			{#if liveUser || liveBot}
				<div
					class="w-full max-w-md text-xs text-gray-500 dark:text-gray-400 space-y-1 px-2 max-h-24 overflow-y-auto"
				>
					{#if liveUser}
						<div><span class="font-medium text-gray-700 dark:text-gray-200">You:</span> {liveUser}</div>
					{/if}
					{#if liveBot}
						<div>
							<span class="font-medium text-gray-700 dark:text-gray-200">Omni:</span>
							{liveBot}
						</div>
					{/if}
				</div>
			{/if}

			<div class="text-sm font-normal line-clamp-1">
				{#if muted}
					Muted
				{:else}
					{status}
				{/if}
				<span class="text-xs text-gray-400 ml-1">· Pipecat Omni</span>
			</div>

			<div class="flex items-center justify-center gap-4 z-10">
				{#if !camera}
					<Tooltip content="Camera (Omni vision)">
						<button
							aria-label="Camera"
							class="p-3 rounded-full bg-gray-50 dark:bg-gray-900"
							type="button"
							on:click={startCamera}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="1.5"
								stroke="currentColor"
								class="size-5"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"
								/>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0ZM18.75 10.5h.008v.008h-.008V10.5Z"
								/>
							</svg>
						</button>
					</Tooltip>
				{/if}

				<Tooltip content={muted ? 'Unmute (M)' : 'Mute (M)'}>
					<button
						class="p-3 rounded-full transition-colors duration-200 {muted
							? 'bg-red-500 text-white'
							: 'bg-gray-50 dark:bg-gray-900'}"
						type="button"
						aria-label={muted ? 'Unmute' : 'Mute'}
						on:click={toggleMute}
					>
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="1.5"
							stroke="currentColor"
							class="size-5"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"
							/>
							{#if muted}
								<line
									x1="3"
									y1="3"
									x2="21"
									y2="21"
									stroke="currentColor"
									stroke-width="1.5"
									stroke-linecap="round"
								/>
							{/if}
						</svg>
					</button>
				</Tooltip>

				<button
					aria-label="End call"
					class="p-3 rounded-full bg-gray-50 dark:bg-gray-900"
					on:click={endCall}
					type="button"
				>
					<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="size-5">
						<path
							d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"
						/>
					</svg>
				</button>
			</div>
		</div>
	</div>
{/if}
