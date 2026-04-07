import os
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.agents.middleware.summarization import SummarizationMiddleware
from langgraph.runtime import Runtime

from src.bible_copilot.file_index import build_bible_file_index
from src.bible_copilot.prompts import SEARCH_RESPONSE_PROMPT
from src.bible_copilot.state import BibleResponse, GraphState, coerce_bible_response
from src.bible_copilot.tools import SEARCH_RESPONSE_TOOLS
from src.config import BibleCopilotContext
from src.kg.context import build_kg_index
from src.middleware import (
    StructuredOutputValidationError,
    StructuredOutputValidatorMiddleware,
    MessageHistoryMiddleware,
)
from src.utils.logger import LOGGER

MAX_RETRIES = int(os.getenv("STRUCTURED_OUTPUT_MAX_RETRIES", "3"))


# ── LLM factory ────────────────────────────────────────────────────────────────


def _make_llm(model_name: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )


# ── Search + Response Agent ───────────────────────────────────────────────────


def create_search_response_agent(model_name: str, bible_data_dir: str, kg_path: str):
    llm = _make_llm(model_name)

    validator = StructuredOutputValidatorMiddleware(
        expected_schema=BibleResponse,
    )

    # Middleware order matters:
    # 1. SummarizationMiddleware — compresses old messages when token threshold is hit
    # 2. MessageHistoryMiddleware — detects disappeared IDs (from summarization) and saves history
    # 3. StructuredOutputValidatorMiddleware — validates the final structured response
    summarization = SummarizationMiddleware(
        model=llm,
        trigger=("tokens", int(os.getenv("SUMMARIZATION_TRIGGER_TOKENS", 90000))),
        keep=("messages", int(os.getenv("SUMMARIZATION_KEEP_MESSAGES", 5))),
        trim_tokens_to_summarize=None
    )
    history = MessageHistoryMiddleware()

    system_prompt = SEARCH_RESPONSE_PROMPT.format(
        bible_file_index=build_bible_file_index(bible_data_dir=bible_data_dir),
        kg_index=build_kg_index(kg_path=kg_path),
    )

    return create_agent(
        model=llm,
        tools=SEARCH_RESPONSE_TOOLS,
        system_prompt=system_prompt,
        state_schema=GraphState,
        response_format=ToolStrategy(BibleResponse),
        middleware=[summarization, history, validator],
    )


def search_response_node(state: GraphState, runtime: Runtime[BibleCopilotContext]) -> dict:
    LOGGER.info("=" * 60)
    LOGGER.info("RUNNING SEARCH + RESPONSE AGENT")
    LOGGER.info("=" * 60)

    model_name = os.getenv("SEARCH_RESPONSE_MODEL", "anthropic/claude-sonnet-4-20250514")
    bible_data_dir = runtime.context.bible_data_dir
    kg_path = runtime.context.kg_path

    # Track existing message IDs so we only log NEW messages from this turn
    # (count-based tracking breaks when summarization replaces old messages)
    logged_ids = {msg.id for msg in state.get("messages", []) if hasattr(msg, "id") and msg.id}

    retry_count = 0
    while retry_count <= MAX_RETRIES:
        try:
            agent = create_search_response_agent(model_name, bible_data_dir=bible_data_dir, kg_path=kg_path)
            LOGGER.info(f"Invoking Search Response Agent (attempt {retry_count + 1}/{MAX_RETRIES + 1})...")

            result = None
            for event in agent.stream(state, stream_mode="values"):
                if "messages" in event:
                    for msg in event["messages"]:
                        msg_id = getattr(msg, "id", None)
                        if msg_id and msg_id not in logged_ids:
                            logged_ids.add(msg_id)
                            LOGGER.info(msg.pretty_repr())
                result = event

            bible_response = coerce_bible_response(result.get("structured_response")) if result else None
            new_messages = result.get("messages", []) if result else []
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
