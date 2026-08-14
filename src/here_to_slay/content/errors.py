"""Path-qualified content errors.

Every problem a modder can create is reported as a :class:`ContentIssue` with a
path that points at the offending YAML node, e.g.::

    data/base/cards/heroes.yaml[3].ability.roll.outcomes[1].effect.op

A ``KeyError`` twenty minutes into a game is a bug in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True, order=True)
class ContentIssue:
    """One problem found in a content pack."""

    path: str
    message: str
    severity: Severity = Severity.ERROR
    hint: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def __str__(self) -> str:
        text = f"{self.path}: {self.message}"
        if self.hint:
            text += f" ({self.hint})"
        return text


class ContentError(Exception):
    """Raised when a pack cannot be loaded or fails validation.

    Carries every issue found, not just the first — a modder should be able to
    fix a whole file in one pass.
    """

    def __init__(self, issues: list[ContentIssue] | ContentIssue, message: str = "") -> None:
        self.issues: list[ContentIssue] = [issues] if isinstance(issues, ContentIssue) else issues
        self.message = message or f"{len(self.issues)} content issue(s)"
        super().__init__(self.message)

    @property
    def errors(self) -> list[ContentIssue]:
        return [i for i in self.issues if i.is_error]

    @property
    def warnings(self) -> list[ContentIssue]:
        return [i for i in self.issues if not i.is_error]

    def __str__(self) -> str:
        return "\n".join([self.message, *(f"  {issue}" for issue in self.issues)])
