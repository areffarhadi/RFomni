# RFOMNI V5 — the owner registers itself

V5 is V4 with one thing replaced: **nobody presses "Register my voice".** The voiceprint is
taken from the conversation as it happens, the agent never mentions it, and the only thing
the user notices is that the *Only my voice* controls stop being greyed out.

```bash
bash start_v5.sh                   # agent on :7900, embedder on :8612
# or the whole platform:
bash ../../run_password_v5.sh      # Open WebUI on :8080 pointed at the V5 agent
```

Everything else — the VoxBlink2 embedder, the threshold, diarization, the progressive
barge-in gate — is V4 unchanged; see `README_V4_INHERITED.md`.

## The rule

Each finished user turn is embedded and grouped with the turns that sound like it. A group
is enrolled as soon as it holds `OMNI_GATE_MIN_ENROLL_S` (4 s) of speech.

```text
turn 1  ─ first voice heard ─────────────► long enough?  → registered, done
                                         └ too short     → carry it

turn 2  ─ same voice?  → concatenate ────► long enough?  → registered, done
        └ someone else? → second candidate

turn 3  ─ matches candidate 1 or 2? → merge with it → registered
        └ a third different voice?  → no owner to infer: throw it all away
                                       and start again at the next turn
```

Nothing above blocks or consumes a turn. Every one of these turns is answered exactly as it
would have been; the capture runs beside the answer in its own task, so it adds no latency,
and a failed embed leaves the session unregistered and tries again next turn.

**Why group turns instead of just taking the first one.** One turn is often two seconds of a
single intonation, which is the weakest voiceprint this stack produces — it is why V4's
manual flow asks for a second take. Concatenating a speaker's turns gets the same benefit
without asking: several seconds of one voice, in several intonations, embedded once.

**Why the match threshold is higher than the gate's.** `OMNI_AUTO_MATCH` (0.45) only decides
*whose turn is whose*. Getting it wrong costs a worse enrolment or a restart, never a
refusal, and both sides of the comparison are full-length utterances rather than an
enrolment scored against a later turn — so it sits nearer the middle of the measured
0.193 (impostor ceiling) / 0.681 (owner floor) window than `OMNI_GATE_THRESHOLD` does.

## What the user sees

| | before a voice is known | after |
|---|---|---|
| Register my voice | hidden | hidden |
| Only my voice / barge-in / separate speakers | greyed out, "available once the agent has heard enough of one voice" | enabled |
| Pill | "Listening" | "1 take · last 0.74" |
| Agent speech | nothing about registration, ever | unchanged |

Turning the gate *on* stays the user's decision — V5 registers a voice, it does not start
refusing people by itself. **Clear** forgets the voiceprint and the capture starts over.

## Settings (`env.tuned`)

| variable | default | meaning |
|---|---|---|
| `OMNI_AUTO_ENROLL` | 1 | 0 restores V4's button exactly, button included |
| `OMNI_AUTO_MATCH` | 0.45 | cosine at which two turns are the same person |
| `OMNI_AUTO_MIN_TURN_S` | 1.0 | shorter turns are not embedded at all |
| `OMNI_AUTO_MAX_DISTINCT` | 3 | distinct voices tolerated before the attempt restarts |
| `OMNI_AUTO_MAX_CANDIDATE_S` | 20.0 | ceiling on audio banked per candidate |

The RTVI message `set-auto-enroll {on: false}` switches capture off mid-session; the
`rfomni-gate` status event carries `autoEnroll`, `autoState`, `autoDistinct` and
`autoSeconds` for debugging (the overlay deliberately does not render the progress ones).

## Files that differ from V4

```
auto_enroll.py             new — the state machine above
omni_voice_service.py      feeds each finished turn to it; merged stats payload
qwen3-omni-voice-agent-webrtc.py   set-auto-enroll handler
client_overlay/rfomni-chrome.js     hides the Register button, reworded hints
env.tuned, start_v5.sh     V5 title, port 7900, the settings above
```

## Privacy

Unchanged from V4 in substance — the voiceprint is in memory, per session, and dies with
the call — but note the difference in kind: V5 embeds turns from people who did not ask to
be enrolled, in order to work out which of them is the owner. Those embeddings live only
inside the attempt in progress and are discarded on registration or restart, and no audio
is written to disk.
