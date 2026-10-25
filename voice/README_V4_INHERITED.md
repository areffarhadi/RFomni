# RFOMNI V3 — goal-speaker recognition, now including barge-in

V3 is V2 plus one rule: **only the enrolled speaker can interrupt the agent mid-sentence.**
Everyone else talks over it and is ignored — the agent finishes its sentence and their audio
is discarded.

```bash
bash start_v3.sh                   # agent on :7880, embedder on :8610
```

Standalone UI: `https://<host>:7880/client/` · Open WebUI: point `PIPECAT_BASE_URL` at `:7880`.

## The problem this solves

Barge-in fires on VAD, roughly 200 ms after speech starts. Identity needs *audio* — the
embedder will not even score a clip under 0.5 s. So "only I can interrupt" cannot be decided
at the moment the interruption would normally happen.

V3 therefore makes `Qwen3OmniVoiceService` the pipeline's **sole interruption authority**.
The user aggregator is built with `enable_interruptions=False`, and the service broadcasts
the interruption itself:

```text
user starts speaking
  ├─ nothing playing, or no voice enrolled → interrupt at once   (V1/V2 behaviour)
  └─ agent is speaking and a voice is enrolled → HOLD the interruption
        ├─ 0.7 s of speech → recognised?  → interrupt
        ├─ 1.5 s           → recognised?  → interrupt
        ├─ 2.5 s           → recognised?  → interrupt
        └─ still not       → keep talking, drop their audio
```

**Why progressive rather than one check.** A short window recognises the owner most of the
time but not always, and deciding once would refuse them outright. Measured on VoxConverse:

| window | owner accepted | owner min score | impostor max score |
|---|---|---|---|
| 0.7 s | 5/6 | 0.099 | 0.268 |
| 1.5 s | 5/6 | 0.411 | 0.309 |
| 2.5 s | **6/6** | 0.610 | 0.284 |

An impostor never cleared 0.36 at *any* window, so the risk was never letting a stranger in
— it was refusing the owner. Re-checking gives the owner the fast path when the first window
is already convincing, and costs one extra ~0.1 s CPU embed only when it is not.

**The gate keys off playback, not generation.** The talker hands back a whole reply as a
single WAV, so generation finishes almost immediately while the transport plays those
6-8 seconds out. `_speaking` therefore says nothing useful about whether the user can hear
the agent; `_agent_holding_the_floor` combines it with `_bot_audible`, tracked from the
output transport's own `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` (which arrive
upstream and do bracket real playback). Keying the decision off generation alone leaves the
whole audible span unprotected — which is to say, unprotected in practice.

**Refusal is silent here, unlike V2's utterance gate.** A spoken "I only answer the enrolled
speaker" would collide with the sentence the agent is still saying, so an unauthorised
overlap is simply dropped. A stranger addressing an *idle* agent still gets V2's spoken
refusal when "Only my voice" is on.

## Measured end to end

Driving the service's real frame path with VoxConverse audio (enrol on half of speaker A's
labelled turns, then have A or B talk over the agent):

| clip | owner allowed | at | impostor allowed | their turn |
|---|---|---|---|---|
| aepyx | yes | 0.7 s | no | dropped |
| aggyz | yes | 0.7 s | no | dropped |
| aiqwk | yes | 0.7 s | no | dropped |
| bpzsc | yes | 2.5 s | no | dropped |
| eoyaz | yes | 0.7 s | no | dropped |
| sebyw | yes | 0.7 s | no | dropped |

**Owner interrupts 6/6 (5/6 immediately). Impostor kept out 6/6.**

Decision-path coverage, all passing: no enrolment → interrupts immediately; agent idle →
interrupts immediately; agent speaking + enrolled → interruption held; owner → interrupts;
stranger → ignored and dropped; overlap shorter than the first checkpoint → unresolved and
dropped; barge gate off → interrupts immediately.

## Controls

| Control | RTVI message | Effect |
|---|---|---|
| Register my voice / Add take | `enroll-voice` | next spoken turn becomes a take (≥4 s) |
| Only my voice | `set-gate {on}` | refuse strangers out loud when the agent is idle |
| **Only my barge-in** | `set-barge-gate {on}` | only the enrolled voice may interrupt |
| Clear | `reset-voice` | forget the voiceprint |
| Languages | `set-lang {lang}` | `auto`, `set`, or explicit codes — see below |

The two gates are independent: you can let anyone talk to the agent while reserving the
right to cut it off. Status arrives as `rfomni-gate`; the barge control appears only when the
agent reports `bargeGateSupported`, so V1 and V2 agents hide it.

### Activation, in detail

Everything is per session and switchable mid-call, from either UI. Nothing persists.

| State | Answer gate | Barge gate |
|---|---|---|
| No voice registered | inactive — cannot be armed | inactive |
| Voice registered | **off** until you turn it on | **on** (see below) |
| Embedder unreachable | inactive, controls hidden | inactive, controls hidden |
| Voiceprint cleared | inactive | inactive |

The two defaults differ on purpose and it is worth knowing which is which: registering a
voice does **not** start refusing strangers, but it does start guarding barge-in, because
that is the behaviour this version exists to provide. `OMNI_BARGE_GATE=0` makes barge-in
opt-in instead, leaving the control there to switch on by hand.

**Switching off is immediate, not next-turn.** Turning the barge gate off — or clearing the
voiceprint — while an interruption is being held releases that hold at once and reinstates an
overlap already marked for dropping, so the utterance in flight is not still governed by a
feature you just disabled.

Deactivation paths, all verified: fresh session inactive; arming the answer gate without an
enrolment is refused; both toggles round-trip; barge off → next interruption immediate; barge
off mid-hold → hold released; barge off after a drop → drop reinstated; clearing the
voiceprint → both inactive and hold released; embedder down → no gating at all.

## Session languages

Two situations, chosen in the Languages row:

| Control | Policy | Behaviour |
|---|---|---|
| Any language | `auto` | no restriction; the model detects the language itself |
| EN · DE · FR · ES · PT | `set` | the session accepts only those five |

The restricted policy acts on both directions. ASR is told the candidate set up front and
asked for the code of whichever one it hears, rather than detecting open-set; the reply is
pinned to the language that was detected. Speech in anything else is **not** transcribed into
one of the five — the speaker is asked out loud to switch, because a mistranscription into a
language the speaker did not use is worse than saying no. The spoken message names the
session's setting rather than claiming an inability, since the model understands far more
languages than any one session is configured for.

The set itself is configuration (`OMNI_LANG_SET`), and `set-lang` also accepts explicit codes
(`en`, or `en,de`), so a single-language lock is the same mechanism with a set of one. The
membership test runs on the transcript rather than the audio LID: text detection is the more
reliable of the two, and a restricted ASR prompt biases the audio LID toward the allowed set
even when the speech was not in it.

## Limits

- **Sub-checkpoint overlaps are dropped.** An interjection shorter than 0.7 s that lands
  while the agent is speaking is never attributed, so it cannot interrupt. Lower
  `OMNI_BARGE_CHECKPOINTS` to trade that against wrongly refusing the owner.
- **An unrecognised voice can talk for up to 2.5 s** before being dropped. It never
  interrupts, but it does occupy one CPU embed per checkpoint.
- **The owner's own barge-in costs 0.7 s** of extra agent speech, sometimes 2.5 s. Turning
  "Only my barge-in" off restores instant barge-in.
- **An idle agent still answers anyone** unless "Only my voice" is also on. The barge gate
  governs who may interrupt, not who may start a conversation — in a crowded room you want
  both toggles.
- **Echo counts as a stranger.** The agent's own voice leaking into the mic scores low and
  is ignored, which is the desired outcome but means AEC still matters for turn quality.
- Everything V2 lists still applies: verification not diarization, typed text ungated,
  enrolments in memory per session.

## Env

| Variable | Default | Meaning |
|---|---|---|
| `OMNI_BARGE_GATE` | `1` | `0` = anyone may interrupt |
| `OMNI_BARGE_CHECKPOINTS` | `0.7,1.5,2.5` | seconds of speech at which identity is checked |
| `OMNI_GATE_THRESHOLD` | `0.45` | shared with the utterance gate |
| `OMNI_GATE_FAIL_OPEN` | `1` | embedder down → allow the interruption |

## Files V3 changes over V2

| File | Change |
|---|---|
| `speaker_gate.py` | barge toggle, checkpoints, `guards_barge_in` |
| `omni_voice_service.py` | sole interruption authority; hold, progressive check, drop |
| `qwen3-omni-voice-agent-webrtc.py` | `enable_interruptions=False`; `set-barge-gate` |
| `client_overlay/rfomni-chrome.js` | "Only my barge-in" control |
