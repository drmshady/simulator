"""Force, moment and energy checks of one unit case (FR-023), on a 45° load with exact and broken reactions."""

import numpy as np
import pytest

from dentalsim.post.convergence import (
    check_unit_case,
    energy_balance_error,
    force_balance_error,
    moment_balance_error,
    strain_energy,
)

F45 = 100.0 * np.array([0.0, -np.sin(np.pi / 4), -np.cos(np.pi / 4)])
LOAD_POINTS = np.array([[0.3, 0.2, 10.0], [-0.2, 0.1, 10.0], [0.0, -0.3, 10.0]])
LOAD_FORCES = np.outer([0.5, 0.3, 0.2], F45)
SUPPORTS = np.array([[5.0, 5.0, 0.0], [-5.0, 5.0, 0.0], [-5.0, -5.0, 0.0], [5.0, -5.0, 0.0]])


def _equilibrium_reactions() -> np.ndarray:
    """Support forces (4, 3) balancing the load's force and moment exactly (minimum-norm solution)."""
    rows = []
    for axis in range(3):
        rows.append(np.concatenate([np.eye(3)[axis]] * 4))
    for axis in range(3):
        row = np.zeros(12)
        for k, p in enumerate(SUPPORTS):
            for j in range(3):
                row[3 * k + j] = np.cross(p, np.eye(3)[j])[axis]
        rows.append(row)
    target = -np.concatenate([LOAD_FORCES.sum(axis=0), np.cross(LOAD_POINTS, LOAD_FORCES).sum(axis=0)])
    solution, *_ = np.linalg.lstsq(np.array(rows), target, rcond=None)
    return solution.reshape(4, 3)


def test_exact_reactions_pass_force_and_moment_balance_at_45_degrees() -> None:
    reactions = _equilibrium_reactions()
    assert force_balance_error(F45, reactions.sum(axis=0)) < 1e-12
    assert moment_balance_error(LOAD_POINTS, LOAD_FORCES, SUPPORTS, reactions) < 1e-12


def test_a_missing_couple_fails_the_moment_balance_but_not_the_force_balance() -> None:
    # Reactions that balance the force but not the moment: all of -F at one support.
    reactions = np.zeros((4, 3))
    reactions[0] = -F45
    assert force_balance_error(F45, reactions.sum(axis=0)) < 1e-12
    assert moment_balance_error(LOAD_POINTS, LOAD_FORCES, SUPPORTS, reactions) > 0.1


def test_force_balance_is_checked_per_component() -> None:
    reaction = -F45 + np.array([1e-4, 0.0, 0.0])
    assert force_balance_error(F45, reaction) == pytest.approx(1e-6, rel=1e-6)


def test_energy_of_uniaxial_stress() -> None:
    # σxx = 10 MPa, εxx = 0.01, V = 1 mm³: U = ½ σ ε V; tensor shear components count twice.
    stress = np.array([[10.0, 0, 0, 2.0, 0, 0]])
    strain = np.array([[0.01, -0.003, -0.003, 0.001, 0, 0]])
    assert strain_energy(stress, strain, [1.0]) == pytest.approx(0.5 * (10 * 0.01 + 2 * 2.0 * 0.001))
    work = 2 * strain_energy(stress, strain, [1.0])
    assert energy_balance_error([[work, 0, 0]], [[1.0, 0, 0]], stress, strain, [1.0]) < 1e-12
    assert energy_balance_error([[2 * work, 0, 0]], [[1.0, 0, 0]], stress, strain, [1.0]) == pytest.approx(0.5)


def test_check_unit_case_uses_the_conventions_tolerances() -> None:
    reactions = _equilibrium_reactions()
    stress = np.array([[10.0, 0, 0, 0, 0, 0]])
    strain = np.array([[0.01, 0, 0, 0, 0, 0]])
    work_per_node = 2 * strain_energy(stress, strain, [1.0]) / 3
    displacements = np.array([f / (f @ f) * work_per_node for f in LOAD_FORCES])
    checks = check_unit_case(
        applied_total=F45,
        reaction_total=reactions.sum(axis=0),
        load_positions=LOAD_POINTS,
        load_forces=LOAD_FORCES,
        reaction_positions=SUPPORTS,
        reaction_forces=reactions,
        load_displacements=displacements,
        stress=stress,
        strain=strain,
        ip_volumes=[1.0],
        residual=1e-14,
    )
    assert [c.name for c in checks] == ["force balance", "moment balance", "energy", "solver residual"]
    assert all(c.ok for c in checks)
    assert [c.tolerance for c in checks] == [1e-6, 1e-5, 1e-4, 1e-10]
    bad = check_unit_case(
        applied_total=F45,
        reaction_total=reactions.sum(axis=0),
        load_positions=LOAD_POINTS,
        load_forces=LOAD_FORCES,
        reaction_positions=SUPPORTS,
        reaction_forces=reactions,
        load_displacements=displacements,
        stress=stress,
        strain=strain,
        ip_volumes=[1.0],
        residual=2e-6,
    )
    assert not bad[-1].ok and "exceeds" in bad[-1].reason()
