"""Reading integration-point values, volumes and reaction totals from .dat files (T015).

`fixtures/cube_dat.txt` is real CalculiX 2.23 output (Windows build from dhondt.de, written with CRLF endings) for
the 6-element cube of `cube_frd.txt`: per step, `*NODE PRINT` U on set Top, `*EL PRINT` S, E, COORD and EVOL on
Eall, and `*NODE PRINT, TOTALS=ONLY` RF on set Base. Step 1 is +1 N along z, step 2 is -1 N along x.
"""

from pathlib import Path

import numpy as np
import pytest

from dentalsim.solvers.calculix.dat_reader import DatFormatError, read_dat

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(tmp_path: Path, name: str = "cube_dat.txt", newline: str = "\r\n") -> Path:
    text = (FIXTURES / name).read_text(encoding="ascii").replace("\r\n", "\n")
    path = tmp_path / name.replace("_dat.txt", ".dat")
    path.write_bytes(text.replace("\n", newline).encode("ascii"))
    return path


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_reads_integration_point_values_per_step(tmp_path: Path, newline: str) -> None:
    steps = read_dat(_fixture(tmp_path, newline=newline))

    assert [(s.step, s.time) for s in steps] == [(1, 1.0), (2, 2.0)]
    step = steps[0]
    assert step.element_ids.tolist() == [e for e in range(1, 7) for _ in range(4)]
    assert step.ip_numbers.tolist() == [1, 2, 3, 4] * 6
    assert step.stress.shape == (24, 6) and step.stress.dtype == np.float64
    assert step.strain.shape == (24, 6)
    assert step.ip_coordinates.shape == (24, 3)
    # "1   1  7.228672E-02  7.349186E-02  2.129921E-01 -1.487960E-04 -5.815019E-02 -1.696918E-03": xx yy zz xy xz yz
    np.testing.assert_array_equal(
        step.stress[0], [7.228672e-02, 7.349186e-02, 2.129921e-01, -1.487960e-04, -5.815019e-02, -1.696918e-03]
    )
    np.testing.assert_array_equal(
        step.strain[0], [-1.365847e-05, -1.209179e-05, 1.692585e-04, -1.934348e-07, -7.559524e-05, -2.205993e-06]
    )


def test_volumes_and_integration_point_volumes(tmp_path: Path) -> None:
    step = read_dat(_fixture(tmp_path))[0]
    assert step.volume_element_ids.tolist() == list(range(1, 7))
    assert step.volumes.sum() == pytest.approx(8.0, rel=1e-6)  # the 2 mm cube
    ip_volumes = step.ip_volumes()
    assert ip_volumes.shape == (24,)
    np.testing.assert_allclose(ip_volumes[:4], step.volumes[0] / 4)
    assert ip_volumes.sum() == pytest.approx(step.volumes.sum())


def test_integration_points_lie_inside_their_elements(tmp_path: Path) -> None:
    step = read_dat(_fixture(tmp_path))[0]
    assert np.all((step.ip_coordinates > 0.0) & (step.ip_coordinates < 2.0))


def test_reaction_totals_balance_each_unit_case(tmp_path: Path) -> None:
    first, second = read_dat(_fixture(tmp_path))
    assert set(first.reaction_totals) == {"BASE"}
    np.testing.assert_allclose(first.reaction_totals["BASE"], [0.0, 0.0, -1.0], atol=1e-9)
    np.testing.assert_allclose(second.reaction_totals["BASE"], [1.0, 0.0, 0.0], atol=1e-9)


def test_printed_nodal_displacements(tmp_path: Path) -> None:
    step = read_dat(_fixture(tmp_path))[0]
    top = step.displacements["TOP"]
    assert top.node_ids.tolist() == [4, 11, 14, 19, 22, 23, 25, 26, 27]
    np.testing.assert_array_equal(top.values[0], [-3.226368e-04, -3.839567e-06, 3.527675e-04])


def test_element_set_is_selectable_and_case_insensitive(tmp_path: Path) -> None:
    path = _fixture(tmp_path)
    assert read_dat(path, elset="eall")[0].stress.shape == (24, 6)
    other = read_dat(path, elset="Upper")[0]
    assert other.stress.shape == (0, 6) and other.volumes.shape == (0,)
    assert set(other.reaction_totals) == {"BASE"}  # nodal blocks do not depend on the element set


def test_rows_are_sorted_and_aligned_across_blocks(tmp_path: Path) -> None:
    path = _fixture(tmp_path, newline="\n")
    lines = path.read_text().split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(" stresses")) + 2
    lines[start], lines[start + 5] = lines[start + 5], lines[start]  # print order no longer sorted
    path.write_text("\n".join(lines))
    step = read_dat(path)[0]
    assert step.ip_numbers.tolist()[:4] == [1, 2, 3, 4]
    np.testing.assert_array_equal(step.stress[0][:3], [7.228672e-02, 7.349186e-02, 2.129921e-01])


def test_mismatched_integration_point_rows_are_an_error(tmp_path: Path) -> None:
    path = _fixture(tmp_path, newline="\n")
    lines = path.read_text().split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(" strains")) + 2
    del lines[start]
    path.write_text("\n".join(lines))
    with pytest.raises(DatFormatError, match="do not match"):
        read_dat(path)


def test_three_digit_exponents_and_nan(tmp_path: Path) -> None:
    path = _fixture(tmp_path, newline="\n")
    text = path.read_text()
    text = text.replace(" 7.228672E-02  7.349186E-02", " 7.228672-102           NaN", 1)
    path.write_text(text)
    step = read_dat(path)[0]
    assert step.stress[0, 0] == pytest.approx(7.228672e-102)
    assert np.isnan(step.stress[0, 1])
    assert step.stress.shape == (24, 6)


def test_without_step_lines_steps_follow_the_time(tmp_path: Path) -> None:
    path = _fixture(tmp_path, newline="\n")
    lines = [line for line in path.read_text().split("\n") if "S T E P" not in line and "INCREMENT" not in line]
    path.write_text("\n".join(lines))
    steps = read_dat(path)
    assert [(s.step, s.time) for s in steps] == [(1, 1.0), (2, 2.0)]
    np.testing.assert_allclose(steps[1].reaction_totals["BASE"], [1.0, 0.0, 0.0], atol=1e-9)


def test_last_increment_of_a_step_is_kept(tmp_path: Path) -> None:
    path = _fixture(tmp_path, "probe_dat.txt", newline="\n")
    text = path.read_text()
    marker = "                                INCREMENT     1"
    body = text[text.index(marker) + len(marker) :]
    halfway = body.replace("0.1000000E+01", "0.5000000E+00").replace("-1.000000E+00", "-5.000000E-01")
    path.write_text(text[: text.index(marker)] + marker + halfway + marker.replace("1", "2") + body)
    (step,) = read_dat(path)
    assert step.time == 1.0
    np.testing.assert_allclose(step.reaction_totals["FIX"], [0.0, 0.0, -1.0], atol=1e-9)
