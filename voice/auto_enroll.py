"""Silent owner capture for the RFOMNI voice agent (V5).

V4 registers the owner by asking: the user presses "Register my voice", the agent
consumes their next turn as an enrolment take and says so out loud. V5 keeps the same
gate and the same voiceprint arithmetic but takes the enrolment from the conversation
itself, without a prompt and without the agent mentioning it.

THE RULE, as the product states it:

  * The first voice of a conversation is the candidate owner.
  * One turn is usually not enough speech to enrol from, so the candidate is carried
    over: if the next turn is the same voice, the two turns are concatenated and the
    pair is enrolled once they add up to ``min_enroll_s``.
  * If the next turn is somebody else, it starts its own candidate, and the turn after
    that decides between them — whichever earlier voice it matches is merged with it and
    enrolled.
  * Three turns from three different voices means the conversation has no obvious owner.
    Everything collected is thrown away and the procedure starts again from the next
    turn.

WHY CLUSTER RATHER THAN TAKE TURN ONE. "Enrol the first speaker" is one embedding of a
possibly two-second utterance, and short single takes are the weakest voiceprints this
stack produces. Grouping turns by who said them and concatenating a group gives the
embedder several seconds of one voice in several intonations — the same thing the manual
flow achieves by asking for a second take, obtained without asking.

NOTHING IS SPOKEN AND NOTHING IS GATED. Capture runs alongside a turn that is answered
normally; the only user-visible consequence is that the "Only my voice" control stops
being greyed out, because a voice is now registered. Turning that control on stays the
user's decision.

Enrolments live in memory for the session only, exactly as in V4 — a voiceprint is
biometric data and nothing here needs it to outlive the call.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from loguru import logger

from speaker_gate import GateError, SpeakerGate

#: Master switch. 0 restores V4's press-a-button enrolment exactly.
AUTO_ENROLL = os.environ.get("OMNI_AUTO_ENROLL", "1") not in ("0", "false", "no")

#: Cosine at which two turns are called the same person. This is a *clustering*
#: decision, not an access decision: getting it wrong only means a worse enrolment or a
#: restart, never a refusal, and both sides are fresh full-length utterances rather than
#: one enrolment against a later turn. It therefore sits higher than the gate threshold,
#: near the middle of the measured owner/impostor window (impostors reach 0.193, owners
#: start at 0.681 on VoxConverse with this embedder).
#: None means "track the gate's threshold", which is what makes this survive a model swap:
#: ecapa512's scores sit lower than samresnet34's, and a number tuned against one model
#: would cluster the other's turns wrongly. Set OMNI_AUTO_MATCH to pin it.
_MATCH_ENV = os.environ.get("OMNI_AUTO_MATCH")
MATCH_THRESHOLD = float(_MATCH_ENV) if _MATCH_ENV else None

#: Turns shorter than this are not embedded at all. The embedder refuses under ~0.5 s,
#: and a sliver of speech clusters badly — "yes" lands near everybody.
MIN_TURN_S = float(os.environ.get("OMNI_AUTO_MIN_TURN_S", "1.0"))

#: How many distinct voices may appear before the attempt is abandoned and restarted.
#: Three is the number the product rule names.
MAX_DISTINCT = int(os.environ.get("OMNI_AUTO_MAX_DISTINCT", "3"))

#: Ceiling on the audio kept per candidate. Enrolment needs seconds, not minutes, and
#: this bounds the memory a long unenrolled conversation can hold.
MAX_CANDIDATE_S = float(os.environ.get("OMNI_AUTO_MAX_CANDIDATE_S", "20.0"))


def _cosine(a: list[float], b: list[float]) -> float:
    """Both sides arrive unit-norm from the embedder, so a dot product is cosine."""
    return sum(x * y for x, y in zip(a, b))


@dataclass
class _Candidate:
    """One voice heard so far, and the audio attributed to it."""

    embeddings: list[list[float]] = field(default_factory=list, repr=False)
    pcm: bytearray = field(default_factory=bytearray, repr=False)
    centroid: list[float] = field(default_factory=list, repr=False)
    turns: int = 0

    def add(self, emb: list[float], pcm: bytes, max_bytes: int) -> None:
        self.embeddings.append(emb)
        self.pcm.extend(pcm)
        if len(self.pcm) > max_bytes:
            # Keep the most recent audio: it is the closest to how the speaker sounds
            # now, which is what later turns will be compared against.
            del self.pcm[: len(self.pcm) - max_bytes]
        self.turns += 1
        n, dim = len(self.embeddings), len(emb)
        c = [sum(e[i] for e in self.embeddings) / n for i in range(dim)]
        norm = sum(x * x for x in c) ** 0.5 or 1.0
        self.centroid = [x / norm for x in c]

    def seconds(self, sample_rate: int) -> float:
        return len(self.pcm) / 2 / sample_rate


@dataclass
class AutoEnroller:
    """Watches completed user turns and registers the owner when one is identifiable."""

    gate: SpeakerGate
    enabled: bool = AUTO_ENROLL
    match_threshold: float | None = MATCH_THRESHOLD
    min_turn_s: float = MIN_TURN_S
    max_distinct: int = MAX_DISTINCT
    max_candidate_s: float = MAX_CANDIDATE_S

    #: What the last observed turn did, for the UI pill and the log.
    state: str = "idle"
    #: Distinct voices in the attempt under way.
    distinct: int = 0
    #: Seconds of the leading candidate's speech banked so far.
    seconds: float = 0.0
    #: Attempts abandoned because three voices disagreed.
    restarts: int = 0

    _candidates: list[_Candidate] = field(default_factory=list, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # --- lifecycle ------------------------------------------------------------------

    @property
    def match(self) -> float:
        """The cosine two turns must reach to be called the same person.

        Read through a property rather than fixed at construction because the gate only
        learns the model's threshold when ``probe()`` runs, which is after this object
        exists. Unpinned, it tracks that threshold: the same operating point the gate
        admits at is a defensible place to group turns, and it moves with the model
        instead of being a constant tuned against one of them.
        """
        if self.match_threshold is not None:
            return self.match_threshold
        return self.gate.threshold

    def reset(self) -> None:
        """Forget the attempt in progress. The next turn is a first turn again."""
        self._candidates.clear()
        self.distinct = 0
        self.seconds = 0.0
        self.state = "idle"

    def set_enabled(self, on: bool) -> bool:
        self.enabled = bool(on)
        if not self.enabled:
            self.reset()
        logger.info("auto-enrolment {}", "ENABLED" if self.enabled else "disabled")
        return self.enabled

    @property
    def active(self) -> bool:
        """True while a turn should be fed here — on, usable, and nobody registered yet."""
        return bool(
            self.enabled
            and self.gate.available
            and not self.gate.enrolled
            and not self.gate.arming
        )

    # --- the state machine ------------------------------------------------------------

    async def observe(self, pcm: bytes, sample_rate: int) -> bool:
        """Fold one finished user turn into the attempt. True when it enrolled someone.

        Safe to call on every turn: it returns immediately unless capture is active, and
        it never raises into the turn — a capture failure leaves the session exactly as
        it was, unregistered, and the next turn tries again.
        """
        if not self.active:
            return False
        async with self._lock:
            # Re-checked under the lock: two turns can arrive while the first embeds,
            # and the second must not enrol a voice on top of the first's result.
            if not self.active:
                return False
            try:
                return await self._observe_locked(pcm, sample_rate)
            except GateError as exc:
                logger.info("auto-enrol: skipped a turn ({})", exc)
                return False
            except Exception:
                logger.exception("auto-enrol: turn failed, attempt left intact")
                return False

    async def _observe_locked(self, pcm: bytes, sample_rate: int) -> bool:
        secs = len(pcm) / 2 / sample_rate
        if secs < self.min_turn_s:
            self.state = "too-short"
            logger.debug("auto-enrol: ignoring {:.1f}s turn", secs)
            return False

        emb = await self.gate._embed(pcm, sample_rate)
        max_bytes = int(self.max_candidate_s * sample_rate) * 2

        best, best_score = None, None
        for cand in self._candidates:
            score = _cosine(emb, cand.centroid)
            if best_score is None or score > best_score:
                best, best_score = cand, score

        if best is not None and best_score is not None and best_score >= self.match:
            best.add(emb, pcm, max_bytes)
            self.state = "matched"
            logger.info(
                "auto-enrol: turn matches candidate {} (score {:.3f}, {:.1f}s banked)",
                self._candidates.index(best) + 1,
                best_score,
                best.seconds(sample_rate),
            )
            target = best
        else:
            cand = _Candidate()
            cand.add(emb, pcm, max_bytes)
            self._candidates.append(cand)
            self.state = "new-voice"
            logger.info(
                "auto-enrol: new voice, candidate {} of {} (best match {})",
                len(self._candidates),
                self.max_distinct,
                f"{best_score:.3f}" if best_score is not None else "none",
            )
            target = cand

        self.distinct = len(self._candidates)
        self.seconds = round(target.seconds(sample_rate), 2)

        if target.seconds(sample_rate) >= self.gate.min_enroll_s:
            return await self._register(target, sample_rate)

        if len(self._candidates) >= self.max_distinct:
            # Three turns, three voices, and none of them long enough to enrol on its
            # own: there is no owner to infer here. Drop everything rather than register
            # whoever happened to speak most.
            self.restarts += 1
            self.reset()
            self.state = "restarted"
            logger.info(
                "auto-enrol: {} distinct voices in {} turns — restarting the attempt",
                self.max_distinct,
                self.max_distinct,
            )
            return False

        self.state = "collecting"
        return False

    async def _register(self, cand: _Candidate, sample_rate: int) -> bool:
        """Enrol a candidate's concatenated audio as the owner's voiceprint.

        The concatenation is handed to the ordinary ``add_take`` rather than to the
        centroid directly: one embedding of the merged audio is what the gate would have
        got from a single long enrolment take, and routing through the same method keeps
        the manual and automatic paths producing identical state.
        """
        merged = bytes(cand.pcm)
        info = await self.gate.add_take(merged, sample_rate)
        self.state = "registered"
        self.seconds = round(cand.seconds(sample_rate), 2)
        logger.info(
            "auto-enrol: registered the owner silently from {} turn(s), {:.1f}s",
            cand.turns,
            info["seconds"],
        )
        self._candidates.clear()
        self.distinct = 0
        return True

    # --- UI ----------------------------------------------------------------------------

    def stats(self) -> dict:
        """Merged into the gate's ``rfomni-gate`` payload, not sent on its own."""
        return {
            "autoSupported": True,
            "autoEnroll": self.enabled,
            "autoActive": self.active,
            "autoState": self.state,
            "autoDistinct": self.distinct,
            "autoMaxDistinct": self.max_distinct,
            "autoSeconds": self.seconds,
            "autoNeeded": self.gate.min_enroll_s,
            "autoRestarts": self.restarts,
            "autoMatch": self.match,
        }
