import os


def setup_langsmith_tracing() -> None:
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        return

    tracing_enabled = os.getenv("LANGSMITH_TRACING", "").lower() in ("true", "1", "yes")
    if not tracing_enabled:
        return

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "bible-copilot")
    os.environ["LANGCHAIN_ENDPOINT"] = os.getenv(
        "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
    )
