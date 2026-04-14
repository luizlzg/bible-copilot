"""
Bible Copilot — API server

Usage:
  uv run uvicorn main:app --reload
  uv run uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import os
import time
import unicodedata
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

import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel

load_dotenv()

from src.bible_copilot.graph import build_graph  # noqa: E402
from src.bible_copilot.state import coerce_bible_response  # noqa: E402
from src.bible_copilot.verse_extractor import _build_book_index, build_label_index, extract_reference_text  # noqa: E402
from src.config import BIBLE_DATA_DIR, MESSAGE_HISTORY_DIR, KG_PATH, BibleCopilotContext  # noqa: E402
from src.utils.logger import LOGGER  # noqa: E402
from src.utils.observability import setup_langsmith_tracing  # noqa: E402
from src.utils.supabase_client import get_supabase  # noqa: E402
from src.utils.usage import build_context_snapshot  # noqa: E402
from src.utils.pricing import compute_cost  # noqa: E402

REQUIRED_ENV_VARS = ["OPENROUTER_API_KEY", "SUPABASE_DB_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SERPER_API_KEY"]


def _normalize_slug(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


_graph = None
_book_index = None
_label_index: dict[str, str] = {}
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
    pre_turn_messages: list,
    usage: dict,
    time_to_first_token_s: float,
    model_name: str,
    device_info: dict | None = None,
    message_id: str | None = None,
) -> None:
    """Write message + update session in Supabase after each /chat call."""
    try:
        db = get_supabase()

        # Look up session row (user_id + running totals + conversation history)
        session_row = (
            db.table("sessions")
            .select(
                "user_id, num_user_messages, num_ai_messages, mean_time_to_first_token, "
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
        context_snapshot = build_context_snapshot(pre_turn_messages, result_messages)

        cost = compute_cost(
            model_id=model_name,
            input_tokens=usage.get("input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            cache_read_tokens=usage.get("cache_read_tokens") or 0,
            cache_creation_tokens=usage.get("cache_creation_tokens") or 0,
        )

        # Insert message record (use pre-generated message_id if provided)
        insert_data = {
            "session_id": thread_id,
            "user_id": user_id,
            "user_message": user_message,
            "ai_response": ai_response,
            "message_type": "ai_message",
            "model_name": model_name,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cache_read_tokens": usage.get("cache_read_tokens"),
            "cache_creation_tokens": usage.get("cache_creation_tokens"),
            "input_cost": cost["input_cost"],
            "output_cost": cost["output_cost"],
            "cache_read_cost": cost["cache_read_cost"],
            "cache_write_cost": cost["cache_write_cost"],
            "total_cost": cost["total_cost"],
            "num_tool_calls": usage["num_tool_calls"],
            "context": context_snapshot,
            "time_to_first_token": round(time_to_first_token_s, 3),
            "device_type": device_info.get("type") if device_info else None,
            "device_info": device_info,
        }
        if message_id:
            insert_data["message_id"] = message_id

        db.table("messages").insert(insert_data).execute()

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
                "message_id": message_id,
                "role": "assistant",
                "content": ai_response.get("message", ""),
                "biblical_references": ai_response.get("biblical_references"),
                "interpretation": ai_response.get("interpretation"),
                "web_sources": ai_response.get("web_sources"),
                "timestamp": timestamp,
            },
        ]

        # Accumulate session-level totals
        prev_ai = session_row.data["num_ai_messages"] or 0
        prev_mean = session_row.data.get("mean_time_to_first_token") or 0.0
        new_ai = prev_ai + 1
        new_mean = (prev_mean * prev_ai + time_to_first_token_s) / new_ai

        db.table("sessions").update({
            "num_user_messages": (session_row.data["num_user_messages"] or 0) + 1,
            "num_ai_messages": new_ai,
            "mean_time_to_first_token": round(new_mean, 3),
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
    global _graph, _book_index, _label_index, _context

    setup_langsmith_tracing()
    _check_env_vars(REQUIRED_ENV_VARS)
    _check_kg_exists()

    _book_index = _build_book_index(bible_data_dir=BIBLE_DATA_DIR)
    _label_index = build_label_index(kg_path=KG_PATH)
    _context = BibleCopilotContext(
        bible_data_dir=BIBLE_DATA_DIR,
        message_history_dir=MESSAGE_HISTORY_DIR,
        kg_path=KG_PATH,
    )

    db_url = os.environ["SUPABASE_DB_URL"]
    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        await checkpointer.setup()  # idempotent — creates checkpoint tables on first run
        _graph = build_graph(checkpointer=checkpointer)
        LOGGER.info("AsyncPostgresSaver connected and ready.")
        yield  # server runs here

    LOGGER.info("Shutting down — AsyncPostgresSaver connection closed.")


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
    device_info: dict | None = None


class BiblePassageResponse(BaseModel):
    book: str
    chapter: int
    verse_start: int | None = None
    verse_end: int | None = None
    text: str | None = None


class WebSourceResponse(BaseModel):
    title: str
    url: str
    snippet: str | None = None


class ChatResponse(BaseModel):
    thread_id: str
    message_id: str | None = None
    message: str
    biblical_references: list[BiblePassageResponse] | None = None
    interpretation: str | None = None
    web_sources: list[WebSourceResponse] | None = None
    error: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.post("/session", response_model=SessionResponse)
def new_session() -> SessionResponse:
    return SessionResponse(thread_id=str(uuid4()))


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    config = {
        "configurable": {"thread_id": request.thread_id},
        "recursion_limit": 1000,
    }

    LOGGER.info(f"[{request.thread_id}] {request.message!r}")

    model_name = os.getenv("SEARCH_RESPONSE_MODEL", "anthropic/claude-sonnet-4-20250514")

    async def generate():
        t0 = time.perf_counter()
        t_first_token: float | None = None
        final_node_output: dict | None = None
        # Per-turn usage — captured from events so we only count this turn's LLM calls,
        # not the accumulated history that result_messages carries from all previous turns.
        last_input_tokens = 0       # last LLM call — full context size (input re-sends all previous tokens)
        last_cache_read_tokens = 0  # last LLM call — cache hits grow with context, not additive
        last_cache_creation_tokens = 0
        turn_output_tokens = 0      # sum — each call generates new output tokens (no overlap)
        turn_tool_calls = 0
        # Full message state at node entry — before the agent runs and before any
        # summarization that may happen during this turn.
        pre_turn_messages: list = []

        try:
            async for event in _graph.astream_events(
                {"messages": [HumanMessage(content=request.message)]},
                config=config,
                context=_context,
                version="v2",
            ):
                kind = event["event"]
                name = event.get("name", "")
                metadata = event.get("metadata", {})
                if metadata.get("langgraph_node") == "SummarizationMiddleware.before_model":
                    continue

                if kind == "on_chain_start" and metadata.get("langgraph_node") == "search_response":
                    pre_turn_messages = (event.get("data", {}).get("input") or {}).get("messages", [])

                elif kind == "on_tool_start":
                    input_data = event.get("data", {}).get("input", {})
                    yield _sse("tool_start", {"tool": name, "input": input_data})

                elif kind == "on_tool_end":
                    turn_tool_calls += 1

                elif kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk:
                        content = chunk.content if isinstance(chunk.content, str) else ""
                        tc_chunks = getattr(chunk, "tool_call_chunks", None) or []
                        if content and not tc_chunks:
                            if t_first_token is None:
                                t_first_token = time.perf_counter()
                            yield _sse("token", {"token": content})

                elif kind == "on_chat_model_end":
                    output_msg = event.get("data", {}).get("output")
                    if output_msg:
                        um = getattr(output_msg, "usage_metadata", None) or {}
                        in_tok = um.get("input_tokens", 0)
                        out_tok = um.get("output_tokens", 0)
                        details = um.get("input_token_details") or {}
                        cache_read = details.get("cache_read", 0) or 0
                        cache_creation = details.get("cache_creation", 0) or 0
                        last_input_tokens = in_tok
                        last_cache_read_tokens = cache_read
                        last_cache_creation_tokens = cache_creation
                        turn_output_tokens += out_tok
                        LOGGER.info(
                            f"[{request.thread_id}] LLM call — "
                            f"input={in_tok} output={out_tok} "
                            f"cache_read={cache_read} cache_creation={cache_creation}"
                        )

                elif kind == "on_chain_end" and metadata.get("langgraph_node") == "search_response":
                    final_node_output = event.get("data", {}).get("output") or {}

            time_to_first_token_s = (t_first_token - t0) if t_first_token is not None else (time.perf_counter() - t0)
            thread_id = request.thread_id

            turn_cost = compute_cost(
                model_id=model_name,
                input_tokens=last_input_tokens,
                output_tokens=turn_output_tokens,
                cache_read_tokens=last_cache_read_tokens,
                cache_creation_tokens=last_cache_creation_tokens,
            )
            LOGGER.info(
                f"[{thread_id}] Turn cost — "
                f"input=${turn_cost['input_cost']} output=${turn_cost['output_cost']} "
                f"cache_read=${turn_cost['cache_read_cost']} cache_write=${turn_cost['cache_write_cost']} "
                f"total=${turn_cost['total_cost']}"
            )

            if not final_node_output or final_node_output.get("invalid_input"):
                err = (final_node_output or {}).get("error_message", "Erro interno.")
                yield _sse("error", {"error": err})
                return

            result_messages = final_node_output.get("messages", [])
            bible_response = coerce_bible_response(final_node_output.get("bible_response"))

            if bible_response:
                refs = [
                    r for r in (bible_response.get("biblical_references") or [])
                    if r.get("book") and r.get("chapter")
                ]
                passages = [
                    BiblePassageResponse(
                        book=_label_index.get(_normalize_slug(r.get("book", "")), r.get("book", "")),
                        chapter=r["chapter"],
                        verse_start=r.get("verse_start"),
                        verse_end=r.get("verse_end") or r.get("verse_start"),
                        text=extract_reference_text(r, _book_index) or None,
                    )
                    for r in refs
                ]
                interp = bible_response.get("interpretation")
                raw_sources = bible_response.get("web_sources") or []
                web_sources = [
                    WebSourceResponse(
                        title=s.get("title", ""),
                        url=s.get("url", ""),
                        snippet=s.get("snippet") or None,
                    )
                    for s in raw_sources
                    if s.get("url")
                ]
                response = ChatResponse(
                    thread_id=thread_id,
                    message=bible_response.get("message", ""),
                    biblical_references=passages or None,
                    interpretation=interp if interp not in (None, "None", "null") else None,
                    web_sources=web_sources or None,
                )
            else:
                response = ChatResponse(
                    thread_id=thread_id,
                    message=result_messages[-1].content if result_messages else "",
                )

            # Pre-generate message_id so we can include it in done without blocking
            message_id = str(uuid4())
            response.message_id = message_id

            # Yield done immediately — don't block on DB write
            yield _sse("done", response.model_dump())

            # Persist asynchronously in a thread so it doesn't delay the response close
            ai_response_dict = {
                "message": response.message,
                "biblical_references": [p.model_dump() for p in (response.biblical_references or [])],
                "interpretation": response.interpretation,
                "web_sources": [s.model_dump() for s in (response.web_sources or [])],
            }
            turn_usage = {
                "input_tokens": last_input_tokens or None,
                "output_tokens": turn_output_tokens or None,
                "cache_read_tokens": last_cache_read_tokens or None,
                "cache_creation_tokens": last_cache_creation_tokens or None,
                "num_tool_calls": turn_tool_calls or None,
                **turn_cost,
            }
            asyncio.create_task(asyncio.to_thread(
                _persist_to_supabase,
                thread_id,
                request.message,
                ai_response_dict,
                result_messages,
                pre_turn_messages,
                turn_usage,
                time_to_first_token_s,
                model_name,
                request.device_info,
                message_id,
            ))

        except Exception as e:
            LOGGER.error(f"[{request.thread_id}] Chat SSE error: {e}", exc_info=True)
            yield _sse("error", {"error": str(e)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
