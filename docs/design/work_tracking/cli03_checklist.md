# Implementation Checklist: W13 Upload Attachments

**Engineer**: Senior Python Implementation Engineer
**Date**: 2026-03-29
**Technical Design**: [cli03_technical_design.md](cli03_technical_design.md)

---

## Pre-Implementation Review

The technical design is mostly accurate with two corrections discovered
during implementation review:

1. **`ItemAttachment` is NOT imported in `service.py`** -- the technical
   design states "ItemAttachment is already imported in service.py" but
   this is false. It must be added to the import block.

2. **No `test_jira_adapter.py` exists** -- the technical design references
   "co-located with existing adapter tests." There is no such file. A new
   `tests/unit/test_jira_attach_file.py` will be created. This keeps the
   test focused and avoids creating a sprawling adapter test file.

3. **No additional normalizer test needed** -- the existing
   `TestNormalizeAttachment` class in `test_jira_normalizer.py` already
   covers the exact JSON shape returned by the upload endpoint (same
   fields: id, filename, mimeType, size, created, author). The upload
   response is an array wrapper around the same object; array extraction
   is adapter logic, not normalizer logic, and is tested in the adapter
   tests.

All other technical design details verified against the codebase.

---

## Checklist

Construction order follows RULES.md: domain first, tests, adapter, service.

### 1. Protocol -- `ports.py`

- [x] Add `attach_file(key, filename, content, *, instance=None) -> ItemAttachment`
      to `WorkTracker` protocol
- [x] Add `ItemAttachment` to the imports from models (already imported:
      `AttachmentContent` is, but `ItemAttachment` is not)
- [x] Docstring follows `download_attachment` style with Parameters, Returns,
      Raises sections

### 2. Unit Tests -- `tests/unit/test_jira_attach_file.py`

- [x] `test_attach_file_empty_filename_raises` -- `WorkTrackingError`,
      message contains "filename must not be empty"
- [x] `test_attach_file_whitespace_filename_raises` -- `WorkTrackingError`
      when filename is `"   "`
- [x] `test_attach_file_empty_content_raises` -- `WorkTrackingError`,
      message contains the filename
- [x] `test_attach_file_success_returns_item_attachment` -- mock
      `_session.post` to return JSON array with one attachment object;
      verify returned `ItemAttachment` has correct fields
- [x] `test_attach_file_404_raises_item_not_found` -- mock 404 response,
      verify `ItemNotFound`
- [x] `test_attach_file_403_raises_permission_denied` -- mock 403
      response, verify `PermissionDenied`

### 3. Adapter -- `adapter.py`

- [x] Add `import io` to imports
- [x] Add `ItemAttachment` to the models import block
- [x] Add `attach_file(self, key, filename, content) -> ItemAttachment`
      method after `download_attachment`
- [x] Input validation: empty/whitespace filename raises `WorkTrackingError`
- [x] Input validation: empty content raises `WorkTrackingError` with
      filename in message
- [x] Upload via `self._client._session.post()` with
      `X-Atlassian-Token: no-check` header and multipart `files=` param
- [x] Extract first element from JSON array response
- [x] Guard against empty/non-list response
- [x] Normalize via existing `normalize_attachment()`
- [x] Debug log: filename, key, byte count
- [x] Error translation: same try/except pattern as all other methods

### 4. Service -- `service.py`

- [x] Add `ItemAttachment` to the models import block
- [x] Add `attach_file()` routing method after `download_attachment`,
      following `_resolve(instance).attach_file(key, filename, content)`
      pattern

### 5. Run Unit Tests

- [x] `pytest tests/unit/ -v` -- 366 passed, 1 pre-existing failure
      (outlook connector, unrelated). All 6 new tests pass.

### 6. Integration Test -- `test_jira_integration.py`

- [x] Add `test_12_attach_and_download_round_trip` to
      `TestJiraCRUDLifecycle` class
- [x] Upload a small `.md` file
- [x] Verify returned `ItemAttachment` has correct filename and size
- [x] Re-fetch the work item, verify attachment ID appears in
      `item.attachments`
- [x] Download via `download_attachment()`, verify content matches
- [x] Uses existing `_created_keys` pattern for cleanup awareness

### 7. Run Integration Tests

- [ ] `pytest tests/integration/test_jira_integration.py -v` -- requires
      live Jira instance with credentials configured

### 8. Housekeeping

- [x] Update `CHANGELOG.md` with W13 entry under `[Unreleased]`
- [x] Verify `ruff check` clean on all changed files
- [x] Verify `ruff format --check` clean on all changed files
