# Slack CLI User Experience Requirements

**Author**: UX Architecture
**Date**: 2026-03-07
**Status**: Draft
**Version**: 1.0

---

## Context

The Slack connector is a transport adapter that gives AI agents authenticated, normalized access to Slack workspaces. The CLI gives humans the same access for verification, debugging, and ad-hoc operations.

The connector supports two identity types -- bot (`xoxb-`) and user (`xoxp-`) -- each with different visibility and permissions. The CLI must expose both identities cleanly without requiring the user to understand token prefixes, environment variable names, or connector internals.

### Who uses this CLI

- Developers verifying connector behavior during development
- Operators checking workspace connectivity and permissions
- Agent developers testing message flows before wiring up their agent

These users are technical but busy. They want to accomplish a task in seconds, not minutes. They should not need to read documentation to use the CLI productively.

---

## Design Rationale

### Identity is the first decision, not a flag

Choosing bot vs user changes what you see and what you can do. It is the most consequential decision in any CLI session. Burying it in a flag (`--identity bot`) makes it forgettable and invisible in shell history.

Making identity the first word after the program name makes it:
- **Permanent** -- always visible in history
- **Obvious** -- tab shows exactly two choices
- **Grammatical** -- reads like a sentence: "appif-slack bot listen"

### Two words should accomplish anything

Every useful operation should be reachable as `appif-slack <identity> <action>`. No deeper nesting. Arguments and options exist but are never required for the default case.

### Tab completion is the documentation

If you can tab your way through the entire command, you never need to read docs. The CLI must be designed so that pressing tab at any cursor position teaches the user what is possible.

### Every output starts with who you are

The user should never wonder which identity they are using. A one-line banner before every command's output eliminates the question permanently.

### Errors suggest the next action

An error that says `missing_scope` without telling you what to do is hostile UX. Every error message must include a concrete suggestion for what to try next.

---

## Requirements

### UX-1: Identity-first command structure

The CLI command structure is `appif-slack <identity> <action> [arguments]`.

`<identity>` is one of: `bot`, `user`.

**Rationale**: Identity determines everything downstream -- what channels are visible, whether sending is possible, what capabilities are reported. Making it positional and first ensures it is never omitted or forgotten.

**Acceptance criteria:**
- `appif-slack bot <action>` authenticates using the bot token
- `appif-slack user <action>` authenticates using the user token
- Both identities expose the same set of actions
- The identity word is visible in shell history for every command

---

### UX-2: Action vocabulary

Five actions are available under each identity:

| Action | Purpose | Connects to Slack? |
|--------|---------|--------------------|
| `status` | Show identity type, team, and capabilities | Yes (auth_test) |
| `channels` | List visible conversations | Yes |
| `messages` | Show recent messages | Yes |
| `listen` | Stream real-time events | Yes |
| `send` | Send a message to a channel or DM | Yes |

**Rationale**: These five actions cover the complete lifecycle of interacting with a workspace: understanding what you have access to (`status`, `channels`), reading what happened (`messages`), watching what is happening (`listen`), and participating (`send`). Each maps directly to an existing connector method.

**Acceptance criteria:**
- All five actions are available under both `bot` and `user`
- Each action produces useful output with no required arguments (except `send`, which requires target and text)
- Actions that the identity lacks permission for produce clear errors (see UX-7)

---

### UX-3: Tab completion at every position

The CLI must provide shell tab completion at every cursor position:

| Position | Completes to |
|----------|-------------|
| `appif-slack <TAB>` | `bot`, `user` |
| `appif-slack bot <TAB>` | `channels`, `listen`, `messages`, `send`, `status` |
| `appif-slack bot send <TAB>` | Channel names and usernames from the workspace |
| `appif-slack bot messages --channel <TAB>` | Channel names from the workspace |
| `appif-slack bot messages --since <TAB>` | Time presets: `5m`, `15m`, `1h`, `4h`, `1d`, `7d` |

**Rationale**: Completion eliminates memorization. For dynamic completions (channel names, usernames), the CLI fetches the workspace data on the first tab press and caches it for the session. This means the user discovers available channels by pressing tab, not by running a separate command first.

**Acceptance criteria:**
- Shell completion is installable via `appif-slack --install-completion`
- Static completions (identity, action) work immediately
- Dynamic completions (channels, users) fetch from the API on first use
- Completion results are cached within a single shell session

---

### UX-4: Identity banner on all output

Every command prints a one-line identity banner before its output:

```
[bot] Highspring Digital - LABS (connected)
```
```
[user] ldawson @ Highspring Digital - LABS (connected)
```

The banner shows:
- Identity type in brackets
- For user identity: the authenticated user's display name
- Workspace name
- Connection state

**Rationale**: When a developer runs multiple commands in sequence, possibly switching between bot and user, the banner provides immediate context. It eliminates "wait, which one am I?" moments.

**Acceptance criteria:**
- Every command prints the banner as its first line of output
- Bot banner shows `[bot]` and workspace name
- User banner shows `[user]`, display name, and workspace name
- The banner is visually distinct from command output (e.g., dim or colored)

---

### UX-5: Smart defaults

Commands work usefully with no arguments beyond identity and action:

| Command | Default behavior |
|---------|-----------------|
| `status` | Show all capabilities and identity info |
| `channels` | List all visible conversations |
| `messages` | Show last 20 messages across all channels |
| `listen` | Stream events from all channels |
| `send` | Requires target and text (no default) |

**Rationale**: The most common use case should require the least typing. A developer checking connectivity should type `appif-slack bot status` and see everything, not `appif-slack bot status --show-capabilities --show-identity --verbose`.

**Acceptance criteria:**
- `status`, `channels`, `messages`, and `listen` produce useful output with no additional arguments
- `send` requires target and text, prompting clearly if either is missing
- Optional filters narrow the defaults, they never expand them

---

### UX-6: Optional filters use short, memorable names

| Command | Optional flags | Purpose |
|---------|---------------|---------|
| `messages` | `--channel`, `-c` | Filter to one channel |
| `messages` | `--since`, `-s` | Time window (e.g., `1h`, `4h`, `1d`) |
| `messages` | `--limit`, `-n` | Maximum messages to show |
| `channels` | `--type`, `-t` | Filter by type: `channel`, `dm`, `group` |

**Rationale**: Flags exist to narrow results, not to enable them. Short aliases (`-c`, `-s`, `-n`, `-t`) reward frequent users without penalizing first-time users who use the long forms.

**Acceptance criteria:**
- All flags have both long and short forms
- `--since` accepts human-friendly time strings (`5m`, `1h`, `1d`)
- Invalid time strings produce clear errors with examples
- Tab completion works for all flag values

---

### UX-7: Errors suggest the next action

Every error message includes:
1. What went wrong (one line)
2. Why (the Slack error code or reason)
3. What to try next (a concrete command or instruction)

Example:
```
[user] ldawson @ Highspring Digital - LABS
ERROR: Cannot send message -- missing_scope
  The user token needs the chat:write scope.
  Fix: Add chat:write in your Slack app's OAuth settings, reinstall, and update your token.
  Check: appif-slack user status
```

**Rationale**: Errors are the moment the user most needs help. A bare error code forces them to search documentation. A suggestion keeps them moving.

**Acceptance criteria:**
- Authorization errors (`missing_scope`, `not_authed`, `token_revoked`) include scope/token guidance
- Target errors (`channel_not_found`) suggest `appif-slack <identity> channels` to discover valid targets
- Transient errors suggest retrying
- No error message is a bare exception traceback

---

### UX-8: Help text teaches by example

The top-level help and each subcommand's help include concrete examples that a user can copy-paste:

```
appif-slack --help

  Slack connector CLI -- one identity, one command.

  Usage: appif-slack <identity> <action>

  Identities:
    bot     Connect as the app bot
    user    Connect as yourself

  Actions:
    status    Show identity and capabilities
    channels  List visible conversations
    messages  Show recent messages
    listen    Stream real-time events
    send      Send a message

  Examples:
    appif-slack bot status
    appif-slack user channels
    appif-slack bot listen
    appif-slack bot messages --since 1h
    appif-slack bot send #general "Deploy complete"

  Setup: appif-slack --install-completion
```

**Rationale**: Examples are more useful than parameter descriptions for first-time users. A user who sees `appif-slack bot listen` in the help output knows exactly what to type. A user who sees `--identity IDENTITY [required]` does not.

**Acceptance criteria:**
- `--help` at every level includes at least two examples
- Examples use realistic values (channel names, messages)
- The help text fits in one terminal screen (no scrolling needed)

---

### UX-9: Shell completion installation

The CLI supports installing shell completions for bash, zsh, and fish via a single command:

```
appif-slack --install-completion
```

**Rationale**: Tab completion is the primary discovery mechanism (UX-3). If installing it requires manual shell configuration, most users will not bother. A single command removes the barrier.

**Acceptance criteria:**
- `--install-completion` works for the user's active shell
- After installation, completions are available in new shell sessions
- The command confirms success and tells the user to open a new terminal

---

## Environment Configuration

The CLI resolves tokens by identity without requiring the user to know variable names:

| Identity | Token source |
|----------|-------------|
| `bot` | `APPIF_SLACK_BOT_OAUTH_TOKEN` from `~/.env` |
| `user` | `APPIF_SLACK_USER_OAUTH_TOKEN` from `~/.env` |
| (both) | `APPIF_SLACK_BOT_APP_LEVEL_TOKEN` from `~/.env` (optional, enables real-time) |

**Rationale**: The env var names already exist and are in use. Renaming them would break existing scripts and require coordinated migration. The CLI maps identities to tokens internally. The user types `bot` or `user` and does not need to care which environment variable is read.

If neither token is configured, the CLI prints setup instructions rather than a stack trace.

---

## Out of Scope

- OAuth browser flow for token acquisition
- Persistent message storage or search
- Multi-workspace support (one workspace per invocation)
- Interactive TUI (menus, scrolling message views)
- File upload or attachment handling