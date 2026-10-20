import json
import os
import re
import secrets
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "", nbytes: int = 5) -> str:
    return prefix + secrets.token_hex(nbytes)


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "item"


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data) -> None:
    """Atomic write: never leaves a half-written file behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def safe_rel_path(rel: str) -> PurePosixPath:
    """Turn a client-supplied relative path into a safe one (no '..', no absolute parts)."""
    parts = [p.strip() for p in re.split(r"[\\/]+", rel or "")]
    parts = [p for p in parts if p and p not in (".", "..")]
    if not parts:
        raise ValueError("empty path")
    return PurePosixPath(*parts)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        cand = path.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
        i += 1
