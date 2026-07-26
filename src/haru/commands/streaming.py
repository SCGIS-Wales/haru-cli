"""Shared streaming helpers for the chat and run commands."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from haru.models.errors import translate_bedrock_error

if TYPE_CHECKING:
    from rich.console import Console

    from haru.models.errors import BedrockContext

_GUARDRAIL_STOPS = frozenset({"guardrail_intervened", "content_filtered"})


def collect_response(
    agent: Any,
    prompt: str,
    on_text: Callable[[str], None],
    *,
    context: BedrockContext | None = None,
) -> Any:
    """Stream a response, forwarding text chunks to ``on_text``.

    Returns the final AgentResult (or None if the stream produced no result
    event). Delegates the agent loop entirely to Strands, converting Bedrock
    permission and configuration failures into actionable haru errors.
    """

    async def _consume() -> Any:
        result: Any = None
        async for event in agent.stream_async(prompt):
            if "data" in event:
                on_text(event["data"])
            if "result" in event:
                result = event["result"]
        return result

    try:
        return asyncio.run(_consume())
    except Exception as exc:
        translated = translate_bedrock_error(exc, context)
        if translated is not None:
            raise translated from exc
        raise


def surface_guardrail(result: Any, console: Console) -> None:
    """Print a warning when the response was blocked or redacted by guardrails."""
    if result is not None and getattr(result, "stop_reason", None) in _GUARDRAIL_STOPS:
        console.print("[bold yellow]Guardrail intervened: the response was blocked or redacted.[/]")


def build_bedrock_context(config: Any, agent_name: str | None) -> BedrockContext | None:
    """Describe the model haru is about to call, for error messages.

    Uses configuration only - it makes no AWS calls.
    """
    from haru.auth.identity import effective_identity
    from haru.models.bedrock import get_model_config, resolve_model_id
    from haru.models.errors import BedrockContext

    if config.models is None:
        return None
    model_key = None
    if agent_name is not None and config.agents is not None:
        agent_cfg = config.agents.agents.get(agent_name)
        model_key = agent_cfg.model if agent_cfg is not None else None
    try:
        entry = get_model_config(config, model_key)
    except Exception:
        return None
    account_id, _, role_name, _ = effective_identity(config.auth.sso)
    return BedrockContext(
        model_id=resolve_model_id(entry.model_id),
        region=entry.region,
        account_id=account_id,
        role_name=role_name,
        guardrail_id=config.guardrails.guardrail_id if config.guardrails else None,
    )
