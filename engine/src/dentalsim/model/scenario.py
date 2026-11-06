"""The scenario definition, format `dentalsim.scenario/1` (contracts/scenario-format.md).

A scenario is an assembly of block instances on a jaw segment plus its load cases. The studio saves it and the
command line reads it. Records around it (project, notes, tags, revision) live in the database, not here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dentalsim.model.settings import LibraryValue, SettingValue

DetailLevel = Literal["preview", "standard", "fine", "extra_fine"]
DETAIL_LEVELS: tuple[DetailLevel, ...] = ("preview", "standard", "fine", "extra_fine")
LoadPattern = Literal["single_bite", "chewing_cycle", "clenching"]
SCENARIO_FORMAT: Literal["dentalsim.scenario/1"] = "dentalsim.scenario/1"
SHARE_TOLERANCE = 1e-9


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AxisAngles(_Model):
    mesiodistal: float = 0.0
    buccolingual: float = 0.0


class Placement(_Model):
    """Offsets applied on top of where the attach point puts the block, in the frame of the socket it attaches to
    (for a block on the jaw, the frame's own axes): see `dentalsim.assembly.placement`."""

    position_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis_angles_deg: AxisAngles = AxisAngles()
    rotation_deg: float = 0.0


class AttachTo(_Model):
    instance_id: str
    attach_point: str


class BlockInstance(_Model):
    instance_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    type_id: str = Field(min_length=1)
    type_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    attach_to: AttachTo | None = None
    placement: Placement = Placement()
    settings: dict[str, SettingValue] = {}


class Direction(_Model):
    """Angle from the axial bite and the azimuth the load comes from, with the conventions sign."""

    angle_deg: float = Field(ge=0, le=90)
    azimuth_deg: float = Field(ge=0, lt=360)


class ContactPoint(_Model):
    """A named occlusal point of a block, or a position on its surface, and its share of the force."""

    block: str
    point: str | None = None
    position_mm: tuple[float, float, float] | None = None
    share: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _point_or_position(self) -> ContactPoint:
        if (self.point is None) == (self.position_mm is None):
            raise ValueError("a contact point needs either a named point or a surface position, not both")
        return self


def _shares_sum_to_one(points: list[ContactPoint], what: str) -> None:
    if not points:
        raise ValueError(f"{what} needs at least one contact point")
    total = sum(p.share for p in points)
    if abs(total - 1.0) > SHARE_TOLERANCE:
        raise ValueError(f"the contact-point shares of {what} must add up to 1, not {total:g}")


class ChewingInstant(_Model):
    """One instant of a chewing cycle: where and how the force acts, and its share of the cycle's force."""

    id: str
    contact_points: list[ContactPoint]
    direction: Direction
    force_share: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _shares(self) -> ChewingInstant:
        _shares_sum_to_one(self.contact_points, f"instant {self.id!r}")
        return self


class LoadCase(_Model):
    """A load pattern (FR-008). Force magnitude and cycle count are library values; the rest are design values.

    - `single_bite` and `clenching` (which also covers bruxism) have one direction and contact points.
    - `chewing_cycle` has a list of instants and a cycle count; its key numbers are those of the worst instant.
    """

    id: str = Field(min_length=1)
    name: str | None = None
    pattern: LoadPattern
    force: LibraryValue
    direction: Direction | None = None
    contact_points: list[ContactPoint] = []
    instants: list[ChewingInstant] = []
    cycles: LibraryValue | None = None

    @model_validator(mode="after")
    def _pattern_fields(self) -> LoadCase:
        if self.pattern == "chewing_cycle":
            if not self.instants:
                raise ValueError(f"chewing cycle {self.id!r} needs at least one instant")
            if self.cycles is None:
                raise ValueError(f"chewing cycle {self.id!r} needs a cycle count from the library")
            if self.direction is not None or self.contact_points:
                raise ValueError(f"chewing cycle {self.id!r} gives directions and contact points per instant")
            if len({i.id for i in self.instants}) != len(self.instants):
                raise ValueError(f"the instants of chewing cycle {self.id!r} need unique ids")
        else:
            if self.direction is None:
                raise ValueError(f"load {self.id!r} needs a direction")
            _shares_sum_to_one(self.contact_points, f"load {self.id!r}")
            if self.instants or self.cycles is not None:
                raise ValueError(f"only a chewing cycle has instants and a cycle count, not {self.pattern}")
        return self

    def all_contact_points(self) -> list[ContactPoint]:
        if self.pattern == "chewing_cycle":
            return [p for instant in self.instants for p in instant.contact_points]
        return list(self.contact_points)


class BrandProduct(_Model):
    name: str
    geometry_source: str


class BrandComparison(_Model):
    """Present only for a neutral comparison of commercial designs (FR-049)."""

    products: list[BrandProduct] = Field(min_length=2)
    disclosures: str = Field(min_length=1)


class Scenario(_Model):
    format: Literal["dentalsim.scenario/1"] = SCENARIO_FORMAT
    name: str = Field(min_length=1)
    conventions_version: str
    detail_level: DetailLevel = "standard"
    blocks: list[BlockInstance]
    loads: list[LoadCase] = []
    brand_comparison: BrandComparison | None = None

    @model_validator(mode="after")
    def _references(self) -> Scenario:
        ids = [b.instance_id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("block instance ids must be unique")
        known = set(ids)
        for block in self.blocks:
            target = block.attach_to.instance_id if block.attach_to is not None else None
            if target is not None and target not in known:
                raise ValueError(f"block {block.instance_id!r} attaches to unknown block {target!r}")
        self._check_no_attach_cycle()
        load_ids = [load.id for load in self.loads]
        if len(load_ids) != len(set(load_ids)):
            raise ValueError("load case ids must be unique")
        for load in self.loads:
            for point in load.all_contact_points():
                if point.block not in known:
                    raise ValueError(f"load {load.id!r} has a contact point on unknown block {point.block!r}")
        return self

    def _check_no_attach_cycle(self) -> None:
        parent = {b.instance_id: b.attach_to.instance_id for b in self.blocks if b.attach_to is not None}
        for start in parent:
            seen, node = {start}, parent.get(start)
            while node is not None:
                if node in seen:
                    raise ValueError(f"block {start!r} is part of an attachment loop")
                seen.add(node)
                node = parent.get(node)

    def block(self, instance_id: str) -> BlockInstance:
        for block in self.blocks:
            if block.instance_id == instance_id:
                return block
        raise KeyError(instance_id)

    def library_values(self) -> list[LibraryValue]:
        """Every library value in the scenario: block settings, then load forces and cycle counts."""
        values = [v for b in self.blocks for v in b.settings.values() if isinstance(v, LibraryValue)]
        for load in self.loads:
            values.append(load.force)
            if load.cycles is not None:
                values.append(load.cycles)
        return values
