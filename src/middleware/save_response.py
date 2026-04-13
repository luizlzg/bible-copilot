"""
SaveResponseValidatorMiddleware — validates that the agent called
save_biblical_response whenever it read Bible passages.
"""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from src.middleware.structured_output import StructuredOutputValidationError
from src.utils.logger import LOGGER

_SOURCE_TOOLS = {"read_bible_file", "search_bible_text", "search_web"}


class SaveResponseValidatorMiddleware(AgentMiddleware):
    """
    Post-agent hook that enforces:
    - If the agent used read_bible_file, search_bible_text, or search_web,
      it must also have called save_biblical_response before finishing.
    - If no sources were consulted (follow-up, clarification), the save tool
      is optional.
    """

    def after_agent(self, state: dict) -> dict:
        if state.get("invalid_input", False):
            return state

        messages = state.get("messages", [])

        tools_called: set[str] = set()
        for m in messages:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    tools_called.add(tc.get("name", ""))

        sources_used = bool(tools_called & _SOURCE_TOOLS)
        save_called = "save_biblical_response" in tools_called

        LOGGER.info(
            f"SaveResponseValidatorMiddleware: sources_used={sources_used} "
            f"save_called={save_called} tools={tools_called}"
        )

        if sources_used and not save_called:
            raise StructuredOutputValidationError(
                "Agent used source tools but did not call save_biblical_response",
                error_feedback_message=(
                    "Você consultou fontes (Bíblia ou web) mas não chamou save_biblical_response. "
                    "ANTES de escrever sua resposta final, chame save_biblical_response "
                    "com todas as referências bíblicas, interpretação e fontes web utilizadas."
                ),
                messages=messages,
                state=state,
            )

        return state
