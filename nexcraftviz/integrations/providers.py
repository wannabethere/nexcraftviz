"""Concrete ``LLMRunner`` implementations.

The core of this package never imports a provider SDK, and nothing here is
required to use it — a host that already has a model passes its own runner, and
the signature is a plain one,
``(system, user, schema) -> (payload, meta)``, so an existing runner drops
straight in.

This module exists for the cases where nexcraftviz owns the model: the hosted
API, the CLI, and the live evals.

Configuration uses the common OpenAI variable names, so there is often nothing
new to set:

===================  ==========================================================
``OPENAI_API_KEY``   the key
``OPENAI_MODEL``     model id, default ``gpt-5-mini``
===================  ==========================================================
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from nexcraftviz.integrations.strict_schema import drop_nulls, to_strict_schema

#: A capable, inexpensive default; set OPENAI_MODEL to change it.
DEFAULT_OPENAI_MODEL = "gpt-5-mini"

#: Model families that reject a custom ``temperature``. Sending one is a hard
#: 400, not a warning — taken from the provider's documentation, not guessed.
FIXED_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")


class ProviderError(RuntimeError):
    """Raised when a provider cannot be reached or configured."""


def openai_runner(
    *,
    model: str | None = None,
    api_key: str | None = None,
    client: Any = None,
    temperature: float | None = None,
    max_retries: int = 1,
) -> Any:
    """Build an :data:`~nexcraftviz.skills.base.LLMRunner` backed by OpenAI.

    Structured output is requested three ways, most reliable first, because
    support varies by model and a hard failure here is indistinguishable from
    a bad prompt:

    1. ``json_schema`` with ``strict: true`` — the schema is enforced.
    2. ``json_schema`` without strict — enforced loosely.
    3. ``json_object`` with the schema pasted into the system text — the
       fallback that works anywhere.

    The chosen mode is reported in the call metadata, so a surprising result
    can be traced to the binding rather than blamed on the prompt.
    """
    resolved_model = model or os.getenv("OPENAI_MODEL", "").strip() or DEFAULT_OPENAI_MODEL

    if client is None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderError(
                "the OpenAI runner needs the `openai` extra: "
                "pip install 'nexcraftviz[openai]'"
            ) from exc
        key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise ProviderError(
                "no OPENAI_API_KEY set. Either export one, or use propose()/commit() "
                "and run your own model."
            )
        client = AsyncOpenAI(api_key=key)

    supports_temperature = not resolved_model.startswith(FIXED_TEMPERATURE_PREFIXES)

    async def run(
        system: str, user: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.monotonic()
        strict = to_strict_schema(schema)
        name = str(schema.get("title") or "Response").replace(" ", "")

        attempts: list[tuple[str, dict[str, Any], str]] = [
            ("json_schema_strict",
             {"type": "json_schema",
              "json_schema": {"name": name, "schema": strict, "strict": True}},
             system),
            ("json_schema",
             {"type": "json_schema",
              "json_schema": {"name": name, "schema": strict, "strict": False}},
             system),
            ("json_object",
             {"type": "json_object"},
             f"{system}\n\n### OUTPUT SCHEMA ###\n\nReturn JSON matching:\n"
             f"{json.dumps(schema, indent=2)}"),
        ]

        last_error: Exception | None = None
        for mode, response_format, system_text in attempts:
            for attempt in range(max_retries + 1):
                try:
                    kwargs: dict[str, Any] = {
                        "model": resolved_model,
                        "messages": [
                            {"role": "system", "content": system_text},
                            {"role": "user", "content": user},
                        ],
                        "response_format": response_format,
                    }
                    if supports_temperature and temperature is not None:
                        kwargs["temperature"] = temperature

                    response = await client.chat.completions.create(**kwargs)
                    payload = _decode(response.choices[0].message.content)
                    usage = getattr(response, "usage", None)
                    return payload, {
                        "model": resolved_model,
                        "structured_output": mode,
                        "wall_ms": int((time.monotonic() - started) * 1000),
                        "retry_count": attempt,
                        "tokens_in": int(getattr(usage, "prompt_tokens", 0) or 0),
                        "tokens_out": int(getattr(usage, "completion_tokens", 0) or 0),
                    }
                except Exception as exc:  # noqa: BLE001 — provider errors vary widely
                    last_error = exc
                    if _is_schema_rejection(exc):
                        break  # try the next binding rather than retrying this one
                    if attempt >= max_retries:
                        break

        raise ProviderError(f"OpenAI call failed ({resolved_model}): {last_error}")

    return run


def default_runner(**kwargs: Any) -> Any:
    """The runner for the configured provider, or None when none is configured.

    Returns None rather than raising so a server can start without a key and
    report ``hosted_mode: false`` honestly.
    """
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None
    try:
        return openai_runner(**kwargs)
    except ProviderError:
        return None


def _decode(content: str | None) -> dict[str, Any]:
    """Parse the model's JSON, stripping the nulls strict mode forces on it."""
    if not content:
        raise ProviderError("the model returned an empty response")
    text = content.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderError(f"the model's output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"expected an object, got {type(parsed).__name__}")
    return drop_nulls(parsed)


def _is_schema_rejection(exc: Exception) -> bool:
    """Whether the provider refused the *binding* rather than the request.

    Retrying a rejected schema just burns the retry budget; falling back to a
    looser binding is the useful move.
    """
    message = str(exc).lower()
    return any(
        hint in message
        for hint in (
            "response_format", "json_schema", "not supported", "unsupported",
            "invalid schema", "strict",
        )
    )
