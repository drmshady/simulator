"""Prosthesis templates (contracts/scenario-format.md "Template contract"; data-model.md "ProsthesisTemplate").

A template is a scenario with its library settings left open: each names a parameter key and how to choose its
value (`typical` by default), and the current approved version is resolved when the template is opened. Built-in
templates are YAML files next to this module; owner templates are saved scenarios, stored in the database.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dentalsim.model.scenario import AttachTo, DetailLevel, Direction, LoadPattern, Placement
from dentalsim.model.settings import CHOICE, DesignValue

TEMPLATE_FORMAT: Literal["dentalsim.template/1"] = "dentalsim.template/1"

ProsthesisType = Literal[
    "single_crown_implant",
    "single_crown_tooth",
    "splinted_crowns",
    "bridge_implant",
    "bridge_tooth",
    "bridge_mixed",
    "cantilever_bridge",
    "full_arch_fixed",
    "implant_overdenture",
    "rpd",
    "complete_denture",
    "custom",
]
Branch = Literal["implant", "tooth", "removable"]
Origin = Literal["builtin", "owner"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LibraryChoice(_Model):
    """A library setting of a template: the entry by parameter key, and how its value is chosen.

    `parameter_key` may be left out of a block setting, which then takes the key the block gives it for its design
    values; when given, it must be that key. `value` is given with the choice `custom`, and only then.
    """

    kind: Literal["library"] = "library"
    parameter_key: str | None = None
    choice: str = "typical"
    value: float | None = None

    @field_validator("choice")
    @classmethod
    def _choice(cls, value: str) -> str:
        if not CHOICE.match(value):
            raise ValueError(f"choice must be 'typical', 'source:<id>' or 'custom', not {value!r}")
        return value

    @model_validator(mode="after")
    def _custom_value(self) -> LibraryChoice:
        if (self.choice == "custom") != (self.value is not None):
            raise ValueError("a value is given with the choice 'custom', and only then")
        return self


TemplateSetting = Annotated[DesignValue | LibraryChoice, Field(discriminator="kind")]


class TemplateBlock(_Model):
    instance_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    type_id: str = Field(min_length=1)
    type_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    attach_to: AttachTo | None = None
    placement: Placement = Placement()
    settings: dict[str, TemplateSetting] = {}


class TemplateContactPoint(_Model):
    block: str
    point: str
    share: float = Field(gt=0, le=1)


class TemplateInstant(_Model):
    id: str
    contact_points: list[TemplateContactPoint]
    direction: Direction
    force_share: float = Field(gt=0, le=1)


class TemplateLoad(_Model):
    """A load case with its force (and, for a chewing cycle, its cycle count) as library choices."""

    id: str = Field(min_length=1)
    name: str | None = None
    pattern: LoadPattern
    force: LibraryChoice
    direction: Direction | None = None
    contact_points: list[TemplateContactPoint] = []
    instants: list[TemplateInstant] = []
    cycles: LibraryChoice | None = None

    @model_validator(mode="after")
    def _keys(self) -> TemplateLoad:
        for what, choice in (("force", self.force), ("cycle count", self.cycles)):
            if choice is not None and choice.parameter_key is None:
                raise ValueError(f"load {self.id!r}: the {what} must name its library parameter key")
        return self


class FitRule(_Model):
    """A condition on the jaw segment (and the blocks on it) that the template needs (FR-011 `template_misfit`).

    `rule` names one of the checks in `dentalsim.templates.fit`; the other fields are its parameters.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    rule: str

    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Approval(_Model):
    """Who set the design defaults, and whether the owner has approved them (built-in templates)."""

    status: Literal["proposed", "approved"]
    proposed_by: str
    proposed_on: date
    approved_on: date | None = None
    note: str = ""

    @model_validator(mode="after")
    def _dates(self) -> Approval:
        if (self.status == "approved") != (self.approved_on is not None):
            raise ValueError("an approved template records its approval date, and only then")
        return self


class ProsthesisTemplate(_Model):
    format: Literal["dentalsim.template/1"] = TEMPLATE_FORMAT
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1)
    prosthesis_type: ProsthesisType
    branch: Branch
    description: str = ""
    origin: Origin = "builtin"
    approval: Approval | None = None
    detail_level: DetailLevel = "standard"
    fit_rules: list[FitRule] = []
    blocks: list[TemplateBlock]
    loads: list[TemplateLoad] = []

    @model_validator(mode="after")
    def _consistent(self) -> ProsthesisTemplate:
        ids = [b.instance_id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("block instance ids must be unique")
        if self.origin == "builtin" and self.approval is None:
            raise ValueError("a built-in template records who proposed its defaults and whether they are approved")
        return self

    def block(self, instance_id: str) -> TemplateBlock | None:
        return next((b for b in self.blocks if b.instance_id == instance_id), None)

    def root(self) -> TemplateBlock:
        roots = [b for b in self.blocks if b.attach_to is None]
        if len(roots) != 1:
            raise ValueError(f"template {self.id!r} needs exactly one block that attaches to nothing")
        return roots[0]
