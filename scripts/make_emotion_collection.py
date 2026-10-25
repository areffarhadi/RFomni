"""Create the sample data collection: a multilingual emotional speech dataset.

Five everyday sentences with no emotion of their own, in English, German, French, Spanish, Persian and Mandarin,
each read as neutral, happy, sad, angry and surprised (25 recordings per person and language).

    .venv/bin/python scripts/make_emotion_collection.py [--live --listed]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import collect as C  # noqa: E402

SENTENCES = {
    ("en", "English"): ["The meeting starts at ten o'clock tomorrow.", "I left my keys on the kitchen table.",
                        "The train to the city leaves every hour.", "She bought a new blue chair yesterday.",
                        "We are going to visit them next week."],
    ("de", "German"): ["Das Treffen beginnt morgen um zehn Uhr.", "Ich habe meine Schlüssel auf dem Küchentisch liegen lassen.",
                       "Der Zug in die Stadt fährt jede Stunde.", "Sie hat gestern einen neuen blauen Stuhl gekauft.",
                       "Wir besuchen sie nächste Woche."],
    ("fr", "French"): ["La réunion commence demain à dix heures.", "J'ai laissé mes clés sur la table de la cuisine.",
                       "Le train pour la ville part toutes les heures.", "Elle a acheté une nouvelle chaise bleue hier.",
                       "Nous allons leur rendre visite la semaine prochaine."],
    ("es", "Spanish"): ["La reunión empieza mañana a las diez.", "Dejé mis llaves en la mesa de la cocina.",
                        "El tren a la ciudad sale cada hora.", "Ayer compró una silla azul nueva.",
                        "Vamos a visitarlos la próxima semana."],
    ("fa", "Persian"): ["جلسه فردا ساعت ده شروع می‌شود.", "کلیدهایم را روی میز آشپزخانه جا گذاشتم.",
                        "قطار شهر هر ساعت حرکت می‌کند.", "او دیروز یک صندلی آبی نو خرید.",
                        "هفته‌ی بعد به دیدنشان می‌رویم."],
    ("zh", "Chinese (Mandarin)"): ["会议明天十点开始。", "我把钥匙忘在厨房的桌子上了。", "去市区的火车每小时一班。",
                                   "她昨天买了一把新的蓝色椅子。", "我们下周去看他们。"],
}
EMOTIONS = [
    ("neutral", "Neutral", "Calm and plain, like reading out a timetable."),
    ("happy", "Happy", "As if you just heard great news."),
    ("sad", "Sad", "As if you just heard bad news."),
    ("angry", "Angry", "As if someone treated you unfairly."),
    ("surprised", "Surprised", "As if you did not expect this at all."),
]

DESIGN = {
    "title": "Multilingual emotional speech",
    "summary": "Read five everyday sentences in five emotions: neutral, happy, sad, angry and surprised. "
               "English, German, French, Spanish, Persian and Mandarin.",
    "instructions": ("We are building a dataset to teach computers to recognise emotion in the voice, across languages. "
                     "Choose a language you speak fluently. Each sentence comes up five times, once for each emotion: say it "
                     "the way the emotion asks, as naturally as you can, as if you meant it. The words stay the same; the "
                     "feeling is in your voice. Find a quiet place, keep the same distance to the microphone, listen to each "
                     "recording and record it again if it does not sound right. It takes about ten minutes per language."),
    "consent": ("I agree that my recordings and the information I give are stored and used to build a speech emotion "
                "dataset for research, and that they may be shared with other researchers without my name."),
    "languages": [{"code": code, "name": name, "prompts": [{"id": f"s{i + 1}", "text": t} for i, t in enumerate(sents)]}
                  for (code, name), sents in SENTENCES.items()],
    "conditions": [{"id": i, "label": l, "hint": hint} for i, l, hint in EMOTIONS],
    "look": {"theme": "forest", "accent": ""},
    "settings": {"sample_rate": 16000, "min_seconds": 1.0, "max_seconds": 12.0, "per_pair": 0,
                 "info": ["gender", "age", "first_language"], "info_required": [], "allow_skip": True,
                 "condition_order": "together"},
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", default="local")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--listed", action="store_true")
    a = ap.parse_args()
    p = C.create({"id": a.owner, "name": ""}, design=DESIGN)
    if a.live:
        C.update(p["id"], {"status": "live"})
    if a.listed:
        C.update(p["id"], {"listed": True})
    p = C.get(p["id"])
    print(f"Data collection {p['id']}: {len(DESIGN['languages'])} languages, 5 sentences x {len(EMOTIONS)} emotions; "
          f"status {p['status']}{', listed' if p.get('listed') else ''}\nLink: /c/{p['token']}")


if __name__ == "__main__":
    main()
