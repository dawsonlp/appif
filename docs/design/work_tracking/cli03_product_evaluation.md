# Product Evaluation: CLI-03 File Attachment Upload

**Evaluator**: Product Officer
**Date**: 2026-03-29
**Enhancement**: CLI-03 -- File attachment upload capability
**Requestor**: RADEMO1 team (blocking requirement D2.5)

---

## 1. Summary

The RADEMO1 team requests `attach_file()` on `WorkTrackingService` to upload
file attachments to work items. The library currently supports reading
attachments (`download_attachment()`, added v0.6.0) but has no write-side
equivalent. This blocks RADEMO1 requirement D2.5, which requires attaching a
generated `.md` file to a Jira ticket.

## 2. Validation of the Request

### Factual Accuracy

The enhancement request is **correct on all points**:

| Claim | Verified |
|-------|----------|
| `download_attachment()` exists | Yes -- `WorkTracker` protocol, `JiraAdapter`, `WorkTrackingService` |
| No `attach_file()` or equivalent exists | Yes -- confirmed in ports, service, and adapter |
| Original requirements listed attachment upload as out of scope | Yes -- W11 Out of Scope in `requirements.md` |
| Design document lists "Attachment upload" as out of scope | Yes -- Section 4, Out of Scope table |
| The RADEMO1 design incorrectly stated appif needed no enhancements | Consistent with the gap described |

### Current State

- **appif version**: 1.1.0 (PyPI, released as 1.0.0 on 2026-03-13)
- **Read-side attachments**: Full support since v0.6.0 design
  - `ItemAttachment` model (metadata)
  - `AttachmentContent` model (metadata + bytes)
  - `WorkTracker.download_attachment()` protocol method
  - `JiraAdapter.download_attachment()` implementation
  - `WorkTrackingService.download_attachment()` routing
- **Write-side attachments**: Not implemented. No model, no protocol method,
  no adapter code, no service routing.

## 3. Product Decision

**Approve. This enhancement should proceed.**

### Rationale

1. **Blocking a downstream deliverable.** RADEMO1 D2.5 cannot be completed
   without this capability. The team has no reasonable workaround within the
   appif integration model.

2. **Natural, incremental extension.** The read-side pattern is already
   established. Adding the write-side is a symmetric completion of an
   existing feature, not a new architectural concern. The domain model
   (`ItemAttachment`), error hierarchy, and multi-instance routing all exist
   and apply unchanged.

3. **Small scope, low risk.** The Jira REST API endpoint is well-documented
   (`POST /rest/api/2/issue/{key}/attachments` with `multipart/form-data`).
   The `atlassian-python-api` library already exposes `add_attachment()`.
   This is adapter plumbing, not a domain design challenge.

4. **The original exclusion was a prioritization decision, not an
   architectural objection.** The requirements document (Out of Scope)
   grouped upload and download together. Download was later promoted to
   in-scope (v0.6.0). Upload was deferred because no consumer needed it at
   the time. A consumer now needs it.

5. **Platform-agnostic principle holds.** File attachment upload is a
   universal concept across all target platforms (Jira, GitHub, Linear,
   Azure DevOps). The domain interface can remain platform-agnostic.

### What This Is Not

- This is not a general "file management" feature. We are not adding
  delete-attachment, replace-attachment, or attachment versioning.
- This is not adding attachment streaming. The existing `AttachmentContent`
  pattern (fully-buffered bytes) applies symmetrically.
- This does not change the domain model. `ItemAttachment` already represents
  attachment metadata. The upload operation returns one.

## 4. Updated Requirements

The following requirement should be added to `requirements.md`:

### W13: Upload Attachments

The system must attach files to existing work items. The caller provides:

- The work item key
- The file content (as bytes or a filesystem path)
- The filename to use on the platform

The system must return the resulting `ItemAttachment` metadata for the
newly created attachment.

The operation must support the same multi-instance routing as all other
`WorkTracker` operations (optional `instance` parameter, default instance
fallback).

Platform-specific upload mechanics (multipart encoding, CSRF headers, size
limits) are adapter concerns and must not leak into the domain interface.

## 5. Request to Architect

This evaluation approves the enhancement. The next step per our workflow is
an architect design document.

**Requested deliverable**: Design document for W13 (Upload Attachments),
following the same structure and conventions as the existing
`docs/design/work_tracking/design.md`.

**Scope guidance for the architect**:

- Define the `WorkTracker` protocol method signature
- Define any new domain types needed (or confirm reuse of existing types)
- Specify the input contract (file path vs bytes vs both)
- Specify the return type
- Specify error cases and how they map to the existing error hierarchy
- State constraints (size limits, MIME handling, filename validation)
- Confirm this fits within the existing multi-instance routing architecture
- Explicitly state what is NOT in scope (delete, replace, streaming)

**Do not specify**: Library choices, HTTP implementation details, or adapter
internals. The implementing engineer will make those decisions within the
Jira adapter.

## 6. Priority and Sequencing

- **Priority**: High. This is the sole blocker for RADEMO1 D2.5.
- **Estimated impact**: Small. One new protocol method, one new adapter
  method, one new service routing method, associated tests.
- **Target release**: appif 1.2.0 (minor version bump -- additive API).
- **Sequencing**: Architect design -> checklist -> approval -> implementation.