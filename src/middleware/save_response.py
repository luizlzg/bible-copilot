"""
SaveResponseValidatorMiddleware — validates that the agent called
save_biblical_response whenever it read Bible passages.
"""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from src.middleware.structured_output import StructuredOutputValidationError
from src.utils.logger import LOGGER

_BIBLE_READ_TOOLS = {"read_bible_file", "search_bible_text"}


class SaveResponseValidatorMiddleware(AgentMiddleware):
    """
    Post-agent hook that enforces:
    - If the agent called read_bible_file or search_bible_text, it must also
      have called save_biblical_response before finishing.
    - If no Bible reading was done (follow-up, clarification), the save tool
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

        bible_was_read = bool(tools_called & _BIBLE_READ_TOOLS)
        save_called = "save_biblical_response" in tools_called

        LOGGER.info(
            f"SaveResponseValidatorMiddleware: bible_read={bible_was_read} "
            f"save_called={save_called} tools={tools_called}"
        )

        if bible_was_read and not save_called:
            raise StructuredOutputValidationError(
                "Agent read Bible passages but did not call save_biblical_response",
                error_feedback_message=(
                    "Você leu passagens bíblicas mas não chamou save_biblical_response. "
                    "ANTES de escrever sua resposta final, chame save_biblical_response "
                    "com todas as referências que você citou e a interpretação exegética."
                ),
                messages=messages,
                state=state,
            )

        return state
