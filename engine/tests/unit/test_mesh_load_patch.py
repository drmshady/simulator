"""The load patch: uniform traction over the faces within the radius, consistent nodal forces summing to F."""

import numpy as np
import pytest

from dentalsim.mesh.load_patch import boundary_faces, patch_load, select_faces


def _flat_grid(n: int = 6, size: float = 4.0, bulge: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A square of 6-node triangles in the plane z = 0 (or bulged into z = bulge·(1 − r²)), centred on the origin."""
    xs = np.linspace(-size / 2, size / 2, 2 * n + 1)
    ids = np.arange(1, xs.size**2 + 1).reshape(xs.size, xs.size)
    gx, gy = np.meshgrid(xs, xs, indexing="ij")
    r2 = (gx**2 + gy**2) / (size / 2) ** 2
    coords = np.stack([gx, gy, bulge * (1 - r2)], axis=-1).reshape(-1, 3)
    faces = []
    for i in range(0, 2 * n, 2):
        for j in range(0, 2 * n, 2):
            a, b, c, d = ids[i, j], ids[i + 2, j], ids[i + 2, j + 2], ids[i, j + 2]
            faces.append((a, b, c, ids[i + 1, j], ids[i + 2, j + 1], ids[i + 1, j + 1]))
            faces.append((a, c, d, ids[i + 1, j + 1], ids[i + 1, j + 2], ids[i, j + 1]))
    return np.array(faces), ids.ravel(), coords


@pytest.mark.parametrize("bulge", [0.0, 0.4])
def test_nodal_forces_add_up_to_the_load(bulge: float) -> None:
    faces, ids, coords = _flat_grid(bulge=bulge)
    force = np.array([0.0, -np.sin(np.radians(45)), -np.cos(np.radians(45))]) * 200.0
    patch = patch_load(faces, ids, coords, [0.0, 0.0, bulge], radius_mm=1.0)
    assert patch.weights.sum() == pytest.approx(1.0, abs=1e-12)
    assert patch.nodal_forces(force).sum(axis=0) == pytest.approx(force, abs=1e-10)


def test_flat_faces_put_no_load_on_corner_nodes() -> None:
    faces, ids, coords = _flat_grid()
    patch = patch_load(faces, ids, coords, [0.0, 0.0, 0.0], radius_mm=1.0)
    corners = set(patch.faces[:, :3].ravel().tolist())
    by_node = dict(zip(patch.node_ids.tolist(), patch.weights.tolist(), strict=True))
    assert all(abs(by_node[n]) < 1e-12 for n in corners)  # consistent loads of a flat quadratic face: corners 0
    assert patch.area_mm2 == pytest.approx(len(patch.faces) * (4.0 / 6) ** 2 / 2)


def test_faces_are_chosen_by_centroid_distance() -> None:
    faces, ids, coords = _flat_grid()
    near = select_faces(faces, ids, coords, [0.0, 0.0, 0.0], 0.5)
    far = select_faces(faces, ids, coords, [0.0, 0.0, 0.0], 1.0)
    assert 0 < len(near) < len(far)
    with pytest.raises(ValueError, match="no surface face"):
        patch_load(faces, ids, coords, [10.0, 10.0, 0.0], radius_mm=0.5)


def test_boundary_faces_of_two_tetrahedra_sharing_a_face() -> None:
    # Two C3D10 elements sharing face (1, 2, 3): 8 faces in all, 6 on the boundary.
    conn = np.array([[1, 2, 3, 4, 10, 11, 12, 13, 14, 15], [1, 3, 2, 5, 12, 11, 10, 16, 17, 18]])
    faces = boundary_faces(conn)
    assert len(faces) == 6
    assert not any(set(f[:3]) == {1, 2, 3} for f in faces.tolist())
