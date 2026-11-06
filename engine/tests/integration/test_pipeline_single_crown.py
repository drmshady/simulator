"""The single implant crown through the whole pipeline (task T083; Story 1; SC-007, FR-008, FR-022, FR-023).

Marked `benchmark`: it needs the solver. The physics gate is opened with a capability store that marks bonded
elastic ready for the current required cases, as a passing verification report would.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from dentalsim.persistence import CapabilityStatus
from dentalsim.physics.registry import read_case_headers, required_cases_in
from dentalsim.pipeline import PipelineRequest, PipelineResult, run_pipeline
from dentalsim.post.gltf import ATTRIBUTES, read_attributes
from dentalsim.solvers.calculix.build import solver_build
from dentalsim.store.cas import ContentStore
from dentalsim.templates.loader import builtin_template, open_template
from dentalsim.testing import SnapshotLibrary

pytestmark = pytest.mark.benchmark
FROST = ("frost.disuse_upper", "frost.adapted_upper", "frost.mild_overload_upper")


class ReadyRepo:
    def __init__(self) -> None:
        cases = list(required_cases_in("bonded_elastic", read_case_headers()))
        self.rows = {
            "bonded_elastic": CapabilityStatus(
                capability_id="bonded_elastic", status="ready", report_id="R1", cases=cases
            )
        }

    def get(self, capability_id: str) -> CapabilityStatus | None:
        return self.rows.get(capability_id)

    def set(self, status: CapabilityStatus) -> None:
        self.rows[status.capability_id] = status

    def list_all(self) -> list[CapabilityStatus]:
        return list(self.rows.values())


@pytest.fixture(scope="module")
def solved(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[PipelineResult, ContentStore, PipelineRequest]]:
    root = tmp_path_factory.mktemp("pipeline")
    library = SnapshotLibrary()
    opened = open_template(builtin_template("single_crown_implant"), library)
    request = PipelineRequest(
        opened.scenario,
        "preview",
        "preview",
        [r.version_ref() for r in opened.library],
        [library.resolve(k) for k in FROST],
    )
    store = ContentStore(root / "store")
    build = solver_build().build_id
    result = run_pipeline(
        request, capabilities=ReadyRepo(), store=store, work_root=root / "work", solver_build_id=build, threads=2
    )
    yield result, store, request


def _solve(request: PipelineRequest, store: ContentStore, root: Path, **changes: object) -> PipelineResult:
    return run_pipeline(
        request, capabilities=ReadyRepo(), store=store, work_root=root, solver_build_id=solver_build().build_id,
        threads=2, use_cache=False, **changes,  # type: ignore[arg-type]
    )  # fmt: skip


def test_a_preview_has_labelled_key_numbers_and_the_screw_locked(
    solved: tuple[PipelineResult, ContentStore, PipelineRequest],
) -> None:
    result, store, _ = solved
    numbers = result.result.key_numbers
    assert {n.load_case for n in numbers} == {"axial", "oblique"}
    assert {n.statistic for n in numbers} == {"mean", "p95"}
    crestal = [n for n in numbers if n.region == "crestal_ring"]
    assert len(crestal) == 4 and all(n.side and n.frost_category and n.unit == "µε" for n in crestal)
    stress = [n for n in numbers if n.reference is not None]
    assert {n.region for n in stress} == {"implant_neck", "implant", "abutment", "crown"}
    assert all(n.percent_of_reference is not None and n.percent_of_reference > 0 for n in stress)
    assert not any(n.region == "screw" and n.unit == "MPa" for n in numbers)
    locked = {(r.block, r.result_type): r.message for r in result.locked_results}
    assert locked[("abutment-1", "screw_stress")] == "not reported: needs screw preload"
    for balance in result.convergence.force_balance:
        assert balance.relative_error <= 1e-6
    assert all(e.relative_error <= 1e-4 for e in result.convergence.energy_balance)
    assert store.is_complete(result.input_hash)
    glb = store.get_bytes(result.input_hash, result.result.visual_assets[0].name)
    gltf, arrays = read_attributes(glb)
    assert set(ATTRIBUTES) <= set(arrays) and arrays["MORPH"].shape == arrays["POSITION"].shape


def test_a_stored_preview_is_reused(
    solved: tuple[PipelineResult, ContentStore, PipelineRequest], tmp_path: Path
) -> None:
    result, store, request = solved
    again = run_pipeline(
        request,
        capabilities=ReadyRepo(),
        store=store,
        work_root=tmp_path,
        solver_build_id=result.versions.solver_build_id,
    )
    assert again.cached and again.input_hash == result.input_hash


def test_solving_again_reproduces_the_key_numbers(
    solved: tuple[PipelineResult, ContentStore, PipelineRequest], tmp_path: Path
) -> None:
    result, store, request = solved
    again = _solve(request, ContentStore(tmp_path / "store"), tmp_path / "work")
    first = {(n.load_case, n.id): n.value for n in result.result.key_numbers}
    second = {(n.load_case, n.id): n.value for n in again.result.key_numbers}
    assert first.keys() == second.keys()
    for key, value in first.items():
        assert second[key] == pytest.approx(value, rel=1e-3), key  # SC-007: within 0.1%


def test_unit_cases_per_axis_superpose_to_the_combined_solve(
    solved: tuple[PipelineResult, ContentStore, PipelineRequest], tmp_path: Path
) -> None:
    """A run's 3 unit cases per contact point, combined, give what one solve of each load case gives (research R4)."""
    result, _, request = solved
    per_axis = PipelineRequest(request.scenario, "preview", "run", request.library_versions, request.frost_edges)
    run = _solve(per_axis, ContentStore(tmp_path / "store"), tmp_path / "work")
    assert len(run.convergence.force_balance) == 6
    combined = {(n.load_case, n.id): n.value for n in result.result.key_numbers}
    superposed = {(n.load_case, n.id): n.value for n in run.result.key_numbers}
    assert combined.keys() == superposed.keys()
    for key, value in combined.items():
        assert np.isclose(superposed[key], value, rtol=1e-4, atol=1e-6), (key, superposed[key], value)
