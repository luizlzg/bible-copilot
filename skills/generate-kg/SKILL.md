---
name: generate-kg
description: Regenerate the Bible Knowledge Graph (src/kg/data/bible_index.json) from the downloaded Bible files. Builds book nodes with chapter maps, theme nodes, era nodes, and covers edges.
trigger: /generate-kg
allowed-tools: [Read, Write, Bash, Agent]
---

# /generate-kg

Rebuild `src/kg/data/bible_index.json` by reading the Bible Markdown files under `.bible_data/pt-br/` and producing a three-layer navigational knowledge graph.

## Usage

```
/generate-kg
```

Prerequisite: Bible files must already be downloaded. If `.bible_data/pt-br/` does not exist, tell the user to run `uv run python main.py --download-bible` first and stop.

---

## What you must do when invoked

Follow these steps in order. Do not skip any step.

---

### Step 1 — Load the authoritative theme and era vocabulary

Read `skills/generate-kg/themes_seed.json`. This file contains:
- **12 eras** — do not invent or modify them, use the `id` values exactly
- **186 themes** (57 broad + 129 life-situation) — use the `id` values exactly

You will pass the complete theme and era lists to every subagent in Step 3. Subagents must only use ids that appear in this file.

---

### Step 2 — Discover all Bible book files

Use Bash to list all `.md` files under `.bible_data/pt-br/old_testament/` and `.bible_data/pt-br/new_testament/`. The corpus is **66 books** (39 OT + 27 NT).

```bash
find .bible_data/pt-br -name "*.md" | sort
```

---

### Step 3 — Index each book with parallel subagents

**You MUST use the Agent tool here. Reading all 64 books yourself sequentially is forbidden — it is too slow and will exceed context. Dispatch all subagents in a single message.**

Split the 64 books into batches of ~10 books each (6–7 subagents). Dispatch all Agent tool calls **in one message** — this is the only way they run in parallel.

Each subagent receives:
- Its batch of book file paths
- The complete list of 185 theme ids + labels (from Step 1)
- The 12 era ids (from Step 1)
- The schema below

Each subagent prompt:

```
You are indexing a batch of Bible books to build a knowledge graph. For each book:
1. Read the full file using the Read tool
2. Produce a JSON object matching the schema below

AVAILABLE ERA IDs (use exact id only):
[paste era id: label pairs from themes_seed.json]

AVAILABLE THEME IDs (use exact id only):
[paste theme id: label (type) lines from themes_seed.json]

Book object schema:
{
  "id": "<snake_case book name, e.g. genesis, 1_samuel>",
  "label": "<book name>",
  "file_path": "<the file path you received>",
  "testament": "old" or "new",
  "genre": "law|history|poetry|prophecy|gospel|epistle|apocalyptic",
  "era": "<era_id from the list above>",
  "purpose": "<1–2 sentences: why was this book written, what does it teach?>",
  "chapter_map": [
    {
      "chapters": "<range like 1-5 or single like 12>",
      "summary": "<what happens or is taught here>",
      "themes": ["<theme_id_1>", "<theme_id_2>"]
    }
  ],
  "covers_edges": [
    {
      "theme_id": "<theme_id from the list above>",
      "relevance": "primary" or "secondary",
      "chapters": "<chapter range where this theme is most prominent>",
      "context": "<why this book matters for this theme, 1–2 sentences>"
    }
  ]
}

Rules:
- chapter_map must cover ALL chapters in the book (group related chapters together)
- covers_edges: aim for 10–25 per book. Short letters (2João, Filemom) may have fewer; major books (gospels, Paul's main letters, Salmos, Isaías, Jeremias) must have 15 or more. Be thorough — a reader should be able to find this book from any theme it genuinely addresses
- covers_edges completeness check: before finalising, scan the full theme list and ask yourself for each theme: "does this book have meaningful content on this topic?" Add a "secondary" edge if yes. Common gaps to watch for:
    - Gospels (Mateus, Marcos, Lucas, João): must include dinheiro, oracao, fe, cura, misericordia, arrependimento, salvacao, lei (or lei-related), vida_eterna, nova_alianca, amor, tentacao
    - Paul's letters (Romanos, 1Corintios, 2Corintios, Galatas, Efesios, Filipenses, Colossenses): must include fe, graca, salvacao, pecado, espirito_santo, nova_alianca, transformacao, amor, misericordia
    - Psalms: must include ansiedade, luto, oracao, louvor, adoracao, confianca, esperanca, paz, depressao, saudade_de_deus, protecao_divina, palavra_de_deus
    - OT history books (2Reis, 2Cronicas, Jeremias): must include exilio when the exile is narrated
    - Hebreus: must include sacrificio, fe, nova_alianca, lei, sacerdocio (use adoracao or lei), perseveranca
- "primary" = a reader searching this theme would specifically seek this book
- "secondary" = theme appears but is not the book's main focus
- All theme ids and era ids must be from the lists above — no exceptions
- Do NOT invent content — read the file first

Chapter grouping:
- Group by logical content unit, not fixed chapter count
- Salmos: use the five traditional books (1–41, 42–72, 73–89, 90–106, 107–150) plus notable psalms as separate entries (23, 42–43, 91, 119)
- Prophetic books: group by oracle/section
- Epistles: greeting → doctrinal sections → practical sections → closing
- Narrative books: group by major events

Return a JSON array of book objects. No explanation, no markdown fences.
```

---

### Step 4 — Merge and validate

Collect all subagent output. Each book object includes a `covers_edges` array — separate them during assembly:

```python
books_clean = []
covers_edges = []
for book in all_books:
    edges = book.pop("covers_edges", []) or []
    for edge in edges:
        covers_edges.append({
            "book_id": book["id"],
            "theme_id": edge["theme_id"],
            "relevance": edge.get("relevance", "secondary"),
            "chapters": edge.get("chapters", ""),
            "context": edge.get("context", ""),
        })
    books_clean.append(book)
```

Validate before writing:
- All `era` values in book nodes exist in the 12 era ids
- All theme ids in `chapter_map.themes` exist in the 185 theme ids
- All `theme_id` in `covers_edges` exist in the 185 theme ids
- No duplicate `id` within the books array

Remove any invalid ids rather than failing. Remove duplicate book entries, keeping the first.

Build the final structure — `themes` and `eras` come directly from `skills/generate-kg/themes_seed.json`, unchanged:

```json
{
  "books": [...],
  "themes": [...from seed...],
  "eras": [...from seed...],
  "covers_edges": [...]
}
```

---

### Step 5 — Write the output

Write the final JSON to `src/kg/data/bible_index.json` using the Write tool. Create the directory first if it does not exist (`mkdir -p src/kg/data`).

Print a summary:
```
Knowledge Graph generated:
  Books:        66
  Themes:       186  (broad: 57, life_situation: 129)
  Eras:         12
  Covers edges: XXX
```

---

## What to avoid

- **Do not read books sequentially yourself.** Use parallel subagents in Step 3 — all dispatched in one message.
- **Do not invent or modify themes or eras.** The seed file is the authoritative vocabulary.
- **Do not create passage nodes.** Chapter ranges belong as metadata on `covers_edge.chapters`.
- **Do not skip the `context` field** on edges — it tells the agent *why* to go to that book.
- **Do not invent chapter content.** Read the file first, then write the `chapter_map`.
- **Do not write generic context.** "Salmos: ansiedade" is noise. "Salmos 42-43: o salmista luta contra a depressão, dialogando com sua própria alma abatida, concluindo com esperança em Deus" is signal.
