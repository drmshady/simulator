"""The content-addressed store: round trips, layout, atomic writes, completion markers and deletion (FR-022)."""

from __future__ import annotations

import io
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import zstandard as zstd

from dentalsim.errors import NotFound
from dentalsim.model.run import LibraryVersionRef
from dentalsim.store import cas
from dentalsim.store.cas import ContentStore
from dentalsim.testing import FixedClock

KEY = "ab" + "0" * 62
OTHER_KEY = "ab" + "1" * 62
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    return ContentStore(tmp_path / "store")


def _temp_files(store: ContentStore, key: str) -> list[str]:
    return [p.name for p in store.folder(key).iterdir() if p.name.startswith(cas.TEMP_PREFIX)]


def test_default_root_is_the_data_folders_store(data_dir: Path) -> None:
    assert ContentStore().root == data_dir / "store"


@pytest.mark.parametrize(
    "array",
    [
        np.arange(12, dtype=np.float64).reshape(3, 4),
        np.array([[1, 2], [3, 4]], dtype=np.int32),
        np.asfortranarray(np.linspace(0, 1, 6).reshape(2, 3)),
        np.zeros((0, 3)),
    ],
    ids=["float64", "int32", "fortran-order", "empty"],
)
def test_arrays_round_trip_with_dtype_and_shape(store: ContentStore, array: np.ndarray[Any, Any]) -> None:
    store.put_array(KEY, "field", array)
    loaded = store.get_array(KEY, "field")
    assert loaded.dtype == array.dtype
    assert loaded.shape == array.shape
    np.testing.assert_array_equal(loaded, array)


def test_json_and_bytes_round_trip(store: ContentStore) -> None:
    store.put_json(KEY, "key_numbers", {"strain": [1.5, 2.5], "unit": "µε", "ok": True})
    store.put_bytes(KEY, "crown-1.bite-1.glb", b"glTF\x02\x00\x00\x00")
    assert store.get_json(KEY, "key_numbers") == {"strain": [1.5, 2.5], "unit": "µε", "ok": True}
    assert store.get_bytes(KEY, "crown-1.bite-1.glb") == b"glTF\x02\x00\x00\x00"


def test_pydantic_models_are_stored_as_json(store: ContentStore) -> None:
    ref = LibraryVersionRef(entry_id="E1", parameter_key="bone.cortical.E", version=2, content_hash="h")
    store.put_json(KEY, "ref", ref)
    assert LibraryVersionRef.model_validate(store.get_json(KEY, "ref")) == ref


def test_layout_is_prefix_key_name_kind_zst(store: ContentStore) -> None:
    store.put_array(KEY, "displacement", np.ones(3))
    store.put_json(KEY, "summary", {})
    store.put_bytes(KEY, "part.glb", b"x")
    folder = store.root / KEY[:2] / KEY
    assert sorted(p.name for p in folder.iterdir()) == ["displacement.npy.zst", "part.glb.bin.zst", "summary.json.zst"]
    for path in folder.iterdir():
        assert path.read_bytes()[:4] == ZSTD_MAGIC


def test_exists_and_names(store: ContentStore) -> None:
    assert not store.exists(KEY, "field")
    assert store.names(KEY) == []
    store.put_array(KEY, "field", np.ones(2))
    store.put_json(KEY, "a_summary", [])
    store.complete(KEY)
    assert store.exists(KEY, "field")
    assert not store.exists(OTHER_KEY, "field")
    assert store.names(KEY) == ["a_summary", "field"]


def test_rewriting_an_artefact_replaces_it(store: ContentStore) -> None:
    store.put_json(KEY, "doc", {"v": 1})
    store.put_json(KEY, "doc", {"v": 2})
    assert store.get_json(KEY, "doc") == {"v": 2}
    assert store.names(KEY) == ["doc"]


@pytest.mark.parametrize(
    "name", ["", "a/b", "a\\b", "..", "a..b", ".hidden", "trailing.", "con", "NUL.json", "com1", "with space", "é"]
)
def test_invalid_names_are_refused(store: ContentStore, name: str) -> None:
    with pytest.raises(ValueError):
        store.put_bytes(KEY, name, b"x")


@pytest.mark.parametrize("key", ["", "abc", "AB" + "0" * 62, "../" + "0" * 61, "g" * 64, "0" * 65])
def test_invalid_keys_are_refused(store: ContentStore, key: str) -> None:
    with pytest.raises(ValueError):
        store.put_bytes(key, "data", b"x")


def test_one_name_holds_one_artefact(store: ContentStore) -> None:
    store.put_json(KEY, "result", {})
    with pytest.raises(ValueError, match="clashes"):
        store.put_array(KEY, "result", np.ones(1))
    with pytest.raises(ValueError, match="clashes"):
        store.put_json(KEY, "Result", {})


def test_json_without_nan_or_infinity(store: ContentStore) -> None:
    for bad in (math.nan, math.inf):
        with pytest.raises(ValueError):
            store.put_json(KEY, "doc", {"value": bad})
    assert not store.exists(KEY, "doc")


def test_object_arrays_are_refused(store: ContentStore) -> None:
    with pytest.raises(ValueError, match="pickle"):
        store.put_array(KEY, "objects", np.array([{"a": 1}], dtype=object))
    assert not store.exists(KEY, "objects")


def test_a_pickled_array_is_never_loaded(store: ContentStore) -> None:
    buffer = io.BytesIO()
    np.save(buffer, np.array([{"a": 1}], dtype=object), allow_pickle=True)
    path = store.path(KEY, "smuggled", "npy")
    path.parent.mkdir(parents=True)
    path.write_bytes(zstd.ZstdCompressor().compress(buffer.getvalue()))
    with pytest.raises(ValueError, match="allow_pickle"):
        store.get_array(KEY, "smuggled")


def test_a_damaged_file_fails_to_read(store: ContentStore) -> None:
    path = store.put_json(KEY, "doc", {"values": list(range(100))})
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(zstd.ZstdError):
        store.get_json(KEY, "doc")


def test_reading_a_missing_artefact_raises_not_found(store: ContentStore) -> None:
    with pytest.raises(NotFound, match="field"):
        store.get_array(KEY, "field")
    store.put_json(KEY, "field", {})
    with pytest.raises(NotFound):
        store.get_array(KEY, "field")


def test_failed_write_leaves_the_old_file_and_no_temporary_file(
    store: ContentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put_json(KEY, "doc", {"v": 1})

    def broken_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("disk unplugged")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk unplugged"):
        store.put_json(KEY, "doc", {"v": 2})
    monkeypatch.undo()
    assert store.get_json(KEY, "doc") == {"v": 1}
    assert _temp_files(store, KEY) == []


def test_failure_while_writing_leaves_nothing(store: ContentStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_write(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("interrupted mid-array")

    monkeypatch.setattr(np.lib.format, "write_array", broken_write)
    with pytest.raises(RuntimeError, match="interrupted"):
        store.put_array(KEY, "field", np.ones(10))
    assert not store.exists(KEY, "field")
    assert _temp_files(store, KEY) == []


def test_complete_marks_a_finished_set(store: ContentStore, fixed_time: FixedClock) -> None:
    assert not store.is_complete(KEY)
    store.put_array(KEY, "field", np.ones(4))
    store.put_json(KEY, "summary", {"ok": True})
    assert not store.is_complete(KEY)
    manifest = store.complete(KEY, meta={"stage": "key_numbers"})
    assert store.is_complete(KEY)
    assert manifest.completed_at == fixed_time.now
    assert [(a.name, a.kind) for a in manifest.artefacts] == [("field", "npy"), ("summary", "json")]
    assert all(a.size_bytes > 0 for a in manifest.artefacts)
    assert store.get_manifest(KEY) == manifest
    assert "manifest" not in store.names(KEY)


def test_a_write_after_completion_makes_the_set_incomplete(store: ContentStore) -> None:
    store.put_json(KEY, "summary", {})
    store.complete(KEY)
    store.put_bytes(KEY, "late.glb", b"x")
    assert not store.is_complete(KEY)
    assert store.get_manifest(KEY) is None


def test_a_missing_or_changed_artefact_makes_the_set_incomplete(store: ContentStore) -> None:
    store.put_json(KEY, "a", {})
    path = store.put_json(KEY, "b", {"v": 1})
    store.complete(KEY)
    path.write_bytes(path.read_bytes() + b"\x00")
    assert not store.is_complete(KEY)
    path.unlink()
    assert not store.is_complete(KEY)


def test_an_empty_set_cannot_be_completed(store: ContentStore) -> None:
    with pytest.raises(ValueError, match="nothing is stored"):
        store.complete(KEY)


def test_delete_removes_everything(store: ContentStore) -> None:
    store.put_array(KEY, "field", np.ones(4))
    store.put_json(KEY, "summary", {})
    (store.folder(KEY) / f"{cas.TEMP_PREFIX}leftover.part").write_bytes(b"partial")
    store.delete(KEY)
    assert not store.folder(KEY).exists()
    assert not (store.root / KEY[:2]).exists()
    assert store.names(KEY) == []
    assert store.size_bytes(KEY) == 0
    store.delete(KEY)


def test_delete_keeps_other_keys_with_the_same_prefix(store: ContentStore) -> None:
    store.put_json(KEY, "doc", {})
    store.put_json(OTHER_KEY, "doc", {})
    store.delete(KEY)
    assert store.get_json(OTHER_KEY, "doc") == {}
    assert store.keys() == [OTHER_KEY]


def test_size_bytes_counts_every_file_on_disk(store: ContentStore) -> None:
    store.put_array(KEY, "field", np.arange(1000.0))
    store.put_bytes(KEY, "part.glb", b"x" * 100)
    store.complete(KEY)
    on_disk = sum(p.stat().st_size for p in store.folder(KEY).iterdir())
    assert store.size_bytes(KEY) == on_disk > 0


def test_keys_lists_every_stored_key(store: ContentStore) -> None:
    assert store.keys() == []
    store.put_json(OTHER_KEY, "doc", {})
    store.put_json(KEY, "doc", {})
    assert store.keys() == [KEY, OTHER_KEY]
