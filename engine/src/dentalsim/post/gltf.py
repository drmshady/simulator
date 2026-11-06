"""Result surfaces as binary glTF, one `.glb` per part and load case (task T080; research R11).

Each file holds the part's surface (its outer surface and its interfaces with other parts, so a part can be shown on
its own), each quadratic face drawn as 4 triangles, with:

- vertex attributes `_STRAIN_UE` (equivalent strain, µε), `_STRAIN_MAXP_UE` (|maximum principal strain|, µε, the
  gauge the result view can switch to), `_STRESS_VM` (von Mises stress, MPa), `_PCT_STRENGTH`
  (the material's stress measure as % of its strength reference; −1 where it is not compared) and `_FROST_CLASS`
  (the Frost window index, 0 to 3, for bone; −1 elsewhere);
- the displacement (mm) as a morph target, whose weight the viewer sets to the stated magnification.

The colours are for display only: the values at the vertices are the integration-point values extrapolated to the
nodes and averaged. Key numbers always come from the integration points (`dentalsim.post.key_numbers`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pygltflib import (
    ARRAY_BUFFER,
    ELEMENT_ARRAY_BUFFER,
    FLOAT,
    GLTF2,
    SCALAR,
    TRIANGLES,
    UNSIGNED_INT,
    VEC3,
    Accessor,
    Asset,
    Attributes,
    Buffer,
    BufferView,
    Mesh,
    Node,
    Primitive,
    Scene,
)

from dentalsim.mesh.build import MeshedModel
from dentalsim.verification.solution import EXTRAPOLATION

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
SUB_TRIANGLES = np.array([[0, 3, 5], [3, 1, 4], [5, 4, 2], [3, 4, 5]])
"""A 6-node face (corners 0–2 counter-clockwise from outside, then the edge nodes of 0-1, 1-2, 2-0) as 4 triangles."""
NOT_COMPARED = -1.0
ATTRIBUTES = ("_STRAIN_UE", "_STRAIN_MAXP_UE", "_STRESS_VM", "_PCT_STRENGTH", "_FROST_CLASS")


def part_surface(mesh: MeshedModel, part: int) -> tuple[IntArray, IntArray]:
    """The node ids of a part's surface and its triangles, as indices into them."""
    faces = mesh.part_faces(part)
    nodes, inverse = np.unique(faces, return_inverse=True)
    local = inverse.reshape(faces.shape)
    triangles = local[:, SUB_TRIANGLES].reshape(-1, 3)
    return np.asarray(nodes, dtype=np.int64), np.asarray(triangles, dtype=np.int64)


def nodal_average(mesh: MeshedModel, part: int, ip_values: FloatArray) -> dict[int, float]:
    """Display values at a part's nodes: each element's integration-point values extrapolated to its corners,
    averaged over the part's elements; an edge node takes the mean of its two corners."""
    elements = np.flatnonzero(mesh.element_part == part)
    per_point = ip_values.reshape(-1, 4)[elements]  # (e, 4)
    corners = per_point @ EXTRAPOLATION.T  # (e, 4)
    conn = mesh.connectivity[elements]
    totals: dict[int, float] = {}
    counts: dict[int, int] = {}
    for node, value in zip(conn[:, :4].ravel().tolist(), corners.ravel().tolist(), strict=True):
        totals[node] = totals.get(node, 0.0) + value
        counts[node] = counts.get(node, 0) + 1
    values = {node: totals[node] / counts[node] for node in totals}
    for k, (a, b) in enumerate(((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)), start=4):
        for row in conn:
            values.setdefault(int(row[k]), 0.5 * (values[int(row[a])] + values[int(row[b])]))
    return values


def _pad(data: bytes) -> bytes:
    return data + b"\x00" * (-len(data) % 4)


def write_glb(
    positions: FloatArray,
    triangles: IntArray,
    attributes: Mapping[str, FloatArray],
    displacement: FloatArray,
    extras: Mapping[str, Any],
) -> bytes:
    """A one-mesh glTF binary with custom scalar attributes and the displacement as morph target 0."""
    chunks: list[bytes] = []
    views: list[BufferView] = []
    accessors: list[Accessor] = []

    def add(array: NDArray[Any], kind: str, component: int, target: int | None, bounds: bool = False) -> int:
        data = np.ascontiguousarray(array).tobytes()
        offset = sum(len(c) for c in chunks)
        chunks.append(_pad(data))
        views.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(data), target=target))
        accessor = Accessor(
            bufferView=len(views) - 1,
            componentType=component,
            count=int(array.shape[0]),
            type=kind,
            min=array.min(axis=0).tolist() if bounds else None,
            max=array.max(axis=0).tolist() if bounds else None,
        )
        accessors.append(accessor)
        return len(accessors) - 1

    position = add(positions.astype(np.float32), VEC3, FLOAT, ARRAY_BUFFER, bounds=True)
    indices = add(triangles.astype(np.uint32).ravel(), SCALAR, UNSIGNED_INT, ELEMENT_ARRAY_BUFFER)
    custom = {name: add(values.astype(np.float32), SCALAR, FLOAT, ARRAY_BUFFER) for name, values in attributes.items()}
    morph = add(displacement.astype(np.float32), VEC3, FLOAT, ARRAY_BUFFER, bounds=True)
    primitive = Primitive(
        attributes=Attributes(POSITION=position, **custom),
        indices=indices,
        mode=TRIANGLES,
        targets=[{"POSITION": morph}],
    )
    gltf = GLTF2(
        asset=Asset(version="2.0", generator="dentalsim"),
        scene=0,
        scenes=[Scene(nodes=[0])],
        nodes=[Node(mesh=0)],
        meshes=[Mesh(primitives=[primitive], weights=[0.0], extras=dict(extras))],
        accessors=accessors,
        bufferViews=views,
        buffers=[Buffer(byteLength=sum(len(c) for c in chunks))],
    )
    gltf.set_binary_blob(b"".join(chunks))
    return bytes(b"".join(gltf.save_to_bytes()))


def part_glb(
    mesh: MeshedModel,
    part: int,
    *,
    displacement: FloatArray,
    ip_fields: Mapping[str, FloatArray],
    extras: Mapping[str, Any],
) -> bytes:
    """The `.glb` of one part for one load case. `ip_fields` holds the per-integration-point values of each
    attribute in `ATTRIBUTES`; `displacement` is (n, 3) at the nodes."""
    nodes, triangles = part_surface(mesh, part)
    rows = nodes - 1
    attributes = {}
    for name in ATTRIBUTES:
        values = nodal_average(mesh, part, ip_fields[name])
        attributes[name] = np.array([values[int(n)] for n in nodes], dtype=np.float64)
    return write_glb(mesh.coordinates[rows], triangles, attributes, displacement[rows], extras)


def read_attributes(glb: bytes) -> tuple[GLTF2, dict[str, NDArray[Any]]]:
    """The glTF and its accessors' arrays by attribute name (for tests and exports)."""
    gltf = GLTF2.load_from_bytes(glb)
    blob = gltf.binary_blob()
    primitive = gltf.meshes[0].primitives[0]
    named: dict[str, int] = {"POSITION": primitive.attributes.POSITION, "INDICES": primitive.indices}
    named.update({k: v for k, v in vars(primitive.attributes).items() if k.startswith("_") and v is not None})
    named["MORPH"] = primitive.targets[0]["POSITION"]
    arrays: dict[str, NDArray[Any]] = {}
    for name, index in named.items():
        accessor = gltf.accessors[index]
        view = gltf.bufferViews[accessor.bufferView]
        dtype = np.uint32 if accessor.componentType == UNSIGNED_INT else np.float32
        width = 3 if accessor.type == VEC3 else 1
        data = np.frombuffer(blob, dtype=dtype, count=accessor.count * width, offset=view.byteOffset)
        arrays[name] = data.reshape(-1, 3) if width == 3 else data
    return gltf, arrays


def frost_index(strain_ue: FloatArray, edges: Sequence[float]) -> FloatArray:
    """The Frost window index (0 to len(edges)) of each strain."""
    return np.asarray(np.searchsorted(np.asarray(edges, dtype=np.float64), strain_ue, side="right"), dtype=np.float64)


def write_scene_glb(meshes: Sequence[tuple[FloatArray, IntArray, Mapping[str, Any]]]) -> bytes:
    """A glTF binary of several plain meshes (positions, triangles, extras), one node each: the builder's scene."""
    chunks: list[bytes] = []
    views: list[BufferView] = []
    accessors: list[Accessor] = []

    def add(array: NDArray[Any], kind: str, component: int, target: int, bounds: bool = False) -> int:
        data = np.ascontiguousarray(array).tobytes()
        offset = sum(len(c) for c in chunks)
        chunks.append(_pad(data))
        views.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(data), target=target))
        accessors.append(
            Accessor(
                bufferView=len(views) - 1,
                componentType=component,
                count=int(array.shape[0]),
                type=kind,
                min=array.min(axis=0).tolist() if bounds else None,
                max=array.max(axis=0).tolist() if bounds else None,
            )
        )
        return len(accessors) - 1

    gltf_meshes, nodes = [], []
    for positions, triangles, extras in meshes:
        if not len(triangles):
            continue
        position = add(positions.astype(np.float32), VEC3, FLOAT, ARRAY_BUFFER, bounds=True)
        indices = add(triangles.astype(np.uint32).ravel(), SCALAR, UNSIGNED_INT, ELEMENT_ARRAY_BUFFER)
        primitive = Primitive(attributes=Attributes(POSITION=position), indices=indices, mode=TRIANGLES)
        gltf_meshes.append(Mesh(primitives=[primitive], extras=dict(extras)))
        nodes.append(Node(mesh=len(gltf_meshes) - 1, name=str(extras.get("name", ""))))
    gltf = GLTF2(
        asset=Asset(version="2.0", generator="dentalsim"),
        scene=0,
        scenes=[Scene(nodes=list(range(len(nodes))))],
        nodes=nodes,
        meshes=gltf_meshes,
        accessors=accessors,
        bufferViews=views,
        buffers=[Buffer(byteLength=sum(len(c) for c in chunks))],
    )
    gltf.set_binary_blob(b"".join(chunks))
    return bytes(b"".join(gltf.save_to_bytes()))
