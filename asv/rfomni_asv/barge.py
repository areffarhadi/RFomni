"""Only the enrolled speaker may cut the agent off mid-sentence.

THE PROBLEM. Barge-in fires on VAD, roughly 200 ms after speech starts. Identity needs
*audio* — the embedder will not even score a clip under 0.5 s. So "only I can interrupt"
cannot be decided at the moment the interruption happens. Something has to give, and what
gives here is latency: the agent keeps talking while the interrupting speech accumulates,
and is cut off the instant the speaker is recognised.

    speech starts while the agent is talking
      │
      ├─ 0.7 s  → recognised? → interrupt
      ├─ 1.5 s  → recognised? → interrupt
      ├─ 2.5 s  → recognised? → interrupt
      └─ still not → keep talking, drop their audio

WHY PROGRESSIVE RATHER THAN ONE CHECK. A short window recognises the owner most of the
time but not always, and deciding once would refuse them outright. Measured on
VoxConverse:

    window   owner accepted   owner min score   impostor max score
    0.7 s    5/6              0.099             0.268
    1.5 s    5/6              0.411             0.309
    2.5 s    6/6              0.610             0.284

An impostor never cleared 0.36 at *any* window, so the risk was never letting a stranger
in — it was refusing the owner. Re-checking gives the owner the fast path when the first
window is already convincing, and costs one extra embed only when it is not.

REFUSAL IS SILENT, unlike the utterance gate. A spoken "I only answer the enrolled
speaker" would collide with the sentence the agent is still saying, so an unauthorised
overlap is simply dropped. A stranger addressing an *idle* agent still gets whatever your
utterance gate does.

GATE ON PLAYBACK, NOT GENERATION. Tell this controller the agent is speaking for as long
as the user can *hear* it. With a TTS that returns a whole reply at once, generation
finishes almost immediately while the transport plays those 6-8 seconds out, so keying
off generation leaves the whole audible span unprotected — which is to say, unprotected
in practice.

Usage, from whatever your framework calls these moments:

    ctrl = BargeInController(gate, sample_rate=16000)

    # VAD says the user started talking
    if await ctrl.on_speech_start(agent_speaking=bot_is_audible):
        interrupt_the_agent()          # not held: nothing to protect, or gate is off

    # every captured chunk, while the user talks
    decision = await ctrl.on_audio(chunk)
    if decision and decision.allowed:
        interrupt_the_agent()

    # the user stopped talking
    ctrl.on_speech_end()
    if ctrl.drop_turn:
        discard_the_utterance()        # someone else talked over the agent
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .gate import GateError, SpeakerGate

try:
    from loguru import logger
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger("rfomni_asv")

#: Back-date the check window by this much so it sees the speech VAD needed in order to
#: fire, not just what arrives after it fired. Without it every checkpoint is short by
#: the VAD's own latency.
VAD_LEAD_S = 0.3


@dataclass
class BargeDecision:
    """The outcome of one checkpoint that actually settled the matter."""

    allowed: bool
    score: float | None
    seconds: float
    #: True when the embedder failed and ``fail_open`` decided this instead of a score.
    degraded: bool = False


class BargeInController:
    """The progressive-checkpoint state machine, owning its own audio buffer.

    One per session. It is not thread-safe and does not need to be: every method is
    called from the same event loop as the audio callback.
    """

    def __init__(self, gate: SpeakerGate, sample_rate: int = 16000):
        self.gate = gate
        self.sample_rate = sample_rate
        self._buf = bytearray()
        self._pending = False
        self._checking = False
        self._idx = 0
        self._task: asyncio.Task | None = None
        #: Set when the interrupter turned out to be someone else. The caller discards
        #: their utterance instead of answering it.
        self.drop_turn = False

    # --- lifecycle ------------------------------------------------------------------

    async def on_speech_start(self, agent_speaking: bool, lead_pcm: bytes = b"") -> bool:
        """Returns True if the caller should interrupt the agent *now*.

        False means the interruption is held pending a speaker check. ``lead_pcm`` is any
        audio your VAD buffered before it fired; pass it if you have it, and the
        controller back-dates the window by :data:`VAD_LEAD_S` if you do not.
        """
        self.drop_turn = False
        self._buf = bytearray(lead_pcm)
        if not (self.gate.guards_barge_in and agent_speaking):
            # Nothing playing to protect, or no enrolled voice to protect it for:
            # interrupt at once, which is what the pipeline would have done anyway.
            self._pending = False
            return True
        self._pending = True
        self._checking = False
        self._idx = 0
        logger.debug("barge-in held for speaker check")
        return False

    async def on_audio(self, pcm: bytes) -> BargeDecision | None:
        """Feed captured audio. Returns a decision only at the checkpoint that settles it.

        None means "still undecided, keep the agent talking". A decision with
        ``allowed=True`` means interrupt now; with ``allowed=False`` the agent finishes
        and :attr:`drop_turn` is set.
        """
        self._buf.extend(pcm)
        if not self._pending or self._checking:
            return None
        need = self.gate.barge_checkpoints[self._idx]
        if len(self._buf) < int(self.sample_rate * 2 * need):
            return None

        self._checking = True
        snapshot = bytes(self._buf)
        try:
            return await self._decide(snapshot)
        finally:
            self._checking = False

    def on_speech_end(self) -> None:
        """The user stopped talking. Any check still in flight is abandoned.

        A held interruption that never resolved means the agent was never cut off, which
        is the correct outcome: the interrupter stopped before proving who they were.
        """
        self._pending = False
        self._cancel()

    def reset(self) -> None:
        """Back to the state of a fresh session — call this when the enrolment changes."""
        self._pending = False
        self._checking = False
        self._idx = 0
        self._buf.clear()
        self.drop_turn = False
        self._cancel()

    def release(self) -> bool:
        """Stop holding an interruption, e.g. because the barge gate was just switched off.

        Returns True if an interruption was being held, so the caller knows to interrupt
        the agent now rather than leave the user talking to a machine that ignores them.
        Switching off has to be immediate, not next-turn.
        """
        was_pending = self._pending
        self._pending = False
        self._cancel()
        return was_pending

    # --- internals ------------------------------------------------------------------

    def _cancel(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()

    async def _decide(self, pcm: bytes) -> BargeDecision | None:
        """Identify the interrupter: cut the agent off, keep listening, or ignore them."""
        last = self._idx >= len(self.gate.barge_checkpoints) - 1
        secs = len(pcm) / (self.sample_rate * 2)
        degraded = False
        try:
            score, accepted = await self.gate.verify(
                pcm, self.sample_rate, allow_diarization=False
            )
            self.gate.last_barge_score = round(score, 3)
        except GateError as exc:
            # No further checkpoint will do better if the service is down, so this is
            # final either way — fail_open decides which way.
            accepted, last, degraded = self.gate.fail_open, True, True
            score = None
            logger.warning(
                f"barge check failed ({exc}) — "
                f"{'interrupting' if accepted else 'ignoring'}"
            )

        if accepted:
            self._pending = False
            logger.info(
                f"barge-in allowed after {secs:.1f}s score={self.gate.last_barge_score}"
            )
            return BargeDecision(True, score, secs, degraded)

        if not last:
            # Not recognisable yet. Keep the agent talking and look again once more of
            # the utterance has arrived — a short window understates a genuine match.
            self._idx += 1
            logger.debug(
                f"barge-in undecided at {secs:.1f}s "
                f"(score={self.gate.last_barge_score}), "
                f"waiting for {self.gate.barge_checkpoints[self._idx]:.1f}s"
            )
            return None

        # Out of checkpoints: someone else talked over the agent. Let it finish and drop
        # their audio — a spoken refusal here would collide with the sentence in progress.
        self._pending = False
        self.drop_turn = True
        logger.info(
            f"barge-in refused after {secs:.1f}s score={self.gate.last_barge_score}"
        )
        return BargeDecision(False, score, secs, degraded)

    @property
    def holding(self) -> bool:
        """True while an interruption is held and undecided."""
        return self._pending
