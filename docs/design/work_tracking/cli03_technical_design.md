# Technical Design: W13 Upload Attachments

**Author**: Lead Senior Systems Engineer
**Date**: 2026-03-29
**Status**: Draft
**Prerequisites**: [Design Document](cli03_design.md), [Product Evaluation](cli03_product_evaluation.md), [Requirements W13](requirements.md)

---

## 1. Overview

This document bridges the architect's design for W13 (Upload Attachments) to
implementation. It covers the protocol extension, service routing, Jira adapter
implementation, normalizer considerations, and testing strategy.

The change is additive. No existing types, methods, or tests are modified.

---

## 2. Technology Choices

No new dependencies. The existing `atlassian-python-api` library provides
`add_attachment()` which handles multipart encoding and the required
`X-Atlassian-Token: no-check` header internally. This is the same library
used for all other Jira adapter operations.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Upload mechanism | `atlassian-python-api` `add_attachment()` | Already a dependency; handles multipart + CSRF header |
| Temp file for bytes | `io.BytesIO` | The library accepts file-like objects; avoids filesystem I/O |
| MIME type detection | Delegated to platform | Jira determines MIME from filename; no `mimetypes` import needed |

---

## 3. Affected Files and Changes

Following the construction order from RULES.md (domain first, tests, adapter,
service last):

| Step | File | Change |
|------|------|--------|
| 1 | `src/appif/domain/work_tracking/ports.py` | Add `attach_file()` to `WorkTracker` protocol |
| 2 | `tests/unit/test_jira_normalizer.py` | Add test for normalizing upload response (array extraction) |
| 3 | `src/appif/adapters/jira/adapter.py` | Add `attach_file()` method to `JiraAdapter` |
| 4 | `tests/unit/test_jira_adapter.py` | Add tests for `attach_file()` (or new test file if none exists for adapter) |
| 5 | `src/appif/domain/work_tracking/service.py` | Add `attach_file()` routing method to `WorkTrackingService` |
| 6 | `tests/integration/test_jira_integration.py` | Add integration test for attach + download round-trip |

Files explicitly **not** changed:

- `models.py` -- no new types
- `errors.py` -- no new exceptions
- `__init__.py` -- no new exports (existing `ItemAttachment` already exported)
- `_normalizer.py` -- existing `normalize_attachment()` handles the response

---

## 4. Protocol Extension

### ports.py Addition

Add after `download_attachment`:

```python
def attach_file(
    self,
    key: str,
    filename: str,
    content: bytes,
    *,
    instance: str | None = None,
) -> ItemAttachment:
    """Attach a file to a work item.

    Parameters
    ----------
    key:
        Work item key (e.g. "PROJ-123").
    filename:
        Filename to use on the platform (e.g. "requirements.md").
    content:
        Complete file content as bytes. Must be non-empty.
    instance:
        Optional instance name. Uses default if omitted.

    Returns
    -------
    ItemAttachment
        Metadata for the newly created attachment, including the
        platform-assigned ``id``, confirmed ``filename``,
        ``mime_type``, ``size_bytes``, ``created``, and ``author``.

    Raises
    ------
    WorkTrackingError
        If filename is empty or content is empty.
    ItemNotFound
        If the work item does not exist.
    PermissionDenied
        If the caller lacks permission to attach files.
    """
    ...
```

Design decisions:

- `key` is the first positional parameter (matches `add_comment(key, body)`)
- `filename` before `content` (reads naturally: "attach to KEY a file named
  FILENAME with CONTENT")
- `instance` is keyword-only (matches all other `WorkTracker` methods)
- Returns `ItemAttachment`, not `AttachmentContent` (caller already has the
  bytes)
- Docstring follows the existing `download_attachment` style

---

## 5. Service Routing

### service.py Addition

Add after `download_attachment` method:

```python
def attach_file(
    self,
    key: str,
    filename: str,
    content: bytes,
    *,
    instance: str | None = None,
) -> ItemAttachment:
    return self._resolve(instance).attach_file(key, filename, content)
```

This follows the exact same `_resolve(instance).method()` delegation pattern
used by every other operation in `WorkTrackingService`. The `instance`
parameter is consumed by `_resolve`; the adapter method receives the
remaining arguments without it.

The `ItemAttachment` import is already present in `service.py`.

---

## 6. Jira Adapter Implementation

### 6.1 Input Validation

The adapter validates before calling the platform:

```python
if not filename or not filename.strip():
    raise WorkTrackingError(
        f"cannot attach file to {key}: filename must not be empty",
        instance=self._instance_name,
    )
if not content:
    raise WorkTrackingError(
        f"cannot attach empty file: {filename}",
        instance=self._instance_name,
    )
```

These raise `WorkTrackingError` (not a subclass) because they are caller
errors, not platform errors. The error messages are specific and actionable.

### 6.2 Upload Mechanics

The `atlassian-python-api` library's `Jira` class exposes `add_attachment()`.
However, this method expects a file path. For bytes-based upload, we use
the underlying REST endpoint directly:

```python
import io

def attach_file(self, key: str, filename: str, content: bytes) -> ItemAttachment:
    # Validation (as above)
    ...

    try:
        response = self._client.post(
            f"rest/api/2/issue/{key}/attachments",
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (filename, io.BytesIO(content))},
        )
    except ...:
        # Error translation (same pattern)
```

**Important implementation note**: The `atlassian-python-api` `Jira` client's
`.post()` method uses `requests.Session` under the hood. We need to access
the session directly for multipart file upload because the library's `.post()`
sends JSON by default. The correct approach:

```python
url = f"{self._server_url}/rest/api/2/issue/{key}/attachments"
response = self._client._session.post(
    url,
    headers={"X-Atlassian-Token": "no-check"},
    files={"file": (filename, io.BytesIO(content))},
)
response.raise_for_status()
```

This is the same pattern used by `download_attachment()` which also uses
`self._client._session.get()` to fetch content bytes. We're consistent
with existing adapter code.

### 6.3 Response Handling

Jira's `POST /rest/api/2/issue/{issueIdOrKey}/attachments` returns a JSON
**array** of attachment objects, even for a single file upload:

```json
[
    {
        "id": "10042",
        "filename": "requirements.md",
        "mimeType": "text/markdown",
        "size": 1234,
        "created": "2026-03-29T10:30:00.000+0000",
        "author": { "accountId": "...", "displayName": "..." },
        "content": "https://..."
    }
]
```

The adapter extracts the first element and normalizes:

```python
attachments = response.json()
if not attachments or not isinstance(attachments, list):
    raise WorkTrackingError(
        f"unexpected response when attaching {filename} to {key}",
        instance=self._instance_name,
    )
return normalize_attachment(attachments[0])
```

The existing `normalize_attachment()` in `_normalizer.py` handles this
structure without modification. It already maps `id`, `filename`,
`mimeType`, `size`, `created`, and `author` to `ItemAttachment` fields.

### 6.4 Error Translation

Uses the existing `_translate_error()` helper:

```python
except (ItemNotFound, WorkTrackingError):
    raise
except Exception as exc:
    raise _translate_error(exc, self._instance_name) from exc
```

This is the identical try/except pattern used by every other method in
`JiraAdapter`. The error mapping is:

| HTTP Status | Domain Exception |
|-------------|-----------------|
| 404 | `ItemNotFound` (work item does not exist) |
| 401, 403 | `PermissionDenied` |
| 429 | `RateLimited` |
| 5xx | `ConnectionFailure` |
| Other | `WorkTrackingError` |

### 6.5 Complete Method

```python
def attach_file(self, key: str, filename: str, content: bytes) -> ItemAttachment:
    """Attach a file to a work item.

    Uses Jira's POST /rest/api/2/issue/{key}/attachments endpoint
    with multipart/form-data encoding. Returns metadata for the
    newly created attachment.
    """
    if not filename or not filename.strip():
        raise WorkTrackingError(
            f"cannot attach file to {key}: filename must not be empty",
            instance=self._instance_name,
        )
    if not content:
        raise WorkTrackingError(
            f"cannot attach empty file: {filename}",
            instance=self._instance_name,
        )

    try:
        url = f"{self._server_url}/rest/api/2/issue/{key}/attachments"
        response = self._client._session.post(
            url,
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (filename, io.BytesIO(content))},
        )
        response.raise_for_status()

        attachments = response.json()
        if not attachments or not isinstance(attachments, list):
            raise WorkTrackingError(
                f"unexpected response when attaching {filename} to {key}",
                instance=self._instance_name,
            )

        log.debug(
            "Attached %s to %s (%d bytes)",
            filename,
            key,
            len(content),
        )

        return normalize_attachment(attachments[0])
    except (ItemNotFound, WorkTrackingError):
        raise
    except Exception as exc:
        raise _translate_error(exc, self._instance_name) from exc
```

---

## 7. Testing Strategy

### 7.1 Unit Tests -- Adapter (no I/O)

**File**: `tests/unit/test_jira_adapter.py` (or co-located with existing
adapter tests)

Tests for `JiraAdapter.attach_file()`:

| Test | Asserts | Self-Check |
|------|---------|------------|
| `test_attach_file_empty_filename_raises` | `WorkTrackingError` with actionable message when filename is `""` | Business rule: empty filenames are invalid |
| `test_attach_file_whitespace_filename_raises` | `WorkTrackingError` when filename is `"   "` | Edge case of the same business rule |
| `test_attach_file_empty_content_raises` | `WorkTrackingError` with filename in message when content is `b""` | Business rule: empty files are meaningless |
| `test_attach_file_success_returns_item_attachment` | Returns `ItemAttachment` with correct fields from mocked Jira response array | Boundary contract: adapter correctly maps platform response to domain type |
| `test_attach_file_404_raises_item_not_found` | `ItemNotFound` when session.post returns 404 | Error translation contract |
| `test_attach_file_403_raises_permission_denied` | `PermissionDenied` when session.post returns 403 | Error translation contract |

**Not testing** (per RULES.md anti-patterns):

- That `io.BytesIO` is constructed (implementation detail)
- That `_session.post` is called with specific headers (mock wiring)
- That `normalize_attachment` is called (tested separately in normalizer tests)

### 7.2 Unit Tests -- Normalizer

**File**: `tests/unit/test_jira_normalizer.py`

The existing `normalize_attachment` tests cover the response structure. One
additional test:

| Test | Asserts |
|------|---------|
| `test_normalize_attachment_from_upload_response` | `normalize_attachment()` correctly handles the shape returned by the upload endpoint (same as download metadata shape -- this confirms the reuse assumption) |

### 7.3 Integration Test

**File**: `tests/integration/test_jira_integration.py`

One round-trip test (marked `@pytest.mark.integration`):

```python
def test_attach_and_download_round_trip(service, test_issue_key):
    """Attach a file, retrieve the item, verify attachment appears,
    download it, verify content matches."""
    content = b"# Test Document\n\nThis is a test attachment."
    filename = "test_attachment.md"

    # Upload
    attachment = service.attach_file(
        test_issue_key, filename, content, instance=TEST_INSTANCE
    )
    assert attachment.filename == filename
    assert attachment.size_bytes == len(content)
    assert attachment.id  # platform-assigned

    # Verify it appears on the work item
    item = service.get_item(test_issue_key, instance=TEST_INSTANCE)
    attachment_ids = [a.id for a in item.attachments]
    assert attachment.id in attachment_ids

    # Download and verify content
    downloaded = service.download_attachment(
        attachment.id, instance=TEST_INSTANCE
    )
    assert downloaded.data == content
    assert downloaded.metadata.filename == filename
```

This test earns its keep because it:
1. Validates a real platform round-trip (high contact with reality)
2. Covers the boundary contract (upload produces an ID that download accepts)
3. Would catch real failures: auth issues, multipart encoding bugs, response parsing errors
4. Verifies the attachment appears on the item (not just in isolation)

---

## 8. Import Changes

### adapter.py

Add `io` to imports:

```python
import io
```

`normalize_attachment` is already imported. `ItemAttachment` needs to be
added to the import from models:

```python
from appif.domain.work_tracking.models import (
    # ... existing imports ...
    ItemAttachment,
)
```

### service.py

`ItemAttachment` is already imported in `service.py` (used by the models
import block). No new imports needed.

---

## 9. Public API Surface

No changes to `__init__.py`. `ItemAttachment` is already exported. The
new `attach_file()` method is accessed through `WorkTrackingService`
(which implements `WorkTracker`), both of which are already public.

Callers:

```python
from appif.domain.work_tracking import WorkTrackingService, ItemAttachment

svc = WorkTrackingService()
metadata: ItemAttachment = svc.attach_file(
    "PROJ-123",
    "requirements.md",
    Path("requirements.md").read_bytes(),
)
```

---

## 10. Construction Order

Following RULES.md and the established pattern from `technical_design.md`
Section 11:

1. Add `attach_file()` to `WorkTracker` protocol in `ports.py`
2. Add unit tests for adapter validation and error translation
3. Add `attach_file()` to `JiraAdapter` in `adapter.py`
4. Run unit tests -- green
5. Add `attach_file()` routing to `WorkTrackingService` in `service.py`
6. Run full unit test suite -- green
7. Add integration test (gated by `@pytest.mark.integration`)
8. Run integration test against live Jira -- green
9. Update `CHANGELOG.md` with the new capability
10. Version bump consideration (1.2.0) in `pyproject.toml`

---

## 11. What Is NOT Changing

Explicit confirmation for reviewers:

- **No new domain types** in `models.py`
- **No new error types** in `errors.py`
- **No new exports** in `__init__.py`
- **No changes to `_normalizer.py`** -- existing `normalize_attachment()` handles the response
- **No changes to `_auth.py`** -- same authentication
- **No changes to existing tests** -- all changes are additive
- **No changes to `InstanceRegistry`** -- same multi-instance architecture
- **No new dependencies** in `pyproject.toml`