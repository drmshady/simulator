"""Setting values (data-model.md "SettingValue", FR-052).

- A **design** setting is chosen freely within the block's limits: sizes, positions, angles, counts and choices.
- A **library** setting points to an approved literature-entry version and records how its value was chosen.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

CHOICE = re.compile(r"^(typical|custom|source:\S+)$")

DesignScalar = StrictBool | StrictInt | StrictFloat | StrictStr


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesignValue(_Model):
    kind: Literal["design"] = "design"
    value: DesignScalar | list[StrictInt | StrictFloat] | list[StrictStr]


class LibraryValue(_Model):
    """`choice` is `typical`, `source:<source id>` or `custom`. `value` is the resolved number.

    `outside_range` is true when the value lies outside the entry's published range; such a run cannot be
    published (FR-052).
    """

    kind: Literal["library"] = "library"
    entry_id: str = Field(min_length=1)
    entry_version: int = Field(ge=1)
    choice: str
    value: float
    outside_range: bool = False

    @field_validator("choice")
    @classmethod
    def _choice(cls, value: str) -> str:
        if not CHOICE.match(value):
            raise ValueError(f"choice must be 'typical', 'source:<id>' or 'custom', not {value!r}")
        return value

    @property
    def source_id(self) -> str | None:
        return self.choice.removeprefix("source:") if self.choice.startswith("source:") else None


SettingValue = Annotated[DesignValue | LibraryValue, Field(discriminator="kind")]
