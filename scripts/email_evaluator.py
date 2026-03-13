#!/usr/bin/env python3
"""Gmail email evaluator -- watches for new messages and classifies them with Claude.

Uses GmailConnector from appif for all Gmail API interaction (auth, polling,
message normalisation, retry logic). This script only adds the Claude
classification layer on top.

The evaluation prompt is loaded from an external file so personal/role
details are not committed to version control.

Prompt file locations (checked in order):
    1. Path in APPIF_EMAIL_EVAL_PROMPT env var
    2. ~/.config/appif/prompts/email_evaluator.txt

If no prompt file is found, a generic fallback prompt is used.

Usage:
    python scripts/email_evaluator.py                    # watch for new emails (default)
    python scripts/email_evaluator.py --interval 60      # poll every 60s instead of 30s
    python scripts/email_evaluator.py --batch 10         # one-shot: evaluate last 10 messages and exit
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------------

def _load_env():
    try:
        from dotenv import load_dotenv
        env_path = Path.home() / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

_load_env()

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

_DEFAULT_PROMPT_PATH = Path.home() / ".config" / "appif" / "prompts" / "email_evaluator.txt"

_FALLBACK_PROMPT = """Evaluate this email and provide a brief, actionable summary.

Classify the email into ONE of these categories and respond in this exact format:

CATEGORY: [one of the categories below]
FROM: [sender name/org]
SUMMARY: [1-2 sentence summary of what this is about]
ACTION: [what, if anything, the recipient should do]

Categories:
- JUNK/SALES: Low-value marketing, cold outreach, vendor spam. Action: ignore/delete.
- INTERESTING OFFER: A promotion, webinar, or product that might genuinely be useful. Note what makes it interesting.
- ACTION REQUESTED: Someone is asking the recipient to do something specific. Say exactly what and any deadline.
- PERSONAL: A message from a real person (colleague, friend, family). Note who and what.
- INFORMATIONAL: Newsletter, digest, or notification worth scanning. Note the key topic.
- SECURITY/IT: Password resets, MFA prompts, IT notifications. Note urgency.
- CALENDAR: Meeting invites, scheduling. Note when and with whom.
- FINANCIAL: Invoices, receipts, expense reports. Note amounts if visible.

---
EMAIL:
From: {sender}
To: {to}
Date: {date}
Subject: {subject}

{body}
---

Respond concisely. No preamble."""


def _load_prompt() -> str:
    """Load evaluation prompt from external file, falling back to built-in default.

    The prompt must contain {sender}, {to}, {date}, {subject}, {body} placeholders.
    """
    prompt_path = Path(os.environ.get("APPIF_EMAIL_EVAL_PROMPT", str(_DEFAULT_PROMPT_PATH)))
    if prompt_path.exists():
        return prompt_path.read_text()
    return _FALLBACK_PROMPT

# ---------------------------------------------------------------------------
# Claude evaluation via langchain
# ---------------------------------------------------------------------------

def _get_evaluator():
    api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("No CLAUDE_API_KEY or ANTHROPIC_API_KEY found in ~/.env")
        sys.exit(1)

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model="claude-sonnet-4-20250514",
        anthropic_api_key=api_key,
        max_tokens=500,
        temperature=0,
    )


def _evaluate_and_print(llm, event):
    """Classify a MessageEvent via Claude and print the result."""
    # Extract fields from the normalised MessageEvent
    sender = event.author.display_name or event.author.id
    to_addr = event.account_id or ""
    subject = event.conversation_ref.opaque_id.get("subject", "(no subject)")
    date_str = event.timestamp.strftime("%a, %d %b %Y %H:%M:%S %z") if event.timestamp else ""

    body = event.content.text or ""
    if len(body) > 2000:
        body = body[:2000] + "\n... [truncated]"

    prompt_template = _load_prompt()
    prompt = prompt_template.format(
        sender=sender,
        to=to_addr,
        date=date_str,
        subject=subject,
        body=body,
    )
    response = llm.invoke(prompt)

    print("=" * 70)
    print(f"  {subject}")
    print(f"  From: {sender}")
    print(f"  Date: {date_str}")
    print("-" * 70)
    print(response.content)
    print()


# ---------------------------------------------------------------------------
# Connector setup
# ---------------------------------------------------------------------------

def _make_connector(poll_interval: int = 30):
    """Create a GmailConnector configured from environment."""
    from appif.adapters.gmail import GmailConnector, FileCredentialAuth

    return GmailConnector(
        auth=FileCredentialAuth(),
        poll_interval=poll_interval,
    )


# ---------------------------------------------------------------------------
# Watch mode -- register a listener, let the poller deliver events
# ---------------------------------------------------------------------------

class _EvalListener:
    """MessageListener that classifies incoming emails with Claude."""

    def __init__(self, llm):
        self._llm = llm
        self._seen: set[str] = set()

    def on_message(self, event) -> None:
        if event.message_id in self._seen:
            return
        self._seen.add(event.message_id)

        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{ts}] New email received\n")

        try:
            _evaluate_and_print(self._llm, event)
        except Exception as e:
            print(f"  [error] Classification failed: {e}")


def run_watch(interval: int = 30):
    """Poll for new messages and evaluate them."""
    print(f"Watching for new emails (polling every {interval}s)...")
    print("Press Ctrl+C to stop.\n")

    llm = _get_evaluator()
    connector = _make_connector(poll_interval=interval)

    listener = _EvalListener(llm)
    connector.register_listener(listener)

    try:
        connector.connect()
        print("Connected. Waiting for new messages...\n")

        # Block until interrupted
        import time
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        connector.disconnect()
        print("Disconnected.")


# ---------------------------------------------------------------------------
# Batch mode -- use backfill to fetch recent messages
# ---------------------------------------------------------------------------

def run_once(count: int = 10):
    """Evaluate the most recent messages using backfill."""
    print("Fetching recent emails for evaluation...")

    llm = _get_evaluator()
    connector = _make_connector()

    collected: list = []

    class _Collector:
        def on_message(self, event):
            collected.append(event)

    collector = _Collector()
    connector.register_listener(collector)

    try:
        connector.connect()

        from appif.domain.messaging.models import BackfillScope
        scope = BackfillScope(
            oldest=datetime.now(timezone.utc) - timedelta(days=7),
        )
        connector.backfill(connector.list_accounts()[0].account_id, scope)
    finally:
        connector.disconnect()

    # Take the most recent N
    recent = collected[-count:] if len(collected) > count else collected
    print(f"Evaluating {len(recent)} emails...\n")

    for event in recent:
        try:
            _evaluate_and_print(llm, event)
        except Exception as e:
            print(f"  [error] Classification failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Gmail email evaluator powered by Claude")
    parser.add_argument("--batch", type=int, default=None, metavar="N", help="One-shot: evaluate last N messages and exit")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30)")
    args = parser.parse_args()

    if args.batch is not None:
        run_once(count=args.batch)
    else:
        run_watch(interval=args.interval)


if __name__ == "__main__":
    main()