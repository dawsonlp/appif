# Design Document: W13 Upload Attachments

**Author**: Architect
**Date**: 2026-03-29
**Status**: Draft
**Requirement**: W13 (Upload Attachments)
**Trigger**: CLI-03 enhancement request, approved by Product (see `cli03_product_evaluation.md`)

---

## 1. Problem Statement

The work tracking domain supports downloading attachments from work items
(`download_attachment()`, added v0.6.0) but cannot upload them. Agents that
generate artifacts (documents, reports, analysis results) have no way to
attach those artifacts to the originating work item through the appif
interface.

This design adds the write-side counterpart to complete the attachment
lifecycle.

---

## 2. Scope

### In Scope

| Responsibility | Description |
|----------------|-------------|
| Upload a file to a work item | Attach caller-provided bytes with a filename to an existing work item |
| Return attachment metadata | Return `ItemAttachment` for the newly created attachment |
| Multi-instance routing | Same `instance` parameter pattern as all `WorkTracker` methods |
| Error translation | Map platform upload errors to existing domain exceptions |

### Out of Scope

| Concern | Why excluded |
|---------|-------------|
| Delete attachment | No current consumer need; add incrementally if required |
| Replace/update attachment | Platforms generally do not support in-place replacement |
| Attachment versioning | Not a platform primitive; application-level concern |
| Streaming upload | Symmetric with `download_attachment()` which is fully-buffered |
| File path resolution | The domain accepts bytes, not filesystem paths (see Section 4) |

---

## 3. Design Decision: Bytes-Only Input

The `WorkTracker` protocol method accepts file content as `bytes` and a
`filename` as `str`. It does not accept filesystem paths.

**Rationale:**

1. **The domain is I/O-free.** Reading a file from disk is infrastructure
   (filesystem I/O). The domain protocol defines what the adapter must do
   with content, not where content comes from. Callers are responsible for
   reading their own files.

2. **Symmetric with download.** `download_attachment()` returns
   `AttachmentContent` containing `bytes`. The upload direction accepts
   `bytes`. The mental model is consistent.

3. **Caller flexibility.** Content may come from a file, from memory, from
   a network response, or from a generation pipeline. Requiring `bytes`
   makes no assumptions about source.

4. **Testability.** Tests pass bytes directly without touching the
   filesystem.

A convenience wrapper that reads a path and calls the protocol method is a
valid helper at the application layer, but it does not belong in the domain
interface.

---

## 4. Protocol Extension

### New Method on `WorkTracker`

```
attach_file(key, filename, content, *, instance?) -> ItemAttachment
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | `str` | Work item key (e.g. "PROJ-123") |
| `filename` | `str` | Filename to use on the platform (e.g. "requirements.md") |
| `content` | `bytes` | Complete file content |
| `instance` | `str or None` | Optional instance name; default instance if omitted |

**Returns**: `ItemAttachment` -- metadata for the newly created attachment.

**Why `ItemAttachment` and not `AttachmentContent`?**

The caller already has the content (they provided it). Returning the full
`AttachmentContent` (metadata + bytes echo) would waste memory for no value.
The caller needs the platform-assigned metadata: the attachment `id`,
confirmed `filename`, `mime_type` as determined by the platform,
`size_bytes`, `created` timestamp, and `author`.

### No New Domain Types

All required types already exist:

- `ItemAttachment` (return type) -- frozen dataclass, already in models
- `ItemAuthor` (within `ItemAttachment.author`) -- already in models
- No new command object is needed. The parameters are simple scalars
  (`str`, `str`, `bytes`). A Pydantic model would add ceremony without
  value for three fields with no cross-field validation rules.

---

## 5. Error Handling

Upload failures map to the existing error hierarchy. No new exception types
are required.

| Failure | Domain Exception | Notes |
|---------|-----------------|-------|
| Work item does not exist | `ItemNotFound` | Platform returns 404 |
| Insufficient permissions | `PermissionDenied` | Platform returns 401/403 |
| Rate limited | `RateLimited` | Platform returns 429 |
| Platform unreachable | `ConnectionFailure` | Network/server error |
| File too large | `WorkTrackingError` | Platform-specific size limit; message should include the limit |
| Empty content | `WorkTrackingError` | Adapter should reject zero-byte uploads before calling the platform |
| Empty filename | `WorkTrackingError` | Adapter should reject before calling the platform |

The adapter translates platform-specific errors to these domain exceptions
using the same `_translate_error` pattern established for all other
operations.

---

## 6. Multi-Instance Routing

Identical to all other `WorkTracker` methods:

1. `WorkTrackingService.attach_file()` receives the call
2. `_resolve(instance)` selects the adapter
3. Adapter's `attach_file()` executes the platform operation
4. Result propagates back

No changes to the routing architecture. No changes to `InstanceRegistry`.

---

## 7. Constraints

1. **Content is fully-buffered.** The method accepts `bytes`, not a stream
   or file-like object. This is symmetric with `download_attachment()` and
   appropriate for the expected file sizes (documents, reports -- not
   multi-gigabyte artifacts). If streaming is needed later, it will be a
   separate method, not a modification of this one.

2. **MIME type is adapter-determined.** The caller provides a filename. The
   adapter (or platform) determines the MIME type from the filename
   extension or content. The caller does not specify MIME type. This keeps
   the interface simple and avoids mismatches between declared and actual
   content types.

3. **Filename must be non-empty.** The adapter validates this before
   calling the platform.

4. **Content must be non-empty.** The adapter validates this before calling
   the platform. Zero-byte attachments are rejected.

5. **Platform size limits are platform concerns.** The domain does not
   define a maximum file size. If the platform rejects an upload due to
   size, the adapter translates it to `WorkTrackingError` with a
   descriptive message. Callers that need to check limits before uploading
   can use platform documentation; the domain does not expose a
   `get_max_attachment_size()` method (YAGNI).

6. **No duplicate detection.** Uploading the same filename twice creates
   two attachments. This matches platform behavior (Jira, GitHub, etc.)
   and is the expected semantic for agents that may re-run.

---

## 8. Impact on Existing Components

| Component | Change |
|-----------|--------|
| `WorkTracker` protocol (`ports.py`) | Add `attach_file()` method |
| `WorkTrackingService` (`service.py`) | Add routing method |
| `JiraAdapter` (`adapter.py`) | Add implementation |
| `_normalizer.py` | May need to handle upload response normalization (existing `normalize_attachment` may suffice) |
| Domain models (`models.py`) | No changes |
| Domain errors (`errors.py`) | No changes |
| Existing tests | No changes (additive feature) |
| New tests | Unit test for adapter, integration test for round-trip |

---

## 9. What This Buys You

| Benefit | How |
|---------|-----|
| Agents can attach generated artifacts to work items | Direct `attach_file()` call, same pattern as `add_comment()` |
| RADEMO1 D2.5 unblocked | Attach `.md` requirements document to originating ticket |
| Symmetric attachment lifecycle | Download and upload both available; read and write parity |
| No new concepts to learn | Same types, same error handling, same instance routing |

---

## 10. Versioning

This is an additive, backward-compatible change. Existing callers are
unaffected. New callers opt in by calling `attach_file()`.

Target release: appif 1.2.0 (minor version bump per semver).

The `WorkTracker` protocol gains a new method. Existing implementations
that do not yet support upload will raise `NotImplementedError` or
equivalent until updated. Since appif currently has one adapter (Jira),
this is implemented atomically.