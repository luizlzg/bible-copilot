"""
Structured Output Validator Middleware for LangChain 1.0

Validates structured outputs from agents and raises errors for retry
when validation fails.
"""

import os
from typing import Any, Callable, Dict, Optional

from langchain.agents.middleware import AgentMiddleware

from src.utils.logger import LOGGER


class StructuredOutputValidationError(Exception):
    """Exception raised when structured output validation fails."""

    def __init__(
        self,
        message: str,
        error_feedback_message: str,
        messages: list,
        state: Dict[str, Any],
    ) -> None:
        super().__init__(message)
        self.error_feedback_message = error_feedback_message
        self.messages = messages
        self.state = state


class StructuredOutputValidatorMiddleware(AgentMiddleware):
    """
    Generic post-agent validator that validates the structured output against
    a schema and an optional custom validator function.

    ToolStrategy puts the agent's structured output in state['structured_response'].

    Parameters:
        expected_schema: A TypedDict or Pydantic class to validate against.
        validator_func: Optional callable (output, state) -> (is_valid, error_msg).
            Called after default validation passes. Use for domain-specific rules.
    """

    def __init__(
        self,
        expected_schema: Any,
        validator_func: Optional[Callable[[Dict[str, Any], Dict[str, Any]], tuple[bool, str]]] = None,
    ) -> None:
        self.expected_schema = expected_schema
        self.validator_func = validator_func or self._default_validator
        self.max_retries = int(os.getenv("STRUCTURED_OUTPUT_MAX_RETRIES", "3"))
        LOGGER.info(
            f"Initialized StructuredOutputValidatorMiddleware (max_retries={self.max_retries} at agent level)"
        )

    def _default_validator(
        self, output: Dict[str, Any], state: Dict[str, Any] = None
    ) -> tuple[bool, str]:
        """Default validation — checks required keys are present and non-empty."""
        if not isinstance(output, dict):
            return False, f"Output is not a dict, got {type(output).__name__}"

        missing_keys = []
        for key in self.expected_schema.__annotations__.keys():
            if key not in output:
                missing_keys.append(key)

        if missing_keys:
            return (
                False,
                f"Missing required fields: {', '.join(missing_keys)}. "
                f"Expected schema: {list(self.expected_schema.__annotations__.keys())}",
            )

        empty_fields = []
        for key, value in output.items():
            if key in self.expected_schema.__annotations__:
                if isinstance(value, (list, str)) and not value:
                    empty_fields.append(key)

        if empty_fields:
            return (
                False,
                f"Required fields are empty: {', '.join(empty_fields)}. "
                f"Please provide values for all required fields.",
            )

        return True, ""

    def after_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Hook that runs after agent completes — validates structured output.

        ToolStrategy puts the output in state['structured_response'].
        """
        LOGGER.info("Running StructuredOutputValidatorMiddleware.after_agent")

        if state.get("invalid_input", False):
            LOGGER.info("Skipping structured output validation - input marked as invalid")
            return state

        structured_output = state.get("structured_response")
        if not structured_output:
            LOGGER.warning("No structured_response found in state")
            messages = state.get("messages", [])

            raise StructuredOutputValidationError(
                "No structured_response found in state",
                "You have NOT generated the required structured output yet. "
                "Please generate the structured response now.",
                messages,
                state,
            )

        is_valid, error_message = self.validator_func(structured_output, state)

        if is_valid:
            LOGGER.info("Structured output validation passed")
            return state

        LOGGER.warning(f"Structured output validation failed: {error_message}")
        messages = state.get("messages", [])

        raise StructuredOutputValidationError(
            f"Structured output validation failed: {error_message}",
            f"The previous response was not in the correct format.\n\n"
            f"Error: {error_message}\n\n"
            f"Please provide the response again in the correct structured format.",
            messages,
            state,
        )
