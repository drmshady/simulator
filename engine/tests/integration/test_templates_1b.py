"""The slice 1b templates (task T105; Story 1 Acceptance 2 and 3, quickstart row 1b).

- Each template opens on its own jaw segment with no misfit and passes the full validity check, and reports
  `template_misfit` on a segment it does not suit.
- Deleting either implant of the cantilever bridge (with the blocks attached to it, as the builder does) reports
  the part left unsupported; so does deleting either implant of the bridge.
- Each template's preview solves (marker `benchmark`: it needs the solver), every connector, pontic and cantilever
  reports its stress, and every implant its crestal strain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dentalsim.assembly.validity import check_validity
from dentalsim.model.scenario import Scenario
from dentalsim.model.settings import DesignValue
from dentalsim.pipeline import PipelineRequest, run_pipeline
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.store.cas import ContentStore
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import SnapshotLibrary

from .test_pipeline_single_crown import FROST, ReadyRepo

TEMPLATES = ("splinted_crowns", "bridge_implant", "cantilever_bridge", "full_arch_fixed")


def opened(template_id: str, **jaw: object) -> Scenario:
    settings = {k: DesignValue(value=list(v) if isinstance(v, tuple) else v) for k, v in jaw.items()}  # type: ignore[arg-type]
    result = open_template(builtin_template(template_id), SnapshotLibrary(), jaw_settings=settings or None)
    assert not jaw or result.misfits, "a changed jaw segment should not fit"
    return result.scenario


def without(scenario: Scenario, instance_id: str) -> Scenario:
    """The scenario less a block, the blocks attached below it and their contact points (as the builder removes)."""
    gone, grew = {instance_id}, True
    while grew:
        grew = False
        for block in scenario.blocks:
            if block.attach_to and block.attach_to.instance_id in gone and block.instance_id not in gone:
                gone.add(block.instance_id)
                grew = True
    loads = [
        load.model_copy(update={"contact_points": [p for p in load.contact_points if p.block not in gone]})
        for load in scenario.loads
        if any(p.block not in gone for p in load.contact_points)
    ]
    blocks = [b for b in scenario.blocks if b.instance_id not in gone]
    return scenario.model_copy(update={"blocks": blocks, "loads": loads})


@pytest.mark.parametrize("template_id", TEMPLATES)
def test_each_template_is_valid_on_its_own_segment(template_id: str) -> None:
    result = open_template(builtin_template(template_id), SnapshotLibrary())
    assert result.misfits == []
    validity = check_validity(result.scenario, mode="full")
    assert validity.ok, validity.problems


@pytest.mark.parametrize(
    ("template_id", "jaw", "says"),
    [
        ("splinted_crowns", {"region": ("36",), "site": "36"}, "spanning 2 teeth"),
        ("bridge_implant", {"region": ("44", "45", "46"), "site": "44"}, "outside the region (44, 45, 46)"),
        (
            "cantilever_bridge",
            {"region": ("35", "36", "37"), "site": "36", "nerve_canal_depth_mm": 11.0},
            "nerve canal",
        ),
        ("full_arch_fixed", {"region": ("34", "35", "36"), "site": "34"}, "spanning 12 teeth"),
    ],
    ids=TEMPLATES,
)
def test_a_template_on_an_unsuitable_segment_says_what_must_change(template_id: str, jaw: dict, says: str) -> None:
    template = builtin_template(template_id)
    base = {b.instance_id: b for b in template.blocks}["jaw"].settings
    settings = {k: v for k, v in base.items() if isinstance(v, DesignValue)}
    settings |= {k: DesignValue(value=list(v) if isinstance(v, tuple) else v) for k, v in jaw.items()}
    misfits = open_template(template, SnapshotLibrary(), jaw_settings=settings).misfits
    assert misfits and all(p.code == "template_misfit" for p in misfits)
    assert any(says in p.message for p in misfits), [p.message for p in misfits]


@pytest.mark.parametrize(
    ("template_id", "implant", "unheld"),
    [
        ("cantilever_bridge", "implant-1", {"cantilever-1", "connector-2"}),
        ("cantilever_bridge", "implant-2", {"connector-1"}),
        ("bridge_implant", "implant-1", {"pontic-1", "connector-1"}),
        ("bridge_implant", "implant-2", {"pontic-1"}),
    ],
)
def test_deleting_an_implant_reports_the_part_left_unsupported(template_id: str, implant: str, unheld: set) -> None:
    scenario = without(open_template(builtin_template(template_id), SnapshotLibrary()).scenario, implant)
    for mode in ("fast", "full"):
        problems = check_validity(scenario, mode=mode).problems
        assert {p.block for p in problems if p.code == "unsupported_pontic"} == unheld, problems
        assert all(p.location_mm is not None for p in problems)


@pytest.mark.benchmark
@pytest.mark.parametrize("template_id", TEMPLATES)
def test_each_template_preview_solves(template_id: str, tmp_path: Path) -> None:
    library = SnapshotLibrary()
    made = open_template(builtin_template(template_id), library)
    request = PipelineRequest(
        made.scenario,
        "preview",
        "preview",
        [r.version_ref() for r in made.library],
        [library.resolve(k) for k in FROST],
    )
    run = run_pipeline(
        request,
        capabilities=ReadyRepo(),
        store=ContentStore(tmp_path / "store"),
        work_root=tmp_path / "work",
        solver_build_id=solver_build().build_id,
        threads=2,
    )
    numbers = run.result.key_numbers
    assert {n.load_case for n in numbers} == {load.id for load in made.scenario.loads}
    reported = {(n.block, n.region) for n in numbers}
    for block in made.scenario.blocks:
        region = {"pontic": "pontic", "splinting_connector": "connector", "cantilever_extension": "cantilever"}.get(
            block.type_id
        )
        if region is not None:
            assert (block.instance_id, region) in reported, block.instance_id
        if block.type_id == "implant":
            assert (block.instance_id, "crestal_ring") in reported, block.instance_id
    assert {r.result_type for r in run.locked_results if (r.block or "").startswith("abutment")} >= {"screw_stress"}
    assert run.convergence
