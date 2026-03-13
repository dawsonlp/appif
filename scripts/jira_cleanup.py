#!/usr/bin/env python3
"""Clean up Jira tickets created by integration tests.

Reads ticket keys from ~/.config/appif/jira/test_cleanup.json and
deletes them from the Jira instance.

Usage:
    python scripts/jira_cleanup.py          # Delete all tracked tickets
    python scripts/jira_cleanup.py --dry-run  # Show what would be deleted
    python scripts/jira_cleanup.py --key TSTADPT-1 TSTADPT-2  # Delete specific keys
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from appif.domain.work_tracking.service import WorkTrackingService

CLEANUP_FILE = Path.home() / ".config" / "appif" / "jira" / "test_cleanup.json"
INSTANCE = "personal"


def load_keys() -> list[str]:
    """Load ticket keys from the cleanup file."""
    if not CLEANUP_FILE.exists():
        return []
    return json.loads(CLEANUP_FILE.read_text())


def main():
    parser = argparse.ArgumentParser(description="Delete Jira test tickets")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    parser.add_argument("--key", nargs="+", help="Specific keys to delete (overrides cleanup file)")
    args = parser.parse_args()

    keys = args.key if args.key else load_keys()
    if not keys:
        print("No tickets to clean up.")
        return

    print(f"Found {len(keys)} ticket(s) to delete: {', '.join(keys)}")

    if args.dry_run:
        print("(dry run -- no changes made)")
        return

    svc = WorkTrackingService(auto_load=True)
    svc.set_default(INSTANCE)
    adapter = svc._resolve(INSTANCE)

    deleted = []
    failed = []
    for key in keys:
        try:
            # atlassian-python-api: delete_issue
            adapter._client.delete_issue(key)
            print(f"  Deleted {key}")
            deleted.append(key)
        except Exception as exc:
            print(f"  FAILED {key}: {exc}")
            failed.append(key)

    # Update cleanup file: keep only failed keys
    if failed:
        CLEANUP_FILE.write_text(json.dumps(failed, indent=2))
        print(f"\n{len(deleted)} deleted, {len(failed)} failed (kept in cleanup file)")
    else:
        if CLEANUP_FILE.exists():
            CLEANUP_FILE.unlink()
        print(f"\nAll {len(deleted)} tickets deleted. Cleanup file removed.")


if __name__ == "__main__":
    main()