"""Cross-cutting credential provider protocol.

Adapters depend on credentials but should not load them directly. The
CredentialProvider protocol separates structural validation (are the keys
present?) from network authentication (does the service accept them?).
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar

T = TypeVar("T", covariant=True)


class CredentialProvider(Protocol[T]):
    """Protocol for providing validated credentials to an adapter.

    The generic parameter T represents the credential type the adapter
    expects (e.g., str for an API key, a ServiceCredentials dataclass
    for username/password, an OAuth token object, etc.).

    validate() performs structural checks only -- no network I/O.
    get_credentials() returns the credential object.
    """

    def validate(self) -> None:
        """Check that required credentials are present and structurally valid.

        Raises on failure. The specific exception type is chosen by the
        implementation (typically CredentialError).

        Must not perform network I/O.
        """
        ...

    def get_credentials(self) -> T:
        """Return the credential object.

        May perform lazy loading (read file, parse config). Should not
        perform network authentication -- that is the adapter's
        responsibility during connect().
        """
        ...