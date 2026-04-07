from langgraph.graph import StateGraph, START, END

from src.bible_copilot.agent_definition import search_response_node
from src.bible_copilot.state import GraphState
from src.config import BibleCopilotContext


def build_graph(checkpointer=None):
    builder = StateGraph(GraphState, context_schema=BibleCopilotContext)

    builder.add_node("search_response", search_response_node)

    builder.add_edge(START, "search_response")
    builder.add_edge("search_response", END)

    return builder.compile(checkpointer=checkpointer)
