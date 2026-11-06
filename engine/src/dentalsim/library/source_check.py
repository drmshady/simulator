"""Check that a cited source's DOI or PMID resolves to the paper as cited (FR-054, research R13).

- DOIs are looked up at Crossref (`api.crossref.org/works/{doi}`), PMIDs at NCBI E-utilities (`esummary`).
- A source matches when the first author's surname, the year and the normalised title (similarity ≥ 0.90) all
  agree with the record.
- Requests carry only the generic User-Agent `dentalsim-studio/<version>`: no e-mail address, no personal data.
- Results: `resolved_match`; `resolved_mismatch` (`detail` says what differs); `unresolved` (no such record);
  `not_checked` (no answer: offline, a timeout, a server error), and then the entry cannot be approved.
- A source with both a DOI and a PMID is checked by DOI; the PMID is tried only when the DOI does not resolve.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal
from urllib.parse import quote

import httpx

from dentalsim.clock import utc_now
from dentalsim.library.models import Resolver, Source, SourceCheck, SourceMatch
from dentalsim.version import package_version

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
NCBI_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
TITLE_THRESHOLD = 0.90
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
UNRESOLVED_HTTP = frozenset({400, 404, 410})
RESOLVER_NAMES: dict[Resolver, str] = {"crossref": "Crossref", "ncbi": "PubMed (NCBI)"}

_TAG = re.compile(r"<[^>]*>")
_NOT_WORD = re.compile(r"[\W_]+")
_YEAR = re.compile(r"(1[89]\d\d|20\d\d|2100)")


def user_agent() -> str:
    return f"dentalsim-studio/{package_version()}"


def make_client() -> httpx.Client:
    """The HTTP client `check_source` uses when none is given."""
    return httpx.Client(headers={"User-Agent": user_agent()}, timeout=TIMEOUT, follow_redirects=True)


# Normalising names and titles ------------------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalise_title(title: str) -> str:
    """Casefolded, without HTML or MathML tags, accents, punctuation or runs of whitespace."""
    text = html.unescape(_TAG.sub(" ", title))
    text = _strip_accents(text).casefold()
    return _NOT_WORD.sub(" ", text).strip()


def title_similarity(cited: str, resolved: str) -> float:
    return SequenceMatcher(None, normalise_title(cited), normalise_title(resolved), autojunk=False).ratio()


def _is_initials(token: str) -> bool:
    letters = token.replace(".", "").replace("-", "")
    return 1 <= len(letters) <= 4 and letters.isalpha() and letters.isupper()


def surname(author: str) -> str:
    """The surname in "Surname AB", "Surname, A. B.", "A. B. Surname" or plain "Surname"."""
    text = author.strip()
    if "," in text:
        return text.split(",", 1)[0].strip()
    tokens = text.split()
    while len(tokens) > 1 and _is_initials(tokens[-1]):
        tokens.pop()
    while len(tokens) > 1 and _is_initials(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens)


def _name_words(name: str) -> list[str]:
    return [word for word in _NOT_WORD.split(_strip_accents(name).casefold()) if word]


def surnames_match(cited_author: str, resolved_author: str) -> bool:
    """Accent- and case-insensitive; "van der Berg" also matches "Berg", and "Smith AB" matches "Smith"."""
    cited, resolved = _name_words(surname(cited_author)), _name_words(surname(resolved_author))
    if not cited or not resolved:
        return False
    return "".join(cited) == "".join(resolved) or cited[-1] == resolved[-1]


# Resolvers --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Record:
    """What a resolver returned: the first author as given there, candidate years and titles (primary first)."""

    resolver: Resolver
    first_author: str | None
    years: tuple[int, ...]
    titles: tuple[str, ...]


@dataclass(frozen=True)
class _Failure:
    status: Literal["unresolved", "not_checked"]
    resolver: Resolver
    detail: str


def _headers() -> dict[str, str]:
    return {"User-Agent": user_agent(), "Accept": "application/json"}


def _get(client: httpx.Client, resolver: Resolver, url: str, params: dict[str, str] | None = None) -> Any:
    """The decoded JSON answer, or a `_Failure`."""
    name = RESOLVER_NAMES[resolver]
    try:
        response = client.get(url, params=params, headers=_headers())
    except httpx.TimeoutException:
        return _Failure("not_checked", resolver, f"{name} did not answer in time")
    except httpx.HTTPError as exc:
        return _Failure("not_checked", resolver, f"{name} could not be reached ({type(exc).__name__})")
    if response.status_code in UNRESOLVED_HTTP:
        return _Failure("unresolved", resolver, f"{name} has no record (HTTP {response.status_code})")
    if response.status_code != 200:
        return _Failure("not_checked", resolver, f"{name} answered HTTP {response.status_code}; try again later")
    try:
        return response.json()
    except ValueError:
        return _Failure("not_checked", resolver, f"{name} gave an answer that is not JSON")


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _crossref_year(message: dict[str, Any], key: str) -> int | None:
    parts = (message.get(key) or {}).get("date-parts") or [[None]]
    year = parts[0][0] if parts and parts[0] else None
    return year if isinstance(year, int) else None


def lookup_crossref(doi: str, client: httpx.Client) -> _Record | _Failure:
    answer = _get(client, "crossref", CROSSREF_URL.format(doi=quote(doi, safe="/")))
    if isinstance(answer, _Failure):
        return _Failure(answer.status, "crossref", f"DOI {doi}: {answer.detail}")
    message = answer.get("message") if isinstance(answer, dict) else None
    if not isinstance(message, dict):
        return _Failure("not_checked", "crossref", f"DOI {doi}: Crossref gave an unexpected answer")
    titles = [_clean(t) for t in message.get("title") or [] if isinstance(t, str)]
    subtitles = [_clean(s) for s in message.get("subtitle") or [] if isinstance(s, str)]
    titles += [f"{titles[0]}: {sub}" for sub in subtitles[:1]] if titles else []
    authors = [a for a in message.get("author") or [] if isinstance(a, dict)]
    first = next((a for a in authors if a.get("sequence") == "first"), authors[0] if authors else None)
    first_author = (first.get("family") or first.get("name")) if first else None
    years = [_crossref_year(message, key) for key in ("issued", "published", "published-print", "published-online")]
    return _Record(
        resolver="crossref",
        first_author=first_author if isinstance(first_author, str) else None,
        years=tuple(dict.fromkeys(y for y in years if y is not None)),
        titles=tuple(titles),
    )


def lookup_pubmed(pmid: str, client: httpx.Client) -> _Record | _Failure:
    answer = _get(client, "ncbi", NCBI_ESUMMARY_URL, {"db": "pubmed", "id": pmid, "retmode": "json"})
    if isinstance(answer, _Failure):
        return _Failure(answer.status, "ncbi", f"PMID {pmid}: {answer.detail}")
    result = answer.get("result") if isinstance(answer, dict) else None
    record = result.get(pmid) if isinstance(result, dict) else None
    if not isinstance(record, dict) or record.get("error") or not record.get("title"):
        return _Failure("unresolved", "ncbi", f"PMID {pmid}: PubMed (NCBI) has no record")
    authors = [a for a in record.get("authors") or [] if isinstance(a, dict) and isinstance(a.get("name"), str)]
    first = next((a for a in authors if a.get("authtype", "Author") == "Author"), None)
    first_author = first["name"] if first else record.get("sortfirstauthor") or None
    years = []
    for key in ("pubdate", "epubdate", "sortpubdate"):
        found = _YEAR.search(str(record.get(key) or ""))
        if found:
            years.append(int(found.group(1)))
    return _Record(
        resolver="ncbi",
        first_author=first_author if isinstance(first_author, str) else None,
        years=tuple(dict.fromkeys(years)),
        titles=(_clean(str(record["title"])),),
    )


# Comparing ---------------------------------------------------------------------------------------------------------


def compare(source: Source, record: _Record) -> tuple[SourceMatch, list[str]]:
    """How the record compares with the source as cited, and what differs (empty when it matches)."""
    cited_author = source.authors[0]
    author_ok = record.first_author is not None and surnames_match(cited_author, record.first_author)
    year_ok = source.year in record.years
    similarity = max((title_similarity(source.title, title) for title in record.titles), default=0.0)
    differences = []
    if not author_ok:
        resolved = surname(record.first_author) if record.first_author else "no author"
        differences.append(f"first author: cited {surname(cited_author)!r}, resolved {resolved!r}")
    if not year_ok:
        resolved_years = ", ".join(str(y) for y in record.years) or "no year"
        differences.append(f"year: cited {source.year}, resolved {resolved_years}")
    if similarity < TITLE_THRESHOLD:
        resolved_title = record.titles[0] if record.titles else "no title"
        differences.append(
            f"title similarity {similarity:.2f} is below {TITLE_THRESHOLD:.2f} (resolved title: {resolved_title!r})"
        )
    match = SourceMatch(
        first_author=author_ok,
        year=year_ok,
        title_similarity=round(similarity, 4),
        resolved_first_author=record.first_author,
        resolved_year=source.year if year_ok else (record.years[0] if record.years else None),
        resolved_title=record.titles[0] if record.titles else None,
    )
    return match, differences


def _result(source: Source, found: _Record | _Failure, *, note: Sequence[str] = ()) -> SourceCheck:
    checked_at = utc_now()
    if isinstance(found, _Failure):
        detail = "; ".join([*note, found.detail])
        return SourceCheck(status=found.status, checked_at=checked_at, resolver=found.resolver, detail=detail)
    match, differences = compare(source, found)
    name = RESOLVER_NAMES[found.resolver]
    if differences:
        status: Literal["resolved_match", "resolved_mismatch"] = "resolved_mismatch"
        detail = f"{name} record differs: " + "; ".join(differences)
    else:
        status = "resolved_match"
        detail = f"{name}: first author, year and title agree (title similarity {match.title_similarity:.2f})"
    return SourceCheck(
        status=status,
        checked_at=checked_at,
        resolver=found.resolver,
        matched=match,
        detail="; ".join([*note, detail]),
    )


def check_source(source: Source, client: httpx.Client | None = None) -> SourceCheck:
    """Look the source up and compare it with the record. Never raises for network trouble: that is `not_checked`."""
    http = client if client is not None else make_client()
    try:
        if source.doi:
            found = lookup_crossref(source.doi, http)
            if not (isinstance(found, _Failure) and found.status == "unresolved" and source.pmid):
                return _result(source, found)
            note = [f"{found.detail}, so the PMID was checked instead"]
            assert source.pmid is not None
            return _result(source, lookup_pubmed(source.pmid, http), note=note)
        assert source.pmid is not None  # a Source always has a DOI or a PMID
        return _result(source, lookup_pubmed(source.pmid, http))
    finally:
        if client is None:
            http.close()
