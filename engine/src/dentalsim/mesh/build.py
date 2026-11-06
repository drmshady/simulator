"""Meshing a joined model into 10-node tetrahedra (research R2; FR-018, FR-023).

1. The level's size fields (`dentalsim.mesh.levels`) and a first-order mesh, single-threaded with a fixed seed.
2. **Quality**: the smallest scaled Jacobian (minimum SICN) of the straight tetrahedra must be at least
   `MIN_QUALITY`; otherwise meshing fails with a plain reason naming the part.
3. **Second order**: the edge nodes are placed on the curved geometry. Where that makes an element's Jacobian
   fall to `MIN_CURVED_JACOBIAN` of its straight value or below at an integration point (a thin feature, a tight
   curve), the element's edges are made straight again, so every element is valid for the solver.
4. **Connectivity** (FR-023): every part must reach a supported part through shared faces, not only shared nodes
   or edges; otherwise the run fails before the solve, naming the part ("Crown 'crown-1' does not touch Abutment
   'abutment-1'").
5. **Extraction** in CalculiX numbering and C3D10 node order: nodes, elements by part, support node sets, and the
   faces of the model's outer surface with the part each belongs to.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import gmsh
import numpy as np
from numpy.typing import NDArray

from dentalsim.errors import DentalsimError
from dentalsim.geometry.assemble import AssembledModel, solver_name
from dentalsim.geometry.transform import Point
from dentalsim.mesh.levels import MeshLevel, add_size_fields, set_mesh_options
from dentalsim.mesh.load_patch import TET10_FACES
from dentalsim.model.scenario import DetailLevel
from dentalsim.post.integration import SHAPE_DERIVATIVES
from dentalsim.solvers.calculix.inp_writer import CCX_TET10_EDGES, GMSH_TO_CCX_TET10

MIN_QUALITY = 0.01
"""The smallest acceptable scaled Jacobian (SICN) of a straight tetrahedron."""
MIN_CURVED_JACOBIAN = 0.05
"""A curved element whose Jacobian falls to this fraction of its straight value is made straight."""
MAX_STRAIGHTENING_PASSES = 10
GMSH_TET4, GMSH_TET10 = 4, 11
_FLIP = np.array([0, 2, 1, 3, 6, 5, 4, 7, 9, 8])
"""Reverses a C3D10 element (swaps corners 2 and 3 and their edge nodes)."""

IntArray = NDArray[np.int64]
FloatArray = NDArray[np.float64]
Progress = Callable[[str], None]


class MeshFailed(DentalsimError):
    """Meshing did not give a model fit to solve. `reason` is plain language for the owner."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, eq=False)
class MeshPart:
    key: str
    instance_id: str
    block_label: str
    name: str
    component: str
    material: str
    e_mpa: float
    nu: float
    strength_setting: str | None
    set_name: str
    element_ids: IntArray


@dataclass(frozen=True, eq=False)
class MeshSupport:
    name: str
    instance_id: str
    set_name: str
    node_ids: IntArray


@dataclass(frozen=True, eq=False)
class MeshedModel:
    level: DetailLevel
    node_ids: IntArray
    """1..n."""
    coordinates: FloatArray
    element_ids: IntArray
    """1..m, part by part."""
    connectivity: IntArray
    """(m, 10) node ids in C3D10 order."""
    element_part: IntArray
    """(m,) the index in `parts` of each element's part."""
    parts: tuple[MeshPart, ...]
    supports: tuple[MeshSupport, ...]
    outer_faces: IntArray
    """(k, 6) node ids of the faces on the model's outer surface, corners counter-clockwise seen from outside."""
    outer_face_part: IntArray
    """(k,) the index in `parts` of each outer face's part."""
    min_quality: float
    straightened: int
    """Elements whose edges were made straight to keep them valid."""
    time_s: float

    @property
    def dof(self) -> int:
        return 3 * int(self.node_ids.size)

    def part_index(self, key: str) -> int:
        for index, part in enumerate(self.parts):
            if part.key == key:
                return index
        raise KeyError(key)

    def outer_faces_of(self, instance_id: str) -> IntArray:
        """The outer-surface faces of a block instance's parts."""
        wanted = [i for i, p in enumerate(self.parts) if p.instance_id == instance_id]
        return np.asarray(self.outer_faces[np.isin(self.outer_face_part, wanted)], dtype=np.int64)

    def part_faces(self, index: int) -> IntArray:
        """The boundary faces of one part's own elements (its outer surface and its interfaces), facing out of
        the part."""
        elements = np.flatnonzero(self.element_part == index)
        faces, _ = boundary_faces_with_owner(self.connectivity[elements])
        return faces


def element_faces(connectivity: IntArray) -> tuple[IntArray, IntArray]:
    """Every face (4 per element) as 6 node ids, facing out of its element, and the element row of each."""
    conn = np.asarray(connectivity, dtype=np.int64)
    inward = conn[:, np.array(TET10_FACES)].reshape(-1, 6)
    # TET10_FACES list each face counter-clockwise seen from inside a positively oriented element: reverse them.
    outward = inward[:, [0, 2, 1, 5, 4, 3]]
    return np.asarray(outward, dtype=np.int64), np.repeat(np.arange(conn.shape[0]), 4)


def boundary_faces_with_owner(connectivity: IntArray) -> tuple[IntArray, IntArray]:
    """The faces that belong to exactly one element, facing outward, with that element's row."""
    faces, owner = element_faces(connectivity)
    keys = np.sort(faces[:, :3], axis=1)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    single = counts[inverse.ravel()] == 1
    return faces[single], owner[single]


def _straighten(coords: FloatArray, conn_rows: IntArray) -> int:
    """Make curved elements straight where their Jacobian is too small; returns how many were straightened."""
    straightened: set[int] = set()
    for _ in range(MAX_STRAIGHTENING_PASSES):
        x = coords[conn_rows]  # (m, 10, 3)
        corners = x[:, :4]
        linear = np.einsum(
            "ij,ij->i",
            np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
            corners[:, 3] - corners[:, 0],
        )
        jac = np.linalg.det(np.einsum("pka,mkb->mpab", SHAPE_DERIVATIVES, x))  # (m, 4)
        bad = np.flatnonzero(jac.min(axis=1) <= MIN_CURVED_JACOBIAN * linear)
        if not bad.size:
            return len(straightened)
        for k, (a, b) in enumerate(CCX_TET10_EDGES, start=4):
            rows = conn_rows[bad]
            coords[rows[:, k]] = 0.5 * (coords[rows[:, a]] + coords[rows[:, b]])
        straightened.update(bad.tolist())
    raise MeshFailed("the mesh has elements that stay invalid even with straight edges; try another detail level")


def _quality_check(volume_tags: dict[int, int], parts_labels: Sequence[str]) -> float:
    types, tags, _ = gmsh.model.mesh.getElements(3)
    all_tags = np.concatenate([np.asarray(t, dtype=np.int64) for t in tags]) if tags else np.empty(0, np.int64)
    if not all_tags.size:
        raise MeshFailed("meshing produced no elements")
    quality = np.asarray(gmsh.model.mesh.getElementQualities(all_tags, "minSICN"), dtype=np.float64)
    worst = int(np.argmin(quality))
    if quality[worst] >= MIN_QUALITY:
        return float(quality[worst])
    _, _, dim, entity = gmsh.model.mesh.getElement(int(all_tags[worst]))
    where = parts_labels[volume_tags[entity]] if dim == 3 and entity in volume_tags else "the model"
    raise MeshFailed(
        f"the mesh has a badly shaped element in {where} (quality {quality[worst]:.3f}, at least {MIN_QUALITY:g} is "
        "needed); a thin or very small feature there is probably the cause, so try another detail level or change "
        "the block's settings"
    )


def _check_connected(
    connectivity: IntArray,
    element_part: IntArray,
    parts: Sequence[MeshPart],
    supported: set[int],
    labels: dict[str, str],
    parent_of: dict[str, str | None],
) -> None:
    faces, owner = element_faces(connectivity)
    keys = np.sort(faces[:, :3], axis=1)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    shared = counts[inverse] == 2
    order = np.argsort(inverse[shared], kind="stable")
    owners = element_part[owner[shared][order]].reshape(-1, 2)
    links: dict[int, set[int]] = {i: set() for i in range(len(parts))}
    for a, b in {(int(a), int(b)) for a, b in owners if a != b}:
        links[a].add(b)
        links[b].add(a)
    reached, queue = set(supported), deque(supported)
    while queue:
        for nxt in links[queue.popleft()] - reached:
            reached.add(nxt)
            queue.append(nxt)
    for index, part in enumerate(parts):
        if index in reached:
            continue
        parent = parent_of.get(part.instance_id)
        touches = f"does not touch {labels[parent]}" if parent else "is not joined to any supported part"
        raise MeshFailed(
            f"{part.block_label} ({part.name}) {touches} through a shared face, so nothing holds it; check how it "
            "is placed"
        )


def build_mesh(
    model: AssembledModel,
    level: MeshLevel,
    *,
    load_points: Sequence[Point] = (),
    progress: Progress | None = None,
) -> MeshedModel:
    """Mesh the joined model in the current gmsh session at `level`."""
    start = time.perf_counter()
    set_mesh_options(level)
    add_size_fields(model, level, load_points)
    if progress:
        progress("meshing")
    try:
        gmsh.model.mesh.generate(3)
    except Exception as exc:  # gmsh raises a bare Exception with its last error
        raise MeshFailed(f"the mesher could not mesh the model at the {level.name} level: {exc}") from exc
    volume_part = {tag: index for index, part in enumerate(model.parts) for tag in part.volumes}
    min_quality = _quality_check(volume_part, [f"{p.block_label} ({p.part.name})" for p in model.parts])
    gmsh.model.mesh.setOrder(2)

    raw_tags, raw_coords, _ = gmsh.model.mesh.getNodes()
    raw_tags = np.asarray(raw_tags, dtype=np.int64)
    blocks: list[IntArray] = []
    owners: list[IntArray] = []
    for index, part in enumerate(model.parts):
        for tag in part.volumes:
            types, _, nodes = gmsh.model.mesh.getElements(3, tag)
            for kind, node_tags in zip(types, nodes, strict=True):
                if kind != GMSH_TET10:
                    raise MeshFailed(f"the mesh of {part.block_label} has elements other than 10-node tetrahedra")
                conn = np.asarray(node_tags, dtype=np.int64).reshape(-1, 10)[:, list(GMSH_TO_CCX_TET10)]
                blocks.append(conn)
                owners.append(np.full(conn.shape[0], index, dtype=np.int64))
    gmsh_conn = np.concatenate(blocks)
    element_part = np.concatenate(owners)

    used = np.unique(gmsh_conn)
    lookup = np.full(int(raw_tags.max()) + 1, -1, dtype=np.int64)
    lookup[raw_tags] = np.arange(raw_tags.size)
    coords_all = np.asarray(raw_coords, dtype=np.float64).reshape(-1, 3)
    coords = coords_all[lookup[used]].copy()
    renumber = np.full(int(raw_tags.max()) + 1, -1, dtype=np.int64)
    renumber[used] = np.arange(used.size)
    rows = renumber[gmsh_conn]

    corners = coords[rows[:, :4]]
    volume6 = np.einsum(
        "ij,ij->i",
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
        corners[:, 3] - corners[:, 0],
    )
    rows[volume6 < 0] = rows[volume6 < 0][:, _FLIP]
    straightened = _straighten(coords, rows)

    node_ids = np.arange(1, used.size + 1, dtype=np.int64)
    connectivity = rows + 1
    element_ids = np.arange(1, connectivity.shape[0] + 1, dtype=np.int64)
    parts = tuple(
        MeshPart(
            key=p.key,
            instance_id=p.instance_id,
            block_label=p.block_label,
            name=p.part.name,
            component=p.part.component,
            material=p.part.material,
            e_mpa=p.part.e_mpa,
            nu=p.part.nu,
            strength_setting=p.part.strength_setting,
            set_name=p.set_name,
            element_ids=element_ids[element_part == index],
        )
        for index, p in enumerate(model.parts)
    )

    supports = []
    supported_parts: set[int] = set()
    for support in model.supports:
        tags: list[IntArray] = []
        for surface in support.surfaces:
            found, _, _ = gmsh.model.mesh.getNodes(2, surface, includeBoundary=True)
            tags.append(np.asarray(found, dtype=np.int64))
        ids = np.unique(renumber[np.concatenate(tags)]) + 1
        if np.any(ids <= 0):
            raise MeshFailed(f"support {support.name!r} has nodes that belong to no element")
        supports.append(
            MeshSupport(
                support.name, support.instance_id, solver_name(f"{support.instance_id}.{support.name}", "S_"), ids
            )
        )
        in_support = np.isin(connectivity, ids)
        supported_parts.update(int(element_part[e]) for e in np.flatnonzero(in_support[:, :4].sum(axis=1) >= 3))
    if not supports:
        raise MeshFailed("the model has no supports, so nothing holds it")
    _check_connected(connectivity, element_part, parts, supported_parts, model.labels, model.parent_of)

    outer, outer_owner = boundary_faces_with_owner(connectivity)
    return MeshedModel(
        level=level.name,
        node_ids=node_ids,
        coordinates=coords,
        element_ids=element_ids,
        connectivity=connectivity,
        element_part=element_part,
        parts=parts,
        supports=tuple(supports),
        outer_faces=outer,
        outer_face_part=element_part[outer_owner],
        min_quality=min_quality,
        straightened=straightened,
        time_s=time.perf_counter() - start,
    )
