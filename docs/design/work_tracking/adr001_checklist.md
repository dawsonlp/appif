# ADR-001 Implementation Checklist: Item Categories with Adapter-Resolved Types

**ADR**: [001-adapter-resolved-issue-types](../../adr/001-adapter-resolved-issue-types.md)
**Version target**: 0.5.0 (breaking change)
**Date created**: 2026-03-01
**Date completed**: 2026-03-01

---

## Phase 1: Dependencies

- [x] Add `pydantic` to `pyproject.toml` `[project.dependencies]`
- [x] Run `uv pip install -e ".[dev]"` to verify resolution

## Phase 2: Domain Objects (models.py)

Construction order: domain objects first, no I/O, no infrastructure.

- [x] Add `ItemCategory` enum to `models.py` (TASK, SUBTASK, STORY, BUG, EPIC) with rich docstrings describing each member's intent for LLM and human consumers
- [x] Convert `CreateItemRequest` from `@dataclass(frozen=True)` to `pydantic.BaseModel` with `model_config = ConfigDict(frozen=True)`
- [x] Change `item_type: str` to `item_type: ItemCategory`
- [x] Add `model_validator` for cross-field rule: `parent_key` is required when `item_type is ItemCategory.SUBTASK`
- [x] `parent_key` allowed permissively for non-SUBTASK categories (supports platforms where any item can have a parent)
- [x] Verify remaining domain entities (`WorkItem`, `ItemComment`, `ItemLink`, etc.) stay as frozen dataclasses -- no changes

## Phase 3: Domain Exports (__init__.py)

- [x] Add `ItemCategory` to imports and `__all__` in `src/appif/domain/work_tracking/__init__.py`

## Phase 4: Domain Unit Tests

No mocks, no I/O. Direct tests of domain objects.

- [x] Test `ItemCategory` enum members exist and have expected values
- [x] Test `CreateItemRequest` accepts valid `ItemCategory` values
- [x] Test `CreateItemRequest` rejects raw strings for `item_type` (Pydantic validation error)
- [x] Test `CreateItemRequest` requires `parent_key` when `item_type=ItemCategory.SUBTASK`
- [x] Test `CreateItemRequest` accepts `parent_key=None` for non-SUBTASK categories
- [x] Test `CreateItemRequest` remains immutable (frozen) -- assignment raises error
- [x] Test `CreateItemRequest` optional fields default correctly (description, labels, priority, assignee_id)

## Phase 5: Adapter -- Type Resolution (JiraAdapter)

Infrastructure layer. Depends on Phase 2 domain objects being stable.

- [x] Add `_type_cache: dict[str, list[IssueTypeInfo]]` instance attribute to `JiraAdapter.__init__`
- [x] Add private method `_get_project_types(project_key: str) -> list[IssueTypeInfo]` that checks cache first, falls back to createmeta API call, caches result
- [x] Add private method `_resolve_issue_type(request: CreateItemRequest) -> str | None` implementing the mapping:
  - `TASK` -> `"Task"` (should always exist)
  - `STORY` -> `"Story"`, fallback `"Task"`
  - `BUG` -> `"Bug"`, fallback `"Task"`
  - `EPIC` -> `"Epic"`, fallback `"Task"`
  - `SUBTASK` -> auto-discover subtask type from project createmeta (type with `subtask=True`), fallback returns `None` (signals fallback strategy)
- [x] Update `JiraAdapter.create_item()` to call `_resolve_issue_type()` instead of using `request.item_type` directly
- [x] Implement SUBTASK fallback: when no subtask type available, create as `"Task"` and add `CHILD_OF` link to `parent_key`
- [x] Add logging: log resolved type name, log when fallback is used, log when subtask fallback to Task+link is used

## Phase 6: Adapter Unit Tests

- [x] Test `_resolve_issue_type()` for each `ItemCategory` with matching type available
- [x] Test `_resolve_issue_type()` fallback for STORY/BUG/EPIC when type not in project
- [x] Test `_resolve_issue_type()` SUBTASK resolution picks the `subtask=True` type
- [x] Test `_resolve_issue_type()` SUBTASK fallback returns `None` when no subtask type exists
- [x] Test `_type_cache` is populated on first call and reused on subsequent calls for same project
- [x] Test `_type_cache` fetches separately for different projects

## Phase 7: Integration Tests

- [x] Update existing integration tests to use `ItemCategory.TASK` instead of `"Task"` string
- [ ] Verify full create-read-update-transition-delete lifecycle still passes (requires live Jira instance)
- [ ] Add integration test: create item with `ItemCategory.SUBTASK` and valid `parent_key` (deferred to next session)
- [x] Discovery APIs (`get_project_issue_types`, `get_link_types`) unchanged -- no code modifications needed

## Phase 8: Quality and Release

- [x] Run `ruff check src/ tests/` -- clean
- [x] Run `ruff format src/ tests/` -- clean
- [x] Run full test suite: `pytest tests/unit -v` -- 290 passed
- [ ] Run integration tests: `pytest tests/integration -v -m integration` -- requires live Jira (deferred)
- [x] Bump version to `0.5.0` in `pyproject.toml` (breaking change: item_type is now ItemCategory)
- [ ] Commit with `feat!: replace item_type string with ItemCategory enum (ADR-001)`
- [ ] Tag `v0.5.0` and push

---

## Design Decisions Resolved

1. **parent_key for non-SUBTASK**: Allowed permissively. SUBTASK requires it; other categories may optionally include it.
2. **SUBTASK fallback link type**: Creates as Task + `CHILD_OF` link via existing `link_items()` method.
3. **Cache invalidation**: Per-instance cache with no TTL. Acceptable for short-lived adapter instances.

---

## Files Modified

| File | Change |
|------|--------|
| `pyproject.toml` | Add pydantic dependency, bump version to 0.5.0 |
| `src/appif/domain/work_tracking/models.py` | Add ItemCategory enum, convert CreateItemRequest to Pydantic |
| `src/appif/domain/work_tracking/__init__.py` | Export ItemCategory |
| `src/appif/adapters/jira/adapter.py` | Type resolution, cache, fallback logic |
| `tests/unit/test_domain_models.py` | New: 24 tests for ItemCategory and CreateItemRequest validation |
| `tests/unit/test_jira_type_resolution.py` | New: 15 tests for type resolution, fallback, and cache |
| `tests/integration/test_jira_integration.py` | Use ItemCategory enum instead of strings |