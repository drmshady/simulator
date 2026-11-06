"""The physics capability registry (FR-011a): required cases come from case files, status from the repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from dentalsim.config import get_settings
from dentalsim.errors import DentalsimError
from dentalsim.persistence import CapabilityStatus, open_repos
from dentalsim.physics.registry import (
    UnknownCapability,
    all_capabilities,
    capability_state,
    default_cases_dir,
    get_capability,
    read_case_headers,
    stored_status,
    summaries,
    summarise,
)


class MemoryCapabilityRepo:
    """A `CapabilityRepo` held in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, CapabilityStatus] = {}

    def get(self, capability_id: str) -> CapabilityStatus | None:
        return self.rows.get(capability_id)

    def set(self, status: CapabilityStatus) -> None:
        self.rows[status.capability_id] = status

    def list_all(self) -> list[CapabilityStatus]:
        return list(self.rows.values())


@pytest.fixture
def repo() -> MemoryCapabilityRepo:
    return MemoryCapabilityRepo()


@pytest.fixture
def cases(tmp_path: Path) -> Path:
    folder = tmp_path / "cases"
    folder.mkdir()
    return folder


def _case(folder: Path, file_name: str, text: str) -> None:
    (folder / file_name).write_text(text, encoding="utf-8")


def _ready(
    repo: MemoryCapabilityRepo, capability_id: str, report_id: str = "R1", cases: tuple[str, ...] = ("a",)
) -> None:
    repo.set(CapabilityStatus(capability_id=capability_id, status="ready", report_id=report_id, cases=list(cases)))


def test_the_five_capabilities_are_registered() -> None:
    capabilities = {c.id: c for c in all_capabilities()}
    assert list(capabilities) == ["bonded_elastic", "screw_preload", "periodontal_ligament", "contact", "fatigue"]
    assert capabilities["bonded_elastic"].linear
    assert not any(c.linear for c in capabilities.values() if c.id != "bonded_elastic")
    assert capabilities["screw_preload"].label == "Screw preload"


def test_unknown_capability_is_a_clear_error() -> None:
    with pytest.raises(UnknownCapability, match="unknown physics capability 'magnetism'.*bonded_elastic"):
        get_capability("magnetism")
    assert issubclass(UnknownCapability, DentalsimError)
    assert issubclass(UnknownCapability, KeyError)


def test_bonded_elastic_hooks_add_nothing() -> None:
    bonded = get_capability("bonded_elastic")
    bonded.write_solver_input(object(), object())
    bonded.post(object())


def test_later_physics_hooks_cannot_run_yet() -> None:
    with pytest.raises(NotImplementedError, match="Story 9"):
        get_capability("contact").write_solver_input(object(), object())
    with pytest.raises(NotImplementedError, match="Story 9"):
        get_capability("contact").post(object())


def test_required_cases_are_read_from_case_files(cases: Path) -> None:
    _case(cases, "textbook-b.yaml", "id: textbook-b\ncapability: bonded_elastic\nrequired: true\n")
    _case(cases, "textbook-a.yaml", "id: textbook-a\ncapability: bonded_elastic\n")
    _case(cases, "textbook-optional.yml", "id: textbook-optional\ncapability: bonded_elastic\nrequired: false\n")
    _case(cases, "bolt.yaml", "id: bolt\ncapability: screw_preload\nquantities: []\n")
    _case(cases, "notes.txt", "id: not-a-case\n")
    assert get_capability("bonded_elastic").required_cases(cases) == ("textbook-a", "textbook-b")
    assert get_capability("screw_preload").required_cases(cases) == ("bolt",)
    assert get_capability("fatigue").required_cases(cases) == ()
    assert [h.id for h in read_case_headers(cases)] == ["bolt", "textbook-a", "textbook-b", "textbook-optional"]


def test_a_missing_cases_folder_holds_no_cases(tmp_path: Path) -> None:
    assert read_case_headers(tmp_path / "nowhere") == []


def test_default_cases_folder_is_in_the_repository() -> None:
    root = get_settings().repo_root
    assert root is not None
    assert default_cases_dir() == root / "content" / "verification" / "cases"


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("id: [unclosed\n", "cannot read"),
        ("- just\n- a list\n", "YAML mapping"),
        ("capability: bonded_elastic\n", "needs an `id`"),
        ("id: case-1\n", "needs a `capability`"),
        ("id: case-1\ncapability: magnetism\n", "unknown capability 'magnetism'"),
        ("id: case-1\ncapability: bonded_elastic\nrequired: maybe\n", "true or false"),
    ],
)
def test_a_broken_case_file_is_an_error(cases: Path, text: str, error: str) -> None:
    _case(cases, "broken.yaml", text)
    with pytest.raises(DentalsimError, match=error):
        read_case_headers(cases)


def test_two_case_files_with_one_id_are_an_error(cases: Path) -> None:
    _case(cases, "one.yaml", "id: same\ncapability: bonded_elastic\n")
    _case(cases, "two.yaml", "id: same\ncapability: bonded_elastic\n")
    with pytest.raises(DentalsimError, match="one.yaml and two.yaml"):
        read_case_headers(cases)


def test_status_defaults_to_awaiting_benchmark(repo: MemoryCapabilityRepo, cases: Path) -> None:
    _case(cases, "a.yaml", "id: a\ncapability: bonded_elastic\n")
    assert stored_status("bonded_elastic", repo) == CapabilityStatus(capability_id="bonded_elastic")
    assert capability_state("bonded_elastic", repo, cases) == "awaiting_benchmark"


def test_ready_needs_the_stored_status_and_a_required_case(repo: MemoryCapabilityRepo, cases: Path) -> None:
    _case(cases, "a.yaml", "id: a\ncapability: bonded_elastic\n")
    repo.set(CapabilityStatus(capability_id="bonded_elastic", status="awaiting_benchmark"))
    assert capability_state("bonded_elastic", repo, cases) == "awaiting_benchmark"
    _ready(repo, "bonded_elastic")
    assert capability_state("bonded_elastic", repo, cases) == "ready"


def test_without_case_files_a_capability_is_never_ready(repo: MemoryCapabilityRepo, cases: Path) -> None:
    _ready(repo, "bonded_elastic")
    _case(cases, "optional.yaml", "id: optional\ncapability: bonded_elastic\nrequired: false\n")
    summary = summarise("bonded_elastic", repo, cases)
    assert summary.status == "awaiting_benchmark"
    assert summary.report_id is None
    assert summary.message == "Bonded elastic is awaiting its benchmark: the textbook cases have not been written yet."


def test_summaries_cover_every_capability(repo: MemoryCapabilityRepo, cases: Path) -> None:
    _case(cases, "a.yaml", "id: a\ncapability: bonded_elastic\n")
    _ready(repo, "bonded_elastic", report_id="R7")
    listed = summaries(repo, cases)
    assert [s.id for s in listed] == [c.id for c in all_capabilities()]
    bonded = listed[0]
    assert (bonded.status, bonded.required_cases, bonded.report_id, bonded.message) == ("ready", ["a"], "R7", None)
    assert bonded.linear
    contact = next(s for s in listed if s.id == "contact")
    assert contact.status == "awaiting_benchmark"
    assert contact.message is not None
    assert contact.message.startswith("Contact is awaiting its benchmark")


def test_stored_status_of_an_unknown_capability_is_an_error(repo: MemoryCapabilityRepo) -> None:
    with pytest.raises(UnknownCapability):
        stored_status("magnetism", repo)


def test_status_through_the_studio_database(data_dir: Path, cases: Path) -> None:
    """A ready row needs a stored verification report (task T071), so this covers the awaiting rows only."""
    _case(cases, "a.yaml", "id: a\ncapability: bonded_elastic\n")
    repos = open_repos(data_dir)
    try:
        assert stored_status("bonded_elastic", repos.capabilities).status == "awaiting_benchmark"
        repos.capabilities.set(CapabilityStatus(capability_id="bonded_elastic"))
        assert capability_state("bonded_elastic", repos.capabilities, cases) == "awaiting_benchmark"
    finally:
        repos.close()
