"""What the assistant knows about Omni Studio and the open project.

Task facts are built from the live task registry (so edited classes and new prompt tasks
are always current); studio know-how is a handful of short cards. Each turn picks only the
cards that matter for the current step and the tasks the user mentions.
"""
import re

from .. import tasks as task_reg
from ..engine import heads
from ..engine.omni import AUDIO_HEADS, HEAD_ONLY, IMAGE_HEADS, NEEDS_HEADS, TRAINED, VIDEO_HEADS

STUDIO = {
    "project": (
        "A project is one folder with its files, the tasks it runs and the annotations. "
        "Each selected task becomes a tier (a lane in the editor)."),
    "data": (
        "Files get into a project two ways: upload (copied into the project; drag files or whole folders "
        "onto the Data page, folder structure is kept) or link a folder on the server (nothing is copied, "
        "best for large datasets). Audio, video and images are detected by extension. Only the user can "
        "pick files on their own computer, so for uploads the assistant opens the Data page and asks them "
        "to drop the files there."),
    "tasks": (
        "There are built-in annotation and captioning tasks. A built-in task's classes and prompt can be "
        "edited; its output type and granularity stay fixed. A new prompt task is an instruction plus an "
        "output (text, label, tags, score, boxes, keypoints) and a granularity (file, segment = per speech "
        "region, window = fixed-length pieces, event = timed events). Test a task on a few sample files "
        "before running it on everything."),
    "test": (
        "Testing runs one task on 1 to 5 random files and shows the results without saving anything. "
        "Use it to check classes and prompts before a full run."),
    "run": (
        "A run applies the selected tasks to the files in the background, one file at a time, and can be "
        "cancelled. Modes: missing = only fill tiers that are still empty (safe default); unreviewed = "
        "re-run tiers nobody reviewed or edited; overwrite = replace everything, including reviewed work."),
    "review": (
        "After a run, the Review page lists the least confident segments first; reviewers accept or fix "
        "them in the editor. Statistics shows label distributions and how often reviewers changed the "
        "model output. Export offers JSON, ELAN, TextGrid, SRT, WebVTT, RTTM, CSV, COCO and a JSONL "
        "training manifest."),
    "model": (
        "Labels come from RFomni, the fine-tuned annotation model. Its language part was trained on speech "
        "annotation (transcription, language, entities) and captioning; other tasks are asked through their "
        "prompt. Speech regions come from Silero VAD. Small trained heads on the same model's frozen audio "
        "and vision encoders handle speaker diarization and overlap, speaker count, voice gender, audio spoofing, phonemes, end "
        "of turn, people and body parts, pose, face spoof and deepfake, head pose and video deepfakes; they "
        "are fast and more accurate than prompting. Forced word alignment and shot cuts cannot run yet."),
}

# words people use -> task ids
ALIASES = {
    "transcri": ["asr"], "speech to text": ["asr"], "subtitle": ["asr"], "language": ["lid"],
    "translat": ["translation"], "entit": ["ner"], "emotion": ["ser"], "feeling": ["ser"],
    "sentiment": ["sentiment"], "topic": ["topic"], "speaker": ["diarization"], "who speaks": ["diarization"],
    "overlap": ["overlap"], "same time": ["overlap"], "at once": ["overlap"], "simultaneous": ["overlap"],
    "talk over": ["overlap"], "interrupt": ["overlap"], "gender": ["gender"], "age": ["age"], "accent": ["accent"],
    "laugh": ["nonverbal"], "cough": ["nonverbal"], "quality": ["sound_quality", "mos"], "noise": ["sound_quality"],
    "sound event": ["sed"], "music": ["sed"], "scene": ["acoustic_scene"], "face": ["face_boxes", "fer"],
    "expression": ["fer"], "person": ["person_boxes"], "people": ["person_boxes"], "object": ["object_boxes"],
    "pose": ["pose"], "ocr": ["ocr"], "text on screen": ["ocr"], "action": ["action"], "shot": ["shots"],
    "spoof": ["spoof_audio", "spoof_video", "spoof_image"], "deepfake": ["spoof_video"], "fake": ["spoof_image"],
    "caption": ["audio_caption", "video_caption", "image_caption"], "describ": ["audio_caption", "video_caption", "image_caption"],
    "vad": ["vad"], "voice activity": ["vad"], "silero": ["vad"], "word timing": ["forced_alignment"], "alignment": ["forced_alignment"],
    "phone": ["phoneme"], "how many speakers": ["speaker_count"], "number of speakers": ["speaker_count"],
    "end of turn": ["turn_end"], "turn taking": ["turn_end"], "finished speaking": ["turn_end"],
    "body part": ["body_parts"], "hand": ["body_parts"], "head pose": ["head_pose"], "yaw": ["head_pose"],
    "looking": ["head_pose"],
}

MOD_SHORT = {"audio": "A", "video": "V", "image": "I"}


def support(task: dict) -> str:
    tid = task["id"]
    if tid in HEAD_ONLY:
        return "cannot run yet"
    if tid in AUDIO_HEADS or tid in IMAGE_HEADS or tid in VIDEO_HEADS:
        kind = "vision" if tid in IMAGE_HEADS or tid in VIDEO_HEADS else "audio"
        if heads.available(kind):
            return "trained head"
        if tid in NEEDS_HEADS:
            return "cannot run yet (the heads service is not running)"
    if task.get("output") == "keypoints" or task.get("granularity") in ("word", "phone"):
        return "cannot run yet"
    if tid in TRAINED:
        return "trained"
    return "via prompt"


def task_card(task: dict, chosen: bool | None = None) -> str:
    mods = "/".join(task.get("modalities", []))
    labels = task.get("labels") or []
    cls = f" Classes: {', '.join(labels[:12])}{'…' if len(labels) > 12 else ''}." if labels else ""
    edited = " (edited)" if task.get("modified") else ""
    custom = " (user task)" if not task.get("builtin") else ""
    used = ("" if chosen is None else " This project already has this task." if chosen
            else " This project does NOT have this task yet; it can be added.")
    return (f"{task['id']}{edited}{custom}: {task.get('title', task['name'])} — {mods}, {task['output']} per "
            f"{task.get('granularity', 'file')}, {support(task)}.{cls} {task.get('description', '')}{used}").strip()


def catalog(modalities=None) -> str:
    """Compact task list: id=title [modalities]; tasks that cannot run yet are marked."""
    rows = []
    for t in task_reg.all_tasks():
        if modalities and not set(t.get("modalities", [])) & set(modalities):
            continue
        mark = "" if not support(t).startswith("cannot") else ", not yet"
        title = re.sub(r"\s*\(.*?\)", "", t.get("title") or t["name"])[:32]
        rows.append(f"{t['id']}={title} [{''.join(MOD_SHORT[m] for m in t.get('modalities', []))}{mark}]")
    return "; ".join(rows)


def _name(t: dict, ids: bool = True) -> str:
    mark = " - cannot run yet" if support(t).startswith("cannot") else ""
    if not ids:
        return f"{t.get('title') or t['name']}{f' ({mark[3:]})' if mark else ''}"
    return f"{t.get('title') or t['name']} ({t['id']}{mark})"


def project_tasks(proj: dict, files_by_media: dict, ids: bool = True) -> list:
    """The project's tasks as the user sees them on its Tasks page: chosen ones and the ones that could be
    added, grouped by the kind of file they work on, with names and ids."""
    by_id = task_reg.tasks_by_id()
    chosen = [by_id[i] for i in proj.get("tasks") or [] if i in by_id]
    MEDIA = ("audio", "video", "image", "text")
    has = [m for m in MEDIA if files_by_media.get(m)]
    out = [f"Tasks chosen for this project ({len(chosen)}):" if chosen else "Tasks chosen for this project: none yet."]
    for m in MEDIA:
        group = [_name(t, ids) for t in chosen if m in t.get("modalities", [])]
        if group:
            out.append(f"  {m} tasks chosen ({len(group)}): {', '.join(group)}")
    chosen_ids = {t["id"] for t in chosen}
    for m in has or MEDIA:
        free = [_name(t, ids) for t in task_reg.all_tasks() if m in t.get("modalities", []) and t["id"] not in chosen_ids]
        out.append(f"  {m} tasks NOT chosen yet, could be added ({len(free)}): {', '.join(free) or 'none'}")
    missing = [m for m in MEDIA if m not in has]
    if has and missing:
        out.append(f"  The project has no {' or '.join(missing)} files, so {'/'.join(missing)} tasks would find nothing to label.")
    return out


def mentioned_tasks(text: str) -> list:
    """Task ids the text names directly or through common words."""
    low = text.lower()
    found = []
    by_id = task_reg.tasks_by_id()
    for tid, t in by_id.items():
        names = {tid.lower(), t["name"].lower(), t.get("title", "").lower()}
        if any(n and re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", low) for n in names):
            found.append(tid)
    for key, ids in ALIASES.items():
        if key in low:
            found.extend(i for i in ids if i in by_id)
    return list(dict.fromkeys(found))


CAPABILITY_RE = re.compile(r"what (can|could|do|does) .*(label|annotat|do|support|offer|handle)|which (tasks|labels)"
                           r"|(list|show) .*(tasks|labels)|capabilit|what tasks|what labels|kinds? of (labels|tasks)")


def asks_capabilities(text: str) -> bool:
    """A general question about what the studio can do: answer from the real task list."""
    return bool(CAPABILITY_RE.search(text.lower()))


def cards_for(step: str, text: str, plan_tasks: list, limit: int = 6, project_tasks: list | None = None) -> list:
    """Studio card for the step plus task cards for the tasks in play (marked chosen or not in the project)."""
    out = [STUDIO[step]] if step in STUDIO else []
    if asks_capabilities(text):
        out += [STUDIO["model"], "All tasks (id=title [A audio, V video, I image]): " + catalog()]
    elif re.search(r"model|engine|support|head", text.lower()):
        out.append(STUDIO["model"])
    by_id = task_reg.tasks_by_id()
    for tid in list(dict.fromkeys(mentioned_tasks(text) + list(plan_tasks)))[:limit]:
        if tid in by_id:
            out.append(task_card(by_id[tid], None if project_tasks is None else tid in project_tasks))
    return out
