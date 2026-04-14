"""OpenRouter model pricing — fetched once per process and cached."""

import os
from typing import Any

import requests

from src.utils.logger import LOGGER

_pricing_cache: dict[str, dict[str, float]] = {}


def _fetch_model_pricing(model_id: str) -> dict[str, float]:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    try:
        res = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        model = next((m for m in data["data"] if m["id"] == model_id), None)
        if not model:
            LOGGER.warning(f"[pricing] Model '{model_id}' not found in OpenRouter models list.")
            return {}
        pricing = model.get("pricing", {})
        return {
            "input":         float(pricing.get("prompt", 0) or 0),
            "output":        float(pricing.get("completion", 0) or 0),
            "cache_read":    float(pricing.get("input_cache_read", 0) or 0),
            "cache_write":   float(pricing.get("input_cache_write", 0) or 0),
        }
    except Exception as e:
        LOGGER.warning(f"[pricing] Failed to fetch pricing for '{model_id}': {e}")
        return {}


def get_model_pricing(model_id: str) -> dict[str, float]:
    """Return cached per-token prices for the given model (fetched once per process)."""
    if model_id not in _pricing_cache:
        _pricing_cache[model_id] = _fetch_model_pricing(model_id)
    return _pricing_cache[model_id]


def compute_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict[str, Any]:
    """
    Compute cost in USD for a single turn.
    Returns a dict with input_cost, output_cost, cache_read_cost, cache_write_cost, total_cost.
    All values are None if pricing is unavailable.
    """
    pricing = get_model_pricing(model_id)
    if not pricing:
        return {
            "input_cost": None, "output_cost": None,
            "cache_read_cost": None, "cache_write_cost": None, "total_cost": None,
        }

    input_cost       = (input_tokens or 0) * pricing["input"]
    output_cost      = (output_tokens or 0) * pricing["output"]
    cache_read_cost  = (cache_read_tokens or 0) * pricing["cache_read"]
    cache_write_cost = (cache_creation_tokens or 0) * pricing["cache_write"]
    total_cost       = input_cost + output_cost + cache_read_cost + cache_write_cost

    return {
        "input_cost":       round(input_cost, 8),
        "output_cost":      round(output_cost, 8),
        "cache_read_cost":  round(cache_read_cost, 8),
        "cache_write_cost": round(cache_write_cost, 8),
        "total_cost":       round(total_cost, 8),
    }
