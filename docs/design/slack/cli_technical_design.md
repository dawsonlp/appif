# Slack CLI Technical Design

**Author**: Senior Engineer
**Date**: 2026-03-07
**Status**: Draft
**Version**: 1.0
**Implements**: `cli_requirements.md` v1.0 (UX-1 through UX-9)

---

## 1. Overview

Rewrite `src/appif/cli/slack.py` to implement the identity-first command structure described in the CLI UX requirements. The CLI is a thin shell over the existing `SlackConnector`. It contains no business logic -- only token resolution, connector invocation, and Rich output formatting.

---

## 2. Dependencies

Already present in `pyproject.toml`:

| Library | Version | Purpose |
|---------|---------|---------|
| `typer` | `>=0.15` | CLI framework, subcommand routing, tab completion |
| `rich` | `>=13.9` | Tables, panels, styled console output |
| `python-dotenv` | `>=1.0` | Load `~/.env` for token resolution |

No new dependencies required. `typer` provides `--install-completion` and `--show-completion` out of the box.

---

## 3. Entry Point

`pyproject.toml` already defines:

```toml
[project.scripts]
appif-slack = "appif.cli.slack:app"
```

The `app` object is the top-level `typer.Typer` instance. No change to the entry point.

---

## 4. Module Structure

Single file: `src/appif/cli/slack.py`. The CLI is small enough that splitting into multiple files adds navigation cost without organizational benefit.

Internal organization:

```
# --- Token resolution (identity -> env var mapping) ---
# --- Connector factory (identity -> SlackConnector) ---
# --- Banner printing (identity context on every command) ---
# --- Error handling (ConnectorError -> Rich error panel) ---
# --- Tab completion callbacks (channels, time presets) ---
# --- Typer app definition (top-level + bot/user sub-apps) ---
# --- Command implementations (status, channels, messages, listen, send) ---
```

---

## 5. Typer App Topology

```python
app = typer.Typer(
    help="Slack connector CLI -- one identity, one command.",
    epilog="Setup: appif-slack --install-completion",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

bot_app = typer.Typer(help="Connect as the app bot")
user_app = typer.Typer(help="Connect as yourself")

app.add_typer(bot_app, name="bot")
app.add_typer(user_app, name="user")
```

Both `bot_app` and `user_app` register the same five command functions. Each command receives the identity as a closure variable -- not as a parameter. This means the five functions are defined once and registered twice.

**Tab completion flow**: `appif-slack <TAB>` yields `bot`, `user` (from the two sub-typers). `appif-slack bot <TAB>` yields the five commands.

---

## 6. Token Resolution

Maps the CLI identity word to environment variable names. No renaming of existing env vars.

```python
_TOKEN_MAP = {
    "bot": "APPIF_SLACK_BOT_OAUTH_TOKEN",
    "user": "APPIF_SLACK_USER_OAUTH_TOKEN",
}
_APP_TOKEN_VAR = "APPIF_SLACK_BOT_APP_LEVEL_TOKEN"
```

Resolution function:

```python
def _resolve_tokens(identity: str) -> tuple[str, str | None]:
    """Return (identity_token, app_token) for the given identity.

    Raises typer.Exit with a Rich error panel if the identity token is missing.
    """
    load_dotenv(Path.home() / ".env")
    env_var = _TOKEN_MAP[identity]
    identity_token = os.environ.get(env_var, "")
    app_token = os.environ.get(_APP_TOKEN_VAR) or None

    if not identity_token:
        # UX-7: error with suggested next action
        console.print(Panel(
            f"[bold red]Token not found[/bold red]\n\n"
            f"Set [bold]{env_var}[/bold] in ~/.env\n"
            f"See: docs/design/slack/setup.md",
            title="Configuration Error",
            border_style="red",
        ))
        raise typer.Exit(1)

    return identity_token, app_token
```

---

## 7. Connector Factory

Creates and connects a `SlackConnector`, printing the identity banner (UX-4).

```python
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
```

For commands that do not need a full connection (none currently, but future-proofing), a `_make_connector` variant constructs without connecting.

---

## 8. Identity Banner (UX-4)

```python
def _print_banner(identity: str, connector: SlackConnector) -> None:
    """Print one-line identity context banner."""
    team = connector._team_name or connector._team_id or "unknown"
    status = connector.get_status().value

    if identity == "user":
        # Resolve display name from authenticated user ID
        user_name = connector._authenticated_user_id or "unknown"
        console.print(f"[dim]\\[{identity}] {user_name} @ {team} ({status})[/dim]")
    else:
        console.print(f"[dim]\\[{identity}] {team} ({status})[/dim]")
```

The banner uses Rich dim styling to be visually subordinate to command output.

---

## 9. Error Handling (UX-7)

All commands wrap connector calls in a shared error handler that maps `ConnectorError` subtypes to actionable Rich panels.

```python
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
            suggestion = (
                f"Fix: Verify your token is valid and not expired.\n"
                f"Check: appif-slack {identity} status"
            )
    elif isinstance(exc, TargetUnavailable):
        suggestion = f"Try: appif-slack {identity} channels"
    elif isinstance(exc, TransientFailure):
        suggestion = "This may be temporary. Try again in a moment."

    body = f"[bold red]{exc}[/bold red]"
    if suggestion:
        body += f"\n\n{suggestion}"

    console.print(Panel(body, title="Error", border_style="red"))
```

---

## 10. Tab Completion Callbacks (UX-3)

### Static completion: time presets

```python
_TIME_PRESETS = ["5m", "15m", "1h", "4h", "1d", "7d"]

def _complete_since(incomplete: str) -> list[str]:
    return [p for p in _TIME_PRESETS if p.startswith(incomplete)]
```

### Dynamic completion: channel names

```python
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
```

The channel completer uses the bot token by default (broader visibility). If the bot token is unavailable, it falls back to an empty list. The cache is module-level and persists for the shell session (completion callbacks run in the same process for a single tab press, but Typer/Click re-invokes for each tab -- so this cache helps within a single completion cycle).

---

## 11. Time String Parsing (UX-6)

Parses human-friendly time strings into `datetime` objects for backfill scope:

```python
import re
from datetime import datetime, timedelta, UTC

_TIME_PATTERN = re.compile(r"^(\d+)([mhd])$")

def _parse_since(since: str) -> datetime:
    """Parse a time string like '1h', '30m', '7d' into a UTC datetime."""
    match = _TIME_PATTERN.match(since)
    if not match:
        console.print(
            f"[red]Invalid time format:[/red] {since}\n"
            f"Expected: 5m, 15m, 1h, 4h, 1d, 7d"
        )
        raise typer.Exit(1)

    value, unit = int(match.group(1)), match.group(2)
    delta = {"m": timedelta(minutes=value), "h": timedelta(hours=value), "d": timedelta(days=value)}[unit]
    return datetime.now(UTC) - delta
```

---

## 12. Command Implementations

### 12.1 Shared Registration Pattern

Each command is defined once and registered on both sub-apps:

```python
def _register_commands(sub_app: typer.Typer, identity: str) -> None:
    """Register all five commands on a sub-app, closing over identity."""

    @sub_app.command()
    def status():
        """Show identity and capabilities."""
        _cmd_status(identity)

    @sub_app.command()
    def channels(type: ... = None):
        ...

    # ... etc for messages, listen, send

_register_commands(bot_app, "bot")
_register_commands(user_app, "user")
```

This avoids code duplication. Both `bot status` and `user status` call the same `_cmd_status` with different identity strings.

### 12.2 `status` Command

Connector method: `get_capabilities()`, `list_accounts()`

```python
def _cmd_status(identity: str) -> None:
    connector = _connect(identity)
    try:
        caps = connector.get_capabilities()
        auth = connector._auth

        table = Table(title="Capabilities", show_header=True)
        table.add_column("Capability", style="bold")
        table.add_column("Value")
        table.add_row("identity_type", auth.identity_type)
        table.add_row("delivery_mode", caps.delivery_mode)
        table.add_row("supports_realtime", _bool(caps.supports_realtime))
        table.add_row("supports_backfill", _bool(caps.supports_backfill))
        table.add_row("supports_threads", _bool(caps.supports_threads))
        table.add_row("supports_reply", _bool(caps.supports_reply))
        table.add_row("supports_auto_send", _bool(caps.supports_auto_send))
        console.print(table)
    finally:
        connector.disconnect()
```

`_bool` is a helper: `"yes" if v else "no"` with green/red styling.

### 12.3 `channels` Command

Connector methods: `list_accounts()`, `list_targets()`

Options:
- `--type` / `-t`: filter by `channel`, `dm`, `group`

Output: Rich Table with columns: Name, Type, ID.

### 12.4 `messages` Command

Connector methods: `list_accounts()`, `list_targets()`, `backfill()`

Options:
- `--channel` / `-c`: filter to one channel (tab-completable)
- `--since` / `-s`: time window, parsed by `_parse_since`
- `--limit` / `-n`: max messages, default 20

Implementation approach:
1. Connect and get account ID
2. If `--channel` specified, resolve channel name to ID from `list_targets()`
3. Build `BackfillScope` with `oldest=_parse_since(since)` if provided, and `conversation_ids`
4. Register a `_CollectorListener` that appends events to a list
5. Call `connector.backfill(account_id, scope)`
6. Sort collected events by timestamp, truncate to `--limit`
7. Render as Rich Table: Time, Author, Channel, Text (truncated to 80 chars)

### 12.5 `listen` Command

Connector methods: `register_listener()`, `connect()`, `listen_forever()`

No required options. Uses a `_RichPrinter` listener (already exists in current code) that formats each `MessageEvent` as a Rich Panel with timestamp and author.

When `supports_realtime` is `False`, prints a warning and exits (graceful degradation).

### 12.6 `send` Command

Connector methods: `list_accounts()`, `list_targets()`, `send()`

Arguments:
- `target`: positional, required, tab-completable (channel name)
- `text`: positional, required (the message body)

Implementation:
1. Connect and get account ID
2. Resolve target name to channel ID via `list_targets()`
3. Build `ConversationRef` with `opaque_id={"channel": channel_id}`
4. Build `MessageContent` with the text
5. Call `connector.send()`, print the `SendReceipt`

If target resolution fails, print error suggesting `appif-slack <identity> channels`.

---

## 13. Files Modified

| File | Change |
|------|--------|
| `src/appif/cli/slack.py` | Full rewrite -- identity-first Typer app with five commands |

## 14. Files NOT Modified

| File | Reason |
|------|--------|
| `pyproject.toml` | `typer` and `rich` already present, entry point unchanged |
| `src/appif/adapters/slack/connector.py` | No connector changes needed |
| `src/appif/domain/messaging/*` | No domain changes needed |
| `src/appif/adapters/slack/_auth.py` | No auth changes needed |

---

## 15. Testing Strategy

The CLI is a thin presentation layer over a tested connector. Testing focuses on:

| Level | What | How |
|-------|------|-----|
| Smoke test | CLI loads and `--help` works | `appif-slack --help`, `appif-slack bot --help` |
| Command test | Each command runs without error against live workspace | Manual verification, same pattern as `scripts/send_proof.py` |
| Error formatting | ConnectorError subtypes render as Rich panels with suggestions | Unit test the `_print_error` function with fake errors |

No mocking of the connector. The CLI is infrastructure code -- its value is in actually connecting. Integration tests via the existing `tests/integration/test_slack_integration.py` already verify the connector methods the CLI calls.

---

## 16. Constraints

1. Single file (`src/appif/cli/slack.py`) -- the CLI is simple enough to keep in one module.
2. No business logic -- the CLI resolves tokens, calls connector methods, and formats output. It does not filter, transform, or interpret message content.
3. All connector calls go through the public `Connector` protocol methods. The only private attribute access is `_auth.identity_type`, `_team_name`, `_team_id`, and `_authenticated_user_id` for the banner display.
4. The existing `scripts/slack_listener.py`, `scripts/slack_proof.py`, and `scripts/send_proof.py` remain as standalone verification tools. They are not replaced by the CLI.
5. `--install-completion` is provided by Typer automatically -- no custom implementation needed.