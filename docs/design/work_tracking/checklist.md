# Work Tracking Domain: Implementation Checklist

**Prerequisites**: [Requirements](requirements.md) | [Design](design.md) | [Technical Design](technical_design.md)

---

## Phase 1: Domain Layer (no I/O, no platform types)

- [x] `src/appif/domain/work_tracking/__init__.py` -- public exports
- [x] `src/appif/domain/work_tracking/models.py` -- frozen dataclasses:
  `LinkType`, `ItemAuthor`, `ItemComment`, `ItemLink`, `WorkItem`,
  `ItemIdentifier`, `CreateItemRequest`, `TransitionInfo`, `SearchCriteria`,
  `SearchResult`, `InstanceInfo`
- [x] `src/appif/domain/work_tracking/errors.py` -- exception hierarchy:
  `WorkTrackingError`, `ItemNotFound`, `PermissionDenied`, `InvalidTransition`,
  `ConnectionFailure`, `RateLimited`, `InstanceNotFound`, `NoDefaultInstance`,
  `InstanceAlreadyRegistered`
- [x] `src/appif/domain/work_tracking/ports.py` -- protocol classes:
  `InstanceRegistry`, `WorkTracker`

## Phase 2: Domain Unit Tests

- [ ] `tests/unit/test_work_tracking_models.py` -- construction, defaults,
  immutability, LinkType enum
- [ ] `tests/unit/test_work_tracking_errors.py` -- message formatting,
  instance field, hierarchy

> Deferred: Integration tests against live Jira provide stronger coverage
> than unit tests with mocks. Domain models and errors are exercised
> transitively through the integration test CRUD lifecycle.

## Phase 3: Jira Adapter

- [x] `src/appif/adapters/jira/__init__.py` -- public exports
- [x] `src/appif/adapters/jira/_auth.py` -- YAML config loading,
  `atlassian.Jira` client creation
- [x] `src/appif/adapters/jira/_normalizer.py` -- Jira REST API dicts to
  domain `WorkItem`, `ItemComment`, `TransitionInfo`
- [x] `src/appif/adapters/jira/adapter.py` -- `JiraAdapter` (concrete class,
  includes error translation, JQL builder)

**Library choice**: `atlassian-python-api` v4.0+ (dict-based API, actively
maintained, covers Jira + Confluence + Bitbucket). Changed from `jira`
(pycontribs) during implementation.

## Phase 4: Jira Adapter Unit Tests

- [ ] `tests/unit/test_jira_normalizer.py`
- [ ] `tests/unit/test_jira_adapter.py`

> Deferred: Same rationale as Phase 2. Integration tests exercise the full
> adapter stack against real Jira. No mocks.

## Phase 5: WorkTrackingService (thin routing layer)

- [x] `src/appif/domain/work_tracking/service.py` -- implements
  `InstanceRegistry` + `WorkTracker`, routes to adapters, auto-loads
  instances from YAML config at `~/.config/appif/jira/config.yaml`

## Phase 6: Service Unit Tests

- [ ] `tests/unit/test_work_tracking_service.py` -- register/unregister,
  default management, instance resolution, delegation

> Deferred: Same rationale. Service routing is verified by integration tests.

## Phase 7: Integration Tests

- [x] `tests/integration/test_jira_integration.py` -- live Jira CRUD
  lifecycle (8 tests, all passing):
  1. Create a task with labels
  2. Retrieve item, verify all domain fields populated
  3. Add comment, verify it appears on re-fetch
  4. Create second item, link as "blocks", verify relationship
  5. List available transitions
  6. Execute a transition, verify status change
  7. Search by project + labels, verify both items found
  8. Record created keys for cleanup

- [x] `scripts/jira_cleanup.py` -- delete test tickets (supports
  `--dry-run` and `--key` flags)

## Phase 8: Project Configuration

- [x] `pyproject.toml` -- added `atlassian-python-api>=4.0` and `pyyaml>=6.0`
- [x] `.env.example` -- documented YAML config format and `APPIF_JIRA_CONFIG` override
- [x] `ADAPTERS.md` -- full Jira adapter documentation with domain models,
  operations, config format, and test instructions
- [x] `~/.config/appif/jira/config.yaml` -- multi-instance config
  (personal + highspring)