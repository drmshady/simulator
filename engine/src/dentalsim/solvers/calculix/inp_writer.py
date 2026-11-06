"""CalculiX input file (.inp) writer for bonded-elastic unit load cases (research R3, R4).

The input is a solver-neutral `SolverModel`: nodes, 10-node tetrahedra in CalculiX C3D10 node order, parts with
materials, support node sets and unit load cases. The writer produces:

- `*NODE`, `*ELEMENT, TYPE=C3D10, ELSET=Eall`, an `*ELSET` per part and per material (`M_<material>`)
- `*MATERIAL` / `*ELASTIC` (isotropic E, nu) and one `*SOLID SECTION` per material set
- `*NSET` and `*BOUNDARY` for each support set (all three DOF unless the set names fewer, for roller supports)
- one `*STEP` / `*STATIC, SOLVER=...` per unit load case, with `*CLOAD, OP=NEW` spread over the patch nodes, so
  every step is an independent unit case
- per step: `*NODE FILE` U (to .frd); `*EL PRINT` S, E, COORD at the integration points; a separate `*EL PRINT`
  EVOL (whole-element values); `*NODE PRINT, TOTALS=ONLY` RF per support set, for the force-balance check (FR-023)

Extra cards (pressure loads in T072, physics capabilities later) are added through `UnitLoadCase.extra_cards`,
`SolverModel.extra_model_cards`, or an `InpExtension` passed to `write_inp`.

CalculiX reads every real number from the first 20 characters of its field (`read(textpart(i)(1:20),'(f20.0)')`
in ccx 2.23) and every integer from the first 10, so `ccx_float` writes the shortest round-trip form when it fits
in 20 characters and otherwise the most digits that do. The same model always gives a byte-identical file.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import ArrayLike, NDArray

CCX_REAL_WIDTH = 20
CCX_INT_MAX = 999_999_999
CCX_LINE_ENTRIES = 16
ALL_ELEMENTS = "Eall"
MATERIAL_SET_PREFIX = "M_"
DEFAULT_SOLVER = "PASTIX"  # open source, and prints its residual (research R3)
EQUATION_SOLVERS = ("PARDISO", "SPOOLES", "PASTIX", "ITERATIVE SCALING", "ITERATIVE CHOLESKY")
WEIGHT_TOLERANCE = 1e-9

# gmsh numbers the last two edge nodes of its 10-node tetrahedron (element type 11) on edges 2-3 and 1-3 (0-based
# corners); CalculiX and Abaqus C3D10 put edge 1-3 before edge 2-3. CalculiX C3D10, 0-based: corners 0-3, then the
# midside nodes of edges 0-1, 1-2, 2-0, 0-3, 1-3, 2-3 (shape10tet.f). So ccx_connectivity = gmsh[:, GMSH_TO_CCX_TET10].
GMSH_TO_CCX_TET10: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 9, 8)
CCX_TET10_EDGES: tuple[tuple[int, int], ...] = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{0,59}")


def gmsh_to_ccx_tet10(connectivity: ArrayLike) -> NDArray[np.int64]:
    """Reorder gmsh 10-node tetrahedron connectivity, shape (M, 10), into CalculiX C3D10 node order."""
    conn = np.asarray(connectivity, dtype=np.int64)
    if conn.ndim != 2 or conn.shape[1] != 10:
        raise ValueError(f"expected connectivity of shape (M, 10), got {conn.shape}")
    return conn[:, list(GMSH_TO_CCX_TET10)]


def ccx_float(value: float) -> str:
    """A finite real number in at most 20 characters, with as many significant digits as fit."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"CalculiX cannot read the non-finite number {value!r}")
    text = repr(number)
    if len(text) <= CCX_REAL_WIDTH:
        return text
    for digits in range(16, 0, -1):
        text = f"{number:.{digits}e}"
        if len(text) <= CCX_REAL_WIDTH:
            return text
    raise AssertionError("unreachable: a one-digit exponent form always fits")


def _ids(values: ArrayLike, what: str) -> NDArray[np.int64]:
    array = np.asarray(values, dtype=np.int64).reshape(-1)
    if array.size and (array.min() < 1 or array.max() > CCX_INT_MAX):
        raise ValueError(f"{what}: ids must be between 1 and {CCX_INT_MAX}")
    return array


@dataclass(frozen=True)
class MaterialDef:
    """An isotropic linear elastic material. Units follow the conventions document (mm, N, MPa)."""

    name: str
    youngs_modulus: float
    poissons_ratio: float


@dataclass(frozen=True)
class PartSet:
    """The elements of one part (block region), all with one material."""

    name: str
    material: str
    element_ids: NDArray[np.int64]

    def __post_init__(self) -> None:
        object.__setattr__(self, "element_ids", _ids(self.element_ids, f"part {self.name}"))


@dataclass(frozen=True)
class SupportSet:
    """Nodes held fixed. `dofs` are 1-based (1 = x, 2 = y, 3 = z); fewer than three make a roller support."""

    name: str
    node_ids: NDArray[np.int64]
    dofs: tuple[int, ...] = (1, 2, 3)

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_ids", _ids(self.node_ids, f"support {self.name}"))


@dataclass(frozen=True)
class UnitLoadCase:
    """One solver step: `force` (N) spread over the patch, each node getting force × weight; weights sum to 1.

    A case with no patch must have a zero force; it can still carry loads in `extra_cards` (for example `*DLOAD`),
    which are written after the `*CLOAD, OP=NEW` card of the step.
    """

    name: str
    patch: tuple[tuple[int, float], ...]
    force: tuple[float, float, float]
    extra_cards: tuple[str, ...] = ()


@dataclass(frozen=True)
class SolverModel:
    """A solver-neutral model of 10-node tetrahedra. `connectivity` holds node ids in CalculiX C3D10 order."""

    node_ids: NDArray[np.int64]
    coordinates: NDArray[np.float64]
    element_ids: NDArray[np.int64]
    connectivity: NDArray[np.int64]
    materials: tuple[MaterialDef, ...]
    parts: tuple[PartSet, ...]
    supports: tuple[SupportSet, ...]
    load_cases: tuple[UnitLoadCase, ...]
    heading: str = ""
    extra_model_cards: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_ids", _ids(self.node_ids, "nodes"))
        object.__setattr__(self, "coordinates", np.asarray(self.coordinates, dtype=np.float64))
        object.__setattr__(self, "element_ids", _ids(self.element_ids, "elements"))
        object.__setattr__(self, "connectivity", np.asarray(self.connectivity, dtype=np.int64))
        for name in ("materials", "parts", "supports", "load_cases", "extra_model_cards"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def dof(self) -> int:
        """Degrees of freedom before supports are applied: 3 per node."""
        return 3 * int(self.node_ids.size)


class InpExtension(Protocol):
    """Adds cards to the input file. Model cards go after the supports, step cards after the step's loads."""

    def model_cards(self, model: SolverModel) -> Iterable[str]: ...

    def step_cards(self, model: SolverModel, step: int, case: UnitLoadCase) -> Iterable[str]: ...


def validate_model(model: SolverModel) -> None:
    """Raise ValueError naming the first inconsistency CalculiX would reject or silently misread."""
    nodes = model.node_ids
    if model.coordinates.shape != (nodes.size, 3):
        raise ValueError(f"coordinates must have shape ({nodes.size}, 3), got {model.coordinates.shape}")
    if not np.all(np.isfinite(model.coordinates)):
        raise ValueError("coordinates must be finite")
    if np.unique(nodes).size != nodes.size:
        raise ValueError("node ids must be unique")
    elements = model.element_ids
    if model.connectivity.shape != (elements.size, 10):
        raise ValueError(f"connectivity must have shape ({elements.size}, 10), got {model.connectivity.shape}")
    if np.unique(elements).size != elements.size:
        raise ValueError("element ids must be unique")
    if not np.all(np.isin(model.connectivity, nodes)):
        raise ValueError("connectivity refers to node ids that are not defined")
    order = np.argsort(nodes)
    rows = order[np.searchsorted(nodes, model.connectivity[:, :4], sorter=order)]
    a, b, c, d = (model.coordinates[rows[:, k]] for k in range(4))
    volumes6 = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)
    if np.any(volumes6 <= 0):
        bad = int(model.element_ids[np.argmax(volumes6 <= 0)])
        raise ValueError(f"element {bad} is inverted or flat: its corners must follow the CalculiX C3D10 order")

    names: dict[str, str] = {ALL_ELEMENTS.upper(): ALL_ELEMENTS}

    def claim(name: str, what: str) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError(f"{what} name {name!r}: use a letter, then letters, digits, _ or -, up to 60 characters")
        key = name.upper()  # CalculiX upper-cases all names
        if key in names:
            raise ValueError(f"{what} name {name!r} clashes with {names[key]!r} (names are not case-sensitive)")
        names[key] = name

    materials: dict[str, MaterialDef] = {}
    for material in model.materials:
        if not _NAME.fullmatch(material.name):
            raise ValueError(f"material name {material.name!r} is not a valid CalculiX name")
        if material.name.upper() in {m.upper() for m in materials}:
            raise ValueError(f"material {material.name!r} is defined twice")
        if not (material.youngs_modulus > 0 and math.isfinite(material.youngs_modulus)):
            raise ValueError(f"material {material.name!r}: Young's modulus must be positive")
        if not -1.0 < material.poissons_ratio < 0.5:
            raise ValueError(f"material {material.name!r}: Poisson's ratio must be between -1 and 0.5")
        materials[material.name] = material
    for part in model.parts:
        claim(part.name, "part")
        if part.material not in materials:
            raise ValueError(f"part {part.name!r} uses the undefined material {part.material!r}")
    for name in sorted({part.material for part in model.parts}):
        claim(MATERIAL_SET_PREFIX + name, "material set")

    assigned = np.concatenate([part.element_ids for part in model.parts]) if model.parts else np.empty(0, np.int64)
    if assigned.size != elements.size or not np.array_equal(np.sort(assigned), np.sort(elements)):
        raise ValueError("every element must belong to exactly one part")

    for support in model.supports:
        claim(support.name, "support")
        if not support.node_ids.size:
            raise ValueError(f"support {support.name!r} has no nodes")
        if not np.all(np.isin(support.node_ids, nodes)):
            raise ValueError(f"support {support.name!r} refers to node ids that are not defined")
        if (
            not support.dofs
            or any(d not in (1, 2, 3) for d in support.dofs)
            or len(set(support.dofs)) != len(support.dofs)
        ):
            raise ValueError(f"support {support.name!r}: dofs must be distinct values among 1, 2, 3")

    if not model.load_cases:
        raise ValueError("the model has no load cases")
    node_set = set(nodes.tolist())
    for case in model.load_cases:
        _patch_forces(case, node_set)


def _patch_forces(case: UnitLoadCase, node_set: set[int]) -> list[tuple[int, int, float]]:
    """(node, dof, value) lines for a case, duplicates summed, in node then dof order; zero components left out."""
    force = tuple(float(f) for f in case.force)
    if len(force) != 3 or not all(math.isfinite(f) for f in force):
        raise ValueError(f"load case {case.name!r}: force must be three finite numbers")
    if not case.patch:
        if any(force):
            raise ValueError(f"load case {case.name!r}: a force needs patch nodes")
        return []
    weights: dict[int, float] = {}
    for node, weight in case.patch:
        if not (math.isfinite(weight) and weight >= 0.0):
            raise ValueError(f"load case {case.name!r}: weights must be finite and not negative")
        if int(node) not in node_set:
            raise ValueError(f"load case {case.name!r}: patch node {node} is not defined")
        weights[int(node)] = weights.get(int(node), 0.0) + float(weight)
    total = math.fsum(weights.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"load case {case.name!r}: patch weights sum to {total!r}, not 1")
    lines = []
    for node in sorted(weights):
        for dof, component in enumerate(force, start=1):
            value = component * weights[node]
            if value != 0.0:
                lines.append((node, dof, value))
    return lines


def _id_lines(ids: NDArray[np.int64]) -> Iterator[str]:
    values = np.unique(ids).tolist()  # sorted, so the output does not depend on input order
    for start in range(0, len(values), CCX_LINE_ENTRIES):
        yield ", ".join(str(v) for v in values[start : start + CCX_LINE_ENTRIES])


def _dof_ranges(dofs: Sequence[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for dof in sorted(dofs):
        if ranges and dof == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], dof)
        else:
            ranges.append((dof, dof))
    return ranges


def _solver_card(solver: str) -> str:
    name = solver.strip().upper()
    if name not in EQUATION_SOLVERS:
        raise ValueError(f"unknown equation solver {solver!r}; expected one of {', '.join(EQUATION_SOLVERS)}")
    return f"*STATIC, SOLVER={name}"


def inp_lines(
    model: SolverModel, *, solver: str = DEFAULT_SOLVER, extensions: Sequence[InpExtension] = ()
) -> Iterator[str]:
    """The lines of the input file, without line endings. The model is validated before the first line."""
    validate_model(model)
    return _lines(model, _solver_card(solver), tuple(extensions))


def _lines(model: SolverModel, static_card: str, extensions: tuple[InpExtension, ...]) -> Iterator[str]:
    node_set = set(model.node_ids.tolist())

    yield "*HEADING"
    yield model.heading.replace("\n", " ").strip() or "dentalsim model"
    yield "** Written by dentalsim; units: mm, N, MPa"

    yield "*NODE"
    for node, (x, y, z) in zip(model.node_ids.tolist(), model.coordinates.tolist(), strict=True):
        yield f"{node}, {ccx_float(x)}, {ccx_float(y)}, {ccx_float(z)}"

    yield f"*ELEMENT, TYPE=C3D10, ELSET={ALL_ELEMENTS}"
    for element, nodes in zip(model.element_ids.tolist(), model.connectivity.tolist(), strict=True):
        yield ", ".join(str(v) for v in (element, *nodes))

    for part in model.parts:
        yield f"*ELSET, ELSET={part.name}"
        yield from _id_lines(part.element_ids)
    by_material: dict[str, list[NDArray[np.int64]]] = {}
    for part in model.parts:
        by_material.setdefault(part.material, []).append(part.element_ids)
    for material_name in sorted(by_material):
        yield f"*ELSET, ELSET={MATERIAL_SET_PREFIX}{material_name}"
        yield from _id_lines(np.concatenate(by_material[material_name]))

    for material in model.materials:
        yield f"*MATERIAL, NAME={material.name}"
        yield "*ELASTIC"
        yield f"{ccx_float(material.youngs_modulus)}, {ccx_float(material.poissons_ratio)}"
    for material_name in sorted(by_material):
        yield f"*SOLID SECTION, ELSET={MATERIAL_SET_PREFIX}{material_name}, MATERIAL={material_name}"

    for support in model.supports:
        yield f"*NSET, NSET={support.name}"
        yield from _id_lines(support.node_ids)
    if model.supports:
        yield "*BOUNDARY"
        for support in model.supports:
            for first, last in _dof_ranges(support.dofs):
                yield f"{support.name}, {first}, {last}"

    yield from model.extra_model_cards
    for extension in extensions:
        yield from extension.model_cards(model)

    for step, case in enumerate(model.load_cases, start=1):
        yield f"** Step {step}: unit load case {case.name}"
        yield "*STEP"
        yield static_card
        yield "*CLOAD, OP=NEW"
        for node, dof, value in _patch_forces(case, node_set):
            yield f"{node}, {dof}, {ccx_float(value)}"
        yield from case.extra_cards
        for extension in extensions:
            yield from extension.step_cards(model, step, case)
        yield "*NODE FILE"
        yield "U"
        yield f"*EL PRINT, ELSET={ALL_ELEMENTS}"
        yield "S, E, COORD"
        # Whole-element variables cannot share an *EL PRINT card with integration-point variables.
        yield f"*EL PRINT, ELSET={ALL_ELEMENTS}"
        yield "EVOL"
        for support in model.supports:
            yield f"*NODE PRINT, NSET={support.name}, TOTALS=ONLY"
            yield "RF"
        yield "*END STEP"


def write_inp(
    model: SolverModel, path: Path, *, solver: str = DEFAULT_SOLVER, extensions: Sequence[InpExtension] = ()
) -> Path:
    """Write the input file with LF line endings and return its path. Nothing is written if the model is invalid."""
    lines = inp_lines(model, solver=solver, extensions=extensions)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as stream:
        for line in lines:
            stream.write(line)
            stream.write("\n")
    return path
