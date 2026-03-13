# Architect Review: Slack Connector Requirements v1.0

**Author**: Chief Systems Architect
**Date**: 2026-03-07
**Status**: Review feedback for Product Management
**Reviewing**: `docs/design/slack/requirements.md` v1.0

---

## Scope of Review

Reviewed the requirements document against:

- Existing design document (`design.md`)
- Current `Connector` protocol and domain models (`ports.py`, `models.py`, `errors.py`)
- Current `SlackConnector` implementation (`connector.py`)
- Setup guide (`setup.md`)
- Project document separation rules (RULES.md)

Seven findings follow, ordered by significance.

---

## Findings

### 1. Identity model: one connector = one identity

**Updated after discussion with PM (2026-03-07).**

The requirements Context section introduces a two-connector-instance usage pattern (user-token instance for reading, bot-token instance for sending). The existing design document (Section 7) describes a single connector that holds both token types and juggles them per operation. Both approaches are wrong.

The correct model, confirmed by PM, is simpler: **a connector authenticates as exactly one identity.** Give it a bot token and it is the bot — it sees what the bot sees, sends as the bot, and reports capabilities honestly. Give it a user token and it is the user — it sees what the user sees, and capabilities reflect what that token allows.

The connector does not combine identities, switch between tokens, or try to be two things. If a consumer wants both perspectives (read as user, send as bot), they construct two separate connectors. That is the consumer's composition choice, not the connector's concern.

This has implications across the requirements and design:

- **Requirements Context section**: Remove the dual-instance usage pattern. Describe the connector as authenticating a single entity and reporting its capabilities honestly.
- **R3 ("User-token mode")**: This is not a "mode." If the connector is constructed with a user token, it IS a user. There is no mode switch. The requirement should be reframed as: the connector must work when authenticated as a user, not only when authenticated as a bot.
- **R4 (Capabilities)**: Capabilities are a consequence of identity, not configuration. This follows naturally.
- **Design.md Section 7 (Authentication)**: Must be rewritten. The current dual-token composition model inside a single connector contradicts this principle.
- **Constructor**: Should accept a single authentication credential (one token, one identity). The app-level token (`xapp-`) for Socket Mode is a transport detail, not an identity — how it is provided is a design decision.

**Recommendation**: Requirements should describe identity-agnostic behavior: authenticate, read what you can see, send if you can, report capabilities honestly. All dual-token mechanics, token-type distinctions, and composition patterns should be removed from the requirements entirely. The design document will address Slack-specific token mechanics as internal adapter concerns.

---

### 2. Several acceptance criteria prescribe mechanism, not need

The PM's own principle — "this sounds like policy not mechanism" — is violated in several places:

| Requirement | Prescriptive language | Underlying need |
|---|---|---|
| R1 AC | "async iterator, callback, or queue" | Consumers receive events without building their own polling loop |
| R1 AC | "without polling or manual pull" | Low-latency event delivery |
| R2 AC | "Feed a `.json` file of raw Slack events" | Testable without a live workspace |
| R3 AC | "`send_message()` raises `NotSupported`" | Sending is clearly unavailable in user-token-only mode |
| R4 AC | "`supports_send=False`, `supports_realtime=False`" | Capabilities accurately reflect what the instance can do |
| R5 AC | Names specific error types (`NotAuthorized`, `TargetUnavailable`, `TransientFailure`) | Failures are distinguishable by category |

**Recommendation**: Rewrite acceptance criteria as observable outcomes. Move mechanism choices (specific error types, interface shapes, field values) to the design document where the architect and engineers can select the right approach.

---

### 3. R1's event streaming interface already exists in the protocol

The `Connector` protocol already defines:

```
connect(*, listener: MessageListener | None = None) -> None
```

where `MessageListener` has an `on_message(event: MessageEvent)` method.

The gap is not a missing interface — it is that the `SlackConnector` implementation buffers events into a plain list instead of calling the provided listener. R1 as written implies the interface needs to be invented, when in fact the implementation needs to honor the existing protocol.

**Recommendation**: Reframe R1 as "the connector's real-time event delivery must work end-to-end through the existing listener interface" or simply "real-time events must be delivered to consumers." The design and implementation will close the gap.

---

### 4. Missing requirements for existing capabilities

Several capabilities present in the design document and implementation have no corresponding requirement. The PM should confirm whether these are intentionally deferred or assumed:

| Capability | Present in design/code | In requirements |
|---|---|---|
| Thread support (reply threading) | Yes — `thread_ts` in backfill, normalizer | Not mentioned |
| Rate limiting behavior | Yes — `_rate_limiter.py`, design Section 9 | Not mentioned |
| Attachment handling | Yes — domain `Attachment` model | Not mentioned |
| Reconnection on disconnect | Yes — design Section 3 scope | Not mentioned |
| User identity caching | Yes — `_user_cache.py` | Not mentioned |

If these are considered working and stable, no requirement is needed. If they need verification or enhancement as part of this cycle, they should be captured.

---

### 5. R6 needs reframing under the one-identity model

R6 says "never raise on construction due to a missing optional token." Under the one-identity model (Finding 1), this becomes simpler: the connector requires exactly one authentication token at construction. That token determines the identity and, consequently, the capabilities.

The app-level token (`xapp-`) is not an identity token — it is a transport credential for Socket Mode. Whether it is a separate constructor parameter or bundled with the authentication credential is a design decision, not a requirement.

**Recommendation**: R6 should state: "the connector requires exactly one authentication token at construction time" and "if real-time event delivery requires additional platform credentials, their absence degrades capability gracefully rather than preventing construction." The design document will specify that the `xapp-` token enables Socket Mode and its absence means `supports_realtime=False`.

---

### 6. R9 does not belong in a requirements document

R9 ("Publish updated library to PyPI") is a release management task, not a functional or non-functional requirement of the connector. It describes operational process, not what the system must do.

**Recommendation**: Remove R9 from requirements. Track it on a release checklist.

---

### 7. Minor items

- **R7 (setup docs)**: Acceptance criteria mention adding user token steps, but neither the requirements nor the current setup guide specify which OAuth scopes the user token needs. The design should enumerate required scopes before the setup guide can be written.
- **R8 (docstrings)**: Code documentation standards are a standing team practice. Including them as a per-feature requirement is unusual. Consider whether this belongs in the requirements or in a team definition of done.

---

## Summary of Actions Requested

| # | Action | Owner |
|---|---|---|
| 1 | Adopt one-connector-one-identity model: remove dual-token and dual-instance concepts from requirements | PM |
| 2 | Rewrite acceptance criteria as outcomes, move mechanism choices to design | PM |
| 3 | Reframe R1 to acknowledge existing `MessageListener` protocol | PM |
| 4 | Confirm whether thread support, rate limiting, attachments, reconnection, user cache are in scope | PM |
| 5 | Reframe R6: one authentication token required, transport credentials degrade gracefully | PM |
| 6 | Remove R9 from requirements | PM |
| 7 | Specify user token OAuth scopes (R7); decide if R8 belongs here or in definition of done | PM |

Once we have alignment on these points, I will update the design document to reflect the one-identity model, rewrite the authentication section, and address event delivery through the existing listener protocol.
