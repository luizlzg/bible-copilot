"""
Message History Middleware

Detects when SummarizationMiddleware has removed messages (by tracking
disappearing message IDs) and saves the accumulated conversation history
to disk as a Markdown file.

Place this middleware AFTER SummarizationMiddleware in the middleware list
so that when before_model runs, the state already reflects the summarization.
"""

import os
from datetime import datetime, timezone
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.utils.logger import LOGGER


class MessageHistoryMiddleware(AgentMiddleware):
    """
    Tracks message IDs across model calls. When IDs disappear (indicating
    summarization), saves the accumulated message history as Markdown.

    Also maintains a full chronological record of every message ever seen
    (including messages summarized away) for use as the context snapshot.
    """

    def __init__(self) -> None:
        # --- disk-save state (reset after each summarization) ---
        self._known_ids: set[str] = set()
        self._message_accumulator: list[Any] = []

        # --- full context (never reset) ---
        self._all_messages: list[Any] = []
        self._all_seen_ids: set[str] = set()

        # --- per-turn metrics ---
        self._summarization_count: int = 0

        self._thread_id: str | None = None
        self._message_history_dir: str | None = None

    # ── Public accessors ───────────────────────────────────────────────────────

    @property
    def summarization_count(self) -> int:
        return self._summarization_count

    @property
    def all_messages(self) -> list[Any]:
        return list(self._all_messages)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _extract_ids(self, messages: list[Any]) -> set[str]:
        return {msg.id for msg in messages if getattr(msg, "id", None)}

    def _format_message_as_markdown(self, msg: Any) -> str:
        if isinstance(msg, HumanMessage):
            return f"### User\n\n{msg.content}\n"
        elif isinstance(msg, AIMessage):
            content = msg.content or ""
            tool_calls = getattr(msg, "tool_calls", None)
            parts = []
            if content:
                parts.append(f"### Assistant\n\n{content}\n")
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "unknown")
                    args = tc.get("args", {})
                    parts.append(f"### Tool Call: {name}\n\n```\n{args}\n```\n")
            return "\n".join(parts) if parts else f"### Assistant\n\n{content}\n"
        elif isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "tool")
            content = msg.content or ""
            return f"### Tool Result: {name}\n\n```\n{content}\n```\n"
        else:
            return f"### Message ({type(msg).__name__})\n\n{getattr(msg, 'content', str(msg))}\n"

    def _save_history(self) -> None:
        if not self._message_accumulator or not self._thread_id or not self._message_history_dir:
            return

        history_dir = os.path.join(self._message_history_dir, self._thread_id)
        os.makedirs(history_dir, exist_ok=True)

        timestamp = datetime.now(tz=timezone.utc)
        filename = f"history_{timestamp.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = os.path.join(history_dir, filename)

        lines = [
            "# Conversation History\n",
            f"**Thread:** {self._thread_id}  ",
            f"**Archived:** {timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
            f"**Reason:** Summarization triggered  ",
            f"**Messages:** {len(self._message_accumulator)}\n",
            "---\n",
        ]

        for msg in self._message_accumulator:
            lines.append(self._format_message_as_markdown(msg))
            lines.append("---\n")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        LOGGER.info(
            f"Saved conversation history ({len(self._message_accumulator)} messages) "
            f"to {filepath}"
        )

    def set_thread_id(self, thread_id: str) -> None:
        self._thread_id = thread_id

    def before_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        if not self._thread_id:
            from langgraph.config import get_config
            from langgraph.runtime import get_runtime

            self._thread_id = get_config().get("configurable", {}).get("thread_id")
            self._message_history_dir = get_runtime().context.message_history_dir

        messages = state.get("messages", [])

        # Filter out system messages — don't track or accumulate them
        messages = [m for m in messages if not isinstance(m, SystemMessage)]

        current_ids = self._extract_ids(messages)

        # Detect disappearing IDs → summarization happened
        summarization_occurred = False
        if self._known_ids:
            disappeared = self._known_ids - current_ids
            if disappeared:
                LOGGER.info(
                    f"Detected {len(disappeared)} disappeared message IDs — "
                    f"summarization triggered, saving history..."
                )
                self._save_history()
                # Reset disk-save batch; _all_messages is NEVER reset
                self._message_accumulator = []
                self._known_ids = set()
                self._summarization_count += 1
                summarization_occurred = True

        # Accumulate to disk-save batch (resets after each save)
        for msg in messages:
            msg_id = getattr(msg, "id", None)
            if msg_id and msg_id not in self._known_ids:
                self._message_accumulator.append(msg)
                self._known_ids.add(msg_id)

        # Accumulate to full history — original messages first, then summary
        # messages as they appear (in chronological order, never reset)
        for msg in messages:
            msg_id = getattr(msg, "id", None)
            if msg_id and msg_id not in self._all_seen_ids:
                self._all_messages.append(msg)
                self._all_seen_ids.add(msg_id)

        if summarization_occurred:
            # Use self._summarization_count (instance var) — state.get() would
            # always read the initial value because middleware return values are
            # not reflected back in the state dict passed to subsequent calls.
            return {"summarization_count": self._summarization_count}
        return None
