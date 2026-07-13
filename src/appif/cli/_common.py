"""Shared helpers for the appif CLIs (Slack, Outlook).

A single Rich ``console``, ``~/.env`` loading, the ``--since`` time-window
parser/completer, a boolean styler, and a collecting listener — all previously
duplicated verbatim across the per-connector CLIs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

from appif.domain.messaging.models import MessageEvent

console = Console()


def load_env() -> None:
    """Load ``~/.env`` if present."""
    env_path = Path.home() / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def bool_style(value: bool) -> str:
    """Render a boolean as coloured yes/no for Rich output."""
    return "[green]yes[/green]" if value else "[red]no[/red]"


TIME_PRESETS = ["5m", "15m", "1h", "4h", "1d", "7d"]
_TIME_PATTERN = re.compile(r"^(\d+)([mhd])$")


def parse_since(since: str) -> datetime:
    """Parse a time string like '1h', '30m', '7d' into a UTC datetime."""
    match = _TIME_PATTERN.match(since)
    if not match:
        console.print(f"[red]Invalid time format:[/red] {since}\nExpected: 5m, 15m, 1h, 4h, 1d, 7d")
        raise typer.Exit(1)
    value, unit = int(match.group(1)), match.group(2)
    delta = {"m": timedelta(minutes=value), "h": timedelta(hours=value), "d": timedelta(days=value)}[unit]
    return datetime.now(UTC) - delta


def complete_since(incomplete: str) -> list[str]:
    """Tab-completion callback for the ``--since`` option."""
    return [p for p in TIME_PRESETS if p.startswith(incomplete)]


class CollectorListener:
    """MessageListener that collects events into a list."""

    def __init__(self) -> None:
        self.events: list[MessageEvent] = []

    def on_message(self, event: MessageEvent) -> None:
        self.events.append(event)
