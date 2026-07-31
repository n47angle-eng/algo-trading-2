"""Structured strategy.v1 validation errors for terminal-AI paste-back."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StrategyValidationIssue:
    """One validation failure in docs/05 error format.

    Rendered as: ``<path>: <what is wrong> — <how to fix>``
    """

    path: str
    message: str
    fix: str
    layer: str  # format | references | semantics | provenance

    def format_line(self) -> str:
        """Return the Owner/AI paste-back line."""
        return f"{self.path}: {self.message} — {self.fix}"


class StrategyValidationError(ValueError):
    """Raised when a strategy.v1 document fails one or more validation layers."""

    def __init__(self, issues: list[StrategyValidationIssue]) -> None:
        if not issues:
            msg = "StrategyValidationError requires at least one issue"
            raise ValueError(msg)
        self.issues = tuple(issues)
        super().__init__(self.format_report())

    def format_report(self) -> str:
        """Multi-line report; each line is paste-back ready."""
        return "\n".join(issue.format_line() for issue in self.issues)
