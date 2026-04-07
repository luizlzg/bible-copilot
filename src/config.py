"""
Shared runtime configuration defaults and context schema.

BIBLE_DATA_DIR and MESSAGE_HISTORY_DIR are read from environment variables at
startup and passed as LangGraph runtime context (graph.invoke(..., context=...)).
Tools and nodes access them via runtime.context — no fallbacks at that layer.
"""

import os
from pydantic.dataclasses import dataclass

BIBLE_DATA_DIR = os.getenv("BIBLE_DATA_DIR", ".bible_data")
MESSAGE_HISTORY_DIR = os.getenv("MESSAGE_HISTORY_DIR", ".message_history")
KG_PATH = os.getenv(
    "KG_PATH",
    os.path.join(os.path.dirname(__file__), "kg", "data", "bible_index.json"),
)
LANGUAGE = "pt-br"


@dataclass
class BibleCopilotContext:
    bible_data_dir: str
    message_history_dir: str
    kg_path: str
