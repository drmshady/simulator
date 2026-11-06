"""Meshes for the textbook cases: gmsh OpenCASCADE shapes meshed with straight-sided 10-node tetrahedra.

Straight edges keep each element's geometry exactly what both the CalculiX solve and the scikit-fem cross-check see
(scikit-fem's quadratic element on a straight-sided mesh), so the two solve the same discrete problem. Meshing is
single-threaded with a fixed seed, so a case's mesh is the same every time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import gmsh
import numpy as np
from numpy.typing import NDArray

from dentalsim.geometry.occ import session
from dentalsim.mesh.load_patch import TET10_FACES, patch_load
from dentalsim.solvers.calculix.inp_writer import gmsh_to_ccx_tet10

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Predicate = Callable[[FloatArray], NDArray[np.bool_]]
"""Takes points (k, 3) and says which lie on a surface."""

GMSH_TET10 = 11
_FLIP = np.array([0, 2, 1, 3, 6, 5, 4, 7, 9, 8])


@dataclass(frozen=True, eq=False)
class SimpleMesh:
    node_ids: IntArray
    coordinates: FloatArray
    element_ids: IntArray
    connectivity: IntArray
    element_volume: IntArray
    """(m,) the index of the gmsh volume (in creation order) each element belongs to."""

    def xyz(self, node_ids: IntArray) -> FloatArray:
        return np.asarray(self.coordinates[node_ids - 1], dtype=np.float64)

    def nodes_where(self, predicate: Predicate) -> IntArray:
        return np.asarray(self.node_ids[predicate(self.coordinates)], dtype=np.int64)

    def boundary_faces(self) -> tuple[IntArray, IntArray, IntArray]:
        """Faces of one element only: (k, 6) node ids, the element id and the CalculiX face number 1–4."""
        faces = self.connectivity[:, np.array(TET10_FACES)].reshape(-1, 6)
        elements = np.repeat(self.element_ids, 4)
        numbers = np.tile(np.arange(1, 5), self.element_ids.size)
        keys = np.sort(faces[:, :3], axis=1)
        _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        single = counts[inverse.ravel()] == 1
        return faces[single], elements[single], numbers[single]

    def faces_on(self, predicate: Predicate) -> tuple[IntArray, IntArray, IntArray]:
        """The boundary faces whose three corners all satisfy `predicate`."""
        faces, elements, numbers = self.boundary_faces()
        corners = self.xyz(faces[:, :3].ravel()).reshape(-1, 3, 3)
        on = predicate(corners.reshape(-1, 3)).reshape(-1, 3).all(axis=1)
        if not on.any():
            raise ValueError("no boundary face lies on the surface")
        return faces[on], elements[on], numbers[on]

    def surface_nodes(self, predicate: Predicate) -> IntArray:
        """Every node (corner and edge) of the boundary faces on a surface."""
        faces, _, _ = self.faces_on(predicate)
        return np.unique(faces)

    def traction_weights(self, predicate: Predicate) -> tuple[tuple[int, float], ...]:
        """Consistent nodal weights (summing to 1) of a uniform traction over the faces on a surface."""
        faces, _, _ = self.faces_on(predicate)
        centre = self.xyz(faces[:, :3].ravel()).mean(axis=0)
        load = patch_load(faces, self.node_ids, self.coordinates, centre, radius_mm=1e12)
        return load.as_patch()


def mesh_shapes(build: Callable[[], Sequence[int]], size: Callable[[], None] | float) -> SimpleMesh:
    """Mesh the volumes `build()` makes (fragmented together, so touching volumes share nodes).

    `size` is a uniform element size in mm, or a function that sets gmsh size fields itself.
    """
    with session("verification"):
        kernel = gmsh.model.occ
        volumes = list(build())
        if len(volumes) > 1:
            _, mapping = kernel.fragment([(3, volumes[0])], [(3, v) for v in volumes[1:]])
            volumes = [next(tag for dim, tag in pieces if dim == 3) for pieces in mapping]
        kernel.synchronize()
        for option, value in (
            ("General.NumThreads", 1),
            ("Mesh.MaxNumThreads3D", 1),
            ("Mesh.RandomSeed", 1),
            ("Mesh.Algorithm3D", 1),
            ("Mesh.OptimizeNetgen", 1),
            ("Mesh.ElementOrder", 2),
            ("Mesh.SecondOrderLinear", 1),
            ("Mesh.MeshSizeFromCurvature", 0),
            ("Mesh.MeshSizeExtendFromBoundary", 0 if callable(size) else 1),
        ):
            gmsh.option.setNumber(option, value)
        if callable(size):
            gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
            size()
        else:
            gmsh.option.setNumber("Mesh.MeshSizeMin", float(size))
            gmsh.option.setNumber("Mesh.MeshSizeMax", float(size))
        gmsh.model.mesh.generate(3)
        raw_tags, raw_coords, _ = gmsh.model.mesh.getNodes()
        blocks, owners = [], []
        for index, volume in enumerate(volumes):
            element_types, _, node_tags = gmsh.model.mesh.getElements(3, volume)
            for kind, nodes in zip(element_types, node_tags, strict=True):
                if kind != GMSH_TET10:
                    raise ValueError("the verification mesh has elements other than 10-node tetrahedra")
                conn = gmsh_to_ccx_tet10(np.asarray(nodes, dtype=np.int64).reshape(-1, 10))
                blocks.append(conn)
                owners.append(np.full(conn.shape[0], index, dtype=np.int64))
    raw_tags = np.asarray(raw_tags, dtype=np.int64)
    gmsh_conn = np.concatenate(blocks)
    used = np.unique(gmsh_conn)
    lookup = np.full(int(raw_tags.max()) + 1, -1, dtype=np.int64)
    lookup[raw_tags] = np.arange(raw_tags.size)
    coords = np.asarray(raw_coords, dtype=np.float64).reshape(-1, 3)[lookup[used]]
    renumber = np.full(int(raw_tags.max()) + 1, -1, dtype=np.int64)
    renumber[used] = np.arange(1, used.size + 1)
    conn = renumber[gmsh_conn]
    corners = coords[conn[:, :4] - 1]
    volume6 = np.einsum(
        "ij,ij->i",
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
        corners[:, 3] - corners[:, 0],
    )
    conn[volume6 < 0] = conn[volume6 < 0][:, _FLIP]
    return SimpleMesh(
        node_ids=np.arange(1, used.size + 1, dtype=np.int64),
        coordinates=coords,
        element_ids=np.arange(1, conn.shape[0] + 1, dtype=np.int64),
        connectivity=conn,
        element_volume=np.concatenate(owners),
    )
