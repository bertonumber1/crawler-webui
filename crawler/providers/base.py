"""Provider protocol. Adding a store means adding one file here.

A provider does exactly two things: describe how to name a watch, and return
the releases currently visible for it. Everything else — dedupe, baselining,
notification, queueing — is the core's job, so providers stay dumb and small.
"""
from typing import Protocol, runtime_checkable
from ..models import Release


class ProviderError(Exception):
    pass


@runtime_checkable
class Provider(Protocol):
    name: str

    def resolve(self, ref: str) -> str:
        """Turn a user-supplied URL/id/slug into a display name. Raises
        ProviderError if the ref is not something this provider can watch."""
        ...

    def poll(self, ref: str, limit: int = 50) -> list[Release]:
        """Current releases for this ref, newest first."""
        ...

    def check(self, ref: str) -> list[tuple[str, bool, str]]:
        """Preflight for the Test button.
        Returns [(check_name, passed, detail), ...] — never raises."""
        ...


_REGISTRY: dict[str, Provider] = {}


def register(p: Provider):
    _REGISTRY[p.name] = p
    return p


def get(name: str) -> Provider:
    if name not in _REGISTRY:
        raise ProviderError(f"no provider named {name!r} (have: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def names() -> list[str]:
    return sorted(_REGISTRY)
