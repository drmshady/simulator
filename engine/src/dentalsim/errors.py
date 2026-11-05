"""Exceptions shared by the engine, the command line and the studio."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class Refused(Exception):
    """The engine declined to act: an invalid assembly, a missing benchmark, a failed checklist.

    The command line maps it to exit code 1 (contracts/cli.md). Its fields follow the studio error shape
    (contracts/studio-api.md), so the studio can return it unchanged.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "refused",
        problems: Sequence[Mapping[str, Any]] = (),
        missing: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.problems = [dict(p) for p in problems]
        self.missing = [dict(m) for m in missing]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "problems": self.problems, "missing": self.missing}
