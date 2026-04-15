from typing import Any


def build_context_snapshot(pre_turn_messages: list[Any]) -> list[dict]:
    """
    Build a context snapshot from the messages present at the START of this turn
    (before the agent ran and before any summarization happened this turn).

    Using pre_turn_messages instead of result_messages avoids including summary
    messages that the SummarizationMiddleware creates during this turn, which
    would appear out of place in the stored context.
    """
    snapshot = []
    for msg in pre_turn_messages:
        role = type(msg).__name__.replace("Message", "").lower()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        snapshot.append({"role": role, "content": content})
    return snapshot
