from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


def extract_usage(messages: list[Any]) -> dict:
    """
    Extract token counts and tool call count from a LangGraph result message list.

    AIMessage objects carry usage_metadata populated by OpenRouter (same format
    as OpenAI). ToolMessage objects are counted as tool calls.
    """
    input_tokens = 0
    output_tokens = 0
    num_tool_calls = 0

    for msg in messages:
        if isinstance(msg, AIMessage) and msg.usage_metadata:
            input_tokens += msg.usage_metadata.get("input_tokens", 0)
            output_tokens += msg.usage_metadata.get("output_tokens", 0)
        if isinstance(msg, ToolMessage):
            num_tool_calls += 1

    return {
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "num_tool_calls": num_tool_calls or None,
    }


def build_context_snapshot(messages: list[Any], max_messages: int = 20) -> list[dict]:
    """
    Build a compact context snapshot from the last N messages.
    Stored in messages.context for debugging/analytics.
    """
    recent = messages[-max_messages:] if len(messages) > max_messages else messages
    snapshot = []
    for msg in recent:
        role = type(msg).__name__.replace("Message", "").lower()
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        snapshot.append({
            "role": role,
            "content": content
        })
    return snapshot
