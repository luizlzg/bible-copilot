import os

from supabase import Client, create_client

_client: Client | None = None


def get_supabase() -> Client:
    """Return a singleton Supabase admin client (service role — bypasses RLS)."""
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        )
    return _client
