"""Owner recognition: enrol a voice, then decide whether the person talking is that voice.

This is an HTTP client plus centroid arithmetic — no model, no torch, no VRAM. Point it
at :mod:`rfomni_asv.service` and it does two jobs:

**The utterance gate.** Verify the speaker *before* spending a large model on their
utterance. ``verify()`` scores a completed turn against the enrolled voiceprint.

**The barge-in gate.** Only the enrolled speaker may cut the agent off mid-sentence. The
decision logic for that lives in :mod:`rfomni_asv.barge`, which uses this class.

DIARIZATION IS OPTIONAL. Verification alone judges the whole utterance, so a turn holding
the owner *and* someone else is scored as the blend and lands between the two — near
enough to the threshold that the call is arbitrary. Diarizing first separates the speakers
and scores each purely, which turns that case from a coin-flip into a correct answer. It
costs ~90 ms per utterance and a resident GPU, so it is a toggle rather than always on,
and it applies to the utterance gate only: barge-in checkpoints run on 0.7-2.5 s of
speech, too little for a diarizer to cluster usefully. Leave ``OMNI_DIARIZE_URL`` empty
and the feature disappears cleanly.

Enrolments are in memory only, per session — a voiceprint is biometric data and nothing
here needs it to outlive the call. :meth:`SpeakerGate.export_voiceprint` exists for the
deployments that do, and makes that a deliberate act.
"""

from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass, field

import httpx

try:  # loguru is nicer but not worth forcing on a consumer
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger("rfomni_asv")

DEFAULT_URL = os.environ.get("OMNI_GATE_URL", "http://127.0.0.1:8612").rstrip("/")

#: Optional diarizer, same contract as omni-annotate's ``diarizen_service.py``. Empty
#: disables the feature outright.
DIARIZE_URL = os.environ.get("OMNI_DIARIZE_URL", "").rstrip("/")
DIARIZE_DEFAULT = os.environ.get("OMNI_DIARIZE", "0") not in ("0", "false", "no")

# THRESHOLD, measured rather than guessed. Re-measured for this repo on 11-12 VoxConverse
# clips with ground-truth speaker labels: enrol on 3x4 s of the dominant speaker, then
# score held-out utterances from them and from the second speaker, with overlapped speech
# excluded so every clip is one voice.
#
#   utterance   lowest owner   highest impostor   separating window
#   0.7 s          -0.024            0.381        none — they interleave
#   1.5 s           0.382            0.412        none — they interleave
#   2.5 s           0.563            0.384        0.384 - 0.563
#   4.0 s           0.502            0.352        0.352 - 0.502
#
# 0.45 is the only round number inside the window at *both* lengths that separate, and no
# impostor reached it at any length. It is also why the barge-in checkpoints exist: below
# ~2 s there is no threshold that works, so a single early decision cannot be made safely
# and the gate has to wait for more speech instead.
#
# An earlier version of this block shipped 0.35, measured on a narrower 18-case set where
# impostors topped out at 0.193. On the wider set above impostors reach 0.412, so 0.35
# false-accepts. Raise rather than lower this default without re-measuring.
#
# The residual bias is still towards letting the owner in: enrolment happens once, in one
# posture at one distance, while later turns vary — quieter speech, a turned head, a
# different room — and every one of those lowers a genuine score, whereas an impostor's
# score does not rise to meet it.
DEFAULT_THRESHOLD = float(os.environ.get("OMNI_GATE_THRESHOLD", "0.45"))
#: True when the operator pinned a threshold, in which case probe() leaves it alone.
THRESHOLD_PINNED = "OMNI_GATE_THRESHOLD" in os.environ
DEFAULT_MIN_ENROLL_S = float(os.environ.get("OMNI_GATE_MIN_ENROLL_S", "4.0"))

# If the embedder is unreachable mid-call, do we let the turn through (1) or refuse (0)?
# Default open, matching the gate's stated bias: locking the owner out of their own
# assistant because a helper service died is the worse outcome. Set 0 for a strict gate —
# do that if you are gating anything that matters, rather than just who gets answered.
FAIL_OPEN = os.environ.get("OMNI_GATE_FAIL_OPEN", "1") not in ("0", "false", "no")

BARGE_GATE = os.environ.get("OMNI_BARGE_GATE", "1") not in ("0", "false", "no")

# Identity is checked progressively, at these many seconds of interrupting speech: the
# agent is cut off as soon as any checkpoint clears the threshold, and keeps talking if
# none do. A short window is enough to recognise the owner most of the time but not
# always — measured on VoxConverse, 0.7 s accepted the owner in 5 of 6 clips (one scored
# 0.099) while 2.5 s accepted all 6, and an impostor never cleared 0.36 at any window.
# Deciding once at 0.7 s would therefore refuse the owner outright, and always waiting
# 2.5 s would make every barge-in sluggish; re-checking costs one extra embed only in the
# ambiguous case.
BARGE_CHECKPOINTS = tuple(
    float(x)
    for x in os.environ.get("OMNI_BARGE_CHECKPOINTS", "0.7,1.5,2.5").split(",")
    if x.strip()
) or (0.7, 1.5, 2.5)


class GateError(RuntimeError):
    """An enrolment or verification could not be completed."""


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """The diarizer takes a WAV; the pipeline carries raw PCM."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def cosine(a, b) -> float:
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
    #: Leave the threshold alone on probe(); set automatically when OMNI_GATE_THRESHOLD is.
    threshold_pinned: bool = THRESHOLD_PINNED
    #: Which registered checkpoint the service reported, once probed.
    model_name: str | None = None

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

    available: bool | None = None  # None = not probed yet
    last_score: float | None = None
    #: How many utterances this session has refused. Show it in the UI instead of having
    #: the agent say anything, so a rejected speaker costs one embedding and no more.
    rejections: int = 0
    _takes: list[list[float]] = field(default_factory=list, repr=False)
    _centroid: list[float] | None = field(default=None, repr=False)

    # --- service ------------------------------------------------------------------

    async def probe(self) -> bool:
        """Check both services once, at connect time, so the UI can hide the controls."""
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.base_url}/health")
            obj = r.json()
            self.available = r.status_code == 200 and obj.get("speaker") == "up"
            # Each checkpoint has its own score distribution, so a threshold measured for
            # one is meaningless for another. Take the serving model's own number unless
            # the operator pinned one deliberately.
            if self.available:
                self.model_name = obj.get("model_name")
                served = obj.get("threshold")
                if (
                    not self.threshold_pinned
                    and served is not None
                    and float(served) != self.threshold
                ):
                    logger.info(
                        f"adopting threshold {served} for model "
                        f"{self.model_name or '?'} (was {self.threshold})"
                    )
                    self.threshold = float(served)
        except Exception as exc:
            logger.info(f"speaker gate unavailable at {self.base_url} ({exc})")
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
                logger.info(f"diarizer unavailable at {self.diarize_url} ({exc})")
                self.diarize_available = False
        if not self.diarize_available:
            self.diarize = False
        return bool(self.available)

    async def _embed(self, pcm: bytes, sample_rate: int) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(f"{self.base_url}/embed?sr={sample_rate}", content=pcm)
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

    async def _diarized_best(self, pcm: bytes, sample_rate: int) -> float | None:
        """Best score across the speakers the diarizer finds, or None to fall back.

        Also records the matching speaker's audio in ``last_owner_pcm`` when the turn held
        more than one voice, so the caller can transcribe the enrolled speaker alone
        instead of the mixture. With a single speaker the original audio is kept: segment
        boundaries are approximate, and stitching them would clip word edges for no gain.

        Segments shorter than half a second are dropped: the embedder rejects them, and a
        sliver of speech is a poor voiceprint either way.
        """
        wav = wav_bytes(pcm, sample_rate)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as c:
                r = await c.post(f"{self.diarize_url}/diarize_wav?id=turn", content=wav)
                r.raise_for_status()
                obj = r.json()
        except Exception as exc:
            logger.warning(f"diarization failed ({exc}) — scoring the whole utterance")
            return None
        if "error" in obj:
            logger.warning(f"diarizer: {obj['error']} — scoring the whole utterance")
            return None

        by_speaker: dict[str, bytes] = {}
        for turn in obj.get("turns") or []:
            start, end = float(turn.get("start", 0)), float(turn.get("end", 0))
            if end - start < 0.5:
                continue
            a, b = int(start * sample_rate) * 2, int(end * sample_rate) * 2
            label = str(turn.get("speaker"))
            by_speaker[label] = by_speaker.get(label, b"") + pcm[a:b]
        self.last_speakers = len(by_speaker)
        if not by_speaker:
            return None

        best, best_chunk = None, None
        for chunk in by_speaker.values():
            if len(chunk) < sample_rate:  # under 0.5 s of audio
                continue
            try:
                score = cosine(await self._embed(chunk, sample_rate), self._centroid or [])
            except GateError:
                continue
            if best is None or score > best:
                best, best_chunk = score, chunk
        if best_chunk is not None and len(by_speaker) > 1:
            self.last_owner_pcm = best_chunk
        return best

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
        n, dim = len(self._takes), len(emb)
        centroid = [sum(t[i] for t in self._takes) / n for i in range(dim)]
        norm = sum(c * c for c in centroid) ** 0.5 or 1.0
        self._centroid = [c / norm for c in centroid]

        logger.info(f"gate enrolled take {n} ({secs:.1f}s), consistency={self.consistency}")
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
        """Agreement between takes — the useful "is this enrolment any good" signal.

        Low agreement means the takes disagree about what the voice sounds like, and the
        centroid built from them will sit off to one side of all of them. Ask for another
        take rather than letting the session run on a bad print.
        """
        if len(self._takes) < 2 or self._centroid is None:
            return None
        return round(min(cosine(t, self._centroid) for t in self._takes), 3)

    def export_voiceprint(self) -> list[float]:
        """The centroid, for the deployments that persist enrolments.

        Kept separate from the dataclass fields so persisting biometric data is something
        you write a line of code to do, not something that happens by default.
        """
        if self._centroid is None:
            raise GateError("nobody is enrolled")
        return list(self._centroid)

    def load_voiceprint(self, centroid: list[float]) -> None:
        """Restore an exported centroid. ``consistency`` stays None — the individual takes
        it was computed from are gone."""
        norm = sum(c * c for c in centroid) ** 0.5 or 1.0
        self._centroid = [c / norm for c in centroid]
        self._takes.clear()

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
        score = cosine(await self._embed(pcm, sample_rate), self._centroid)
        self.last_score = round(score, 3)
        return score, score >= self.threshold

    async def allows(self, pcm: bytes, sample_rate: int) -> bool:
        """The utterance gate in one call: should this turn be answered?

        Returns True when the gate is off, when nobody is enrolled, or when the speaker is
        recognised. On a service failure it follows ``fail_open``.
        """
        if not (self.enabled and self.enrolled):
            return True
        try:
            _, accepted = await self.verify(pcm, sample_rate)
        except GateError as exc:
            logger.warning(
                f"gate verify failed ({exc}) — "
                f"{'letting it through' if self.fail_open else 'refusing'}"
            )
            return self.fail_open
        if not accepted:
            self.rejections += 1
        return accepted

    # --- toggles ------------------------------------------------------------------

    def set_diarize(self, on: bool) -> bool:
        if on and not self.diarize_available:
            raise GateError("diarizer is not reachable")
        self.diarize = bool(on)
        logger.info(f"diarization {'ENABLED' if self.diarize else 'disabled'}")
        return self.diarize

    def set_barge_gate(self, on: bool) -> bool:
        self.barge_gate = bool(on)
        logger.info(f"barge gate {'ENABLED' if self.barge_gate else 'disabled'}")
        return self.barge_gate

    def set_enabled(self, on: bool) -> bool:
        if on and not self.enrolled:
            raise GateError("register a voice before turning the gate on")
        self.enabled = bool(on)
        logger.info(f"gate {'ENABLED' if self.enabled else 'disabled'}")
        return self.enabled

    @property
    def guards_barge_in(self) -> bool:
        """True when an interruption should be held for a speaker check.

        Needs an enrolment to compare against, so with nobody registered barge-in behaves
        exactly as it does without this feature.
        """
        return bool(self.barge_gate and self.enrolled and self.available)

    # --- UI ------------------------------------------------------------------------

    def stats(self) -> dict:
        """One JSON blob for whatever chrome is showing the gate's state."""
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
            "minEnrollSeconds": self.min_enroll_s,
            "lastScore": self.last_score,
            "rejections": self.rejections,
            # Presence of these keys is how a client knows the agent supports the feature.
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
