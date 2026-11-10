"""How well the page assistant edits Task sharing and Subjective test pages (backend/pages/service._chat_page).

Each case starts from a default page, sends one message and checks the page that comes back (nothing is saved).
Needs the small model (`qwen9b start`).  Run:  .venv/bin/python -m scripts.eval_page_edits [runs]
"""
import sys
import time

from backend import shared_tasks as ST
from backend.pages import catalogue as C
from backend.pages import design as D
from backend.pages import service as SV
from backend.pages import settings as S

SV._data_line = lambda kind, obj: "Data: none uploaded yet."   # no task on disk


def start(spec) -> tuple:
    """spec: a template id, or {"tid", "questions": [...], "title", "chat": [(owner, assistant), ...]} for a page
    someone has already changed."""
    spec = {"tid": spec} if isinstance(spec, str) else spec
    tid = spec["tid"]
    tpl = C.get(tid)
    design = ST.validate_design(S.compile_task(tpl, tpl["settings"]))
    view = D.view(design, 0, tpl["modality"])
    if "questions" in spec or "title" in spec:
        view = D.clean({**view, **{k: spec[k] for k in ("questions", "title") if k in spec}}, tpl["modality"])
        design = D.build(view, design)
    chat = [m for o, a in spec.get("chat", []) for m in ({"role": "owner", "text": o}, {"role": "assistant", "text": a})]
    obj = {"id": "st00000000", "design": design, "chat": chat, "page": {"template": tid, "versions": [{"view": view, "design": design}]}}
    return obj, tpl, view


T = lambda q: {"question": q, "type": "text", "answers": [], "help": "", "required": False, "big_box": True, "hint": ""}
B = lambda q, a: {"question": q, "type": "buttons", "answers": a, "help": "", "required": True}
# the page of a real conversation (2026-11-09) after it added the entities box
ENT = {"tid": "voice-classify", "title": "Audio labels",
       "questions": [T("Import Entities"), T("Transcription"), B("Speaker Gender", ["Male", "Female"]), B("Emotion", ["Happy", "Sad", "Neutral"])],
       "chat": [("now i needa new box for iporting the entities", "Done: added “Import Entities” (a text box).")]}
ENT_FAILED = {**ENT, "chat": ENT["chat"] + [("the title of this box should be only \"Entities\"", "Nothing changed.")]}
renamed = lambda o, n: [q["question"].lower() for q in qs(n)] == ["entities", "transcription", "speaker gender", "emotion"] and n["title"] == o["title"]
FIRST = "first"   # the description given when the page is made
SC = lambda q, a, b: {"question": q, "type": "scale", "from": a, "to": b, "words": [], "answers": [], "help": "", "required": False}
EMO = B("Which emotion do you hear?", ["Neutral", "Happy", "Sad", "Angry", "Not sure"])
# a second real conversation (2026-11-09 afternoon)
FOUR = {"tid": "voice-classify", "title": "Emotion recognition",
        "questions": [EMO, T("Transcription"), {**T("Language of the utterance"), "big_box": False}, SC("Quality of the voice", 0, 10)],
        "chat": [("now i want to be able to say the quality of the voice as a sliding knob from 0 to 10", "Done: added “Quality of the voice” (scale 0 to 10).")]}
others_kept = lambda o, n: all(q in qs(n) for q in qs(o)[:3]) and any("quality" in t for t in texts(n))


qs = lambda v: v["questions"]
q_of = lambda v, kind: next(q for q in v["questions"] if q["type"] == kind)


def has(v: dict, kind: str, labels: set = frozenset(), stem: bool = False) -> bool:
    """The page has a question of this kind, with these answers or labels (any case; `stem`: "places" counts as "place")."""
    norm = lambda a: a.lower().rstrip("s") if stem else a.lower()
    return any(q["type"] == kind and {norm(x) for x in labels} <= {norm(a) for a in q.get("answers") or []} for q in v["questions"])
texts = lambda v: [q["question"].lower() for q in v["questions"]]
same_qs = lambda old, new: old["questions"] == new["questions"]
CASES = [
    ("voice-classify", "we should have a page that the audio is on top to play and then people can select male or female button. and below of that the button of each emotion which is sad/happy/neutral/ angry",
     lambda o, n: len(qs(n)) == 2 and {"Male", "Female"} <= set(qs(n)[0]["answers"]) and {"Sad", "Happy", "Neutral", "Angry"} <= set(qs(n)[1]["answers"])),
    ("voice-classify", "also ask how sure they are, from 1 to 5",
     lambda o, n: len(qs(n)) == 2 and qs(o)[0] in qs(n) and any(q["type"] == "scale" and (q["from"], q["to"]) == (1, 5) for q in qs(n))),
    ("voice-classify", "let people pick several classes", lambda o, n: len(qs(n)) == 1 and qs(n)[0]["type"] == "checkboxes"),
    ("voice-classify", "add an optional comment box",
     lambda o, n: len(qs(n)) == 2 and qs(o)[0] in qs(n) and any(q["type"] == "text" and not q["required"] for q in qs(n))),
    ("voice-classify", "3 people per clip", lambda o, n: n["rules"]["people_per_item"] == 3 and same_qs(o, n)),
    ("voice-classify", "dark colours please", lambda o, n: n["look"]["colours"] == "dark" and same_qs(o, n)),
    ("voice-classify", "ask their age before they start",
     lambda o, n: any("age" in b["question"].lower() or "old" in b["question"].lower() for b in n["before_start"]) and same_qs(o, n)),
    ("voice-classify", "remove the Not sure answer", lambda o, n: "Not sure" not in qs(n)[0]["answers"] and len(qs(n)[0]["answers"]) == len(qs(o)[0]["answers"]) - 1),
    ("voice-classify", "people should draw a box around the speaker's face", lambda o, n: same_qs(o, n)),
    ("voice-classify", "they must listen to the whole clip before answering", lambda o, n: n["rules"]["play_to_end"] and same_qs(o, n)),
    ("voice-mos", "also rate how easy it is to understand, on the same scale",
     lambda o, n: len(qs(n)) == 2 and all(q["type"] == "scale" for q in qs(n)) and qs(o)[0] in qs(n)),
    ("voice-mos", "use a 1 to 7 scale", lambda o, n: len(qs(n)) == 1 and (qs(n)[0]["from"], qs(n)[0]["to"]) == (1, 7)),
    ("text-classify", "topics are sport, politics, science and other",
     lambda o, n: len(qs(n)) == 1 and {"sport", "politics", "science", "other"} <= {a.lower() for a in qs(n)[0]["answers"]}),
    ("image-classify", "also ask if the photo is blurry: yes or no",
     lambda o, n: len(qs(n)) == len(qs(o)) + 1 and qs(o)[0] in qs(n) and any({"yes", "no"} <= {a.lower() for a in q["answers"]} for q in qs(n))),
    ("voice-transcribe", "also ask which language is spoken: English, German or French",
     lambda o, n: len(qs(n)) == 2 and qs(o)[0] in qs(n) and any({"English", "German", "French"} <= set(q["answers"]) for q in qs(n))),
    ("video-classify", "put the video above the questions", lambda o, n: n["item_position"] == "top" and same_qs(o, n)),
    ("text-rate", "show people everyone's answers at the end", lambda o, n: "everyone" in n["charts"]["last_screen"] and same_qs(o, n)),
    ("image-caption", "remove the caption box and ask them to choose: indoor or outdoor",
     lambda o, n: len(qs(n)) == 1 and qs(n)[0]["type"] == "buttons" and {"indoor", "outdoor"} <= {a.lower().rstrip("s") for a in qs(n)[0]["answers"]}),
    # a real conversation
    (ENT, "the title of this box should be only \"Entities\"", renamed),
    (ENT, "i meant the title of the box that already is \"import entites\" shuld be \"entities\"", renamed),
    (ENT, "but i want to change the title of this box to entities, instead of import entities", renamed),
    (ENT_FAILED, "why you didnt change", renamed),
    (ENT, "change the page title to Speech labels", lambda o, n: n["title"].lower() == "speech labels" and same_qs(o, n)),
    ({"tid": "voice-classify", "questions": [B("Speaker Gender", ["Male", "Female"]), B("Emotion", ["Happy", "Sad", "Neutral"]), T("Transcription")]},
     "bring the transcription window up as first task", lambda o, n: texts(n) == ["transcription", "speaker gender", "emotion"]),
    ({"tid": "voice-classify", "questions": [T("Transcription"), B("Speaker Gender", ["Male", "Female"]), B("Emotion", ["Happy", "Sad", "Neutral"])]},
     "now i needa new box for iporting the entities",
     lambda o, n: len(qs(n)) == 4 and all(q in qs(n) for q in qs(o)) and any(q["type"] == "text" and "entit" in q["question"].lower() for q in qs(n))),
    ({"tid": "voice-classify", "questions": [EMO], "chat": [("i need to make a shaerd task for my dataset for emotion recognition", "Done: title “Shared Emotion Recognition Task” · new instructions.")]},
     "good . i need to be able to write the trasncribe of the audio as well as a user. so put a box for that in addition to emotion",
     lambda o, n: len(qs(n)) == 2 and qs(n)[0] == EMO and qs(n)[1]["type"] == "text" and "transcri" in texts(n)[1]),
    (FOUR, "now i said the quality should be a continues sliding knob that user can slif it to left and right which sould be any number between 0 to 10.",
     lambda o, n: others_kept(o, n) and qs(n)[3]["type"] == "slider" and (qs(n)[3]["from"], qs(n)[3]["to"]) == (0, 10)),
    (FOUR, "but i didnt talked about \"language\" i talked aaaabout \"quality of the voice\"", others_kept),
    # it was asked for a sliding knob and made buttons: the question means "you did not do it"
    (FOUR, "do you know what is sliding knob?", lambda o, n: others_kept(o, n) and qs(n)[3]["type"] == "slider"),
    ({"tid": "voice-classify", "questions": [EMO, T("Transcription")]}, "now another box for language       of the utterance, should be free text by user",
     lambda o, n: len(qs(n)) == 3 and qs(n)[:2] == qs(o) and qs(n)[2]["type"] == "text" and "language" in texts(n)[2]),
    # a third (2026-11-09 evening): the slider
    ({"tid": "voice-classify", "questions": [EMO, B("What is the speaker's gender?", ["Male", "Female", "Not sure"]), T("What do you transcribe?")]},
     "and i need to have a sliding bar for quality of voice which people can move it on any number vbetween 0 to 10. left side should be 0 and right side should be 10",
     lambda o, n: qs(n)[:3] == qs(o) and len(qs(n)) == 4 and qs(n)[3]["type"] == "slider" and (qs(n)[3]["from"], qs(n)[3]["to"]) == (0, 10)),
    ({"tid": "voice-classify", "questions": [EMO, B("What is the speaker's gender?", ["Male", "Female", "Not sure"]), T("What do you transcribe?"),
                                             SC("How would you rate the quality of the voice?", 0, 10)]},
     "for “How would you rate the quality of the voice?”  that we made right now, instead of discrete numbers between 0 to 10 i want to have a sliding "
     "bar which user can select any number between 0 to 10. this number could be in the format of X.XX . user only move the bar based on his perception",
     lambda o, n: qs(n)[:3] == qs(o)[:3] and len(qs(n)) == 4 and qs(n)[3]["type"] == "slider" and qs(n)[3].get("decimals") == 2),
    ({"tid": "voice-classify", FIRST: True}, "i need to have a page for labeling my dataset by other people",
     lambda o, n: not SV._same_text(n["title"], "i need to have a page for labeling my dataset by other people")
     and not any("dataset" in t for t in texts(n)) and 1 <= len(qs(n)) <= 2),
    ({"tid": "voice-classify", FIRST: True}, "people listen to each clip and pick male or female, then the emotion: sad, happy, neutral or angry",
     lambda o, n: len(qs(n)) == 2 and {"Male", "Female"} <= set(qs(n)[0]["answers"]) and {"Sad", "Happy", "Neutral", "Angry"} <= set(qs(n)[1]["answers"])),
    ({"tid": "image-classify", FIRST: True}, "is there a cat, a dog or nothing in the photo",
     lambda o, n: len(qs(n)) == 1 and {"cat", "dog"} <= {a.lower() for a in qs(n)[0]["answers"]}),
    # the blocks (backend/blocks.py), 2026-11-09 night
    ("image-classify", "people should draw boxes around every car and person in the photo", lambda o, n: has(n, "boxes", {"car", "person"})),
    ("image-classify", "let them click on each person's head so we can count people", lambda o, n: has(n, "points")),
    ("image-classify", "they should outline the sky and the road", lambda o, n: has(n, "regions", {"sky", "road"})),
    ("voice-transcribe", "people should mark where each speaker talks: speaker 1 and speaker 2", lambda o, n: has(n, "time_ranges", {"speaker 1", "speaker 2"})),
    ("voice-classify", "people tap each time they hear a laugh", lambda o, n: has(n, "time_points")),
    ("voice-mos", "instead of one score, they keep moving a slider while the clip plays to show how natural it sounds at each moment",
     lambda o, n: has(n, "rating_over_time")),
    ("text-classify", "people should highlight the names of people and places in the text", lambda o, n: has(n, "highlights") and len(q_of(n, "highlights")["answers"]) >= 2),
    ("voice-mos", "make it an A/B test: two versions side by side and people pick the better one", lambda o, n: has(n, "ab_choice")),
    ("voice-mos", "i want a CMOS test from -3 to +3 comparing two systems", lambda o, n: has(n, "ab_scale")),
    ("voice-mos", "change it to a MUSHRA test with a hidden reference", lambda o, n: has(n, "mushra")),
    ("voice-mos", "an ABX test: can people hear the difference between the two systems", lambda o, n: has(n, "abx")),
    ("image-rate", "show 4 images at once and people pick the best and the worst", lambda o, n: has(n, "best_worst") and q_of(n, "best_worst").get("count") == 4),
    ("image-classify", "show 6 pictures at once and people pick all the ones with a dog",
     lambda o, n: has(n, "pick_items") and q_of(n, "pick_items").get("count") == 6 and q_of(n, "pick_items").get("several")),
    ("voice-classify", "add a dropdown for the language: English, German, French, Spanish, Italian, Persian, Arabic, Turkish, Hindi, Chinese, Japanese, Korean",
     lambda o, n: qs(o)[0] in qs(n) and has(n, "dropdown") and len(q_of(n, "dropdown")["answers"]) == 12),
    ("voice-classify", "also ask how many speakers they hear, as a number", lambda o, n: qs(o)[0] in qs(n) and has(n, "number")),
    ("text-rate", "add a question where people rank fluency, accuracy and style from best to worst", lambda o, n: has(n, "ranking", {"fluency", "accuracy", "style"})),
    ("voice-mos", "rate naturalness, clarity and noise, each from 1 to 5",
     lambda o, n: has(n, "grid", {"naturalness", "clarity", "noise"}) or sum(q["type"] == "scale" for q in qs(n)) == 3),
    ("voice-transcribe", "the box should start with the automatic transcript so people only correct it", lambda o, n: has(n, "correct_text") and len(qs(n)) == len(qs(o))),
    ("voice-mos", "people may play each clip only twice", lambda o, n: n["rules"]["max_plays"] == 2 and same_qs(o, n)),
    ("voice-classify", "give them 20 seconds per clip", lambda o, n: n["rules"]["seconds_per_item"] == 20 and same_qs(o, n)),
    # a new page described with a block
    ({"tid": "image-classify", FIRST: True}, "people draw a box around every car, bus and bike in street photos", lambda o, n: has(n, "boxes", {"car", "bus", "bike"})),
    ({"tid": "voice-mos", FIRST: True}, "a MUSHRA listening test of our speech systems against the original recordings", lambda o, n: has(n, "mushra")),
    ({"tid": "text-classify", FIRST: True}, "annotators highlight the person names, places and dates in news sentences",
     lambda o, n: has(n, "highlights") and len(q_of(n, "highlights")["answers"]) >= 3),
    # not asked for: none of the new kinds
    ("voice-classify", "add a box where they can write what they hear", lambda o, n: len(qs(n)) == 2 and qs(n)[1]["type"] == "text"),
    ("image-classify", "also ask how sure they are from 1 to 5", lambda o, n: any(q["type"] == "scale" for q in qs(n)) and not has(n, "points")),
]


def main(runs: int = 1, only: str = "") -> None:
    """only: run just the cases whose message has these words."""
    ok = total = 0
    took = []
    for tid, msg, good in CASES:
        if only.lower() not in msg.lower():
            continue
        hits = 0
        for _ in range(runs):
            obj, tpl, old = start(tid)
            t = time.time()
            new, out, problems, _ = SV._chat_page(obj, tpl, msg, isinstance(tid, dict) and tid.get(FIRST, False))
            took.append(time.time() - t)
            new = new or old
            try:
                hit = bool(good(old, new))
            except (KeyError, IndexError, TypeError):
                hit = False
            hits += hit
            if not hit:
                print(f"   miss: {D.describe(old, new) or '(no change)'}" + (f" | cannot: {out['cannot']}" if out["cannot"] else "")
                      + (f" | {problems}" if problems else ""))
        ok += hits
        total += runs
        print(f"{hits}/{runs}  {tid if isinstance(tid, str) else tid['tid']:17} {msg[:90]}")
    print(f"\n{ok}/{total} right ({100 * ok // total}%), {sum(took) / len(took):.1f} s per message")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1, sys.argv[2] if len(sys.argv) > 2 else "")
