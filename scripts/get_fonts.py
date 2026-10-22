"""Download the Satoshi font (Fontshare, ITF Free Font License) for the landing page.

Satoshi is free to use, but its license does not allow sharing the font files through a
repository, so they are not part of this project: every install downloads its own official
copy from Fontshare into frontend/landing/fonts/satoshi/ (git-ignored). Without it the page
falls back to the system font.

    .venv/bin/python scripts/get_fonts.py
"""
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://api.fontshare.com/v2/fonts/download/satoshi"
DEST = Path(__file__).resolve().parent.parent / "frontend" / "landing" / "fonts" / "satoshi"
WANT = ["Satoshi-Regular.woff2", "Satoshi-Medium.woff2", "Satoshi-Bold.woff2", "Satoshi-Black.woff2"]


def main(quiet=False):
    if all((DEST / f).exists() for f in WANT):
        if not quiet:
            print("Satoshi is already installed in", DEST)
        return 0
    try:
        with urllib.request.urlopen(URL, timeout=20) as r:
            data = r.read()
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e:  # offline: the landing page uses the system font instead
        print(f"Could not download Satoshi ({e}); the landing page will use the system font.")
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    names = z.namelist()
    for want in WANT + ["FFL.txt"]:
        src = next((n for n in names if n.endswith("/" + want) and ("/WEB/" in n or want.endswith(".txt"))), None)
        if src:  # official files, unchanged (the license does not allow converting or subsetting them)
            (DEST / want).write_bytes(z.read(src))
    if not quiet:
        print("Satoshi installed in", DEST)
    return 0


if __name__ == "__main__":
    sys.exit(main(quiet="--quiet" in sys.argv))
