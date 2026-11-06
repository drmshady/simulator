"""Loading and opening prosthesis templates (contracts/scenario-format.md "Template contract").

- **Built-in templates** are the `*.yaml` files next to this module, read once and checked against the block
  catalogue: every block type and version exists, every setting is one the block has, of the right kind, and every
  fit rule is known.
- **Owner templates** are saved scenarios (`template_from_scenario`): their library settings go back to parameter
  keys and choices, so that opening one takes the library's current approved versions.
- **Opening** a template (`open_template`) resolves each library setting to the current approved version of its
  entry, `typical` unless the template chose otherwise, and checks the fit rules against the jaw segment. A jaw
  segment of the owner's (`jaw_settings`) replaces the template's own, which is how a template that does not fit
  is found: the problems say what must change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import ValidationError

from dentalsim.blocks.registry import AnyBlockType, catalogue
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import Conventions
from dentalsim.errors import DentalsimError, NotFound, Refused
from dentalsim.library.service import ResolvedValue
from dentalsim.model.scenario import BlockInstance, LoadCase, Scenario
from dentalsim.model.settings import DesignValue, LibraryValue, SettingValue
from dentalsim.model.validity import Problem
from dentalsim.templates.fit import check_rule_shape, fit_problems
from dentalsim.templates.model import (
    Branch,
    LibraryChoice,
    ProsthesisTemplate,
    ProsthesisType,
    TemplateBlock,
    TemplateContactPoint,
    TemplateInstant,
    TemplateLoad,
)

TEMPLATES_DIR = Path(__file__).parent


class ValueResolver(Protocol):
    """Takes a value from the current approved version of an entry (`LibraryService.resolve`)."""

    def resolve(
        self, parameter_key: str, choice: str = "typical", *, custom_value: float | None = None
    ) -> ResolvedValue: ...


# --- loading -----------------------------------------------------------------------------------------------------


def _check_blocks(template: ProsthesisTemplate) -> None:
    found = catalogue()
    where = f"template {template.id!r}"
    for block in template.blocks:
        block_type = found.block_type(block.type_id, block.type_version)
        specs = {s.id: s for s in block_type.setting_specs()}
        for setting_id, value in block.settings.items():
            spec = specs.get(setting_id)
            if spec is None:
                raise DentalsimError(f"{where}: {block_type.label} {block.instance_id!r} has no setting {setting_id!r}")
            if spec.kind != value.kind:
                raise DentalsimError(
                    f"{where}: {block.instance_id!r} {setting_id!r} is a {spec.kind} setting, not a {value.kind} one"
                )
        design = block_type.design_values({k: v for k, v in block.settings.items() if isinstance(v, DesignValue)})
        for setting_id, key in block_type.parameter_keys(design).items():
            named = block.settings.get(setting_id)
            if isinstance(named, LibraryChoice) and named.parameter_key not in (None, key):
                raise DentalsimError(
                    f"{where}: {block.instance_id!r} {setting_id!r} names {named.parameter_key}, but the block's "
                    f"design values take {key}"
                )
    template.root()
    for rule in template.fit_rules:
        check_rule_shape(rule, template.id)


def parse_template(text: str, *, name: str = "the template") -> ProsthesisTemplate:
    try:
        data = yaml.safe_load(text)
        template = ProsthesisTemplate.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise DentalsimError(f"{name} is not a valid template: {exc}") from exc
    _check_blocks(template)
    return template


def load_template_file(path: Path) -> ProsthesisTemplate:
    template = parse_template(path.read_text(encoding="utf-8"), name=path.name)
    if template.id != path.stem:
        raise DentalsimError(f"{path.name}: the template id {template.id!r} must match the file name")
    return template


@cache
def _builtin() -> dict[str, ProsthesisTemplate]:
    templates = [load_template_file(path) for path in sorted(TEMPLATES_DIR.glob("*.yaml"))]
    for template in templates:
        if template.origin != "builtin":
            raise DentalsimError(f"built-in template {template.id!r} must have origin 'builtin'")
    return {t.id: t for t in templates}


def builtin_templates() -> list[ProsthesisTemplate]:
    return list(_builtin().values())


def builtin_template(template_id: str) -> ProsthesisTemplate:
    try:
        return _builtin()[template_id]
    except KeyError:
        raise NotFound(f"there is no built-in template {template_id!r}") from None


# --- opening -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenedTemplate:
    scenario: Scenario
    misfits: list[Problem]
    """`template_misfit` problems; the scenario is still made, so the owner can change what they say."""
    library: list[ResolvedValue] = field(default_factory=list)
    """Every library value taken, in the order taken."""


def _resolve_choice(
    library: ValueResolver, key: str, choice: LibraryChoice, taken: list[ResolvedValue]
) -> LibraryValue:
    resolved = library.resolve(key, choice.choice, custom_value=choice.value)
    taken.append(resolved)
    return resolved.library_value()


def _open_block(
    block: TemplateBlock,
    block_type: AnyBlockType,
    library: ValueResolver,
    overrides: Mapping[str, SettingValue],
    conventions: Conventions,
    taken: list[ResolvedValue],
) -> BlockInstance:
    given: dict[str, DesignValue | LibraryChoice | LibraryValue] = {**block.settings, **overrides}
    design = block_type.design_values({k: v for k, v in given.items() if isinstance(v, DesignValue)})
    keys = block_type.parameter_keys(design, conventions)
    settings: dict[str, SettingValue] = {k: v for k, v in given.items() if isinstance(v, DesignValue | LibraryValue)}
    for setting_id, key in keys.items():
        value = given.get(setting_id)
        if isinstance(value, LibraryValue):
            continue
        choice = value if isinstance(value, LibraryChoice) else LibraryChoice()
        if choice.parameter_key is not None and choice.parameter_key != key:
            choice = LibraryChoice()  # the owner's jaw settings name another entry: take its typical value
        settings[setting_id] = _resolve_choice(library, key, choice, taken)
    return BlockInstance(
        instance_id=block.instance_id,
        type_id=block.type_id,
        type_version=block.type_version,
        attach_to=block.attach_to,
        placement=block.placement,
        settings=settings,
    )


def _point(point: TemplateContactPoint) -> dict[str, Any]:
    return point.model_dump()


def _open_load(load: TemplateLoad, library: ValueResolver, taken: list[ResolvedValue]) -> LoadCase:
    assert load.force.parameter_key is not None
    data: dict[str, Any] = {
        "id": load.id,
        "name": load.name,
        "pattern": load.pattern,
        "force": _resolve_choice(library, load.force.parameter_key, load.force, taken),
        "direction": load.direction,
        "contact_points": [_point(p) for p in load.contact_points],
        "instants": [
            {**i.model_dump(exclude={"contact_points"}), "contact_points": [_point(p) for p in i.contact_points]}
            for i in load.instants
        ],
    }
    if load.cycles is not None:
        assert load.cycles.parameter_key is not None
        data["cycles"] = _resolve_choice(library, load.cycles.parameter_key, load.cycles, taken)
    try:
        return LoadCase.model_validate(data)
    except ValidationError as exc:
        raise DentalsimError(f"load {load.id!r} of the template is not valid: {exc}") from exc


def open_template(
    template: ProsthesisTemplate,
    library: ValueResolver,
    *,
    jaw_settings: Mapping[str, SettingValue] | None = None,
    name: str | None = None,
    conventions: Conventions | None = None,
) -> OpenedTemplate:
    """A scenario made from `template`, with every library value resolved, and its fit problems.

    `jaw_settings` replace the template's settings of its root block (the jaw segment). Refused
    (`validation_failed`) when a library entry it needs has no approved version.
    """
    conventions = conventions or get_conventions()
    found = catalogue()
    root = template.root().instance_id
    taken: list[ResolvedValue] = []
    blocks = [
        _open_block(
            block,
            found.block_type(block.type_id, block.type_version),
            library,
            jaw_settings if block.instance_id == root and jaw_settings else {},
            conventions,
            taken,
        )
        for block in template.blocks
    ]
    loads = [_open_load(load, library, taken) for load in template.loads]
    try:
        scenario = Scenario(
            name=name or template.name,
            conventions_version=conventions.version,
            detail_level=template.detail_level,
            blocks=blocks,
            loads=loads,
        )
    except ValidationError as exc:
        raise Refused(
            f"the template {template.name!r} does not make a valid scenario: {exc}", code="validation_failed"
        ) from None
    return OpenedTemplate(scenario=scenario, misfits=fit_problems(template, scenario), library=taken)


# --- owner templates ---------------------------------------------------------------------------------------------


def _choice_of(value: LibraryValue, key: str) -> LibraryChoice:
    if value.choice == "custom":
        return LibraryChoice(parameter_key=key, choice="custom", value=value.value)
    return LibraryChoice(parameter_key=key, choice=value.choice)


def template_from_scenario(
    scenario: Scenario,
    *,
    template_id: str,
    name: str,
    branch: Branch,
    parameter_key_of: Callable[[str], str],
    prosthesis_type: ProsthesisType = "custom",
    description: str = "",
) -> ProsthesisTemplate:
    """An owner template from a scenario. `parameter_key_of(entry_id)` gives each library entry's parameter key;
    the template keeps each setting's choice, and the value only for a custom choice."""
    blocks = []
    for block in scenario.blocks:
        settings: dict[str, DesignValue | LibraryChoice] = {}
        for setting_id, value in block.settings.items():
            if isinstance(value, LibraryValue):
                settings[setting_id] = _choice_of(value, parameter_key_of(value.entry_id))
            else:
                settings[setting_id] = value
        blocks.append(
            TemplateBlock(
                instance_id=block.instance_id,
                type_id=block.type_id,
                type_version=block.type_version,
                attach_to=block.attach_to,
                placement=block.placement,
                settings=settings,
            )
        )
    loads = []
    for load in scenario.loads:
        named = [p for p in load.all_contact_points() if p.point is None]
        if named:
            raise Refused(
                f"load {load.id!r} acts at a surface position, which a template cannot carry; use named points",
                code="validation_failed",
            )
        loads.append(
            TemplateLoad(
                id=load.id,
                name=load.name,
                pattern=load.pattern,
                force=_choice_of(load.force, parameter_key_of(load.force.entry_id)),
                direction=load.direction,
                contact_points=[
                    TemplateContactPoint.model_validate(p.model_dump(exclude={"position_mm"}))
                    for p in load.contact_points
                ],
                instants=[
                    TemplateInstant(
                        id=i.id,
                        direction=i.direction,
                        force_share=i.force_share,
                        contact_points=[
                            TemplateContactPoint.model_validate(p.model_dump(exclude={"position_mm"}))
                            for p in i.contact_points
                        ],
                    )
                    for i in load.instants
                ],
                cycles=None if load.cycles is None else _choice_of(load.cycles, parameter_key_of(load.cycles.entry_id)),
            )
        )
    template = ProsthesisTemplate(
        id=template_id,
        name=name,
        prosthesis_type=prosthesis_type,
        branch=branch,
        description=description,
        origin="owner",
        detail_level=scenario.detail_level,
        blocks=blocks,
        loads=loads,
    )
    _check_blocks(template)
    return template
