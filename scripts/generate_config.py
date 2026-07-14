#!/usr/bin/env python3
"""Generate per-service ``config.yaml`` files from environment variables.

appif reads all configuration from a single discoverable base directory
(``~/.config/appif`` by default; see :mod:`appif.config`), one subdirectory per
service. This helper bootstraps those files from the ``APPIF_*`` environment
variables you already have (typically sourced from ``~/.env``) -- it copies the
same information into the new structure without touching ``~/.env``.

Existing files are left alone unless ``--force`` is given. Secret *values* are
never printed; only which files were written and which keys were populated.

Usage::

    python scripts/generate_config.py            # write any missing config.yaml
    python scripts/generate_config.py --dry-run  # show what would be written
    python scripts/generate_config.py --force    # overwrite existing files
"""

from __future__ import annotations

import argparse
import os

import yaml

from appif import config

# (service, {yaml_field: env_var}). client_id/tenant fall back across vars via
# the tuple of candidate env names.
_MESSAGING = {
    "gmail": {
        "account_env": "APPIF_GMAIL_ACCOUNT",
        "default_account": "default",
        "fields": {
            "client_id": ("APPIF_GMAIL_CLIENT_ID",),
            "client_secret": ("APPIF_GMAIL_CLIENT_SECRET",),
        },
    },
    "outlook": {
        "account_env": "APPIF_OUTLOOK_ACCOUNT",
        "default_account": "default",
        "fields": {
            "client_id": ("APPIF_OUTLOOK_CLIENT_ID",),
            "client_secret": ("APPIF_OUTLOOK_CLIENT_SECRET",),
            "tenant_id": ("APPIF_OUTLOOK_TENANT_ID",),
        },
    },
    "teams": {
        "account_env": "APPIF_TEAMS_ACCOUNT",
        "default_account": "default",
        "fields": {
            "client_id": ("APPIF_TEAMS_CLIENT_ID", "APPIF_OUTLOOK_CLIENT_ID"),
            "client_secret": ("APPIF_TEAMS_CLIENT_SECRET", "APPIF_OUTLOOK_CLIENT_SECRET"),
            "tenant_id": ("APPIF_TEAMS_TENANT_ID", "APPIF_OUTLOOK_TENANT_ID"),
        },
    },
    "slack": {
        "account_env": "APPIF_SLACK_ACCOUNT",
        "default_account": "default",
        "fields": {
            "bot_oauth_token": ("APPIF_SLACK_BOT_OAUTH_TOKEN",),
            "user_oauth_token": ("APPIF_SLACK_USER_OAUTH_TOKEN",),
            "app_level_token": ("APPIF_SLACK_BOT_APP_LEVEL_TOKEN",),
            "signing_secret": ("APPIF_SLACK_BOT_SIGNING_SECRET",),
            "client_id": ("APPIF_SLACK_BOT_CLIENT_ID",),
            "client_secret": ("APPIF_SLACK_BOT_CLIENT_SECRET",),
            "app_id": ("APPIF_SLACK_BOT_APP_ID",),
        },
    },
}


def _first_env(candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        val = os.environ.get(name)
        if val:
            return val
    return None


def _build_account(fields: dict[str, tuple[str, ...]]) -> dict[str, str]:
    account: dict[str, str] = {}
    for field, candidates in fields.items():
        val = _first_env(candidates)
        if val:
            account[field] = val
    return account


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show what would be written")
    parser.add_argument("--force", action="store_true", help="overwrite existing config.yaml files")
    args = parser.parse_args()

    config.load_env()
    print(f"config dir: {config.config_dir()}")

    for service, spec in _MESSAGING.items():
        path = config.service_config_path(service)
        account_name = os.environ.get(spec["account_env"]) or spec["default_account"]
        account = _build_account(spec["fields"])

        if not account:
            print(f"  {service}: no env values found — skipped")
            continue

        if path.exists() and not args.force:
            print(f"  {service}: {path} exists — skipped (use --force to overwrite)")
            continue

        doc = {"accounts": {account_name: account}, "default": account_name}
        keys = ", ".join(sorted(account.keys()))
        if args.dry_run:
            print(f"  {service}: would write {path} — account '{account_name}' with [{keys}]")
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
        path.chmod(0o600)
        print(f"  {service}: wrote {path} — account '{account_name}' with [{keys}]")

    print("\njira: managed separately (instances in jira/config.yaml) — left unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
