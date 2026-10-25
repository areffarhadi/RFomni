"""Tables of texts (.csv, .tsv, .jsonl) become one text document per row.

The text column is the first of TEXT_COLS that exists, else the column with the longest values. The other columns
of the row are kept with the document (media item "info"), e.g. an id or a gold label from the source dataset.
"""
import csv
import io
import json
from pathlib import Path

from .config import TEXT_MAX_BYTES

TEXT_COLS = ("text", "sentence", "content", "body", "review", "comment", "message", "utterance", "document", "abstract")
MAX_ROWS = 20000


def _rows(path: Path) -> list[dict]:
    raw = path.read_bytes()
    if len(raw) > TEXT_MAX_BYTES * 25:
        raise ValueError("table larger than 50 MB")
    txt = raw.decode("utf-8-sig", errors="replace")
    if path.suffix.lower() == ".jsonl":
        out = []
        for line in txt.splitlines():
            line = line.strip()
            if line:
                obj = json.loads(line)
                out.append(obj if isinstance(obj, dict) else {"text": str(obj)})
        return out
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    return list(csv.DictReader(io.StringIO(txt), delimiter=delim))


def split_table(path: Path) -> list[tuple[str, dict]]:
    """[(text, other columns), ...] for each non-empty row."""
    rows = _rows(Path(path))
    if not rows:
        raise ValueError("no rows")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"more than {MAX_ROWS:,} rows; split the table first")
    cols = [k for k in rows[0].keys() if k is not None]
    low = {str(c).strip().lower(): c for c in cols}
    col = next((low[c] for c in TEXT_COLS if c in low), None)
    if col is None:  # the column with the longest values
        col = max(cols, key=lambda c: sum(len(str(r.get(c) or "")) for r in rows[:200]))
    out = []
    for r in rows:
        text = str(r.get(col) or "").strip()
        if text:
            info = {str(k): v for k, v in r.items() if k not in (col, None) and v not in (None, "")}
            out.append((text, {k: (v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False))
                               for k, v in list(info.items())[:12]}))
    return out


def write_documents(path: Path, dest_dir: Path) -> list[tuple[Path, dict]]:
    """Write each row as dest_dir/<n>.txt; returns [(file, info), ...]."""
    docs = split_table(path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    width = max(4, len(str(len(docs))))
    out = []
    for i, (text, info) in enumerate(docs, 1):
        f = dest_dir / f"{str(i).zfill(width)}.txt"
        f.write_text(text, encoding="utf-8")
        out.append((f, info))
    return out
