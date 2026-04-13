import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import Command

from src.bible_copilot.state import BiblePassage, WebSource
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


def _fetch_page_text(url: str, max_chars: int = 2500) -> str:
    import requests as _req
    try:
        r = _req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
        # Strip script and style blocks first (removes inline JS/CSS noise)
        html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", html)      # strip remaining HTML tags
        text = re.sub(r"\s+", " ", text).strip()   # collapse whitespace
        return text[:max_chars]
    except Exception:
        return ""


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


# ── Web Search Tool ───────────────────────────────────────────────────────────


@tool
def search_web(query: str, runtime: ToolRuntime) -> Command:
    """
    Search the web for Christianity-related information not directly found in the Bible text.

    Use for:
      - Current liturgical season/Sunday (e.g. "3rd Sunday of Advent 2025")
      - Papal encyclicals, apostolic exhortations, Vatican documents
      - Official Catechism of the Catholic Church references
      - Canon Law references
      - Saint feast days, beatifications, canonizations
      - Ecumenical councils and their documents
      - Liturgical norms, sacraments, rites
      - Church history facts not covered by the Bible

    Do NOT use for questions answerable directly from the Bible text.
    The Bible is always the primary source.
    """
    import requests as _requests

    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        content = "Pesquisa web indisponível: SERPER_API_KEY não configurado."
        return Command(update={
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
        })

    try:
        resp = _requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 5, "gl": "br", "hl": "pt"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = [
            {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
            for r in data.get("organic", [])[:5]
        ]

        # Enrich top 3 results with actual page content fetched in parallel
        urls = [r["url"] for r in results[:3]]
        with ThreadPoolExecutor(max_workers=3) as pool:
            page_texts = list(pool.map(_fetch_page_text, urls, timeout=5))
        for result, text in zip(results[:3], page_texts):
            if text:
                result["snippet"] = text

        content = json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        content = f"Erro na busca web: {e}"

    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


# ── Save Response Tool ────────────────────────────────────────────────────────


@tool
def save_biblical_response(
    biblical_references: list[BiblePassage],
    interpretation: str | None,
    web_sources: list[WebSource] | None,
    runtime: ToolRuntime,
) -> Command:
    """
    Save the structured data for this response before writing the final answer.
    Call this AFTER consulting all sources (Bible tools and/or web search).

    Args:
        biblical_references: List of Bible passages cited. Each entry:
            - book (str): exact book slug from the file index (e.g. "genesis", "joao")
            - chapter (int): chapter number
            - verse_start (int, optional): first verse number
            - verse_end (int, optional): last verse number — equals verse_start for a single verse
        interpretation: Optional exegetical analysis of the cited passages —
            literary context, historical background, theological significance.
            Only reference passages listed in biblical_references.
        web_sources: List of web sources used. Each entry:
            - title (str): page title
            - url (str): full URL
            - snippet (str, optional): relevant excerpt from the page
            Include only sources you actually cited in the answer using [1], [2], etc.
    """
    # Auto-fill verse_end from verse_start when missing
    refs = []
    for ref in (biblical_references or []):
        if isinstance(ref, dict):
            r = dict(ref)
            if r.get("verse_start") and not r.get("verse_end"):
                r["verse_end"] = r["verse_start"]
            refs.append(r)
        else:
            refs.append(ref)

    return Command(update={
        "bible_response": {
            "message": "",  # filled from final AI message in the node
            "biblical_references": refs,
            "interpretation": interpretation,
            "web_sources": web_sources or [],
        },
        "messages": [ToolMessage(
            content="Referências, interpretação e fontes web salvas.",
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
    # Web search — for Christianity questions not in the Bible text
    search_web,
    # Structured response save tool (call before final answer)
    save_biblical_response,
    # Conversation history tools
    list_conversation_history,
    grep_conversation_history,
    read_conversation_history,
]
