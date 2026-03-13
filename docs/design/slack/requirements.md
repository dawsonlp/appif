# Slack Connector Requirements

**Author**: Product Management
**Date**: 2026-03-07
**Status**: Approved
**Version**: 2.0

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | 2026-03-07 | Revised per architect review. Adopted one-identity model, removed dual-token/dual-instance concepts, rewrote acceptance criteria as outcomes, reframed R3 and R6, removed R9 |
| 1.0 | 2026-03-07 | Initial approved version |

---

## Context

The Slack connector provides programmatic, normalized access to a Slack workspace. It authenticates as a single entity — the identity determined by the credential it is given — and provides access to everything that identity can see, send to, and do.

The connector is a transport adapter. It does not reason about content, store meaning, or decide importance. It connects, normalizes inbound events, delivers outbound messages, and reports its capabilities honestly.

Different identities have different visibility and permissions. Capabilities reflect what the authenticated identity can actually do. If a consumer needs multiple perspectives on the same workspace, it constructs multiple connectors — that composition choice belongs to the consumer, not the connector.

---

## Assumed Stable

The following capabilities exist in the current implementation and are considered stable for this requirements cycle. They are not in scope for new work. Regressions are treated as bugs.

- Thread support (reply threading)
- Rate limiting and retry behavior
- Reconnection on disconnect
- User identity resolution and caching

---

## Requirements

### R1: Real-time event delivery

The connector must deliver real-time message events to consumers as they occur. A consumer must be able to receive normalized events through the established connector protocol without building its own polling loop.

**Acceptance criteria:**
- A consumer receives events as they occur, as normalized domain objects
- Event delivery works through the existing connector protocol
- The interface supports both programmatic consumers and test tooling
- No dependency on CLI frameworks or terminal rendering

### R2: Standalone event streaming verification

Event streaming must be independently verifiable without the CLI. A standalone script must demonstrate that real-time events are received, normalized, and delivered correctly.

**Acceptance criteria:**
- A verification script exists in `scripts/`
- The script connects, receives events, and prints them to stdout
- Output is machine-readable (normalized event fields: timestamp, author, channel, body)
- The script exits after a configurable number of events or timeout
- No dependency on CLI frameworks

### R3: Connector works for all supported identity types

The connector must function when authenticated as any supported Slack identity — not only as a bot. Visibility, send capability, and real-time availability are consequences of the identity, not special modes.

**Acceptance criteria:**
- The connector can be constructed and used with any supported credential type
- Backfill and channel listing work for identities that have read access
- Operations unavailable to the authenticated identity are clearly communicated through capabilities and errors
- No identity type is treated as a second-class configuration

### R4: Capabilities reflect authenticated identity

The connector's reported capabilities must accurately reflect what the authenticated identity can do. Consumers must be able to query capabilities to understand what operations are available.

**Acceptance criteria:**
- Capabilities accurately reflect the identity's actual permissions
- Capabilities are queryable before calling `connect()`
- The capability model is sufficient to distinguish between identities with different permissions

### R5: Error categories are distinguishable

When operations fail, the connector must communicate failures in a way that allows consumers to distinguish between categories: authorization failures, target unavailability, and transient errors.

**Acceptance criteria:**
- A consumer can distinguish authorization failures from availability failures from transient failures
- Error information is sufficient for a consumer to decide whether to retry, report, or abandon
- Error behavior is consistent across operations

### R6: Construction and graceful degradation

The connector requires exactly one authentication credential at construction time. If the platform requires additional transport credentials for specific capabilities (such as real-time event delivery), their absence degrades capability gracefully rather than preventing construction.

**Acceptance criteria:**
- The connector can be constructed with a single authentication credential
- Missing transport credentials result in reduced capabilities, not construction failure
- Capabilities accurately reflect what is available after construction

### R7: Setup documentation covers all identity types

The setup guide must include instructions for obtaining and configuring credentials for all supported identity types, not only the bot identity.

**Acceptance criteria:**
- Documents credential acquisition for each supported identity type
- Documents the required configuration (environment variables, scopes)
- Explains the practical differences between identity types (visibility, permissions)

### R8: Docstrings support external consumers

The Slack connector is a published library consumed by external developers. Public classes and methods must have docstrings sufficient for an agent developer to use the connector without reading the source code.

**Acceptance criteria:**
- An agent developer can understand how to instantiate, connect, and use the connector from docstrings alone
- Error conditions and capability constraints are documented
- The connector's role as a transport adapter is clear from the documentation

### R9: Design documentation

The design document must describe:

1. How the connector handles different identity types and what each provides
2. What the connector produces (normalized events, delivery confirmations, capabilities)
3. The interface contract consumers depend on

**Acceptance criteria:**
- Design doc explains identity types and resulting capabilities without prescribing consumer composition
- Documents the event and send interfaces
- Documents the capability model
- Does not prescribe what consumers do with connector outputs

---

## Out of scope

- The connector does not implement Kafka producers or consumers
- The connector does not interact with Mimir or any knowledge store
- The connector does not make decisions about message content or routing
- How consumers compose multiple connectors is not the connector's concern
- Interactive components (buttons, modals, slash commands)
- File upload/download
- Attachment content retrieval

---

## Priority

| Requirement | Priority | Rationale |
|-------------|----------|-----------|
| R1 | High | Consumers cannot receive events without this |
| R2 | High | Cannot verify R1 works without this |
| R3 | High | Consumer agents require non-bot identity support |
| R4 | Medium | Consumers need capabilities, but can work around |
| R5 | Medium | Error handling improves reliability, not blocking |
| R6 | High | Construction model must support all identity types |
| R7 | Medium | Setup is manual, can be communicated informally initially |
| R8 | Medium | Improves developer experience for published library |
| R9 | High | Design guidance needed before implementation |