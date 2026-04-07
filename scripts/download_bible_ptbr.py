"""
Downloads the Portuguese Bible from the thiagobodruk/biblia GitHub repository
and saves each book as a Markdown file under:
  .bible_data/<language>/old_testament/<slug>.md
  .bible_data/<language>/new_testament/<slug>.md

Markdown format per book:
  # <Book Name>

  ## Capítulo 1

  **1** <verse text>
  **2** <verse text>
  ...

Usage:
  python scripts/download_bible_ptbr.py
  python scripts/download_bible_ptbr.py --version nvi
  python scripts/download_bible_ptbr.py --version aa --output-dir /path/to/project
"""

import argparse
import os
import sys
import json
import requests

VERSION_URLS = {
    "aa": "https://raw.githubusercontent.com/thiagobodruk/biblia/master/json/aa.json",
    "nvi": "https://raw.githubusercontent.com/thiagobodruk/biblia/master/json/nvi.json",
}
DEFAULT_VERSION = "aa"

# Language code used as the subfolder name under .bible_data/
LANGUAGE = "pt-br"

# Maps JSON abbreviation (lowercased) -> (testament_dir, file_slug)
ABBREV_MAP = {
    "gn":  ("old_testament", "genesis"),
    "ex":  ("old_testament", "exodo"),
    "lv":  ("old_testament", "levitico"),
    "nm":  ("old_testament", "numeros"),
    "dt":  ("old_testament", "deuteronomio"),
    "js":  ("old_testament", "josue"),
    "jz":  ("old_testament", "juizes"),
    "rt":  ("old_testament", "rute"),
    "1sm": ("old_testament", "1samuel"),
    "2sm": ("old_testament", "2samuel"),
    "1rs": ("old_testament", "1reis"),
    "2rs": ("old_testament", "2reis"),
    "1cr": ("old_testament", "1cronicas"),
    "2cr": ("old_testament", "2cronicas"),
    "ed":  ("old_testament", "esdras"),
    "ne":  ("old_testament", "neemias"),
    "et":  ("old_testament", "ester"),
    "jo":  ("old_testament", "jo"),
    "sl":  ("old_testament", "salmos"),
    "pv":  ("old_testament", "proverbios"),
    "ec":  ("old_testament", "eclesiastes"),
    "ct":  ("old_testament", "canticos"),
    "is":  ("old_testament", "isaias"),
    "jr":  ("old_testament", "jeremias"),
    "lm":  ("old_testament", "lamentacoes"),
    "ez":  ("old_testament", "ezequiel"),
    "dn":  ("old_testament", "daniel"),
    "os":  ("old_testament", "oseias"),
    "jl":  ("old_testament", "joel"),
    "am":  ("old_testament", "amos"),
    "ob":  ("old_testament", "obadias"),
    "jn":  ("old_testament", "jonas"),
    "mq":  ("old_testament", "miqueias"),
    "na":  ("old_testament", "naum"),
    "hc":  ("old_testament", "habacuque"),
    "sf":  ("old_testament", "sofonias"),
    "ag":  ("old_testament", "ageu"),
    "zc":  ("old_testament", "zacarias"),
    "ml":  ("old_testament", "malaquias"),
    "mt":  ("new_testament", "mateus"),
    "mc":  ("new_testament", "marcos"),
    "lc":  ("new_testament", "lucas"),
    "jo2": ("new_testament", "joao"),
    "at":  ("new_testament", "atos"),
    "rm":  ("new_testament", "romanos"),
    "1co": ("new_testament", "1corintios"),
    "2co": ("new_testament", "2corintios"),
    "gl":  ("new_testament", "galatas"),
    "ef":  ("new_testament", "efesios"),
    "fp":  ("new_testament", "filipenses"),
    "cl":  ("new_testament", "colossenses"),
    "1ts": ("new_testament", "1tessalonicenses"),
    "2ts": ("new_testament", "2tessalonicenses"),
    "1tm": ("new_testament", "1timoteo"),
    "2tm": ("new_testament", "2timoteo"),
    "tt":  ("new_testament", "tito"),
    "fm":  ("new_testament", "filemon"),
    "hb":  ("new_testament", "hebreus"),
    "tg":  ("new_testament", "tiago"),
    "1pe": ("new_testament", "1pedro"),
    "2pe": ("new_testament", "2pedro"),
    "1jo": ("new_testament", "1joao"),
    "2jo": ("new_testament", "2joao"),
    "3jo": ("new_testament", "3joao"),
    "jd":  ("new_testament", "judas"),
    "ap":  ("new_testament", "apocalipse"),
}

# Used to disambiguate "jo" appearing as both Job (OT) and John (NT)
OT_BOOKS_COUNT = 39


def book_to_markdown(book_name_pt: str, chapters: list) -> str:
    """Converts a book's chapter/verse data into a Markdown string."""
    lines = [f"# {book_name_pt}", ""]
    for chapter_idx, verses in enumerate(chapters, start=1):
        lines.append(f"## Capítulo {chapter_idx}")
        lines.append("")
        for verse_idx, verse_text in enumerate(verses, start=1):
            lines.append(f"**{verse_idx}** {str(verse_text).strip()}")
        lines.append("")
    return "\n".join(lines)


def resolve_abbrev(abbrev: str, book_index: int) -> tuple[str, str] | None:
    """
    Resolves abbreviation to (testament_dir, slug).
    Handles the "jo" collision: OT position -> Job, NT position -> John.
    """
    key = abbrev.lower()

    if key == "jo":
        if book_index < OT_BOOKS_COUNT:
            return ABBREV_MAP["jo"]
        else:
            return ABBREV_MAP["jo2"]

    return ABBREV_MAP.get(key)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download the Portuguese Bible and save as Markdown files."
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        choices=list(VERSION_URLS.keys()),
        help="Bible version (default: aa = Almeida Atualizada)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args()

    url = VERSION_URLS[args.version]
    print(f"Downloading Bible version '{args.version}' from:\n  {url}\n")

    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Download error: {e}", file=sys.stderr)
        sys.exit(1)

    response.encoding = "utf-8-sig"
    bible_data = json.loads(response.text)

    base_dir = os.path.join(args.output_dir, ".bible_data", LANGUAGE)
    os.makedirs(os.path.join(base_dir, "old_testament"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "new_testament"), exist_ok=True)

    written = 0
    skipped = 0

    for book_index, book_dict in enumerate(bible_data):
        abbrev = book_dict.get("abbrev", "")
        name_pt = book_dict.get("book", "")
        chapters = book_dict.get("chapters", [])

        mapping = resolve_abbrev(abbrev, book_index)
        if mapping is None:
            print(f"  WARNING: unknown abbreviation '{abbrev}' (book #{book_index + 1}: {name_pt}) — skipped.")
            skipped += 1
            continue

        testament_dir, slug = mapping
        md_content = book_to_markdown(name_pt, chapters)
        out_path = os.path.join(base_dir, testament_dir, f"{slug}.md")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"  Written: {out_path}  ({len(chapters)} chapters)")
        written += 1

    print(f"\nDone. {written} books written, {skipped} skipped.")
    print(f"Saved to: {base_dir}/")


if __name__ == "__main__":
    main()
