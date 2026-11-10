"""How reliably the page assistant (backend/pages/agent.py) turns chat messages into the right settings.

Each case is a message to a default page and a check on the settings that result. Needs the small model running
(`qwen9b start`). Run from studio/:  .venv/bin/python -m scripts.eval_pages [runs]
"""
import sys
import time

from backend.pages import agent as A
from backend.pages import catalogue as C

CASES = [  # (default page, message, check on the new settings)
    ("voice-classify", "my classes are happy, sad, angry, fearful and neutral", lambda s: len(s["classes"]) == 5),
    ("voice-classify", "people can choose more than one class", lambda s: s["multiple"]),
    ("voice-classify", "remove the not sure option", lambda s: not s["notSure"]),
    ("voice-classify", "5 people per clip", lambda s: s["peoplePerItem"] == 5),
    ("voice-classify", "they must listen to the end before answering", lambda s: s["mustPlay"]),
    ("voice-classify", "ask how confident they are", lambda s: s["confidence"]),
    ("text-classify", "classes: spam and not spam", lambda s: [c.lower() for c in s["classes"]] == ["spam", "not spam"]),
    ("text-classify", "everyone should get the texts in the same order", lambda s: not s["randomOrder"]),
    ("text-classify", "each person does at most 100 texts", lambda s: s["itemsPerPerson"] == 100),
    ("image-classify", "is there a dog in the picture? yes or no", lambda s: len(s["classes"]) == 2 and "dog" in s["question"].lower()),
    ("voice-mos", "use a 1 to 7 scale", lambda s: (s["scaleMin"], s["scaleMax"]) == (1, 7)),
    ("voice-mos", "also ask about the audio quality", lambda s: bool(s["secondQuestion"]) and "natural" in s["question"].lower()),
    ("voice-mos", "the first 3 clips are practice", lambda s: s["practiceItems"] == 3),
    ("voice-mos", "add a comment box people must fill in", lambda s: s["comment"] == "required"),
    ("voice-mos", "ask if they wear headphones", lambda s: "headphones" in s["askAbout"]),
    ("voice-mos", "show answers per day on my results page", lambda s: "timeline" in s["charts"]),
    ("video-rate", "rate how smooth the motion is", lambda s: "smooth" in s["question"].lower()),
    ("voice-transcribe", "also rate how clear the speech is", lambda s: bool(s["extraRating"])),
    ("voice-transcribe", "single line answers are enough", lambda s: not s["multiline"]),
    ("voice-transcribe", "put the page in Persian", lambda s: s["language"] == "fa"),
    ("voice-read", "each sentence in a whisper and in a normal voice", lambda s: len(s["ways"]) == 2),
    ("voice-read", "recordings can be up to 30 seconds", lambda s: s["maxSeconds"] == 30),
    ("voice-read", "record at full quality", lambda s: s["sampleRate"] == "48000"),
    ("voice-read", "use these sentences: The cat sat down. The dog ran away. It is late.", lambda s: len(s["prompts"]) == 3),
    ("image-caption", "dark mode please", lambda s: s["theme"] == "dark" and not s["accent"]),
    ("text-rate", "show progress as a ring", lambda s: s["progress"] == "ring"),
    ("image-classify", "show people how everyone answered at the end", lambda s: "everyone" in s["finishCharts"]),
]


def main(runs: int = 2) -> None:
    ok = total = 0
    secs = []
    for tid, msg, check in CASES:
        t = C.get(tid)
        good = 0
        for _ in range(runs):
            t0 = time.time()
            r = A.edit(t, t["settings"], msg, [])
            secs.append(time.time() - t0)
            try:
                good += bool(check({**t["settings"], **r["changes"]}))
            except Exception:
                pass
        ok += good
        total += runs
        print(f"{good}/{runs}  {tid:17} {msg}" + ("" if good == runs else f"   -> {r['changes']}"))
    print(f"\n{ok}/{total} right ({ok / total:.0%}), {sum(secs) / len(secs):.1f} s per message")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
