"""Model builders of the verification cases, found by the name a case file's `model.builder` gives."""

from __future__ import annotations

from dentalsim.errors import DentalsimError
from dentalsim.verification.builders.textbook import BUILDERS, Builder, BuiltCase

__all__ = ["BUILDERS", "Builder", "BuiltCase", "builder_for"]


def builder_for(name: str) -> Builder:
    try:
        return BUILDERS[name]
    except KeyError:
        known = ", ".join(sorted(BUILDERS))
        raise DentalsimError(f"there is no verification model builder {name!r}; the known ones are {known}") from None
