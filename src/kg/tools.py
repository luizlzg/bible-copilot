"""
Cypher query tool for the Bible Knowledge Graph, backed by GrandCypher + NetworkX.
"""

import json
import os

import networkx as nx
from grandcypher import GrandCypher
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import Command

from src.config import BibleCopilotContext

_GRAPH: nx.MultiDiGraph | None = None


def _get_graph(kg_path: str) -> nx.MultiDiGraph:
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH

    with open(kg_path, encoding="utf-8") as f:
        kg = json.load(f)

    G = nx.MultiDiGraph()

    for book in kg["books"]:
        G.add_node(
            book["id"],
            __labels__={"Book"},
            id=book["id"],
            label=book.get("label", ""),
            label_lower=book.get("label", "").lower(),
            testament=book.get("testament", ""),
            genre=book.get("genre", ""),
            era=book.get("era", ""),
            purpose=book.get("purpose", ""),
            file_path=book.get("file_path", ""),
            chapter_map=json.dumps(book.get("chapter_map", []), ensure_ascii=False),
        )

    for theme in kg["themes"]:
        G.add_node(
            theme["id"],
            __labels__={"Theme"},
            id=theme["id"],
            label=theme.get("label", ""),
            label_lower=theme.get("label", "").lower(),
            type=theme.get("type", ""),
            description=theme.get("description", ""),
            aliases=", ".join(theme.get("aliases", [])),
        )

    for era in kg["eras"]:
        G.add_node(
            era["id"],
            __labels__={"Era"},
            id=era["id"],
            label=era.get("label", ""),
            era_order=era.get("order", 0),
            description=era.get("description", ""),
            approximate_period=era.get("approximate_period", ""),
        )

    for edge in kg.get("covers_edges", []):
        G.add_edge(
            edge["book_id"], edge["theme_id"],
            __labels__={"COVERS"},
            chapters=edge.get("chapters", ""),
            relevance=edge.get("relevance", ""),
            context=edge.get("context", ""),
        )

    seen: set[tuple] = set()
    for theme in kg["themes"]:
        for rel_id in theme.get("related_themes", []):
            if rel_id in G and theme["id"] in G:
                key = tuple(sorted([theme["id"], rel_id]))
                if key not in seen:
                    seen.add(key)
                    G.add_edge(theme["id"], rel_id, __labels__={"RELATED_TO"})
                    G.add_edge(rel_id, theme["id"], __labels__={"RELATED_TO"})

    era_ids = {e["id"] for e in kg["eras"]}
    for book in kg["books"]:
        if book.get("era") in era_ids:
            G.add_edge(book["id"], book["era"], __labels__={"BELONGS_TO"})

    sorted_eras = sorted(kg["eras"], key=lambda e: e.get("order", 0))
    for i in range(len(sorted_eras) - 1):
        G.add_edge(sorted_eras[i]["id"], sorted_eras[i + 1]["id"], __labels__={"CONTINUES"})

    _GRAPH = G
    return _GRAPH  # type: ignore[return-value]


def _format_results(raw: dict) -> list[dict]:
    if not raw:
        return []
    # GrandCypher may return Token objects as keys — normalise to strings
    str_keys = {k: str(k) for k in raw}
    n = len(raw[next(iter(raw))])
    rows = []
    for i in range(n):
        row = {}
        for k in raw:
            val = raw[k][i]
            if isinstance(val, dict):
                # MultiDiGraph full-edge data: {0: {attrs}, 1: {attrs}, ...} — flatten
                if val and all(isinstance(ek, int) for ek in val):
                    merged: dict = {}
                    for edge_attrs in val.values():
                        merged.update({ek: ev for ek, ev in edge_attrs.items()
                                       if isinstance(ek, str) and not ek.startswith("__")})
                    val = merged
                # Relationship property access: {(edge_key, rel_type): value} — unwrap
                elif val and all(isinstance(ek, tuple) for ek in val):
                    values = list(val.values())
                    val = values[0] if len(values) == 1 else values
                else:
                    val = {ek: ev for ek, ev in val.items()
                           if not (isinstance(ek, str) and ek.startswith("__"))}
            row[str_keys[k]] = val
        rows.append(row)
    return rows


@tool
def kg_cypher_query(query: str, runtime: ToolRuntime[BibleCopilotContext]) -> Command:
    """
    Execute a Cypher query against the Bible Knowledge Graph.
    The graph schema and available node/relationship IDs are described in the
    system prompt — consult them to write accurate queries.

    Syntax notes:
    - Use double quotes for strings: WHERE n.id = "genesis"
    - CONTAINS is case-sensitive; use label_lower or aliases for keyword search
    - OR conditions are supported: WHERE n.id = "a" OR n.id = "b"
    - Unsupported: DISTINCT, ORDER BY, node property projection t {id, label}

    Examples:
      MATCH (n:Book) WHERE n.testament = "old" RETURN n
      MATCH (n:Book) WHERE n.label_lower CONTAINS "samuel" RETURN n
      MATCH (b:Book)-[e:COVERS]->(t:Theme) WHERE b.id = "genesis" RETURN t, e
      MATCH (b:Book)-[:BELONGS_TO]->(era:Era) WHERE era.id = "exile" RETURN b
      MATCH (t:Theme)-[:RELATED_TO]->(r:Theme) WHERE t.id = "fe" RETURN r
      MATCH (b:Book) WHERE b.genre = "prophecy" AND b.testament = "old" RETURN b
    """
    try:
        import re as _re
        # Replace single quotes with double quotes
        clean_query = query.replace("'", '"')
        # Strip DISTINCT — not supported
        clean_query = _re.sub(r'\bDISTINCT\s+', '', clean_query, flags=_re.IGNORECASE)
        # Strip ORDER BY — not supported
        clean_query = _re.sub(r'\bORDER\s+BY\b.*?(?=\bLIMIT\b|$)', '', clean_query,
                              flags=_re.IGNORECASE | _re.DOTALL).strip()
        # Convert node property projection  t {a, b, c}  →  t
        clean_query = _re.sub(r'(\b\w+)\s*\{[^}]*\}', r'\1', clean_query)
        # GrandCypher is stateful — a fresh instance is required per query
        results = _format_results(GrandCypher(_get_graph(runtime.context.kg_path)).run(clean_query))
        content = json.dumps(results, ensure_ascii=False, indent=2) if results else "No results."
    except Exception as e:
        content = f"Query error: {e}"

    return Command(update={
        "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id)],
    })


KG_TOOLS = [kg_cypher_query]
