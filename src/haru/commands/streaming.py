"""Shared streaming helpers for the chat and run commands."""

import asyncio
from collections.abc import Callable
from typing import Any

from rich.console import Console

_GUARDRAIL_STOPS = frozenset({"guardrail_intervened", "content_filtered"})


def collect_response(agent: Any, prompt: str, on_text: Callable[[str], None]) -> Any:
    """Stream a response, forwarding text chunks to ``on_text``.

    Returns the final AgentResult (or None if the stream produced no result
    event). Delegates the agent loop entirely to Strands.
    """

    async def _consume() -> Any:
        result: Any = None
        async for event in agent.stream_async(prompt):
            if "data" in event:
                on_text(event["data"])
            if "result" in event:
                result = event["result"]
        return result

    return asyncio.run(_consume())


def surface_guardrail(result: Any, console: Console) -> None:
    """Print a warning when the response was blocked or redacted by guardrails."""
    if result is not None and getattr(result, "stop_reason", None) in _GUARDRAIL_STOPS:
        console.print("[bold yellow]Guardrail intervened: the response was blocked or redacted.[/]")
