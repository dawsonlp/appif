"""Outlook CLI — verify setup and exercise the connector.

Usage::

    appif-outlook status
    appif-outlook folders
    appif-outlook inbox [--limit 5]
    appif-outlook send user@example.com "Hello from appif"
    appif-outlook send user@example.com "Subject line" --subject "Re: Meeting"
    appif-outlook consent
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from appif.domain.messaging.errors import ConnectorError, NotAuthorized, TransientFailure
from appif.domain.messaging.models import (
    ConversationRef,
    MessageContent,
    MessageEvent,
)

logger = logging.getLogger(__name__)
console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_ENV_VARS = {
    "client_id": "APPIF_OUTLOOK_CLIENT_ID",
    "client_secret": "APPIF_OUTLOOK_CLIENT_SECRET",
    "tenant_id": "APPIF_OUTLOOK_TENANT_ID",
    "account": "APPIF_OUTLOOK_ACCOUNT",
    "credentials_dir": "APPIF_OUTLOOK_CREDENTIALS_DIR",
}


def _load_env() -> None:
    """Load ~/.env if available."""
    env_path = Path.home() / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _get_connector():
    """Build and connect an OutlookConnector, printing status."""
    from appif.adapters.outlook.connector import OutlookConnector

    _load_env()
    connector = OutlookConnector()

    try:
        connector.connect()
    except ConnectorError as exc:
        _print_error(exc)
        raise typer.Exit(1)

    return connector


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def _print_error(exc: ConnectorError) -> None:
    """Print a Rich error panel with remediation guidance."""
    suggestion = ""
    if isinstance(exc, NotAuthorized):
        suggestion = (
            "Fix: Check your credentials and re-run consent if needed.\n"
            "  python scripts/outlook_consent.py\n"
            "  appif-outlook status"
        )
    elif isinstance(exc, TransientFailure):
        suggestion = "This may be temporary. Try again in a moment."
    else:
        suggestion = "Check the setup guide: docs/design/outlook/setup.md"

    body = f"[bold red]{exc}[/bold red]"
    if suggestion:
        body += f"\n\n{suggestion}"

    console.print(Panel(body, title="Error", border_style="red"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bool_style(v: bool) -> str:
    return "[green]yes[/green]" if v else "[red]no[/red]"


_TIME_PATTERN = re.compile(r"^(\d+)([mhd])$")
_TIME_PRESETS = ["5m", "15m", "1h", "4h", "1d", "7d"]


def _parse_since(since: str) -> datetime:
    """Parse a time string like '1h', '30m', '7d' into a UTC datetime."""
    match = _TIME_PATTERN.match(since)
    if not match:
        console.print(f"[red]Invalid time format:[/red] {since}\nExpected: 5m, 15m, 1h, 4h, 1d, 7d")
        raise typer.Exit(1)
    value, unit = int(match.group(1)), match.group(2)
    delta = {"m": timedelta(minutes=value), "h": timedelta(hours=value), "d": timedelta(days=value)}[unit]
    return datetime.now(UTC) - delta


def _complete_since(incomplete: str) -> list[str]:
    return [p for p in _TIME_PRESETS if p.startswith(incomplete)]


# ---------------------------------------------------------------------------
# Listener for backfill
# ---------------------------------------------------------------------------


class _CollectorListener:
    """Collects MessageEvent objects into a list."""

    def __init__(self) -> None:
        self.events: list[MessageEvent] = []

    def on_message(self, event: MessageEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    help="Outlook connector CLI -- verify setup and test the connector.\n\n"
    "Examples:\n\n"
    "  appif-outlook status\n\n"
    "  appif-outlook folders\n\n"
    "  appif-outlook inbox --limit 5\n\n"
    '  appif-outlook send user@example.com "Hello from appif"\n\n'
    "  appif-outlook consent",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show configuration, connection status, and capabilities."""
    _load_env()

    # Show configuration
    table = Table(title="Configuration", show_header=True)
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    table.add_column("Source", style="dim")

    client_id = os.environ.get("APPIF_OUTLOOK_CLIENT_ID", "")
    tenant_id = os.environ.get("APPIF_OUTLOOK_TENANT_ID", "common")
    account = os.environ.get("APPIF_OUTLOOK_ACCOUNT", "default")
    cred_dir = os.environ.get("APPIF_OUTLOOK_CREDENTIALS_DIR", str(Path.home() / ".config" / "appif" / "outlook"))
    cred_file = Path(cred_dir) / f"{account}.json"

    table.add_row(
        "Client ID",
        f"{client_id[:8]}...{client_id[-4:]}" if len(client_id) > 12 else (client_id or "[red]NOT SET[/red]"),
        "APPIF_OUTLOOK_CLIENT_ID",
    )
    table.add_row("Tenant ID", tenant_id, "APPIF_OUTLOOK_TENANT_ID")
    table.add_row("Account", account, "APPIF_OUTLOOK_ACCOUNT")
    table.add_row(
        "Credential file",
        f"{'[green]exists[/green]' if cred_file.exists() else '[red]missing[/red]'} ({cred_file})",
        "APPIF_OUTLOOK_CREDENTIALS_DIR",
    )
    console.print(table)
    console.print()

    if not client_id:
        console.print(
            Panel(
                "[bold red]Client ID not configured[/bold red]\n\n"
                "Set APPIF_OUTLOOK_CLIENT_ID in ~/.env\n"
                "See: docs/design/outlook/setup.md",
                title="Setup Required",
                border_style="red",
            )
        )
        return

    if not cred_file.exists():
        console.print(
            Panel(
                "[bold yellow]Credential file not found[/bold yellow]\n\n"
                "Run the consent flow first:\n"
                "  python scripts/outlook_consent.py\n\n"
                "Or: appif-outlook consent",
                title="Consent Required",
                border_style="yellow",
            )
        )
        return

    # Try connecting
    connector = _get_connector()
    try:
        accounts = connector.list_accounts()
        caps = connector.get_capabilities()

        console.print(f"[green bold]Connected[/green bold] as [cyan]{accounts[0].display_name}[/cyan]")
        console.print()

        cap_table = Table(title="Capabilities", show_header=True)
        cap_table.add_column("Capability", style="bold")
        cap_table.add_column("Value")
        cap_table.add_row("supports_realtime", _bool_style(caps.supports_realtime))
        cap_table.add_row("supports_backfill", _bool_style(caps.supports_backfill))
        cap_table.add_row("supports_threads", _bool_style(caps.supports_threads))
        cap_table.add_row("supports_reply", _bool_style(caps.supports_reply))
        cap_table.add_row("supports_auto_send", _bool_style(caps.supports_auto_send))
        cap_table.add_row("delivery_mode", caps.delivery_mode)
        console.print(cap_table)
    finally:
        connector.disconnect()


@app.command()
def folders() -> None:
    """List mail folders visible to the connector."""
    connector = _get_connector()
    try:
        accounts = connector.list_accounts()
        targets = connector.list_targets(accounts[0].account_id)

        table = Table(title=f"Mail Folders ({len(targets)})", show_header=True)
        table.add_column("Name", style="bold")
        table.add_column("ID", style="dim")

        for t in targets:
            table.add_row(t.display_name, t.target_id)

        console.print(table)
    finally:
        connector.disconnect()


@app.command()
def inbox(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of messages to show")] = 10,
    since: Annotated[
        str | None,
        typer.Option("--since", "-s", help="Time window (e.g. 1h, 4h, 1d)", autocompletion=_complete_since),
    ] = None,
) -> None:
    """Show recent inbox messages."""
    import httpx

    connector = _get_connector()
    try:
        token = connector._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        params: dict = {
            "$top": str(limit),
            "$select": "id,from,subject,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc",
        }

        if since:
            oldest = _parse_since(since)
            params["$filter"] = f"receivedDateTime ge {oldest.isoformat()}"

        response = httpx.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages",
            headers=headers,
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()

        msgs = response.json().get("value", [])

        if not msgs:
            console.print("[dim]No messages found.[/dim]")
            return

        table = Table(title=f"Inbox ({len(msgs)} messages)", show_header=True)
        table.add_column("Received", style="dim", width=19)
        table.add_column("From", style="bold cyan", width=25)
        table.add_column("Subject", width=40)
        table.add_column("Preview", style="dim")

        for m in msgs:
            sender = m.get("from", {}).get("emailAddress", {})
            received = m.get("receivedDateTime", "?")
            if received != "?":
                try:
                    dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
                    received = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            name = sender.get("name", sender.get("address", "?"))
            subject = m.get("subject", "(no subject)")
            preview = (m.get("bodyPreview", "") or "")[:60]

            table.add_row(received, name, subject, preview)

        console.print(table)
    finally:
        connector.disconnect()


@app.command()
def send(
    to: Annotated[str, typer.Argument(help="Recipient email address")],
    text: Annotated[str, typer.Argument(help="Message body")],
    subject: Annotated[
        str | None,
        typer.Option("--subject", "-s", help="Subject line (default: 'appif test')"),
    ] = None,
) -> None:
    """Send an email to a recipient."""
    from appif.adapters.outlook._message_builder import build_message as _build

    connector = _get_connector()
    try:
        actual_subject = subject or "appif test"

        conv = ConversationRef(
            connector="outlook",
            account_id=connector._account,
            type="email_thread",
            opaque_id={"recipient": to},
        )
        content = MessageContent(text=text)

        # Build and inject subject into the payload manually
        # since the connector.send() doesn't pass subject through
        import httpx

        payload = _build(conv, content, subject=actual_subject)
        route = payload.pop("_route")
        token = connector._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        if route == "reply":
            parent_id = payload.pop("_parent_message_id")
            response = httpx.post(
                f"https://graph.microsoft.com/v1.0/me/messages/{parent_id}/reply",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
        else:
            response = httpx.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                json=payload,
                headers=headers,
                timeout=30.0,
            )

        response.raise_for_status()

        console.print(
            Panel(
                f"[green bold]Message sent![/green bold]\n\n"
                f"  To:      {to}\n"
                f"  Subject: {actual_subject}\n"
                f"  Status:  HTTP {response.status_code}",
                title="Send Result",
                border_style="green",
            )
        )
    except httpx.HTTPStatusError as exc:
        console.print(
            Panel(
                f"[bold red]Send failed[/bold red]\n\n"
                f"  Status: {exc.response.status_code}\n"
                f"  Body:   {exc.response.text[:300]}",
                title="Error",
                border_style="red",
            )
        )
    finally:
        connector.disconnect()


@app.command()
def consent(
    account: Annotated[str, typer.Option("--account", "-a", help="Account label")] = "default",
    tenant: Annotated[str | None, typer.Option("--tenant", "-t", help="Azure AD tenant ID")] = None,
) -> None:
    """Run the OAuth consent flow (opens a browser)."""
    import subprocess
    import sys

    _load_env()

    cmd = [sys.executable, "scripts/outlook_consent.py", "--account", account]
    if tenant:
        cmd.extend(["--tenant", tenant])

    console.print(f"[dim]Running: {' '.join(cmd)}[/dim]\n")
    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[3]))
    raise typer.Exit(result.returncode)
