# v0.7.0 Checklist: Projects as First-Class Domain Concept

**Goal**: Make projects a first-class domain concept with list, get, create, and delete operations.

---

## Domain Layer (Construction Order: Domain first)

- [ ] **1. `ProjectInfo` domain model** (`models.py`)
  - Frozen dataclass: `key`, `name`, `description`, `lead` (ItemAuthor | None), `project_type` (str), `url` (str)
  - Follows same pattern as `IssueTypeInfo`, `LinkTypeInfo`, `InstanceInfo`

- [ ] **2. `CreateProjectRequest` command object** (`models.py`)
  - Pydantic BaseModel(frozen=True): `key`, `name`, `project_type` (str, default "software"), `description` (str, default ""), `lead_account_id` (str | None, default None)
  - Validation: key must be uppercase alphanumeric

- [ ] **3. `ProjectNotFound` error** (`errors.py`)
  - Subclass of `WorkTrackingError`, takes `key` and optional `instance`
  - Follows `ItemNotFound` pattern

- [ ] **4. Add project methods to `WorkTracker` protocol** (`ports.py`)
  - `list_projects(*, instance) -> list[ProjectInfo]`
  - `get_project(key, *, instance) -> ProjectInfo`
  - `create_project(request, *, instance) -> ProjectInfo`
  - `delete_project(key, *, instance) -> None`

- [ ] **5. Export new types from `__init__.py`**
  - Add `ProjectInfo`, `CreateProjectRequest`, `ProjectNotFound` to imports and `__all__`

## Adapter Layer

- [ ] **6. `normalize_project()` function** (`_normalizer.py`)
  - Maps Jira project JSON dict -> `ProjectInfo` domain type
  - Fields: `key`, `name`, `description` (from Jira's description or ""), `lead` (from `lead.displayName` + `lead.accountId`), `project_type` (from `projectTypeKey`), `url` (from `self` link)

- [ ] **7. Implement project methods in `JiraAdapter`** (`adapter.py`)
  - `list_projects()`: Jira `GET /rest/api/2/project` via `self._client.projects()`
  - `get_project(key)`: Jira `GET /rest/api/2/project/{key}` via `self._client.project(key)`
  - `create_project(request)`: Jira `POST /rest/api/2/project` via `self._client.create_project()`
  - `delete_project(key)`: Jira `DELETE /rest/api/2/project/{key}` via HTTP call
  - Error mapping: 404 -> `ProjectNotFound`, 403 -> `PermissionDenied`, etc.

## Service Layer

- [ ] **8. Route project methods through `WorkTrackingService`** (`service.py`)
  - Four thin passthrough methods following existing pattern: `_resolve(instance).method(...)`
  - Import new types

## Tests

- [ ] **9. Unit tests for `normalize_project()`** (`test_jira_normalizer.py`)
  - Test with full Jira project dict
  - Test with minimal fields (missing lead, missing description)
  - Test URL extraction

- [ ] **10. Unit tests for domain models** (`test_domain_models.py`)
  - `ProjectInfo` frozen dataclass construction
  - `CreateProjectRequest` validation (valid, missing key, invalid key format)

- [ ] **11. Integration tests** (`test_jira_integration.py`)
  - `test_list_projects` -- live call, verify returns list of `ProjectInfo`
  - `test_get_project` -- live call with known project key
  - Note: create/delete are destructive admin ops, test manually or skip in automated suite

## Documentation

- [ ] **12. Update design.md** -- Add ProjectInfo to Section 5 canonical model table, add project methods to Section 6 protocol table, update scope in Section 3

- [ ] **13. Update technical_design.md** -- Add project normalizer mapping, adapter methods, error mapping for project operations

- [ ] **14. Update readme.md** -- Add project operations to feature list

## Release

- [ ] **15. Version bump to 0.7.0** (`pyproject.toml`, `__init__.py`)

- [ ] **16. CHANGELOG.md entry** for v0.7.0

- [ ] **17. Git commit and tag** -- `feat: add project CRUD as first-class domain concept`

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Methods on `WorkTracker` not `InstanceRegistry` | `WorkTracker` | Projects are operational concepts agents interact with, not admin setup |
| `ProjectInfo` not `Project` | Avoid collision with common names; consistent with `IssueTypeInfo`, `LinkTypeInfo` |
| `ProjectNotFound` vs reuse `ItemNotFound` | New error | Projects are not items; separate error aids diagnostics |
| `create_project` returns `ProjectInfo` not `ProjectIdentifier` | `ProjectInfo` | Projects have rich metadata worth returning immediately |
| No `update_project` in v0.7.0 | YAGNI | User asked for get, create, delete; update can come later |