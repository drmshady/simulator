"""Reader for CalculiX printed results (.dat): integration-point stress, strain and coordinates, element volumes,
support reaction totals and printed nodal displacements, per step.

Layout written by ccx 2.23 (printout.f, printoutint.f, printoutelem.f, printoutnode.f, calinput.f):

- each step starts with a `S T E P <n>` line and each increment with `INCREMENT <n>`
- each block starts with a header such as
  ` stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set EALL and time  0.1000000E+01`, then a blank line,
  then its data lines; set names are upper case; the time is the total time, which grows by 1.0 per static step
- stresses and strains: element (I10), integration point (I3), 6 × E13.6; coordinates: element, point, 3 × E13.6;
  volumes: element, 1 × E13.6; `total force` (`*NODE PRINT, TOTALS=ONLY`): 3 × E13.6; nodal values: node (I10),
  3 × E13.6. Every value has a blank before it, so values never touch. A Fortran exponent of three digits drops
  the `E` (`1.234567-100`); that form is read too.

Component order and meaning, which downstream code relies on:

- stress `S`: Cauchy stress, columns xx, yy, zz, xy, xz, yz (MPa when the model is in mm, N, MPa)
- strain `E`: Lagrange strain tensor components (equal to the small-strain tensor in a linear step), columns
  xx, yy, zz, xy, xz, yz. The shear columns are TENSOR components: exy = (du/dy + dv/dx) / 2, half the engineering
  shear strain gamma_xy
- integration-point coordinates: global x, y, z; C3D10 has 4 integration points per element, each carrying one
  quarter of the element volume (research R5)

Steps are told apart by the `S T E P` lines, or, in files without them, by the time in the block headers. When a
step has several increments, its last increment is kept.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

ELEMENT_SET = "EALL"
_HEADER = re.compile(
    rb"^(?P<title>[a-z][a-z .]*?)\s*(?:\(|for\s*set|and\s+time|$)",
)
_SET = re.compile(rb"for\s*set\s+(\S+)")
_TIME = re.compile(rb"and\s+time\s+(\S+)")
_FORTRAN_EXPONENT = re.compile(rb"(?<=\d)([+-]\d{3})(?=\s|$)")

# block title -> (kind, number of leading id columns, number of values)
_BLOCKS = {
    b"stresses": ("stress", 2, 6),
    b"strains": ("strain", 2, 6),
    b"global coordinates": ("coordinates", 2, 3),
    b"volume": ("volume", 1, 1),
    b"total force": ("total_force", 0, 3),
    b"forces": ("forces", 1, 3),
    b"displacements": ("displacements", 1, 3),
}


class DatFormatError(ValueError):
    """The .dat file does not have the layout this reader expects."""


@dataclass(frozen=True)
class NodalValues:
    """Values printed for a node set: node ids (n,) and an (n, 3) array."""

    node_ids: NDArray[np.int64]
    values: NDArray[np.float64]


def _empty_ids() -> NDArray[np.int64]:
    return np.empty(0, np.int64)


def _empty(columns: int) -> NDArray[np.float64]:
    return np.empty((0, columns), np.float64)


@dataclass(frozen=True)
class DatStep:
    """Printed results of one step. Integration-point rows are sorted by (element, integration point), so `stress`,
    `strain` and `ip_coordinates` line up row by row; volumes are sorted by element."""

    step: int
    time: float
    element_ids: NDArray[np.int64] = field(default_factory=_empty_ids)
    ip_numbers: NDArray[np.int64] = field(default_factory=_empty_ids)
    stress: NDArray[np.float64] = field(default_factory=lambda: _empty(6))
    strain: NDArray[np.float64] = field(default_factory=lambda: _empty(6))
    ip_coordinates: NDArray[np.float64] = field(default_factory=lambda: _empty(3))
    volume_element_ids: NDArray[np.int64] = field(default_factory=_empty_ids)
    volumes: NDArray[np.float64] = field(default_factory=lambda: np.empty(0, np.float64))
    reaction_totals: Mapping[str, NDArray[np.float64]] = field(default_factory=dict)
    reactions: Mapping[str, NodalValues] = field(default_factory=dict)
    displacements: Mapping[str, NodalValues] = field(default_factory=dict)

    def ip_volumes(self) -> NDArray[np.float64]:
        """The volume carried by each integration-point row: its element's volume divided by the number of
        integration points of that element (4 for C3D10)."""
        if not self.element_ids.size:
            return np.empty(0, np.float64)
        elements, counts = np.unique(self.element_ids, return_counts=True)
        where = np.searchsorted(self.volume_element_ids, elements)
        if np.any(where >= self.volume_element_ids.size) or np.any(self.volume_element_ids[where] != elements):
            raise DatFormatError("some elements with integration-point values have no printed volume")
        per_point = self.volumes[where] / counts
        return np.repeat(per_point, counts)


@dataclass
class _Block:
    kind: str
    set_name: str
    time: float
    ids: int
    values: int
    rows: list[bytes] = field(default_factory=list)


def _parse_rows(rows: list[bytes], columns: int) -> NDArray[np.float64]:
    if not rows:
        return np.empty((0, columns), np.float64)
    blob = b"\n".join(rows)
    try:
        table = np.loadtxt(io.BytesIO(blob), dtype=np.float64, ndmin=2)
    except ValueError:
        table = _parse_rows_slowly(rows, columns)
    if table.shape[1] != columns:
        table = _parse_rows_slowly(rows, columns)
    return table


def _parse_rows_slowly(rows: list[bytes], columns: int) -> NDArray[np.float64]:
    """Token by token: tolerates three-digit Fortran exponents and trailing text (orientation names)."""
    table = np.empty((len(rows), columns), np.float64)
    for i, row in enumerate(rows):
        tokens = row.split()
        if len(tokens) < columns:
            raise DatFormatError(f"expected {columns} values, got: {row[:80]!r}")
        for j in range(columns):
            table[i, j] = float(_FORTRAN_EXPONENT.sub(rb"E\1", tokens[j]))
    return table


class _StepBuilder:
    def __init__(self, step: int, elset: str) -> None:
        self.step = step
        self.elset = elset
        self.time = 0.0
        self.ip: dict[str, NDArray[np.float64]] = {}
        self.volume: NDArray[np.float64] | None = None
        self.reaction_totals: dict[str, NDArray[np.float64]] = {}
        self.reactions: dict[str, NodalValues] = {}
        self.displacements: dict[str, NodalValues] = {}

    def add(self, block: _Block) -> None:
        self.time = block.time
        table = _parse_rows(block.rows, block.ids + block.values)
        if block.kind in ("stress", "strain", "coordinates", "volume"):
            if block.set_name != self.elset:
                return
            if block.kind == "volume":
                self.volume = table
            else:
                self.ip[block.kind] = table
        elif block.kind == "total_force":
            if table.shape[0] != 1:
                raise DatFormatError(f"expected one total force line for set {block.set_name}")
            self.reaction_totals[block.set_name] = table[0]
        else:
            values = NodalValues(node_ids=table[:, 0].astype(np.int64), values=table[:, 1:])
            target = self.reactions if block.kind == "forces" else self.displacements
            target[block.set_name] = values

    def build(self) -> DatStep:
        keys = np.empty((0, 2), np.int64)
        arrays: dict[str, NDArray[np.float64]] = {}
        for kind, table in sorted(self.ip.items()):
            table = table[np.lexsort((table[:, 1], table[:, 0]))]
            these = table[:, :2].astype(np.int64)
            if arrays and not np.array_equal(keys, these):
                raise DatFormatError(f"step {self.step}: {kind} rows do not match the other integration-point rows")
            keys = these
            arrays[kind] = table[:, 2:]
        volume_ids, volumes = _empty_ids(), np.empty(0, np.float64)
        if self.volume is not None:
            volume = self.volume[np.argsort(self.volume[:, 0], kind="stable")]
            volume_ids, volumes = volume[:, 0].astype(np.int64), volume[:, 1].copy()
        return DatStep(
            step=self.step,
            time=self.time,
            element_ids=keys[:, 0].copy(),
            ip_numbers=keys[:, 1].copy(),
            stress=arrays.get("stress", _empty(6)),
            strain=arrays.get("strain", _empty(6)),
            ip_coordinates=arrays.get("coordinates", _empty(3)),
            volume_element_ids=volume_ids,
            volumes=volumes,
            reaction_totals=dict(self.reaction_totals),
            reactions=dict(self.reactions),
            displacements=dict(self.displacements),
        )


def read_dat(path: Path, *, elset: str = ELEMENT_SET) -> list[DatStep]:
    """Every step of a .dat file, in step order. Element blocks are kept for `elset` only (case does not matter);
    nodal blocks are kept for every node set, keyed by the upper-case set name."""
    elset = elset.upper()
    blocks: list[tuple[int | None, int, _Block]] = []  # (step marker, increment marker, block)
    step_marker: int | None = None
    increment = 0
    current: _Block | None = None
    with Path(path).open("rb") as stream:
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(b"S T E P"):
                step_marker = int(line.split()[-1])
                increment = 0
                current = None
            elif line.startswith(b"INCREMENT"):
                increment = int(line.split()[-1])
                current = None
            elif line[:1].isalpha() and _TIME.search(line):
                current = _header(line)
                if current is not None:
                    blocks.append((step_marker, increment, current))
            elif current is not None:
                current.rows.append(line)  # a data line; NaN or Infinity values also start with a letter
    return _group(blocks, elset)


def _header(line: bytes) -> _Block | None:
    title = _HEADER.match(line)
    time = _TIME.search(line)
    if title is None or time is None:
        return None
    spec = _BLOCKS.get(title.group("title").strip())
    if spec is None:
        return None  # a block this reader does not use; its data lines are skipped
    set_match = _SET.search(line)
    set_name = set_match.group(1).decode("ascii", errors="replace").upper() if set_match else ""
    kind, ids, values = spec
    time_value = float(_FORTRAN_EXPONENT.sub(rb"E\1", time.group(1)))
    return _Block(kind=kind, set_name=set_name, time=time_value, ids=ids, values=values)


def _group(blocks: list[tuple[int | None, int, _Block]], elset: str) -> list[DatStep]:
    with_markers = any(marker is not None for marker, _, _ in blocks)
    keyed: dict[float | int, list[tuple[int, _Block]]] = {}
    for marker, increment, block in blocks:
        key: float | int = (marker if marker is not None else 0) if with_markers else block.time
        keyed.setdefault(key, []).append((increment, block))
    steps = []
    for number, key in enumerate(keyed, start=1):
        entries = keyed[key]
        last_increment = max(increment for increment, _ in entries)
        last_time = max(block.time for increment, block in entries if increment == last_increment)
        builder = _StepBuilder(int(key) if with_markers else number, elset)
        for increment, block in entries:
            if increment == last_increment and block.time == last_time:
                builder.add(block)
        steps.append(builder.build())
    return steps
