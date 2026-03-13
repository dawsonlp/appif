# Jira Adapter -- Setup Guide

Step-by-step instructions to create an API token and configure the Jira adapter.

---

## 1. Prerequisites

- An Atlassian Cloud account (e.g., `https://your-domain.atlassian.net`)
- At least one Jira project you have permission to create/edit issues in

## 2. Create an API Token

1. Go to [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**
3. Give it a label (e.g., `appif`)
4. Click **Create**
5. **Copy the token** -- you will not be able to see it again

## 3. Create the Config File

The Jira adapter uses a YAML config file (not environment variables).

Create the directory and file:

```bash
mkdir -p ~/.config/appif/jira
```

Edit `~/.config/appif/jira/config.yaml`:

```yaml
instances:
  personal:
    jira:
      url: https://your-domain.atlassian.net
      username: your-email@example.com
      api_token: your-api-token-here

default: personal
```

**Do not commit this file to version control.** It contains your API token and lives in your home directory, outside the repo.

## 4. Verify It Works

```python
from appif.domain.work_tracking.service import WorkTrackingService

service = WorkTrackingService()  # Loads from ~/.config/appif/jira/config.yaml

# List registered instances
for inst in service.list_instances():
    print(f"{inst.name}: {inst.url} (default={inst.is_default})")

# Search for recent issues in a project
from appif.domain.work_tracking.models import SearchCriteria
results = service.search(SearchCriteria(project="MYPROJECT"))
for item in results.items:
    print(f"  {item.key}: {item.title} [{item.status}]")
```

## 5. Multi-instance Setup

Add multiple Jira instances for different accounts or organizations:

```yaml
instances:
  personal:
    jira:
      url: https://personal.atlassian.net
      username: you@personal.com
      api_token: token-1

  work:
    jira:
      url: https://company.atlassian.net
      username: you@company.com
      api_token: token-2

default: personal
```

Operations use the default instance unless you specify otherwise:

```python
# Uses default instance
service.get_item("PROJ-123")

# Uses a specific instance
service.get_item("PROJ-123", instance="work")
```

You can also change the default at runtime:

```python
service.set_default("work")
```

## 6. Config Path Override

By default the adapter looks for config at `~/.config/appif/jira/config.yaml`.

Override with the `APPIF_JIRA_CONFIG` environment variable:

```bash
export APPIF_JIRA_CONFIG=/path/to/custom/config.yaml
```

Or in your `~/.env`:

```
APPIF_JIRA_CONFIG=/path/to/custom/config.yaml
```

## 7. Testing

Integration tests run against the live Jira API -- they are NOT mocked.

```bash
# Integration tests (hits real Jira)
pytest tests/integration/test_jira_integration.py -v

# Clean up test tickets after a run
python scripts/jira_cleanup.py
```

Tests create tickets in the `TSTADPT` project. The cleanup script reads a
tracking file and deletes any tickets created during the test run.

```bash
# Preview what would be deleted (no actual deletion)
python scripts/jira_cleanup.py --dry-run

# Delete a specific ticket manually
python scripts/jira_cleanup.py --key TSTADPT-42
```

## 8. Config Format Compatibility

The YAML config format is compatible with the `jira-helper` MCP server. If you
already have a config for that server, the Jira adapter can read the same file.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ConnectionFailure: Could not connect` | Check URL in config -- must include `https://` |
| `PermissionDenied` on create | Your account lacks create permission in the target project |
| `ItemNotFound` on get | Issue key is wrong or you lack permission to view it |
| `InvalidTransition` | The transition name doesn't match available transitions -- use `get_transitions()` to list |
| `InstanceNotFound` | Instance name doesn't match any entry in config YAML |
| `NoDefaultInstance` | No `default:` key in config and no instance specified |
| Config file not found | Verify `~/.config/appif/jira/config.yaml` exists, or set `APPIF_JIRA_CONFIG` |
| `401 Unauthorized` from Jira API | API token expired or incorrect -- regenerate at id.atlassian.com |