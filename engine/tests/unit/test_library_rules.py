"""The literature library rules (T038; FR-012 to FR-016, FR-053 to FR-055) through `LibraryService` and the
`dentalsim library` commands, on the real SQLite repositories, with Crossref and NCBI mocked."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from dentalsim import cli
from dentalsim.commands import library as library_commands
from dentalsim.conventions.loader import get_conventions
from dentalsim.conventions.schema import LibraryUnit
from dentalsim.errors import NotFound, Refused
from dentalsim.library import source_check
from dentalsim.library.models import Source, SourceCheck, ValueBySource
from dentalsim.library.service import EntryChanges, LibraryService
from dentalsim.library.source_check import check_source
from dentalsim.library.yaml_io import ProposalEntry, ProposalRange, ProposalSource, ProposalValue, load_proposal
from dentalsim.model.settings import LibraryValue
from dentalsim.persistence import Repos, open_repos
from dentalsim.testing import FixedClock

DOI_A = "10.1000/example.a"
DOI_B = "10.1000/example.b"
PMID_C = "10000003"
TITLE_A = "Elastic modulus of cancellous bone by density class"
TITLE_B = "Poisson's ratio of trabecular bone in compression"
TITLE_C = "Bite force in adults with implant-supported crowns"


class Internet:
    """Mocked Crossref and NCBI that answer from registered records; `offline` makes every request fail."""

    def __init__(self) -> None:
        self.works: dict[str, dict[str, Any]] = {}
        self.summaries: dict[str, dict[str, Any]] = {}
        self.offline = False
        self.requests: list[httpx.Request] = []

    def add_work(self, doi: str, *, family: str, year: int, title: str) -> None:
        self.works[doi.lower()] = {
            "status": "ok",
            "message-type": "work",
            "message": {
                "DOI": doi.lower(),
                "title": [title],
                "author": [{"given": "A. B.", "family": family, "sequence": "first", "affiliation": []}],
                "issued": {"date-parts": [[year, 3]]},
                "container-title": ["Example Journal"],
            },
        }

    def add_pubmed(self, pmid: str, *, name: str, year: int, title: str) -> None:
        record = {
            "uid": pmid,
            "pubdate": f"{year} Mar",
            "authors": [{"name": name, "authtype": "Author"}],
            "title": title,
        }
        self.summaries[pmid] = {"header": {"type": "esummary"}, "result": {"uids": [pmid], pmid: record}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("no network")
        if request.url.host == "api.crossref.org":
            work = self.works.get(request.url.path.removeprefix("/works/").lower())
            return httpx.Response(200, json=work) if work else httpx.Response(404, text="Resource not found.")
        pmid = request.url.params["id"]
        empty = {"result": {"uids": [pmid], pmid: {"uid": pmid, "error": "cannot get document summary"}}}
        return httpx.Response(200, json=self.summaries.get(pmid, empty))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def checker(self) -> Callable[[Source], SourceCheck]:
        client = self.client()
        return lambda source: check_source(source, client)


def _entry(key: str = "bone.cancellous.E.D4", **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "parameter_key": key,
        "category": "bone",
        "label": "Cancellous bone elastic modulus, density class D4",
        "units": "MPa",
        "typical": 690,
        "range": {"min": 200, "max": 1400},
        "values_by_source": [{"source": "src-a", "value": 690, "note": "Mean of the D4 range."}],
        "notes": "Linear isotropic model.",
    }
    data.update(changes)
    return data


def _nu_entry() -> dict[str, Any]:
    return _entry(
        "bone.cancellous.nu",
        label="Cancellous bone Poisson's ratio",
        units="1",
        typical=0.3,
        range={"min": 0.2, "max": 0.35},
        values_by_source=[{"source": "src-b", "value": 0.3}, {"source": "src-a", "value": 0.32}],
        notes=None,
    )


def _source(sid: str = "src-a", **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": sid,
        "authors": ["Author AB", "Second CD"],
        "year": 2000,
        "title": TITLE_A,
        "journal": "Example Journal",
        "doi": DOI_A,
        "pmid": None,
        "study_type": "experimental",
    }
    data.update(changes)
    return data


def _sources() -> list[dict[str, Any]]:
    return [_source(), _source("src-b", authors=["Other EF"], year=2004, title=TITLE_B, doi=DOI_B)]


def _proposal(
    entries: list[dict[str, Any]] | None = None, sources: list[dict[str, Any]] | None = None, **header: Any
) -> str:
    document = {"format": "dentalsim.library/1", "prepared_by": "claude", "prepared_at": "2026-11-05", **header}
    document["entries"] = [_entry(), _nu_entry()] if entries is None else entries
    document["sources"] = _sources() if sources is None else sources
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _write(folder: Path, text: str, name: str = "proposal.yaml") -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def repos(data_dir: Path) -> Iterator[Repos]:
    opened = open_repos(data_dir)
    yield opened
    opened.close()


@pytest.fixture
def internet() -> Internet:
    net = Internet()
    net.add_work(DOI_A, family="Author", year=2000, title=TITLE_A)
    net.add_work(DOI_B, family="Other", year=2004, title=TITLE_B)
    net.add_pubmed(PMID_C, name="Third GH", year=2010, title=TITLE_C)
    return net


@pytest.fixture
def service(repos: Repos, internet: Internet, fixed_time: FixedClock) -> LibraryService:
    return LibraryService(repos, checker=internet.checker())


def _import(service: LibraryService, tmp_path: Path, text: str | None = None) -> dict[str, str]:
    """Import and return entry ids by parameter key."""
    result = service.import_file(_write(tmp_path, text or _proposal()))
    return {item.parameter_key: item.entry_id for item in result.imported}


def _approved_entry(service: LibraryService, tmp_path: Path, key: str = "bone.cancellous.E.D4") -> str:
    entry_id = _import(service, tmp_path)[key]
    service.check_sources(entry_id, 1)
    service.approve(entry_id, 1)
    return entry_id


# Import validation rules -------------------------------------------------------------------------------------------


def _without(data: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != key}


RULES: dict[str, tuple[Callable[[], str], str, str]] = {
    # name: (file text, expected problem code, text the message must contain)
    "missing units": (lambda: _proposal([_without(_entry(), "units")]), "missing_field", "'units' is missing"),
    "missing range": (lambda: _proposal([_without(_entry(), "range")]), "missing_field", "'range' is missing"),
    "missing category": (lambda: _proposal([_without(_entry(), "category")]), "missing_field", "'category'"),
    "missing sources": (lambda: _proposal([_entry(values_by_source=[])]), "invalid_value", "values_by_source"),
    "typical above range": (lambda: _proposal([_entry(typical=1500)]), "range", "typical 1500 lies outside"),
    "range reversed": (lambda: _proposal([_entry(range={"min": 1400, "max": 200})]), "range", "greater than"),
    "source value outside range": (
        lambda: _proposal([_entry(values_by_source=[{"source": "src-a", "value": 150}])]),
        "range",
        "the value 150 from source 'src-a' lies outside the range 200–1400",
    ),
    "unknown source": (
        lambda: _proposal([_entry(values_by_source=[{"source": "src-z", "value": 690}])]),
        "unknown_source",
        "'src-z' is not listed under sources",
    ),
    "source without DOI or PMID": (
        lambda: _proposal(sources=[_source(doi=None), _sources()[1]]),
        "no_identifier",
        "needs a DOI or a PMID",
    ),
    "modulus in GPa": (lambda: _proposal([_entry(units="GPa", typical=0.69)]), "units", "units must be 'MPa'"),
    "key matches no units row": (
        lambda: _proposal([_entry("bone.cancellous.density")]),
        "units",
        "allowed patterns: bone.*.E, bone.*.E.*, bone.*.nu, bone.cortical.thickness.*",
    ),
    "key of another category": (lambda: _proposal([_entry(category="material")]), "units", "category material"),
    "malformed key": (lambda: _proposal([_entry("bone..E")]), "invalid_key", "dot-separated"),
    "duplicate key": (lambda: _proposal([_entry(), _entry()]), "duplicate_key", "more than once"),
    "unknown category": (lambda: _proposal([_entry(category="tissue")]), "invalid_value", "category"),
    "unknown field": (lambda: _proposal([_entry(status="approved")]), "unknown_field", "unknown field 'status'"),
}


@pytest.mark.parametrize("rule", RULES)
def test_each_import_rule_produces_a_problem(rule: str, service: LibraryService, tmp_path: Path) -> None:
    text, code, fragment = RULES[rule]
    with pytest.raises(Refused) as caught:
        service.import_file(_write(tmp_path, text()))
    assert caught.value.code == "validation_failed"
    matching = [p for p in caught.value.problems if p["code"] == code]
    assert matching, caught.value.problems
    assert any(fragment in p["message"] for p in matching), matching
    assert service.list_entries() == []


def test_every_problem_is_reported_at_once(service: LibraryService, tmp_path: Path) -> None:
    text = _proposal(
        [
            _entry(units="GPa", typical=1500, values_by_source=[{"source": "src-a", "value": 150}]),
            _entry(
                "bone.cancellous.nu",
                units="1",
                typical=0.3,
                range={"min": 0.2, "max": 0.35},
                values_by_source=[{"source": "src-z", "value": 0.3}],
            ),  # fmt: skip
        ],
        sources=[_source(doi=None)],
    )
    with pytest.raises(Refused) as caught:
        service.import_file(_write(tmp_path, text))
    problems = caught.value.problems
    assert sorted(p["code"] for p in problems) == ["no_identifier", "range", "range", "units", "unknown_source"]
    assert {p.get("entry") for p in problems} == {"bone.cancellous.E.D4", "bone.cancellous.nu", None}
    assert all(isinstance(p.get("line"), int) for p in problems)
    assert "5 problems" in caught.value.message


def test_import_creates_proposed_versions_and_maps_sources(service: LibraryService, tmp_path: Path) -> None:
    result = service.import_file(_write(tmp_path, _proposal()))
    assert result.origin == "claude_proposal"
    assert [(i.parameter_key, i.version, i.new_entry) for i in result.imported] == [
        ("bone.cancellous.E.D4", 1, True),
        ("bone.cancellous.nu", 1, True),
    ]
    assert len(result.new_sources) == 2 and set(result.source_ids) == {"src-a", "src-b"}
    entry = service.find_entry("bone.cancellous.nu")
    assert entry.label == "Cancellous bone Poisson's ratio" and entry.current_version is None
    version = service.get_version(entry.id, 1)
    assert version.status == "proposed" and version.origin == "claude_proposal"
    assert [v.source_id for v in version.values_by_source] == [result.source_ids["src-b"], result.source_ids["src-a"]]
    assert version.units == "1" and version.notes is None
    assert len(version.content_hash) == 64
    assert all(s.check.status == "not_checked" for s in service.cited_sources(version))
    assert service.find_entry(entry.id) == entry


def test_owner_files_have_origin_owner(service: LibraryService, tmp_path: Path) -> None:
    result = service.import_file(_write(tmp_path, _proposal(prepared_by="owner")))
    assert result.origin == "owner"
    assert service.get_version(result.imported[0].entry_id, 1).origin == "owner"


def test_importing_an_existing_key_creates_version_2(service: LibraryService, tmp_path: Path) -> None:
    first = service.import_file(_write(tmp_path, _proposal()))
    again = service.import_file(_write(tmp_path, _proposal()))
    assert again.imported == [] and [u.version for u in again.unchanged] == [1, 1]

    changed = _proposal(
        [_entry(typical=700, values_by_source=[{"source": "paper", "value": 700}])],
        sources=[_source("paper", doi=DOI_A.upper())],  # same paper, other local id, DOI in another case
    )
    second = service.import_file(_write(tmp_path, changed, "second.yaml"))
    (item,) = second.imported
    assert item.entry_id == first.imported[0].entry_id
    assert (item.version, item.new_entry) == (2, False)
    assert second.new_sources == []
    assert second.source_ids == {"paper": first.source_ids["src-a"]}
    assert [v.version for v in service.describe(item.entry_id).versions] == [1, 2]
    assert len(service.list_entries()) == 2


def test_a_stored_key_keeps_its_category(repos: Repos, internet: Internet, tmp_path: Path) -> None:
    # Conventions in which the key is allowed in two categories, so only the stored entry can tell them apart.
    conventions = get_conventions()
    extra = LibraryUnit(category="material", pattern="bone.*.E.*", quantity="elastic modulus", units="MPa")
    conventions = conventions.model_copy(update={"library_units": (*conventions.library_units, extra)})
    service = LibraryService(repos, checker=internet.checker(), conventions=conventions)
    _import(service, tmp_path)
    with pytest.raises(Refused) as caught:
        service.import_file(_write(tmp_path, _proposal([_entry(category="material", typical=700)])))
    (problem,) = caught.value.problems
    assert problem["code"] == "category"
    assert "the stored entry has category bone, not material" in problem["message"]
    assert [v.version for v in service.describe(service.find_entry("bone.cancellous.E.D4").id).versions] == [1]


def test_owner_entry_is_proposed_and_can_cite_a_pmid(service: LibraryService) -> None:
    entry = ProposalEntry(
        parameter_key="load.bite_force.molar",
        category="load",
        label="Molar bite force",
        units="N",
        typical=600,
        range=ProposalRange(min=300, max=900),
        values_by_source=[ProposalValue(source="mine", value=600)],
    )
    source = ProposalSource(
        id="mine", authors=["Third GH"], year=2010, title=TITLE_C, pmid=PMID_C, study_type="clinical"
    )
    added = service.add_owner_entry(entry, [source])
    version = service.get_version(added.entry_id, 1)
    assert (version.status, version.origin) == ("proposed", "owner")
    service.check_sources(added.entry_id, 1)
    assert service.approval_problems(added.entry_id, 1) == []
    with pytest.raises(Refused, match="nothing to add"):
        service.add_owner_entry(entry, [source])
    with pytest.raises(Refused) as caught:
        service.add_owner_entry(entry.model_copy(update={"units": "kN"}), [source])
    assert caught.value.problems[0]["code"] == "units"


# Source check and approval (FR-054) ----------------------------------------------------------------------------------


def test_approval_is_refused_until_the_sources_are_checked(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    with pytest.raises(Refused) as caught:
        service.approve(entry_id, 1)
    (problem,) = caught.value.problems
    assert (problem["code"], problem["status"]) == ("source_not_matched", "not_checked")
    assert "Author AB et al. 2000 (doi 10.1000/example.a)" in problem["message"]
    assert service.get_version(entry_id, 1).status == "proposed"


@pytest.mark.parametrize("status", ["resolved_mismatch", "unresolved", "not_checked"])
def test_approval_is_refused_unless_every_source_matches(
    status: str, service: LibraryService, internet: Internet, tmp_path: Path, fixed_time: FixedClock
) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.nu"]
    if status == "resolved_mismatch":
        internet.add_work(DOI_B, family="Other", year=1990, title=TITLE_B)
    elif status == "unresolved":
        del internet.works[DOI_B]
    else:
        internet.offline = True
    checked = service.check_sources(entry_id, 1)
    statuses = {source.doi: check.status for source, check in checked}
    assert statuses[DOI_B] == status
    assert all(source.check == check for source, check in checked)
    with pytest.raises(Refused) as caught:
        service.approve(entry_id, 1)
    failing = [p for p in caught.value.problems if p["code"] == "source_not_matched"]
    assert [p["status"] for p in failing] == ([status, "not_checked"] if status == "not_checked" else [status])

    internet.offline = False
    internet.add_work(DOI_B, family="Other", year=2004, title=TITLE_B)
    fixed_time.advance(hours=1)
    assert {c.status for _, c in service.check_sources(entry_id, 1)} == {"resolved_match"}
    result = service.approve(entry_id, 1)
    assert result.version.status == "approved" and result.version.approved_at == fixed_time.now
    assert result.superseded == []
    assert service.get_entry(entry_id).current_version == 1


def test_only_a_proposed_version_can_be_approved(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _approved_entry(service, tmp_path)
    with pytest.raises(Refused) as caught:
        service.approve(entry_id, 1)
    assert caught.value.problems[0]["code"] == "not_proposed"
    with pytest.raises(NotFound):
        service.approve(entry_id, 7)


def test_approving_v2_supersedes_v1(service: LibraryService, tmp_path: Path, fixed_time: FixedClock) -> None:
    entry_id = _approved_entry(service, tmp_path)
    v2 = service.edit(
        entry_id, {"typical": 720, "values_by_source": [{"source_id": _source_id(service, entry_id), "value": 720}]}
    )
    assert (v2.version, v2.status, v2.origin) == (2, "proposed", "owner")
    assert service.get_version(entry_id, 1).status == "approved"
    assert service.resolve("bone.cancellous.E.D4").version == 1  # v1 stays in use until v2 is approved
    fixed_time.advance(days=1)
    result = service.approve(entry_id, 2)
    assert result.superseded == [1]
    v1 = service.get_version(entry_id, 1)
    assert (v1.status, v1.superseded_at) == ("superseded", fixed_time.now)
    assert v1.typical == 690  # superseded versions stay readable
    resolved = service.resolve("bone.cancellous.E.D4")
    assert (resolved.version, resolved.value) == (2, 720)


def test_an_older_proposed_version_cannot_replace_a_newer_approved_one(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    service.edit(entry_id, {"typical": 700})
    service.check_sources(entry_id, 2)
    service.approve(entry_id, 2)
    with pytest.raises(Refused) as caught:
        service.approve(entry_id, 1)
    assert caught.value.problems[0]["code"] == "newer_version_approved"


# Resolving values (FR-014, FR-015) ---------------------------------------------------------------------------------


def _source_id(service: LibraryService, entry_id: str) -> str:
    return service.newest_version(entry_id).values_by_source[0].source_id


def test_a_proposed_version_cannot_be_resolved(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    with pytest.raises(Refused, match="no approved version"):
        service.resolve("bone.cancellous.E.D4")
    with pytest.raises(Refused, match="proposed; only approved versions"):
        service.resolve_value(LibraryValue(entry_id=entry_id, entry_version=1, choice="typical", value=690))
    with pytest.raises(Refused, match="no library entry"):
        service.resolve("bone.cortical.E")


def test_resolve_typical_source_and_custom(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _approved_entry(service, tmp_path, "bone.cancellous.nu")
    versions = service.get_version(entry_id, 1).values_by_source
    other = versions[1].source_id

    typical = service.resolve("bone.cancellous.nu")
    assert (typical.value, typical.outside_range, typical.units, typical.version) == (0.3, False, "1", 1)
    by_source = service.resolve("bone.cancellous.nu", f"source:{other}")
    assert (by_source.value, by_source.choice) == (0.32, f"source:{other}")
    inside = service.resolve("bone.cancellous.nu", "custom", custom_value=0.25)
    assert not inside.outside_range
    outside = service.resolve("bone.cancellous.nu", "custom", custom_value=0.45)
    assert outside.outside_range
    stored = outside.library_value()
    assert (stored.entry_id, stored.entry_version, stored.value, stored.outside_range) == (entry_id, 1, 0.45, True)
    assert outside.version_ref().content_hash == service.get_version(entry_id, 1).content_hash

    with pytest.raises(Refused, match="cites no source"):
        service.resolve("bone.cancellous.nu", "source:nobody")
    with pytest.raises(Refused, match="custom"):
        service.resolve("bone.cancellous.nu", "custom")
    with pytest.raises(Refused, match="custom"):
        service.resolve("bone.cancellous.nu", "typical", custom_value=0.3)
    with pytest.raises(Refused, match="choice must be"):
        service.resolve("bone.cancellous.nu", "median")


def test_resolve_value_checks_a_stored_setting(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _approved_entry(service, tmp_path)
    source_id = _source_id(service, entry_id)

    def value(choice: str, number: float, *, version: int = 1, outside: bool = False) -> LibraryValue:
        return LibraryValue(
            entry_id=entry_id, entry_version=version, choice=choice, value=number, outside_range=outside
        )

    assert service.resolve_value(value("typical", 690)).value == 690
    assert service.resolve_value(value(f"source:{source_id}", 690)).value == 690
    assert service.resolve_value(value("custom", 1500)).outside_range  # recomputed, not taken from the setting
    assert not service.resolve_value(value("custom", 500, outside=True)).outside_range
    with pytest.raises(Refused, match="not the typical value"):
        service.resolve_value(value("typical", 691))
    with pytest.raises(Refused, match="value 700 is not source"):
        service.resolve_value(value(f"source:{source_id}", 700))
    with pytest.raises(Refused, match="no version 4"):
        service.resolve_value(value("typical", 690, version=4))

    service.edit(entry_id, {"typical": 700})
    service.approve(entry_id, 2)
    with pytest.raises(Refused, match="superseded; only approved versions can be chosen; the approved version is 2"):
        service.resolve_value(value("typical", 690))


# Rejection (FR-055) ------------------------------------------------------------------------------------------------


def test_reject_a_proposed_version(service: LibraryService, tmp_path: Path, fixed_time: FixedClock) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    result = service.reject(entry_id, 1, "  The cited study measured D2 bone.  ")
    assert result.version.status == "rejected"
    assert result.version.rejected_reason == "The cited study measured D2 bone."
    assert result.version.rejected_at == fixed_time.now
    assert not result.was_current
    assert (result.run_flagging, result.note) == ("not_available", "run flagging not available yet")
    with pytest.raises(Refused):
        service.approve(entry_id, 1)
    with pytest.raises(Refused, match="only a proposed or approved version"):
        service.reject(entry_id, 1, "again")


def test_rejecting_the_current_version_leaves_no_approved_version(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _approved_entry(service, tmp_path)
    service.edit(entry_id, {"typical": 700})
    service.approve(entry_id, 2)
    result = service.reject(entry_id, 2, "Transcription error.")
    assert result.was_current
    assert service.get_entry(entry_id).current_version is None
    assert service.get_version(entry_id, 1).status == "superseded"  # not silently approved again
    with pytest.raises(Refused, match="no approved version"):
        service.resolve("bone.cancellous.E.D4")
    with pytest.raises(Refused, match="rejected"):
        service.resolve_value(LibraryValue(entry_id=entry_id, entry_version=2, choice="typical", value=700))


def test_rejection_flags_runs_through_the_hook(repos: Repos, internet: Internet, tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    def flag(entry_id: str, version: int) -> list[str]:
        calls.append((entry_id, version))
        return ["RUN1", "RUN2"]

    service = LibraryService(repos, checker=internet.checker(), on_rejected=flag)
    entry_id = _approved_entry(service, tmp_path)
    result = service.reject(entry_id, 1, "Wrong units in the paper.")
    assert calls == [(entry_id, 1)]
    assert (result.flagged_runs, result.run_flagging, result.note) == (["RUN1", "RUN2"], "done", None)


def test_a_rejection_needs_a_reason(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    with pytest.raises(Refused, match="needs a reason"):
        service.reject(entry_id, 1, "   ")


# Editing (FR-016) --------------------------------------------------------------------------------------------------


def test_an_edit_creates_a_new_version(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    base = service.get_version(entry_id, 1)
    edited = service.edit(entry_id, EntryChanges(range_max=1500, notes=None))
    assert (edited.version, edited.status, edited.range_max, edited.notes) == (2, "proposed", 1500, None)
    assert edited.typical == base.typical and edited.values_by_source == base.values_by_source
    assert edited.content_hash != base.content_hash
    assert service.get_version(entry_id, 1) == base
    third = service.edit(entry_id, {"notes": "Range widened."})
    assert (third.version, third.range_max, third.notes) == (3, 1500, "Range widened.")


def test_an_edit_follows_the_same_rules(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _import(service, tmp_path)["bone.cancellous.E.D4"]
    with pytest.raises(Refused) as caught:
        service.edit(entry_id, {"typical": 1600, "units": "GPa"})
    assert sorted(p["code"] for p in caught.value.problems) == ["range", "units"]
    with pytest.raises(Refused) as caught:
        service.edit(entry_id, {"values_by_source": [ValueBySource(source_id="not-stored", value=690)]})
    assert caught.value.problems[0]["code"] == "unknown_source"
    with pytest.raises(Refused, match="as it is"):
        service.edit(entry_id, {"typical": 690})
    assert [v.version for v in service.describe(entry_id).versions] == [1]


# Snapshot export -------------------------------------------------------------------------------------------------


def test_export_writes_the_current_approved_versions(service: LibraryService, tmp_path: Path) -> None:
    entry_id = _approved_entry(service, tmp_path)
    service.edit(entry_id, {"typical": 700})  # proposed: not exported
    path = service.export_snapshot(tmp_path / "out" / "library-snapshot.yaml")
    snapshot = load_proposal(path)
    assert snapshot.is_snapshot
    (entry,) = snapshot.entries
    assert (entry.parameter_key, entry.version, entry.typical) == ("bone.cancellous.E.D4", 1, 690)
    assert entry.content_hash == service.get_version(entry_id, 1).content_hash
    assert [s.doi for s in snapshot.sources] == [DOI_A]


def test_default_snapshot_path_is_in_the_repository(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from dentalsim.library import service as service_module

    monkeypatch.setattr(service_module, "get_settings", lambda: SimpleNamespace(repo_root=tmp_path))
    assert (
        service_module.default_snapshot_path()
        == tmp_path / "content" / "library" / "snapshots" / "library-snapshot.yaml"
    )


# Command line ------------------------------------------------------------------------------------------------------


@pytest.fixture
def offline_cli(
    monkeypatch: pytest.MonkeyPatch, internet: Internet, data_dir: Path, fixed_time: FixedClock
) -> Internet:
    """The commands' source checks go to the mocked internet."""
    monkeypatch.setattr(source_check, "make_client", internet.client)
    return internet


def run(*argv: str) -> int:
    return cli.main(["library", *argv], modules=[library_commands])


def test_cli_import_check_approve_reject_export(
    offline_cli: Internet, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proposal = _write(tmp_path, _proposal())
    assert run("import", str(proposal)) == 0
    out = capsys.readouterr().out
    assert "imported 2 proposed version(s)" in out and "bone.cancellous.E.D4 v1 (new entry)" in out

    assert run("approve", "bone.cancellous.E.D4", "1") == 1
    err = capsys.readouterr().err
    assert "refused: bone.cancellous.E.D4 version 1 cannot be approved" in err
    assert "has not been checked" in err

    assert run("check", "bone.cancellous.E.D4") == 0
    out = capsys.readouterr().out
    assert "typical 690 MPa; range 200–1400 MPa" in out
    assert "690 MPa  Author AB et al. 2000 (doi 10.1000/example.a)  (Mean of the D4 range.)" in out
    assert "resolved_match: Author AB, Second CD (2000)" in out
    assert "title similarity 1.00" in out
    assert "can be approved: dentalsim library approve bone.cancellous.E.D4 1" in out
    assert {r.url.host for r in offline_cli.requests} == {"api.crossref.org"}

    assert run("approve", "bone.cancellous.E.D4", "1") == 0
    assert capsys.readouterr().out.strip() == "approved bone.cancellous.E.D4 v1"

    target = tmp_path / "snap.yaml"
    assert run("export", "--to", str(target), "--json") == 0
    assert json.loads(capsys.readouterr().out) == {"path": str(target), "entries": 1}
    assert target.is_file()

    assert run("reject", "bone.cancellous.E.D4", "1", "--reason", "Wrong density class.") == 0
    out = capsys.readouterr().out
    assert "rejected bone.cancellous.E.D4 v1: Wrong density class." in out
    assert "has no approved version now" in out
    assert "note: run flagging not available yet" in out


def test_cli_check_shows_a_mismatch_and_json(
    offline_cli: Internet, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offline_cli.add_work(DOI_B, family="Other", year=1990, title=TITLE_B)
    assert run("import", str(_write(tmp_path, _proposal()))) == 0
    entry_id = json.loads(_json_run(capsys, "check", "bone.cancellous.nu", "--version", "1"))["entry"]["id"]
    assert run("check", entry_id) == 0
    out = capsys.readouterr().out
    assert "resolved_mismatch: Other EF (2004)" in out
    assert "year differs (1990)" in out
    assert "cannot be approved:" in out


def _json_run(capsys: pytest.CaptureFixture[str], *argv: str) -> str:
    capsys.readouterr()
    assert run(*argv, "--json") == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["approvable"] is False
    assert [s["check"]["status"] for s in data["sources"]] == ["resolved_mismatch", "resolved_match"]
    return out


def test_cli_refusals_and_errors(offline_cli: Internet, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = _write(tmp_path, _proposal([_entry(units="GPa", typical=1500)]))
    assert run("import", str(bad)) == 1
    err = capsys.readouterr().err
    assert "refused: proposal.yaml does not meet the library import rules (2 problems)" in err
    assert "entry bone.cancellous.E.D4: units must be 'MPa'" in err
    assert "typical 1500 lies outside the range 200–1400" in err

    assert run("import", str(bad), "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["code"] == "validation_failed" and len(data["problems"]) == 2

    assert run("check", "bone.cortical.E") == 2  # no such entry: an error, not a refusal
    assert "no library entry" in capsys.readouterr().err
    assert run("import", str(tmp_path / "missing.yaml")) == 2

    assert run("import", str(_write(tmp_path, _proposal()))) == 0
    offline_cli.offline = True
    assert run("check", "bone.cancellous.E.D4") == 0
    out = capsys.readouterr().out
    assert "not_checked" in out and "could not be reached" in out
    assert run("approve", "bone.cancellous.E.D4", "1") == 1
    with pytest.raises(SystemExit) as exited:  # --reason is required; argparse exits with 2
        run("reject", "bone.cancellous.E.D4", "1")
    assert exited.value.code == 2
