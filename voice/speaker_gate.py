"""Goal-speaker recognition for the RFOMNI voice agent (V2).

Verify that the person talking is the enrolled speaker *before* spending the model
on their utterance. This is the Pipecat port of the gate already shipped in
``omni-annotate/realtime/server.py``.

WHY NO MODEL HERE. Both models are served on loopback — the voiceprint embedder by
``omni-annotate/realtime/speaker_service_wespeaker.py`` (WeSpeaker VoxBlink2
SimAM-ResNet34) and the optional diarizer by ``realtime/diarizen_service.py``. This
module is only an HTTP client plus the centroid arithmetic, so the agent loads no torch
and takes no VRAM away from Omni.

DIARIZATION IS OPTIONAL. Verification alone judges the whole utterance, so a turn
containing the owner **and** someone else is scored as the blend and lands between the
two — near enough to the threshold that the call is arbitrary. Diarizing first separates
the speakers and scores each one purely, which turns that case from a coin-flip into a
correct answer. It costs ~90 ms per utterance and a resident GPU, so it is a toggle
rather than always on, and it applies to the utterance gate only: barge-in checkpoints
run on 0.7-2.5 s of speech, too little for a diarizer to cluster usefully.

Enrolments are in memory only, per session — a voiceprint is biometric data and nothing
here needs it to outlive the call.
"""

from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass, field

import httpx
from loguru import logger

# The embedding service from omni-annotate. Start it with:
#   $DIARIZEN/w2vbert-env/bin/python omni-annotate/realtime/speaker_service.py --port 8610
DEFAULT_URL = os.environ.get("OMNI_GATE_URL", "http://127.0.0.1:8612").rstrip("/")

#: Optional diarizer. Empty disables the feature outright.
DIARIZE_URL = os.environ.get("OMNI_DIARIZE_URL", "http://127.0.0.1:8611").rstrip("/")
#: Whether a session starts with diarization on, once a voice is enrolled.
DIARIZE_DEFAULT = os.environ.get("OMNI_DIARIZE", "0") not in ("0", "false", "no")

# THRESHOLD, measured rather than guessed. On VoxConverse with ground-truth speaker
# labels, this embedder puts owners at 0.681-0.839 and impostors at -0.082-0.193, so
# anything in 0.193-0.681 separates them on that data.
#
# The default sits low in that window rather than at its centre. Enrolment happens once,
# in one posture at one distance, while later turns vary — quieter speech, a turned head,
# a different room — and every one of those lowers a genuine score. An impostor's score
# does not rise to meet it. So the cost of a low threshold is small and the cost of a
# high one is being refused by your own assistant, which is the failure people actually
# notice. 0.35 keeps ~0.16 above the highest impostor seen and ~0.33 below the lowest
# owner.
#
# V5.1: this is now a *fallback*. The embedding service reports the threshold its own
# checkpoint was measured at, and a threshold measured for one model means nothing for
# another — ecapa512's scores sit differently from samresnet34's, so carrying 0.35 across
# the swap would silently change who gets in. ``probe()`` adopts the served number unless
# OMNI_GATE_THRESHOLD is set explicitly, in which case the operator's value wins and is
# logged as such.
THRESHOLD_ENV = os.environ.get("OMNI_GATE_THRESHOLD")
THRESHOLD_PINNED = THRESHOLD_ENV is not None
DEFAULT_THRESHOLD = float(THRESHOLD_ENV or "0.35")
DEFAULT_MIN_ENROLL_S = float(os.environ.get("OMNI_GATE_MIN_ENROLL_S", "4.0"))

# If the embedder is unreachable mid-call, do we let the turn through (1) or refuse (0)?
# Default open, matching the gate's stated bias: locking the owner out of their own
# assistant because a helper service died is the worse outcome. Set 0 for a strict gate.
FAIL_OPEN = os.environ.get("OMNI_GATE_FAIL_OPEN", "1") not in ("0", "false", "no")

# Only the enrolled speaker may cut the agent off mid-sentence.
BARGE_GATE = os.environ.get("OMNI_BARGE_GATE", "1") not in ("0", "false", "no")

# Identity is checked progressively, at these many seconds of interrupting speech: the
# agent is cut off as soon as any checkpoint clears the threshold, and keeps talking if
# none do. A short window is enough to recognise the owner most of the time but not
# always — measured on VoxConverse, 0.7 s accepted the owner in 5 of 6 clips (one
# scored 0.099) while 2 s accepted all 6, and an impostor never cleared 0.36 at any
# window. Deciding once at 0.7 s would therefore refuse the owner outright, and always
# waiting 2 s would make every barge-in sluggish; re-checking costs one extra ~0.1 s
# CPU embed only in the ambiguous case.
BARGE_CHECKPOINTS = tuple(
    float(x)
    for x in os.environ.get("OMNI_BARGE_CHECKPOINTS", "0.7,1.5,2.5").split(",")
    if x.strip()
) or (0.7, 1.5, 2.5)

class GateError(RuntimeError):
    """An enrolment or verification could not be completed."""


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """The diarizer takes a WAV; the pipeline carries raw PCM."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _cosine(a: list[float], b: list[float]) -> float:
    """Both sides arrive unit-norm from the service, so a dot product is already cosine."""
    return sum(x * y for x, y in zip(a, b))


@dataclass
class SpeakerGate:
    """Per-session enrolment state plus the verification decision."""

    base_url: str = DEFAULT_URL
    threshold: float = DEFAULT_THRESHOLD
    min_enroll_s: float = DEFAULT_MIN_ENROLL_S
    fail_open: bool = FAIL_OPEN
    timeout_s: float = 30.0

    #: gate actively refusing anyone who is not the enrolled speaker
    enabled: bool = False
    #: the next completed user turn is consumed as an enrolment take, not answered
    arming: bool = False
    #: separate the speakers in an utterance before scoring, instead of scoring the blend
    diarize: bool = DIARIZE_DEFAULT
    diarize_url: str = DIARIZE_URL
    diarize_available: bool | None = None
    last_speakers: int | None = None
    #: The enrolled speaker's audio from the last diarized turn, when the turn held more
    #: than one voice. None means "use the whole utterance".
    last_owner_pcm: bytes | None = field(default=None, repr=False)
    #: hold barge-in until the interrupter is confirmed to be the enrolled speaker
    barge_gate: bool = BARGE_GATE
    barge_checkpoints: tuple[float, ...] = BARGE_CHECKPOINTS
    last_barge_score: float | None = None

    #: True when OMNI_GATE_THRESHOLD was set explicitly, which stops probe() adopting the
    #: model's own number.
    threshold_pinned: bool = THRESHOLD_PINNED
    #: What the service says it is serving, for the UI and the log.
    model_name: str = ""
    model_threshold: float | None = None

    available: bool | None = None  # None = not probed yet
    last_score: float | None = None
    #: How many utterances this session has refused. The UI shows it instead of the
    #: agent saying anything, so a rejected speaker costs one embedding and no more.
    rejections: int = 0
    _takes: list[list[float]] = field(default_factory=list, repr=False)
    _centroid: list[float] | None = field(default=None, repr=False)

    # --- service ------------------------------------------------------------------

    async def probe(self) -> bool:
        """Check both services once, at connect time, so the UI can hide the controls."""
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.base_url}/health")
            health = r.json() if r.status_code == 200 else {}
            self.available = health.get("speaker") == "up"
            self.model_name = str(health.get("model_name") or health.get("model") or "")
            served = health.get("threshold")
            if served is not None:
                self.model_threshold = float(served)
                if self.threshold_pinned:
                    logger.info(
                        "gate: {} ships threshold {} — keeping the pinned {}",
                        self.model_name or "embedder",
                        self.model_threshold,
                        self.threshold,
                    )
                else:
                    self.threshold = self.model_threshold
                    logger.info(
                        "gate: adopting {}'s own threshold {}",
                        self.model_name or "embedder",
                        self.threshold,
                    )
        except Exception as exc:
            logger.info("speaker gate unavailable at {} ({})", self.base_url, exc)
            self.available = False

        if not self.diarize_url:
            self.diarize_available = False
        else:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(f"{self.diarize_url}/health")
                self.diarize_available = (
                    r.status_code == 200 and r.json().get("diarizen") == "up"
                )
            except Exception as exc:
                logger.info("diarizer unavailable at {} ({})", self.diarize_url, exc)
                self.diarize_available = False
        if not self.diarize_available:
            self.diarize = False
        return bool(self.available)

    async def _diarized_best(self, pcm: bytes, sample_rate: int) -> float | None:
        """Best score across the speakers the diarizer finds, or None to fall back.

        Also records the matching speaker's audio in ``last_owner_pcm`` when the turn
        held more than one voice, so the caller can transcribe the enrolled speaker
        alone instead of the mixture. With a single speaker the original audio is kept:
        segment boundaries are approximate, and stitching them would clip word edges for
        no gain.

        Segments shorter than half a second are dropped: the embedder rejects them, and a
        sliver of speech is a poor voiceprint either way.
        """
        wav = _wav_bytes(pcm, sample_rate)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(f"{self.diarize_url}/diarize_wav?id=turn", content=wav)
                r.raise_for_status()
                obj = r.json()
        except Exception as exc:
            logger.warning("diarization failed ({}) — scoring the whole utterance", exc)
            return None
        if "error" in obj:
            logger.warning("diarizer: {} — scoring the whole utterance", obj["error"])
            return None

        by_speaker: dict[str, bytes] = {}
        for turn in obj.get("turns") or []:
            start, end = float(turn.get("start", 0)), float(turn.get("end", 0))
            if end - start < 0.5:
                continue
            a, b = int(start * sample_rate) * 2, int(end * sample_rate) * 2
            by_speaker[str(turn.get("speaker"))] = by_speaker.get(str(turn.get("speaker")), b"") + pcm[a:b]
        self.last_speakers = len(by_speaker)
        if not by_speaker:
            return None

        best, best_chunk = None, None
        for label, chunk in by_speaker.items():
            if len(chunk) < sample_rate:          # under 0.5 s of audio
                continue
            try:
                score = _cosine(await self._embed(chunk, sample_rate), self._centroid or [])
            except GateError:
                continue
            if best is None or score > best:
                best, best_chunk = score, chunk
        if best_chunk is not None and len(by_speaker) > 1:
            self.last_owner_pcm = best_chunk
        return best

    async def _embed(self, pcm: bytes, sample_rate: int) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(
                    f"{self.base_url}/embed?sr={sample_rate}", content=pcm
                )
                r.raise_for_status()
                obj = r.json()
        except Exception as exc:
            raise GateError(f"embedder unreachable: {exc}") from exc
        if "error" in obj:
            raise GateError(str(obj["error"]))
        emb = obj.get("embedding")
        if not emb:
            raise GateError("embedder returned no embedding")
        return emb

    # --- enrolment ----------------------------------------------------------------

    async def add_take(self, pcm: bytes, sample_rate: int) -> dict:
        """Fold one utterance into the voiceprint.

        Several takes are averaged into a centroid: a few seconds of one intonation is a
        narrow view of a voice, and averaging is markedly more robust than a single sample.
        """
        secs = len(pcm) / 2 / sample_rate
        if secs < self.min_enroll_s:
            raise GateError(
                f"need at least {self.min_enroll_s:.0f} seconds of speech, got {secs:.1f}"
            )
        emb = await self._embed(pcm, sample_rate)

        self._takes.append(emb)
        n = len(self._takes)
        dim = len(emb)
        centroid = [sum(t[i] for t in self._takes) / n for i in range(dim)]
        norm = sum(c * c for c in centroid) ** 0.5 or 1.0
        self._centroid = [c / norm for c in centroid]

        logger.info(
            "gate enrolled take {} ({:.1f}s), consistency={}", n, secs, self.consistency
        )
        return {"takes": n, "seconds": round(secs, 2), "consistency": self.consistency}

    def reset(self) -> None:
        self._takes.clear()
        self._centroid = None
        self.enabled = False
        self.arming = False
        self.last_score = None
        self.last_barge_score = None
        self.last_speakers = None
        self.rejections = 0
        logger.info("gate enrolment cleared")

    @property
    def enrolled(self) -> bool:
        return self._centroid is not None

    @property
    def consistency(self) -> float | None:
        """Agreement between takes — the useful "is this enrolment any good" signal."""
        if len(self._takes) < 2 or self._centroid is None:
            return None
        return round(min(_cosine(t, self._centroid) for t in self._takes), 3)

    # --- verification -------------------------------------------------------------

    async def verify(
        self, pcm: bytes, sample_rate: int, *, allow_diarization: bool = True
    ) -> tuple[float, bool]:
        """Score this utterance against the enrolled voice. Returns (score, accepted).

        With diarization on, the score is the best match among the speakers found, so an
        utterance the owner shares with someone else is judged on the owner's part alone.
        ``allow_diarization=False`` forces the single-embedding path — barge-in checks run
        on too little speech for a diarizer to cluster.
        """
        if self._centroid is None:
            raise GateError("gate is on but nobody is enrolled")

        self.last_owner_pcm = None
        if allow_diarization and self.diarize and self.diarize_available:
            best = await self._diarized_best(pcm, sample_rate)
            if best is not None:
                self.last_score = round(best, 3)
                return best, best >= self.threshold

        self.last_speakers = None
        score = _cosine(await self._embed(pcm, sample_rate), self._centroid)
        self.last_score = round(score, 3)
        return score, score >= self.threshold

    def set_diarize(self, on: bool) -> bool:
        if on and not self.diarize_available:
            raise GateError("diarizer is not reachable")
        self.diarize = bool(on)
        logger.info("diarization {}", "ENABLED" if self.diarize else "disabled")
        return self.diarize

    def set_barge_gate(self, on: bool) -> bool:
        self.barge_gate = bool(on)
        logger.info("barge gate {}", "ENABLED" if self.barge_gate else "disabled")
        return self.barge_gate

    @property
    def guards_barge_in(self) -> bool:
        """True when an interruption should be held for a speaker check.

        Needs an enrolment to compare against, so with nobody registered barge-in
        behaves exactly as it does without this feature.
        """
        return bool(self.barge_gate and self.enrolled and self.available)

    def set_enabled(self, on: bool) -> bool:
        if on and not self.enrolled:
            raise GateError("register a voice before turning the gate on")
        self.enabled = bool(on)
        logger.info("gate {}", "ENABLED" if self.enabled else "disabled")
        return self.enabled

    # --- UI ------------------------------------------------------------------------

    def stats(self) -> dict:
        """RTVI payload for the client chrome / Open WebUI overlay."""
        return {
            "type": "rfomni-gate",
            "available": bool(self.available),
            "enrolled": self.enrolled,
            "gate": self.enabled,
            "arming": self.arming,
            "takes": len(self._takes),
            "consistency": self.consistency,
            "threshold": self.threshold,
            "model": self.model_name,
            "modelThreshold": self.model_threshold,
            "thresholdPinned": self.threshold_pinned,
            "minEnrollSeconds": self.min_enroll_s,
            "lastScore": self.last_score,
            "rejections": self.rejections,
            # Presence of this key is how a client knows the agent can gate barge-in.
            "diarizeSupported": True,
            "diarizeAvailable": bool(self.diarize_available),
            "diarize": self.diarize,
            "lastSpeakers": self.last_speakers,
            "bargeGateSupported": True,
            "bargeGate": self.barge_gate,
            "bargeGuarding": self.guards_barge_in,
            "bargeCheckpoints": list(self.barge_checkpoints),
            "lastBargeScore": self.last_barge_score,
        }
