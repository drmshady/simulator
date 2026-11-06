"""No patient data in any model, and no unknown fields accepted (FR-048, constitution Principle I).

The walk covers every pydantic model defined in the `dentalsim.model` package, in `dentalsim.library.models`, in
`dentalsim.persistence` (its record classes) and in the store and physics modules, plus every model reachable through
their field types.

The deny list is explicit and aims at patient identity and patient data, not at the word "name":

- anything containing `patient`, `birth`, `address`, `scan`, `photo` or an imaging term (`dicom`, `cbct`,
  `radiograph`, `xray`)
- the tokens `dob` and `mrn` (medical record number)
- the names of a person: `first_name`, `last_name`, `full_name`, `given_name`, `family_name`, `middle_name`,
  `maiden_name`, `person_name`, `patient_name`, `surname`, `forename`, alone or as the end of a longer name

A bare `name` is allowed: it is the title of a scenario, project or template, or the name of a product in a brand
comparison, never a person's name. Literature authors (`authors`, `first_author`) are published researchers, not
patients, so they are allowed too.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Iterator
from types import ModuleType
from typing import Any, get_args, get_origin

import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ValidationError

import dentalsim.model
from dentalsim.model.scenario import Scenario

DENIED_SUBSTRINGS = ("patient", "birth", "address", "scan", "photo", "dicom", "cbct", "radiograph", "xray", "x_ray")
DENIED_TOKENS = ("dob", "mrn")
PERSON_NAMES = (
    "first_name",
    "last_name",
    "full_name",
    "given_name",
    "family_name",
    "middle_name",
    "maiden_name",
    "person_name",
    "patient_name",
    "surname",
    "forename",
)


EXTRA_MODULES = (
    "dentalsim.store.hashing",
    "dentalsim.store.cas",
    "dentalsim.store.staleness",
    "dentalsim.physics.registry",
    "dentalsim.physics.gate",
)


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).replace("-", "_").lower()


def is_patient_field(name: str) -> bool:
    snake = _snake(name)
    tokens = snake.split("_")
    return (
        any(term in snake for term in DENIED_SUBSTRINGS)
        or any(token in tokens for token in DENIED_TOKENS)
        or any(snake == person or snake.endswith(f"_{person}") for person in PERSON_NAMES)
    )


def _modules() -> list[ModuleType]:
    package = dentalsim.model
    names = sorted(f"{package.__name__}.{info.name}" for info in pkgutil.iter_modules(package.__path__))
    names += ["dentalsim.library.models", "dentalsim.persistence", *EXTRA_MODULES]
    return [importlib.import_module(name) for name in names]


def _models_in(annotation: Any) -> Iterator[type[BaseModel]]:
    if get_origin(annotation) is None and isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for argument in get_args(annotation):
        yield from _models_in(argument)


def all_models() -> list[type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    pending = [
        value
        for module in _modules()
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == module.__name__
    ]
    while pending:
        model = pending.pop()
        key = f"{model.__module__}.{model.__qualname__}"
        if key in found:
            continue
        found[key] = model
        for info in model.model_fields.values():
            pending.extend(_models_in(info.annotation))
    return [found[key] for key in sorted(found)]


def _field_names(model: type[BaseModel]) -> set[str]:
    names: set[str] = set()
    for field_name, info in model.model_fields.items():
        names.add(field_name)
        for alias in (info.alias, info.serialization_alias, info.validation_alias):
            names.update(_alias_names(alias))
    for field_name, computed in model.model_computed_fields.items():
        names.add(field_name)
        if computed.alias:
            names.add(computed.alias)
    return names


def _alias_names(alias: str | AliasPath | AliasChoices | None) -> list[str]:
    if alias is None:
        return []
    if isinstance(alias, str):
        return [alias]
    if isinstance(alias, AliasPath):
        return [part for part in alias.path if isinstance(part, str)]
    return [name for choice in alias.choices for name in _alias_names(choice)]


MODELS = all_models()


def _id(model: type[BaseModel]) -> str:
    return f"{model.__module__.removeprefix('dentalsim.')}.{model.__qualname__}"


def test_the_walk_finds_the_models() -> None:
    names = {model.__name__ for model in MODELS}
    expected = {
        "Scenario",
        "BlockInstance",
        "Placement",
        "LoadCase",
        "ChewingInstant",
        "ContactPoint",
        "DesignValue",
        "LibraryValue",
        "Problem",
        "ValidityResult",
        "KeyNumber",
        "LockedResult",
        "ResultSet",
        "Run",
        "LibraryVersionRef",
        "Source",
        "LiteratureEntry",
        "LiteratureEntryVersion",
        "CapabilityStatus",
        "JobRecord",
        "ScenarioRecord",
        "InputRecord",
        "CapabilitySummary",
        "GateResult",
    }
    assert expected <= names


@pytest.mark.parametrize("model", MODELS, ids=_id)
def test_every_model_forbids_unknown_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("model", MODELS, ids=_id)
def test_no_field_holds_patient_data(model: type[BaseModel]) -> None:
    denied = sorted(name for name in _field_names(model) if is_patient_field(name))
    assert denied == [], f"{_id(model)} has patient-data fields: {denied}"


@pytest.mark.parametrize(
    "name",
    [
        "patient",
        "patient_id",
        "patientName",
        "first_name",
        "lastName",
        "owner_full_name",
        "surname",
        "date_of_birth",
        "birthday",
        "dob",
        "mrn",
        "patient_mrn",
        "home_address",
        "scan_file",
        "intraoral_scan",
        "photo",
        "photos",
        "cbct_volume",
        "dicom_series",
    ],
)
def test_the_deny_list_catches_patient_fields(name: str) -> None:
    assert is_patient_field(name)


@pytest.mark.parametrize(
    "name", ["name", "label", "title", "instance_id", "first_author", "authors", "parameter_key", "type_id"]
)
def test_the_deny_list_allows_ordinary_fields(name: str) -> None:
    assert not is_patient_field(name)


def _scenario_data() -> dict[str, Any]:
    return {
        "name": "Unknown-field test",
        "conventions_version": "1.0.0",
        "blocks": [
            {
                "instance_id": "base",
                "type_id": "test_base",
                "type_version": "1.0.0",
                "placement": {"position_mm": [0, 0, 0], "axis_angles_deg": {"mesiodistal": 0, "buccolingual": 0}},
                "settings": {
                    "width": {"kind": "design", "value": 10},
                    "stiffness": {
                        "kind": "library",
                        "entry_id": "E1",
                        "entry_version": 1,
                        "choice": "typical",
                        "value": 1,
                    },
                },
            }
        ],
        "loads": [
            {
                "id": "bite-1",
                "pattern": "single_bite",
                "force": {"kind": "library", "entry_id": "E2", "entry_version": 1, "choice": "typical", "value": 200},
                "direction": {"angle_deg": 30, "azimuth_deg": 90},
                "contact_points": [{"block": "base", "point": "top", "share": 1.0}],
            }
        ],
    }


def test_the_scenario_fixture_is_valid() -> None:
    Scenario.model_validate(_scenario_data())


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("blocks", 0),
        ("blocks", 0, "placement"),
        ("blocks", 0, "placement", "axis_angles_deg"),
        ("blocks", 0, "settings", "width"),
        ("blocks", 0, "settings", "stiffness"),
        ("loads", 0),
        ("loads", 0, "force"),
        ("loads", 0, "direction"),
        ("loads", 0, "contact_points", 0),
    ],
    ids=lambda path: "/".join(str(p) for p in path) or "top-level",
)
def test_a_scenario_with_an_unknown_key_is_refused(path: tuple[str | int, ...]) -> None:
    data = _scenario_data()
    target: Any = data
    for part in path:
        target = target[part]
    target["patient_name"] = "Jane Doe"
    with pytest.raises(ValidationError, match="patient_name"):
        Scenario.model_validate(data)
