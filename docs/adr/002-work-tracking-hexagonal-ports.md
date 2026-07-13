# ADR-002: Keep the Hexagonal Shape for Work Tracking (Driven Port + Composition Factory)

**Status**: Accepted
**Date**: 2026-07-13
**Deciders**: ldawson
**Applies to**: Work Tracking Domain (appif, post-1.5.0)

---

## Context

`WorkTrackingService` lives in the domain (`domain/work_tracking/service.py`) but
imported and constructed the concrete Jira adapter directly:

```python
from appif.adapters.jira._auth import load_config      # config I/O
from appif.adapters.jira.adapter import JiraAdapter     # concrete adapter

self._adapters: dict[str, JiraAdapter] = {}             # typed to the concrete class
...
if platform == "jira":
    adapter = JiraAdapter(server_url, credentials, instance_name=name)
```

This inverts the intended dependency direction. In a hexagonal (ports-and-adapters)
architecture the domain defines ports and adapters depend on the domain — the
arrow points inward. Here the Jira adapter correctly implements the domain's
protocols *and* the domain constructs the Jira adapter, forming a cycle
(domain ↔ adapter).

**Concrete problems this caused:**

1. **A dependency cycle.** The `WorkTracker` port existed but the service never
   routed through it — it stored `JiraAdapter` and hard-coded `platform == "jira"`.
   The port was decorative; the abstraction wasn't real.
2. **The domain wasn't type-checkable in isolation.** Scoping `mypy` to
   `src/appif/domain` still followed the import into the Jira adapter, dragging in
   errors from untyped third-party SDKs (`yaml`, `atlassian-python-api`). We had
   to paper over it with `follow_imports = "silent"`.
3. **Config I/O in the domain.** `auto_load=True` read a Jira YAML file from the
   domain layer — infrastructure concern in the wrong place.

The messaging side does not have this problem: there is no central service, the
caller constructs `GmailConnector()` etc. directly, and the domain only defines
`Connector`. Work tracking was the outlier.

### The KISS tension

Only **one** work-tracking platform (Jira) is implemented. A swappable backend
*port* plus a routing *service* plus a *composition factory* is more machinery
than a single hard-wired adapter would need. Judged purely by KISS (keep it
simple), the abstraction is ceremony for a codebase with one adapter.

We weighed that against the architectural goal and decided the **architectural
shape matters more here** than minimizing moving parts.

---

## Decision

Invert the dependency and make the port explicit. Accept the extra structure as a
deliberate, documented trade against KISS.

### 1. Add an explicit driven port — `WorkTrackerBackend`

`domain/work_tracking/ports.py` gains `WorkTrackerBackend`: the per-instance
operations an adapter implements (no `instance` routing), plus read-only
`platform` / `server_url`. This is the port adapters plug into. `WorkTracker`
(with `instance=` routing) and `InstanceRegistry` remain the *driver-side*
interfaces the application uses; `WorkTrackingService` implements those.

### 2. The service depends only on the port

`WorkTrackingService` stores `dict[str, WorkTrackerBackend]` and
`register(name, backend, *, make_default=False)` takes a constructed backend. It
imports nothing from `appif.adapters`. The first registered backend becomes the
default; `make_default=True` overrides.

### 3. Composition moves to the adapter layer

`appif.adapters.jira.create_work_tracking_service(auto_load=True)` is the
composition root: it constructs `JiraAdapter` instances (reading YAML config when
`auto_load` is set) and registers them. It is the only place that knows both the
domain service and the concrete adapter — and the adapter layer is allowed to
depend on the domain.

Programmatic use stays simple:

```python
service = WorkTrackingService()
service.register("prod", JiraAdapter(url, creds), make_default=True)
```

---

## Consequences

**Positive**

- Dependency arrow points inward again; the domain imports no adapter.
- The domain type-checks in isolation — `follow_imports = "silent"` was removed
  and `mypy src/appif/domain` is now naturally hermetic and gated in CI.
- Config I/O left the domain.
- A second platform, or a fake backend in tests, plugs in by implementing
  `WorkTrackerBackend` — no change to the domain.

**Negative / accepted cost**

- More structure (an extra protocol + a factory) for a currently single-platform
  feature. This is the KISS cost we consciously accepted; it is only justified
  while the hexagonal shape is a project goal. If work tracking were ever
  deliberately narrowed to "Jira only, forever," collapsing the service into the
  adapter would be the simpler choice.

**Breaking API change**

- `WorkTrackingService(auto_load=...)` and
  `register(name, platform, server_url, credentials)` are removed.
- Programmatic callers now construct the adapter and call
  `register(name, backend, *, make_default=...)`.
- YAML auto-loading moves to `appif.adapters.jira.create_work_tracking_service`.
