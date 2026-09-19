"""Нормализация billing usage от разных LLM-провайдеров (OpenAI-compat, DeepSeek, …)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _dump_usage(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json", exclude_none=False)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    return {"repr": repr(obj)}


def extract_token_counts_and_extras(
    usage: Any,
) -> Tuple[int, int, int, Dict[str, Any], Optional[int], Optional[int]]:
    """
    (prompt_tokens, completion_tokens, total_tokens, raw_usage_dict,
     cached_input_tokens | None, reasoning_output_tokens | None).
    """
    raw = _dump_usage(usage)
    if usage is None and not raw:
        return 0, 0, 0, {}, None, None

    pt = getattr(usage, "prompt_tokens", None)
    ct = getattr(usage, "completion_tokens", None)
    tt = getattr(usage, "total_tokens", None)

    if pt is None and isinstance(raw.get("prompt_tokens"), int):
        pt = raw["prompt_tokens"]
    if ct is None and isinstance(raw.get("completion_tokens"), int):
        ct = raw["completion_tokens"]
    if tt is None and isinstance(raw.get("total_tokens"), int):
        tt = raw["total_tokens"]

    pin = getattr(usage, "input_tokens", None)
    cout = getattr(usage, "output_tokens", None)
    if pt is None and isinstance(pin, int):
        pt = pin
    if ct is None and isinstance(cout, int):
        ct = cout

    pt_i = int(pt or 0)
    ct_i = int(ct or 0)
    tt_i = int(tt if tt is not None else pt_i + ct_i)

    cached_in: Optional[int] = None
    reasoning_out: Optional[int] = None

    ptd = getattr(usage, "prompt_tokens_details", None) or raw.get(
        "prompt_tokens_details"
    )
    if ptd:
        try:
            c = getattr(ptd, "cached_tokens", None)
            if c is None and isinstance(ptd, dict):
                c = ptd.get("cached_tokens")
            if c is not None:
                cached_in = int(c)
        except (TypeError, ValueError):
            pass

    ctd = getattr(usage, "completion_tokens_details", None) or raw.get(
        "completion_tokens_details"
    )
    if ctd:
        try:
            r = getattr(ctd, "reasoning_tokens", None)
            if r is None and isinstance(ctd, dict):
                r = ctd.get("reasoning_tokens")
            if r is not None:
                reasoning_out = int(r)
        except (TypeError, ValueError):
            pass

    return pt_i, ct_i, tt_i, raw, cached_in, reasoning_out
