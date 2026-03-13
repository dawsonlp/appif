"""Slack CLI — identity-first command structure.

Usage::

    appif-slack bot status
    appif-slack bot channels
    appif-slack bot listen
    appif-slack bot messages --since 1h
    appif-slack bot send #general "Deploy complete"
    appif-slack user channels
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from appif.adapters.slack.connector import SlackConnector
from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TargetUnavailable, TransientFailure
from appif.domain.messaging.models import (
    BackfillScope,
    ConversationRef,
    MessageContent,
    MessageEvent,
)

logger = logging.getLogger(__name__)
console = Console()

# ---------------------------------------------------------------------------
# Token resolution (UX-1, Environment Configuration)
# ---------------------------------------------------------------------------

_TOKEN_MAP = {
    "bot": "APPIF_SLACK_BOT_OAUTH_TOKEN",
    "user": "APPIF_SLACK_USER_OAUTH_TOKEN",
}
_APP_TOKEN_VAR = "APPIF_SLACK_BOT_APP_LEVEL_TOKEN"


def _resolve_tokens(identity: str) -> tuple[str, str | None]:
    """Return (identity_token, app_token) for the given identity."""
    load_dotenv(Path.home() / ".env")
    env_var = _TOKEN_MAP[identity]
    identity_token = os.environ.get(env_var, "")
    app_token = os.environ.get(_APP_TOKEN_VAR) or None

    if not identity_token:
        console.print(
            Panel(
                f"[bold red]Token not found[/bold red]\n\n"
                f"Set [bold]{env_var}[/bold] in ~/.env\n"
                f"See: docs/design/slack/setup.md",
                title="Configuration Error",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    return identity_token, app_token


# ---------------------------------------------------------------------------
# Connector factory + banner (UX-4)
# ---------------------------------------------------------------------------


def _connect(identity: str) -> SlackConnector:
    """Resolve tokens, construct connector, connect, print banner."""
    identity_token, app_token = _resolve_tokens(identity)
    connector = SlackConnector(identity_token=identity_token, app_token=app_token)

    try:
        connector.connect()
    except ConnectorError as exc:
        _print_error(identity, exc)
        raise typer.Exit(1)

    _print_banner(identity, connector)
    return connector


def _print_banner(identity: str, connector: SlackConnector) -> None:
    """Print one-line identity context banner."""
    team = connector._team_name or connector._team_id or "unknown"
    status = connector.get_status().value

    if identity == "user":
        user_name = connector._authenticated_user_id or "unknown"
        console.print(f"[dim]\\[{identity}] {user_name} @ {team} ({status})[/dim]")
    else:
        console.print(f"[dim]\\[{identity}] {team} ({status})[/dim]")


# ---------------------------------------------------------------------------
# Error handling (UX-7)
# ---------------------------------------------------------------------------


def _print_error(identity: str, exc: ConnectorError) -> None:
    """Print a Rich error panel with suggested next action."""
    suggestion = ""
    if isinstance(exc, NotAuthorized):
        if "missing_scope" in str(exc):
            suggestion = (
                f"Fix: Add the required scope in your Slack app's OAuth settings, "
                f"reinstall, and update your token.\n"
                f"Check: appif-slack {identity} status"
            )
        else:
            suggestion = f"Fix: Verify your token is valid and not expired.\nCheck: appif-slack {identity} status"
    elif isinstance(exc, TargetUnavailable):
        suggestion = f"Try: appif-slack {identity} channels"
    elif isinstance(exc, TransientFailure):
        suggestion = "This may be temporary. Try again in a moment."

    body = f"[bold red]{exc}[/bold red]"
    if suggestion:
        body += f"\n\n{suggestion}"

    console.print(Panel(body, title="Error", border_style="red"))


# ---------------------------------------------------------------------------
# Tab completion callbacks (UX-3)
# ---------------------------------------------------------------------------

_TIME_PRESETS = ["5m", "15m", "1h", "4h", "1d", "7d"]


def _complete_since(incomplete: str) -> list[str]:
    return [p for p in _TIME_PRESETS if p.startswith(incomplete)]


_channel_cache: list[str] | None = None


def _complete_channels(incomplete: str) -> list[str]:
    global _channel_cache
    if _channel_cache is None:
        try:
            identity_token, app_token = _resolve_tokens("bot")
            connector = SlackConnector(identity_token=identity_token, app_token=app_token)
            connector.connect()
            accounts = connector.list_accounts()
            if accounts:
                targets = connector.list_targets(accounts[0].account_id)
                _channel_cache = [t.display_name for t in targets]
            else:
                _channel_cache = []
            connector.disconnect()
        except Exception:
            _channel_cache = []
    return [c for c in _channel_cache if c.startswith(incomplete)]


# ---------------------------------------------------------------------------
# Time string parsing (UX-6)
# ---------------------------------------------------------------------------

_TIME_PATTERN = re.compile(r"^(\d+)([mhd])$")


def _parse_since(since: str) -> datetime:
    """Parse a time string like '1h', '30m', '7d' into a UTC datetime."""
    match = _TIME_PATTERN.match(since)
    if not match:
        console.print(f"[red]Invalid time format:[/red] {since}\nExpected: 5m, 15m, 1h, 4h, 1d, 7d")
        raise typer.Exit(1)

    value, unit = int(match.group(1)), match.group(2)
    delta = {"m": timedelta(minutes=value), "h": timedelta(hours=value), "d": timedelta(days=value)}[unit]
    return datetime.now(UTC) - delta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool_style(v: bool) -> str:
    return "[green]yes[/green]" if v else "[red]no[/red]"


# ---------------------------------------------------------------------------
# Listener for listen + messages commands
# ---------------------------------------------------------------------------


class _RichPrinter:
    """MessageListener that pretty-prints every message to the console."""

    def on_message(self, event: MessageEvent) -> None:
        ts = event.timestamp.astimezone(UTC).strftime("%H:%M:%S")
        header = Text()
        header.append(f"[{ts}] ", style="dim")
        header.append(event.author.display_name, style="bold cyan")
        console.print(Panel(event.content.text, title=header, border_style="blue"))


class _CollectorListener:
    """MessageListener that collects events into a list."""

    def __init__(self) -> None:
        self.events: list[MessageEvent] = []

    def on_message(self, event: MessageEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _cmd_status(identity: str) -> None:
    """Show identity and capabilities."""
    connector = _connect(identity)
    try:
        caps = connector.get_capabilities()

        table = Table(title="Capabilities", show_header=True)
        table.add_column("Capability", style="bold")
        table.add_column("Value")
        table.add_row("identity_type", connector._auth.identity_type)
        table.add_row("delivery_mode", caps.delivery_mode)
        table.add_row("supports_realtime", _bool_style(caps.supports_realtime))
        table.add_row("supports_backfill", _bool_style(caps.supports_backfill))
        table.add_row("supports_threads", _bool_style(caps.supports_threads))
        table.add_row("supports_reply", _bool_style(caps.supports_reply))
        table.add_row("supports_auto_send", _bool_style(caps.supports_auto_send))
        console.print(table)
    finally:
        connector.disconnect()


def _cmd_channels(identity: str, type_filter: str | None) -> None:
    """List visible conversations."""
    connector = _connect(identity)
    try:
        accounts = connector.list_accounts()
        if not accounts:
            console.print("[yellow]No accounts found.[/yellow]")
            return

        targets = connector.list_targets(accounts[0].account_id)

        if type_filter:
            targets = [t for t in targets if t.type == type_filter]

        table = Table(title=f"Channels ({len(targets)})", show_header=True)
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("ID", style="dim")

        for t in sorted(targets, key=lambda x: (x.type, x.display_name)):
            table.add_row(t.display_name, t.type, t.target_id)

        console.print(table)
    finally:
        connector.disconnect()


def _cmd_messages(identity: str, channel: str | None, since: str | None, limit: int) -> None:
    """Show recent messages."""
    connector = _connect(identity)
    try:
        accounts = connector.list_accounts()
        if not accounts:
            console.print("[yellow]No accounts found.[/yellow]")
            return

        account_id = accounts[0].account_id

        # Resolve channel name to ID if provided
        conversation_ids: list[str] | None = None
        if channel:
            targets = connector.list_targets(account_id)
            # Strip leading # if present
            channel_name = channel.lstrip("#")
            matched = [t for t in targets if t.display_name == channel_name or t.target_id == channel_name]
            if not matched:
                console.print(
                    Panel(
                        f"[bold red]Channel not found:[/bold red] {channel}\n\n"
                        f"Try: appif-slack {identity} channels",
                        title="Error",
                        border_style="red",
                    )
                )
                return
            conversation_ids = [matched[0].target_id]
        else:
            # Default: get recent DMs and channels (first 10)
            targets = connector.list_targets(account_id)
            conversation_ids = [t.target_id for t in targets[:10]]

        oldest = _parse_since(since) if since else None
        scope = BackfillScope(
            conversation_ids=conversation_ids,
            oldest=oldest,
        )

        collector = _CollectorListener()
        connector.register_listener(collector)
        connector.backfill(account_id, scope)

        events = sorted(collector.events, key=lambda e: e.timestamp)
        if limit:
            events = events[-limit:]

        if not events:
            console.print("[dim]No messages found.[/dim]")
            return

        table = Table(title=f"Messages ({len(events)})", show_header=True)
        table.add_column("Time", style="dim")
        table.add_column("Author", style="bold cyan")
        table.add_column("Channel", style="dim")
        table.add_column("Text")

        for ev in events:
            ts = ev.timestamp.astimezone(UTC).strftime("%H:%M:%S")
            text = ev.content.text[:80] + "..." if len(ev.content.text) > 80 else ev.content.text
            ch_name = ev.conversation.opaque_id.get("channel", "") if ev.conversation else ""
            table.add_row(ts, ev.author.display_name, ch_name, text)

        console.print(table)
    finally:
        connector.disconnect()


def _cmd_listen(identity: str) -> None:
    """Stream real-time events."""
    identity_token, app_token = _resolve_tokens(identity)
    connector = SlackConnector(identity_token=identity_token, app_token=app_token)

    caps = connector.get_capabilities()
    if not caps.supports_realtime:
        console.print(
            Panel(
                "[yellow]No app-level token — API-only mode.[/yellow]\n\n"
                f"Set [bold]{_APP_TOKEN_VAR}[/bold] in ~/.env to enable real-time listening.",
                title="Real-time Unavailable",
                border_style="yellow",
            )
        )
        raise typer.Exit(0)

    connector.register_listener(_RichPrinter())

    try:
        connector.connect()
    except ConnectorError as exc:
        _print_error(identity, exc)
        raise typer.Exit(1)

    _print_banner(identity, connector)
    console.print("[green]Listening ... Ctrl+C to stop[/green]\n")

    try:
        connector.listen_forever()
    except KeyboardInterrupt:
        connector.disconnect()
        console.print("\n[yellow]Stopped.[/yellow]")


# Pattern for raw Slack channel IDs: C (public), D (DM), G (group/mpim), W (enterprise)
_CHANNEL_ID_PATTERN = re.compile(r"^[CDGW][A-Z0-9]{8,}$")


def _cmd_send(identity: str, target: str, text: str) -> None:
    """Send a message to a channel or DM."""
    connector = _connect(identity)
    try:
        accounts = connector.list_accounts()
        if not accounts:
            console.print("[yellow]No accounts found.[/yellow]")
            return

        account_id = accounts[0].account_id

        # If target looks like a raw channel ID, send directly without resolution
        clean_target = target.lstrip("#").lstrip("@")
        if _CHANNEL_ID_PATTERN.match(clean_target):
            channel_id = clean_target
            channel_type = "dm" if clean_target.startswith("D") else "channel"
        else:
            # Resolve channel name to ID
            try:
                targets = connector.list_targets(account_id)
            except Exception as exc:
                console.print(
                    Panel(
                        f"[bold red]Cannot list channels:[/bold red] {exc}\n\n"
                        f"Tip: If you know the channel ID, pass it directly:\n"
                        f"  appif-slack {identity} send D0ABC123XYZ \"{text[:30]}...\"",
                        title="Error",
                        border_style="red",
                    )
                )
                return

            matched = [t for t in targets if t.display_name == clean_target or t.target_id == clean_target]
            if not matched:
                console.print(
                    Panel(
                        f"[bold red]Target not found:[/bold red] {target}\n\n"
                        f"Try: appif-slack {identity} channels",
                        title="Error",
                        border_style="red",
                    )
                )
                return

            channel_id = matched[0].target_id
            channel_type = matched[0].type

        ref = ConversationRef(
            connector="slack",
            account_id=account_id,
            type=channel_type,
            opaque_id={"channel": channel_id},
        )
        content = MessageContent(text=text)

        try:
            receipt = connector.send(ref, content)
            console.print(f"[green]Sent![/green] ts={receipt.external_id}  at={receipt.timestamp}")
        except ConnectorError as exc:
            _print_error(identity, exc)
    finally:
        connector.disconnect()


# ---------------------------------------------------------------------------
# Typer app topology (UX-1)
# ---------------------------------------------------------------------------

app = typer.Typer(
    help="Slack connector CLI -- one identity, one command.\n\n"
    "Examples:\n\n"
    "  appif-slack bot status\n\n"
    "  appif-slack user channels\n\n"
    "  appif-slack bot listen\n\n"
    "  appif-slack bot messages --since 1h\n\n"
    '  appif-slack bot send general "Deploy complete"',
    no_args_is_help=True,
    rich_markup_mode="rich",
)

bot_app = typer.Typer(help="Connect as the app bot")
user_app = typer.Typer(help="Connect as yourself")

app.add_typer(bot_app, name="bot")
app.add_typer(user_app, name="user")


# ---------------------------------------------------------------------------
# Command registration (shared between bot and user)
# ---------------------------------------------------------------------------


def _register_commands(sub_app: typer.Typer, identity: str) -> None:
    """Register all five commands on a sub-app, closing over identity."""

    @sub_app.command()
    def status() -> None:
        """Show identity and capabilities."""
        _cmd_status(identity)

    @sub_app.command()
    def channels(
        type: Annotated[
            Optional[str],
            typer.Option("--type", "-t", help="Filter by type: channel, dm, group"),
        ] = None,
    ) -> None:
        """List visible conversations."""
        _cmd_channels(identity, type)

    @sub_app.command()
    def messages(
        channel: Annotated[
            Optional[str],
            typer.Option("--channel", "-c", help="Filter to one channel", autocompletion=_complete_channels),
        ] = None,
        since: Annotated[
            Optional[str],
            typer.Option("--since", "-s", help="Time window (e.g. 1h, 4h, 1d)", autocompletion=_complete_since),
        ] = None,
        limit: Annotated[
            int,
            typer.Option("--limit", "-n", help="Maximum messages to show"),
        ] = 20,
    ) -> None:
        """Show recent messages."""
        _cmd_messages(identity, channel, since, limit)

    @sub_app.command()
    def listen() -> None:
        """Stream real-time events."""
        _cmd_listen(identity)

    @sub_app.command()
    def send(
        target: Annotated[str, typer.Argument(help="Channel name or ID", autocompletion=_complete_channels)],
        text: Annotated[str, typer.Argument(help="Message text")],
    ) -> None:
        """Send a message to a channel or DM."""
        _cmd_send(identity, target, text)


_register_commands(bot_app, "bot")
_register_commands(user_app, "user")