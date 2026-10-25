"""Bring the Persian TTS blind listening test (github.com/areffarhadi/QwenOmni-Talker, listening_test/) into
Omni Studio as a subjective test (a shared task of kind "listening").

The 60 test clips (12 FLEURS-fa sentences, each from five systems) and 2 practice clips are copied in with their
sentence text. Listeners answer three 1-5 scales (naturalness, pronunciation, accent) and an optional comment, in a
Persian right-to-left page: practice first, every clip played to the end before rating, never the same sentence twice
in a row, back to change an answer, overall comments at the end. The clip-to-system key is not in the repository;
add it later in the studio (Results, Blind key) for MOS per system.

    .venv/bin/python scripts/import_listening_test.py --test /path/to/QwenOmni-Talker/listening_test/persian_tts_listening_test [--live --listed]
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import shared_tasks as ST  # noqa: E402
from backend.util import write_json  # noqa: E402

DESIGN = {
    "title": "آزمون شنیداری گفتار فارسی · Persian speech listening test",
    "summary": "For native Persian speakers: listen to 60 short clips and rate naturalness, pronunciation and accent "
               "(about 20 minutes, headphones).",
    "instructions": ("لطفاً با هدفون و در محیطی آرام گوش دهید. برای هر نمونه، ابتدا صدا را تا آخر گوش دهید (می‌توانید چند بار پخش "
                     "کنید)، سپس به سه پرسش پاسخ دهید. متن جمله بالای هر نمونه نوشته شده است. بعضی نمونه‌ها صدای انسان و بعضی "
                     "صدای ساخته‌شده با رایانه‌اند. هر نمونه را جداگانه و بدون مقایسه با نمونه‌ی قبلی ارزیابی کنید. آزمون ۶۲ "
                     "نمونه دارد (۲ نمونه‌ی تمرینی) و حدود ۲۰ دقیقه طول می‌کشد.\n\n"
                     "Use headphones in a quiet room. Listen to each clip to the end, then answer three questions. Some clips "
                     "are human, some synthetic; rate each on its own. About 20 minutes; you can stop and continue later with "
                     "your session ID."),
    "fields": [
        {"id": "nat", "type": "rating", "min": 1, "max": 5, "required": True,
         "label": "۱. طبیعی بودن صدا · Naturalness: how natural does the voice sound?",
         "min_label": "خیلی بد، کاملاً مصنوعی", "max_label": "عالی، کاملاً طبیعی"},
        {"id": "pron", "type": "rating", "min": 1, "max": 5, "required": True,
         "label": "۲. تلفظ درست کلمات · Pronunciation: are the words pronounced correctly (vowels, ezafe, consonants)?",
         "min_label": "بیشتر کلمات نادرست", "max_label": "همه‌ی کلمات درست"},
        {"id": "acc", "type": "rating", "min": 1, "max": 5, "required": True,
         "label": "۳. لهجه · Accent: how native does the Persian accent sound?",
         "min_label": "لهجه‌ی خارجی شدید", "max_label": "کاملاً مانند فارسی‌زبان بومی"},
        {"id": "comment", "type": "text", "required": False, "multiline": False,
         "label": "توضیح (اختیاری) · Optional comment", "placeholder": "مثلاً کدام کلمه نادرست بود"},
    ],
    "intro_fields": [
        {"id": "pid", "label": "نام یا شناسه (اختیاری) · Name or ID (optional)", "type": "text", "required": False},
        {"id": "native", "label": "آیا زبان مادری شما فارسی است؟ · Native Persian speaker?", "type": "choice",
         "options": ["بله", "خیر"], "required": True},
        {"id": "device", "label": "با چه وسیله‌ای گوش می‌دهید؟ · Listening device", "type": "choice",
         "options": ["هدفون", "بلندگو", "بلندگوی گوشی"], "required": True},
        {"id": "age", "label": "گروه سنی (اختیاری) · Age range (optional)", "type": "choice",
         "options": ["18-25", "26-35", "36-50", "51+"], "required": False},
    ],
    "look": {"theme": "forest", "accent": "", "layout": "stacked", "dir": "rtl", "ui_lang": "fa"},
    "settings": {"labels_per_item": 0, "max_per_session": 0, "allow_skip": False, "shuffle": True, "ask_name": False,
                 "group_label": "Group", "must_listen": True, "no_repeat": True, "allow_back": True, "final_comment": True},
}


def parse(index_html: Path) -> tuple:
    s = index_html.read_text(encoding="utf-8")
    items = json.loads(re.search(r"const ITEMS = (\[.*?\]);", s, re.S).group(1))
    practice = json.loads(re.search(r"const PRACTICE = (\[.*?\]);", s, re.S).group(1))
    return items, practice


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--test", required=True, help="the persian_tts_listening_test folder (index.html + audio/)")
    ap.add_argument("--task", help="update this existing task instead of creating one")
    ap.add_argument("--owner", default="local")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--listed", action="store_true")
    a = ap.parse_args()
    test = Path(a.test).expanduser()
    clips, practice = parse(test / "index.html")

    tid = a.task or ST.create({"id": a.owner, "name": ""}, DESIGN["title"], "listening")["id"]
    ST.update(tid, {"design": DESIGN})
    media = ST.ROOT / tid / "media"
    media.mkdir(parents=True, exist_ok=True)
    items = []
    for c, is_practice in [(p, True) for p in practice] + [(c, False) for c in clips]:
        src = test / "audio" / f"{c['id']}.wav"
        if not src.is_file():
            print(f"  missing {src.name}")
            continue
        shutil.copy2(src, media / src.name)
        it = ST._media_item(media / src.name, src.name, "upload")
        it.update(id=f"i{c['id']}", text=c["text"], stimulus=f"s{c['sent']}")
        if is_practice:
            it["practice"] = True
        items.append(it)
    write_json(ST.ROOT / tid / "items.json", {"items": items})
    if a.live:
        ST.update(tid, {"status": "live"})
    if a.listed:
        ST.update(tid, {"listed": True})
    t = ST.get(tid)
    n_p = sum(1 for i in items if i.get("practice"))
    print(f"Listening test {tid}: {len(items) - n_p} clips ({len({i['stimulus'] for i in items if not i.get('practice')})} sentences) "
          f"+ {n_p} practice; status {t['status']}{', listed' if t.get('listed') else ''}\nLink: /t/{t['token']}")


if __name__ == "__main__":
    main()
