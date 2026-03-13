# ADR-001: Domain-Level Item Categories with Adapter-Resolved Issue Types

**Status**: Accepted
**Date**: 2026-03-01
**Deciders**: ldawson
**Applies to**: Work Tracking Domain (appif v0.4.0+)

---

## Context

`CreateItemRequest.item_type` is a raw string (`str`) that callers must set
to a platform-specific value such as `"Sub-task"`, `"Task"`, or `"Story"`.
This leaks platform concerns into calling code and contradicts the design
document's constraint that no platform-specific types, field names, or
conventions appear in the domain model.

**Problems observed during the solver framework E2E run:**

1. The solver hardcoded `item_type="Sub-task"` which worked for one Jira
   project but failed for another where the subtask type had a different name.
2. When `parent_key` is set, the caller's intent is already clear (create a
   child item), but they must redundantly guess the correct `item_type` string.
3. Different Jira projects configure different issue type schemes. The caller
   cannot portably express "create a task" without knowing the target project's
   configuration.

The work tracking domain already solved this problem for relationships:
callers say `LinkType.BLOCKS` (domain vocabulary) and the Jira adapter
translates it to the platform string `"Blocks"`. Issue types should follow
the same pattern.

---

## Decision

### 1. Introduce `ItemCategory` enum

A domain-level vocabulary for work item types, independent of any platform's
naming:

```
TASK      -- A unit of work (default for most creation)
SUBTASK   -- A child unit of work under a parent item
STORY     -- A user-facing feature or requirement
BUG       -- A defect report
EPIC      -- A large body of work that contains other items
```

This is the same architectural pattern as `LinkType`.

### 2. Change `CreateItemRequest.item_type` from `str` to `ItemCategory`

The field remains **required**. Callers express intent in domain vocabulary.
The adapter translates to platform-specific strings.

### 3. Convert `CreateItemRequest` to Pydantic `BaseModel`

Command objects (requests) benefit from input validation, JSON schema
generation, and structured error messages. Domain entities (WorkItem,
ItemComment, etc.) remain frozen dataclasses -- they are normalized output,
not user input.

Pydantic provides:
- Enum validation with clear error messages
- Cross-field validation (`parent_key` required when `item_type=SUBTASK`)
- `model_json_schema()` for OpenAPI and LLM consumption
- Serialization from dicts and JSON

### 4. Adapter resolves platform-specific issue type names

Each adapter maps `ItemCategory` to the target platform's type system:

| ItemCategory | Jira (typical) | Jira (fallback) |
|-------------|----------------|-----------------|
| `TASK` | "Task" | First non-subtask type |
| `SUBTASK` | Project's subtask type (auto-discovered) | Create as Task + CHILD_OF link |
| `STORY` | "Story" | "Task" |
| `BUG` | "Bug" | "Task" |
| `EPIC` | "Epic" | "Task" |

For `SUBTASK`, the Jira adapter queries the project's available issue types
(via createmeta), finds the type with `subtask=True`, and uses it. Results
are cached per project for the adapter's lifetime. If no subtask type exists,
the adapter creates a standard Task and links it to the parent with
`CHILD_OF`.

### 5. Discovery APIs remain available

`get_project_issue_types()` and `get_link_types()` are retained for
administrative tools, dashboards, and debugging. They are not required
for item creation.

---

## Rationale

### This is platform translation, not business logic

The design document states the domain "does not interpret priority, infer
relationships, or decide workflow transitions." Type resolution is none
of these. It is platform translation -- the same category as:

- `link_items()` mapping `LinkType.BLOCKS` to Jira's `"Blocks"` string
- The normalizer mapping Jira's `"issuetype": {"name": "Sub-task"}` to
  `item_type: "sub-task"`
- Error translation mapping HTTP 404 to `ItemNotFound`

### Required enum is better than optional string

An optional `item_type` field creates ambiguity (when do I provide it?
what happens if I don't?). A required enum with a small, documented
vocabulary is unambiguous and self-documenting. LLMs and humans both
benefit from constrained choices over open strings.

### Pydantic for command objects, dataclasses for entities

Command objects are user-facing input that benefits from validation.
Domain entities are adapter-produced output that is already normalized.
Mixing the two technologies at this boundary is intentional -- each
tool is used where it adds value.

---

## Consequences

**Positive:**

- Callers express intent in domain vocabulary, not platform strings
- Agents become portable across differently-configured projects
- Input validation catches errors before they reach the platform API
- JSON schema available for OpenAPI and LLM tool definitions
- Consistent with the `LinkType` pattern already established

**Negative:**

- Breaking change: `item_type` goes from `str` to `ItemCategory` (acceptable at 0.x)
- Adapter makes an extra API call (createmeta) on first `SUBTASK` creation
  per project (cached)
- Fallback strategies (e.g., Task + CHILD_OF link instead of native subtask)
  may produce different Jira structures depending on project configuration

**Neutral:**

- Pydantic added as a validation tool for command objects only; domain
  entities remain dataclasses
- Discovery APIs unchanged and still available

---

## Future Considerations

If the deterministic mapping proves insufficient for projects with exotic
type hierarchies, a `TypeResolver` strategy protocol could be introduced:

```python
class TypeResolver(Protocol):
    def resolve(
        self,
        category: ItemCategory,
        available_types: list[IssueTypeInfo],
    ) -> str: ...
```

The adapter would accept a resolver at construction. The default is the
deterministic mapper described above. An LLM-assisted resolver could be
provided for edge cases, keeping non-determinism opt-in and at the edge.

This is not planned for v0.4.0.

---

## Affected Components

| Component | Change |
|-----------|--------|
| `models.py` | Add `ItemCategory` enum; convert `CreateItemRequest` to Pydantic `BaseModel` |
| `JiraAdapter.create_item()` | Map `ItemCategory` to Jira type string with fallback |
| `WorkTracker` protocol | No change (already accepts `CreateItemRequest`) |
| `WorkTrackingService` | No change (pass-through routing) |
| `design.md` | Updated to reflect `ItemCategory` and Pydantic usage |
| Unit/integration tests | Updated for enum-based API |

---

## References

- [Work Tracking Design Document](../design/work_tracking/design.md) -- Section 5 (updated)
- [Work Tracking Technical Design](../design/work_tracking/technical_design.md)