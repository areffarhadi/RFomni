"""Sample items for the preview of a page made from a default, until its owner adds data.

They are static files (frontend/pages/samples/<modality>/, and texts.json) and only ever reach the owner's
preview: a task cannot go live without its own data.
"""
import json
import secrets

from .. import config

DIR = config.FRONTEND_DIR / "pages" / "samples"
KIND = {"voice": "audio", "image": "image", "video": "video", "text": "text"}


def item(modality: str) -> dict | None:
    """One random sample item, shaped like the items the public page gets (shared_routes._item_out)."""
    if modality == "text":
        texts = json.loads((DIR / "texts.json").read_text(encoding="utf-8"))
        i = secrets.randbelow(len(texts))
        return {"id": f"sample-{i}", "kind": "text", "name": f"Sample text {i + 1}", "text": texts[i], "info": {}, "sample": True}
    files = sorted((DIR / modality).glob("*")) if modality in KIND else []
    if not files:
        return None
    f = secrets.choice(files)
    return {"id": f"sample-{f.stem}", "kind": KIND[modality], "name": f.name, "src": f"/pages/samples/{modality}/{f.name}",
            "playable": True, "sample": True}


def items(modality: str) -> list:
    """Every sample item of a kind, in random order: a comparison shows several at once."""
    if modality == "text":
        texts = json.loads((DIR / "texts.json").read_text(encoding="utf-8"))
        out = [{"id": f"sample-{i}", "kind": "text", "name": f"Sample text {i + 1}", "text": t, "info": {}, "sample": True}
               for i, t in enumerate(texts)]
    else:
        out = [{"id": f"sample-{f.stem}", "kind": KIND[modality], "name": f.name, "src": f"/pages/samples/{modality}/{f.name}",
                "playable": True, "sample": True} for f in sorted((DIR / modality).glob("*"))] if modality in KIND else []
    secrets.SystemRandom().shuffle(out)
    return out
