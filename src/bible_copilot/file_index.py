"""
Builds the Bible file index injected into the agent's system prompt.
"""

import os

from src.config import BIBLE_DATA_DIR, LANGUAGE


def build_bible_file_index(bible_data_dir: str = BIBLE_DATA_DIR) -> str:
    """
    Returns a formatted listing of all Bible .md files, organized by testament.
    Injected into the system prompt so the agent has exact file paths without
    any tool call.
    """
    lang_dir = os.path.join(bible_data_dir, LANGUAGE)

    if not os.path.isdir(lang_dir):
        return (
            f"No Bible data found under '{lang_dir}'. "
            "Run `uv run python main.py --download-bible` to fetch it."
        )

    lines: list[str] = []
    for testament in sorted(os.listdir(lang_dir)):
        testament_path = os.path.join(lang_dir, testament)
        if not os.path.isdir(testament_path):
            continue
        lines.append(f"{testament}/")
        for filename in sorted(os.listdir(testament_path)):
            if filename.endswith(".md"):
                lines.append(f"  {bible_data_dir}/{LANGUAGE}/{testament}/{filename}")

    return "\n".join(lines) if lines else f"No files found under {lang_dir}/"
