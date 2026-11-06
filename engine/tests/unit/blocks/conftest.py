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
from dentalsim.conventions.loader import get_conventions
from dentalsim.geometry.occ import session
from dentalsim.geometry.transform import IDENTITY, Transform
from dentalsim.model.scenario import AttachTo, BlockInstance, Scenario
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
    "pontic": {"E": 210000.0, "nu": 0.3, "strength": 1000.0},
    "cantilever_extension": {"E": 210000.0, "nu": 0.3, "strength": 1000.0},
    "splinting_connector": {"E": 210000.0, "nu": 0.3, "strength": 1000.0},
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

    def scenario(self, *blocks: tuple[str, str, tuple[str, str] | None, dict[str, Any]]) -> Scenario:
        """A scenario of (instance id, type id, (parent, socket) or None, design values), with test library values."""
        instances = [
            BlockInstance(
                instance_id=instance_id,
                type_id=type_id,
                type_version=self.block(type_id).version,
                attach_to=None if parent is None else AttachTo(instance_id=parent[0], attach_point=parent[1]),
                settings=self.raw(type_id, **design),
            )
            for instance_id, type_id, parent, design in blocks
        ]
        return Scenario(name="test", conventions_version=get_conventions().version, blocks=instances)

    def unit_on_implant(
        self, n: int, site: str, crown: dict[str, Any] | None = None
    ) -> list[tuple[str, str, tuple[str, str] | None, dict[str, Any]]]:
        """An implant at `site` of block "jaw", with an abutment and a crown (premolar-sized unless given)."""
        return [
            (f"implant-{n}", "implant", ("jaw", f"site:{site}"), {}),
            (f"abutment-{n}", "abutment", (f"implant-{n}", "platform"), {}),
            (
                f"crown-{n}",
                "crown",
                (f"abutment-{n}", "crown_seat"),
                crown or {"mesiodistal_mm": 6.6, "buccolingual_mm": 8.0},
            ),
        ]

    def bridge(self, *, without: tuple[str, ...] = ()) -> Scenario:
        """A three-unit bridge 34–36 on implants at 34 and 36, a pontic at 35 and a connector on each side of it,
        leaving out the named blocks."""
        blocks = [
            ("jaw", "jaw_segment", None, {"region": ("34", "35", "36"), "site": "34", "neighbours": ("33", "37")}),
            *self.unit_on_implant(1, "34"),
            *self.unit_on_implant(2, "36", {"mesiodistal_mm": 10.5, "buccolingual_mm": 10.0}),
            ("pontic-1", "pontic", ("jaw", "site:35"), {"mesiodistal_mm": 6.6}),
            ("connector-1", "splinting_connector", ("pontic-1", "connector_mesial"), {}),
            ("connector-2", "splinting_connector", ("crown-2", "connector_mesial"), {}),
        ]
        return self.scenario(*(b for b in blocks if b[0] not in without))


@pytest.fixture
def kit() -> Kit:
    return Kit()


@pytest.fixture
def occ_model() -> Iterator[None]:
    """A fresh gmsh model for one test."""
    with session("block-test"):
        yield
