"""The default pages (templates/*.json), loaded once and checked.

A template: id, area (tasks | listen | collect), modality (voice | text | image | video), family (settings.py), name,
pick (what it is for, read by the model that picks a page), summary, layout, suggest (chat suggestions), examples
(requests and the settings they change, shown to the model) and settings (the default values of every setting).
"""
import json
from functools import lru_cache
from pathlib import Path

from . import settings as S

DIR = Path(__file__).parent / "templates"
AREAS = {"tasks": "Task sharing", "listen": "Subjective tests", "collect": "Data collection"}
MODALITIES = {"voice": "Voice", "text": "Text", "image": "Image", "video": "Video"}


@lru_cache(maxsize=1)
def _all() -> dict:
    out = {}
    for f in sorted(DIR.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        assert t["area"] in AREAS and t["modality"] in MODALITIES, f.name
        sp = S.spec(t["family"], t["modality"])
        missing = sorted(set(sp) - set(t["settings"]))
        assert not missing, f"{f.name}: no default for {missing}"
        t["settings"] = S.check(t["family"], t["modality"], t["settings"])  # a bad default fails here, at start
        out[t["id"]] = t
    return out


def get(tid: str) -> dict:
    t = _all().get(tid)
    if not t:
        raise KeyError(f"There is no default page {tid}")
    return t


def find(area: str, modality: str = "") -> list:
    return [t for t in _all().values() if t["area"] == area and (not modality or t["modality"] == modality)]


def form(t: dict) -> list:
    """The settings of a template as the studio's settings form shows them, in order."""
    sp = S.spec(t["family"], t["modality"])
    return [{"name": k, **{x: v for x, v in s.items()}} for k, s in sp.items()]


def public(t: dict) -> dict:
    return {k: t.get(k) for k in ("id", "area", "modality", "family", "name", "layout", "pick", "summary", "suggest")}
