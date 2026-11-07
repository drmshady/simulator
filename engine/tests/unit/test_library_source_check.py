"""The DOI and PMID check (T036, FR-054) against mocked Crossref and NCBI answers; nothing reaches the internet."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from dentalsim.library import source_check
from dentalsim.library.models import Source
from dentalsim.library.source_check import check_source, normalise_title, surname, surnames_match, title_similarity
from dentalsim.testing import FIXED_TIME, FixedClock
from dentalsim.version import package_version

DOI = "10.1016/S0021-9290(99)00137-2"
PMID = "10580382"
TITLE = "Elastic modulus and strength of human trabecular bone: a comparison of in vitro methods"

CROSSREF_WORK: dict[str, Any] = {
    "status": "ok",
    "message-type": "work",
    "message-version": "1.0.0",
    "message": {
        "DOI": DOI.lower(),
        "type": "journal-article",
        "publisher": "Elsevier BV",
        "title": ["Elastic modulus and strength of human trabecular bone: a comparison of <i>in vitro</i> methods"],
        "subtitle": [],
        "container-title": ["Journal of Biomechanics"],
        "author": [
            {"given": "Øyvind", "family": "Sjöström", "sequence": "first", "affiliation": []},
            {"given": "Tony M.", "family": "Keaveny", "sequence": "additional", "affiliation": []},
        ],
        "issued": {"date-parts": [[2000, 1]]},
        "published-print": {"date-parts": [[2000, 1]]},
        "published-online": {"date-parts": [[1999, 12, 20]]},
        "volume": "33",
        "page": "37-46",
    },
}

NCBI_SUMMARY: dict[str, Any] = {
    "header": {"type": "esummary", "version": "0.3"},
    "result": {
        "uids": [PMID],
        PMID: {
            "uid": PMID,
            "pubdate": "2000 Jan",
            "epubdate": "",
            "source": "J Biomech",
            "authors": [
                {"name": "Sjostrom O", "authtype": "Author", "clusterid": ""},
                {"name": "Keaveny TM", "authtype": "Author", "clusterid": ""},
            ],
            "lastauthor": "Keaveny TM",
            "title": "Elastic modulus and strength of human trabecular bone: a comparison of in vitro methods.",
            "sortfirstauthor": "Sjostrom O",
            "fulljournalname": "Journal of biomechanics",
            "sortpubdate": "2000/01/01 00:00",
            "articleids": [{"idtype": "pubmed", "idtypen": 1, "value": PMID}],
        },
    },
}

NCBI_NO_RECORD: dict[str, Any] = {
    "header": {"type": "esummary", "version": "0.3"},
    "result": {"uids": ["99999999"], "99999999": {"uid": "99999999", "error": "cannot get document summary"}},
}


def _source(**changes: Any) -> Source:
    fields: dict[str, Any] = {
        "id": "S1",
        "authors": ["Sjöström Ø", "Keaveny TM"],
        "year": 2000,
        "title": TITLE,
        "journal": "J Biomech",
        "doi": DOI,
        "pmid": None,
        "study_type": "experimental",
    }
    fields.update(changes)
    return Source(**fields)


Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """A mock transport that records the requests it answers."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _crossref(work: dict[str, Any] | None = None, *, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.crossref.org":
            return httpx.Response(500)
        if status != 200:
            return httpx.Response(status, text="Resource not found.")
        return httpx.Response(200, json=work or CROSSREF_WORK)

    return handler


def _ncbi(summary: dict[str, Any] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return httpx.Response(404, text="Resource not found.")
        return httpx.Response(200, json=summary or NCBI_SUMMARY)

    return handler


@pytest.fixture(autouse=True)
def clock(fixed_time: FixedClock) -> FixedClock:
    return fixed_time


def test_crossref_match() -> None:
    recorder = Recorder(_crossref())
    check = check_source(_source(), recorder.client())
    assert check.status == "resolved_match"
    assert check.resolver == "crossref"
    assert check.checked_at == FIXED_TIME
    assert check.matched is not None
    assert check.matched.first_author and check.matched.year
    assert check.matched.title_similarity == 1.0
    assert check.matched.resolved_first_author == "Sjöström"
    assert check.matched.resolved_year == 2000
    assert check.matched.resolved_title == (
        "Elastic modulus and strength of human trabecular bone: a comparison of in vitro methods"
    )
    (request,) = recorder.requests
    assert request.url.path == f"/works/{DOI}"


def test_requests_carry_only_the_generic_user_agent() -> None:
    recorder = Recorder(_ncbi())
    check_source(_source(doi=None, pmid=PMID), recorder.client())
    (request,) = recorder.requests
    assert request.headers["User-Agent"] == f"dentalsim-studio/{package_version()}"
    assert request.url.host == "eutils.ncbi.nlm.nih.gov"
    assert dict(request.url.params) == {"db": "pubmed", "id": PMID, "retmode": "json"}
    text = str(request.url).lower() + " ".join(f"{k}:{v}" for k, v in request.headers.items()).lower()
    for personal in ("mailto", "email", "@"):
        assert personal not in text


def test_the_default_client_uses_the_user_agent_and_a_timeout() -> None:
    with source_check.make_client() as client:
        assert client.headers["User-Agent"] == f"dentalsim-studio/{package_version()}"
        assert client.timeout.read is not None


def test_mismatched_year() -> None:
    check = check_source(_source(year=2003), Recorder(_crossref()).client())
    assert check.status == "resolved_mismatch"
    assert check.matched is not None and not check.matched.year and check.matched.first_author
    assert check.detail is not None and "year: cited 2003, resolved 2000, 1999" in check.detail


def test_online_first_year_also_matches() -> None:
    assert check_source(_source(year=1999), Recorder(_crossref()).client()).status == "resolved_match"


def test_mismatched_title() -> None:
    check = check_source(
        _source(title="Cortical bone thickness in the posterior mandible"), Recorder(_crossref()).client()
    )
    assert check.status == "resolved_mismatch"
    assert check.matched is not None and check.matched.title_similarity < 0.9
    assert check.detail is not None and "title similarity" in check.detail


def test_mismatched_first_author() -> None:
    check = check_source(_source(authors=["Morgan EF"]), Recorder(_crossref()).client())
    assert check.status == "resolved_mismatch"
    assert check.detail is not None and "first author: cited 'Morgan', resolved 'Sjöström'" in check.detail


def test_doi_not_found_is_unresolved() -> None:
    check = check_source(_source(), Recorder(_crossref(status=404)).client())
    assert check.status == "unresolved"
    assert check.resolver == "crossref"
    assert check.matched is None
    assert check.detail is not None and "HTTP 404" in check.detail


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("no route to host"), httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")]
)
def test_network_error_is_not_checked(error: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    check = check_source(_source(pmid=PMID), Recorder(handler).client())
    assert check.status == "not_checked"
    assert check.checked_at == FIXED_TIME
    assert check.detail is not None and "Crossref" in check.detail


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_trouble_is_not_checked(status: int) -> None:
    check = check_source(_source(), Recorder(_crossref(status=status)).client())
    assert check.status == "not_checked"


def test_answer_that_is_not_json_is_not_checked() -> None:
    check = check_source(_source(), Recorder(lambda r: httpx.Response(200, text="<html>busy</html>")).client())
    assert check.status == "not_checked"


def test_pubmed_match() -> None:
    check = check_source(_source(doi=None, pmid=PMID), Recorder(_ncbi()).client())
    assert check.status == "resolved_match"
    assert check.resolver == "ncbi"
    assert check.matched is not None and check.matched.resolved_first_author == "Sjostrom O"
    assert check.matched.title_similarity == 1.0


def test_pubmed_without_a_record_is_unresolved() -> None:
    check = check_source(_source(doi=None, pmid="99999999"), Recorder(_ncbi(NCBI_NO_RECORD)).client())
    assert check.status == "unresolved"
    assert check.resolver == "ncbi"


def test_pmid_is_checked_when_the_doi_does_not_resolve() -> None:
    recorder = Recorder(_ncbi())
    check = check_source(_source(pmid=PMID), recorder.client())
    assert [r.url.host for r in recorder.requests] == ["api.crossref.org", "eutils.ncbi.nlm.nih.gov"]
    assert check.status == "resolved_match"
    assert check.resolver == "ncbi"
    assert check.detail is not None and "so the PMID was checked instead" in check.detail


def test_pmid_is_not_tried_when_the_doi_resolves_to_another_paper() -> None:
    recorder = Recorder(_crossref())
    check = check_source(_source(pmid=PMID, year=2010), recorder.client())
    assert len(recorder.requests) == 1
    assert check.status == "resolved_mismatch"


def test_pmid_is_not_tried_when_crossref_cannot_be_reached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json=NCBI_SUMMARY)

    recorder = Recorder(handler)
    assert check_source(_source(pmid=PMID), recorder.client()).status == "not_checked"
    assert len(recorder.requests) == 1


def test_crossref_record_without_authors_or_dates_is_a_mismatch() -> None:
    work = copy.deepcopy(CROSSREF_WORK)
    del work["message"]["author"], work["message"]["issued"], work["message"]["published-print"]
    del work["message"]["published-online"]
    check = check_source(_source(), Recorder(_crossref(work)).client())
    assert check.status == "resolved_mismatch"
    assert check.detail is not None and "no author" in check.detail and "no year" in check.detail


def test_crossref_subtitle_is_part_of_the_title() -> None:
    work = copy.deepcopy(CROSSREF_WORK)
    work["message"]["title"] = ["Elastic modulus and strength of human trabecular bone"]
    work["message"]["subtitle"] = ["A comparison of in vitro methods"]
    assert check_source(_source(), Recorder(_crossref(work)).client()).status == "resolved_match"


def test_check_source_creates_and_closes_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[httpx.Client] = []

    def make_client() -> httpx.Client:
        clients.append(Recorder(_crossref()).client())
        return clients[-1]

    monkeypatch.setattr(source_check, "make_client", make_client)
    assert check_source(_source()).status == "resolved_match"
    assert clients[0].is_closed


@pytest.mark.parametrize(
    ("cited", "resolved", "same"),
    [
        ("Smith AB", "Smith", True),
        ("Smith, A. B.", "smith", True),
        ("A. B. Smith", "Smith", True),
        ("Müller K", "Muller", True),
        ("van der Berg J", "Berg", True),
        ("Al-Sukhun J", "Al Sukhun", True),
        ("Smith AB", "Smith AB", True),
        ("LEE AB", "Lee", True),
        ("Rho JY, Ashman RB, Turner CH", "Rho", True),
        ("van der Berg J, Smith AB", "Berg", True),
        ("Rho JY, Ashman RB", "Ashman", False),
        ("Smith AB", "Smyth", False),
        ("Jones", "Smith", False),
    ],
)
def test_surname_comparison(cited: str, resolved: str, same: bool) -> None:
    assert surnames_match(cited, resolved) is same


def test_surname_extraction() -> None:
    assert surname("Smith AB") == "Smith"
    assert surname("Smith, A.B.") == "Smith"
    assert surname("J.-P. Dupont") == "Dupont"
    assert surname("Smith") == "Smith"
    assert surname("Rho JY, Ashman RB, Turner CH") == "Rho"
    assert surname("van der Berg, J.") == "van der Berg"


def test_title_normalisation() -> None:
    assert normalise_title("Stress in <i>peri-implant</i> bone: a <mml:math><mml:mi>3D</mml:mi></mml:math> FEA.") == (
        "stress in peri implant bone a 3d fea"
    )
    assert normalise_title("Étude   des contraintes &amp; déformations") == "etude des contraintes deformations"
    assert title_similarity("A study of bone.", "a STUDY of  bone") == 1.0
    assert title_similarity("Bone remodelling", "Implant fatigue under cyclic load") < 0.9
