import json
import os
import re
from datetime import datetime, timezone

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import Command

from src.bible_copilot.state import BiblePassage
from src.config import BibleCopilotContext
from src.kg.tools import KG_TOOLS


# ── Helpers ────────────────────────────────────────────────────────────────────


def _read_lines(path: str, start_line: int, end_line: int) -> tuple[str, int]:
    """
    Reads lines start_line through end_line (1-indexed, inclusive) from a file.
    Returns (text_with_line_numbers, total_lines_in_file).
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")

    with open(path, encoding="utf-8") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    start = max(1, start_line)
    end = min(total, end_line)

    selected = all_lines[start - 1 : end]
    text = "".join(f"{start + i}: {line}" for i, line in enumerate(selected))
    return text, total


def _grep_file(path: str, pattern: str) -> list[tuple[int, str]]:
    """
    Searches a file for lines matching a regex pattern (case-insensitive).
    Returns list of (line_number, line_text) tuples.
    """
    if not os.path.isfile(path):
        return []

    compiled = re.compile(pattern, re.IGNORECASE)
    matches = []
    with open(path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            if compiled.search(line):
                matches.append((line_num, line.rstrip()))
    return matches


# ── Bible Tools ───────────────────────────────────────────────────────────────


@tool
def read_bible_file(path: str, start_line: int, end_line: int, runtime: ToolRuntime) -> Command:
    """
    Reads lines start_line through end_line (1-indexed, inclusive) from a Bible
    Markdown file. Returns the raw text with line numbers and the total number
    of lines in the file.

    Use this tool to read chapters or specific passages after locating them
    via search_bible_text. Always read a few lines before and after the target
    to preserve context.
    """
    try:
        text, total_lines = _read_lines(path, start_line, end_line)
        header = f"[{path}] lines {start_line}-{end_line} of {total_lines}"
        content = f"{header}\n{text}"
    except FileNotFoundError as e:
        content = f"Error: {e}"

    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


@tool
def search_bible_text(
    pattern: str,
    book_paths: list[str],
    runtime: ToolRuntime,
) -> Command:
    """
    Grep-like regex search across the given Bible Markdown files.
    Returns matching lines with file path and line number so you can follow up
    with read_bible_file on the relevant sections.

    Supports full regex syntax (case-insensitive). Use alternation for multiple
    terms, e.g. "paz|confia|não temas".
    """
    parts = []
    for path in book_paths:
        matches = _grep_file(path, pattern)
        if matches:
            parts.append(f"--- {path} ({len(matches)} matches) ---")
            for line_num, line_text in matches:
                parts.append(f"  {line_num}: {line_text}")

    content = "\n".join(parts) if parts else "No matches found."
    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


# ── Conversation History Tools ────────────────────────────────────────────────


@tool
def list_conversation_history(runtime: ToolRuntime[BibleCopilotContext]) -> Command:
    """
    Lists all saved conversation history files for this session.
    Shows file size and last modified timestamp for each file.

    Use this when you need to recall earlier parts of the conversation
    that may have been summarized away.
    """
    thread_id = runtime.config["configurable"]["thread_id"]
    history_dir = os.path.join(runtime.context.message_history_dir, thread_id)

    if not os.path.isdir(history_dir):
        content = "No conversation history saved yet for this session."
        return Command(update={
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
        })

    files = sorted(f for f in os.listdir(history_dir) if f.endswith(".md"))
    if not files:
        content = "No conversation history files found."
        return Command(update={
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
        })

    entries = []
    for filename in files:
        filepath = os.path.join(history_dir, filename)
        stat = os.stat(filepath)
        size_kb = stat.st_size / 1024
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        entries.append({
            "filename": filename,
            "size": f"{size_kb:.1f} KB",
            "last_modified": modified,
        })

    content = json.dumps(entries, ensure_ascii=False, indent=2)
    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


@tool
def grep_conversation_history(pattern: str, runtime: ToolRuntime[BibleCopilotContext]) -> Command:
    """
    Regex search across all saved conversation history files for this session.
    Returns matches with filename and line numbers.

    Use this to find specific topics, references, or discussions from earlier
    in the conversation that were summarized away.
    """
    thread_id = runtime.config["configurable"]["thread_id"]
    history_dir = os.path.join(runtime.context.message_history_dir, thread_id)

    if not os.path.isdir(history_dir):
        content = "No conversation history saved yet for this session."
        return Command(update={
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
        })

    files = sorted(f for f in os.listdir(history_dir) if f.endswith(".md"))
    parts = []
    for filename in files:
        filepath = os.path.join(history_dir, filename)
        matches = _grep_file(filepath, pattern)
        if matches:
            parts.append(f"--- {filename} ({len(matches)} matches) ---")
            for line_num, line_text in matches:
                parts.append(f"  {line_num}: {line_text}")

    content = "\n".join(parts) if parts else "No matches found."
    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


@tool
def read_conversation_history(
    filename: str,
    start_line: int,
    end_line: int,
    runtime: ToolRuntime[BibleCopilotContext],
) -> Command:
    """
    Reads specific lines from a conversation history file (1-indexed, inclusive).
    Returns the text with line numbers and the total number of lines.

    Use this after grep_conversation_history to read full context around a match,
    or after list_conversation_history to read an entire history file.
    """
    thread_id = runtime.config["configurable"]["thread_id"]
    filepath = os.path.join(runtime.context.message_history_dir, thread_id, filename)

    try:
        text, total_lines = _read_lines(filepath, start_line, end_line)
        header = f"[{filename}] lines {start_line}-{end_line} of {total_lines}"
        content = f"{header}\n{text}"
    except FileNotFoundError:
        content = f"Error: History file '{filename}' not found for this session."

    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


# ── Save Response Tool ────────────────────────────────────────────────────────


@tool
def save_biblical_response(
    biblical_references: list[BiblePassage],
    interpretation: str | None,
    runtime: ToolRuntime,
) -> Command:
    """
    Save the biblical references and exegetical interpretation for this response.
    Call this BEFORE writing your final answer, after reading all relevant passages.
    Only include passages you actually retrieved with the tools in this turn.

    Args:
        biblical_references: List of passages cited. Each dict must have:
            - book (str): exact book name as it appears in the file index
            - chapter (int): chapter number
            - verse_start (int, optional): first verse number
            - verse_end (int, optional): last verse number
        interpretation: Optional exegetical analysis of the cited passages —
            their literary context, historical background, and theological significance.
            Only reference passages listed in biblical_references.
    """
    return Command(update={
        "bible_response": {
            "message": "",  # filled from final AI message in the node
            "biblical_references": biblical_references or [],
            "interpretation": interpretation,
        },
        "messages": [ToolMessage(
            content="Referências e interpretação salvas.",
            tool_call_id=runtime.tool_call_id,
        )],
    })


# ── Tool lists ─────────────────────────────────────────────────────────────────

SEARCH_RESPONSE_TOOLS = [
    # Knowledge Graph — single Cypher query tool
    *KG_TOOLS,
    # Bible file tools (paths come from KG or system prompt index)
    read_bible_file,
    search_bible_text,
    # Structured response save tool (call before final answer)
    save_biblical_response,
    # Conversation history tools
    list_conversation_history,
    grep_conversation_history,
    read_conversation_history,
]
