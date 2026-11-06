"""The CalculiX input writer (T013): cards, unit load cases, number format, determinism and node order."""

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

import gmsh
import numpy as np
import pytest

from dentalsim.solvers.calculix.inp_writer import (
    CCX_TET10_EDGES,
    GMSH_TO_CCX_TET10,
    MaterialDef,
    PartSet,
    SolverModel,
    SupportSet,
    UnitLoadCase,
    _id_lines,
    ccx_float,
    gmsh_to_ccx_tet10,
    inp_lines,
    write_inp,
)

CORNERS = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


def two_tets() -> SolverModel:
    """Two C3D10 tetrahedra sharing the face (1, 2, 3): one below z = 0, one above."""
    corners = np.vstack([CORNERS, [[0.0, 0.0, -1.0]]])
    tets = [(0, 1, 2, 3), (0, 2, 1, 4)]
    coordinates = [tuple(c) for c in corners]
    index = {c: i for i, c in enumerate(coordinates)}
    connectivity = []
    for tet in tets:
        nodes = list(tet)
        for a, b in CCX_TET10_EDGES:
            mid = tuple((corners[tet[a]] + corners[tet[b]]) / 2)
            if mid not in index:
                index[mid] = len(coordinates)
                coordinates.append(mid)
            nodes.append(index[mid])
        connectivity.append([n + 1 for n in nodes])
    node_ids = np.arange(1, len(coordinates) + 1)
    return SolverModel(
        node_ids=node_ids,
        coordinates=np.array(coordinates),
        element_ids=np.array([1, 2]),
        connectivity=np.array(connectivity),
        materials=(MaterialDef("Ti", 110000.0, 0.34), MaterialDef("Bone", 13700.0, 0.3)),
        parts=(PartSet("Crown", "Ti", np.array([1])), PartSet("Jaw", "Bone", np.array([2]))),
        supports=(SupportSet("Base", np.array([5])), SupportSet("Roller", np.array([2, 3]), dofs=(3,))),
        load_cases=(
            UnitLoadCase("Fx", ((4, 0.25), (1, 0.5), (4, 0.25)), (1.0, 0.0, 0.0)),
            UnitLoadCase("Fz", ((4, 1.0),), (0.0, 0.0, -1.0)),
        ),
        heading="two tets",
    )


def lines_of(model: SolverModel, **kwargs: object) -> list[str]:
    return list(inp_lines(model, **kwargs))  # type: ignore[arg-type]


def steps(lines: list[str]) -> list[list[str]]:
    starts = [i for i, line in enumerate(lines) if line == "*STEP"]
    return [lines[s : lines.index("*END STEP", s) + 1] for s in starts]


def test_gmsh_tet10_permutation_matches_calculix_edges() -> None:
    gmsh.initialize(readConfigFiles=False, interruptible=False)
    try:
        name, dim, order, count, local, primary = gmsh.model.mesh.getElementProperties(11)
    finally:
        gmsh.finalize()
    assert (dim, order, count, primary) == (3, 2, 10, 4)
    gmsh_nodes = np.asarray(local).reshape(10, 3)
    ccx_nodes = gmsh_nodes[list(GMSH_TO_CCX_TET10)]
    np.testing.assert_array_equal(ccx_nodes[:4], CORNERS)  # same reference corners in both codes
    for k, (a, b) in enumerate(CCX_TET10_EDGES, start=4):
        np.testing.assert_allclose(ccx_nodes[k], (CORNERS[a] + CORNERS[b]) / 2)


def test_gmsh_to_ccx_swaps_the_last_two_nodes() -> None:
    gmsh_conn = np.arange(1, 21).reshape(2, 10)
    ccx = gmsh_to_ccx_tet10(gmsh_conn)
    assert ccx[0].tolist() == [1, 2, 3, 4, 5, 6, 7, 8, 10, 9]
    with pytest.raises(ValueError):
        gmsh_to_ccx_tet10(np.zeros((2, 4)))


@pytest.mark.parametrize(
    "value", [0.0, -0.0, 1.0, 0.1, 210000.0, -1.2345678901234567e-05, 1e-300, -9.87654321e123, 2.0 / 3.0]
)
def test_ccx_float_fits_20_characters_and_round_trips(value: float) -> None:
    text = ccx_float(value)
    assert len(text) <= 20
    assert float(text) == pytest.approx(value, rel=1e-12, abs=0.0)


def test_ccx_float_keeps_short_numbers_exact() -> None:
    assert ccx_float(0.1) == "0.1"
    assert ccx_float(210000.0) == "210000.0"
    assert ccx_float(-0.12345678901234568) == "-0.12345678901234568"  # exactly 20 characters
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ccx_float(bad)


def test_model_cards() -> None:
    lines = lines_of(two_tets())
    assert lines[:2] == ["*HEADING", "two tets"]
    assert "*ELEMENT, TYPE=C3D10, ELSET=Eall" in lines
    element_line = lines[lines.index("*ELEMENT, TYPE=C3D10, ELSET=Eall") + 1]
    assert element_line.split(", ")[0] == "1" and len(element_line.split(", ")) == 11
    for card in ("*ELSET, ELSET=Crown", "*ELSET, ELSET=Jaw", "*ELSET, ELSET=M_Bone", "*ELSET, ELSET=M_Ti"):
        assert card in lines
    assert lines[lines.index("*MATERIAL, NAME=Ti") + 1 : lines.index("*MATERIAL, NAME=Ti") + 3] == [
        "*ELASTIC",
        "110000.0, 0.34",
    ]
    assert "*SOLID SECTION, ELSET=M_Ti, MATERIAL=Ti" in lines
    assert "*SOLID SECTION, ELSET=M_Bone, MATERIAL=Bone" in lines
    boundary = lines.index("*BOUNDARY")
    assert lines[boundary + 1 : boundary + 3] == ["Base, 1, 3", "Roller, 3, 3"]
    assert lines[lines.index("*NSET, NSET=Roller") + 1] == "2, 3"


def test_one_independent_step_per_unit_case() -> None:
    first, second = steps(lines_of(two_tets()))
    assert first[:4] == ["*STEP", "*STATIC, SOLVER=PASTIX", "*CLOAD, OP=NEW", "1, 1, 0.5"]
    assert first[4] == "4, 1, 0.5"  # the two weights of node 4 are summed; zero components are left out
    assert second[3:4] == ["4, 3, -1.0"]
    for step in (first, second):
        assert step[step.index("*NODE FILE") + 1] == "U"
        prints = [i for i, line in enumerate(step) if line == "*EL PRINT, ELSET=Eall"]
        assert [step[i + 1] for i in prints] == ["S, E, COORD", "EVOL"]  # EVOL needs its own card
        assert "*NODE PRINT, NSET=Base, TOTALS=ONLY" in step
        assert "*NODE PRINT, NSET=Roller, TOTALS=ONLY" in step
        assert step[step.index("*NODE PRINT, NSET=Base, TOTALS=ONLY") + 1] == "RF"


def test_equation_solver_is_configurable() -> None:
    lines = lines_of(two_tets(), solver="spooles")
    assert lines.count("*STATIC, SOLVER=SPOOLES") == 2
    with pytest.raises(ValueError, match="equation solver"):
        lines_of(two_tets(), solver="mumps")


def test_output_is_deterministic(tmp_path: Path) -> None:
    model = two_tets()
    first = write_inp(model, tmp_path / "a.inp").read_bytes()
    second = write_inp(two_tets(), tmp_path / "b.inp").read_bytes()
    assert first == second
    assert b"\r\n" not in first


def test_real_fields_never_exceed_20_characters(tmp_path: Path) -> None:
    model = two_tets()
    shifted = replace(model, coordinates=model.coordinates * np.pi * 1e-3 - 1.0 / 3.0)
    for line in write_inp(shifted, tmp_path / "x.inp").read_text().splitlines():
        if not line.startswith("*"):
            assert all(len(field.strip()) <= 20 for field in line.split(","))


def test_sets_hold_at_most_16_ids_per_line() -> None:
    lines = list(_id_lines(np.arange(18, 0, -1)))
    assert [len(line.split(", ")) for line in lines] == [16, 2]
    assert lines[1] == "17, 18"  # sorted


def test_extensions_and_extra_cards() -> None:
    class Extra:
        def model_cards(self, model: SolverModel) -> Iterable[str]:
            return ["*SURFACE, NAME=Top", "1, S1"]

        def step_cards(self, model: SolverModel, step: int, case: UnitLoadCase) -> Iterable[str]:
            return [f"** extension step {step} {case.name}"]

    base = two_tets()
    cases = (base.load_cases[0], UnitLoadCase("Pressure", (), (0.0, 0.0, 0.0), extra_cards=("*DLOAD", "Top, P, 1.0")))
    model = replace(base, load_cases=cases, extra_model_cards=("** model extra",))
    lines = lines_of(model, extensions=[Extra()])
    assert lines.index("** model extra") < lines.index("*SURFACE, NAME=Top") < lines.index("*STEP")
    assert lines.index("*BOUNDARY") < lines.index("** model extra")
    second = steps(lines)[1]
    assert second[2:7] == ["*CLOAD, OP=NEW", "*DLOAD", "Top, P, 1.0", "** extension step 2 Pressure", "*NODE FILE"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"parts": (PartSet("Crown", "Ti", np.array([1, 2])), PartSet("Jaw", "Bone", np.array([2])))}, "exactly one"),
        ({"parts": (PartSet("Crown", "Gold", np.array([1])), PartSet("Jaw", "Bone", np.array([2])))}, "undefined"),
        ({"parts": (PartSet("EALL", "Ti", np.array([1])), PartSet("Jaw", "Bone", np.array([2])))}, "clashes"),
        ({"supports": (SupportSet("crown", np.array([5])),)}, "clashes"),
        ({"supports": (SupportSet("bad name", np.array([5])),)}, "letter"),
        ({"supports": (SupportSet("Base", np.array([99])),)}, "not defined"),
        ({"supports": (SupportSet("Base", np.array([5]), dofs=(1, 1)),)}, "dofs"),
        ({"load_cases": (UnitLoadCase("Fx", ((4, 0.5),), (1.0, 0.0, 0.0)),)}, "sum to"),
        ({"load_cases": (UnitLoadCase("Fx", ((4, -1.0), (1, 2.0)), (1.0, 0.0, 0.0)),)}, "negative"),
        ({"load_cases": (UnitLoadCase("Fx", (), (1.0, 0.0, 0.0)),)}, "patch"),
        ({"load_cases": ()}, "no load cases"),
        ({"materials": (MaterialDef("Ti", 110000.0, 0.5), MaterialDef("Bone", 1.0, 0.3))}, "Poisson"),
    ],
)
def test_invalid_models_are_refused_before_writing(tmp_path: Path, change: dict[str, object], message: str) -> None:
    model = replace(two_tets(), **change)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=message):
        write_inp(model, tmp_path / "bad.inp")
    assert not (tmp_path / "bad.inp").exists()


def test_inverted_elements_are_refused() -> None:
    model = two_tets()
    flipped = model.connectivity.copy()
    flipped[1, [1, 2]] = flipped[1, [2, 1]]
    with pytest.raises(ValueError, match="element 2 is inverted"):
        lines_of(replace(model, connectivity=flipped))


def test_dof_counts_three_per_node() -> None:
    assert two_tets().dof == 3 * two_tets().node_ids.size
