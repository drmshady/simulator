"""Reading nodal displacements from .frd files (T015).

`fixtures/cube_frd.txt` is real CalculiX 2.23 output (Windows build from dhondt.de, ccx_dynamic.exe with PARDISO):
a 2 mm cube of 6 C3D10 elements, 2 unit steps, `*NODE FILE` U and `*EL FILE` S, so each step also has a STRESS
and an ERROR block that the reader must skip. Negative values touch (`-3.22637E-04-3.83957E-06`).
"""

from pathlib import Path

import numpy as np
import pytest

from dentalsim.solvers.calculix.frd_reader import FrdFormatError, read_frd, read_frd_displacements

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str, tmp_path: Path, newline: str = "\n") -> Path:
    text = (FIXTURES / name).read_text(encoding="ascii").replace("\r\n", "\n")
    path = tmp_path / name.replace("_frd.txt", ".frd")
    path.write_bytes(text.replace("\n", newline).encode("ascii"))
    return path


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_reads_coordinates_and_displacements_of_each_step(tmp_path: Path, newline: str) -> None:
    result = read_frd(_fixture("cube_frd.txt", tmp_path, newline))

    assert result.node_ids.tolist() == list(range(1, 28))
    assert result.coordinates.shape == (27, 3)
    assert result.coordinates[3].tolist() == [2.0, 2.0, 2.0]

    assert [(s.step, s.increment, s.time) for s in result.steps] == [(1, 1, 1.0), (2, 1, 2.0)]
    first = result.steps[0]
    assert first.node_ids.tolist() == list(range(1, 28))
    assert first.displacements.dtype == np.float64
    assert first.displacements.shape == (27, 3)
    # " -1         4-3.22637E-04-3.83957E-06 3.52767E-04": glued negative values are split by column
    np.testing.assert_array_equal(first.displacements[3], [-3.22637e-04, -3.83957e-06, 3.52767e-04])
    np.testing.assert_array_equal(first.displacements[0], [0.0, 0.0, 0.0])


def test_steps_are_independent_unit_cases(tmp_path: Path) -> None:
    steps = read_frd_displacements(_fixture("cube_frd.txt", tmp_path))
    # Step 1 pulls along +z, step 2 along -x: the top corner node moves accordingly.
    assert steps[0].displacements[3, 2] > 0
    assert steps[1].displacements[3, 0] < 0


def test_probe_file(tmp_path: Path) -> None:
    result = read_frd(_fixture("probe_frd.txt", tmp_path))
    assert len(result.steps) == 1
    assert result.steps[0].node_ids.size == 10
    assert result.steps[0].displacements[3, 2] > 0  # the loaded apex moves up


def test_irregular_lines_fall_back_to_line_by_line_parsing(tmp_path: Path) -> None:
    path = _fixture("probe_frd.txt", tmp_path)
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(" -4  DISP")) + 5
    lines[index] = lines[index] + "   "  # trailing blanks change the line width
    path.write_text("\n".join(lines) + "\n")
    assert read_frd(path).steps[0].node_ids.size == 10


def test_truncated_file_is_an_error(tmp_path: Path) -> None:
    path = _fixture("cube_frd.txt", tmp_path)
    text = path.read_text()
    path.write_text(text[: text.index(" 9999")])
    with pytest.raises(FrdFormatError, match="end marker"):
        read_frd(path)
    path.write_text(text[: text.index("  100CL  102") + 200])
    with pytest.raises(FrdFormatError):
        read_frd(path)


def test_binary_results_are_refused(tmp_path: Path) -> None:
    path = _fixture("probe_frd.txt", tmp_path)
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("    2C"))
    lines[index] = lines[index][:-1] + "3"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(FrdFormatError, match="binary"):
        read_frd(path)
