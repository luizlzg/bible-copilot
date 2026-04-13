# Bible Copilot

A conversational Bible and Christian faith assistant served as a REST API. Built with LangGraph, LangChain, and OpenRouter.

Supports the full range of how people engage with the Bible and Christian life:
- **Life situations and emotions** — grief, anxiety, forgiveness, purpose, fear, betrayal
- **Topical and doctrinal study** — what the Bible says about marriage, money, suffering, prayer
- **Book and chapter comprehension** — summaries, themes, narrative context
- **Historical and timeline questions** — the exile, the early church, the patriarchs
- **Character studies** — David, Ruth, Paul, the prophets
- **Reading guidance** — where to start, what to read next
- **Liturgy and Church** — current liturgical season, Sunday Gospel, Catechism, saints' feast days, Vatican documents, ecumenical councils

The agent navigates a Knowledge Graph before opening any file, reads the actual Bible text for every passage it cites, and uses web search for Church and liturgy questions not covered by Scripture. It never quotes from memory.

---

## Stack

- **LangGraph** — stateful multi-turn agent graph with `AsyncPostgresSaver` (Supabase Postgres) for persistent checkpointing
- **LangChain** — agent creation, middleware (summarization, message history, structured output validation)
- **OpenRouter** — LLM provider (`ChatOpenAI` pointed at `openrouter.ai/api/v1`)
- **FastAPI** — REST API server with Server-Sent Events streaming
- **GrandCypher + NetworkX** — in-memory Cypher queries over the Bible Knowledge Graph
- **Supabase** — Postgres checkpointer + session/message persistence
- **Serper** — web search for Church and liturgy questions

---

## Setup

**1. Install dependencies**

```bash
uv sync
```

**2. Configure environment**

```bash
cp .env.example .env
# edit .env with your keys
```

**3. Download the Bible**

```bash
uv run python scripts/download_bible_ptbr.py               # Almeida Atualizada (default)
uv run python scripts/download_bible_ptbr.py --version nvi  # Nova Versão Internacional
```

This downloads all 66 books as Markdown files to `.bible_data/pt-br/`.

**4. Generate the Knowledge Graph** *(mandatory — not committed to the repo)*

```
/generate-kg
```

Run this Claude Code skill to build `src/kg/data/bible_index.json` from the downloaded Bible files. This step is required before starting the API — the server will refuse to start without the KG file. See `skills/generate-kg/SKILL.md` for what the skill does.

---

## Running the API

```bash
uv run uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

---

## API

### `POST /session`

Creates a new conversation. Returns a `thread_id` that must be sent with every subsequent message in that conversation.

**Response**
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### `POST /chat`

Sends a message in an existing conversation. Returns a Server-Sent Events stream.

**Request**
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Estou com ansiedade, o que a Bíblia diz sobre isso?"
}
```

**SSE events**

| Event | Payload | Description |
|-------|---------|-------------|
| `tool_start` | `{"tool": "read_bible_file", "input": {...}}` | Agent called a tool |
| `token` | `{"token": "..."}` | Streamed token of the final answer |
| `done` | Full `ChatResponse` JSON | Response complete |
| `error` | `{"error": "..."}` | Unrecoverable error |

**`done` payload example**
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "message_id": "...",
  "message": "A Bíblia fala diretamente sobre a ansiedade em vários lugares...",
  "biblical_references": [
    {
      "book": "Filipenses",
      "chapter": 4,
      "verse_start": 6,
      "verse_end": 7,
      "text": "**6** Não andeis ansiosos por coisa alguma..."
    }
  ],
  "interpretation": "Em Filipenses 4:6-7, Paulo escreve da prisão...",
  "web_sources": null,
  "error": null
}
```

The `text` field in each reference contains the actual verse text extracted from the Bible files. The `web_sources` field is populated for responses that used web search (e.g. liturgical calendar questions).

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | — | LLM provider API key |
| `SUPABASE_DB_URL` | Yes | — | Postgres direct connection URL (`postgresql://...`) |
| `SUPABASE_URL` | Yes | — | Supabase project URL (`https://[ref].supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | — | Supabase service role key (bypasses RLS) |
| `SERPER_API_KEY` | Yes | — | Serper web search API key |
| `SEARCH_RESPONSE_MODEL` | No | `anthropic/claude-sonnet-4-20250514` | Model for the search/response agent |
| `BIBLE_DATA_DIR` | No | `.bible_data` | Root directory for Bible Markdown files |
| `MESSAGE_HISTORY_DIR` | No | `.message_history` | Directory for archived conversation history |
| `KG_PATH` | No | `src/kg/data/bible_index.json` | Path to the Bible Knowledge Graph JSON |
| `SUMMARIZATION_TRIGGER_TOKENS` | No | `90000` | Token count that triggers conversation summarization |
| `SUMMARIZATION_KEEP_MESSAGES` | No | `5` | Messages to keep after summarization |
| `STRUCTURED_OUTPUT_MAX_RETRIES` | No | `3` | Retry limit for structured output validation |
| `CORS_ORIGINS` | No | `http://localhost:3000` | Allowed origins for CORS (comma-separated) |
| `LANGSMITH_API_KEY` | No | — | Enables LangSmith tracing when set |
| `LANGSMITH_TRACING` | No | — | Set to `true` to activate tracing |
| `LANGSMITH_PROJECT` | No | — | LangSmith project name |

---

## Project structure

```
bible-copilot/
├── main.py                        # FastAPI server + SSE streaming + Supabase persistence
├── pyproject.toml
├── scripts/
│   └── download_bible_ptbr.py     # Downloads Bible files
├── skills/
│   └── generate-kg/
│       └── SKILL.md               # Instructions for regenerating the KG
└── src/
    ├── config.py                  # Shared path defaults + BibleCopilotContext
    ├── bible_copilot/
    │   ├── state.py               # GraphState, BibleResponse schema
    │   ├── tools.py               # Bible file tools + web search + conversation history tools
    │   ├── prompts.py             # Agent system prompt
    │   ├── agent_definition.py    # Agent creation + search_response_node
    │   ├── file_index.py          # Bible file listing for system prompt
    │   ├── verse_extractor.py     # Extracts verse text from Markdown files
    │   └── graph.py               # StateGraph wiring
    ├── kg/
    │   ├── context.py             # KG index for system prompt
    │   ├── tools.py               # kg_cypher_query tool (GrandCypher)
    │   └── data/                  # gitignored — generated by /generate-kg
    ├── middleware/
    │   ├── message_history.py     # Saves history to disk on summarization
    │   ├── structured_output.py   # Validates structured response schema
    │   └── __init__.py
    └── utils/
        ├── logger.py
        ├── observability.py       # LangSmith tracing setup
        ├── supabase_client.py     # Singleton Supabase admin client
        └── usage.py              # Token count + tool call extraction
```

---

## Knowledge Graph

The Bible Knowledge Graph (`src/kg/data/bible_index.json`) is the navigational core of the system. It models the Bible as three layers:

- **Book content maps** — every book has a chapter-by-chapter index: what each section covers, what themes surface, and why a reader would go there
- **Timeline** — era nodes (Creation → Patriarchs → Exodus → ... → End Times) anchor the narrative spine so the agent can answer historical and context questions
- **Thematic trails** — broad theological themes (salvation, covenant, grace) and life-situation themes (anxiety, grief, betrayal, purpose) connect to the specific books and passages where they're addressed, with aliases covering the natural-language words users actually type

The KG turns a broad search across 66 books into a targeted lookup. The agent consults it before opening any file.

### Regenerating the KG

A Claude Code skill is included to regenerate the KG from scratch if books are added, themes need refinement, or the chapter maps are too sparse:

```
/generate-kg
```

The skill is defined in `skills/generate-kg/SKILL.md`.

---

## How it works

1. **Knowledge Graph navigation** — before reading any file, the agent queries the KG to orient itself: which books cover the topic, which chapters are most relevant, what era or theme connects to the question. For life-situation queries the KG resolves natural-language terms to biblical themes via pre-built aliases.

2. **Read before answering** — for Bible questions, the agent reads the actual Markdown Bible files with `read_bible_file` for every passage it cites. It never quotes or paraphrases from memory, and may not include a passage in `biblical_references` unless it was actually read in that conversation.

3. **Web search for Church and liturgy** — for questions about the liturgical calendar, sacraments, Church documents, saints, ecumenical councils, and Catholic teaching, the agent uses Serper web search and cites sources inline with `[1]`, `[2]` notation. Bible is always the primary source; web search is used only for what isn't in Scripture.

4. **Conversation memory** — the graph uses an `AsyncPostgresSaver` (Supabase Postgres) checkpointer keyed by `thread_id`. Long conversations trigger automatic summarization; archived messages are saved to `.message_history/` and the agent can retrieve them when needed.

5. **Streaming** — the `/chat` endpoint returns Server-Sent Events. `tool_start` events are emitted as the agent works, `token` events stream the final answer, and `done` delivers the complete structured response. This allows the UI to show tool activity and stream text in real time.

6. **Persistence** — after each `/chat` call, token counts, model name, full AI response, and conversation history are written to Supabase asynchronously so they don't block the streaming response.
