import os
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langgraph.runtime import Runtime

from src.bible_copilot.file_index import build_bible_file_index
from src.bible_copilot.prompts import SEARCH_RESPONSE_PROMPT
from src.bible_copilot.state import GraphState, coerce_bible_response
from src.bible_copilot.tools import SEARCH_RESPONSE_TOOLS
from src.config import BibleCopilotContext
from src.kg.context import build_kg_index
from src.middleware import (
    StructuredOutputValidationError,
    MessageHistoryMiddleware,
    SaveResponseValidatorMiddleware,
)
from src.utils.logger import LOGGER

MAX_RETRIES = int(os.getenv("STRUCTURED_OUTPUT_MAX_RETRIES", "3"))


# ── LLM factory ────────────────────────────────────────────────────────────────


def _make_llm(model_name: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        streaming=True,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
        extra_body={
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    )


# ── Search + Response Agent ───────────────────────────────────────────────────


def create_search_response_agent(model_name: str, bible_data_dir: str, kg_path: str):
    llm = _make_llm(model_name)

    summarization = SummarizationMiddleware(
        model=llm,
        trigger=("tokens", int(os.getenv("SUMMARIZATION_TRIGGER_TOKENS", 90000))),
        keep=("messages", int(os.getenv("SUMMARIZATION_KEEP_MESSAGES", 5))),
        trim_tokens_to_summarize=None
    )
    history = MessageHistoryMiddleware()
    save_validator = SaveResponseValidatorMiddleware()

    current_date = datetime.now(timezone.utc).strftime("%A, %d de %B de %Y")
    system_prompt = SEARCH_RESPONSE_PROMPT.format(
        bible_file_index=build_bible_file_index(bible_data_dir=bible_data_dir),
        kg_index=build_kg_index(kg_path=kg_path),
        current_date=current_date,
    )

    return create_agent(
        model=llm,
        tools=SEARCH_RESPONSE_TOOLS,
        system_prompt=system_prompt,
        state_schema=GraphState,
        middleware=[summarization, history, save_validator],
    )


async def search_response_node(state: GraphState, runtime: Runtime[BibleCopilotContext]) -> dict:
    LOGGER.info("=" * 60)
    LOGGER.info("RUNNING SEARCH + RESPONSE AGENT")
    LOGGER.info("=" * 60)

    model_name = os.getenv("SEARCH_RESPONSE_MODEL", "anthropic/claude-sonnet-4-20250514")
    bible_data_dir = runtime.context.bible_data_dir
    kg_path = runtime.context.kg_path

    logged_ids = {msg.id for msg in state.get("messages", []) if hasattr(msg, "id") and msg.id}

    retry_count = 0
    while retry_count <= MAX_RETRIES:
        try:
            agent = create_search_response_agent(model_name, bible_data_dir=bible_data_dir, kg_path=kg_path)
            LOGGER.info(f"Invoking Search Response Agent (attempt {retry_count + 1}/{MAX_RETRIES + 1})...")

            # Clear bible_response so old turn data doesn't bleed if the tool isn't called
            state = {**state, "bible_response": {}}

            result = None
            async for event in agent.astream(state, stream_mode="values"):
                if "messages" in event:
                    for msg in event["messages"]:
                        msg_id = getattr(msg, "id", None)
                        if msg_id and msg_id not in logged_ids:
                            logged_ids.add(msg_id)
                            LOGGER.info(msg.pretty_repr())
                result = event

            new_messages = result.get("messages", []) if result else []

            # bible_response was cleared before running, so this only has data if
            # the agent called save_biblical_response this turn
            saved_data = result.get("bible_response") if result else None
            if isinstance(saved_data, str):
                saved_data = coerce_bible_response(saved_data)
            saved_data = saved_data or {}

            # Final AI message content is the streamed natural-language answer
            last_ai = next(
                (m for m in reversed(new_messages)
                 if isinstance(m, AIMessage) and not m.tool_calls and m.content),
                None,
            )
            final_message = last_ai.content if last_ai else ""

            bible_response = {
                "message": final_message,
                "biblical_references": saved_data.get("biblical_references", []),
                "interpretation": saved_data.get("interpretation"),
                "web_sources": saved_data.get("web_sources", []),
            }

            LOGGER.info("Search Response Agent completed successfully.")
            return {"messages": new_messages, "bible_response": bible_response}

        except StructuredOutputValidationError as e:
            retry_count += 1
            LOGGER.warning(f"Response validation failed (attempt {retry_count}): {e}")
            if retry_count > MAX_RETRIES:
                LOGGER.error("Search Response: max retries exceeded.")
                return {"invalid_input": True, "error_message": str(e)}
            state = e.state
            state["messages"] = e.messages + [
                HumanMessage(content=e.error_feedback_message)
            ]

        except Exception as e:
            LOGGER.error(f"Search Response unexpected error: {e}", exc_info=True)
            return {"invalid_input": True, "error_message": str(e)}

    return {"invalid_input": True, "error_message": "Max retries exceeded"}
