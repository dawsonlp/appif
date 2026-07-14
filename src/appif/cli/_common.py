"""Shared helpers for the appif CLIs (Slack, Outlook).

A single Rich ``console``, ``~/.env`` loading, the ``--since`` time-window
parser/completer, a boolean styler, and a collecting listener — all previously
duplicated verbatim across the per-connector CLIs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table

from appif import config
from appif.domain.messaging.models import MessageEvent

console = Console()


def load_env() -> None:
    """Load the shared env file (see :func:`appif.config.load_env`)."""
    config.load_env()


#: (service, YAML section, token-cache glob) for the discoverability report.
_SERVICES = [
    ("gmail", "accounts", "*.json"),
    ("outlook", "accounts", "*.json"),
    ("teams", "accounts", "*.json"),
    ("slack", "accounts", None),
    ("jira", "instances", None),
]


def print_config_report() -> None:
    """Print where appif discovers configuration and what it finds.

    Shows the resolved config dir, the shared env file, and per-service
    ``config.yaml`` presence, configured account names, and token caches.
    """
    cfg_dir = config.config_dir()
    env_path = config.env_file()
    loaded = config.load_env()

    console.print("[bold]appif configuration[/bold]")
    console.print(f"  config dir: {cfg_dir} {bool_style(cfg_dir.exists())}")
    env_note = "[green]loaded[/green]" if loaded or env_path.exists() else "[dim]not found[/dim]"
    console.print(f"  env file:   {env_path} ({env_note})")
    console.print()

    table = Table(show_header=True)
    table.add_column("Service", style="bold cyan")
    table.add_column("config.yaml")
    table.add_column("Accounts")
    table.add_column("Token caches", style="dim")

    for name, section, cache_glob in _SERVICES:
        cfg_path = config.service_config_path(name)
        has_cfg = "[green]yes[/green]" if cfg_path.exists() else "[dim]—[/dim]"
        accounts = config.account_names(name, section=section)
        acc = ", ".join(accounts) if accounts else "[dim]—[/dim]"
        caches = "[dim]n/a[/dim]"
        if cache_glob:
            sdir = config.service_dir(name)
            files = sorted(p.name for p in sdir.glob(cache_glob)) if sdir.exists() else []
            caches = ", ".join(files) if files else "[dim]—[/dim]"
        table.add_row(name, has_cfg, acc, caches)

    console.print(table)


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
