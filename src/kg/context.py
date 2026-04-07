"""
Builds the KG index injected into the agent's system prompt.
"""

import json

_KG_CACHE: dict | None = None


def _load_kg(kg_path: str) -> dict:
    global _KG_CACHE
    if _KG_CACHE is None:
        with open(kg_path, encoding="utf-8") as f:
            _KG_CACHE = json.load(f)
    return _KG_CACHE


def build_kg_index(kg_path: str) -> str:
    """
    Returns a compact schema + node catalogue from the KG, injected into the
    system prompt so the agent can write accurate Cypher queries.
    """
    _load_kg(kg_path)
    lines: list[str] = []

    lines.append("### Schema")
    lines.append("")
    lines.append("Node labels and properties:")
    lines.append("  Book  {id, label, label_lower, testament (old|new), genre, era, purpose, file_path, chapter_map (JSON)}")
    lines.append("  Theme {id, label, label_lower, type (broad|life_situation), description, aliases (comma-sep lowercase string)}")
    lines.append("  Era   {id, label, era_order (int), description, approximate_period}")
    lines.append("")
    lines.append("Relationships:")
    lines.append("  (Book)-[:COVERS {chapters, relevance, context}]->(Theme)")
    lines.append("  (Book)-[:BELONGS_TO]->(Era)")
    lines.append("  (Theme)-[:RELATED_TO]->(Theme)  [stored bidirectionally]")
    lines.append("  (Era)-[:CONTINUES]->(Era)        [ordered by era_order]")
    lines.append("")

    return "\n".join(lines)
