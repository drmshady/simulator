"""The content-addressed store under `<data>/store/`: meshes, solver outputs and processed fields (research R12, R14).

Everything computed from one input record is stored together under its input hash (the key):

    <root>/<key[:2]>/<key>/<name>.<kind>.zst

- `kind` is `npy` (a NumPy array in `.npy` format, always read back without pickle), `json`, or `bin` (raw bytes,
  such as a `.glb`). Every file is zstd-compressed with a frame checksum, so a damaged file fails to read.
- Every write goes to a temporary file in the same folder, is flushed to disk, and is then renamed into place, so a
  reader never sees half a file.
- `complete(key)` writes `manifest.json`, listing every artefact; the pipeline calls it last. Any later write removes
  the manifest again, so `is_complete(key)` tells a finished set from one interrupted mid-write.
- `delete(key)` removes the whole set, so a failed run leaves nothing partial behind (FR-022).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Literal

import numpy as np
import numpy.typing as npt
import zstandard as zstd
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import to_jsonable_python

from dentalsim.clock import utc_now
from dentalsim.config import get_settings
from dentalsim.errors import DentalsimError, NotFound

ArtefactKind = Literal["npy", "json", "bin"]
KINDS: tuple[ArtefactKind, ...] = ("npy", "json", "bin")
_KIND_BY_EXTENSION: dict[str, ArtefactKind] = {kind: kind for kind in KINDS}
KEY = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
SUFFIX = ".zst"
MANIFEST = "manifest.json"
TEMP_PREFIX = ".tmp-"
COMPRESSION_LEVEL = 3


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestEntry(_Model):
    name: str
    kind: ArtefactKind
    size_bytes: int = Field(ge=0)
    """The compressed size on disk."""


class StoreManifest(_Model):
    """Written last by `complete()`: the set of artefacts stored under `key` is finished."""

    key: str
    completed_at: AwareDatetime
    artefacts: list[ManifestEntry]
    meta: dict[str, Any] = {}


def check_key(key: str) -> str:
    """A store key is an input hash: 64 lowercase hexadecimal characters."""
    if not KEY.match(key):
        raise ValueError(f"a store key must be a SHA-256 hex digest, not {key!r}")
    return key


def check_name(name: str) -> str:
    """An artefact name is one file name: letters, digits, `_`, `-` and `.`, with no separators and no `..`."""
    if not NAME.match(name) or ".." in name or name.endswith("."):
        raise ValueError(f"not a valid artefact name: {name!r}")
    if name.split(".")[0].lower() in WINDOWS_RESERVED:
        raise ValueError(f"{name!r} is a reserved file name on Windows")
    return name


class ContentStore:
    """Artefacts keyed by (input hash, name). `root` defaults to `<data folder>/store`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else get_settings().data_dir / "store"

    def folder(self, key: str) -> Path:
        check_key(key)
        return self.root / key[:2] / key

    def path(self, key: str, name: str, kind: ArtefactKind) -> Path:
        return self.folder(key) / f"{check_name(name)}.{kind}{SUFFIX}"

    # Writing

    def put_array(self, key: str, name: str, array: npt.NDArray[Any]) -> Path:
        plain = np.asarray(array)
        if plain.dtype.hasobject:
            raise ValueError(f"array {name!r} holds Python objects, which could only be stored with pickle")
        return self._write(key, name, "npy", lambda out: np.lib.format.write_array(out, plain, allow_pickle=False))

    def put_json(self, key: str, name: str, obj: Any) -> Path:
        """`obj` is anything JSON can hold, pydantic models included. NaN and infinities are refused."""
        text = json.dumps(to_jsonable_python(obj), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        data = text.encode("utf-8")
        return self._write(key, name, "json", lambda out: out.write(data))

    def put_bytes(self, key: str, name: str, data: bytes) -> Path:
        return self._write(key, name, "bin", lambda out: out.write(data))

    def _write(self, key: str, name: str, kind: ArtefactKind, write: Callable[[IO[bytes]], object]) -> Path:
        target = self.path(key, name, kind)
        folder = target.parent
        folder.mkdir(parents=True, exist_ok=True)
        self._check_no_clash(key, name, kind)
        (folder / MANIFEST).unlink(missing_ok=True)

        def compressed(raw: IO[bytes]) -> None:
            compressor = zstd.ZstdCompressor(level=COMPRESSION_LEVEL, write_checksum=True)
            with compressor.stream_writer(raw, closefd=False) as out:
                write(out)

        _atomic_write(target, compressed)
        return target

    def _check_no_clash(self, key: str, name: str, kind: ArtefactKind) -> None:
        """One name holds one artefact. Names that differ only in case would share a file on Windows."""
        for existing, existing_kind in self._artefacts(key):
            if existing.casefold() == name.casefold() and (existing, existing_kind) != (name, kind):
                raise ValueError(f"{name!r} clashes with the stored {existing_kind} artefact {existing!r}")

    # Reading

    def get_array(self, key: str, name: str) -> npt.NDArray[Any]:
        with self._open(key, name, "npy") as reader:
            array = np.lib.format.read_array(reader, allow_pickle=False)
            # Reading to the end of the frame makes zstd verify its checksum.
            if reader.read(1):
                raise DentalsimError(f"stored array {name!r} under {key} has unexpected trailing data")
        return array

    def get_json(self, key: str, name: str) -> Any:
        with self._open(key, name, "json") as reader:
            return json.loads(reader.read().decode("utf-8"))

    def get_bytes(self, key: str, name: str) -> bytes:
        with self._open(key, name, "bin") as reader:
            return reader.read()

    @contextmanager
    def _open(self, key: str, name: str, kind: ArtefactKind) -> Iterator[zstd.ZstdDecompressionReader]:
        path = self.path(key, name, kind)
        try:
            raw = path.open("rb")
        except FileNotFoundError:
            raise NotFound(f"no {kind} artefact {name!r} is stored under {key}") from None
        with raw, zstd.ZstdDecompressor().stream_reader(raw) as reader:
            yield reader

    def exists(self, key: str, name: str) -> bool:
        return any(self.path(key, name, kind).is_file() for kind in KINDS)

    def names(self, key: str) -> list[str]:
        """The stored artefact names, sorted. Temporary files and the manifest are not artefacts."""
        return sorted(name for name, _ in self._artefacts(key))

    def _artefacts(self, key: str) -> list[tuple[str, ArtefactKind]]:
        folder = self.folder(key)
        if not folder.is_dir():
            return []
        found: list[tuple[str, ArtefactKind]] = []
        for path in folder.iterdir():
            if not path.is_file() or path.name.startswith(TEMP_PREFIX) or not path.name.endswith(SUFFIX):
                continue
            name, _, extension = path.name.removesuffix(SUFFIX).rpartition(".")
            kind = _KIND_BY_EXTENSION.get(extension)
            if name and kind is not None:
                found.append((name, kind))
        return found

    def keys(self) -> list[str]:
        """Every key with a folder in the store, complete or not, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(
            folder.name
            for prefix in self.root.iterdir()
            if prefix.is_dir()
            for folder in prefix.iterdir()
            if folder.is_dir() and KEY.match(folder.name) and folder.name[:2] == prefix.name
        )

    def size_bytes(self, key: str) -> int:
        """Disk space used under `key`: every file, compressed, the manifest included."""
        folder = self.folder(key)
        if not folder.is_dir():
            return 0
        return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())

    # Completion and removal

    def complete(self, key: str, *, meta: Mapping[str, Any] | None = None) -> StoreManifest:
        """Declare the set under `key` finished by writing its manifest. Call it after the last artefact."""
        artefacts = sorted(self._artefacts(key))
        if not artefacts:
            raise ValueError(f"nothing is stored under {key}, so it cannot be complete")
        manifest = StoreManifest(
            key=key,
            completed_at=utc_now(),
            artefacts=[
                ManifestEntry(name=name, kind=kind, size_bytes=self.path(key, name, kind).stat().st_size)
                for name, kind in artefacts
            ],
            meta=dict(meta or {}),
        )
        data = manifest.model_dump_json(indent=2).encode("utf-8")
        _atomic_write(self.folder(key) / MANIFEST, lambda raw: raw.write(data))
        return manifest

    def get_manifest(self, key: str) -> StoreManifest | None:
        try:
            text = (self.folder(key) / MANIFEST).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            return StoreManifest.model_validate_json(text)
        except ValidationError:
            return None

    def is_complete(self, key: str) -> bool:
        """True when the manifest exists and every artefact it lists is on disk at its recorded size."""
        manifest = self.get_manifest(key)
        if manifest is None or manifest.key != key:
            return False
        for entry in manifest.artefacts:
            path = self.path(key, entry.name, entry.kind)
            if not path.is_file() or path.stat().st_size != entry.size_bytes:
                return False
        return True

    def delete(self, key: str) -> None:
        """Remove everything stored under `key`. The manifest goes first, so an interrupted delete is incomplete."""
        folder = self.folder(key)
        if not folder.exists():
            return
        (folder / MANIFEST).unlink(missing_ok=True)
        shutil.rmtree(folder)
        with contextlib.suppress(OSError):  # other keys still share the prefix folder
            folder.parent.rmdir()


def _atomic_write(target: Path, write: Callable[[IO[bytes]], object]) -> None:
    """Write to a temporary file beside `target`, flush it to disk, then rename it into place.

    On any failure the temporary file is removed and `target` is left as it was.
    """
    handle, temp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".part", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as raw:
            write(raw)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _fsync_folder(target.parent)


def _fsync_folder(folder: Path) -> None:
    """Make the rename durable. Windows cannot open a folder for fsync; NTFS journals the rename itself."""
    if os.name == "nt":
        return
    handle = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
