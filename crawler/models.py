"""Normalised shapes shared by every provider.

Release deliberately mirrors bpdl/models.py so anything that already speaks
the bpdl Release shape can consume these without a translation layer.
"""
from dataclasses import dataclass, field, asdict


@dataclass
class Release:
    id: str = ""
    name: str = ""
    artists: list = field(default_factory=list)
    label: str = ""
    catalog_number: str = ""
    publish_date: str = ""
    track_count: int = 0
    url: str = ""
    image: str = ""
    store: str = ""

    def key(self) -> tuple:
        return (self.store, str(self.id))

    def title(self) -> str:
        a = ", ".join(self.artists) if self.artists else "Unknown"
        return f"{a} - {self.name}"

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Watch:
    id: int = 0
    store: str = ""
    ref: str = ""
    name: str = ""
    notify: int = 1
    auto_queue: str = "off"      # off | wishlist | download
    muted: int = 0
    poll_interval: int = 1800
    last_poll: str = ""
    last_ok: str = ""
    last_error: str = ""


@dataclass
class Item:
    """A crawled thing: an article, a forum post, a thread."""
    id: str = ""
    name: str = ""
    url: str = ""
    author: str = ""
    published: str = ""
    kind: str = "item"           # article | post | thread | release
    summary: str = ""
    links: list = field(default_factory=list)   # [{"url":..,"host":..,"bucket":..}]
    store: str = ""

    def title(self) -> str:
        return self.name or self.url

    def dict(self) -> dict:
        return asdict(self)
