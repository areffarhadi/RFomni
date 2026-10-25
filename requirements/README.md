# Requirements

One file per virtualenv `platform/setup.sh` builds. Run `setup.sh` rather than these by
hand — it creates each venv, installs in the right order, and verifies afterwards.

| File | venv | Needed for |
|---|---|---|
| `asv.txt` | `$STACK/venvs/asv` | speaker verification — the V5.1 gate |
| `pipecat-agent.txt` | `$STACK/pipecat/.venv` | the voice agent |
| `diarizen.txt` | `$STACK/venvs/diarizen` | optional speaker separation (`DIARIZER=1`) |

**Open WebUI is not listed here.** It pins its own backend dependencies, and `setup.sh`
installs them from `backend/requirements.txt` inside the clone it makes — copying that list
into this repository would only let the two versions drift.

No model weights are installed by any of these. Everything the platform loads comes from
the shared store on `/data/d01`; the only downloads are Python wheels.
