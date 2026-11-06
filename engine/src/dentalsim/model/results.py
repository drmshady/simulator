"""Stored results (data-model.md "ResultSet", "KeyNumber").

Key numbers come from integration points only: a region mean or a volume-weighted 95th percentile, never a nodal
peak (constitution, Principle II).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from dentalsim.conventions.schema import SectorName

Statistic = Literal["mean", "p95"]
ReferenceKind = Literal["strength", "frost_window"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Reference(_Model):
    """The library value a key number is compared with, and which entry version it came from."""

    kind: ReferenceKind
    parameter_key: str
    entry_id: str
    entry_version: int
    value: float
    units: str


class KeyNumber(_Model):
    id: str
    label: str
    load_case: str
    block: str | None
    region: str
    side: SectorName | None = None
    statistic: Statistic
    value: float
    unit: str
    reference: Reference | None = None
    percent_of_reference: float | None = None
    frost_category: str | None = None
    instant: str | None = None
    """For a chewing cycle, the worst instant, which the key number reports (FR-008)."""


class LockedResult(_Model):
    """A result type that is not reported because a capability it needs is not ready (FR-011a, FR-059)."""

    result_type: str
    block: str | None
    missing_capability: str
    message: str


class FieldArtefact(_Model):
    """A stored field array: per part and unit load case, at integration points or nodes."""

    name: str
    part: str
    unit_case: str | None
    location: Literal["integration_points", "nodes"]
    store_key: str


class VisualAsset(_Model):
    """One `.glb` per part and load case (research R11)."""

    name: str
    part: str
    load_case: str
    store_key: str


class ResultSet(_Model):
    input_hash: str
    fields: list[FieldArtefact]
    combined_cases: list[str]
    key_numbers: list[KeyNumber]
    visual_assets: list[VisualAsset]
    locked_results: list[LockedResult] = []
