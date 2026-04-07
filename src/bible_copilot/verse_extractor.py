"""
Extracts verse text from Bible markdown files given book/chapter/verse coordinates.
"""

import os
import re
import unicodedata

from src.config import BIBLE_DATA_DIR, LANGUAGE


def _normalize(text: str) -> str:
    """Strip accents and lowercase for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _build_book_index(bible_data_dir: str = BIBLE_DATA_DIR) -> dict[str, str]:
    """
    Builds a mapping from normalized book name -> file path.
    E.g. {"joao": ".bible_data/pt-br/new_testament/joao.md", ...}
    Also maps common display names like "1 joao" -> path.
    """
    lang_dir = os.path.join(bible_data_dir, LANGUAGE)
    index = {}

    if not os.path.isdir(lang_dir):
        return index

    for testament in os.listdir(lang_dir):
        testament_path = os.path.join(lang_dir, testament)
        if not os.path.isdir(testament_path):
            continue
        for filename in os.listdir(testament_path):
            if not filename.endswith(".md"):
                continue
            slug = filename[:-3]  # remove .md
            full_path = os.path.join(lang_dir, testament, filename)
            normalized = _normalize(slug)
            index[normalized] = full_path

            # Also add with space before digit prefix: "1samuel" -> "1 samuel"
            match = re.match(r"^(\d+)(.+)$", normalized)
            if match:
                index[f"{match.group(1)} {match.group(2)}"] = full_path

    return index


def _find_book_path(book_name: str, index: dict[str, str]) -> str | None:
    """Find the file path for a book name using the index."""
    normalized = _normalize(book_name)

    # Direct match
    if normalized in index:
        return index[normalized]

    # Try removing spaces: "1 João" -> "1joao"
    no_spaces = normalized.replace(" ", "")
    if no_spaces in index:
        return index[no_spaces]

    # Substring match as fallback
    for key, path in index.items():
        if normalized in key or key in normalized:
            return path

    return None


def _extract_verses_from_file(
    path: str, chapter: int, verse_start: int, verse_end: int
) -> str:
    """
    Reads a Bible markdown file and extracts verses from a specific chapter.
    Returns the verse text as a plain string.
    """
    if not os.path.isfile(path):
        return ""

    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Find the chapter heading
    chapter_pattern = rf"^## Capítulo {chapter}\s*$"
    chapter_match = re.search(chapter_pattern, content, re.MULTILINE)
    if not chapter_match:
        return ""

    # Find the next chapter heading (or end of file)
    next_chapter = re.search(r"^## Capítulo \d+", content[chapter_match.end():], re.MULTILINE)
    if next_chapter:
        chapter_text = content[chapter_match.end():chapter_match.end() + next_chapter.start()]
    else:
        chapter_text = content[chapter_match.end():]

    # Extract individual verses
    verse_pattern = re.compile(r"\*\*(\d+)\*\*\s*(.*)")
    verses = []
    for m in verse_pattern.finditer(chapter_text):
        verse_num = int(m.group(1))
        if verse_start <= verse_num <= verse_end:
            verses.append(f"**{verse_num}** {m.group(2).strip()}")

    return "\n".join(verses)


def extract_reference_text(
    ref: dict,
    book_index: dict[str, str] | None = None,
    bible_data_dir: str = BIBLE_DATA_DIR,
) -> str:
    """
    Given a biblical reference dict (book, chapter, verse_start, verse_end),
    extracts the actual verse text from the markdown files.

    Args:
        ref: Dict with book, chapter, verse_start, verse_end.
        book_index: Pre-built book index. If None, builds one.
        bible_data_dir: Root directory for Bible data files.

    Returns:
        The extracted verse text, or empty string if not found.
    """
    if book_index is None:
        book_index = _build_book_index(bible_data_dir)

    book = ref.get("book", "")
    chapter = ref.get("chapter", 0)
    verse_start = ref.get("verse_start", 0)
    verse_end = ref.get("verse_end", 0)

    path = _find_book_path(book, book_index)
    if not path:
        return ""

    return _extract_verses_from_file(path, chapter, verse_start, verse_end)
