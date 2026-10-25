"""Create a demo project from local files (linked, never copied).

    .venv/bin/python scripts/make_demo.py [--force]

- Conservatives_s1.wav (+ .mp4 if present) with the real Qwen3-Omni / vLLM-omni annotations imported
- the mixed sample folder of the ckpt50000 demo (audio, video, images), not yet annotated
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import formats  # noqa: E402
from backend.main import _new_item, store  # noqa: E402

DESKTOP = Path.home() / "Desktop"
WAV = DESKTOP / "projects/data-pipeline/Conservatives_s1.wav"
MP4 = Path.home() / "Downloads/Conservatives_s1.mp4"
ANN = DESKTOP / "projects/eaf/Conservatives_s1_omni_vllm_V2_annotations.json"
SAMPLES = DESKTOP / "Omni_ckpt50000_demo/samples"
PID = "demo-omni-annotations"

TASKS = ["asr", "forced_alignment", "phoneme", "diarization", "lid", "ser", "sound_quality", "ner",
         "audio_caption", "fer", "video_caption", "image_caption"]


def main(force: bool):
    existing = {p["id"] for p in store.list_projects()}
    if PID in existing:
        if not force:
            print(f"Project '{PID}' already exists (use --force to rebuild).")
            return
        store.delete_project(PID)
    p = store.create_project("Demo · Omni annotations", (
        "Conservative Party conference speech with real Qwen3-Omni (vLLM-omni) annotations, "
        "plus a mixed folder of audio, video and image samples to try 'Run tasks' on."), TASKS)
    assert p["id"] == PID, p["id"]

    ann = json.loads(ANN.read_text()) if ANN.exists() else None
    for path in (WAV, MP4):
        if not path.exists():
            print("skip (missing):", path)
            continue
        item = _new_item(path, f"speech/{path.name}", "linked")
        store.add_media(PID, [item])
        if ann:
            imported = formats.import_omni_json(ann)
            doc = formats.merge_import(store.load_doc(PID, item["id"]), imported)
            store.save_doc(PID, item["id"], doc)
            print(f"linked {path.name}: {len(imported['segments'])} segments imported")

    if SAMPLES.exists():
        items = []
        for f in sorted(SAMPLES.iterdir()):
            if f.suffix.lower() in {".wav", ".mp4", ".jpg", ".png"}:
                items.append(_new_item(f, f"samples/{f.name}", "linked"))
        store.add_media(PID, items)
        print(f"linked {len(items)} sample files")
    print("done ->", f"http://127.0.0.1:8710/#/p/{PID}")


if __name__ == "__main__":
    main("--force" in sys.argv)
