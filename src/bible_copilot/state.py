import json
from typing import Any, Optional, Union
from langchain.agents import AgentState
from typing_extensions import TypedDict


class BiblePassage(TypedDict):
    book: str
    chapter: int
    verse_start: int
    verse_end: int


class BibleResponse(TypedDict):
    message: str
    biblical_references: Optional[Union[list[BiblePassage], str]]
    interpretation: Optional[str]


class GraphState(AgentState):
    invalid_input: bool
    error_message: str
    bible_response: Optional[BibleResponse]


def coerce_bible_response(response: Any) -> BibleResponse:
    """Coerce biblical_references from JSON string to list if needed."""
    if not isinstance(response, dict):
        return response
    refs = response.get("biblical_references")
    if isinstance(refs, str):
        try:
            response = dict(response)
            response["biblical_references"] = json.loads(refs)
        except (json.JSONDecodeError, ValueError):
            response["biblical_references"] = None
    return response
