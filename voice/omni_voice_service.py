"""Pipecat service: Qwen3-Omni utterance-level ASR + spoken reply via vLLM-Omni.

Designed for locally-driven turn detection (Silero VAD + speech-timeout in the user
aggregator). On each completed user turn:
  1. ASR hop (text-only) on the buffered utterance
  2. Speak hop (text + audio) with assistant / translate prompt
Supports barge-in via ``InterruptionFrame`` (cancels in-flight httpx streams).

This service is the pipeline's sole interruption authority: the user aggregator is
configured with ``enable_interruptions=False`` so that when a voice is enrolled, an
interruption is broadcast only once the interrupter has been confirmed to be that
speaker. See ``_on_user_turn_start``.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Literal

from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserAudioRawFrame,
    UserImageRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.time import time_now_iso8601

from omni_client import (
    LANG_SET,
    SPEAKABLE,
    Qwen3OmniClient,
    OmniClientConfig,
    VoiceMode,
    decode_wav_b64,
    effective_mode,
    normalize_speaker,
    norm_lid,
    voice_prompt,
)
from omni_concurrency import omni_turn_slot
from auto_enroll import AutoEnroller
from speaker_gate import GateError, SpeakerGate

STUDIO_URL = os.environ.get("OMNI_STUDIO_URL", "https://127.0.0.1:8710").rstrip("/")


def _post_json(url: str, body: dict, timeout: float = 120.0, headers: dict | None = None) -> dict:
    import json
    import ssl
    import urllib.request

    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # the studio uses the self-signed LAN certificate
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.load(resp)

VoiceModeSetting = Literal["assistant", "translate_en", "translate_de", "say", "annotator", "studio"]

_VISION_HINTS = (
    "look",
    "see",
    "watching",
    "camera",
    "face",
    "expression",
    "reaction",
    "smile",
    "frown",
    "what am i",
    "do i look",
    "my face",
    "my reaction",
    "visible",
)


# Session language policy: which languages the session accepts at all. Empty means
# unrestricted, and the model does its own open-set detection. A non-empty set narrows
# ASR to those candidates and bounds the language the reply is spoken in; a set of one is
# a single-language lock.
LANG_POLICIES: dict[str, tuple[str, ...]] = {
    "auto": (),
    "set": LANG_SET,
}


@dataclass
class Qwen3OmniVoiceSettings:
    mode: VoiceModeSetting = "assistant"
    max_tokens: int = 200
    min_utterance_bytes: int = 6400  # ~200 ms @ 16 kHz mono s16
    # How many prior user+assistant turns to keep in the speak prompt.
    # Omni itself is stateless — memory is only what we put in this prompt.
    context_turns: int = 12
    speakable_fallback: str = "English"
    vision_enabled: bool = True
    speaker: str = "ethan"
    #: Languages this session accepts; empty is unrestricted.
    allowed_langs: tuple[str, ...] = ()
    #: The single accepted language, when there is exactly one. Derived from
    #: ``allowed_langs`` so the single-language paths stay inert for a wider set.
    force_lang: str = ""


class Qwen3OmniVoiceService(FrameProcessor):
    """Utterance-level Qwen3-Omni voice agent backed by vLLM-Omni."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8410/v1",
        model: str = "qwen3-omni-voice",
        context: LLMContext | None = None,
        settings: Qwen3OmniVoiceSettings | None = None,
        session_id: str = "",
        on_status=None,
        studio_auth: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        # studio mode: the pass Omni Studio gave this session, sent back so turns act for its user
        self._studio_auth = studio_auth
        self._settings = settings or Qwen3OmniVoiceSettings()
        self._context = context
        self._session_id = session_id
        self._on_status = on_status  # async callable(dict) for UI chrome
        # Allow env default (e.g. OMNI_SPEAKER=chelsie)
        if not settings or not getattr(settings, "speaker", None):
            self._settings.speaker = normalize_speaker(
                os.environ.get("OMNI_SPEAKER", self._settings.speaker)
            )
        self._client = Qwen3OmniClient(
            OmniClientConfig(
                base_url=base_url,
                model=model,
                speakable_fallback=self._settings.speakable_fallback,
                speaker=self._settings.speaker,
            )
        )
        self._sample_rate = 16000
        self._audio_buffer = bytearray()
        # OMNI_MIC_DEBUG_DIR: record the raw mic stream per session and log its level once a
        # second, to tell silence, echo and a dead track apart. Off unless the variable is set.
        self._mic_dir = os.environ.get("OMNI_MIC_DEBUG_DIR", "").strip()
        self._mic_wav = None
        self._mic_win = bytearray()
        self._mic_last = 0.0
        self._user_speaking = False
        self._preroll_bytes = 0
        self._turn_task: asyncio.Task | None = None
        self._speaking = False
        # `_speaking` covers generation only. The talker returns a whole reply as one
        # WAV, so generation finishes while the transport is still playing it out — for
        # most of the time the user can actually hear the agent. Playback is tracked
        # separately, from the output transport's own bot-speaking frames.
        self._bot_audible = False
        self._latest_jpeg_b64: str | None = None
        # Goal-speaker recognition. State lives on the service instance, which Pipecat
        # builds once per WebRTC session, so enrolments never leak between callers.
        self._gate = SpeakerGate()
        # V5: the owner is captured from the conversation instead of being asked for.
        # It watches finished turns, never blocks one, and only ever adds an enrolment.
        self._auto = AutoEnroller(gate=self._gate)
        self._auto_task: asyncio.Task | None = None
        # Deferred barge-in. `_barge_start` marks where in the utterance buffer the
        # interrupting speech begins; `_drop_turn` marks an utterance that talked over
        # the agent and was not the enrolled speaker.
        self._barge_pending = False
        self._barge_checking = False
        self._barge_idx = 0
        self._barge_start = 0
        self._barge_task: asyncio.Task | None = None
        self._drop_turn = False
        # True only for a turn the gate scored as the enrolled speaker. Studio mode passes
        # it on, so a spoken "yes" approves an action only in the owner's voice.
        self._speaker_verified = False
        self._vision_enabled = self._settings.vision_enabled and (
            os.environ.get("OMNI_VISION", "1") not in ("0", "false", "no")
        )

    def set_mode(self, mode: str) -> str:
        mode = (mode or "").strip().lower()
        allowed = {"assistant", "translate_en", "translate_de", "say", "annotator", "studio"}
        if mode not in allowed:
            raise ValueError(f"unknown mode {mode!r}; expected one of {sorted(allowed)}")
        self._settings.mode = mode  # type: ignore[assignment]
        logger.info("Omni [{}] mode → {}", self._session_id[:8] or "?", mode)
        return mode

    def set_speaker(self, speaker: str) -> str:
        applied = normalize_speaker(speaker)
        self._settings.speaker = applied
        self._client.config.speaker = applied
        logger.info("Omni [{}] speaker → {}", self._session_id[:8] or "?", applied)
        return applied

    def set_force_lang(self, lang: str) -> str:
        """Set the session language policy, and return the name a client should show.

        Accepts a policy name ("auto", "set"), a comma-separated list of ISO 639-1 codes,
        or a single code.
        """
        raw = (lang or "").strip().lower()
        if raw in ("", "auto", "detect", "none", "free"):
            self._settings.allowed_langs = ()
            self._settings.force_lang = ""
            logger.info("Omni [{}] languages → any", self._session_id[:8] or "?")
            return "auto"

        codes = LANG_POLICIES.get(raw)
        if codes is None:
            codes = tuple(c for c in (norm_lid(part) for part in raw.split(",")) if c)
        unknown = [c for c in codes if c not in SPEAKABLE]
        if not codes or unknown:
            raise ValueError(
                f"unsupported language policy {lang!r}; expected auto, set, or codes "
                f"from {sorted(SPEAKABLE)}"
            )
        self._settings.allowed_langs = codes
        self._settings.force_lang = codes[0] if len(codes) == 1 else ""
        logger.info(
            "Omni [{}] languages → {}", self._session_id[:8] or "?", ",".join(codes)
        )
        return self.lang_policy_name()

    def lang_policy_name(self) -> str:
        """Policy name for the current set, or the codes themselves if it matches none."""
        codes = self._settings.allowed_langs
        if not codes:
            return "auto"
        for name, preset in LANG_POLICIES.items():
            if preset and tuple(preset) == tuple(codes):
                return name
        return ",".join(codes)

    # --- goal-speaker recognition (V2) --------------------------------------------

    def _stats_payload(self) -> dict:
        """One RTVI event carrying both the gate's state and the silent capture's.

        The client reads a single ``rfomni-gate`` message, so V5's extra fields ride
        along with V4's rather than arriving as a second event the overlay would have
        to correlate with the first.
        """
        return {**self._gate.stats(), **self._auto.stats()}

    def set_auto_enroll(self, on: bool) -> dict:
        """Turn silent capture on or off. Off leaves V4's button-driven enrolment."""
        self._auto.set_enabled(bool(on))
        return self._stats_payload()

    def _start_auto_capture(self, pcm: bytes) -> None:
        """Observe this turn in the background, so capture costs the turn no latency.

        The embed is ~0.1 s of GPU and the answer that follows is seconds of model, but
        the two are independent: nothing in the reply path depends on the capture, and a
        capture that fails must not delay or break a turn that was going to be answered
        either way. The task reference is held because asyncio only weakly references
        running tasks.
        """
        if not self._auto.active:
            return
        pcm = bytes(pcm)

        async def _run():
            try:
                registered = await self._auto.observe(pcm, self._sample_rate)
            finally:
                self._auto_task = None
            # Nothing is reported while the attempt is still gathering voices: a status
            # event mid-capture is how the user would find out this is happening, and
            # the point of V5 is that they do not have to. The one event that is sent
            # is the finished registration, because the "Only my voice" control has to
            # stop being greyed out.
            if registered:
                await self._emit_status(
                    {**self._stats_payload(), "event": "auto-enrolled"}
                )

        self._auto_task = asyncio.create_task(_run())

    async def gate_probe(self) -> bool:
        """Probe the embedder at connect time so the UI can hide the controls."""
        return await self._gate.probe()

    def arm_enroll(self) -> dict:
        """Consume the next completed user turn as an enrolment take."""
        if not self._gate.available:
            raise GateError("speaker embedder is not reachable")
        self._gate.arming = True
        logger.info("Omni [{}] gate: armed for enrolment", self._session_id[:8] or "?")
        return self._stats_payload()

    async def reset_enroll(self) -> dict:
        """Forget the voiceprint. Both gates go inactive: there is nothing left to
        compare against, so a hold in progress is released too."""
        self._gate.reset()
        # Clearing the voiceprint restarts the silent capture from scratch: the next
        # voice heard is a first voice again.
        self._auto.reset()
        self._drop_turn = False
        if self._barge_pending:
            self._barge_pending = False
            await self._cancel_barge_check()
            if self._agent_holding_the_floor:
                await self.broadcast_interruption()
        return self._stats_payload()

    def set_gate(self, on: bool) -> dict:
        self._gate.set_enabled(on)
        return self._stats_payload()

    def gate_stats(self) -> dict:
        return self._stats_payload()

    @property
    def _agent_holding_the_floor(self) -> bool:
        """True while the user can hear the agent — generating or still playing out."""
        return self._speaking or self._bot_audible

    def set_diarize(self, on: bool) -> dict:
        """Separate the speakers in an utterance before scoring, rather than the blend."""
        self._gate.set_diarize(on)
        return self._stats_payload()

    async def set_barge_gate(self, on: bool) -> dict:
        """Let only the enrolled speaker cut the agent off mid-sentence.

        Switching this off takes effect on the utterance in flight, not just the next
        one: a hold in progress is released and an overlap already marked for dropping
        is reinstated, so turning the feature off is immediate from the user's side.
        """
        self._gate.set_barge_gate(on)
        if not on:
            self._drop_turn = False
            if self._barge_pending:
                self._barge_pending = False
                await self._cancel_barge_check()
                if self._agent_holding_the_floor:
                    await self.broadcast_interruption()
        return self._stats_payload()

    async def _on_user_turn_start(self):
        """Interrupt the agent now, or hold the interruption until we know who spoke."""
        self._drop_turn = False
        if not (self._gate.guards_barge_in and self._agent_holding_the_floor):
            # Nothing playing to protect, or no enrolled voice to protect it for:
            # interrupt at once, which is what the aggregator would have done.
            await self.broadcast_interruption()
            return
        # Hold it. The agent keeps talking for up to barge_window_s while the
        # interrupting speech accumulates; only the enrolled speaker gets to cut in.
        self._barge_pending = True
        self._barge_checking = False
        self._barge_idx = 0
        # Back-date the window so the check sees the speech VAD needed to fire, not
        # just what arrives afterwards.
        lead = int(self._sample_rate * 2 * 0.3)
        self._barge_start = max(0, len(self._audio_buffer) - lead)
        logger.debug(
            "Omni [{}] barge-in held for speaker check", self._session_id[:8] or "?"
        )

    async def _cancel_barge_check(self):
        task = self._barge_task
        self._barge_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _decide_barge(self, pcm: bytes):
        """Identify the interrupter: cut the agent off, keep listening, or ignore them."""
        last = self._barge_idx >= len(self._gate.barge_checkpoints) - 1
        secs = len(pcm) / (self._sample_rate * 2)
        try:
            score, accepted = await self._gate.verify(
                pcm, self._sample_rate, allow_diarization=False
            )
            self._gate.last_barge_score = round(score, 3)
        except GateError as exc:
            accepted, last = self._gate.fail_open, True
            logger.warning(
                "barge check failed ({}) — {}",
                exc,
                "interrupting" if accepted else "ignoring",
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._barge_checking = False

        if accepted:
            self._barge_pending = False
            logger.info(
                "Omni [{}] barge-in allowed after {:.1f}s score={}",
                self._session_id[:8] or "?",
                secs,
                self._gate.last_barge_score,
            )
            await self._emit_status(
                {**self._stats_payload(), "bargeAllowed": True, "event": "barge"}
            )
            # If the agent finished on its own while we were checking there is nothing
            # to interrupt, and the turn proceeds normally.
            if self._agent_holding_the_floor:
                await self.broadcast_interruption()
            return

        if not last:
            # Not recognisable yet. Keep the agent talking and look again once more of
            # the utterance has arrived — a short window understates a genuine match.
            self._barge_idx += 1
            logger.debug(
                "barge-in undecided at {:.1f}s (score={}), waiting for {:.1f}s",
                secs,
                self._gate.last_barge_score,
                self._gate.barge_checkpoints[self._barge_idx],
            )
            return

        # Out of checkpoints: someone else talked over the agent. Let it finish and drop
        # their audio — a spoken refusal here would collide with the sentence in progress.
        self._barge_pending = False
        self._drop_turn = True
        logger.info(
            "Omni [{}] barge-in refused after {:.1f}s score={}",
            self._session_id[:8] or "?",
            secs,
            self._gate.last_barge_score,
        )
        await self._emit_status(
            {**self._stats_payload(), "bargeAllowed": False, "event": "barge"}
        )

    async def _handle_enroll_take(self, pcm: bytes):
        """Fold this utterance into the voiceprint instead of answering it."""
        try:
            info = await self._gate.add_take(pcm, self._sample_rate)
        except GateError as exc:
            # Stay armed: "too short" should let the user simply try again, and the
            # complaint is spoken because this UI has no other channel to the user.
            await self._emit_status({**self._stats_payload(), "gateError": str(exc)})
            await self._stream_speak(voice_prompt("say", f"Registration failed. {exc}."))
            return
        self._gate.arming = False
        await self._emit_status(self._stats_payload())
        agreement = info["consistency"]
        spoken = (
            f"Voice registered from {info['seconds']:.0f} seconds of speech. "
            f"This is take {info['takes']}."
        )
        if agreement is not None and agreement < 0.55:
            # Low agreement between takes means the enrolment is a poor description of
            # the voice; say so rather than let the gate misbehave later.
            spoken += " The takes do not agree well. Consider clearing and re-registering."
        await self._stream_speak(voice_prompt("say", spoken))

    async def _gate_allows(self, pcm: bytes) -> bool:
        """False = not the enrolled speaker, so the turn stops here.

        A rejected speaker costs one embedding and nothing else: no ASR, no reply, and no
        interruption of whatever the agent is doing. The refusal is reported to the client
        as a status event so the UI can show it, which keeps a rejection visible without
        spending the model on someone the session is not listening to.
        """
        try:
            score, accepted = await self._gate.verify(pcm, self._sample_rate)
        except GateError as exc:
            decision = "letting it through" if self._gate.fail_open else "refusing"
            logger.warning("gate verify failed ({}) — {}", exc, decision)
            await self._emit_status({**self._stats_payload(), "gateError": str(exc)})
            return self._gate.fail_open
        if accepted:
            self._speaker_verified = True
            await self._emit_status(self._stats_payload())
            logger.info(
                "Omni [{}] gate accept score={:.3f}", self._session_id[:8] or "?", score
            )
            return True
        self._gate.rejections += 1
        logger.info(
            "Omni [{}] gate refuse score={:.3f} < {:.2f} (rejection {})",
            self._session_id[:8] or "?",
            score,
            self._gate.threshold,
            self._gate.rejections,
        )
        await self._emit_status({**self._stats_payload(), "event": "gate-rejected"})
        return False

    async def _outside_language_policy(self, transcript: str) -> bool:
        """True when the speaker used a language this session does not accept.

        Judged on the transcript rather than the audio LID: text detection is the more
        reliable of the two, and a restricted ASR prompt biases the audio LID toward the
        allowed set even when the speech was not in it.

        The caller shows the transcript and then stops. Seeing their own words appear
        while nothing is answered tells the speaker what happened, so a spoken correction
        would only talk over them to say what the screen already shows.
        """
        allowed = self._settings.allowed_langs
        if not allowed or not (transcript or "").strip():
            return False
        try:
            detected = norm_lid(await self._client.detect_lang_text(transcript))
        except Exception as exc:
            logger.debug("language detect failed: {}", exc)
            return False
        if not detected or detected in allowed:
            return False
        logger.info(
            "Omni [{}] languages={} rejected input lid={} → transcribe only",
            self._session_id[:8] or "?",
            ",".join(allowed),
            detected,
        )
        return True

    def memory_stats(self) -> dict:
        used = 0
        if self._context:
            used = sum(1 for m in self._context.messages if m.get("role") in ("user", "assistant"))
            used = (used + 1) // 2
        return {
            "type": "rfomni-memory",
            "memoryTurns": used,
            "maxTurns": self._settings.context_turns,
            "mode": self._settings.mode,
            "speaker": self._settings.speaker,
            "forceLang": self.lang_policy_name(),
            "langPolicy": self.lang_policy_name(),
            "allowedLangs": list(self._settings.allowed_langs),
        }

    async def _emit_status(self, extra: dict | None = None):
        if not self._on_status:
            return
        payload = self.memory_stats()
        if extra:
            payload.update(extra)
        try:
            await self._on_status(payload)
        except Exception as exc:
            logger.debug("status emit failed: {}", exc)

    async def setup(self, setup):
        await super().setup(setup)
        if setup.audio_in_sample_rate:
            self._sample_rate = setup.audio_in_sample_rate
        preroll_secs = float(os.environ.get("OMNI_PREROLL_SECS", "1.5"))
        # s16 mono = 2 bytes/sample; keep audio before VAD fires for clean ASR.
        self._preroll_bytes = int(self._sample_rate * 2 * preroll_secs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            await self._cancel_turn()
            if self._mic_wav is not None:
                self._mic_wav.close()
                self._mic_wav = None
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            await self._cancel_turn()
            # User is barge-ing in — keep buffered mic audio or we lose the
            # start of their sentence (classic "only hears the last part" bug).
            self._user_speaking = True
            if self._speaking:
                await self.push_frame(TTSStoppedFrame())
                await self.push_frame(LLMFullResponseEndFrame())
                self._speaking = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMContextFrame):
            text, intro = self._text_from_context(frame.context.messages)
            if text:
                # Voice utterances are handled on UserStoppedSpeakingFrame with
                # the audio buffer. Skip duplicate LLMContextFrame runs while
                # the user is speaking or we already buffered their utterance.
                # Only defer while the user is actively speaking; preroll keeps the
                # buffer above min_utterance_bytes even when idle.
                voice_pending = not intro and self._user_speaking
                if voice_pending:
                    logger.debug("Omni: deferring LLMContextFrame — voice turn pending")
                else:
                    await self._start_turn_task(self._run_text_turn(text, intro=intro))
            await self.push_frame(frame, direction)
            return

        # Turn start. The aggregator no longer interrupts on its own, so the decision
        # is made here — immediately, or after the speaker has been identified.
        if isinstance(frame, (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)):
            first = not self._user_speaking
            self._user_speaking = True
            if first:
                await self._on_user_turn_start()
            await self.push_frame(frame, direction)
            return

        # End of user turn: VAD silence + speech-timeout (full utterance for Omni ASR).
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            pcm = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            # A held interruption that never resolved means this utterance overlapped
            # the agent's speech without ever being confirmed as the enrolled speaker.
            # An unresolved hold means the overlap was never attributed. Drop it only if
            # the agent is still mid-sentence; if it has finished there is nothing left to
            # protect, and who gets answered is the utterance gate's business.
            unresolved = self._barge_pending and self._agent_holding_the_floor
            self._barge_pending = False
            self._barge_checking = False
            await self._cancel_barge_check()
            if self._drop_turn or unresolved:
                self._drop_turn = False
                logger.info(
                    "Omni [{}] dropped {} bytes talked over the agent by another speaker",
                    self._session_id[:8] or "?",
                    len(pcm),
                )
                await self.push_frame(frame, direction)
                return
            if len(pcm) >= self._settings.min_utterance_bytes:
                logger.debug("Omni voice turn: {} bytes (~{:.0f} ms)", len(pcm), len(pcm) / (self._sample_rate * 2) * 1000)
                await self._start_turn_task(self._run_user_turn(pcm))
            else:
                logger.debug(
                    "Omni: user turn too short ({} bytes), skipping", len(pcm)
                )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # End-of-turn is decided by LocalSmartTurnAnalyzerV3 (V5.3), not raw VAD.
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (InputAudioRawFrame, UserAudioRawFrame)):
            if self._mic_dir:
                self._debug_mic(frame.audio)
            self._buffer_audio(frame.audio)
            await self.push_frame(frame, direction)
            return

        # Playback bracketing from the output transport. These arrive upstream, so the
        # direction is preserved on the way through.
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_audible = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_audible = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserImageRawFrame):
            self._store_user_image(frame)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _store_user_image(self, frame: UserImageRawFrame):
        """Keep latest camera JPEG for vision-aware turns."""
        try:
            import io

            from PIL import Image

            img = Image.frombytes(frame.format or "RGB", frame.size, frame.image)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=75)
            import base64

            self._latest_jpeg_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:
            logger.debug("user image store failed: {}", exc)

    def _debug_mic(self, audio: bytes):
        import time
        import wave

        import numpy as np

        now = time.monotonic()
        if self._mic_wav is None:
            os.makedirs(self._mic_dir, exist_ok=True)
            path = os.path.join(self._mic_dir, f"{self._session_id[:8] or 'session'}_{int(time.time())}.wav")
            self._mic_wav = wave.open(path, "wb")
            self._mic_wav.setnchannels(1)
            self._mic_wav.setsampwidth(2)
            self._mic_wav.setframerate(self._sample_rate)
            logger.info("Omni [{}] mic debug: recording {}", self._session_id[:8] or "?", path)
        elif now - self._mic_last > 0.5:
            logger.info("Omni [{}] mic debug: no audio for {:.2f}s", self._session_id[:8] or "?", now - self._mic_last)
        self._mic_last = now
        self._mic_wav.writeframes(audio)
        self._mic_win += audio
        if len(self._mic_win) >= self._sample_rate * 2:
            x = np.frombuffer(bytes(self._mic_win), dtype=np.int16).astype(np.float32)
            self._mic_win.clear()
            rms = float(np.sqrt(np.mean(x * x))) or 1.0
            logger.info(
                "Omni [{}] mic debug: {:.1f} dBFS peak {:.1f} dBFS bot_audible={} user_speaking={}",
                self._session_id[:8] or "?",
                20 * np.log10(rms / 32768),
                20 * np.log10((float(np.abs(x).max()) or 1.0) / 32768),
                self._bot_audible,
                self._user_speaking,
            )

    def _buffer_audio(self, audio: bytes):
        self._audio_buffer += audio
        if not self._user_speaking and len(self._audio_buffer) > self._preroll_bytes:
            discard = len(self._audio_buffer) - self._preroll_bytes
            del self._audio_buffer[:discard]
            return
        # Enough of the interrupting speech has arrived for the next identity check.
        if self._barge_pending and not self._barge_checking:
            need = self._gate.barge_checkpoints[self._barge_idx]
            if len(self._audio_buffer) - self._barge_start >= int(self._sample_rate * 2 * need):
                self._barge_checking = True
                pcm = bytes(self._audio_buffer[self._barge_start :])
                self._barge_task = asyncio.create_task(self._decide_barge(pcm))

    async def _cancel_turn(self):
        task = self._turn_task
        self._turn_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _start_turn_task(self, coro):
        await self._cancel_turn()
        self._turn_task = asyncio.create_task(coro)

    async def _run_text_turn(self, text: str, *, intro: bool = False):
        """Typed text or intro — skip ASR, speak via Omni talker."""
        try:
            async with omni_turn_slot(session_id=self._session_id):
                await self._run_text_turn_inner(text, intro=intro)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            logger.warning("Omni text turn queued too long: {}", exc)
            await self.push_frame(ErrorFrame(error=str(exc)))
        except Exception as exc:
            logger.exception("Omni text turn failed")
            await self.push_frame(ErrorFrame(error=f"Omni text: {exc}"))

    async def _run_text_turn_inner(self, text: str, *, intro: bool = False):
        if not intro:
            await self.push_frame(
                TranscriptionFrame(
                    text=text,
                    user_id="",
                    timestamp=time_now_iso8601(),
                    finalized=True,
                )
            )
            self._ensure_user_in_context(text)
        if intro:
            force = self._settings.force_lang or "en"
            prompt = (
                voice_prompt("assistant", "", force, strict_lang=True).rstrip()
                + "\n\nFollow this instruction and greet the user:\n"
                + text
            )
            reply_text = await self._stream_speak(prompt)
        elif self._settings.mode == "annotator":
            # Typed annotator: treat text as already-known ASR; still translate if needed.
            lid = self._settings.force_lang or await self._client.detect_lang_text(text)
            reply_text = await self._run_annotator_speak({"ASR": text, "LID": lid})
        else:
            if await self._outside_language_policy(text):
                # Shown, not answered: the transcript already says what happened.
                await self._emit_status({**self._stats_payload(), "event": "lang-skipped"})
                return
            else:
                mode: VoiceMode = self._settings.mode  # type: ignore[assignment]
                lid = self._settings.force_lang or await self._client.detect_lang_text(text)
                eff = effective_mode(mode, lid)
                if self._wants_vision(text):
                    reply_text = await self._vision_then_speak(text, lid)
                else:
                    prompt = self._conversation_prompt(eff, text, lid)
                    reply_text = await self._stream_speak(prompt)
        clean = self._clean_reply_text(reply_text)
        if clean and not intro:
            self._append_context("assistant", clean)
            await self._emit_status()
            logger.info(
                "Omni [{}] memory: {} msgs after text turn",
                self._session_id[:8] or "?",
                len(self._context.messages) if self._context else 0,
            )

    def _text_from_context(self, messages) -> tuple[str, bool]:
        """Return (text, is_intro). Intro uses developer/system when no user message yet."""
        if not messages:
            return "", False
        for msg in reversed(messages):
            if msg.get("role") == "user":
                t = self._text_content(msg.get("content", "")).strip()
                if t:
                    return t, False
        for msg in reversed(messages):
            if msg.get("role") in ("developer", "system"):
                t = self._text_content(msg.get("content", "")).strip()
                if t:
                    return t, True
        return "", False

    async def _run_intro(self):
        """Legacy helper — prefer LLMContextFrame path."""
        if not self._context:
            return
        text, _ = self._text_from_context(self._context.messages)
        if text:
            await self._run_text_turn(text, intro=True)

    async def _run_user_turn(self, pcm: bytes):
        try:
            async with omni_turn_slot(session_id=self._session_id):
                await self._run_user_turn_inner(pcm)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            logger.warning("Omni voice turn queued too long: {}", exc)
            await self.push_frame(ErrorFrame(error=str(exc)))
        except Exception as exc:
            logger.exception("Omni user turn failed")
            await self.push_frame(ErrorFrame(error=f"Omni turn: {exc}"))

    async def _run_user_turn_inner(self, pcm: bytes):
        # Goal-speaker recognition comes first: an enrolment take must not be answered,
        # and a stranger must not cost a model call at all.
        self._speaker_verified = False
        if self._gate.arming:
            await self._handle_enroll_take(pcm)
            return
        # V5: with nobody registered, this turn is also an enrolment candidate. It is
        # still answered exactly as it would have been — capture runs beside it.
        self._start_auto_capture(pcm)
        if self._gate.enabled and not await self._gate_allows(pcm):
            return

        # Target speaker extraction: when the gate diarized a turn that held more than
        # one voice, transcribe the enrolled speaker's segments alone. The model never
        # sees the other speakers, so the transcript carries only what the owner said and
        # the hop is shorter than the original turn.
        if self._gate.last_owner_pcm:
            kept = len(self._gate.last_owner_pcm) / (self._sample_rate * 2)
            whole = len(pcm) / (self._sample_rate * 2)
            logger.info(
                "Omni [{}] target speaker: {:.1f}s of {:.1f}s kept ({} speakers)",
                self._session_id[:8] or "?",
                kept,
                whole,
                self._gate.last_speakers,
            )
            pcm = self._gate.last_owner_pcm

        mode = self._settings.mode
        if mode == "annotator":
            ann = await self._client.annotate(pcm, self._sample_rate)
            transcript = (ann.get("ASR") or "").strip()
            if not transcript:
                return
            if self._settings.force_lang:
                ann["LID"] = self._settings.force_lang
            meta = self._format_annotation_meta(ann)
            shown = transcript if not meta else f"{transcript}  [{meta}]"
            await self.push_frame(
                TranscriptionFrame(
                    text=shown,
                    user_id="",
                    timestamp=time_now_iso8601(),
                    finalized=True,
                )
            )
            self._ensure_user_in_context(shown)
            reply_text = await self._run_annotator_speak(ann)
        else:
            force = self._settings.force_lang
            transcript, lid = await self._client.transcribe(
                pcm,
                self._sample_rate,
                force_lang=force,
                allowed_langs=self._settings.allowed_langs,
            )
            if not transcript:
                return
            await self.push_frame(
                TranscriptionFrame(
                    text=transcript,
                    user_id="",
                    timestamp=time_now_iso8601(),
                    finalized=True,
                )
            )
            self._ensure_user_in_context(transcript)

            if await self._outside_language_policy(transcript):
                # The transcript is already on screen; answering in a language the
                # session does not accept, or correcting the speaker out loud, would
                # both add noise. Drop it from the context too, so the next turn is not
                # built on an utterance nothing replied to.
                self._drop_last_context_message()
                await self._emit_status({**self._stats_payload(), "event": "lang-skipped"})
                return
            else:
                if force:
                    lid = force
                elif not lid:
                    lid = await self._client.detect_lang_text(transcript)
                eff = effective_mode(mode, lid)  # type: ignore[arg-type]
                if mode == "studio":
                    reply_text = await self._studio_turn(transcript, lid)
                elif self._wants_vision(transcript):
                    reply_text = await self._vision_then_speak(transcript, lid)
                else:
                    prompt = self._conversation_prompt(eff, transcript, lid)
                    reply_text = await self._stream_speak(prompt)

        clean = self._clean_reply_text(reply_text)
        if clean:
            self._append_context("assistant", clean)
            await self._emit_status()
            logger.info(
                "Omni [{}] memory: {} msgs after voice turn",
                self._session_id[:8] or "?",
                len(self._context.messages) if self._context else 0,
            )

    async def _studio_turn(self, transcript: str, lid: str) -> str:
        """Studio mode: the Omni Studio assistant decides, this service only listens and speaks.

        The full reply (proposal card, page actions, step) goes to the client as an
        ``rfomni-studio`` server message; the spoken part is read out word for word.
        """
        body = {"text": transcript, "via": "voice", "speaker_ok": self._speaker_verified}
        try:
            reply = await asyncio.to_thread(_post_json, f"{STUDIO_URL}/api/agent/turn", body,
                                            headers={"X-Omni-Auth": self._studio_auth} if self._studio_auth else None)
        except Exception as exc:
            logger.warning("Omni [{}] studio assistant unreachable: {}", self._session_id[:8] or "?", exc)
            reply = {"say": "I can't reach Omni Studio right now. Please try again in a moment."}
        await self._emit_status({"type": "rfomni-studio", "reply": reply, "speakerOk": self._speaker_verified})
        say = (reply.get("say") or "").strip()
        if not say:
            return ""
        return await self._stream_speak(voice_prompt("say", say, lid or "en"))

    async def _run_annotator_speak(self, ann: dict) -> str:
        """Annotate (+ EN translation if not English) then speak a short summary."""
        asr = (ann.get("ASR") or "").strip()
        lid = norm_lid(ann.get("LID") or "")
        translation = ""
        if asr and lid and lid != "en":
            translation = await self._client.translate_to_en(asr, source_lang=lid)
            if translation:
                await self.push_frame(LLMTextFrame(text=f"\nEN: {translation}"))

        bits = [self._format_annotation_meta(ann)]
        spoken_parts = []
        if asr:
            spoken_parts.append(f"Transcript: {asr}.")
        if translation and translation.strip() != asr:
            spoken_parts.append(f"English: {translation}.")
        if bits and bits[0]:
            spoken_parts.append(f"Tags: {bits[0]}.")
        if not spoken_parts:
            spoken_parts.append("I could not annotate that utterance.")

        # Read the annotation aloud (say mode = exact text).
        speak_text = " ".join(spoken_parts)
        prompt = voice_prompt("say", speak_text, "en")
        return await self._stream_speak(prompt)

    @staticmethod
    def _format_annotation_meta(ann: dict) -> str:
        bits = []
        for key, label in (
            ("LID", "lang"),
            ("EMO", "emotion"),
            ("GEN", "gender"),
            ("EVENT", "event"),
            ("QUALITY", "quality"),
        ):
            val = ann.get(key)
            if val:
                bits.append(f"{label}={val}")
        ner = ann.get("NER")
        if isinstance(ner, list) and ner:
            bits.append(f"entities={len(ner)}")
        return ", ".join(bits)

    def _wants_vision(self, text: str) -> bool:
        if not self._vision_enabled or not self._latest_jpeg_b64:
            return False
        t = (text or "").lower()
        return any(h in t for h in _VISION_HINTS)

    async def _vision_then_speak(self, user_text: str, lid: str) -> str:
        """Use latest camera frame so Omni can comment on the user's reaction/appearance."""
        vision_q = (
            "You can see the user's camera image. In one short sentence, describe what "
            "you notice about their face/expression/reaction that is relevant to this "
            f"question:\n\n{user_text}"
        )
        try:
            visual = await self._client.vision_reply(
                vision_q, image_jpeg_b64=self._latest_jpeg_b64, max_tokens=80
            )
        except Exception as exc:
            logger.warning("vision hop failed ({}), falling back to audio-only", exc)
            visual = ""
        if visual:
            await self.push_frame(LLMTextFrame(text=f"\n[vision] {visual}"))
            combined = (
                f"{user_text}\n\n(Camera observation: {visual})\n"
                "Answer the user using both what they said and what you see."
            )
        else:
            combined = user_text
        prompt = self._conversation_prompt("assistant", combined, lid)
        return await self._stream_speak(prompt)

    def _conversation_prompt(self, mode: VoiceMode, user_text: str, lid: str) -> str:
        """Build hop-2 prompt with recent chat history (Omni has no session memory)."""
        strict = bool(self._settings.allowed_langs)
        use_lid = self._settings.force_lang or lid
        if mode != "assistant" or not self._context:
            return voice_prompt(mode, user_text, use_lid, strict_lang=strict)

        history: list[tuple[str, str]] = []
        for msg in self._context.messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            text = self._clean_reply_text(self._text_content(msg.get("content", "")))
            if text:
                history.append((role, text))

        # Keep last N turns; drop trailing copy of the current user line (added below).
        max_msgs = max(2, self._settings.context_turns * 2)
        history = history[-max_msgs:]
        if history and history[-1] == ("user", user_text.strip()):
            history = history[:-1]

        lines = [
            voice_prompt("assistant", "", use_lid, strict_lang=strict).rstrip(),
            "Use the conversation history below. Remember facts the user already stated.",
            "",
        ]
        for role, text in history:
            lines.append(f"{role}: {text}")
        lines.append(f"user: {user_text.strip()}")
        lines.append("assistant:")
        prompt = "\n".join(lines)
        logger.debug(
            "Omni prompt history turns≈{} chars={}",
            len(history) // 2,
            len(prompt),
        )
        return prompt

    def _ensure_user_in_context(self, text: str):
        text = (text or "").strip()
        if not text or self._context is None:
            return
        msgs = self._context.messages
        if msgs:
            last = msgs[-1]
            if last.get("role") == "user" and self._text_content(
                last.get("content", "")
            ).strip() == text:
                return
        self._append_context("user", text)

    @staticmethod
    def _clean_reply_text(text: str) -> str:
        """Keep spoken reply text; drop accidental WAV/base64 audio blobs."""
        t = (text or "").strip()
        if not t:
            return ""
        # RIFF/WAV base64 often starts with UklGR; never store that as chat memory.
        if "UklGR" in t:
            t = t.split("UklGR", 1)[0].strip()
        # Drop absurdly long blobs (audio mislabeled as text).
        if len(t) > 2000:
            t = t[:2000].rsplit(" ", 1)[0].strip()
        return t

    @staticmethod
    def _text_content(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    parts.append(item)
            return " ".join(parts)
        return str(content or "")

    def _drop_last_context_message(self):
        """Remove the message the transcript path just appended."""
        if self._context is None:
            return
        msgs = getattr(self._context, "messages", None)
        if msgs:
            try:
                msgs.pop()
            except Exception as exc:
                logger.debug("could not drop context message: {}", exc)

    def _append_context(self, role: str, text: str):
        if self._context is not None:
            self._context.add_message({"role": role, "content": text})

    async def _stream_speak(self, prompt: str, on_text=None) -> str:
        reply_parts: list[str] = []
        started = False
        # The talker sends a ~0.3 s first chunk, then stalls ~0.7 s before the next 2 s
        # block. Forwarding the first chunk alone plays it and then runs the browser dry
        # (audible stutter in the first second). Hold audio until this much is queued;
        # after that the stream runs well ahead of real time. 0 disables.
        prebuffer_bytes = 0
        prebuffer_secs = float(os.environ.get("OMNI_AUDIO_PREBUFFER_SECS", "0.5"))
        pending: list[tuple[bytes, int, int]] = []
        pending_bytes = 0

        async def flush_pending():
            nonlocal pending_bytes
            for p, s, c in pending:
                await self.push_frame(TTSAudioRawFrame(audio=p, sample_rate=s, num_channels=c))
            pending.clear()
            pending_bytes = 0

        async for kind, piece in self._client.speak_stream(
            prompt,
            max_tokens=self._settings.max_tokens,
            speaker=self._settings.speaker,
        ):
            if kind == "text":
                reply_parts.append(piece)
                if on_text:
                    on_text(piece)
                # The callers append the cleaned reply to context themselves; letting the
                # pipeline's assistant aggregator also store these frames put every reply
                # into the history twice.
                text_frame = LLMTextFrame(text=piece)
                text_frame.append_to_context = False
                await self.push_frame(text_frame)
                continue

            pcm, sr, ch = decode_wav_b64(piece)
            if not started:
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TTSStartedFrame())
                started = True
                self._speaking = True
                prebuffer_bytes = int(sr * 2 * ch * prebuffer_secs)
            if prebuffer_bytes:
                pending.append((pcm, sr, ch))
                pending_bytes += len(pcm)
                if pending_bytes >= prebuffer_bytes:
                    await flush_pending()
                    prebuffer_bytes = 0
                continue
            await self.push_frame(
                TTSAudioRawFrame(audio=pcm, sample_rate=sr, num_channels=ch)
            )

        if started:
            await flush_pending()  # reply shorter than the prebuffer
            await self.push_frame(TTSStoppedFrame())
            await self.push_frame(LLMFullResponseEndFrame())
            self._speaking = False
        return "".join(reply_parts)
