"""Trusted report scope used to build app predicates without arbitrary SQL."""

from dataclasses import dataclass
import re

ALIAS_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Scope:
    app_id: int | None = None

    @classmethod
    def all(cls) -> "Scope":
        return cls()

    @classmethod
    def for_app(cls, app_id: int) -> "Scope":
        if app_id <= 0:
            raise ValueError("app_id must be positive")
        return cls(app_id=app_id)

    def predicate(self, alias: str | None = None) -> tuple[str, tuple]:
        if alias is not None and not ALIAS_RE.fullmatch(alias):
            raise ValueError(f"Invalid SQL alias {alias!r}")
        column = f"{alias}.app_id" if alias else "app_id"
        if self.app_id is None:
            return "true", ()
        return f"{column} = %s", (self.app_id,)
