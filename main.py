"""
Bible Copilot — API server

Usage:
  uv run uvicorn main:app --reload
  uv run uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
import warnings
from uuid import uuid4

from dotenv import load_dotenv

# LangGraph's Runtime dataclass uses a generic `context: ContextT = None` that
# Pydantic can't resolve to the concrete schema at serialization time, so it warns
# when the MemorySaver checkpointer serializes a Runtime object carrying our context.
# The serialization is functionally correct — this is a known LangGraph gap.
warnings.filterwarnings(
    "ignore",
    message=r".*field_name='context'.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*Pydantic serializer warnings.*",
    category=UserWarning,
)
from fastapi import FastAPI
from langchain.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

load_dotenv()

from src.bible_copilot.graph import build_graph  # noqa: E402
from src.bible_copilot.state import coerce_bible_response  # noqa: E402
from src.bible_copilot.verse_extractor import _build_book_index, extract_reference_text  # noqa: E402
from src.config import BIBLE_DATA_DIR, MESSAGE_HISTORY_DIR, KG_PATH, BibleCopilotContext  # noqa: E402
from src.utils.logger import LOGGER  # noqa: E402
from src.utils.observability import setup_langsmith_tracing  # noqa: E402

REQUIRED_ENV_VARS = ["OPENROUTER_API_KEY"]


def _check_env_vars(required: list[str]) -> None:
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {missing}")


def _check_kg_exists() -> None:
    if not os.path.isfile(KG_PATH):
        raise FileNotFoundError(
            f"Knowledge Graph not found at '{KG_PATH}'. "
            "Run the /generate-kg skill first (see skills/generate-kg/SKILL.md)."
        )


setup_langsmith_tracing()
_check_env_vars(REQUIRED_ENV_VARS)
_check_kg_exists()

app = FastAPI(title="Bible Copilot")

_graph = build_graph(checkpointer=MemorySaver())
_book_index = _build_book_index(bible_data_dir=BIBLE_DATA_DIR)
_context = BibleCopilotContext(
    bible_data_dir=BIBLE_DATA_DIR,
    message_history_dir=MESSAGE_HISTORY_DIR,
    kg_path=KG_PATH,
)


# ── Request / Response models ──────────────────────────────────────────────────


class SessionResponse(BaseModel):
    thread_id: str


class ChatRequest(BaseModel):
    message: str
    thread_id: str


class BiblePassageResponse(BaseModel):
    book: str
    chapter: int
    verse_start: int | None = None
    verse_end: int | None = None
    text: str | None = None


class ChatResponse(BaseModel):
    thread_id: str
    message: str
    biblical_references: list[BiblePassageResponse] | None = None
    interpretation: str | None = None
    error: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.post("/session", response_model=SessionResponse)
def new_session() -> SessionResponse:
    return SessionResponse(thread_id=str(uuid4()))


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    config = {
        "configurable": {"thread_id": request.thread_id},
        "recursion_limit": 1000,
    }

    LOGGER.info(f"[{request.thread_id}] {request.message!r}")

    result = _graph.invoke(
        {"messages": [HumanMessage(content=request.message)]},
        config=config,
        context=_context,
    )

    thread_id = request.thread_id

    if result.get("invalid_input"):
        return ChatResponse(
            thread_id=thread_id,
            message="",
            error=result.get("error_message", "Unknown error."),
        )

    bible_response = coerce_bible_response(result.get("bible_response"))
    if bible_response:
        refs = [
            r for r in (bible_response.get("biblical_references") or [])
            if r.get("book") and r.get("chapter")
        ]
        passages = [
            BiblePassageResponse(
                book=r["book"],
                chapter=r["chapter"],
                verse_start=r.get("verse_start"),
                verse_end=r.get("verse_end"),
                text=extract_reference_text(r, _book_index) or None,
            )
            for r in refs
        ]
        interp = bible_response.get("interpretation")
        return ChatResponse(
            thread_id=thread_id,
            message=bible_response.get("message", ""),
            biblical_references=passages or None,
            interpretation=interp if interp not in (None, "None", "null") else None,
        )

    messages = result.get("messages", [])
    return ChatResponse(
        thread_id=thread_id,
        message=messages[-1].content if messages else "",
    )
