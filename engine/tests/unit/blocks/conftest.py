"""Fixtures for the block tests: a gmsh model, settings with test library values, and a placed slice-1a chain.

The library values here are test inputs only (plausible magnitudes); the engine never holds library numbers.
Placement is chained by hand (jaw `site` → implant `platform` → abutment `crown_seat` → crown), as the assembly
(task T066) will do it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from dentalsim.blocks.base import BlockContext, Environment, ParentLink, Socket
from dentalsim.blocks.registry import catalogue
from dentalsim.geometry.occ import session
from dentalsim.geometry.transform import IDENTITY, Transform
from dentalsim.model.settings import DesignValue, LibraryValue, SettingValue

LIBRARY: dict[str, dict[str, float]] = {
    "jaw_segment": {
        "cortical_thickness": 2.0,
        "cortical_E": 13700.0,
        "cortical_nu": 0.3,
        "cancellous_E": 1370.0,
        "cancellous_nu": 0.3,
    },
    "implant": {"E": 105000.0, "nu": 0.37, "strength": 480.0},
    "abutment": {
        "E": 110000.0,
        "nu": 0.34,
        "strength": 880.0,
        "screw_E": 110000.0,
        "screw_nu": 0.34,
        "screw_strength": 880.0,
    },
    "crown": {"E": 210000.0, "nu": 0.3, "strength": 1000.0},
}


def library_value(value: float) -> LibraryValue:
    return LibraryValue(entry_id="01TESTENTRY", entry_version=1, choice="custom", value=value)


@dataclass
class Chain:
    jaw: BlockContext[Any]
    implant: BlockContext[Any]
    abutment: BlockContext[Any]
    crown: BlockContext[Any]


class Kit:
    """Builds settings, contexts and chains for the slice-1a block types."""

    def __init__(self) -> None:
        self.catalogue = catalogue()

    def block(self, type_id: str) -> Any:
        return self.catalogue.latest(type_id)

    def settings(self, type_id: str, **design: Any) -> Any:
        values = {**LIBRARY[type_id], **design}
        return self.block(type_id).settings_model(**values)

    def raw(self, type_id: str, **design: Any) -> dict[str, SettingValue]:
        """Scenario-style setting values: every library setting, plus the given design values."""
        raw: dict[str, SettingValue] = {k: library_value(v) for k, v in LIBRARY[type_id].items()}
        for key, value in design.items():
            raw[key] = DesignValue(value=list(value) if isinstance(value, tuple) else value)
        return raw

    def context(
        self,
        type_id: str,
        instance_id: str | None = None,
        *,
        settings: Any = None,
        to_frame: Transform = IDENTITY,
        parent: Socket | None = None,
        parent_id: str = "parent",
        environment: Environment | None = None,
        **design: Any,
    ) -> BlockContext[Any]:
        block = self.block(type_id)
        return block.context(
            instance_id or type_id,
            settings if settings is not None else self.settings(type_id, **design),
            to_frame=to_frame,
            parent=None if parent is None else ParentLink(parent_id, parent),
            environment=environment or Environment(),
        )

    def jaw_environment(self, jaw: BlockContext[Any]) -> Environment:
        block = self.block("jaw_segment")
        return Environment(bone=block.bone_volumes(jaw), keep_outs=block.keep_out(jaw))

    def chain(
        self,
        *,
        jaw: dict[str, Any] | None = None,
        implant: dict[str, Any] | None = None,
        abutment: dict[str, Any] | None = None,
        crown: dict[str, Any] | None = None,
        implant_offset: Transform = IDENTITY,
    ) -> Chain:
        """A single implant crown on 36 with both neighbours present, each block with these design values."""
        jaw_values = {"region": ("36",), "site": "36", "neighbours": ("35", "37"), **(jaw or {})}
        jaw_ctx = self.context("jaw_segment", "jaw", **jaw_values)
        site = self.block("jaw_segment").sockets(jaw_ctx)[0]
        implant_ctx = self.context(
            "implant",
            "implant-1",
            to_frame=site.frame @ implant_offset,
            parent=site,
            parent_id="jaw",
            environment=self.jaw_environment(jaw_ctx),
            **(implant or {}),
        )
        platform = self.block("implant").sockets(implant_ctx)[0]
        abutment_ctx = self.context(
            "abutment",
            "abutment-1",
            to_frame=platform.frame,
            parent=platform,
            parent_id="implant-1",
            **(abutment or {}),
        )
        seat = self.block("abutment").sockets(abutment_ctx)[0]
        crown_ctx = self.context(
            "crown", "crown-1", to_frame=seat.frame, parent=seat, parent_id="abutment-1", **(crown or {})
        )
        return Chain(jaw_ctx, implant_ctx, abutment_ctx, crown_ctx)


@pytest.fixture
def kit() -> Kit:
    return Kit()


@pytest.fixture
def occ_model() -> Iterator[None]:
    """A fresh gmsh model for one test."""
    with session("block-test"):
        yield
