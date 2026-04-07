# Bible Copilot

A conversational Bible assistant served as a REST API. Built with LangGraph, LangChain, and OpenRouter.

Supports the full range of how people engage with the Bible:
- **Life situations and emotions** — grief, anxiety, forgiveness, purpose, fear, betrayal
- **Topical and doctrinal study** — what the Bible says about marriage, money, suffering, prayer
- **Book and chapter comprehension** — summaries, themes, narrative context
- **Historical and timeline questions** — the exile, the early church, the patriarchs
- **Character studies** — David, Ruth, Paul, the prophets
- **Reading guidance** — where to start, what to read next

The agent navigates a Knowledge Graph before opening any file, reads the actual Bible text for every passage it cites, and never quotes from memory.

---

## Stack

- **LangGraph** — stateful multi-turn agent graph with `MemorySaver` checkpointing
- **LangChain** — agent creation, middleware (summarization, message history, structured output validation)
- **OpenRouter** — LLM provider (`ChatOpenAI` pointed at `openrouter.ai/api/v1`)
- **FastAPI** — REST API server
- **GrandCypher + NetworkX** — in-memory Cypher queries over the Bible Knowledge Graph

---

## Setup

**1. Install dependencies**

```bash
uv sync
```

**2. Configure environment**

```bash
cp .env.example .env
# edit .env and set OPENROUTER_API_KEY
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

Sends a message in an existing conversation.

**Request**
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Estou com ansiedade, o que a Bíblia diz sobre isso?"
}
```

**Response**
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
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
  "error": null
}
```

The `text` field in each reference contains the actual verse text extracted from the Bible files — the frontend does not need to make a second request for it.

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | — | LLM provider API key |
| `SEARCH_RESPONSE_MODEL` | No | `anthropic/claude-sonnet-4-20250514` | Model for the search/response agent |
| `BIBLE_DATA_DIR` | No | `.bible_data` | Root directory for Bible Markdown files |
| `MESSAGE_HISTORY_DIR` | No | `.message_history` | Directory for archived conversation history |
| `KG_PATH` | No | `src/kg/data/bible_index.json` | Path to the Bible Knowledge Graph JSON |
| `SUMMARIZATION_TRIGGER_TOKENS` | No | `90000` | Token count that triggers conversation summarization |
| `SUMMARIZATION_KEEP_MESSAGES` | No | `5` | Messages to keep after summarization |
| `STRUCTURED_OUTPUT_MAX_RETRIES` | No | `3` | Retry limit for structured output validation |
| `LANGSMITH_API_KEY` | No | — | Enables LangSmith tracing when set |
| `LANGSMITH_TRACING` | No | — | Set to `true` to activate tracing |
| `LANGSMITH_PROJECT` | No | — | LangSmith project name |

---

## Project structure

```
bible-copilot/
├── main.py                        # FastAPI server
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
    │   ├── tools.py               # Bible file tools + conversation history tools
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
        └── observability.py       # LangSmith tracing setup
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

The skill is defined in `skills/generate-kg/SKILL.md`. It dispatches parallel subagents to read and index all 66 books, build the theme vocabulary, assign era nodes, and produce the `covers_edges` that connect books to themes with context.

---

## How it works

1. **Knowledge Graph navigation** — before reading any file, the agent queries the KG to orient itself: which books cover the topic, which chapters are most relevant, what era or theme connects to the question. For life-situation queries the KG resolves natural-language terms to biblical themes via pre-built aliases. For book or character queries it provides structure and context before any file is opened.

2. **Read before answering** — the agent reads the actual Markdown Bible files for every passage it cites. It never quotes or paraphrases from memory. Interpretations are always traceable to the text retrieved in that conversation.

3. **Conversation memory** — the graph uses a `MemorySaver` checkpointer keyed by `thread_id`. Long conversations trigger automatic summarization; archived messages are saved to `.message_history/` and the agent can retrieve them via tools when the user references something that was discussed earlier.

4. **Structured output** — every response is validated against the `BibleResponse` schema (message, references with coordinates, interpretation). If validation fails the agent retries with feedback up to `STRUCTURED_OUTPUT_MAX_RETRIES` times.
