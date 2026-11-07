"""The block framework (contracts/scenario-format.md "Block type contract"; data-model.md "BlockType").

A block type is one module under `dentalsim/blocks/` that exports `BLOCK_TYPES`, a tuple of `BlockType` instances
(more than one when an older version is kept for saved scenarios). The registry finds them; generic code reaches
them only through the registry and the members defined here, never by importing a block module (SC-012).

**Settings.** Each block has a pydantic settings model (`BlockSettings`). Every field is annotated with exactly one
of two markers, which carry what the interface and the checks need:

- `Design(group, label, unit, minimum, maximum, ...)`: chosen freely within the limits (FR-052). A value outside
  them, or a choice the block does not offer, is a `design_limit` problem: the scenario can still be saved, but not
  previewed or run (FR-011). A `Literal[...]` type gives the allowed choices.
- `Library(group, label, parameter_key, ...)`: a value from an approved literature entry (FR-014). The parameter key
  may name design settings of the same block in braces, `material.{material}.E`, and `{material:strength}` stands
  for the strength reference the conventions give that material (`yield_strength` for metals,
  `flexural_strength` for brittle materials).

The five setting groups are those of FR-003.

**Frames.** A block builds its geometry in its own local frame: origin at the point where it attaches, +z pointing
away from what it attaches to (occlusal for the blocks of slices 1a and 1b), +x mesial, +y buccal. Everything a
block returns to generic code is in the conventions frame, through the context's `to_frame` transform: placed
parts, sockets, keep-out and bone volumes, occlusal points, report anchors and problem locations.

**Geometry.** `build_geometry(ctx)` makes gmsh OpenCASCADE solids in the local frame, with no meshing and no solver
call; `build(ctx)` places them. Parts may overlap where they are meant to (an implant sits inside the bone): the part
whose `displaces` names the other's component takes the overlap when the assembly is joined. A `Cutter` removes
material afterwards from the components it names, such as an empty screw-access channel. Any other overlap is a
collision (`overlap`, found by the assembly).

**Sockets.** A block attaches to a socket of its parent by name. A keyed attach point gives one socket per value of
a list setting (`site:34`, `site:35`, … one per tooth of the jaw segment's region); see `AttachPoint`.

**Joints and links** (slice 1b). The placement tree says where a block sits, not what holds it up: a pontic sits
at a site of the jaw segment but is carried only by the connectors that join it to its neighbours. A prosthesis
unit lists its contact areas as `Joint`s, a connector reports the two joints it joins as a `Link`, and both reach
every block through its `Environment`, so a pontic or a cantilever extension can check that it is held
(`Environment.supported_at`, problem `unsupported_pontic`).
"""

from __future__ import annotations

import math
import re
import string
import types
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any, ClassVar, Literal, get_args, get_origin

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.fields import FieldInfo

from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.regions import ReportAnchor
from dentalsim.conventions.schema import Conventions, is_parameter_key
from dentalsim.errors import DentalsimError, Refused
from dentalsim.geometry import occ as occ_tools
from dentalsim.geometry.shapes import Placed, Volume, as_points
from dentalsim.geometry.transform import IDENTITY, Point, Transform
from dentalsim.model.scenario import BlockInstance
from dentalsim.model.settings import DesignValue, LibraryValue, SettingValue
from dentalsim.model.validity import Problem, ProblemCode

SettingGroup = Literal["patient_bone", "implant", "prosthesis", "bite_and_load", "model_detail"]
SETTING_GROUPS: dict[SettingGroup, str] = {
    "patient_bone": "Patient bone",
    "implant": "Implant",
    "prosthesis": "Prosthesis",
    "bite_and_load": "Bite and load",
    "model_detail": "Model detail",
}
SettingKind = Literal["design", "library"]
ValueType = Literal["number", "integer", "boolean", "string", "string_list", "number_list"]

TYPE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SLICE = re.compile(r"^(1a|1b|story9:[a-z_]+)$")
BONDED_ELASTIC = "bonded_elastic"

BoolArray = NDArray[np.bool_]


# --- setting markers and specs -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Design:
    """Marks a design setting. `minimum` and `maximum` apply to numbers, `min_items` and `max_items` to lists.

    `site_width_offset_mm`: a block newly added on a jaw site starts with this setting at the site's tooth width
    plus this offset (see `assembly.site_defaults`), not at the fixed default.
    """

    group: SettingGroup
    label: str
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_items: int | None = None
    max_items: int | None = None
    help: str = ""
    site_width_offset_mm: float | None = None


@dataclass(frozen=True)
class Library:
    """Marks a library setting. `above` and `below` are strict physical bounds (an elastic modulus above 0, a
    Poisson's ratio between -1 and 0.5); a value outside them is refused, not merely marked outside the range."""

    group: SettingGroup
    label: str
    parameter_key: str
    unit: str | None = None
    above: float | None = None
    below: float | None = None
    help: str = ""


@dataclass(frozen=True)
class SettingSpec:
    """One setting of a block type, as the interface, the checks and the templates see it."""

    id: str
    kind: SettingKind
    group: SettingGroup
    label: str
    value_type: ValueType
    unit: str | None = None
    help: str = ""
    default: Any = None
    """The design default; None for library settings."""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    min_items: int | None = None
    max_items: int | None = None
    parameter_key: str | None = None
    above: float | None = None
    below: float | None = None
    site_width_offset_mm: float | None = None
    """For a design setting: a new block on a jaw site starts at the site's tooth width plus this."""

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"title": self.label, "group": self.group, "kind": self.kind}
        if self.help:
            schema["description"] = self.help
        if self.value_type.endswith("_list"):
            item = "string" if self.value_type == "string_list" else "number"
            schema.update(type="array", items={"type": item})
            if self.min_items is not None:
                schema["minItems"] = self.min_items
            if self.max_items is not None:
                schema["maxItems"] = self.max_items
        else:
            schema["type"] = self.value_type
        if self.unit is not None:
            schema["unit"] = self.unit
        if self.kind == "design":
            schema["default"] = list(self.default) if isinstance(self.default, tuple) else self.default
        for key, value in (("minimum", self.minimum), ("maximum", self.maximum)):
            if value is not None:
                schema[key] = value
        if self.choices is not None:
            schema["enum"] = list(self.choices)
        if self.parameter_key is not None:
            schema["parameter_key"] = self.parameter_key
        if self.above is not None:
            schema["exclusiveMinimum"] = self.above
        if self.below is not None:
            schema["exclusiveMaximum"] = self.below
        if self.site_width_offset_mm is not None:
            schema["site_width_offset_mm"] = self.site_width_offset_mm
        return schema


def _value_type(name: str, annotation: Any) -> tuple[ValueType, tuple[str, ...] | None]:
    """The value type and, for a `Literal`, the choices of a settings field."""
    origin = get_origin(annotation)
    if origin is Literal:
        choices = get_args(annotation)
        if not choices or not all(isinstance(c, str) for c in choices):
            raise TypeError(f"setting {name!r}: a Literal of choices must list strings")
        return "string", tuple(choices)
    if origin is tuple:
        args = get_args(annotation)
        if len(args) == 2 and args[1] is Ellipsis and args[0] in (str, float):
            return ("string_list" if args[0] is str else "number_list"), None
        raise TypeError(f"setting {name!r}: a list setting must be tuple[str, ...] or tuple[float, ...]")
    simple: dict[Any, ValueType] = {float: "number", int: "integer", bool: "boolean", str: "string"}
    if annotation in simple:
        return simple[annotation], None
    if isinstance(annotation, types.UnionType):
        raise TypeError(f"setting {name!r}: optional or union settings are not supported")
    raise TypeError(f"setting {name!r}: unsupported type {annotation!r}")


def _spec(name: str, info: FieldInfo) -> SettingSpec:
    markers = [m for m in info.metadata if isinstance(m, Design | Library)]
    if len(markers) != 1:
        raise TypeError(f"setting {name!r} needs exactly one Design(...) or Library(...) marker")
    marker = markers[0]
    value_type, choices = _value_type(name, info.annotation)
    if isinstance(marker, Library):
        if value_type != "number":
            raise TypeError(f"library setting {name!r} must be a float")
        if not info.is_required():
            raise TypeError(f"library setting {name!r} must not have a default: its value comes from the library")
        return SettingSpec(
            id=name,
            kind="library",
            group=marker.group,
            label=marker.label,
            value_type=value_type,
            unit=marker.unit,
            help=marker.help,
            parameter_key=marker.parameter_key,
            above=marker.above,
            below=marker.below,
        )
    if info.is_required():
        raise TypeError(f"design setting {name!r} needs a default")
    return SettingSpec(
        id=name,
        kind="design",
        group=marker.group,
        label=marker.label,
        value_type=value_type,
        unit=marker.unit,
        help=marker.help,
        default=info.default,
        minimum=marker.minimum,
        maximum=marker.maximum,
        choices=choices,
        min_items=marker.min_items,
        max_items=marker.max_items,
        site_width_offset_mm=marker.site_width_offset_mm,
    )


class BlockSettings(BaseModel):
    """Base of every block's settings model: plain values (numbers, choices, lists), one field per setting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @classmethod
    def specs(cls) -> tuple[SettingSpec, ...]:
        return _specs_of(cls)


@cache
def _specs_of(model: type[BlockSettings]) -> tuple[SettingSpec, ...]:
    return tuple(_spec(name, info) for name, info in model.model_fields.items())


# --- parameter keys ----------------------------------------------------------------------------------------------


class _KeyFormatter(string.Formatter):
    def __init__(self, conventions: Conventions) -> None:
        self.conventions = conventions

    def get_value(self, key: int | str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> Any:
        if not isinstance(key, str) or key not in kwargs:
            raise KeyError(f"the parameter key names {key!r}, which is not a design setting of the block")
        return kwargs[key]

    def format_field(self, value: Any, format_spec: str) -> str:
        if format_spec == "":
            return str(value)
        if format_spec == "strength":
            rule = self.conventions.strength_rule_for(str(value))
            if rule is None or rule.reference == "none":
                raise KeyError(f"the conventions compare no strength reference with the material {value!r}")
            return rule.reference
        raise KeyError(f"unknown parameter-key format {format_spec!r}; only ':strength' is defined")


def resolve_parameter_key(template: str, design: Mapping[str, Any], conventions: Conventions | None = None) -> str:
    """The parameter key of `template` with the block's design values filled in."""
    try:
        key = _KeyFormatter(conventions or get_conventions()).vformat(template, (), design)
    except KeyError as exc:
        raise DentalsimError(f"cannot resolve the parameter key {template!r}: {exc.args[0]}") from None
    if not is_parameter_key(key):
        raise DentalsimError(f"the parameter key {template!r} resolves to {key!r}, which is not a parameter key")
    return key


# --- results, attach points and contexts -------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultType:
    """A kind of result a block reports. Physics beyond the block's own, such as `screw_preload` for screw stress,
    goes in `needs_physics`; until it is ready the result type is listed as locked (FR-011a)."""

    id: str
    label: str
    regions: tuple[str, ...] = ()
    needs_physics: frozenset[str] = frozenset()


KEY_SEPARATOR = ":"
X_TOWARD = "x_toward"
"""Socket property: whether the socket's +x points `mesial` or `distal` (see `BlockContext.mesial_sign`)."""


def split_socket_name(name: str) -> tuple[str, str | None]:
    """The attach point and the key of a socket name: `site:36` is (`site`, `36`), `platform` is (`platform`, None)."""
    base, separator, key = name.partition(KEY_SEPARATOR)
    return base, (key if separator else None)


@dataclass(frozen=True)
class AttachPoint:
    """A socket a block offers to others, such as the implant's `platform`. `capacity` is how many blocks each
    socket holds: one for every socket so far (an implant site holds one implant, a platform one abutment).

    A **keyed** attach point is offered once per value of a list setting of the block: `keys_from` names that
    string-list design setting, and each socket is named `<name>:<value>` (the jaw segment offers `site:34`,
    `site:35`, … for the teeth of its region). `default_key_from` names the design setting whose value a bare
    `<name>` stands for, so a scenario that names the attach point alone (written before it was keyed) keeps its
    meaning.
    """

    name: str
    label: str
    description: str = ""
    capacity: int = 1
    keys_from: str | None = None
    default_key_from: str | None = None

    @property
    def keyed(self) -> bool:
        return self.keys_from is not None

    def socket_name(self, key: str | None = None) -> str:
        return self.name if key is None else f"{self.name}{KEY_SEPARATOR}{key}"

    def socket_label(self, key: str | None = None) -> str:
        return self.label if key is None else f"{self.label} {key}"


@dataclass(frozen=True)
class Socket:
    """An offered socket of a placed block instance.

    `frame` maps socket coordinates to the conventions frame: a block attached to this socket has its local origin
    and axes here, before its own placement offsets. `properties` describe what is built at the socket (for the
    implant platform: the connection, its radii and depths); the offering block's module documents them.
    """

    name: str
    frame: Transform
    properties: Mapping[str, float | str] = field(default_factory=dict)

    @property
    def attach_point(self) -> str:
        """The attach point it belongs to: `site` for the keyed socket `site:36`."""
        return split_socket_name(self.name)[0]

    def number(self, key: str) -> float:
        value = self.properties.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise DentalsimError(f"socket {self.name!r} has no number {key!r}")
        return float(value)

    def text(self, key: str) -> str:
        value = self.properties.get(key)
        if not isinstance(value, str):
            raise DentalsimError(f"socket {self.name!r} has no text {key!r}")
        return value


@dataclass(frozen=True)
class ParentLink:
    """The block instance and socket a block is attached to."""

    instance_id: str
    socket: Socket


@dataclass(frozen=True, eq=False)
class KeepOut:
    """A space other blocks may not enter, with the problem raised when a part does (FR-011)."""

    name: str
    label: str
    """How a message names it: "the nerve canal", "the root of tooth 35"."""
    code: ProblemCode
    volume: Volume
    """In the conventions frame."""
    owner: str = ""
    """The block instance it belongs to."""


@dataclass(frozen=True)
class Joint:
    """A contact area of a prosthesis unit (a crown, a pontic) where a connector can join it to its neighbour, in
    the conventions frame (FR-011 "unsupported pontics").

    - `socket` is the unit's socket a connector hangs from there (`connector_mesial`, `connector_distal`).
    - `normal` points out of the unit, toward the neighbour it would be joined to.
    - `carried`: the unit stands on its own support (a crown on its abutment), rather than only on the units it is
      joined to (a pontic, a cantilever extension).
    """

    owner: str
    label: str
    """How a message names the unit: "Crown 'crown-1'"."""
    socket: str
    position_mm: Point
    normal: Point
    carried: bool
    occlusal: Point = (0.0, 0.0, 1.0)
    """The unit's occlusal direction."""
    half_widths_mm: tuple[float, float] = (0.0, 0.0)
    """The unit's mesiodistal and buccolingual half-widths at the contact's height (0 when not known)."""
    max_depth_mm: float = 0.0
    """How far a connector may reach into the unit from the contact area (to keep clear of an abutment post, or of
    the connector on its other side); 0 when not known."""
    room_below_mm: float = math.inf
    room_above_mm: float = math.inf
    """The unit's height below and above the contact area: to its base or margin, and to its occlusal table."""


@dataclass(frozen=True)
class Link:
    """Two units joined by a connector `by`: `a` is the joint it hangs from, `b` the one it reaches; each is an
    (owner, socket) pair of a `Joint`."""

    by: str
    a: tuple[str, str]
    b: tuple[str, str]


@dataclass(frozen=True, eq=False)
class Environment:
    """What a block's checks and anchors can see of the rest of the assembly, in the conventions frame: the bone
    (from every block's `bone_volumes`), the keep-out volumes of the other blocks, every prosthesis unit's joints
    (from `joints`) and the links the connectors make between them (from `links`)."""

    bone: tuple[Volume, ...] = ()
    keep_outs: tuple[KeepOut, ...] = ()
    joints: tuple[Joint, ...] = ()
    links: tuple[Link, ...] = ()

    def joint(self, owner: str, socket: str) -> Joint | None:
        return next((j for j in self.joints if (j.owner, j.socket) == (owner, socket)), None)

    def joined_at(self, owner: str, socket: str) -> list[tuple[str, str]]:
        """The joints of other units linked to the joint (owner, socket)."""
        found = []
        for link in self.links:
            if link.a == (owner, socket):
                found.append(link.b)
            elif link.b == (owner, socket):
                found.append(link.a)
        return found

    def is_carried(self, owner: str) -> bool:
        return any(j.carried for j in self.joints if j.owner == owner)

    def supported_at(self, owner: str, socket: str) -> bool:
        """Whether the unit `owner` is held at its joint `socket`: a link there leads, through linked units that do
        not come back through `owner`, to a unit that stands on its own support. Two pontics side by side between
        two crowns are each held on both sides."""
        neighbours: dict[str, set[str]] = {}
        for link in self.links:
            neighbours.setdefault(link.a[0], set()).add(link.b[0])
            neighbours.setdefault(link.b[0], set()).add(link.a[0])
        start = {unit for unit, _ in self.joined_at(owner, socket)} - {owner}
        reached, frontier = set(start), list(start)
        while frontier:
            unit = frontier.pop()
            if self.is_carried(unit):
                return True
            for following in neighbours.get(unit, set()) - reached - {owner}:
                reached.add(following)
                frontier.append(following)
        return False

    def in_bone(self, points: ArrayLike) -> BoolArray:
        p = as_points(points)
        inside = np.zeros(len(p), dtype=np.bool_)
        for volume in self.bone:
            inside |= volume.contains(p)
        return inside

    def in_keep_out(self, points: ArrayLike) -> BoolArray:
        p = as_points(points)
        inside = np.zeros(len(p), dtype=np.bool_)
        for keep_out in self.keep_outs:
            inside |= keep_out.volume.contains(p)
        return inside


EMPTY_ENVIRONMENT = Environment()


@dataclass(frozen=True, eq=False)
class BlockContext[S: BlockSettings]:
    """Everything a block instance's methods may use."""

    instance_id: str
    settings: S
    conventions: Conventions
    to_frame: Transform = IDENTITY
    """The block's local frame in the conventions frame."""
    parent: ParentLink | None = None
    environment: Environment = EMPTY_ENVIRONMENT

    def place(self, volume: Volume) -> Volume:
        """A volume defined in the local frame, placed in the conventions frame."""
        return volume if self.to_frame.is_identity() else Placed(volume, self.to_frame)

    def socket(
        self, name: str, local: Transform = IDENTITY, properties: Mapping[str, float | str] | None = None
    ) -> Socket:
        """A socket at `local` (in the block's frame), expressed in the conventions frame."""
        return Socket(name, self.to_frame @ local, dict(properties or {}))

    def parent_socket(self, *names: str) -> Socket:
        """The socket the block is attached to; refused when it is not attached to one of `names` (attach point
        names: a keyed socket `site:36` is a `site` socket)."""
        if self.parent is None or split_socket_name(self.parent.socket.name)[0] not in names:
            wanted = " or ".join(repr(n) for n in names)
            raise Refused(
                f"block {self.instance_id!r} must be attached to a {wanted} socket",
                code="validation_failed",
                problems=[{"block": self.instance_id, "message": f"attach it to a {wanted} socket"}],
            )
        return self.parent.socket

    @property
    def mesial_sign(self) -> float:
        """+1 when the block's local +x points mesial, −1 when it points distal: a site past the midline of a full
        arch (the jaw segment's sockets say so in their `x_toward` property, and each block passes it on to the
        sockets it offers), so that blocks name their mesial and distal sides anatomically."""
        if self.parent is None:
            return 1.0
        return -1.0 if self.parent.socket.properties.get(X_TOWARD) == "distal" else 1.0

    @property
    def x_toward(self) -> str:
        """`mesial` or `distal`: for a block to pass on in the properties of the sockets it offers."""
        return "mesial" if self.mesial_sign > 0 else "distal"

    def problem(self, code: ProblemCode, message: str, location: ArrayLike | None = None) -> Problem:
        where = None
        if location is not None:
            x, y, z = (float(c) for c in np.asarray(location, dtype=np.float64))
            where = (round(x, 3), round(y, 3), round(z, 3))
        return Problem(code=code, message=message, block=self.instance_id, location_mm=where)


# --- geometry results --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """A solid of one material in one component, as OpenCASCADE volume tags in the current gmsh model.

    - `component` names what the region rules and the key numbers see (`implant`, `screw`, `bone`, ...).
    - `material` is a material id of the conventions (`titanium_g4`, `cortical_bone`, ...); `e_mpa` and `nu` are
      its resolved library values.
    - `displaces`: components whose overlap with this part belongs to this part (an implant displaces bone).
    - `strength_setting`: the block setting that holds the material's strength reference, if it has one.
    """

    name: str
    component: str
    material: str
    e_mpa: float
    nu: float
    volumes: tuple[int, ...]
    displaces: frozenset[str] = frozenset()
    strength_setting: str | None = None


@dataclass(frozen=True)
class Cutter:
    """A space emptied of the named components once overlaps are resolved, such as a screw-access channel."""

    name: str
    volumes: tuple[int, ...]
    cuts: frozenset[str]


@dataclass(frozen=True)
class SupportPlane:
    """The block's boundary faces lying on this plane are held fixed (for the jaw: its two cut faces).
    `normal` points out of the block."""

    name: str
    point: Point
    normal: Point

    def transformed(self, transform: Transform) -> SupportPlane:
        return SupportPlane(self.name, transform.point(self.point), transform.direction(self.normal))


@dataclass(frozen=True)
class RefinementHint:
    """Where the mesh must be finer: within `extent_mm` of the surfaces of part `part`, fine enough to resolve
    features of size `feature_mm` (a thread pitch, a connector height). The detail level sets how fine."""

    name: str
    part: str
    feature_mm: float
    extent_mm: float


@dataclass(frozen=True)
class Parts:
    parts: tuple[Part, ...]
    cutters: tuple[Cutter, ...] = ()
    supports: tuple[SupportPlane, ...] = ()
    hints: tuple[RefinementHint, ...] = ()

    def part(self, name: str) -> Part:
        for part in self.parts:
            if part.name == name:
                return part
        raise KeyError(name)

    def volume_tags(self) -> list[int]:
        return [t for p in self.parts for t in p.volumes] + [t for c in self.cutters for t in c.volumes]

    def transformed(self, transform: Transform) -> Parts:
        """The same parts with their supports moved; the OpenCASCADE volumes are moved separately."""
        return Parts(self.parts, self.cutters, tuple(s.transformed(transform) for s in self.supports), self.hints)


@dataclass(frozen=True)
class OcclusalPoint:
    """A named point on a load-bearing surface where a load can act, in the conventions frame."""

    name: str
    label: str
    position_mm: Point
    normal: Point


@dataclass(frozen=True)
class SettingsReading[S: BlockSettings]:
    """The settings of one block instance: the typed settings when they are within every limit, the problems
    otherwise, and the plain values with defaults filled in."""

    settings: S | None
    problems: tuple[Problem, ...]
    values: Mapping[str, Any]

    @property
    def ok(self) -> bool:
        return self.settings is not None


# --- the block type ----------------------------------------------------------------------------------------------


def _conforms(value_type: ValueType, value: Any) -> bool:
    def number(v: Any) -> bool:
        return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)

    match value_type:
        case "number":
            return number(value)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "string":
            return isinstance(value, str)
        case "string_list":
            return isinstance(value, list | tuple) and all(isinstance(v, str) for v in value)
        case "number_list":
            return isinstance(value, list | tuple) and all(number(v) for v in value)
        case _:
            return False


def _fmt(value: float, unit: str | None) -> str:
    return f"{value:g} {unit}" if unit and unit != "1" else f"{value:g}"


class BlockType[S: BlockSettings](ABC):
    """A kind of building block. Subclasses set the class attributes and implement `build_geometry`; the other
    members have defaults for blocks that do not need them."""

    type_id: ClassVar[str]
    version: ClassVar[str]
    slice: ClassVar[str]
    """`1a`, `1b` or `story9:<physics>`."""
    label: ClassVar[str]
    description: ClassVar[str] = ""
    settings_model: type[S]
    attach_points: ClassVar[tuple[AttachPoint, ...]] = ()
    attaches_to: ClassVar[tuple[str, ...]] = ()
    """Socket names this block plugs into; empty for the root of an assembly (the jaw segment)."""
    result_types: ClassVar[tuple[ResultType, ...]] = ()
    report_regions: ClassVar[tuple[str, ...]] = ()
    physics: ClassVar[frozenset[str]] = frozenset({BONDED_ELASTIC})
    """The physics every instance needs; `needs_physics` may add more from the settings."""
    is_axis_reference: ClassVar[bool] = False
    """The first instance of such a block (an implant, later a tooth) sets the frame's +z (conventions frame)."""

    # --- settings ---------------------------------------------------------------------------------------------

    def setting_specs(self) -> tuple[SettingSpec, ...]:
        return self.settings_model.specs()

    def setting_spec(self, setting_id: str) -> SettingSpec:
        for spec in self.setting_specs():
            if spec.id == setting_id:
                return spec
        raise KeyError(setting_id)

    def settings_json_schema(self) -> dict[str, Any]:
        """The settings as a JSON Schema, each property with its `group`, `kind`, limits and parameter key."""
        specs = self.setting_specs()
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"{self.label} settings",
            "type": "object",
            "additionalProperties": False,
            "properties": {spec.id: spec.json_schema() for spec in specs},
            "required": [spec.id for spec in specs if spec.kind == "library"],
            "groups": [{"id": group, "label": label} for group, label in SETTING_GROUPS.items()],
        }

    def design_values(self, raw: Mapping[str, SettingValue]) -> dict[str, Any]:
        """The design values given in `raw`, with the defaults for those not given. Nothing is checked."""
        values: dict[str, Any] = {}
        for spec in self.setting_specs():
            if spec.kind == "design":
                given = raw.get(spec.id)
                values[spec.id] = given.value if isinstance(given, DesignValue) else spec.default
        return values

    def parameter_keys(self, design: Mapping[str, Any], conventions: Conventions | None = None) -> dict[str, str]:
        """The library parameter key of each library setting, for these design values."""
        conventions = conventions or get_conventions()
        return {
            spec.id: resolve_parameter_key(spec.parameter_key, design, conventions)
            for spec in self.setting_specs()
            if spec.kind == "library" and spec.parameter_key is not None
        }

    def read_settings(
        self, raw: Mapping[str, SettingValue], *, instance_id: str, conventions: Conventions | None = None
    ) -> SettingsReading[S]:
        """Check one instance's settings.

        - Refused (`validation_failed`): a setting the block does not have, a missing library value, a design value
          where a library value belongs (or the reverse), a value of the wrong type, or a library value outside its
          physical bounds.
        - `design_limit` problems: a design value outside the block's limits or choices, and the block's own rules
          between settings (`rules`). The settings are then returned as None.
        """
        specs = self.setting_specs()
        errors: list[dict[str, str]] = []

        def error(setting: str, message: str) -> None:
            errors.append({"block": instance_id, "setting": setting, "message": message})

        for unknown in sorted(set(raw) - {s.id for s in specs}):
            error(unknown, f"{self.label} has no setting {unknown!r}")
        values: dict[str, Any] = {}
        for spec in specs:
            given = raw.get(spec.id)
            if given is None:
                if spec.kind == "library":
                    error(spec.id, f"{spec.label} needs a value from the literature library")
                else:
                    values[spec.id] = spec.default
                continue
            if given.kind != spec.kind:
                error(spec.id, f"{spec.label} is a {spec.kind} setting, not a {given.kind} setting")
                continue
            if not _conforms(spec.value_type, given.value):
                error(spec.id, f"{spec.label} must be a {spec.value_type.replace('_', ' ')}, not {given.value!r}")
                continue
            if isinstance(given, LibraryValue):
                bounds_ok = (spec.above is None or given.value > spec.above) and (
                    spec.below is None or given.value < spec.below
                )
                if not bounds_ok:
                    low = "" if spec.above is None else f"above {spec.above:g}"
                    high = "" if spec.below is None else f"below {spec.below:g}"
                    error(spec.id, f"{spec.label} must be {' and '.join(p for p in (low, high) if p)}")
                    continue
            values[spec.id] = tuple(given.value) if isinstance(given.value, list) else given.value
        if errors:
            raise Refused(
                f"The settings of {self.label} {instance_id!r} are not valid: "
                + "; ".join(e["message"] for e in errors)
                + ".",
                code="validation_failed",
                problems=errors,
            )
        problems = [p for spec in specs if (p := self._limit_problem(spec, values[spec.id], instance_id))]
        if problems:
            return SettingsReading(None, tuple(problems), values)
        try:
            settings = self.settings_model.model_validate(values)
        except ValidationError as exc:
            raise Refused(
                f"The settings of {self.label} {instance_id!r} are not valid: {exc}", code="validation_failed"
            ) from None
        problems = self.rules(settings, instance_id)
        return SettingsReading(None if problems else settings, tuple(problems), values)

    def read(self, instance: BlockInstance, conventions: Conventions | None = None) -> SettingsReading[S]:
        if (instance.type_id, instance.type_version) != (self.type_id, self.version):
            raise DentalsimError(
                f"block {instance.instance_id!r} is {instance.type_id} {instance.type_version}, not "
                f"{self.type_id} {self.version}"
            )
        return self.read_settings(instance.settings, instance_id=instance.instance_id, conventions=conventions)

    def _limit_problem(self, spec: SettingSpec, value: Any, instance_id: str) -> Problem | None:
        if spec.kind != "design":
            return None
        what = f"{self.label} {instance_id!r}: {spec.label}"
        message = None
        if spec.choices is not None and value not in spec.choices:
            message = f"{what} must be one of {', '.join(spec.choices)}, not {value!r}"
        elif spec.value_type in ("number", "integer"):
            low, high = spec.minimum, spec.maximum
            if (low is not None and value < low) or (high is not None and value > high):
                if low is not None and high is not None:
                    bound = f"{_fmt(low, spec.unit)} to {_fmt(high, spec.unit)}"
                elif low is not None:
                    bound = f"at least {_fmt(low, spec.unit)}"
                else:
                    bound = f"at most {_fmt(high, spec.unit)}"  # type: ignore[arg-type]
                message = f"{what} is {_fmt(value, spec.unit)}; it must be {bound}"
        elif spec.value_type.endswith("_list"):
            count = len(value)
            if spec.min_items is not None and count < spec.min_items:
                message = f"{what} needs at least {spec.min_items} item(s)"
            elif spec.max_items is not None and count > spec.max_items:
                message = f"{what} takes at most {spec.max_items} item(s)"
        if message is None:
            return None
        return Problem(code="design_limit", message=message + ".", block=instance_id)

    def rules(self, settings: S, instance_id: str) -> list[Problem]:
        """The block's own `design_limit` rules between its settings (one setting against another)."""
        return []

    def design_problem(self, instance_id: str, message: str) -> Problem:
        return Problem(code="design_limit", message=message, block=instance_id)

    # --- physics and catalogue ----------------------------------------------------------------------------------

    # --- sockets ----------------------------------------------------------------------------------------------

    def attach_point(self, socket_name: str) -> AttachPoint | None:
        """The attach point a socket name belongs to (`site:36` belongs to `site`)."""
        base, _ = split_socket_name(socket_name)
        return next((a for a in self.attach_points if a.name == base), None)

    def resolve_socket_name(self, socket_name: str, design: Mapping[str, Any]) -> str:
        """The full name of the socket `socket_name` stands for, given this block's design values: a bare name of a
        keyed attach point means its default key (`site` is `site:<the jaw segment's site>`)."""
        point = self.attach_point(socket_name)
        _, key = split_socket_name(socket_name)
        if point is None or not point.keyed or key is not None or point.default_key_from is None:
            return socket_name
        return point.socket_name(str(design[point.default_key_from]))

    def socket_keys(self, point: AttachPoint, design: Mapping[str, Any]) -> tuple[str, ...]:
        """The keys of a keyed attach point's sockets for these design values (none for an unkeyed one)."""
        if point.keys_from is None:
            return ()
        return tuple(str(v) for v in design[point.keys_from])

    def needs_physics(self, settings: Mapping[str, SettingValue]) -> frozenset[str]:
        """The capabilities an instance with these (raw) settings needs. The gate reads only this and the result
        types (FR-011a), and calls it even when the settings have problems."""
        return self.physics

    def describe(self) -> dict[str, Any]:
        """The block type for the interface's palette (`GET /api/blocks`); readiness is added by the server."""
        return {
            "type_id": self.type_id,
            "version": self.version,
            "slice": self.slice,
            "label": self.label,
            "description": self.description,
            "settings_schema": self.settings_json_schema(),
            "attach_points": [
                {
                    "name": a.name,
                    "label": a.label,
                    "description": a.description,
                    "capacity": a.capacity,
                    "keys_from": a.keys_from,
                    "default_key_from": a.default_key_from,
                }
                for a in self.attach_points
            ],
            "attaches_to": list(self.attaches_to),
            "needs_physics": sorted(self.physics),
            "result_types": [
                {"id": r.id, "label": r.label, "regions": list(r.regions), "needs_physics": sorted(r.needs_physics)}
                for r in self.result_types
            ],
            "report_regions": list(self.report_regions),
        }

    # --- contexts ---------------------------------------------------------------------------------------------

    def context(
        self,
        instance_id: str,
        settings: S,
        *,
        conventions: Conventions | None = None,
        to_frame: Transform = IDENTITY,
        parent: ParentLink | None = None,
        environment: Environment = EMPTY_ENVIRONMENT,
    ) -> BlockContext[S]:
        if not isinstance(settings, self.settings_model):
            raise TypeError(f"{self.type_id} needs {self.settings_model.__name__}, not {type(settings).__name__}")
        return BlockContext(
            instance_id=instance_id,
            settings=settings,
            conventions=conventions or get_conventions(),
            to_frame=to_frame,
            parent=parent,
            environment=environment,
        )

    # --- geometry and checks -------------------------------------------------------------------------------------

    @abstractmethod
    def build_geometry(self, ctx: BlockContext[S]) -> Parts:
        """Make the block's solids in the current gmsh model, in the block's local frame."""

    def build(self, ctx: BlockContext[S]) -> Parts:
        """`build_geometry`, placed in the conventions frame, with construction leftovers removed."""
        made = self.build_geometry(ctx)
        names = [p.name for p in made.parts] + [c.name for c in made.cutters]
        if len(names) != len(set(names)):
            raise DentalsimError(f"{self.type_id} built parts with repeated names: {names}")
        for part in made.parts:
            if not part.volumes:
                raise DentalsimError(f"{self.type_id} part {part.name!r} has no volume")
            if ctx.conventions.material_class_of(part.material) is None:
                raise DentalsimError(
                    f"{self.type_id} part {part.name!r}: {part.material!r} is not a conventions material"
                )
        part_names = {p.name for p in made.parts}
        for hint in made.hints:
            if hint.part not in part_names:
                raise DentalsimError(f"{self.type_id} refinement hint {hint.name!r} names no part {hint.part!r}")
        occ_tools.transform_volumes(made.volume_tags(), ctx.to_frame)
        occ_tools.remove_orphans()
        return made.transformed(ctx.to_frame)

    def sockets(self, ctx: BlockContext[S]) -> tuple[Socket, ...]:
        """The sockets this instance offers (one per attach point), in the conventions frame."""
        return ()

    def keep_out(self, ctx: BlockContext[S]) -> tuple[KeepOut, ...]:
        """Spaces other blocks may not enter, in the conventions frame."""
        return ()

    def bone_volumes(self, ctx: BlockContext[S]) -> tuple[Volume, ...]:
        """Bone this block provides for others to sit in, in the conventions frame."""
        return ()

    def check(self, ctx: BlockContext[S]) -> list[Problem]:
        """Validity rules that need the assembly: the environment, or the socket the block sits on."""
        return []

    def occlusal_points(self, ctx: BlockContext[S]) -> tuple[OcclusalPoint, ...]:
        """Named points where loads can act, if the block carries load."""
        return ()

    def report_anchors(self, ctx: BlockContext[S]) -> tuple[ReportAnchor, ...]:
        """Axes and radius profiles for the region rules (crestal ring, implant neck), in the conventions frame."""
        return ()

    def joints(self, ctx: BlockContext[S]) -> tuple[Joint, ...]:
        """The contact areas where connectors can join this prosthesis unit to its neighbours, in the conventions
        frame. Called before the environment is known."""
        return ()

    def links(self, ctx: BlockContext[S]) -> tuple[Link, ...]:
        """The units this block joins (a connector), found among `ctx.environment.joints`; the environment's links
        are not known yet when this is called."""
        return ()


def keep_out_intrusions(keep_outs: Iterable[KeepOut], points: ArrayLike) -> list[tuple[KeepOut, BoolArray]]:
    """The keep-out volumes that contain any of `points`, each with the mask of the points inside it."""
    p = as_points(points)
    found = []
    for keep_out in keep_outs:
        inside = keep_out.volume.contains(p)
        if inside.any():
            found.append((keep_out, inside))
    return found
