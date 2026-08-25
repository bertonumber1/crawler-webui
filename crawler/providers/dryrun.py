"""Offline provider backed by JSON fixtures. Touches no network at all.

Its job is to make the whole app demoable and testable before any real store
adapter exists: the UI, the queue state machine, the ownership gate and both
notification channels all get exercised end to end.

`advance()` reveals one more release from the fixture, which is how you
simulate "a new release appeared" without waiting for one to actually appear.
"""
import json, os
from ..models import Release
from .base import register, ProviderError

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "fixtures")


class DryRun:
    name = "dryrun"

    def _load(self, ref) -> dict:
        path = os.path.join(FIXTURES, f"{ref}.json")
        if not os.path.exists(path):
            raise ProviderError(f"no fixture {ref}.json in {FIXTURES}")
        with open(path) as f:
            return json.load(f)

    def resolve(self, ref) -> str:
        return self._load(ref).get("label", ref)

    def poll(self, ref, limit=50) -> list[Release]:
        data = self._load(ref)
        n = data.get("revealed", len(data["releases"]))
        out = []
        for r in data["releases"][:n][:limit]:
            out.append(Release(store=self.name, label=data.get("label", ref), **r))
        return out

    def advance(self, ref) -> int:
        """Reveal one more release. Returns the new revealed count."""
        path = os.path.join(FIXTURES, f"{ref}.json")
        data = self._load(ref)
        data["revealed"] = min(data.get("revealed", 0) + 1, len(data["releases"]))
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return data["revealed"]

    def check(self, ref) -> list:
        out = []
        try:
            data = self._load(ref)
            out.append(("fixture readable", True, f"{len(data['releases'])} releases"))
            out.append(("revealed", True, f"{data.get('revealed', 0)} of {len(data['releases'])}"))
        except Exception as e:
            out.append(("fixture readable", False, str(e)))
        return out


register(DryRun())
