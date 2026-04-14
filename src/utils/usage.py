from typing import Any


def build_context_snapshot(pre_turn_messages: list[Any], result_messages: list[Any]) -> list[dict]:
    """
    Build a context snapshot from result_messages, excluding the final answer
    (which is already stored in ai_response). The final answer is identified as
    the last AIMessage without tool_calls that has non-empty content.
    """
    from langchain_core.messages import AIMessage

    final_answer_id: str | None = None
    for msg in reversed(result_messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None) and msg.content:
            final_answer_id = getattr(msg, "id", None)
            break

    snapshot = []
    for msg in result_messages:
        if getattr(msg, "id", None) == final_answer_id:
            continue
        role = type(msg).__name__.replace("Message", "").lower()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        snapshot.append({"role": role, "content": content})
    return snapshot
