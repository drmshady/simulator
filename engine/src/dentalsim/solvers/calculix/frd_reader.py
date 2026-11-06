"""Reader for CalculiX ASCII result files (.frd): node coordinates and nodal displacements per step.

The file is read by fixed columns, as ccx 2.23 writes it (frd.c, frdheader.c, frdvector.c):

- node block: `    2C` + 18 blanks + node count (I12) + format flag (I38; 1 = ASCII); data lines are
  ` -1` + node id (I10) + x, y, z (E12.5 each), ended by ` -3`
- step parameters: `    1PSTEP` with the result-block counter, the increment and the step number in columns 25-36,
  37-48 and 49-60
- result header: `  100CL` + block number (I5) + total time (12 characters) + node count (I12) ... + format flag in
  column 75; then ` -4  DISP` and the ` -5` component lines; then ` -1` + node id (I10) + 3 × E12.5, ended by ` -3`

Values are glued together when negative (` -1         8-1.26674E-09-1.09566E-11-8.13527E-10`), so splitting on
blanks would be wrong. ccx writes values with `%12.5E`, which keeps 6 significant digits: use the .dat file when more
precision is needed. The file ends with ` 9999`. Lines may end with LF or CRLF.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np
from numpy.typing import NDArray

_FIELD = 12


class FrdFormatError(ValueError):
    """The .frd file is not ASCII output from ccx, or is truncated."""


@dataclass(frozen=True)
class FrdStep:
    """Nodal displacements of one solver step (the last increment of that step)."""

    step: int
    increment: int
    time: float
    node_ids: NDArray[np.int64]
    displacements: NDArray[np.float64]


@dataclass(frozen=True)
class FrdResult:
    node_ids: NDArray[np.int64]
    coordinates: NDArray[np.float64]
    steps: tuple[FrdStep, ...]


def _lines(stream: BinaryIO) -> Iterator[bytes]:
    for raw in stream:
        yield raw.rstrip(b"\r\n")


def _int(field: bytes) -> int:
    text = field.strip()
    return int(text) if text else 0


def _block(lines: Iterator[bytes], id_width: int, values: int) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    """Read ` -1` data lines up to ` -3`. Fast path: all lines the same width, converted column-wise by NumPy."""
    rows: list[bytes] = []
    for line in lines:
        if line.startswith(b" -3"):
            break
        if line.startswith(b" -1"):
            rows.append(line)
        elif not line.startswith(b" -2"):  # continuation lines only occur for more than 6 values
            raise FrdFormatError(f"unexpected line in a data block: {line[:40]!r}")
    else:
        raise FrdFormatError("the file ends inside a data block (truncated .frd)")
    width = 3 + id_width + values * _FIELD
    if not rows:
        return np.empty(0, np.int64), np.empty((0, values), np.float64)
    if all(len(row) == width for row in rows):
        table = np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(len(rows), width)
        ids = np.ascontiguousarray(table[:, 3 : 3 + id_width]).view(f"S{id_width}").ravel().astype(np.int64)
        data = np.ascontiguousarray(table[:, 3 + id_width :]).view(f"S{_FIELD}").reshape(len(rows), values)
        return ids, data.astype(np.float64)
    ids_list = []
    data_list = []
    for row in rows:
        if len(row) < width:
            raise FrdFormatError(f"short data line: {row!r}")
        ids_list.append(int(row[3 : 3 + id_width]))
        start = 3 + id_width
        data_list.append([float(row[start + k * _FIELD : start + (k + 1) * _FIELD]) for k in range(values)])
    return np.asarray(ids_list, np.int64), np.asarray(data_list, np.float64)


def _skip_block(lines: Iterator[bytes]) -> None:
    for line in lines:
        if line.startswith(b" -3"):
            return
    raise FrdFormatError("the file ends inside a data block (truncated .frd)")


def read_frd(path: Path) -> FrdResult:
    """Node coordinates and the displacement field (DISP) of every step, in step order.

    When a step has several increments (nonlinear steps, later), its last increment is kept. Other result blocks
    (stresses, forces, errors) are skipped.
    """
    node_ids = np.empty(0, np.int64)
    coordinates = np.empty((0, 3), np.float64)
    steps: dict[int, FrdStep] = {}
    step = increment = 0
    seen_end = False
    with Path(path).open("rb") as stream:
        lines = _lines(stream)
        for line in lines:
            key = line[:6]
            if key == b"    2C":
                if _int(line[36:74]) not in (0, 1):
                    raise FrdFormatError("binary .frd output is not supported; write ASCII results")
                id_width = 5 if _int(line[36:74]) == 0 else 10
                node_ids, coordinates = _block(lines, id_width, 3)
            elif key == b"    3C":
                _skip_block(lines)
            elif line.startswith(b"    1PSTEP"):
                increment = _int(line[36:48])
                step = _int(line[48:60])
            elif line.startswith(b"  100C"):
                if line[74:75] not in (b"", b"1", b" "):
                    raise FrdFormatError("binary .frd output is not supported; write ASCII results")
                time = float(line[12:24])
                header = next(lines, b"")
                if not header.startswith(b" -4"):
                    raise FrdFormatError(f"expected a -4 line after the result header, got {header[:40]!r}")
                name = header[5:13].strip()
                components = _int(header[13:18])
                body = _components(lines)
                if name == b"DISP":
                    ids, values = _block_after(body, lines, 10, 3, components)
                    number = step if step else len(steps) + 1
                    steps[number] = FrdStep(number, increment or 1, time, ids, values)
                else:
                    _skip_from(body, lines)
            elif line.strip() == b"9999":
                seen_end = True
                break
    if not seen_end:
        raise FrdFormatError("the .frd file has no end marker (9999); the run did not finish")
    return FrdResult(node_ids=node_ids, coordinates=coordinates, steps=tuple(steps[k] for k in sorted(steps)))


def _components(lines: Iterator[bytes]) -> bytes:
    """Skip the ` -5` component lines and return the first line after them."""
    for line in lines:
        if not line.startswith(b" -5"):
            return line
    raise FrdFormatError("the file ends inside a result header (truncated .frd)")


def _block_after(
    first: bytes, lines: Iterator[bytes], id_width: int, values: int, components: int
) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
    if components not in (3, 4):  # DISP is announced with 4 components: D1, D2, D3 and the derived ALL
        raise FrdFormatError(f"unexpected DISP block with {components} components")
    # itertools.chain, not a generator with `yield from`: closing that would close the file's line iterator too.
    return _block(itertools.chain([first], lines), id_width, values)


def _skip_from(first: bytes, lines: Iterator[bytes]) -> None:
    if first.startswith(b" -3"):
        return
    _skip_block(lines)


def read_frd_displacements(path: Path) -> list[FrdStep]:
    """The displacement field of every step, in step order: node ids (N,) and an (N, 3) float64 array."""
    return list(read_frd(path).steps)
