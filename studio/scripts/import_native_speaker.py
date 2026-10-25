"""Bring the native-speaker labelling site (github.com/areffarhadi/native-speaker-labeling) into Omni Studio
as a shared task.

Each speaker becomes one audio item: their three longest utterances in that language, joined into one clip
(as the old site did), in the group of their language. People choose a language, hear a speaker and answer
"Is this speaker a native {language} speaker?" (native / not native) plus an optional accent. Everyone labels
every speaker of the language they choose, as on the old site. Labels already given there (results.json, or
the per-user files in user_results/) come in as answers.

    .venv/bin/python scripts/import_native_speaker.py --dataset /path/to/TidyVoiceX_Dev \
        --index /path/to/native-speaker-labeling/file_index.json [--results results.json ...] [--live --listed]

With --no-audio (and --index) every speaker is listed from file_index.json before the recordings are here;
their page says the recording comes soon, and a later run with --dataset adds the audio to the same items.
Without --dataset or --no-audio it only creates the task (its page, questions and settings) so the data can be added later;
--task <id> fills or updates an existing task. Item IDs come from language + speaker, so running it again
(more speakers, new audio) keeps the answers already given.
"""
import hashlib
import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import shared_tasks as ST  # noqa: E402
from backend.util import now_iso, write_json  # noqa: E402

LANG_NAMES = {
    "ar": "Arabic", "ba": "Bashkir", "be": "Belarusian", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan",
    "cv": "Chuvash", "cy": "Welsh", "de": "German", "dv": "Dhivehi", "el": "Greek", "en": "English", "fa": "Persian",
    "fr": "French", "ha": "Hausa", "hi": "Hindi", "hsb": "Upper Sorbian", "hy-AM": "Armenian", "ja": "Japanese",
    "ka": "Georgian", "lg": "Luganda", "lt": "Lithuanian", "mk": "Macedonian", "ml": "Malayalam", "mr": "Marathi",
    "nl": "Dutch", "or": "Odia", "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "ta": "Tamil", "th": "Thai",
    "tk": "Turkmen", "tr": "Turkish", "ug": "Uyghur", "uz": "Uzbek", "yo": "Yoruba", "yue": "Cantonese",
    "zh-CN": "Chinese (Mandarin)",
}
NATIVE, NOT_NATIVE = "native", "not native"

DESIGN = {
    "title": "Native speaker labelling",
    "summary": "Listen to short recordings and tell whether the speaker is a native speaker of the language. 39 languages.",
    "instructions": ("Choose a language you speak well. For each speaker you hear a few of their recordings, joined into "
                     "one clip. Listen, then say whether they sound like a native {group} speaker. If you can tell the "
                     "accent or dialect, note it; otherwise leave it empty."),
    "fields": [
        {"id": "native", "type": "choice", "label": "Is this speaker a native {group} speaker?",
         "options": [NATIVE, NOT_NATIVE], "required": True},
        {"id": "accent", "type": "text", "label": "Accent (optional)", "placeholder": "e.g. regional accent, dialect…",
         "multiline": False, "required": False},
    ],
    "look": {"theme": "forest", "accent": "", "layout": "stacked"},
    "settings": {"labels_per_item": 0, "max_per_session": 0, "allow_skip": True, "shuffle": True, "ask_name": False,
                 "group_label": "Language"},
}


def wav_seconds(p: Path) -> float:
    try:
        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return p.stat().st_size / 1e9


def three_longest(dataset: Path, speaker: str, lang: str, fallback: str) -> list:
    d = dataset / speaker / lang
    clips = sorted(d.glob("*.wav"), key=wav_seconds, reverse=True)[:3] if d.is_dir() else []
    if not clips and fallback and (dataset / fallback).is_file():
        clips = [dataset / fallback]
    return clips


def join(clips: list, out: Path) -> float:
    """One WAV of the clips in order; a clip in another format ends the join (as the old site did)."""
    with wave.open(str(clips[0]), "rb") as w0:
        p0 = w0.getparams()
        fmt0 = (p0.nchannels, p0.sampwidth, p0.framerate)
        frames = [w0.readframes(w0.getnframes())]
    for c in clips[1:]:
        with wave.open(str(c), "rb") as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != fmt0:
                break
            frames.append(w.readframes(w.getnframes()))
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as o:
        o.setnchannels(p0.nchannels)
        o.setsampwidth(p0.sampwidth)
        o.setframerate(p0.framerate)
        for f in frames:
            o.writeframes(f)
    return round(sum(len(f) for f in frames) / (p0.nchannels * p0.sampwidth * p0.framerate), 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", help="the TidyVoiceX_Dev folder (speaker/lang/*.wav); without it only the task is created")
    ap.add_argument("--index", help="file_index.json of the old site (needed with --dataset)")
    ap.add_argument("--task", help="fill or update this existing shared task instead of creating one")
    ap.add_argument("--no-audio", action="store_true", help="list the speakers from --index without their recordings")
    ap.add_argument("--results", nargs="*", default=[], help="results.json and/or user_results/*.json to bring in")
    ap.add_argument("--owner", default="local", help="studio user id of the owner (local while sign-in is off)")
    ap.add_argument("--live", action="store_true", help="open the link right away")
    ap.add_argument("--listed", action="store_true", help="list it on the public Task sharing page")
    a = ap.parse_args()
    if a.task:
        tid = ST.get(a.task)["id"]
    else:
        tid = ST.create({"id": a.owner, "name": ""}, DESIGN["title"])["id"]
        ST.update(tid, {"design": DESIGN})
    if not a.dataset and not a.no_audio:
        print(f"Shared task {tid} created with its page and questions, no data yet.\n"
              f"Add the data later with: --task {tid} --dataset ... --index ...")
        return
    if not a.index:
        ap.error("--index is needed with --dataset or --no-audio")
    dataset = Path(a.dataset).expanduser() if a.dataset else None
    index = json.loads(Path(a.index).expanduser().read_text())
    media = ST.ROOT / tid / "media"
    items, by_key, missing = [], {}, 0
    for lang, entries in sorted(index.items(), key=lambda kv: LANG_NAMES.get(kv[0], kv[0])):
        name = LANG_NAMES.get(lang, lang)
        for e in entries:
            iid = "i" + hashlib.sha1(f"{lang}:{e['speaker']}".encode()).hexdigest()[:10]  # stable across runs
            clips = three_longest(dataset, e["speaker"], lang, e.get("file", "")) if dataset else []
            if not clips:
                missing += 1
                if dataset and not a.no_audio:
                    continue
                it = {"id": iid, "kind": "audio", "name": e["speaker"], "source": "index", "pending": True, "group": name,
                      "info": {"speaker": e["speaker"], "language": lang, "file": e.get("file", "")}}
                items.append(it)
                by_key[f"{lang}:{e['speaker']}"] = it
                continue
            rel = f"{lang}/{e['speaker']}.wav"
            dur = join(clips, media / rel)
            it = {"id": iid, "kind": "audio", "name": e["speaker"], "rel_path": rel, "source": "upload",
                  "duration": dur, "playable": True, "group": name,
                  "info": {"speaker": e["speaker"], "language": lang, "clips": [str(c.relative_to(dataset)) for c in clips]}}
            items.append(it)
            by_key[f"{lang}:{e['speaker']}"] = it
        print(f"  {name}: {sum(1 for i in items if i['group'] == name)} speakers")
    write_json(ST.ROOT / tid / "items.json", {"items": items})

    # labels from the old site: one session per old annotator file
    n = 0
    for f in a.results:
        data = json.loads(Path(f).expanduser().read_text())
        if not data:
            continue
        if any(x.get("name") == f"imported: {Path(f).stem}" for x in ST._sessions(tid).values()):
            print(f"  {f}: already imported, skipped")
            continue
        s = ST.start_session(ST.get(tid), f"imported: {Path(f).stem}")
        with open(ST.ROOT / tid / "answers.jsonl", "a", encoding="utf-8") as out:
            for key, r in data.items():
                it = by_key.get(key) or by_key.get(f"{r.get('language')}:{r.get('speaker')}")
                if not it:
                    continue
                vals = {"native": NATIVE if r.get("is_native") else NOT_NATIVE}
                if (r.get("accent") or "").strip():
                    vals["accent"] = r["accent"].strip()
                out.write(json.dumps({"item": it["id"], "session": s["id"], "name": s["name"], "values": vals,
                                      "at": now_iso(), "ms": None}, ensure_ascii=False) + "\n")
                n += 1
        ss = ST._sessions(tid)
        ss[s["id"]]["done"] = sum(1 for k in data if k in by_key)
        ST._save_sessions(tid, ss)

    if a.live:
        ST.update(tid, {"status": "live"})
    if a.listed:
        ST.update(tid, {"listed": True})
    print(f"\nShared task {tid}: {len(items)} speakers in {len({i['group'] for i in items})} languages"
          f"{f', {missing} without audio yet' if missing else ''}; {n} earlier labels imported."
          f"\nLink: /t/{ST.get(tid)['token']}  (status {ST.get(tid)['status']}{', listed' if a.listed else ''})")


if __name__ == "__main__":
    main()
