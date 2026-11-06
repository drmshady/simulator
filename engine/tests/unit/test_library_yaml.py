"""The `dentalsim.library/1` file format (T035, contracts/library-entry-format.md): parsing with line numbers, the
snapshot-only keys, and deterministic snapshots."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from dentalsim.errors import Refused
from dentalsim.library.models import LiteratureEntry, LiteratureEntryVersion, Source, ValueBySource
from dentalsim.library.yaml_io import (
    SnapshotItem,
    load_proposal,
    parse_proposal,
    proposal_problems,
    snapshot_text,
    write_snapshot,
)

PROPOSAL = """\
format: dentalsim.library/1
prepared_by: claude
prepared_at: 2026-11-05
entries:
  - parameter_key: bone.cancellous.E.D4
    category: bone
    label: Cancellous bone elastic modulus, density class D4
    units: MPa
    typical: 690
    range: {min: 200, max: 1400}
    values_by_source:
      - source: src-a
        value: 690
        note: "Mean of the reported D4 range."
    notes: "Linear isotropic model."
  - parameter_key: bone.cancellous.nu
    category: bone
    units: 1
    typical: 0.3
    range: {min: 0.2, max: 0.35}
    values_by_source:
      - {source: src-b, value: 0.3}
sources:
  - id: src-a
    authors: ["Sjöström Ø", "Keaveny TM"]
    year: 2000
    title: "Elastic modulus and strength of human trabecular bone"
    journal: "J Biomech"
    doi: "https://doi.org/10.1016/S0021-9290(99)00137-2"
    pmid: null
    study_type: experimental
  - id: src-b
    authors: ["Smith AB"]
    year: 2001
    title: "Poisson's ratio of cancellous bone"
    pmid: 10580382
    study_type: experimental
"""


def _refusal(text: str) -> Refused:
    with pytest.raises(Refused) as caught:
        parse_proposal(text)
    assert caught.value.code == "validation_failed"
    return caught.value


def test_parses_a_valid_proposal() -> None:
    proposal = parse_proposal(PROPOSAL)
    assert proposal.prepared_by == "claude"
    assert proposal.prepared_at == date(2026, 11, 5)
    assert not proposal.is_snapshot
    first, second = proposal.entries
    assert first.parameter_key == "bone.cancellous.E.D4"
    assert first.range.min == 200 and first.range.max == 1400
    assert first.values_by_source[0].note == "Mean of the reported D4 range."
    assert second.units == "1"  # `units: 1` reads as a number in YAML
    assert second.label is None
    assert proposal.sources[0].doi == "10.1016/S0021-9290(99)00137-2"  # the resolver prefix is removed
    assert proposal.sources[1].pmid == "10580382"


def test_load_proposal_reads_a_file_with_a_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "seed.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + PROPOSAL.encode())
    assert len(load_proposal(path).entries) == 2


def test_problems_carry_the_entry_key_and_line() -> None:
    text = PROPOSAL.replace("typical: 690", "typical: 2000").replace("units: MPa", "units: GPa")
    refused = _refusal(text)
    by_code = {p["code"]: p for p in refused.problems}
    assert by_code["range"]["entry"] == "bone.cancellous.E.D4"
    assert by_code["range"]["line"] == 9
    assert (
        by_code["range"]["message"]
        == "line 9, entry bone.cancellous.E.D4: typical 2000 lies outside the range 200–1400"
    )
    assert by_code["units"]["line"] == 8
    assert "units must be 'MPa'" in by_code["units"]["message"]


def test_structural_problems_are_reported_with_their_field() -> None:
    text = PROPOSAL.replace("    typical: 0.3\n", "").replace(
        "study_type: experimental\n  - id: src-b", "study_type: guess\n  - id: src-b"
    )
    text = text.replace("label: Cancellous", "colour: red\n    label: Cancellous")
    refused = _refusal(text)
    messages = [p["message"] for p in refused.problems]
    assert any("entry bone.cancellous.nu: the required field 'typical' is missing" in m for m in messages)
    assert any("entry bone.cancellous.E.D4: unknown field 'colour'" in m for m in messages)
    assert any("source src-a: study_type: Input should be" in m for m in messages)
    # The source with the invalid study type is not also reported as unknown where it is cited.
    assert not any(p["code"] == "unknown_source" for p in refused.problems)


def test_header_problems() -> None:
    refused = _refusal(
        PROPOSAL.replace("format: dentalsim.library/1", "format: dentalsim.library/2").replace(
            "prepared_by: claude\n", ""
        )
    )
    fields = {p.get("field") for p in refused.problems}
    assert "format" in fields
    assert "prepared_by" in fields


def test_invalid_yaml_and_non_mapping_are_refused() -> None:
    refused = _refusal("format: [unclosed\n")
    assert refused.problems[0]["code"] == "invalid_yaml"
    assert refused.problems[0]["line"] is not None
    assert _refusal("- just\n- a list\n").problems[0]["code"] == "invalid_yaml"


def test_snapshot_only_keys_need_snapshot_of() -> None:
    text = PROPOSAL.replace(
        '    notes: "Linear isotropic model."\n', "    notes: x\n    version: 3\n    content_hash: abc\n"
    )
    text = text.replace(
        "prepared_at: 2026-11-05\n", "prepared_at: 2026-11-05\nexported_at: 2026-11-05T10:00:00+00:00\n"
    )
    refused = _refusal(text)
    snapshot_only = [p for p in refused.problems if p["code"] == "snapshot_only"]
    assert {p["field"] for p in snapshot_only} == {"exported_at", "version", "content_hash"}
    entry_problem = next(p for p in snapshot_only if p["field"] == "version")
    assert entry_problem["entry"] == "bone.cancellous.E.D4"
    assert entry_problem["line"] == 17


def test_a_proposal_needs_entries_and_sources() -> None:
    refused = _refusal("format: dentalsim.library/1\nprepared_by: owner\nprepared_at: 2026-11-05\nentries: []\n")
    codes = {p["code"] for p in refused.problems}
    assert codes == {"no_entries", "missing_field"}


# Snapshots -------------------------------------------------------------------------------------------------------

T0 = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def _item(key: str, source_id: str, *, typical: float = 690.0, notes: str | None = None) -> SnapshotItem:
    source = Source(
        id=source_id,
        authors=["Smith AB"],
        year=2001,
        title=f"Paper {source_id}",
        doi=f"10.1000/{source_id.lower()}",
        study_type="experimental",
    )
    entry = LiteratureEntry(
        id=f"E-{key}", parameter_key=key, category="bone", label=key, current_version=2, created_at=T0
    )
    version = LiteratureEntryVersion(
        entry_id=entry.id,
        version=2,
        units="MPa",
        typical=typical,
        range_min=200.0,
        range_max=1400.5,
        values_by_source=[ValueBySource(source_id=source_id, value=typical, note="mean")],
        notes=notes,
        status="approved",
        origin="claude_proposal",
        proposed_at=T0,
        approved_at=T0,
        content_hash=f"hash-{key}",
    )
    return SnapshotItem(entry=entry, version=version, sources=(source,))


def test_snapshot_is_sorted_and_deterministic(tmp_path: Path) -> None:
    items = [_item("bone.cortical.E", "S2"), _item("bone.cancellous.E.D4", "S1", notes="Linear.")]
    first = snapshot_text(items, exported_at=T0)
    assert first == snapshot_text(list(reversed(items)), exported_at=T0)
    data: dict[str, Any] = yaml.safe_load(first)
    assert first.startswith("# Approved literature library snapshot")
    assert [e["parameter_key"] for e in data["entries"]] == ["bone.cancellous.E.D4", "bone.cortical.E"]
    assert [s["id"] for s in data["sources"]] == ["S1", "S2"]
    assert data["snapshot_of"] == "approved"
    assert "prepared_by" not in data
    entry = data["entries"][0]
    assert entry["typical"] == 690 and isinstance(entry["typical"], int)  # 690.0 is written as 690
    assert entry["range"] == {"min": 200, "max": 1400.5}
    assert entry["version"] == 2 and entry["content_hash"] == "hash-bone.cancellous.E.D4"
    assert "notes" not in data["entries"][1]

    path = write_snapshot(items, tmp_path / "snapshots" / "library-snapshot.yaml", exported_at=T0)
    assert path.read_bytes() == first.encode()  # LF endings on every platform


def test_snapshot_parses_back() -> None:
    text = snapshot_text([_item("bone.cancellous.E.D4", "S1")], exported_at=T0)
    proposal = parse_proposal(text)
    assert proposal.is_snapshot
    assert proposal.exported_at == T0
    (entry,) = proposal.entries
    assert (entry.version, entry.content_hash, entry.approved_at) == (2, "hash-bone.cancellous.E.D4", T0)
    assert proposal_problems(proposal) == []


def test_snapshot_entries_need_their_version_fields() -> None:
    text = snapshot_text([_item("bone.cancellous.E.D4", "S1")], exported_at=T0).replace(
        "  content_hash: hash-bone.cancellous.E.D4\n", ""
    )
    refused = _refusal(text)
    assert [(p["code"], p["field"]) for p in refused.problems] == [("missing_field", "content_hash")]


def test_empty_snapshot_is_valid() -> None:
    assert parse_proposal(snapshot_text([], exported_at=T0)).entries == []


def test_only_approved_versions_are_written() -> None:
    item = _item("bone.cancellous.E.D4", "S1")
    proposed = item.model_copy(update={"version": item.version.model_copy(update={"status": "proposed"})})
    with pytest.raises(ValueError, match="not approved"):
        snapshot_text([proposed], exported_at=T0)
