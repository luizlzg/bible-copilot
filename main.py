"""
Bible Copilot — API server

Usage:
  uv run uvicorn main:app --reload
  uv run uvicorn main:app --host 0.0.0.0 --port 8000
"""

import os
import time
import warnings
from contextlib import asynccontextmanager
from uuid import uuid4

from dotenv import load_dotenv

# LangGraph's Runtime dataclass uses a generic `context: ContextT = None` that
# Pydantic can't resolve to the concrete schema at serialization time, so it warns
# when the checkpointer serializes a Runtime object carrying our context.
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
from fastapi.middleware.cors import CORSMiddleware
from langchain.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel

load_dotenv()

from src.bible_copilot.graph import build_graph  # noqa: E402
from src.bible_copilot.state import coerce_bible_response  # noqa: E402
from src.bible_copilot.verse_extractor import _build_book_index, extract_reference_text  # noqa: E402
from src.config import BIBLE_DATA_DIR, MESSAGE_HISTORY_DIR, KG_PATH, BibleCopilotContext  # noqa: E402
from src.utils.logger import LOGGER  # noqa: E402
from src.utils.observability import setup_langsmith_tracing  # noqa: E402
from src.utils.supabase_client import get_supabase  # noqa: E402
from src.utils.usage import extract_usage, build_context_snapshot  # noqa: E402

REQUIRED_ENV_VARS = ["OPENROUTER_API_KEY", "SUPABASE_DB_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]

_graph = None
_book_index = None
_context: BibleCopilotContext | None = None


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


def _persist_to_supabase(
    thread_id: str,
    user_message: str,
    ai_response: dict,
    result_messages: list,
    time_to_answer_s: float,
    model_name: str,
) -> None:
    """Write message + update session in Supabase after each /chat call."""
    try:
        db = get_supabase()

        # Look up session row (user_id + running totals + conversation history)
        session_row = (
            db.table("sessions")
            .select(
                "user_id, num_user_messages, num_ai_messages, mean_time_to_answer, "
                "total_input_tokens, total_output_tokens, num_tool_calls, conversation_history"
            )
            .eq("session_id", thread_id)
            .maybe_single()
            .execute()
        )
        if not session_row.data:
            LOGGER.warning(f"[{thread_id}] Session not found in Supabase — skipping persistence.")
            return

        user_id = session_row.data["user_id"]
        usage = extract_usage(result_messages)
        context_snapshot = build_context_snapshot(result_messages)

        # Insert message record
        db.table("messages").insert({
            "session_id": thread_id,
            "user_id": user_id,
            "user_message": user_message,
            "ai_response": ai_response,
            "message_type": "ai_message",
            "model_name": model_name,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "num_tool_calls": usage["num_tool_calls"],
            "context": context_snapshot,
            "time_to_answer": round(time_to_answer_s, 1),
        }).execute()

        # Append user + assistant entries to conversation_history (used by sidebar labels and page restore)
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        prev_history = session_row.data.get("conversation_history") or []
        new_history = prev_history + [
            {
                "id": str(uuid4()),
                "role": "user",
                "content": user_message,
                "timestamp": timestamp,
            },
            {
                "id": str(uuid4()),
                "role": "assistant",
                "content": ai_response.get("message", ""),
                "biblical_references": ai_response.get("biblical_references"),
                "interpretation": ai_response.get("interpretation"),
                "timestamp": timestamp,
            },
        ]

        # Accumulate session-level totals
        prev_ai = session_row.data["num_ai_messages"] or 0
        prev_mean = session_row.data["mean_time_to_answer"] or 0.0
        new_ai = prev_ai + 1
        new_mean = (prev_mean * prev_ai + time_to_answer_s) / new_ai

        db.table("sessions").update({
            "num_user_messages": (session_row.data["num_user_messages"] or 0) + 1,
            "num_ai_messages": new_ai,
            "mean_time_to_answer": round(new_mean, 1),
            "total_input_tokens": (session_row.data["total_input_tokens"] or 0) + (usage["input_tokens"] or 0),
            "total_output_tokens": (session_row.data["total_output_tokens"] or 0) + (usage["output_tokens"] or 0),
            "num_tool_calls": (session_row.data["num_tool_calls"] or 0) + (usage["num_tool_calls"] or 0),
            "conversation_history": new_history,
            "updated_at": "now()",
        }).eq("session_id", thread_id).execute()

    except Exception as exc:
        # Persistence failures must never break the chat response
        LOGGER.error(f"[{thread_id}] Supabase persistence failed: {exc}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph, _book_index, _context

    setup_langsmith_tracing()
    _check_env_vars(REQUIRED_ENV_VARS)
    _check_kg_exists()

    _book_index = _build_book_index(bible_data_dir=BIBLE_DATA_DIR)
    _context = BibleCopilotContext(
        bible_data_dir=BIBLE_DATA_DIR,
        message_history_dir=MESSAGE_HISTORY_DIR,
        kg_path=KG_PATH,
    )

    db_url = os.environ["SUPABASE_DB_URL"]
    with PostgresSaver.from_conn_string(db_url) as checkpointer:
        checkpointer.setup()  # idempotent — creates checkpoint tables on first run
        _graph = build_graph(checkpointer=checkpointer)
        LOGGER.info("PostgresSaver connected and ready.")
        yield  # server runs here

    LOGGER.info("Shutting down — PostgresSaver connection closed.")


app = FastAPI(title="Bible Copilot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

    model_name = os.getenv("SEARCH_RESPONSE_MODEL", "anthropic/claude-sonnet-4-20250514")
    t0 = time.perf_counter()

    result = _graph.invoke(
        {"messages": [HumanMessage(content=request.message)]},
        config=config,
        context=_context,
    )

    elapsed_s = time.perf_counter() - t0
    thread_id = request.thread_id
    result_messages = result.get("messages", [])

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
        response = ChatResponse(
            thread_id=thread_id,
            message=bible_response.get("message", ""),
            biblical_references=passages or None,
            interpretation=interp if interp not in (None, "None", "null") else None,
        )
    else:
        response = ChatResponse(
            thread_id=thread_id,
            message=result_messages[-1].content if result_messages else "",
        )

    # Persist to Supabase (non-blocking best-effort)
    ai_response_dict = {
        "message": response.message,
        "biblical_references": [p.model_dump() for p in (response.biblical_references or [])],
        "interpretation": response.interpretation,
    }
    _persist_to_supabase(
        thread_id=thread_id,
        user_message=request.message,
        ai_response=ai_response_dict,
        result_messages=result_messages,
        time_to_answer_s=elapsed_s,
        model_name=model_name,
    )

    return response
