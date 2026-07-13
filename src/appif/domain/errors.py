"""Cross-cutting error base for the appif ecosystem.

Every adapter error descends from ``AppifError``, so callers can catch at
whatever granularity they need:

    except AppifError        -- catches everything
    except ConnectorError    -- messaging errors only (appif.domain.messaging.errors)
    except WorkTrackingError -- work-tracking errors only (appif.domain.work_tracking.errors)

Each domain owns its own concrete error types; this module only defines the
shared root they inherit from.
"""

from __future__ import annotations


class AppifError(Exception):
    """Base error for all appif errors."""
