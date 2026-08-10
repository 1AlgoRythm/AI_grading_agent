"""Extract selected pages of a PDF into a Markdown file under textbook/.

Usage:
    python tools/extract_textbook.py ~/books/clrs.pdf clrs_greedy 414 432

Extracted text is NOT committed -- see .gitignore. Ship your own written
corpus in the repo so a clean checkout still demonstrates retrieval.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from pypdf import PdfReader


def extract(pdf_path: str, name: str, first_page: int, last_page: int) -> Path:
    reader = PdfReader(pdf_path)
    pages = []
    for number in range(first_page - 1, min(last_page, len(reader.pages))):
        text = reader.pages[number].extract_text() or ""
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        pages.append(text.strip())

    out = Path("textbook") / f"{name}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(f"# {name.replace('_', ' ').title()}\n\n" + "\n\n".join(pages), encoding="utf8")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    path = extract(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    print(f"Wrote {path} ({path.stat().st_size} bytes). Read it and clean up the pseudocode by hand.")
