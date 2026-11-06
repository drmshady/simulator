"""The builder's 3D scene: each block's surfaces and the keep-out volumes, tessellated, as one glTF binary (task T086).

The parts are built as each block makes them, in the conventions frame, without joining them: the scene shows where
the blocks are, not the solved model, so it is quick enough to redraw after every move. Each mesh's extras name the
block instance, the part or keep-out, its component or problem code, and a label.
"""

from __future__ import annotations

from typing import Any

import gmsh
import numpy as np

from dentalsim.assembly.placement import Assembly
from dentalsim.geometry import occ as occ_tools
from dentalsim.post.gltf import write_scene_glb

SURFACE_SIZE_MM = 1.5
CURVATURE_ELEMENTS = 0
GMSH_TRIANGLE = 2


def _surface_mesh(
    volume_tags: list[int], lookup: np.ndarray[Any, Any], coordinates: np.ndarray[Any, Any]
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    surfaces = {abs(t) for d, t in gmsh.model.getBoundary([(3, v) for v in volume_tags], oriented=False) if d == 2}
    node_tags: list[np.ndarray[Any, Any]] = []
    for surface in sorted(surfaces):
        types, _, nodes = gmsh.model.mesh.getElements(2, surface)
        for kind, tags in zip(types, nodes, strict=True):
            if kind == GMSH_TRIANGLE:
                node_tags.append(np.asarray(tags, dtype=np.int64).reshape(-1, 3))
    if not node_tags:
        return np.empty((0, 3)), np.empty((0, 3), dtype=np.int64)
    triangles = np.concatenate(node_tags)
    used, local = np.unique(triangles, return_inverse=True)
    return coordinates[lookup[used]], local.reshape(-1, 3)


def builder_glb(assembly: Assembly) -> bytes:
    """The placed blocks' surfaces and every keep-out volume, tessellated; blocks that cannot be placed are left
    out."""
    with occ_tools.session("builder"):
        items: list[tuple[list[int], dict[str, Any]]] = []
        for placed, ctx in assembly.placed():
            made = placed.block_type.build(ctx)
            for part in made.parts:
                items.append(
                    (
                        list(part.volumes),
                        {
                            "kind": "part",
                            "block": placed.instance_id,
                            "type_id": placed.block_type.type_id,
                            "part": part.name,
                            "component": part.component,
                            "material": part.material,
                            "name": f"{placed.instance_id}.{part.name}",
                            "label": placed.label,
                        },
                    )  # fmt: skip
                )
            occ_tools.occ().remove(occ_tools.volumes([t for c in made.cutters for t in c.volumes]), recursive=True)
            for keep_out in placed.block_type.keep_out(ctx):
                items.append(
                    (
                        keep_out.volume.build_occ(),
                        {
                            "kind": "keep_out",
                            "block": placed.instance_id,
                            "code": keep_out.code,
                            "name": f"{placed.instance_id}.{keep_out.name}",
                            "label": keep_out.label,
                        },
                    )  # fmt: skip
                )
        occ_tools.occ().synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMax", SURFACE_SIZE_MM)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", CURVATURE_ELEMENTS)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.model.mesh.generate(2)
        tags, flat, _ = gmsh.model.mesh.getNodes()
        tags = np.asarray(tags, dtype=np.int64)
        lookup = np.zeros(int(tags.max()) + 1, dtype=np.int64)
        lookup[tags] = np.arange(tags.size)
        coordinates = np.asarray(flat, dtype=np.float64).reshape(-1, 3)
        meshes = [(*_surface_mesh(volumes, lookup, coordinates), extras) for volumes, extras in items]
    return write_scene_glb(meshes)
